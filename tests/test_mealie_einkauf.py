"""Wocheneinkauf nach Mealie – gegen eine Einkaufsliste im Speicher, ohne Netz."""

from __future__ import annotations

import copy
import itertools
import threading
from datetime import date, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

from essensplaner import runner
from essensplaner.mealie_einkauf import (
    ANGEBOT_ZEICHEN,
    KEY,
    WOCHE,
    EinkaufSync,
    eigener_teil,
    eintraege,
)
from essensplaner.runner import Kontext
from essensplaner.scheduler import Scheduler
from essensplaner.shopping import ShoppingItem, ShoppingList

FREITAG = date(2026, 12, 4)


class FakeMealie:
    """Labels, Einkaufslisten und ihre Einträge – so wie Mealie v3 sie liefert."""

    def __init__(self) -> None:
        self.ids = (f"id{n}" for n in itertools.count(1))
        self.labels: list[dict] = []
        self.listen: dict[str, dict] = {}
        self.items: dict[str, dict] = {}
        self.aufrufe: list[tuple[str, str]] = []

    def liste_anlegen(self, name: str) -> str:
        lid = next(self.ids)
        self.listen[lid] = {"id": lid, "name": name}
        return lid

    def item(self, liste: str, note: str, checked: bool = False, **extras: str) -> dict:
        iid = next(self.ids)
        self.items[iid] = {
            "id": iid,
            "shoppingListId": liste,
            "note": note,
            "quantity": 0,
            "checked": checked,
            "labelId": None,
            "extras": dict(extras),
        }
        return self.items[iid]

    def auf_liste(self, name: str = "Wocheneinkauf") -> list[dict]:
        lid = next(i for i, li in self.listen.items() if li["name"] == name)
        return [i for i in self.items.values() if i["shoppingListId"] == lid]

    def __call__(self, methode: str, pfad: str, daten: Any = None) -> Any:
        self.aufrufe.append((methode, pfad))
        teile = urlsplit(pfad)
        basis = teile.path
        if basis == "/api/groups/labels":
            if methode == "GET":
                return {"items": list(self.labels)}
            label = {"id": next(self.ids), **daten}
            self.labels.append(label)
            return label
        if basis == "/api/households/shopping/lists":
            if methode == "GET":
                return {"items": list(self.listen.values())}
            return dict(self.listen[self.liste_anlegen(daten["name"])])
        if basis.startswith("/api/households/shopping/lists/"):
            lid = basis.rsplit("/", 1)[1]
            items = [copy.deepcopy(i) for i in self.items.values() if i["shoppingListId"] == lid]
            return {**self.listen[lid], "listItems": items}
        if basis == "/api/households/shopping/items":
            if methode == "POST":
                for i in self.items.values():
                    # Mealie legt gleichlautende Notizen zusammen
                    if (
                        i["shoppingListId"] == daten["shoppingListId"]
                        and i["note"] == daten["note"]
                    ):
                        raise AssertionError(f"Mealie würde {daten['note']!r} zusammenlegen")
                iid = next(self.ids)
                self.items[iid] = {"id": iid, "checked": False, **copy.deepcopy(daten)}
                return {"createdItems": [self.items[iid]]}
            if methode == "PUT":
                for neu in daten:
                    self.items[neu["id"]] = copy.deepcopy(neu)
                return {"updatedItems": daten}
            if methode == "DELETE":
                for iid in parse_qs(teile.query)["ids"]:
                    del self.items[iid]
                return None
        raise AssertionError(f"unerwarteter Aufruf {methode} {pfad}")


def _item(slug: str, name: str, abteilung: str, menge: float, einheit: str, *gerichte: str):
    return ShoppingItem(
        slug=slug,
        name=name,
        abteilung=abteilung,
        menge=menge,
        einheit=einheit,
        gerichte=list(gerichte),
    )


@pytest.fixture
def frisch() -> ShoppingList:
    return ShoppingList(
        titel="Frischeliste",
        art="fresh",
        items=[
            _item("tomate", "Tomaten", "Obst & Gemüse", 800, "g", "Shakshuka"),
            _item("feta", "Feta", "Käse", 200, "g", "Shakshuka", "Ofengemüse"),
        ],
    )


@pytest.fixture
def vorrat() -> ShoppingList:
    return ShoppingList(
        titel="Vorrat",
        art="pantry",
        items=[_item("spaghetti", "Spaghetti", "Trockenware", 500, "g", "Bolognese")],
    )


GRUND = ["Kühlregal:", "2 l Milch", "Butter"]


@pytest.fixture
def soll(frisch, vorrat):
    return eintraege(FREITAG, frisch, vorrat, GRUND)


# ------------------------------------------------------------- Einträge


def test_eintraege_tragen_text_und_maschinenlesbare_extras(soll):
    nach_key = {e.key: e for e in soll}
    assert set(nach_key) == {
        "frisch:tomate",
        "frisch:feta",
        "vorrat:spaghetti",
        "grund:2 l Milch",
        "grund:Butter",
    }
    feta = nach_key["frisch:feta"]
    assert feta.note == "200 g Feta · für Ofengemüse, Shakshuka"
    assert feta.abteilung == "Käse"
    assert feta.extras[KEY] == "frisch:feta"
    assert feta.extras[WOCHE] == "2026-12-04"
    assert feta.extras["essensplaner_slug"] == "feta"
    assert feta.extras["essensplaner_menge"] == "200"
    assert feta.extras["essensplaner_einheit"] == "g"
    assert feta.extras["essensplaner_frisch"] == "ja"
    assert nach_key["vorrat:spaghetti"].extras["essensplaner_frisch"] == "nein"
    milch = nach_key["grund:2 l Milch"]
    assert milch.note == "2 l Milch"
    assert milch.abteilung == "Kühlregal"
    assert "essensplaner_slug" not in milch.extras
    # Alle Werte sind Text – so speichert Mealie seine extras
    assert all(isinstance(v, str) for e in soll for v in e.extras.values())


def test_abgewaehltes_fehlt(frisch, vorrat):
    soll = eintraege(FREITAG, frisch, vorrat, GRUND, abgewaehlt={"frisch:feta", "grund:Butter"})
    assert {e.key for e in soll} == {"frisch:tomate", "vorrat:spaghetti", "grund:2 l Milch"}


def test_gleicher_text_nur_einmal(frisch, vorrat):
    extra = [{"text": "2 l Milch", "abteilung": "Kühlregal"}]
    soll = eintraege(FREITAG, frisch, vorrat, GRUND, extra=extra)
    assert [e.note for e in soll].count("2 l Milch") == 1


def test_eigener_teil_endet_am_angebotszeichen():
    assert eigener_teil("2 l Milch · 🏷 Penny 0,79 € bis Sa") == "2 l Milch"
    assert eigener_teil("2 l Milch") == "2 l Milch"
    assert eigener_teil(None) == ""


# ------------------------------------------------------------------ Sync


def test_legt_liste_an_und_schreibt_den_zettel(soll):
    mealie = FakeMealie()
    bericht = EinkaufSync(mealie).synchronisiere(FREITAG, soll)

    assert sorted(bericht.angelegt) == sorted(e.key for e in soll)
    items = mealie.auf_liste()
    assert len(items) == 5
    assert all(i["quantity"] == 0 for i in items)
    labels = {lb["id"]: lb["name"] for lb in mealie.labels}
    tomate = next(i for i in items if i["extras"][KEY] == "frisch:tomate")
    assert labels[tomate["labelId"]] == "Obst & Gemüse"


def test_zweiter_lauf_aendert_nichts(soll):
    mealie = FakeMealie()
    EinkaufSync(mealie).synchronisiere(FREITAG, soll)
    mealie.aufrufe.clear()

    bericht = EinkaufSync(mealie).synchronisiere(FREITAG, soll)
    assert (bericht.angelegt, bericht.aktualisiert, bericht.geloescht) == ([], [], [])
    assert bericht.unveraendert == 5
    assert not [a for a in mealie.aufrufe if a[0] != "GET"]


def test_fremde_eintraege_bleiben_unangetastet(soll):
    mealie = FakeMealie()
    lid = mealie.liste_anlegen("Wocheneinkauf")
    hand = mealie.item(lid, "Grillkohle")
    scanner = mealie.item(lid, "Kaffee · 🏷 EDEKA 4,99 €", angebotsscanner_id="x")

    EinkaufSync(mealie).synchronisiere(FREITAG, soll)
    assert mealie.items[hand["id"]] == hand
    assert mealie.items[scanner["id"]] == scanner


def test_angebotsnotiz_ueberlebt_eine_aenderung(frisch, vorrat, soll):
    mealie = FakeMealie()
    EinkaufSync(mealie).synchronisiere(FREITAG, soll)
    tomate = next(i for i in mealie.auf_liste() if i["extras"][KEY] == "frisch:tomate")
    tomate["note"] += f" · {ANGEBOT_ZEICHEN} EDEKA 1,99 € bis Sa 5.12."
    tomate["extras"]["angebotsscanner_angebot"] = "marktguru:1"

    frisch.items[0].menge = 1000
    bericht = EinkaufSync(mealie).synchronisiere(FREITAG, eintraege(FREITAG, frisch, vorrat, GRUND))

    assert bericht.aktualisiert == ["frisch:tomate"]
    neu = mealie.items[tomate["id"]]
    assert neu["note"] == "1 kg Tomaten · für Shakshuka · 🏷 EDEKA 1,99 € bis Sa 5.12."
    assert neu["extras"]["angebotsscanner_angebot"] == "marktguru:1"
    assert neu["extras"]["essensplaner_menge"] == "1000"


def test_nur_angebotsnotiz_ist_keine_aenderung(soll):
    mealie = FakeMealie()
    EinkaufSync(mealie).synchronisiere(FREITAG, soll)
    milch = next(i for i in mealie.auf_liste() if i["extras"][KEY] == "grund:2 l Milch")
    milch["note"] += " · 🏷 Aldi Nord 0,88 €"
    bericht = EinkaufSync(mealie).synchronisiere(FREITAG, soll)
    assert bericht.aktualisiert == []
    assert milch["note"].endswith("0,88 €")


def test_abgehakt_bleibt_auch_wenn_nicht_mehr_gebraucht(frisch, vorrat, soll):
    mealie = FakeMealie()
    EinkaufSync(mealie).synchronisiere(FREITAG, soll)
    for i in mealie.auf_liste():
        if i["extras"][KEY] in ("frisch:feta", "frisch:tomate"):
            i["checked"] = True

    frisch.items = [frisch.items[0]]  # Feta wird nicht mehr gebraucht
    frisch.items[0].menge = 1000  # Tomaten jetzt mehr – aber schon im Wagen
    vorrat.items = []  # Spaghetti auch nicht, und die sind nicht abgehakt
    bericht = EinkaufSync(mealie).synchronisiere(FREITAG, eintraege(FREITAG, frisch, vorrat, GRUND))

    keys = {i["extras"][KEY] for i in mealie.auf_liste()}
    assert "frisch:feta" in keys
    assert "vorrat:spaghetti" not in keys
    assert bericht.geloescht == ["vorrat:spaghetti"]
    assert sorted(bericht.abgehakt) == ["frisch:feta", "frisch:tomate"]
    tomate = next(i for i in mealie.auf_liste() if i["extras"][KEY] == "frisch:tomate")
    assert tomate["note"].startswith("800 g")


def test_alte_woche_wird_abgeraeumt(soll):
    mealie = FakeMealie()
    lid = mealie.liste_anlegen("Wocheneinkauf")
    alt_offen = mealie.item(lid, "Lauch", **{KEY: "frisch:lauch", WOCHE: "2026-11-27"})
    alt_hak = mealie.item(lid, "Reis", checked=True, **{KEY: "vorrat:reis", WOCHE: "2026-11-27"})

    bericht = EinkaufSync(mealie).synchronisiere(FREITAG, soll)
    assert alt_offen["id"] not in mealie.items
    assert alt_hak["id"] not in mealie.items
    assert sorted(bericht.geloescht) == ["frisch:lauch", "vorrat:reis"]


def test_dry_run_schreibt_nichts(soll):
    mealie = FakeMealie()
    bericht = EinkaufSync(mealie, dry_run=True).synchronisiere(FREITAG, soll)
    assert len(bericht.angelegt) == 5
    assert not mealie.listen
    assert not mealie.labels
    assert not [a for a in mealie.aufrufe if a[0] != "GET"]


def test_eigener_listenname(soll):
    mealie = FakeMealie()
    EinkaufSync(mealie, liste="Einkauf Familie").synchronisiere(FREITAG, soll)
    assert [li["name"] for li in mealie.listen.values()] == ["Einkauf Familie"]


# ---------------------------------------------------------- Runner, Plan


@pytest.fixture
def tmp_config(config, tmp_path, monkeypatch):
    monkeypatch.setenv("ESSENSPLANER_PLANS_DIR", str(tmp_path / "plans"))
    return copy.deepcopy(config)


def test_runner_schreibt_den_zettel_eines_echten_plans(tmp_config, rezepte, zutaten):
    ctx = Kontext(config=tmp_config, zutaten=zutaten, rezepte=rezepte, bestand={})
    mealie = FakeMealie()
    bericht = runner.einkauf_nach_mealie(ctx, freitag=FREITAG, transport=mealie)

    assert bericht.woche == FREITAG
    items = mealie.auf_liste()
    assert len(items) == len(bericht.angelegt) > 5
    quellen = {i["extras"]["essensplaner_quelle"] for i in items}
    assert {"frisch", "grund"} <= quellen
    assert all(i["extras"][WOCHE] == "2026-12-04" for i in items)


def test_dry_run_legt_keinen_plan_an(tmp_config, rezepte, zutaten):
    from essensplaner import data

    ctx = Kontext(config=tmp_config, zutaten=zutaten, rezepte=rezepte, bestand={})
    with pytest.raises(runner.RunnerFehler, match="keinen Plan"):
        runner.einkauf_nach_mealie(ctx, freitag=FREITAG, transport=FakeMealie(), dry_run=True)
    assert not data.plan_vorhanden(tmp_config.plans_dir, "2026-12")


def test_runner_ohne_mealie_zugang(tmp_config, rezepte, zutaten, monkeypatch):
    monkeypatch.delenv("MEALIE_URL", raising=False)
    ctx = Kontext(config=tmp_config, zutaten=zutaten, rezepte=rezepte, bestand={})
    with pytest.raises(runner.RunnerFehler, match="MEALIE_URL"):
        runner.einkauf_nach_mealie(ctx, freitag=FREITAG)


# -------------------------------------------------------------- Scheduler


def _um(tag: date, stunde: int, minute: int = 5) -> datetime:
    return datetime(tag.year, tag.month, tag.day, stunde, minute, tzinfo=ZoneInfo("Europe/Berlin"))


@pytest.fixture
def mit_mealie(monkeypatch):
    monkeypatch.setenv("MEALIE_URL", "http://mealie:9000")
    monkeypatch.setenv("MEALIE_TOKEN", "test")


DONNERSTAG = date(2026, 12, 3)


@pytest.fixture
def ohne_erinnerung(tmp_config):
    """Die Erinnerung liegt auf demselben Termin; hier geht es nur um Mealie."""
    tmp_config.raw.setdefault("mail", {})["erinnerung_tag"] = ""


def test_rueckfall_ist_donnerstags_um_achtzehn_faellig(tmp_config, mit_mealie):
    s = Scheduler(tmp_config)
    assert s.mealie_einkauf_faellig(_um(DONNERSTAG, 18), {}) == FREITAG
    assert s.mealie_einkauf_faellig(_um(DONNERSTAG, 17), {}) is None
    assert s.mealie_einkauf_faellig(_um(DONNERSTAG, 21), {}) is None
    assert s.mealie_einkauf_faellig(_um(date(2026, 11, 29), 18), {}) is None


def test_rueckfall_entfaellt_nach_zettel_fertig(tmp_config, mit_mealie):
    s = Scheduler(tmp_config)
    assert s.mealie_einkauf_faellig(_um(DONNERSTAG, 18), {"mealie-einkauf": "2026-12-04"}) is None


def test_einkauf_ohne_mealie_nie_faellig(tmp_config, monkeypatch):
    monkeypatch.delenv("MEALIE_URL", raising=False)
    s = Scheduler(tmp_config)
    assert s.mealie_einkauf_faellig(_um(DONNERSTAG, 18), {}) is None


def test_leerer_tag_schaltet_einkauf_ab(tmp_config, mit_mealie):
    tmp_config.raw.setdefault("mealie", {})["einkauf_tag"] = ""
    s = Scheduler(tmp_config)
    assert s.mealie_einkauf_faellig(_um(DONNERSTAG, 18), {}) is None


def test_tick_fuehrt_einkauf_aus_und_merkt_ihn_sich(
    tmp_config, mit_mealie, ohne_erinnerung, monkeypatch
):
    aufrufe = []
    danach = []
    monkeypatch.setattr(
        "essensplaner.wochenablauf.nach_dem_uebertragen",
        lambda freitag, posten, automatisch=False: danach.append((freitag, posten, automatisch)),
    )

    def fake(ctx, freitag):
        aufrufe.append(freitag)
        from essensplaner.mealie_einkauf import EinkaufBericht

        return EinkaufBericht(woche=freitag, angelegt=["a"])

    monkeypatch.setattr("essensplaner.scheduler.einkauf_nach_mealie", fake)
    s = Scheduler(tmp_config)
    jetzt = _um(DONNERSTAG, 18)
    assert s.tick(jetzt) == ["mealie-einkauf 2026-12-04: 1 neu, 0 geändert, 0 gelöscht"]
    assert s.tick(jetzt) == []
    assert aufrufe == [FREITAG]
    for t in threading.enumerate():
        if t.name == "nach-dem-uebertragen":
            t.join(5)
    assert danach == [(FREITAG, 1, True)]


def test_tick_ueberlebt_mealie_ausfall(tmp_config, mit_mealie, ohne_erinnerung, monkeypatch):
    def kaputt(ctx, freitag):
        raise RuntimeError("Mealie down")

    monkeypatch.setattr("essensplaner.scheduler.einkauf_nach_mealie", kaputt)
    s = Scheduler(tmp_config)
    assert s.tick(_um(DONNERSTAG, 18)) == []
    assert runner.lade_state(tmp_config).get("mealie-einkauf") is None
