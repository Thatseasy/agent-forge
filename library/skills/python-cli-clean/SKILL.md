---
name: python-cli-clean
description: Build and refactor Python CLI tools using uv, Typer, UTF-8 without BOM, LF line endings, and strict type hints. Use when writing Python CLIs or scripts. Do not use for browser CSS styling.
---

# Python CLI Clean

1. Use `uv run` for every Python invocation.
2. Separate business logic from Typer command wrappers at testable seams.
3. Write text/JSON outputs with `encoding="utf-8"` and `newline="\n"`.
4. Verify with `uv run ruff check`, `uv run mypy`, and `uv run pytest`.
