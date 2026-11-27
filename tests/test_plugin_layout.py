"""Layout, token-budget, encoding, and privacy tests for the native Antigravity plugin."""

from __future__ import annotations

import json
import re
from pathlib import Path

import defusedxml.ElementTree as ET
import yaml

from forge.config import estimate_tokens
from forge.gate.validator import parse_frontmatter, validate_skill_spec

REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_COMMANDS = {
    "forge-init",
    "forge-setup",
    "forge-audit",
    "forge-bench",
    "forge-unit",
    "forge-qa",
    "forge-gate",
    "forge-retro",
}
EXPECTED_SKILLS = {
    "forge-orchestrator",
    "superarchivist-setup",
    "exploratory-qa-charter",
}
EXPECTED_AGENTS = {
    "research-scout",
    "adversarial-reviewer",
    "qa-explorer",
}
SLIM_MCP_TOOLS = (
    "forge_doctor",
    "forge_workspace",
    "forge_research",
    "forge_eval",
    "forge_evolve",
)
FORBIDDEN_PRIVATE_STRINGS = (
    "c:" + "\\dev",
    "c:/" + "dev",
    "imi" + "se",
    "one" + "drive",
    "sales-" + "setting",
    "close" + ".io",
)


def _owned_plugin_files() -> list[Path]:
    files: list[Path] = [
        REPO_ROOT / "plugin.json",
        REPO_ROOT / "assets" / "logo.svg",
        REPO_ROOT / "rules" / "AGENTS.md",
        Path(__file__).resolve(),
    ]
    files.extend(sorted((REPO_ROOT / "commands").glob("*.md")))
    files.extend(sorted((REPO_ROOT / "skills").glob("*/SKILL.md")))
    files.extend(sorted((REPO_ROOT / "agents").glob("*.md")))
    return files


def _extract_frontmatter_block(raw_text: str) -> str:
    lines = raw_text.splitlines()
    assert len(lines) >= 3 and lines[0].strip() == "---", "Missing YAML frontmatter start"
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return "\n".join(lines[: idx + 1])
    raise AssertionError("Missing YAML frontmatter closing delimiter")


def test_plugin_manifest_and_logo() -> None:
    manifest_path = REPO_ROOT / "plugin.json"
    assert manifest_path.is_file()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert data["name"] == "agent-forge"
    assert re.fullmatch(r"\d+\.\d+\.\d+", data["version"])
    assert data["version"] == "1.0.0"
    assert data["license"] == "MIT"
    assert data["repository"] == "https://github.com/Thatseasy/agent-forge"
    assert data["icon"] == "assets/logo.svg"
    assert data["commands"] == "./commands"
    assert data["skills"] == "./skills"
    assert data["agents"] == "./agents"
    assert data["rules"] == "./rules/AGENTS.md"
    assert data["hooks"] == "./hooks.json"
    assert data["mcpServers"] == "./mcp_config.json"
    assert isinstance(data["capabilities"], list) and len(data["capabilities"]) >= 5
    assert isinstance(data["keywords"], list) and len(data["keywords"]) >= 5

    for key in ("icon", "commands", "skills", "agents", "rules"):
        rel = data[key].removeprefix("./")
        assert (REPO_ROOT / rel).exists(), f"Missing target for plugin.json[{key!r}]: {rel}"

    logo_path = REPO_ROOT / "assets" / "logo.svg"
    assert logo_path.is_file()
    assert logo_path.stat().st_size < 4096
    root = ET.fromstring(logo_path.read_text(encoding="utf-8"))
    assert root.tag.endswith("svg")


def test_plugin_counts_and_contracts() -> None:
    command_files = sorted((REPO_ROOT / "commands").glob("*.md"))
    assert {p.stem for p in command_files} == EXPECTED_COMMANDS
    for cmd_path in command_files:
        text = cmd_path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        assert str(meta.get("description") or "").strip(), f"{cmd_path.name} missing description"
        assert str(meta.get("argument-hint") or "").strip(), (
            f"{cmd_path.name} missing argument-hint"
        )
        assert any(tool in body for tool in SLIM_MCP_TOOLS), (
            f"{cmd_path.name} must reference a Slim-MCP forge_* tool"
        )
        assert "agent-forge" in body, f"{cmd_path.name} missing agent-forge CLI fallback"
        assert "## Output Contract" in body, f"{cmd_path.name} missing Output Contract"

    skill_files = sorted((REPO_ROOT / "skills").glob("*/SKILL.md"))
    assert {p.parent.name for p in skill_files} == EXPECTED_SKILLS
    for skill_md in skill_files:
        text = skill_md.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(text)
        assert meta.get("name") == skill_md.parent.name
        desc = str(meta.get("description") or "")
        assert "Use when" in desc and "Do not use" in desc
        spec_res = validate_skill_spec(skill_md.parent, run_external=False)
        assert spec_res.passed, f"{skill_md} failed Stage 2 spec: {spec_res.errors}"

    agent_files = sorted((REPO_ROOT / "agents").glob("*.md"))
    assert {p.stem for p in agent_files} == EXPECTED_AGENTS
    for agent_path in agent_files:
        text = agent_path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        assert meta.get("name") == agent_path.stem
        assert str(meta.get("description") or "").strip()
        assert "Context Firewall" in body
        assert "Output Contract" in body


def test_rules_and_combined_static_context_budget() -> None:
    rules_path = REPO_ROOT / "rules" / "AGENTS.md"
    rules_text = rules_path.read_text(encoding="utf-8")
    assert not rules_text.startswith("---"), "rules/AGENTS.md must not have YAML frontmatter"
    assert len(rules_text.splitlines()) < 60

    for expected in (
        "forge.assets",
        "AGENT_FORGE_HOME",
        "~/.agent-forge/secrets.env",
        *SLIM_MCP_TOOLS,
        *(f"/{cmd}" for cmd in EXPECTED_COMMANDS),
    ):
        assert expected in rules_text, f"rules/AGENTS.md missing routing token {expected!r}"

    skill_files = sorted((REPO_ROOT / "skills").glob("*/SKILL.md"))
    skill_full_texts = [p.read_text(encoding="utf-8") for p in skill_files]
    skill_frontmatters = [_extract_frontmatter_block(t) for t in skill_full_texts]

    for fm in skill_frontmatters:
        loaded = yaml.safe_load(fm.strip("-\n"))
        assert isinstance(loaded, dict) and loaded.get("name") and loaded.get("description")

    metadata_tokens = estimate_tokens("\n".join([rules_text, *skill_frontmatters]))
    full_tokens = estimate_tokens("\n".join([rules_text, *skill_full_texts]))
    assert metadata_tokens < 1500, f"Static metadata context too large: {metadata_tokens} tokens"
    assert full_tokens < 1500, f"Full rules + 3 core skills too large: {full_tokens} tokens"


def test_plugin_files_encoding_lf_and_privacy() -> None:
    files = _owned_plugin_files()
    assert len(files) == 18  # 4 root/test + 8 commands + 3 skills + 3 agents
    for path in files:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"BOM found in {path}"
        assert b"\r\n" not in raw, f"CRLF found in {path}"
        text = raw.decode("utf-8")
        lowered = text.lower()
        for forbidden in FORBIDDEN_PRIVATE_STRINGS:
            assert forbidden not in lowered, f"Forbidden private string {forbidden!r} in {path}"
