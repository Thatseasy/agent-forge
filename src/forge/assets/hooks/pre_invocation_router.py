"""PreInvocation hook: routes user prompts to mandatory skills via skill-rules.json."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

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


def _load_rules(root: Path) -> list[dict[str, Any]]:
    rules_path = root / "library" / "nudges" / "skill-rules.json"
    if not rules_path.exists():
        return []
    try:
        data = json.loads(rules_path.read_text(encoding="utf-8"))
        rules = data.get("rules", [])
        return [r for r in rules if isinstance(r, dict)]
    except (OSError, json.JSONDecodeError):
        return []


def _rule_matches(rule: dict[str, Any], prompt: str) -> bool:
    prompt_lower = prompt.lower()
    for kw in rule.get("keywords", []):
        if isinstance(kw, str) and kw.lower() in prompt_lower:
            return True
    pattern = rule.get("regex")
    if isinstance(pattern, str) and pattern:
        try:
            if re.search(pattern, prompt):
                return True
        except re.error:
            pass
    tokens = re.findall(r"[A-Za-z0-9_./\\-]+", prompt_lower)
    for glob_pat in rule.get("path_globs", []):
        if isinstance(glob_pat, str) and any(
            fnmatch.fnmatch(tok, glob_pat.lower()) for tok in tokens
        ):
            return True
    return False


def main() -> None:
    payload = read_stdin_json()
    root = resolve_repo_root()
    conversation_id = str(payload.get("conversationId") or "default")
    prompt = str(payload.get("lastUserInput") or "")

    if not prompt.strip():
        write_stdout_json({"injectSteps": [], "terminationBehavior": ""})
        return

    all_state = load_all_state(root)
    session = get_session(all_state, conversation_id)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    matched_paths: list[str] = []
    for rule in _load_rules(root):
        skill_path = str(rule.get("skill_path") or "").strip()
        if not skill_path:
            continue
        if rule.get("mandatory", True) and _rule_matches(rule, prompt):
            matched_paths.append(skill_path)

    loaded = {p.lower() for p in session.get("loaded_skills", [])}
    pending = [p for p in matched_paths if p.lower() not in loaded]

    if prompt_hash != session.get("last_prompt_hash"):
        session["last_prompt_hash"] = prompt_hash
        session["stop_nudged"] = False
        session["warned_skills"] = []
        existing = list(session.get("mandatory_pending", []))
        for p in pending:
            if p not in existing:
                existing.append(p)
        session["mandatory_pending"] = existing
        save_all_state(root, all_state)

    current_pending = list(session.get("mandatory_pending", []))
    inject_steps: list[dict[str, Any]] = []
    if current_pending:
        listed = ", ".join(current_pending)
        inject_steps.append(
            {
                "systemMessage": {
                    "systemMessage": (
                        "Mandatory skill(s) matched for this request. "
                        f"Read before editing or running commands: {listed}"
                    )
                }
            }
        )

    write_stdout_json({"injectSteps": inject_steps, "terminationBehavior": ""})


if __name__ == "__main__":
    main()
