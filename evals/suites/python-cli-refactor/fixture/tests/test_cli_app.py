"""Public suite tests visible inside the benchmark worktree."""

from __future__ import annotations

from pathlib import Path

from cli_app import export_manifest, main, normalize_records


def test_normalize_records_dedup_and_sort() -> None:
    assert normalize_records(["Alpha Skill", "alpha skill", " ", "Beta"]) == [
        "alpha-skill",
        "beta",
    ]


def test_export_manifest_writes_utf8_no_bom(tmp_path: Path) -> None:
    out = tmp_path / "manifest.json"
    export_manifest(["One", "Two"], out)
    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw


def test_main_missing_file_returns_nonzero(tmp_path: Path) -> None:
    rc = main([str(tmp_path / "missing.txt"), "--out", str(tmp_path / "out.json")])
    assert rc != 0
