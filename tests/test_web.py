"""Die Weboberfläche.

Sie steht hinter einer Domain im Internet, sobald Coolify eine davorhängt.
Die Tests prüfen deshalb nicht nur, dass der Plan angezeigt wird, sondern
auch, dass ohne Anmeldung nichts geht, dass Fremde keine Gerichte tauschen
können und dass eine abgelehnte Änderung den gespeicherten Plan unberührt
lässt.
"""

from __future__ import annotations

import re

import pytest

from essensplaner import data, rules, web
from essensplaner.runner import Kontext

PASSWORT = "geheim-fuer-den-test"
MONAT = "2026-12"


@pytest.fixture
def plans_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ESSENSPLANER_PLANS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def app(config, rezepte, zutaten, plans_dir, monkeypatch, plan):
    monkeypatch.setenv("ESSENSPLANER_WEB_SECRET", "test-secret")
    monkeypatch.setenv("ESSENSPLANER_WEB_UNSICHER", "true")
    data.save_plan(plan, plans_dir)
    ctx = Kontext(config=config, zutaten=zutaten, rezepte=rezepte, bestand={})
    anwendung = web.erstelle_app(ctx, passwort=PASSWORT)
    anwendung.config["TESTING"] = True
    return anwendung


@pytest.fixture
def client(app):
    return app.test_client()


def _token(html: bytes) -> str:
    treffer = re.search(rb'name="csrf" value="([^"]+)"', html)
    assert treffer, "Im Formular steht kein CSRF-Token"
    return treffer.group(1).decode()


@pytest.fixture
def angemeldet(client):
    token = _token(client.get("/anmelden").data)
    antwort = client.post("/anmelden", data={"csrf": token, "passwort": PASSWORT})
    assert antwort.status_code == 302
    return client


# -------------------------------------------------------------- Aufbau


def test_ohne_passwort_startet_die_oberflaeche_nicht(config, rezepte, zutaten, monkeypatch):
    """Lieber keine Seite als eine offene."""
    monkeypatch.delenv("ESSENSPLANER_WEB_PASSWORT", raising=False)
    ctx = Kontext(config=config, zutaten=zutaten, rezepte=rezepte, bestand={})
    with pytest.raises(web.WebFehler, match="PASSWORT"):
        web.erstelle_app(ctx)


def test_web_aktiv_haengt_am_passwort(monkeypatch):
    monkeypatch.delenv("ESSENSPLANER_WEB_PASSWORT", raising=False)
    assert web.web_aktiv() is False
    monkeypatch.setenv("ESSENSPLANER_WEB_PASSWORT", "x")
    assert web.web_aktiv() is True


# -------------------------------------------------------------- Zugang


def test_startseite_verlangt_anmeldung(client):
    antwort = client.get("/")
    assert antwort.status_code == 302
    assert "/anmelden" in antwort.headers["Location"]


def test_monatsseite_verlangt_anmeldung(client):
    antwort = client.get(f"/monat/{MONAT}")
    assert antwort.status_code == 302
    assert "/anmelden" in antwort.headers["Location"]


def test_startseite_fuehrt_zum_laufenden_monat(angemeldet):
    """Die Startseite muss den Monat treffen, zu dem die laufende Woche gehört."""
    antwort = angemeldet.get("/")
    assert antwort.status_code == 302
    ziel = antwort.headers["Location"]
    assert re.fullmatch(r"/monat/\d{4}-\d{2}", ziel), ziel
    assert angemeldet.get(ziel).status_code == 200


def test_laufender_monat_kann_der_folgemonat_sein():
    """Die Woche ab dem letzten Freitag gehört womöglich schon zum nächsten."""
    from datetime import date

    assert web._aktueller_monat(date(2026, 11, 30)) == "2026-12"
    assert web._aktueller_monat(date(2026, 11, 20)) == "2026-11"


def test_gesundheitsabfrage_ist_offen(client):
    antwort = client.get("/gesund")
    assert antwort.status_code == 200
    assert antwort.get_json()["status"] == "ok"


def test_falsches_passwort_meldet_sich_nicht_an(client):
    token = _token(client.get("/anmelden").data)
    antwort = client.post("/anmelden", data={"csrf": token, "passwort": "daneben"})
    assert antwort.status_code == 200
    assert "Passwort stimmt nicht" in antwort.get_data(as_text=True)


def test_zu_viele_fehlversuche_sperren_voruebergehend(client):
    token = _token(client.get("/anmelden").data)
    for _ in range(web.MAX_FEHLVERSUCHE):
        client.post("/anmelden", data={"csrf": token, "passwort": "daneben"})
    antwort = client.post("/anmelden", data={"csrf": token, "passwort": PASSWORT})
    assert antwort.status_code == 429
    assert "Fehlversuche" in antwort.get_data(as_text=True)


def test_weiterleitung_bleibt_auf_der_eigenen_seite(client):
    """Ein ?weiter= auf eine fremde Adresse darf nicht befolgt werden."""
    token = _token(client.get("/anmelden").data)
    antwort = client.post(
        "/anmelden?weiter=https://fremde.example.com/",
        data={"csrf": token, "passwort": PASSWORT},
    )
    assert antwort.headers["Location"] == "/"


def test_abmelden_beendet_die_sitzung(angemeldet):
    seite = angemeldet.get(f"/monat/{MONAT}")
    angemeldet.post("/abmelden", data={"csrf": _token(seite.data)})
    assert angemeldet.get("/").status_code == 302


# ----------------------------------------------------------- Monatsplan


def test_monatsseite_zeigt_alle_wochen(angemeldet, plan):
    antwort = angemeldet.get(f"/monat/{MONAT}")
    text = antwort.get_data(as_text=True)
    assert antwort.status_code == 200
    assert text.count("Woche ab") == len(plan.weeks)
    for meal in plan.alle_meals:
        assert meal.recipe_name in text


def test_zwei_tages_slots_sind_ausgewiesen(angemeldet, config, plan):
    text = angemeldet.get(f"/monat/{MONAT}").get_data(as_text=True)
    if any(config.slot_tage(m.slot) == 2 for m in plan.alle_meals):
        assert "2 Tage" in text


def test_monat_ohne_plan_bietet_das_erzeugen_an(angemeldet):
    text = angemeldet.get("/monat/2030-05").get_data(as_text=True)
    assert "noch keinen Plan" in text
    assert "Plan jetzt erzeugen" in text


def test_unsinniger_monat_ist_nicht_gefunden(angemeldet):
    assert angemeldet.get("/monat/dezember").status_code == 404


# --------------------------------------------------------------- Tausch


def _erster_tauschpfad(client) -> str:
    seite = client.get(f"/monat/{MONAT}")
    treffer = re.search(rb'href="(/monat/[\d-]+/[\d-]+/\w+)"', seite.data)
    assert treffer, "Auf der Monatsseite steht kein Tausch-Link"
    return treffer.group(1).decode()


def test_tauschseite_zeigt_nur_erlaubte_gerichte(angemeldet, planner, plan):
    pfad = _erster_tauschpfad(angemeldet)
    _, _, _, datum, slot = pfad.split("/")
    from datetime import date

    erlaubt = {r.slug for r in planner.alternativen(plan, date.fromisoformat(datum), slot)}
    seite = angemeldet.get(pfad)
    angeboten = set(re.findall(rb'name="rezept" value="([^"]+)"', seite.data))
    assert {s.decode() for s in angeboten} == erlaubt


def test_tausch_aendert_den_gespeicherten_plan(angemeldet, plans_dir, rezepte, zutaten, config):
    pfad = _erster_tauschpfad(angemeldet)
    seite = angemeldet.get(pfad)
    token = _token(seite.data)
    slug = re.search(rb'name="rezept" value="([^"]+)"', seite.data).group(1).decode()

    antwort = angemeldet.post(pfad, data={"csrf": token, "rezept": slug})
    assert antwort.status_code == 302

    gespeichert = data.load_plan(plans_dir, MONAT)
    assert slug in {m.recipe_slug for m in gespeichert.alle_meals}
    assert rules.nur_fehler(rules.pruefe_plan(gespeichert, rezepte, zutaten, config)) == []


def test_tausch_ohne_token_wird_abgewiesen(angemeldet, plans_dir):
    pfad = _erster_tauschpfad(angemeldet)
    vorher = data.load_plan(plans_dir, MONAT).to_dict()
    antwort = angemeldet.post(pfad, data={"rezept": "pizza"})
    assert antwort.status_code == 400
    assert data.load_plan(plans_dir, MONAT).to_dict() == vorher


def test_unzulaessiges_gericht_laesst_den_plan_stehen(angemeldet, plans_dir):
    pfad = _erster_tauschpfad(angemeldet)
    token = _token(angemeldet.get(pfad).data)
    vorher = data.load_plan(plans_dir, MONAT).to_dict()

    antwort = angemeldet.post(
        pfad, data={"csrf": token, "rezept": "beilage-salat"}, follow_redirects=True
    )
    assert "nicht zulässig" in antwort.get_data(as_text=True)
    assert data.load_plan(plans_dir, MONAT).to_dict() == vorher


def test_plan_erzeugen_legt_eine_datei_an(angemeldet, plans_dir):
    seite = angemeldet.get("/monat/2030-05")
    token = _token(seite.data)
    antwort = angemeldet.post("/monat/2030-05/erzeugen", data={"csrf": token})
    assert antwort.status_code == 302
    assert (plans_dir / "2030-05.json").is_file()
