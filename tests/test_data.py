"""Stammdaten: Vollständigkeit, Referenzen, Speichern und Laden von Plänen."""

from __future__ import annotations

import pytest

from essensplaner import data
from essensplaner.models import ALL_SLOTS
from essensplaner.shopping import ABTEILUNG_REIHENFOLGE


def test_alle_rezepte_laden(rezepte):
    assert len(rezepte) >= 40, "die Spec nennt rund 35 Lieblingsgerichte plus Variationen"


def test_alle_zutaten_laden(zutaten):
    assert len(zutaten) >= 100


def test_jede_rezeptzutat_hat_stammdaten(rezepte, zutaten):
    fehlend = data.unbekannte_zutaten(rezepte, zutaten)
    assert not fehlend, "\n".join(f"{r}: {z}" for r, z in fehlend)


def test_slug_passt_zum_dateinamen(rezepte, config):
    for slug in rezepte:
        assert (config.recipes_dir / f"{slug}.yaml").is_file()


def test_rezepte_nutzen_nur_bekannte_slots(rezepte):
    for rezept in rezepte.values():
        for slot in rezept.slots:
            assert slot in ALL_SLOTS, f"{rezept.slug}: unbekannter Slot {slot}"


def test_zutaten_nutzen_nur_bekannte_abteilungen(zutaten):
    unbekannt = {
        z.department for z in zutaten.values() if z.department not in ABTEILUNG_REIHENFOLGE
    }
    assert not unbekannt, f"Abteilungen ohne Sortierplatz: {unbekannt}"


def test_jede_zutat_ist_pantry_oder_fresh(zutaten):
    for z in zutaten.values():
        assert z.type in ("pantry", "fresh")


def test_jede_zutat_hat_eine_packungsgroesse(zutaten):
    for z in zutaten.values():
        assert z.pack_qty > 0, f"{z.slug} hat keine Packungsgröße"
        assert z.pack_unit


def test_stueckzutaten_mit_protein_haben_ein_gewicht(zutaten):
    """Ohne Stückgewicht lässt sich 'eine Dose' nicht in Protein umrechnen."""
    from essensplaner import units

    for z in zutaten.values():
        if z.protein_per_100 <= 0:
            continue
        if units.basis_einheit(z.pack_unit) in ("g", "ml"):
            continue
        assert z.stk_gewicht_g, f"{z.slug}: Stückgewicht fehlt"


def test_jedes_rezept_hat_zutaten_und_schritte(rezepte):
    for r in rezepte.values():
        assert r.ingredients, f"{r.slug} hat keine Zutaten"
        assert r.steps, f"{r.slug} hat keine Zubereitung"
        assert r.base_servings > 0


def test_beilagen_werden_nicht_eingeplant(rezepte):
    """Die Bausteine aus Abschnitt 6 sind kombinierbar, aber keine Hauptgerichte."""
    beilagen = [r for r in rezepte.values() if "beilage" in r.tags]
    assert beilagen, "die Beilagen-Bausteine fehlen"
    for r in beilagen:
        assert r.slots == (), f"{r.slug} hat Slots und würde eingeplant"


def test_kernrezepte_sind_markiert(rezepte):
    core = [r for r in rezepte.values() if r.core]
    assert len(core) >= 25, "die Lieblingsgerichte aus Abschnitt 6 sollten core sein"


def test_es_gibt_genug_rezepte_je_slot(rezepte):
    """Ein Slot mit zu wenig Kandidaten lässt die Planung scheitern."""
    for slot in ALL_SLOTS:
        passend = [r for r in rezepte.values() if slot in r.slots]
        assert len(passend) >= 8, f"{slot}: nur {len(passend)} Rezepte"


def test_es_gibt_genug_wochenend_highlights(rezepte, config):
    """Jedes Wochenende braucht ein süßes oder zünftiges Gericht."""
    highlights = [
        r
        for r in rezepte.values()
        if any(t in r.tags for t in config.wochenende_pflicht_tags) and {"SA", "SO"} & set(r.slots)
    ]
    assert len(highlights) >= 5, f"nur {len(highlights)} Highlight-Rezepte"


def test_varianten_tragen_einen_hinweis(rezepte):
    for r in rezepte.values():
        for name, v in r.variants.items():
            assert v.hinweis, f"{r.slug}/{name}: kein Hinweis für die Mail"


def test_protein_varianten_existieren_wo_noetig(rezepte, config):
    """Jedes Rezept unter dem Minimum, das für MO/MI freigegeben ist, braucht
    eine Protein-Variante."""
    for r in rezepte.values():
        if not set(config.protein_slots) & set(r.slots):
            continue
        if r.protein_per_serving_g >= config.protein_min_g:
            continue
        assert "protein" in r.variants, (
            f"{r.slug}: {r.protein_per_serving_g} g und keine Protein-Variante"
        )


# ------------------------------------------------------------ Fehlerfaelle


def test_unbekannter_typ_wird_abgelehnt():
    with pytest.raises(data.DataError, match="type"):
        data.parse_ingredient("x", {"name": "X", "type": "irgendwas", "department": "TK"})


def test_fehlender_name_wird_abgelehnt():
    with pytest.raises(data.DataError, match="name"):
        data.parse_ingredient("x", {"type": "fresh", "department": "TK"})


def test_rezept_ohne_pflichtfeld_wird_abgelehnt():
    with pytest.raises(data.DataError, match="slots"):
        data.parse_recipe(
            {"slug": "x", "name": "X", "base_servings": 4, "protein_per_serving_g": 1}
        )


def test_unbekannter_slot_wird_abgelehnt():
    with pytest.raises(data.DataError, match="Slot"):
        data.parse_recipe(
            {
                "slug": "x",
                "name": "X",
                "slots": ["DI"],
                "base_servings": 4,
                "protein_per_serving_g": 1,
            }
        )


def test_ungueltiger_monat_in_seasons_wird_abgelehnt():
    with pytest.raises(data.DataError, match="Monat"):
        data.parse_recipe(
            {
                "slug": "x",
                "name": "X",
                "slots": ["SA"],
                "seasons": [13],
                "base_servings": 4,
                "protein_per_serving_g": 1,
            }
        )


def test_fehlender_rezeptordner_wird_abgelehnt(tmp_path):
    with pytest.raises(data.DataError, match="nicht gefunden"):
        data.load_recipes(tmp_path / "gibtsnicht")


def test_leerer_rezeptordner_wird_abgelehnt(tmp_path):
    with pytest.raises(data.DataError, match="Keine Rezepte"):
        data.load_recipes(tmp_path)


# -------------------------------------------------------- Plan speichern


def test_plan_speichern_und_laden(plan, tmp_path):
    pfad = data.save_plan(plan, tmp_path)
    assert pfad.is_file()
    geladen = data.load_plan(tmp_path, plan.monat)
    assert geladen.monat == plan.monat
    assert len(geladen.weeks) == len(plan.weeks)
    assert [m.to_dict() for m in geladen.alle_meals] == [m.to_dict() for m in plan.alle_meals]


def test_plan_vorhanden(plan, tmp_path):
    assert not data.plan_vorhanden(tmp_path, plan.monat)
    data.save_plan(plan, tmp_path)
    assert data.plan_vorhanden(tmp_path, plan.monat)


def test_fehlender_plan_wirft_fehler(tmp_path):
    with pytest.raises(data.DataError, match="Kein Plan"):
        data.load_plan(tmp_path, "2099-01")


def test_plan_json_ist_lesbar_und_versionierbar(plan, tmp_path):
    """Pläne sollen sich im Git-Diff ansehen lassen."""
    pfad = data.save_plan(plan, tmp_path)
    inhalt = pfad.read_text(encoding="utf-8")
    assert inhalt.endswith("\n")
    assert '"monat"' in inhalt
    assert "\n  " in inhalt, "Einrückung fehlt"
    # Umlaute als echte Zeichen, nicht als ü
    assert "\\u" not in inhalt


def test_rezept_serialisieren_und_zuruecklesen(rezepte):
    """Grundlage für die Review-Warteschlange der Claude-API-Vorschläge."""
    import yaml

    original = rezepte["chili-sin-carne"]
    text = data.dump_recipe(original)
    zurueck = data.parse_recipe(yaml.safe_load(text))
    assert zurueck.slug == original.slug
    assert zurueck.slots == original.slots
    assert len(zurueck.ingredients) == len(original.ingredients)
    assert zurueck.protein_per_serving_g == original.protein_per_serving_g
    assert set(zurueck.variants) == set(original.variants)
