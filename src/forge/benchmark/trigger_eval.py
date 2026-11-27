"""Phase A simultaneous Trigger-Eval measuring Recall and Precision across skills."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from forge.benchmark.models import EvalSuite, TriggerMetrics
from forge.benchmark.skill_pack import resolve_skill_dirs, skill_pack_name
from forge.gate.validator import parse_frontmatter


def _extract_skill_profile(skill_dir: Path) -> tuple[str, str, str]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return skill_dir.name, "", ""
    raw = skill_md.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_frontmatter(raw)
    name = str(meta.get("name") or skill_dir.name)
    desc = str(meta.get("description") or "")
    return name, desc, body


def _parse_do_not_use(description: str) -> list[str]:
    match = re.search(r"do not use(?:\s+for)?\s+([^.]+)", description, re.I)
    if not match:
        return []
    return [tok.strip().lower() for tok in re.split(r"[,;/]|\bor\b", match.group(1)) if tok.strip()]


def _should_trigger(prompt: str, name: str, description: str, body: str) -> bool:
    for neg in _parse_do_not_use(description):
        if neg and len(neg) >= 3 and neg in prompt.lower():
            return False

    skill_text = f"{name} {description} {body[:400]}"
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
    try:
        mat = vectorizer.fit_transform([prompt, skill_text])
        sim = float(cosine_similarity(mat[0:1], mat[1:2])[0][0])
    except ValueError:
        sim = 0.0

    prompt_words = set(re.findall(r"[a-z0-9]{3,}", prompt.lower()))
    desc_words = set(re.findall(r"[a-z0-9]{3,}", f"{name} {description}".lower()))
    overlap = len(prompt_words & desc_words)
    return sim >= 0.08 or overlap >= 2


def evaluate_triggers(
    suite: EvalSuite,
    skill_dirs: list[Path],
    metrics_jsonl: Path | None = None,
) -> list[TriggerMetrics]:
    """Evaluate all provided skills simultaneously against positive & near-miss prompts."""
    logged_skills: set[str] = set()
    if metrics_jsonl and metrics_jsonl.exists():
        for line in metrics_jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec.get("skill_loaded"):
                    logged_skills.add(Path(str(rec["skill_loaded"])).parent.name.lower())
            except json.JSONDecodeError:
                continue

    results: list[TriggerMetrics] = []
    for sdir in skill_dirs:
        modules = resolve_skill_dirs(sdir) or [sdir]
        profiles = [_extract_skill_profile(mod) for mod in modules]
        pack_name = (
            profiles[0][0]
            if len(modules) == 1 and (modules[0] / "SKILL.md").is_file()
            else skill_pack_name(sdir)
        )
        tp = fp = fn = tn = 0
        for task in suite.tasks:
            triggered = pack_name.lower() in logged_skills or any(
                _should_trigger(task.prompt, m_name, m_desc, m_body)
                or m_name.lower() in logged_skills
                for m_name, m_desc, m_body in profiles
            )
            if triggered:
                tp += 1
            else:
                fn += 1
        for neg_prompt in suite.near_miss_prompts:
            if any(
                _should_trigger(neg_prompt, m_name, m_desc, m_body)
                for m_name, m_desc, m_body in profiles
            ):
                fp += 1
            else:
                tn += 1

        recall = round(tp / max(1, tp + fn), 4)
        precision = round(tp / max(1, tp + fp), 4)
        f1 = round((2 * precision * recall) / max(1e-6, precision + recall), 4)
        results.append(
            TriggerMetrics(
                skill_name=pack_name,
                true_positives=tp,
                false_positives=fp,
                false_negatives=fn,
                true_negatives=tn,
                trigger_recall=recall,
                trigger_precision=precision,
                f1=f1,
            )
        )
    return results
