"""End-to-end stdio JSON-RPC tests for the Lean Slim-MCP server."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from forge.config import estimate_tokens

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TOOLS = {
    "forge_doctor",
    "forge_workspace",
    "forge_research",
    "forge_eval",
    "forge_evolve",
}
EXPECTED_KEYS = {"status", "summary_one_line", "metrics", "artifact_paths"}


def _extract_json(result: Any) -> dict[str, Any]:
    assert result.content, "MCP tool returned empty content"
    text = result.content[0].text
    parsed = json.loads(text)
    assert isinstance(parsed, dict)
    assert set(parsed.keys()) == EXPECTED_KEYS
    return parsed


def test_mcp_config_json_valid_utf8_lf() -> None:
    cfg_path = REPO_ROOT / "mcp_config.json"
    raw = cfg_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw

    data = json.loads(raw.decode("utf-8"))
    srv = data["mcpServers"]["agent-forge"]
    assert srv["command"] == "uvx"
    assert srv["args"] == [
        "--from",
        "git+https://github.com/Thatseasy/agent-forge",
        "agent-forge-mcp",
    ]
    assert srv["env"] == {"PYTHONUTF8": "1"}


def test_stdio_mcp_server_external_workspace_isolation(tmp_path: Path) -> None:
    forge_home = tmp_path / "forge_home"
    external_ws = tmp_path / "external_empty_ws"
    external_ws.mkdir(parents=True, exist_ok=True)

    dummy_secret = "pplx-test-secret-never-emit-1234567890"
    env = {
        **os.environ,
        "AGENT_FORGE_HOME": str(forge_home),
        "PERPLEXITY_API_KEY": dummy_secret,
        "PYTHONUTF8": "1",
    }
    env.pop("FORGE_ROOT", None)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "forge.mcp_server"],
        env=env,
        cwd=external_ws,
    )

    async def _exercise() -> dict[str, Any]:
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                init_res = await session.initialize()
                assert init_res.server_info.name == "agent-forge"

                tools_res = await session.list_tools()
                tools_payload = [
                    t.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for t in tools_res.tools
                ]
                token_count = estimate_tokens(json.dumps(tools_payload))
                assert len(tools_res.tools) == 5
                assert {t.name for t in tools_res.tools} == EXPECTED_TOOLS
                assert token_count <= 1200

                doc = _extract_json(await session.call_tool("forge_doctor", {}))
                assert doc["status"] == "ok"
                assert Path(doc["metrics"]["agent_forge_home"]).resolve() == forge_home.resolve()
                assert doc["metrics"]["package_assets_ok"] is True
                assert doc["metrics"]["api_keys_configured"]["perplexity"] is True
                assert all(
                    isinstance(v, bool) for v in doc["metrics"]["api_keys_configured"].values()
                )
                assert dummy_secret not in json.dumps(doc)
                doc_art = Path(doc["artifact_paths"][0])
                assert doc_art.is_file()
                assert dummy_secret not in doc_art.read_text(encoding="utf-8")

                audit = _extract_json(
                    await session.call_tool(
                        "forge_workspace",
                        {"action": "audit", "options_json": json.dumps({"dry_run": True})},
                    )
                )
                assert audit["status"] == "ok"
                assert audit["metrics"]["projects_scanned"] == 1
                assert all(Path(p).is_file() for p in audit["artifact_paths"])

                setup_dry = _extract_json(
                    await session.call_tool(
                        "forge_workspace",
                        {"action": "setup", "options_json": json.dumps({"dry_run": True})},
                    )
                )
                assert setup_dry["status"] == "ok"
                assert setup_dry["metrics"]["dry_run"] is True
                assert not list(external_ws.iterdir())

                oracle = _extract_json(
                    await session.call_tool(
                        "forge_research",
                        {"mode": "cost_oracle", "query": "fast"},
                    )
                )
                assert oracle["status"] == "ok"
                assert oracle["metrics"]["count"] >= 1
                assert Path(oracle["artifact_paths"][0]).is_file()

                discover = _extract_json(
                    await session.call_tool(
                        "forge_research",
                        {"mode": "discover", "query": "dry_run"},
                    )
                )
                assert discover["status"] == "ok"
                assert discover["metrics"]["count"] >= 1
                assert (forge_home / "staging" / "inbox" / "radar-sample-skill").is_dir()

                unit_list = _extract_json(
                    await session.call_tool(
                        "forge_eval",
                        {"action": "unit", "spec_json": json.dumps({"op": "list"})},
                    )
                )
                assert unit_list["status"] == "ok"
                assert "example-python-cli" in unit_list["metrics"]["units"]

                retro = _extract_json(
                    await session.call_tool(
                        "forge_evolve",
                        {"action": "retro", "options_json": json.dumps({"dry_run": True})},
                    )
                )
                assert retro["status"] == "ok"
                assert retro["metrics"]["nudge_count"] >= 1

                bad_opts = _extract_json(
                    await session.call_tool(
                        "forge_workspace",
                        {"action": "gate", "options_json": "[1, 2, 3]"},
                    )
                )
                assert bad_opts["status"] == "error"

                return {"token_count": token_count}

    out = anyio.run(_exercise)
    assert out["token_count"] <= 1200
    assert (forge_home / "runs").is_dir()
