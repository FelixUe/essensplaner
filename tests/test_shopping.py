"""Akzeptanzkriterien 5 und 6.

5. Die Frischeliste ist nach Abteilung sortiert und mengenmäßig aggregiert.
6. Die Vorratsliste summiert alle Pantry-Zutaten des Monats und rundet auf
   Packungsgrößen auf.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from essensplaner import shopping, units
from essensplaner.shopping import ABTEILUNG_REIHENFOLGE, abteilung_sortierung

from .conftest import TEST_MONATE


@pytest.fixture
def frisch(plan, rezepte, zutaten, config):
    return shopping.frischeliste(plan.weeks[0], rezepte, zutaten, config)


@pytest.fixture
def vorrat(plan, rezepte, zutaten, config):
    return shopping.vorratsliste(plan, rezepte, zutaten, config)


# -------------------------------------------- Kriterium 5: Frischeliste


def test_frischeliste_enthaelt_nur_frischware(frisch, zutaten):
    for item in frisch.items:
        assert zutaten[item.slug].is_fresh, f"{item.slug} ist kein Frischprodukt"


def test_frischeliste_ist_nicht_leer(frisch):
    assert frisch.anzahl >= 10, "eine Woche braucht mehr als eine Handvoll Frischware"


def test_frischeliste_ist_nach_abteilung_sortiert(frisch):
    reihenfolge = [abteilung_sortierung(abt)[0] for abt, _ in frisch.abteilungen]
    assert reihenfolge == sorted(reihenfolge), "Abteilungen nicht in Ladenreihenfolge"


def test_bekannte_abteilungen_stehen_vor_unbekannten(frisch):
    namen = [abt for abt, _ in frisch.abteilungen]
    bekannt = [n for n in namen if n in ABTEILUNG_REIHENFOLGE]
    assert namen[: len(bekannt)] == bekannt


def test_innerhalb_einer_abteilung_alphabetisch(frisch):
    for _abteilung, items in frisch.abteilungen:
        namen = [i.name.lower() for i in items]
        assert namen == sorted(namen)


def test_jede_zutat_steht_nur_einmal_auf_der_liste(frisch):
    """Aggregation: zwei Rezepte mit Zwiebeln ergeben eine Zeile."""
    schluessel = [(i.slug, i.einheit) for i in frisch.items]
    assert len(schluessel) == len(set(schluessel))


def test_mengen_sind_ueber_alle_gerichte_aufaddiert(plan, rezepte, zutaten, config):
    """Die Liste muss mindestens den rechnerischen Bedarf abdecken."""
    woche = plan.weeks[0]
    erwartet: dict[tuple[str, str], float] = defaultdict(float)
    for meal in woche.meals:
        rezept = rezepte[meal.recipe_slug]
        faktor = meal.portionen / rezept.base_servings
        for zeile in rezept.zutaten_fuer(meal.variant):
            zutat = zutaten[zeile.item]
            if not zutat.is_fresh:
                continue
            menge, basis = units.in_basis(zeile.qty * faktor, zeile.unit)
            if units.ist_kleinmenge(basis):
                kf = units.kleinmenge_faktor(basis)
                pack_basis = units.basis_einheit(zutat.pack_unit)
                if kf is not None and pack_basis in ("g", "ml"):
                    menge, basis = menge * kf, pack_basis
            erwartet[(zutat.slug, basis)] += menge

    liste = shopping.frischeliste(woche, rezepte, zutaten, config)
    # Verglichen wird gegen "bedarf": die Liste rundet auf kaufbare Mengen
    # auf und wechselt dabei die Einheit (3 Stk Möhren -> 1 kg Beutel).
    # Der aufsummierte Bedarf bleibt in der Rezepteinheit stehen.
    tatsaechlich = {(i.slug, i.bedarf_einheit): i.bedarf for i in liste.items}
    assert set(tatsaechlich) == {k for k, v in erwartet.items() if v > 0}
    for schluessel, soll in erwartet.items():
        if soll <= 0:
            continue
        assert tatsaechlich[schluessel] >= soll - 1e-6, (
            f"{schluessel}: {tatsaechlich[schluessel]} deckt {soll} nicht ab"
        )


def test_jede_zeile_nennt_die_gerichte_dazu(frisch):
    """Damit man beim Streichen weiß, was dann ausfällt."""
    for item in frisch.items:
        assert item.gerichte, f"{item.slug} nennt kein Gericht"


def test_mengen_sind_kaufbar_gerundet(frisch):
    for item in frisch.items:
        if item.einheit in ("g", "ml"):
            assert item.menge % 10 == 0 or item.menge % 5 == 0, (
                f"{item.name}: {item.menge} {item.einheit} ist keine runde Menge"
            )
        elif units.ist_stueck(item.einheit):
            assert item.menge == int(item.menge), f"{item.name}: {item.menge} Stück"


# -------------------------------------------- Kriterium 6: Vorratsliste


def test_vorratsliste_enthaelt_nur_haltbares(vorrat, zutaten):
    for item in vorrat.items:
        assert zutaten[item.slug].is_pantry, f"{item.slug} ist keine Vorratszutat"


@pytest.mark.parametrize("monat", TEST_MONATE)
def test_vorratsliste_deckt_den_ganzen_monat_ab(mach_plan, rezepte, zutaten, config, monat):
    plan = mach_plan(monat, 42)
    liste = shopping.vorratsliste(plan, rezepte, zutaten, config)

    erwartet: dict[tuple[str, str], float] = defaultdict(float)
    for meal in plan.alle_meals:
        if meal.aus_gefriertruhe:
            continue
        rezept = rezepte[meal.recipe_slug]
        faktor = meal.portionen / rezept.base_servings
        if meal.batch_cook:
            faktor *= 2
        for zeile in rezept.zutaten_fuer(meal.variant):
            zutat = zutaten[zeile.item]
            if not zutat.is_pantry:
                continue
            menge, basis = units.in_basis(zeile.qty * faktor, zeile.unit)
            if units.ist_kleinmenge(basis):
                kf = units.kleinmenge_faktor(basis)
                pack_basis = units.basis_einheit(zutat.pack_unit)
                if kf is not None and pack_basis in ("g", "ml"):
                    menge, basis = menge * kf, pack_basis
            erwartet[(zutat.slug, basis)] += menge

    tatsaechlich = {(i.slug, i.einheit): i.menge for i in liste.items}
    fehlend = {k for k, v in erwartet.items() if v > 0} - set(tatsaechlich)
    assert not fehlend, f"nicht auf der Vorratsliste: {fehlend}"
    for schluessel, soll in erwartet.items():
        if soll <= 0:
            continue
        assert tatsaechlich[schluessel] >= soll - 1e-6


def test_vorratsliste_rundet_auf_ganze_packungen(vorrat, zutaten):
    for item in vorrat.items:
        if item.packungen is None:
            continue
        zutat = zutaten[item.slug]
        pack_basis, _ = units.in_basis(zutat.pack_qty, zutat.pack_unit)
        assert item.packungen == int(item.packungen), (
            f"{item.name}: {item.packungen} Packungen ist keine ganze Zahl"
        )
        assert item.menge == pytest.approx(item.packungen * pack_basis), (
            f"{item.name}: Menge {item.menge} passt nicht zu {item.packungen} × {pack_basis}"
        )


def test_packungszahl_deckt_den_bedarf(vorrat):
    for item in vorrat.items:
        if item.packungen is None:
            continue
        assert item.menge >= item.bedarf - 1e-6, (
            f"{item.name}: {item.menge} deckt Bedarf {item.bedarf} nicht"
        )


def test_packungstext_ist_lesbar(vorrat):
    for item in vorrat.items:
        assert item.pack_text, f"{item.name} hat keinen Packungshinweis"
        assert not item.pack_text.startswith("0"), f"{item.name}: {item.pack_text}"


def test_loeffelmengen_werden_zu_einer_zeile_zusammengefasst(vorrat):
    """Salz steht in Rezepten als TL und als Prise – auf der Liste einmal."""
    salz = [i for i in vorrat.items if i.slug == "salz"]
    assert len(salz) == 1, f"Salz steht {len(salz)}× auf der Liste"
    assert salz[0].einheit == "g"


def test_wochenliste_und_monatsliste_sind_disjunkt(frisch, vorrat):
    """Frischware kommt wöchentlich, Vorrat monatlich – nie beides."""
    assert not {i.slug for i in frisch.items} & {i.slug for i in vorrat.items}


# ------------------------------------------------------------- Vorratsbestand


def test_vorhandener_bestand_wird_abgezogen(plan, rezepte, zutaten, config):
    ohne = shopping.vorratsliste(plan, rezepte, zutaten, config)
    reis = next((i for i in ohne.items if i.slug == "reis"), None)
    if reis is None:
        pytest.skip("In diesem Plan kommt kein Reis vor")

    bestand = {"reis": (reis.bedarf, reis.einheit)}
    mit = shopping.vorratsliste(plan, rezepte, zutaten, config, bestand)
    assert not [i for i in mit.items if i.slug == "reis"], (
        "voll gedeckter Bedarf müsste von der Liste verschwinden"
    )


def test_teilbestand_reduziert_die_menge(plan, rezepte, zutaten, config):
    ohne = shopping.vorratsliste(plan, rezepte, zutaten, config)
    kandidat = next((i for i in ohne.items if i.bedarf > 200 and i.einheit == "g"), None)
    if kandidat is None:
        pytest.skip("Keine passende Zutat im Plan")

    bestand = {kandidat.slug: (kandidat.bedarf / 2, kandidat.einheit)}
    mit = shopping.vorratsliste(plan, rezepte, zutaten, config, bestand)
    neu = next(i for i in mit.items if i.slug == kandidat.slug)
    assert neu.bedarf < kandidat.bedarf


def test_fehlende_bestandsdatei_ist_kein_fehler(tmp_path):
    assert shopping.lade_bestand(tmp_path / "gibtsnicht.yaml") == {}


def test_bestandsdatei_wird_gelesen(tmp_path):
    pfad = tmp_path / "bestand.yaml"
    pfad.write_text("reis:\n  qty: 500\n  unit: g\n", encoding="utf-8")
    assert shopping.lade_bestand(pfad) == {"reis": (500.0, "g")}


# ------------------------------------------------------------- Gefriertruhe


def test_portionen_aus_der_truhe_werden_nicht_doppelt_eingekauft(plan, rezepte, zutaten, config):
    """Was eingefroren wurde, ist schon bezahlt."""
    from essensplaner.models import MonthPlan, WeekPlan

    woche = plan.weeks[0]
    kopie = WeekPlan(start=woche.start, meals=[m for m in woche.meals])
    kopie.meals[0].aus_gefriertruhe = True
    liste_mit = shopping.frischeliste(kopie, rezepte, zutaten, config)
    kopie.meals[0].aus_gefriertruhe = False
    liste_ohne = shopping.frischeliste(kopie, rezepte, zutaten, config)
    assert liste_mit.anzahl <= liste_ohne.anzahl
    MonthPlan(monat=plan.monat, weeks=[kopie])  # Modell bleibt konsistent


# ------------------------------------------- Frischware in kaufbaren Mengen


def test_packungen_rechnet_stueck_in_gewicht_um():
    """Rezepte zählen Möhren in Stück, der Laden verkauft sie im Kilobeutel."""
    assert units.packungen(3, "Stk", 1, "kg", 80) == 1
    assert units.packungen(20, "Stk", 1, "kg", 80) == 2
    assert units.packungen(800, "g", 1, "Stk", 1000) == 1
    assert units.packungen(1200, "g", 1, "Stk", 1000) == 2


def test_packungen_ohne_stueckgewicht_bleibt_unbestimmt():
    assert units.packungen(3, "Stk", 1, "kg") is None


@pytest.mark.parametrize("monat", TEST_MONATE[:4])
def test_frischeliste_nennt_nur_kaufbare_mengen(mach_plan, rezepte, zutaten, config, monat):
    """Niemand kauft 20 g Ingwer.

    Jede Zeile der Frischeliste muss eine Menge nennen, die es so im Laden
    gibt: entweder ganze Packungen oder ganze Stück.
    """
    plan = mach_plan(monat)
    for woche in plan.weeks:
        liste = shopping.frischeliste(woche, rezepte, zutaten, config)
        for item in liste.items:
            if item.packungen is not None:
                assert item.packungen == int(item.packungen), item.anzeige
                assert item.packungen >= 1, item.anzeige
            else:
                assert item.menge == int(item.menge), (
                    f"{item.anzeige} ist weder eine Packung noch ein ganzes Stück"
                )


@pytest.mark.parametrize("monat", TEST_MONATE[:4])
def test_frischeliste_deckt_den_bedarf(mach_plan, rezepte, zutaten, config, monat):
    """Aufrunden heißt aufrunden – gekauft wird nie weniger als gebraucht."""
    plan = mach_plan(monat)
    for woche in plan.weeks:
        for item in shopping.frischeliste(woche, rezepte, zutaten, config).items:
            if item.bedarf_einheit == item.einheit:
                assert item.menge >= item.bedarf - 1e-6, item.anzeige


def test_frischeliste_verweist_nicht_auf_den_vorrat(plan, rezepte, zutaten, config):
    """ "Vorrat prüfen" passt zu Mehl, nicht zu einer Gurke."""
    for woche in plan.weeks:
        for item in shopping.frischeliste(woche, rezepte, zutaten, config).items:
            assert item.pack_text != shopping.VORRAT_PRUEFEN, item.anzeige


def test_jede_frischware_laesst_sich_in_eine_packung_umrechnen(rezepte, zutaten):
    """Sonst stünde irgendwann wieder eine unkaufbare Menge auf dem Zettel."""
    offen = []
    for zutat in zutaten.values():
        if not zutat.is_fresh:
            continue
        einheiten = {
            zeile.unit
            for rezept in rezepte.values()
            for variante in (None, *rezept.variants)
            for zeile in rezept.zutaten_fuer(variante)
            if zeile.item == zutat.slug and not units.ist_kleinmenge(zeile.unit)
        }
        for einheit in einheiten:
            if (
                units.packungen(1.0, einheit, zutat.pack_qty, zutat.pack_unit, zutat.stk_gewicht_g)
                is None
            ):
                offen.append(f"{zutat.name} ({einheit} vs. {zutat.pack_qty} {zutat.pack_unit})")
    assert offen == []
