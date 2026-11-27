"""Exa neural search wrapper supporting `auto` and `deep` modes."""

from __future__ import annotations

import httpx

from forge.research.models import ResearchResult, post_json

EXA_SEARCH_URL = "https://api.exa.ai/search"


def query_exa(
    query: str,
    api_key: str,
    mode: str = "auto",
    max_tokens: int = 1500,
    dry_run: bool = False,
    client: httpx.Client | None = None,
) -> ResearchResult:
    """Run an Exa technical search (`auto` or `deep`) or return dry-run synthesis."""
    normalized_mode = "deep" if mode.lower() == "deep" else "auto"
    if dry_run:
        return ResearchResult(
            provider="exa",
            query=query,
            mode=normalized_mode,
            markdown=(
                f"## Exa ({normalized_mode}) Dry-Run Results\n\n"
                f"- Query: `{query}`\n"
                "- Ranked technical references ready for synthesis.\n"
            ),
            sources=["https://exa.ai/"],
            dry_run=True,
        )

    if not api_key:
        raise ValueError("Missing EXA_API_KEY. Set it in .env or pass --dry-run.")

    payload = {
        "query": query,
        "type": "deep" if normalized_mode == "deep" else "auto",
        "numResults": 5,
        "contents": {"text": {"maxCharacters": max_tokens * 2}},
    }
    data = post_json(
        EXA_SEARCH_URL,
        payload=payload,
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        client=client,
    )
    items = data.get("results") or []
    lines: list[str] = []
    sources: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "Untitled")
        url = str(item.get("url") or "")
        snippet = str(item.get("text") or "").strip()[:400]
        if url:
            sources.append(url)
        lines.append(f"### {title}\n{url}\n\n{snippet}\n")

    return ResearchResult(
        provider="exa",
        query=query,
        mode=normalized_mode,
        markdown="\n".join(lines) or "_No Exa results found._",
        sources=sources,
    )
