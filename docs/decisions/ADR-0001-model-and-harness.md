# ADR-0001: Model Selection and Local CLI/API Harness Defaults

- **Status:** Accepted
- **Date:** 2026-11-26
- **Scope:** `src/forge/config.py`, `.env.example`, `.env`, `forge.py`, Stage 1/3/6 runners

## Context

Initial end-to-end live smoke tests of `agent-forge` on Windows 11 (`uv`, Python 3.14, `gh`, `agy 1.1.4`, Playwright, `ffmpeg`) uncovered several live API and CLI incompatibilities:
1. Gemini 2.5 model identifiers returned HTTP 404 during Stage 6 video-audit testing.
2. Perplexity's current Agent API uses `/v1/responses` rather than legacy chat-completions payloads.
3. `gh api search/repositories -f q=...` defaults to `POST` unless `--method GET` is specified.
4. Invoking `uv run python forge.py ...` from the repository root places the root directory at `sys.path[0]`, risking import shadowing against `src/forge/`.
5. `gpt-4.1-mini` as the default Stage 3 Flash-Judge can be replaced with the newer, lower-cost, live-verified `gpt-5.6-luna` model.

## Decisions

1. **Stage 3 Flash-Judge Default (`FORGE_JUDGE_MODEL=gpt-5.6-luna`)**
   - Updated the default `judge_model` in `ForgeConfig` (`src/forge/config.py`), `.env.example`, and `.env` from `gpt-4.1-mini` to `gpt-5.6-luna`.
   - `src/forge/benchmark/grader.py` continues to enforce A/B-swap consistency (`judge_ab_with_swap`) and provider-key checks (`_has_judge_key`).

2. **Stage 6 Video-Audit Models (`gemini-3.5-flash-lite` / `gemini-3.8-flash`)**
   - Replaced failing Gemini 2.5 references with `FORGE_VIDEO_LITE_MODEL=gemini-3.5-flash-lite` (for clips `< 300s`) and `FORGE_VIDEO_FLASH_MODEL=gemini-3.8-flash` (for clips `>= 300s`), processing silent 1 FPS VP9 streams (`strip_audio_1fps`) via `google.genai`.

3. **Perplexity Agent API (`/v1/responses`)**
   - `src/forge/research/perplexity_sonar.py` targets `https://api.perplexity.ai/v1/responses` with `model: "perplexity/sonar"`, `effort: "low" | "high"`, `max_output_tokens`, `instructions`, and `input`, extracting `output_text`, `output[].content[]`, `search_results`, and `citations`.

4. **Explicit `gh api --method GET`**
   - `src/forge/discovery/radar.py` passes `["api", "--method", "GET", "search/repositories", "-f", ...]` so `gh` never switches to `POST` when query parameters are passed with `-f`.

5. **Root Entrypoint `sys.path[0]` Pinning**
   - `forge.py` prepends `<repo>/src` to `sys.path[0]` before importing `forge.cli`, guaranteeing `src/forge` resolution regardless of caller working directory or environment layout.

6. **Dynamic `agy` Flag Detection & File Hygiene**
   - `src/forge/benchmark/runner.py` probes `agy --help` via `agy_supports_output_format()` before passing `--output-format`.
   - All repository text/config files are written as UTF-8 without BOM and LF (`\n`) line endings, and Python commands execute via `uv run`.

## Consequences

- Model choices remain overridable via `.env` without touching source code.
- Live Stage 1A/1B/3/6 smoke tests run reliably on Windows 11 with strict UTF-8/LF hygiene enforced by `tests/test_repo_hygiene.py`.
