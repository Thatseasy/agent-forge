---
name: example-python-cli-conventions
description: Enforce cross-platform Python CLI conventions including Typer options, Pydantic v2 boundaries, UTF-8 LF file writes, and strict typing. Use when scaffolding or refactoring Python CLI commands or config loaders. Do not use for frontend UI styling or SQL migrations.
---

# Python CLI Conventions

Apply these rules whenever implementing or refactoring Python CLI modules:

1. **Explicit Path & Target Options:** Accept `--target` cleanly defaulting to `Path.cwd().resolve()`, never assume the repository root equals CWD.
2. **UTF-8 LF Serialization:** Always write JSON, YAML, Markdown, and generated source files using `encoding="utf-8"` without BOM and `newline="\n"`.
3. **Strict Typing & Validation:** Annotate every function signature cleanly for `mypy --strict` and validate untrusted inputs via Pydantic v2 models.
4. **Safe Subprocess Execution:** Pass argument lists to `subprocess.run(..., shell=False)` with explicit timeouts and validated non-option URLs or paths.
5. **Verification Gate:** Run `ruff check`, `mypy`, `bandit -ll`, and `pytest` after every non-trivial change.
