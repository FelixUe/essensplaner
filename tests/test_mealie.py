"""Übertragung nach Mealie – gegen ein Mealie im Speicher, ohne Netz."""

from __future__ import annotations

import itertools
import re
from typing import Any

import pytest

from essensplaner.mealie import EXTRA_SLUG, SYNC_TAG, MealieSync, beschreibung, notizen


class FakeMealie:
    """Spielt die paar Endpunkte nach, die der Sync benutzt."""

    def __init__(self) -> None:
        self.ids = itertools.count(1)
        self.tabellen: dict[str, list[dict]] = {
            "/api/organizers/tags": [],
            "/api/units": [],
            "/api/foods": [],
            "/api/groups/labels": [],
        }
        self.rezepte: dict[str, dict] = {}
        self.aufrufe: list[tuple[str, str]] = []

    def _slug(self, name: str) -> str:
        basis = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        slug, n = basis, 1
        while slug in self.rezepte:
            n += 1
            slug = f"{basis}-{n}"
        return slug

    def __call__(self, methode: str, pfad: str, daten: Any = None) -> Any:
        self.aufrufe.append((methode, pfad))
        basis = pfad.split("?")[0]
        if methode == "GET" and basis in self.tabellen:
            return {"items": list(self.tabellen[basis])}
        if methode == "POST" and basis in self.tabellen:
            eintrag = {"id": f"id{next(self.ids)}", "slug": daten["name"].lower(), **daten}
            self.tabellen[basis].append(eintrag)
            return eintrag
        if basis == "/api/recipes" and methode == "GET":
            return {
                "items": [{"slug": r["slug"], "tags": r["tags"]} for r in self.rezepte.values()]
            }
        if basis == "/api/recipes" and methode == "POST":
            slug = self._slug(daten["name"])
            self.rezepte[slug] = {"slug": slug, "name": daten["name"], "tags": [], "extras": {}}
            return slug
        if basis.startswith("/api/recipes/"):
            slug = basis.rsplit("/", 1)[1]
            if methode == "GET":
                return dict(self.rezepte[slug])
            if methode == "PUT":
                self.rezepte[slug] = {**daten, "slug": slug}
                return self.rezepte[slug]
        raise AssertionError(f"unerwarteter Aufruf {methode} {pfad}")


@pytest.fixture
def kleine_auswahl(rezepte):
    return {s: rezepte[s] for s in ("kokos-linsen-curry", "pizza", "erbsensuppe")}


def test_legt_rezepte_mit_zutaten_und_tag_an(kleine_auswahl, zutaten):
    mealie = FakeMealie()
    bericht = MealieSync(mealie).synchronisiere(kleine_auswahl, zutaten)

    assert sorted(bericht.angelegt) == sorted(kleine_auswahl)
    assert bericht.aktualisiert == []
    assert len(mealie.rezepte) == 3

    curry = next(
        r for r in mealie.rezepte.values() if r["extras"][EXTRA_SLUG] == "kokos-linsen-curry"
    )
    assert any(t["name"] == SYNC_TAG for t in curry["tags"])
    linsen = next(z for z in curry["recipeIngredient"] if z["food"]["name"] == "Rote Linsen")
    assert linsen["quantity"] == 300
    assert linsen["unit"]["name"] == "g"
    assert curry["recipeServings"] == 4
    assert len(curry["recipeInstructions"]) == len(kleine_auswahl["kokos-linsen-curry"].steps)
    assert curry["notes"][0]["title"].startswith("Protein-Variante")


def test_zweiter_lauf_aktualisiert_statt_doppelt_anzulegen(kleine_auswahl, zutaten):
    mealie = FakeMealie()
    MealieSync(mealie).synchronisiere(kleine_auswahl, zutaten)
    bericht = MealieSync(mealie).synchronisiere(kleine_auswahl, zutaten)

    assert bericht.angelegt == []
    assert sorted(bericht.aktualisiert) == sorted(kleine_auswahl)
    assert len(mealie.rezepte) == 3
    # Einheiten und Lebensmittel werden wiederverwendet, nicht neu angelegt.
    namen = [f["name"] for f in mealie.tabellen["/api/foods"]]
    assert len(namen) == len(set(namen))


def test_fremde_rezepte_bleiben_unberuehrt_und_verwaiste_werden_gemeldet(kleine_auswahl, zutaten):
    mealie = FakeMealie()
    mealie.rezepte["omas-kuchen"] = {"slug": "omas-kuchen", "name": "Omas Kuchen", "tags": []}
    MealieSync(mealie).synchronisiere(kleine_auswahl, zutaten)

    weniger = {k: v for k, v in kleine_auswahl.items() if k != "pizza"}
    bericht = MealieSync(mealie).synchronisiere(weniger, zutaten)

    assert bericht.verwaist == ["pizza"]
    assert mealie.rezepte["omas-kuchen"] == {
        "slug": "omas-kuchen",
        "name": "Omas Kuchen",
        "tags": [],
    }


def test_dry_run_schreibt_nichts(kleine_auswahl, zutaten):
    mealie = FakeMealie()
    bericht = MealieSync(mealie, dry_run=True).synchronisiere(kleine_auswahl, zutaten)

    assert len(bericht.angelegt) == 3
    assert not [a for a in mealie.aufrufe if a[0] in ("POST", "PUT")]


def test_gestrichene_bekommen_ein_tag(kleine_auswahl, zutaten):
    mealie = FakeMealie()
    MealieSync(mealie).synchronisiere(kleine_auswahl, zutaten, gestrichen={"pizza"})
    pizza = next(r for r in mealie.rezepte.values() if r["extras"][EXTRA_SLUG] == "pizza")
    assert "Gestrichen" in {t["name"] for t in pizza["tags"]}


def test_alle_echten_rezepte_lassen_sich_umwandeln(rezepte, zutaten):
    """Kein Rezept im Repo darf an der Umwandlung scheitern."""
    sync = MealieSync(FakeMealie(), dry_run=True)
    sync._laden()
    for rezept in rezepte.values():
        daten = sync.rezept_daten(rezept, zutaten, gestrichen=False)
        assert daten["recipeIngredient"]
        assert beschreibung(rezept)
        notizen(rezept, zutaten)
