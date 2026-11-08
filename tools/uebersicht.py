#!/usr/bin/env python3
"""Rezepte und Zutaten als JSON für die Durchsicht exportieren.

Die Rezepte stehen in 52 YAML-Dateien. Zum Durchsehen ist das unpraktisch:
Mengen beziehen sich auf ``base_servings``, die Protein-Variante steht als
Differenz zur Basis (``zusatz``/``entfernt``/``ersetzt``) und Haltbarkeiten
liegen in den Zutaten. Dieses Skript löst all das auf und schreibt eine
Datei, die ``tools/uebersicht.html`` anzeigt.

Mit exportiert wird, was beim Durchsehen zählt und nicht in der YAML steht:

* die aus den Zutaten nachgerechnete Proteinmenge neben der Angabe,
* welche Zutaten empfindlich sind (und damit den MI-Slot sperren),
* wie oft ein Gericht in einem Beispieljahr tatsächlich auf dem Tisch steht.

Aufruf::

    python tools/uebersicht.py                    # schreibt tools/rezepte.json
    python tools/uebersicht.py --out /tmp/x.json
    python tools/uebersicht.py --kein-jahr        # ohne Häufigkeiten (schneller)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "src"))

from essensplaner import nutrition, units  # noqa: E402
from essensplaner.config import load_config  # noqa: E402
from essensplaner.models import Ingredient, Recipe  # noqa: E402
from essensplaner.planner import Planner, PlanungsFehler  # noqa: E402
from essensplaner.rules import empfindliche_zutaten, nur_fehler, pruefe_rezept  # noqa: E402
from essensplaner.runner import Kontext  # noqa: E402
from essensplaner.shopping import ABTEILUNG_REIHENFOLGE  # noqa: E402

#: Jahr, über das die Häufigkeiten gerechnet werden. Ein fester Seed, damit
#: zwei Exporte dieselben Zahlen liefern – sonst wirkt jede Durchsicht wie
#: eine Änderung.
BEISPIEL_JAHR = 2027
BEISPIEL_SEED = 42

#: Stelle in tools/uebersicht.html, an der die Daten landen.
PLATZHALTER = "__DATEN__"

MONATSNAMEN = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)


def zutaten_zeilen(
    rezept: Recipe,
    zutaten: dict[str, Ingredient],
    variante: str | None,
) -> list[dict[str, Any]]:
    """Eine Zutatenliste, wie sie in der Mail stehen würde."""
    zeilen = []
    for zeile in rezept.zutaten_fuer(variante):
        zutat = zutaten.get(zeile.item)
        zeilen.append(
            {
                "slug": zeile.item,
                "name": zutat.name if zutat else zeile.item,
                "menge": units.formatiere_rezept(zeile.qty, zeile.unit),
                "abteilung": zutat.department if zutat else "?",
                "optional": zeile.optional,
                "hinweis": zeile.hinweis,
                "empfindlich": bool(zutat and zutat.empfindlich),
                "grundzutat": bool(zutat and zutat.grundzutat),
                "haltbar_tage": zutat.shelf_days if zutat else 0,
                # Ohne diesen Wert kann das Protein der Zeile nicht
                # nachgerechnet werden – beim Durchsehen gut zu wissen.
                "protein_bekannt": bool(zutat and zutat.protein_per_100 > 0),
                # EL, TL, Prise: liefern ohnehin kein Protein, der fehlende
                # Wert ist dort keine Lücke in den Stammdaten.
                "kleinmenge": units.ist_kleinmenge(zeile.unit),
            }
        )
    return zeilen


def variante_block(
    rezept: Recipe,
    zutaten: dict[str, Ingredient],
    variante: str | None,
) -> dict[str, Any]:
    angabe, gerechnet, delta = nutrition.abweichung(rezept, zutaten, variante)
    return {
        "name": variante or "basis",
        "hinweis": rezept.variants[variante].hinweis if variante else "",
        "protein_angabe": round(angabe, 1),
        "protein_gerechnet": round(gerechnet, 1),
        "protein_abweichung": round(delta, 3),
        "zutaten": zutaten_zeilen(rezept, zutaten, variante),
        "steps": list(rezept.steps_fuer(variante)),
    }


def rezept_block(
    rezept: Recipe,
    zutaten: dict[str, Ingredient],
    config: Any,
    haeufigkeit: dict[str, Any],
) -> dict[str, Any]:
    varianten = [variante_block(rezept, zutaten, None)]
    for name in sorted(rezept.variants):
        varianten.append(variante_block(rezept, zutaten, name))

    # Empfindliche Zutaten sperren den MI-Slot: fünf Tage nach dem Einkauf
    # ist Blattsalat nichts mehr, was man servieren will.
    empfindlich = sorted(
        {
            name
            for variante in (None, *sorted(rezept.variants))
            for name in empfindliche_zutaten(rezept, variante, zutaten)
        }
    )

    hinweise = [str(i) for i in pruefe_rezept(rezept, zutaten, config)]
    fehler = [str(i) for i in nur_fehler(pruefe_rezept(rezept, zutaten, config))]

    return {
        "slug": rezept.slug,
        "name": rezept.name,
        "core": rezept.core,
        "slots": list(rezept.slots),
        "tags": list(rezept.tags),
        "seasons": list(rezept.seasons),
        "ganzjaehrig": len(rezept.seasons) == 12,
        "saison_namen": [MONATSNAMEN[m - 1] for m in sorted(rezept.seasons)],
        "prep_minutes": rezept.prep_minutes,
        "base_servings": rezept.base_servings,
        "varianten": varianten,
        "empfindlich": empfindlich,
        "fehler": fehler,
        "hinweise": [h for h in hinweise if h not in fehler],
        "haeufigkeit": haeufigkeit.get(rezept.slug, {"gesamt": 0, "slots": {}}),
        "quelle": Path(rezept.quelle).name if rezept.quelle else "",
    }


def jahres_haeufigkeit(kontext: Kontext) -> tuple[dict[str, Any], list[str]]:
    """Wie oft jedes Gericht in einem Beispieljahr geplant wird.

    Das ist die Probe aufs Exempl: ein Rezept, das nie erscheint, ist
    entweder zu eng auf Slots oder Saison geschnitten – das sieht man an
    den YAML-Dateien nicht.
    """
    planer = Planner(kontext.rezepte, kontext.zutaten, kontext.config)
    gesamt: Counter[str] = Counter()
    pro_slot: dict[str, Counter[str]] = {}
    probleme: list[str] = []

    for monat in range(1, 13):
        kennung = f"{BEISPIEL_JAHR}-{monat:02d}"
        try:
            plan = planer.plan_monat(kennung, seed=BEISPIEL_SEED)
        except PlanungsFehler as fehler:
            probleme.append(f"{kennung}: {fehler}")
            continue
        for meal in plan.alle_meals:
            gesamt[meal.recipe_slug] += 1
            pro_slot.setdefault(meal.recipe_slug, Counter())[meal.slot] += 1

    ergebnis = {
        slug: {"gesamt": gesamt.get(slug, 0), "slots": dict(pro_slot.get(slug, {}))}
        for slug in kontext.rezepte
    }
    return ergebnis, probleme


def baue_seite(daten: dict[str, Any], ziel: Path) -> Path:
    """Die Vorlage mit den Daten füllen.

    Die Daten stehen als JSON im Script-Block der Seite, nicht in einer
    zweiten Datei: so zeigt die Seite sofort alles an und braucht keinen
    Server, der eine JSON-Datei ausliefert.
    """
    vorlage = WURZEL / "tools" / "uebersicht.html"
    if not vorlage.is_file():
        raise SystemExit(f"Vorlage fehlt: {vorlage}")
    text = vorlage.read_text(encoding="utf-8")
    if PLATZHALTER not in text:
        raise SystemExit(f"Platzhalter {PLATZHALTER} steht nicht in {vorlage}")
    roh = json.dumps(daten, ensure_ascii=False, separators=(",", ":"))
    # "<" maskieren, damit kein Zutatenname den Script-Block beenden kann.
    roh = roh.replace("<", chr(92) + "u003c")
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(text.replace(PLATZHALTER, roh), encoding="utf-8", newline=chr(10))
    return ziel


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=WURZEL / "tools" / "rezepte.json",
        help="Zieldatei (Standard: tools/rezepte.json)",
    )
    parser.add_argument("--config", type=Path, default=None, help="Pfad zur config.yaml")
    parser.add_argument(
        "--html",
        type=Path,
        nargs="?",
        const=WURZEL / "tools" / "uebersicht.build.html",
        default=None,
        help="Zusaetzlich die fertige Seite schreiben (Vorlage: tools/uebersicht.html)",
    )
    parser.add_argument(
        "--kein-jahr",
        action="store_true",
        help="Häufigkeiten nicht rechnen – deutlich schneller",
    )
    args = parser.parse_args()

    config = load_config(args.config or WURZEL / "config.yaml")
    # streng=False: der Export soll auch dann noch etwas anzeigen, wenn ein
    # Rezept gerade eine Regel bricht. Der Verstoß steht dann am Rezept.
    kontext = Kontext.laden(config, streng=False)

    if args.kein_jahr:
        haeufigkeit: dict[str, Any] = {}
        probleme: list[str] = []
    else:
        haeufigkeit, probleme = jahres_haeufigkeit(kontext)

    rezepte = [
        rezept_block(r, kontext.zutaten, config, haeufigkeit)
        for r in sorted(kontext.rezepte.values(), key=lambda r: r.name.lower())
    ]

    daten = {
        "regeln": {
            "protein_min_g": config.protein_min_g,
            "protein_slots": list(config.protein_slots),
            "frischware_slots": list(config.frischware_slots),
            "max_pro_monat": config.max_pro_monat,
            "min_abstand_tage": config.min_abstand_tage,
            "core_anteil": config.core_anteil,
            "erwachsene": config.erwachsene,
            "kinder": config.kinder,
            "ausschluesse": [a.gruppe for a in config.ausschluesse],
            "abteilungen": list(ABTEILUNG_REIHENFOLGE),
            "slots": {
                name: {
                    "kochtag": config.slot_kochtag(name),
                    "deckt_ab": config.slot_deckt_ab(name),
                    "portionen": config.slot_portionen(name),
                }
                for name in config.slot_namen
            },
        },
        "beispieljahr": {
            "jahr": BEISPIEL_JAHR,
            "seed": BEISPIEL_SEED,
            "gerechnet": not args.kein_jahr,
            "probleme": probleme,
        },
        "zutaten_gesamt": len(kontext.zutaten),
        "rezepte": rezepte,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(daten, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    if args.html is not None:
        seite = baue_seite(daten, args.html)
        print(f"Seite geschrieben -> {seite}")

    print(f"{len(rezepte)} Rezepte, {len(kontext.zutaten)} Zutaten -> {args.out}")
    for p in probleme:
        print(f"  Hinweis: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
