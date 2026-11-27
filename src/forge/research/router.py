"""Stage 1B research router combining SQLite caching with direct API clients."""

from __future__ import annotations

from pathlib import Path

import httpx

from forge.config import ForgeConfig, write_text_utf8_lf
from forge.research.cache import ResearchCache
from forge.research.context7_client import query_context7
from forge.research.exa_client import query_exa
from forge.research.firecrawl_client import query_firecrawl
from forge.research.models import ResearchResult
from forge.research.perplexity_sonar import query_sonar


class ResearchRouter:
    """Unified research entry point with 24h SQLite response cache."""

    def __init__(
        self,
        config: ForgeConfig,
        cache: ResearchCache | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.cache = cache or ResearchCache(config.state_dir / "research_cache.db")
        self.client = client

    def query(
        self,
        query: str,
        provider: str = "sonar",
        mode: str = "",
        max_tokens: int = 1500,
        dry_run: bool = False,
        out_path: Path | None = None,
        bypass_cache: bool = False,
    ) -> ResearchResult:
        """Execute or retrieve cached research across sonar|firecrawl|context7|exa."""
        prov = provider.lower().strip()
        effective_mode = mode or self._default_mode(prov)

        if not bypass_cache:
            cached = self.cache.get(prov, query, effective_mode, max_tokens)
            if cached is not None:
                if out_path is not None:
                    write_text_utf8_lf(out_path, cached.render_markdown(max_tokens))
                return cached

        result = self._dispatch(prov, query, effective_mode, max_tokens, dry_run)
        self.cache.set(result, max_tokens=max_tokens)
        if out_path is not None:
            write_text_utf8_lf(out_path, result.render_markdown(max_tokens))
        return result

    @staticmethod
    def _default_mode(provider: str) -> str:
        return {
            "sonar": "low",
            "firecrawl": "scrape",
            "context7": "docs",
            "exa": "auto",
        }.get(provider, "low")

    def _dispatch(
        self,
        provider: str,
        query: str,
        mode: str,
        max_tokens: int,
        dry_run: bool,
    ) -> ResearchResult:
        if provider == "sonar":
            return query_sonar(
                query,
                api_key=self.config.perplexity_api_key,
                mode=mode,
                max_tokens=max_tokens,
                dry_run=dry_run,
                client=self.client,
            )
        if provider == "firecrawl":
            return query_firecrawl(
                query,
                api_key=self.config.firecrawl_api_key,
                action=mode,
                dry_run=dry_run,
                client=self.client,
            )
        if provider == "context7":
            return query_context7(
                query,
                api_key=self.config.context7_api_key,
                max_tokens=max_tokens,
                dry_run=dry_run,
                client=self.client,
            )
        if provider == "exa":
            return query_exa(
                query,
                api_key=self.config.exa_api_key,
                mode=mode,
                max_tokens=max_tokens,
                dry_run=dry_run,
                client=self.client,
            )
        raise ValueError(f"Unsupported research provider: {provider!r}")
