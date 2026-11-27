---
description: Run Stage 3 trigger eval and 3-arm ablation benchmark (Vanilla vs. Hype vs. Underdog) with Cost-Oracle arbitrage.
argument-hint: "--suite <id> --underdog <path> [--hype <path>] [--trials 3] [--dry-run]"
---

# /forge-bench

Benchmark a candidate skill objectively across Vanilla, Hype, and Underdog arms using trigger recall/precision, `pass^k`, normalized gain, and `ValueScore`.

## Steps

1. Optionally inspect model arbitrage tiers first:
   - Call `forge_research(mode="cost_oracle", query="balanced")`.
   - CLI fallback: `agent-forge cost-oracle --tier balanced`.
2. Run the 3-arm ablation benchmark (default `--dry-run` unless live evaluation is requested):
   - Call `forge_eval(action="bench", target_path="<underdog-path>", spec_json='{"suite": "<id>", "hype": "<optional-path>", "trials": 3, "dry_run": true}')`.
   - CLI fallback: `agent-forge bench --suite <id> --underdog <path> [--hype <path>] --trials 3 [--dry-run]`.
3. Inspect the generated `BENCHMARK.json` metrics (`pass_pow_3`, `normalized_gain`, `value_score`, `trigger_recall`, `trigger_precision`).

## Output Contract

- Return compact JSON: `{"status": "ok"|"error", "summary_one_line": "...", "metrics": {"pass_pow_k": 0.0, "normalized_gain": 0.0, "value_score": 0.0}, "artifact_paths": ["<underdog-path>/BENCHMARK.json"]}`.
- Keep full trial transcripts under `AGENT_FORGE_HOME/runs/<run_id>/`.
