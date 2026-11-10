"""Datenmodell für Zutaten, Rezepte und Pläne."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

ALL_SLOTS = ("FR", "SA", "SO", "MO", "MI")
ALL_MONTHS = tuple(range(1, 13))


@dataclass(frozen=True, slots=True)
class Ingredient:
    """Stammdatensatz einer Zutat aus ingredients.yaml."""

    slug: str
    name: str
    type: str  # "pantry" | "fresh"
    department: str
    pack_qty: float
    pack_unit: str
    shelf_days: int = 0
    aliases: tuple[str, ...] = ()
    #: Gramm Protein je 100 g bzw. 100 ml. Grundlage der Nachrechnung in
    #: :mod:`essensplaner.nutrition`.
    protein_per_100: float = 0.0
    #: Gewicht einer Stückeinheit in Gramm – bei Dosen das Abtropfgewicht.
    #: Nötig, damit "2 Dosen" in Protein umgerechnet werden kann.
    stk_gewicht_g: float | None = None
    #: Empfindliche Frischware (Blattsalat, Kräuter, Avocado, Pilze, Beeren).
    #: Darf nur in Slots nahe am Einkaufstag eingeplant werden.
    empfindlich: bool = False
    #: Stärkebeilage bzw. Grundzutat (Kartoffeln, Nudeln, Reis, Brot, Mehl).
    #: Der Planer vermeidet, dass dieselbe Grundzutat die Woche dominiert, und
    #: zählt sie nicht als nützliche Zutatenüberlappung.
    grundzutat: bool = False
    #: True für Gewürzmischungen. Diese sind nur erlaubt, wenn fenchelfrei gesetzt ist.
    gewuerzmischung: bool = False
    fenchelfrei: bool | None = None

    @property
    def is_pantry(self) -> bool:
        return self.type == "pantry"

    @property
    def is_fresh(self) -> bool:
        return self.type == "fresh"

    def such_begriffe(self) -> tuple[str, ...]:
        """Alle Texte, gegen die die Ausschlussprüfung läuft."""
        return (self.slug.replace("_", " "), self.name, *self.aliases)


@dataclass(frozen=True, slots=True)
class RecipeIngredient:
    """Eine Zutatenzeile eines Rezepts, Menge bezogen auf base_servings."""

    item: str
    qty: float
    unit: str
    optional: bool = False
    hinweis: str = ""


@dataclass(frozen=True, slots=True)
class Variant:
    """Abweichung eines Rezepts für einen bestimmten Einsatz.

    ``protein`` wird in den Vorkoch-Slots (MO/MI) verwendet und hebt das Protein
    über das Pflichtminimum. ``family`` erlaubt eine mildere oder kindgerechtere
    Fassung für die Familien-Slots.
    """

    name: str
    hinweis: str = ""
    #: Zusätzliche Zutaten gegenüber der Basisfassung.
    zusatz: tuple[RecipeIngredient, ...] = ()
    #: Zutaten der Basisfassung, die in dieser Variante entfallen (Slugs).
    entfernt: tuple[str, ...] = ()
    #: Zutaten, die ersetzt werden: {alter_slug: neue Zutatenzeile}.
    ersetzt: dict[str, RecipeIngredient] = field(default_factory=dict)
    protein_per_serving_g: float | None = None
    zusatz_steps: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Recipe:
    """Ein Rezept aus data/recipes/<slug>.yaml."""

    slug: str
    name: str
    core: bool
    slots: tuple[str, ...]
    tags: tuple[str, ...]
    seasons: tuple[int, ...]
    prep_minutes: int
    base_servings: int
    protein_per_serving_g: float
    ingredients: tuple[RecipeIngredient, ...]
    steps: tuple[str, ...]
    variants: dict[str, Variant] = field(default_factory=dict)
    quelle: str = ""

    def hat_tag(self, tag: str) -> bool:
        return tag in self.tags

    def in_saison(self, monat: int) -> bool:
        return monat in self.seasons

    def erlaubt_in_slot(self, slot: str) -> bool:
        return slot in self.slots

    def variant_fuer(self, slot: str, protein_slots: tuple[str, ...]) -> str | None:
        """Welche Variante dieses Rezept in einem Slot nutzt."""
        if slot in protein_slots and "protein" in self.variants:
            return "protein"
        if slot not in protein_slots and "family" in self.variants:
            return "family"
        return None

    def zutaten_fuer(self, variant: str | None = None) -> tuple[RecipeIngredient, ...]:
        """Effektive Zutatenliste inklusive Variantenanpassungen."""
        if not variant or variant not in self.variants:
            return self.ingredients
        v = self.variants[variant]
        ergebnis: list[RecipeIngredient] = []
        for zutat in self.ingredients:
            if zutat.item in v.entfernt:
                continue
            if zutat.item in v.ersetzt:
                ergebnis.append(v.ersetzt[zutat.item])
                continue
            ergebnis.append(zutat)
        ergebnis.extend(v.zusatz)
        return tuple(ergebnis)

    def protein_fuer(self, variant: str | None = None) -> float:
        """Protein je Erwachsenenportion in der gewählten Variante."""
        if variant and variant in self.variants:
            override = self.variants[variant].protein_per_serving_g
            if override is not None:
                return override
        return self.protein_per_serving_g

    def steps_fuer(self, variant: str | None = None) -> tuple[str, ...]:
        if variant and variant in self.variants:
            return (*self.steps, *self.variants[variant].zusatz_steps)
        return self.steps


@dataclass(slots=True)
class PlannedMeal:
    """Ein belegter Slot im Plan."""

    slot: str
    datum: date
    recipe_slug: str
    recipe_name: str
    variant: str | None
    #: Erwachsenenportionen, die gekocht werden.
    portionen: float
    deckt_ab: str
    #: Esstage, die dieser Slot abdeckt – 1 für Freitag bis Sonntag,
    #: 2 für Montag und Mittwoch, deren Folgetag mitgegessen wird.
    tage: int = 1
    #: True, wenn doppelt gekocht und eine Portion eingefroren wird.
    batch_cook: bool = False
    #: True, wenn dieser Slot aus der Gefriertruhe bedient wird (nicht gekocht).
    aus_gefriertruhe: bool = False

    def to_dict(self) -> dict:
        return {
            "slot": self.slot,
            "datum": self.datum.isoformat(),
            "recipe_slug": self.recipe_slug,
            "recipe_name": self.recipe_name,
            "variant": self.variant,
            "portionen": self.portionen,
            "deckt_ab": self.deckt_ab,
            "tage": self.tage,
            "batch_cook": self.batch_cook,
            "aus_gefriertruhe": self.aus_gefriertruhe,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PlannedMeal:
        return cls(
            slot=d["slot"],
            datum=date.fromisoformat(d["datum"]),
            recipe_slug=d["recipe_slug"],
            recipe_name=d["recipe_name"],
            variant=d.get("variant"),
            portionen=d["portionen"],
            deckt_ab=d.get("deckt_ab", ""),
            tage=int(d.get("tage", 1)),
            batch_cook=d.get("batch_cook", False),
            aus_gefriertruhe=d.get("aus_gefriertruhe", False),
        )


@dataclass(slots=True)
class WeekPlan:
    """Eine Planungswoche von Freitag bis Donnerstag."""

    start: date  # immer ein Freitag
    meals: list[PlannedMeal] = field(default_factory=list)

    @property
    def ende(self) -> date:
        return self.start + timedelta(days=6)

    def meal(self, slot: str) -> PlannedMeal | None:
        for m in self.meals:
            if m.slot == slot:
                return m
        return None

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "ende": self.ende.isoformat(),
            "meals": [m.to_dict() for m in self.meals],
        }

    @classmethod
    def from_dict(cls, d: dict) -> WeekPlan:
        return cls(
            start=date.fromisoformat(d["start"]),
            meals=[PlannedMeal.from_dict(m) for m in d["meals"]],
        )


@dataclass(slots=True)
class MonthPlan:
    """Der Plan für einen Monat, aufgeteilt in Freitag-bis-Donnerstag-Wochen."""

    monat: str  # "2026-12"
    weeks: list[WeekPlan] = field(default_factory=list)
    erzeugt_am: date | None = None

    @property
    def alle_meals(self) -> list[PlannedMeal]:
        return [m for w in self.weeks for m in w.meals]

    def woche_fuer(self, tag: date) -> WeekPlan | None:
        """Die Planungswoche, die diesen Tag enthält."""
        for w in self.weeks:
            if w.start <= tag <= w.ende:
                return w
        return None

    def woche_ab(self, freitag: date) -> WeekPlan | None:
        for w in self.weeks:
            if w.start == freitag:
                return w
        return None

    def to_dict(self) -> dict:
        return {
            "monat": self.monat,
            "erzeugt_am": self.erzeugt_am.isoformat() if self.erzeugt_am else None,
            "weeks": [w.to_dict() for w in self.weeks],
        }

    @classmethod
    def from_dict(cls, d: dict) -> MonthPlan:
        return cls(
            monat=d["monat"],
            weeks=[WeekPlan.from_dict(w) for w in d["weeks"]],
            erzeugt_am=date.fromisoformat(d["erzeugt_am"]) if d.get("erzeugt_am") else None,
        )
