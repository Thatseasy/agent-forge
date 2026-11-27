# QA Charter: Impatient User

- **Ziel:** Doppelklicks (< 200 ms), Abbrüche während laufender Requests und Mehrfach-Submits prüfen.
- **Heuristiken:**
  1. Jeden primären Action-Button doppelt innerhalb von 150 ms klicken.
  2. Während laufender Lade-Spinner sofort zurück navigieren oder erneut absenden.
  3. Prüfen auf doppelte `POST`/`PUT`-Requests, uncaught Console-Errors und hängende Loading-Overlays.
- **Erfolgs-Gate:** Keine doppelten Mutations-Requests, keine Konsolenfehler, kein blockierter UI-Zustand.
