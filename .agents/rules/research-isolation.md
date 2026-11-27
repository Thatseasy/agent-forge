---
trigger: model_decision
description: Use whenever external web research, library documentation lookup, or scraping is required so research stays isolated from the main context.
---

# Research-Isolation

1. Führe Web-Suchen und Doku-Abrufe über `uv run python forge.py research "<query>" --provider sonar|firecrawl|context7|exa --max-tokens 1500` oder über den `research`-Subagenten (`.agents/skills/forge-research/SKILL.md`) aus.
2. Verwende keine Browser-Automation für Perplexity-Web-UIs.
3. Liefere in den Hauptkontext nur eine verdichtete Synthese (< 800 Tokens) mit Quellenliste zurück.
