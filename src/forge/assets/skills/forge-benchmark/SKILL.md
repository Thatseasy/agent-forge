---
name: forge-benchmark
description: Run automated 3-arm skill ablation benchmarks (Vanilla vs. Hype vs. Underdog) with trigger evaluation, anti-cheating patch re-apply, and ValueScore calculation. Use when comparing candidate skills or running eval suites. Do not use for browser QA recordings.
---

# Forge Benchmark Arena

Vergleiche Kandidaten-Skills objektiv gegen bestehende Hype-Skills und Vanilla-Baseline.

## Schritte

1. Prüfe Optionen via `uv run python forge.py bench --help`.
2. Starte 3-Arm-Benchmark (Worktree-Isolation Standard, `--dry-run` ohne Live-Model-Calls):
   `uv run python forge.py bench --suite python-cli-refactor --underdog staging/inbox/<slug> --hype library/skills/<slug> --trials 3`
3. Prüfe `BENCHMARK.json` (`pass_pow_3`, `normalized_gain`, `value_score`, `trigger_recall`, `trigger_precision`) im Kandidaten-Ordner.
