"""Ein Gericht im fertigen Plan tauschen.

Der Planer würfelt einen regelkonformen Monat. Wer von Hand eingreift, darf
ihn dabei nicht kaputt machen – weder durch ein Gericht, das in diesem Slot
nichts zu suchen hat, noch durch eines, das erst im Zusammenspiel mit dem
Rest eine Regel bricht.
"""

from __future__ import annotations

import pytest

from essensplaner import rules
from essensplaner.editing import AenderungsFehler, finde_mahlzeit, gericht_tauschen

from .conftest import TEST_MONATE

# ------------------------------------------------------------ Alternativen


@pytest.mark.parametrize("monat", TEST_MONATE[:4])
def test_alternativen_sind_im_slot_zulaessig(planner, mach_plan, monat):
    plan = mach_plan(monat)
    for meal in plan.alle_meals:
        for rezept in planner.alternativen(plan, meal.datum, meal.slot):
            assert rezept.erlaubt_in_slot(meal.slot)
            assert rezept.in_saison(meal.datum.month)


def test_alternativen_enthalten_das_aktuelle_gericht_nicht(planner, plan):
    for meal in plan.alle_meals:
        slugs = {r.slug for r in planner.alternativen(plan, meal.datum, meal.slot)}
        assert meal.recipe_slug not in slugs


def test_alternativen_wiederholen_nichts_aus_derselben_woche(planner, plan):
    for woche in plan.weeks:
        in_woche = {m.recipe_slug for m in woche.meals}
        for meal in woche.meals:
            slugs = {r.slug for r in planner.alternativen(plan, meal.datum, meal.slot)}
            assert not (slugs & in_woche)


def test_alternativen_halten_den_mindestabstand(planner, plan, config):
    for meal in plan.alle_meals:
        for rezept in planner.alternativen(plan, meal.datum, meal.slot):
            termine = [
                m.datum for m in plan.alle_meals if m.recipe_slug == rezept.slug and m is not meal
            ]
            for frueher in termine:
                abstand = abs((meal.datum - frueher).days)
                assert abstand >= config.min_abstand_tage, (
                    f"{rezept.slug} läge nur {abstand} Tage vom {frueher} entfernt"
                )


def test_vorkoch_slots_bieten_nur_proteinreiches_an(planner, plan, config):
    """In MO und MI muss auch die Alternative das Minimum erreichen."""
    for meal in plan.alle_meals:
        if meal.slot not in config.protein_slots:
            continue
        for rezept in planner.alternativen(plan, meal.datum, meal.slot):
            variante = rezept.variant_fuer(meal.slot, config.protein_slots)
            assert rezept.protein_fuer(variante) >= config.protein_min_g


# ----------------------------------------------------------------- Tausch


def _erster_tausch(planner, plan):
    """Ein Slot mit mindestens einer Alternative, plus diese Alternative."""
    for meal in plan.alle_meals:
        alternativen = planner.alternativen(plan, meal.datum, meal.slot)
        if alternativen:
            return meal, alternativen[0]
    raise AssertionError("Kein Slot im Plan hat eine Alternative")


def test_tausch_setzt_das_neue_gericht_ein(planner, plan):
    meal, neu = _erster_tausch(planner, plan)
    geaendert = gericht_tauschen(plan, meal.datum, meal.slot, neu.slug, planner)
    danach = finde_mahlzeit(geaendert, meal.datum, meal.slot)
    assert danach is not None
    assert danach.recipe_slug == neu.slug
    assert danach.recipe_name == neu.name


def test_tausch_laesst_den_alten_plan_in_ruhe(planner, plan):
    meal, neu = _erster_tausch(planner, plan)
    vorher = meal.recipe_slug
    gericht_tauschen(plan, meal.datum, meal.slot, neu.slug, planner)
    assert finde_mahlzeit(plan, meal.datum, meal.slot).recipe_slug == vorher


def test_geaenderter_plan_besteht_die_volle_pruefung(planner, plan, rezepte, zutaten, config):
    meal, neu = _erster_tausch(planner, plan)
    geaendert = gericht_tauschen(plan, meal.datum, meal.slot, neu.slug, planner)
    assert rules.nur_fehler(rules.pruefe_plan(geaendert, rezepte, zutaten, config)) == []


def test_tausch_uebernimmt_portionen_variante_und_tage(planner, plan, config):
    meal, neu = _erster_tausch(planner, plan)
    geaendert = gericht_tauschen(plan, meal.datum, meal.slot, neu.slug, planner)
    danach = finde_mahlzeit(geaendert, meal.datum, meal.slot)
    assert danach.portionen == config.slot_portionen(meal.slot)
    assert danach.tage == config.slot_tage(meal.slot)
    assert danach.variant == neu.variant_fuer(meal.slot, config.protein_slots)


def test_tausch_loest_die_gefriertruhe(planner, mach_plan):
    """Von Hand gewählt heißt gekocht, nicht aufgetaut."""
    plan = mach_plan("2027-01")
    meal, neu = _erster_tausch(planner, plan)
    geaendert = gericht_tauschen(plan, meal.datum, meal.slot, neu.slug, planner)
    danach = finde_mahlzeit(geaendert, meal.datum, meal.slot)
    assert danach.aus_gefriertruhe is False
    assert danach.batch_cook is False


# -------------------------------------------------------------- Ablehnung


def test_unbekanntes_rezept_wird_abgelehnt(planner, plan):
    meal = plan.alle_meals[0]
    with pytest.raises(AenderungsFehler, match="steht nicht zur Verfügung"):
        gericht_tauschen(plan, meal.datum, meal.slot, "gibt-es-nicht", planner)


def test_dasselbe_gericht_wird_abgelehnt(planner, plan):
    meal = plan.alle_meals[0]
    with pytest.raises(AenderungsFehler, match="steht dort bereits"):
        gericht_tauschen(plan, meal.datum, meal.slot, meal.recipe_slug, planner)


def test_beilage_ohne_slot_wird_abgelehnt(planner, plan):
    meal = plan.alle_meals[0]
    with pytest.raises(AenderungsFehler, match="nicht zulässig"):
        gericht_tauschen(plan, meal.datum, meal.slot, "beilage-salat", planner)


def test_gericht_aus_derselben_woche_wird_abgelehnt(planner, plan):
    woche = plan.weeks[0]
    ziel, quelle = woche.meals[0], woche.meals[1]
    with pytest.raises(AenderungsFehler, match="nicht zulässig"):
        gericht_tauschen(plan, ziel.datum, ziel.slot, quelle.recipe_slug, planner)


def test_unbekannter_slot_wird_abgelehnt(planner, plan):
    meal = plan.alle_meals[0]
    with pytest.raises(AenderungsFehler, match="kein Slot"):
        gericht_tauschen(plan, meal.datum, "XX", "pizza", planner)
