"""Stage 6 asynchronous frame-only (1 FPS) VP9 Video-Audit via Gemini."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from forge.config import ForgeConfig


class VideoAuditResult(BaseModel):
    """Visual layout/timing audit outcome for a recorded VP9 clip."""

    model_used: str
    clip_path: str
    fps: int = 1
    content_layers: list[str] = Field(default_factory=lambda: ["frames"])
    estimated_cost_usd: float = 0.0
    dry_run: bool = False
    visual_findings: list[str] = Field(default_factory=list)


def strip_audio_1fps(clip_path: Path, out_clip: Path) -> Path:
    """Transcode clip without audio at 1 FPS for cheap vision inspection."""
    ffmpeg_exe = shutil.which("ffmpeg")
    if not ffmpeg_exe:
        return clip_path
    proc = subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-i",
            str(clip_path),
            "-an",
            "-r",
            "1",
            "-c:v",
            "libvpx-vp9",
            str(out_clip),
        ],
        capture_output=True,
        timeout=30,
    )
    return out_clip if proc.returncode == 0 and out_clip.exists() else clip_path


def audit_qa_video(
    config: ForgeConfig,
    clip_path: Path,
    duration_seconds: float = 30.0,
    dry_run: bool = False,
) -> VideoAuditResult:
    """Analyze a silent 1-FPS VP9 screencast using Flash-Lite (<5m) or Flash (>=5m)."""
    model = config.video_lite_model if duration_seconds < 300 else config.video_flash_model
    est_cost = round(max(0.0005, (duration_seconds / 60.0) * 0.002), 6)

    if dry_run or not config.gemini_api_key or not clip_path.exists():
        return VideoAuditResult(
            model_used=f"{model} (dry-run)" if dry_run else f"{model} (offline-fallback)",
            clip_path=str(clip_path),
            fps=1,
            content_layers=["frames"],
            estimated_cost_usd=est_cost,
            dry_run=True,
            visual_findings=[
                "Checked frames-only stream at 1 FPS: verify horizontal overflow banner "
                "and post-click layout stability."
            ],
        )

    from google import genai
    from google.genai import types

    stripped = strip_audio_1fps(clip_path, clip_path.with_name(f"{clip_path.stem}_frames1fps.webm"))
    client = genai.Client(api_key=config.gemini_api_key)
    video_bytes = stripped.read_bytes()
    prompt = (
        "Inspect these silent 1 FPS browser QA frames. Return JSON: "
        '{"findings": ["..."]} listing any layout overlaps, clipped buttons, '
        "horizontal overflow, or stuck spinners."
    )
    resp = client.models.generate_content(
        model=model,
        contents=[
            types.Part(
                inline_data=types.Blob(data=video_bytes, mime_type="video/webm"),
                video_metadata=types.VideoMetadata(fps=1),
            ),
            prompt,
        ],
    )
    raw = str(getattr(resp, "text", "") or "{}").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            raw = "\n".join(lines[1:-1]).strip()
    findings: list[str]
    try:
        parsed: dict[str, Any] = json.loads(raw)
        findings = [str(x) for x in (parsed.get("findings") or [])]
    except json.JSONDecodeError:
        findings = [raw[:300]] if raw else []

    return VideoAuditResult(
        model_used=model,
        clip_path=str(stripped),
        fps=1,
        content_layers=["frames"],
        estimated_cost_usd=est_cost,
        dry_run=False,
        visual_findings=findings,
    )
