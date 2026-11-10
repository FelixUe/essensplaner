"""Wie viele Esstage ein Gericht abdeckt.

Die Zahl stand früher nur im Fließtext von ``deckt_ab``: "Montag + Dienstag
Mittag" liest sich leicht als ein Tag mit zwei Mahlzeiten. Jetzt steht sie als
``tage`` in der Konfiguration, wandert in jede geplante Mahlzeit und taucht in
der Mail auf. Diese Tests halten die Zusicherung fest, die daran hängt:
niemand isst dasselbe Gericht länger als zwei Tage.
"""

from __future__ import annotations

import copy

import pytest

from essensplaner import mail, rules
from essensplaner.config import MAX_TAGE_JE_SLOT, Config
from essensplaner.models import PlannedMeal

from .conftest import TEST_MONATE, TEST_SEEDS

# ----------------------------------------------------------- Konfiguration


def test_jeder_slot_nennt_seine_tage(config):
    """Ohne Angabe gälte stillschweigend ein Tag – das soll nicht passieren."""
    for slot in config.slot_namen:
        assert "tage" in config.slots[slot], f"Slot {slot} hat keine Tagesangabe"


def test_kein_slot_deckt_mehr_als_zwei_tage_ab(config):
    for slot in config.slot_namen:
        tage = config.slot_tage(slot)
        assert 1 <= tage <= MAX_TAGE_JE_SLOT, f"Slot {slot} deckt {tage} Tage ab"


def test_nur_die_vorkoch_slots_decken_zwei_tage_ab(config):
    """Die Zwei-Tage-Slots sind genau die, in denen vorgekocht wird.

    Dort gilt auch das Proteinminimum – beides hängt daran, dass am Folgetag
    die Reste gegessen werden.
    """
    zwei = {s for s in config.slot_namen if config.slot_tage(s) == 2}
    assert zwei == set(config.protein_slots)


def test_fehlende_angabe_gilt_als_ein_tag(config):
    assert config.slot_tage("gibt-es-nicht") == 1


# -------------------------------------------------------------- Prüfung


def test_saubere_konfiguration_wird_nicht_beanstandet(config):
    assert rules.pruefe_slots(config) == []


def _config_mit_tagen(config: Config, slot: str, tage: int) -> Config:
    roh = copy.deepcopy(config.raw)
    roh["slots"][slot]["tage"] = tage
    return Config(raw=roh, repo_root=config.repo_root, smtp=config.smtp)


def test_drei_tage_werden_gemeldet(config):
    kaputt = _config_mit_tagen(config, "MO", 3)
    codes = [i.code for i in rules.pruefe_slots(kaputt)]
    assert "slot_zu_viele_tage" in codes


def test_null_tage_werden_gemeldet(config):
    kaputt = _config_mit_tagen(config, "FR", 0)
    codes = [i.code for i in rules.pruefe_slots(kaputt)]
    assert "slot_tage_ungueltig" in codes


def test_stammdatenpruefung_schliesst_die_slots_ein(config, rezepte, zutaten):
    kaputt = _config_mit_tagen(config, "MI", 4)
    codes = [i.code for i in rules.pruefe_stammdaten(rezepte, zutaten, kaputt)]
    assert "slot_zu_viele_tage" in codes


# ------------------------------------------------------------------ Plan


@pytest.mark.parametrize("monat", TEST_MONATE)
def test_jede_mahlzeit_traegt_die_tage_ihres_slots(mach_plan, config, monat):
    plan = mach_plan(monat)
    for meal in plan.alle_meals:
        assert meal.tage == config.slot_tage(meal.slot)


@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_niemand_isst_dasselbe_laenger_als_zwei_tage(mach_plan, seed):
    """Die Zusicherung selbst, am fertigen Plan geprüft."""
    plan = mach_plan("2027-02", seed)
    for meal in plan.alle_meals:
        assert meal.tage <= MAX_TAGE_JE_SLOT


def test_tage_ueberleben_das_speichern(plan):
    for meal in plan.alle_meals:
        wieder = PlannedMeal.from_dict(meal.to_dict())
        assert wieder.tage == meal.tage


def test_alter_plan_ohne_tage_bleibt_lesbar(plan):
    """Pläne aus der Zeit vor diesem Feld liegen im Volume und müssen laden."""
    roh = plan.alle_meals[0].to_dict()
    del roh["tage"]
    assert PlannedMeal.from_dict(roh).tage == 1


# ------------------------------------------------------------------ Mail


def test_zwei_tage_stehen_in_der_mail(config, rezepte, zutaten, plan):
    renderer = mail.MailRenderer(config, rezepte, zutaten)
    woche = plan.weeks[1]
    gerendert = renderer.wochenmail(woche)
    zwei_tage_slots = [m.slot for m in woche.meals if config.slot_tage(m.slot) == 2]
    assert zwei_tage_slots, "Die Testwoche hat keinen Zwei-Tage-Slot"
    assert "(2 Tage)" in gerendert.text
    assert "(2 Tage)" in gerendert.html


def test_ein_tages_slot_bekommt_keinen_zusatz(config, rezepte, zutaten, plan):
    """Bei einem Tag wäre der Hinweis nur Rauschen."""
    renderer = mail.MailRenderer(config, rezepte, zutaten)
    gerendert = renderer.wochenmail(plan.weeks[1])
    assert "(1 Tage)" not in gerendert.text
    assert "(1 Tag)" not in gerendert.text
