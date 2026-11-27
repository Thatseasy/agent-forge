"""Plugin manifest validation and distribution wheel packaging (`agent-forge plugin`)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from pydantic import BaseModel, Field

from forge.config import ForgeConfig, estimate_tokens
from forge.doctor import validate_plugin_hooks

REQUIRED_PLUGIN_KEYS: tuple[str, ...] = (
    "name",
    "version",
    "description",
    "license",
    "icon",
    "commands",
    "skills",
    "agents",
    "rules",
    "hooks",
    "mcpServers",
)

PRIVATE_LEAK_RE = re.compile(
    r"(?i)(?:C:\\\\?Dev|C:\\\\?Users\\\\[^\\\s]+|/Users/[^/\s]+|OneDrive)"
)


class PluginValidationReport(BaseModel):
    """Structured validation result for an Antigravity plugin root."""

    valid: bool
    plugin_root: str
    name: str = ""
    version: str = ""
    command_count: int = 0
    skill_count: int = 0
    agent_count: int = 0
    rules_line_count: int = 0
    static_context_tokens: int = 0
    mcp_schema_tokens: int = 0
    hooks_valid: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PluginBuildReport(BaseModel):
    """Build artifact paths and verification metrics."""

    validation: PluginValidationReport
    out_dir: str
    wheel_path: str
    sdist_path: str | None = None
    wheel_bundled_assets_count: int = 0


def _check_utf8_no_bom_lf(path: Path) -> str | None:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return f"{path.name}: UTF-8 BOM detected"
    if b"\r\n" in raw:
        return f"{path.name}: CRLF line endings detected (LF required)"
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return f"{path.name}: invalid UTF-8 ({exc})"
    return None


def _compute_mcp_schema_tokens() -> int:
    try:
        from forge.mcp_server import mcp

        tools_payload: list[dict[str, Any]] = []
        tool_mgr = getattr(mcp, "_tool_manager", None)
        if tool_mgr is not None and hasattr(tool_mgr, "list_tools"):
            for tool in tool_mgr.list_tools():
                tools_payload.append(
                    {
                        "name": getattr(tool, "name", ""),
                        "description": getattr(tool, "description", ""),
                        "inputSchema": getattr(tool, "parameters", {}),
                    }
                )
        if not tools_payload:
            return 0
        return estimate_tokens(json.dumps({"tools": tools_payload}, separators=(",", ":")))
    except Exception:
        return 0


def validate_plugin(root_dir: Path) -> PluginValidationReport:
    """Validate `plugin.json`, UTF-8/LF hygiene, token budgets, hooks, and path safety."""
    root = root_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []

    manifest_path = root / "plugin.json"
    if not manifest_path.is_file():
        return PluginValidationReport(
            valid=False,
            plugin_root=str(root),
            errors=[f"Missing plugin.json at {manifest_path}"],
        )

    enc_err = _check_utf8_no_bom_lf(manifest_path)
    if enc_err:
        errors.append(enc_err)

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return PluginValidationReport(
            valid=False,
            plugin_root=str(root),
            errors=[f"Invalid plugin.json: {exc}"],
        )

    if not isinstance(manifest, dict):
        errors.append("plugin.json must be a JSON object")
        manifest = {}

    for key in REQUIRED_PLUGIN_KEYS:
        if not manifest.get(key):
            errors.append(f"plugin.json missing required key: {key}")

    for path_key in ("icon", "commands", "skills", "agents", "rules", "hooks", "mcpServers"):
        raw_val = manifest.get(path_key)
        if not isinstance(raw_val, str) or not raw_val.strip():
            continue
        p = Path(raw_val)
        if (
            p.is_absolute()
            or PurePosixPath(raw_val).is_absolute()
            or PureWindowsPath(raw_val).is_absolute()
        ):
            errors.append(f"plugin.json `{path_key}` must be a relative path: {raw_val!r}")
            continue
        resolved = (root / p).resolve()
        if not resolved.is_relative_to(root):
            errors.append(f"plugin.json `{path_key}` escapes plugin root: {raw_val!r}")
            continue
        if not resolved.exists():
            errors.append(f"plugin.json `{path_key}` target missing: {resolved}")

    # Check mcp_config.json UTF-8 LF and JSON syntax
    mcp_cfg = root / str(manifest.get("mcpServers") or "mcp_config.json")
    if mcp_cfg.is_file():
        mcp_err = _check_utf8_no_bom_lf(mcp_cfg)
        if mcp_err:
            errors.append(f"mcp_config.json: {mcp_err}")
        try:
            mcp_data = json.loads(mcp_cfg.read_text(encoding="utf-8"))
            if "mcpServers" not in mcp_data:
                errors.append("mcp_config.json missing top-level `mcpServers` object")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"mcp_config.json invalid JSON: {exc}")

    # Commands, skills, agents, rules counts & hygiene
    cmds_dir = root / str(manifest.get("commands") or "commands")
    cmd_files = sorted(cmds_dir.glob("forge-*.md")) if cmds_dir.is_dir() else []
    if len(cmd_files) < 8:
        errors.append(f"Expected 8 commands/forge-*.md files, found {len(cmd_files)}")

    skills_dir = root / str(manifest.get("skills") or "skills")
    skill_files = sorted(skills_dir.glob("*/SKILL.md")) if skills_dir.is_dir() else []
    if not 1 <= len(skill_files) <= 3:
        errors.append(f"Expected 1..3 bundled core skills under skills/, found {len(skill_files)}")

    agents_dir = root / str(manifest.get("agents") or "agents")
    agent_files = sorted(agents_dir.glob("*.md")) if agents_dir.is_dir() else []
    if len(agent_files) < 3:
        errors.append(f"Expected >= 3 agent blueprints under agents/, found {len(agent_files)}")

    rules_file = root / str(manifest.get("rules") or "rules/AGENTS.md")
    rules_lines = 0
    static_text_parts: list[str] = []
    if rules_file.is_file():
        rules_text = rules_file.read_text(encoding="utf-8", errors="replace")
        rules_lines = len(rules_text.splitlines())
        if rules_lines >= 60:
            errors.append(f"rules/AGENTS.md must stay < 60 lines (got {rules_lines})")
        static_text_parts.append(rules_text)

    for sf in skill_files:
        static_text_parts.append(sf.read_text(encoding="utf-8", errors="replace"))

    static_tokens = estimate_tokens("\n".join(static_text_parts))
    if static_tokens >= 1500:
        errors.append(
            f"Static context budget exceeded: {static_tokens} tokens (>= 1500)"
        )

    mcp_tokens = _compute_mcp_schema_tokens()
    if mcp_tokens > 1200:
        errors.append(f"MCP tool schema budget exceeded: {mcp_tokens} tokens (> 1200)")

    # Validate LF/UTF-8 and absence of private local paths across all plugin files
    plugin_text_files = [
        manifest_path,
        *([mcp_cfg] if mcp_cfg.is_file() else []),
        *([rules_file] if rules_file.is_file() else []),
        *cmd_files,
        *skill_files,
        *agent_files,
    ]
    logo_path = root / str(manifest.get("icon") or "assets/logo.svg")
    if logo_path.is_file():
        plugin_text_files.append(logo_path)

    for fpath in plugin_text_files:
        if fpath.is_file():
            f_err = _check_utf8_no_bom_lf(fpath)
            if f_err:
                errors.append(f"{fpath.relative_to(root).as_posix()}: {f_err}")
            text = fpath.read_text(encoding="utf-8", errors="replace")
            if PRIVATE_LEAK_RE.search(text):
                errors.append(
                    f"Private/local path leak detected in {fpath.relative_to(root).as_posix()}"
                )

    hooks_res = validate_plugin_hooks(root)
    if not hooks_res.valid:
        errors.extend(hooks_res.errors)
    warnings.extend(hooks_res.warnings)

    return PluginValidationReport(
        valid=(not errors),
        plugin_root=str(root),
        name=str(manifest.get("name") or ""),
        version=str(manifest.get("version") or ""),
        command_count=len(cmd_files),
        skill_count=len(skill_files),
        agent_count=len(agent_files),
        rules_line_count=rules_lines,
        static_context_tokens=static_tokens,
        mcp_schema_tokens=mcp_tokens,
        hooks_valid=hooks_res.valid,
        errors=errors,
        warnings=warnings,
    )


def build_plugin(
    root_dir: Path,
    *,
    out_dir: Path | None = None,
    config: ForgeConfig | None = None,
) -> PluginBuildReport:
    """Validate plugin invariants and build `.whl` / `.tar.gz` artifacts cleanly."""
    _ = config
    root = root_dir.resolve()
    validation = validate_plugin(root)
    if not validation.valid:
        raise ValueError("Plugin validation failed:\n- " + "\n- ".join(validation.errors))

    dest_dir = (out_dir or (root / "dist")).resolve()
    if dest_dir.exists():
        for existing in dest_dir.glob("*.whl"):
            existing.unlink()
        for existing in dest_dir.glob("*.tar.gz"):
            existing.unlink()
    dest_dir.mkdir(parents=True, exist_ok=True)

    uv_bin = shutil.which("uv")
    if uv_bin:
        cmd = [uv_bin, "build", "--wheel", "--sdist", "--out-dir", str(dest_dir)]
    else:
        cmd = [sys.executable, "-m", "build", "--outdir", str(dest_dir)]

    proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=180, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Plugin package build failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout}"
        )

    wheels = sorted(dest_dir.glob("*.whl"))
    sdists = sorted(dest_dir.glob("*.tar.gz"))
    if not wheels:
        raise FileNotFoundError(f"No .whl file produced in {dest_dir}")

    wheel_path = wheels[-1]
    bundled_assets = 0
    with zipfile.ZipFile(wheel_path, "r") as zf:
        asset_entries = [name for name in zf.namelist() if name.startswith("forge/assets/")]
        bundled_assets = len(asset_entries)
        if bundled_assets == 0:
            raise ValueError(
                f"Built wheel {wheel_path.name} is missing `forge/assets/` package data"
            )

    return PluginBuildReport(
        validation=validation,
        out_dir=str(dest_dir),
        wheel_path=str(wheel_path),
        sdist_path=str(sdists[-1]) if sdists else None,
        wheel_bundled_assets_count=bundled_assets,
    )
