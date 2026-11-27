"""Stop hook: non-looping local doc-drift and test-run quality nudge."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from _hook_lib import (  # noqa: E402
    get_session,
    load_all_state,
    read_stdin_json,
    resolve_repo_root,
    save_all_state,
    write_stdout_json,
)


def _changed_files(root: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            return []
        return [
            line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()
        ]
    except (OSError, subprocess.SubprocessError):
        return []


def main() -> None:
    payload = read_stdin_json()
    root = resolve_repo_root()
    conversation_id = str(payload.get("conversationId") or "default")
    fully_idle = bool(payload.get("fullyIdle", True))
    continuation_active = bool(payload.get("continuationActive", False))
    execution_num = int(payload.get("executionNum", 0) or 0)

    if not fully_idle or continuation_active or execution_num > 0:
        write_stdout_json({"decision": "stop"})
        return

    all_state = load_all_state(root)
    session = get_session(all_state, conversation_id)
    if session.get("stop_nudged"):
        write_stdout_json({"decision": "stop"})
        return

    changed = _changed_files(root)
    src_changed = any(f.startswith("src/") for f in changed)
    docs_updated = any(
        f in {"AGENTS.md", "README.md", "HUMAN_HANDOFF.md"} or f.startswith(("library/", "docs/"))
        for f in changed
    )
    tests_ran = bool(session.get("tests_ran"))

    if src_changed and (not tests_ran or not docs_updated):
        session["stop_nudged"] = True
        save_all_state(root, all_state)
        missing_parts: list[str] = []
        if not tests_ran:
            missing_parts.append("pytest noch nicht gelaufen")
        if not docs_updated:
            missing_parts.append("Doku/AGENTS.md unverändert")
        write_stdout_json(
            {
                "decision": "continue",
                "reason": (
                    "Qualitäts-Check: Änderungen unter src/ erkannt ("
                    + ", ".join(missing_parts)
                    + "). Bitte kurz verifizieren oder Dokumentation synchron halten."
                ),
            }
        )
        return

    write_stdout_json({"decision": "stop"})


if __name__ == "__main__":
    main()
