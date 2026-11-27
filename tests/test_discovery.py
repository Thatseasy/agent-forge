"""Tests for Stage 1A Zero-Token Discovery Radar."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx

from forge.config import ForgeConfig
from forge.discovery import DiscoveryCollector


def test_discovery_collect_stages_github_and_rss_candidates(tmp_path: Path) -> None:
    sources_yaml = tmp_path / "sources.yaml"
    sources_yaml.write_text(
        (
            "hype_star_threshold: 100\n"
            "github_queries:\n"
            "  - query: 'filename:SKILL.md'\n"
            "rss_feeds:\n"
            "  - name: 'releases'\n"
            "    url: 'https://feeds.example.test/releases.atom'\n"
        ),
        encoding="utf-8",
    )

    def _gh_mock(args: list[str]) -> str:
        return json.dumps(
            {
                "items": [
                    {
                        "full_name": "indie/tiny-skill",
                        "html_url": "https://github.com/indie/tiny-skill",
                        "stargazers_count": 12,
                    },
                    {
                        "full_name": "mega/hype-skill",
                        "html_url": "https://github.com/mega/hype-skill",
                        "stargazers_count": 1200,
                    },
                ]
            }
        )

    atom_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry><title>New Spec Release</title>"
        '<link href="https://example.test/release/1"/></entry>'
        "</feed>"
    )
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text=atom_xml))
    )
    cfg = ForgeConfig(root=tmp_path)
    collector = DiscoveryCollector(cfg, gh_runner=_gh_mock, http_client=http_client)
    candidates = collector.collect(sources_path=sources_yaml)

    assert len(candidates) == 3
    tags = {c.slug: c.tag for c in candidates}
    assert tags["indie-tiny-skill"] == "underdog"
    assert tags["mega-hype-skill"] == "hype"
    assert (tmp_path / "staging" / "inbox" / "indie-tiny-skill" / "SKILL.md").exists()
    meta = json.loads(
        (tmp_path / "staging" / "inbox" / "indie-tiny-skill" / "DISCOVERY_META.json").read_text(
            "utf-8"
        )
    )
    assert meta["hype_stars"] == 12


def test_cli_discover_dry_run_creates_staging_candidate() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(repo_root / "forge.py"), "discover", "--dry-run"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "radar-sample-skill" in proc.stdout
    candidate_dir = repo_root / "staging" / "inbox" / "radar-sample-skill"
    assert (candidate_dir / "SKILL.md").exists()
    assert (candidate_dir / "DISCOVERY_META.json").exists()
