"""Stage 2 local TF-IDF semantic deduplication against `library/skills/`."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from forge.gate.validator import parse_frontmatter


class DuplicateMatch(BaseModel):
    """Similarity hit against an existing library skill."""

    skill_name: str
    skill_path: str
    similarity: float


class DedupResult(BaseModel):
    """Result of local TF-IDF overlap comparison."""

    duplicate_flagged: bool = False
    max_similarity: float = 0.0
    matches: list[DuplicateMatch] = Field(default_factory=list)


def _skill_text(skill_dir: Path) -> str:
    md_path = skill_dir / "SKILL.md"
    if not md_path.exists():
        return ""
    raw = md_path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_frontmatter(raw)
    desc = str(meta.get("description") or "")
    return f"{desc}\n{body}".strip()


def check_semantic_duplicates(
    candidate_dir: Path,
    library_skills_dir: Path,
    threshold: float = 0.50,
) -> DedupResult:
    """Compare candidate skill text against existing library skills using local TF-IDF cosine."""
    candidate_text = _skill_text(candidate_dir)
    if not candidate_text or not library_skills_dir.exists():
        return DedupResult()

    library_dirs = [
        p
        for p in sorted(library_skills_dir.iterdir())
        if p.is_dir() and (p / "SKILL.md").exists() and p.resolve() != candidate_dir.resolve()
    ]
    if not library_dirs:
        return DedupResult()

    corpus = [candidate_text] + [_skill_text(d) for d in library_dirs]
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
    try:
        matrix = vectorizer.fit_transform(corpus)
    except ValueError:
        return DedupResult()

    sims = cosine_similarity(matrix[0:1], matrix[1:]).flatten()
    matches: list[DuplicateMatch] = []
    for skill_dir, score in zip(library_dirs, sims, strict=True):
        val = round(float(score), 4)
        if val >= threshold:
            matches.append(
                DuplicateMatch(
                    skill_name=skill_dir.name,
                    skill_path=str(skill_dir),
                    similarity=val,
                )
            )
    matches.sort(key=lambda m: m.similarity, reverse=True)
    max_sim = round(float(max(sims)) if len(sims) else 0.0, 4)
    return DedupResult(
        duplicate_flagged=len(matches) > 0,
        max_similarity=max_sim,
        matches=matches,
    )
