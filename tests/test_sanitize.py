"""Unit and integration tests for local PII sanitization (`forge.sanitize`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.sanitize import assert_corpus_sanitized, sanitize_path


def test_sanitize_directory_handles_markdown_csv_json_and_secrets(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw_sales"
    raw_dir.mkdir(parents=True)

    (raw_dir / "transcript.md").write_text(
        "# Discovery Call\n"
        "Kunde: Anna Müller (anna.mueller@acme-kunde.de)\n"
        "Telefon: +49 170 1234567 | IBAN: DE89 3704 0044 0532 0130 00 | USt-Id: DE123456789\n"
        "Notiz vom 2026-11-27: API Key sk-proj-supersecretkey1234567890 darf nicht raus.\n"
        "Version 1.2.3 und Beispiel info@example.com bleiben erhalten.\n",
        encoding="utf-8",
        newline="\n",
    )
    (raw_dir / "leads.csv").write_text(
        "first_name,company_name,email,phone,notes\n"
        "Max,Muster GmbH,max@muster-gmbh.de,030-98765432,Rückruf von Max\n",
        encoding="utf-8",
        newline="\n",
    )
    (raw_dir / "lead.json").write_text(
        json.dumps(
            {
                "contact_name": "Erika Mustermann",
                "company": "Beta AG",
                "domain": "acme-kunde.de",
                "token": "Bearer abcdefghijklmnopqrstuvwx",
            }
        ),
        encoding="utf-8",
        newline="\n",
    )

    out_dir = tmp_path / "sanitized"
    report = sanitize_path(
        raw_dir,
        out_dir,
        extra_names=["Anna Müller", "Max Mustermann"],
        extra_companies=["Muster GmbH"],
    )

    assert report.clean is True
    assert report.total_files == 3
    assert report.modified_files == 3
    assert report.counts_by_category["secret"] >= 2
    assert report.counts_by_category["email"] >= 2
    assert report.counts_by_category["phone"] >= 2
    assert report.counts_by_category["iban"] == 1
    assert report.counts_by_category["tax_id"] == 1

    clean_md = (out_dir / "transcript.md").read_text("utf-8")
    assert "anna.mueller@acme-kunde.de" not in clean_md
    assert "acme-kunde.de" not in clean_md
    assert "170 1234567" not in clean_md
    assert "DE89 3704" not in clean_md
    assert "DE123456789" not in clean_md
    assert "sk-proj-" not in clean_md
    assert "2026-11-27" in clean_md
    assert "1.2.3" in clean_md
    assert "info@example.com" in clean_md

    clean_csv = (out_dir / "leads.csv").read_text("utf-8")
    assert "Muster GmbH" not in clean_csv
    assert "max@muster-gmbh.de" not in clean_csv
    assert "[PERSON_" in clean_csv

    clean_json = json.loads((out_dir / "lead.json").read_text("utf-8"))
    assert clean_json["contact_name"].startswith("[PERSON_")
    assert clean_json["company"].startswith("[COMPANY_")
    assert "abcdefghijklmnop" not in clean_json["token"]

    assert (out_dir / "SANITIZE_REPORT.json").is_file()
    # Sanitized output must pass assert_corpus_sanitized cleanly
    verified = assert_corpus_sanitized(out_dir)
    assert len(verified) == 3

    # Raw dir must be rejected by assert_corpus_sanitized
    with pytest.raises(ValueError, match="Unsanitized PII/secrets"):
        assert_corpus_sanitized(raw_dir)


def test_sanitize_dry_run_writes_no_files(tmp_path: Path) -> None:
    raw_file = tmp_path / "note.md"
    raw_file.write_text("Email: lead@kundenfirma.de\n", encoding="utf-8", newline="\n")
    out_file = tmp_path / "clean_note.md"

    import subprocess
    import sys

    report = sanitize_path(raw_file, out_file, dry_run=True)
    assert report.dry_run is True
    assert report.counts_by_category["email"] == 1
    assert not out_file.exists()
    assert not (tmp_path / "SANITIZE_REPORT.json").exists()

    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            sys.executable,
            str(repo_root / "forge.py"),
            "sanitize",
            "--input",
            str(raw_file),
            "--out",
            str(out_file),
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert out_file.is_file()
    subprocess.run(
        [
            sys.executable,
            str(repo_root / "forge.py"),
            "sanitize",
            "--input",
            str(out_file),
            "--verify",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
