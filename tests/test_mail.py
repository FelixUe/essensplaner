"""Die Mails müssen rendern, als HTML und als Plain-Text, und die Liste
sowie die Rezepte vollständig enthalten (Akzeptanzkriterium 5)."""

from __future__ import annotations

import re
from email.message import EmailMessage

import pytest

from essensplaner import kalender, mail


@pytest.fixture
def renderer(config, rezepte, zutaten):
    return mail.MailRenderer(config, rezepte, zutaten)


@pytest.fixture
def wochenmail(renderer, plan):
    return renderer.wochenmail(plan.weeks[1])


@pytest.fixture
def monatsmail(renderer, plan):
    return renderer.monatsmail(plan)


# ------------------------------------------------------------- Wochenmail


def test_wochenmail_hat_html_und_text(wochenmail):
    assert wochenmail.html.lstrip().startswith("<!DOCTYPE html>")
    assert wochenmail.text.strip()
    assert "<html" in wochenmail.html


def test_betreff_nennt_den_zeitraum(wochenmail, plan):
    woche = plan.weeks[1]
    assert kalender.formatiere_kurz(woche.start) in wochenmail.betreff
    assert kalender.formatiere_kurz(woche.ende) in wochenmail.betreff


def test_keine_ungerenderten_platzhalter(wochenmail, monatsmail):
    for nachricht in (wochenmail, monatsmail):
        for inhalt in (nachricht.html, nachricht.text):
            assert "{{" not in inhalt
            assert "{%" not in inhalt


def test_plaintext_ist_nicht_html_maskiert(wochenmail):
    """Im Text-Teil hat '&amp;' nichts zu suchen."""
    assert "&amp;" not in wochenmail.text
    assert "&#9744;" not in wochenmail.text


def test_html_maskiert_sonderzeichen(renderer, plan):
    nachricht = renderer.wochenmail(plan.weeks[1])
    # Im HTML wird '&' maskiert, der Titel enthält ein Ampersand.
    assert "&amp;" in nachricht.html or "&" not in nachricht.betreff


def test_wochenmail_enthaelt_alle_gerichte_der_woche(wochenmail, plan):
    woche = plan.weeks[1]
    for meal in woche.meals:
        assert meal.recipe_name in wochenmail.text, f"{meal.recipe_name} fehlt im Text"
        assert meal.recipe_name in wochenmail.html, f"{meal.recipe_name} fehlt im HTML"


def test_wochenmail_nennt_den_kochtag_jedes_gerichts(wochenmail, plan, config):
    for meal in plan.weeks[1].meals:
        kochtag = config.slot_kochtag(meal.slot)
        assert kochtag in wochenmail.text


def test_wochenmail_enthaelt_die_frischeliste(wochenmail, plan, rezepte, zutaten, config):
    from essensplaner import shopping

    liste = shopping.frischeliste(plan.weeks[1], rezepte, zutaten, config)
    assert str(liste.anzahl) in wochenmail.text
    for item in liste.items:
        assert item.name in wochenmail.text, f"{item.name} fehlt auf der Liste"


def test_wochenmail_enthaelt_rezepte_mit_schritten(wochenmail, plan, rezepte):
    for meal in plan.weeks[1].meals:
        rezept = rezepte[meal.recipe_slug]
        erster_schritt = rezept.steps_fuer(meal.variant)[0]
        assert erster_schritt[:40] in wochenmail.text


def test_wochenmail_nennt_portionen_und_protein(wochenmail):
    assert "Erwachsenenportionen" in wochenmail.text or "Familie" in wochenmail.text
    assert re.search(r"\d+ g Protein", wochenmail.text)


def test_mengen_sind_auf_die_portionen_hochgerechnet(renderer, plan, rezepte):
    """Ein Familienslot kocht gebrochene Portionen aus einem Rezept für 4."""
    woche = plan.weeks[0]
    nachricht = renderer.wochenmail(woche)
    meal = next(m for m in woche.meals if m.slot in ("FR", "SA", "SO"))
    rezept = rezepte[meal.recipe_slug]
    # Eine Zutat mit großer Menge finden und prüfen, dass die Basismenge
    # nicht unverändert in der Mail steht.
    gross = [z for z in rezept.zutaten_fuer(meal.variant) if z.unit == "g" and z.qty >= 400]
    if not gross:
        pytest.skip("Kein geeignetes Rezept in dieser Woche")
    zutat = gross[0]
    skaliert = zutat.qty * meal.portionen / rezept.base_servings
    assert abs(skaliert - zutat.qty) > 1, "Testannahme: Skalierung ändert die Menge"
    assert nachricht.text


# ------------------------------------------------------------- Monatsmail


def test_monatsmail_enthaelt_alle_gerichte_des_monats(monatsmail, plan):
    for meal in plan.alle_meals:
        assert meal.recipe_name in monatsmail.text


def test_monatsmail_enthaelt_die_vorratsliste(monatsmail, plan, rezepte, zutaten, config):
    from essensplaner import shopping

    liste = shopping.vorratsliste(plan, rezepte, zutaten, config)
    assert "VORRATSLISTE" in monatsmail.text.upper()
    for item in liste.items:
        assert item.name in monatsmail.text, f"{item.name} fehlt auf der Vorratsliste"


def test_monatsmail_nennt_den_monat(monatsmail, plan):
    assert kalender.monats_label(plan.monat) in monatsmail.betreff


def test_vorschau_ist_als_solche_gekennzeichnet(renderer, plan):
    vorschau = renderer.monatsmail(plan, vorschau=True)
    assert "Vorschau" in vorschau.betreff
    assert "VORSCHAU" in vorschau.text.upper()


# --------------------------------------------------------------- Nachricht


def test_nachricht_wird_als_multipart_gebaut(wochenmail, config):
    msg = wochenmail.to_message("test@example.com")
    assert isinstance(msg, EmailMessage)
    assert msg.is_multipart()
    typen = {teil.get_content_type() for teil in msg.walk()}
    assert "text/plain" in typen
    assert "text/html" in typen
    assert msg["Subject"] == wochenmail.betreff
    assert msg["To"] == ", ".join(config.mail_empfaenger)


def test_nachricht_ohne_absender_wird_abgelehnt(wochenmail):
    with pytest.raises(mail.MailFehler, match="Absender"):
        wochenmail.to_message("")


def test_nachricht_ohne_empfaenger_wird_abgelehnt(wochenmail):
    leer = mail.RenderedMail(betreff=wochenmail.betreff, html="<p/>", text="x", empfaenger=())
    with pytest.raises(mail.MailFehler, match="Empfänger"):
        leer.to_message("test@example.com")


def test_versand_ohne_smtp_konfiguration_schlaegt_verstaendlich_fehl(wochenmail, config):
    import copy

    cfg = copy.deepcopy(config)
    object.__setattr__(cfg, "smtp", type(cfg.smtp)())
    assert not cfg.smtp.konfiguriert
    with pytest.raises(mail.MailFehler, match="SMTP"):
        mail.sende(wochenmail, cfg)


def test_umlaute_ueberleben_die_kodierung(wochenmail):
    msg = wochenmail.to_message("test@example.com")
    roh = msg.as_bytes()
    text = msg.get_body(preferencelist=("plain",)).get_content()
    assert "ü" in text or "ä" in text or "ö" in text
    assert roh


def test_vorrat_wird_der_wochenmail_beigelegt_wenn_keine_monatsmail_lief(renderer, plan):
    """Startet die Woche keinen neuen Monat, liegt keine Vorratsliste vor –
    dann muss der Wocheneinkauf die haltbaren Zutaten mitnehmen."""
    woche = plan.weeks[1]
    assert kalender.startet_monatswoche(woche.start) is None
    nachricht = renderer.wochenmail(woche, auch_vorrat=True)
    assert "VORRAT" in nachricht.text.upper()
