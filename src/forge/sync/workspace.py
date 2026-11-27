"""Stage 5 selective profile-based workspace synchronization."""

from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from forge.config import ForgeConfig, write_json_utf8_lf, write_text_utf8_lf


class WorkspaceProfile(BaseModel):
    """Profile manifest under `library/profiles/<name>.yaml`."""

    name: str
    description: str = ""
    skills: list[str]
    include_hooks: bool = True
    skill_rules: list[str] = Field(default_factory=list)


class SyncReport(BaseModel):
    """Summary of planned or applied workspace synchronization."""

    profile: str
    target_dir: str
    dry_run: bool
    synced_skills: list[str] = Field(default_factory=list)
    planned_files: list[str] = Field(default_factory=list)
    diffs: list[str] = Field(default_factory=list)
    hooks_schema_valid: bool = True


def validate_hooks_schema(hooks_json_path: Path) -> bool:
    """Validate `.agents/hooks.json` against Antigravity asymmetric schema invariants."""
    raw = hooks_json_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf") or b"\r\n" in raw:
        return False
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or not data:
        return False
    for _hook_name, spec in data.items():
        if not isinstance(spec, dict):
            return False
        for flat_ev in ("SessionStart", "PreInvocation", "PostInvocation", "Stop"):
            if flat_ev in spec:
                arr = spec[flat_ev]
                if not isinstance(arr, list):
                    return False
                if any(not isinstance(h, dict) or "hooks" in h or "matcher" in h for h in arr):
                    return False
        for tool_ev in ("PreToolUse", "PostToolUse"):
            if tool_ev in spec:
                groups = spec[tool_ev]
                if not isinstance(groups, list):
                    return False
                for g in groups:
                    if (
                        not isinstance(g, dict)
                        or "matcher" not in g
                        or not isinstance(g.get("hooks"), list)
                    ):
                        return False
    return True


class WorkspaceSyncer:
    """Deploys curated profiles (skills + hooks + skill-rules.json) into target workspaces."""

    def __init__(self, config: ForgeConfig) -> None:
        self.config = config

    def load_profile(self, profile_name: str) -> WorkspaceProfile:
        p = self.config.resolve_profile_path(profile_name)
        if p is None or not p.exists():
            raise FileNotFoundError(
                f"Profile not found: {self.config.profiles_dir / f'{profile_name}.yaml'}"
            )
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        return WorkspaceProfile.model_validate(data)

    def _resolve_skill_source(self, skill_name: str) -> Path:
        cand = self.config.resolve_skill_dir(skill_name)
        if cand is not None:
            return cand
        raise FileNotFoundError(
            f"Profile skill `{skill_name}` missing from library/.agents/assets"
        )

    def _build_filtered_skill_rules(self, profile: WorkspaceProfile) -> dict[str, Any]:
        src_rules = self.config.resolve_nudge_file("skill-rules.json")
        if src_rules is None or not src_rules.exists():
            return {"version": "1.0", "rules": []}
        data = json.loads(src_rules.read_text(encoding="utf-8"))
        allowed = set(profile.skill_rules or profile.skills)
        rules = [r for r in (data.get("rules") or []) if r.get("id") in allowed]
        return {"version": data.get("version", "1.0"), "rules": rules}

    def sync(
        self,
        profile_name: str,
        target_dir: Path,
        dry_run: bool = False,
    ) -> SyncReport:
        """Sync curated skills, hooks, and skill-rules.json into `target_dir`."""
        profile = self.load_profile(profile_name)
        resolved_target = target_dir.resolve()
        planned: list[str] = []
        diffs: list[str] = []

        # Collect skill files
        skill_ops: list[tuple[Path, Path]] = []
        for sname in profile.skills:
            src_dir = self._resolve_skill_source(sname)
            dest_dir = resolved_target / ".agents" / "skills" / sname
            for f in sorted(src_dir.rglob("*")):
                if f.is_file() and f.name not in {"BENCHMARK.json", "GATE_REPORT.json"}:
                    rel = f.relative_to(src_dir)
                    skill_ops.append((f, dest_dir / rel))

        # Collect hook scripts & hooks.json
        hook_ops: list[tuple[Path, Path]] = []
        hooks_src = self.config.resolve_hooks_dir()
        if profile.include_hooks and hooks_src is not None and hooks_src.exists():
            for f in sorted(hooks_src.glob("*.py")):
                hook_ops.append((f, resolved_target / ".agents" / "hooks" / f.name))

        hooks_json_src = self.config.resolve_hooks_json()
        filtered_rules = self._build_filtered_skill_rules(profile)
        rules_dest = resolved_target / "library" / "nudges" / "skill-rules.json"
        hooks_json_dest = resolved_target / ".agents" / "hooks.json"

        # Compute diffs for existing text files before overwriting
        for src_f, dst_f in [*skill_ops, *hook_ops]:
            planned.append(str(dst_f))
            if dst_f.exists():
                diff = self._text_diff(dst_f, src_f.read_text("utf-8", errors="replace"))
                if diff:
                    diffs.append(diff)

        if profile.include_hooks and hooks_json_src is not None and hooks_json_src.exists():
            planned.append(str(hooks_json_dest))
            hooks_text = hooks_json_src.read_text(encoding="utf-8")
            if hooks_json_dest.exists():
                diff = self._text_diff(hooks_json_dest, hooks_text)
                if diff:
                    diffs.append(diff)

        rules_text = json.dumps(filtered_rules, indent=2, ensure_ascii=False) + "\n"
        planned.append(str(rules_dest))
        if rules_dest.exists():
            diff = self._text_diff(rules_dest, rules_text)
            if diff:
                diffs.append(diff)

        schema_ok = True
        if not dry_run:
            resolved_target.mkdir(parents=True, exist_ok=True)
            for src_f, dst_f in [*skill_ops, *hook_ops]:
                dst_f.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_f, dst_f)
            if profile.include_hooks and hooks_json_src is not None and hooks_json_src.exists():
                write_text_utf8_lf(hooks_json_dest, hooks_json_src.read_text(encoding="utf-8"))
                schema_ok = validate_hooks_schema(hooks_json_dest)
            write_json_utf8_lf(rules_dest, filtered_rules)

        return SyncReport(
            profile=profile_name,
            target_dir=str(resolved_target),
            dry_run=dry_run,
            synced_skills=list(profile.skills),
            planned_files=planned,
            diffs=diffs,
            hooks_schema_valid=schema_ok,
        )

    @staticmethod
    def _text_diff(existing_path: Path, new_text: str) -> str:
        old_lines = existing_path.read_text(encoding="utf-8", errors="replace").splitlines()
        new_lines = new_text.splitlines()
        diff = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"existing:{existing_path.name}",
                tofile=f"incoming:{existing_path.name}",
                lineterm="",
            )
        )
        return "\n".join(diff)
