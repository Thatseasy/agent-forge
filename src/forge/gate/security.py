"""Stage 2 static security scanner for Instruction-File Poisoning (AST01/ASI01)."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

HIDDEN_UNICODE_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u202a-\u202e\u2066-\u2069]")
PROMPT_INJECTION_RE = re.compile(
    r"(?i)(ignore\s+(all\s+)?previous\s+instructions|"
    r"disregard\s+(all\s+)?prior\s+(rules|instructions)|"
    r"override\s+system\s+prompt|"
    r"you\s+are\s+now\s+in\s+developer\s+mode)"
)
HTML_CMD_COMMENT_RE = re.compile(
    r"<!--[\s\S]*?(curl|wget|invoke-webrequest|powershell|bash\s+-c|rm\s+-rf|eval\()[\s\S]*?-->",
    re.I,
)
SECRET_ACCESS_RE = re.compile(
    r"(?i)(~/\.ssh|~/\.gemini|~/\.aws|/etc/passwd|(?<!\.)\.env\b(?!\.example))"
)
EXFIL_CMD_RE = re.compile(
    r"(?i)(curl\s+[^#\n]*(http|https)|wget\s+[^#\n]*(http|https)|"
    r"invoke-webrequest|iwr\s+[^#\n]*(http|https)|"
    r"base64\s+-d|-encodedcommand|base64\.b64decode)"
)


class SecurityFinding(BaseModel):
    """Single security issue found during Stage 2 static scan."""

    rule_id: str
    severity: str
    file: str
    detail: str


class SecurityScanResult(BaseModel):
    """Aggregated Stage 2 security evaluation."""

    safe: bool
    findings: list[SecurityFinding] = Field(default_factory=list)
    external_scanners: dict[str, str] = Field(default_factory=dict)


def _scan_python_ast(path: Path, rel_name: str) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return findings

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn_name = _call_name(node.func)
            if fn_name in {"eval", "exec", "os.system", "base64.b64decode"}:
                findings.append(
                    SecurityFinding(
                        rule_id="AST-DANGEROUS-CALL",
                        severity="high",
                        file=rel_name,
                        detail=f"Disallowed dynamic/obfuscated call `{fn_name}`",
                    )
                )
            if fn_name in {"subprocess.run", "subprocess.Popen", "subprocess.call"}:
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value:
                        findings.append(
                            SecurityFinding(
                                rule_id="AST-SHELL-TRUE",
                                severity="high",
                                file=rel_name,
                                detail=f"`{fn_name}(..., shell=True)` detected",
                            )
                        )
    return findings


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return ""


def _scan_text_file(path: Path, rel_name: str) -> list[SecurityFinding]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    findings: list[SecurityFinding] = []
    # Strip leading UTF-8 BOM only if handled elsewhere; zero-width inside body is flagged
    body_for_zw = raw[1:] if raw.startswith("\ufeff") else raw
    if HIDDEN_UNICODE_RE.search(body_for_zw):
        findings.append(
            SecurityFinding(
                rule_id="ASI01-HIDDEN-UNICODE",
                severity="critical",
                file=rel_name,
                detail="Zero-width or BiDi override Unicode character detected",
            )
        )
    if PROMPT_INJECTION_RE.search(raw):
        findings.append(
            SecurityFinding(
                rule_id="ASI01-PROMPT-INJECTION",
                severity="critical",
                file=rel_name,
                detail="Prompt-injection override phrase detected",
            )
        )
    if HTML_CMD_COMMENT_RE.search(raw):
        findings.append(
            SecurityFinding(
                rule_id="ASI01-HIDDEN-HTML-CMD",
                severity="critical",
                file=rel_name,
                detail="Shell/network payload hidden inside HTML comment",
            )
        )
    if SECRET_ACCESS_RE.search(raw) and (
        EXFIL_CMD_RE.search(raw) or "~/.ssh" in raw or "~/.gemini" in raw
    ):
        findings.append(
            SecurityFinding(
                rule_id="ASI01-SECRET-EXFIL",
                severity="critical",
                file=rel_name,
                detail=(
                    "Unannounced secret/credential path combined with network/obfuscation pattern"
                ),
            )
        )
    elif EXFIL_CMD_RE.search(raw):
        findings.append(
            SecurityFinding(
                rule_id="ASI01-SUSPICIOUS-EXFIL",
                severity="high",
                file=rel_name,
                detail="Potential obfuscated command or outbound curl/Invoke-WebRequest pattern",
            )
        )
    return findings


def _run_external_scanner(cmd: str, args: list[str], cwd: Path) -> str | None:
    exe = shutil.which(cmd)
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return f"exit={proc.returncode}: {(proc.stdout or proc.stderr or '').strip()[:250]}"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"error: {exc}"


def scan_skill_security(skill_dir: Path, run_external: bool = True) -> SecurityScanResult:
    """Scan all markdown and script files in `skill_dir` for poisoning & exfiltration."""
    findings: list[SecurityFinding] = []
    scan_exts = {".md", ".py", ".sh", ".ps1", ".js", ".ts", ".yaml", ".yml", ".json"}

    for file_path in sorted(skill_dir.rglob("*")):
        if not file_path.is_file() or file_path.suffix.lower() not in scan_exts:
            continue
        if file_path.name in {"GATE_REPORT.json", "DISCOVERY_META.json", "BENCHMARK.json"}:
            continue
        rel = str(file_path.relative_to(skill_dir)).replace("\\", "/")
        findings.extend(_scan_text_file(file_path, rel))
        if file_path.suffix.lower() == ".py":
            findings.extend(_scan_python_ast(file_path, rel))

    external: dict[str, str] = {}
    if run_external:
        for cmd, args in (
            ("skill-scanner", ["scan", str(skill_dir)]),
            ("husk", ["scan", str(skill_dir)]),
        ):
            res = _run_external_scanner(cmd, args, skill_dir)
            if res is not None:
                external[cmd] = res
                if res.startswith("exit=") and not res.startswith("exit=0:"):
                    findings.append(
                        SecurityFinding(
                            rule_id=f"EXT-{cmd.upper()}",
                            severity="high",
                            file=".",
                            detail=res,
                        )
                    )

    blocking = any(f.severity in {"critical", "high"} for f in findings)
    return SecurityScanResult(safe=not blocking, findings=findings, external_scanners=external)
