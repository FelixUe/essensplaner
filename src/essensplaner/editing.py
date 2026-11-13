"""Einen fertigen Monatsplan von Hand ändern.

Der Planer würfelt einen Plan, der alle harten Regeln erfüllt. Was er nicht
weiß: dass am Samstag Besuch kommt oder dass auf Linsen gerade niemand Lust
hat. Dieses Modul erlaubt genau eine Änderung – ein Gericht in einem Slot
gegen ein anderes tauschen – und zwar so, dass der Plan danach dieselben
Prüfungen besteht wie ein frisch gewürfelter.

Zwei Arten von Änderung gibt es: ein Gericht gegen ein anderes tauschen
(:func:`gericht_tauschen`) und zwei geplante Gerichte die Plätze tauschen
lassen (:func:`plaetze_tauschen`) – das Verschieben innerhalb des Monats.

Zwei Schichten sichern beides ab, dieselben wie beim Planen:

* :meth:`~essensplaner.planner.Planner.alternativen` liefert nur Gerichte,
  die in diesem Slot erlaubt wären, und
  :meth:`~essensplaner.planner.Planner.tauschpartner` nur Termine, mit denen
  sich die Plätze tauschen lassen. Die Oberfläche zeigt nichts anderes an.
* Beide Funktionen prüfen den geänderten Plan anschließend noch einmal
  vollständig. Erst das fängt die Regeln, die nicht an einem einzelnen Slot
  hängen – etwa dass jedes Wochenende ein süßes oder zünftiges Gericht
  braucht. Schlägt die Prüfung an, bleibt der alte Plan stehen.
"""

from __future__ import annotations

import copy
from datetime import date

from .models import MonthPlan, PlannedMeal
from .planner import Planner
from .rules import Issue, nur_fehler, pruefe_plan


class AenderungsFehler(Exception):
    """Eine gewünschte Änderung wurde nicht übernommen."""

    def __init__(self, nachricht: str, issues: list[Issue] | None = None) -> None:
        super().__init__(nachricht)
        self.nachricht = nachricht
        self.issues = issues or []

    def __str__(self) -> str:
        if not self.issues:
            return self.nachricht
        return self.nachricht + " – " + "; ".join(i.nachricht for i in self.issues)


def finde_mahlzeit(plan: MonthPlan, datum: date, slot: str) -> PlannedMeal | None:
    for woche in plan.weeks:
        for meal in woche.meals:
            if meal.slot == slot and meal.datum == datum:
                return meal
    return None


def gericht_tauschen(
    plan: MonthPlan,
    datum: date,
    slot: str,
    neuer_slug: str,
    planner: Planner,
) -> MonthPlan:
    """Ein Gericht austauschen und den geänderten Plan zurückgeben.

    Der übergebene Plan bleibt unangetastet: geändert wird eine Kopie, und
    die gibt es nur zurück, wenn sie sauber durch die Prüfung kommt. So kann
    eine abgelehnte Änderung keinen halb geänderten Plan hinterlassen.
    """
    alt = finde_mahlzeit(plan, datum, slot)
    if alt is None:
        raise AenderungsFehler(f"Im Plan steht am {datum} kein Slot {slot}")

    rezept = planner.rezepte.get(neuer_slug)
    if rezept is None:
        # Entweder Tippfehler oder ein Rezept, das der Planer wegen eigener
        # Regelverstöße gar nicht erst in den Topf genommen hat.
        grund = (
            " – es verstößt selbst gegen eine harte Regel"
            if neuer_slug in planner.abgelehnt
            else ""
        )
        raise AenderungsFehler(f"Rezept {neuer_slug} steht nicht zur Verfügung{grund}")

    if rezept.slug == alt.recipe_slug:
        raise AenderungsFehler(f"{rezept.name} steht dort bereits")

    erlaubt = {r.slug for r in planner.alternativen(plan, datum, slot)}
    if rezept.slug not in erlaubt:
        raise AenderungsFehler(f"{rezept.name} ist für {slot} am {datum} nicht zulässig")

    neuer_plan = copy.deepcopy(plan)
    ziel_woche = neuer_plan.woche_fuer(datum)
    if ziel_woche is None:  # pragma: no cover - finde_mahlzeit hat schon getroffen
        raise AenderungsFehler(f"Keine Woche im Plan enthält den {datum}")

    neue_mahlzeit = planner.mahlzeit_bauen(rezept, slot, datum)
    ziel_woche.meals = [
        neue_mahlzeit if (m.slot == slot and m.datum == datum) else m for m in ziel_woche.meals
    ]
    ziel_woche.meals.sort(key=lambda m: m.datum)

    fehler = nur_fehler(pruefe_plan(neuer_plan, planner.rezepte, planner.zutaten, planner.config))
    if fehler:
        raise AenderungsFehler(
            f"{rezept.name} würde den Monatsplan ungültig machen",
            fehler,
        )

    return neuer_plan


def plaetze_tauschen(
    plan: MonthPlan,
    datum_a: date,
    slot_a: str,
    datum_b: date,
    slot_b: str,
    planner: Planner,
) -> MonthPlan:
    """Zwei geplante Gerichte die Plätze tauschen lassen.

    Das ist das Verschieben innerhalb des Monats: Samstag und Montag
    wechseln, weil am Samstag Besuch kommt. Es bleibt bei einem Tausch und
    wird kein freies Verschieben – ein Slot darf nicht leer bleiben, und
    jedes Gericht landet auf einem Platz, den es haben darf.

    Portionen, Variante und Tagesabdeckung richten sich danach neu nach dem
    neuen Slot: ein Gericht, das von Samstag auf Montag rückt, wird für vier
    Portionen gekocht und deckt zwei Tage ab.
    """
    a = planner.mahlzeit_am(plan, datum_a, slot_a)
    if a is None:
        raise AenderungsFehler(f"Im Plan steht am {datum_a} kein Slot {slot_a}")
    b = planner.mahlzeit_am(plan, datum_b, slot_b)
    if b is None:
        raise AenderungsFehler(f"Im Plan steht am {datum_b} kein Slot {slot_b}")
    if a.slot == b.slot and a.datum == b.datum:
        raise AenderungsFehler("Ein Gericht kann nicht mit sich selbst tauschen")

    rezept_a = planner.rezepte.get(a.recipe_slug)
    rezept_b = planner.rezepte.get(b.recipe_slug)
    if rezept_a is None or rezept_b is None:
        fehlend = a.recipe_name if rezept_a is None else b.recipe_name
        raise AenderungsFehler(
            f"{fehlend} steht nicht mehr zur Verfügung – gestrichen oder gelöscht"
        )
    if not planner.tausch_moeglich(plan, a, b):
        raise AenderungsFehler(
            f"{a.recipe_name} und {b.recipe_name} können die Plätze nicht tauschen"
        )

    neuer_plan = copy.deepcopy(plan)
    neu_a = planner.mahlzeit_bauen(rezept_b, a.slot, a.datum)
    neu_b = planner.mahlzeit_bauen(rezept_a, b.slot, b.datum)
    for woche in neuer_plan.weeks:
        woche.meals = [
            neu_a
            if (m.slot == a.slot and m.datum == a.datum)
            else neu_b
            if (m.slot == b.slot and m.datum == b.datum)
            else m
            for m in woche.meals
        ]
        woche.meals.sort(key=lambda m: m.datum)

    fehler = nur_fehler(pruefe_plan(neuer_plan, planner.rezepte, planner.zutaten, planner.config))
    if fehler:
        raise AenderungsFehler(
            f"{a.recipe_name} und {b.recipe_name} zu tauschen würde den Monatsplan ungültig machen",
            fehler,
        )

    return neuer_plan
