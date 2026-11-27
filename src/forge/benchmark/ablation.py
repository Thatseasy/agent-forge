"""Phase B 3-Arm Ablation Arena (`vanilla` vs. `hype` vs. `underdog`) & BENCHMARK.json writer."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from forge.benchmark.adapters import RunnerAdapter
from forge.benchmark.grader import (
    Stage1CheckResult,
    grade_stage1_deterministic,
    judge_ab_with_swap,
)
from forge.benchmark.models import ArmMetrics, BenchmarkReport, EvalSuite, TrialTelemetry
from forge.benchmark.runner import IsolatedRunner
from forge.benchmark.scoring import (
    normalized_gain,
    pass_at_k,
    pass_pow_k,
    value_score,
)
from forge.benchmark.skill_pack import resolve_skill_dirs, skill_pack_name
from forge.benchmark.trigger_eval import evaluate_triggers
from forge.config import ForgeConfig, write_json_utf8_lf


class AblationArena:
    """Orchestrates Trigger-Eval, 3-Arm isolated ablation, grading, and scoring."""

    def __init__(
        self,
        config: ForgeConfig,
        isolation: str = "worktree",
        runner: str = "auto",
        adapter: RunnerAdapter | None = None,
    ) -> None:
        self.config = config
        self.isolation = isolation
        self.runner_mode = runner
        self.runner = IsolatedRunner(config, isolation=isolation, runner=runner, adapter=adapter)

    def run(
        self,
        suite_name: str,
        underdog_dir: Path,
        hype_dir: Path | None = None,
        trials: int = 3,
        compare_models: list[str] | None = None,
        dry_run: bool = False,
    ) -> BenchmarkReport:
        """Run complete Stage 3 benchmark and write `BENCHMARK.json` into `underdog_dir`."""
        if not resolve_skill_dirs(underdog_dir):
            raise FileNotFoundError(f"Missing SKILL.md in underdog directory: {underdog_dir}")
        if hype_dir is not None and not resolve_skill_dirs(hype_dir):
            raise FileNotFoundError(f"Missing SKILL.md in hype directory: {hype_dir}")

        suite_dir = self.config.evals_suites_dir / suite_name
        suite = EvalSuite.load(suite_dir)

        skill_dirs = [underdog_dir]
        if hype_dir is not None:
            skill_dirs.append(hype_dir)
        trigger_metrics = evaluate_triggers(
            suite,
            skill_dirs,
            metrics_jsonl=self.config.state_dir / "metrics.jsonl",
        )
        recall_by_name = {tm.skill_name: tm.trigger_recall for tm in trigger_metrics}
        for sdir, tm in zip(skill_dirs, trigger_metrics, strict=False):
            recall_by_name[skill_pack_name(sdir)] = tm.trigger_recall

        underdog_label = skill_pack_name(underdog_dir)
        arms_spec: list[tuple[str, str, Path | None]] = [("vanilla", "vanilla-baseline", None)]
        if hype_dir is not None:
            arms_spec.append(("hype", skill_pack_name(hype_dir), hype_dir))
        arms_spec.append(("underdog", underdog_label, underdog_dir))

        all_trials: list[TrialTelemetry] = []
        sample_patches: dict[str, str] = {}
        stage1_cache: dict[str, Stage1CheckResult] = {}
        base_parent, base_repo = self.runner.prepare_suite_base(suite)

        try:
            for arm_key, _label, arm_skill_dir in arms_spec:
                for task in suite.tasks:
                    for trial_idx in range(1, trials + 1):
                        tel, regrade_dir = self.runner.run_trial(
                            suite=suite,
                            task=task,
                            arm=arm_key,
                            skill_dir=arm_skill_dir,
                            trial_index=trial_idx,
                            dry_run=dry_run,
                            base_repo=base_repo,
                        )
                        try:
                            if tel.infra_ok:
                                patch_key = hashlib.sha256(
                                    f"{suite.suite_id}:{task.id}:{task.verifier_cmd}:{tel.patch_text}".encode()
                                ).hexdigest()
                                s1 = stage1_cache.get(patch_key)
                                if s1 is None:
                                    s1 = grade_stage1_deterministic(
                                        regrade_dir,
                                        tel.diff_lines,
                                        verifier_cmd=task.verifier_cmd,
                                    )
                                    stage1_cache[patch_key] = s1
                                tel = tel.model_copy(
                                    update={
                                        "stage1_passed": s1.passed,
                                        "passed": s1.passed,
                                        "error": "" if s1.passed else s1.details,
                                    }
                                )
                        finally:
                            shutil.rmtree(regrade_dir, ignore_errors=True)
                        all_trials.append(tel)
                        if arm_key not in sample_patches and tel.passed:
                            sample_patches[arm_key] = tel.patch_text

            arm_metrics = self._aggregate_arms(
                arms_spec, all_trials, recall_by_name, k=min(3, trials)
            )

            arbitrage_matrix: list[dict[str, Any]] = []
            for model_id in compare_models or []:
                arb_tel, regrade_dir = self.runner.run_trial(
                    suite=suite,
                    task=suite.tasks[0],
                    arm="underdog",
                    skill_dir=underdog_dir,
                    trial_index=1,
                    model=model_id,
                    dry_run=dry_run,
                    base_repo=base_repo,
                )
                shutil.rmtree(regrade_dir, ignore_errors=True)
                arbitrage_matrix.append(
                    {
                        "model": model_id,
                        "passed": arb_tel.passed,
                        "cost_usd": arb_tel.cost_usd,
                        "latency_s": arb_tel.latency_s,
                    }
                )
        finally:
            shutil.rmtree(base_parent, ignore_errors=True)

        judge_res = None
        if "underdog" in sample_patches and "hype" in sample_patches:
            judge_res = judge_ab_with_swap(
                self.config,
                suite.soft_rubric,
                underdog_patch=sample_patches["underdog"],
                hype_patch=sample_patches["hype"],
                dry_run=dry_run,
            )

        report = BenchmarkReport(
            suite_id=suite.suite_id,
            isolation=self.isolation,
            trials_per_task=trials,
            trigger_metrics=trigger_metrics,
            arms=arm_metrics,
            arbitrage_matrix=arbitrage_matrix,
            judge=judge_res,
            trial_records=all_trials,
        )
        write_json_utf8_lf(underdog_dir / "BENCHMARK.json", report.model_dump())
        return report

    @staticmethod
    def _aggregate_arms(
        arms_spec: list[tuple[str, str, Path | None]],
        trials: list[TrialTelemetry],
        recall_by_name: dict[str, float],
        k: int,
    ) -> dict[str, ArmMetrics]:
        raw: dict[str, ArmMetrics] = {}
        for arm_key, skill_name, _ in arms_spec:
            subset = [t for t in trials if t.arm == arm_key]
            n = len(subset)
            c = sum(1 for t in subset if t.passed)
            infra_fails = sum(1 for t in subset if not t.infra_ok)
            tot_in = sum(t.input_tokens for t in subset)
            tot_cache = sum(t.cache_read_input_tokens for t in subset)
            hit_rate = round(tot_cache / max(1, tot_in), 4)
            raw[arm_key] = ArmMetrics(
                arm=arm_key,
                skill_name=skill_name,
                trials=n,
                passes=c,
                infra_failures=infra_fails,
                pass_at_k=pass_at_k(n, c, k=k),
                pass_pow_k=pass_pow_k(n, c, k=k),
                mean_cost_usd=round(sum(t.cost_usd for t in subset) / max(1, n), 6),
                mean_latency_s=round(sum(t.latency_s for t in subset) / max(1, n), 3),
                mean_tool_calls=round(sum(t.tool_calls for t in subset) / max(1, n), 2),
                cache_hit_rate=hit_rate,
            )

        vanilla = raw["vanilla"]
        for arm_key, m in raw.items():
            if arm_key == "vanilla":
                continue
            gain = normalized_gain(m.pass_pow_k, vanilla.pass_pow_k)
            recall = recall_by_name.get(m.skill_name, 1.0)
            v_score = value_score(
                gain=gain,
                trigger_recall=recall,
                cost_skill=m.mean_cost_usd,
                cost_vanilla=vanilla.mean_cost_usd,
                cache_hit_vanilla=vanilla.cache_hit_rate,
                cache_hit_skill=m.cache_hit_rate,
            )
            raw[arm_key] = m.model_copy(update={"normalized_gain": gain, "value_score": v_score})
        return raw
