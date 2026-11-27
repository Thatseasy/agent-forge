"""Shared models and HTTP request helpers for Stage 1B research providers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field


class ResearchResult(BaseModel):
    """Normalized markdown research response across all Stage 1B providers."""

    provider: str
    query: str
    mode: str = "low"
    markdown: str
    sources: list[str] = Field(default_factory=list)
    cached: bool = False
    dry_run: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    def render_markdown(self, max_tokens: int = 1500) -> str:
        """Format compact markdown with sources truncated to max_tokens budget."""
        char_limit = max(200, max_tokens * 4)
        body = self.markdown.strip()
        if len(body) > char_limit:
            body = body[: char_limit - 16].rstrip() + "\n\n...[truncated]"
        if self.sources:
            src_block = "\n".join(f"- {s}" for s in self.sources[:10])
            return f"{body}\n\n### Sources\n{src_block}\n"
        return f"{body}\n"


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    client: httpx.Client | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Send a JSON POST request using an injected or ephemeral httpx.Client."""
    if client is not None:
        resp = client.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    with httpx.Client(timeout=timeout) as owned:
        resp = owned.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Send a GET request and decode JSON/text cleanly."""
    if client is not None:
        resp = client.get(url, params=params, headers=headers or {}, timeout=timeout)
        resp.raise_for_status()
        return _decode_get_response(resp)
    with httpx.Client(timeout=timeout) as owned:
        resp = owned.get(url, params=params, headers=headers or {})
        resp.raise_for_status()
        return _decode_get_response(resp)


def _decode_get_response(resp: httpx.Response) -> dict[str, Any]:
    content_type = resp.headers.get("content-type", "")
    if "json" in content_type.lower():
        data = resp.json()
        return data if isinstance(data, dict) else {"items": data}
    return {"text": resp.text}
