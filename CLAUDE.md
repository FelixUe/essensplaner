# CLAUDE.md

Hinweise für Claude Code (claude.ai/code) zur Arbeit in diesem Repo.

## Worum es geht

Regelbasierter Monatsessensplan für einen Haushalt.
Reines Python, keine Datenbank: Stammdaten sind versionierte YAML-Dateien,
erzeugte Pläne sind Laufzeitdaten. Läuft als Container mit eingebautem
Scheduler und einer kleinen Flask-Oberfläche.

Die fachlichen Vorgaben stehen in [SPEC.md](SPEC.md), die Bedienung in
[README.md](README.md). **Vor inhaltlichen Änderungen beide lesen** — die
Regeln sind gesetzt, nicht aus dem Code ableitbar.

## Aufbau

Der vollständige Dateibaum steht in README.md unter „Aufbau". Kurzfassung:

- `config.yaml` — Haushalt, die fünf Slots, Ernährungs- und Planungsregeln, Ausschlüsse
- `data/ingredients.yaml`, `data/recipes/*.yaml` — Stammdaten, versioniert
- `plans/<JJJJ-MM>.json` — erzeugte Pläne, Laufzeitdaten, gitignored
- `src/essensplaner/` — ein Modul je Aufgabe; der Modul-Docstring sagt, welche

Arbeitsreihenfolge für eine fachliche Änderung: `config.yaml`/Stammdaten →
`rules.py` → `planner.py` → Darstellung (`shopping.py`, `mail.py`, `web.py`) →
Tests. Ein neues Rezept ist nur eine YAML-Datei plus `essensplaner validate`.

## Befehle

```bash
uv venv --python 3.12                     # holt sich die 3.12 selbst, falls nötig
uv pip install -e ".[dev]"
source .venv/bin/activate                 # Windows: .venv\Scripts\activate

essensplaner validate                     # Stammdaten gegen alle harten Regeln
essensplaner generate-month 2026-12 --seed 1 --dry-run
essensplaner preview weekly --monat 2026-12   # schreibt preview-weekly.html ins cwd
essensplaner serve --once                 # Scheduler einmal prüfen lassen

pytest -q
ruff check . && ruff format --check .     # genau das prüft auch die CI
```

Der README-Schnellstart nutzt `python -m venv` und `pip`; `uv` tut dasselbe und
ist auf beiden Entwicklungsmaschinen vorhanden. Auf Windows ist `python` oft nur
der Store-Platzhalter — `uv` bringt die 3.12 selbst mit.

## Gotchas

- **Harte Regeln wirken an drei Stellen** (`rules.py`): beim Laden der
  Stammdaten, als Kandidatenfilter im Planer und als Abnahme des fertigen
  Plans. Eine neue Regel muss an allen drei greifen. Ein Plan, der eine harte
  Regel bricht, wird nicht gespeichert — lieber kein Plan als ein falscher.
- **`essensplaner validate` ist das Tor.** Die CI fährt es gegen die echten
  Rezepte im Repo, dazu einen Probeplan mit `--seed 1`. Ein Rezept mit Fenchel,
  Salbei oder Blauschimmel bricht die Pipeline, nicht erst die Freitagsmail.
- **Protein nicht von Hand passend machen.** `protein_per_serving_g` im Rezept
  ist der verbindliche Wert, `nutrition.py` rechnet ihn aus den Zutaten nach,
  `tools/protein_sync.py` gleicht beides ab.
- **Deutsche Benennung ist Absicht** — Domänenbegriffe, Funktionsnamen,
  Kommentare und Commit-Messages. Beibehalten.
- **Laufzeitdaten gehören nicht ins Repo.** `plans/*.json` und die
  Katalogänderungen aus `katalog.py` (eigene Rezepte, gestrichene Gerichte)
  liegen im Volume. Rezept im Repo ändern = Commit; Gericht streichen = Volume.
- **Weboberfläche lokal:** startet nur, wenn `ESSENSPLANER_WEB_PASSWORT`
  gesetzt ist. Über `http://` zusätzlich `ESSENSPLANER_WEB_UNSICHER=true`,
  sonst ist das Sitzungs-Cookie secure-only und die Anmeldung scheitert
  stillschweigend. Hinter einem Reverse Proxy mit TLS auf `false` lassen.
- **LF ist erzwungen** (`.gitattributes`), das Image läuft unter Linux. CRLF in
  Konfigurationen oder Skripten führt dort zu schwer auffindbaren Fehlern.
- **`tzdata` ist Pflichtabhängigkeit**, nicht Ballast: ohne sie kennt
  `zoneinfo` auf Windows und in slim-Images nicht einmal `UTC`.
