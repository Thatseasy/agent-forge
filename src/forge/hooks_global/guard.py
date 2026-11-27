"""Stdlib-only PreToolUse safety guard (Lethal Trifecta & Secret/PII Write Guard)."""

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

SHELL_TOOLS = frozenset({"run_command", "execute_command", "bash", "sh", "terminal", "shell"})
WRITE_TOOLS = frozenset(
    {
        "write_file",
        "write_to_file",
        "edit_file",
        "replace_file_content",
        "multi_replace_file_content",
    }
)

OUTBOUND_NET_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:curl|wget|invoke-webrequest|iwr|invoke-restmethod|irm|nc|ncat|netcat|ssh|scp)"
    r"(?:\.exe)?\b"
    r"|\bpython(?:\d+(?:\.\d+)*)?(?:\.exe)?\b[^\n]*"
    r"(?:urllib(?:\.request)?|requests|httpx|aiohttp|http\.client)"
    r")"
)

SAFE_EXAMPLE_RE = re.compile(
    r"(?i)(?:^|[\\/])\.env\.(?:example|sample|template|schema)$|\.pub$"
)
SENSITIVE_WRITE_PATH_RE = re.compile(
    r"(?i)(?:^|[\\/])("
    r"\.env(?:\.[a-z0-9_.-]+)?"
    r"|secrets\.env"
    r"|[^\\/]*\.(?:pem|key|p12|pfx)"
    r"|id_(?:rsa|ed25519|ecdsa|dsa)(?:[._-][^\\/]*)?"
    r"|[^\\/]*credentials[^\\/]*"
    r"|\.npmrc|\.pypirc|\.netrc"
    r"|\.aws[\\/]credentials"
    r")$"
)

HIGH_CONFIDENCE_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9_-])("
    r"sk-proj-[A-Za-z0-9_-]{16,}"
    r"|sk-ant-[A-Za-z0-9_-]{16,}"
    r"|sk-[A-Za-z0-9_-]{16,}"
    r"|AIza[0-9A-Za-z_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9_]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}(?![0-9A-Z])"
    r"|pplx-[A-Za-z0-9_-]{16,}"
    r"|fc-[A-Za-z0-9_-]{16,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r")"
    r"|-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
)

IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){10,26}\b")
BEARER_RE = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._\-+/=]{20,})")
GENERIC_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(?:api_?key|secret_?key|access_?token|auth_?token|client_?secret|private_?key|password|passwd)"
    r"\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']"
)
DB_URI_PASS_RE = re.compile(
    r"(?i)\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis|amqp)"
    r"://[^:\s/]+:([^@\s]{8,})@[^\s]+"
)
PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:your[_-]|example|placeholder|changeme|dummy|sample|redacted|xxxx|\*+|\$\{|<)"
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
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] guard.py ({context}):\n{tb}\n"
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


def _load_session_state(conversation_id: str) -> dict[str, Any]:
    candidates = [
        _resolve_home() / "state" / "global_hooks_state.json",
        Path(tempfile.gettempdir()) / "agent-forge" / "state" / "global_hooks_state.json",
    ]
    for state_path in candidates:
        if not state_path.is_file():
            continue
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            entry = data.get(conversation_id)
            if isinstance(entry, dict):
                return entry
    return {}


def _extract_command(args: dict[str, Any]) -> str:
    for key in ("CommandLine", "command_line", "command", "cmd", "script"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _extract_write_target(args: dict[str, Any]) -> str:
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


def _extract_write_content(args: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "Content",
        "content",
        "Replacement",
        "replacement",
        "CodeContent",
        "code_content",
        "new_str",
        "NewString",
    ):
        val = args.get(key)
        if isinstance(val, str) and val:
            parts.append(val)
    for chunks_key in ("ReplacementChunks", "replacement_chunks"):
        chunks = args.get(chunks_key)
        if isinstance(chunks, list):
            for chunk in chunks:
                if isinstance(chunk, dict):
                    for ck in ("ReplacementContent", "replacement_content", "Replacement"):
                        cval = chunk.get(ck)
                        if isinstance(cval, str) and cval:
                            parts.append(cval)
    return "\n".join(parts)


def _is_sensitive_write_target(target: str) -> bool:
    if not target:
        return False
    cleaned = target.strip().replace("\\", "/")
    if SAFE_EXAMPLE_RE.search(cleaned):
        return False
    return bool(SENSITIVE_WRITE_PATH_RE.search(cleaned))


def _is_non_placeholder_secret_value(val: str) -> bool:
    if PLACEHOLDER_RE.match(val):
        return False
    if re.fullmatch(r"[A-Z][A-Z0-9_]+", val):
        return False
    return True


def _detect_suspicious_content(content: str) -> str:
    if IBAN_RE.search(content):
        return "possible IBAN"
    for match in BEARER_RE.finditer(content):
        if _is_non_placeholder_secret_value(match.group(1)):
            return "possible Bearer token"
    for match in GENERIC_SECRET_ASSIGN_RE.finditer(content):
        if _is_non_placeholder_secret_value(match.group(1)):
            return "possible hardcoded credential literal"
    for match in DB_URI_PASS_RE.finditer(content):
        if _is_non_placeholder_secret_value(match.group(1)):
            return "possible connection URI with embedded password"
    return ""


def _evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    raw_tc = payload.get("toolCall")
    tool_call: dict[str, Any] = raw_tc if isinstance(raw_tc, dict) else {}
    tool_name = str(tool_call.get("name") or payload.get("tool_name") or "").strip().lower()
    raw_args = tool_call.get("args")
    if isinstance(raw_args, dict):
        args: dict[str, Any] = raw_args
    else:
        raw_input = payload.get("tool_input")
        args = raw_input if isinstance(raw_input, dict) else {}
    conversation_id = (
        str(
            payload.get("conversationId")
            or payload.get("conversation_id")
            or payload.get("session_id")
            or "default"
        ).strip()
        or "default"
    )

    if tool_name in SHELL_TOOLS:
        cmd = _extract_command(args)
        session = _load_session_state(conversation_id)
        if session.get("sensitive_read") and cmd and OUTBOUND_NET_RE.search(cmd):
            return {
                "decision": "deny",
                "reason": (
                    "Blocked by Lethal Trifecta guard: sensitive/credential file was read in "
                    "this conversation before an outbound network command."
                ),
            }
        return {"decision": "allow"}

    if tool_name in WRITE_TOOLS:
        target = _extract_write_target(args)
        if _is_sensitive_write_target(target):
            return {
                "decision": "deny",
                "reason": (
                    f"Blocked by Secret Write Guard: writing directly to sensitive file "
                    f"'{target}' is not allowed."
                ),
            }
        content = _extract_write_content(args)
        if content:
            if HIGH_CONFIDENCE_SECRET_RE.search(content):
                return {
                    "decision": "deny",
                    "reason": (
                        "Blocked by Secret/PII Write Guard: high-confidence secret or "
                        "private key detected in write payload."
                    ),
                }
            suspicious = _detect_suspicious_content(content)
            if suspicious:
                return {
                    "decision": "ask",
                    "reason": (
                        f"Secret/PII Write Guard: {suspicious} detected in write payload. "
                        "Confirm this is intentional and non-sensitive before proceeding."
                    ),
                }
        return {"decision": "allow"}

    return {"decision": "allow"}


def main() -> None:
    try:
        payload = _read_stdin_json()
        _write_stdout_json(_evaluate(payload))
    except Exception:
        _log_exception("unhandled")
        _write_stdout_json({})


if __name__ == "__main__":
    main()
