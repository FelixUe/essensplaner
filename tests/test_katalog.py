"""Der Rezeptkatalog zur Laufzeit.

Die 52 Rezepte liegen schreibgeschützt im Image. Was sich zur Laufzeit
ändern lässt – ein eigenes Rezept, ein gestrichenes Gericht – gehört ins
Volume. Der heikle Teil ist dabei nicht das Speichern, sondern die Zusage:
ein gestrichenes Gericht verschwindet aus der Planung, aber nicht aus einem
Plan, der schon steht. Sonst bräche die Einkaufsliste einer laufenden Woche.
"""

from __future__ import annotations

import dataclasses

import pytest

from essensplaner import data, katalog, rules, shopping
from essensplaner.katalog import KatalogFehler
from essensplaner.planner import Planner
from essensplaner.runner import Kontext


@pytest.fixture
def volume(config, tmp_path, monkeypatch):
    """Ein leeres Volume – Pläne und Katalogdateien landen hier."""
    monkeypatch.setenv("ESSENSPLANER_PLANS_DIR", str(tmp_path))
    return tmp_path


# ------------------------------------------------------------ Kurznamen


def test_slug_aus_name_macht_umlaute_lesbar():
    assert katalog.slug_aus_name("Gebackener Camembert") == "gebackener-camembert"
    assert katalog.slug_aus_name("Käse-Lauch-Suppe") == "kaese-lauch-suppe"
    assert katalog.slug_aus_name("Püree  mit   Soße!") == "pueree-mit-sosse"


@pytest.mark.parametrize(
    "boese", ["../flucht", "mit/schraegstrich", "Gross", "", "mit leerzeichen"]
)
def test_unsaubere_kurznamen_werden_abgelehnt(boese):
    """Der Kurzname wird zum Dateinamen – er darf nicht aus dem Ordner führen."""
    with pytest.raises(KatalogFehler):
        katalog.slug_pruefen(boese)


# ------------------------------------------------------- Zutaten einlesen


def test_zutaten_werden_ueber_den_namen_gefunden(zutaten):
    zeilen = katalog.parse_zutaten("800 g Zucchini\n2 Stk Paprika rot", zutaten)
    assert [(z.item, z.qty, z.unit) for z in zeilen] == [
        ("zucchini", 800.0, "g"),
        ("paprika_rot", 2.0, "Stk"),
    ]


def test_zutaten_gehen_auch_ueber_den_kurznamen(zutaten):
    assert katalog.parse_zutaten("200 g feta", zutaten)[0].item == "feta"


def test_komma_als_dezimaltrennzeichen(zutaten):
    assert katalog.parse_zutaten("1,5 kg Zucchini", zutaten)[0].qty == 1.5


def test_optional_wird_erkannt(zutaten):
    assert katalog.parse_zutaten("1 Bund Petersilie (optional)", zutaten)[0].optional


def test_unbekannte_zutat_wird_gemeldet(zutaten):
    with pytest.raises(KatalogFehler, match="nicht bekannt"):
        katalog.parse_zutaten("100 g Drachenfrucht", zutaten)


def test_kaputte_zeile_nennt_die_zeilennummer(zutaten):
    with pytest.raises(KatalogFehler, match="Zeile 2"):
        katalog.parse_zutaten("200 g Feta\nirgendwas", zutaten)


def test_ohne_zutaten_kein_rezept(zutaten):
    with pytest.raises(KatalogFehler, match="Ohne Zutaten"):
        katalog.parse_zutaten("   \n\n", zutaten)


# --------------------------------------------------------- Rezept bauen


def _bauen(zutaten, **abweichend):
    argumente = {
        "name": "Testauflauf mit Zucchini",
        "slots": ["SA"],
        "zutaten_text": "800 g Zucchini\n200 g Feta\n1 Stk Baguette",
        "schritte_text": "Gemüse schneiden.\nBacken.",
        "zutaten": zutaten,
        "prep_minutes": 35,
        "base_servings": 4,
    }
    argumente.update(abweichend)
    return katalog.rezept_bauen(**argumente)


def test_rezept_bauen_rechnet_das_protein_aus(zutaten):
    """Geschätzte Proteinwerte driften von den Zutaten ab – also rechnen."""
    from essensplaner import nutrition

    rezept = _bauen(zutaten)
    assert rezept.protein_per_serving_g > 0
    assert rezept.protein_per_serving_g == pytest.approx(
        nutrition.protein_berechnet(rezept, zutaten), abs=0.1
    )


def test_rezept_bauen_ist_regelkonform(zutaten, config):
    rezept = _bauen(zutaten)
    assert rules.nur_fehler(rules.pruefe_rezept(rezept, zutaten, config)) == []


@pytest.mark.parametrize(
    "abweichend,meldung",
    [
        ({"name": "  "}, "Namen"),
        ({"slots": []}, "Slot"),
        ({"prep_minutes": 0}, "Zubereitungszeit"),
        ({"base_servings": 0}, "Grundmenge"),
        ({"schritte_text": "  "}, "Zubereitungsschritt"),
    ],
)
def test_unvollstaendiges_formular_wird_abgelehnt(zutaten, abweichend, meldung):
    with pytest.raises(KatalogFehler, match=meldung):
        _bauen(zutaten, **abweichend)


# ------------------------------------------------------------- Speichern


def test_eigenes_rezept_ueberlebt_das_neu_laden(volume, config, zutaten):
    rezept = _bauen(zutaten)
    katalog.speichere_eigenes(config, rezept)
    wieder = katalog.lade_eigene(config)
    assert rezept.slug in wieder
    assert wieder[rezept.slug].name == rezept.name
    assert len(wieder[rezept.slug].ingredients) == len(rezept.ingredients)


def test_kaputtes_eigenes_rezept_blockiert_den_start_nicht(volume, config, zutaten):
    """Eine halb geschriebene Datei darf nicht die ganze Anwendung aussperren."""
    katalog.speichere_eigenes(config, _bauen(zutaten))
    (katalog.eigene_dir(config) / "kaputt.yaml").write_text("}{ kein yaml", encoding="utf-8")
    geladen = katalog.lade_eigene(config)
    assert "testauflauf-mit-zucchini" in geladen
    assert "kaputt" not in geladen


def test_eigenes_rezept_laesst_sich_loeschen(volume, config, zutaten):
    rezept = _bauen(zutaten)
    katalog.speichere_eigenes(config, rezept)
    assert katalog.loesche_eigenes(config, rezept.slug) is True
    assert katalog.lade_eigene(config) == {}
    assert katalog.loesche_eigenes(config, rezept.slug) is False


def test_eigenes_rezept_sticht_das_aus_dem_repo(volume, config, zutaten, rezepte):
    eigen = dataclasses.replace(_bauen(zutaten), slug="pizza", name="Pizza nach Hausart")
    katalog.speichere_eigenes(config, eigen)
    zusammen = katalog.alle_rezepte(config, rezepte)
    assert zusammen["pizza"].name == "Pizza nach Hausart"
    assert len(zusammen) == len(rezepte)


# ------------------------------------------------------------ Streichen


def test_streichen_wird_gemerkt(volume, config):
    katalog.streichen(config, "pizza")
    assert katalog.lade_gestrichen(config) == {"pizza"}
    katalog.streichen(config, "wraps")
    assert katalog.lade_gestrichen(config) == {"pizza", "wraps"}
    katalog.aufnehmen(config, "pizza")
    assert katalog.lade_gestrichen(config) == {"wraps"}


def test_kaputte_streichliste_streicht_nichts(volume, config):
    """Lieber alles einplanen als gar nicht starten."""
    katalog.gestrichen_pfad(config).write_text("kein json", encoding="utf-8")
    assert katalog.lade_gestrichen(config) == set()


def test_gestrichenes_gericht_wird_nicht_mehr_geplant(rezepte, zutaten, config):
    planer = Planner(rezepte, zutaten, config, gestrichen={"pizza"})
    assert "pizza" not in planer.rezepte
    plan = planer.plan_monat("2027-03", seed=5)
    assert all(m.recipe_slug != "pizza" for m in plan.alle_meals)


def test_gestrichenes_gericht_bleibt_fuer_bestehende_plaene_lesbar(
    volume, config, rezepte, zutaten, mach_plan
):
    """Der heikle Fall: die Woche ist eingekauft, dann wird gestrichen."""
    plan = mach_plan("2026-12")
    gericht = plan.alle_meals[0].recipe_slug
    katalog.streichen(config, gericht)

    ctx = Kontext.laden(config, streng=False)
    assert gericht in ctx.rezepte, "das Rezept muss ladbar bleiben"
    assert gericht in ctx.gestrichen
    assert gericht not in ctx.planner().rezepte

    woche = plan.weeks[0]
    liste = shopping.frischeliste(woche, ctx.rezepte, ctx.zutaten, config)
    assert liste.items, "der Einkaufszettel der laufenden Woche darf nicht leer werden"


def test_gestrichenes_gericht_wird_nicht_als_alternative_angeboten(
    rezepte, zutaten, config, mach_plan
):
    plan = mach_plan("2026-12")
    meal = plan.alle_meals[0]
    offen = Planner(rezepte, zutaten, config)
    kandidat = offen.alternativen(plan, meal.datum, meal.slot)[0].slug

    eng = Planner(rezepte, zutaten, config, gestrichen={kandidat})
    assert kandidat not in {r.slug for r in eng.alternativen(plan, meal.datum, meal.slot)}


def test_eigenes_rezept_wird_eingeplant(volume, config, zutaten, rezepte):
    """Anlegen soll etwas bewirken, nicht nur eine Datei erzeugen."""
    eigen = _bauen(zutaten, slots=["SA", "SO"])
    katalog.speichere_eigenes(config, eigen)
    zusammen = katalog.alle_rezepte(config, rezepte)
    planer = Planner(zusammen, zutaten, config)
    assert eigen.slug in planer.rezepte


def test_dump_und_parse_sind_umkehrbar(zutaten):
    rezept = _bauen(zutaten)
    wieder = data.parse_recipe(__import__("yaml").safe_load(data.dump_recipe(rezept)), quelle="x")
    assert wieder.slug == rezept.slug
    assert wieder.name == rezept.name
    assert wieder.slots == rezept.slots
    assert [(z.item, z.qty, z.unit) for z in wieder.ingredients] == [
        (z.item, z.qty, z.unit) for z in rezept.ingredients
    ]
