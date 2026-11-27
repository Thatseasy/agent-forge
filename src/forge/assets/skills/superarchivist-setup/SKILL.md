---
name: superarchivist-setup
description: Assess evolving repositories, audit documentation health, source primary stack docs/MCPs, and bootstrap an idempotent agent harness. Use when onboarding a new or vibe-coded project, auditing project maturity, or running forge archive-audit, source, and setup. Do not use for Stage 3 ablation benchmarks or exploratory Playwright QA sessions.
---

# Superarchivist Setup

Understand any evolving repository before editing, select only the docs and rules matching its maturity, and wire primary sources plus hooks idempotently.

## 1. Assess Project Maturity

Inspect the target directory read-only first:
- **Manifests & stack:** `package.json`, `pyproject.toml`, `tsconfig.json`, `next.config.*`, `supabase/`, `playwright.config.*`, `Dockerfile`.
- **Docs & decisions:** `README.md`, `AGENTS.md`, `docs/decisions/ADR-*.md`, `tasks/todo.md`, `tasks/lessons.md`.
- **Harness & quality:** `.agents/`, `.pre-commit-config.yaml`, `tests/`, CI workflows.

Classify into one of three maturity tiers:
- `prototype_3h_chat`: Minimal app code + manifest, missing `AGENTS.md`, ADRs, `tasks/`, and `.agents/` hooks. Needs full lean boilerplate.
- `active_product`: Partial docs or tests present, missing some ADRs, `tasks/lessons.md`, or curated MCP/hook wiring. Needs gap-fill without overwriting existing docs.
- `mature_repo`: Structured docs, tests, and CI/hooks already present. Audit gaps, recommend missing primary sources, and preserve existing customizations.

## 2. Choose Appropriate Docs

- Keep `AGENTS.md` a lean routing map (<100 lines, <24 KB), never a dump of implementation code.
- Create `docs/decisions/ADR-0001.md` capturing detected stack choices and harness invariants only if no ADR exists.
- Initialize `tasks/todo.md` and `tasks/lessons.md` only when missing so work tracking and error memory persist across sessions.
- Prefer token-lean MCP wrappers (`mcp2cli --toon`, `--slim` flags) in `mcp_config.recommended.json`.

## 3. Run the Forge Pipeline

1. **Audit docs & extract reusable patterns (read-only on target):**
   `uv run python forge.py archive-audit --project <path> --no-extract`
2. **Source top 3-5 primary docs/MCP servers for the detected stack:**
   `uv run python forge.py source <path> --dry-run`
3. **Preview unified setup plan (zero writes):**
   `uv run python forge.py setup --target <path> --dry-run`
4. **Apply idempotent harness & boilerplate:**
   `uv run python forge.py setup --target <path>`

Omit `--dry-run` on `source` when live Context7/`llms.txt` reachability probes are desired.

## 4. Verify Invariants

- Re-running `forge.py setup --target <path>` must report zero unexpected overwrites for existing custom docs.
- All generated text/JSON/YAML/Markdown files use UTF-8 without BOM and LF (`\n`) endings.
- `.agents/hooks.json` must pass `validate_hooks_schema` (flat arrays for `PreInvocation`/`Stop`, matcher groups for `PreToolUse`/`PostToolUse`).
