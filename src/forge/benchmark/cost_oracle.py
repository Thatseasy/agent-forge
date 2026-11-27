"""Model Cost-Oracle combining offline snapshot rates with Artificial Analysis & OpenRouter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from forge.config import ForgeConfig, write_json_utf8_lf
from forge.paths import resolve_package_assets_dir

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
TierKind = Literal["fast", "balanced", "judge"]

DEFAULT_FALLBACK_RATE = (1.25, 5.0, 0.125)


class ModelRate(BaseModel):
    """Normalized model cost, throughput, and quality record for Stage 3 arbitrage."""

    model_id: str
    provider: str = ""
    tier: TierKind = "balanced"
    input_per_m: float
    output_per_m: float
    cache_per_m: float = 0.0
    context_window: int | None = None
    tokens_per_s: float | None = None
    intelligence_index: float | None = None
    source: str = "offline-fallback-snapshot"


class CostOracleSnapshot(BaseModel):
    """Serialized snapshot under `library/clis/cost_oracle_snapshot.json` or staging cache."""

    version: str = "1.0"
    source: str = "offline-fallback-snapshot"
    models: list[ModelRate] = Field(default_factory=list)


def _canonical_model_key(model_id: str) -> str:
    clean = model_id.strip().lower()
    if "/" in clean:
        clean = clean.split("/", 1)[1]
    return clean


class CostOracle:
    """Resolves per-model token pricing and price-performance arbitrage candidates."""

    def __init__(
        self,
        config: ForgeConfig | None = None,
        *,
        snapshot_path: Path | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.http_client = http_client
        default_snap = (
            (
                config.resolve_cli_file("cost_oracle_snapshot.json")
                or (config.library_dir / "clis" / "cost_oracle_snapshot.json")
            )
            if config is not None
            else (resolve_package_assets_dir() / "clis" / "cost_oracle_snapshot.json")
        )
        self.snapshot_path = snapshot_path or default_snap
        self.cache_path = (
            config.root / "staging" / "cache" / "cost_oracle_live.json"
            if config is not None
            else None
        )
        self._rates: dict[str, ModelRate] = {}
        self._load()

    def _load(self) -> None:
        for candidate in (self.snapshot_path, self.cache_path):
            if candidate is None or not candidate.is_file():
                continue
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
                snap = CostOracleSnapshot.model_validate(raw)
                for item in snap.models:
                    self._rates[_canonical_model_key(item.model_id)] = item
            except (OSError, json.JSONDecodeError, ValueError):
                continue

    def all_models(self) -> list[ModelRate]:
        return sorted(
            self._rates.values(),
            key=lambda m: (m.input_per_m + m.output_per_m, m.model_id),
        )

    def lookup(self, model_id: str) -> ModelRate:
        """Resolve exact/canonical model rate or return safe generic fallback."""
        key = _canonical_model_key(model_id)
        if key in self._rates:
            return self._rates[key]
        for known_key, rate in self._rates.items():
            if known_key in key or key in known_key:
                return rate
        in_p, out_p, cache_p = DEFAULT_FALLBACK_RATE
        return ModelRate(
            model_id=model_id or "default",
            provider=model_id.split("/", 1)[0] if "/" in model_id else "",
            input_per_m=in_p,
            output_per_m=out_p,
            cache_per_m=cache_p,
            source="generic-fallback",
        )

    def estimate_cost(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
    ) -> float:
        """Estimate USD cost for a single call using per-model oracle pricing."""
        rate = self.lookup(model_id)
        uncached = max(0, input_tokens - cache_read_tokens)
        total = (
            (uncached / 1_000_000) * rate.input_per_m
            + (cache_read_tokens / 1_000_000) * rate.cache_per_m
            + (output_tokens / 1_000_000) * rate.output_per_m
        )
        return round(total, 6)

    def recommend_arbitrage_models(
        self,
        *,
        tier: TierKind | None = None,
        limit: int = 3,
    ) -> list[ModelRate]:
        """Return candidate models ordered by blended quality-per-dollar efficiency."""
        candidates = [m for m in self._rates.values() if tier is None or m.tier == tier]
        if not candidates:
            candidates = list(self._rates.values())

        def _score_key(m: ModelRate) -> tuple[float, float]:
            blended_price = max(0.01, (m.input_per_m * 0.3) + (m.output_per_m * 0.7))
            quality = m.intelligence_index if m.intelligence_index is not None else 50.0
            return (-(quality / blended_price), blended_price)

        return sorted(candidates, key=_score_key)[: max(1, limit)]

    def ingest_artificial_analysis_models(self, payload: dict[str, Any] | list[Any]) -> int:
        """Ingest model benchmark/pricing rows obtained from Artificial Analysis MCP or API."""
        rows = (
            payload
            if isinstance(payload, list)
            else (payload.get("data") or payload.get("models") or [])
        )
        if not isinstance(rows, list):
            return 0
        count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            slug = str(row.get("slug") or row.get("model_id") or row.get("name") or "").strip()
            if not slug:
                continue
            raw_pricing = row.get("pricing")
            pricing: dict[str, Any] = raw_pricing if isinstance(raw_pricing, dict) else {}
            in_p = float(
                row.get("input_per_m")
                or pricing.get("price_1m_input_tokens")
                or DEFAULT_FALLBACK_RATE[0]
            )
            out_p = float(
                row.get("output_per_m")
                or pricing.get("price_1m_output_tokens")
                or DEFAULT_FALLBACK_RATE[1]
            )
            cache_p = float(row.get("cache_per_m") or round(in_p * 0.1, 4))
            raw_evals = row.get("evaluations")
            evals: dict[str, Any] = raw_evals if isinstance(raw_evals, dict) else {}
            intel = evals.get("artificial_analysis_intelligence_index") or row.get(
                "intelligence_index"
            )
            tps = row.get("median_output_tokens_per_second") or row.get("tokens_per_s")
            tier: TierKind = "fast" if out_p <= 1.0 else ("balanced" if out_p <= 3.5 else "judge")
            rate = ModelRate(
                model_id=slug,
                provider=str(row.get("model_creator") or row.get("provider") or ""),
                tier=tier,
                input_per_m=round(in_p, 4),
                output_per_m=round(out_p, 4),
                cache_per_m=round(cache_p, 4),
                tokens_per_s=float(tps) if tps is not None else None,
                intelligence_index=float(intel) if intel is not None else None,
                source="artificial-analysis",
            )
            self._rates[_canonical_model_key(slug)] = rate
            count += 1
        self._persist_cache()
        return count

    def refresh_from_openrouter(self, *, timeout_s: float = 10.0) -> list[ModelRate]:
        """Refresh pricing snapshot live from OpenRouter `/api/v1/models`."""
        own_client = self.http_client is None
        client = self.http_client or httpx.Client(timeout=timeout_s)
        try:
            resp = client.get(OPENROUTER_MODELS_URL, headers={"Accept": "application/json"})
            resp.raise_for_status()
            payload = resp.json()
        finally:
            if own_client:
                client.close()

        rows = payload.get("data") if isinstance(payload, dict) else []
        updated: list[ModelRate] = []
        if not isinstance(rows, list):
            return updated
        for row in rows:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("id") or "").strip()
            raw_pricing = row.get("pricing")
            pricing: dict[str, Any] = raw_pricing if isinstance(raw_pricing, dict) else {}
            try:
                in_per_m = round(float(pricing.get("prompt") or 0.0) * 1_000_000, 4)
                out_per_m = round(float(pricing.get("completion") or 0.0) * 1_000_000, 4)
            except (TypeError, ValueError):
                continue
            if not mid or (in_per_m == 0.0 and out_per_m == 0.0 and ":free" not in mid):
                continue
            tier: TierKind = (
                "fast" if out_per_m <= 1.0 else ("balanced" if out_per_m <= 3.5 else "judge")
            )
            ctx = row.get("context_length")
            rate = ModelRate(
                model_id=mid,
                provider=mid.split("/", 1)[0] if "/" in mid else "",
                tier=tier,
                input_per_m=in_per_m,
                output_per_m=out_per_m,
                cache_per_m=round(in_per_m * 0.1, 4),
                context_window=int(ctx) if isinstance(ctx, int) else None,
                source="openrouter-live",
            )
            self._rates[_canonical_model_key(mid)] = rate
            updated.append(rate)
        if updated:
            self._persist_cache()
        return updated

    def _persist_cache(self) -> None:
        if self.cache_path is None:
            return
        snap = CostOracleSnapshot(source="live-cache", models=self.all_models())
        write_json_utf8_lf(self.cache_path, snap.model_dump())
