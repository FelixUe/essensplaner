"""Einkaufslisten: Frischeliste je Woche, Vorratsliste je Monat, Grundeinkauf.

Mengen werden aus den Rezepten auf die tatsächlich zu kochenden Portionen
hochgerechnet (``portionen / base_servings``), je Zutat und Einheit addiert
und erst zum Schluss auf eine kaufbare Menge gerundet. Die Vorratsliste
rundet zusätzlich auf ganze Packungen auf.

Daneben steht der Grundeinkauf: Milch, Brot, Kaffee – was nicht an einem
Rezept hängt und trotzdem jede Woche mit in den Wagen soll. Er wird nicht
gerechnet, sondern von Hand gepflegt und liegt deshalb im Volume.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from . import data, units
from .config import Config
from .models import Ingredient, MonthPlan, PlannedMeal, Recipe, WeekPlan

log = logging.getLogger(__name__)

#: Reihenfolge der Abteilungen – ungefähr der Weg durch den Laden.
ABTEILUNG_REIHENFOLGE = (
    "Obst & Gemüse",
    "Backwaren",
    "Kühlregal",
    "Käse",
    "TK",
    "Trockenware",
    "Konserven",
    "Gewürze",
    "Getränke",
    "Sonstiges",
)


#: Hinweis, wenn sich keine Packungszahl ausrechnen lässt (z. B. Muskatnuss).
VORRAT_PRUEFEN = "Vorrat prüfen"


def abteilung_sortierung(name: str) -> tuple[int, str]:
    try:
        return (ABTEILUNG_REIHENFOLGE.index(name), name)
    except ValueError:
        return (len(ABTEILUNG_REIHENFOLGE), name)


@dataclass(slots=True)
class ShoppingItem:
    """Eine Zeile der Einkaufsliste."""

    slug: str
    name: str
    abteilung: str
    menge: float
    einheit: str
    #: Nur in der Vorratsliste gesetzt: aufgerundete Packungszahl.
    packungen: float | None = None
    pack_text: str = ""
    #: Rechnerischer Bedarf vor der Aufrundung auf Packungen.
    bedarf: float = 0.0
    bedarf_einheit: str = ""
    #: Gerichte, für die diese Zutat gebraucht wird.
    gerichte: list[str] = field(default_factory=list)
    kleinmenge: bool = False
    optional: bool = False

    @property
    def menge_text(self) -> str:
        return units.formatiere(self.menge, self.einheit)

    @property
    def bedarf_text(self) -> str:
        if not self.bedarf_einheit:
            return ""
        return units.formatiere(self.bedarf, self.bedarf_einheit)

    @property
    def anzeige(self) -> str:
        """Was auf dem Einkaufszettel steht.

        Vorratsliste: '2 Dosen Kidneybohnen (Bedarf 480 g)'.
        Frischeliste: '750 g Kartoffeln festkochend'.
        """
        if self.pack_text == VORRAT_PRUEFEN:
            return f"{self.name} – {VORRAT_PRUEFEN}"
        if self.pack_text:
            zeile = f"{self.pack_text} {self.name}"
            bedarf = self.bedarf_text
            if bedarf and bedarf != units.formatiere(self.menge, self.einheit):
                zeile += f" (Bedarf {bedarf})"
            return zeile
        return f"{self.menge_text} {self.name}"

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "abteilung": self.abteilung,
            "menge": round(self.menge, 2),
            "einheit": self.einheit,
            "menge_text": self.menge_text,
            "packungen": self.packungen,
            "pack_text": self.pack_text,
            "bedarf": round(self.bedarf, 2),
            "bedarf_einheit": self.bedarf_einheit,
            "bedarf_text": self.bedarf_text,
            "gerichte": self.gerichte,
            "kleinmenge": self.kleinmenge,
            "optional": self.optional,
        }


@dataclass(slots=True)
class ShoppingList:
    """Eine fertige Einkaufsliste, gruppiert nach Abteilung."""

    titel: str
    art: str  # "fresh" | "pantry"
    von: date | None = None
    bis: date | None = None
    items: list[ShoppingItem] = field(default_factory=list)

    @property
    def abteilungen(self) -> list[tuple[str, list[ShoppingItem]]]:
        """Items nach Abteilung gruppiert, in Ladenreihenfolge."""
        gruppen: dict[str, list[ShoppingItem]] = defaultdict(list)
        for item in self.items:
            gruppen[item.abteilung].append(item)
        for liste in gruppen.values():
            liste.sort(key=lambda i: i.name.lower())
        return sorted(gruppen.items(), key=lambda kv: abteilung_sortierung(kv[0]))

    @property
    def anzahl(self) -> int:
        return len(self.items)

    def to_dict(self) -> dict:
        return {
            "titel": self.titel,
            "art": self.art,
            "von": self.von.isoformat() if self.von else None,
            "bis": self.bis.isoformat() if self.bis else None,
            "items": [i.to_dict() for i in self.items],
        }


# ------------------------------------------------------------------ Sammeln


@dataclass(slots=True)
class _Posten:
    menge: float = 0.0
    gerichte: set[str] = field(default_factory=set)
    kleinmenge: bool = False
    nur_optional: bool = True


def _sammle(
    meals: list[PlannedMeal],
    rezepte: dict[str, Recipe],
    zutaten: dict[str, Ingredient],
    config: Config,
    art: str,
) -> dict[tuple[str, str], _Posten]:
    """Alle Zutaten der Mahlzeiten nach (Zutat, Basiseinheit) aufaddieren."""
    posten: dict[tuple[str, str], _Posten] = defaultdict(_Posten)

    for meal in meals:
        # Was aus der Gefriertruhe kommt, wurde schon früher eingekauft.
        if meal.aus_gefriertruhe:
            continue
        rezept = rezepte.get(meal.recipe_slug)
        if rezept is None:
            continue
        faktor = meal.portionen / rezept.base_servings
        if meal.batch_cook:
            faktor *= 2
        for zeile in rezept.zutaten_fuer(meal.variant):
            zutat = zutaten.get(zeile.item)
            if zutat is None:
                continue
            if zutat.type != art:
                continue
            menge, basis = units.in_basis(zeile.qty * faktor, zeile.unit)

            # Löffelmengen gleich hier in die Packungsbasis umrechnen. Sonst
            # stünde dieselbe Zutat zweimal auf der Liste, wenn ein Rezept
            # "1 TL Salz" und ein anderes "1 Prise Salz" verlangt.
            if units.ist_kleinmenge(basis):
                kf = units.kleinmenge_faktor(basis)
                pack_basis = units.basis_einheit(zutat.pack_unit)
                if kf is not None and pack_basis in ("g", "ml"):
                    menge, basis = menge * kf, pack_basis

            p = posten[(zutat.slug, basis)]
            p.menge += menge
            p.gerichte.add(rezept.name)
            p.kleinmenge = p.kleinmenge or units.ist_kleinmenge(zeile.unit)
            if not zeile.optional:
                p.nur_optional = False
    return posten


def _bestand_abziehen(
    posten: dict[tuple[str, str], _Posten], bestand: dict[str, tuple[float, str]]
) -> None:
    """Vorhandene Vorräte von den Mengen abziehen (Abschnitt 5.3 der Spec)."""
    for (slug, basis), p in posten.items():
        if slug not in bestand:
            continue
        haben_menge, haben_einheit = bestand[slug]
        haben_basis, haben_b = units.in_basis(haben_menge, haben_einheit)
        if haben_b != basis:
            continue
        p.menge = max(0.0, p.menge - haben_basis)


def lade_bestand(pfad: Path) -> dict[str, tuple[float, str]]:
    """Optionale Liste 'habe ich noch' aus data/bestand.yaml."""
    if not pfad.is_file():
        return {}
    with pfad.open(encoding="utf-8") as fh:
        roh = yaml.safe_load(fh) or {}
    bestand: dict[str, tuple[float, str]] = {}
    for slug, eintrag in roh.items():
        if isinstance(eintrag, dict):
            bestand[str(slug)] = (float(eintrag.get("qty", 0)), str(eintrag.get("unit", "Stk")))
    return bestand


def _zu_items(
    posten: dict[tuple[str, str], _Posten],
    zutaten: dict[str, Ingredient],
    mit_packungen: bool,
) -> list[ShoppingItem]:
    items: list[ShoppingItem] = []
    for (slug, basis), p in posten.items():
        if p.menge <= 0:
            continue
        zutat = zutaten[slug]
        menge, einheit = p.menge, basis
        gerundet = menge if units.ist_kleinmenge(einheit) else units.runde_einkauf(menge, einheit)
        bedarf, bedarf_einheit = gerundet, einheit
        packungs_zahl: float | None = None
        pack_text = ""

        if mit_packungen:
            packungs_zahl = units.packungen(
                gerundet, einheit, zutat.pack_qty, zutat.pack_unit, zutat.stk_gewicht_g
            )
            if packungs_zahl is not None and packungs_zahl > 0:
                # Auf ganze Packungen aufrunden – so steht es im Regal.
                pack_basis, pack_basis_einheit = units.in_basis(zutat.pack_qty, zutat.pack_unit)
                gerundet = packungs_zahl * pack_basis
                einheit = pack_basis_einheit
                pack_text = units.packung_text(packungs_zahl, zutat.pack_qty, zutat.pack_unit)
            elif zutat.is_pantry:
                # Haltbares ohne passende Packungsgröße: lieber ehrlich auf
                # den Vorrat verweisen, als eine Menge zu erfinden.
                pack_text = VORRAT_PRUEFEN
            # Frischware ohne Packungsgröße bleibt bei der blanken Menge:
            # "Vorrat prüfen" wäre bei einer Gurke Unsinn.

        items.append(
            ShoppingItem(
                slug=slug,
                name=zutat.name,
                abteilung=zutat.department,
                menge=gerundet,
                einheit=einheit,
                packungen=packungs_zahl,
                pack_text=pack_text,
                bedarf=bedarf,
                bedarf_einheit=bedarf_einheit,
                gerichte=sorted(p.gerichte),
                kleinmenge=p.kleinmenge,
                optional=p.nur_optional,
            )
        )
    items.sort(key=lambda i: (abteilung_sortierung(i.abteilung), i.name.lower()))
    return items


# -------------------------------------------------------------- öffentlich


def frischeliste(
    woche: WeekPlan,
    rezepte: dict[str, Recipe],
    zutaten: dict[str, Ingredient],
    config: Config,
    bestand: dict[str, tuple[float, str]] | None = None,
) -> ShoppingList:
    """Frischware für eine Planungswoche (Freitag bis Donnerstag)."""
    posten = _sammle(woche.meals, rezepte, zutaten, config, art="fresh")
    if bestand:
        _bestand_abziehen(posten, bestand)
    return ShoppingList(
        titel="Frischeliste",
        art="fresh",
        von=woche.start,
        bis=woche.ende,
        items=_zu_items(posten, zutaten, mit_packungen=True),
    )


def vorratsliste(
    plan: MonthPlan,
    rezepte: dict[str, Recipe],
    zutaten: dict[str, Ingredient],
    config: Config,
    bestand: dict[str, tuple[float, str]] | None = None,
) -> ShoppingList:
    """Haltbare Zutaten für den ganzen Monat, auf Packungsgrößen aufgerundet."""
    posten = _sammle(plan.alle_meals, rezepte, zutaten, config, art="pantry")
    if bestand:
        _bestand_abziehen(posten, bestand)
    von = plan.weeks[0].start if plan.weeks else None
    bis = plan.weeks[-1].ende if plan.weeks else None
    return ShoppingList(
        titel="Vorratsliste",
        art="pantry",
        von=von,
        bis=bis,
        items=_zu_items(posten, zutaten, mit_packungen=True),
    )


def wochen_pantry_liste(
    woche: WeekPlan,
    rezepte: dict[str, Recipe],
    zutaten: dict[str, Ingredient],
    config: Config,
    bestand: dict[str, tuple[float, str]] | None = None,
) -> ShoppingList:
    """Haltbare Zutaten nur dieser Woche.

    Wird gebraucht, wenn keine Monatsmail mit Vorratsliste vorausging –
    dann muss der Wocheneinkauf die Vorratszutaten mit abdecken.
    """
    posten = _sammle(woche.meals, rezepte, zutaten, config, art="pantry")
    if bestand:
        _bestand_abziehen(posten, bestand)
    return ShoppingList(
        titel="Vorrat diese Woche",
        art="pantry",
        von=woche.start,
        bis=woche.ende,
        items=_zu_items(posten, zutaten, mit_packungen=True),
    )


# ---------------------------------------------------------- Grundeinkauf

#: Lieber zu eng als zu weit: die Liste soll ein Zettel bleiben, den man im
#: Laden überblickt, und keine zweite Datenbank.
GRUNDEINKAUF_MAX_ZEILEN = 80
GRUNDEINKAUF_MAX_LAENGE = 80

#: Vorschlag für den Anfang. Er steht nur da, solange nichts Eigenes
#: gespeichert ist – die erste Speicherung ersetzt ihn vollständig.
STANDARD_GRUNDEINKAUF: tuple[str, ...] = (
    "Kühlregal:",
    "Milch",
    "Butter",
    "Eier",
    "Joghurt",
    "Käse fürs Brot",
    "Aufschnitt",
    "Backwaren:",
    "Brot",
    "Brötchen fürs Wochenende",
    "Obst & Gemüse:",
    "Obst für die Brotdosen",
    "Bananen",
    "Äpfel",
    "Trockenware:",
    "Kaffee",
    "Müsli",
    "Nudeln als Vorrat",
    "Reis als Vorrat",
)


class EinkaufFehler(Exception):
    """Eine Änderung am Grundeinkauf wurde nicht übernommen."""


def grundeinkauf_pfad(config: Config) -> Path:
    return config.plans_dir / "grundeinkauf.json"


def lade_grundeinkauf(config: Config) -> tuple[list[str], bool]:
    """Die Zeilen des Grundeinkaufs und ob sie selbst gepflegt sind.

    Ohne eigene Liste kommt der Vorschlag zurück. Das zweite Feld sagt, was
    von beidem es ist – die Oberfläche soll einen Vorschlag als solchen
    kennzeichnen und nicht so tun, als hätte ihn jemand aufgeschrieben.
    """
    pfad = grundeinkauf_pfad(config)
    if not pfad.is_file():
        return list(STANDARD_GRUNDEINKAUF), False
    try:
        with pfad.open(encoding="utf-8") as fh:
            roh = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("grundeinkauf.json nicht lesbar (%s) – es gilt der Vorschlag", exc)
        return list(STANDARD_GRUNDEINKAUF), False
    if isinstance(roh, dict):
        roh = roh.get("zeilen", [])
    if not isinstance(roh, list):
        return list(STANDARD_GRUNDEINKAUF), False
    return [str(z) for z in roh if isinstance(z, str)], True


def pruefe_grundeinkauf(text: str) -> list[str]:
    """Aus dem Textfeld die Zeilen machen – oder sagen, was nicht geht."""
    zeilen = [z.strip() for z in text.splitlines()]
    zeilen = [z for z in zeilen if z]
    if len(zeilen) > GRUNDEINKAUF_MAX_ZEILEN:
        raise EinkaufFehler(
            f"{len(zeilen)} Zeilen sind zu viele, mehr als {GRUNDEINKAUF_MAX_ZEILEN} "
            "nimmt die Liste nicht."
        )
    zu_lang = [z for z in zeilen if len(z) > GRUNDEINKAUF_MAX_LAENGE]
    if zu_lang:
        raise EinkaufFehler(
            f"Diese Zeile ist zu lang (mehr als {GRUNDEINKAUF_MAX_LAENGE} Zeichen): "
            f"{zu_lang[0][:60]}…"
        )
    return zeilen


def speichere_grundeinkauf(config: Config, text: str) -> list[str]:
    """Den Grundeinkauf ersetzen. Ein leeres Feld löscht die Liste."""
    zeilen = pruefe_grundeinkauf(text)
    data.schreibe_json(
        grundeinkauf_pfad(config),
        {"zeilen": zeilen, "geaendert": date.today().isoformat()},
    )
    return zeilen


def grundeinkauf_gruppen(zeilen: list[str]) -> list[tuple[str, list[str]]]:
    """Die Zeilen nach Überschriften gruppieren.

    Eine Zeile mit Doppelpunkt am Ende ist eine Überschrift ("Kühlregal:"),
    alles darunter gehört dazu. Das ist die einfachste Gliederung, die man
    in einem Textfeld hinschreiben kann, ohne sie zu lernen.
    """
    gruppen: list[tuple[str, list[str]]] = []
    aktuell: list[str] = []
    titel = ""
    for zeile in zeilen:
        if zeile.endswith(":") and len(zeile) > 1:
            if aktuell or titel:
                gruppen.append((titel, aktuell))
            titel = zeile[:-1].strip()
            aktuell = []
        else:
            aktuell.append(zeile)
    if aktuell or titel:
        gruppen.append((titel, aktuell))
    return gruppen


def _erstes_wort(text: str) -> str:
    teile = text.split()
    return teile[0].casefold() if teile else ""


def grundeinkauf_doppelt(zeilen: list[str], *listen: ShoppingList) -> set[str]:
    """Zeilen des Grundeinkaufs, die schon auf einer gerechneten Liste stehen.

    Sonst steht Milch zweimal auf demselben Zettel: einmal für ein Rezept
    ausgerechnet, einmal als Grundeinkauf. Der Hinweis sagt das nur – er
    streicht nichts, denn die Rezeptmenge deckt selten den Hausgebrauch.

    Verglichen wird streng: die ganze Zeile gegen den Zutatennamen oder
    dessen erstes Wort. Eine lockerere Suche hält sonst "Milch" für einen
    Treffer in "Kokosmilch".
    """
    namen = {i.name.casefold() for sl in listen for i in sl.items}
    ersten = {_erstes_wort(i.name) for sl in listen for i in sl.items}
    treffer: set[str] = set()
    for zeile in zeilen:
        if zeile.endswith(":"):
            continue
        schluessel = zeile.strip().casefold()
        if schluessel in namen or schluessel in ersten:
            treffer.add(zeile)
    return treffer
