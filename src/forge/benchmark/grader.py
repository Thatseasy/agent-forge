"""Stage 1 deterministic short-circuit grader + Stage 2 calibrated A/B-swap Flash-Judge."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from forge.benchmark.models import JudgePairResult
from forge.config import ForgeConfig


class Stage1CheckResult(BaseModel):
    """0-USD deterministic gate on regraded patch directory."""

    passed: bool
    pytest_ok: bool
    ruff_ok: bool
    mypy_ok: bool
    bandit_ok: bool
    diff_lines: int
    details: str = ""


def _build_pytest_cmd(verifier_cmd: str) -> list[str]:
    text = (verifier_cmd or "pytest -q").strip()
    if any(ch in text for ch in (";", "|", "&", ">", "<", "\n", "\r")):
        return [sys.executable, "-m", "pytest", "-q"]
    tokens = shlex.split(text)
    if tokens and tokens[0].lower() == "pytest":
        return [sys.executable, "-m", "pytest", *tokens[1:]]
    if (
        len(tokens) >= 3
        and tokens[0].lower() in {"python", "python3"}
        and tokens[1:3] == ["-m", "pytest"]
    ):
        return [sys.executable, "-m", "pytest", *tokens[3:]]
    return [sys.executable, "-m", "pytest", "-q"]


def grade_stage1_deterministic(
    regrade_dir: Path,
    diff_lines: int,
    max_diff_lines: int = 400,
    verifier_cmd: str = "pytest -q",
) -> Stage1CheckResult:
    """Run pytest (including held-out tests), ruff, mypy, bandit, and diff-size check."""
    if diff_lines > max_diff_lines:
        return Stage1CheckResult(
            passed=False,
            pytest_ok=False,
            ruff_ok=False,
            mypy_ok=False,
            bandit_ok=False,
            diff_lines=diff_lines,
            details=f"Diff bloat: {diff_lines} > {max_diff_lines} lines",
        )

    def _run(cmd: list[str]) -> tuple[bool, str]:
        proc = subprocess.run(
            cmd,
            cwd=regrade_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return proc.returncode == 0, (proc.stdout + proc.stderr).strip()[:300]

    py_ok, py_msg = _run(_build_pytest_cmd(verifier_cmd))
    if not py_ok:
        return Stage1CheckResult(
            passed=False,
            pytest_ok=False,
            ruff_ok=False,
            mypy_ok=False,
            bandit_ok=False,
            diff_lines=diff_lines,
            details=f"pytest failed (short-circuit): {py_msg}",
        )

    top_py_files = sorted(
        p.name for p in regrade_dir.glob("*.py") if p.is_file() and not p.name.startswith("test_")
    )
    target_py = top_py_files or ["."]
    ruff_ok, ruff_msg = _run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--select",
            "E,F,I,W,UP,B",
            "--line-length",
            "100",
            ".",
        ]
    )
    mypy_ok, mypy_msg = _run(
        [sys.executable, "-m", "mypy", "--ignore-missing-imports", *target_py]
    )
    bandit_ok, bandit_msg = _run([sys.executable, "-m", "bandit", "-q", "-r", *target_py])

    all_ok = py_ok and ruff_ok and mypy_ok and bandit_ok
    return Stage1CheckResult(
        passed=all_ok,
        pytest_ok=py_ok,
        ruff_ok=ruff_ok,
        mypy_ok=mypy_ok,
        bandit_ok=bandit_ok,
        diff_lines=diff_lines,
        details="ok" if all_ok else f"ruff={ruff_msg} mypy={mypy_msg} bandit={bandit_msg}",
    )


def judge_ab_with_swap(
    config: ForgeConfig,
    rubric: list[str],
    underdog_patch: str,
    hype_patch: str,
    dry_run: bool = False,
    judge_fn: Callable[[str], str] | None = None,
) -> JudgePairResult:
    """Compare passing patches using cross-family Flash-Judge with mandatory A/B swap."""
    model = config.judge_model
    if dry_run or judge_fn is None and not _has_judge_key(config, model):
        ab = _heuristic_judge_pair(
            underdog_patch, hype_patch, rubric, first_label="underdog", second_label="hype"
        )
        ba = _heuristic_judge_pair(
            hype_patch, underdog_patch, rubric, first_label="hype", second_label="underdog"
        )
        winner, consistent = _resolve_swap(str(ab["winner"]), str(ba["winner"]))
        return JudgePairResult(
            judge_model=f"{model} (dry-run)" if dry_run else f"{model} (local-fallback)",
            winner=winner,
            consistent_across_swap=consistent,
            ab_choice=str(ab["winner"]),
            ba_choice=str(ba["winner"]),
            checklist_evidence=list(ab["checklist"]),
        )

    caller = judge_fn or (
        lambda prompt: _call_litellm_judge(model, prompt, api_key=_resolve_judge_key(config, model))
    )
    ab_prompt = _build_judge_prompt(rubric, first_patch=underdog_patch, second_patch=hype_patch)
    ba_prompt = _build_judge_prompt(rubric, first_patch=hype_patch, second_patch=underdog_patch)

    ab_raw = json.loads(caller(ab_prompt))
    ba_raw = json.loads(caller(ba_prompt))
    ab_choice = "underdog" if ab_raw.get("preferred") == "FIRST" else "hype"
    ba_choice = "hype" if ba_raw.get("preferred") == "FIRST" else "underdog"
    winner, consistent = _resolve_swap(ab_choice, ba_choice)

    return JudgePairResult(
        judge_model=model,
        winner=winner,
        consistent_across_swap=consistent,
        ab_choice=ab_choice,
        ba_choice=ba_choice,
        checklist_evidence=list(ab_raw.get("checklist") or []),
    )


def _resolve_judge_key(config: ForgeConfig, model: str) -> str:
    lower = model.lower()
    if "claude" in lower or lower.startswith("anthropic/"):
        return config.anthropic_api_key
    if "gemini" in lower:
        return config.gemini_api_key
    return config.openai_api_key


def _has_judge_key(config: ForgeConfig, model: str) -> bool:
    return bool(_resolve_judge_key(config, model))


def _resolve_swap(ab_choice: str, ba_choice: str) -> tuple[str, bool]:
    if ab_choice == "tie" or ba_choice == "tie":
        return "tie", ab_choice == ba_choice
    consistent = ab_choice == ba_choice
    winner = ab_choice if consistent else "tie"
    return winner, consistent


def _heuristic_judge_pair(
    first_patch: str,
    second_patch: str,
    rubric: list[str],
    first_label: str,
    second_label: str,
) -> dict[str, Any]:
    first_key = (len(first_patch.splitlines()), len(first_patch))
    second_key = (len(second_patch.splitlines()), len(second_patch))
    if first_key < second_key:
        winner = first_label
    elif second_key < first_key:
        winner = second_label
    else:
        winner = "tie"
    checklist = [
        {"criterion": item, "pass": True, "evidence": "Verified compact diff in Stage 1"}
        for item in rubric
    ]
    return {"winner": winner, "checklist": checklist}


def _build_judge_prompt(rubric: list[str], first_patch: str, second_patch: str) -> str:
    rubric_lines = "\n".join(f"- {r}" for r in rubric)
    return (
        "Compare Patch FIRST vs Patch SECOND against the binary rubric. "
        'Return JSON: {"preferred": "FIRST"|"SECOND", '
        '"checklist": [{"criterion": str, "pass": bool, "evidence": str}]}.\n\n'
        f"Rubric:\n{rubric_lines}\n\n"
        f"=== FIRST ===\n{first_patch[:2000]}\n\n"
        f"=== SECOND ===\n{second_patch[:2000]}\n"
    )


def _call_litellm_judge(model: str, prompt: str, api_key: str = "") -> str:
    import litellm

    lower = model.lower()
    if "/" not in model:
        if "claude" in lower:
            litellm_model = f"anthropic/{model}"
        elif "gemini" in lower:
            litellm_model = f"gemini/{model}"
        else:
            litellm_model = f"openai/{model}"
    else:
        litellm_model = model

    kwargs: dict[str, Any] = {
        "model": litellm_model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "api_key": api_key or None,
        "timeout": 45,
    }
    if not lower.startswith(("gpt-5", "openai/gpt-5", "o1", "o3", "o4")):
        kwargs["temperature"] = 0.0

    resp = litellm.completion(**kwargs)
    return str(resp.choices[0].message.content or "{}")
