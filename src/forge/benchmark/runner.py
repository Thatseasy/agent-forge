"""Isolated Stage 3 execution runner (`worktree` / `docker`) and anti-cheating patch re-apply."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from forge.benchmark.adapters import (
    DryRunRunnerAdapter,
    RunnerAdapter,
    agy_supports_output_format,
    resolve_runner_adapter,
)
from forge.benchmark.models import EvalSuite, EvalTask, TrialTelemetry
from forge.benchmark.skill_pack import resolve_skill_dirs
from forge.config import ForgeConfig, write_text_utf8_lf

__all__ = ["IsolatedRunner", "RunnerAdapter", "agy_supports_output_format"]


class IsolatedRunner:
    """Runs benchmark trials in isolated worktrees/containers and regrades patches cleanly."""

    def __init__(
        self,
        config: ForgeConfig,
        isolation: str = "worktree",
        runner: str = "auto",
        adapter: RunnerAdapter | None = None,
    ) -> None:
        self.config = config
        self.isolation = isolation if isolation in {"worktree", "docker"} else "worktree"
        self.runner_mode = runner
        self.adapter = adapter

    def prepare_suite_base(self, suite: EvalSuite) -> tuple[Path, Path]:
        """Create one clean baseline git snapshot per suite run (without held-out tests)."""
        suite_dir = self.config.evals_suites_dir / suite.suite_id
        fixture_src = suite_dir / suite.fixture_dir
        base_parent = Path(tempfile.gettempdir()) / f"forge-base-{uuid.uuid4().hex[:8]}"
        base_repo = base_parent / "base-repo"
        shutil.copytree(fixture_src, base_repo)
        self._init_git_snapshot(base_repo)
        return base_parent, base_repo

    def run_trial(
        self,
        suite: EvalSuite,
        task: EvalTask,
        arm: str,
        skill_dir: Path | None,
        trial_index: int,
        model: str = "default",
        dry_run: bool = False,
        base_repo: Path | None = None,
    ) -> tuple[TrialTelemetry, Path]:
        """Execute one isolated trial and return telemetry plus path to fresh regrade tree."""
        run_id = uuid.uuid4().hex[:10]
        temp_root = Path(tempfile.gettempdir()) / f"forge-eval-{run_id}"
        temp_root.mkdir(parents=True, exist_ok=True)
        suite_dir = self.config.evals_suites_dir / suite.suite_id
        fixture_src = suite_dir / suite.fixture_dir

        if base_repo is None:
            active_base = temp_root / "base-repo"
            shutil.copytree(fixture_src, active_base)
            self._init_git_snapshot(active_base)
        else:
            active_base = base_repo

        worktree_dir = temp_root / f"wt-{arm}"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree_dir)],
            cwd=active_base,
            capture_output=True,
            check=True,
        )

        try:
            if skill_dir is not None:
                for mod_dir in resolve_skill_dirs(skill_dir):
                    dest_skill = worktree_dir / ".agents" / "skills" / mod_dir.name
                    shutil.copytree(mod_dir, dest_skill)

            active_adapter = (
                DryRunRunnerAdapter()
                if dry_run
                else (
                    self.adapter
                    or resolve_runner_adapter(
                        self.config,
                        isolation=self.isolation,
                        runner=self.runner_mode,
                        dry_run=dry_run,
                    )
                )
            )
            t0 = time.perf_counter()
            telemetry = active_adapter.run(
                worktree_dir=worktree_dir,
                arm=arm,
                task=task,
                skill_dir=skill_dir,
                trial_index=trial_index,
                model=model,
            )
            latency_s = round(time.perf_counter() - t0, 3)

            subprocess.run(
                ["git", "add", "-N", "--", "*.py"],
                cwd=worktree_dir,
                capture_output=True,
            )
            diff_proc = subprocess.run(
                ["git", "diff", "--", "*.py"],
                cwd=worktree_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            patch_text = diff_proc.stdout
            diff_lines = sum(
                1
                for ln in patch_text.splitlines()
                if (ln.startswith("+") or ln.startswith("-")) and not ln.startswith(("+++", "---"))
            )
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree_dir)],
                cwd=active_base,
                capture_output=True,
            )
            shutil.rmtree(temp_root, ignore_errors=True)

        regrade_dir = self._prepare_regrade_dir(
            run_id=run_id,
            fixture_src=fixture_src,
            held_out_dir=self.config.evals_held_out_dir / suite.suite_id,
            patch_text=patch_text,
        )
        final_telemetry = telemetry.model_copy(
            update={
                "latency_s": telemetry.latency_s if dry_run else latency_s,
                "patch_text": patch_text,
                "diff_lines": diff_lines,
            }
        )
        return final_telemetry, regrade_dir

    @staticmethod
    def _init_git_snapshot(repo_dir: Path) -> None:
        subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "bench@agent-forge.local"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Agent Forge"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "baseline"],
            cwd=repo_dir,
            capture_output=True,
            check=True,
        )

    @staticmethod
    def _prepare_regrade_dir(
        run_id: str,
        fixture_src: Path,
        held_out_dir: Path,
        patch_text: str,
    ) -> Path:
        """Apply `agent.patch` onto a fresh temp dir and mount held-out tests only here."""
        regrade_dir = Path(tempfile.gettempdir()) / f"forge-regrade-{run_id}"
        if regrade_dir.exists():
            shutil.rmtree(regrade_dir, ignore_errors=True)
        shutil.copytree(fixture_src, regrade_dir)

        if patch_text.strip():
            patch_file = regrade_dir / "agent.patch"
            write_text_utf8_lf(patch_file, patch_text)
            subprocess.run(
                ["git", "apply", "--whitespace=nowarn", str(patch_file)],
                cwd=regrade_dir,
                capture_output=True,
            )
            patch_file.unlink(missing_ok=True)

        if held_out_dir.exists():
            shutil.copytree(held_out_dir, regrade_dir, dirs_exist_ok=True)
        return regrade_dir
