---
name: forge-research
description: Run token-lean external web research and library doc lookups via local forge CLI wrappers. Use when gathering docs, scraping URLs, or researching external tools. Do not use consumer browser automation on Perplexity.
---

# Forge Research (Subagent-Only)

Isoliere jede Web-Recherche vom Hauptkontext über `uv run python forge.py research`.

## Schritte

1. Wähle den schlanksten Provider:
   - Fakten & Web-Synthese: `--provider sonar --mode low`
   - Konkrete URL/Doku als sauberes Markdown: `--provider firecrawl --mode scrape`
   - Versionsgenaue Library-Doku: `--provider context7`
   - Tiefe technische Suche (Fallback): `--provider exa --mode auto`
2. Führe aus:
   `uv run python forge.py research "<query>" --provider sonar --max-tokens 1500 --out staging/research_note.md`
3. Liefere ausschließlich eine verdichtete Synthese (< 800 Tokens) samt Quellenliste zurück.
