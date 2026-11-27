"""Stage 2 deterministic 0-USD Security, Spec, Normalization, and Dedup Gate."""

from forge.gate.dedup import DedupResult, check_semantic_duplicates
from forge.gate.normalizer import NormalizationResult, normalize_skill_dir
from forge.gate.pipeline import GatePipeline, GateReport, GateStatus
from forge.gate.security import SecurityFinding, SecurityScanResult, scan_skill_security
from forge.gate.validator import SpecValidationResult, parse_frontmatter, validate_skill_spec

__all__ = [
    "DedupResult",
    "GatePipeline",
    "GateReport",
    "GateStatus",
    "NormalizationResult",
    "SecurityFinding",
    "SecurityScanResult",
    "SpecValidationResult",
    "check_semantic_duplicates",
    "normalize_skill_dir",
    "parse_frontmatter",
    "scan_skill_security",
    "validate_skill_spec",
]
