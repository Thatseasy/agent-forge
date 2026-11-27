"""Zero-friction onboarding wizard (`agent-forge init`) and graceful degradation checks."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from forge.config import ForgeConfig, write_text_utf8_lf
from forge.doctor import DoctorReport, run_doctor
from forge.paths import ensure_agent_forge_home, resolve_target_workspace
from forge.research.cache import ResearchCache
from forge.setup import SetupOrchestrator

OPTIONAL_API_KEY_NAMES: tuple[str, ...] = (
    "PERPLEXITY_API_KEY",
    "FIRECRAWL_API_KEY",
    "CONTEXT7_API_KEY",
    "EXA_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AA_API_KEY",
)

SECRET_WRAPPER_TOOLS: tuple[str, ...] = ("infisical", "doppler", "op")

DEFAULT_CONFIG_TOML = """# agent-forge user configuration (stored under AGENT_FORGE_HOME)
# Zero-default exfiltration: optional cloud exports and telemetry stay disabled by default.

[telemetry]
enabled = false

[cloud_sync]
enabled = false

[defaults]
judge_model = "gpt-5.6-luna"
profile = "core-minimal"
"""


class FeatureAvailability(BaseModel):
    """Availability status of one agent-forge capability."""

    feature: str
    tier: str
    available: bool
    mode: str
    requirement: str


class InitReport(BaseModel):
    """Structured result of running `run_init`."""

    status: str = "ok"
    home_dir: str
    target_dir: str
    config_toml_path: str
    secrets_env_path: str
    cache_db_path: str
    seeded_files: list[str] = Field(default_factory=list)
    secret_wrappers_detected: list[str] = Field(default_factory=list)
    features: list[FeatureAvailability] = Field(default_factory=list)
    doctor: DoctorReport
    setup_ran: bool = False
    playwright_installed: bool = False
    next_commands: list[str] = Field(default_factory=list)


def _restrict_file_permissions(path: Path) -> None:
    """Restrict `secrets.env` permissions (`0600` on POSIX, best-effort on Windows)."""
    if not path.exists():
        return
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def upsert_secrets_env(secrets_path: Path, updates: dict[str, str]) -> Path:
    """Create or update `AGENT_FORGE_HOME/secrets.env` with restrictive permissions (`0600`)."""
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    existing_lines: list[str] = []
    existing_map: dict[str, str] = {}
    if secrets_path.is_file():
        for raw in secrets_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                existing_lines.append(raw)
                continue
            k, v = line.split("=", 1)
            existing_map[k.strip()] = v.strip()

    for k, v in updates.items():
        clean_k = k.strip()
        clean_v = v.strip()
        if clean_k and clean_v:
            existing_map[clean_k] = f'"{clean_v}"' if " " in clean_v else clean_v

    header = "# Stored in AGENT_FORGE_HOME outside any Git repository (chmod 0600)."
    rendered = [header, ""]
    for key in sorted(existing_map):
        rendered.append(f"{key}={existing_map[key]}")
    write_text_utf8_lf(secrets_path, "\n".join(rendered) + "\n")
    _restrict_file_permissions(secrets_path)
    return secrets_path


def _seed_home_assets(cfg: ForgeConfig, dry_run: bool) -> list[str]:
    """Copy read-only default catalogs and generic example unit into `AGENT_FORGE_HOME`."""
    seeded: list[str] = []
    assets = cfg.assets_dir
    mappings: list[tuple[Path, Path]] = []

    for sub_glob in (
        ("clis", "*.yaml"),
        ("clis", "*.json"),
        ("nudges", "discovery_sources.yaml"),
        ("nudges", "skill-rules.json"),
        ("profiles", "*.yaml"),
    ):
        src_dir = assets / sub_glob[0]
        if not src_dir.is_dir():
            continue
        for src in sorted(src_dir.glob(sub_glob[1])):
            if src.is_file():
                dst = cfg.home_dir / "library" / sub_glob[0] / src.name
                if not dst.exists():
                    mappings.append((src, dst))

    example_unit_src = assets / "units" / "example-python-cli"
    example_unit_dst = cfg.home_dir / "library" / "units" / "example-python-cli"
    if example_unit_src.is_dir() and not example_unit_dst.exists():
        for src in sorted(p for p in example_unit_src.rglob("*") if p.is_file()):
            rel = src.relative_to(example_unit_src)
            mappings.append((src, example_unit_dst / rel))

    for src, dst in mappings:
        seeded.append(str(dst))
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return seeded


def build_feature_matrix(doctor: DoctorReport) -> list[FeatureAvailability]:
    """Build explicit 0-USD offline vs optional API/extra feature readiness matrix."""
    keys = doctor.api_keys_configured
    has_research_key = any(
        keys.get(k, False)
        for k in ("PERPLEXITY_API_KEY", "FIRECRAWL_API_KEY", "CONTEXT7_API_KEY", "EXA_API_KEY")
    )
    has_llm_key = any(
        keys.get(k, False) for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY")
    )
    has_pw = doctor.tools.get("playwright", False)
    has_docker = doctor.tools.get("docker", False)

    return [
        FeatureAvailability(
            feature="Local Setup, Gate, Audit, Sanitize, Lock, Prune",
            tier="0-USD Core",
            available=True,
            mode="ready (offline)",
            requirement="none",
        ),
        FeatureAvailability(
            feature="Live Web/Docs Research (/forge-audit, /forge-research)",
            tier="Optional API",
            available=has_research_key,
            mode="live" if has_research_key else "graceful (--dry-run / cached)",
            requirement="PERPLEXITY_API_KEY / FIRECRAWL_API_KEY / EXA_API_KEY / CONTEXT7_API_KEY",
        ),
        FeatureAvailability(
            feature="Live 3-Arm LLM Benchmark & Flash-Judge (/forge-bench)",
            tier="Optional API",
            available=has_llm_key,
            mode="live" if has_llm_key else "graceful (--dry-run)",
            requirement="OPENAI_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY",
        ),
        FeatureAvailability(
            feature="Live Browser Exploratory QA & Video Audit (/forge-qa)",
            tier="Optional Binary",
            available=has_pw,
            mode="live" if has_pw else "graceful (--dry-run)",
            requirement="playwright chromium (uvx playwright install chromium)",
        ),
        FeatureAvailability(
            feature="Docker Container Benchmark Isolation",
            tier="Optional Binary",
            available=has_docker,
            mode="docker" if has_docker else "graceful (--isolation worktree)",
            requirement="Docker daemon",
        ),
    ]


def _install_playwright_chromium() -> bool:
    """Optionally install Chromium via `uv run` or `uvx playwright install chromium`."""
    candidates: list[list[str]] = []
    if shutil.which("playwright"):
        candidates.append(["playwright", "install", "chromium"])
    if shutil.which("uv"):
        candidates.append(["uv", "run", "playwright", "install", "chromium"])
    for cmd in candidates:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
            if proc.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            continue
    return False


def run_init(
    config: ForgeConfig,
    *,
    target_dir: Path | None = None,
    secret_updates: dict[str, str] | None = None,
    install_playwright: bool = False,
    with_setup: bool = False,
    dry_run: bool = False,
) -> InitReport:
    """Initialize `AGENT_FORGE_HOME`, secrets boundary, catalogs, and optional workspace setup."""
    resolved_target = resolve_target_workspace(target_dir)
    home = config.home_dir
    config_toml = home / "config.toml"
    secrets_env = home / "secrets.env"
    cache_db = home / "state" / "research_cache.db"

    seeded_files: list[str] = []
    if not dry_run:
        ensure_agent_forge_home(home)
        if not config_toml.exists():
            write_text_utf8_lf(config_toml, DEFAULT_CONFIG_TOML)
            seeded_files.append(str(config_toml))
        ResearchCache(cache_db)
        seeded_files.extend(_seed_home_assets(config, dry_run=False))
        if secret_updates or not secrets_env.exists():
            upsert_secrets_env(secrets_env, secret_updates or {})
    else:
        seeded_files.extend(_seed_home_assets(config, dry_run=True))

    pw_installed = _install_playwright_chromium() if (install_playwright and not dry_run) else False

    refreshed_cfg = ForgeConfig.load(root=config.root)
    doctor = run_doctor(refreshed_cfg, target_dir=resolved_target, check_hooks=True)

    setup_ran = False
    if with_setup and not dry_run:
        SetupOrchestrator(refreshed_cfg).run(resolved_target, dry_run=False)
        setup_ran = True

    wrappers = [tool for tool in SECRET_WRAPPER_TOOLS if shutil.which(tool) is not None]
    features = build_feature_matrix(doctor)

    next_cmds = [
        "/forge-setup   # Assess repo maturity and scaffold docs, rules, and opt-in hooks",
        "/forge-audit   # Run read-only portfolio/repo audit without modifying project files",
        "/forge-gate    # Run deterministic 0-USD Security, Spec, and PII checks",
        "/forge-unit    # Install or sync curated specialist units into this workspace",
    ]

    status = "ok" if doctor.status != "error" else "error"
    return InitReport(
        status=status,
        home_dir=str(home),
        target_dir=str(resolved_target),
        config_toml_path=str(config_toml),
        secrets_env_path=str(secrets_env),
        cache_db_path=str(cache_db),
        seeded_files=seeded_files,
        secret_wrappers_detected=wrappers,
        features=features,
        doctor=doctor,
        setup_ran=setup_ran,
        playwright_installed=pw_installed,
        next_commands=next_cmds,
    )


def summarize_init_json(report: InitReport) -> dict[str, Any]:
    """Return compact JSON summary matching Context-Firewall contract."""
    return {
        "status": report.status,
        "home_dir": report.home_dir,
        "target_dir": report.target_dir,
        "secrets_env_path": report.secrets_env_path,
        "hooks_valid": bool(report.doctor.hooks_check and report.doctor.hooks_check.valid),
        "secret_wrappers": report.secret_wrappers_detected,
        "features": [f.model_dump() for f in report.features],
        "next_commands": report.next_commands,
    }
