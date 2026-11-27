# Reusable Antigravity Hook Templates

Die kanonischen Python-3-Hook-Implementierungen liegen unter `.agents/hooks/` und werden von `forge.py sync --profile <name> --target <path>` direkt in Zielprojekte ausgerollt:

- `pre_invocation_router.py` (`PreInvocation`, 5s Timeout)
- `pre_tool_guard.py` (`PreToolUse`, 5s Timeout)
- `post_tool_tracker.py` (`PostToolUse`, 5s Timeout)
- `stop_quality_nudge.py` (`Stop`, 15s Timeout)
- `_hook_lib.py` (Stdlib-only Shared State & Metrics Helper)
