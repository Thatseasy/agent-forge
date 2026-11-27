"""PostToolUse hook: tracks sensitive reads, skill activations, QA walkthroughs, and metrics."""

from __future__ import annotations

import re
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from _hook_lib import (  # noqa: E402
    READ_TOOLS,
    SHELL_TOOLS,
    WRITE_TOOLS,
    append_metric,
    extract_tool_target,
    get_session,
    load_all_state,
    normalize_rel_path,
    read_stdin_json,
    resolve_repo_root,
    save_all_state,
    write_stdout_json,
)

SENSITIVE_FILE_RE = re.compile(
    r"(?i)(^|[\\/])(\.env(\.[a-z0-9_-]+)?|.*\.pem|.*\.key|id_rsa.*|.*secret.*|.*token.*|\.ssh[\\/].*)$"
)
RAW_PII_FILE_RE = re.compile(
    r"(?i)(?:^|[\\/])(?:raw_sales|raw_corpus|raw_transcripts|"
    r"close_export[^\\/]*\.csv|customer_data[^\\/]*)(?:$|[\\/])"
)
SAFE_EXAMPLE_RE = re.compile(r"(?i)(\.env\.example|skill-rules\.json|tests[\\/]|sanitized[\\/])")


def _is_sensitive_target(target: str) -> bool:
    if not target or SAFE_EXAMPLE_RE.search(target):
        return False
    return bool(SENSITIVE_FILE_RE.search(target.strip()))


def _is_raw_pii_target(target: str) -> bool:
    if not target or SAFE_EXAMPLE_RE.search(target):
        return False
    return bool(RAW_PII_FILE_RE.search(target.strip()))


def main() -> None:
    payload = read_stdin_json()
    root = resolve_repo_root()
    conversation_id = str(payload.get("conversationId") or "default")
    tool_call = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    tool_name = str(tool_call.get("name") or "").lower()
    target = extract_tool_target(tool_call)
    norm_target = normalize_rel_path(target, root)
    error = str(payload.get("error") or "")

    all_state = load_all_state(root)
    session = get_session(all_state, conversation_id)
    changed = False
    skill_loaded = ""

    # Track sensitive and raw PII reads
    if tool_name in (READ_TOOLS | SHELL_TOOLS) and _is_sensitive_target(target):
        if not session.get("sensitive_read"):
            session["sensitive_read"] = True
            changed = True
    if tool_name in (READ_TOOLS | SHELL_TOOLS) and _is_raw_pii_target(target):
        if not session.get("pii_read"):
            session["pii_read"] = True
            changed = True

    # Track exploratory QA completion signals
    if (
        (tool_name in SHELL_TOOLS and re.search(r"(?i)\bforge(\.py)?\s+qa\b", target))
        or (
            tool_name in (WRITE_TOOLS | READ_TOOLS)
            and any(k in norm_target for k in ("qa_walkthrough", "qa_report", "qa-artifacts/"))
        )
    ) and not error:
        if not session.get("walkthrough_completed"):
            session["walkthrough_completed"] = True
            changed = True

    # Track test runs
    if tool_name in SHELL_TOOLS and "pytest" in target.lower() and not error:
        if not session.get("tests_ran"):
            session["tests_ran"] = True
            changed = True

    # Clear mandatory_pending when SKILL.md is read
    if tool_name in READ_TOOLS and norm_target:
        pending = [str(p) for p in session.get("mandatory_pending", [])]
        remaining: list[str] = []
        for p in pending:
            norm_p = normalize_rel_path(p, root)
            if (
                norm_target == norm_p
                or norm_target.endswith("/" + norm_p)
                or norm_p.endswith("/" + norm_target)
            ):
                skill_loaded = p
                loaded = list(session.get("loaded_skills", []))
                if p not in loaded:
                    loaded.append(p)
                session["loaded_skills"] = loaded
                changed = True
            else:
                remaining.append(p)
        if skill_loaded:
            session["mandatory_pending"] = remaining

    if changed:
        save_all_state(root, all_state)

    append_metric(
        root,
        {
            "conversationId": conversation_id,
            "tool": tool_name,
            "target": norm_target or target,
            "skill_loaded": skill_loaded or None,
            "error": error or None,
        },
    )
    write_stdout_json({})


if __name__ == "__main__":
    main()
