# Essensplaner

Automatisierter Monatsessensplan für eine Familie mit zwei Erwachsenen und zwei
Kindern. Einmal im Monat entsteht der komplette Plan für den Folgemonat, jeden
Freitag kommt die Einkaufsliste für die Woche per Mail.

Läuft als Container – zunächst unter Coolify auf einem Hetzner-Server, später
unverändert auf eigener Homelab-Hardware.

Die fachlichen Vorgaben stehen in [SPEC.md](SPEC.md).

---

## Was der Planer macht

- **Vegetarisch**, mit einer einzigen Ausnahme: Fischstäbchen, höchstens einmal
  im Monat.
- **Harte Ausschlüsse**: kein Blauschimmelkäse, kein Salbei, kein Fenchel in
  irgendeiner Form. Jedes Rezept und jede Variante wird vor dem Einplanen
  dagegen geprüft, Synonyme eingeschlossen. Gewürzmischungen sind nur erlaubt,
  wenn sie ausdrücklich als fenchelfrei markiert sind.
- **Montags und mittwochs** wird für zwei Erwachsene auf vier Portionen
  vorgekocht, die den jeweils nächsten Mittag mitabdecken. Dort gilt ein
  Mindestwert von 30 g Protein je Portion – notfalls greift die
  Protein-Variante des Rezepts.
- **Wochenende** darf aufwendiger sein; pro Wochenende steht mindestens ein
  süßes oder zünftiges Gericht auf dem Plan.
- **Abwechslung**: ein Gericht höchstens dreimal im Monat, nie zweimal
  innerhalb von sieben Tagen, nie zweimal in derselben Woche. Rund 60 % der
  Slots gehen an die Lieblingsgerichte, der Rest an Variationen.
- **Haltbarkeit**: Eingekauft wird freitags, der Mittwoch liegt fünf Tage
  später. Rezepte mit Blattsalat, frischen Kräutern, Avocado, Pilzen oder
  Beeren landen deshalb nie im Mittwochs-Slot.
- **Saison**: Melone-Feta nur im Sommer, Kürbissuppe nur von September bis
  Februar.

## Der Wochenrhythmus

Einkaufstag ist Freitag, eine Planungswoche läuft deshalb **Freitag bis
Donnerstag**.

| Slot | Kochtag  | Deckt ab              | Tage | Portionen              |
|------|----------|-----------------------|-----:|------------------------|
| FR   | Freitag  | Freitagabend          |    1 | Familie (2,5)          |
| SA   | Samstag  | Samstag               |    1 | Familie (2,5)          |
| SO   | Sonntag  | Sonntag               |    1 | Familie (2,5)          |
| MO   | Montag   | Montag + Dienstag     |    2 | 4 Erwachsenenportionen |
| MI   | Mittwoch | Mittwoch + Donnerstag |    2 | 4 Erwachsenenportionen |

**Kein Gericht deckt mehr als zwei Tage ab.** Montag und Mittwoch reichen bis
in den Folgetag – gekocht wird abends, die Reste sind am nächsten Tag das
Mittagessen der Erwachsenen. Freitag, Samstag und Sonntag sind je ein Tag.
Die Zahl steht als `tage` in `config.yaml` und nicht nur im Text von
`deckt_ab`; `essensplaner validate` lehnt einen Slot über zwei Tagen ab, und
in der Wochenmail steht sie bei den Zwei-Tages-Slots ausdrücklich dabei.

Die Kinder essen Mo–Fr mittags in der Kita, unter der Woche wird nichts für sie
geplant. Ein Kind zählt als halbe Erwachsenenportion, eine Familienmahlzeit
also als 2,5 Portionen. Beides ist in `config.yaml` einstellbar.

Ein Monatsplan beginnt mit der Woche, die am letzten Freitag vor dem
Monatsersten startet, und endet vor der Woche, die den Folgemonat einläutet.
Die Übergangswoche gehört damit immer genau einem Monat – dem neuen.

## Die beiden Mails

- **Monatsmail**, am letzten Freitag vor Monatsbeginn, zusammen mit dem
  normalen Wocheneinkauf: Übersicht aller Gerichte des Monats und die
  **Vorratsliste** – alle haltbaren Zutaten des Monats, summiert und auf ganze
  Packungen aufgerundet.
- **Wochenmail**, jeden Freitag um 07:00 Europe/Berlin: die Gerichte von
  Freitag bis Donnerstag mit Kochtag, die **Frischeliste** nach Abteilung
  sortiert und mit aggregierten Mengen, dazu alle Rezepte mit den auf die
  Portionen hochgerechneten Mengen.

Beide Mails gehen als HTML mit Plain-Text-Fallback raus und sind auf dem Handy
ohne Zoom lesbar.

---

## Die Weboberfläche

Der Planer würfelt einen regelkonformen Monat. Was er nicht wissen kann: dass
am Samstag Besuch kommt. Dafür gibt es eine kleine Oberfläche im Container –
Monatsübersicht, und je Tag ein Knopf *Ändern*. Dahinter liegen zwei Wege:

- **Auf einen anderen Tag schieben.** Zwei geplante Gerichte tauschen die
  Plätze. Kein freies Verschieben – ein Kochtag soll nicht leer bleiben.
  Beide Gerichte müssen auf dem neuen Platz erlaubt sein, und Portionen,
  Variante und Tagesabdeckung richten sich danach neu: vom Samstag auf den
  Montag heißt vier Portionen und zwei Esstage.
- **Ein anderes Gericht einsetzen.** Angeboten wird nur, was in diesem Slot
  erlaubt ist.

Erlaubt heißt in beiden Fällen dasselbe: richtiger Slot, in der Saison, diese
Woche noch nicht auf dem Tisch, Mindestabstand eingehalten, Monatsgrenze nicht
erreicht, genug Protein an den Vorkochtagen und keine Frischware, die bis
dahin nicht hält. Nach jeder Änderung läuft die vollständige Planprüfung noch
einmal; scheitert sie, bleibt der alte Plan stehen. Ein von Hand geänderter
Plan ist damit genauso regelkonform wie ein gewürfelter.

### Merkliste

Gerichte, die demnächst auf den Tisch sollen, kommen auf die **Merkliste** –
ein Knopf auf jeder Rezeptseite. Sie plant nichts von selbst: beim Ändern
eines Tages stehen die vorgemerkten Gerichte oben in der Auswahl, sofern sie
dort erlaubt sind. Wird eines eingeplant, nimmt es die Liste selbst herunter.
Umgekehrt kann das herausgenommene Gericht per Häkchen gleich auf die
Merkliste wandern – so tauscht man eines gegen das andere.

Je Woche gibt es dort auch den **Einkaufszettel**: Frischware für den
Einkaufstag und die haltbaren Zutaten dieser Woche. Beide Listen nennen
kaufbare Mengen – 20 g Ingwer bekommt man nirgends, also steht dort die
100-g-Knolle und daneben der tatsächliche Bedarf. Wo Rezept und Regal
verschiedene Einheiten sprechen (drei Möhren, ein Kilobeutel), rechnet das
hinterlegte Stückgewicht um. Der Zettel wird bei jedem Aufruf neu gerechnet,
stimmt nach einem Tausch also sofort.

Darunter steht der **Grundeinkauf**: Milch, Brot, Kaffee – was an keinem
Rezept hängt und trotzdem jede Woche mit soll. Diese Liste wird nicht
gerechnet, sondern von Hand gepflegt (ein Posten je Zeile, eine Zeile mit
Doppelpunkt am Ende wird eine Überschrift). Beim ersten Mal steht dort ein
Vorschlag, bis er gespeichert und damit ersetzt wird. Posten, die oben schon
ausgerechnet stehen, bekommen einen Hinweis – gestrichen wird nichts, denn die
Rezeptmenge deckt selten den Hausgebrauch.

**Mailversand ist optional.** Bleibt `SMTP_HOST` leer, erzeugt der Scheduler
weiterhin die Monatspläne und verschickt nur nichts – alles steht in der
Oberfläche. Wer die Mails will, trägt die SMTP-Werte nach; dann laufen beide
Wege nebeneinander.

### Rezepte pflegen

Die Oberfläche zeigt zu jedem Gericht die Zutaten und die Zubereitung – auch
die Protein-Variante, die in den Vorkoch-Slots gilt. Dazu lässt sich der
Katalog ändern:

- **Nicht mehr einplanen.** Das Gericht verschwindet aus der Auswahl des
  Planers, bleibt aber ladbar. Ein Plan, der schon steht, nennt es weiter
  beim Namen, und sein Einkaufszettel stimmt. Jederzeit umkehrbar.
- **Rezept anlegen.** Name, Slots, Zeiten, Zutaten und Schritte im Formular;
  die Zutaten als eine Zeile je Posten (`800 g Zucchini`), erkannt werden
  Name, Kurzname und Alias. Der Proteinwert wird aus den Zutaten gerechnet,
  nicht abgefragt – eine geschätzte Zahl wäre genau die Angabe, die später
  von den Zutaten abweicht.

Ein neues Rezept muss dieselbe Prüfung bestehen wie die aus dem Repo:
ausgeschlossene Zutaten, Protein in MO/MI, Haltbarkeit. Was durchfällt, wird
gar nicht erst gespeichert.

Beides landet im Volume neben den Plänen (`rezepte/*.yaml` und
`gestrichen.json`, dazu `merkliste.json` und `grundeinkauf.json`) und
überlebt damit jeden Rollout. Die 52 Rezepte aus dem
Repo liegen schreibgeschützt im Image – für sie ist Streichen der einzige
Weg, und Änderungen an ihnen gehören weiter in git.

Die Oberfläche startet nur, wenn `ESSENSPLANER_WEB_PASSWORT` gesetzt ist.
Ohne Passwort läuft allein der Scheduler. Das ist Absicht: hinter einer
Domain stünde sonst eine offene Seite, auf der jeder den Essensplan umbauen
kann. Dazu kommen ein signiertes Sitzungs-Cookie, eine Pause nach mehreren
Fehlversuchen und ein Token in jedem Formular.

```bash
ESSENSPLANER_WEB_PASSWORT=... essensplaner serve
```

Scheduler und Oberfläche laufen im selben Prozess, weil beide dieselben
Plandateien anfassen. Eine Sperre im Prozess reicht; zwei Container müssten
sich über das Volume abstimmen.

---

## Schnellstart

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Stammdaten prüfen:

```bash
essensplaner validate
```

Einen Plan erzeugen, ohne eine Mail zu verschicken:

```bash
essensplaner generate-month 2026-12 --seed 1 --dry-run
```

Die Mail ansehen, bevor sie jemand bekommt:

```bash
essensplaner preview weekly --monat 2026-12
```

Das schreibt `preview-weekly.html` und `preview-weekly.txt` ins
Arbeitsverzeichnis – HTML im Browser öffnen.

Tests:

```bash
pytest
```

## Die Kommandozeile

| Befehl | Zweck |
|---|---|
| `essensplaner validate` | Zutaten und Rezepte gegen alle harten Regeln prüfen, Proteinangaben nachrechnen, Slotdeckung zeigen |
| `essensplaner generate-month [MONAT]` | Monatsplan erzeugen und Vorschau mailen. Ohne Monat: der Folgemonat |
| `essensplaner send-weekly` | Die Freitagsmail verschicken |
| `essensplaner show MONAT` | Einen gespeicherten Plan im Terminal ansehen |
| `essensplaner shopping MONAT` | Einkaufslisten im Terminal |
| `essensplaner preview weekly\|monthly` | Eine Mail als HTML-Datei schreiben, ohne zu verschicken |
| `essensplaner serve` | Eingebauter Scheduler für den Dauerbetrieb |

`--dry-run` gibt es bei `generate-month` und `send-weekly`: alles wird gebaut
und angezeigt, aber nichts verschickt.

---

## Ein Rezept hinzufügen oder ändern

Rezepte sind einzelne YAML-Dateien unter `data/recipes/`, Zutaten stehen
gesammelt in `data/ingredients.yaml`. Beides ist versioniert – eine Änderung
ist ein Commit, und die Pipeline rollt sie aus.

```yaml
# data/recipes/mein-gericht.yaml
slug: mein-gericht          # muss dem Dateinamen entsprechen
name: Mein Gericht
core: true                  # Lieblingsgericht, kommt häufiger vor
slots: [MO, MI, SA]         # wo es eingeplant werden darf
tags: [lunchbox_ok, freezer_ok, kids_ok]
seasons: all                # oder [9, 10, 11, 12, 1, 2]
prep_minutes: 35
base_servings: 4            # Mengen gelten für vier Erwachsenenportionen
protein_per_serving_g: 32
ingredients:
  - { item: kidneybohnen_dose, qty: 2, unit: Dose }
  - { item: rote_linsen, qty: 150, unit: g }
steps:
  - Erster Arbeitsschritt.
variants:
  protein:                  # wird in den Slots MO und MI genommen
    hinweis: Steht so in der Mail.
    zusatz:
      - { item: skyr, qty: 200, unit: g }
    protein_per_serving_g: 35
```

Danach:

```bash
python tools/protein_sync.py          # Proteinangaben nachrechnen
python tools/protein_sync.py --write  # und übernehmen
essensplaner validate
```

`validate` meldet unter anderem eine Zutat ohne Stammdaten, eine
ausgeschlossene Zutat, ein Rezept, das in einem Protein-Slot unter 30 g bleibt,
und eine Proteinangabe, die nicht zu den Zutaten passt.

### Zutaten-Stammdaten

```yaml
kidneybohnen_dose:
  name: Kidneybohnen (Dose, 400 g)
  type: pantry              # pantry = Vorratsliste, fresh = Wochenliste
  department: Konserven     # Sortierung der Einkaufsliste
  pack: { qty: 1, unit: Dose }
  shelf_days: 730
  protein_per_100: 8.0      # Grundlage der Nachrechnung
  stk_gewicht_g: 240        # bei Dosen das Abtropfgewicht
```

Zwei Felder steuern die Planung:

- `empfindlich: true` – nur in Slots nah am Einkaufstag (FR/SA/SO/MO).
- `grundzutat: true` – Stärkebeilage. Der Planer vermeidet, dass dieselbe
  Grundzutat die Woche dominiert, und zählt sie nicht als nützliche
  Zutatenüberlappung. Sonst gäbe es fünf Tage Kartoffeln, nur weil der Sack
  schon offen ist.

### Alle Rezepte am Stück durchsehen

52 YAML-Dateien liest niemand gern nacheinander. `tools/uebersicht.py` baut
daraus eine Seite, auf der alles steht, was beim Durchsehen zählt: die Mengen
der Basis- und der Protein-Variante nebeneinander, das aus den Zutaten
nachgerechnete Protein neben der Angabe, die empfindlichen Zutaten und wie oft
ein Gericht in einem Beispieljahr tatsächlich geplant wird.

```bash
python tools/uebersicht.py --html
```

Das schreibt `tools/rezepte.json` und `tools/uebersicht.build.html` – beide
sind Ergebnisse, keine Quelldateien, und stehen in `.gitignore`. Die Vorlage
ist `tools/uebersicht.html`. Als Artifact veröffentlicht merkt sich die Seite
zusätzlich, welches Rezept schon durchgesehen ist.

---

---

## Aufbau

```
config.yaml              Personen, Portionen, Protein-Ziel, Ausschlüsse, Regeln
data/ingredients.yaml    Zutaten-Stammdaten
data/recipes/*.yaml      ein Rezept je Datei
plans/<JJJJ-MM>.json     erzeugte Pläne (Laufzeitdaten, im Volume)
src/essensplaner/
  models.py              Zutat, Rezept, Variante, Plan
  config.py              Konfiguration und Secrets aus der Umgebung
  data.py                Laden und Speichern
  kalender.py            Wochenrhythmus Freitag bis Donnerstag
  rules.py               harte Regeln: Ausschlüsse, Protein, Haltbarkeit, Wiederholung
  nutrition.py           Protein aus den Zutaten nachrechnen
  planner.py             regelbasierter Scheduler
  editing.py             ein Gericht im fertigen Plan tauschen
  katalog.py             eigene Rezepte und gestrichene Gerichte
  shopping.py            Frischeliste und Vorratsliste
  units.py               Einheiten zusammenfassen und ausgeben
  mail.py                Mails rendern und verschicken
  templates/             HTML- und Text-Vorlagen
  runner.py              die beiden geplanten Läufe
  scheduler.py           eingebauter Scheduler
  cli.py                 Kommandozeile
  web.py                 Weboberfläche (Flask, nur mit Passwort)
  templates/web/         Vorlagen der Oberfläche (Monat, Einkauf, Ändern,
                         Rezepte, Merkliste, Grundeinkauf)
tools/protein_sync.py    Proteinangaben mit der Nachrechnung abgleichen
tools/uebersicht.py      alle Rezepte als JSON und als Seite zum Durchsehen
tests/                   die sechs Akzeptanzkriterien und alles darum
```

Die Regeln sind an drei Stellen wirksam: beim Laden der Stammdaten, als
Kandidatenfilter während der Planung und als Abnahme des fertigen Plans. Ein
Plan, der eine harte Regel bricht, wird nicht gespeichert – lieber kein Plan
als ein falscher.

---

## Deployment

Die Pipeline baut bei jedem Push auf `main` ein Image, veröffentlicht es in der
GitHub-Registry und löst den Rollout in Coolify aus. Der Rollout läuft nur,
wenn vorher alle Prüfungen grün waren.

Das Image entsteht für `linux/amd64` und `linux/arm64`. Derselbe Tag läuft
damit auf einem x86-Server, auf Hetzners ARM-Reihe und auf einem Raspberry Pi
mit 64-Bit-System; Docker wählt beim Ziehen die passende Fassung.

Die vollständige Anleitung samt Coolify-Einrichtung, Secrets und Mailversand
steht in [docs/deployment.md](docs/deployment.md).

Kurzfassung:

1. In Coolify eine Ressource vom Typ **Docker Image** anlegen, Image
   `ghcr.io/<owner>/essensplaner:latest`.
2. Ein Volume auf `/data/plans` legen, damit die Pläne einen Rollout überleben.
3. Die SMTP- und Mail-Variablen setzen (siehe `.env.example`), Passwörter als
   Secret.
4. In Coolify ein API-Token mit der Berechtigung **Deploy** erzeugen und die
   Deploy-URL der Ressource kopieren.
5. Im GitHub-Repo die Secrets `COOLIFY_WEBHOOK` und `COOLIFY_TOKEN` setzen.
6. Für die Oberfläche eine Domain auf Port 8080 legen und
   `ESSENSPLANER_WEB_PASSWORT` setzen – ohne Passwort startet sie nicht.

Ohne diese beiden Secrets läuft die Pipeline trotzdem durch: Das Image landet
in der Registry, nur der Rollout wird mit einem Hinweis übersprungen.

### Zeitsteuerung

Im Container läuft standardmäßig `essensplaner serve`. Der Scheduler schickt
die Wochenmail freitags um 07:00 und erzeugt am vorletzten Freitag des Monats
den Plan für den Folgemonat. Jede Aufgabe läuft pro Termin höchstens einmal;
der Zustand liegt in `/data/plans/state.json`, ein Neustart löst also keine
zweite Mail aus.

Wer die Coolify-Scheduled-Tasks bevorzugt, überschreibt das Kommando und legt
zwei Tasks an:

| Task | Kommando | Zeitplan |
|---|---|---|
| `send-weekly` | `essensplaner send-weekly` | `0 7 * * 5` |
| `generate-month` | `essensplaner generate-month` | `30 7 * * 5` (prüft selbst, ob der Termin passt) |

### Umzug ins Homelab

Das Image ist an nichts gebunden, was es nur bei Hetzner gibt. Auf der neuen
Hardware genügt:

```bash
cp .env.example .env    # ausfüllen
docker compose up -d
```

Das Volume `essensplaner-plans` mitnehmen, dann bleiben die bereits erzeugten
Pläne erhalten.

---

## Nächste Schritte (v2)

Aus der Spec noch offen:

- Bewertung per Link in der Mail (👍/👎), die auf die Gewichtung wirkt.
- Tausch-Link „Gericht ersetzen“ für einzelne Slots.
- Vorratsbestand pflegen. Die Rechenseite steht schon: eine
  `data/bestand.yaml` mit `slug: { qty, unit }` wird beim Erzeugen der
  Vorratsliste abgezogen – es fehlt nur eine bequeme Pflege.
- Export in eine Einkaufs-App wie Bring!.
- Rezeptvorschläge über die Claude API. Vorgesehen ist eine
  Review-Warteschlange unter `data/recipes/_pending/`; Vorschläge werden erst
  nach Freigabe eingeplant und laufen vorher durch Ausschluss- und
  Protein-Check.
