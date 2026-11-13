# Essensplaner – Spezifikation

Automatisierter Monatsessensplan für einen Haushalt mit wöchentlicher Einkaufsliste per Mail. Läuft auf einem Hetzner-Server unter Coolify.

---

## 1. Ziele

- Einmal im Monat wird der komplette Plan für den Folgemonat erzeugt.
- Jeden Freitag kommt eine Einkaufsliste per Mail (Frischware für die Woche).
- Einmal im Monat kommt zusätzlich eine Vorratsliste (haltbare Zutaten für den ganzen Monat), damit der Wocheneinkauf klein bleibt.
- Bekannte Lieblingsgerichte kommen regelmäßig vor, ergänzt um Variationen und neue Rezepte.

---

## 2. Wochenrhythmus

Einkaufstag ist **Freitag**. Eine Planungswoche läuft daher **Freitag bis Donnerstag**.

| Slot | Kochtag | Deckt ab | Für wen | Portionen | Anspruch |
|---|---|---|---|---|---|
| FR | Freitag | Freitag Abend | Familie | 2 Erw. + 1 Kind | Familie, kindertauglich |
| SA | Samstag | Samstag | Familie | 2 Erw. + 1 Kind | darf aufwendiger, süß oder zünftig sein |
| SO | Sonntag | Sonntag | Familie | 2 Erw. + 1 Kind | darf aufwendiger, süß oder zünftig sein |
| MO | Montag | Mo + Di Mittag | 2 Erwachsene | 4 Erwachsenenportionen | schnell, proteinreich, aufwärmbar |
| MI | Mittwoch | Mi + Do Mittag | 2 Erwachsene | 4 Erwachsenenportionen | schnell, proteinreich, aufwärmbar |

Hinweise:
- Die Kinder essen Mo–Fr mittags in der Kita. Unter der Woche wird nichts für sie geplant.
- Mo–Do wird zu Hause gegessen, Gerichte sollen aber möglichst auch **mitnehmbar** sein (Tag `lunchbox_ok`: schmeckt aufgewärmt oder kalt, läuft nicht aus).
- Portionsfaktor Kinder konfigurierbar (Default: Kind = 0,5 Erwachsenenportion → Familie = 2,5).

---

## 3. Ernährungsregeln

### 3.1 Grundsatz
- **Vegetarisch.** Einzige feste Ausnahme: **Fischstäbchen** (normale, nicht vegetarisch) sind erlaubt.
- Fleischersatzprodukte (Veggie-Würstchen, Köttbullar von Greenforce, Veggie-Chicken-Stripes, Veggie-Burger-Patties) sind erlaubt.

### 3.2 Protein
- **MO- und MI-Slots: mindestens 30 g Protein pro Erwachsenenportion.** Harte Regel.
- Wochenende: kein Mindestwert, aber Protein-Beilage vorschlagen, wo es leicht geht.
- Lieblingsgerichte, die das Ziel nicht erreichen, bekommen für Mo–Do eine **Protein-Variante** (siehe Abschnitt 6), z. B. Linsen ins Chili, weiße Bohnen in die Kartoffelsuppe, Skyr- oder Hüttenkäse-Dip dazu.
- Proteinquellen: Linsen, Kichererbsen, Bohnen, Erbsen, Tofu, Tempeh, Seitan, Edamame, Eier, Skyr, Magerquark, Hüttenkäse, Feta, Halloumi, Parmesan, Proteinnudeln (Linsen- oder Kichererbsennudeln).

### 3.3 Ausschlüsse (hart, niemals verwenden)
Jedes Rezept und jede generierte Variante muss vor dem Speichern gegen diese Liste geprüft werden (Zutatenname, Synonyme, Gewürzmischungen):

- **Blauschimmelkäse und Ähnliches:** Gorgonzola, Roquefort, Stilton, Cambozola, Danablu, Bavaria Blu, Bergader, Blue Cheese, Blauschimmel-Dressings. Gebackener Camembert (Weißschimmel) ist erlaubt.
- **Salbei** (frisch, getrocknet, in Kräutermischungen)
- **Fenchel** in jeder Form: Knolle, Grün, **Fenchelsamen**, Fenchel in Gewürzmischungen (z. B. manche Garam-Masala-, Brot- oder Italienische Mischungen). Gewürzmischungen nur verwenden, wenn als fenchelfrei markiert, sonst Einzelgewürze ausschreiben.

### 3.4 Wochenende
- Pro Wochenende mindestens ein Gericht mit Tag `suess` oder `zuenftig`.
  Der Planer baut es beim Würfeln ein; von Hand darf man es wieder
  wegschieben, dann steht es nur noch als Warnung im Prüfbericht.
- Fischstäbchen höchstens 1× pro Monat (konfigurierbar).

---

## 4. Planungsregeln

- **Wiederholung:** ein Gericht max. **3× pro Monat**, Mindestabstand **7 Tage**.
- **Kernrezepte** (Tag `core`, die Lieblingsgerichte aus Abschnitt 6): Anteil ca. **60 %** der Slots. Der Rest sind Variationen und neue Rezepte.
- Kein Gericht zweimal in derselben Woche.
- **Saisonalität:** Rezepte haben `seasons` (z. B. Kürbissuppe Sep–Feb, Melone-Feta Jun–Aug). Außerhalb der Saison nicht planen.
- **Haltbarkeit der Frischware:** Da Freitag eingekauft wird, liegt der MI-Slot 5 Tage nach dem Einkauf. MI-Rezepte bevorzugt mit haltbarem oder TK-Gemüse (Kohl, Möhren, Kartoffeln, Kürbis, Rote Bete, Zwiebeln, TK-Spinat, TK-Erbsen, Konserven). Empfindliche Zutaten (Blattsalat, Kräuter, Avocado, Pilze, Beeren) nur in FR/SA/SO/MO.
- **Zutatenüberlappung:** Wenn möglich Rezepte so kombinieren, dass angebrochene Zutaten (halber Becher Sour Cream, halber Kohl, Bund Lauch) in derselben Woche aufgebraucht werden.
- **Gefriertruhe** (Konfiguration `freezer: true|false`): Wenn vorhanden, darf der Planer Gerichte mit Tag `freezer_ok` doppelt kochen lassen und eine Portion für einen späteren MO/MI-Slot einfrieren. Default: `false`.

---

## 5. Einkaufslisten

### 5.1 Klassifizierung jeder Zutat
- `pantry` (Vorrat): Nudeln, Reis, Spätzle (trocken), Hülsenfrüchte (trocken/Dose), Dosentomaten, Kokosmilch, Gewürze, Öl, Mehl, Zucker, Brühe, Saucen, TK-Ware, Fischstäbchen, Veggie-Produkte aus dem TK.
- `fresh` (Frisch): Obst, Gemüse, Milchprodukte, Eier, Tofu, Brot, Kühlprodukte.
- Jede Zutat hat außerdem eine `department` (Obst & Gemüse, Kühlregal, Käse, TK, Trockenware, Konserven, Gewürze, Backwaren, Sonstiges) für die Sortierung.

### 5.2 Mails
- **Monatsmail** (am letzten Freitag vor Monatsbeginn, mit dem normalen Wocheneinkauf): Monatsübersicht aller Gerichte + **Vorratsliste** = Summe aller `pantry`-Zutaten des Monats, auf sinnvolle Packungsgrößen aufgerundet.
- **Wochenmail** (jeden Freitag, 07:00 Europe/Berlin): Gerichte Fr–Do mit Kochtag + **Frischeliste** für diese Woche, nach Abteilung sortiert, Mengen aggregiert.
- Beide Mails als HTML (lesbar auf dem Handy) plus Plain-Text-Fallback. Rezepte in der Mail verlinkt oder als Kurzanleitung angehängt.

### 5.3 Vorratsbestand (optional, v2)
- Einfache Liste „habe ich noch“, die beim Generieren der Vorratsliste abgezogen wird.
- Später evtl. Abgleich mit dem Familien-Notvorrat (Rotation).

---

## 6. Startbestand: Lieblingsgerichte (`core`)

Diese Gerichte sollen regelmäßig vorkommen. Claude Code soll für jedes ein vollständiges Rezept mit Mengen (Basis 4 Erwachsenenportionen), Zubereitungszeit, Protein pro Portion (Schätzung) und Tags anlegen. Die Rezepte werden vor dem ersten Lauf durchgesehen.

### Mo–Do-tauglich (vorkochbar, aufwärmbar)
| Gericht | Hinweis / Protein-Variante für MO/MI |
|---|---|
| Chili sin carne | Kidneybohnen + rote Linsen, Skyr statt Sour Cream |
| Milde Currys mit Reis | Kichererbsen, Linsen oder Tofu; Varianten rotieren (Kokos-Linsen, Kichererbsen-Spinat, Tofu-Gemüse) |
| Käse-Lauch-Suppe (mild, nicht deftig) | Veggie-Hack oder weiße Bohnen, dazu Brot |
| Kartoffelsuppe mit vegetarischen Würstchen | + weiße Bohnen |
| Erbsensuppe | Schälerbsen sind schon proteinreich, + Veggie-Würstchen |
| Kürbissuppe (saisonal) | + rote Linsen oder Kichererbsen-Topping |
| Nudelsuppe | + Tofu oder Ei, Proteinnudeln |
| Wirsing-Kartoffel-Eintopf | + weiße Bohnen oder Linsen |
| Gebratener Reis mit Hoisinsoße und Gemüse | + Tofu, Ei, Edamame |
| Pilzpfanne mit Spätzle oder Tagliatelle | + Tofu oder Seitan, Skyr in die Sauce (nur MO-Slot wegen Pilzen) |

### Wochenende / Familie
- Tortilla (spanisch) mit Gurkensalat
- Reibekuchen mit Gurkensalat und/oder Apfelmus
- Pfannkuchen (süß) – `suess`
- Kartoffelbrei mit Spinat und Ei
- Kartoffelbrei mit Fischstäbchen – Ausnahme Fisch
- Burritos, gefüllt mit Paprika, Süßkartoffel, Zwiebeln, Guacamole, Sour Cream, Salat, Tomaten, mit Käse überbacken (Option: schwarze Bohnen)
- Wraps mit Salat, Mais, Paprika, Gurke, Frischkäse, Kartoffeln, Sour Cream
- Spaghetti mit Kinder-Tomatensauce (gekauft) und Parmesan
- Flammkuchen (selbstgemacht) – `zuenftig`
- Pizza (selbstgemacht)
- Bauernfrühstück – `zuenftig`
- Schupfnudeln mit Gemüse
- Kartoffelgratin
- Vegetarische Burger
- Vegetarische Köttbullar (Greenforce) mit Soße, Preiselbeeren, Apfelmus und Kartoffeln – `zuenftig`
- Gebackener Camembert mit Preiselbeeren, Salat und Baguette – `zuenftig`
- Caesar Salad mit vegetarischen Chicken-Stripes

### Salate (saisonal)
- Melone-Feta-Salat (Sommer)
- Rote-Bete-Salat mit Feta
- Bunter Salat mit Parmesan, Möhren und Gurken

### Beilagen-Bausteine (eigene Kategorie, kombinierbar)
Gurkensalat, Süßkartoffelpüree, Ofengemüse, Parmesan-Chips, Apfelmus, Salat

### Variationen und neue Rezepte
Die Familie ist offen für neue Vorschläge. Neue Rezepte sollen sich an obigen Gerichten orientieren (Stilrichtungen: deutsche Hausmannskost, Tex-Mex, milde asiatische Küche, Suppen/Eintöpfe, Ofengerichte). Keine scharfen Gerichte für Familienslots.

---

## 7. Datenmodell (Vorschlag)

```yaml
# recipes/<slug>.yaml
slug: chili-sin-carne
name: Chili sin carne
core: true
slots: [MO, MI, SA]          # wo es eingeplant werden darf
tags: [lunchbox_ok, freezer_ok, kids_ok]
seasons: all                  # oder [9,10,11,12,1,2]
prep_minutes: 35
base_servings: 4              # Erwachsenenportionen
protein_per_serving_g: 32     # Schätzung
ingredients:
  - { item: kidneybohnen_dose, qty: 2, unit: dose }
  - { item: rote_linsen, qty: 150, unit: g }
  ...
steps: [...]
variants:
  family: { ... }             # optional abweichende Zutaten für Familien-Slot
```

```yaml
# ingredients.yaml – Stammdaten
kidneybohnen_dose:
  name: Kidneybohnen (Dose, 400 g)
  type: pantry
  department: Konserven
  pack: { qty: 1, unit: dose }
  shelf_days: 730
```

- Plan als JSON pro Monat (`plans/2026-12.json`) mit Slots, Rezept, Portionen.
- Rezepte und Zutaten als Dateien im Git-Repo, damit sie leicht editierbar und versioniert sind.

---

## 8. Architektur

- **Sprache:** Python 3.12.
- **Planer:** regelbasierter Scheduler (Constraint-Prüfung aus Abschnitt 3 und 4, gewichtete Zufallsauswahl, Favoriten-Gewichtung).
- **Optional: Claude API** für neue Rezeptvorschläge. Neue Rezepte landen in einer Review-Warteschlange (`recipes/_pending/`) und werden erst nach Freigabe eingeplant. Jeder Vorschlag läuft durch den Ausschluss- und Protein-Check.
- **Mail:** SMTP (Zugangsdaten als Secrets in Coolify), Empfänger konfigurierbar.
- **Deployment:** Container in Coolify auf dem Hetzner-Server, zwei Scheduled Tasks:
  - `generate-month`: am vorletzten Freitag des Monats, erzeugt den Plan für den Folgemonat und schickt eine Vorschau.
  - `send-weekly`: jeden Freitag 07:00 Europe/Berlin.
- **Konfiguration:** `config.yaml` (Personen, Portionsfaktoren, Protein-Ziel, Ausschlüsse, Wiederholungsregeln, Freezer, Empfänger).

---

## 9. Nice-to-have (v2)

- Bewertung per Link in der Mail (👍/👎), beeinflusst die Gewichtung.
- Tausch-Link „Gericht ersetzen“ für einzelne Slots.
- Vorratsbestand pflegen (Abschnitt 5.3).
- Export der Einkaufsliste in eine Einkaufs-App (z. B. Bring!).

---

## 10. Akzeptanzkriterien für v1

1. Ein generierter Monatsplan enthält keine ausgeschlossene Zutat (automatischer Test).
2. Alle MO/MI-Gerichte haben ≥ 30 g Protein pro Portion.
3. Kein Gericht öfter als 3× im Monat und nie zweimal innerhalb von 7 Tagen.
4. Jedes Wochenende hat mindestens ein süßes oder zünftiges Gericht (Vorgabe für den Planer, keine Sperre für Änderungen von Hand).
5. Wochenmail kommt freitags an, Frischeliste ist nach Abteilung sortiert und mengenmäßig aggregiert.
6. Vorratsliste summiert alle Pantry-Zutaten des Monats, auf Packungsgrößen gerundet.
