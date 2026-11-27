# Konzept & Backlog: Superarchivist, Intelligence-Sourcer & Unified `setup`

> **Status:** `[Backlog / Spec]` — Synchronisierbar nach Notion unter `Projekte → Agent-Forge & Superarchivist-System` via `uv run python scripts/push_notion_projekte.py`.

---

## 1. Vision & Gesamtziel

Aus vielen bestehenden Projekten (mit unterschiedlichen Prompts, Skills, Dokumentations-Stilen und Lücken) wird empirisch gelernt und ein wiederverwendbares **Superarchivist- + Intelligence-Sourcer-System** aufgebaut, das als eigenständige **Boilerplate** sowie als gemeinsamer **`setup`-Befehl** agiert:

- **Szenario:** Ein Projekt wächst innerhalb weniger Stunden im Chat (z. B. zur SaaS-Website). Sobald die Entwicklungsphase Fahrt aufnimmt, startet der Entwickler `setup` (bzw. ruft den Superarchivist-Skill auf).
- **Ergebnis:** Das System analysiert das Projekt, wählt die passenden Dokumentations-Artefakte (`AGENTS.md`, ADRs, Change-Records, DevLog, Notion-Backlog), schließt **3–5 hochpräzise, domänenspezifische Web-/Doku-Quellen** effizient via MCP/CLI an (statt aus Trainingsdaten zu raten oder Token bei Ad-hoc-Suchen zu verbrennen) und härtet das Agenten-Harness (Hooks, Plugins, Lockfiles, QA-Gates), damit nichts mehr vergessen wird.

---

## 2. Geplante Vertical-Slice-Tickets (Backlog für später)

### Ticket 1: `[Backlog] Projekt-Initialisierung & Basis-Harness finalisieren`
- **Typ:** HITL / AFK
- **Blockiert durch:** Keine (aktueller Fokus)
- **Scope:** Git-Repo `agent-forge` sauber committen/pushen, Notion-Integration verbinden, Live-Benchmark (`forge.py bench --trials 3`) mit geladenen API-Credits ausführen.
- **Abnahmekriterien:**
  - [ ] `git push` ins private GitHub-Repo inkl. `CODEOWNERS` & CI-Workflows
  - [ ] Notion-Unterseite unter `Projekte` verbunden & synchronisiert
  - [ ] Erster Live-`BENCHMARK.json` inkl. `ValueScore` erzeugt

### Ticket 2: `[Backlog] Automatischen Archivist & Projekt-Dokumentations-Auditor aktivieren`
- **Typ:** AFK
- **Blockiert durch:** Ticket 1
- **Scope:** Automatisierten Archivist-Flow (basierend auf `archivist`-Skill + Stop-/Post-Commit-Hooks) auf bestehende Projekte ansetzen: erkennen, wo Doku veraltet oder ausgelassen wurde, welche Projekte beispielhafte Struktur hatten und welche Prompts/Skills dort funktionierten.
- **Abnahmekriterien:**
  - [ ] Portfolio-Scan über wählbare Projekt-Ordner liefert strukturierten `ARCHIVE_AUDIT.md` (Doku-Drift, fehlende ADRs, wirksame Patterns)
  - [ ] Extrahierte Skills/Rules werden automatisch in `staging/inbox/` für Stage-2/3-Prüfung überführt
  - [ ] Append-only History (`docs/history/`, `docs/decisions/`) mit Zero-Secret-Garantie

### Ticket 3: `[Backlog] Brutaler Skill-Vergleich: Eigene Projekt-Patterns vs. Top-Web-Skills`
- **Typ:** AFK
- **Blockiert durch:** Ticket 2
- **Scope:** Extrahierte Skills aus eigenen Projekten in `agent-forge` Stage 3 (`AblationArena`) gegen die stärksten Web-Skills (via Stage-1-Radar / Registries) antreten lassen — inklusive `pass^3`, Token-Overhead, Prompt-Cache-Treffern und `ValueScore`.
- **Abnahmekriterien:**
  - [ ] Mindestens 1 Portfolio-Skill vs. Top-Web-Skill Benchmark mit `--trials 5` und Held-Out-Suite abgeschlossen
  - [ ] Gewinner per `forge.py prune` token-optimiert und in `skills-lock.json` gelockt

### Ticket 4: `[Backlog] Intelligence-Sourcer: Top 3–5 Webquellen pro Domäne finden & per MCP/CLI verdrahten`
- **Typ:** HITL / AFK
- **Blockiert durch:** Ticket 1
- **Scope:** Spezialisierter Agent/Skill, der anhand des Tech-Stacks (z. B. Next.js, Supabase, Stripe, Playwright) automatisch die **3–5 verlässlichsten Primärquellen** identifiziert (Context7, LLMs.txt, offizielle MCPs wie Artificial Analysis / Notion, `mcp2cli`), Token-Kosten vs. Trainingsdaten-Raten bewertet und eine fertige, minimale MCP/CLI-Konfiguration erzeugt.
- **Abnahmekriterien:**
  - [ ] Quell-Scoring-Matrix (`Signalqualität`, `Aktualität`, `Token-Overhead`, `Anbindungsart: MCP vs. CLI vs. Context7`)
  - [ ] Generiert fertige `mcp_config.json`-/CLI-Templates ohne Kontext-Überladung (`mcp2cli --toon` / `--slim`)

### Ticket 5: `[Backlog] Superarchivist + Intelligence-Sourcer als eigenständige Boilerplate & `setup`-Befehl`
- **Typ:** AFK
- **Blockiert durch:** Ticket 2, Ticket 3, Ticket 4
- **Scope:** Eigenständige Boilerplate + gemeinsamer `setup`-Befehl (z. B. `forge.py setup <projektpfad>` / `/setup`), der ein ad-hoc entstandenes Projekt (z. B. 3h-Chat-SaaS-Prototyp) analysiert, passende Docs + Agenten/Hooks/Plugins + die 3–5 besten MCP-Quellen + Notion-Backlog automatisch einrichtet und absichert.
- **Abnahmekriterien:**
  - [ ] Erkennung von Projekt-Phase (Prototyp vs. aktive SaaS-Entwicklung) und automatische Auswahl benötigter Docs/ADRs/Rules
  - [ ] Idempotenter `setup`-Durchlauf auf einem Demo-SaaS-Projekt inkl. bestandenem Stage-2-Gate & Hook-Schema-Check
  - [ ] Sicherheitsnetz (Pre-Commit, Hooks, Lockfile, Stop-Nudge), sodass Doku und Tests nicht mehr vergessen werden
