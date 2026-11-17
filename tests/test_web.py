"""Die Weboberfläche.

Sie steht hinter einer Domain im Internet, sobald Coolify eine davorhängt.
Die Tests prüfen deshalb nicht nur, dass der Plan angezeigt wird, sondern
auch, dass ohne Anmeldung nichts geht, dass Fremde keine Gerichte tauschen
können und dass eine abgelehnte Änderung den gespeicherten Plan unberührt
lässt.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest

from essensplaner import data, katalog, rules, shopping, web
from essensplaner.runner import Kontext

PASSWORT = "geheim-fuer-den-test"
MONAT = "2026-12"
NL = chr(10)


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


# ----------------------------------------------------------- Einkaufszettel


def _einkaufspfad(client) -> str:
    seite = client.get(f"/monat/{MONAT}")
    treffer = re.search(rb'href="(/monat/[\d-]+/woche/[\d-]+/einkauf)"', seite.data)
    assert treffer, "Auf der Monatsseite steht kein Einkaufs-Link"
    return treffer.group(1).decode()


def test_monatsseite_verlinkt_den_einkauf_je_woche(angemeldet, plan):
    seite = angemeldet.get(f"/monat/{MONAT}")
    links = re.findall(rb"/monat/[\d-]+/woche/([\d-]+)/einkauf", seite.data)
    assert len(links) == len(plan.weeks)
    assert {w.start.isoformat() for w in plan.weeks} == {x.decode() for x in links}


def test_einkaufsseite_fuehrt_alles_auf_einem_zettel(angemeldet, config, rezepte, zutaten, plan):
    """Frischware und Vorrat stehen zusammen, nach Abteilung statt nach Herkunft.

    Früher waren das zwei Überschriften auf einer Seite. Im Laden läuft man
    aber einen Weg: deshalb ein Zettel, sortiert wie die Regale.
    """
    woche = plan.weeks[0]
    frisch = shopping.frischeliste(woche, rezepte, zutaten, config)
    vorrat = shopping.wochen_pantry_liste(woche, rezepte, zutaten, config)

    antwort = angemeldet.get(_einkaufspfad(angemeldet))
    text = antwort.get_data(as_text=True)
    assert antwort.status_code == 200
    assert "Frischeliste" not in text
    assert "Vorrat diese Woche" not in text
    assert frisch.items and vorrat.items, "Die Testwoche braucht beide Arten"
    assert frisch.items[0].name in text
    assert vorrat.items[0].name in text


def test_einkaufsseite_nennt_die_zutaten_der_woche(angemeldet, config, rezepte, zutaten, plan):
    """Was auf dem Zettel steht, muss aus dem Plan dieser Woche kommen."""
    from essensplaner import shopping

    woche = plan.weeks[0]
    erwartet = shopping.frischeliste(woche, rezepte, zutaten, config)
    text = angemeldet.get(f"/monat/{MONAT}/woche/{woche.start.isoformat()}/einkauf").get_data(
        as_text=True
    )
    assert erwartet.items, "Die Testwoche braucht Frischware"
    for item in erwartet.items:
        assert item.name in text, f"{item.name} fehlt auf dem Zettel"


def test_einkauf_verlangt_anmeldung(client, plan):
    pfad = f"/monat/{MONAT}/woche/{plan.weeks[0].start.isoformat()}/einkauf"
    antwort = client.get(pfad)
    assert antwort.status_code == 302
    assert "/anmelden" in antwort.headers["Location"]


def test_einkauf_fuer_unbekannte_woche_ist_nicht_gefunden(angemeldet):
    assert angemeldet.get(f"/monat/{MONAT}/woche/2099-01-01/einkauf").status_code == 404


def test_einkauf_fuer_monat_ohne_plan_ist_nicht_gefunden(angemeldet):
    assert angemeldet.get("/monat/2030-05/woche/2030-04-26/einkauf").status_code == 404


# ---------------------------------------------------------- Rezeptkatalog


def test_rezeptliste_zeigt_alle_gerichte(angemeldet, rezepte):
    text = angemeldet.get("/rezepte").get_data(as_text=True)
    assert f"{len(rezepte)} Gerichte" in text
    assert "Pizza (selbstgemacht)" in text


def test_rezeptseite_zeigt_zutaten_und_schritte(angemeldet, rezepte, zutaten):
    antwort = angemeldet.get("/rezepte/pizza")
    text = antwort.get_data(as_text=True)
    assert antwort.status_code == 200
    rezept = rezepte["pizza"]
    for zeile in rezept.ingredients:
        assert zutaten[zeile.item].name in text, f"{zeile.item} fehlt"
    assert rezept.steps[0][:30] in text


def test_monatsseite_verlinkt_die_rezepte(angemeldet, plan):
    text = angemeldet.get(f"/monat/{MONAT}").get_data(as_text=True)
    for meal in plan.alle_meals:
        assert f"/rezepte/{meal.recipe_slug}" in text


def test_unbekanntes_rezept_ist_nicht_gefunden(angemeldet):
    assert angemeldet.get("/rezepte/gibt-es-nicht").status_code == 404


def test_streichen_nimmt_das_gericht_aus_der_planung(angemeldet, config, plans_dir):
    from essensplaner import katalog

    seite = angemeldet.get("/rezepte/pizza")
    antwort = angemeldet.post(
        "/rezepte/pizza/streichen", data={"csrf": _token(seite.data)}, follow_redirects=True
    )
    assert antwort.status_code == 200
    assert "pizza" in katalog.lade_gestrichen(config)
    assert "gestrichen" in angemeldet.get("/rezepte/pizza").get_data(as_text=True)


def test_gestrichenes_gericht_laesst_sich_wieder_aufnehmen(angemeldet, config):
    from essensplaner import katalog

    seite = angemeldet.get("/rezepte/pizza")
    angemeldet.post("/rezepte/pizza/streichen", data={"csrf": _token(seite.data)})
    seite = angemeldet.get("/rezepte/pizza")
    angemeldet.post("/rezepte/pizza/aufnehmen", data={"csrf": _token(seite.data)})
    assert katalog.lade_gestrichen(config) == set()


def test_streichen_ohne_token_passiert_nichts(angemeldet, config):
    from essensplaner import katalog

    assert angemeldet.post("/rezepte/pizza/streichen").status_code == 400
    assert katalog.lade_gestrichen(config) == set()


def _formular(**abweichend):
    daten = {
        "name": "Testauflauf mit Zucchini",
        "slots": "SA",
        "prep_minutes": "35",
        "base_servings": "4",
        "tags": "kids_ok",
        "zutaten": "800 g Zucchini\n200 g Feta\n1 Stk Baguette",
        "schritte": "Gemüse schneiden.\nBacken.",
    }
    daten.update(abweichend)
    return daten


def test_rezept_anlegen_schreibt_es_ins_volume(angemeldet, config, plans_dir):
    from essensplaner import katalog

    seite = angemeldet.get("/rezepte/neu")
    antwort = angemeldet.post(
        "/rezepte/neu", data=_formular(csrf=_token(seite.data)), follow_redirects=True
    )
    assert antwort.status_code == 200
    assert (plans_dir / "rezepte" / "testauflauf-mit-zucchini.yaml").is_file()
    assert "testauflauf-mit-zucchini" in katalog.lade_eigene(config)
    assert "Testauflauf mit Zucchini" in angemeldet.get("/rezepte").get_data(as_text=True)


def test_angelegtes_rezept_wird_sofort_eingeplant(angemeldet, plans_dir):
    """Ohne Neuladen des Katalogs bliebe es bis zum Neustart unsichtbar."""
    seite = angemeldet.get("/rezepte/neu")
    angemeldet.post("/rezepte/neu", data=_formular(csrf=_token(seite.data), slots="SA"))
    text = angemeldet.get("/rezepte/testauflauf-mit-zucchini").get_data(as_text=True)
    assert "eigenes Rezept" in text
    assert "Zucchini" in text


def test_fehlerhaftes_rezept_wird_nicht_gespeichert(angemeldet, plans_dir):
    seite = angemeldet.get("/rezepte/neu")
    antwort = angemeldet.post(
        "/rezepte/neu",
        data=_formular(csrf=_token(seite.data), zutaten="100 g Drachenfrucht"),
        follow_redirects=True,
    )
    assert "nicht bekannt" in antwort.get_data(as_text=True)
    assert not (plans_dir / "rezepte").exists() or not list((plans_dir / "rezepte").glob("*.yaml"))


def test_rezept_gegen_eine_harte_regel_wird_abgelehnt(angemeldet, plans_dir):
    """Ein Montagsgericht unter 30 g Protein darf gar nicht erst entstehen."""
    seite = angemeldet.get("/rezepte/neu")
    antwort = angemeldet.post(
        "/rezepte/neu",
        data=_formular(csrf=_token(seite.data), slots="MO", zutaten="800 g Zucchini"),
        follow_redirects=True,
    )
    text = antwort.get_data(as_text=True)
    assert "Protein" in text
    assert (
        not list((plans_dir / "rezepte").glob("*.yaml"))
        if (plans_dir / "rezepte").exists()
        else True
    )


def test_doppelter_kurzname_wird_abgelehnt(angemeldet):
    seite = angemeldet.get("/rezepte/neu")
    antwort = angemeldet.post(
        "/rezepte/neu",
        # "Pizza" ergibt den Kurznamen "pizza" - den gibt es schon im Repo.
        data=_formular(csrf=_token(seite.data), name="Pizza"),
        follow_redirects=True,
    )
    assert "schon ein Rezept" in antwort.get_data(as_text=True)


def test_eigenes_rezept_laesst_sich_loeschen(angemeldet, plans_dir):
    seite = angemeldet.get("/rezepte/neu")
    angemeldet.post("/rezepte/neu", data=_formular(csrf=_token(seite.data)))
    seite = angemeldet.get("/rezepte/testauflauf-mit-zucchini")
    antwort = angemeldet.post(
        "/rezepte/testauflauf-mit-zucchini/loeschen",
        data={"csrf": _token(seite.data)},
        follow_redirects=True,
    )
    assert antwort.status_code == 200
    assert not (plans_dir / "rezepte" / "testauflauf-mit-zucchini.yaml").exists()


def test_repo_rezept_laesst_sich_nicht_loeschen(angemeldet):
    """Die liegen schreibgeschützt im Image – dafür gibt es das Streichen."""
    seite = angemeldet.get("/rezepte/pizza")
    assert (
        angemeldet.post("/rezepte/pizza/loeschen", data={"csrf": _token(seite.data)}).status_code
        == 404
    )


# ---------------------------------------------------------- Verschieben


def _paar(planner, plan):
    """Ein Termin mit Tauschpartner, als Pfad und Formularwert."""
    for meal in plan.alle_meals:
        partner = planner.tauschpartner(plan, meal.datum, meal.slot)
        if partner:
            ziel = partner[0]
            pfad = f"/monat/{MONAT}/{meal.datum}/{meal.slot}"
            return meal, ziel, pfad, f"{ziel.datum}_{ziel.slot}"
    raise AssertionError("Kein Termin im Plan hat einen Tauschpartner")


def test_aenderungsseite_bietet_das_verschieben_an(angemeldet, planner, plan):
    _, ziel, pfad, wert = _paar(planner, plan)
    text = angemeldet.get(pfad).get_data(as_text=True)
    assert "Auf einen anderen Tag schieben" in text
    assert wert in text
    assert ziel.recipe_name in text


def test_verschieben_tauscht_die_beiden_gerichte(
    angemeldet, planner, plan, plans_dir, rezepte, zutaten, config
):
    meal, ziel, pfad, wert = _paar(planner, plan)
    token = _token(angemeldet.get(pfad).data)

    antwort = angemeldet.post(f"{pfad}/verschieben", data={"csrf": token, "ziel": wert})
    assert antwort.status_code == 302

    gespeichert = data.load_plan(plans_dir, MONAT)
    hier = next(m for m in gespeichert.alle_meals if m.datum == meal.datum and m.slot == meal.slot)
    dort = next(m for m in gespeichert.alle_meals if m.datum == ziel.datum and m.slot == ziel.slot)
    assert hier.recipe_slug == ziel.recipe_slug
    assert dort.recipe_slug == meal.recipe_slug
    assert rules.nur_fehler(rules.pruefe_plan(gespeichert, rezepte, zutaten, config)) == []


def test_verschieben_ohne_token_passiert_nichts(angemeldet, planner, plan, plans_dir):
    _, _, pfad, wert = _paar(planner, plan)
    vorher = data.load_plan(plans_dir, MONAT).to_dict()
    assert angemeldet.post(f"{pfad}/verschieben", data={"ziel": wert}).status_code == 400
    assert data.load_plan(plans_dir, MONAT).to_dict() == vorher


def test_unsinniges_ziel_laesst_den_plan_stehen(angemeldet, planner, plan, plans_dir):
    _, _, pfad, _ = _paar(planner, plan)
    token = _token(angemeldet.get(pfad).data)
    vorher = data.load_plan(plans_dir, MONAT).to_dict()
    antwort = angemeldet.post(
        f"{pfad}/verschieben", data={"csrf": token, "ziel": "irgendwas"}, follow_redirects=True
    )
    assert "Kein gültiger Tag" in antwort.get_data(as_text=True)
    assert data.load_plan(plans_dir, MONAT).to_dict() == vorher


def test_unmoegliches_verschieben_laesst_den_plan_stehen(angemeldet, planner, plan, plans_dir):
    """Ein Platz, den die Seite nicht anbietet, wird auch nicht angenommen."""
    meal = plan.alle_meals[0]
    erlaubt = {(p.datum, p.slot) for p in planner.tauschpartner(plan, meal.datum, meal.slot)}
    unmoeglich = next(
        m
        for m in plan.alle_meals
        if (m.datum, m.slot) not in erlaubt and (m.datum, m.slot) != (meal.datum, meal.slot)
    )
    pfad = f"/monat/{MONAT}/{meal.datum}/{meal.slot}"
    token = _token(angemeldet.get(pfad).data)
    vorher = data.load_plan(plans_dir, MONAT).to_dict()

    antwort = angemeldet.post(
        f"{pfad}/verschieben",
        data={"csrf": token, "ziel": f"{unmoeglich.datum}_{unmoeglich.slot}"},
        follow_redirects=True,
    )
    assert antwort.status_code == 200
    assert data.load_plan(plans_dir, MONAT).to_dict() == vorher


# ------------------------------------------------------------ Merkliste


def test_merkliste_ist_zunaechst_leer(angemeldet):
    assert "Noch nichts vorgemerkt" in angemeldet.get("/merkliste").get_data(as_text=True)


def test_merkliste_verlangt_anmeldung(client):
    antwort = client.get("/merkliste")
    assert antwort.status_code == 302
    assert "/anmelden" in antwort.headers["Location"]


def test_vormerken_ohne_token_passiert_nichts(angemeldet, config):
    assert angemeldet.post("/merkliste/pizza/merken").status_code == 400
    assert katalog.lade_merkliste(config) == []


def test_vormerken_zeigt_das_gericht_auf_der_merkliste(angemeldet, config, rezepte):
    seite = angemeldet.get("/rezepte/pizza")
    assert "Vormerken" in seite.get_data(as_text=True)
    antwort = angemeldet.post(
        "/merkliste/pizza/merken", data={"csrf": _token(seite.data)}, follow_redirects=True
    )
    assert antwort.status_code == 200
    assert katalog.lade_merkliste(config) == ["pizza"]
    assert rezepte["pizza"].name in angemeldet.get("/merkliste").get_data(as_text=True)
    assert "vorgemerkt" in angemeldet.get("/rezepte/pizza").get_data(as_text=True)


def test_vormerken_eines_unbekannten_rezepts_ist_nicht_gefunden(angemeldet):
    seite = angemeldet.get("/merkliste")
    antwort = angemeldet.post("/merkliste/gibt-es-nicht/merken", data={"csrf": _token(seite.data)})
    assert antwort.status_code == 404


def test_herunternehmen_leert_die_merkliste(angemeldet, config):
    seite = angemeldet.get("/rezepte/pizza")
    angemeldet.post("/merkliste/pizza/merken", data={"csrf": _token(seite.data)})
    seite = angemeldet.get("/merkliste")
    angemeldet.post("/merkliste/pizza/vergessen", data={"csrf": _token(seite.data)})
    assert katalog.lade_merkliste(config) == []


def _vorgemerkter_tausch(client, planner, plan):
    """Ein Slot mit Alternative – und diese Alternative vorgemerkt."""
    for meal in plan.alle_meals:
        alternativen = planner.alternativen(plan, meal.datum, meal.slot)
        if alternativen:
            kandidat = alternativen[0]
            seite = client.get(f"/rezepte/{kandidat.slug}")
            client.post(f"/merkliste/{kandidat.slug}/merken", data={"csrf": _token(seite.data)})
            return meal, kandidat, f"/monat/{MONAT}/{meal.datum}/{meal.slot}"
    raise AssertionError("Kein Slot im Plan hat eine Alternative")


def test_vorgemerktes_gericht_steht_beim_aendern_ganz_oben(angemeldet, planner, plan):
    _, kandidat, pfad = _vorgemerkter_tausch(angemeldet, planner, plan)
    text = angemeldet.get(pfad).get_data(as_text=True)
    assert "Von der Merkliste" in text
    # Vor den anderen Gerichten, damit man es zuerst sieht.
    assert text.index("Von der Merkliste") < text.index("Anderes Gericht")
    assert text.index(kandidat.name) < text.index("Anderes Gericht")


def test_eingeplantes_gericht_verlaesst_die_merkliste(angemeldet, planner, plan, config):
    _, kandidat, pfad = _vorgemerkter_tausch(angemeldet, planner, plan)
    token = _token(angemeldet.get(pfad).data)
    antwort = angemeldet.post(
        pfad, data={"csrf": token, "rezept": kandidat.slug}, follow_redirects=True
    )
    assert "Von der Merkliste genommen" in antwort.get_data(as_text=True)
    assert katalog.lade_merkliste(config) == []


def test_ohne_kreuz_sammelt_die_merkliste_nichts(angemeldet, planner, plan, config):
    _, kandidat, pfad = _vorgemerkter_tausch(angemeldet, planner, plan)
    token = _token(angemeldet.get(pfad).data)
    angemeldet.post(pfad, data={"csrf": token, "rezept": kandidat.slug})
    assert katalog.lade_merkliste(config) == []


def test_mit_kreuz_wird_das_ersetzte_gericht_vorgemerkt(
    angemeldet, planner, plan, plans_dir, config
):
    meal, kandidat, pfad = _vorgemerkter_tausch(angemeldet, planner, plan)
    seite = angemeldet.get(pfad)
    assert "auf die Merkliste setzen" in seite.get_data(as_text=True)
    angemeldet.post(
        pfad, data={"csrf": _token(seite.data), "rezept": kandidat.slug, "merken": "on"}
    )
    assert katalog.lade_merkliste(config) == [meal.recipe_slug]
    gespeichert = data.load_plan(plans_dir, MONAT)
    danach = next(
        m for m in gespeichert.alle_meals if m.datum == meal.datum and m.slot == meal.slot
    )
    assert danach.recipe_slug == kandidat.slug


def test_geloeschtes_eigenes_rezept_verlaesst_die_merkliste(angemeldet, config, plans_dir):
    seite = angemeldet.get("/rezepte/neu")
    angemeldet.post("/rezepte/neu", data=_formular(csrf=_token(seite.data)))
    slug = "testauflauf-mit-zucchini"
    seite = angemeldet.get(f"/rezepte/{slug}")
    angemeldet.post(f"/merkliste/{slug}/merken", data={"csrf": _token(seite.data)})
    assert katalog.lade_merkliste(config) == [slug]

    seite = angemeldet.get(f"/rezepte/{slug}")
    angemeldet.post(f"/rezepte/{slug}/loeschen", data={"csrf": _token(seite.data)})
    assert katalog.lade_merkliste(config) == []


# --------------------------------------------------------- Grundeinkauf


def test_grundeinkauf_zeigt_zuerst_einen_vorschlag(angemeldet):
    text = angemeldet.get("/grundeinkauf").get_data(as_text=True)
    assert "nur ein Vorschlag" in text
    assert "Milch" in text


def test_grundeinkauf_verlangt_anmeldung(client):
    antwort = client.get("/grundeinkauf")
    assert antwort.status_code == 302
    assert "/anmelden" in antwort.headers["Location"]


def test_grundeinkauf_speichern_wirkt_auf_den_einkaufszettel(angemeldet, config):
    seite = angemeldet.get("/grundeinkauf")
    antwort = angemeldet.post(
        "/grundeinkauf",
        data={"csrf": _token(seite.data), "zeilen": "Haushalt:" + NL + "Kaffeefilter"},
        follow_redirects=True,
    )
    assert "gespeichert" in antwort.get_data(as_text=True)
    assert shopping.lade_grundeinkauf(config) == (["Haushalt:", "Kaffeefilter"], True)

    einkauf = angemeldet.get(_einkaufspfad(angemeldet)).get_data(as_text=True)
    assert "Kaffeefilter" in einkauf
    # Die eigene Überschrift bleibt als Gruppe stehen, statt in "Sonstiges"
    # zu verschwinden: wer sie hinschreibt, will sie auf dem Zettel sehen.
    assert "Haushalt" in einkauf
    assert "immer dabei" in einkauf


def test_grundeinkauf_ohne_token_wird_abgewiesen(angemeldet, config):
    assert angemeldet.post("/grundeinkauf", data={"zeilen": "Milch"}).status_code == 400
    assert shopping.lade_grundeinkauf(config)[1] is False


def test_zu_lange_liste_wird_nicht_gespeichert(angemeldet, config):
    seite = angemeldet.get("/grundeinkauf")
    text = NL.join(f"Posten {n}" for n in range(shopping.GRUNDEINKAUF_MAX_ZEILEN + 1))
    antwort = angemeldet.post(
        "/grundeinkauf",
        data={"csrf": _token(seite.data), "zeilen": text},
        follow_redirects=True,
    )
    assert "zu viele" in antwort.get_data(as_text=True)
    assert shopping.lade_grundeinkauf(config)[1] is False


def test_einkaufszettel_nennt_den_vorschlag_als_solchen(angemeldet):
    assert "Vorschlag" in angemeldet.get(_einkaufspfad(angemeldet)).get_data(as_text=True)


def test_doppeltes_im_grundeinkauf_wird_angemerkt(angemeldet, plan, rezepte, zutaten, config):
    """Was schon ausgerechnet oben steht, bekommt einen Hinweis."""
    woche = plan.weeks[0]
    frisch = shopping.frischeliste(woche, rezepte, zutaten, config)
    schon_da = frisch.items[0].name

    seite = angemeldet.get("/grundeinkauf")
    angemeldet.post(
        "/grundeinkauf",
        data={"csrf": _token(seite.data), "zeilen": schon_da + NL + "Klopapier"},
    )
    text = angemeldet.get(f"/monat/{MONAT}/woche/{woche.start}/einkauf").get_data(as_text=True)
    assert "Klopapier" in text
    assert "steht schon als Rezeptmenge drauf" in text


# ----------------------------------------------------- Ziehen und Ablegen


def _tauschkarte(text: str) -> dict:
    """Die Karte der erlaubten Ziele aus der Monatsseite lesen."""
    marke = '<script id="tauschdaten" type="application/json">'
    start = text.index(marke) + len(marke)
    return json.loads(text[start : text.index("</script>", start)])


def test_monatsseite_schickt_die_erlaubten_ziele_mit(angemeldet, planner, plan):
    """Beim Ziehen muss sofort zu sehen sein, wohin ein Gericht darf."""
    karte = _tauschkarte(angemeldet.get(f"/monat/{MONAT}").get_data(as_text=True))
    assert set(karte) == {f"{m.datum}_{m.slot}" for m in plan.alle_meals}
    for meal in plan.alle_meals:
        erwartet = [
            f"{p.datum}_{p.slot}" for p in planner.tauschpartner(plan, meal.datum, meal.slot)
        ]
        assert karte[f"{meal.datum}_{meal.slot}"] == erwartet


def test_die_karte_gilt_in_beide_richtungen(angemeldet):
    """Wer A als Ziel von B nennt, muss B auch als Ziel von A nennen."""
    karte = _tauschkarte(angemeldet.get(f"/monat/{MONAT}").get_data(as_text=True))
    for platz, ziele in karte.items():
        for ziel in ziele:
            assert platz in karte[ziel], f"{platz} nach {ziel} geht, zurück nicht"


def test_ein_griff_nur_wo_es_ein_ziel_gibt(angemeldet):
    """Ein Griff, der nirgendwo hinführt, ist eine Enttäuschung."""
    text = angemeldet.get(f"/monat/{MONAT}").get_data(as_text=True)
    karte = _tauschkarte(text)
    mit_ziel = sum(1 for ziele in karte.values() if ziele)
    assert text.count('class="griff"') == mit_ziel


def test_ziehen_landet_im_selben_pfad_wie_der_knopf(angemeldet, planner, plan):
    """Das Ablegen nutzt dieselbe geprüfte Route, nicht eine zweite."""
    text = angemeldet.get(f"/monat/{MONAT}").get_data(as_text=True)
    assert 'id="verschiebeform"' in text
    assert f'data-basis="/monat/{MONAT}"' in text

    meal, _, pfad, wert = _paar(planner, plan)
    assert f'data-platz="{meal.datum}_{meal.slot}"' in text
    # So setzt das Skript die Adresse zusammen - sie muss es wirklich geben.
    assert f"/monat/{MONAT}/{meal.datum}/{meal.slot}" == pfad
    token = _token(angemeldet.get(f"/monat/{MONAT}").data)
    antwort = angemeldet.post(f"{pfad}/verschieben", data={"csrf": token, "ziel": wert})
    assert antwort.status_code == 302


def test_monat_ohne_plan_kommt_ohne_karte_aus(angemeldet):
    text = angemeldet.get("/monat/2030-05").get_data(as_text=True)
    assert "tauschdaten" not in text
    assert "griff" not in text


def test_der_vorschlag_laesst_sich_zurueckholen(angemeldet, config):
    """Nach dem ersten Speichern muss man wieder an die Grundliste kommen."""
    seite = angemeldet.get("/grundeinkauf")
    angemeldet.post(
        "/grundeinkauf",
        data={"csrf": _token(seite.data), "zeilen": "Haushalt:" + NL + "Müllbeutel"},
    )
    assert shopping.lade_grundeinkauf(config) == (["Haushalt:", "Müllbeutel"], True)

    text = angemeldet.get("/grundeinkauf?vorschlag=1").get_data(as_text=True)
    assert "Kühlschrank:" in text
    assert "Grundnahrungsmittel zum Durchgehen" in text
    # Nur im Feld, noch nicht gespeichert.
    assert shopping.lade_grundeinkauf(config) == (["Haushalt:", "Müllbeutel"], True)


def test_der_knopf_steht_da_wo_er_hilft(angemeldet):
    """Zurück zum Vorschlag braucht nur, wer eine eigene Liste hat."""
    seite = angemeldet.get("/grundeinkauf")
    assert "vorschlag=1" not in seite.get_data(as_text=True)

    angemeldet.post("/grundeinkauf", data={"csrf": _token(seite.data), "zeilen": "Müllbeutel"})
    assert "vorschlag=1" in angemeldet.get("/grundeinkauf").get_data(as_text=True)
    assert "vorschlag=1" not in angemeldet.get("/grundeinkauf?vorschlag=1").get_data(as_text=True)


def test_das_wochenende_blockiert_das_verschieben_nicht(angemeldet, planner, plan, config):
    """Das süße Wochenendgericht darf per Hand auf einen Werktag.

    Vorher bot die Seite den Tausch an und lehnte ihn beim Ablegen ab.
    """
    geprueft = 0
    for meal in plan.alle_meals:
        rezept = planner.rezepte[meal.recipe_slug]
        ist_highlight = any(rezept.hat_tag(t) for t in config.wochenende_pflicht_tags)
        if meal.slot not in ("SA", "SO") or not ist_highlight:
            continue
        for partner in planner.tauschpartner(plan, meal.datum, meal.slot):
            if partner.slot in ("SA", "SO"):
                continue
            pfad = f"/monat/{MONAT}/{meal.datum}/{meal.slot}"
            token = _token(angemeldet.get(pfad).data)
            antwort = angemeldet.post(
                f"{pfad}/verschieben",
                data={"csrf": token, "ziel": f"{partner.datum}_{partner.slot}"},
                follow_redirects=True,
            )
            assert "Getauscht" in antwort.get_data(as_text=True)
            geprueft += 1
            break
        if geprueft:
            break
    assert geprueft, "Der Plan hat keinen solchen Tausch - der Test prüft nichts"


# ------------------------------------------------- Wochenansicht und Abwahl


def _wochenpfad(plan) -> str:
    return f"/monat/{MONAT}/woche/{plan.weeks[0].start.isoformat()}"


def test_wochenseite_zeigt_nur_diese_woche(angemeldet, plan):
    """Die Arbeitsfläche für eine Woche: alle Gerichte dieser Woche, keine fremden."""
    woche = plan.weeks[0]
    text = angemeldet.get(_wochenpfad(plan)).get_data(as_text=True)
    for meal in woche.meals:
        assert meal.recipe_name in text, f"{meal.recipe_name} fehlt in der Woche"
    spaeter = {m.recipe_name for w in plan.weeks[1:] for m in w.meals}
    diese = {m.recipe_name for m in woche.meals}
    for name in spaeter - diese:
        assert name not in text, f"{name} gehört in eine andere Woche"


def test_wochenseite_verlinkt_nachbarn_und_einkauf(angemeldet, plan):
    text = angemeldet.get(_wochenpfad(plan)).get_data(as_text=True)
    zweite = plan.weeks[1].start.isoformat()
    assert f"/monat/{MONAT}/woche/{zweite}" in text
    assert f"/monat/{MONAT}/woche/{plan.weeks[0].start.isoformat()}/einkauf" in text


def test_wochenseite_sagt_wohin_vorgemerktes_passt(angemeldet, plan, config, rezepte):
    """Der Backlog nützt nur, wenn man sieht, wo das Gericht hinkann."""
    woche = plan.weeks[0]
    gericht = rezepte[woche.meals[0].recipe_slug]
    katalog.merken(config, gericht.slug)

    text = angemeldet.get(_wochenpfad(plan)).get_data(as_text=True)
    assert gericht.name in text
    assert "passt diese Woche auf" in text or "passt diese Woche auf keinen Tag" in text


def test_wochenseite_nimmt_jeden_tag_der_woche(angemeldet, plan):
    """Ein Tag mitten in der Woche öffnet dieselbe Woche wie ihr Freitag.

    Dasselbe Verhalten wie beim Einkaufszettel: wer ein Datum aus der URL
    abschreibt, soll nicht auf einer Fehlerseite landen, nur weil es kein
    Freitag war.
    """
    woche = plan.weeks[0]
    mittendrin = woche.start + timedelta(days=3)
    text = angemeldet.get(f"/monat/{MONAT}/woche/{mittendrin.isoformat()}").get_data(as_text=True)
    assert f"Woche ab {woche.start.day}" in text or woche.meals[0].recipe_name in text


def test_wochenseite_kennt_keine_erfundene_woche(angemeldet):
    assert angemeldet.get(f"/monat/{MONAT}/woche/2099-01-01").status_code == 404
    assert angemeldet.get(f"/monat/{MONAT}/woche/kein-datum").status_code == 404


def test_wochenseite_verlangt_anmeldung(client, plan):
    antwort = client.get(_wochenpfad(plan))
    assert antwort.status_code == 302
    assert "/anmelden" in antwort.headers["Location"]


def test_posten_abwaehlen_und_wieder_anwaehlen(angemeldet, plan, config, rezepte, zutaten):
    """Was noch zu Hause ist, verschwindet vom Zettel – und kommt zurück."""
    woche = plan.weeks[0]
    pfad = f"/monat/{MONAT}/woche/{woche.start.isoformat()}/einkauf"
    frisch = shopping.frischeliste(woche, rezepte, zutaten, config)
    key = f"frisch:{frisch.items[0].slug}"

    seite = angemeldet.get(pfad)
    assert (
        angemeldet.post(
            pfad + "/umschalten", data={"csrf": _token(seite.data), "key": key}
        ).status_code
        == 302
    )
    assert shopping.lade_abwahl(config, woche.start.isoformat()) == {key}
    assert "abgewählt" in angemeldet.get(pfad).get_data(as_text=True)

    seite = angemeldet.get(pfad)
    angemeldet.post(pfad + "/umschalten", data={"csrf": _token(seite.data), "key": key})
    assert shopping.lade_abwahl(config, woche.start.isoformat()) == set()


def test_abwahl_gilt_nur_fuer_ihre_woche(angemeldet, plan, config):
    """Zwei Wochen, zwei Zettel: der Haken der einen gilt der anderen nicht."""
    erste, zweite = plan.weeks[0], plan.weeks[1]
    pfad = f"/monat/{MONAT}/woche/{erste.start.isoformat()}/einkauf"
    seite = angemeldet.get(pfad)
    angemeldet.post(pfad + "/umschalten", data={"csrf": _token(seite.data), "key": "grund:Milch"})

    assert shopping.lade_abwahl(config, erste.start.isoformat()) == {"grund:Milch"}
    assert shopping.lade_abwahl(config, zweite.start.isoformat()) == set()


def test_abwahl_zuruecksetzen_raeumt_nur_diese_woche(angemeldet, plan, config):
    erste, zweite = plan.weeks[0], plan.weeks[1]
    shopping.speichere_abwahl(config, erste.start.isoformat(), {"grund:Milch"})
    shopping.speichere_abwahl(config, zweite.start.isoformat(), {"grund:Butter"})

    pfad = f"/monat/{MONAT}/woche/{erste.start.isoformat()}/einkauf"
    seite = angemeldet.get(pfad)
    antwort = angemeldet.post(
        pfad + "/zuruecksetzen", data={"csrf": _token(seite.data)}, follow_redirects=True
    )
    assert antwort.status_code == 200
    assert shopping.lade_abwahl(config, erste.start.isoformat()) == set()
    assert shopping.lade_abwahl(config, zweite.start.isoformat()) == {"grund:Butter"}


def test_abwaehlen_ohne_token_wird_abgewiesen(angemeldet, plan, config):
    woche = plan.weeks[0]
    pfad = f"/monat/{MONAT}/woche/{woche.start.isoformat()}/einkauf/umschalten"
    assert angemeldet.post(pfad, data={"key": "grund:Milch"}).status_code == 400
    assert shopping.lade_abwahl(config, woche.start.isoformat()) == set()


def test_abwaehlen_verlangt_anmeldung(client, plan):
    woche = plan.weeks[0]
    pfad = f"/monat/{MONAT}/woche/{woche.start.isoformat()}/einkauf/umschalten"
    antwort = client.post(pfad, data={"key": "grund:Milch"})
    assert antwort.status_code == 302
    assert "/anmelden" in antwort.headers["Location"]
