"""Ein Gericht im fertigen Plan tauschen.

Der Planer würfelt einen regelkonformen Monat. Wer von Hand eingreift, darf
ihn dabei nicht kaputt machen – weder durch ein Gericht, das in diesem Slot
nichts zu suchen hat, noch durch eines, das erst im Zusammenspiel mit dem
Rest eine Regel bricht.
"""

from __future__ import annotations

import pytest

from essensplaner import rules
from essensplaner.editing import (
    AenderungsFehler,
    finde_mahlzeit,
    gericht_tauschen,
    plaetze_tauschen,
)

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


# ------------------------------------------------------------ Verschieben


def _platz(meal):
    return meal.datum, meal.slot


def _erstes_paar(planner, plan):
    """Ein Termin mit mindestens einem Tauschpartner, plus dieser Partner."""
    for meal in plan.alle_meals:
        partner = planner.tauschpartner(plan, meal.datum, meal.slot)
        if partner:
            return meal, partner[0]
    raise AssertionError("Kein Termin im Plan hat einen Tauschpartner")


def test_der_plan_laesst_sich_ueberhaupt_umstellen(planner, plan):
    """Ohne Tauschpartner wäre die Funktion in der Oberfläche nutzlos."""
    mit_partner = [m for m in plan.alle_meals if planner.tauschpartner(plan, *_platz(m))]
    assert len(mit_partner) >= len(plan.alle_meals) // 2


def test_verschieben_vertauscht_beide_gerichte(planner, plan):
    hier, dort = _erstes_paar(planner, plan)
    geaendert = plaetze_tauschen(plan, hier.datum, hier.slot, dort.datum, dort.slot, planner)
    assert finde_mahlzeit(geaendert, hier.datum, hier.slot).recipe_slug == dort.recipe_slug
    assert finde_mahlzeit(geaendert, dort.datum, dort.slot).recipe_slug == hier.recipe_slug


def test_verschieben_laesst_den_alten_plan_in_ruhe(planner, plan):
    hier, dort = _erstes_paar(planner, plan)
    vorher = (hier.recipe_slug, dort.recipe_slug)
    plaetze_tauschen(plan, hier.datum, hier.slot, dort.datum, dort.slot, planner)
    assert (
        finde_mahlzeit(plan, hier.datum, hier.slot).recipe_slug,
        finde_mahlzeit(plan, dort.datum, dort.slot).recipe_slug,
    ) == vorher


def test_verschobenes_gericht_bekommt_die_daten_seines_neuen_platzes(planner, plan, config):
    """Vom Samstag auf den Montag heißt: vier Portionen und zwei Tage."""
    hier, dort = _erstes_paar(planner, plan)
    geaendert = plaetze_tauschen(plan, hier.datum, hier.slot, dort.datum, dort.slot, planner)
    neu_hier = finde_mahlzeit(geaendert, hier.datum, hier.slot)
    neu_dort = finde_mahlzeit(geaendert, dort.datum, dort.slot)
    for meal in (neu_hier, neu_dort):
        assert meal.portionen == config.slot_portionen(meal.slot)
        assert meal.tage == config.slot_tage(meal.slot)
        assert meal.deckt_ab == config.slot_deckt_ab(meal.slot)
    assert neu_hier.variant == planner.rezepte[dort.recipe_slug].variant_fuer(
        hier.slot, config.protein_slots
    )


def test_jeder_angebotene_partner_laesst_sich_auch_wirklich_tauschen(
    planner, plan, rezepte, zutaten, config
):
    """Die Oberfläche bietet nichts an, was die Endprüfung dann ablehnt."""
    geprueft = 0
    for meal in plan.alle_meals[:6]:
        for partner in planner.tauschpartner(plan, *_platz(meal)):
            geaendert = plaetze_tauschen(
                plan, meal.datum, meal.slot, partner.datum, partner.slot, planner
            )
            assert rules.nur_fehler(rules.pruefe_plan(geaendert, rezepte, zutaten, config)) == []
            geprueft += 1
    assert geprueft > 0


def test_partner_duerfen_auf_dem_platz_des_anderen_stehen(planner, plan):
    for meal in plan.alle_meals:
        for partner in planner.tauschpartner(plan, *_platz(meal)):
            hin = planner.rezepte[partner.recipe_slug]
            zurueck = planner.rezepte[meal.recipe_slug]
            assert hin.erlaubt_in_slot(meal.slot)
            assert zurueck.erlaubt_in_slot(partner.slot)
            assert hin.in_saison(meal.datum.month)
            assert zurueck.in_saison(partner.datum.month)


def test_partner_erreichen_das_protein_des_vorkochtags(planner, plan, config):
    """Ein Samstagsgericht darf nur auf den Montag, wenn es dort sättigt."""
    for meal in plan.alle_meals:
        if meal.slot not in config.protein_slots:
            continue
        for partner in planner.tauschpartner(plan, *_platz(meal)):
            rezept = planner.rezepte[partner.recipe_slug]
            variante = rezept.variant_fuer(meal.slot, config.protein_slots)
            assert rezept.protein_fuer(variante) >= config.protein_min_g


def test_partner_wiederholen_kein_gericht_in_derselben_woche(planner, plan):
    for woche in plan.weeks:
        in_woche = {m.recipe_slug for m in woche.meals}
        for meal in woche.meals:
            for partner in planner.tauschpartner(plan, *_platz(meal)):
                if partner in woche.meals:
                    continue  # in derselben Woche tauschen die beiden nur die Plätze
                assert partner.recipe_slug not in in_woche


def test_ein_gericht_kann_nicht_mit_sich_selbst_tauschen(planner, plan):
    meal = plan.alle_meals[0]
    with pytest.raises(AenderungsFehler, match="mit sich selbst"):
        plaetze_tauschen(plan, meal.datum, meal.slot, meal.datum, meal.slot, planner)


def test_unpassende_paare_werden_abgelehnt(planner, plan):
    """Was die Oberfläche nicht anbietet, nimmt auch die Funktion nicht an."""
    quelle = plan.alle_meals[0]
    erlaubt = {_platz(p) for p in planner.tauschpartner(plan, *_platz(quelle))}
    abgelehnt = 0
    for anderes in plan.alle_meals:
        if _platz(anderes) in erlaubt or _platz(anderes) == _platz(quelle):
            continue
        with pytest.raises(AenderungsFehler):
            plaetze_tauschen(plan, quelle.datum, quelle.slot, anderes.datum, anderes.slot, planner)
        abgelehnt += 1
    assert abgelehnt > 0


def test_unbekannter_platz_wird_abgelehnt(planner, plan):
    meal, anderes = plan.alle_meals[0], plan.alle_meals[1]
    with pytest.raises(AenderungsFehler, match="kein Slot"):
        plaetze_tauschen(plan, meal.datum, "XX", anderes.datum, anderes.slot, planner)
    with pytest.raises(AenderungsFehler, match="kein Slot"):
        plaetze_tauschen(plan, meal.datum, meal.slot, anderes.datum, "XX", planner)


def _ist_highlight(planner, config, meal) -> bool:
    rezept = planner.rezepte[meal.recipe_slug]
    return any(rezept.hat_tag(t) for t in config.wochenende_pflicht_tags)


def test_das_wochenende_haelt_kein_gericht_fest(planner, mach_plan, rezepte, zutaten, config):
    """Das Pflicht-Highlight darf das Wochenende verlassen.

    Der Planer legt samstags oder sonntags ein süßes oder zünftiges Gericht
    ab. Als harte Regel würde das jeden Tausch verbieten, der es auf einen
    Werktag schiebt – und genau diese Tausche hat die Oberfläche angeboten
    und dann abgelehnt. Jetzt ist es nur noch eine Warnung.
    """
    geprueft = 0
    leere_wochenenden = 0
    for monat in TEST_MONATE[:3]:
        plan = mach_plan(monat)
        for meal in plan.alle_meals:
            if meal.slot not in ("SA", "SO") or not _ist_highlight(planner, config, meal):
                continue
            for partner in planner.tauschpartner(plan, *_platz(meal)):
                if partner.slot in ("SA", "SO"):
                    continue
                geaendert = plaetze_tauschen(
                    plan, meal.datum, meal.slot, partner.datum, partner.slot, planner
                )
                befunde = rules.pruefe_plan(geaendert, rezepte, zutaten, config)
                assert rules.nur_fehler(befunde) == []
                geprueft += 1
                ohne = [i for i in befunde if i.code == "wochenende_ohne_highlight"]
                if ohne:
                    assert all(i.severity == rules.SEVERITY_WARNUNG for i in ohne)
                    leere_wochenenden += 1
    assert geprueft, "Kein Testmonat bietet so einen Tausch an – der Test prüft nichts"
    assert leere_wochenenden, (
        "Kein Tausch räumt ein Wochenende leer – dann prüft der Test die alte Regel nicht"
    )
