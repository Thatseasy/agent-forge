"""Lean Slim-MCP server exposing 5 coarse-grained Context-Firewall tools over stdio."""

from __future__ import annotations

import importlib.util
import inspect
import json
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

from forge.archivist import run_archive_audit
from forge.benchmark import AblationArena, CostOracle
from forge.config import ForgeConfig, write_json_utf8_lf
from forge.discovery import DiscoveryCollector
from forge.gate import GatePipeline
from forge.paths import ensure_agent_forge_home, resolve_target_workspace
from forge.qa import QAExplorer
from forge.refine import RetroEngine, audit_and_prune_skills
from forge.research import ResearchRouter
from forge.sanitize import assert_corpus_sanitized, sanitize_path
from forge.setup import SetupOrchestrator
from forge.sourcer import run_sourcing
from forge.sync import LockfileManager
from forge.units import UnitManager

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ModuleNotFoundError:  # pragma: no cover - mcp 1.x compatibility
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found,no-redef,attr-defined]

_F = TypeVar("_F", bound=Callable[..., str])

mcp = FastMCP("agent-forge")


def _register_tool() -> Callable[[_F], _F]:
    """Register an MCP tool without redundant output schemas when supported by the SDK."""
    params = inspect.signature(mcp.tool).parameters
    if "structured_output" in params:
        return mcp.tool(structured_output=False)
    return mcp.tool()


def _compact_json(
    *,
    status: Literal["ok", "error"],
    summary_one_line: str,
    metrics: dict[str, Any] | None = None,
    artifact_paths: list[str] | None = None,
) -> str:
    clean_summary = " ".join(summary_one_line.splitlines()).strip()
    payload = {
        "status": status,
        "summary_one_line": clean_summary[:240],
        "metrics": metrics or {},
        "artifact_paths": artifact_paths or [],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _error_json(exc: Exception, artifact_paths: list[str] | None = None) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    return _compact_json(
        status="error",
        summary_one_line=msg,
        metrics={"error_type": type(exc).__name__},
        artifact_paths=artifact_paths,
    )


def _new_run_dir(cfg: ForgeConfig, prefix: str) -> Path:
    home_dir = ensure_agent_forge_home(cfg.home_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = home_dir / "runs" / f"{prefix}_{stamp}_{uuid.uuid4().hex[:6]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _parse_json_obj(raw: str, label: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must decode to a JSON object.")
    return parsed


def _resolve_target(target_path: str) -> Path:
    clean = target_path.strip()
    return resolve_target_workspace(clean if clean else None)


@_register_tool()
def forge_doctor(target_path: str = "") -> str:
    """Check runtime binaries, API key bools, AGENT_FORGE_HOME, assets, and hooks."""
    try:
        cfg = ForgeConfig.load()
        target_dir = _resolve_target(target_path)
        run_dir = _new_run_dir(cfg, "doctor")

        tools = {
            "uv": bool(shutil.which("uv")),
            "git": bool(shutil.which("git")),
            "playwright": bool(
                importlib.util.find_spec("playwright") is not None or shutil.which("playwright")
            ),
            "docker": bool(shutil.which("docker")),
        }
        api_keys = {
            "perplexity": bool(cfg.perplexity_api_key),
            "firecrawl": bool(cfg.firecrawl_api_key),
            "exa": bool(cfg.exa_api_key),
            "context7": bool(cfg.context7_api_key),
            "gemini": bool(cfg.gemini_api_key),
            "openai": bool(cfg.openai_api_key),
            "anthropic": bool(cfg.anthropic_api_key),
            "artificial_analysis": bool(cfg.aa_api_key),
            "notion": bool(cfg.notion_api_key),
        }
        assets_ok = (cfg.assets_dir / "hooks.json").is_file() and (
            cfg.assets_dir / "clis" / "sourcer_catalog.yaml"
        ).is_file()
        hooks = {
            "workspace_hooks_json": (target_dir / ".agents" / "hooks.json").is_file(),
            "package_hooks_json": cfg.resolve_hooks_json() is not None,
        }
        metrics: dict[str, Any] = {
            "tools": tools,
            "api_keys_configured": api_keys,
            "agent_forge_home": str(cfg.home_dir),
            "home_exists": cfg.home_dir.is_dir(),
            "package_assets_dir": str(cfg.assets_dir),
            "package_assets_ok": assets_ok,
            "hooks_present": hooks,
            "target_dir": str(target_dir),
        }
        report_path = run_dir / "doctor.json"
        write_json_utf8_lf(report_path, metrics)

        ready_tools = sum(1 for v in tools.values() if v)
        ready_keys = sum(1 for v in api_keys.values() if v)
        return _compact_json(
            status="ok",
            summary_one_line=(
                f"tools={ready_tools}/4 keys={ready_keys}/{len(api_keys)} "
                f"assets={'ok' if assets_ok else 'missing'} "
                f"hooks={'ok' if any(hooks.values()) else 'missing'}"
            ),
            metrics=metrics,
            artifact_paths=[str(report_path)],
        )
    except Exception as exc:
        return _error_json(exc)


@_register_tool()
def forge_workspace(
    action: Literal["audit", "source", "setup", "gate", "sanitize"],
    target_path: str = "",
    options_json: str = "{}",
) -> str:
    """Run 0-USD workspace audit, source, setup, gate, or sanitize and log artifacts."""
    try:
        cfg = ForgeConfig.load()
        target_dir = _resolve_target(target_path)
        opts = _parse_json_obj(options_json, "options_json")
        dry_run = bool(opts.get("dry_run", False))
        run_dir = _new_run_dir(cfg, f"workspace_{action}")

        if action == "audit":
            raw_projects = opts.get("projects")
            projects = (
                [Path(str(p)).expanduser().resolve() for p in raw_projects]
                if isinstance(raw_projects, list) and raw_projects
                else None
            )
            raw_roots = opts.get("roots")
            roots = (
                [Path(str(r)).expanduser().resolve() for r in raw_roots]
                if isinstance(raw_roots, list) and raw_roots
                else None
            )
            if projects is None and roots is None:
                projects = [target_dir]
            extract_skills = bool(opts.get("extract_skills", not dry_run))
            audit_dir = run_dir / "archive_audit"
            inbox_dir = (
                Path(str(opts["inbox_dir"])).expanduser().resolve()
                if opts.get("inbox_dir")
                else (cfg.home_dir / "staging" / "inbox")
            )
            audit_rep, ext_rep = run_archive_audit(
                projects=projects,
                roots=roots,
                out_dir=audit_dir,
                extract_skills=extract_skills,
                inbox_dir=inbox_dir,
                config=cfg,
            )
            extracted = len(ext_rep.candidates) if ext_rep else 0
            artifacts = [
                str(audit_dir / "ARCHIVE_AUDIT.json"),
                str(audit_dir / "ARCHIVE_AUDIT.md"),
            ]
            return _compact_json(
                status="ok",
                summary_one_line=(
                    f"Audited {audit_rep.project_count} project(s), "
                    f"extracted {extracted} candidate(s)."
                ),
                metrics={
                    "projects_scanned": audit_rep.project_count,
                    "extracted_skills": extracted,
                    "dry_run": dry_run,
                },
                artifact_paths=artifacts,
            )

        if action == "source":
            effective_dry = bool(opts["dry_run"]) if "dry_run" in opts else True
            out_dir = (
                target_dir if bool(opts.get("write_to_workspace", False)) else run_dir
            )
            rep = run_sourcing(
                target_dir,
                config=cfg,
                out_dir=out_dir,
                min_sources=int(opts.get("min_sources", 3)),
                max_sources=int(opts.get("max_sources", 5)),
                dry_run=effective_dry,
                write_artifacts=bool(opts.get("write_artifacts", True)),
            )
            return _compact_json(
                status="ok",
                summary_one_line=(
                    f"Sourced {len(rep.top_sources)} source(s) for "
                    f"domains={','.join(rep.detection.domains) or 'general'}."
                ),
                metrics={
                    "domains": rep.detection.domains,
                    "top_sources_count": len(rep.top_sources),
                    "top_source_ids": [s.candidate.id for s in rep.top_sources],
                    "dry_run": rep.dry_run,
                },
                artifact_paths=list(rep.artifacts.values()),
            )

        if action == "setup":
            rep_setup = SetupOrchestrator(cfg).run(
                target_dir,
                profile=str(opts.get("profile", "")),
                dry_run=dry_run,
                live_source=bool(opts.get("live_source", False)),
            )
            report_path = run_dir / "setup_report.json"
            write_json_utf8_lf(report_path, rep_setup.model_dump(mode="json"))
            return _compact_json(
                status="ok",
                summary_one_line=(
                    f"Setup {rep_setup.assessment.maturity} profile={rep_setup.profile} "
                    f"created={len(rep_setup.created_files)} "
                    f"skipped={len(rep_setup.skipped_files)}."
                ),
                metrics={
                    "maturity": rep_setup.assessment.maturity,
                    "profile": rep_setup.profile,
                    "created_count": len(rep_setup.created_files),
                    "skipped_count": len(rep_setup.skipped_files),
                    "hooks_schema_valid": rep_setup.hooks_schema_valid,
                    "dry_run": rep_setup.dry_run,
                },
                artifact_paths=[str(report_path)],
            )

        if action == "gate":
            pipeline = GatePipeline(cfg)
            apply_fixes = False if dry_run else bool(opts.get("apply_fixes", True))
            move_rejected = False if dry_run else bool(opts.get("move_rejected", False))
            skill_arg = opts.get("skill_path")
            skill_candidate = (
                Path(str(skill_arg)).expanduser().resolve()
                if skill_arg
                else (target_dir if (target_dir / "SKILL.md").is_file() else None)
            )
            if skill_candidate is not None and not bool(opts.get("all", False)):
                reports = [
                    pipeline.evaluate_skill(
                        skill_candidate,
                        apply_fixes=apply_fixes,
                        move_rejected=move_rejected,
                    )
                ]
            else:
                reports = pipeline.evaluate_all(apply_fixes=apply_fixes)

            failed = sum(1 for r in reports if r.status in {"failed_security", "failed_spec"})
            passed = sum(1 for r in reports if r.status == "passed")
            report_path = run_dir / "gate_report.json"
            write_json_utf8_lf(
                report_path,
                {
                    "evaluated": len(reports),
                    "reports": [r.model_dump(mode="json") for r in reports],
                },
            )
            return _compact_json(
                status="error" if failed > 0 else "ok",
                summary_one_line=(
                    f"Gate evaluated {len(reports)} skill(s): passed={passed}, failed={failed}."
                ),
                metrics={
                    "evaluated": len(reports),
                    "passed": passed,
                    "failed": failed,
                    "statuses": {r.skill_slug: r.status for r in reports},
                    "dry_run": dry_run,
                },
                artifact_paths=[str(report_path)],
            )

        if action == "sanitize":
            input_path = (
                Path(str(opts["input_path"])).expanduser().resolve()
                if opts.get("input_path")
                else target_dir
            )
            if bool(opts.get("verify_only", False)):
                checked = assert_corpus_sanitized(input_path, cfg)
                report_path = run_dir / "sanitize_verify.json"
                write_json_utf8_lf(report_path, {"verified_files": checked})
                return _compact_json(
                    status="ok",
                    summary_one_line=f"Verified {len(checked)} sanitized file(s).",
                    metrics={"verified_files": len(checked)},
                    artifact_paths=[str(report_path)],
                )

            out_path = (
                Path(str(opts["out_path"])).expanduser().resolve()
                if opts.get("out_path")
                else (run_dir / "sanitized")
            )
            names_file = (
                Path(str(opts["names_file"])).expanduser().resolve()
                if opts.get("names_file")
                else None
            )
            raw_domains = opts.get("allow_domains")
            allow_domains = (
                [str(d) for d in raw_domains] if isinstance(raw_domains, list) else None
            )
            rep_san = sanitize_path(
                input_path,
                out_path,
                names_file=names_file,
                allowlist_domains=allow_domains,
                dry_run=dry_run,
            )
            report_path = run_dir / "sanitize_report.json"
            write_json_utf8_lf(report_path, rep_san.model_dump(mode="json"))
            redactions = sum(rep_san.counts_by_category.values())
            return _compact_json(
                status="ok",
                summary_one_line=(
                    f"Sanitized {rep_san.total_files} file(s), "
                    f"redacted {redactions} item(s)."
                ),
                metrics={
                    "total_files": rep_san.total_files,
                    "modified_files": rep_san.modified_files,
                    "redactions": redactions,
                    "counts_by_category": rep_san.counts_by_category,
                    "dry_run": rep_san.dry_run,
                },
                artifact_paths=[str(report_path)],
            )

        raise ValueError(f"Unsupported workspace action: {action!r}")
    except Exception as exc:
        return _error_json(exc)


@_register_tool()
def forge_research(
    mode: Literal["discover", "search", "cost_oracle"],
    query: str = "",
    provider: str = "sonar",
) -> str:
    """Run discovery radar, cached research search, or CostOracle pricing lookup."""
    try:
        cfg = ForgeConfig.load()
        run_dir = _new_run_dir(cfg, f"research_{mode}")

        if mode == "discover":
            q_clean = query.strip().lower()
            dry_run = q_clean != "live" and provider.strip().lower() != "live"
            candidates = DiscoveryCollector(cfg).collect(dry_run=dry_run)
            report_path = run_dir / "discover.json"
            write_json_utf8_lf(
                report_path,
                [c.model_dump(mode="json") for c in candidates],
            )
            return _compact_json(
                status="ok",
                summary_one_line=f"Discovered {len(candidates)} candidate(s) (dry_run={dry_run}).",
                metrics={
                    "count": len(candidates),
                    "slugs": [c.slug for c in candidates[:10]],
                    "dry_run": dry_run,
                },
                artifact_paths=[str(report_path)],
            )

        if mode == "search":
            q_text = query.strip()
            dry_run = provider.strip().lower() == "dry_run"
            eff_provider = "sonar" if dry_run else provider.strip().lower()
            if q_text.lower().startswith("dry_run:"):
                dry_run = True
                q_text = q_text.split(":", 1)[1].strip()
            if not q_text:
                raise ValueError("query is required for research mode='search'.")
            md_path = run_dir / "research.md"
            res = ResearchRouter(cfg).query(
                query=q_text,
                provider=eff_provider,
                dry_run=dry_run,
                out_path=md_path,
            )
            return _compact_json(
                status="ok",
                summary_one_line=(
                    f"Research via {res.provider} cached={res.cached} "
                    f"sources={len(res.sources)}."
                ),
                metrics={
                    "provider": res.provider,
                    "cached": res.cached,
                    "dry_run": res.dry_run,
                    "sources_count": len(res.sources),
                },
                artifact_paths=[str(md_path)],
            )

        if mode == "cost_oracle":
            oracle = CostOracle(cfg)
            q_clean = query.strip()
            q_lower = q_clean.lower()
            report_path = run_dir / "cost_oracle.json"
            if q_lower in {"fast", "balanced", "judge"}:
                recs = oracle.recommend_arbitrage_models(tier=q_lower, limit=5)  # type: ignore[arg-type]
                payload = {"tier": q_lower, "models": [m.model_dump() for m in recs]}
                write_json_utf8_lf(report_path, payload)
                return _compact_json(
                    status="ok",
                    summary_one_line=(
                        f"CostOracle returned {len(recs)} model(s) for tier={q_lower}."
                    ),
                    metrics={
                        "tier": q_lower,
                        "count": len(recs),
                        "top_model": recs[0].model_id if recs else "",
                    },
                    artifact_paths=[str(report_path)],
                )
            if q_clean and q_lower != "all":
                rate = oracle.lookup(q_clean)
                write_json_utf8_lf(report_path, rate.model_dump())
                return _compact_json(
                    status="ok",
                    summary_one_line=(
                        f"{rate.model_id}: in=${rate.input_per_m}/M out=${rate.output_per_m}/M."
                    ),
                    metrics=rate.model_dump(),
                    artifact_paths=[str(report_path)],
                )
            all_rates = oracle.all_models()
            write_json_utf8_lf(report_path, {"models": [m.model_dump() for m in all_rates]})
            return _compact_json(
                status="ok",
                summary_one_line=f"CostOracle loaded {len(all_rates)} model rate(s).",
                metrics={
                    "count": len(all_rates),
                    "cheapest_model": all_rates[0].model_id if all_rates else "",
                },
                artifact_paths=[str(report_path)],
            )

        raise ValueError(f"Unsupported research mode: {mode!r}")
    except Exception as exc:
        return _error_json(exc)


@_register_tool()
def forge_eval(
    action: Literal["bench", "qa", "unit"],
    target_path: str = "",
    spec_json: str = "{}",
) -> str:
    """Run Stage 3 ablation bench, exploratory QA, or specialist unit list/install/sync."""
    try:
        cfg = ForgeConfig.load()
        target_dir = _resolve_target(target_path)
        spec = _parse_json_obj(spec_json, "spec_json")
        dry_run = bool(spec.get("dry_run", False))
        run_dir = _new_run_dir(cfg, f"eval_{action}")

        if action == "bench":
            suite = str(spec.get("suite") or "").strip()
            underdog_raw = str(spec.get("underdog") or target_path or "").strip()
            if not suite or not underdog_raw:
                raise ValueError("spec_json.suite and target_path/spec_json.underdog are required.")
            underdog_dir = Path(underdog_raw).expanduser().resolve()
            hype_dir = (
                Path(str(spec["hype"])).expanduser().resolve() if spec.get("hype") else None
            )
            raw_compare = spec.get("compare_models")
            compare_models = (
                [str(m) for m in raw_compare] if isinstance(raw_compare, list) else []
            )
            runner = str(spec.get("runner") or ("dry-run" if dry_run else "auto"))
            rep = AblationArena(
                cfg,
                isolation=str(spec.get("isolation", "worktree")),
                runner=runner,
            ).run(
                suite_name=suite,
                underdog_dir=underdog_dir,
                hype_dir=hype_dir,
                trials=int(spec.get("trials", 3)),
                compare_models=compare_models,
                dry_run=dry_run,
            )
            report_path = run_dir / "bench_report.json"
            write_json_utf8_lf(report_path, rep.model_dump(mode="json"))
            artifacts = [str(report_path)]
            bench_file = underdog_dir / "BENCHMARK.json"
            if bench_file.is_file():
                artifacts.append(str(bench_file))
            arms_metrics = {
                k: {
                    "pass_pow_k": m.pass_pow_k,
                    "value_score": m.value_score,
                    "mean_cost_usd": m.mean_cost_usd,
                }
                for k, m in rep.arms.items()
            }
            return _compact_json(
                status="ok",
                summary_one_line=f"Benchmarked suite={rep.suite_id} across {len(rep.arms)} arm(s).",
                metrics={"suite_id": rep.suite_id, "arms": arms_metrics, "dry_run": dry_run},
                artifact_paths=artifacts,
            )

        if action == "qa":
            url = str(spec.get("url") or target_path or "").strip()
            if not url:
                raise ValueError("spec_json.url or target_path is required for qa.")
            charter = str(spec.get("charter", "all"))
            extra_domains = spec.get("allowed_domains")
            domains = ["localhost", "127.0.0.1"]
            if isinstance(extra_domains, list):
                domains.extend(str(d) for d in extra_domains)
            storage_state = (
                Path(str(spec["storage_state"])).expanduser().resolve()
                if spec.get("storage_state")
                else None
            )
            qa_dir = run_dir / "qa_artifacts"
            qa_rep = QAExplorer(cfg).explore(
                url=url,
                charter=charter,
                out_dir=qa_dir,
                storage_state=storage_state,
                allowed_domains=domains,
                dry_run=dry_run,
            )
            report_path = run_dir / "qa_report.json"
            write_json_utf8_lf(report_path, qa_rep.model_dump(mode="json"))
            artifacts = [str(report_path)]
            for extra in (qa_rep.video_clip_path, qa_rep.reproduction_spec_path):
                if extra:
                    artifacts.append(extra)
            return _compact_json(
                status="ok",
                summary_one_line=(
                    f"QA explored {url} ({charter}): {len(qa_rep.findings)} finding(s)."
                ),
                metrics={
                    "url": url,
                    "charter": charter,
                    "findings_count": len(qa_rep.findings),
                    "dry_run": dry_run,
                },
                artifact_paths=artifacts,
            )

        if action == "unit":
            mgr = UnitManager(cfg)
            op = str(spec.get("op") or spec.get("action") or "list").strip().lower()
            if op == "list":
                units = mgr.list_units()
                report_path = run_dir / "units_list.json"
                write_json_utf8_lf(report_path, {"units": units})
                return _compact_json(
                    status="ok",
                    summary_one_line=f"Listed {len(units)} specialist unit(s).",
                    metrics={"count": len(units), "units": units},
                    artifact_paths=[str(report_path)],
                )
            if op == "install":
                source = str(spec.get("source") or target_path or "").strip()
                if not source:
                    raise ValueError("spec_json.source or target_path required for unit install.")
                inst_rep = mgr.install_unit(
                    source,
                    unit=str(spec.get("unit", "")),
                    dry_run=dry_run,
                )
                report_path = run_dir / "unit_install.json"
                write_json_utf8_lf(report_path, inst_rep.model_dump(mode="json"))
                return _compact_json(
                    status="ok",
                    summary_one_line=(
                        f"Installed {len(inst_rep.installed)} unit(s) (dry_run={inst_rep.dry_run})."
                    ),
                    metrics={
                        "installed_count": len(inst_rep.installed),
                        "domains": [r.domain for r in inst_rep.installed],
                        "dry_run": inst_rep.dry_run,
                    },
                    artifact_paths=[str(report_path)],
                )
            if op == "sync":
                domain = str(spec.get("domain") or spec.get("unit") or "").strip()
                if not domain:
                    raise ValueError("spec_json.domain is required for unit sync.")
                sync_rep = mgr.sync_unit(
                    domain,
                    target_dir,
                    include_profile=bool(spec.get("include_profile", True)),
                    dry_run=dry_run,
                )
                report_path = run_dir / "unit_sync.json"
                write_json_utf8_lf(report_path, sync_rep.model_dump(mode="json"))
                return _compact_json(
                    status="ok",
                    summary_one_line=(
                        f"Synced unit={sync_rep.domain} skills={len(sync_rep.unit_skills)} "
                        f"files={len(sync_rep.planned_files)}."
                    ),
                    metrics={
                        "domain": sync_rep.domain,
                        "unit_skills": sync_rep.unit_skills,
                        "planned_files_count": len(sync_rep.planned_files),
                        "gate_passed": sync_rep.gate_passed,
                        "dry_run": sync_rep.dry_run,
                    },
                    artifact_paths=[str(report_path)],
                )
            raise ValueError(f"Unsupported unit op: {op!r}")

        raise ValueError(f"Unsupported eval action: {action!r}")
    except Exception as exc:
        return _error_json(exc)


@_register_tool()
def forge_evolve(
    action: Literal["retro", "nudge", "prune", "lock"],
    target_path: str = "",
    options_json: str = "{}",
) -> str:
    """Run Stage 4/5 retro nudges, nudge apply, skill pruning, or lockfile verify."""
    try:
        cfg = ForgeConfig.load()
        opts = _parse_json_obj(options_json, "options_json")
        dry_run = bool(opts.get("dry_run", False))
        run_dir = _new_run_dir(cfg, f"evolve_{action}")

        if action == "retro":
            mode = str(opts.get("mode", "session"))
            tx_raw = str(opts.get("transcript_path") or target_path or "").strip()
            tx_path = Path(tx_raw).expanduser().resolve() if tx_raw else None
            nudges = RetroEngine(cfg).run(mode=mode, transcript_path=tx_path, dry_run=dry_run)
            report_path = run_dir / "retro_nudges.json"
            write_json_utf8_lf(report_path, [n.model_dump(mode="json") for n in nudges])
            artifacts = [str(report_path), *(n.backlog_file for n in nudges if n.backlog_file)]
            return _compact_json(
                status="ok",
                summary_one_line=f"Retro ({mode}) generated {len(nudges)} nudge(s).",
                metrics={
                    "mode": mode,
                    "nudge_count": len(nudges),
                    "targets": [n.target for n in nudges],
                    "dry_run": dry_run,
                },
                artifact_paths=artifacts,
            )

        if action == "nudge":
            nudge_id = str(opts.get("nudge_id") or opts.get("id") or target_path or "").strip()
            if nudge_id:
                updated = RetroEngine(cfg).apply_nudge(nudge_id)
                report_path = run_dir / "nudge_apply.json"
                write_json_utf8_lf(report_path, {"applied_file": str(updated)})
                return _compact_json(
                    status="ok",
                    summary_one_line=f"Applied nudge {updated.name}.",
                    metrics={"applied_id": nudge_id, "file": str(updated)},
                    artifact_paths=[str(report_path), str(updated)],
                )
            backlog_dir = cfg.nudges_dir / "backlog"
            items = (
                [p.name for p in sorted(backlog_dir.glob("*.md"))]
                if backlog_dir.is_dir()
                else []
            )
            report_path = run_dir / "nudge_backlog.json"
            write_json_utf8_lf(report_path, {"backlog": items})
            return _compact_json(
                status="ok",
                summary_one_line=f"Found {len(items)} backlog nudge(s).",
                metrics={"backlog_count": len(items), "items": items[:20]},
                artifact_paths=[str(report_path)],
            )

        if action == "prune":
            days = int(opts.get("days", 30))
            apply_archive = bool(opts.get("apply_archive", False))
            rep_prune = audit_and_prune_skills(
                cfg,
                days=days,
                apply_archive=apply_archive,
                dry_run=dry_run,
            )
            report_path = run_dir / "prune_report.json"
            write_json_utf8_lf(report_path, rep_prune.model_dump(mode="json"))
            return _compact_json(
                status="ok",
                summary_one_line=(
                    f"Audited {rep_prune.total_skills} skill(s); "
                    f"flagged={rep_prune.flagged_count}."
                ),
                metrics={
                    "total_skills": rep_prune.total_skills,
                    "flagged_count": rep_prune.flagged_count,
                    "window_days": rep_prune.window_days,
                    "dry_run": dry_run,
                },
                artifact_paths=[str(report_path)],
            )

        if action == "lock":
            mgr = LockfileManager(cfg)
            if bool(opts.get("update", False)) and not dry_run:
                mgr.update()
            res = mgr.verify()
            report_path = run_dir / "lock_verify.json"
            write_json_utf8_lf(report_path, res.model_dump(mode="json"))
            return _compact_json(
                status="ok" if res.valid else "error",
                summary_one_line=(
                    f"Lockfile {'verified' if res.valid else 'failed'}: "
                    f"mismatches={len(res.mismatches)} missing={len(res.missing)} "
                    f"untracked={len(res.untracked)}."
                ),
                metrics={
                    "valid": res.valid,
                    "mismatches": res.mismatches,
                    "missing": res.missing,
                    "untracked": res.untracked,
                },
                artifact_paths=[str(report_path)],
            )

        raise ValueError(f"Unsupported evolve action: {action!r}")
    except Exception as exc:
        return _error_json(exc)


def main() -> None:
    """Run the Slim-MCP server over stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
