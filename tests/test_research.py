"""Tests for Stage 1B research wrappers, SQLite cache, and `--dry-run` CLI."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

from forge.config import ForgeConfig
from forge.research import ResearchCache, ResearchRouter


def test_research_router_caches_and_expires(tmp_path: Path) -> None:
    cfg = ForgeConfig(root=tmp_path)
    cache = ResearchCache(tmp_path / "cache.db", ttl_seconds=60)
    calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "output_text": "Concise Sonar answer.",
                "search_results": [{"url": "https://example.org/docs"}],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    cfg = cfg.model_copy(update={"perplexity_api_key": "test-key"})
    router = ResearchRouter(cfg, cache=cache, client=client)

    first = router.query("antigravity hooks", provider="sonar", max_tokens=500)
    assert not first.cached
    assert calls == 1
    assert "Concise Sonar answer." in first.markdown

    second = router.query("antigravity hooks", provider="sonar", max_tokens=500)
    assert second.cached
    assert calls == 1

    # Expired entry triggers fresh query
    assert cache.get("sonar", "antigravity hooks", "low", 500, now_epoch=time.time() + 120) is None


def test_research_providers_with_mock_transport(tmp_path: Path) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "firecrawl.dev" in url:
            return httpx.Response(
                200, json={"data": {"markdown": "Skip to content\n# Docs Title\nBody"}}
            )
        if "context7.com/api/v1/search" in url:
            return httpx.Response(200, json={"results": [{"id": "pydantic/pydantic"}]})
        if "context7.com/api/v1/" in url:
            return httpx.Response(200, json={"text": "BaseModel v2 reference."})
        if "exa.ai/search" in url:
            return httpx.Response(
                200,
                json={
                    "results": [{"title": "Exa Hit", "url": "https://exa.ai/x", "text": "Details"}]
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(_handler))
    cfg = ForgeConfig(
        root=tmp_path,
        firecrawl_api_key="fc-key",
        exa_api_key="exa-key",
    )
    router = ResearchRouter(cfg, client=client)

    fc = router.query("https://docs.example.com", provider="firecrawl")
    assert "# Docs Title" in fc.markdown
    assert "Skip to content" not in fc.markdown

    c7 = router.query("pydantic BaseModel", provider="context7")
    assert "BaseModel v2 reference." in c7.markdown

    exa = router.query("agent skill benchmarks", provider="exa", mode="deep")
    assert "Exa Hit" in exa.markdown


def test_cli_research_dry_run_writes_out_and_cache(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out_file = tmp_path / "out.md"
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "forge.py"),
            "research",
            "test query",
            "--dry-run",
            "--out",
            str(out_file),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "Sonar (low) Dry-Run Synthesis" in proc.stdout
    raw = out_file.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    assert (repo_root / ".agents" / "state" / "research_cache.db").exists()
    assert json.loads(json.dumps({"ok": True}))["ok"]
