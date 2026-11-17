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

Liegt schon ein `.venv`, bricht `uv venv` mit „already exists" ab. Das ist kein
Fehler, dann genügt `uv pip install -e ".[dev]"`. `--clear` nur bewusst setzen,
es wirft das vorhandene Environment weg. Ohne Aktivierung gehen die Befehle
auch direkt: `.\.venv\Scripts\pytest.exe -q`.

`ruff check` und `ruff format --check` sind in der CI **zwei getrennte
Schritte** — reine Formatierung bricht den Build unabhängig vom Lint. Die CI
baut darüber hinaus das Image und fährt darin `validate`,
`generate-month --dry-run` und `serve --once`; was lokal grün ist, kann an
diesem Rauchtest noch scheitern, wenn eine Datei nicht ins Image kommt.

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
  Kommentare und Commit-Messages. Beibehalten. Commit-Betreffs tragen kein
  Präfix wie `feat:` oder `fix:`, sie sagen schlicht, was die Änderung tut.
- **Laufzeitdaten gehören nicht ins Repo.** `plans/*.json` und die
  Katalogänderungen aus `katalog.py` (eigene Rezepte, gestrichene Gerichte)
  liegen im Volume. Rezept im Repo ändern = Commit; Gericht streichen = Volume.
- **Weboberfläche lokal:** startet nur, wenn `ESSENSPLANER_WEB_PASSWORT`
  gesetzt ist. Über `http://` zusätzlich `ESSENSPLANER_WEB_UNSICHER=true`,
  sonst ist das Sitzungs-Cookie secure-only und die Anmeldung scheitert
  stillschweigend. Hinter einem Reverse Proxy mit TLS auf `false` lassen.
  `ESSENSPLANER_WEB_SECRET` fest setzen, sonst meldet jeder Neustart ab.
  Die Oberfläche hört auf Port 8080 (`ESSENSPLANER_WEB_PORT`).
- **Es gibt keinen dotenv-Loader.** Nur docker-compose liest `.env`; der nackte
  Prozess sieht ausschließlich, was in der Shell steht. Eine `.env` anzulegen
  und `essensplaner serve` zu starten, reicht also nicht.
- **Neue Abhängigkeiten müssen auf ARM ohne Übersetzen laufen.** Das Image
  entsteht für `linux/amd64` **und** `linux/arm64` — deshalb `waitress` statt
  eines Servers mit C-Erweiterung.
- **Mailversand ist optional.** Ohne `SMTP_HOST` erzeugt der Scheduler weiter
  Pläne und verschickt nur nichts. Diesen Pfad funktionsfähig halten.
- **LF ist erzwungen** (`.gitattributes`), das Image läuft unter Linux. CRLF in
  Konfigurationen oder Skripten führt dort zu schwer auffindbaren Fehlern.
- **`tzdata` ist Pflichtabhängigkeit**, nicht Ballast: ohne sie kennt
  `zoneinfo` auf Windows und in slim-Images nicht einmal `UTC`.
