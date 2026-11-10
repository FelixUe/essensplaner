"""Einen fertigen Monatsplan von Hand ändern.

Der Planer würfelt einen Plan, der alle harten Regeln erfüllt. Was er nicht
weiß: dass am Samstag Besuch kommt oder dass auf Linsen gerade niemand Lust
hat. Dieses Modul erlaubt genau eine Änderung – ein Gericht in einem Slot
gegen ein anderes tauschen – und zwar so, dass der Plan danach dieselben
Prüfungen besteht wie ein frisch gewürfelter.

Zwei Schichten sichern das ab, dieselben wie beim Planen:

* :meth:`~essensplaner.planner.Planner.alternativen` liefert nur Gerichte,
  die in diesem Slot erlaubt wären. Die Oberfläche zeigt nichts anderes an.
* :func:`gericht_tauschen` prüft den geänderten Plan anschließend noch einmal
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
