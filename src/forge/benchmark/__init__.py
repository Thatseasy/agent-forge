"""Stage 3 automated skill benchmarking arena."""

from forge.benchmark.ablation import AblationArena
from forge.benchmark.adapters import (
    AgyCliRunnerAdapter,
    DirectApiRunnerAdapter,
    DockerRunnerAdapter,
    DryRunRunnerAdapter,
    RunnerAdapter,
    resolve_runner_adapter,
)
from forge.benchmark.cost_oracle import CostOracle, ModelRate
from forge.benchmark.grader import Stage1CheckResult, grade_stage1_deterministic, judge_ab_with_swap
from forge.benchmark.models import ArmMetrics, BenchmarkReport, EvalSuite, TriggerMetrics
from forge.benchmark.runner import IsolatedRunner, agy_supports_output_format
from forge.benchmark.scoring import (
    cache_penalty,
    estimate_cost_usd,
    normalized_gain,
    pass_at_k,
    pass_pow_k,
    value_score,
)
from forge.benchmark.trigger_eval import evaluate_triggers

__all__ = [
    "AblationArena",
    "AgyCliRunnerAdapter",
    "ArmMetrics",
    "BenchmarkReport",
    "CostOracle",
    "DirectApiRunnerAdapter",
    "DockerRunnerAdapter",
    "DryRunRunnerAdapter",
    "EvalSuite",
    "IsolatedRunner",
    "ModelRate",
    "RunnerAdapter",
    "Stage1CheckResult",
    "TriggerMetrics",
    "agy_supports_output_format",
    "cache_penalty",
    "estimate_cost_usd",
    "evaluate_triggers",
    "grade_stage1_deterministic",
    "judge_ab_with_swap",
    "normalized_gain",
    "pass_at_k",
    "pass_pow_k",
    "resolve_runner_adapter",
    "value_score",
]
