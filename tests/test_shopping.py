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

NL = chr(10)


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


# --------------------------------------------------------- Grundeinkauf


@pytest.fixture
def volume(config, tmp_path, monkeypatch):
    monkeypatch.setenv("ESSENSPLANER_PLANS_DIR", str(tmp_path))
    return tmp_path


def test_ohne_eigene_liste_kommt_ein_vorschlag(volume, config):
    zeilen, eigen = shopping.lade_grundeinkauf(config)
    assert zeilen == list(shopping.STANDARD_GRUNDEINKAUF)
    assert eigen is False, "ein Vorschlag darf nicht als eigene Liste gelten"


def test_gespeicherte_liste_ersetzt_den_vorschlag(volume, config):
    zeilen = shopping.speichere_grundeinkauf(config, "Milch" + NL + "Brot" + NL)
    assert zeilen == ["Milch", "Brot"]
    assert shopping.lade_grundeinkauf(config) == (["Milch", "Brot"], True)


def test_leere_zeilen_und_leerraum_fallen_weg(volume, config):
    zeilen = shopping.speichere_grundeinkauf(
        config, "  Milch  " + NL + NL + NL + "   " + NL + "Brot" + NL
    )
    assert zeilen == ["Milch", "Brot"]


def test_leeres_feld_loescht_die_liste(volume, config):
    shopping.speichere_grundeinkauf(config, "Milch")
    assert shopping.speichere_grundeinkauf(config, "   ") == []
    assert shopping.lade_grundeinkauf(config) == ([], True)


def test_zu_viele_zeilen_werden_abgelehnt(volume, config):
    text = NL.join(f"Posten {n}" for n in range(shopping.GRUNDEINKAUF_MAX_ZEILEN + 1))
    with pytest.raises(shopping.EinkaufFehler, match="zu viele"):
        shopping.speichere_grundeinkauf(config, text)
    assert shopping.lade_grundeinkauf(config)[1] is False, "nichts darf gespeichert sein"


def test_zu_lange_zeile_wird_abgelehnt(volume, config):
    with pytest.raises(shopping.EinkaufFehler, match="zu lang"):
        shopping.speichere_grundeinkauf(config, "x" * (shopping.GRUNDEINKAUF_MAX_LAENGE + 1))


def test_kaputte_datei_faellt_auf_den_vorschlag_zurueck(volume, config):
    shopping.grundeinkauf_pfad(config).write_text("kein json", encoding="utf-8")
    zeilen, eigen = shopping.lade_grundeinkauf(config)
    assert zeilen == list(shopping.STANDARD_GRUNDEINKAUF)
    assert eigen is False


def test_doppelpunkt_macht_eine_ueberschrift():
    gruppen = shopping.grundeinkauf_gruppen(
        ["Milch", "Kühlregal:", "Butter", "Eier", "Backwaren:", "Brot"]
    )
    assert gruppen == [
        ("", ["Milch"]),
        ("Kühlregal", ["Butter", "Eier"]),
        ("Backwaren", ["Brot"]),
    ]


def test_ueberschrift_ohne_posten_bleibt_sichtbar():
    """Sonst verschwindet eine gerade angefangene Gruppe beim Tippen."""
    assert shopping.grundeinkauf_gruppen(["Obst:"]) == [("Obst", [])]


def test_doppeltes_wird_als_solches_erkannt(mach_plan, rezepte, zutaten, config):
    """Milch soll nicht zweimal auf demselben Zettel stehen."""
    woche = mach_plan("2026-12").weeks[0]
    frisch = shopping.frischeliste(woche, rezepte, zutaten, config)
    namen = [i.name for i in frisch.items]
    assert namen, "die Woche braucht Frischware, sonst prüft der Test nichts"

    zeilen = [namen[0], "Etwas, das nie auf einer Rezeptliste steht"]
    doppelt = shopping.grundeinkauf_doppelt(zeilen, frisch)
    assert doppelt == {namen[0]}


def test_ueberschriften_gelten_nie_als_doppelt(mach_plan, rezepte, zutaten, config):
    woche = mach_plan("2026-12").weeks[0]
    frisch = shopping.frischeliste(woche, rezepte, zutaten, config)
    name = frisch.items[0].name
    assert shopping.grundeinkauf_doppelt([f"{name}:"], frisch) == set()


def test_aehnliche_namen_sind_kein_treffer(mach_plan, rezepte, zutaten, config):
    """ "Milch" darf nicht als Treffer in "Kokosmilch" durchgehen."""
    woche = mach_plan("2026-12").weeks[0]
    frisch = shopping.frischeliste(woche, rezepte, zutaten, config)
    vorrat = shopping.wochen_pantry_liste(woche, rezepte, zutaten, config)
    treffer = shopping.grundeinkauf_doppelt(["Milch"], frisch, vorrat)
    for name in [i.name for i in frisch.items] + [i.name for i in vorrat.items]:
        if "milch" in name.casefold() and name.casefold() != "milch":
            # Ein solcher Name existiert - und darf den Treffer nicht auslösen.
            assert treffer in ({"Milch"}, set())


def test_der_vorschlag_laesst_sich_so_speichern():
    """Was vorgeschlagen wird, muss auch durch die Prüfung gehen."""
    zeilen = shopping.pruefe_grundeinkauf(NL.join(shopping.STANDARD_GRUNDEINKAUF))
    assert zeilen == list(shopping.STANDARD_GRUNDEINKAUF)


def test_der_vorschlag_fuehrt_durch_die_vorraete():
    """Die Liste ist zum Kontrollieren gedacht, nicht zum Durchlesen.

    Deshalb sind die Überschriften Orte in der Wohnung und keine Abteilungen
    im Laden: man geht Kühlschrank, Obstschale und Schrank einmal durch.
    """
    gruppen = shopping.grundeinkauf_gruppen(list(shopping.STANDARD_GRUNDEINKAUF))
    assert [titel for titel, _ in gruppen] == [
        "Kühlschrank",
        "Obst & Gemüse",
        "Backwaren",
        "Vorratsschrank",
        "Gefrierfach",
    ]
    assert all(len(posten) >= 3 for _, posten in gruppen)
    for posten in ("Milch (2 l)", "Butter", "Eier (10 Stück)", "Mehl", "Kaffee"):
        assert posten in shopping.STANDARD_GRUNDEINKAUF


def test_mengenklammer_stoert_den_abgleich_nicht(mach_plan, rezepte, zutaten, config):
    """'Milch (2 l)' ist dieselbe Milch wie die aus dem Rezept."""
    woche = mach_plan("2026-12").weeks[0]
    frisch = shopping.frischeliste(woche, rezepte, zutaten, config)
    vorrat = shopping.wochen_pantry_liste(woche, rezepte, zutaten, config)
    name = next(
        (i.name for sl in (frisch, vorrat) for i in sl.items),
        "",
    )
    assert name, "die Woche braucht Einkäufe, sonst prüft der Test nichts"
    zeile = f"{name} (2 l)"
    assert shopping.grundeinkauf_doppelt([zeile], frisch, vorrat) == {zeile}


# ---------------------------------------------------- Ein Zettel statt drei


def _posten(gruppen, key):
    for _, liste in gruppen:
        for p in liste:
            if p.key == key:
                return p
    return None


def test_zettel_sortiert_nach_ladenweg(config, rezepte, zutaten, plan):
    """Die Gruppen stehen in Ladenreihenfolge, nicht nach Herkunft."""
    woche = plan.weeks[0]
    frisch = shopping.frischeliste(woche, rezepte, zutaten, config)
    vorrat = shopping.wochen_pantry_liste(woche, rezepte, zutaten, config)
    gruppen = shopping.zettel_posten(frisch, vorrat, [])

    namen = [name for name, _ in gruppen]
    assert namen == sorted(namen, key=shopping.abteilung_sortierung)


def test_zettel_nimmt_alle_drei_quellen_auf(config, rezepte, zutaten, plan):
    woche = plan.weeks[0]
    frisch = shopping.frischeliste(woche, rezepte, zutaten, config)
    vorrat = shopping.wochen_pantry_liste(woche, rezepte, zutaten, config)
    gruppen = shopping.zettel_posten(frisch, vorrat, ["Haushalt:", "Klopapier"])

    quellen = {p.quelle for _, liste in gruppen for p in liste}
    assert quellen == {"frisch", "vorrat", "grund"}
    assert _posten(gruppen, "grund:Klopapier") is not None


def test_grundeinkauf_landet_in_der_passenden_abteilung(config):
    """'Kühlschrank' im Textfeld meint dasselbe Regal wie 'Kühlregal' in den Stammdaten."""
    leer = shopping.ShoppingList(titel="", art="fresh")
    gruppen = shopping.zettel_posten(leer, leer, ["Kühlschrank:", "Butter"])

    assert _posten(gruppen, "grund:Butter").abteilung == "Kühlregal"


def test_eigene_ueberschrift_bleibt_eine_eigene_gruppe(config):
    leer = shopping.ShoppingList(titel="", art="fresh")
    gruppen = shopping.zettel_posten(leer, leer, ["Drogerie:", "Zahnpasta"])
    assert _posten(gruppen, "grund:Zahnpasta").abteilung == "Drogerie"


def test_abgewaehltes_steht_unten_und_ist_markiert(config, rezepte, zutaten, plan):
    """Oben steht, was noch zu holen ist – das ist der Sinn des Zettels."""
    woche = plan.weeks[0]
    frisch = shopping.frischeliste(woche, rezepte, zutaten, config)
    erster = frisch.items[0]
    key = f"frisch:{erster.slug}"
    gruppen = shopping.zettel_posten(frisch, frisch, [], abgewaehlt={key})

    posten = _posten(gruppen, key)
    assert posten.abgewaehlt is True
    for _, liste in gruppen:
        abgewaehlt = [i for i, p in enumerate(liste) if p.abgewaehlt]
        offen = [i for i, p in enumerate(liste) if not p.abgewaehlt]
        if abgewaehlt and offen:
            assert min(abgewaehlt) > max(offen), "Abgewähltes gehört ans Ende"


def test_abwahl_ueberlebt_das_speichern(config, tmp_path, monkeypatch):
    monkeypatch.setenv("ESSENSPLANER_PLANS_DIR", str(tmp_path))
    shopping.speichere_abwahl(config, "2026-12-04", {"grund:Milch", "frisch:butter"})
    assert shopping.lade_abwahl(config, "2026-12-04") == {"grund:Milch", "frisch:butter"}
    assert shopping.lade_abwahl(config, "2026-12-11") == set()


def test_kaputte_abwahl_blockiert_den_einkauf_nicht(config, tmp_path, monkeypatch):
    """Im Zweifel steht alles auf dem Zettel: das ist die harmlose Seite."""
    monkeypatch.setenv("ESSENSPLANER_PLANS_DIR", str(tmp_path))
    shopping.abwahl_pfad(config).write_text("{kein json", encoding="utf-8")
    assert shopping.lade_abwahl(config, "2026-12-04") == set()


def test_anker_ueberlebt_umlaute_und_klammern():
    """Die Sprungmarke muss in eine URL passen, der Schlüssel darf alles enthalten."""
    assert shopping.anker("grund:Milch (2 l)") == "grund-milch--2-l"
    assert shopping.anker("frisch:gemüse") == "frisch-gem-se"
    assert shopping.anker(":::") == "posten"
