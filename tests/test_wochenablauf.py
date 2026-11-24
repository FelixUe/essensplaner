"""Wochenablauf: Vorschlag per ntfy, Zettel fertig, Rückfall."""

from __future__ import annotations

import copy
import re
import threading
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from essensplaner import data, runner, web, wochenablauf
from essensplaner.mealie_einkauf import EinkaufBericht
from essensplaner.runner import Kontext
from essensplaner.scheduler import Scheduler

MITTWOCH = date(2026, 12, 2)
FREITAG = date(2026, 12, 4)


class FakeSender:
    def __init__(self, antworten=None):
        self.aufrufe = []
        self.antworten = antworten or {}

    def __call__(self, url, daten, kopf, timeout):
        self.aufrufe.append((url, daten, kopf))
        for teil, antwort in self.antworten.items():
            if teil in url:
                if isinstance(antwort, Exception):
                    raise antwort
                return antwort
        return 200, {}


def _um(tag: date, stunde: int, minute: int = 0) -> datetime:
    return datetime(tag.year, tag.month, tag.day, stunde, minute, tzinfo=ZoneInfo("Europe/Berlin"))


@pytest.fixture
def tmp_config(config, tmp_path, monkeypatch):
    monkeypatch.setenv("ESSENSPLANER_PLANS_DIR", str(tmp_path / "plans"))
    return copy.deepcopy(config)


@pytest.fixture
def ntfy(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "geheimes-topic")


# ---------------------------------------------------------------- ntfy


def test_ohne_topic_kein_push():
    sender = FakeSender()
    assert wochenablauf.sende_push(wochenablauf.Push("t", "x"), sender) is False
    assert sender.aufrufe == []


def test_push_geht_an_ntfy_sh_mit_aktionen(ntfy):
    sender = FakeSender()
    push = wochenablauf.Push("Titel", "Text", klick="http://a", aktionen=[("Los", "http://b")])
    assert wochenablauf.sende_push(push, sender) is True
    url, daten, kopf = sender.aufrufe[0]
    assert url == "https://ntfy.sh"
    assert daten["topic"] == "geheimes-topic"
    assert daten["click"] == "http://a"
    assert daten["actions"] == [
        {"action": "view", "label": "Los", "url": "http://b", "clear": True}
    ]
    assert kopf == {}


def test_eigener_server_und_token(ntfy, monkeypatch):
    monkeypatch.setenv("NTFY_SERVER", "https://ntfy.example/")
    monkeypatch.setenv("NTFY_TOKEN", "tk")
    sender = FakeSender()
    wochenablauf.sende_push(wochenablauf.Push("t", "x"), sender)
    url, _, kopf = sender.aufrufe[0]
    assert url == "https://ntfy.example"
    assert kopf == {"Authorization": "Bearer tk"}


def test_ntfy_ausfall_wirft_nicht_und_verraet_kein_topic(ntfy, caplog):
    sender = FakeSender({"ntfy": OSError("weg mit geheimes-topic")})
    assert wochenablauf.sende_push(wochenablauf.Push("t", "x"), sender) is False
    assert "geheimes-topic" not in caplog.text


def test_ntfy_fehlerstatus(ntfy):
    assert (
        wochenablauf.sende_push(wochenablauf.Push("t", "x"), FakeSender({"ntfy": (429, {})}))
        is False
    )


# ------------------------------------------------------------ Vorschlag


def test_vorschlag_listet_die_gerichte_und_verlinkt_die_woche(plan, config, monkeypatch):
    monkeypatch.setenv("ESSENSPLANER_BASIS_URL", "http://essen.home.arpa/")
    woche = plan.weeks[1]
    push = wochenablauf.vorschlag_push(woche, config.slot_kochtag)
    for meal in woche.meals:
        assert meal.recipe_name in push.text
    monat = plan.monat
    assert push.klick == f"http://essen.home.arpa/monat/{monat}/woche/{woche.start}"
    assert [a[0] for a in push.aktionen] == ["Gerichte ändern", "Einkaufszettel"]
    assert push.aktionen[1][1].endswith(f"/woche/{woche.start}/einkauf")


def test_vorschlag_ohne_basis_url_ohne_links(plan, config):
    push = wochenablauf.vorschlag_push(plan.weeks[0], config.slot_kochtag)
    assert push.klick is None and push.aktionen == []


def test_vorschlag_ist_mittwochs_um_halb_acht_faellig(tmp_config, ntfy):
    s = Scheduler(tmp_config)
    assert s.vorschlag_faellig(_um(MITTWOCH, 19, 30), {}) == FREITAG
    assert s.vorschlag_faellig(_um(MITTWOCH, 19, 29), {}) is None
    assert s.vorschlag_faellig(_um(MITTWOCH, 21, 31), {}) is None
    assert s.vorschlag_faellig(_um(date(2026, 12, 3), 19, 30), {}) is None
    assert s.vorschlag_faellig(_um(MITTWOCH, 20), {"vorschlag": "2026-12-04"}) is None


def test_vorschlag_ohne_ntfy_nie_faellig(tmp_config):
    assert Scheduler(tmp_config).vorschlag_faellig(_um(MITTWOCH, 19, 30), {}) is None


def test_tick_schickt_vorschlag_einmal(tmp_config, ntfy, monkeypatch, mach_plan):
    data.save_plan(mach_plan("2026-12", 42), tmp_config.plans_dir)
    gesendet = []
    monkeypatch.setattr(
        "essensplaner.wochenablauf.sende_push", lambda push: gesendet.append(push) or True
    )
    s = Scheduler(tmp_config)
    erledigt = s.tick(_um(MITTWOCH, 19, 31))
    assert len(erledigt) == 1 and erledigt[0].startswith("vorschlag 2026-12-04")
    assert s.tick(_um(MITTWOCH, 19, 32)) == []
    assert len(gesendet) == 1
    assert runner.lade_state(tmp_config)["vorschlag"] == "2026-12-04"


def test_tick_versucht_vorschlag_erneut_wenn_ntfy_ausfaellt(
    tmp_config, ntfy, monkeypatch, mach_plan
):
    data.save_plan(mach_plan("2026-12", 42), tmp_config.plans_dir)
    monkeypatch.setattr("essensplaner.wochenablauf.sende_push", lambda push: False)
    s = Scheduler(tmp_config)
    assert s.tick(_um(MITTWOCH, 19, 31)) == []
    assert "vorschlag" not in runner.lade_state(tmp_config)


# --------------------------------------------------------- Zettel fertig


SCANNER_OK = {
    "status": "ok",
    "anzahl": 2,
    "ersparnis_euro": 3.5,
    "angebote": [
        {"artikel": "Butter", "preis": 1.79, "markt": "ALDI Nord"},
        {"artikel": "Kaffee", "preis": 4.99, "markt": "EDEKA"},
    ],
}


def test_fertig_push_mit_angeboten(monkeypatch):
    monkeypatch.setenv("ESSENSPLANER_BASIS_URL", "http://essen.home.arpa")
    push = wochenablauf.fertig_push(FREITAG, 23, SCANNER_OK)
    assert "23 Posten" in push.text
    assert "2 Angebote dazu, etwa 3,50 € gespart." in push.text
    assert "🏷 Butter · 1,79 € · ALDI Nord" in push.text
    assert push.klick.endswith("/woche/2026-12-04/einkauf")


def test_fertig_push_automatisch_und_scanner_weg(monkeypatch):
    monkeypatch.setenv("ANGEBOTSSCANNER_URL", "http://angebotsscanner:8099")
    push = wochenablauf.fertig_push(FREITAG, 5, None, automatisch=True)
    assert "nicht erreichbar" in push.text
    assert "Automatisch übertragen" in push.text


def test_scanner_lauf_mit_token(monkeypatch):
    monkeypatch.setenv("ANGEBOTSSCANNER_URL", "http://angebotsscanner:8099/")
    monkeypatch.setenv("ANGEBOTSSCANNER_AUSLOESER_TOKEN", "abc")
    sender = FakeSender({"/lauf": (200, {"code": 0, "zusammenfassung": SCANNER_OK})})
    assert wochenablauf.scanner_lauf(sender) == SCANNER_OK
    url, _, kopf = sender.aufrufe[0]
    assert url == "http://angebotsscanner:8099/lauf"
    assert kopf == {"Authorization": "Bearer abc"}


def test_scanner_nicht_erreichbar(monkeypatch):
    monkeypatch.setenv("ANGEBOTSSCANNER_URL", "http://angebotsscanner:8099")
    assert wochenablauf.scanner_lauf(FakeSender({"/lauf": OSError("weg")})) is None


def test_nach_dem_uebertragen_scanner_dann_push(monkeypatch, ntfy):
    monkeypatch.setenv("ANGEBOTSSCANNER_URL", "http://angebotsscanner:8099")
    sender = FakeSender({"/lauf": (200, {"zusammenfassung": SCANNER_OK})})
    wochenablauf.nach_dem_uebertragen(FREITAG, 7, sender=sender)
    assert [a[0] for a in sender.aufrufe] == ["http://angebotsscanner:8099/lauf", "https://ntfy.sh"]
    assert "2 Angebote" in sender.aufrufe[1][1]["message"]


# ----------------------------------------------------------- Oberfläche


@pytest.fixture
def angemeldet(config, rezepte, zutaten, tmp_path, monkeypatch, plan):
    monkeypatch.setenv("ESSENSPLANER_PLANS_DIR", str(tmp_path))
    monkeypatch.setenv("ESSENSPLANER_WEB_SECRET", "test-secret")
    monkeypatch.setenv("ESSENSPLANER_WEB_UNSICHER", "true")
    data.save_plan(plan, tmp_path)
    ctx = Kontext(config=config, zutaten=zutaten, rezepte=rezepte, bestand={})
    app = web.erstelle_app(ctx, passwort="pw")
    app.config["TESTING"] = True
    client = app.test_client()
    token = _token(client.get("/anmelden").data)
    client.post("/anmelden", data={"csrf": token, "passwort": "pw"})
    return client


def _token(html: bytes) -> str:
    return re.search(rb'name="csrf" value="([^"]+)"', html).group(1).decode()


def test_einkaufsseite_ohne_mealie_ohne_knopf(angemeldet, plan, monkeypatch):
    monkeypatch.delenv("MEALIE_URL", raising=False)
    pfad = f"/monat/{plan.monat}/woche/{plan.weeks[1].start}/einkauf"
    assert b"Zettel fertig" not in angemeldet.get(pfad).data


def test_zettel_fertig_uebertraegt_und_merkt_es_sich(angemeldet, plan, config, monkeypatch):
    monkeypatch.setenv("MEALIE_URL", "http://mealie:9000")
    monkeypatch.setenv("MEALIE_TOKEN", "t")
    woche = plan.weeks[1]
    uebertragen, danach = [], []
    monkeypatch.setattr(
        "essensplaner.web.einkauf_nach_mealie",
        lambda ctx, freitag: (
            uebertragen.append(freitag)
            or EinkaufBericht(woche=freitag, angelegt=["a", "b"], unveraendert=3)
        ),
    )
    monkeypatch.setattr(
        "essensplaner.wochenablauf.nach_dem_uebertragen",
        lambda freitag, posten, automatisch=False: danach.append((freitag, posten)),
    )
    pfad = f"/monat/{plan.monat}/woche/{woche.start}/einkauf"
    seite = angemeldet.get(pfad)
    assert b"Zettel fertig" in seite.data
    antwort = angemeldet.post(
        f"{pfad}/fertig", data={"csrf": _token(seite.data)}, follow_redirects=True
    )
    assert b"5 Posten stehen in Mealie" in antwort.data
    assert "Erneut übertragen".encode() in antwort.data
    for t in threading.enumerate():
        if t.name == "nach-dem-uebertragen":
            t.join(5)
    assert uebertragen == [woche.start]
    assert danach == [(woche.start, 5)]
    state = runner.lade_state(config)
    assert state["mealie-einkauf"] == state["zettel-fertig"] == woche.start.isoformat()


def test_zettel_fertig_braucht_csrf(angemeldet, plan, monkeypatch):
    monkeypatch.setenv("MEALIE_URL", "http://mealie:9000")
    monkeypatch.setenv("MEALIE_TOKEN", "t")
    pfad = f"/monat/{plan.monat}/woche/{plan.weeks[1].start}/einkauf/fertig"
    assert angemeldet.post(pfad, data={}).status_code in (400, 403)
