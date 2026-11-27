# Lessons Learned — `agent-forge` Live Smoke Tests

Actionable rules captured from real failures during live smoke tests (2026-11-26).

## 1. Perplexity Sonar Uses `/v1/responses`
- **Symptom:** Legacy Sonar chat-completions payload shapes and response parsing missed Agent API responses.
- **Root Cause:** Perplexity Agent API expects `POST https://api.perplexity.ai/v1/responses` with `model: "perplexity/sonar"`, `effort`, `max_output_tokens`, `instructions`, and `input`.
- **Rule:** Use `src/forge/research/perplexity_sonar.py`'s `/v1/responses` schema and parse `output_text`, `output[].content[]` (`annotations[].url`), `search_results`, and `citations` (keeping `choices` only as a fallback).

## 2. Verify Model IDs Live (Gemini 2.5 404 -> `gemini-3.5-flash-lite`; Judge -> `gpt-5.6-luna`)
- **Symptom:** Gemini 2.5 model identifiers returned HTTP `404` during live Stage 6 QA video-audit runs.
- **Root Cause:** Hardcoded or older model IDs drift across API releases.
- **Rule:** Keep model IDs configurable in `ForgeConfig` / `.env` (`FORGE_VIDEO_LITE_MODEL=gemini-3.5-flash-lite`, `FORGE_VIDEO_FLASH_MODEL=gemini-3.8-flash`, `FORGE_JUDGE_MODEL=gpt-5.6-luna`) and verify model availability with a live smoke call before locking defaults.

## 3. Always Pass `--method GET` to `gh api` When Using `-f`
- **Symptom:** `gh api search/repositories -f q=...` failed when invoked without an explicit HTTP method.
- **Root Cause:** Passing `-f` / `--raw-field` flags causes `gh api` to default to `POST`, which GitHub's `search/repositories` endpoint rejects.
- **Rule:** Always pass `--method GET` explicitly in `gh api` invocations that pass query parameters with `-f` (see `src/forge/discovery/radar.py`).

## 4. Guard Against `sys.path[0]` Shadowing in `forge.py`
- **Symptom:** Importing `forge` from the repo root (e.g., `python forge.py` or `python -c "from forge..."`) resolves root `forge.py` instead of `src/forge/`, raising `ModuleNotFoundError: No module named 'forge.cli'; 'forge' is not a package`.
- **Root Cause:** Python places the script directory / CWD at `sys.path[0]`, so root `forge.py` shadows the `forge` package in `src/forge/`.
- **Rule:** Keep `<repo>/src` pinned at `sys.path[0]` at the top of `forge.py` before importing `forge.*`, set `pythonpath = ["src"]` for pytest (or `sys.path.insert(0, "src")` for inline `-c` commands), and run Python commands via `uv run`.

## 5. Probe `agy` CLI Capabilities & Guard Windows Encoding
- **Symptom:** Local `agy 1.1.4` supports `-p`/`--print` without `--output-format`; Windows editors/tools also tend to emit CRLF or UTF-8 BOM, breaking `tests/test_repo_hygiene.py`.
- **Rule:** Probe `agy --help` dynamically (`agy_supports_output_format()`), use explicit timeouts for headless CLI calls, and write all repository text/config files with `write_text_utf8_lf` / `write_json_utf8_lf` (UTF-8 without BOM, LF `\n`).
