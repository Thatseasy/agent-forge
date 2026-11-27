"""Offline-safe unit & CLI tests for Cost-Oracle and Artificial Analysis ingestion."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx

from forge.benchmark import CostOracle, estimate_cost_usd
from forge.config import ForgeConfig

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_cost_oracle_snapshot_lookup_and_scoring_integration() -> None:
    cfg = ForgeConfig(root=REPO_ROOT)
    oracle = CostOracle(cfg)

    nano = oracle.lookup("openai/gpt-5.4-nano")
    luna = oracle.lookup("gpt-5.6-luna")
    assert nano.input_per_m < luna.input_per_m

    cost_nano = estimate_cost_usd(100_000, 10_000, 50_000, model="openai/gpt-5.4-nano")
    cost_luna = estimate_cost_usd(100_000, 10_000, 50_000, model="gpt-5.6-luna")
    assert 0.0 < cost_nano < cost_luna

    fast_recs = oracle.recommend_arbitrage_models(tier="fast", limit=2)
    assert len(fast_recs) == 2
    assert all(m.tier == "fast" for m in fast_recs)


def test_cost_oracle_ingests_artificial_analysis_and_mock_openrouter(tmp_path: Path) -> None:
    (tmp_path / "library" / "clis").mkdir(parents=True)
    snap = REPO_ROOT / "library" / "clis" / "cost_oracle_snapshot.json"
    (tmp_path / "library" / "clis" / "cost_oracle_snapshot.json").write_text(
        snap.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )

    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "openai/gpt-5.4-mini",
                        "context_length": 400000,
                        "pricing": {"prompt": "0.0000003", "completion": "0.0000012"},
                    }
                ]
            },
        )
    )
    cfg = ForgeConfig(root=tmp_path)
    oracle = CostOracle(cfg, http_client=httpx.Client(transport=transport))
    updated = oracle.refresh_from_openrouter()
    assert len(updated) == 1
    assert oracle.lookup("gpt-5.4-mini").source == "openrouter-live"

    aa_count = oracle.ingest_artificial_analysis_models(
        {
            "models": [
                {
                    "slug": "gemini-3.8-flash",
                    "model_creator": "google",
                    "pricing": {"price_1m_input_tokens": 0.25, "price_1m_output_tokens": 1.8},
                    "evaluations": {"artificial_analysis_intelligence_index": 79.4},
                    "median_output_tokens_per_second": 210.0,
                }
            ]
        }
    )
    assert aa_count == 1
    gem = oracle.lookup("gemini-3.8-flash")
    assert gem.source == "artificial-analysis"
    assert gem.intelligence_index == 79.4

    # Live cache file must exist cleanly as JSON without BOM
    cache_file = tmp_path / "staging" / "cache" / "cost_oracle_live.json"
    assert cache_file.is_file()
    raw = cache_file.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert json.loads(raw.decode("utf-8"))["source"] == "live-cache"


def test_cli_cost_oracle_runs_offline() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "forge.py"), "cost-oracle", "--tier", "fast"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "Stage 3 Cost-Oracle" in proc.stdout
    assert "gemini-3.5-flash-lite" in proc.stdout
