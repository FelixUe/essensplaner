"""Wochenrhythmus: Einkaufstag Freitag, Planungswoche Freitag bis Donnerstag."""

from __future__ import annotations

from datetime import date

import pytest

from essensplaner import kalender
from essensplaner.kalender import (
    FREITAG,
    SLOT_OFFSET,
    folgemonat,
    letzter_freitag_bis,
    monat_der_woche,
    monat_str,
    naechster_freitag,
    parse_monat,
    planungswochen,
    slot_datum,
    startet_monatswoche,
    vorletzter_freitag,
    vormonat,
)


def test_slot_offsets_entsprechen_den_kochtagen():
    """FR=Freitag, SA, SO, MO und MI – Dienstag und Donnerstag werden
    von Montag bzw. Mittwoch mitabgedeckt."""
    assert SLOT_OFFSET == {"FR": 0, "SA": 1, "SO": 2, "MO": 3, "MI": 5}


def test_slot_datum_liegt_auf_dem_richtigen_wochentag():
    freitag = date(2026, 12, 4)
    assert freitag.weekday() == FREITAG
    assert slot_datum(freitag, "FR") == date(2026, 12, 4)
    assert slot_datum(freitag, "SA") == date(2026, 12, 5)
    assert slot_datum(freitag, "SO") == date(2026, 12, 6)
    assert slot_datum(freitag, "MO") == date(2026, 12, 7)
    assert slot_datum(freitag, "MI") == date(2026, 12, 9)


def test_slot_datum_lehnt_unbekannte_slots_ab():
    with pytest.raises(ValueError, match="Unbekannter Slot"):
        slot_datum(date(2026, 12, 4), "DI")


@pytest.mark.parametrize(
    "tag,erwartet",
    [
        (date(2026, 12, 4), date(2026, 12, 4)),  # ist selbst Freitag
        (date(2026, 12, 5), date(2026, 12, 4)),  # Samstag
        (date(2026, 12, 10), date(2026, 12, 4)),  # Donnerstag der Woche
        (date(2026, 12, 11), date(2026, 12, 11)),  # nächster Freitag
    ],
)
def test_letzter_freitag_bis(tag, erwartet):
    assert letzter_freitag_bis(tag) == erwartet


def test_naechster_freitag():
    assert naechster_freitag(date(2026, 12, 2)) == date(2026, 12, 4)
    assert naechster_freitag(date(2026, 12, 4)) == date(2026, 12, 4)
    assert naechster_freitag(date(2026, 12, 4), inklusiv=False) == date(2026, 12, 11)


def test_planungswochen_beginnen_am_letzten_freitag_vor_monatsbeginn():
    """Der 1.12.2026 ist ein Dienstag, die erste Woche startet am 27.11."""
    wochen = planungswochen(2026, 12)
    assert wochen[0] == date(2026, 11, 27)
    assert all(w.weekday() == FREITAG for w in wochen)


def test_planungswochen_beginnen_vor_dem_monatsersten():
    for jahr in (2026, 2027, 2028):
        for nr in range(1, 13):
            wochen = planungswochen(jahr, nr)
            assert wochen, f"{jahr}-{nr} hat keine Wochen"
            assert wochen[0] <= date(jahr, nr, 1), f"{jahr}-{nr}: Monatsanfang fehlt"
            for a, b in zip(wochen, wochen[1:], strict=False):
                assert (b - a).days == 7, f"{jahr}-{nr}: Lücke zwischen {a} und {b}"


def test_planungswochen_der_monate_bilden_eine_lueckenlose_folge():
    """Jeder Freitag gehört genau einem Monatsplan – keine Lücke, keine
    Überschneidung. Sonst wüsste send-weekly nicht, welchen Plan es laden soll.
    """
    alle: list[date] = []
    for jahr in (2026, 2027, 2028):
        for nr in range(1, 13):
            alle.extend(planungswochen(jahr, nr))

    assert len(alle) == len(set(alle)), "ein Freitag steht in zwei Monatsplänen"
    assert alle == sorted(alle)
    for a, b in zip(alle, alle[1:], strict=False):
        assert (b - a).days == 7, f"Lücke zwischen {a} und {b}"


def test_uebergangswoche_gehoert_dem_neuen_monat():
    """Die Woche ab 30.01.2026 enthält den 1. Februar – sie gehört zum Februar."""
    assert date(2026, 1, 30) not in planungswochen(2026, 1)
    assert planungswochen(2026, 2)[0] == date(2026, 1, 30)


def test_planungswochen_sind_vier_oder_fuenf():
    for jahr in (2026, 2027, 2028):
        for nr in range(1, 13):
            anzahl = len(planungswochen(jahr, nr))
            assert anzahl in (4, 5), f"{jahr}-{nr}: {anzahl} Wochen"


def test_startet_monatswoche_erkennt_den_monatswechsel():
    # In der Woche ab 27.11.2026 liegt der 1. Dezember.
    assert startet_monatswoche(date(2026, 11, 27)) == "2026-12"
    # In der Woche ab 4.12. liegt kein Monatserster.
    assert startet_monatswoche(date(2026, 12, 4)) is None


def test_startet_monatswoche_wenn_der_freitag_selbst_der_erste_ist():
    assert date(2027, 1, 1).weekday() == FREITAG
    assert startet_monatswoche(date(2027, 1, 1)) == "2027-01"


def test_monat_der_woche_ordnet_die_uebergangswoche_dem_neuen_monat_zu():
    assert monat_der_woche(date(2026, 11, 27)) == "2026-12"
    assert monat_der_woche(date(2026, 12, 4)) == "2026-12"
    assert monat_der_woche(date(2026, 12, 25)) == "2026-12"


def test_jede_planungswoche_wird_ihrem_monat_zugeordnet():
    """Wichtig: send-weekly findet über monat_der_woche den richtigen Plan."""
    for jahr in (2026, 2027):
        for nr in range(1, 13):
            monat = monat_str(jahr, nr)
            for start in planungswochen(jahr, nr):
                assert monat_der_woche(start) == monat, (
                    f"Woche ab {start} wird {monat_der_woche(start)} statt {monat} zugeordnet"
                )


def test_vorletzter_freitag():
    # Dezember 2026: Freitage 4., 11., 18., 25.
    assert vorletzter_freitag(2026, 12) == date(2026, 12, 18)


def test_vorletzter_freitag_existiert_in_jedem_monat():
    for jahr in (2026, 2027, 2028):
        for nr in range(1, 13):
            tag = vorletzter_freitag(jahr, nr)
            assert tag.weekday() == FREITAG
            assert tag.month == nr


def test_vorletzter_freitag_liegt_vor_dem_monatswechsel():
    """Die Monatsaufgabe muss früh genug laufen, damit der Plan steht, bevor
    die erste Woche des Folgemonats beginnt."""
    for jahr in (2026, 2027):
        for nr in range(1, 13):
            lauf = vorletzter_freitag(jahr, nr)
            j2, n2 = folgemonat(jahr, nr)
            erste_woche = planungswochen(j2, n2)[0]
            assert lauf < erste_woche, f"{jahr}-{nr}: Lauf {lauf}, Woche {erste_woche}"


def test_monatsarithmetik():
    assert folgemonat(2026, 12) == (2027, 1)
    assert folgemonat(2026, 5) == (2026, 6)
    assert vormonat(2027, 1) == (2026, 12)
    assert vormonat(2027, 5) == (2027, 4)


def test_parse_monat():
    assert parse_monat("2026-12") == (2026, 12)
    with pytest.raises(ValueError):
        parse_monat("2026-13")
    with pytest.raises(ValueError):
        parse_monat("Dezember")


def test_monats_label():
    assert kalender.monats_label("2026-12") == "Dezember 2026"
    assert kalender.monats_label("2027-03") == "März 2027"


def test_datumsformate_sind_deutsch():
    assert kalender.formatiere_datum(date(2026, 12, 4)) == "Freitag, 04.12.2026"
    assert kalender.formatiere_kurz(date(2026, 12, 4)) == "04.12."
