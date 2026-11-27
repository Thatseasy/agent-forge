# Stage 2 Gate — Scanner-Verfügbarkeit & Fallback-Engine

Stage 2 (`src/forge/gate/`) kombiniert lokale deterministische Python-Prüfer (immer aktiv, 0 USD) mit optionalen externen Scanner-Binaries, sofern sie im `PATH` verfügbar sind.

## Geprüfte externe Scanner unter Windows 11

| Tool | Zweck | Windows-Status (System-Audit) | Verhalten in Stage 2 |
| :--- | :--- | :--- | :--- |
| `agnix` | Spec-/Hook-Linter (`npm` v0.61.0) | Fehlgeschlagen im `install.js`-Postinstall (`ExtractToDirectory`-Overload-Fehler unter PowerShell) | Optionaler Aufruf via `shutil.which("agnix")`; Fallback auf eingebauten `validator.py` |
| `skill-validator` | `agentskills.io` Spec-Validator | Nicht als Windows-Binary / npm-Paket installiert | Eingebauter `validator.py` übernimmt Frontmatter-, Längen-, Token- & `--help`-Checks |
| `skill-scanner` | Statischer SKILL.md Security-Scanner | Nicht im `PATH` installiert | Eingebauter `security.py` (Python AST/Regex/Unicode-BiDi/Prompt-Injection) übernimmt vollständig |
| `Husk` / `snyk` (`agent-scan`) | Supply-Chain- & Agent-Security-Scan | Nicht konfiguriert / nicht installiert | Optionaler Hook bei Vorhandensein im `PATH`, sonst deterministische Basis-Engine |

## Eingebaute Basis-Engine (immer aktiv)

1. `validator.py`: Prüft YAML-Frontmatter (`name <= 64`, `description <= 1024`, `Use when ...` / `Do not use ...`), Zeilenlimits (Warnung ab 400, Fail ab 500 Zeilen oder > 5.000 Tokens), `--help`-Support für Skripte unter `scripts/`.
2. `normalizer.py`: Entfernt Verpackungsmüll (`.DS_Store`, `node_modules`, `LICENSE`, Build-Artefakte) und erkennt Claude-Code-Syntax (`$ARGUMENTS`, `Task(...)`, `.claude/skills/`) als `needs_porting` / Patch.
3. `security.py`: Statische Analyse auf Zero-Width-/BiDi-Unicode, versteckte HTML-Kommentare, Prompt-Injection, Exfiltration (`curl`/`Invoke-WebRequest`), Base64-Obfuskation und unangekündigte Zugriffe auf `.env`/`~/.ssh`/`~/.gemini`.
4. `dedup.py`: Lokale TF-IDF Cosine-Similarity (`scikit-learn`) gegen alle Skills in `library/skills/`.
