# Python CLI Engineering Conventions

1. **Deterministic File I/O:** Always write Markdown, JSON, YAML, and code files as UTF-8 without BOM using LF (`\n`) line endings.
2. **Strict Type Contracts:** Enforce `from __future__ import annotations`, explicit return annotations, and Pydantic v2 models at module boundaries.
3. **Fail-Fast Validation:** Validate slug inputs and file paths before mutating disk state; never shell out with `shell=True`.
4. **Cross-Platform Subprocesses:** Resolve directories via `pathlib.Path` rather than hardcoded drive letters or OS-specific separators.
