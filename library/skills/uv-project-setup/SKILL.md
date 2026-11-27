---
name: uv-project-setup
description: Configure Python workspaces exclusively with uv, pyproject.toml, and deterministic dependency locking. Use when setting up Python environments or adding packages. Do not use for Node.js packages.
---

# uv Project Setup

1. Initialize and synchronize dependencies via `uv sync` and `uv add`.
2. Keep `ruff`, `mypy`, `bandit`, and `pytest` inside `[dependency-groups] dev`.
3. Execute tools via `uv run ...` rather than global `pip`.
