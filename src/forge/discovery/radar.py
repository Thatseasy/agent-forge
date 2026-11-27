"""Stage 1A Zero-Token Discovery Radar for new skill candidates."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import defusedxml.ElementTree as ET
import httpx
import yaml
from pydantic import BaseModel, Field

from forge.config import ForgeConfig, write_json_utf8_lf, write_text_utf8_lf
from forge.research.firecrawl_client import query_firecrawl


class DiscoveredCandidate(BaseModel):
    """Metadata for a discovered skill candidate staged in staging/inbox/."""

    slug: str
    name: str
    source_url: str
    source_type: str
    discovered_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    hype_stars: int = 0
    tag: str = "underdog"
    staging_path: str = ""


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return slug[:48] or "candidate"


def _default_gh_runner(args: list[str]) -> str:
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"gh exited with {proc.returncode}")
    return proc.stdout


class DiscoveryCollector:
    """Zero-LLM-token collector writing candidate bundles into `staging/inbox/`."""

    def __init__(
        self,
        config: ForgeConfig,
        gh_runner: Callable[[list[str]], str] | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.gh_runner = gh_runner or _default_gh_runner
        self.http_client = http_client

    def load_sources(self, sources_path: Path | None = None) -> dict[str, Any]:
        path = (
            sources_path
            or self.config.resolve_nudge_file("discovery_sources.yaml")
            or (self.config.nudges_dir / "discovery_sources.yaml")
        )
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def collect(
        self,
        dry_run: bool = False,
        sources_path: Path | None = None,
    ) -> list[DiscoveredCandidate]:
        """Collect candidates from GitHub, RSS, X URLs, and registries (0 LLM tokens)."""
        sources = self.load_sources(sources_path)
        threshold = int(sources.get("hype_star_threshold", 500))

        if dry_run:
            return [self._stage_dry_run_candidate()]

        candidates: list[DiscoveredCandidate] = []
        candidates.extend(self._collect_github(sources.get("github_queries") or [], threshold))
        candidates.extend(self._collect_rss(sources.get("rss_feeds") or []))
        candidates.extend(self._collect_x_urls(sources.get("x_urls") or []))
        candidates.extend(self._collect_registries(sources.get("registries") or []))
        return [self._stage_candidate(c) for c in candidates]

    def _stage_dry_run_candidate(self) -> DiscoveredCandidate:
        candidate = DiscoveredCandidate(
            slug="radar-sample-skill",
            name="radar-sample-skill",
            source_url="https://github.com/example/radar-sample-skill",
            source_type="github-dry-run",
            hype_stars=42,
            tag="underdog",
        )
        return self._stage_candidate(
            candidate,
            skill_body=(
                "---\n"
                "name: radar-sample-skill\n"
                "description: Validate CSV schemas quickly with deterministic checks. "
                "Use when auditing CSV imports. Do not use for binary Parquet files.\n"
                "---\n\n"
                "# Radar Sample Skill\n\n"
                "Run `python scripts/check_csv.py --help` before parsing tabular files.\n"
            ),
        )

    def _stage_candidate(
        self,
        candidate: DiscoveredCandidate,
        skill_body: str | None = None,
    ) -> DiscoveredCandidate:
        target_dir = self.config.staging_inbox_dir / candidate.slug
        target_dir.mkdir(parents=True, exist_ok=True)
        resolved = candidate.model_copy(update={"staging_path": str(target_dir)})

        body = skill_body or (
            "---\n"
            f"name: {candidate.slug}\n"
            f"description: Candidate skill imported from {candidate.source_url}. "
            "Use when evaluating discovered workflow automation. "
            "Do not use directly without Stage 2 gate.\n"
            "---\n\n"
            f"# {candidate.name}\n\n"
            f"- Source: {candidate.source_url}\n"
            f"- Tag: {candidate.tag} ({candidate.hype_stars} stars)\n"
        )
        write_text_utf8_lf(target_dir / "SKILL.md", body)
        write_json_utf8_lf(target_dir / "DISCOVERY_META.json", resolved.model_dump())
        return resolved

    def _collect_github(
        self, queries: list[dict[str, Any]], threshold: int
    ) -> list[DiscoveredCandidate]:
        found: list[DiscoveredCandidate] = []
        for entry in queries:
            q = str(entry.get("query") or "").strip()
            if not q:
                continue
            per_page = int(entry.get("per_page", 10))
            raw = self.gh_runner(
                [
                    "api",
                    "--method",
                    "GET",
                    "search/repositories",
                    "-f",
                    f"q={q}",
                    "-f",
                    f"per_page={per_page}",
                ]
            )
            data = json.loads(raw)
            for item in data.get("items") or []:
                stars = int(item.get("stargazers_count") or 0)
                full_name = str(item.get("full_name") or item.get("name") or "github-skill")
                found.append(
                    DiscoveredCandidate(
                        slug=_slugify(full_name.replace("/", "-")),
                        name=full_name,
                        source_url=str(item.get("html_url") or ""),
                        source_type="github",
                        hype_stars=stars,
                        tag="hype" if stars >= threshold else "underdog",
                    )
                )
        return found

    def _collect_rss(self, feeds: list[dict[str, Any]]) -> list[DiscoveredCandidate]:
        found: list[DiscoveredCandidate] = []
        for feed in feeds:
            url = str(feed.get("url") or "").strip()
            if not url:
                continue
            xml_text = self._fetch_text(url)
            if not xml_text:
                continue
            root = ET.fromstring(xml_text)
            for elem in root.findall(".//{http://www.w3.org/2005/Atom}entry")[:5]:
                title = elem.findtext("{http://www.w3.org/2005/Atom}title") or "rss-skill"
                link_el = elem.find("{http://www.w3.org/2005/Atom}link")
                href = link_el.attrib.get("href", url) if link_el is not None else url
                found.append(
                    DiscoveredCandidate(
                        slug=_slugify(title),
                        name=title,
                        source_url=href,
                        source_type="rss",
                        tag="underdog",
                    )
                )
        return found

    def _collect_x_urls(self, urls: list[str]) -> list[DiscoveredCandidate]:
        found: list[DiscoveredCandidate] = []
        for url in urls:
            if not isinstance(url, str) or not url.strip():
                continue
            if self.config.firecrawl_api_key:
                query_firecrawl(
                    url,
                    api_key=self.config.firecrawl_api_key,
                    action="scrape",
                    client=self.http_client,
                )
            slug = _slugify(url.split("/")[-1] or "x-bookmark")
            found.append(
                DiscoveredCandidate(
                    slug=f"x-{slug}",
                    name=f"X Bookmark {slug}",
                    source_url=url,
                    source_type="x_url",
                    tag="underdog",
                )
            )
        return found

    def _collect_registries(self, registries: list[dict[str, Any]]) -> list[DiscoveredCandidate]:
        found: list[DiscoveredCandidate] = []
        for reg in registries:
            url = str(reg.get("url") or "").strip()
            name = str(reg.get("name") or "registry")
            if not url:
                continue
            text = self._fetch_text(url)
            if not text:
                continue
            found.append(
                DiscoveredCandidate(
                    slug=_slugify(f"{name}-index"),
                    name=f"{name} Snapshot",
                    source_url=url,
                    source_type="registry",
                    tag="underdog",
                )
            )
        return found

    def _fetch_text(self, url: str) -> str:
        try:
            if self.http_client is not None:
                resp = self.http_client.get(url, timeout=15.0)
                resp.raise_for_status()
                return resp.text
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.text
        except (httpx.HTTPError, ET.ParseError):
            return ""
