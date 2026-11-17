"""Die beiden geplanten Läufe und der Scheduler.

Hier wird nichts verschickt: ``mail_senden=False`` rendert nur. Der
Plan-Ordner liegt in tmp_path, damit kein Test ins Repo schreibt.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from essensplaner import data, kalender, runner
from essensplaner.runner import Kontext, RunnerFehler
from essensplaner.scheduler import Scheduler


@pytest.fixture
def tmp_config(config, tmp_path, monkeypatch):
    """Gleiche Konfiguration, aber Pläne landen in tmp_path."""
    monkeypatch.setenv("ESSENSPLANER_PLANS_DIR", str(tmp_path / "plans"))
    import copy

    return copy.deepcopy(config)


@pytest.fixture
def ctx(tmp_config, rezepte, zutaten):
    return Kontext(config=tmp_config, zutaten=zutaten, rezepte=rezepte, bestand={})


# ------------------------------------------------------------ generate-month


def test_generate_month_erzeugt_und_speichert(ctx):
    ergebnis = runner.generate_month(ctx, monat="2026-12", seed=3, mail_senden=False)
    assert ergebnis.plan_erzeugt
    assert ergebnis.plan_pfad and ergebnis.plan_pfad.is_file()
    assert ergebnis.mails and "Vorschau" in ergebnis.mails[0]
    assert not ergebnis.versendet


def test_generate_month_ohne_monat_nimmt_den_folgemonat(ctx):
    heute = date(2026, 11, 20)
    ergebnis = runner.generate_month(ctx, seed=3, mail_senden=False, heute=heute)
    assert ergebnis.monat == "2026-12"


def test_generate_month_ueberschreibt_nicht_ungefragt(ctx):
    runner.generate_month(ctx, monat="2026-12", seed=3, mail_senden=False)
    zweiter = runner.generate_month(ctx, monat="2026-12", seed=4, mail_senden=False)
    assert not zweiter.plan_erzeugt
    assert any("existiert bereits" in h for h in zweiter.hinweise)


def test_generate_month_mit_force_erzeugt_neu(ctx):
    runner.generate_month(ctx, monat="2026-12", seed=3, mail_senden=False)
    erste = data.load_plan(ctx.config.plans_dir, "2026-12")
    runner.generate_month(ctx, monat="2026-12", seed=999, force=True, mail_senden=False)
    zweite = data.load_plan(ctx.config.plans_dir, "2026-12")
    assert [m.recipe_slug for m in erste.alle_meals] != [m.recipe_slug for m in zweite.alle_meals]


def test_generate_month_lehnt_kaputtes_monatsformat_ab(ctx):
    with pytest.raises(ValueError):
        runner.generate_month(ctx, monat="Dezember", mail_senden=False)


def test_gespeicherter_plan_ist_regelkonform(ctx):
    """generate_month prüft den Plan vor dem Speichern noch einmal komplett."""
    from essensplaner import rules

    runner.generate_month(ctx, monat="2027-07", seed=8, mail_senden=False)
    plan = data.load_plan(ctx.config.plans_dir, "2027-07")
    fehler = rules.nur_fehler(rules.pruefe_plan(plan, ctx.rezepte, ctx.zutaten, ctx.config))
    assert not fehler


# --------------------------------------------------------------- send-weekly


def test_send_weekly_rendert_wochenmail(ctx):
    runner.generate_month(ctx, monat="2026-12", seed=3, mail_senden=False)
    ergebnis = runner.send_weekly(ctx, tag=date(2026, 12, 4), mail_senden=False)
    assert ergebnis.woche_start == date(2026, 12, 4)
    assert len(ergebnis.mails) == 1
    assert "04.12." in ergebnis.mails[0]


def test_send_weekly_funktioniert_an_jedem_wochentag(ctx):
    """Ein Lauf am Samstag soll die laufende Woche erwischen, nicht die nächste."""
    runner.generate_month(ctx, monat="2026-12", seed=3, mail_senden=False)
    for tag in (date(2026, 12, 4), date(2026, 12, 6), date(2026, 12, 10)):
        ergebnis = runner.send_weekly(ctx, tag=tag, mail_senden=False)
        assert ergebnis.woche_start == date(2026, 12, 4)


def test_send_weekly_schickt_zum_monatsbeginn_beide_mails(ctx):
    """Am letzten Freitag vor Monatsbeginn kommt die Vorratsliste dazu."""
    assert kalender.startet_monatswoche(date(2026, 11, 27)) == "2026-12"
    ergebnis = runner.send_weekly(ctx, tag=date(2026, 11, 27), seed=3, mail_senden=False)
    assert len(ergebnis.mails) == 2
    assert any("Vorratsliste" in b for b in ergebnis.mails)


def test_send_weekly_legt_vorrat_bei_wenn_keine_monatsmail_lief(ctx):
    """Mitten im Monat muss der Wocheneinkauf die haltbaren Zutaten mitnehmen."""
    runner.generate_month(ctx, monat="2026-12", seed=3, mail_senden=False)
    ergebnis = runner.send_weekly(ctx, tag=date(2026, 12, 11), mail_senden=False)
    assert len(ergebnis.mails) == 1
    renderer = ctx.renderer()
    plan = data.load_plan(ctx.config.plans_dir, "2026-12")
    woche = plan.woche_ab(date(2026, 12, 11))
    nachricht = renderer.wochenmail(woche, auch_vorrat=True)
    assert "VORRAT" in nachricht.text.upper()


def test_send_weekly_erzeugt_fehlenden_plan_statt_auszufallen(ctx):
    """Die Familie soll freitags nicht ohne Einkaufsliste dastehen."""
    assert not data.plan_vorhanden(ctx.config.plans_dir, "2026-12")
    ergebnis = runner.send_weekly(ctx, tag=date(2026, 12, 4), seed=3, mail_senden=False)
    assert ergebnis.plan_erzeugt
    assert any("noch keinen Plan" in h for h in ergebnis.hinweise)
    assert ergebnis.mails


def test_send_weekly_findet_die_woche_in_jedem_monat(ctx):
    """Für jede Planungswoche des Jahres muss eine Mail herauskommen."""
    for nr in (1, 3, 6, 9, 12):
        monat = kalender.monat_str(2027, nr)
        for start in kalender.planungswochen(2027, nr):
            ergebnis = runner.send_weekly(ctx, tag=start, seed=5, mail_senden=False)
            assert ergebnis.mails, f"{monat}, Woche ab {start}: keine Mail"
            assert ergebnis.woche_start == start


# ------------------------------------------------------------------- Zustand


def test_state_wird_gespeichert_und_gelesen(tmp_config):
    assert runner.lade_state(tmp_config) == {}
    runner.speichere_state(tmp_config, {"send-weekly": "2026-12-04"})
    assert runner.lade_state(tmp_config) == {"send-weekly": "2026-12-04"}


def test_kaputte_statusdatei_wird_ignoriert(tmp_config):
    tmp_config.plans_dir.mkdir(parents=True, exist_ok=True)
    (tmp_config.plans_dir / "state.json").write_text("kein json", encoding="utf-8")
    assert runner.lade_state(tmp_config) == {}


def test_kontext_laden_meldet_kaputte_stammdaten(tmp_path, config):
    import copy

    cfg = copy.deepcopy(config)
    # Ein Ausschluss, den die echten Daten garantiert treffen.
    cfg.raw["ausschluesse"] = {"alles": {"begriffe": ["salz"], "ausnahmen": []}}
    with pytest.raises(RunnerFehler, match="harte Regel"):
        Kontext.laden(cfg)


# ----------------------------------------------------------------- Scheduler


@pytest.fixture
def sched(tmp_config):
    """Scheduler ohne Mailserver - der Normalfall seit dem Betrieb ohne SMTP."""
    return Scheduler(tmp_config)


@pytest.fixture
def sched_mit_mail(tmp_config):
    """Scheduler mit konfiguriertem Mailserver, fuer die Versandpfade."""
    import dataclasses

    cfg = dataclasses.replace(
        tmp_config, smtp=dataclasses.replace(tmp_config.smtp, host="smtp.example.com")
    )
    return Scheduler(cfg)


def _freitag_morgen(tag: date, stunde: int = 7, minute: int = 5) -> datetime:
    return datetime(tag.year, tag.month, tag.day, stunde, minute, tzinfo=ZoneInfo("Europe/Berlin"))


def test_wochenmail_ist_freitags_nach_sieben_faellig(sched):
    freitag = date(2026, 12, 4)
    assert sched.wochenmail_faellig(_freitag_morgen(freitag), {}) == freitag


def test_wochenmail_ist_vor_sieben_nicht_faellig(sched):
    jetzt = _freitag_morgen(date(2026, 12, 4), stunde=6, minute=30)
    assert sched.wochenmail_faellig(jetzt, {}) is None


def test_wochenmail_ist_an_anderen_tagen_nicht_faellig(sched):
    for tag in (date(2026, 12, 3), date(2026, 12, 5), date(2026, 12, 7)):
        assert sched.wochenmail_faellig(_freitag_morgen(tag), {}) is None


def test_wochenmail_laeuft_pro_freitag_nur_einmal(sched):
    freitag = date(2026, 12, 4)
    state = {"send-weekly": freitag.isoformat()}
    assert sched.wochenmail_faellig(_freitag_morgen(freitag), state) is None


def test_wochenmail_am_abend_nicht_mehr_nachgeholt(sched):
    """Ein Neustart um 22 Uhr soll keine Mail mehr auslösen."""
    jetzt = _freitag_morgen(date(2026, 12, 4), stunde=22)
    assert sched.wochenmail_faellig(jetzt, {}) is None


def test_monatsplan_ist_am_vorletzten_freitag_faellig(sched):
    tag = kalender.vorletzter_freitag(2026, 12)
    jetzt = _freitag_morgen(tag, stunde=7, minute=35)
    assert sched.monatsplan_faellig(jetzt, {}) == "2027-01"


def test_monatsplan_ist_an_anderen_freitagen_nicht_faellig(sched):
    for tag in kalender.freitage_im_monat(2026, 12):
        if tag == kalender.vorletzter_freitag(2026, 12):
            continue
        jetzt = _freitag_morgen(tag, stunde=7, minute=35)
        assert sched.monatsplan_faellig(jetzt, {}) is None


def test_monatsplan_laeuft_pro_monat_nur_einmal(sched):
    tag = kalender.vorletzter_freitag(2026, 12)
    jetzt = _freitag_morgen(tag, stunde=7, minute=35)
    assert sched.monatsplan_faellig(jetzt, {"generate-month": "2027-01"}) is None


def test_monatsplan_laeuft_nach_der_wochenmail(sched):
    """Erst die Mail für die laufende Woche, dann der neue Monatsplan."""
    tag = kalender.vorletzter_freitag(2026, 12)
    gleich_nach_sieben = _freitag_morgen(tag, stunde=7, minute=5)
    assert sched.wochenmail_faellig(gleich_nach_sieben, {}) == tag
    assert sched.monatsplan_faellig(gleich_nach_sieben, {}) is None


def test_tick_fuehrt_faellige_aufgaben_aus_und_merkt_sie_sich(sched_mit_mail, monkeypatch):
    """Die Mail darf nach einem Neustart nicht ein zweites Mal rausgehen."""
    gesendet: list[str] = []

    def fake_sende(nachricht, config):
        gesendet.append(nachricht.betreff)

    monkeypatch.setattr("essensplaner.mail.sende", fake_sende)

    freitag = date(2026, 12, 4)
    jetzt = _freitag_morgen(freitag)
    erledigt = sched_mit_mail.tick(jetzt)
    assert erledigt, "die Wochenmail müsste gelaufen sein"
    assert gesendet

    vorher = len(gesendet)
    sched_mit_mail.tick(jetzt)
    assert len(gesendet) == vorher, "zweiter Tick hat erneut verschickt"


def test_tick_laeuft_weiter_wenn_eine_aufgabe_scheitert(sched_mit_mail, monkeypatch):
    """Ein Fehler beim Versand darf den Scheduler nicht beenden."""

    def kaputt(nachricht, config):
        raise RuntimeError("SMTP down")

    monkeypatch.setattr("essensplaner.mail.sende", kaputt)
    erledigt = sched_mit_mail.tick(_freitag_morgen(date(2026, 12, 4)))
    assert erledigt == []
    assert runner.lade_state(sched_mit_mail.config).get("send-weekly") is None


def test_unbekannte_zeitzone_faellt_auf_utc_zurueck(config):
    import copy

    cfg = copy.deepcopy(config)
    cfg.raw.setdefault("mail", {})["zeitzone"] = "Mars/Olympus"
    s = Scheduler(cfg)
    assert str(s.zone) in ("UTC", "utc")
    # Der Dienst muss trotzdem arbeiten können.
    assert s.wochenmail_faellig(_freitag_morgen(date(2026, 12, 4)), {}) is not None


def test_unlesbare_uhrzeit_faellt_auf_sieben_zurueck(config):
    import copy

    cfg = copy.deepcopy(config)
    cfg.raw.setdefault("mail", {})["wochenmail_uhrzeit"] = "morgens"
    s = Scheduler(cfg)
    assert (s.stunde, s.minute) == (7, 0)


# ------------------------------------------------------- Betrieb ohne SMTP


def _spion(aufrufe: list, rueckgabe):
    def _f(*args, **kwargs):
        aufrufe.append(kwargs)
        return rueckgabe

    return _f


def test_ohne_smtp_wird_geplant_aber_nicht_verschickt(sched, monkeypatch):
    """Ohne Mailserver soll der Scheduler trotzdem Pläne erzeugen.

    Die Oberfläche zeigt den Plan; ein Versandversuch ohne SMTP wäre nur
    eine Fehlermeldung alle Freitage.
    """
    assert not sched.config.smtp.konfiguriert
    aufrufe: list = []
    monkeypatch.setattr(
        "essensplaner.scheduler.send_weekly",
        _spion(aufrufe, runner.LaufErgebnis(aktion="send-weekly")),
    )
    monkeypatch.setattr(
        "essensplaner.scheduler.generate_month",
        _spion(aufrufe, runner.LaufErgebnis(aktion="generate-month")),
    )

    sched.tick(_freitag_morgen(date(2026, 12, 4)))

    assert aufrufe, "Der Scheduler hat gar nichts ausgeführt"
    for kwargs in aufrufe:
        assert kwargs.get("mail_senden") is False


def test_mit_smtp_wird_verschickt(sched_mit_mail, monkeypatch):
    aufrufe: list = []
    monkeypatch.setattr(
        "essensplaner.scheduler.send_weekly",
        _spion(aufrufe, runner.LaufErgebnis(aktion="send-weekly")),
    )
    monkeypatch.setattr(
        "essensplaner.scheduler.generate_month",
        _spion(aufrufe, runner.LaufErgebnis(aktion="generate-month")),
    )

    sched_mit_mail.tick(_freitag_morgen(date(2026, 12, 4)))

    assert aufrufe
    for kwargs in aufrufe:
        assert kwargs.get("mail_senden") is True


def test_hinweistext_als_smtp_host_gilt_nicht_als_konfiguriert():
    """Coolify setzt bei einer Pflichtvariablen deren Hinweistext als Wert ein.

    Aus "${SMTP_HOST:?SMTP_HOST muss gesetzt sein}" wird dann wirklich
    SMTP_HOST="SMTP_HOST muss gesetzt sein". Ohne diese Pruefung haelt der
    Planer das fuer einen Mailserver und scheitert jeden Freitag an der
    Namensaufloesung.
    """
    from essensplaner.config import SmtpConfig

    assert not SmtpConfig(host="SMTP_HOST muss gesetzt sein").konfiguriert
    assert not SmtpConfig(host="   ").konfiguriert
    assert not SmtpConfig(host="").konfiguriert
    assert SmtpConfig(host="smtp.example.com").konfiguriert
    assert SmtpConfig(host="localhost").konfiguriert


# -------------------------------------------------- Erinnerung am Vorabend


def _abend(tag: date, stunde: int = 18, minute: int = 5) -> datetime:
    return datetime(tag.year, tag.month, tag.day, stunde, minute, tzinfo=ZoneInfo("Europe/Berlin"))


def test_erinnerung_ist_donnerstagabend_faellig(sched):
    """Donnerstag abends, und gemeint ist der Freitag danach."""
    donnerstag = date(2026, 12, 3)
    assert sched.erinnerung_faellig(_abend(donnerstag), {}) == date(2026, 12, 4)


def test_erinnerung_ist_vor_der_uhrzeit_nicht_faellig(sched):
    jetzt = _abend(date(2026, 12, 3), stunde=17, minute=30)
    assert sched.erinnerung_faellig(jetzt, {}) is None


def test_erinnerung_ist_an_anderen_tagen_nicht_faellig(sched):
    for tag in (date(2026, 12, 2), date(2026, 12, 4), date(2026, 12, 6)):
        assert sched.erinnerung_faellig(_abend(tag), {}) is None


def test_erinnerung_kommt_nur_einmal_pro_woche(sched):
    donnerstag = date(2026, 12, 3)
    state = {"erinnerung": "2026-12-04"}
    assert sched.erinnerung_faellig(_abend(donnerstag), state) is None


def test_erinnerung_laesst_sich_abschalten(tmp_config):
    """Leerer Tag heisst aus - und der Scheduler laeuft trotzdem."""
    import copy

    cfg = copy.deepcopy(tmp_config)
    cfg.raw["mail"]["erinnerung_tag"] = ""
    s = Scheduler(cfg)
    assert s.erinnerung_tag is None
    assert s.erinnerung_faellig(_abend(date(2026, 12, 3)), {}) is None


def test_unbekannter_wochentag_schaltet_die_erinnerung_ab(tmp_config):
    """Ein Tippfehler darf den Scheduler nicht umbringen."""
    import copy

    cfg = copy.deepcopy(tmp_config)
    cfg.raw["mail"]["erinnerung_tag"] = "Donnerstagabend"
    s = Scheduler(cfg)
    assert s.erinnerung_tag is None
    # Die Wochenmail haengt nicht daran und bleibt faellig.
    assert s.wochenmail_faellig(_freitag_morgen(date(2026, 12, 4)), {}) == date(2026, 12, 4)


def test_erinnerung_laesst_sich_auf_einen_anderen_tag_legen(tmp_config):
    import copy

    cfg = copy.deepcopy(tmp_config)
    cfg.raw["mail"]["erinnerung_tag"] = "Mittwoch"
    cfg.raw["mail"]["erinnerung_uhrzeit"] = "20:30"
    s = Scheduler(cfg)
    mittwoch = date(2026, 12, 2)
    assert s.erinnerung_faellig(_abend(mittwoch, stunde=20, minute=45), {}) == date(2026, 12, 4)
    assert s.erinnerung_faellig(_abend(mittwoch, stunde=19), {}) is None


def test_send_reminder_rendert_den_zettel(ctx):
    freitag = date(2026, 12, 4)
    runner.generate_month(ctx, monat="2026-12", mail_senden=False)
    ergebnis = runner.send_reminder(ctx, freitag=freitag, mail_senden=False)
    assert ergebnis.mails, "Die Erinnerung muss eine Mail erzeugen"
    assert ergebnis.woche_start == freitag
    assert ergebnis.versendet is False


def test_send_reminder_erzeugt_keinen_plan(ctx):
    """Ein Hinweis ist kein Anlass, abends einen Monatsplan zu wuerfeln."""
    freitag = date(2026, 12, 4)
    ergebnis = runner.send_reminder(ctx, freitag=freitag, mail_senden=False)
    assert ergebnis.mails == []
    assert ergebnis.plan_erzeugt is False
    assert any("keinen Plan" in h for h in ergebnis.hinweise)
    assert not data.plan_vorhanden(ctx.config.plans_dir, "2026-12")
