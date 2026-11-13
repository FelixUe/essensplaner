"""Regelbasierter Scheduler für den Monatsplan.

Vorgehen: Slots werden in der Reihenfolge ihrer Einschränkungen belegt
(MI zuerst, weil dort Haltbarkeit und Protein zusammenkommen, FR zuletzt).
Für jeden Slot bleiben nur Rezepte übrig, die alle harten Regeln erfüllen;
daraus wird gewichtet gezogen – Kernrezepte stärker, bereits oft geplante
Gerichte schwächer, Rezepte mit Zutatenüberlappung zur laufenden Woche
etwas stärker.

Scheitert ein Durchgang (ein Slot ohne zulässigen Kandidaten), wird mit
einem anderen Zufallsstrom neu begonnen. Erst wenn alle Versuche scheitern,
wirft der Planer einen Fehler mit Begründung – lieber kein Plan als ein Plan,
der eine harte Regel bricht.
"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from . import units
from .config import Config
from .kalender import monat_str, parse_monat, planungswochen, slot_datum
from .models import Ingredient, MonthPlan, PlannedMeal, Recipe, WeekPlan
from .rules import Issue, nur_fehler, pruefe_plan, pruefe_rezept

#: Reihenfolge, in der Slots belegt werden – von am stärksten eingeschränkt zu am freiesten.
SLOT_REIHENFOLGE = ("MI", "MO", "SA", "SO", "FR")

MAX_VERSUCHE = 400


class PlanungsFehler(Exception):
    """Es konnte kein Plan erzeugt werden, der alle harten Regeln erfüllt."""

    def __init__(self, nachricht: str, details: list[str] | None = None) -> None:
        super().__init__(nachricht)
        self.details = details or []


@dataclass(slots=True)
class _Zustand:
    """Laufender Zustand eines Planungsdurchgangs."""

    monatszaehler: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    termine: dict[str, list[date]] = field(default_factory=lambda: defaultdict(list))
    fisch_zaehler: int = 0
    gekocht_gesamt: int = 0
    core_gesamt: int = 0
    #: Rezepte, von denen eine Portion in der Truhe liegt: slug -> Quell-Mahlzeit.
    truhe: dict[str, PlannedMeal] = field(default_factory=dict)

    @property
    def core_anteil(self) -> float:
        if not self.gekocht_gesamt:
            return 0.0
        return self.core_gesamt / self.gekocht_gesamt


class Planner:
    """Erzeugt Monatspläne aus den freigegebenen Rezepten."""

    def __init__(
        self,
        rezepte: dict[str, Recipe],
        zutaten: dict[str, Ingredient],
        config: Config,
        gestrichen: set[str] | None = None,
    ) -> None:
        self.config = config
        self.zutaten = zutaten
        #: Von Hand gestrichene Gerichte. Sie bleiben ausserhalb des Planers
        #: ladbar, damit bestehende Plaene weiter stimmen.
        self.gestrichen: set[str] = set(gestrichen or ())
        # Nur Rezepte planen, die selbst regelkonform sind. Ein Rezept mit
        # ausgeschlossener Zutat oder zu wenig Protein im Protein-Slot kommt
        # gar nicht in den Kandidatenpool.
        self.rezepte: dict[str, Recipe] = {}
        self.abgelehnt: dict[str, list[Issue]] = {}
        for slug, rezept in rezepte.items():
            if slug in self.gestrichen:
                continue
            fehler = nur_fehler(pruefe_rezept(rezept, zutaten, config))
            if fehler:
                self.abgelehnt[slug] = fehler
            else:
                self.rezepte[slug] = rezept

    # ------------------------------------------------------------ Hilfsmittel

    #: Ab dieser Menge gilt eine Grundzutat als Hauptkomponente des Gerichts.
    GRUNDZUTAT_MIN_G = 200.0
    GRUNDZUTAT_MIN_STK = 2.0

    def _zutaten_slugs(self, rezept: Recipe, variante: str | None) -> set[str]:
        return {z.item for z in rezept.zutaten_fuer(variante)}

    def _grundzutaten(self, rezept: Recipe, variante: str | None) -> set[str]:
        """Die Stärkebeilagen, die dieses Gericht prägen.

        Die 40 g Mehl in einer Béchamel machen das Gericht nicht zum
        Mehlgericht – deshalb die Mengenschwelle.
        """
        treffer: set[str] = set()
        for zeile in rezept.zutaten_fuer(variante):
            zutat = self.zutaten.get(zeile.item)
            if zutat is None or not zutat.grundzutat or zeile.optional:
                continue
            menge, basis = units.in_basis(zeile.qty, zeile.unit)
            if basis in ("g", "ml"):
                if menge >= self.GRUNDZUTAT_MIN_G:
                    treffer.add(zeile.item)
            elif menge >= self.GRUNDZUTAT_MIN_STK:
                treffer.add(zeile.item)
        return treffer

    def _ist_highlight(self, rezept: Recipe) -> bool:
        return any(rezept.hat_tag(t) for t in self.config.wochenende_pflicht_tags)

    def _ist_fisch(self, rezept: Recipe) -> bool:
        return rezept.hat_tag("fisch") or any(
            "fischstaebchen" in z.item for z in rezept.ingredients
        )

    def _slot_passt(self, rezept: Recipe, slot: str, variante: str | None) -> bool:
        """Protein und Haltbarkeit am konkreten Slot prüfen.

        Doppelte Sicherung neben der Rezeptfreigabe, denn beides hängt nicht
        am Rezept allein, sondern am Platz: dasselbe Gericht kann samstags in
        Ordnung sein und montags nicht – weil dort 30 g Protein Pflicht sind
        oder weil der Blattsalat bis Mittwoch nicht mehr frisch ist.
        """
        if slot in self.config.protein_slots:
            if rezept.protein_fuer(variante) < self.config.protein_min_g:
                return False
        if slot not in self.config.frischware_slots:
            if any(
                (z := self.zutaten.get(zeile.item)) is not None and z.empfindlich
                for zeile in rezept.zutaten_fuer(variante)
                if not zeile.optional
            ):
                return False
        return True

    def _regeln_erlauben(
        self,
        rezept: Recipe,
        slot: str,
        datum: date,
        zustand: _Zustand,
        wochen_slugs: set[str],
    ) -> bool:
        """Harte Planungsregeln für ein Rezept in einem konkreten Slot."""
        if not rezept.erlaubt_in_slot(slot):
            return False
        if not rezept.in_saison(datum.month):
            return False
        if rezept.slug in wochen_slugs:
            return False  # kein Gericht zweimal in derselben Woche
        if zustand.monatszaehler[rezept.slug] >= self.config.max_pro_monat:
            return False
        for frueher in zustand.termine[rezept.slug]:
            if abs((datum - frueher).days) < self.config.min_abstand_tage:
                return False
        if (
            self._ist_fisch(rezept)
            and zustand.fisch_zaehler >= self.config.fischstaebchen_max_pro_monat
        ):
            return False
        return True

    def _gewicht(
        self,
        rezept: Recipe,
        zustand: _Zustand,
        wochen_zutaten: set[str],
        wochen_grundzutaten: set[str],
        variante: str | None,
    ) -> float:
        gewicht = 1.0

        # Kernrezepte sollen etwa core_anteil der Slots füllen.
        unter_ziel = zustand.core_anteil < self.config.core_anteil
        if rezept.core:
            gewicht *= 3.0 if unter_ziel else 0.6
        else:
            gewicht *= 0.5 if unter_ziel else 1.6

        # Schon geplante Gerichte abwerten, damit der Monat abwechslungsreich bleibt.
        gewicht *= 0.45 ** zustand.monatszaehler[rezept.slug]

        # Zutatenüberlappung innerhalb der Woche belohnen: ein halber Becher
        # Sour Cream, halber Kohl oder ein Bund Lauch soll in derselben Woche
        # aufgebraucht werden. Grundzutaten zählen hier nicht – sonst gäbe es
        # fünf Tage Kartoffeln, nur weil der Sack schon offen ist.
        if wochen_zutaten:
            eigene = self._zutaten_slugs(rezept, variante)
            nuetzlich = {
                s
                for s in eigene & wochen_zutaten
                if (z := self.zutaten.get(s)) is not None
                and z.is_fresh
                and not z.grundzutat
                and z.shelf_days <= 14
            }
            if eigene:
                gewicht *= 1.0 + 0.8 * (len(nuetzlich) / len(eigene))

        # Abwechslung bei der Stärkebeilage: was diese Woche schon die
        # Hauptrolle hatte, wird deutlich unwahrscheinlicher.
        if wochen_grundzutaten:
            wiederholt = self._grundzutaten(rezept, variante) & wochen_grundzutaten
            if wiederholt:
                gewicht *= 0.3 ** len(wiederholt)

        return max(gewicht, 1e-6)

    # --------------------------------------------------------- ein Durchgang

    def _versuch(self, jahr: int, monat: int, rng: random.Random) -> MonthPlan:
        zustand = _Zustand()
        plan = MonthPlan(monat=monat_str(jahr, monat), erzeugt_am=date.today())

        for wochenstart in planungswochen(jahr, monat):
            woche = WeekPlan(start=wochenstart)
            wochen_slugs: set[str] = set()
            wochen_zutaten: set[str] = set()
            wochen_grundzutaten: set[str] = set()

            # Einer der beiden Wochenendtage trägt das Pflicht-Highlight.
            highlight_slot = rng.choice(["SA", "SO"])
            highlight_erledigt = False

            for slot in SLOT_REIHENFOLGE:
                datum = slot_datum(wochenstart, slot)
                braucht_highlight = slot == highlight_slot and not highlight_erledigt

                meal = self._slot_belegen(
                    slot=slot,
                    datum=datum,
                    zustand=zustand,
                    wochen_slugs=wochen_slugs,
                    wochen_zutaten=wochen_zutaten,
                    wochen_grundzutaten=wochen_grundzutaten,
                    braucht_highlight=braucht_highlight,
                    rng=rng,
                )
                if meal is None:
                    raise PlanungsFehler(
                        f"Kein zulässiges Rezept für Slot {slot} am {datum}",
                        [f"Woche ab {wochenstart}, Slot {slot}"],
                    )

                rezept = self.rezepte[meal.recipe_slug]
                woche.meals.append(meal)
                wochen_slugs.add(meal.recipe_slug)
                wochen_zutaten |= self._zutaten_slugs(rezept, meal.variant)
                wochen_grundzutaten |= self._grundzutaten(rezept, meal.variant)
                zustand.monatszaehler[meal.recipe_slug] += 1
                zustand.termine[meal.recipe_slug].append(datum)
                if self._ist_fisch(rezept):
                    zustand.fisch_zaehler += 1
                if self._ist_highlight(rezept) and slot in ("SA", "SO"):
                    highlight_erledigt = True
                if not meal.aus_gefriertruhe:
                    zustand.gekocht_gesamt += 1
                    if rezept.core:
                        zustand.core_gesamt += 1

            # Slots in der Reihenfolge der Kochtage ablegen, nicht in Planungsreihenfolge.
            woche.meals.sort(key=lambda m: m.datum)
            plan.weeks.append(woche)

        return plan

    def _slot_belegen(
        self,
        slot: str,
        datum: date,
        zustand: _Zustand,
        wochen_slugs: set[str],
        wochen_zutaten: set[str],
        wochen_grundzutaten: set[str],
        braucht_highlight: bool,
        rng: random.Random,
    ) -> PlannedMeal | None:
        portionen = self.config.slot_portionen(slot)
        deckt_ab = self.config.slot_deckt_ab(slot)
        tage = self.config.slot_tage(slot)

        # Gefriertruhe: einen Vorkoch-Slot aus dem Vorrat bedienen, statt neu zu kochen.
        if (
            self.config.freezer
            and slot in self.config.protein_slots
            and not braucht_highlight
            and zustand.truhe
            and rng.random() < 0.35
        ):
            for slug, quelle in list(zustand.truhe.items()):
                rezept = self.rezepte.get(slug)
                if rezept is None:
                    continue
                if not self._regeln_erlauben(rezept, slot, datum, zustand, wochen_slugs):
                    continue
                del zustand.truhe[slug]
                quelle.batch_cook = True
                quelle.portionen = quelle.portionen * 2
                return PlannedMeal(
                    slot=slot,
                    datum=datum,
                    recipe_slug=slug,
                    recipe_name=rezept.name,
                    variant=quelle.variant,
                    portionen=portionen,
                    deckt_ab=deckt_ab,
                    tage=tage,
                    aus_gefriertruhe=True,
                )

        kandidaten: list[tuple[Recipe, str | None, float]] = []
        for rezept in self.rezepte.values():
            if braucht_highlight and not self._ist_highlight(rezept):
                continue
            if not self._regeln_erlauben(rezept, slot, datum, zustand, wochen_slugs):
                continue
            variante = rezept.variant_fuer(slot, self.config.protein_slots)
            if not self._slot_passt(rezept, slot, variante):
                continue

            gewicht = self._gewicht(rezept, zustand, wochen_zutaten, wochen_grundzutaten, variante)
            kandidaten.append((rezept, variante, gewicht))

        if not kandidaten:
            return None

        rezepte = [k[0] for k in kandidaten]
        gewichte = [k[2] for k in kandidaten]
        gewaehlt = rng.choices(range(len(rezepte)), weights=gewichte, k=1)[0]
        rezept, variante, _ = kandidaten[gewaehlt]

        meal = PlannedMeal(
            slot=slot,
            datum=datum,
            recipe_slug=rezept.slug,
            recipe_name=rezept.name,
            variant=variante,
            portionen=portionen,
            deckt_ab=deckt_ab,
            tage=tage,
        )

        # Für die Truhe vormerken, wenn das Gericht sich dafür eignet.
        if (
            self.config.freezer
            and rezept.hat_tag("freezer_ok")
            and slot in self.config.protein_slots
            and rezept.slug not in zustand.truhe
        ):
            zustand.truhe[rezept.slug] = meal

        return meal

    # ------------------------------------------------------------ öffentlich

    def plan_monat(self, monat: str, seed: int | None = None) -> MonthPlan:
        """Einen vollständigen, regelkonformen Monatsplan erzeugen.

        Es wird so lange neu gewürfelt, bis ein Plan alle harten Regeln erfüllt.
        """
        jahr, monat_nr = parse_monat(monat)
        basis_seed = seed if seed is not None else self.config.seed
        letzte_fehler: list[str] = []

        # Der Monat geht in den Seed ein, damit ein fester Seed denselben Monat
        # reproduzierbar macht, zwei Monate aber nicht denselben Plan bekommen.
        # sha256 statt hash(), weil Pythons String-Hash pro Prozess variiert.
        monats_offset = int(hashlib.sha256(monat.encode()).hexdigest()[:8], 16)

        for versuch in range(MAX_VERSUCHE):
            rng = random.Random(
                (basis_seed + monats_offset + versuch)
                if basis_seed is not None
                else random.randrange(1 << 30)
            )
            try:
                plan = self._versuch(jahr, monat_nr, rng)
            except PlanungsFehler as exc:
                letzte_fehler = [str(exc), *exc.details]
                continue

            fehler = nur_fehler(pruefe_plan(plan, self.rezepte, self.zutaten, self.config))
            if not fehler:
                return plan
            letzte_fehler = [str(f) for f in fehler]

        details = letzte_fehler[:10]
        if self.abgelehnt:
            details.append(
                "Nicht eingeplante Rezepte wegen eigener Regelverstöße: "
                + ", ".join(sorted(self.abgelehnt))
            )
        raise PlanungsFehler(
            f"Nach {MAX_VERSUCHE} Versuchen kein regelkonformer Plan für {monat}. "
            "Meist fehlen zulässige Rezepte für einen Slot – mehr Rezepte anlegen "
            "oder Regeln in config.yaml lockern.",
            details,
        )

    # ------------------------------------------ Plan nachträglich ändern

    @staticmethod
    def _gleicher_platz(a: PlannedMeal, b: PlannedMeal) -> bool:
        """Ob zwei Mahlzeiten denselben Platz im Monat meinen."""
        return a.slot == b.slot and a.datum == b.datum

    def _zustand_aus_plan(
        self,
        plan: MonthPlan,
        ohne: PlannedMeal | Iterable[PlannedMeal] | None = None,
    ) -> _Zustand:
        """Den Planungszustand aus einem fertigen Plan nachbilden.

        Gebraucht wird das beim Ändern eines fertigen Plans: dann soll der
        Zustand der des Monats sein, als stünden die betroffenen Slots noch
        leer. Beim Verschieben sind das zwei – sonst blockierten sich die
        beiden Gerichte über Monatsgrenze und Mindestabstand gegenseitig. Die Gefriertruhe bleibt aus – ein von Hand gewähltes Gericht
        wird gekocht, nicht aufgetaut.
        """
        if ohne is None:
            ausgenommen: tuple[PlannedMeal, ...] = ()
        elif isinstance(ohne, PlannedMeal):
            ausgenommen = (ohne,)
        else:
            ausgenommen = tuple(ohne)

        zustand = _Zustand()
        for meal in plan.alle_meals:
            if any(self._gleicher_platz(meal, frei) for frei in ausgenommen):
                continue
            rezept = self.rezepte.get(meal.recipe_slug)
            zustand.monatszaehler[meal.recipe_slug] += 1
            zustand.termine[meal.recipe_slug].append(meal.datum)
            if rezept is not None and self._ist_fisch(rezept):
                zustand.fisch_zaehler += 1
            if not meal.aus_gefriertruhe:
                zustand.gekocht_gesamt += 1
                if rezept is not None and rezept.core:
                    zustand.core_gesamt += 1
        return zustand

    def alternativen(self, plan: MonthPlan, datum: date, slot: str) -> list[Recipe]:
        """Welche Gerichte in diesem Slot ebenfalls zulässig wären.

        Geprüft werden dieselben harten Regeln wie beim Planen, nur mit dem
        betroffenen Slot als leer gedacht: Slot-Zulassung, Saison, kein
        Gericht zweimal in einer Woche, Monatsobergrenze, Mindestabstand und
        die Fischstäbchen-Grenze. Was hier nicht auftaucht, darf dort nicht
        stehen – deshalb bekommt die Oberfläche genau diese Liste und keine
        längere mit Warnhinweisen.
        """
        woche = plan.woche_fuer(datum)
        if woche is None:
            return []
        aktuell = woche.meal(slot)
        zustand = self._zustand_aus_plan(plan, ohne=aktuell)
        wochen_slugs = {
            m.recipe_slug for m in woche.meals if not (m.slot == slot and m.datum == datum)
        }
        treffer = [
            rezept
            for rezept in self.rezepte.values()
            if (aktuell is None or rezept.slug != aktuell.recipe_slug)
            and self._regeln_erlauben(rezept, slot, datum, zustand, wochen_slugs)
            and self._slot_passt(rezept, slot, rezept.variant_fuer(slot, self.config.protein_slots))
        ]
        return sorted(treffer, key=lambda r: r.name.lower())

    def mahlzeit_am(self, plan: MonthPlan, datum: date, slot: str) -> PlannedMeal | None:
        """Die Mahlzeit, die an diesem Platz im Plan steht."""
        woche = plan.woche_fuer(datum)
        if woche is None:
            return None
        meal = woche.meal(slot)
        return meal if meal is not None and meal.datum == datum else None

    def tausch_moeglich(self, plan: MonthPlan, a: PlannedMeal, b: PlannedMeal) -> bool:
        """Ob zwei Termine die Plätze tauschen könnten.

        Geprüft wird in beide Richtungen und mit beiden Plätzen als leer
        gedacht: jedes der Gerichte muss dort stehen dürfen, wo das andere
        steht. Würde nur eine Richtung geprüft, käme ein Gericht auf einen
        Montag, für den es zu wenig Protein hat.
        """
        rezept_a = self.rezepte.get(a.recipe_slug)
        rezept_b = self.rezepte.get(b.recipe_slug)
        if rezept_a is None or rezept_b is None:
            return False
        if self._gleicher_platz(a, b) or rezept_a.slug == rezept_b.slug:
            return False
        # Gefriertruhe: der Platz hängt an einer anderen Woche, in der doppelt
        # gekocht wurde. Verschieben würde diese Kopplung zerreißen.
        if any(m.aus_gefriertruhe or m.batch_cook for m in (a, b)):
            return False

        zustand = self._zustand_aus_plan(plan, ohne=(a, b))
        for ziel, rezept in ((a, rezept_b), (b, rezept_a)):
            woche = plan.woche_fuer(ziel.datum)
            if woche is None:  # pragma: no cover - Mahlzeiten stammen aus dem Plan
                return False
            wochen_slugs = {
                m.recipe_slug
                for m in woche.meals
                if not self._gleicher_platz(m, a) and not self._gleicher_platz(m, b)
            }
            if not self._regeln_erlauben(rezept, ziel.slot, ziel.datum, zustand, wochen_slugs):
                return False
            variante = rezept.variant_fuer(ziel.slot, self.config.protein_slots)
            if not self._slot_passt(rezept, ziel.slot, variante):
                return False
        return True

    def tauschpartner(self, plan: MonthPlan, datum: date, slot: str) -> list[PlannedMeal]:
        """Mit welchen anderen Terminen dieses Gericht die Plätze tauschen kann.

        Der Planer würfelt einen Monat, in dem alles passt; was er nicht
        weiß, ist, dass am Samstag Besuch kommt. Dann soll sich das Gericht
        auf einen anderen Tag schieben lassen, statt gegen ein ganz anderes
        ausgetauscht zu werden. Zurück kommen nur Termine, bei denen der
        Tausch in beide Richtungen regelkonform wäre.
        """
        quelle = self.mahlzeit_am(plan, datum, slot)
        if quelle is None:
            return []
        partner = [m for m in plan.alle_meals if self.tausch_moeglich(plan, quelle, m)]
        return sorted(partner, key=lambda m: (m.datum, m.slot))

    def mahlzeit_bauen(self, rezept: Recipe, slot: str, datum: date) -> PlannedMeal:
        """Eine Mahlzeit für einen Slot zusammensetzen, ohne zu würfeln."""
        return PlannedMeal(
            slot=slot,
            datum=datum,
            recipe_slug=rezept.slug,
            recipe_name=rezept.name,
            variant=rezept.variant_fuer(slot, self.config.protein_slots),
            portionen=self.config.slot_portionen(slot),
            deckt_ab=self.config.slot_deckt_ab(slot),
            tage=self.config.slot_tage(slot),
        )

    def statistik(self, plan: MonthPlan) -> dict[str, object]:
        """Kennzahlen eines Plans für die Vorschau-Mail und die CLI."""
        meals = plan.alle_meals
        gekocht = [m for m in meals if not m.aus_gefriertruhe]
        core = sum(1 for m in gekocht if (r := self.rezepte.get(m.recipe_slug)) and r.core)
        haeufigkeit: dict[str, int] = defaultdict(int)
        for m in meals:
            haeufigkeit[m.recipe_name] += 1
        protein_werte = [
            self.rezepte[m.recipe_slug].protein_fuer(m.variant)
            for m in meals
            if m.slot in self.config.protein_slots and m.recipe_slug in self.rezepte
        ]
        return {
            "slots": len(meals),
            "wochen": len(plan.weeks),
            "gekocht": len(gekocht),
            "core_anteil": (core / len(gekocht)) if gekocht else 0.0,
            "verschiedene_gerichte": len({m.recipe_slug for m in meals}),
            "haeufigkeit": dict(sorted(haeufigkeit.items(), key=lambda kv: (-kv[1], kv[0]))),
            "protein_min": min(protein_werte) if protein_werte else 0.0,
            "protein_schnitt": (sum(protein_werte) / len(protein_werte)) if protein_werte else 0.0,
            "batch_cook": sum(1 for m in meals if m.batch_cook),
            "aus_truhe": sum(1 for m in meals if m.aus_gefriertruhe),
        }
