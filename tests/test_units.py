"""Einheiten zusammenfassen, runden und ausgeben."""

from __future__ import annotations

import pytest

from essensplaner import units


@pytest.mark.parametrize(
    "menge,einheit,erwartet",
    [
        (1, "kg", (1000.0, "g")),
        (500, "g", (500.0, "g")),
        (1, "l", (1000.0, "ml")),
        (2, "Dose", (2.0, "dose")),
        (1, "Stk", (1.0, "stk")),
    ],
)
def test_in_basis(menge, einheit, erwartet):
    assert units.in_basis(menge, einheit) == erwartet


def test_gleiche_dimension_ist_kompatibel():
    assert units.kompatibel("g", "kg")
    assert units.kompatibel("ml", "l")
    assert not units.kompatibel("g", "ml")
    assert not units.kompatibel("Stk", "g")


@pytest.mark.parametrize(
    "menge,einheit,erwartet",
    [
        (1500, "g", "1,5 kg"),
        (1000, "g", "1 kg"),
        (750, "g", "750 g"),
        (1000, "ml", "1 l"),
        (250, "ml", "250 ml"),
        (1, "Bund", "1 Bund"),
        (2, "Bund", "2 Bund"),
        (1, "Dose", "1 Dose"),
        (3, "Dose", "3 Dosen"),
        (1, "Stk", "1 Stk"),
        (2, "stk", "2 Stk"),
        (1, "EL", "1 EL"),
        (2, "el", "2 EL"),
    ],
)
def test_formatiere_schreibt_einheiten_gross(menge, einheit, erwartet):
    assert units.formatiere(menge, einheit) == erwartet


def test_formatiere_rezept_rundet_kuechentauglich():
    """Die Skalierung auf 3 Portionen erzeugt krumme Werte."""
    assert units.formatiere_rezept(0.2, "Bund") == "etwas"
    assert units.formatiere_rezept(0.375, "Bund") == "0,5 Bund"
    assert units.formatiere_rezept(0.75, "Bund") == "1 Bund"
    assert units.formatiere_rezept(1.5, "Stk") == "1,5 Stk"
    assert units.formatiere_rezept(187.5, "g") == "190 g"
    assert units.formatiere_rezept(37.5, "g") == "40 g"


def test_runde_einkauf_auf_kaufbare_mengen():
    assert units.runde_einkauf(37, "g") == 40
    assert units.runde_einkauf(220, "g") == 250
    assert units.runde_einkauf(1.2, "Stk") == 2
    assert units.runde_einkauf(130, "ml") == 150


def test_packungen_rundet_auf():
    # 480 g Bedarf, Packung 400 g -> zwei Packungen
    assert units.packungen(480, "g", 400, "g") == 2
    # Genau eine Packung bleibt eine
    assert units.packungen(400, "g", 400, "g") == 1
    # Dosen zählen als Stück
    assert units.packungen(3, "dose", 1, "Dose") == 3


def test_packungen_ohne_passende_einheit():
    assert units.packungen(2, "dose", 400, "g") is None
    assert units.packungen(100, "g", 0, "g") is None


def test_packung_text():
    assert units.packung_text(3, 1, "Dose") == "3 Dosen"
    assert units.packung_text(1, 1, "Dose") == "1 Dose"
    assert units.packung_text(2, 500, "g") == "2 × 500 g"
    assert units.packung_text(1, 750, "ml") == "1 × 750 ml"


def test_kleinmenge_faktor():
    assert units.kleinmenge_faktor("EL") == 15.0
    assert units.kleinmenge_faktor("TL") == 5.0
    assert units.kleinmenge_faktor("Prise") == 0.5
    assert units.kleinmenge_faktor("g") is None


def test_kleinmengen_und_stueck_werden_erkannt():
    assert units.ist_kleinmenge("EL")
    assert units.ist_kleinmenge("prise")
    assert not units.ist_kleinmenge("g")
    assert units.ist_stueck("Dose")
    assert units.ist_stueck("bund")
    assert not units.ist_stueck("ml")


def test_deutsche_zahlenformatierung():
    assert units.formatiere(1.5, "kg") == "1,5 kg"
    assert units.formatiere(2.0, "kg") == "2 kg"
