"""Wochenrhythmus: eine Planungswoche läuft Freitag bis Donnerstag.

Einkaufstag ist Freitag, deshalb beginnt jede Woche dort. Ein Monatsplan
beginnt mit der Woche, die am letzten Freitag vor oder am Monatsersten startet,
und endet mit der Woche, die den letzten Tag des Monats enthält.
"""

from __future__ import annotations

import calendar as _calendar
from datetime import date, timedelta

FREITAG = 4  # date.weekday(): Montag = 0

#: Abstand des Kochtags zum Wochenstart (Freitag).
SLOT_OFFSET: dict[str, int] = {
    "FR": 0,
    "SA": 1,
    "SO": 2,
    "MO": 3,
    "MI": 5,
}

WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

MONATSNAMEN = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)


def monatsname(monat: int) -> str:
    return MONATSNAMEN[monat - 1]


def monats_label(monat_str: str) -> str:
    """'2026-12' -> 'Dezember 2026'."""
    jahr, monat = parse_monat(monat_str)
    return f"{monatsname(monat)} {jahr}"


def parse_monat(monat_str: str) -> tuple[int, int]:
    """'2026-12' -> (2026, 12)."""
    try:
        jahr_s, monat_s = monat_str.split("-")
        jahr, monat = int(jahr_s), int(monat_s)
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            f"Monat muss im Format JJJJ-MM angegeben werden, nicht {monat_str!r}"
        ) from exc
    if not 1 <= monat <= 12:
        raise ValueError(f"Ungültiger Monat: {monat_str!r}")
    return jahr, monat


def monat_str(jahr: int, monat: int) -> str:
    return f"{jahr:04d}-{monat:02d}"


def letzter_tag(jahr: int, monat: int) -> date:
    return date(jahr, monat, _calendar.monthrange(jahr, monat)[1])


def letzter_freitag_bis(tag: date) -> date:
    """Der letzte Freitag, der nicht nach ``tag`` liegt."""
    return tag - timedelta(days=(tag.weekday() - FREITAG) % 7)


def wochentag_nummer(name: str) -> int | None:
    """Aus "Donnerstag" die 3 machen, wie ``date.weekday()`` sie zählt.

    Gibt ``None`` zurück, wenn der Name nicht passt. Der Aufrufer entscheidet
    dann, ob das ein Fehler ist oder schlicht "abgeschaltet" heißt – in der
    Konfiguration ist ein leerer Tag genau das.
    """
    gesucht = name.strip().casefold()
    if not gesucht:
        return None
    for nummer, tag in enumerate(WOCHENTAGE):
        if tag.casefold() == gesucht:
            return nummer
    return None


def naechster_freitag(tag: date, inklusiv: bool = True) -> date:
    """Der nächste Freitag ab ``tag``."""
    abstand = (FREITAG - tag.weekday()) % 7
    if abstand == 0 and not inklusiv:
        abstand = 7
    return tag + timedelta(days=abstand)


def planungswochen(jahr: int, monat: int) -> list[date]:
    """Die Freitage, an denen die Planungswochen dieses Monats beginnen.

    Ein Monat beginnt mit der Woche, die am letzten Freitag vor oder am
    Monatsersten startet, und endet vor der Woche, die den Folgemonat
    einläutet. Die Übergangswoche gehört damit immer genau einem Monat –
    dem neuen. Andernfalls stünde ein Freitag in zwei Monatsplänen und
    ``send-weekly`` müsste raten, welcher gilt.

    Für Januar 2026 heißt das: die Woche ab 30.01. gehört schon zum Februar,
    der Januarplan endet mit der Woche ab 23.01.
    """
    start = letzter_freitag_bis(date(jahr, monat, 1))
    jahr_folge, monat_folge = folgemonat(jahr, monat)
    ende_exklusiv = letzter_freitag_bis(date(jahr_folge, monat_folge, 1))

    wochen: list[date] = []
    aktuell = start
    while aktuell < ende_exklusiv:
        wochen.append(aktuell)
        aktuell += timedelta(days=7)
    return wochen


def slot_datum(wochenstart: date, slot: str) -> date:
    """Kochtag eines Slots in der Woche ab ``wochenstart``."""
    if slot not in SLOT_OFFSET:
        raise ValueError(f"Unbekannter Slot: {slot!r}")
    return wochenstart + timedelta(days=SLOT_OFFSET[slot])


def freitage_im_monat(jahr: int, monat: int) -> list[date]:
    """Alle Freitage, die in diesem Kalendermonat liegen."""
    erster = date(jahr, monat, 1)
    ende = letzter_tag(jahr, monat)
    tage: list[date] = []
    aktuell = naechster_freitag(erster)
    while aktuell <= ende:
        tage.append(aktuell)
        aktuell += timedelta(days=7)
    return tage


def vorletzter_freitag(jahr: int, monat: int) -> date:
    """Der vorletzte Freitag eines Monats – Termin für ``generate-month``."""
    tage = freitage_im_monat(jahr, monat)
    if len(tage) < 2:
        raise ValueError(f"Monat {jahr}-{monat:02d} hat weniger als zwei Freitage")
    return tage[-2]


def folgemonat(jahr: int, monat: int) -> tuple[int, int]:
    return (jahr + 1, 1) if monat == 12 else (jahr, monat + 1)


def vormonat(jahr: int, monat: int) -> tuple[int, int]:
    return (jahr - 1, 12) if monat == 1 else (jahr, monat - 1)


def startet_monatswoche(freitag: date) -> str | None:
    """Gibt den Monat zurück, dessen erste Planungswoche an diesem Freitag beginnt.

    Das ist der Termin für die Monatsmail mit der Vorratsliste. Liegt in der
    Woche ab ``freitag`` kein Monatserster, ist das Ergebnis None.
    """
    for versatz in range(7):
        tag = freitag + timedelta(days=versatz)
        if tag.day == 1:
            return monat_str(tag.year, tag.month)
    return None


def monat_der_woche(freitag: date) -> str:
    """Der Monat, zu dessen Plan die Woche ab ``freitag`` gehört.

    Eine Woche gehört zu dem Monat, dessen Planungswochen sie enthalten –
    also zum Monat des Donnerstags am Wochenende der Woche, sobald der
    Freitag noch im Vormonat liegt.
    """
    kandidat = startet_monatswoche(freitag)
    if kandidat:
        return kandidat
    return monat_str(freitag.year, freitag.month)


def formatiere_datum(tag: date) -> str:
    """'Freitag, 04.12.2026'."""
    return f"{WOCHENTAGE[tag.weekday()]}, {tag.strftime('%d.%m.%Y')}"


def formatiere_kurz(tag: date) -> str:
    """'04.12.'."""
    return tag.strftime("%d.%m.")
