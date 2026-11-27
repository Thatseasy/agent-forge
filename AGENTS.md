# agent-forge — Routing-Karte

Lokale Skill-Automatisierungs-Pipeline unter Windows 11. Details liegen in den Zielmodulen und Skills.

## Orientierung

- CLI-Einstiegspunkt: `uv run python forge.py --help` (`forge.py` → `src/forge/`)
- Pipeline-Module (`src/forge/`): `research/`, `discovery/`, `gate/`, `benchmark/` (`adapters.py`, `cost_oracle.py`), `refine/`, `qa/`, `sync/`, `archivist/`, `sourcer/`, `setup/`, `sanitize/`, `units/`
- Kuratierte Profile, Einheiten & Nudges: `library/profiles/`, `library/units/`, `library/nudges/`, `library/skills/`
- Eval-Suites & Tresor: `evals/suites/`, `evals/held-out/`
- Ungeprüfte Rohfunde / Archiv: `staging/inbox/`, `archive/`

## Verbindliche Arbeitsregeln

1. **Research-Isolation:** Web-Recherchen außerhalb des Hauptkontexts über `uv run python forge.py research ...` oder isolierte Subagenten ausführen.
2. **QA Live-First:** Vor dem Anlegen neuer `*.spec.ts`-Tests explorativen Live-Walkthrough (`/browser`, `chrome-devtools-mcp --slim` oder `uv run python forge.py qa`) durchführen.
3. **Scar-Tissue-Regel:** Neue Regeln erst nach realen Fehlern ergänzen und strikt am kanonischen Ort ablegen (`AGENTS.md` vs. `SKILL.md` vs. Hook).
4. **System-Invarianten:** Python-Befehle via `uv run`, alle Text-/JSON-/YAML-Dateien als UTF-8 ohne BOM mit LF-Zeilenenden.
