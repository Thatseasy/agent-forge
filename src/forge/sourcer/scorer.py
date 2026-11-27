"""Scoring, top-N selection, and lean artifact formatting for Intelligence-Sourcer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from forge.sourcer.catalog import BindingType, ProbeResult, SourceCandidate
from forge.sourcer.detector import StackDetectionResult

_BINDING_BASE_SCORES: dict[BindingType, float] = {
    "mcp2cli": 0.96,
    "context7": 0.93,
    "llms_txt": 0.91,
    "cli": 0.87,
    "mcp": 0.72,
}


def _clamp01(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def _has_slim_flag(candidate: SourceCandidate) -> bool:
    if not candidate.mcp_server:
        return False
    for spec in candidate.mcp_server.values():
        if isinstance(spec, dict):
            args = spec.get("args") or []
            if isinstance(args, list) and any(str(a) == "--slim" for a in args):
                return True
    return False


class ScoredSource(BaseModel):
    """Evaluated source candidate with normalized sub-scores and total rank score."""

    candidate: SourceCandidate
    signal_quality: float
    freshness: float
    token_efficiency: float
    binding_type: BindingType
    binding_score: float
    domain_relevance: float
    total_score: float
    probe: ProbeResult | None = None
    rationale: str = ""


class SourceReport(BaseModel):
    """Full sourcing report serialized to `SOURCES_REPORT.json`."""

    project_name: str
    project_dir: str
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    dry_run: bool = False
    detection: StackDetectionResult
    top_sources: list[ScoredSource] = Field(default_factory=list)
    all_candidates: list[ScoredSource] = Field(default_factory=list)
    probes: list[ProbeResult] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)


class SourceScorer:
    """Scores source candidates and selects the top 3-5 diverse primary sources."""

    def score_candidate(
        self,
        candidate: SourceCandidate,
        detection: StackDetectionResult,
        probe: ProbeResult | None = None,
    ) -> ScoredSource:
        """Compute normalized quality, freshness, token efficiency, and binding scores."""
        signal = candidate.base_signal_quality
        freshness = candidate.base_freshness
        token_eff = candidate.base_token_efficiency

        binding_score = _BINDING_BASE_SCORES[candidate.binding_type]
        if candidate.binding_type == "mcp" and _has_slim_flag(candidate):
            binding_score = min(1.0, binding_score + 0.14)

        if candidate.cli_template and candidate.cli_template.get("command") == "mcp2cli":
            args = [str(a) for a in (candidate.cli_template.get("defaultArgs") or [])]
            if "--toon" in args:
                token_eff = min(1.0, token_eff + 0.02)

        notes: list[str] = [f"binding={candidate.binding_type}"]
        if probe is not None:
            if probe.reachable:
                signal += probe.signal_boost
                freshness += probe.freshness_boost
                notes.append(f"probe=ok ({probe.details})")
            elif not probe.dry_run:
                if probe.probe_kind == "cli_local":
                    signal -= 0.03
                    notes.append("probe=not-on-path (installable)")
                else:
                    signal -= 0.15
                    freshness -= 0.15
                    notes.append(f"probe=unreachable ({probe.details})")

        domain_conf_map = {s.domain: s.confidence for s in detection.signals}
        matched_confs = [domain_conf_map[d] for d in candidate.domains if d in domain_conf_map]
        if matched_confs:
            domain_rel = max(matched_confs)
        elif "general" in candidate.domains:
            domain_rel = 0.55
        else:
            domain_rel = 0.30

        signal = _clamp01(signal)
        freshness = _clamp01(freshness)
        token_eff = _clamp01(token_eff)
        binding_score = _clamp01(binding_score)
        domain_rel = _clamp01(domain_rel)

        total = _clamp01(
            0.32 * signal
            + 0.20 * freshness
            + 0.28 * token_eff
            + 0.10 * binding_score
            + 0.10 * domain_rel
        )

        return ScoredSource(
            candidate=candidate,
            signal_quality=signal,
            freshness=freshness,
            token_efficiency=token_eff,
            binding_type=candidate.binding_type,
            binding_score=binding_score,
            domain_relevance=domain_rel,
            total_score=total,
            probe=probe,
            rationale="; ".join(notes),
        )

    def score_all(
        self,
        candidates: list[SourceCandidate],
        detection: StackDetectionResult,
        probes: dict[str, ProbeResult] | None = None,
    ) -> list[ScoredSource]:
        """Score and sort all candidate sources descending by `total_score`."""
        probe_map = probes or {}
        scored = [self.score_candidate(c, detection, probe=probe_map.get(c.id)) for c in candidates]
        scored.sort(key=lambda item: (-item.total_score, item.candidate.id))
        return scored

    def select_top_sources(
        self,
        scored: list[ScoredSource],
        detection: StackDetectionResult,
        *,
        min_sources: int = 3,
        max_sources: int = 5,
    ) -> list[ScoredSource]:
        """Select top 3-5 primary sources balancing score with detected domain coverage."""
        if not scored:
            return []
        cap = max(min_sources, max_sources)
        ordered = sorted(scored, key=lambda item: (-item.total_score, item.candidate.id))

        selected: list[ScoredSource] = []
        selected_ids: set[str] = set()

        # First pass: cover each detected domain with its strongest available source.
        for domain in detection.domains:
            if len(selected) >= cap:
                break
            for item in ordered:
                if item.candidate.id in selected_ids:
                    continue
                if domain in item.candidate.domains:
                    selected.append(item)
                    selected_ids.add(item.candidate.id)
                    break

        # Second pass: fill up to cap (at least min_sources if available) by highest score.
        for item in ordered:
            if len(selected) >= cap:
                break
            if item.candidate.id not in selected_ids:
                selected.append(item)
                selected_ids.add(item.candidate.id)

        selected.sort(key=lambda item: (-item.total_score, item.candidate.id))
        return selected[:cap]


def build_recommended_mcp_config(report: SourceReport) -> dict[str, Any]:
    """Build a token-lean `mcp_config.recommended.json` payload from top selected sources."""
    mcp_servers: dict[str, Any] = {}
    mcp2cli_wrappers: dict[str, Any] = {}
    cli_tools: dict[str, Any] = {}
    context7_docs: list[dict[str, str]] = []
    llms_txt_docs: list[dict[str, str]] = []

    for item in report.top_sources:
        cand = item.candidate
        if cand.binding_type == "mcp" and cand.mcp_server:
            for server_name, server_spec in cand.mcp_server.items():
                if isinstance(server_spec, dict):
                    mcp_servers[server_name] = dict(server_spec)
        elif cand.binding_type == "mcp2cli" or (
            cand.cli_template and cand.cli_template.get("command") == "mcp2cli"
        ):
            tpl = dict(cand.cli_template or {})
            args = [str(a) for a in (tpl.get("defaultArgs") or [])]
            if "--toon" not in args:
                args = ["--session-start", "--toon", *args]
            mcp2cli_wrappers[cand.id] = {
                "command": str(tpl.get("command") or "mcp2cli"),
                "args": args,
                "url": cand.url,
                "description": str(tpl.get("description") or cand.description),
            }
        elif cand.binding_type == "cli":
            tpl = dict(cand.cli_template or {})
            cli_tools[cand.id] = {
                "command": str(tpl.get("command") or cand.id),
                "args": [str(a) for a in (tpl.get("defaultArgs") or [])],
                "url": cand.url,
                "description": str(tpl.get("description") or cand.description),
            }
        elif cand.binding_type == "context7":
            context7_docs.append(
                {
                    "id": cand.id,
                    "name": cand.name,
                    "query": cand.context7_query or cand.domain,
                    "url": cand.url,
                }
            )
        elif cand.binding_type == "llms_txt":
            llms_txt_docs.append(
                {
                    "id": cand.id,
                    "name": cand.name,
                    "url": cand.url,
                }
            )

    return {
        "generated_by": "agent-forge.sourcer",
        "project_name": report.project_name,
        "detected_domains": report.detection.domains,
        "token_budget_policy": {
            "prefer_mcp2cli_toon": True,
            "prefer_slim_mcp": True,
            "top_sources_cap": len(report.top_sources),
        },
        "mcpServers": mcp_servers,
        "mcp2cliWrappers": mcp2cli_wrappers,
        "cliTools": cli_tools,
        "docsSources": {
            "context7": context7_docs,
            "llmsTxt": llms_txt_docs,
        },
    }


def render_sources_markdown(report: SourceReport) -> str:
    """Render concise `SOURCES_RECOMMENDATION.md` from a `SourceReport`."""
    domains_str = ", ".join(report.detection.domains) if report.detection.domains else "general"
    mode_label = "dry-run (offline mock probes)" if report.dry_run else "live probes"
    lines: list[str] = [
        f"# Primary Sources Recommendation — {report.project_name}",
        "",
        f"- **Project Dir:** `{report.project_dir}`",
        f"- **Detected Domains:** {domains_str}",
        f"- **Probe Mode:** {mode_label}",
        f"- **Selected Top Sources:** {len(report.top_sources)}",
        "",
        "## Top Primary Sources",
        "",
        "| Rank | Source | Binding | Signal | Freshness | Token Eff. | Score | Target |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]

    for idx, item in enumerate(report.top_sources, start=1):
        cand = item.candidate
        target = cand.url or cand.id
        lines.append(
            f"| {idx} | {cand.name} (`{cand.id}`) | `{item.binding_type}` | "
            f"{item.signal_quality:.2f} | {item.freshness:.2f} | {item.token_efficiency:.2f} | "
            f"{item.total_score:.3f} | {target} |"
        )

    lines.extend(["", "## Stack Signals", ""])
    if report.detection.signals:
        for sig in report.detection.signals:
            ev = ", ".join(sig.evidence[:5])
            lines.append(f"- **{sig.domain}** (confidence `{sig.confidence:.2f}`): {ev}")
    else:
        lines.append("- No explicit framework manifests detected; using `general` baseline.")

    lines.extend(["", "## Integration & Binding Notes", ""])
    for item in report.top_sources:
        cand = item.candidate
        detail = cand.description or item.rationale
        lines.append(f"- **{cand.name}** (`{item.binding_type}`): {detail}")
        if cand.cli_template:
            cmd = str(cand.cli_template.get("command") or "")
            args = " ".join(str(a) for a in (cand.cli_template.get("defaultArgs") or []))
            lines.append(f"  - CLI: `{cmd} {args}`".rstrip())
        if cand.auth_note:
            lines.append(f"  - Auth: {cand.auth_note}")

    top_ids = {item.candidate.id for item in report.top_sources}
    runners = [item for item in report.all_candidates if item.candidate.id not in top_ids][:5]
    if runners:
        lines.extend(["", "## Runner-Up Candidates", ""])
        for item in runners:
            lines.append(
                f"- `{item.candidate.id}` ({item.binding_type}, score {item.total_score:.3f}): "
                f"{item.candidate.url}"
            )

    return "\n".join(lines) + "\n"
