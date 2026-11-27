"""Stdlib-only PostToolUse tracker for global safety hooks (Lethal Trifecta state)."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

READ_TOOLS = frozenset({"read_file", "view_file"})

SAFE_EXAMPLE_RE = re.compile(
    r"(?i)(?:^|[\\/])\.env\.(?:example|sample|template|schema)$|\.pub$"
)
SENSITIVE_PATH_RE = re.compile(
    r"(?i)(?:^|[\\/])("
    r"\.env(?:\.[a-z0-9_.-]+)?"
    r"|secrets\.env"
    r"|[^\\/]*\.(?:pem|key|p12|pfx)"
    r"|id_(?:rsa|ed25519|ecdsa|dsa)(?:[._-][^\\/]*)?"
    r"|[^\\/]*credentials[^\\/]*"
    r"|\.npmrc|\.pypirc|\.netrc"
    r"|\.aws[\\/]credentials"
    r"|\.ssh[\\/][^\\/]+"
    r")$"
)


def _resolve_home() -> Path:
    env_home = os.environ.get("AGENT_FORGE_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser()
    try:
        home = Path.home()
        if str(home).strip() not in {"", "/"}:
            return home / ".agent-forge"
    except (RuntimeError, OSError):
        pass
    return Path(tempfile.gettempdir()) / "agent-forge"


def _log_exception(context: str) -> None:
    tb = traceback.format_exc()
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] tracker.py ({context}):\n{tb}\n"
    for base in (_resolve_home(), Path(tempfile.gettempdir()) / "agent-forge"):
        try:
            log_path = base / "logs" / "hooks.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
            return
        except OSError:
            continue


def _read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _write_stdout_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _extract_target(args: dict[str, Any]) -> str:
    for key in (
        "Path",
        "path",
        "TargetFile",
        "target_file",
        "AbsolutePath",
        "absolute_path",
        "FilePath",
        "file_path",
    ):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _is_sensitive_read(target: str) -> bool:
    if not target:
        return False
    cleaned = target.strip().replace("\\", "/")
    if SAFE_EXAMPLE_RE.search(cleaned):
        return False
    return bool(SENSITIVE_PATH_RE.search(cleaned))


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state_atomic(state: dict[str, Any]) -> None:
    serialized = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    candidates = [
        _resolve_home() / "state" / "global_hooks_state.json",
        Path(tempfile.gettempdir()) / "agent-forge" / "state" / "global_hooks_state.json",
    ]
    for state_path in candidates:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = state_path.with_suffix(f".tmp.{os.getpid()}")
            tmp_path.write_text(serialized, encoding="utf-8", newline="\n")
            os.replace(tmp_path, state_path)
            return
        except OSError:
            continue


def _run() -> None:
    payload = _read_stdin_json()
    raw_tc = payload.get("toolCall")
    tool_call: dict[str, Any] = raw_tc if isinstance(raw_tc, dict) else {}
    tool_name = str(tool_call.get("name") or payload.get("tool_name") or "").strip().lower()
    if tool_name not in READ_TOOLS:
        _write_stdout_json({})
        return

    if payload.get("error"):
        _write_stdout_json({})
        return

    raw_args = tool_call.get("args")
    if isinstance(raw_args, dict):
        args: dict[str, Any] = raw_args
    else:
        raw_input = payload.get("tool_input")
        args = raw_input if isinstance(raw_input, dict) else {}
    target = _extract_target(args)
    if _is_sensitive_read(target):
        conversation_id = (
            str(
                payload.get("conversationId")
                or payload.get("conversation_id")
                or payload.get("session_id")
                or "default"
            ).strip()
            or "default"
        )
        primary = _resolve_home() / "state" / "global_hooks_state.json"
        fallback = (
            Path(tempfile.gettempdir()) / "agent-forge" / "state" / "global_hooks_state.json"
        )
        state = _load_state(primary) or _load_state(fallback)
        entry = state.get(conversation_id)
        if not isinstance(entry, dict):
            entry = {}
        files = entry.get("sensitive_files")
        if not isinstance(files, list):
            files = []
        if target not in files:
            files.append(target)
        entry["sensitive_read"] = True
        entry["sensitive_files"] = files[-20:]
        entry["updated_at"] = time.time()
        state[conversation_id] = entry
        _save_state_atomic(state)

    _write_stdout_json({})


def main() -> None:
    try:
        _run()
    except Exception:
        _log_exception("unhandled")
        _write_stdout_json({})


if __name__ == "__main__":
    main()
