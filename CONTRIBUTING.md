# Contributing to `agent-forge`

Thank you for helping make AI agent harnesses safer, leaner, and objectively measurable.

## Development Setup

1. Install [uv](https://docs.astral.sh/uv/) and Python 3.11+.
2. Clone the repository and sync dependencies:

```bash
uv sync
```

## Coding Standards

- **Formatting & Encoding:** Every text, Markdown, YAML, JSON, and Python file must be UTF-8 without BOM with LF (`\n`) line endings.
- **Typing & Linting:** Pass strict `mypy` (`disallow_untyped_defs = true`), `ruff check`, and `bandit -r src -ll`.
- **Context Discipline:** Keep `rules/AGENTS.md` under 60 lines, bundled core skills under 1,500 static tokens, and `agent-forge-mcp` tool schemas under 1,200 tokens.
- **No Private Data:** Never commit API keys, PII, real customer datasets, or local filesystem paths (`C:\Users\...`, `/Users/...`).
- **Stdlib-Only Global Hooks:** Everything inside `src/forge/hooks_global/` must import only Python standard-library modules.

## Required Verification Before Opening a PR

```bash
uv run ruff check src tests .agents/hooks forge.py scripts
uv run mypy src
uv run bandit -r src -ll
uv run agent-forge doctor --hooks
uv run agent-forge plugin validate --strict
uv run pytest -q
```
