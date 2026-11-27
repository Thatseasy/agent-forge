"""Environment, path, API-key presence, and hooks diagnostics for agent-forge."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from forge.config import ForgeConfig
from forge.paths import find_forge_checkout
from forge.sync.workspace import validate_hooks_schema

REQUIRED_GLOBAL_TIMEOUT_MAX = 5
REQUIRED_PRE_WRITE_PARITY = ("write_file", "write_to_file")
REQUIRED_POST_READ_PARITY = ("read_file", "view_file")


class HookCheckResult(BaseModel):
    """Validation result for global plugin `hooks.json`, repo hooks, and `hooks_global/`."""

    valid: bool
    root_hooks_path: str
    root_hooks_exists: bool = False
    target_hooks_path: str | None = None
    target_hooks_exists: bool | None = None
    schema_valid: bool = True
    utf8_no_bom_lf: bool = True
    timeouts_ok: bool = True
    tool_parity_ok: bool = True
    stdlib_only_ok: bool = True
    non_stdlib_imports: dict[str, list[str]] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DoctorReport(BaseModel):
    """Structured diagnostic report returned by `run_doctor`."""

    status: str = "ok"
    os_name: str
    os_release: str
    python_version: str
    python_executable: str
    tools: dict[str, bool] = Field(default_factory=dict)
    tool_paths: dict[str, str | None] = Field(default_factory=dict)
    agent_forge_home: str
    agent_forge_home_exists: bool
    agent_forge_home_writable: bool
    package_assets_dir: str
    package_assets_exists: bool
    target_dir: str | None = None
    root_hooks_present: bool = False
    target_hooks_present: bool = False
    api_keys_configured: dict[str, bool] = Field(default_factory=dict)
    hooks_check: HookCheckResult | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _check_utf8_no_bom_lf(path: Path) -> tuple[bool, str | None]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return False, f"{path}: must not contain UTF-8 BOM"
    if b"\r\n" in raw:
        return False, f"{path}: must use LF line endings (found CRLF)"
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return False, f"{path}: invalid UTF-8 ({exc})"
    return True, None


def _extract_matcher_tokens(groups: Any) -> set[str]:
    tokens: set[str] = set()
    if not isinstance(groups, list):
        return tokens
    for group in groups:
        if not isinstance(group, dict):
            continue
        matcher = str(group.get("matcher") or "")
        for part in matcher.split("|"):
            tok = part.strip().lower()
            if tok:
                tokens.add(tok)
    return tokens


def _check_tool_parity(data: dict[str, Any], label: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    pre_tokens: set[str] = set()
    post_tokens: set[str] = set()
    for spec in data.values():
        if not isinstance(spec, dict):
            continue
        pre_tokens |= _extract_matcher_tokens(spec.get("PreToolUse"))
        post_tokens |= _extract_matcher_tokens(spec.get("PostToolUse"))

    missing_pre = [t for t in REQUIRED_PRE_WRITE_PARITY if t not in pre_tokens]
    if missing_pre:
        errors.append(f"{label}: PreToolUse missing tool-name parity for {missing_pre}")

    missing_post = [t for t in REQUIRED_POST_READ_PARITY if t not in post_tokens]
    if missing_post:
        errors.append(f"{label}: PostToolUse missing tool-name parity for {missing_post}")

    return (not errors), errors


def _check_global_timeouts(data: dict[str, Any], label: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    for suite_name, spec in data.items():
        if not isinstance(spec, dict):
            continue
        for flat_ev in ("SessionStart", "PreInvocation", "PostInvocation", "Stop"):
            arr = spec.get(flat_ev)
            if isinstance(arr, list):
                for idx, handler in enumerate(arr):
                    timeout = handler.get("timeout") if isinstance(handler, dict) else None
                    if (
                        not isinstance(timeout, (int, float))
                        or isinstance(timeout, bool)
                        or timeout <= 0
                        or timeout > REQUIRED_GLOBAL_TIMEOUT_MAX
                    ):
                        errors.append(
                            f"{label} [{suite_name}.{flat_ev}[{idx}]]: "
                            f"timeout must be <= {REQUIRED_GLOBAL_TIMEOUT_MAX}s (got {timeout!r})"
                        )
        for tool_ev in ("PreToolUse", "PostToolUse"):
            groups = spec.get(tool_ev)
            if isinstance(groups, list):
                for g_idx, group in enumerate(groups):
                    hooks = group.get("hooks") if isinstance(group, dict) else None
                    if not isinstance(hooks, list):
                        continue
                    for h_idx, handler in enumerate(hooks):
                        timeout = handler.get("timeout") if isinstance(handler, dict) else None
                        if (
                            not isinstance(timeout, (int, float))
                            or isinstance(timeout, bool)
                            or timeout <= 0
                            or timeout > REQUIRED_GLOBAL_TIMEOUT_MAX
                        ):
                            errors.append(
                                f"{label} [{suite_name}.{tool_ev}[{g_idx}].hooks[{h_idx}]]: "
                                f"timeout must be <= {REQUIRED_GLOBAL_TIMEOUT_MAX}s "
                                f"(got {timeout!r})"
                            )
    return (not errors), errors


def _inspect_stdlib_only(hooks_global_dir: Path) -> tuple[bool, dict[str, list[str]], list[str]]:
    allowed = sys.stdlib_module_names | {"__future__"}
    non_stdlib: dict[str, list[str]] = {}
    errors: list[str] = []

    if not hooks_global_dir.is_dir():
        errors.append(f"Missing global hooks package directory: {hooks_global_dir}")
        return False, non_stdlib, errors

    for required_script in ("guard.py", "tracker.py"):
        script_path = hooks_global_dir / required_script
        if not script_path.is_file():
            errors.append(f"Missing required global hook script: {script_path}")

    py_files = sorted(hooks_global_dir.glob("*.py"))
    if not py_files:
        errors.append(f"No Python files found under {hooks_global_dir}")
        return False, non_stdlib, errors

    for py_file in py_files:
        enc_ok, enc_err = _check_utf8_no_bom_lf(py_file)
        if not enc_ok and enc_err:
            errors.append(enc_err)
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{py_file.name}: AST parse failed ({exc})")
            continue

        bad_mods: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in allowed and top not in bad_mods:
                        bad_mods.append(top)
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    rel = f".{node.module or ''}"
                    if rel not in bad_mods:
                        bad_mods.append(rel)
                elif node.module:
                    top = node.module.split(".")[0]
                    if top not in allowed and top not in bad_mods:
                        bad_mods.append(top)

        if bad_mods:
            non_stdlib[py_file.name] = bad_mods
            errors.append(f"{py_file.name}: non-stdlib imports disallowed: {bad_mods}")

    return (not errors and not non_stdlib), non_stdlib, errors


def validate_plugin_hooks(
    root_dir: Path,
    target_dir: Path | None = None,
    assets_dir: Path | None = None,
) -> HookCheckResult:
    """Validate plugin root `hooks.json`, optional `.agents/hooks.json`, and `hooks_global/`."""
    resolved_root = root_dir.resolve()
    root_hooks_path = resolved_root / "hooks.json"
    if not root_hooks_path.is_file() and assets_dir is not None:
        bundled_global = assets_dir / "global_hooks.json"
        if bundled_global.is_file():
            root_hooks_path = bundled_global
    effective_target = target_dir.resolve() if target_dir is not None else resolved_root
    target_hooks_path = effective_target / ".agents" / "hooks.json"

    errors: list[str] = []
    warnings: list[str] = []
    schema_valid = True
    utf8_no_bom_lf = True
    timeouts_ok = True
    tool_parity_ok = True

    root_exists = root_hooks_path.is_file()
    if not root_exists:
        schema_valid = False
        errors.append(f"Missing plugin root hooks.json: {root_hooks_path}")
    else:
        enc_ok, enc_err = _check_utf8_no_bom_lf(root_hooks_path)
        if not enc_ok:
            utf8_no_bom_lf = False
            if enc_err:
                errors.append(enc_err)
        try:
            if not validate_hooks_schema(root_hooks_path):
                schema_valid = False
                errors.append(f"{root_hooks_path}: failed validate_hooks_schema")
            data = json.loads(root_hooks_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            schema_valid = False
            data = None
            errors.append(f"{root_hooks_path}: invalid JSON ({exc})")

        if isinstance(data, dict):
            t_ok, t_errs = _check_global_timeouts(data, "hooks.json")
            if not t_ok:
                timeouts_ok = False
                errors.extend(t_errs)
            p_ok, p_errs = _check_tool_parity(data, "hooks.json")
            if not p_ok:
                tool_parity_ok = False
                errors.extend(p_errs)

    target_exists = target_hooks_path.is_file()
    if target_exists:
        enc_ok, enc_err = _check_utf8_no_bom_lf(target_hooks_path)
        if not enc_ok:
            utf8_no_bom_lf = False
            if enc_err:
                errors.append(enc_err)
        try:
            if not validate_hooks_schema(target_hooks_path):
                schema_valid = False
                errors.append(f"{target_hooks_path}: failed validate_hooks_schema")
            tdata = json.loads(target_hooks_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            schema_valid = False
            tdata = None
            errors.append(f"{target_hooks_path}: invalid JSON ({exc})")

        if isinstance(tdata, dict):
            p_ok, p_errs = _check_tool_parity(tdata, ".agents/hooks.json")
            if not p_ok:
                tool_parity_ok = False
                errors.extend(p_errs)

    hooks_global_dir = resolved_root / "src" / "forge" / "hooks_global"
    if not hooks_global_dir.is_dir() and assets_dir is not None:
        installed_global_dir = Path(__file__).resolve().parent / "hooks_global"
        if installed_global_dir.is_dir():
            hooks_global_dir = installed_global_dir
    stdlib_only_ok, non_stdlib, stdlib_errs = _inspect_stdlib_only(hooks_global_dir)
    if stdlib_errs:
        errors.extend(stdlib_errs)

    valid = (
        root_exists
        and schema_valid
        and utf8_no_bom_lf
        and timeouts_ok
        and tool_parity_ok
        and stdlib_only_ok
        and not errors
    )
    return HookCheckResult(
        valid=valid,
        root_hooks_path=str(root_hooks_path),
        root_hooks_exists=root_exists,
        target_hooks_path=str(target_hooks_path),
        target_hooks_exists=target_exists,
        schema_valid=schema_valid,
        utf8_no_bom_lf=utf8_no_bom_lf,
        timeouts_ok=timeouts_ok,
        tool_parity_ok=tool_parity_ok,
        stdlib_only_ok=stdlib_only_ok,
        non_stdlib_imports=non_stdlib,
        errors=errors,
        warnings=warnings,
    )


def _is_dir_writable(path: Path) -> tuple[bool, bool]:
    """Return `(exists, writable)` without leaving probe files behind."""
    if path.exists():
        if not path.is_dir():
            return False, False
        probe = path / f".forge_doctor_probe_{os.getpid()}"
        try:
            probe.write_text("ok", encoding="utf-8", newline="\n")
            probe.unlink(missing_ok=True)
            return True, True
        except OSError:
            return True, False

    for ancestor in path.parents:
        if ancestor.exists():
            return False, os.access(ancestor, os.W_OK)
    return False, False


def _resolve_plugin_root(config: ForgeConfig) -> Path:
    if (config.root / "hooks.json").is_file() or (
        config.root / "src" / "forge" / "hooks_global"
    ).is_dir():
        return config.root
    checkout = find_forge_checkout()
    if checkout is not None and (checkout / "hooks.json").is_file():
        return checkout
    pkg_root = Path(__file__).resolve().parents[2]
    if (pkg_root / "hooks.json").is_file():
        return pkg_root
    return config.root


def run_doctor(
    config: ForgeConfig,
    target_dir: Path | None = None,
    check_hooks: bool = False,
) -> DoctorReport:
    """Run environment, path, API-key presence, and optional hooks validation checks."""
    uv_path = shutil.which("uv")
    git_path = shutil.which("git")
    docker_path = shutil.which("docker")
    pw_cli = shutil.which("playwright")
    pw_mod = importlib.util.find_spec("playwright") is not None

    tools = {
        "uv": uv_path is not None,
        "git": git_path is not None,
        "playwright": (pw_cli is not None) or pw_mod,
        "docker": docker_path is not None,
    }
    tool_paths: dict[str, str | None] = {
        "uv": uv_path,
        "git": git_path,
        "playwright": pw_cli or ("python-module:playwright" if pw_mod else None),
        "docker": docker_path,
    }

    home_exists, home_writable = _is_dir_writable(config.home_dir)
    assets_exists = config.assets_dir.is_dir()

    api_keys_configured = {
        "PERPLEXITY_API_KEY": bool(
            config.perplexity_api_key or os.environ.get("PERPLEXITY_API_KEY", "").strip()
        ),
        "FIRECRAWL_API_KEY": bool(
            config.firecrawl_api_key or os.environ.get("FIRECRAWL_API_KEY", "").strip()
        ),
        "CONTEXT7_API_KEY": bool(
            config.context7_api_key or os.environ.get("CONTEXT7_API_KEY", "").strip()
        ),
        "EXA_API_KEY": bool(config.exa_api_key or os.environ.get("EXA_API_KEY", "").strip()),
        "OPENAI_API_KEY": bool(
            config.openai_api_key or os.environ.get("OPENAI_API_KEY", "").strip()
        ),
        "GEMINI_API_KEY": bool(
            config.gemini_api_key or os.environ.get("GEMINI_API_KEY", "").strip()
        ),
        "ANTHROPIC_API_KEY": bool(
            config.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        ),
    }

    plugin_root = _resolve_plugin_root(config)
    effective_target = target_dir.resolve() if target_dir is not None else None
    root_hooks_present = (plugin_root / "hooks.json").is_file() or (
        config.assets_dir / "global_hooks.json"
    ).is_file()
    target_hooks_present = (
        (effective_target / ".agents" / "hooks.json").is_file()
        if effective_target is not None
        else (plugin_root / ".agents" / "hooks.json").is_file()
    )

    errors: list[str] = []
    warnings: list[str] = []
    if not home_writable:
        errors.append(f"AGENT_FORGE_HOME is not writable: {config.home_dir}")
    if not assets_exists:
        errors.append(f"Package assets directory missing: {config.assets_dir}")
    if not tools["uv"]:
        warnings.append("`uv` not found on PATH.")
    if not tools["git"]:
        warnings.append("`git` not found on PATH.")

    hooks_check: HookCheckResult | None = None
    if check_hooks:
        hooks_check = validate_plugin_hooks(
            plugin_root,
            target_dir=effective_target,
            assets_dir=config.assets_dir,
        )
        errors.extend(hooks_check.errors)
        warnings.extend(hooks_check.warnings)

    status = "error" if errors else ("warning" if warnings else "ok")
    return DoctorReport(
        status=status,
        os_name=platform.system(),
        os_release=platform.release(),
        python_version=platform.python_version(),
        python_executable=sys.executable,
        tools=tools,
        tool_paths=tool_paths,
        agent_forge_home=str(config.home_dir),
        agent_forge_home_exists=home_exists,
        agent_forge_home_writable=home_writable,
        package_assets_dir=str(config.assets_dir),
        package_assets_exists=assets_exists,
        target_dir=str(effective_target) if effective_target is not None else None,
        root_hooks_present=root_hooks_present,
        target_hooks_present=target_hooks_present,
        api_keys_configured=api_keys_configured,
        hooks_check=hooks_check,
        errors=errors,
        warnings=warnings,
    )
