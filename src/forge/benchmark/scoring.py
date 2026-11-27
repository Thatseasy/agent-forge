"""Unbiased pass@k / pass^k estimators, normalized gain, and ValueScore calculations."""

from __future__ import annotations

import math


def pass_at_k(n: int, c: int, k: int = 3) -> float:
    """Compute unbiased pass@k = 1 - C(n - c, k) / C(n, k)."""
    if n <= 0 or c <= 0 or k <= 0:
        return 0.0
    eff_k = min(k, n)
    if n - c < eff_k:
        return 1.0
    return round(1.0 - (math.comb(n - c, eff_k) / math.comb(n, eff_k)), 4)


def pass_pow_k(n: int, c: int, k: int = 3) -> float:
    """Compute consistency probability pass^k = C(c, k) / C(n, k) (or (c/n)^k when n < k)."""
    if n <= 0 or c <= 0 or k <= 0:
        return 0.0
    if n < k:
        return round((c / n) ** k, 4)
    if c < k:
        return 0.0
    return round(math.comb(c, k) / math.comb(n, k), 4)


def normalized_gain(skill_pass: float, vanilla_pass: float) -> float:
    """Hake's normalized gain g = (skill - vanilla) / (1 - vanilla)."""
    denom = 1.0 - vanilla_pass
    if denom <= 1e-6:
        return 0.0 if skill_pass <= vanilla_pass else 1.0
    return round((skill_pass - vanilla_pass) / denom, 4)


def cache_penalty(vanilla_hit_rate: float, skill_hit_rate: float) -> float:
    """Non-negative penalty when a skill degrades prompt-cache hit rate relative to baseline."""
    return round(max(0.0, vanilla_hit_rate - skill_hit_rate), 4)


def value_score(
    gain: float,
    trigger_recall: float,
    cost_skill: float,
    cost_vanilla: float,
    cache_hit_vanilla: float,
    cache_hit_skill: float,
    alpha: float = 0.5,
) -> float:
    """Compute Best-Price ValueScore balancing gain, trigger recall, cost ratio & cache penalty."""
    if gain <= 0.0 or trigger_recall <= 0.0:
        return 0.0
    safe_vanilla = max(cost_vanilla, 1e-6)
    cost_ratio = max(0.05, cost_skill / safe_vanilla)
    pen = cache_penalty(cache_hit_vanilla, cache_hit_skill)
    denom = cost_ratio * (1.0 + alpha * pen)
    return round((gain * trigger_recall) / denom, 4)


def estimate_cost_usd(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    input_per_m: float | None = None,
    output_per_m: float | None = None,
    cache_per_m: float | None = None,
    model: str = "",
) -> float:
    """Estimate USD cost using CostOracle per-model rates when `model` is specified."""
    if model and input_per_m is None and output_per_m is None and cache_per_m is None:
        from forge.benchmark.cost_oracle import CostOracle

        return CostOracle().estimate_cost(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
        )

    eff_in = 1.25 if input_per_m is None else input_per_m
    eff_out = 5.0 if output_per_m is None else output_per_m
    eff_cache = 0.125 if cache_per_m is None else cache_per_m
    uncached = max(0, input_tokens - cache_read_tokens)
    total = (
        (uncached / 1_000_000) * eff_in
        + (cache_read_tokens / 1_000_000) * eff_cache
        + (output_tokens / 1_000_000) * eff_out
    )
    return round(total, 6)
