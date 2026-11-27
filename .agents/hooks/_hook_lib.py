"""Shared stdlib-only helpers for Antigravity lifecycle hooks."""

from __future__ import annotations

import fnmatch
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

WRITE_TOOLS = frozenset({"write_file", "edit_file", "write_to_file", "replace_file_content"})
READ_TOOLS = frozenset({"read_file", "view_file"})
SHELL_TOOLS = frozenset({"run_command"})


def resolve_repo_root() -> Path:
    """Resolve repository root containing `.agents/`, overridable via FORGE_HOOK_ROOT."""
    override = os.environ.get("FORGE_HOOK_ROOT", "").strip()
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[2]


def read_stdin_json() -> dict[str, Any]:
    """Parse camelCase hook payload from stdin without raising on empty input."""
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def write_stdout_json(payload: dict[str, Any]) -> None:
    """Write UTF-8 JSON response to stdout."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _state_path(root: Path) -> Path:
    return root / ".agents" / "state" / "session_state.json"


def _metrics_path(root: Path) -> Path:
    return root / ".agents" / "state" / "metrics.jsonl"


def load_all_state(root: Path) -> dict[str, Any]:
    """Load full session state dictionary from `.agents/state/session_state.json`."""
    path = _state_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def get_session(all_state: dict[str, Any], conversation_id: str) -> dict[str, Any]:
    """Return mutable session dict for a conversationId with initialized defaults."""
    entry = all_state.setdefault(conversation_id, {})
    if not isinstance(entry, dict):
        entry = {}
        all_state[conversation_id] = entry
    entry.setdefault("mandatory_pending", [])
    entry.setdefault("warned_skills", [])
    entry.setdefault("loaded_skills", [])
    entry.setdefault("sensitive_read", False)
    entry.setdefault("pii_read", False)
    entry.setdefault("walkthrough_completed", False)
    entry.setdefault("owned_paths", [])
    entry.setdefault("forbidden_paths", [])
    entry.setdefault("tests_ran", False)
    entry.setdefault("stop_nudged", False)
    entry.setdefault("last_prompt_hash", "")
    return entry


def save_all_state(root: Path, state: dict[str, Any]) -> None:
    """Atomically write session state as UTF-8 without BOM and LF line endings."""
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f".tmp.{os.getpid()}")
    tmp_path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp_path, path)


def append_metric(root: Path, record: dict[str, Any]) -> None:
    """Append a single telemetry JSON line to `.agents/state/metrics.jsonl`."""
    path = _metrics_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    record.setdefault("timestamp", datetime.now(UTC).isoformat())
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)


def normalize_rel_path(raw_path: str, root: Path) -> str:
    """Normalize Windows/POSIX paths relative to `root` using forward slashes."""
    if not raw_path:
        return ""
    cleaned = raw_path.strip().replace("\\", "/")
    try:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            rel = candidate.resolve().relative_to(root.resolve())
            return rel.as_posix().lower()
    except (OSError, ValueError):
        pass
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lower()


def extract_tool_target(tool_call: dict[str, Any]) -> str:
    """Extract target file path or command line from camelCase toolCall args."""
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return ""
    for key in (
        "Path",
        "path",
        "TargetFile",
        "target_file",
        "AbsolutePath",
        "file_path",
        "CommandLine",
        "command",
    ):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def extract_tool_content(tool_call: dict[str, Any]) -> str:
    """Extract inline file/patch content from write or edit tool arguments."""
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return ""
    parts: list[str] = []
    for key in ("Content", "content", "Replacement", "replacement", "CodeContent", "new_str"):
        val = args.get(key)
        if isinstance(val, str) and val:
            parts.append(val)
    return "\n".join(parts)


def load_ownership_policy(
    root: Path,
    conversation_id: str,
    session: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Return `(owned_paths, forbidden_paths)` merged from session state and swarm manifest."""
    owned = [str(p) for p in session.get("owned_paths", []) if isinstance(p, str) and p.strip()]
    forbidden = [
        str(p) for p in session.get("forbidden_paths", []) if isinstance(p, str) and p.strip()
    ]
    manifest_path = root / ".agents" / "state" / "swarm_ownership.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entry = manifest.get(conversation_id) if isinstance(manifest, dict) else None
            if isinstance(entry, dict):
                owned.extend(str(p) for p in entry.get("owned_paths", []) if str(p).strip())
                forbidden.extend(
                    str(p) for p in entry.get("forbidden_paths", []) if str(p).strip()
                )
        except (OSError, json.JSONDecodeError):
            pass
    return owned, forbidden


def path_matches_patterns(norm_rel: str, patterns: list[str], root: Path) -> bool:
    """Return True if normalized relative path matches any glob or directory prefix pattern."""
    if not norm_rel or not patterns:
        return False
    for raw_pat in patterns:
        norm_pat = normalize_rel_path(raw_pat, root)
        if not norm_pat:
            continue
        if fnmatch.fnmatch(norm_rel, norm_pat):
            return True
        prefix = norm_pat.rstrip("/*")
        if prefix and (norm_rel == prefix or norm_rel.startswith(prefix + "/")):
            return True
    return False
