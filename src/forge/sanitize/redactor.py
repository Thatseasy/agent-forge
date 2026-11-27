"""Deterministic local PII and secret redactor for transcripts, CSV exports, JSON, and Markdown."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from forge.config import ForgeConfig, write_json_utf8_lf, write_text_utf8_lf

SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9_-])("
    r"sk-proj-[A-Za-z0-9_-]{16,}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|pplx-[A-Za-z0-9_-]{16,}"
    r"|fc-[A-Za-z0-9_-]{16,}"
    r"|AIza[0-9A-Za-z_-]{20,}"
    r"|gh[pous]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r")"
)
BEARER_RE = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._\-]{16,}")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){10,26}\b")
TAX_ID_RE = re.compile(r"\b(?:DE\d{9}|(?:AT|CH|NL|FR)[A-Z0-9]{8,12})\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
PHONE_RE = re.compile(
    r"(?<!\w)(?:"
    r"(?:\+|00)\d{1,3}[\s./-]?(?:\(?\d{2,5}\)?[\s./-]?){1,4}\d{3,8}"
    r"|0\d{1,4}[\s./-]+(?:\(?\d{2,5}\)?[\s./-]?){0,3}\d{3,8}"
    r"|\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}"
    r")(?!\w)"
)
ISO_DATE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$")
SEMVER_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")
SPEAKER_ROLE_RE = re.compile(
    r"(?m)^(\s*(?:Kunde|Prospect|Lead|Ansprechpartner|Contact|Callee|Client)\s*:\s*)"
    r"([A-ZÄÖÜ][a-zäöüß-]{1,25}(?:\s+[A-ZÄÖÜ][a-zäöüß-]{1,25})?)"
    r"(?=\s*(?:\n|$|[-–(|]))"
)

SAFE_PLACEHOLDER_DOMAINS = frozenset(
    {"example.com", "example.org", "example.net", "agent-forge.local", "localhost"}
)
PII_NAME_KEYS = frozenset(
    {
        "first_name",
        "last_name",
        "full_name",
        "contact_name",
        "lead_name",
        "customer_name",
        "person_name",
        "owner_name",
        "vorname",
        "nachname",
        "ansprechpartner",
    }
)
PII_COMPANY_KEYS = frozenset(
    {"company", "company_name", "organization", "org_name", "account_name", "firma", "unternehmen"}
)
PII_ADDRESS_KEYS = frozenset({"street", "strasse", "address", "adresse", "postal_code", "plz"})
PII_PHONE_KEYS = frozenset({"phone", "phone_number", "mobile", "telefon", "handy", "tel"})
PII_EMAIL_KEYS = frozenset({"email", "mail", "email_address", "e-mail"})

TEXT_EXTENSIONS = frozenset({".md", ".txt", ".markdown", ".log"})
STRUCTURED_EXTENSIONS = frozenset({".json", ".jsonl", ".csv", ".yaml", ".yml"})


class SanitizeResult(BaseModel):
    """Per-file sanitization outcome without storing raw sensitive text."""

    relative_path: str
    modified: bool
    counts: dict[str, int] = Field(default_factory=dict)


class SanitizeReport(BaseModel):
    """Summary report written as `SANITIZE_REPORT.json` into output root."""

    input_path: str
    output_path: str
    dry_run: bool = False
    total_files: int = 0
    modified_files: int = 0
    counts_by_category: dict[str, int] = Field(default_factory=dict)
    clean: bool = True
    warnings: list[str] = Field(default_factory=list)
    files: list[SanitizeResult] = Field(default_factory=list)


class PiiSanitizer:
    """Consistent entity-preserving local PII & secret redactor."""

    def __init__(
        self,
        *,
        extra_names: list[str] | None = None,
        extra_companies: list[str] | None = None,
        extra_domains: list[str] | None = None,
        allowlist_domains: list[str] | None = None,
    ) -> None:
        self.extra_names = sorted(
            {n.strip() for n in (extra_names or []) if n.strip()}, key=len, reverse=True
        )
        self.extra_companies = sorted(
            {c.strip() for c in (extra_companies or []) if c.strip()}, key=len, reverse=True
        )
        self.extra_domains = sorted(
            {d.strip().lower() for d in (extra_domains or []) if d.strip()}, key=len, reverse=True
        )
        self.allowlist_domains = SAFE_PLACEHOLDER_DOMAINS | {
            d.strip().lower() for d in (allowlist_domains or []) if d.strip()
        }
        self._aliases: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}

    def _alias(self, category: str, raw_value: str) -> str:
        norm = " ".join(raw_value.strip().split())
        key = (category, norm.lower())
        existing = self._aliases.get(key)
        if existing is not None:
            return existing
        idx = self._counters.get(category, 0) + 1
        self._counters[category] = idx
        token = f"[{category.upper()}_{idx:02d}]"
        self._aliases[key] = token
        return token

    def sanitize_text(self, text: str) -> tuple[str, dict[str, int]]:
        """Scrub unstructured text while keeping coreference placeholders deterministic."""
        counts: dict[str, int] = {}

        def _bump(cat: str, n: int = 1) -> None:
            counts[cat] = counts.get(cat, 0) + n

        def _sub_secret(match: re.Match[str]) -> str:
            _bump("secret")
            return "[REDACTED_SECRET]"

        out = SECRET_RE.sub(_sub_secret, text)

        def _sub_bearer(match: re.Match[str]) -> str:
            _bump("secret")
            return f"{match.group(1)}[REDACTED_SECRET]"

        out = BEARER_RE.sub(_sub_bearer, out)

        def _sub_iban(match: re.Match[str]) -> str:
            _bump("iban")
            return self._alias("iban", match.group(0))

        out = IBAN_RE.sub(_sub_iban, out)

        def _sub_tax(match: re.Match[str]) -> str:
            _bump("tax_id")
            return self._alias("tax_id", match.group(0))

        out = TAX_ID_RE.sub(_sub_tax, out)

        discovered_domains: set[str] = set(self.extra_domains)

        def _sub_email(match: re.Match[str]) -> str:
            full = match.group(0)
            domain = match.group(1).lower()
            if domain in self.allowlist_domains:
                return full
            discovered_domains.add(domain)
            _bump("email")
            return self._alias("email", full)

        out = EMAIL_RE.sub(_sub_email, out)

        for dom in sorted(discovered_domains, key=len, reverse=True):
            if dom in self.allowlist_domains:
                continue
            dom_re = re.compile(rf"(?i)(?<![A-Za-z0-9.-]){re.escape(dom)}(?![A-Za-z0-9.-])")
            out, n_dom = dom_re.subn(lambda m: self._alias("domain", m.group(0)), out)
            if n_dom:
                _bump("domain", n_dom)

        def _sub_phone(match: re.Match[str]) -> str:
            candidate = match.group(0).strip()
            if ISO_DATE_RE.match(candidate) or SEMVER_RE.match(candidate):
                return match.group(0)
            digits = re.sub(r"\D", "", candidate)
            if len(digits) < 7 or len(digits) > 15:
                return match.group(0)
            # Avoid matching pure decimal / float numbers separated only by a single dot
            if (
                "." in candidate
                and not candidate.startswith(("+", "0"))
                and candidate.count(".") == 1
            ):
                return match.group(0)
            _bump("phone")
            return self._alias("phone", candidate)

        out = PHONE_RE.sub(_sub_phone, out)

        def _sub_speaker(match: re.Match[str]) -> str:
            prefix, speaker_name = match.group(1), match.group(2)
            _bump("person")
            return f"{prefix}{self._alias('person', speaker_name)}"

        out = SPEAKER_ROLE_RE.sub(_sub_speaker, out)

        for name in self.extra_names:
            name_re = re.compile(rf"(?i)(?<!\w){re.escape(name)}(?!\w)")
            out, n_hits = name_re.subn(lambda m: self._alias("person", m.group(0)), out)
            if n_hits:
                _bump("person", n_hits)

        for comp in self.extra_companies:
            comp_re = re.compile(rf"(?i)(?<!\w){re.escape(comp)}(?!\w)")
            out, n_hits = comp_re.subn(lambda m: self._alias("company", m.group(0)), out)
            if n_hits:
                _bump("company", n_hits)

        return out, counts

    def sanitize_structured_value(self, key: str, value: Any, counts: dict[str, int]) -> Any:
        """Sanitize JSON/YAML/CSV nodes using field semantics plus regex scrubbers."""
        norm_key = key.strip().lower().replace("-", "_")
        if isinstance(value, dict):
            return {
                str(k): self.sanitize_structured_value(str(k), v, counts) for k, v in value.items()
            }
        if isinstance(value, list):
            return [self.sanitize_structured_value(key, item, counts) for item in value]
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return value

        if norm_key in PII_NAME_KEYS and not text.startswith("[PERSON_"):
            counts["person"] = counts.get("person", 0) + 1
            return self._alias("person", text)
        if norm_key in PII_COMPANY_KEYS and not text.startswith("[COMPANY_"):
            counts["company"] = counts.get("company", 0) + 1
            return self._alias("company", text)
        if norm_key in PII_ADDRESS_KEYS and not text.startswith("[ADDRESS_"):
            counts["address"] = counts.get("address", 0) + 1
            return self._alias("address", text)
        if norm_key in PII_PHONE_KEYS and not text.startswith("[PHONE_"):
            counts["phone"] = counts.get("phone", 0) + 1
            return self._alias("phone", text)
        if norm_key in PII_EMAIL_KEYS and not text.startswith("[EMAIL_"):
            cleaned, sub_counts = self.sanitize_text(text)
            if cleaned == text and "@" in text:
                domain = text.split("@", 1)[1].lower()
                if domain not in self.allowlist_domains:
                    counts["email"] = counts.get("email", 0) + 1
                    return self._alias("email", text)
            for k, v in sub_counts.items():
                counts[k] = counts.get(k, 0) + v
            return cleaned

        cleaned, sub_counts = self.sanitize_text(value)
        for k, v in sub_counts.items():
            counts[k] = counts.get(k, 0) + v
        return cleaned

    def sanitize_file_content(self, path: Path, raw_text: str) -> tuple[str, dict[str, int]]:
        """Dispatch file sanitization cleanly by extension (text vs CSV/JSON/JSONL/YAML)."""
        ext = path.suffix.lower()
        counts: dict[str, int] = {}

        if ext == ".csv":
            reader = csv.DictReader(io.StringIO(raw_text))
            fieldnames = list(reader.fieldnames or [])
            if not fieldnames:
                return self.sanitize_text(raw_text)
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            for row in reader:
                sanitized_row = {
                    col: self.sanitize_structured_value(col, val or "", counts)
                    for col, val in row.items()
                    if col is not None
                }
                writer.writerow(sanitized_row)
            return buf.getvalue(), counts

        if ext == ".json":
            try:
                loaded = json.loads(raw_text)
                cleaned = self.sanitize_structured_value("", loaded, counts)
                return json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n", counts
            except json.JSONDecodeError:
                return self.sanitize_text(raw_text)

        if ext == ".jsonl":
            out_lines: list[str] = []
            for line in raw_text.splitlines():
                if not line.strip():
                    continue
                try:
                    loaded = json.loads(line)
                    cleaned = self.sanitize_structured_value("", loaded, counts)
                    out_lines.append(json.dumps(cleaned, ensure_ascii=False))
                except json.JSONDecodeError:
                    txt, sub = self.sanitize_text(line)
                    for k, v in sub.items():
                        counts[k] = counts.get(k, 0) + v
                    out_lines.append(txt)
            return ("\n".join(out_lines) + "\n") if out_lines else "", counts

        if ext in {".yaml", ".yml"}:
            try:
                loaded = yaml.safe_load(raw_text)
                if loaded is not None:
                    cleaned = self.sanitize_structured_value("", loaded, counts)
                    return (
                        yaml.safe_dump(cleaned, sort_keys=False, allow_unicode=True),
                        counts,
                    )
            except yaml.YAMLError:
                pass
            return self.sanitize_text(raw_text)

        return self.sanitize_text(raw_text)


def sanitize_path(
    input_path: Path,
    out_path: Path,
    *,
    names_file: Path | None = None,
    extra_names: list[str] | None = None,
    extra_companies: list[str] | None = None,
    allowlist_domains: list[str] | None = None,
    dry_run: bool = False,
) -> SanitizeReport:
    """Sanitize a file or directory recursively and emit `SANITIZE_REPORT.json`."""
    src = input_path.resolve()
    if not src.exists():
        raise FileNotFoundError(f"Sanitize input path does not exist: {input_path}")

    names = list(extra_names or [])
    if names_file is not None and names_file.is_file():
        names.extend(
            line.strip()
            for line in names_file.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )

    sanitizer = PiiSanitizer(
        extra_names=names,
        extra_companies=extra_companies,
        allowlist_domains=allowlist_domains,
    )

    if src.is_file():
        target_files = [(src, src.name)]
        dest_root = out_path.parent if out_path.suffix else out_path
    else:
        supported = TEXT_EXTENSIONS | STRUCTURED_EXTENSIONS
        target_files = [
            (p, p.relative_to(src).as_posix())
            for p in sorted(src.rglob("*"))
            if p.is_file() and p.name != "SANITIZE_REPORT.json" and p.suffix.lower() in supported
        ]
        dest_root = out_path

    agg_counts: dict[str, int] = {}
    file_results: list[SanitizeResult] = []
    modified_files = 0

    for file_path, rel_str in target_files:
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        cleaned, file_counts = sanitizer.sanitize_file_content(file_path, raw)
        changed = cleaned != raw or sum(file_counts.values()) > 0
        if changed:
            modified_files += 1
        for k, v in file_counts.items():
            agg_counts[k] = agg_counts.get(k, 0) + v
        file_results.append(
            SanitizeResult(relative_path=rel_str, modified=changed, counts=file_counts)
        )

        if not dry_run:
            dest_file = (
                out_path.resolve()
                if src.is_file() and out_path.suffix
                else dest_root.resolve() / rel_str
            )
            write_text_utf8_lf(dest_file, cleaned)

    report = SanitizeReport(
        input_path=str(input_path),
        output_path=str(out_path),
        dry_run=dry_run,
        total_files=len(target_files),
        modified_files=modified_files,
        counts_by_category=agg_counts,
        clean=True,
        files=file_results,
    )
    if not dry_run:
        report_dir = out_path.parent if src.is_file() and out_path.suffix else dest_root
        write_json_utf8_lf(report_dir.resolve() / "SANITIZE_REPORT.json", report.model_dump())
    return report


def assert_corpus_sanitized(corpus_path: Path, config: ForgeConfig | None = None) -> list[str]:
    """Verify corpus directory/file has zero detectable residual secrets/emails/IBANs/phones."""
    del config
    target = corpus_path.resolve()
    if not target.exists():
        raise FileNotFoundError(f"Corpus path not found: {corpus_path}")

    files = (
        [target]
        if target.is_file()
        else [
            p
            for p in sorted(target.rglob("*"))
            if p.is_file()
            and p.name != "SANITIZE_REPORT.json"
            and p.suffix.lower() in (TEXT_EXTENSIONS | STRUCTURED_EXTENSIONS)
        ]
    )
    residuals: list[str] = []
    checker = PiiSanitizer()
    for p in files:
        raw = p.read_text(encoding="utf-8", errors="replace")
        _, found = checker.sanitize_file_content(p, raw)
        # Disallow any unredacted secrets, IBANs, tax IDs, real emails, or phone numbers
        blocking_cats = {"secret", "email", "phone", "iban", "tax_id"}
        blocking = {k: v for k, v in found.items() if k in blocking_cats}
        if blocking:
            rel = p.name if target.is_file() else p.relative_to(target).as_posix()
            residuals.append(f"{rel}: {blocking}")
    if residuals:
        raise ValueError(
            "Unsanitized PII/secrets detected in corpus. Run `forge.py sanitize` first: "
            + "; ".join(residuals[:5])
        )
    return [f.name if target.is_file() else f.relative_to(target).as_posix() for f in files]
