"""Pluggable runner adapters for isolated Stage 3 skill benchmarking."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from forge.benchmark.models import EvalTask, TrialTelemetry
from forge.benchmark.scoring import estimate_cost_usd
from forge.benchmark.skill_pack import resolve_skill_dirs
from forge.config import ForgeConfig, write_text_utf8_lf


@runtime_checkable
class RunnerAdapter(Protocol):
    """Execution adapter interface for running one isolated benchmark trial."""

    name: str

    def run(
        self,
        worktree_dir: Path,
        arm: str,
        task: EvalTask,
        skill_dir: Path | None,
        trial_index: int,
        model: str = "default",
    ) -> TrialTelemetry:
        """Execute one task trial inside `worktree_dir` and return raw telemetry."""


def agy_supports_output_format() -> bool:
    """Detect dynamically whether the installed `agy` CLI supports `--output-format`."""
    exe = shutil.which("agy")
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        return "--output-format" in (proc.stdout + proc.stderr)
    except (OSError, subprocess.SubprocessError):
        return False


def has_direct_api_key(config: ForgeConfig) -> bool:
    """Return True if any supported LLM API key is configured for DirectApiRunnerAdapter."""
    return bool(config.openai_api_key or config.gemini_api_key or config.anthropic_api_key)


def _resolve_model_and_key(config: ForgeConfig, requested_model: str) -> tuple[str, str]:
    req = requested_model.strip()
    if req and req != "default":
        lower = req.lower()
        if "claude" in lower or lower.startswith("anthropic/"):
            model_id = req if "/" in req else f"anthropic/{req}"
            return model_id, config.anthropic_api_key
        if "gemini" in lower:
            model_id = req if "/" in req else f"gemini/{req}"
            return model_id, config.gemini_api_key
        model_id = req if "/" in req else f"openai/{req}"
        return model_id, config.openai_api_key

    if config.openai_api_key:
        return "openai/gpt-5.4-mini", config.openai_api_key
    if config.gemini_api_key:
        return f"gemini/{config.video_lite_model}", config.gemini_api_key
    if config.anthropic_api_key:
        return "anthropic/claude-sonnet-4-20250514", config.anthropic_api_key
    return "", ""


class DryRunRunnerAdapter:
    """Deterministic local simulation adapter for 0-USD pipeline smoke & unit tests."""

    name: str = "dry-run"

    def run(
        self,
        worktree_dir: Path,
        arm: str,
        task: EvalTask,
        skill_dir: Path | None,
        trial_index: int,
        model: str = "default",
    ) -> TrialTelemetry:
        cli_file = worktree_dir / "cli_app.py"
        if arm == "vanilla" and task.edge_case and trial_index % 2 == 0:
            if cli_file.is_file():
                text = cli_file.read_text(encoding="utf-8")
                cli_file.write_text(
                    text.replace("if not cleaned or cleaned in seen:", "if cleaned in seen:"),
                    encoding="utf-8",
                    newline="\n",
                )
            in_tok, out_tok, cache_tok, steps, lat = 2600, 620, 1200, 7, 1.4
        elif arm == "hype":
            if cli_file.is_file():
                text = cli_file.read_text(encoding="utf-8")
                cli_file.write_text(
                    text
                    + "\n# Refactored via enterprise architecture checklist\n"
                    + "# Added telemetry wrapper placeholder\n"
                    + "# Added configuration factory placeholder\n",
                    encoding="utf-8",
                    newline="\n",
                )
            in_tok, out_tok, cache_tok, steps, lat = 5800, 1450, 1600, 12, 3.1
        elif arm == "underdog":
            if cli_file.is_file():
                text = cli_file.read_text(encoding="utf-8")
                cli_file.write_text(
                    text + "\n# Lean deterministic helper check\n",
                    encoding="utf-8",
                    newline="\n",
                )
            in_tok, out_tok, cache_tok, steps, lat = 2100, 480, 1750, 5, 1.1
        else:
            in_tok, out_tok, cache_tok, steps, lat = 2400, 540, 1400, 6, 1.3

        cost = estimate_cost_usd(in_tok, out_tok, cache_tok)
        return TrialTelemetry(
            arm=arm,
            task_id=task.id,
            trial_index=trial_index,
            model=model,
            passed=True,
            stage1_passed=True,
            infra_ok=True,
            input_tokens=in_tok,
            output_tokens=out_tok,
            thinking_tokens=int(out_tok * 0.25),
            cache_read_input_tokens=cache_tok,
            tool_calls=steps,
            latency_s=lat,
            cost_usd=cost,
        )


class AgyCliRunnerAdapter:
    """Headless Antigravity (`agy -p`) worktree trial adapter."""

    name: str = "agy"

    def run(
        self,
        worktree_dir: Path,
        arm: str,
        task: EvalTask,
        skill_dir: Path | None,
        trial_index: int,
        model: str = "default",
    ) -> TrialTelemetry:
        exe = shutil.which("agy")
        if not exe:
            return TrialTelemetry(
                arm=arm,
                task_id=task.id,
                trial_index=trial_index,
                model=model,
                passed=False,
                stage1_passed=False,
                infra_ok=False,
                error="agy CLI not found in PATH",
            )
        cmd = [exe, "-p", task.prompt, "--dangerously-skip-permissions"]
        if model and model != "default":
            cmd.extend(["--model", model])
        if agy_supports_output_format():
            cmd.extend(["--output-format", "json"])
        try:
            proc = subprocess.run(
                cmd,
                cwd=worktree_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return TrialTelemetry(
                arm=arm,
                task_id=task.id,
                trial_index=trial_index,
                model=model,
                passed=False,
                stage1_passed=False,
                infra_ok=False,
                error=str(exc),
            )

        in_tok, out_tok, cache_tok = _parse_agy_usage(proc.stdout)
        return TrialTelemetry(
            arm=arm,
            task_id=task.id,
            trial_index=trial_index,
            model=model,
            passed=proc.returncode == 0,
            stage1_passed=proc.returncode == 0,
            infra_ok=proc.returncode == 0,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_input_tokens=cache_tok,
            cost_usd=estimate_cost_usd(in_tok, out_tok, cache_tok),
            error="" if proc.returncode == 0 else proc.stderr[:300],
        )


class DockerRunnerAdapter:
    """Air-gapped (`--network none`) Docker sandbox verifier adapter."""

    name: str = "docker"

    def run(
        self,
        worktree_dir: Path,
        arm: str,
        task: EvalTask,
        skill_dir: Path | None,
        trial_index: int,
        model: str = "default",
    ) -> TrialTelemetry:
        docker_exe = shutil.which("docker")
        if not docker_exe:
            return TrialTelemetry(
                arm=arm,
                task_id=task.id,
                trial_index=trial_index,
                model=model,
                passed=False,
                stage1_passed=False,
                infra_ok=False,
                error="Docker not installed (see HUMAN_HANDOFF.md)",
            )
        cmd = [
            docker_exe,
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{worktree_dir}:/workspace",
            "agent-forge-eval:latest",
            "pytest",
            "-q",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            return TrialTelemetry(
                arm=arm,
                task_id=task.id,
                trial_index=trial_index,
                model=model,
                passed=proc.returncode == 0,
                stage1_passed=proc.returncode == 0,
                infra_ok=True,
                error="" if proc.returncode == 0 else proc.stderr[:300],
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return TrialTelemetry(
                arm=arm,
                task_id=task.id,
                trial_index=trial_index,
                model=model,
                passed=False,
                stage1_passed=False,
                infra_ok=False,
                error=str(exc),
            )


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List relative file paths inside the isolated workspace.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file inside the isolated workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Relative file path."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write complete UTF-8 content (LF line endings) to a workspace file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path."},
                    "content": {"type": "string", "description": "Full updated file text."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run local verification command (`pytest -q`, `ruff`, or `mypy`).",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Allowlisted test/lint command, e.g. 'pytest -q'.",
                    }
                },
                "required": [],
            },
        },
    },
]


class DirectApiRunnerAdapter:
    """Direct LiteLLM tool-loop adapter executing inside an isolated git worktree."""

    name: str = "direct"

    def __init__(
        self,
        config: ForgeConfig,
        completion_fn: Callable[..., Any] | None = None,
        max_turns: int = 8,
    ) -> None:
        self.config = config
        self.completion_fn = completion_fn
        self.max_turns = max(1, max_turns)

    def run(
        self,
        worktree_dir: Path,
        arm: str,
        task: EvalTask,
        skill_dir: Path | None,
        trial_index: int,
        model: str = "default",
    ) -> TrialTelemetry:
        resolved_model, api_key = _resolve_model_and_key(self.config, model)
        if self.completion_fn is None and not api_key:
            return TrialTelemetry(
                arm=arm,
                task_id=task.id,
                trial_index=trial_index,
                model=model,
                passed=False,
                stage1_passed=False,
                infra_ok=False,
                error="Missing LLM API key for DirectApiRunnerAdapter (check .env).",
            )

        effective_model = resolved_model or (model if model != "default" else "mock/direct")
        caller = self.completion_fn or self._litellm_call
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _build_system_prompt(skill_dir)},
            {
                "role": "user",
                "content": (
                    f"Task ({task.id}): {task.prompt}\n\n"
                    f"Preferred verifier command: {task.verifier_cmd}\n"
                    "Inspect files, apply minimal clean edits with `write_file`, "
                    "verify with `run_tests` (`pytest` and `ruff check --select E,F,I,W,UP,B .`), "
                    "then summarize concisely."
                ),
            },
        ]

        total_in = 0
        total_out = 0
        total_cache = 0
        total_tools = 0
        total_cost = 0.0
        t0 = time.perf_counter()

        try:
            for _turn in range(self.max_turns):
                resp = caller(
                    model=effective_model,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    api_key=api_key,
                )
                msg, in_t, out_t, cache_t, resp_cost = _extract_response_parts(resp)
                total_in += in_t
                total_out += out_t
                total_cache += cache_t
                total_cost += (
                    resp_cost
                    if resp_cost is not None
                    else estimate_cost_usd(in_t, out_t, cache_t, model=effective_model)
                )
                messages.append(msg)

                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    break

                for tc in tool_calls:
                    total_tools += 1
                    tc_id = str(_read_field(tc, "id") or f"call_{total_tools}")
                    fn_obj = _read_field(tc, "function") or {}
                    fn_name = str(_read_field(fn_obj, "name") or "")
                    raw_args = _read_field(fn_obj, "arguments") or "{}"
                    result_text = _dispatch_tool(worktree_dir, fn_name, raw_args)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": result_text,
                        }
                    )
        except Exception as exc:  # noqa: BLE001
            return TrialTelemetry(
                arm=arm,
                task_id=task.id,
                trial_index=trial_index,
                model=effective_model,
                passed=False,
                stage1_passed=False,
                infra_ok=False,
                input_tokens=total_in,
                output_tokens=total_out,
                cache_read_input_tokens=total_cache,
                tool_calls=total_tools,
                latency_s=round(time.perf_counter() - t0, 3),
                cost_usd=round(total_cost, 6),
                error=str(exc)[:300],
            )

        return TrialTelemetry(
            arm=arm,
            task_id=task.id,
            trial_index=trial_index,
            model=effective_model,
            passed=True,
            stage1_passed=True,
            infra_ok=True,
            input_tokens=total_in,
            output_tokens=total_out,
            cache_read_input_tokens=total_cache,
            tool_calls=total_tools,
            latency_s=round(time.perf_counter() - t0, 3),
            cost_usd=round(total_cost, 6),
        )

    @staticmethod
    def _litellm_call(
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        api_key: str,
    ) -> Any:
        import litellm

        return litellm.completion(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            api_key=api_key or None,
            timeout=45,
        )


def resolve_runner_adapter(
    config: ForgeConfig,
    *,
    isolation: str = "worktree",
    runner: str = "auto",
    dry_run: bool = False,
) -> RunnerAdapter:
    """Select concrete RunnerAdapter from runner mode, isolation mode, and config."""
    if dry_run or runner == "dry-run":
        return DryRunRunnerAdapter()
    if isolation == "docker" or runner == "docker":
        return DockerRunnerAdapter()
    if runner == "direct":
        return DirectApiRunnerAdapter(config)
    if runner == "agy":
        return AgyCliRunnerAdapter()
    # auto mode: prefer DirectApi when any LLM key exists, else structured agy CLI
    if has_direct_api_key(config):
        return DirectApiRunnerAdapter(config)
    if agy_supports_output_format():
        return AgyCliRunnerAdapter()
    return DirectApiRunnerAdapter(config)


def _build_system_prompt(skill_dir: Path | None) -> str:
    base = (
        "You are an isolated software engineering agent working inside a disposable git worktree.\n"
        "Use the provided tools (`list_files`, `read_file`, `write_file`, `run_tests`) cleanly, "
        "write UTF-8 without BOM and LF line endings, keep lines <= 100 chars, preserve "
        "unrelated functions and docstrings, keep functions minimal and typed, "
        "and avoid unnecessary churn."
    )
    if skill_dir is None:
        return base
    modules = resolve_skill_dirs(skill_dir)
    if not modules:
        return base
    blocks: list[str] = []
    for mod in modules:
        skill_text = (mod / "SKILL.md").read_text(encoding="utf-8", errors="replace").strip()
        blocks.append(f'<active_skill name="{mod.name}">\n{skill_text}\n</active_skill>')
    return f"{base}\n\n" + "\n\n".join(blocks)


def _resolve_jailed_path(worktree_dir: Path, raw_path: str) -> Path:
    rel = (raw_path or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/"):
        raise ValueError(f"Path must be relative inside workspace: {raw_path!r}")
    root = worktree_dir.resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"Path traversal outside workspace blocked: {raw_path!r}")
    if ".git" in target.relative_to(root).parts:
        raise ValueError("Direct access to .git metadata is blocked.")
    return target


def _dispatch_tool(worktree_dir: Path, name: str, raw_args: Any) -> str:
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
    except (json.JSONDecodeError, TypeError):
        return "ERROR: invalid JSON tool arguments"

    try:
        if name == "list_files":
            files = sorted(
                p.relative_to(worktree_dir).as_posix()
                for p in worktree_dir.rglob("*")
                if p.is_file()
                and not any(part in {".git", "__pycache__", ".pytest_cache"} for part in p.parts)
            )
            return json.dumps(files, ensure_ascii=False)

        if name == "read_file":
            target = _resolve_jailed_path(worktree_dir, str(args.get("path") or ""))
            if not target.is_file():
                return f"ERROR: file not found: {args.get('path')}"
            return target.read_text(encoding="utf-8", errors="replace")

        if name == "write_file":
            target = _resolve_jailed_path(worktree_dir, str(args.get("path") or ""))
            write_text_utf8_lf(target, str(args.get("content") or ""))
            return f"OK: wrote {target.relative_to(worktree_dir).as_posix()}"

        if name == "run_tests":
            return _run_allowlisted_verifier(worktree_dir, str(args.get("command") or "pytest -q"))

        return f"ERROR: unknown tool {name!r}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def _run_allowlisted_verifier(worktree_dir: Path, command: str) -> str:
    if any(ch in command for ch in (";", "|", "&", ">", "<", "\n", "\r")):
        return "ERROR: shell chaining characters are blocked in run_tests."
    tokens = shlex.split(command.strip() or "pytest -q")
    if not tokens:
        tokens = ["pytest", "-q"]
    head = tokens[0].lower()
    if head in {"pytest", "ruff", "mypy"}:
        cmd = [sys.executable, "-m", head, *tokens[1:]]
    elif head in {"python", "python3"} and len(tokens) >= 3 and tokens[1] == "-m" and tokens[2] in {
        "pytest",
        "ruff",
        "mypy",
    }:
        cmd = [sys.executable, "-m", tokens[2], *tokens[3:]]
    else:
        return "ERROR: only pytest, ruff, or mypy commands are allowlisted."

    proc = subprocess.run(
        cmd,
        cwd=worktree_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    out = (proc.stdout + proc.stderr).strip()
    return f"exit_code={proc.returncode}\n{out[:2000]}"


def _read_field(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _extract_response_parts(resp: Any) -> tuple[dict[str, Any], int, int, int, float | None]:
    choices = _read_field(resp, "choices") or []
    first_choice = choices[0] if choices else {}
    raw_msg = _read_field(first_choice, "message") or {}

    content = _read_field(raw_msg, "content") or ""
    raw_tcs = _read_field(raw_msg, "tool_calls") or []
    norm_tcs: list[dict[str, Any]] = []
    for idx, tc in enumerate(raw_tcs, start=1):
        fn = _read_field(tc, "function") or {}
        norm_tcs.append(
            {
                "id": str(_read_field(tc, "id") or f"call_{idx}"),
                "type": "function",
                "function": {
                    "name": str(_read_field(fn, "name") or ""),
                    "arguments": _read_field(fn, "arguments") or "{}",
                },
            }
        )
    msg_dict: dict[str, Any] = {"role": "assistant", "content": content}
    if norm_tcs:
        msg_dict["tool_calls"] = norm_tcs

    usage = _read_field(resp, "usage") or {}
    in_tok = int(_read_field(usage, "prompt_tokens") or _read_field(usage, "input_tokens") or 0)
    out_tok = int(
        _read_field(usage, "completion_tokens") or _read_field(usage, "output_tokens") or 0
    )
    details = _read_field(usage, "prompt_tokens_details") or {}
    cache_tok = int(
        _read_field(usage, "cache_read_input_tokens")
        or _read_field(details, "cached_tokens")
        or 0
    )

    cost_val: float | None = None
    try:
        import litellm

        computed = litellm.completion_cost(completion_response=resp)
        if computed is not None and float(computed) > 0.0:
            cost_val = float(computed)
    except Exception:  # noqa: BLE001
        cost_val = None
    return msg_dict, in_tok, out_tok, cache_tok, cost_val


def _parse_agy_usage(stdout_text: str) -> tuple[int, int, int]:
    try:
        data = json.loads(stdout_text)
        usage = data.get("usage") if isinstance(data, dict) else {}
        if isinstance(usage, dict):
            return (
                int(usage.get("input_tokens") or 0),
                int(usage.get("output_tokens") or 0),
                int(usage.get("cache_read_input_tokens") or 0),
            )
    except json.JSONDecodeError:
        pass
    return (max(500, len(stdout_text) // 4), 250, 0)
