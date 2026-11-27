"""Tests for Ticket 4 Intelligence-Sourcer: stack detection, probing, scoring, and artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from typer.testing import CliRunner

from forge.config import ForgeConfig
from forge.sourcer import (
    IntelligenceSourcer,
    SourceCatalog,
    SourceScorer,
    StackDetector,
    cmd_source,
    run_sourcing,
    sourcer_app,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _make_fullstack_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "vibe-saas-demo",
                "dependencies": {
                    "next": "^15.0.0",
                    "react": "^19.0.0",
                    "@supabase/supabase-js": "^2.45.0",
                    "@notionhq/client": "^2.2.0",
                    "ai": "^4.0.0",
                },
                "devDependencies": {
                    "@playwright/test": "^1.50.0",
                    "typescript": "^5.6.0",
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "tsconfig.json").write_text("{}", encoding="utf-8")
    (root / "next.config.ts").write_text("export default {};\n", encoding="utf-8")
    (root / "playwright.config.ts").write_text("export default {};\n", encoding="utf-8")
    (root / "vercel.json").write_text("{}", encoding="utf-8")
    (root / "supabase").mkdir()
    (root / ".env.example").write_text(
        "NEXT_PUBLIC_SUPABASE_URL=\nSUPABASE_ANON_KEY=\nNOTION_API_KEY=\nVERCEL_TOKEN=\n",
        encoding="utf-8",
    )
    # Must NEVER be read or leaked into env_hints/artifacts
    (root / ".env").write_text("SECRET_TOKEN=super-secret-value-123\n", encoding="utf-8")
    return root


def _make_python_ai_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "eval-runner"\ndependencies = ["pydantic>=2.9", "litellm>=1.5"]\n'
        '[dependency-groups]\ndev = ["pytest>=8.0", "playwright>=1.50"]\n'
        "[tool.uv]\nmanaged = true\n",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "Dockerfile").write_text("FROM python:3.14-slim\n", encoding="utf-8")
    (root / "evals").mkdir()
    (root / ".env.example").write_text("OPENROUTER_API_KEY=\nFORGE_JUDGE_MODEL=gpt-5.6-luna\n")
    return root


def test_stack_detector_fullstack_and_secret_isolation(tmp_path: Path) -> None:
    proj = _make_fullstack_project(tmp_path / "saas")
    res = StackDetector().detect(proj)

    assert res.project_name == "vibe-saas-demo"
    for expected in (
        "nextjs-react",
        "supabase",
        "playwright-qa",
        "notion",
        "vercel",
        "typescript-node",
    ):
        assert expected in res.domains

    assert "NEXT_PUBLIC_SUPABASE_URL" in res.env_hints
    assert "SECRET_TOKEN" not in res.env_hints
    assert all("super-secret-value-123" not in hint for hint in res.env_hints)
    assert "supabase/" in res.detected_files
    assert "next.config.ts" in res.detected_files


def test_stack_detector_python_uv_and_missing_dir(tmp_path: Path) -> None:
    py_proj = _make_python_ai_project(tmp_path / "py_ai")
    detector = StackDetector()
    res = detector.detect(py_proj)

    assert res.project_name == "eval-runner"
    assert "python-uv" in res.domains
    assert "ai-evals" in res.domains
    assert "playwright-qa" in res.domains
    assert "docker-infra" in res.domains

    empty_res = detector.detect(tmp_path / "does_not_exist")
    assert empty_res.domains == []
    assert empty_res.project_name == "does_not_exist"


def test_catalog_loads_yaml_and_mcp_templates_dry_run_probes() -> None:
    cfg = ForgeConfig.load(REPO_ROOT)
    catalog = SourceCatalog(cfg)
    candidates = catalog.load_candidates()
    by_id = {c.id: c for c in candidates}

    assert "chrome-devtools-slim" in by_id
    assert by_id["chrome-devtools-slim"].mcp_server is not None
    assert "--slim" in by_id["chrome-devtools-slim"].mcp_server["chrome-devtools"]["args"]
    assert "openrouter-models-feed" in by_id
    assert "artificial-analysis-mcp" in by_id

    probes = catalog.probe_candidates(candidates, dry_run=True)
    assert all(p.dry_run and p.reachable for p in probes.values())
    assert probes["openrouter-models-feed"].metadata["free_models_count"] > 0
    assert probes["context7-nextjs"].metadata["resolved_id"] == "/next"


def test_catalog_live_probes_with_mock_transport(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "context7.com/api/v1/search" in url:
            if "query=next" in url:
                return httpx.Response(200, json={"results": [{"id": "vercel/next.js"}]})
            return httpx.Response(404, json={"error": "not found"})
        if "nextjs.org/docs/llms.txt" in url:
            return httpx.Response(
                200,
                text="# Next.js Docs\n- App Router\n- Server Actions\n",
                headers={"content-type": "text/plain"},
            )
        if "supabase.com/llms.txt" in url:
            return httpx.Response(503, text="unavailable")
        if "openrouter.ai/api/v1/models" in url:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "google/gemini-2.5-flash:free",
                            "pricing": {"prompt": "0", "completion": "0"},
                        },
                        {
                            "id": "openai/gpt-5.6-luna",
                            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                        },
                    ]
                },
            )
        if "mcp.artificialanalysis.ai/mcp" in url:
            return httpx.Response(401, json={"error": "oauth required"})
        return httpx.Response(404, text="missing")

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    cfg = ForgeConfig.load(REPO_ROOT)
    catalog = SourceCatalog(cfg, http_client=client)
    by_id = {c.id: c for c in catalog.load_candidates()}

    c7_ok = catalog.probe_candidate(by_id["context7-nextjs"], dry_run=False, client=client)
    assert c7_ok.reachable
    assert c7_ok.metadata["resolved_id"] == "vercel/next.js"

    llms_ok = catalog.probe_candidate(by_id["nextjs-llms-txt"], dry_run=False, client=client)
    assert llms_ok.reachable
    assert llms_ok.metadata["line_count"] >= 3

    llms_fail = catalog.probe_candidate(by_id["supabase-llms-txt"], dry_run=False, client=client)
    assert not llms_fail.reachable
    assert llms_fail.status_code == 503

    or_res = catalog.probe_candidate(by_id["openrouter-models-feed"], dry_run=False, client=client)
    assert or_res.reachable
    assert or_res.metadata["model_count"] == 2
    assert or_res.metadata["free_models_count"] == 1
    assert or_res.metadata["sample_free_models"] == ["google/gemini-2.5-flash:free"]

    aa_res = catalog.probe_candidate(by_id["artificial-analysis-mcp"], dry_run=False, client=client)
    assert aa_res.reachable
    assert aa_res.metadata["auth_required"] is True


def test_scorer_selects_top_3_to_5_diverse_and_penalizes_unreachable() -> None:
    cfg = ForgeConfig.load(REPO_ROOT)
    catalog = SourceCatalog(cfg)
    scorer = SourceScorer()
    detection = StackDetector().detect(REPO_ROOT)

    candidates = catalog.candidates_for_domains(["nextjs-react", "supabase", "playwright-qa"])
    dry_probes = catalog.probe_candidates(candidates, dry_run=True)
    scored = scorer.score_all(candidates, detection, probes=dry_probes)
    top = scorer.select_top_sources(scored, detection, min_sources=3, max_sources=5)

    assert 3 <= len(top) <= 5
    covered_domains = {dom for item in top for dom in item.candidate.domains}
    assert "nextjs-react" in covered_domains or "python-uv" in covered_domains

    # Verify unreachable live HTTP probe lowers total_score relative to reachable probe
    by_id = {c.id: c for c in candidates}
    nxt = by_id["nextjs-llms-txt"]
    ok_score = scorer.score_candidate(nxt, detection, dry_probes["nextjs-llms-txt"])
    bad_probe = dry_probes["nextjs-llms-txt"].model_copy(
        update={"reachable": False, "dry_run": False, "details": "timeout"}
    )
    bad_score = scorer.score_candidate(nxt, detection, bad_probe)
    assert bad_score.total_score < ok_score.total_score


def test_run_sourcing_emits_lean_utf8_lf_artifacts(tmp_path: Path) -> None:
    proj = _make_fullstack_project(tmp_path / "project")
    out_dir = tmp_path / "out"
    cfg = ForgeConfig.load(REPO_ROOT)

    report = run_sourcing(
        proj,
        config=cfg,
        out_dir=out_dir,
        max_sources=5,
        dry_run=True,
        write_artifacts=True,
    )

    assert 3 <= len(report.top_sources) <= 5
    mcp_file = out_dir / "mcp_config.recommended.json"
    md_file = out_dir / "SOURCES_RECOMMENDATION.md"
    json_file = out_dir / "SOURCES_REPORT.json"

    for artifact in (mcp_file, md_file, json_file):
        assert artifact.is_file()
        raw = artifact.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in raw

    mcp_data = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert mcp_data["token_budget_policy"]["prefer_mcp2cli_toon"] is True
    assert mcp_data["token_budget_policy"]["prefer_slim_mcp"] is True
    for wrapper in mcp_data["mcp2cliWrappers"].values():
        assert "--toon" in wrapper["args"]

    md_text = md_file.read_text(encoding="utf-8")
    assert "Primary Sources Recommendation" in md_text
    assert "vibe-saas-demo" in md_text

    rep_data = json.loads(json_file.read_text(encoding="utf-8"))
    assert len(rep_data["top_sources"]) == len(report.top_sources)


def test_no_write_and_typer_cli_integration(tmp_path: Path) -> None:
    proj = _make_python_ai_project(tmp_path / "py_proj")
    cfg = ForgeConfig.load(REPO_ROOT)
    sourcer = IntelligenceSourcer(cfg)
    preview = sourcer.source_project(proj, dry_run=True, write_artifacts=False)
    assert 3 <= len(preview.top_sources) <= 5
    assert not (proj / "mcp_config.recommended.json").exists()

    out_dir = tmp_path / "cli_out"
    runner = CliRunner()
    result = runner.invoke(
        sourcer_app,
        [str(proj), "--out-dir", str(out_dir), "--max-sources", "4", "--dry-run"],
    )
    assert result.exit_code == 0, result.stdout
    assert (out_dir / "mcp_config.recommended.json").is_file()
    assert (out_dir / "SOURCES_RECOMMENDATION.md").is_file()
    assert (out_dir / "SOURCES_REPORT.json").is_file()

    # Verify direct command function signature works for cli.py wiring
    direct = cmd_source(target=REPO_ROOT, out_dir=None, max_sources=5, dry_run=True, no_write=True)
    assert "python-uv" in direct.detection.domains
    assert 3 <= len(direct.top_sources) <= 5
