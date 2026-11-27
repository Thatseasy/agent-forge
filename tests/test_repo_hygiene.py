"""Repository-wide hygiene invariants: UTF-8 without BOM, LF line endings, and size limits."""

from __future__ import annotations

from pathlib import Path

from forge.config import estimate_tokens

REPO_ROOT = Path(__file__).resolve().parents[1]
TEXT_EXTS = {".py", ".json", ".yaml", ".yml", ".md"}
IGNORED_PARTS = {
    ".venv",
    ".git",
    "staging",
    "archive",
    "qa-artifacts",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


def test_all_repo_text_files_utf8_no_bom_and_lf() -> None:
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTS:
            continue
        if any(part in IGNORED_PARTS for part in path.parts):
            continue
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"BOM found in {path}"
        assert b"\r\n" not in raw, f"CRLF found in {path}"
        raw.decode("utf-8")


def test_agents_md_and_skills_respect_context_budgets() -> None:
    agents_md = REPO_ROOT / "AGENTS.md"
    agents_raw = agents_md.read_bytes()
    assert len(agents_raw) < 24_000
    assert len(agents_raw.decode("utf-8").splitlines()) < 100

    assert not (REPO_ROOT / ".agents" / "skills" / "_archive").exists()

    for root in (REPO_ROOT / ".agents" / "skills", REPO_ROOT / "library" / "skills"):
        for skill_md in root.glob("*/SKILL.md"):
            text = skill_md.read_text(encoding="utf-8")
            assert len(text.splitlines()) < 500, f"{skill_md} exceeds 500 lines"
            assert estimate_tokens(text) < 5000, f"{skill_md} exceeds 5000 tokens"
            assert "Use when" in text and "Do not use" in text, f"{skill_md} missing triggers"
