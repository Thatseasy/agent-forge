"""Stage 6 Exploratory Hybrid-Browser QA with Playwright Screencast & Multi-Signal Telemetry."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from forge.config import ForgeConfig, write_json_utf8_lf, write_text_utf8_lf


class QABugFinding(BaseModel):
    """Structured bug finding separating product bugs from locator-healing hints."""

    bug_id: str
    category: str  # "console_error" | "network_error" | "layout_overflow" | "interaction"
    severity: str
    message: str
    reproduction_hint: str
    fix_classification: str = "product_bug"  # vs "locator_healing"


class QASessionReport(BaseModel):
    """Complete output of an exploratory QA walkthrough."""

    target_url: str
    charter: str
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    dry_run: bool = False
    console_errors: list[str] = Field(default_factory=list)
    network_failures: list[str] = Field(default_factory=list)
    overflow_detected: bool = False
    overflow_metrics: dict[str, int] = Field(default_factory=dict)
    findings: list[QABugFinding] = Field(default_factory=list)
    video_clip_path: str = ""
    action_tape_path: str = ""
    reproduction_spec_path: str = ""
    report_path: str = ""


def _validate_url_allowed(url: str, allowed_domains: list[str]) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Blocked unsupported URL scheme: {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if host not in {d.lower() for d in allowed_domains}:
        raise ValueError(f"Blocked domain {host!r}: not in allowed_domains ({allowed_domains})")


def _create_minimal_webm_stub(dest: Path) -> None:
    """Create a valid minimal VP9 WebM clip via ffmpeg when simulating dry-run."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_exe = shutil.which("ffmpeg")
    if ffmpeg_exe:
        proc = subprocess.run(
            [
                ffmpeg_exe,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=white:s=375x812:d=1",
                "-an",
                "-c:v",
                "libvpx-vp9",
                "-b:v",
                "100k",
                str(dest),
            ],
            capture_output=True,
            timeout=15,
        )
        if proc.returncode == 0 and dest.exists():
            return
    # Minimal EBML/WebM header fallback if ffmpeg unavailable
    dest.write_bytes(b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\x82\x84webm")


class QAExplorer:
    """Drives exploratory browser walkthroughs and exports reproduction specs on failures."""

    DEFAULT_ALLOWED_DOMAINS = ["localhost", "127.0.0.1"]

    def __init__(self, config: ForgeConfig) -> None:
        self.config = config

    def explore(
        self,
        url: str,
        charter: str = "all",
        out_dir: Path | None = None,
        storage_state: Path | None = None,
        allowed_domains: list[str] | None = None,
        dry_run: bool = False,
    ) -> QASessionReport:
        """Run exploratory QA against `url`, collecting console/network/overflow & VP9 clip."""
        whitelist = allowed_domains or list(self.DEFAULT_ALLOWED_DOMAINS)
        _validate_url_allowed(url, whitelist)

        artifacts_dir = out_dir or (self.config.root / "qa-artifacts" / "latest")
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        clip_path = artifacts_dir / "session_vp9.webm"
        tape_path = artifacts_dir / "action_tape.jsonl"

        if dry_run:
            return self._run_dry_explore(url, charter, artifacts_dir, clip_path, tape_path)

        return self._run_playwright_explore(
            url=url,
            charter=charter,
            artifacts_dir=artifacts_dir,
            clip_path=clip_path,
            tape_path=tape_path,
            storage_state=storage_state,
        )

    def _run_dry_explore(
        self,
        url: str,
        charter: str,
        artifacts_dir: Path,
        clip_path: Path,
        tape_path: Path,
    ) -> QASessionReport:
        actions = [
            {"step": 1, "action": "goto", "target": url, "status": "ok"},
            {"step": 2, "action": "set_viewport", "size": "375x812", "status": "ok"},
            {"step": 3, "action": "check_overflow", "scrollWidth": 640, "innerWidth": 375},
            {"step": 4, "action": "dblclick_impatient", "target": "#submit-btn", "status": "ok"},
        ]
        write_text_utf8_lf(
            tape_path,
            "\n".join(json.dumps(a, ensure_ascii=False) for a in actions) + "\n",
        )
        _create_minimal_webm_stub(clip_path)

        console_errors = ["Intentional fixture runtime console error: failed config load"]
        overflow_metrics = {"scrollWidth": 640, "innerWidth": 375}
        findings = self._build_findings(console_errors, [], True, overflow_metrics)
        return self._finalize_report(
            url=url,
            charter=charter,
            dry_run=True,
            artifacts_dir=artifacts_dir,
            clip_path=clip_path,
            tape_path=tape_path,
            console_errors=console_errors,
            network_failures=[],
            overflow_detected=True,
            overflow_metrics=overflow_metrics,
            findings=findings,
        )

    def _run_playwright_explore(
        self,
        url: str,
        charter: str,
        artifacts_dir: Path,
        clip_path: Path,
        tape_path: Path,
        storage_state: Path | None,
    ) -> QASessionReport:
        from playwright.sync_api import sync_playwright

        console_errors: list[str] = []
        network_failures: list[str] = []
        actions: list[dict[str, Any]] = []
        overflow_detected = False
        overflow_metrics: dict[str, int] = {}

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx_kwargs: dict[str, Any] = {"viewport": {"width": 375, "height": 812}}
            if storage_state and storage_state.exists():
                ctx_kwargs["storage_state"] = str(storage_state)
            context = browser.new_context(**ctx_kwargs)
            page = context.new_page()

            page.on(
                "console",
                lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
            )
            page.on("pageerror", lambda err: console_errors.append(str(err)))
            page.on(
                "response",
                lambda resp: (
                    network_failures.append(f"HTTP {resp.status} {resp.url}")
                    if resp.status >= 400
                    else None
                ),
            )
            page.on(
                "requestfailed",
                lambda req: network_failures.append(f"FAILED {req.url}: {req.failure}"),
            )

            with page.screencast.start(path=clip_path, fps=5):
                page.goto(url, wait_until="domcontentloaded", timeout=15_000)
                actions.append({"step": 1, "action": "goto", "target": url})

                # Mobile overflow check (375x812)
                dims = page.evaluate(
                    "() => ({scrollWidth: document.documentElement.scrollWidth, "
                    "innerWidth: window.innerWidth})"
                )
                sw = int(dims.get("scrollWidth", 0))
                iw = int(dims.get("innerWidth", 0))
                overflow_metrics = {"scrollWidth": sw, "innerWidth": iw}
                overflow_detected = sw > iw
                actions.append({"step": 2, "action": "check_overflow", **overflow_metrics})

                # Impatient double-click & adversarial boundary input if controls exist
                if charter in {"all", "adversarial-empty"}:
                    inputs = page.locator("input[type='text'], input:not([type]), textarea")
                    if inputs.count() > 0:
                        inputs.first.fill("A" * 310 + " 🚀 <test>")
                        actions.append({"step": 3, "action": "adversarial_fill"})

                if charter in {"all", "impatient-user"}:
                    buttons = page.locator("button, [role='button']")
                    if buttons.count() > 0:
                        buttons.first.dblclick(delay=80)
                        actions.append({"step": 4, "action": "impatient_dblclick"})

                page.wait_for_timeout(300)

            context.close()
            browser.close()

        write_text_utf8_lf(
            tape_path,
            "\n".join(json.dumps(a, ensure_ascii=False) for a in actions) + "\n",
        )
        findings = self._build_findings(
            console_errors, network_failures, overflow_detected, overflow_metrics
        )
        # retain-on-failure: delete screencast clip if zero bugs discovered
        if not findings and clip_path.exists():
            clip_path.unlink(missing_ok=True)

        return self._finalize_report(
            url=url,
            charter=charter,
            dry_run=False,
            artifacts_dir=artifacts_dir,
            clip_path=clip_path if clip_path.exists() else None,
            tape_path=tape_path,
            console_errors=console_errors,
            network_failures=network_failures,
            overflow_detected=overflow_detected,
            overflow_metrics=overflow_metrics,
            findings=findings,
        )

    @staticmethod
    def _build_findings(
        console_errors: list[str],
        network_failures: list[str],
        overflow_detected: bool,
        overflow_metrics: dict[str, int],
    ) -> list[QABugFinding]:
        findings: list[QABugFinding] = []
        for idx, err in enumerate(console_errors, start=1):
            findings.append(
                QABugFinding(
                    bug_id=f"BUG-CONSOLE-{idx:03d}",
                    category="console_error",
                    severity="high",
                    message=err,
                    reproduction_hint="Listen to page.on('console') and assert zero error logs.",
                    fix_classification="product_bug",
                )
            )
        for idx, net in enumerate(network_failures, start=1):
            findings.append(
                QABugFinding(
                    bug_id=f"BUG-NET-{idx:03d}",
                    category="network_error",
                    severity="high",
                    message=net,
                    reproduction_hint="Intercept network responses and assert status < 400.",
                    fix_classification="product_bug",
                )
            )
        if overflow_detected:
            findings.append(
                QABugFinding(
                    bug_id="BUG-LAYOUT-001",
                    category="layout_overflow",
                    severity="medium",
                    message=(
                        f"Horizontal overflow at mobile viewport: "
                        f"scrollWidth={overflow_metrics.get('scrollWidth')} > "
                        f"innerWidth={overflow_metrics.get('innerWidth')}"
                    ),
                    reproduction_hint="Set viewport 375x812 and assert scrollWidth <= innerWidth.",
                    fix_classification="product_bug",
                )
            )
        return findings

    def _finalize_report(
        self,
        url: str,
        charter: str,
        dry_run: bool,
        artifacts_dir: Path,
        clip_path: Path | None,
        tape_path: Path,
        console_errors: list[str],
        network_failures: list[str],
        overflow_detected: bool,
        overflow_metrics: dict[str, int],
        findings: list[QABugFinding],
    ) -> QASessionReport:
        spec_path_str = ""
        if findings:
            spec_path = artifacts_dir / "reproduction_001.spec.ts"
            spec_code = self._render_reproduction_spec(url, findings)
            write_text_utf8_lf(spec_path, spec_code)
            spec_path_str = str(spec_path)

        report_path = artifacts_dir / "qa_report.json"
        report = QASessionReport(
            target_url=url,
            charter=charter,
            dry_run=dry_run,
            console_errors=console_errors,
            network_failures=network_failures,
            overflow_detected=overflow_detected,
            overflow_metrics=overflow_metrics,
            findings=findings,
            video_clip_path=str(clip_path) if clip_path else "",
            action_tape_path=str(tape_path),
            reproduction_spec_path=spec_path_str,
            report_path=str(report_path),
        )
        write_json_utf8_lf(report_path, report.model_dump())
        return report

    @staticmethod
    def _render_reproduction_spec(url: str, findings: list[QABugFinding]) -> str:
        summary_lines = "\n".join(
            f"// - [{f.bug_id}] ({f.fix_classification}) {f.message}" for f in findings
        )
        return (
            "import { test, expect } from '@playwright/test';\n\n"
            "// Auto-generated from exploratory QA walkthrough.\n"
            "// Product bugs vs. locator healing must be fixed separately.\n"
            f"{summary_lines}\n\n"
            "test('reproduce exploratory QA findings', async ({ page }) => {\n"
            "  const consoleErrors: string[] = [];\n"
            "  page.on('console', (msg) => {\n"
            "    if (msg.type() === 'error') consoleErrors.push(msg.text());\n"
            "  });\n"
            "  await page.setViewportSize({ width: 375, height: 812 });\n"
            f"  await page.goto({json.dumps(url)});\n"
            "  const overflow = await page.evaluate(\n"
            "    () => document.documentElement.scrollWidth > window.innerWidth\n"
            "  );\n"
            "  expect(overflow).toBeFalsy();\n"
            "  expect(consoleErrors).toEqual([]);\n"
            "});\n"
        )
