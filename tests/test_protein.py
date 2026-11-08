"""Akzeptanzkriterium 2: Alle MO/MI-Gerichte haben mindestens 30 g Protein
je Erwachsenenportion.

Zusätzlich wird geprüft, dass die Angaben in den Rezepten zu den Zutaten
passen – eine Zahl, die niemand nachrechnet, ist keine Zusicherung.
"""

from __future__ import annotations

import pytest

from essensplaner import nutrition, rules
from essensplaner.models import Ingredient

from .conftest import TEST_MONATE, TEST_SEEDS


def test_protein_minimum_ist_konfiguriert(config):
    assert config.protein_min_g == 30
    assert set(config.protein_slots) == {"MO", "MI"}


def test_jedes_rezept_erreicht_in_seinen_protein_slots_das_minimum(rezepte, config):
    """Ein Rezept, das für MO oder MI freigegeben ist, muss das Minimum dort
    erreichen – notfalls über seine Protein-Variante."""
    verstoesse = [i for r in rezepte.values() for i in rules.pruefe_rezept_protein(r, config)]
    assert not verstoesse, "\n".join(str(v) for v in verstoesse)


def test_proteinangaben_passen_zu_den_zutaten(rezepte, zutaten):
    meldungen = nutrition.pruefe_angaben(rezepte, zutaten)
    assert not meldungen, "\n".join(meldungen) + (
        "\n\nMit 'python tools/protein_sync.py --write' übernehmen."
    )


def test_nachrechnung_beruecksichtigt_stueckgewichte():
    """Ohne Stückgewicht lässt sich 'eine Dose' nicht in Protein umrechnen."""
    mit_gewicht = Ingredient(
        slug="bohnen",
        name="Bohnen",
        type="pantry",
        department="Konserven",
        pack_qty=1,
        pack_unit="Dose",
        protein_per_100=8.0,
        stk_gewicht_g=240,
    )
    ohne_gewicht = Ingredient(
        slug="bohnen",
        name="Bohnen",
        type="pantry",
        department="Konserven",
        pack_qty=1,
        pack_unit="Dose",
        protein_per_100=8.0,
    )
    assert nutrition.protein_einer_zeile(2, "Dose", mit_gewicht) == pytest.approx(38.4)
    assert nutrition.protein_einer_zeile(2, "Dose", ohne_gewicht) == 0.0


def test_loeffelmengen_zaehlen_nicht_als_proteinquelle():
    oel = Ingredient(
        slug="oel",
        name="Öl",
        type="pantry",
        department="Trockenware",
        pack_qty=1,
        pack_unit="l",
        protein_per_100=0.0,
    )
    assert nutrition.protein_einer_zeile(3, "EL", oel) == 0.0


def test_jedes_rezept_hat_nachrechenbares_protein(rezepte, zutaten):
    """Jede Zutat mit Proteinbeitrag braucht Stammdaten, sonst ist die
    Nachrechnung wertlos."""
    ohne = [r.slug for r in rezepte.values() if nutrition.protein_berechnet(r, zutaten) <= 0]
    assert not ohne, f"Protein nicht nachrechenbar: {ohne}"


@pytest.mark.parametrize("monat", TEST_MONATE)
@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_alle_vorkoch_slots_im_plan_erreichen_30g(mach_plan, rezepte, config, monat, seed):
    plan = mach_plan(monat, seed)
    geprueft = 0
    for meal in plan.alle_meals:
        if meal.slot not in config.protein_slots:
            continue
        geprueft += 1
        rezept = rezepte[meal.recipe_slug]
        protein = rezept.protein_fuer(meal.variant)
        assert protein >= config.protein_min_g, (
            f"{rezept.name} am {meal.datum} ({meal.slot}, Variante "
            f"{meal.variant or 'basis'}): nur {protein:.0f} g"
        )
    assert geprueft >= 8, "Es müssten mindestens 8 Vorkoch-Slots im Monat sein"


@pytest.mark.parametrize("monat", TEST_MONATE)
def test_nachgerechnetes_protein_im_plan_erreicht_das_minimum(
    mach_plan, rezepte, zutaten, config, monat
):
    """Nicht nur die Angabe, auch die Nachrechnung aus den Zutaten muss
    das Minimum tragen."""
    plan = mach_plan(monat, 42)
    for meal in plan.alle_meals:
        if meal.slot not in config.protein_slots:
            continue
        rezept = rezepte[meal.recipe_slug]
        gerechnet = nutrition.protein_berechnet(rezept, zutaten, meal.variant)
        assert gerechnet >= config.protein_min_g * 0.95, (
            f"{rezept.name} ({meal.slot}): nachgerechnet nur {gerechnet:.0f} g"
        )
