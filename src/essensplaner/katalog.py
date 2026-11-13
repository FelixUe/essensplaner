"""Der Rezeptkatalog zur Laufzeit: eigene Rezepte und gestrichene Gerichte.

Die 52 Rezepte im Repo sind geprüft und liegen im Image. Zur Laufzeit soll
sich der Katalog trotzdem ändern lassen: ein Gericht, das niemand mehr sehen
will, und eines, das dazukommt. Beides gehört ins Volume, sonst wäre es nach
dem nächsten Rollout weg.

Zwei Dateien neben den Plänen:

``rezepte/<slug>.yaml``
    Selbst angelegte Rezepte, im selben Format wie die im Repo. Sie werden
    beim Laden dazugemischt und lassen sich jederzeit wieder löschen.

``gestrichen.json``
    Slugs, die nicht mehr eingeplant werden sollen.

``merkliste.json``
    Slugs, die demnächst auf den Tisch sollen. Die Merkliste plant nichts
    von selbst – sie ist der Zettel, von dem man beim Ändern eines Slots
    etwas herunternimmt.

Gestrichen heißt **nicht gelöscht**: das Rezept bleibt ladbar. Ein Plan, der
schon steht, nennt es weiterhin beim Namen, und sein Einkaufszettel stimmt.
Nur der Planer nimmt es nicht mehr in die Auswahl. Für die 52 Rezepte aus dem
Repo ist das ohnehin der einzig mögliche Weg – ihre Dateien liegen
schreibgeschützt im Image.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from datetime import date
from pathlib import Path

import yaml

from . import data, nutrition
from .config import Config
from .models import ALL_MONTHS, Ingredient, Recipe, RecipeIngredient

log = logging.getLogger(__name__)

#: Slugs bestehen aus Kleinbuchstaben, Ziffern und Bindestrichen. Alles
#: andere wäre ein Dateiname, der nicht zum Rezept passt – oder schlimmer,
#: einer, der aus dem Verzeichnis herausführt.
SLUG_MUSTER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class KatalogFehler(Exception):
    """Eine Katalogänderung wurde nicht übernommen."""


# ---------------------------------------------------------------- Pfade


def eigene_dir(config: Config) -> Path:
    """Wo selbst angelegte Rezepte liegen – im Volume, neben den Plänen."""
    return config.plans_dir / "rezepte"


def gestrichen_pfad(config: Config) -> Path:
    return config.plans_dir / "gestrichen.json"


def ist_eigenes(config: Config, rezept: Recipe) -> bool:
    """Ob das Rezept zur Laufzeit angelegt wurde und nicht aus dem Repo kommt."""
    if not rezept.quelle:
        return False
    try:
        return Path(rezept.quelle).parent == eigene_dir(config).resolve() or Path(
            rezept.quelle
        ).parent == eigene_dir(config)
    except OSError:  # pragma: no cover - kaputter Pfad
        return False


def slug_pruefen(slug: str) -> str:
    if not SLUG_MUSTER.match(slug):
        raise KatalogFehler(
            f"{slug!r} ist kein gültiger Kurzname. Erlaubt sind Kleinbuchstaben, "
            "Ziffern und Bindestriche, etwa 'ofengemuese-feta'."
        )
    return slug


def slug_aus_name(name: str) -> str:
    """Aus 'Gebackener Camembert' wird 'gebackener-camembert'."""
    ersatz = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
    text = name.strip().lower()
    for alt, neu in ersatz.items():
        text = text.replace(alt, neu)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "rezept"


# ------------------------------------------------------- Gestrichene Slugs


def lade_gestrichen(config: Config) -> set[str]:
    pfad = gestrichen_pfad(config)
    if not pfad.is_file():
        return set()
    try:
        with pfad.open(encoding="utf-8") as fh:
            roh = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # Lieber alles einplanen als gar nicht starten.
        log.warning("gestrichen.json nicht lesbar (%s) – es wird nichts gestrichen", exc)
        return set()
    if isinstance(roh, dict):
        roh = roh.get("slugs", [])
    return {str(s) for s in roh if isinstance(s, str)}


def speichere_gestrichen(config: Config, slugs: set[str]) -> Path:
    return data.schreibe_json(
        gestrichen_pfad(config),
        {"slugs": sorted(slugs), "geaendert": date.today().isoformat()},
    )


def streichen(config: Config, slug: str) -> set[str]:
    aktuell = lade_gestrichen(config)
    aktuell.add(slug)
    speichere_gestrichen(config, aktuell)
    return aktuell


def aufnehmen(config: Config, slug: str) -> set[str]:
    aktuell = lade_gestrichen(config)
    aktuell.discard(slug)
    speichere_gestrichen(config, aktuell)
    return aktuell


# -------------------------------------------------------------- Merkliste

#: Mehr als das ist keine Merkliste mehr, sondern ein zweiter Katalog.
MERKLISTE_MAX = 50


def merkliste_pfad(config: Config) -> Path:
    return config.plans_dir / "merkliste.json"


def lade_merkliste(config: Config) -> list[str]:
    """Vorgemerkte Gerichte, in der Reihenfolge, in der sie notiert wurden."""
    pfad = merkliste_pfad(config)
    if not pfad.is_file():
        return []
    try:
        with pfad.open(encoding="utf-8") as fh:
            roh = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("merkliste.json nicht lesbar (%s) – die Merkliste bleibt leer", exc)
        return []
    if isinstance(roh, dict):
        roh = roh.get("slugs", [])
    if not isinstance(roh, list):
        return []
    # Reihenfolge erhalten, Doppelte entfernen.
    eintraege: list[str] = []
    for eintrag in roh:
        if isinstance(eintrag, str) and eintrag not in eintraege:
            eintraege.append(eintrag)
    return eintraege


def speichere_merkliste(config: Config, slugs: list[str]) -> Path:
    return data.schreibe_json(
        merkliste_pfad(config),
        {"slugs": slugs, "geaendert": date.today().isoformat()},
    )


def merken(config: Config, slug: str) -> list[str]:
    """Ein Gericht vormerken. Zweimal merken ändert nichts."""
    aktuell = lade_merkliste(config)
    if slug in aktuell:
        return aktuell
    if len(aktuell) >= MERKLISTE_MAX:
        raise KatalogFehler(
            f"Auf der Merkliste stehen schon {MERKLISTE_MAX} Gerichte. "
            "Erst eines einplanen oder wieder herunternehmen."
        )
    aktuell.append(slug)
    speichere_merkliste(config, aktuell)
    return aktuell


def vergessen(config: Config, slug: str) -> list[str]:
    """Ein Gericht von der Merkliste nehmen."""
    aktuell = lade_merkliste(config)
    if slug not in aktuell:
        return aktuell
    aktuell = [s for s in aktuell if s != slug]
    speichere_merkliste(config, aktuell)
    return aktuell


# --------------------------------------------------------- Eigene Rezepte


def lade_eigene(config: Config) -> dict[str, Recipe]:
    """Selbst angelegte Rezepte aus dem Volume laden.

    Ein kaputtes Rezept darf den Start nicht verhindern – es wird
    übersprungen und protokolliert. Sonst sperrt eine halb geschriebene
    Datei die ganze Anwendung aus.
    """
    verzeichnis = eigene_dir(config)
    if not verzeichnis.is_dir():
        return {}
    rezepte: dict[str, Recipe] = {}
    for pfad in sorted(verzeichnis.glob("*.yaml")):
        try:
            with pfad.open(encoding="utf-8") as fh:
                roh = yaml.safe_load(fh) or {}
            rezept = data.parse_recipe(roh, quelle=str(pfad))
        except (OSError, yaml.YAMLError, data.DataError) as exc:
            log.warning("Eigenes Rezept %s übersprungen: %s", pfad.name, exc)
            continue
        if rezept.slug != pfad.stem:
            log.warning("Eigenes Rezept %s: slug passt nicht zum Dateinamen", pfad.name)
            continue
        rezepte[rezept.slug] = rezept
    return rezepte


def speichere_eigenes(config: Config, rezept: Recipe) -> Path:
    """Ein selbst angelegtes Rezept ins Volume schreiben."""
    slug_pruefen(rezept.slug)
    verzeichnis = eigene_dir(config)
    verzeichnis.mkdir(parents=True, exist_ok=True)
    pfad = verzeichnis / f"{rezept.slug}.yaml"
    with data.PLAN_SPERRE:
        temp = pfad.with_suffix(".tmp")
        temp.write_text(data.dump_recipe(rezept), encoding="utf-8", newline="\n")
        temp.replace(pfad)
    return pfad


def loesche_eigenes(config: Config, slug: str) -> bool:
    """Ein selbst angelegtes Rezept endgültig entfernen."""
    slug_pruefen(slug)
    pfad = eigene_dir(config) / f"{slug}.yaml"
    if not pfad.is_file():
        return False
    with data.PLAN_SPERRE:
        pfad.unlink()
    return True


# ------------------------------------------------------------ Zusammenbau


def alle_rezepte(config: Config, aus_repo: dict[str, Recipe]) -> dict[str, Recipe]:
    """Repo-Rezepte und eigene zusammenführen.

    Ein eigenes Rezept mit demselben Kurznamen gewinnt – so lässt sich ein
    Rezept aus dem Repo überschreiben, ohne es dort zu ändern.
    """
    zusammen = dict(aus_repo)
    for slug, rezept in lade_eigene(config).items():
        zusammen[slug] = rezept
    return zusammen


# ------------------------------------------------- Rezept aus dem Formular

#: Eine Zutatenzeile: "500 g Kartoffeln mehligkochend" oder "1 Stk Zwiebel".
#: Hinten darf " (optional)" stehen.
ZEILE_MUSTER = re.compile(
    r"^\s*(?P<menge>[\d.,]+)\s+(?P<einheit>[^\s]+)\s+(?P<zutat>.+?)"
    r"(?:\s*\((?P<optional>optional)\))?\s*$",
    re.IGNORECASE,
)


def finde_zutat(text: str, zutaten: dict[str, Ingredient]) -> Ingredient | None:
    """Eine Zutat über Kurznamen, Namen oder Alias finden.

    Niemand tippt ``kartoffeln_mehlig``; auf dem Zettel steht "Kartoffeln
    mehligkochend". Beides soll gehen.
    """
    gesucht = text.strip().casefold()
    if not gesucht:
        return None
    for zutat in zutaten.values():
        if zutat.slug.casefold() == gesucht:
            return zutat
    for zutat in zutaten.values():
        if zutat.name.casefold() == gesucht:
            return zutat
    for zutat in zutaten.values():
        if any(a.casefold() == gesucht for a in zutat.aliases):
            return zutat
    return None


def parse_zutaten(text: str, zutaten: dict[str, Ingredient]) -> list[RecipeIngredient]:
    """Den Zutaten-Block eines Formulars lesen, Zeile für Zeile."""
    zeilen: list[RecipeIngredient] = []
    fehler: list[str] = []
    for nummer, roh in enumerate(text.splitlines(), start=1):
        if not roh.strip():
            continue
        treffer = ZEILE_MUSTER.match(roh)
        if treffer is None:
            fehler.append(f"Zeile {nummer}: {roh.strip()!r} – erwartet wird 'Menge Einheit Zutat'")
            continue
        try:
            menge = float(treffer.group("menge").replace(",", "."))
        except ValueError:
            fehler.append(f"Zeile {nummer}: {treffer.group('menge')!r} ist keine Zahl")
            continue
        if menge <= 0:
            fehler.append(f"Zeile {nummer}: Menge muss größer als null sein")
            continue
        zutat = finde_zutat(treffer.group("zutat"), zutaten)
        if zutat is None:
            fehler.append(
                f"Zeile {nummer}: Zutat {treffer.group('zutat').strip()!r} ist nicht bekannt"
            )
            continue
        zeilen.append(
            RecipeIngredient(
                item=zutat.slug,
                qty=menge,
                unit=treffer.group("einheit"),
                optional=bool(treffer.group("optional")),
            )
        )
    if fehler:
        raise KatalogFehler(" · ".join(fehler))
    if not zeilen:
        raise KatalogFehler("Ohne Zutaten geht es nicht.")
    return zeilen


def rezept_bauen(
    *,
    name: str,
    slots: list[str],
    zutaten_text: str,
    schritte_text: str,
    zutaten: dict[str, Ingredient],
    prep_minutes: int,
    base_servings: int,
    core: bool = False,
    tags: str = "",
    monate: list[int] | None = None,
    slug: str = "",
) -> Recipe:
    """Aus Formularfeldern ein Rezept bauen – oder sagen, was fehlt.

    Das Protein wird aus den Zutaten gerechnet, nicht abgefragt: geschätzte
    Zahlen wären genau die Angabe, die später von den Zutaten abweicht.
    """
    name = name.strip()
    if not name:
        raise KatalogFehler("Das Rezept braucht einen Namen.")
    if not slots:
        raise KatalogFehler("Mindestens ein Slot muss angekreuzt sein.")
    if prep_minutes <= 0:
        raise KatalogFehler("Die Zubereitungszeit muss größer als null sein.")
    if base_servings <= 0:
        raise KatalogFehler("Die Grundmenge muss größer als null sein.")

    schritte = tuple(z.strip() for z in schritte_text.splitlines() if z.strip())
    if not schritte:
        raise KatalogFehler("Mindestens ein Zubereitungsschritt wird gebraucht.")

    rezept = Recipe(
        slug=slug_pruefen(slug.strip() or slug_aus_name(name)),
        name=name,
        core=core,
        slots=tuple(slots),
        tags=tuple(t.strip() for t in tags.split(",") if t.strip()),
        seasons=tuple(monate) if monate else ALL_MONTHS,
        prep_minutes=prep_minutes,
        base_servings=base_servings,
        protein_per_serving_g=0.0,
        ingredients=tuple(parse_zutaten(zutaten_text, zutaten)),
        steps=schritte,
    )
    gerechnet = nutrition.protein_berechnet(rezept, zutaten)
    return replace(rezept, protein_per_serving_g=round(gerechnet, 1))
