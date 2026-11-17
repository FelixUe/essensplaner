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

#: Vorschlag für den Anfang: ein Gang durch Kühlschrank, Obstschale und
#: Vorratsschrank. Gedacht als Kontrollliste – was fehlt, bleibt stehen,
#: der Rest wird gestrichen. Er steht nur da, solange nichts Eigenes
#: gespeichert ist; die erste Speicherung ersetzt ihn vollständig.
#: Haushaltskram (Spülmittel, Müllbeutel) fehlt bewusst – das kommt über
#: die Bearbeiten-Seite dazu, wenn es gebraucht wird.
STANDARD_GRUNDEINKAUF: tuple[str, ...] = (
    "Kühlschrank:",
    "Milch (2 l)",
    "Butter",
    "Eier (10 Stück)",
    "Naturjoghurt",
    "Quark oder Skyr",
    "Sahne oder Schmand",
    "Käse fürs Brot",
    "Streukäse zum Überbacken",
    "Aufschnitt",
    "Frischkäse",
    "Senf und Ketchup",
    "Obst & Gemüse:",
    "Bananen",
    "Äpfel",
    "Obst für die Brotdosen",
    "Zitronen",
    "Zwiebeln",
    "Knoblauch",
    "Möhren",
    "Kartoffeln",
    "Salat oder Gurke",
    "Tomaten",
    "Backwaren:",
    "Brot",
    "Brötchen fürs Wochenende",
    "Toast",
    "Vorratsschrank:",
    "Nudeln",
    "Reis",
    "Mehl",
    "Haferflocken",
    "Müsli",
    "Zucker",
    "Salz",
    "Pfeffer",
    "Bratöl",
    "Olivenöl",
    "Gemüsebrühe",
    "Passierte Tomaten",
    "Gehackte Tomaten (Dose)",
    "Kidneybohnen oder Kichererbsen",
    "Kaffee",
    "Tee",
    "Gefrierfach:",
    "TK-Erbsen",
    "TK-Spinat",
    "TK-Beeren",
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


def _ohne_menge(zeile: str) -> str:
    """'Milch (2 l)' wird zu 'milch'.

    Die Klammer hinter dem Posten ist ein Hinweis für den Haushalt und
    gehört nicht zum Namen – sonst erkennt der Abgleich unten die Milch
    nicht wieder, nur weil eine Zielmenge dabeisteht.
    """
    text = zeile.strip()
    if text.endswith(")") and "(" in text:
        text = text[: text.rindex("(")].strip()
    return text.casefold()


def grundeinkauf_doppelt(zeilen: list[str], *listen: ShoppingList) -> set[str]:
    """Zeilen des Grundeinkaufs, die schon auf einer gerechneten Liste stehen.

    Sonst steht Milch zweimal auf demselben Zettel: einmal für ein Rezept
    ausgerechnet, einmal als Grundeinkauf. Der Hinweis sagt das nur – er
    streicht nichts, denn die Rezeptmenge deckt selten den Hausgebrauch.

    Verglichen wird streng: die ganze Zeile ohne Mengenklammer gegen den
    Zutatennamen oder dessen erstes Wort. Eine lockerere Suche hält sonst
    "Milch" für einen Treffer in "Kokosmilch".
    """
    namen = {i.name.casefold() for sl in listen for i in sl.items}
    ersten = {_erstes_wort(i.name) for sl in listen for i in sl.items}
    treffer: set[str] = set()
    for zeile in zeilen:
        if zeile.endswith(":"):
            continue
        schluessel = _ohne_menge(zeile)
        if schluessel in namen or schluessel in ersten:
            treffer.add(zeile)
    return treffer


# ------------------------------------------------------------ Ein Zettel

#: Überschriften des Grundeinkaufs, die dieselbe Abteilung meinen wie die
#: Zutaten-Stammdaten, nur anders heißen. Der Grundeinkauf ist ein Textfeld,
#: in das man schreibt, wie man im Haushalt redet – "Kühlschrank", nicht
#: "Kühlregal". Ohne diese Brücke stünde derselbe Gang zweimal auf dem Zettel.
GRUNDEINKAUF_ABTEILUNG: dict[str, str] = {
    "kühlschrank": "Kühlregal",
    "kühlung": "Kühlregal",
    "obst": "Obst & Gemüse",
    "gemüse": "Obst & Gemüse",
    "obst & gemüse": "Obst & Gemüse",
    "obst und gemüse": "Obst & Gemüse",
    "brot": "Backwaren",
    "bäcker": "Backwaren",
    "tiefkühl": "TK",
    "trocken": "Trockenware",
    "vorrat": "Trockenware",
    "konserve": "Konserven",
    "konserven": "Konserven",
    "gewürz": "Gewürze",
    "gewürze": "Gewürze",
    "getränke": "Getränke",
}

#: Gruppe für Grundeinkauf-Zeilen ohne eigene Überschrift.
GRUNDEINKAUF_REST = "Immer dabei"


@dataclass(slots=True)
class ZettelPosten:
    """Eine Zeile auf dem fertigen Einkaufszettel.

    Der Zettel führt zusammen, was bisher auf drei Listen stand: Frischware,
    Vorratszutaten und Grundeinkauf. Im Laden läuft man einen Weg, nicht
    drei – deshalb zählt die Abteilung und nicht die Herkunft. Woher ein
    Posten kommt, steht trotzdem dran: beim Abwählen ist der Unterschied
    zwischen "für ein Gericht gebraucht" und "steht immer drauf" wichtig.
    """

    #: Stabiler Schlüssel für die Abwahl, z. B. ``frisch:kartoffeln``.
    key: str
    text: str
    abteilung: str
    quelle: str  # "frisch" | "vorrat" | "grund"
    gerichte: list[str] = field(default_factory=list)
    hinweis: str = ""
    abgewaehlt: bool = False

    @property
    def anker(self) -> str:
        return anker(self.key)


def anker(key: str) -> str:
    """Aus einem Posten-Schlüssel eine Sprungmarke machen.

    Nach dem Abwählen soll die Seite dort stehen, wo man geklickt hat, und
    nicht wieder oben. Der Schlüssel enthält dafür zu viel – Leerzeichen,
    Klammern, Umlaute – deshalb bleibt hier nur, was in einer Fragment-URL
    unfallfrei durchgeht.
    """
    sauber = [z if z.isalnum() and z.isascii() else "-" for z in key]
    return "".join(sauber).strip("-").lower() or "posten"


def _zettel_abteilung(titel: str) -> str:
    """Die Überschrift einer Grundeinkauf-Gruppe auf eine Abteilung abbilden."""
    schluessel = titel.strip().casefold()
    if not schluessel:
        return GRUNDEINKAUF_REST
    if schluessel in GRUNDEINKAUF_ABTEILUNG:
        return GRUNDEINKAUF_ABTEILUNG[schluessel]
    for name in ABTEILUNG_REIHENFOLGE:
        if name.casefold() == schluessel:
            return name
    # Eine eigene Überschrift bleibt stehen, statt in "Sonstiges" zu
    # verschwinden: wer sie hinschreibt, will sie auf dem Zettel sehen.
    return titel.strip()


def zettel_posten(
    frisch: ShoppingList,
    vorrat: ShoppingList,
    grund_zeilen: list[str],
    abgewaehlt: set[str] | None = None,
    extra: list[dict[str, str]] | None = None,
) -> list[tuple[str, list[ZettelPosten]]]:
    """Alle drei Quellen zu einem Zettel in Ladenreihenfolge zusammenführen.

    Dopplungen werden nicht entfernt, nur angeschrieben: die Rezeptmenge
    deckt selten den Hausgebrauch, zwei Liter Milch fürs Wochenende sind
    neben 200 ml für eine Soße kein Fehler.
    """
    gewaehlt = abgewaehlt or set()
    doppelt = grundeinkauf_doppelt(grund_zeilen, frisch, vorrat)

    gruppen: dict[str, list[ZettelPosten]] = defaultdict(list)
    for liste, quelle in ((frisch, "frisch"), (vorrat, "vorrat")):
        for item in liste.items:
            key = f"{quelle}:{item.slug}"
            gruppen[item.abteilung].append(
                ZettelPosten(
                    key=key,
                    text=item.anzeige,
                    abteilung=item.abteilung,
                    quelle=quelle,
                    gerichte=list(item.gerichte),
                    hinweis="optional" if item.optional else "",
                    abgewaehlt=key in gewaehlt,
                )
            )

    for titel, zeilen in grundeinkauf_gruppen(grund_zeilen):
        abteilung = _zettel_abteilung(titel)
        for zeile in zeilen:
            key = f"grund:{zeile}"
            gruppen[abteilung].append(
                ZettelPosten(
                    key=key,
                    text=zeile,
                    abteilung=abteilung,
                    quelle="grund",
                    hinweis="steht schon als Rezeptmenge drauf" if zeile in doppelt else "",
                    abgewaehlt=key in gewaehlt,
                )
            )

    for eintrag in extra or []:
        key = f"extra:{eintrag['text']}"
        abteilung = _zettel_abteilung(eintrag.get("abteilung", ""))
        gruppen[abteilung].append(
            ZettelPosten(
                key=key,
                text=eintrag["text"],
                abteilung=abteilung,
                quelle="extra",
                hinweis="nur diese Woche",
                abgewaehlt=key in gewaehlt,
            )
        )

    for posten in gruppen.values():
        # Abgewähltes nach unten, der Rest alphabetisch: der Zettel soll
        # oben das zeigen, was noch zu holen ist.
        posten.sort(key=lambda p: (p.abgewaehlt, p.text.casefold()))
    return sorted(gruppen.items(), key=lambda kv: abteilung_sortierung(kv[0]))


# ------------------------------------------------------------- Abwahl


def abwahl_pfad(config: Config) -> Path:
    """Liegt bei den Plänen: das ist Laufzeitstand, kein Stammdatum."""
    return config.plans_dir / "einkauf-abwahl.json"


def lade_abwahl(config: Config, woche: str) -> set[str]:
    pfad = abwahl_pfad(config)
    if not pfad.exists():
        return set()
    try:
        with pfad.open(encoding="utf-8") as fh:
            roh = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # Ein kaputter Abwahlstand darf den Einkauf nicht blockieren: im
        # Zweifel steht alles auf dem Zettel, und das ist die harmlose Seite.
        log.warning("einkauf-abwahl.json nicht lesbar (%s) – es gilt nichts als abgewählt", exc)
        return set()
    if not isinstance(roh, dict):
        return set()
    keys = roh.get(woche, [])
    return {str(k) for k in keys} if isinstance(keys, list) else set()


def speichere_abwahl(config: Config, woche: str, keys: set[str]) -> None:
    """Den Abwahlstand einer Woche ablegen.

    Andere Wochen bleiben stehen; wer eine Woche leer räumt, verliert nicht
    den Stand der nächsten.
    """
    pfad = abwahl_pfad(config)
    bestand: dict[str, list[str]] = {}
    if pfad.exists():
        try:
            with pfad.open(encoding="utf-8") as fh:
                roh = json.load(fh)
            if isinstance(roh, dict):
                bestand = {str(k): list(v) for k, v in roh.items() if isinstance(v, list)}
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("einkauf-abwahl.json nicht lesbar (%s) – wird neu geschrieben", exc)
    if keys:
        bestand[woche] = sorted(keys)
    else:
        bestand.pop(woche, None)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    data.schreibe_json(pfad, bestand)


# ------------------------------------- Grundeinkauf als verwalteter Speicher


def _ist_ueberschrift(zeile: str) -> bool:
    return zeile.endswith(":") and len(zeile) > 1


def grundeinkauf_posten(zeilen: list[str]) -> list[dict[str, str]]:
    """Die Zeilen als einzelne Posten mit ihrer Abteilung.

    Gespeichert bleibt die Zeilenliste: sie ist das Format, das die Mail,
    der Zettel und das Textfeld schon lesen. Für die Verwaltung ist eine
    flache Liste aus Posten und Abteilung aber das, womit man arbeitet –
    niemand will eine Überschrift verschieben, um Butter umzubenennen.
    """
    posten: list[dict[str, str]] = []
    titel = ""
    for zeile in zeilen:
        if _ist_ueberschrift(zeile):
            titel = zeile[:-1].strip()
            continue
        posten.append({"text": zeile, "abteilung": titel or GRUNDEINKAUF_REST})
    return posten


def grundeinkauf_abteilungen(zeilen: list[str]) -> list[str]:
    """Abteilungen zur Auswahl: die bekannten und die selbst erfundenen."""
    eigene = [z[:-1].strip() for z in zeilen if _ist_ueberschrift(z)]
    bekannt = list(ABTEILUNG_REIHENFOLGE)
    alle = bekannt + [e for e in eigene if e and e not in bekannt]
    return sorted(alle, key=abteilung_sortierung)


def _zeilen_aus_posten(posten: list[dict[str, str]]) -> list[str]:
    """Aus der flachen Liste wieder Überschriften und Zeilen machen."""
    nach_abteilung: dict[str, list[str]] = defaultdict(list)
    for p in posten:
        nach_abteilung[p["abteilung"] or GRUNDEINKAUF_REST].append(p["text"])
    zeilen: list[str] = []
    for abteilung in sorted(nach_abteilung, key=abteilung_sortierung):
        zeilen.append(f"{abteilung}:")
        zeilen.extend(nach_abteilung[abteilung])
    return zeilen


def _grundeinkauf_schreiben(config: Config, posten: list[dict[str, str]]) -> list[str]:
    zeilen = pruefe_grundeinkauf(chr(10).join(_zeilen_aus_posten(posten)))
    data.schreibe_json(
        grundeinkauf_pfad(config),
        {"zeilen": zeilen, "geaendert": date.today().isoformat()},
    )
    return zeilen


def pruefe_posten(text: str) -> str:
    """Einen einzelnen Posten annehmen – oder sagen, was nicht geht."""
    sauber = " ".join(text.split())
    if not sauber:
        raise EinkaufFehler("Ohne Namen lässt sich nichts hinzufügen.")
    if len(sauber) > GRUNDEINKAUF_MAX_LAENGE:
        raise EinkaufFehler(f"Der Posten ist zu lang (mehr als {GRUNDEINKAUF_MAX_LAENGE} Zeichen).")
    if _ist_ueberschrift(sauber):
        # Sonst legt ein Posten namens "Backwaren:" beim nächsten Laden eine
        # Abteilung an und verschiebt alles, was darunter steht.
        raise EinkaufFehler("Ein Posten darf nicht auf einen Doppelpunkt enden.")
    return sauber


def grundeinkauf_hinzufuegen(config: Config, text: str, abteilung: str) -> list[str]:
    """Einen Posten in den Speicher legen."""
    sauber = pruefe_posten(text)
    zeilen, _ = lade_grundeinkauf(config)
    posten = grundeinkauf_posten(zeilen)
    if any(p["text"].casefold() == sauber.casefold() for p in posten):
        raise EinkaufFehler(f"„{sauber}“ steht schon im Speicher.")
    ziel = " ".join(abteilung.split()) or GRUNDEINKAUF_REST
    posten.append({"text": sauber, "abteilung": ziel})
    return _grundeinkauf_schreiben(config, posten)


def grundeinkauf_entfernen(config: Config, text: str) -> list[str]:
    """Einen Posten aus dem Speicher nehmen."""
    zeilen, _ = lade_grundeinkauf(config)
    posten = [p for p in grundeinkauf_posten(zeilen) if p["text"] != text]
    return _grundeinkauf_schreiben(config, posten)


def grundeinkauf_aendern(config: Config, alt: str, neu: str, abteilung: str) -> list[str]:
    """Einen Posten umbenennen oder in eine andere Abteilung legen."""
    sauber = pruefe_posten(neu)
    zeilen, _ = lade_grundeinkauf(config)
    posten = grundeinkauf_posten(zeilen)
    if not any(p["text"] == alt for p in posten):
        raise EinkaufFehler(f"„{alt}“ steht nicht im Speicher.")
    if sauber.casefold() != alt.casefold() and any(
        p["text"].casefold() == sauber.casefold() for p in posten
    ):
        raise EinkaufFehler(f"„{sauber}“ steht schon im Speicher.")
    ziel = " ".join(abteilung.split()) or GRUNDEINKAUF_REST
    for p in posten:
        if p["text"] == alt:
            p["text"] = sauber
            p["abteilung"] = ziel
    return _grundeinkauf_schreiben(config, posten)


# --------------------------------------------- Zusätzliche Posten je Woche


def extra_pfad(config: Config) -> Path:
    return config.plans_dir / "einkauf-extra.json"


def lade_extra(config: Config, woche: str) -> list[dict[str, str]]:
    """Was für diese eine Woche von Hand auf den Zettel geschrieben wurde."""
    pfad = extra_pfad(config)
    if not pfad.exists():
        return []
    try:
        with pfad.open(encoding="utf-8") as fh:
            roh = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("einkauf-extra.json nicht lesbar (%s) – es gilt nichts", exc)
        return []
    if not isinstance(roh, dict):
        return []
    eintraege = roh.get(woche, [])
    if not isinstance(eintraege, list):
        return []
    sauber: list[dict[str, str]] = []
    for e in eintraege:
        if isinstance(e, dict) and isinstance(e.get("text"), str):
            sauber.append(
                {
                    "text": e["text"],
                    "abteilung": str(e.get("abteilung") or GRUNDEINKAUF_REST),
                }
            )
    return sauber


def _extra_schreiben(config: Config, woche: str, eintraege: list[dict[str, str]]) -> None:
    pfad = extra_pfad(config)
    bestand: dict[str, list[dict[str, str]]] = {}
    if pfad.exists():
        try:
            with pfad.open(encoding="utf-8") as fh:
                roh = json.load(fh)
            if isinstance(roh, dict):
                bestand = {str(k): v for k, v in roh.items() if isinstance(v, list)}
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("einkauf-extra.json nicht lesbar (%s) – wird neu geschrieben", exc)
    if eintraege:
        bestand[woche] = eintraege
    else:
        bestand.pop(woche, None)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    data.schreibe_json(pfad, bestand)


def extra_hinzufuegen(
    config: Config, woche: str, text: str, abteilung: str
) -> list[dict[str, str]]:
    """Einen Posten nur für diese Woche auf den Zettel schreiben."""
    sauber = pruefe_posten(text)
    eintraege = lade_extra(config, woche)
    if any(e["text"].casefold() == sauber.casefold() for e in eintraege):
        raise EinkaufFehler(f"„{sauber}“ steht schon auf diesem Zettel.")
    if len(eintraege) >= GRUNDEINKAUF_MAX_ZEILEN:
        raise EinkaufFehler("Für diese Woche sind schon genug Posten ergänzt.")
    eintraege.append(
        {"text": sauber, "abteilung": " ".join(abteilung.split()) or GRUNDEINKAUF_REST}
    )
    _extra_schreiben(config, woche, eintraege)
    return eintraege


def extra_entfernen(config: Config, woche: str, text: str) -> list[dict[str, str]]:
    eintraege = [e for e in lade_extra(config, woche) if e["text"] != text]
    _extra_schreiben(config, woche, eintraege)
    return eintraege
