"""Akzeptanzkriterium 1: Ein generierter Monatsplan enthält keine
ausgeschlossene Zutat.

Geprüft wird auf drei Ebenen: die Ausschlusslogik selbst, die Stammdaten im
Repo und die fertigen Pläne.
"""

from __future__ import annotations

import pytest

from essensplaner import rules
from essensplaner.config import Exclusion
from essensplaner.models import Ingredient, Recipe, RecipeIngredient
from essensplaner.rules import ausschluss_treffer, normalisieren

from .conftest import TEST_MONATE, TEST_SEEDS

# --------------------------------------------------------------- Textsuche


def test_normalisieren_vereinheitlicht_umlaute():
    assert normalisieren("Blauschimmelkäse") == normalisieren("Blauschimmelkaese")
    assert normalisieren("WEISSSCHIMMEL") == normalisieren("Weißschimmel")


@pytest.mark.parametrize(
    "text",
    [
        "Gorgonzola",
        "gorgonzola-sauce",
        "Roquefort AOP",
        "Stilton",
        "Cambozola",
        "Danablu",
        "Bavaria Blu",
        "Bergader Edelpilz",
        "Blue Cheese Dressing",
        "Blauschimmelkäse",
        "Blauschimmelkaese",
    ],
)
def test_blauschimmel_wird_erkannt(text, config):
    assert ausschluss_treffer(text, config.ausschluesse), f"{text!r} müsste auffallen"


@pytest.mark.parametrize(
    "text",
    [
        "Salbei",
        "salbei getrocknet",
        "Salbeiblätter",
        "Sage",
        "Butter-Salbei-Sauce",
    ],
)
def test_salbei_wird_erkannt(text, config):
    assert ausschluss_treffer(text, config.ausschluesse), f"{text!r} müsste auffallen"


@pytest.mark.parametrize(
    "text",
    [
        "Fenchel",
        "Fenchelknolle",
        "Fenchelgrün",
        "Fenchelsamen",
        "fennel seeds",
        "Finocchio",
        "Gewürzmischung mit Fenchelsaat",
    ],
)
def test_fenchel_wird_in_jeder_form_erkannt(text, config):
    assert ausschluss_treffer(text, config.ausschluesse), f"{text!r} müsste auffallen"


@pytest.mark.parametrize(
    "text",
    [
        "Camembert zum Backen",
        "Weißschimmelkäse",
        "Brie",
        "Gouda gerieben",
        "Kreuzkümmel",  # Kümmel ist erlaubt, nur Fenchel nicht
        "Kümmel ganz",
        "Blumenkohl",
        "Rosmarin",
        "Thymian",
    ],
)
def test_erlaubte_zutaten_schlagen_nicht_an(text, config):
    assert not ausschluss_treffer(text, config.ausschluesse), f"{text!r} ist erlaubt"


def test_ausnahme_gilt_nur_fuer_den_ausnahmebegriff():
    """Camembert ist erlaubt – 'Blauschimmel-Camembert' trotzdem nicht."""
    gruppe = (
        Exclusion(gruppe="blauschimmel", begriffe=("blauschimmel",), ausnahmen=("camembert",)),
    )
    assert not ausschluss_treffer("Camembert", gruppe)
    assert ausschluss_treffer("Blauschimmel-Camembert", gruppe)


# ------------------------------------------------------------- Stammdaten


def test_keine_zutat_im_repo_verletzt_die_ausschluesse(zutaten, config):
    verstoesse = [i for z in zutaten.values() for i in rules.pruefe_zutat(z, config)]
    assert not verstoesse, "\n".join(str(v) for v in verstoesse)


def test_kein_rezept_im_repo_verletzt_die_ausschluesse(rezepte, zutaten, config):
    verstoesse = [
        i for r in rezepte.values() for i in rules.pruefe_rezept_ausschluesse(r, zutaten, config)
    ]
    assert not verstoesse, "\n".join(str(v) for v in verstoesse)


def test_gewuerzmischung_ohne_freigabe_faellt_auf(config):
    """Mischungen sind nur erlaubt, wenn sie als fenchelfrei markiert sind."""
    mischung = Ingredient(
        slug="garam_masala",
        name="Garam Masala",
        type="pantry",
        department="Gewürze",
        pack_qty=40,
        pack_unit="g",
        gewuerzmischung=True,
        fenchelfrei=None,
    )
    codes = [i.code for i in rules.pruefe_zutat(mischung, config)]
    assert "gewuerzmischung_ungeprueft" in codes


def test_als_fenchelfrei_markierte_mischung_ist_erlaubt(config):
    mischung = Ingredient(
        slug="curry_fenchelfrei",
        name="Currypulver",
        type="pantry",
        department="Gewürze",
        pack_qty=40,
        pack_unit="g",
        gewuerzmischung=True,
        fenchelfrei=True,
    )
    assert not rules.pruefe_zutat(mischung, config)


def test_ausgeschlossene_zutat_in_einer_variante_faellt_auf(zutaten, config):
    """Auch Varianten werden geprüft, nicht nur die Basisfassung."""
    gorgonzola = Ingredient(
        slug="gorgonzola",
        name="Gorgonzola",
        type="fresh",
        department="Käse",
        pack_qty=150,
        pack_unit="g",
    )
    erweitert = dict(zutaten)
    erweitert["gorgonzola"] = gorgonzola

    from essensplaner.models import Variant

    rezept = Recipe(
        slug="test-nudeln",
        name="Nudeln mit Sauce",
        core=False,
        slots=("SA",),
        tags=(),
        seasons=tuple(range(1, 13)),
        prep_minutes=20,
        base_servings=4,
        protein_per_serving_g=20,
        ingredients=(RecipeIngredient(item="penne", qty=400, unit="g"),),
        steps=("Nudeln kochen.",),
        variants={
            "family": Variant(
                name="family",
                zusatz=(RecipeIngredient(item="gorgonzola", qty=150, unit="g"),),
                protein_per_serving_g=25,
            )
        },
    )
    codes = [i.code for i in rules.pruefe_rezept_ausschluesse(rezept, erweitert, config)]
    assert "ausschluss" in codes


# ------------------------------------------------------------------ Pläne


@pytest.mark.parametrize("monat", TEST_MONATE)
@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_plan_enthaelt_keine_ausgeschlossene_zutat(
    mach_plan, rezepte, zutaten, config, monat, seed
):
    plan = mach_plan(monat, seed)
    verstoesse = [
        i
        for i in rules.pruefe_plan(plan, rezepte, zutaten, config)
        if i.code in ("ausschluss", "gewuerzmischung_ungeprueft")
    ]
    assert not verstoesse, "\n".join(str(v) for v in verstoesse)
