"""Unit scaffold initialization and verified deployment sync (`forge.py unit init|sync`)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from forge.config import ForgeConfig, write_json_utf8_lf, write_text_utf8_lf
from forge.gate import GatePipeline
from forge.sanitize import assert_corpus_sanitized
from forge.sync.workspace import SyncReport, WorkspaceSyncer
from forge.units.models import UnitManifest

DOMAIN_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")


class UnitSyncReport(BaseModel):
    """Summary returned by `UnitManager.sync`."""

    domain: str
    target_dir: str
    dry_run: bool
    profile_report: SyncReport | None = None
    unit_skills: list[str] = Field(default_factory=list)
    planned_files: list[str] = Field(default_factory=list)
    gate_passed: bool = True


class InstalledUnitRecord(BaseModel):
    """Installed specialist unit record pinned with SHA-256 tree digest."""

    domain: str
    source: str
    sha256: str
    skills: list[str] = Field(default_factory=list)
    install_dir: str
    installed_at: str


class UnitInstallReport(BaseModel):
    """Summary returned by `UnitManager.install_unit`."""

    source: str
    dry_run: bool
    installed: list[InstalledUnitRecord] = Field(default_factory=list)
    lockfile_path: str


def _is_git_url(source: str) -> bool:
    clean = source.strip()
    return (
        clean.startswith(("git+", "http://", "https://", "ssh://", "git@", "file://"))
        or clean.endswith(".git")
    )


def _sha256_tree(root: Path) -> str:
    hasher = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name in {"GATE_REPORT.json", "BENCHMARK.json"}:
            continue
        rel = path.relative_to(root).as_posix()
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _discover_unit_dirs(src_root: Path) -> list[Path]:
    if (src_root / "unit.yaml").is_file():
        return [src_root]
    found: list[Path] = []
    for base in (src_root / "library" / "units", src_root / "units"):
        if base.is_dir():
            for child in sorted(base.iterdir()):
                if child.is_dir() and (child / "unit.yaml").is_file():
                    found.append(child)
    return found


class UnitManager:
    """Initializes, validates, and deploys canonical specialist units."""

    def __init__(self, config: ForgeConfig) -> None:
        self.config = config

    def unit_dir(self, domain: str, *, writable: bool = False) -> Path:
        slug = domain.strip().lower()
        if not DOMAIN_SLUG_RE.match(slug):
            raise ValueError(
                f"Invalid domain slug {domain!r}; expected lowercase slug (2-49 chars)."
            )
        primary = self.config.units_dir / slug
        if writable:
            return primary
        if (primary / "unit.yaml").is_file():
            return primary
        alt_home = self.config.home_dir / "units" / slug
        if (alt_home / "unit.yaml").is_file():
            return alt_home
        bundled = self.config.assets_dir / "units" / slug
        if (bundled / "unit.yaml").is_file():
            return bundled
        return primary

    def init_unit(
        self,
        domain: str,
        *,
        description: str = "",
        profile: str = "superarchivist-boilerplate",
    ) -> Path:
        """Create canonical `library/units/<domain>/` directory structure."""
        udir = self.unit_dir(domain, writable=True)
        slug = udir.name
        (udir / "corpus").mkdir(parents=True, exist_ok=True)
        (udir / "skills").mkdir(parents=True, exist_ok=True)
        (udir / "evals").mkdir(parents=True, exist_ok=True)

        manifest_path = udir / "unit.yaml"
        if not manifest_path.exists():
            manifest = UnitManifest(
                domain=slug,
                description=description or f"Domain specialist unit for {slug}.",
                profile=profile,
                sanitized_verified=True,
                compliance_notes=[
                    "All corpus inputs must pass `forge.py sanitize` prior to tailoring or upload."
                ],
            )
            manifest.save(udir)

        corpus_readme = udir / "corpus" / "README.md"
        if not corpus_readme.exists():
            write_text_utf8_lf(
                corpus_readme,
                f"# {slug} Sanitized Knowledge Corpus\n\n"
                "Place only sanitized domain materials here via `forge.py sanitize`.\n",
            )

        sources_yaml = udir / "sources.yaml"
        if not sources_yaml.exists():
            write_text_utf8_lf(
                sources_yaml,
                f"domain: {slug}\nsources: []\n",
            )
        return udir

    def sync_unit(
        self,
        domain: str,
        target_dir: Path,
        *,
        include_profile: bool = True,
        dry_run: bool = False,
    ) -> UnitSyncReport:
        """Gate-check unit skills, verify corpus hygiene, and sync into `target_dir`."""
        udir = self.unit_dir(domain)
        manifest = UnitManifest.load(udir)

        corpus_dir = udir / manifest.corpus_dir
        if corpus_dir.exists():
            assert_corpus_sanitized(corpus_dir, self.config)

        skills_root = udir / "skills"
        skill_dirs = sorted(
            [p for p in skills_root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()]
        ) if skills_root.is_dir() else []

        gate = GatePipeline(self.config)
        gate_passed = True
        for sdir in skill_dirs:
            rep = gate.evaluate_skill(sdir, apply_fixes=False)
            if rep.status in {"failed_security", "failed_spec"}:
                gate_passed = False
                raise ValueError(
                    f"Unit skill `{sdir.name}` failed Stage 2 gate ({rep.status}): "
                    f"security={len(rep.security.findings)} spec={rep.spec.warnings}"
                )

        resolved_target = target_dir.resolve()
        profile_report: SyncReport | None = None
        if include_profile and self.config.resolve_profile_path(manifest.profile) is not None:
            profile_report = WorkspaceSyncer(self.config).sync(
                manifest.profile, resolved_target, dry_run=dry_run
            )

        planned: list[str] = list(profile_report.planned_files) if profile_report else []
        synced_names: list[str] = []
        for sdir in skill_dirs:
            synced_names.append(sdir.name)
            dest_skill_dir = resolved_target / ".agents" / "skills" / sdir.name
            for src_file in sorted(sdir.rglob("*")):
                if src_file.is_file() and src_file.name not in {
                    "BENCHMARK.json",
                    "GATE_REPORT.json",
                }:
                    rel = src_file.relative_to(sdir)
                    dst = dest_skill_dir / rel
                    planned.append(str(dst))
                    if not dry_run:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dst)

        manifest_dst = resolved_target / ".agents" / f"unit-{manifest.domain}.yaml"
        planned.append(str(manifest_dst))
        if not dry_run:
            write_text_utf8_lf(
                manifest_dst,
                (udir / "unit.yaml").read_text(encoding="utf-8"),
            )

        return UnitSyncReport(
            domain=manifest.domain,
            target_dir=str(resolved_target),
            dry_run=dry_run,
            profile_report=profile_report,
            unit_skills=synced_names,
            planned_files=planned,
            gate_passed=gate_passed,
        )

    def list_units(self) -> list[str]:
        """List installed domain unit slugs under `config.units_dir` (and assets)."""
        slugs: set[str] = set()
        for base in (self.config.units_dir, self.config.assets_dir / "units"):
            if base.is_dir():
                for child in base.iterdir():
                    if child.is_dir() and (child / "unit.yaml").is_file():
                        slugs.add(child.name)
        return sorted(slugs)

    def install_unit(
        self,
        source: str,
        *,
        unit: str = "",
        dry_run: bool = False,
        run_external: bool = True,
    ) -> UnitInstallReport:
        """Install one or more units from a local directory or Git URL with strict gating."""
        clean_src = source.strip()
        if not clean_src or clean_src.startswith("-"):
            raise ValueError(f"Invalid unit source: {source!r}")

        if _is_git_url(clean_src):
            clone_url = clean_src[4:] if clean_src.startswith("git+") else clean_src
            if clone_url.startswith("-"):
                raise ValueError(f"Refused unsafe Git URL: {clone_url!r}")
            with tempfile.TemporaryDirectory(prefix="forge-unit-install-") as tmp_dir:
                clone_dest = Path(tmp_dir) / "repo"
                proc = subprocess.run(
                    ["git", "clone", "--depth", "1", clone_url, str(clone_dest)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                if proc.returncode != 0:
                    raise RuntimeError(
                        f"git clone failed ({proc.returncode}): {proc.stderr.strip()}"
                    )
                return self._install_from_local_root(
                    clone_dest,
                    source_label=clean_src,
                    unit=unit,
                    dry_run=dry_run,
                    run_external=run_external,
                )

        local_root = Path(clean_src).expanduser().resolve()
        if not local_root.exists():
            raise FileNotFoundError(f"Unit source path not found: {local_root}")
        return self._install_from_local_root(
            local_root,
            source_label=str(local_root),
            unit=unit,
            dry_run=dry_run,
            run_external=run_external,
        )

    def _install_from_local_root(
        self,
        src_root: Path,
        *,
        source_label: str,
        unit: str,
        dry_run: bool,
        run_external: bool,
    ) -> UnitInstallReport:
        candidates = _discover_unit_dirs(src_root)
        if not candidates:
            raise FileNotFoundError(f"No `unit.yaml` found under {src_root}")

        want = unit.strip().lower()
        if want:
            candidates = [c for c in candidates if UnitManifest.load(c).domain == want]
            if not candidates:
                raise FileNotFoundError(f"Unit `{want}` not found under {src_root}")

        gate = GatePipeline(self.config)
        records: list[InstalledUnitRecord] = []
        now_iso = datetime.now(UTC).isoformat()

        for udir in candidates:
            manifest = UnitManifest.load(udir)
            slug = manifest.domain
            if not DOMAIN_SLUG_RE.match(slug):
                raise ValueError(f"Invalid unit domain slug: {slug!r}")

            corpus_dir = udir / manifest.corpus_dir
            if corpus_dir.is_dir():
                assert_corpus_sanitized(corpus_dir, self.config)

            skills_dir = udir / "skills"
            skill_names: list[str] = []
            if skills_dir.is_dir():
                for sdir in sorted(skills_dir.iterdir()):
                    if sdir.is_dir() and (sdir / "SKILL.md").is_file():
                        rep = gate.evaluate_skill(
                            sdir,
                            apply_fixes=False,
                            move_rejected=False,
                            run_external=run_external,
                        )
                        if rep.status in {"failed_security", "failed_spec"}:
                            raise ValueError(
                                f"Refused unit `{slug}`: skill `{sdir.name}` failed Stage 2 gate "
                                f"({rep.status})"
                            )
                        skill_names.append(sdir.name)

            digest = _sha256_tree(udir)
            dest_dir = self.unit_dir(slug, writable=True)
            if not dry_run:
                if dest_dir.exists():
                    shutil.rmtree(dest_dir)
                dest_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(udir, dest_dir)

            records.append(
                InstalledUnitRecord(
                    domain=slug,
                    source=source_label,
                    sha256=digest,
                    skills=skill_names,
                    install_dir=str(dest_dir),
                    installed_at=now_iso,
                )
            )

        lock_path = self.config.units_lockfile_path
        if not dry_run:
            existing: dict[str, object] = {}
            if lock_path.is_file():
                try:
                    loaded = json.loads(lock_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict) and isinstance(loaded.get("units"), dict):
                        existing = dict(loaded["units"])
                except (OSError, json.JSONDecodeError):
                    existing = {}
            for rec in records:
                existing[rec.domain] = rec.model_dump()
            write_json_utf8_lf(lock_path, {"version": "1.0", "units": existing})

        return UnitInstallReport(
            source=source_label,
            dry_run=dry_run,
            installed=records,
            lockfile_path=str(lock_path),
        )
