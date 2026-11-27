# QA Charter: Adversarial Empty & Boundary Inputs

- **Ziel:** Leere Zustände, Extrem-Strings (300+ Zeichen), Emojis und Sonderzeichen testen.
- **Heuristiken:**
  1. Pflichtfelder leer absenden und Validierungs-Feedback prüfen.
  2. 320-Zeichen-String ohne Leerzeichen + Unicode-Emojis + `<script>alert(1)</script>` einfügen.
  3. Prüfen auf Layout-Überläufe, unescaped HTML, 500er-Antworten und JS-Exceptions.
- **Erfolgs-Gate:** Saubere Fehlermeldungen, kein XSS, kein HTTP >= 500, keine Layout-Sprengung.
