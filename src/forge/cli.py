"""Central Typer CLI orchestration for agent-forge."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge.archivist import cmd_archive_audit
from forge.benchmark import AblationArena, CostOracle
from forge.config import ForgeConfig
from forge.discovery import DiscoveryCollector
from forge.doctor import run_doctor
from forge.gate import GatePipeline, GateReport
from forge.init_wizard import OPTIONAL_API_KEY_NAMES, run_init, summarize_init_json
from forge.paths import find_forge_checkout, resolve_target_workspace
from forge.plugin_pkg import build_plugin, validate_plugin
from forge.qa import QAExplorer, audit_qa_video
from forge.refine import RetroEngine, audit_and_prune_skills, propose_skill_pruning
from forge.research import ResearchRouter
from forge.sanitize import assert_corpus_sanitized, sanitize_path
from forge.setup import SetupOrchestrator
from forge.sourcer import cmd_source
from forge.sync import CloudExporter, LockfileManager, WorkspaceSyncer
from forge.units import UnitManager, tailor_unit_skill

app = typer.Typer(
    name="forge",
    help="agent-forge: Token-lean research, gating, benchmarking, QA, and skill sync.",
    no_args_is_help=True,
)
nudge_app = typer.Typer(help="Manage evolutionary nudge backlog items.")
unit_app = typer.Typer(help="Manage canonical domain specialist units (`library/units/`).")
plugin_app = typer.Typer(help="Validate and build distributable Antigravity plugin bundles.")
app.add_typer(nudge_app, name="nudge")
app.add_typer(unit_app, name="unit")
app.add_typer(plugin_app, name="plugin")
console = Console()


@app.command("research")
def cmd_research(
    query: Annotated[str, typer.Argument(help="Search query, URL (Firecrawl), or package name.")],
    provider: Annotated[
        str, typer.Option("--provider", help="sonar|firecrawl|context7|exa")
    ] = "sonar",
    mode: Annotated[
        str, typer.Option("--mode", help="Provider-specific mode (e.g. low|high|scrape|auto|deep).")
    ] = "",
    max_tokens: Annotated[
        int, typer.Option("--max-tokens", help="Approximate token budget.")
    ] = 1500,
    out: Annotated[
        Path | None, typer.Option("--out", help="Optional Markdown output path.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Return mock Markdown without live API calls.")
    ] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass 24h SQLite cache.")] = False,
) -> None:
    """Execute token-lean web/docs research with 24h SQLite response cache."""
    cfg = ForgeConfig.load()
    router = ResearchRouter(cfg)
    result = router.query(
        query=query,
        provider=provider,
        mode=mode,
        max_tokens=max_tokens,
        dry_run=dry_run,
        out_path=out,
        bypass_cache=no_cache,
    )
    rendered = result.render_markdown(max_tokens=max_tokens)
    console.print(rendered)
    console.print(
        f"[dim]provider={result.provider} cached={result.cached} dry_run={result.dry_run}[/dim]"
    )


@app.command("discover")
def cmd_discover(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Stage a deterministic sample candidate without network."),
    ] = False,
    sources: Annotated[
        Path | None, typer.Option("--sources", help="Override discovery_sources.yaml path.")
    ] = None,
) -> None:
    """Run zero-token discovery radar and write candidate bundles to staging/inbox/."""
    cfg = ForgeConfig.load()
    collector = DiscoveryCollector(cfg)
    candidates = collector.collect(dry_run=dry_run, sources_path=sources)

    table = Table(title="Stage 1A Discovery Radar")
    table.add_column("Slug")
    table.add_column("Source")
    table.add_column("Stars", justify="right")
    table.add_column("Tag")
    table.add_column("Staging Path")
    for c in candidates:
        table.add_row(c.slug, c.source_type, str(c.hype_stars), c.tag, c.staging_path)
    console.print(table)


@app.command("gate")
def cmd_gate(
    target: Annotated[
        Path | None, typer.Argument(help="Path to candidate skill directory.")
    ] = None,
    all_candidates: Annotated[
        bool, typer.Option("--all", help="Evaluate all folders under staging/inbox/.")
    ] = False,
    no_fix: Annotated[
        bool, typer.Option("--no-fix", help="Do not delete clutter or write ported patch.")
    ] = False,
    strict: Annotated[
        bool, typer.Option("--strict", help="Exit with code 1 if any skill fails gate.")
    ] = False,
) -> None:
    """Run Stage 2 deterministic 0-USD Security, Spec, Normalization, and Dedup checks."""
    cfg = ForgeConfig.load()
    pipeline = GatePipeline(cfg)

    reports: list[GateReport]
    if all_candidates or target is None:
        reports = pipeline.evaluate_all(apply_fixes=not no_fix)
    else:
        reports = [pipeline.evaluate_skill(target, apply_fixes=not no_fix)]

    table = Table(title="Stage 2 Gate Results")
    table.add_column("Skill")
    table.add_column("Status")
    table.add_column("Lines", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Security Findings", justify="right")
    table.add_column("Max Overlap", justify="right")
    for rep in reports:
        table.add_row(
            rep.skill_slug,
            rep.status,
            str(rep.spec.line_count),
            str(rep.spec.token_estimate),
            str(len(rep.security.findings)),
            f"{rep.dedup.max_similarity:.2f}",
        )
    console.print(table)

    if strict and any(r.status in {"failed_security", "failed_spec"} for r in reports):
        raise typer.Exit(code=1)


@app.command("bench")
def cmd_bench(
    suite: Annotated[str, typer.Option("--suite", help="Suite ID under evals/suites/.")],
    underdog: Annotated[Path, typer.Option("--underdog", help="Path to candidate underdog skill.")],
    hype: Annotated[
        Path | None, typer.Option("--hype", help="Optional competing hype skill directory.")
    ] = None,
    trials: Annotated[int, typer.Option("--trials", help="Trials per task per arm.")] = 3,
    isolation: Annotated[str, typer.Option("--isolation", help="worktree|docker")] = "worktree",
    runner: Annotated[
        str, typer.Option("--runner", help="auto|direct|agy|docker|dry-run")
    ] = "auto",
    compare_model: Annotated[
        list[str] | None,
        typer.Option("--compare-model", help="Optional extra model(s) for arbitrage matrix."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Simulate execution and Flash-Judge without API costs."),
    ] = False,
) -> None:
    """Run Stage 3 Trigger-Eval + 3-Arm Ablation Arena and write BENCHMARK.json."""
    cfg = ForgeConfig.load()
    arena = AblationArena(cfg, isolation=isolation, runner=runner)
    report = arena.run(
        suite_name=suite,
        underdog_dir=underdog.resolve(),
        hype_dir=hype.resolve() if hype else None,
        trials=trials,
        compare_models=compare_model or [],
        dry_run=dry_run,
    )

    table = Table(title=f"Stage 3 Benchmark Arena ({report.suite_id})")
    table.add_column("Arm")
    table.add_column("Skill")
    table.add_column("Passes", justify="right")
    table.add_column("pass^k", justify="right")
    table.add_column("Gain g", justify="right")
    table.add_column("Cache Hit", justify="right")
    table.add_column("Mean Cost $", justify="right")
    table.add_column("ValueScore", justify="right")
    for arm_key in ("vanilla", "hype", "underdog"):
        if arm_key not in report.arms:
            continue
        m = report.arms[arm_key]
        table.add_row(
            m.arm,
            m.skill_name,
            f"{m.passes}/{m.trials}",
            f"{m.pass_pow_k:.3f}",
            f"{m.normalized_gain:.3f}",
            f"{m.cache_hit_rate:.1%}",
            f"{m.mean_cost_usd:.5f}",
            f"{m.value_score:.3f}",
        )
    console.print(table)
    if report.judge:
        console.print(
            f"[dim]Flash-Judge ({report.judge.judge_model}): winner={report.judge.winner} "
            f"consistent_swap={report.judge.consistent_across_swap}[/dim]"
        )


@app.command("refine")
def cmd_refine(
    skill_dir: Annotated[Path, typer.Argument(help="Skill folder to prune.")],
    benchmark_json: Annotated[
        Path | None, typer.Option("--benchmark", help="Optional BENCHMARK.json path.")
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview pruning diff without writing SKILL.pruned.md."),
    ] = False,
) -> None:
    """Propose dead-section token pruning and script extraction for a skill."""
    proposal = propose_skill_pruning(
        skill_dir.resolve(),
        benchmark_json_path=benchmark_json.resolve() if benchmark_json else None,
        write_pruned=not dry_run,
    )
    console.print(
        f"Tokens: {proposal.tokens_before} -> {proposal.tokens_after} | "
        f"Dead sections: {proposal.dead_sections or ['none']} | "
        f"Script candidates: {proposal.script_extraction_candidates or ['none']}"
    )
    if proposal.diff_preview:
        console.print(proposal.diff_preview)


@app.command("retro")
def cmd_retro(
    mode: Annotated[str, typer.Option("--mode", help="session|outcome|audit")] = "session",
    transcript: Annotated[
        Path | None, typer.Option("--transcript", help="Optional transcript.jsonl path.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Use mock telemetry if no live transcript exists.")
    ] = False,
) -> None:
    """Extract up to 10 canonically routed incremental nudges into library/nudges/backlog/."""
    cfg = ForgeConfig.load()
    nudges = RetroEngine(cfg).run(
        mode=mode,
        transcript_path=transcript.resolve() if transcript else None,
        dry_run=dry_run,
    )
    table = Table(title=f"Stage 4 Retro ({mode})")
    table.add_column("ID")
    table.add_column("Target")
    table.add_column("Title")
    table.add_column("Backlog File")
    for n in nudges:
        table.add_row(n.id, n.target, n.title, n.backlog_file)
    console.print(table)


@nudge_app.command("apply")
def cmd_nudge_apply(
    nudge_id: Annotated[str, typer.Argument(help="Nudge ID or filename stem from backlog.")],
) -> None:
    """Mark a backlog nudge as applied."""
    cfg = ForgeConfig.load()
    updated = RetroEngine(cfg).apply_nudge(nudge_id)
    console.print(f"Applied nudge: {updated}")


@app.command("prune")
def cmd_prune(
    days: Annotated[int, typer.Option("--days", help="Inactivity lookback window in days.")] = 30,
    apply_archive: Annotated[
        bool,
        typer.Option(
            "--apply", help="Move confirmed unused library skills to archive/deprecated/."
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report only; never move directories.")
    ] = False,
) -> None:
    """Audit skill activations & bloat and optionally archive deprecated library skills."""
    cfg = ForgeConfig.load()
    report = audit_and_prune_skills(
        cfg,
        days=days,
        apply_archive=apply_archive,
        dry_run=dry_run,
    )
    table = Table(title=f"Stage 4 Usage & Token Audit ({days}d)")
    table.add_column("Skill")
    table.add_column("Loc")
    table.add_column("Activations", justify="right")
    table.add_column("Lines", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Flags")
    for rec in report.records:
        table.add_row(
            rec.skill_name,
            rec.location,
            str(rec.activations_in_window),
            str(rec.line_count),
            str(rec.token_estimate),
            ", ".join(rec.flagged_reasons) or "ok",
        )
    console.print(table)


@app.command("qa")
def cmd_qa(
    url: Annotated[str, typer.Option("--url", help="Target localhost/staging/file:// URL.")],
    charter: Annotated[
        str,
        typer.Option("--charter", help="all|impatient-user|adversarial-empty|mobile-layout"),
    ] = "all",
    out_dir: Annotated[
        Path | None, typer.Option("--out-dir", help="Artifact output directory.")
    ] = None,
    storage_state: Annotated[
        Path | None, typer.Option("--storage-state", help="Optional Playwright auth state JSON.")
    ] = None,
    allowed_domain: Annotated[
        list[str] | None, typer.Option("--allow-domain", help="Extra allowed hostnames.")
    ] = None,
    video_audit: Annotated[
        bool, typer.Option("--video-audit/--no-video-audit", help="Run 1-FPS video audit.")
    ] = True,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Simulate exploratory session & video audit.")
    ] = False,
) -> None:
    """Run Stage 6 exploratory Playwright QA with VP9 screencast and bug spec export."""
    cfg = ForgeConfig.load()
    domains = ["localhost", "127.0.0.1", *(allowed_domain or [])]
    explorer = QAExplorer(cfg)
    report = explorer.explore(
        url=url,
        charter=charter,
        out_dir=out_dir.resolve() if out_dir else None,
        storage_state=storage_state.resolve() if storage_state else None,
        allowed_domains=domains,
        dry_run=dry_run,
    )

    table = Table(title="Stage 6 Exploratory QA")
    table.add_column("Bug ID")
    table.add_column("Category")
    table.add_column("Class")
    table.add_column("Message")
    for f in report.findings:
        table.add_row(f.bug_id, f.category, f.fix_classification, f.message[:80])
    console.print(table)
    console.print(
        f"[dim]clip={report.video_clip_path or 'none'} "
        f"spec={report.reproduction_spec_path or 'none'}[/dim]"
    )

    if video_audit and report.video_clip_path:
        va = audit_qa_video(cfg, Path(report.video_clip_path), dry_run=dry_run)
        console.print(
            f"[dim]VideoAudit ({va.model_used}, {va.fps} FPS, "
            f"est=${va.estimated_cost_usd:.4f}): {len(va.visual_findings)} note(s)[/dim]"
        )


@app.command("lock")
def cmd_lock(
    update: Annotated[
        bool, typer.Option("--update", help="Recompute and save skills-lock.json.")
    ] = False,
    verify: Annotated[
        bool, typer.Option("--verify", help="Verify skills match skills-lock.json.")
    ] = False,
) -> None:
    """Manage or verify SHA-256 & SemVer integrity in skills-lock.json."""
    cfg = ForgeConfig.load()
    mgr = LockfileManager(cfg)
    if not update and not verify:
        verify = True
    if update:
        lf = mgr.update()
        console.print(f"Updated skills-lock.json ({len(lf.skills)} skill(s))")
    if verify:
        res = mgr.verify()
        if not res.valid:
            console.print(
                f"[red]Lockfile verification FAILED[/red]: mismatches={res.mismatches} "
                f"missing={res.missing} untracked={res.untracked}"
            )
            raise typer.Exit(code=1)
        console.print("[green]skills-lock.json verified OK[/green]")


@app.command("sync")
def cmd_sync(
    profile: Annotated[
        str, typer.Option("--profile", help="Profile name under library/profiles/.")
    ],
    target: Annotated[
        Path | None, typer.Option("--target", help="Target workspace directory.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview diffs & planned files without writing.")
    ] = False,
) -> None:
    """Selectively sync a curated profile (skills, hooks, skill-rules) into a workspace."""
    cfg = ForgeConfig.load()
    resolved_target = resolve_target_workspace(target)
    rep = WorkspaceSyncer(cfg).sync(profile, resolved_target, dry_run=dry_run)
    console.print(
        f"Sync profile={rep.profile} target={rep.target_dir} "
        f"skills={len(rep.synced_skills)} files={len(rep.planned_files)} "
        f"dry_run={rep.dry_run} hooks_schema_valid={rep.hooks_schema_valid}"
    )
    for d in rep.diffs:
        console.print(d)


@app.command("cloud-sync")
def cmd_cloud_sync(
    target: Annotated[str, typer.Option("--target", help="obsidian|notion|drive|all")] = "obsidian",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview export locally.")] = False,
) -> None:
    """Export leaderboards, nudges, and binary artifacts to Obsidian, Notion, or rclone Drive."""
    cfg = ForgeConfig.load()
    exporter = CloudExporter(cfg)
    targets = ["obsidian", "notion", "drive"] if target == "all" else [target.lower()]
    for t in targets:
        if t == "obsidian":
            res = exporter.export_obsidian(dry_run=dry_run)
        elif t == "notion":
            res = exporter.export_notion(dry_run=dry_run)
        elif t == "drive":
            res = exporter.export_drive(dry_run=dry_run)
        else:
            raise typer.BadParameter(f"Unsupported cloud-sync target: {t!r}")
        console.print(f"[{res.target}] {res.notes}")


@app.command("push")
def cmd_push(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Verify lockfile and git status only.")
    ] = True,
) -> None:
    """Verify skills-lock.json integrity before preparing/pushing Git changes."""
    cfg = ForgeConfig.load()
    res = CloudExporter(cfg).prepare_github_push(dry_run=dry_run)
    console.print(f"[{res.target}] {res.notes}")


app.command("archive-audit")(cmd_archive_audit)
app.command("source")(cmd_source)


@app.command("setup")
def cmd_setup(
    target: Annotated[
        Path | None, typer.Option("--target", help="Target workspace directory to assess.")
    ] = None,
    profile: Annotated[
        str, typer.Option("--profile", help="Override profile under library/profiles/.")
    ] = "",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview setup actions without writing files.")
    ] = False,
    live_source: Annotated[
        bool, typer.Option("--live-source", help="Run live network probes in sourcer.")
    ] = False,
) -> None:
    """Assess project maturity and idempotently setup docs, sources, rules, and hooks."""
    cfg = ForgeConfig.load()
    resolved_target = resolve_target_workspace(target)
    report = SetupOrchestrator(cfg).run(
        resolved_target,
        profile=profile,
        dry_run=dry_run,
        live_source=live_source,
    )
    table = Table(
        title=(f"Forge Setup: {report.assessment.project_name} ({report.assessment.maturity})")
    )
    table.add_column("Action")
    table.add_column("Path")
    table.add_column("Detail")
    for act in report.actions:
        table.add_row(act.action, act.path, act.detail)
    console.print(table)
    console.print(
        f"[dim]maturity={report.assessment.maturity} profile={report.profile} "
        f"dry_run={report.dry_run} created={len(report.created_files)} "
        f"skipped={len(report.skipped_files)} hooks_schema_valid={report.hooks_schema_valid}[/dim]"
    )


@app.command("sanitize")
def cmd_sanitize(
    input_path: Annotated[Path, typer.Option("--input", help="File or folder with raw corpus.")],
    out_path: Annotated[
        Path | None, typer.Option("--out", help="Destination path for sanitized corpus.")
    ] = None,
    names_file: Annotated[
        Path | None, typer.Option("--names-file", help="Optional newline-separated PII names list.")
    ] = None,
    allow_domain: Annotated[
        list[str] | None, typer.Option("--allow-domain", help="Domain(s) preserved unredacted.")
    ] = None,
    verify_only: Annotated[
        bool, typer.Option("--verify", help="Verify an already-sanitized corpus has zero PII.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview redaction counts without writing files.")
    ] = False,
) -> None:
    """Anonymize local customer/transcript PII & secrets before unit tailoring or cloud calls."""
    if verify_only:
        checked = assert_corpus_sanitized(input_path)
        console.print(f"[green]Sanitized corpus verified clean[/green] ({len(checked)} file(s))")
        return

    dest = out_path or (input_path.parent / f"{input_path.name}_sanitized")
    report = sanitize_path(
        input_path,
        dest,
        names_file=names_file,
        allowlist_domains=allow_domain,
        dry_run=dry_run,
    )
    table = Table(title="Local PII & Secret Sanitization")
    table.add_column("Category")
    table.add_column("Redacted Count", justify="right")
    for cat, cnt in sorted(report.counts_by_category.items()):
        table.add_row(cat, str(cnt))
    if not report.counts_by_category:
        table.add_row("none", "0")
    console.print(table)
    console.print(
        f"[dim]files={report.total_files} modified={report.modified_files} "
        f"out={dest} dry_run={report.dry_run}[/dim]"
    )


@unit_app.command("init")
def cmd_unit_init(
    domain: Annotated[
        str, typer.Argument(help="Lowercase unit domain slug, e.g. example-python-cli.")
    ],
    description: Annotated[str, typer.Option("--description", help="Unit description.")] = "",
    profile: Annotated[
        str, typer.Option("--profile", help="Base workspace profile.")
    ] = "superarchivist-boilerplate",
) -> None:
    """Initialize canonical `library/units/<domain>/` specialist scaffolding."""
    cfg = ForgeConfig.load()
    udir = UnitManager(cfg).init_unit(domain, description=description, profile=profile)
    console.print(f"[green]Initialized specialist unit[/green]: {udir}")


@unit_app.command("install")
def cmd_unit_install(
    source: Annotated[str, typer.Argument(help="Local unit/repo path or Git URL.")],
    unit: Annotated[str, typer.Option("--unit", help="Optional domain slug filter.")] = "",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate without copying into AGENT_FORGE_HOME.")
    ] = False,
) -> None:
    """Gate-verify, SHA-256 pin, and install specialist unit(s) into AGENT_FORGE_HOME."""
    cfg = ForgeConfig.load()
    rep = UnitManager(cfg).install_unit(source, unit=unit, dry_run=dry_run)
    for rec in rep.installed:
        console.print(
            f"[green]Installed unit[/green] {rec.domain} sha256={rec.sha256[:12]} "
            f"skills={rec.skills} dry_run={rep.dry_run}"
        )


@unit_app.command("list")
def cmd_unit_list() -> None:
    """List installed and bundled specialist domain units."""
    cfg = ForgeConfig.load()
    slugs = UnitManager(cfg).list_units()
    for slug in slugs:
        console.print(slug)


@unit_app.command("sync")
def cmd_unit_sync(
    domain: Annotated[str, typer.Argument(help="Unit slug under library/units/.")],
    target: Annotated[
        Path | None, typer.Option("--target", help="Target workspace directory.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview files without writing.")
    ] = False,
    no_profile: Annotated[
        bool, typer.Option("--no-profile", help="Skip base profile sync; deploy unit skills only.")
    ] = False,
) -> None:
    """Gate-verify a specialist unit and deploy it into a target repository."""
    cfg = ForgeConfig.load()
    resolved_target = resolve_target_workspace(target)
    rep = UnitManager(cfg).sync_unit(
        domain,
        resolved_target,
        include_profile=not no_profile,
        dry_run=dry_run,
    )
    console.print(
        f"Unit sync domain={rep.domain} skills={rep.unit_skills} "
        f"files={len(rep.planned_files)} dry_run={rep.dry_run} gate_passed={rep.gate_passed}"
    )


@app.command("tailor")
def cmd_tailor(
    unit: Annotated[str, typer.Option("--unit", help="Target specialist unit slug.")],
    base_skill: Annotated[
        str, typer.Option("--base-skill", help="Path or slug of external/local base skill.")
    ],
    corpus: Annotated[
        Path, typer.Option("--corpus", help="Sanitized corpus folder or file.")
    ],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview tailoring without writes.")
    ] = False,
) -> None:
    """Blend a base skill with a sanitized corpus into a Stage-2-gated unit skill & eval suite."""
    cfg = ForgeConfig.load()
    rep = tailor_unit_skill(
        cfg,
        domain=unit,
        base_skill=base_skill,
        corpus_path=corpus,
        dry_run=dry_run,
    )
    console.print(
        f"[green]Tailored[/green] {rep.derived_skill_slug} (unit={rep.domain}, "
        f"gate={rep.gate_status}, signals={len(rep.extracted_signals)})"
    )
    console.print(f"[dim]skill={rep.skill_path} eval={rep.eval_suite_path}[/dim]")


@app.command("cost-oracle")
def cmd_cost_oracle(
    tier: Annotated[str, typer.Option("--tier", help="all|fast|balanced|judge")] = "all",
    refresh_openrouter: Annotated[
        bool, typer.Option("--refresh-openrouter", help="Fetch live prices from OpenRouter API.")
    ] = False,
    limit: Annotated[int, typer.Option("--limit", help="Max models shown.")] = 8,
) -> None:
    """Inspect or refresh model pricing and Stage 3 arbitrage recommendations."""
    cfg = ForgeConfig.load()
    oracle = CostOracle(cfg)
    if refresh_openrouter:
        oracle.refresh_from_openrouter()

    tier_filter = None if tier == "all" else tier
    if tier_filter in {"fast", "balanced", "judge"}:
        rows = oracle.recommend_arbitrage_models(tier=tier_filter, limit=limit)  # type: ignore[arg-type]
    else:
        rows = oracle.all_models()[:limit]

    table = Table(title=f"Stage 3 Cost-Oracle ({tier})")
    table.add_column("Model", no_wrap=True)
    table.add_column("Tier")
    table.add_column("In $/1M", justify="right")
    table.add_column("Out $/1M", justify="right")
    table.add_column("Cache $/1M", justify="right")
    table.add_column("Source")
    for m in rows:
        table.add_row(
            m.model_id,
            m.tier,
            f"{m.input_per_m:.3f}",
            f"{m.output_per_m:.3f}",
            f"{m.cache_per_m:.3f}",
            m.source,
        )
    console.print(table)


@app.command("doctor")
def cmd_doctor(
    target: Annotated[
        Path | None, typer.Option("--target", help="Optional target workspace to inspect.")
    ] = None,
    hooks: Annotated[
        bool, typer.Option("--hooks", help="Validate global & opt-in hooks schema and parity.")
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit structured JSON report.")
    ] = False,
) -> None:
    """Diagnose tools, AGENT_FORGE_HOME, API keys, and Antigravity hook parity."""
    cfg = ForgeConfig.load()
    resolved_target = resolve_target_workspace(target) if target is not None else None
    report = run_doctor(cfg, target_dir=resolved_target, check_hooks=hooks)
    if json_output:
        typer.echo(json.dumps(report.model_dump(), indent=2))
    else:
        table = Table(title=f"Agent-Forge Doctor (status={report.status})")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Detail")
        table.add_row(
            "Environment",
            "ok",
            f"{report.os_name} {report.os_release} | Python {report.python_version}",
        )
        table.add_row(
            "AGENT_FORGE_HOME",
            "ok" if report.agent_forge_home_writable else "error",
            report.agent_forge_home,
        )
        for tool_name, present in report.tools.items():
            table.add_row(
                f"tool:{tool_name}",
                "ok" if present else "missing",
                report.tool_paths.get(tool_name) or "-",
            )
        if report.hooks_check is not None:
            table.add_row(
                "hooks",
                "ok" if report.hooks_check.valid else "error",
                (
                    f"schema={report.hooks_check.schema_valid} "
                    f"stdlib={report.hooks_check.stdlib_only_ok} "
                    f"parity={report.hooks_check.tool_parity_ok}"
                ),
            )
        console.print(table)
    if report.status == "error":
        raise typer.Exit(code=1)


@app.command("init")
def cmd_init(
    target: Annotated[
        Path | None, typer.Option("--target", help="Target workspace directory.")
    ] = None,
    non_interactive: Annotated[
        bool, typer.Option("--non-interactive", help="Initialize without prompting for keys.")
    ] = False,
    install_playwright: Annotated[
        bool,
        typer.Option("--install-playwright", help="Optionally install Playwright Chromium."),
    ] = False,
    with_setup: Annotated[
        bool,
        typer.Option("--with-setup", help="Also run `/forge-setup` scaffolding on --target."),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview initialization without writing files.")
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit structured JSON onboarding summary.")
    ] = False,
) -> None:
    """Zero-friction onboarding: initialize AGENT_FORGE_HOME, secrets.env (0600), and doctor."""
    cfg = ForgeConfig.load()
    resolved_target = resolve_target_workspace(target)
    secret_updates: dict[str, str] = {}

    if not non_interactive and not dry_run:
        pre_doc = run_doctor(cfg, target_dir=resolved_target, check_hooks=False)
        console.print(
            "[bold]Optional API keys[/bold] (leave empty to skip; 0-USD local features still work):"
        )
        for key_name in OPTIONAL_API_KEY_NAMES[:6]:
            if pre_doc.api_keys_configured.get(key_name, False):
                continue
            entered = typer.prompt(
                f"  {key_name} (optional)", default="", hide_input=True, show_default=False
            )
            if entered.strip():
                secret_updates[key_name] = entered.strip()

    report = run_init(
        cfg,
        target_dir=resolved_target,
        secret_updates=secret_updates or None,
        install_playwright=install_playwright,
        with_setup=with_setup,
        dry_run=dry_run,
    )

    if json_output:
        typer.echo(json.dumps(summarize_init_json(report), indent=2))
    else:
        table = Table(title=f"Agent-Forge Init ({report.status})")
        table.add_column("Capability")
        table.add_column("Tier")
        table.add_column("Mode")
        table.add_column("Requirement")
        for feat in report.features:
            table.add_row(feat.feature, feat.tier, feat.mode, feat.requirement)
        console.print(table)
        hooks_ok = bool(report.doctor.hooks_check and report.doctor.hooks_check.valid)
        console.print(
            f"[dim]AGENT_FORGE_HOME={report.home_dir} | secrets={report.secrets_env_path} | "
            f"seeded={len(report.seeded_files)} | hooks_valid={hooks_ok}[/dim]"
        )
        for cmd in report.next_commands:
            console.print(f"  {cmd}")

    if report.status == "error":
        raise typer.Exit(code=1)


@plugin_app.command("validate")
def cmd_plugin_validate(
    root: Annotated[
        Path | None, typer.Option("--root", help="Plugin root directory (default: checkout CWD).")
    ] = None,
    strict: Annotated[
        bool, typer.Option("--strict", help="Exit non-zero on any plugin validation error.")
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit structured JSON validation report.")
    ] = False,
) -> None:
    """Validate plugin manifest, UTF-8/LF hygiene, token budgets, and global hook parity."""
    plugin_root = root.resolve() if root is not None else (find_forge_checkout() or Path.cwd())
    rep = validate_plugin(plugin_root)
    if json_output:
        typer.echo(json.dumps(rep.model_dump(), indent=2))
    else:
        console.print(
            f"Plugin validate name={rep.name} version={rep.version} valid={rep.valid} "
            f"commands={rep.command_count} skills={rep.skill_count} agents={rep.agent_count} "
            f"static_tokens={rep.static_context_tokens} mcp_tokens={rep.mcp_schema_tokens} "
            f"hooks_valid={rep.hooks_valid}"
        )
        for err in rep.errors:
            console.print(f"[red]ERROR:[/red] {err}")
    if (strict or not json_output) and not rep.valid:
        raise typer.Exit(code=1)


@plugin_app.command("build")
def cmd_plugin_build(
    root: Annotated[
        Path | None, typer.Option("--root", help="Plugin root directory (default: checkout CWD).")
    ] = None,
    out_dir: Annotated[
        Path | None, typer.Option("--out-dir", help="Destination directory for wheel/sdist.")
    ] = None,
) -> None:
    """Validate plugin invariants and build distributable `.whl` / `.tar.gz` artifacts."""
    cfg = ForgeConfig.load()
    plugin_root = root.resolve() if root is not None else (find_forge_checkout() or Path.cwd())
    rep = build_plugin(plugin_root, out_dir=out_dir, config=cfg)
    console.print(
        f"[green]Built plugin[/green] wheel={rep.wheel_path} "
        f"bundled_assets={rep.wheel_bundled_assets_count}"
    )


if __name__ == "__main__":
    app()
