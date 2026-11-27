"""PreToolUse hook: enforces Lethal Trifecta, Swarm Ownership, PII/Secret, QA, and Skill guards."""

from __future__ import annotations

import re
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from _hook_lib import (  # noqa: E402
    SHELL_TOOLS,
    WRITE_TOOLS,
    extract_tool_content,
    extract_tool_target,
    get_session,
    load_all_state,
    load_ownership_policy,
    normalize_rel_path,
    path_matches_patterns,
    read_stdin_json,
    resolve_repo_root,
    save_all_state,
    write_stdout_json,
)

OUTBOUND_NET_RE = re.compile(
    r"(?i)\b(curl|wget|invoke-webrequest|iwr|invoke-restmethod|irm|nc|ncat|netcat)\b"
    r"|python[^\n]*(urllib\.request|requests\.|httpx\.)"
)
UNSANITIZED_RESEARCH_RE = re.compile(
    r"(?i)\bforge(?:\.py)?\s+(?:research|source|tailor)\b.*\braw[_-]"
)
SECRET_INLINE_RE = re.compile(
    r"(?:sk-proj-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{20,}|pplx-[A-Za-z0-9_-]{16,}"
    r"|fc-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|gh[pous]_[A-Za-z0-9]{20,})"
)
IBAN_INLINE_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){10,26}\b")
EMAIL_INLINE_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
SAFE_EMAIL_DOMAINS = frozenset(
    {"example.com", "example.org", "example.net", "agent-forge.local", "localhost"}
)


def _detect_inline_pii_or_secret(content: str) -> str:
    if not content:
        return ""
    if SECRET_INLINE_RE.search(content):
        return "inline API key/secret detected"
    if IBAN_INLINE_RE.search(content):
        return "inline IBAN detected"
    for match in EMAIL_INLINE_RE.finditer(content):
        if match.group(1).lower() not in SAFE_EMAIL_DOMAINS:
            return f"inline personal/customer email domain ({match.group(1)}) detected"
    return ""


def main() -> None:
    payload = read_stdin_json()
    root = resolve_repo_root()
    conversation_id = str(payload.get("conversationId") or "default")
    tool_call = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    tool_name = str(tool_call.get("name") or "").lower()
    target = extract_tool_target(tool_call)
    norm_target = normalize_rel_path(target, root)

    all_state = load_all_state(root)
    session = get_session(all_state, conversation_id)

    # 1. Lethal Trifecta & PII Exfiltration Blocker
    if (
        (session.get("sensitive_read") or session.get("pii_read"))
        and tool_name in SHELL_TOOLS
        and OUTBOUND_NET_RE.search(target)
    ):
        write_stdout_json(
            {
                "decision": "deny",
                "reason": (
                    "Blocked: Lethal Trifecta detected "
                    "(sensitive/PII read followed by outbound network command)."
                ),
            }
        )
        return

    # 1B. PII Corpus Sanitization Guard
    if (
        tool_name in SHELL_TOOLS
        and session.get("pii_read")
        and UNSANITIZED_RESEARCH_RE.search(target)
    ):
        write_stdout_json(
            {
                "decision": "deny",
                "reason": (
                    "Blocked by PII guard: run `forge.py sanitize` on raw data before "
                    "calling research, source, or tailor."
                ),
            }
        )
        return

    if tool_name in WRITE_TOOLS and any(
        zone in norm_target for zone in ("library/units/", "corpus/", "staging/")
    ) and "tests/" not in norm_target:
        pii_issue = _detect_inline_pii_or_secret(extract_tool_content(tool_call))
        if pii_issue:
            write_stdout_json(
                {
                    "decision": "deny",
                    "reason": (
                        f"Blocked by PII/secret guard ({pii_issue}). "
                        "Sanitize locally via `forge.py sanitize` first."
                    ),
                }
            )
            return

    # 1C. Swarm Worker Path Ownership Guard
    owned_paths, forbidden_paths = load_ownership_policy(root, conversation_id, session)
    if tool_name in WRITE_TOOLS and norm_target:
        if forbidden_paths and path_matches_patterns(norm_target, forbidden_paths, root):
            write_stdout_json(
                {
                    "decision": "deny",
                    "reason": (
                        f"Blocked by swarm forbidden_paths guard: '{norm_target}' is off-limits."
                    ),
                }
            )
            return
        if owned_paths and not path_matches_patterns(norm_target, owned_paths, root):
            write_stdout_json(
                {
                    "decision": "deny",
                    "reason": (
                        f"Blocked by swarm owned_paths guard: '{norm_target}' is outside "
                        f"worker scope {owned_paths}."
                    ),
                }
            )
            return

    # 2. QA Live-First Blocker
    if tool_name in WRITE_TOOLS and target.lower().endswith(".spec.ts"):
        if not session.get("walkthrough_completed"):
            write_stdout_json(
                {
                    "decision": "deny",
                    "reason": (
                        "Run exploratory QA (/browser + DevTools) "
                        "before writing Playwright scripts."
                    ),
                }
            )
            return

    # 3. Two-Try Skill Guard
    pending = [str(p) for p in session.get("mandatory_pending", []) if isinstance(p, str)]
    if pending and tool_name in (WRITE_TOOLS | SHELL_TOOLS):
        warned = {str(p).lower() for p in session.get("warned_skills", [])}
        unwarned = [p for p in pending if p.lower() not in warned]
        if unwarned:
            session["warned_skills"] = list(warned | {p.lower() for p in unwarned})
            save_all_state(root, all_state)
            write_stdout_json(
                {
                    "decision": "deny",
                    "reason": f"Read required skill first: {', '.join(unwarned)}",
                }
            )
            return
        write_stdout_json(
            {
                "decision": "ask",
                "reason": "Two-Try-Guard: allowing second attempt after prior skill reminder.",
            }
        )
        return

    # 4. Explicit default pass-through (never return empty {})
    write_stdout_json({"decision": "ask"})


if __name__ == "__main__":
    main()
