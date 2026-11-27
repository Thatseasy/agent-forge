"""Curated source registry and live/dry-run reachability probes."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from pydantic import BaseModel, Field

from forge.config import ForgeConfig
from forge.research.context7_client import CONTEXT7_SEARCH_URL

BindingType = Literal["mcp", "mcp2cli", "context7", "llms_txt", "cli"]

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
ARTIFICIAL_ANALYSIS_MCP_URL = "https://mcp.artificialanalysis.ai/mcp"


class SourceCandidate(BaseModel):
    """A primary documentation, feed, CLI, or token-lean MCP source candidate."""

    id: str
    name: str
    domain: str
    domains: list[str] = Field(default_factory=list)
    binding_type: BindingType
    url: str = ""
    probe_url: str = ""
    probe_kind: str = "http_head"
    context7_query: str = ""
    mcp_server: dict[str, Any] | None = None
    cli_template: dict[str, Any] | None = None
    base_signal_quality: float = 0.85
    base_freshness: float = 0.85
    base_token_efficiency: float = 0.85
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    auth_note: str = ""


class ProbeResult(BaseModel):
    """Result of an optional live or dry-run source availability probe."""

    source_id: str
    probe_kind: str
    reachable: bool
    dry_run: bool = False
    status_code: int | None = None
    latency_ms: float = 0.0
    freshness_boost: float = 0.0
    signal_boost: float = 0.0
    details: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceCatalog:
    """Loads curated stack sources from YAML + mcp_templates.json and runs probes."""

    def __init__(
        self,
        config: ForgeConfig,
        catalog_path: Path | None = None,
        mcp_templates_path: Path | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.catalog_path = (
            catalog_path
            or config.resolve_cli_file("sourcer_catalog.yaml")
            or (config.library_dir / "clis" / "sourcer_catalog.yaml")
        )
        self.mcp_templates_path = (
            mcp_templates_path
            or config.resolve_cli_file("mcp_templates.json")
            or (config.library_dir / "clis" / "mcp_templates.json")
        )
        self.http_client = http_client

    def load_mcp_templates(self) -> dict[str, Any]:
        """Read `library/clis/mcp_templates.json` as read-only reference templates."""
        if not self.mcp_templates_path.is_file():
            return {}
        try:
            data = json.loads(self.mcp_templates_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def load_candidates(self) -> list[SourceCandidate]:
        """Load curated candidates from YAML and enrich with `mcp_templates.json`."""
        templates = self.load_mcp_templates()
        mcp_servers = templates.get("mcpServers") or {}
        cli_templates = templates.get("cliTemplates") or {}

        if not self.catalog_path.is_file():
            return []

        raw = yaml.safe_load(self.catalog_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []

        items = raw.get("sources") or []
        candidates: list[SourceCandidate] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            cand = SourceCandidate.model_validate(entry)
            if not cand.domains:
                cand.domains = [cand.domain]
            elif cand.domain not in cand.domains:
                cand.domains = [cand.domain, *cand.domains]
            self._enrich_from_templates(cand, mcp_servers, cli_templates)
            candidates.append(cand)
        return candidates

    def _enrich_from_templates(
        self,
        cand: SourceCandidate,
        mcp_servers: dict[str, Any],
        cli_templates: dict[str, Any],
    ) -> None:
        if cand.id == "chrome-devtools-slim" and not cand.mcp_server:
            if isinstance(mcp_servers.get("chrome-devtools"), dict):
                cand.mcp_server = {"chrome-devtools": mcp_servers["chrome-devtools"]}
        if cand.id == "artificial-analysis-mcp" and not cand.mcp_server:
            if isinstance(mcp_servers.get("artificial-analysis"), dict):
                cand.mcp_server = {"artificial-analysis": mcp_servers["artificial-analysis"]}
        if cand.id == "playwright-cli" and not cand.cli_template:
            if isinstance(cli_templates.get("playwrightCli"), dict):
                cand.cli_template = cli_templates["playwrightCli"]
        if cand.id == "mcp2cli-bridge" and not cand.cli_template:
            if isinstance(cli_templates.get("mcp2cli"), dict):
                cand.cli_template = cli_templates["mcp2cli"]

    def candidates_for_domains(self, domains: list[str]) -> list[SourceCandidate]:
        """Return candidates matching detected domains plus general fallbacks when needed."""
        all_candidates = self.load_candidates()
        if not domains:
            return [c for c in all_candidates if "general" in c.domains] or all_candidates[:5]

        domain_set = set(domains)
        matched = [c for c in all_candidates if domain_set.intersection(c.domains)]
        if len(matched) < 5:
            seen = {c.id for c in matched}
            for cand in all_candidates:
                if "general" in cand.domains and cand.id not in seen:
                    matched.append(cand)
                    seen.add(cand.id)
        return matched

    def probe_candidates(
        self,
        candidates: list[SourceCandidate],
        dry_run: bool = False,
        client: httpx.Client | None = None,
    ) -> dict[str, ProbeResult]:
        """Run availability/freshness probes across all candidate sources."""
        active_client = client or self.http_client
        results: dict[str, ProbeResult] = {}
        if dry_run:
            for cand in candidates:
                results[cand.id] = self.probe_candidate(cand, dry_run=True, client=active_client)
            return results

        if active_client is not None:
            for cand in candidates:
                results[cand.id] = self.probe_candidate(cand, dry_run=False, client=active_client)
            return results

        with httpx.Client(timeout=6.0, follow_redirects=True) as owned:
            for cand in candidates:
                results[cand.id] = self.probe_candidate(cand, dry_run=False, client=owned)
        return results

    def probe_candidate(
        self,
        candidate: SourceCandidate,
        dry_run: bool = False,
        client: httpx.Client | None = None,
    ) -> ProbeResult:
        """Execute a single candidate's probe (dry-run mock or live HTTP/CLI check)."""
        kind = candidate.probe_kind or "none"
        if dry_run:
            return self._mock_probe(candidate, kind)

        if kind == "none":
            return ProbeResult(
                source_id=candidate.id,
                probe_kind=kind,
                reachable=True,
                dry_run=False,
                freshness_boost=0.02,
                signal_boost=0.02,
                details="curated static MCP/source definition",
            )
        if kind == "cli_local":
            return self._probe_cli_local(candidate)
        if kind == "context7":
            return self._probe_context7(candidate, client=client)
        if kind == "llms_txt":
            return self._probe_llms_txt(candidate, client=client)
        if kind == "openrouter":
            return self._probe_openrouter(candidate, client=client)
        if kind == "artificial_analysis":
            return self._probe_artificial_analysis(candidate, client=client)
        return self._probe_http_head(candidate, client=client)

    def _mock_probe(self, candidate: SourceCandidate, kind: str) -> ProbeResult:
        if kind == "context7":
            query = candidate.context7_query or candidate.id
            return ProbeResult(
                source_id=candidate.id,
                probe_kind=kind,
                reachable=True,
                dry_run=True,
                status_code=200,
                freshness_boost=0.05,
                signal_boost=0.05,
                details=f"dry-run Context7 index verified for `{query}`",
                metadata={"resolved_id": f"/{query}"},
            )
        if kind == "llms_txt":
            return ProbeResult(
                source_id=candidate.id,
                probe_kind=kind,
                reachable=True,
                dry_run=True,
                status_code=200,
                freshness_boost=0.04,
                signal_boost=0.04,
                details="dry-run llms.txt availability check",
                metadata={"content_length": 4096},
            )
        if kind == "openrouter":
            return ProbeResult(
                source_id=candidate.id,
                probe_kind=kind,
                reachable=True,
                dry_run=True,
                status_code=200,
                freshness_boost=0.06,
                signal_boost=0.05,
                details="dry-run OpenRouter 0-USD /api/v1/models pricing feed",
                metadata={
                    "model_count": 300,
                    "free_models_count": 20,
                    "sample_free_models": [
                        "google/gemini-2.5-flash:free",
                        "meta-llama/llama-3.3-70b-instruct:free",
                    ],
                },
            )
        if kind == "artificial_analysis":
            return ProbeResult(
                source_id=candidate.id,
                probe_kind=kind,
                reachable=True,
                dry_run=True,
                status_code=200,
                freshness_boost=0.04,
                signal_boost=0.05,
                details="dry-run Artificial Analysis MCP endpoint reference",
                metadata={"endpoint": candidate.probe_url or ARTIFICIAL_ANALYSIS_MCP_URL},
            )
        if kind == "cli_local":
            cmd = str((candidate.cli_template or {}).get("command") or candidate.id)
            return ProbeResult(
                source_id=candidate.id,
                probe_kind=kind,
                reachable=True,
                dry_run=True,
                freshness_boost=0.02,
                signal_boost=0.03,
                details=f"dry-run local CLI template `{cmd}`",
                metadata={"command": cmd},
            )
        return ProbeResult(
            source_id=candidate.id,
            probe_kind=kind,
            reachable=True,
            dry_run=True,
            status_code=200,
            freshness_boost=0.02,
            signal_boost=0.02,
            details="dry-run probe OK",
        )

    def _probe_cli_local(self, candidate: SourceCandidate) -> ProbeResult:
        cmd = str((candidate.cli_template or {}).get("command") or "")
        found = shutil.which(cmd) if cmd else None
        if found:
            return ProbeResult(
                source_id=candidate.id,
                probe_kind="cli_local",
                reachable=True,
                freshness_boost=0.03,
                signal_boost=0.04,
                details=f"local binary available: {found}",
                metadata={"command": cmd, "path": found},
            )
        return ProbeResult(
            source_id=candidate.id,
            probe_kind="cli_local",
            reachable=False,
            details=f"local binary `{cmd}` not on PATH (installable template)",
            metadata={"command": cmd, "path": ""},
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        client: httpx.Client | None = None,
    ) -> tuple[httpx.Response, float]:
        t0 = time.perf_counter()
        if client is not None:
            resp = client.request(method, url, params=params, headers=headers or {}, timeout=6.0)
        else:
            with httpx.Client(timeout=6.0, follow_redirects=True) as owned:
                resp = owned.request(method, url, params=params, headers=headers or {})
        elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return resp, elapsed_ms

    def _probe_context7(
        self, candidate: SourceCandidate, client: httpx.Client | None = None
    ) -> ProbeResult:
        query = candidate.context7_query or candidate.domain
        headers = {"Accept": "application/json"}
        if self.config.context7_api_key:
            headers["Authorization"] = f"Bearer {self.config.context7_api_key}"
        try:
            resp, latency = self._request(
                "GET",
                CONTEXT7_SEARCH_URL,
                params={"query": query},
                headers=headers,
                client=client,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results") or data.get("items") or []
                if isinstance(results, list) and results and isinstance(results[0], dict):
                    lib_id = str(results[0].get("id") or query)
                    return ProbeResult(
                        source_id=candidate.id,
                        probe_kind="context7",
                        reachable=True,
                        status_code=resp.status_code,
                        latency_ms=latency,
                        freshness_boost=0.05,
                        signal_boost=0.05,
                        details=f"Context7 resolved `{lib_id}`",
                        metadata={"resolved_id": lib_id, "match_count": len(results)},
                    )
            return ProbeResult(
                source_id=candidate.id,
                probe_kind="context7",
                reachable=False,
                status_code=resp.status_code,
                latency_ms=latency,
                details=f"Context7 returned status {resp.status_code} for `{query}`",
            )
        except (httpx.HTTPError, ValueError) as exc:
            return ProbeResult(
                source_id=candidate.id,
                probe_kind="context7",
                reachable=False,
                details=f"Context7 probe error: {exc}",
            )

    def _probe_llms_txt(
        self, candidate: SourceCandidate, client: httpx.Client | None = None
    ) -> ProbeResult:
        target_url = candidate.probe_url or candidate.url
        if not target_url:
            return ProbeResult(
                source_id=candidate.id,
                probe_kind="llms_txt",
                reachable=False,
                details="missing llms.txt URL",
            )
        try:
            resp, latency = self._request("GET", target_url, client=client)
            content_type = resp.headers.get("content-type", "").lower()
            text = resp.text.strip()
            is_valid = (
                resp.status_code == 200 and len(text) > 20 and "text/html" not in content_type
            )
            if is_valid:
                return ProbeResult(
                    source_id=candidate.id,
                    probe_kind="llms_txt",
                    reachable=True,
                    status_code=resp.status_code,
                    latency_ms=latency,
                    freshness_boost=0.04,
                    signal_boost=0.05,
                    details=f"llms.txt available ({len(text)} chars)",
                    metadata={"content_length": len(text), "line_count": len(text.splitlines())},
                )
            return ProbeResult(
                source_id=candidate.id,
                probe_kind="llms_txt",
                reachable=False,
                status_code=resp.status_code,
                latency_ms=latency,
                details=f"llms.txt unavailable (status={resp.status_code}, ct={content_type})",
            )
        except httpx.HTTPError as exc:
            return ProbeResult(
                source_id=candidate.id,
                probe_kind="llms_txt",
                reachable=False,
                details=f"llms.txt probe error: {exc}",
            )

    def _probe_openrouter(
        self, candidate: SourceCandidate, client: httpx.Client | None = None
    ) -> ProbeResult:
        target_url = candidate.probe_url or OPENROUTER_MODELS_URL
        try:
            resp, latency = self._request(
                "GET",
                target_url,
                headers={"Accept": "application/json"},
                client=client,
            )
            if resp.status_code != 200:
                return ProbeResult(
                    source_id=candidate.id,
                    probe_kind="openrouter",
                    reachable=False,
                    status_code=resp.status_code,
                    latency_ms=latency,
                    details=f"OpenRouter feed returned HTTP {resp.status_code}",
                )
            payload = resp.json()
            models = payload.get("data") if isinstance(payload, dict) else []
            if not isinstance(models, list):
                models = []
            free_ids: list[str] = []
            for item in models:
                if not isinstance(item, dict):
                    continue
                pricing = item.get("pricing") or {}
                if (
                    isinstance(pricing, dict)
                    and str(pricing.get("prompt", "")) == "0"
                    and str(pricing.get("completion", "")) == "0"
                ):
                    mid = str(item.get("id") or "")
                    if mid:
                        free_ids.append(mid)
            return ProbeResult(
                source_id=candidate.id,
                probe_kind="openrouter",
                reachable=True,
                status_code=resp.status_code,
                latency_ms=latency,
                freshness_boost=0.06,
                signal_boost=0.05,
                details=(
                    f"OpenRouter 0-USD feed live: {len(models)} models, {len(free_ids)} free tier"
                ),
                metadata={
                    "model_count": len(models),
                    "free_models_count": len(free_ids),
                    "sample_free_models": free_ids[:5],
                },
            )
        except (httpx.HTTPError, ValueError) as exc:
            return ProbeResult(
                source_id=candidate.id,
                probe_kind="openrouter",
                reachable=False,
                details=f"OpenRouter probe error: {exc}",
            )

    def _probe_artificial_analysis(
        self, candidate: SourceCandidate, client: httpx.Client | None = None
    ) -> ProbeResult:
        target_url = candidate.probe_url or ARTIFICIAL_ANALYSIS_MCP_URL
        try:
            resp, latency = self._request(
                "GET",
                target_url,
                headers={"Accept": "application/json, text/event-stream"},
                client=client,
            )
            # Remote Streamable HTTP MCP endpoints commonly return 200, 401/403 (auth), or 405/406
            reachable = resp.status_code < 500 and resp.status_code != 404
            auth_required = resp.status_code in {401, 403}
            return ProbeResult(
                source_id=candidate.id,
                probe_kind="artificial_analysis",
                reachable=reachable,
                status_code=resp.status_code,
                latency_ms=latency,
                freshness_boost=0.04 if reachable else 0.0,
                signal_boost=0.04 if reachable else 0.0,
                details=(
                    "Artificial Analysis MCP endpoint live (OAuth/API-key auth required)"
                    if auth_required
                    else f"Artificial Analysis MCP HTTP {resp.status_code}"
                ),
                metadata={"endpoint": target_url, "auth_required": auth_required},
            )
        except httpx.HTTPError as exc:
            return ProbeResult(
                source_id=candidate.id,
                probe_kind="artificial_analysis",
                reachable=False,
                details=f"Artificial Analysis probe error: {exc}",
            )

    def _probe_http_head(
        self, candidate: SourceCandidate, client: httpx.Client | None = None
    ) -> ProbeResult:
        target_url = candidate.probe_url or candidate.url
        if not target_url:
            return ProbeResult(
                source_id=candidate.id,
                probe_kind="http_head",
                reachable=False,
                details="no URL configured",
            )
        try:
            resp, latency = self._request("HEAD", target_url, client=client)
            reachable = resp.status_code < 400
            return ProbeResult(
                source_id=candidate.id,
                probe_kind="http_head",
                reachable=reachable,
                status_code=resp.status_code,
                latency_ms=latency,
                freshness_boost=0.02 if reachable else 0.0,
                signal_boost=0.02 if reachable else 0.0,
                details=f"HTTP {resp.status_code}",
            )
        except httpx.HTTPError as exc:
            return ProbeResult(
                source_id=candidate.id,
                probe_kind="http_head",
                reachable=False,
                details=f"HTTP probe error: {exc}",
            )
