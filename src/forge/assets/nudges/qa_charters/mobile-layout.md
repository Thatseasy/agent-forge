# QA Charter: Mobile Layout (375x812)

- **Ziel:** Responsive Darstellung, Überlappungen, Kontrast und horizontalen Overflow auf Mobilgeräten prüfen.
- **Viewport:** `375x812`
- **JS-Checks:**
  - `document.documentElement.scrollWidth > window.innerWidth`
  - Sichtbare Overlays mit `pointer-events` die interaktive Buttons verdecken
- **Erfolgs-Gate:** Kein horizontaler Scrollbar-Overflow (`scrollWidth <= innerWidth`), alle Buttons klickbar.
