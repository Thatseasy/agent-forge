"""Context7 client for version-accurate library documentation lookup without web search."""

from __future__ import annotations

import httpx

from forge.research.models import ResearchResult, get_json

CONTEXT7_SEARCH_URL = "https://context7.com/api/v1/search"
CONTEXT7_DOCS_URL = "https://context7.com/api/v1/{lib_id}"


def query_context7(
    query: str,
    api_key: str = "",
    max_tokens: int = 1500,
    dry_run: bool = False,
    client: httpx.Client | None = None,
) -> ResearchResult:
    """Resolve package docs via Context7 API (or deterministic dry-run)."""
    if dry_run:
        return ResearchResult(
            provider="context7",
            query=query,
            mode="docs",
            markdown=(
                f"## Context7 Dry-Run Docs: `{query}`\n\n"
                "- Resolved library documentation snippet without generic web search.\n"
                "- Token-capped to requested budget.\n"
            ),
            sources=[f"https://context7.com/?q={query.replace(' ', '+')}"],
            dry_run=True,
        )

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    pkg_token = query.strip().split()[0]
    search_data = get_json(
        CONTEXT7_SEARCH_URL,
        params={"query": pkg_token},
        headers=headers,
        client=client,
    )
    results = search_data.get("results") or search_data.get("items") or []
    if not results or not isinstance(results[0], dict):
        return ResearchResult(
            provider="context7",
            query=query,
            mode="docs",
            markdown=f"_No Context7 library match found for `{pkg_token}`._",
            sources=[],
        )

    lib_id = str(results[0].get("id") or pkg_token).lstrip("/")
    docs_url = CONTEXT7_DOCS_URL.format(lib_id=lib_id)
    doc_data = get_json(
        docs_url,
        params={"topic": query, "tokens": max_tokens},
        headers=headers,
        client=client,
    )
    text = str(doc_data.get("text") or doc_data.get("content") or "_Empty documentation snippet._")
    return ResearchResult(
        provider="context7",
        query=query,
        mode="docs",
        markdown=text,
        sources=[f"https://context7.com/{lib_id}"],
    )
