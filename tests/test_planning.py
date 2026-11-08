"""Akzeptanzkriterien 3 und 4 sowie die übrigen Planungsregeln.

3. Kein Gericht öfter als 3× im Monat und nie zweimal innerhalb von 7 Tagen.
4. Jedes Wochenende hat mindestens ein süßes oder zünftiges Gericht.

Dazu: Saisonalität, Haltbarkeit der Frischware, Fischstäbchen-Obergrenze,
Slot-Zulässigkeit und Portionsberechnung.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

import pytest

from essensplaner import kalender, rules
from essensplaner.planner import Planner

from .conftest import TEST_MONATE, TEST_SEEDS

# ------------------------------------------------------- Gesamtabnahme


@pytest.mark.parametrize("monat", TEST_MONATE)
@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_plan_verletzt_keine_harte_regel(mach_plan, rezepte, zutaten, config, monat, seed):
    """Die Abnahme, die auch der Runner vor dem Speichern fährt."""
    plan = mach_plan(monat, seed)
    fehler = rules.nur_fehler(rules.pruefe_plan(plan, rezepte, zutaten, config))
    assert not fehler, "\n".join(str(f) for f in fehler)


# ------------------------------------------------ Kriterium 3: Wiederholung


@pytest.mark.parametrize("monat", TEST_MONATE)
@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_kein_gericht_oefter_als_dreimal_im_monat(mach_plan, config, monat, seed):
    plan = mach_plan(monat, seed)
    zaehler: dict[str, int] = defaultdict(int)
    for meal in plan.alle_meals:
        zaehler[meal.recipe_slug] += 1
    zu_oft = {slug: n for slug, n in zaehler.items() if n > config.max_pro_monat}
    assert not zu_oft, f"zu häufig geplant: {zu_oft}"


@pytest.mark.parametrize("monat", TEST_MONATE)
@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_mindestens_sieben_tage_zwischen_zwei_gleichen_gerichten(mach_plan, config, monat, seed):
    plan = mach_plan(monat, seed)
    termine: dict[str, list[date]] = defaultdict(list)
    for meal in plan.alle_meals:
        termine[meal.recipe_slug].append(meal.datum)
    for slug, tage in termine.items():
        tage.sort()
        for frueher, spaeter in zip(tage, tage[1:], strict=False):
            abstand = (spaeter - frueher).days
            assert abstand >= config.min_abstand_tage, (
                f"{slug}: {frueher} und {spaeter} liegen nur {abstand} Tage auseinander"
            )


@pytest.mark.parametrize("monat", TEST_MONATE)
@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_kein_gericht_zweimal_in_derselben_woche(mach_plan, monat, seed):
    plan = mach_plan(monat, seed)
    for woche in plan.weeks:
        slugs = [m.recipe_slug for m in woche.meals]
        assert len(slugs) == len(set(slugs)), (
            f"Woche ab {woche.start} enthält ein Gericht doppelt: {slugs}"
        )


# -------------------------------------------------- Kriterium 4: Wochenende


@pytest.mark.parametrize("monat", TEST_MONATE)
@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_jedes_wochenende_hat_ein_suesses_oder_zuenftiges_gericht(
    mach_plan, rezepte, config, monat, seed
):
    plan = mach_plan(monat, seed)
    for woche in plan.weeks:
        wochenende = [m for m in woche.meals if m.slot in ("SA", "SO")]
        assert wochenende, f"Woche ab {woche.start} hat keine Wochenendslots"
        tags = {t for m in wochenende for t in rezepte[m.recipe_slug].tags}
        assert tags & set(config.wochenende_pflicht_tags), (
            f"Wochenende ab {woche.start} ohne {config.wochenende_pflicht_tags}: "
            + ", ".join(m.recipe_name for m in wochenende)
        )


# ------------------------------------------------------------ Saisonalität


@pytest.mark.parametrize("monat", TEST_MONATE)
@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_kein_gericht_ausserhalb_seiner_saison(mach_plan, rezepte, monat, seed):
    plan = mach_plan(monat, seed)
    for meal in plan.alle_meals:
        rezept = rezepte[meal.recipe_slug]
        assert rezept.in_saison(meal.datum.month), (
            f"{rezept.name} am {meal.datum} (Monat {meal.datum.month}), Saison: {rezept.seasons}"
        )


def test_sommersalat_wird_im_winter_nicht_geplant(mach_plan, rezepte):
    """Melone-Feta ist auf Juni bis August begrenzt."""
    assert rezepte["melone-feta-salat"].seasons == (6, 7, 8)
    for monat in ("2026-12", "2027-01", "2027-02"):
        plan = mach_plan(monat, 42)
        slugs = {m.recipe_slug for m in plan.alle_meals}
        assert "melone-feta-salat" not in slugs


def test_kuerbissuppe_nur_in_der_kuerbiszeit(rezepte):
    assert set(rezepte["kuerbissuppe"].seasons) == {9, 10, 11, 12, 1, 2}


# ------------------------------------------------------------- Haltbarkeit


@pytest.mark.parametrize("monat", TEST_MONATE)
@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_empfindliche_frischware_nie_im_mittwochsslot(
    mach_plan, rezepte, zutaten, config, monat, seed
):
    """Der MI-Slot liegt fünf Tage nach dem Einkauf – Blattsalat, Kräuter,
    Avocado, Pilze und Beeren haben dort nichts zu suchen."""
    plan = mach_plan(monat, seed)
    for meal in plan.alle_meals:
        if meal.slot in config.frischware_slots:
            continue
        heikel = rules.empfindliche_zutaten(rezepte[meal.recipe_slug], meal.variant, zutaten)
        assert not heikel, (
            f"{meal.recipe_name} in Slot {meal.slot} am {meal.datum} braucht {heikel}"
        )


def test_pilzgericht_ist_nur_fuer_fruehe_slots_freigegeben(rezepte, config):
    pilze = rezepte["pilzpfanne-spaetzle"]
    assert set(pilze.slots) <= set(config.frischware_slots)
    assert "MI" not in pilze.slots


# -------------------------------------------------------------- Fisch


@pytest.mark.parametrize("monat", TEST_MONATE)
@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_fischstaebchen_hoechstens_einmal_im_monat(mach_plan, rezepte, config, monat, seed):
    plan = mach_plan(monat, seed)
    fisch = [m for m in plan.alle_meals if "fisch" in rezepte[m.recipe_slug].tags]
    assert len(fisch) <= config.fischstaebchen_max_pro_monat, f"{len(fisch)}× Fisch: " + ", ".join(
        f"{m.recipe_name} am {m.datum}" for m in fisch
    )


def test_fisch_ist_die_einzige_ausnahme_vom_vegetarischen(rezepte):
    """Außer Fischstäbchen darf kein Rezept Fleisch oder Fisch enthalten."""
    fisch_rezepte = {r.slug for r in rezepte.values() if "fisch" in r.tags}
    assert fisch_rezepte == {"kartoffelbrei-fischstaebchen"}


# ------------------------------------------------------- Slots und Portionen


@pytest.mark.parametrize("monat", TEST_MONATE)
@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_jeder_slot_jeder_woche_ist_belegt(mach_plan, config, monat, seed):
    plan = mach_plan(monat, seed)
    for woche in plan.weeks:
        belegt = {m.slot for m in woche.meals}
        assert belegt == set(config.slot_namen), (
            f"Woche ab {woche.start}: fehlende Slots {set(config.slot_namen) - belegt}"
        )


@pytest.mark.parametrize("monat", TEST_MONATE)
def test_gericht_steht_nur_in_freigegebenen_slots(mach_plan, rezepte, monat):
    plan = mach_plan(monat, 42)
    for meal in plan.alle_meals:
        assert rezepte[meal.recipe_slug].erlaubt_in_slot(meal.slot), (
            f"{meal.recipe_name} ist nicht für {meal.slot} freigegeben"
        )


def test_portionen_familie_und_erwachsene(config):
    """2 Erwachsene + 1 Kind à 0,5 ergeben 2,5 Erwachsenenportionen."""
    assert config.familien_portionen == pytest.approx(2.5)
    assert config.slot_portionen("FR") == pytest.approx(2.5)
    assert config.slot_portionen("SA") == pytest.approx(2.5)
    assert config.slot_portionen("SO") == pytest.approx(2.5)
    assert config.slot_portionen("MO") == pytest.approx(4.0)
    assert config.slot_portionen("MI") == pytest.approx(4.0)


@pytest.mark.parametrize("monat", TEST_MONATE)
def test_portionen_im_plan_entsprechen_der_konfiguration(mach_plan, config, monat):
    plan = mach_plan(monat, 42)
    for meal in plan.alle_meals:
        if meal.batch_cook:
            continue
        assert meal.portionen == pytest.approx(config.slot_portionen(meal.slot))


# ------------------------------------------------------------- Kernrezepte


@pytest.mark.parametrize("monat", TEST_MONATE)
@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_kernrezepte_machen_etwa_sechzig_prozent_aus(
    mach_plan, rezepte, planner, config, monat, seed
):
    """Die Spec nennt 60 % als Richtwert – geprüft wird ein Korridor."""
    plan = mach_plan(monat, seed)
    st = planner.statistik(plan)
    anteil = st["core_anteil"]
    assert 0.45 <= anteil <= 0.85, f"Kernrezept-Anteil {anteil:.0%} liegt außerhalb"


@pytest.mark.parametrize("monat", TEST_MONATE)
def test_plan_ist_abwechslungsreich(mach_plan, planner, monat):
    plan = mach_plan(monat, 42)
    st = planner.statistik(plan)
    assert st["verschiedene_gerichte"] >= 15, (
        f"nur {st['verschiedene_gerichte']} verschiedene Gerichte im Monat"
    )


# ------------------------------------------------------- Reproduzierbarkeit


def test_gleicher_seed_ergibt_gleichen_plan(planner):
    a = planner.plan_monat("2027-03", seed=99)
    b = planner.plan_monat("2027-03", seed=99)
    assert [m.to_dict() for m in a.alle_meals] == [m.to_dict() for m in b.alle_meals]


def test_verschiedene_monate_ergeben_verschiedene_plaene(planner):
    """Ein fester Seed darf nicht dazu führen, dass jeder Monat gleich aussieht."""
    a = planner.plan_monat("2027-03", seed=99)
    b = planner.plan_monat("2027-04", seed=99)
    assert [m.recipe_slug for m in a.alle_meals] != [m.recipe_slug for m in b.alle_meals]


# ----------------------------------------------------------- Gefriertruhe


def test_gefriertruhe_ist_standardmaessig_aus(config):
    assert config.freezer is False


def test_mit_gefriertruhe_bleibt_der_plan_regelkonform(rezepte, zutaten, config):
    """Das Doppelkochen darf keine Regel aushebeln."""
    import copy

    cfg = copy.deepcopy(config)
    cfg.raw.setdefault("planung", {})["freezer"] = True
    assert cfg.freezer is True

    planner = Planner(rezepte, zutaten, cfg)
    for monat in ("2026-12", "2027-05"):
        plan = planner.plan_monat(monat, seed=5)
        fehler = rules.nur_fehler(rules.pruefe_plan(plan, rezepte, zutaten, cfg))
        assert not fehler, "\n".join(str(f) for f in fehler)
        for meal in plan.alle_meals:
            if meal.aus_gefriertruhe:
                assert meal.slot in cfg.protein_slots


# ---------------------------------------------------------- Planbarkeit


def test_planung_gelingt_fuer_jeden_monat_eines_ganzen_jahres(planner, rezepte, zutaten, config):
    """Zwölf Monate in Folge – kein Monat darf an fehlenden Kandidaten scheitern."""
    for nr in range(1, 13):
        monat = kalender.monat_str(2027, nr)
        plan = planner.plan_monat(monat, seed=2027)
        fehler = rules.nur_fehler(rules.pruefe_plan(plan, rezepte, zutaten, config))
        assert not fehler, f"{monat}: " + "\n".join(str(f) for f in fehler)
