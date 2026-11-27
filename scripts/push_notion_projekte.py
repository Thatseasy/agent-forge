"""Push Superarchivist & Intelligence-Sourcer docs + vertical-slice backlog tickets to Notion."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import httpx

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from forge.config import ForgeConfig  # noqa: E402

NOTION_VERSION = "2022-06-28"

TICKETS: list[dict[str, Any]] = [
    {
        "title": "[Backlog] Projekt-Initialisierung & Basis-Harness finalisieren",
        "type": "HITL",
        "status": "In progress",
        "priority": "High",
        "blocked_by": "None",
        "summary": (
            "Git-Repo pushen, Notion-Integration verbinden und ersten Live-Benchmark "
            "mit geladenen API-Credits abschließen."
        ),
    },
    {
        "title": "[Backlog] Automatischen Archivist & Projekt-Doku-Auditor aktivieren",
        "type": "AFK",
        "status": "Not started",
        "priority": "High",
        "blocked_by": "Ticket 1",
        "summary": (
            "Bestehende Projekte auf Doku-Lücken, Drift und erfolgreiche Prompt-/Skill-Patterns "
            "scannen und Best Practices extrahieren."
        ),
    },
    {
        "title": "[Backlog] Brutaler Skill-Vergleich: Projekt-Patterns vs. Top-Web-Skills",
        "type": "AFK",
        "status": "Not started",
        "priority": "Medium",
        "blocked_by": "Ticket 2",
        "summary": (
            "Eigene projekt-extrahierte Skills in Stage 3 (AblationArena) gegen Top-Web-Skills "
            "anhand pass^3, Cache-Hit und ValueScore benchmarken."
        ),
    },
    {
        "title": "[Backlog] Intelligence-Sourcer: Top 3-5 Webquellen per MCP/CLI anbinden",
        "type": "HITL",
        "status": "Not started",
        "priority": "High",
        "blocked_by": "Ticket 1",
        "summary": (
            "Pro Tech-Stack die 3-5 hochwertigsten Primärquellen finden und token-minimal via "
            "MCP, mcp2cli oder Context7 verdrahten."
        ),
    },
    {
        "title": "[Backlog] Superarchivist + Intelligence-Sourcer als Boilerplate & `setup`-Befehl",
        "type": "AFK",
        "status": "Not started",
        "priority": "High",
        "blocked_by": "Ticket 2, Ticket 3, Ticket 4",
        "summary": (
            "Eigenständige Boilerplate + gemeinsamer `setup`-Befehl, der gewachsene Chat-Projekte "
            "(z. B. SaaS-Prototypen) mit Docs, Hooks, Plugins und Top-Quellen absichert."
        ),
    },
]


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _find_parent_page(client: httpx.Client, api_key: str, query: str) -> str | None:
    resp = client.post(
        "https://api.notion.com/v1/search",
        headers=_headers(api_key),
        json={"query": query, "filter": {"value": "page", "property": "object"}, "page_size": 20},
    )
    resp.raise_for_status()
    for obj in resp.json().get("results", []):
        props = obj.get("properties") or {}
        for val in props.values():
            if val.get("type") == "title":
                title = "".join(t.get("plain_text", "") for t in val.get("title", []))
                if title.strip().lower() == query.strip().lower():
                    return str(obj["id"])
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create Notion subpage under 'Projekte' with Superarchivist docs & tickets."
    )
    parser.add_argument("--parent", default="Projekte", help="Notion parent page title.")
    parser.add_argument("--dry-run", action="store_true", help="Print payload summary only.")
    args = parser.parse_args()

    cfg = ForgeConfig.load()
    if args.dry_run:
        print(f"[dry-run] Would create subpage under '{args.parent}' with {len(TICKETS)} tickets.")
        return 0

    if not cfg.notion_api_key:
        print("ERROR: NOTION_API_KEY is missing in .env", file=sys.stderr)
        return 1

    with httpx.Client(timeout=20.0) as client:
        parent_id = _find_parent_page(client, cfg.notion_api_key, args.parent)
        if not parent_id:
            print(
                f"Notion page '{args.parent}' is not visible to NOTION_API_KEY yet.\n"
                f"Open '{args.parent}' in Notion -> '...' -> Connections -> add your integration,\n"
                "then re-run: uv run python scripts/push_notion_projekte.py",
                file=sys.stderr,
            )
            return 2

        page_resp = client.post(
            "https://api.notion.com/v1/pages",
            headers=_headers(cfg.notion_api_key),
            json={
                "parent": {"page_id": parent_id},
                "properties": {
                    "title": [{"text": {"content": "Agent-Forge & Superarchivist-System"}}]
                },
                "children": [
                    {
                        "object": "block",
                        "type": "heading_2",
                        "heading_2": {
                            "rich_text": [{"text": {"content": "Architektur & Vision (Backlog)"}}]
                        },
                    },
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {
                                    "text": {
                                        "content": (
                                            "Eigenständige Boilerplate + `setup`-Befehl "
                                            "aus Superarchivist (Doku-Audit, Harness, "
                                            "Skill-Benchmark) und Intelligence-Sourcer "
                                            "(Top 3-5 Webquellen per MCP/CLI)."
                                        )
                                    }
                                }
                            ]
                        },
                    },
                ],
            },
        )
        page_resp.raise_for_status()
        subpage_id = page_resp.json()["id"]

        db_resp = client.post(
            "https://api.notion.com/v1/databases",
            headers=_headers(cfg.notion_api_key),
            json={
                "parent": {"type": "page_id", "page_id": subpage_id},
                "title": [{"type": "text", "text": {"content": "Backlog Tickets"}}],
                "properties": {
                    "Name": {"title": {}},
                    "Type": {"select": {"options": [{"name": "HITL"}, {"name": "AFK"}]}},
                    "Status": {
                        "select": {
                            "options": [
                                {"name": "Not started"},
                                {"name": "In progress"},
                                {"name": "Done"},
                            ]
                        }
                    },
                    "Priority": {
                        "select": {
                            "options": [{"name": "High"}, {"name": "Medium"}, {"name": "Low"}]
                        }
                    },
                    "Blocked by": {"rich_text": {}},
                },
            },
        )
        db_resp.raise_for_status()
        db_id = db_resp.json()["id"]

        for t in TICKETS:
            t_resp = client.post(
                "https://api.notion.com/v1/pages",
                headers=_headers(cfg.notion_api_key),
                json={
                    "parent": {"database_id": db_id},
                    "properties": {
                        "Name": {"title": [{"text": {"content": t["title"]}}]},
                        "Type": {"select": {"name": t["type"]}},
                        "Status": {"select": {"name": t["status"]}},
                        "Priority": {"select": {"name": t["priority"]}},
                        "Blocked by": {"rich_text": [{"text": {"content": t["blocked_by"]}}]},
                    },
                    "children": [
                        {
                            "object": "block",
                            "type": "paragraph",
                            "paragraph": {"rich_text": [{"text": {"content": t["summary"]}}]},
                        }
                    ],
                },
            )
            t_resp.raise_for_status()

        print(f"Created Notion subpage {subpage_id} and ticket database {db_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
