"""Monatsplan nach Mealie – gegen einen Speiseplan im Speicher, ohne Netz.

Der Sync darf beliebig oft laufen. Deshalb prüfen die Tests nicht nur, dass
beim ersten Lauf etwas ankommt, sondern vor allem, was beim zweiten, dritten
und nach einem Tausch passiert – und dass von Hand eingetragene Mahlzeiten
dabei stehen bleiben.
"""

from __future__ import annotations

import copy
import itertools
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from essensplaner import runner
from essensplaner.mealie import EXTRA_SLUG, SYNC_TAG
from essensplaner.mealie_speiseplan import (
    ABEND,
    MARKE,
    MITTAG,
    SpeiseplanSync,
    eintraege,
)
from essensplaner.runner import Kontext


class FakeMealie:
    """Rezepte und Speiseplan, so wie Mealie v3 sie liefert."""

    def __init__(self, rezepte: dict | None = None) -> None:
        self.ids = itertools.count(1)
        self.rezepte: dict[str, dict] = {}
        self.plaene: dict[int, dict] = {}
        self.aufrufe: list[tuple[str, str]] = []
        for slug, name in (rezepte or {}).items():
            self.rezept(slug, name)

    def rezept(self, slug: str, name: str, mit_tag: bool = True) -> dict:
        eintrag = {
            "id": f"uuid-{slug}",
            "slug": slug.replace("_", "-"),
            "name": name,
            "tags": [{"name": SYNC_TAG}] if mit_tag else [],
            "extras": {EXTRA_SLUG: slug} if mit_tag else {},
        }
        self.rezepte[eintrag["slug"]] = eintrag
        return eintrag

    def plan(self, datum: str, art: str, slug: str | None, text: str = MARKE) -> dict:
        pid = next(self.ids)
        rezept = self.rezepte.get((slug or "").replace("_", "-"))
        self.plaene[pid] = {
            "id": pid,
            "date": datum,
            "entryType": art,
            "title": "",
            "text": text,
            "recipeId": rezept["id"] if rezept else None,
            "recipe": {"id": rezept["id"], "name": rezept["name"]} if rezept else None,
        }
        return self.plaene[pid]

    def __call__(self, methode: str, pfad: str, daten: Any = None) -> Any:
        self.aufrufe.append((methode, pfad))
        teile = urlsplit(pfad)
        basis = teile.path
        if basis == "/api/recipes" and methode == "GET":
            return {"items": [copy.deepcopy(r) for r in self.rezepte.values()]}
        if basis.startswith("/api/recipes/") and methode == "GET":
            return copy.deepcopy(self.rezepte[basis.rsplit("/", 1)[1]])
        if basis == "/api/households/mealplans":
            if methode == "GET":
                frage = parse_qs(teile.query)
                von = frage["start_date"][0]
                bis = frage["end_date"][0]
                return {
                    "items": [
                        copy.deepcopy(e) for e in self.plaene.values() if von <= e["date"] <= bis
                    ]
                }
            if methode == "POST":
                pid = next(self.ids)
                rezept = next(
                    (r for r in self.rezepte.values() if r["id"] == daten.get("recipeId")), None
                )
                self.plaene[pid] = {
                    "id": pid,
                    "recipe": {"id": rezept["id"], "name": rezept["name"]} if rezept else None,
                    **copy.deepcopy(daten),
                }
                return self.plaene[pid]
        if basis.startswith("/api/households/mealplans/"):
            pid = int(basis.rsplit("/", 1)[1])
            if methode == "PUT":
                self.plaene[pid] = {**self.plaene[pid], **copy.deepcopy(daten)}
                return self.plaene[pid]
            if methode == "DELETE":
                del self.plaene[pid]
                return None
        raise AssertionError(f"unerwarteter Aufruf {methode} {pfad}")

    # -- Zum Nachsehen im Test ----------------------------------------------

    def plaetze(self) -> set[tuple[str, str, str | None]]:
        return {(e["date"], e["entryType"], e.get("recipeId")) for e in self.plaene.values()}

    def eigene(self) -> list[dict]:
        return [e for e in self.plaene.values() if e.get("text") == MARKE]


@pytest.fixture
def mealie(plan, rezepte):
    """Ein Mealie, das alle Rezepte des Plans kennt."""
    return FakeMealie({m.recipe_slug: rezepte[m.recipe_slug].name for m in plan.alle_meals})


def _zeitraum(soll):
    return min(e.datum for e in soll), max(e.datum for e in soll)


def _lauf(mealie, plan, dry_run=False):
    soll = eintraege(plan)
    von, bis = _zeitraum(soll)
    return SpeiseplanSync(mealie, dry_run=dry_run).synchronisiere(von, bis, soll)


# ----------------------------------------------------------- Die Einträge


def test_jede_mahlzeit_wird_ein_abendessen(plan):
    soll = eintraege(plan)
    abende = [e for e in soll if e.art == ABEND]
    assert len(abende) == len(plan.alle_meals)
    assert {(e.datum, e.slug) for e in abende} == {
        (m.datum, m.recipe_slug) for m in plan.alle_meals
    }


def test_der_vorkochtag_deckt_den_folgetag_mittags_ab(plan):
    soll = eintraege(plan)
    mittage = {(e.datum, e.slug) for e in soll if e.art == MITTAG}
    erwartet = {(m.datum + timedelta(days=1), m.recipe_slug) for m in plan.alle_meals if m.tage > 1}
    assert mittage == erwartet
    assert erwartet, "Der Plan braucht Vorkochtage, sonst prüft der Test nichts"


def test_die_eintraege_stehen_in_der_reihenfolge_des_essens(plan):
    soll = eintraege(plan)
    assert soll == sorted(soll, key=lambda e: (e.datum, e.art != MITTAG))


# --------------------------------------------------------------- Der Lauf


def test_erster_lauf_traegt_alles_ein(mealie, plan):
    bericht = _lauf(mealie, plan)
    soll = eintraege(plan)

    assert len(bericht.angelegt) == len(soll)
    assert bericht.aktualisiert == bericht.geloescht == []
    assert bericht.unveraendert == 0
    assert bericht.fehlende_rezepte == []
    assert len(mealie.plaene) == len(soll)
    assert all(e["text"] == MARKE for e in mealie.plaene.values())
    assert mealie.plaetze() == {(e.datum.isoformat(), e.art, f"uuid-{e.slug}") for e in soll}


def test_zweiter_lauf_aendert_nichts(mealie, plan):
    _lauf(mealie, plan)
    vorher = copy.deepcopy(mealie.plaene)

    bericht = _lauf(mealie, plan)
    assert bericht.angelegt == bericht.aktualisiert == bericht.geloescht == []
    assert bericht.unveraendert == len(eintraege(plan))
    assert mealie.plaene == vorher


def test_was_von_hand_schon_richtig_dasteht_bleibt_liegen(mealie, plan):
    """Felix hat den Montag selbst eingetragen – das zählt."""
    erster = eintraege(plan)[0]
    von_hand = mealie.plan(erster.datum.isoformat(), erster.art, erster.slug, text="")
    bericht = _lauf(mealie, plan)

    assert von_hand["id"] in mealie.plaene
    assert mealie.plaene[von_hand["id"]]["text"] == ""
    assert bericht.unveraendert == 1
    assert len(bericht.angelegt) == len(eintraege(plan)) - 1


def test_fremde_mahlzeiten_bleiben_stehen(mealie, plan):
    """Was jemand selbst plant, gehört ihm – auch am selben Tag."""
    erster = eintraege(plan)[0]
    mealie.rezept("omas-eintopf", "Omas Eintopf", mit_tag=False)
    fremd = mealie.plan(erster.datum.isoformat(), erster.art, "omas-eintopf", text="")

    bericht = _lauf(mealie, plan)
    assert mealie.plaene[fremd["id"]]["text"] == ""
    assert len(bericht.angelegt) == len(eintraege(plan))
    assert bericht.geloescht == []


def test_nach_einem_tausch_wird_der_eintrag_nachgezogen(mealie, plan, rezepte):
    _lauf(mealie, plan)
    vorher = len(mealie.plaene)

    # Dasselbe Datum, ein anderes Gericht – wie nach einem Tausch im Plan.
    erster = eintraege(plan)[0]
    anders = next(s for s in rezepte if s != erster.slug)
    mealie.rezept(anders, rezepte[anders].name)
    soll = [
        e
        if e.datum != erster.datum or e.art != erster.art
        else type(e)(e.datum, e.art, anders, rezepte[anders].name)
        for e in eintraege(plan)
    ]
    von, bis = _zeitraum(soll)
    bericht = SpeiseplanSync(mealie).synchronisiere(von, bis, soll)

    assert len(bericht.aktualisiert) == 1
    assert bericht.angelegt == bericht.geloescht == []
    assert len(mealie.plaene) == vorher, "kein zweiter Eintrag am selben Platz"
    assert (erster.datum.isoformat(), erster.art, f"uuid-{anders}") in mealie.plaetze()


def test_eigene_eintraege_ohne_platz_im_plan_verschwinden(mealie, plan):
    _lauf(mealie, plan)
    erster = eintraege(plan)[0]
    # Ein Mittagessen, das der Plan nicht (mehr) kennt.
    veraltet = mealie.plan(erster.datum.isoformat(), MITTAG, erster.slug)

    bericht = _lauf(mealie, plan)
    assert len(bericht.geloescht) == 1
    assert veraltet["id"] not in mealie.plaene


def test_fehlende_rezepte_werden_gemeldet_nicht_geraten(plan, rezepte):
    """Ohne mealie-sync gibt es nichts, worauf der Speiseplan zeigen kann."""
    mealie = FakeMealie()
    bericht = _lauf(mealie, plan)
    assert mealie.plaene == {}
    assert bericht.angelegt == []
    assert set(bericht.fehlende_rezepte) == {m.recipe_slug for m in plan.alle_meals}


def test_dry_run_schreibt_nichts(mealie, plan):
    bericht = _lauf(mealie, plan, dry_run=True)
    assert bericht.angelegt, "der Bericht soll trotzdem sagen, was käme"
    assert mealie.plaene == {}
    assert {m for m, _ in mealie.aufrufe} == {"GET"}


def test_andere_monate_werden_nicht_angefasst(mealie, plan):
    weit_weg = mealie.plan("2030-01-01", ABEND, eintraege(plan)[0].slug)
    _lauf(mealie, plan)
    assert weit_weg["id"] in mealie.plaene


# -------------------------------------------------------------- Der Runner


@pytest.fixture
def tmp_config(config, tmp_path, monkeypatch):
    monkeypatch.setenv("ESSENSPLANER_PLANS_DIR", str(tmp_path / "plans"))
    return copy.deepcopy(config)


def test_runner_schreibt_den_speiseplan_eines_echten_plans(tmp_config, rezepte, zutaten):
    ctx = Kontext(config=tmp_config, zutaten=zutaten, rezepte=rezepte, bestand={})
    mealie = FakeMealie({slug: r.name for slug, r in rezepte.items()})

    bericht = runner.plan_nach_mealie(ctx, monat="2026-12", transport=mealie)

    assert bericht.von.year == 2026
    assert len(bericht.angelegt) == len(mealie.plaene) > 15
    assert bericht.fehlende_rezepte == []
    # Der Zeitraum richtet sich nach den Einträgen, nicht nach dem Kalender.
    assert bericht.von <= date(2026, 12, 1)
    assert all(bericht.von.isoformat() <= e["date"] for e in mealie.plaene.values())


def test_runner_dry_run_legt_keinen_plan_an(tmp_config, rezepte, zutaten):
    from essensplaner import data

    ctx = Kontext(config=tmp_config, zutaten=zutaten, rezepte=rezepte, bestand={})
    with pytest.raises(runner.RunnerFehler, match="keinen Plan"):
        runner.plan_nach_mealie(ctx, monat="2026-12", transport=FakeMealie(), dry_run=True)
    assert not data.plan_vorhanden(tmp_config.plans_dir, "2026-12")


def test_runner_ohne_mealie_zugang(tmp_config, rezepte, zutaten, monkeypatch):
    monkeypatch.delenv("MEALIE_URL", raising=False)
    ctx = Kontext(config=tmp_config, zutaten=zutaten, rezepte=rezepte, bestand={})
    with pytest.raises(runner.RunnerFehler, match="MEALIE_URL"):
        runner.plan_nach_mealie(ctx, monat="2026-12")
