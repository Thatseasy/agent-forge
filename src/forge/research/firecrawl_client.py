"""Firecrawl REST wrapper supporting scrape, crawl, and map with clean Markdown output."""

from __future__ import annotations

import re

import httpx

from forge.research.models import ResearchResult, post_json

FIRECRAWL_BASE = "https://api.firecrawl.dev/v1"


def _strip_nav_boilerplate(markdown: str) -> str:
    lines = []
    for line in markdown.splitlines():
        if re.match(r"^\s*(skip to content|cookie|accept all|navigation|menu)\s*$", line, re.I):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def query_firecrawl(
    target_url: str,
    api_key: str,
    action: str = "scrape",
    dry_run: bool = False,
    client: httpx.Client | None = None,
) -> ResearchResult:
    """Run Firecrawl `scrape`, `crawl`, or `map` against a URL (or dry-run mock)."""
    op = action.lower() if action.lower() in {"scrape", "crawl", "map"} else "scrape"
    if dry_run:
        return ResearchResult(
            provider="firecrawl",
            query=target_url,
            mode=op,
            markdown=(
                f"## Firecrawl `{op}` Dry-Run\n\n"
                f"- Target: {target_url}\n"
                "- Extracted clean Markdown content without navigation chrome.\n"
            ),
            sources=[target_url] if target_url.startswith("http") else ["https://example.com"],
            dry_run=True,
        )

    if not api_key:
        raise ValueError("Missing FIRECRAWL_API_KEY. Set it in .env or pass --dry-run.")

    endpoint = f"{FIRECRAWL_BASE}/{op}"
    payload: dict[str, object] = {"url": target_url}
    if op == "scrape":
        payload["formats"] = ["markdown"]
        payload["onlyMainContent"] = True
    elif op == "crawl":
        payload["limit"] = 10
        payload["scrapeOptions"] = {"formats": ["markdown"], "onlyMainContent": True}

    data = post_json(
        endpoint,
        payload=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        client=client,
    )
    raw_inner = data.get("data")
    inner: dict[str, object] = raw_inner if isinstance(raw_inner, dict) else {}
    if op == "map":
        links = [str(u) for u in (data.get("links") or [])]
        md = "\n".join(f"- {u}" for u in links[:50]) or "_No links mapped._"
        return ResearchResult(
            provider="firecrawl",
            query=target_url,
            mode=op,
            markdown=md,
            sources=links,
        )

    raw_md = str(inner.get("markdown") or data.get("markdown") or "")
    cleaned = _strip_nav_boilerplate(raw_md)
    return ResearchResult(
        provider="firecrawl",
        query=target_url,
        mode=op,
        markdown=cleaned or "_No markdown extracted._",
        sources=[target_url],
    )
