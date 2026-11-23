"""Rezepte nach Mealie übertragen.

Mealie (https://mealie.io) ist eine Rezeptverwaltung mit Wochenplan und
Einkaufslisten. Der Essensplaner bleibt die Quelle: ``essensplaner
mealie-sync`` schreibt alle Rezepte **einseitig** nach Mealie und
überschreibt dort Rezepte, die er selbst angelegt hat. Was in Mealie von Hand
dazukommt, bleibt unangetastet.

Zuordnung: Jedes übertragene Rezept trägt das Tag ``Essensplaner`` und in
``extras`` den Kurznamen (``essensplaner_slug``). Daran erkennt der nächste
Lauf, welches Mealie-Rezept zu welchem YAML gehört – auch wenn der Name sich
geändert hat.

Zutaten werden als echte Mealie-Einheiten und -Lebensmittel angelegt, die
Abteilung aus ``ingredients.yaml`` wird zum Mealie-Label. Damit fasst eine
Mealie-Einkaufsliste dieselbe Zutat zusammen und sortiert nach Abteilung.

Nur Standardbibliothek (urllib), damit das Image ohne neue Abhängigkeit
auskommt.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import ALL_MONTHS, Ingredient, Recipe, RecipeIngredient
from .units import einheit_anzeige, normalisiere_einheit

log = logging.getLogger(__name__)

#: Tag, an dem der Sync seine eigenen Rezepte erkennt.
SYNC_TAG = "Essensplaner"
#: Schlüssel in Mealies ``extras``.
EXTRA_SLUG = "essensplaner_slug"

#: Lesbare Namen für die Tags aus den Rezept-YAMLs.
TAG_NAMEN: dict[str, str] = {
    "lunchbox_ok": "Lunchbox",
    "freezer_ok": "Einfrierbar",
    "kids_ok": "Kinder",
    "schnell": "Schnell",
    "beilage": "Beilage",
    "fisch": "Fisch",
    "suess": "Süß",
    "zuenftig": "Zünftig",
}

SLOT_NAMEN: dict[str, str] = {
    "FR": "Freitag",
    "SA": "Samstag",
    "SO": "Sonntag",
    "MO": "Montag (vorkochen)",
    "MI": "Mittwoch (vorkochen)",
}

MONATE = ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")


class MealieFehler(Exception):
    """Mealie nicht erreichbar oder Anfrage abgelehnt."""


class Transport(Protocol):
    def __call__(self, methode: str, pfad: str, daten: Any = None) -> Any: ...


class HttpTransport:
    """JSON über HTTP mit API-Token."""

    def __init__(self, basis_url: str, token: str, timeout: float = 30.0) -> None:
        self.basis_url = basis_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def __call__(self, methode: str, pfad: str, daten: Any = None) -> Any:
        body = None if daten is None else json.dumps(daten).encode()
        anfrage = urllib.request.Request(
            self.basis_url + pfad,
            data=body,
            method=methode,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(anfrage, timeout=self.timeout) as antwort:
                inhalt = antwort.read()
        except urllib.error.HTTPError as exc:
            text = exc.read().decode(errors="replace")[:300]
            raise MealieFehler(f"{methode} {pfad}: HTTP {exc.code} {text}") from exc
        except urllib.error.URLError as exc:
            raise MealieFehler(f"Mealie unter {self.basis_url} nicht erreichbar: {exc}") from exc
        return json.loads(inhalt) if inhalt else None


# --------------------------------------------------------------- Umwandlung


def _saison_text(rezept: Recipe) -> str:
    if rezept.seasons == ALL_MONTHS:
        return "ganzjährig"
    return ", ".join(MONATE[m - 1] for m in rezept.seasons)


def _zahl(wert: float) -> str:
    return str(int(wert)) if float(wert).is_integer() else str(wert).replace(".", ",")


def beschreibung(rezept: Recipe) -> str:
    """Kurzer Text mit dem, was in Mealie kein eigenes Feld hat."""
    slots = ", ".join(SLOT_NAMEN.get(s, s) for s in rezept.slots)
    teile = [
        f"Slots: {slots}",
        f"Saison: {_saison_text(rezept)}",
        f"{_zahl(rezept.protein_per_serving_g)} g Protein je Portion",
    ]
    if rezept.core:
        teile.insert(0, "Lieblingsgericht")
    return " · ".join(teile)


def _zutaten_text(zeilen: tuple[RecipeIngredient, ...], zutaten: dict[str, Ingredient]) -> str:
    teile = []
    for z in zeilen:
        name = zutaten[z.item].name if z.item in zutaten else z.item
        teile.append(f"{_zahl(z.qty)} {einheit_anzeige(z.unit)} {name}")
    return ", ".join(teile)


def notizen(rezept: Recipe, zutaten: dict[str, Ingredient]) -> list[dict[str, str]]:
    """Die Varianten als Mealie-Notizen – Mealie kennt keine Rezeptvarianten."""
    ergebnis = []
    for name, v in rezept.variants.items():
        zeilen = []
        if v.hinweis:
            zeilen.append(v.hinweis)
        if v.zusatz:
            zeilen.append("Zusätzlich: " + _zutaten_text(v.zusatz, zutaten))
        if v.entfernt:
            weg = [zutaten[s].name if s in zutaten else s for s in v.entfernt]
            zeilen.append("Ohne: " + ", ".join(weg))
        if v.ersetzt:
            for alt, neu in v.ersetzt.items():
                alt_name = zutaten[alt].name if alt in zutaten else alt
                zeilen.append(f"Statt {alt_name}: " + _zutaten_text((neu,), zutaten))
        zeilen.extend(v.zusatz_steps)
        if v.protein_per_serving_g is not None:
            zeilen.append(f"{_zahl(v.protein_per_serving_g)} g Protein je Portion")
        titel = {"protein": "Protein-Variante (MO/MI)", "family": "Familien-Variante"}.get(
            name, f"Variante {name}"
        )
        ergebnis.append({"title": titel, "text": "\n".join(zeilen)})
    return ergebnis


# ------------------------------------------------------------------ Sync


@dataclass
class Bericht:
    angelegt: list[str] = field(default_factory=list)
    aktualisiert: list[str] = field(default_factory=list)
    #: In Mealie mit Sync-Tag, aber im Essensplaner nicht (mehr) vorhanden.
    verwaist: list[str] = field(default_factory=list)


class MealieSync:
    """Hält die Nachschlagetabellen eines Laufs (Tags, Einheiten, Lebensmittel, Labels)."""

    def __init__(self, transport: Transport, dry_run: bool = False) -> None:
        self.t = transport
        self.dry_run = dry_run
        self._tags: dict[str, dict] = {}
        self._einheiten: dict[str, dict] = {}
        self._lebensmittel: dict[str, dict] = {}
        self._labels: dict[str, dict] = {}

    # -- Nachschlagen und Anlegen -------------------------------------------

    def _alle(self, pfad: str) -> list[dict]:
        seite = self.t("GET", f"{pfad}?page=1&perPage=-1")
        return list(seite.get("items", []))

    def _laden(self) -> None:
        self._tags = {t["name"].lower(): t for t in self._alle("/api/organizers/tags")}
        self._einheiten = {e["name"].lower(): e for e in self._alle("/api/units")}
        self._lebensmittel = {f["name"].lower(): f for f in self._alle("/api/foods")}
        self.lade_labels()

    def lade_labels(self) -> None:
        self._labels = {lb["name"].lower(): lb for lb in self._alle("/api/groups/labels")}

    def _hole_oder_lege_an(
        self, tabelle: dict[str, dict], pfad: str, name: str, daten: dict
    ) -> dict:
        schluessel = name.lower()
        if schluessel not in tabelle:
            if self.dry_run:
                tabelle[schluessel] = {"id": None, "name": name, "slug": schluessel}
            else:
                tabelle[schluessel] = self.t("POST", pfad, {"name": name, **daten})
        return tabelle[schluessel]

    def tag(self, name: str) -> dict:
        t = self._hole_oder_lege_an(self._tags, "/api/organizers/tags", name, {})
        return {"id": t["id"], "name": t["name"], "slug": t.get("slug")}

    def label(self, name: str) -> dict:
        return self._hole_oder_lege_an(self._labels, "/api/groups/labels", name, {})

    def einheit(self, einheit: str) -> dict:
        name = einheit_anzeige(einheit)
        e = self._hole_oder_lege_an(
            self._einheiten,
            "/api/units",
            name,
            {"abbreviation": name, "useAbbreviation": normalisiere_einheit(einheit) == name},
        )
        return {"id": e["id"], "name": e["name"]}

    def lebensmittel(self, zutat: Ingredient | None, item: str) -> dict:
        if zutat is None:
            f = self._hole_oder_lege_an(self._lebensmittel, "/api/foods", item, {})
        else:
            label = self.label(zutat.department)
            f = self._hole_oder_lege_an(
                self._lebensmittel, "/api/foods", zutat.name, {"labelId": label["id"]}
            )
        return {"id": f["id"], "name": f["name"]}

    # -- Rezept ---------------------------------------------------------------

    def rezept_daten(self, rezept: Recipe, zutaten: dict[str, Ingredient], gestrichen: bool):
        """Die Felder, die der Sync in Mealie setzt (alles andere bleibt, wie es ist)."""
        tag_namen = [SYNC_TAG, *(TAG_NAMEN.get(t, t) for t in rezept.tags)]
        if rezept.core:
            tag_namen.append("Lieblingsgericht")
        if gestrichen:
            tag_namen.append("Gestrichen")

        zeilen = []
        for z in rezept.ingredients:
            notiz = " · ".join(x for x in (z.hinweis, "optional" if z.optional else "") if x)
            zeilen.append(
                {
                    "quantity": z.qty,
                    "unit": self.einheit(z.unit),
                    "food": self.lebensmittel(zutaten.get(z.item), z.item),
                    "note": notiz,
                    "originalText": None,
                }
            )

        return {
            "name": rezept.name,
            "description": beschreibung(rezept),
            "recipeServings": rezept.base_servings,
            "recipeYieldQuantity": 0,
            "recipeYield": "",
            "totalTime": f"{rezept.prep_minutes} Minuten",
            "prepTime": None,
            "performTime": None,
            "tags": [self.tag(n) for n in tag_namen],
            "recipeIngredient": zeilen,
            "recipeInstructions": [{"text": s} for s in rezept.steps],
            "nutrition": {"proteinContent": _zahl(rezept.protein_per_serving_g)},
            "notes": notizen(rezept, zutaten),
            "extras": {EXTRA_SLUG: rezept.slug},
        }

    def vorhandene(self) -> dict[str, str]:
        """Essensplaner-Slug → Mealie-Slug aller schon übertragenen Rezepte."""
        ergebnis: dict[str, str] = {}
        for zusammenfassung in self._alle("/api/recipes"):
            tags = {t["name"].lower() for t in zusammenfassung.get("tags") or []}
            if SYNC_TAG.lower() not in tags:
                continue
            voll = self.t("GET", f"/api/recipes/{zusammenfassung['slug']}")
            slug = (voll.get("extras") or {}).get(EXTRA_SLUG)
            if slug:
                ergebnis[slug] = voll["slug"]
        return ergebnis

    def synchronisiere(
        self,
        rezepte: dict[str, Recipe],
        zutaten: dict[str, Ingredient],
        gestrichen: set[str] | None = None,
    ) -> Bericht:
        gestrichen = gestrichen or set()
        self._laden()
        vorhanden = self.vorhandene()
        bericht = Bericht()

        for slug in sorted(rezepte):
            rezept = rezepte[slug]
            daten = self.rezept_daten(rezept, zutaten, slug in gestrichen)
            mealie_slug = vorhanden.get(slug)
            if mealie_slug is None:
                bericht.angelegt.append(slug)
                if self.dry_run:
                    continue
                mealie_slug = self.t("POST", "/api/recipes", {"name": rezept.name})
            else:
                bericht.aktualisiert.append(slug)
                if self.dry_run:
                    continue
            aktuell = self.t("GET", f"/api/recipes/{mealie_slug}")
            self.t("PUT", f"/api/recipes/{mealie_slug}", {**aktuell, **daten})
            log.info("Mealie: %s → %s", slug, mealie_slug)

        bericht.verwaist = sorted(set(vorhanden) - set(rezepte))
        return bericht
