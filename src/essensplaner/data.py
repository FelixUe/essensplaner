"""Laden und Speichern von Zutaten-Stammdaten, Rezepten und Plänen."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import yaml

from .models import ALL_MONTHS, ALL_SLOTS, Ingredient, MonthPlan, Recipe, RecipeIngredient, Variant


class DataError(Exception):
    """Stammdaten sind unbrauchbar (fehlende Datei, kaputtes YAML, Pflichtfeld fehlt)."""


# --------------------------------------------------------------------- Zutaten


def parse_ingredient(slug: str, roh: dict[str, Any]) -> Ingredient:
    if not isinstance(roh, dict):
        raise DataError(f"Zutat {slug}: erwartet ein Mapping, bekam {type(roh).__name__}")
    pack = roh.get("pack", {}) or {}
    typ = str(roh.get("type", "")).strip()
    if typ not in {"pantry", "fresh"}:
        raise DataError(f"Zutat {slug}: type muss 'pantry' oder 'fresh' sein, nicht {typ!r}")
    if "name" not in roh:
        raise DataError(f"Zutat {slug}: Feld 'name' fehlt")
    if "department" not in roh:
        raise DataError(f"Zutat {slug}: Feld 'department' fehlt")
    return Ingredient(
        slug=slug,
        name=str(roh["name"]),
        type=typ,
        department=str(roh["department"]),
        pack_qty=float(pack.get("qty", 1)),
        pack_unit=str(pack.get("unit", "Stk")),
        shelf_days=int(roh.get("shelf_days", 0)),
        aliases=tuple(str(a) for a in roh.get("aliases", []) or []),
        protein_per_100=float(roh.get("protein_per_100", 0) or 0),
        stk_gewicht_g=(
            float(roh["stk_gewicht_g"]) if roh.get("stk_gewicht_g") is not None else None
        ),
        empfindlich=bool(roh.get("empfindlich", False)),
        grundzutat=bool(roh.get("grundzutat", False)),
        gewuerzmischung=bool(roh.get("gewuerzmischung", False)),
        fenchelfrei=roh.get("fenchelfrei"),
    )


def load_ingredients(pfad: Path) -> dict[str, Ingredient]:
    """ingredients.yaml einlesen."""
    if not pfad.is_file():
        raise DataError(f"Zutatendatei nicht gefunden: {pfad}")
    with pfad.open(encoding="utf-8") as fh:
        roh = yaml.safe_load(fh) or {}
    if not isinstance(roh, dict):
        raise DataError(f"{pfad}: erwartet ein Mapping von Slug auf Zutat")
    return {slug: parse_ingredient(slug, daten) for slug, daten in roh.items()}


# --------------------------------------------------------------------- Rezepte


def _parse_seasons(wert: Any, slug: str) -> tuple[int, ...]:
    if wert is None or wert == "all":
        return ALL_MONTHS
    if isinstance(wert, str):
        raise DataError(
            f"Rezept {slug}: seasons muss 'all' oder eine Monatsliste sein, nicht {wert!r}"
        )
    monate = tuple(int(m) for m in wert)
    for m in monate:
        if m not in ALL_MONTHS:
            raise DataError(f"Rezept {slug}: ungültiger Monat {m} in seasons")
    return monate


def _parse_ingredient_line(roh: Any, slug: str) -> RecipeIngredient:
    if not isinstance(roh, dict):
        raise DataError(f"Rezept {slug}: Zutatenzeile muss ein Mapping sein, bekam {roh!r}")
    if "item" not in roh:
        raise DataError(f"Rezept {slug}: Zutatenzeile ohne 'item': {roh!r}")
    return RecipeIngredient(
        item=str(roh["item"]),
        qty=float(roh.get("qty", 0)),
        unit=str(roh.get("unit", "Stk")),
        optional=bool(roh.get("optional", False)),
        hinweis=str(roh.get("hinweis", "")),
    )


def _parse_variant(name: str, roh: dict[str, Any], slug: str) -> Variant:
    ersetzt = {
        str(alt): _parse_ingredient_line(neu, slug)
        for alt, neu in (roh.get("ersetzt", {}) or {}).items()
    }
    return Variant(
        name=name,
        hinweis=str(roh.get("hinweis", "")),
        zusatz=tuple(_parse_ingredient_line(z, slug) for z in roh.get("zusatz", []) or []),
        entfernt=tuple(str(e) for e in roh.get("entfernt", []) or []),
        ersetzt=ersetzt,
        protein_per_serving_g=(
            float(roh["protein_per_serving_g"])
            if roh.get("protein_per_serving_g") is not None
            else None
        ),
        zusatz_steps=tuple(str(s) for s in roh.get("zusatz_steps", []) or []),
    )


def parse_recipe(roh: dict[str, Any], quelle: str = "") -> Recipe:
    if not isinstance(roh, dict):
        raise DataError(f"{quelle}: erwartet ein Mapping")
    for pflicht in ("slug", "name", "slots", "base_servings", "protein_per_serving_g"):
        if pflicht not in roh:
            raise DataError(f"{quelle or roh.get('slug', '?')}: Feld {pflicht!r} fehlt")
    slug = str(roh["slug"])
    slots = tuple(str(s) for s in roh["slots"])
    for s in slots:
        if s not in ALL_SLOTS:
            raise DataError(f"Rezept {slug}: unbekannter Slot {s!r}, erlaubt sind {ALL_SLOTS}")
    variants = {
        str(name): _parse_variant(str(name), daten or {}, slug)
        for name, daten in (roh.get("variants", {}) or {}).items()
    }
    return Recipe(
        slug=slug,
        name=str(roh["name"]),
        core=bool(roh.get("core", False)),
        slots=slots,
        tags=tuple(str(t) for t in roh.get("tags", []) or []),
        seasons=_parse_seasons(roh.get("seasons"), slug),
        prep_minutes=int(roh.get("prep_minutes", 0)),
        base_servings=int(roh["base_servings"]),
        protein_per_serving_g=float(roh["protein_per_serving_g"]),
        ingredients=tuple(
            _parse_ingredient_line(z, slug) for z in roh.get("ingredients", []) or []
        ),
        steps=tuple(str(s) for s in roh.get("steps", []) or []),
        variants=variants,
        quelle=quelle,
    )


def load_recipes(verzeichnis: Path) -> dict[str, Recipe]:
    """Alle Rezepte aus data/recipes/*.yaml laden. `_pending` wird ausgelassen."""
    if not verzeichnis.is_dir():
        raise DataError(f"Rezeptverzeichnis nicht gefunden: {verzeichnis}")
    rezepte: dict[str, Recipe] = {}
    for pfad in sorted(verzeichnis.glob("*.yaml")):
        if pfad.name.startswith("_"):
            continue
        with pfad.open(encoding="utf-8") as fh:
            roh = yaml.safe_load(fh) or {}
        rezept = parse_recipe(roh, quelle=str(pfad))
        if rezept.slug != pfad.stem:
            raise DataError(f"{pfad}: slug {rezept.slug!r} passt nicht zum Dateinamen")
        if rezept.slug in rezepte:
            raise DataError(f"Rezept-Slug doppelt vergeben: {rezept.slug}")
        rezepte[rezept.slug] = rezept
    if not rezepte:
        raise DataError(f"Keine Rezepte in {verzeichnis} gefunden")
    return rezepte


def load_pending_recipes(verzeichnis: Path) -> dict[str, Recipe]:
    """Rezeptvorschläge aus der Review-Warteschlange laden (nie eingeplant)."""
    if not verzeichnis.is_dir():
        return {}
    rezepte: dict[str, Recipe] = {}
    for pfad in sorted(verzeichnis.glob("*.yaml")):
        with pfad.open(encoding="utf-8") as fh:
            roh = yaml.safe_load(fh) or {}
        rezept = parse_recipe(roh, quelle=str(pfad))
        rezepte[rezept.slug] = rezept
    return rezepte


def unbekannte_zutaten(
    rezepte: dict[str, Recipe], zutaten: dict[str, Ingredient]
) -> list[tuple[str, str]]:
    """Alle Zutaten-Slugs, die in Rezepten vorkommen, aber keine Stammdaten haben."""
    fehler: list[tuple[str, str]] = []
    for rezept in rezepte.values():
        varianten: list[str | None] = [None, *rezept.variants.keys()]
        gesehen: set[str] = set()
        for variante in varianten:
            for zutat in rezept.zutaten_fuer(variante):
                if zutat.item in gesehen:
                    continue
                gesehen.add(zutat.item)
                if zutat.item not in zutaten:
                    fehler.append((rezept.slug, zutat.item))
    return fehler


# ----------------------------------------------------------------------- Pläne


def plan_pfad(plans_dir: Path, monat: str) -> Path:
    return plans_dir / f"{monat}.json"


#: Schuetzt das Lesen und Schreiben der Plaene. Scheduler und Weboberflaeche
#: laufen im selben Prozess in verschiedenen Threads; ohne diese Sperre
#: koennte die Freitagsmail einen Plan lesen, waehrend die Oberflaeche gerade
#: ein Gericht tauscht. Reentrant, damit Lesen innerhalb einer Aenderung geht.
PLAN_SPERRE = threading.RLock()


def save_plan(plan: MonthPlan, plans_dir: Path) -> Path:
    """Einen Plan speichern - erst vollstaendig schreiben, dann umbenennen.

    Direkt in die Zieldatei zu schreiben hiesse: bricht der Container mitten
    im Schreiben ab, liegt dort ein halber Plan, und der naechste Start
    scheitert am kaputten JSON. ``os.replace`` ist innerhalb eines
    Dateisystems atomar - es gibt also entweder den alten oder den neuen Plan.
    """
    plans_dir.mkdir(parents=True, exist_ok=True)
    pfad = plan_pfad(plans_dir, plan.monat)
    with PLAN_SPERRE:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=plans_dir,
            prefix=f".{plan.monat}-",
            suffix=".tmp",
            delete=False,
            newline=chr(10),
        ) as fh:
            json.dump(plan.to_dict(), fh, ensure_ascii=False, indent=2)
            fh.write(chr(10))
            temp = Path(fh.name)
        os.replace(temp, pfad)
    return pfad


def schreibe_json(pfad: Path, inhalt: object) -> Path:
    """Eine kleine JSON-Datei im Volume ersetzen, ohne Zwischenzustand.

    Dieselbe Vorsicht wie bei :func:`save_plan`: erst vollstaendig daneben
    schreiben, dann umbenennen. Sonst steht nach einem Abbruch halbes JSON
    in der Datei und die Anwendung liest es beim naechsten Start nicht mehr.
    """
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with PLAN_SPERRE:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=pfad.parent,
            prefix=f".{pfad.stem}-",
            suffix=".tmp",
            delete=False,
            newline=chr(10),
        ) as fh:
            json.dump(inhalt, fh, ensure_ascii=False, indent=2)
            fh.write(chr(10))
            temp = Path(fh.name)
        os.replace(temp, pfad)
    return pfad


def load_plan(plans_dir: Path, monat: str) -> MonthPlan:
    pfad = plan_pfad(plans_dir, monat)
    if not pfad.is_file():
        raise DataError(f"Kein Plan für {monat} gefunden ({pfad})")
    with pfad.open(encoding="utf-8") as fh:
        return MonthPlan.from_dict(json.load(fh))


def plan_vorhanden(plans_dir: Path, monat: str) -> bool:
    return plan_pfad(plans_dir, monat).is_file()


def dump_recipe(rezept: Recipe) -> str:
    """Ein Rezept als YAML serialisieren (für die Review-Warteschlange)."""
    daten: dict[str, Any] = {
        "slug": rezept.slug,
        "name": rezept.name,
        "core": rezept.core,
        "slots": list(rezept.slots),
        "tags": list(rezept.tags),
        "seasons": "all" if rezept.seasons == ALL_MONTHS else list(rezept.seasons),
        "prep_minutes": rezept.prep_minutes,
        "base_servings": rezept.base_servings,
        "protein_per_serving_g": rezept.protein_per_serving_g,
        "ingredients": [
            {"item": z.item, "qty": z.qty, "unit": z.unit}
            | ({"optional": True} if z.optional else {})
            | ({"hinweis": z.hinweis} if z.hinweis else {})
            for z in rezept.ingredients
        ],
        "steps": list(rezept.steps),
    }
    if rezept.variants:
        daten["variants"] = {
            name: {
                "hinweis": v.hinweis,
                "zusatz": [{"item": z.item, "qty": z.qty, "unit": z.unit} for z in v.zusatz],
                "entfernt": list(v.entfernt),
                "ersetzt": {
                    alt: {"item": neu.item, "qty": neu.qty, "unit": neu.unit}
                    for alt, neu in v.ersetzt.items()
                },
                "protein_per_serving_g": v.protein_per_serving_g,
                "zusatz_steps": list(v.zusatz_steps),
            }
            for name, v in rezept.variants.items()
        }
    return yaml.safe_dump(daten, allow_unicode=True, sort_keys=False)
