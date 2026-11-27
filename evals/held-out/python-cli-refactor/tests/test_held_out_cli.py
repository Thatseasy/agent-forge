"""Held-out verification tests mounted ONLY during isolated patch re-apply."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from cli_app import export_manifest, normalize_records


def test_held_out_empty_and_whitespace_inputs(tmp_path: Path) -> None:
    assert normalize_records([]) == []
    assert normalize_records(["", "   ", "\t"]) == []
    out = tmp_path / "empty.json"
    export_manifest([], out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == {"count": 0, "items": []}


def test_held_out_cli_help_flag_exits_zero() -> None:
    fixture_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(fixture_root / "cli_app.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Normalize CLI records cleanly." in proc.stdout
