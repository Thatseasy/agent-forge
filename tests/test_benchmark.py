"""Acceptance tests for Stage 3 benchmarking arena, scoring, short-circuit & judge swap."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from forge.benchmark import (
    AblationArena,
    DirectApiRunnerAdapter,
    RunnerAdapter,
    judge_ab_with_swap,
    normalized_gain,
    pass_at_k,
    pass_pow_k,
    resolve_runner_adapter,
    value_score,
)
from forge.benchmark.grader import grade_stage1_deterministic
from forge.benchmark.models import EvalTask
from forge.benchmark.skill_pack import resolve_skill_dirs, skill_pack_name
from forge.benchmark.trigger_eval import evaluate_triggers
from forge.config import ForgeConfig


def test_scoring_math_and_value_score() -> None:
    assert pass_at_k(n=3, c=2, k=3) == 1.0
    assert pass_pow_k(n=3, c=3, k=3) == 1.0
    assert pass_pow_k(n=6, c=4, k=3) == 0.2
    gain = normalized_gain(skill_pass=0.9, vanilla_pass=0.5)
    assert gain == 0.8

    # Higher cache hit and lower cost yields higher ValueScore
    lean_score = value_score(
        gain=gain,
        trigger_recall=1.0,
        cost_skill=0.002,
        cost_vanilla=0.003,
        cache_hit_vanilla=0.5,
        cache_hit_skill=0.8,
    )
    bloat_score = value_score(
        gain=gain,
        trigger_recall=1.0,
        cost_skill=0.009,
        cost_vanilla=0.003,
        cache_hit_vanilla=0.5,
        cache_hit_skill=0.2,
    )
    assert lean_score > bloat_score


def test_stage1_short_circuit_fails_on_broken_test(tmp_path: Path) -> None:
    (tmp_path / "cli_app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "test_sample.py").write_text(
        "def test_fail() -> None:\n    assert False\n", "utf-8"
    )
    res = grade_stage1_deterministic(tmp_path, diff_lines=5)
    assert res.passed is False
    assert res.pytest_ok is False
    assert "short-circuit" in res.details


def test_calibrated_flash_judge_requires_consistent_ab_swap(tmp_path: Path) -> None:
    cfg = ForgeConfig(root=tmp_path, openai_api_key="dummy-key")
    # Inconsistent swap (always picks FIRST -> order bias!) must produce "tie"
    biased = judge_ab_with_swap(
        cfg,
        rubric=["Clean seam"],
        underdog_patch="+a",
        hype_patch="+b",
        judge_fn=lambda _prompt: json.dumps({"preferred": "FIRST", "checklist": []}),
    )
    assert biased.consistent_across_swap is False
    assert biased.winner == "tie"

    # Consistent swap (picks underdog first, then SECOND when underdog is second) -> "underdog"
    calls = iter(
        [
            json.dumps({"preferred": "FIRST", "checklist": [{"criterion": "Clean", "pass": True}]}),
            json.dumps(
                {"preferred": "SECOND", "checklist": [{"criterion": "Clean", "pass": True}]}
            ),
        ]
    )
    consistent = judge_ab_with_swap(
        cfg,
        rubric=["Clean seam"],
        underdog_patch="+a",
        hype_patch="+b",
        judge_fn=lambda _prompt: next(calls),
    )
    assert consistent.consistent_across_swap is True
    assert consistent.winner == "underdog"


def test_cli_bench_dry_run_produces_benchmark_json(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    underdog_dir = tmp_path / "demo-underdog"
    underdog_dir.mkdir(parents=True)
    (underdog_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo-underdog\n"
        "description: Refactor Python CLI scripts with strict UTF-8/LF hygiene, clean seams, "
        "and --help support. Use when refactoring Python CLI tools or deduplicating record "
        "pipelines. Do not use for frontend CSS or SQL tuning.\n"
        "---\n\n"
        "# Python CLI Refactoring (Lean Underdog)\n\n"
        "1. Isolate shared normalization into one pure helper.\n"
        "2. Enforce UTF-8 without BOM and LF line endings.\n",
        encoding="utf-8",
        newline="\n",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "forge.py"),
            "bench",
            "--suite",
            "python-cli-refactor",
            "--underdog",
            str(underdog_dir),
            "--hype",
            "library/skills/demo-hype",
            "--dry-run",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "Stage 3 Benchmark Arena" in proc.stdout
    bench_file = underdog_dir / "BENCHMARK.json"
    assert bench_file.exists()
    data = json.loads(bench_file.read_text(encoding="utf-8"))
    assert set(data["arms"].keys()) == {"vanilla", "hype", "underdog"}
    assert data["arms"]["underdog"]["value_score"] > data["arms"]["hype"]["value_score"]
    assert data["judge"]["consistent_across_swap"] is True


def test_resolve_runner_adapter_prefers_direct_when_key_present(tmp_path: Path) -> None:
    cfg_with_key = ForgeConfig(root=tmp_path, openai_api_key="test-key")
    adapter = resolve_runner_adapter(cfg_with_key, runner="auto")
    assert adapter.name == "direct"
    assert isinstance(adapter, RunnerAdapter)

    dry = resolve_runner_adapter(cfg_with_key, runner="auto", dry_run=True)
    assert dry.name == "dry-run"


def test_direct_api_runner_adapter_tool_loop_and_path_jail(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    underdog_dir = tmp_path / "underdog"
    underdog_dir.mkdir(parents=True)
    (underdog_dir / "SKILL.md").write_text(
        "---\nname: underdog\ndescription: Python CLI clean refactor.\n---\n# Clean\n",
        encoding="utf-8",
        newline="\n",
    )

    seen_systems: list[str] = []

    def fake_completion(**kwargs: object) -> dict[str, object]:
        msgs = list(kwargs["messages"])  # type: ignore[arg-type]
        if len(msgs) == 2:
            seen_systems.append(str(msgs[0]["content"]))
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "tc1",
                                    "function": {"name": "list_files", "arguments": "{}"},
                                },
                                {
                                    "id": "tc2",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps({"path": "../secret.txt"}),
                                    },
                                },
                                {
                                    "id": "tc3",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps({"path": "cli_app.py"}),
                                    },
                                },
                            ],
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 300,
                    "completion_tokens": 50,
                    "prompt_tokens_details": {"cached_tokens": 120},
                },
            }
        if len(msgs) == 6:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "tc4",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps(
                                            {
                                                "path": "cli_app.py",
                                                "content": (
                                                    msgs[-1]["content"].rstrip()
                                                    + "\n# tool-loop verified\n"
                                                ),
                                            }
                                        ),
                                    },
                                },
                                {
                                    "id": "tc5",
                                    "function": {
                                        "name": "run_tests",
                                        "arguments": json.dumps({"command": "pytest -q"}),
                                    },
                                },
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 400, "completion_tokens": 40},
            }
        return {
            "choices": [{"message": {"content": "Done."}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 15},
        }

    cfg = ForgeConfig(root=repo_root)
    adapter = DirectApiRunnerAdapter(cfg, completion_fn=fake_completion, max_turns=5)

    # First verify isolated direct call + path jail directly on worktree
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "cli_app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    task = EvalTask(id="t1", prompt="Inspect and verify cli_app.py", verifier_cmd="pytest -q")
    tel = adapter.run(wt, arm="underdog", task=task, skill_dir=underdog_dir, trial_index=1)
    assert tel.infra_ok is True
    assert tel.tool_calls == 5
    assert tel.input_tokens == 900
    assert tel.cache_read_input_tokens == 120
    assert "# tool-loop verified" in (wt / "cli_app.py").read_text("utf-8")
    assert "<active_skill name=\"underdog\">" in seen_systems[0]

    # Missing key returns explicit infra_ok=False without crashing
    no_key_adapter = DirectApiRunnerAdapter(ForgeConfig(root=repo_root))
    fail_tel = no_key_adapter.run(wt, arm="vanilla", task=task, skill_dir=None, trial_index=1)
    assert fail_tel.infra_ok is False
    assert "Missing LLM API key" in fail_tel.error


def test_ablation_arena_accepts_custom_injected_runner_adapter(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    underdog_dir = tmp_path / "demo-underdog"
    underdog_dir.mkdir(parents=True)
    (underdog_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo-underdog\n"
        "description: Refactor Python CLI scripts with UTF-8 and --help checks.\n"
        "---\n\n# Helper\nKeep pure normalization.\n",
        encoding="utf-8",
        newline="\n",
    )

    def arena_completion(**kwargs: object) -> dict[str, object]:
        msgs = list(kwargs["messages"])  # type: ignore[arg-type]
        if len(msgs) == 2:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "r1",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps({"path": "cli_app.py"}),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 20},
            }
        if len(msgs) == 4:
            orig = str(msgs[-1]["content"])
            sys_msg = str(msgs[0]["content"])
            suffix = "\n# underdog patch\n" if "<active_skill" in sys_msg else "\n# vanilla patch\n"
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "w1",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": json.dumps(
                                            {"path": "cli_app.py", "content": orig + suffix}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 250, "completion_tokens": 25},
            }
        return {
            "choices": [{"message": {"content": "Finished."}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 10},
        }

    cfg = ForgeConfig(root=repo_root)
    custom_adapter = DirectApiRunnerAdapter(cfg, completion_fn=arena_completion)
    arena = AblationArena(cfg, isolation="worktree", adapter=custom_adapter)
    report = arena.run(
        suite_name="python-cli-refactor",
        underdog_dir=underdog_dir,
        trials=1,
        dry_run=False,
    )
    assert set(report.arms.keys()) == {"vanilla", "underdog"}
    assert report.arms["underdog"].infra_failures == 0
    assert report.arms["underdog"].passes == len(report.trial_records) // 2


def test_multi_module_unit_pack_trigger_and_prompt(tmp_path: Path) -> None:
    from forge.benchmark.adapters import _build_system_prompt
    from forge.benchmark.models import EvalSuite

    unit_dir = tmp_path / "ledger-cli"
    skills_dir = unit_dir / "skills"
    mod_a = skills_dir / "mod-audit"
    mod_b = skills_dir / "mod-ledger"
    mod_a.mkdir(parents=True)
    mod_b.mkdir(parents=True)
    (unit_dir / "unit.yaml").write_text("domain: ledger-cli\n", encoding="utf-8", newline="\n")
    (mod_a / "SKILL.md").write_text(
        "---\n"
        "name: mod-audit\n"
        "description: Ledger live audit trail and consent gate. Do not use for Terraform.\n"
        "---\n\n# Audit\nKeep immutable digest before write.\n",
        encoding="utf-8",
        newline="\n",
    )
    (mod_b / "SKILL.md").write_text(
        "---\n"
        "name: mod-ledger\n"
        "description: Ledger integer cents valuation and webhook verification. "
        "Do not use for CSS.\n"
        "---\n\n# Ledger Sync\nCompute expected_value_cents cleanly.\n",
        encoding="utf-8",
        newline="\n",
    )

    resolved = resolve_skill_dirs(unit_dir)
    assert [p.name for p in resolved] == ["mod-audit", "mod-ledger"]
    assert skill_pack_name(unit_dir) == "ledger-cli"
    assert skill_pack_name(skills_dir) == "ledger-cli"

    sys_prompt = _build_system_prompt(unit_dir)
    assert '<active_skill name="mod-audit">' in sys_prompt
    assert '<active_skill name="mod-ledger">' in sys_prompt

    suite = EvalSuite(
        suite_id="pack-test",
        domain="ledger-cli",
        description="Pack trigger test",
        tasks=[
            EvalTask(id="t1", prompt="Implement Ledger live audit trail and consent gate."),
            EvalTask(id="t2", prompt="Fix Ledger integer cents valuation and webhook check."),
        ],
        near_miss_prompts=["Write a Terraform AWS bucket policy.", "Fix dark-mode CSS grid."],
    )
    tms = evaluate_triggers(suite, [unit_dir])
    assert len(tms) == 1
    assert tms[0].skill_name == "ledger-cli"
    assert tms[0].trigger_recall == 1.0
    assert tms[0].false_positives == 0
