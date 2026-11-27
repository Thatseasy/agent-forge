"""Pydantic data models for Stage 3 skill benchmarking and scoring."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class EvalTask(BaseModel):
    """Single coding or workflow task within a benchmark suite."""

    id: str
    prompt: str
    verifier_cmd: str = "pytest -q"
    edge_case: bool = False


class EvalSuite(BaseModel):
    """Benchmark suite loaded from `evals/suites/<domain>/suite.yaml`."""

    suite_id: str
    domain: str
    description: str = ""
    fixture_dir: str = "fixture"
    tasks: list[EvalTask]
    near_miss_prompts: list[str] = Field(default_factory=list)
    soft_rubric: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, suite_dir: Path) -> EvalSuite:
        suite_yaml = suite_dir / "suite.yaml"
        if not suite_yaml.exists():
            raise FileNotFoundError(f"Missing suite.yaml at {suite_yaml}")
        raw = yaml.safe_load(suite_yaml.read_text(encoding="utf-8"))
        return cls.model_validate(raw)


class TrialTelemetry(BaseModel):
    """Execution telemetry and grading status for one isolated trial."""

    arm: str
    task_id: str
    trial_index: int
    model: str = "default"
    passed: bool
    stage1_passed: bool
    infra_ok: bool = True
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cache_read_input_tokens: int = 0
    tool_calls: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0
    diff_lines: int = 0
    patch_text: str = ""
    error: str = ""


class TriggerMetrics(BaseModel):
    """Phase A simultaneous trigger evaluation metrics."""

    skill_name: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    trigger_recall: float = 0.0
    trigger_precision: float = 0.0
    f1: float = 0.0


class ArmMetrics(BaseModel):
    """Aggregated statistics for one arm (`vanilla`, `hype`, or `underdog`)."""

    arm: str
    skill_name: str
    trials: int
    passes: int
    infra_failures: int = 0
    pass_at_k: float
    pass_pow_k: float
    mean_cost_usd: float
    mean_latency_s: float
    mean_tool_calls: float
    cache_hit_rate: float
    normalized_gain: float = 0.0
    value_score: float = 0.0


class JudgePairResult(BaseModel):
    """Calibrated cross-family Flash-Judge A/B swap outcome."""

    judge_model: str
    winner: str  # "underdog" | "hype" | "tie"
    consistent_across_swap: bool
    ab_choice: str
    ba_choice: str
    checklist_evidence: list[dict[str, Any]] = Field(default_factory=list)


class BenchmarkReport(BaseModel):
    """Full Stage 3 report serialized to `BENCHMARK.json`."""

    suite_id: str
    isolation: str
    trials_per_task: int
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    trigger_metrics: list[TriggerMetrics] = Field(default_factory=list)
    arms: dict[str, ArmMetrics] = Field(default_factory=dict)
    arbitrage_matrix: list[dict[str, Any]] = Field(default_factory=list)
    judge: JudgePairResult | None = None
    trial_records: list[TrialTelemetry] = Field(default_factory=list)
