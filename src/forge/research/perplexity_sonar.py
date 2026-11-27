"""Perplexity Sonar Agent API (`/v1/responses`) wrapper with low-effort token economy."""

from __future__ import annotations

from typing import Any

import httpx

from forge.research.models import ResearchResult, post_json

PERPLEXITY_URL = "https://api.perplexity.ai/v1/responses"


def _extract_responses_output(data: dict[str, Any]) -> tuple[str, list[str]]:
    text = str(data.get("output_text") or "").strip()
    sources: list[str] = []

    if not text:
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                    chunk = str(part.get("text") or "").strip()
                    if chunk:
                        text = f"{text}\n{chunk}".strip() if text else chunk
                    for ann in part.get("annotations") or []:
                        if isinstance(ann, dict) and ann.get("url"):
                            sources.append(str(ann["url"]))

    if not text:
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            text = str((choices[0].get("message") or {}).get("content") or "").strip()

    for res in data.get("search_results") or []:
        if isinstance(res, dict) and res.get("url"):
            sources.append(str(res["url"]))
    for cit in data.get("citations") or []:
        sources.append(str(cit))

    deduped = list(dict.fromkeys(s for s in sources if s))
    return text or "_Empty Sonar response._", deduped


def query_sonar(
    query: str,
    api_key: str,
    mode: str = "low",
    max_tokens: int = 1500,
    dry_run: bool = False,
    client: httpx.Client | None = None,
) -> ResearchResult:
    """Query Perplexity Agent API (`/v1/responses`) or return dry-run mock."""
    normalized_mode = "high" if mode.lower() == "high" else "low"
    if dry_run:
        return ResearchResult(
            provider="sonar",
            query=query,
            mode=normalized_mode,
            markdown=(
                f"## Sonar ({normalized_mode}) Dry-Run Synthesis\n\n"
                f"- Query: `{query}`\n"
                "- Key finding: Direct API/CLI retrieval avoids full browser overhead.\n"
                "- Next action: Verify exact version constraints against local toolchain.\n"
            ),
            sources=["https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/overview"],
            dry_run=True,
        )

    if not api_key:
        raise ValueError("Missing PERPLEXITY_API_KEY. Set it in .env or pass --dry-run.")

    payload = {
        "model": "perplexity/sonar",
        "effort": normalized_mode,
        "max_output_tokens": max_tokens,
        "instructions": (
            "Respond in concise bulleted Markdown with concrete technical facts only."
        ),
        "input": query,
    }
    data = post_json(
        PERPLEXITY_URL,
        payload=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        client=client,
    )
    content, citations = _extract_responses_output(data)
    return ResearchResult(
        provider="sonar",
        query=query,
        mode=normalized_mode,
        markdown=content,
        sources=citations,
    )
