"""Harte Regeln: Ausschlüsse, Protein, Saison, Haltbarkeit, Wiederholung.

Jede Regel ist einzeln prüfbar und wird an drei Stellen benutzt:

* beim Laden der Stammdaten (``pruefe_stammdaten``),
* während der Planung (Kandidatenfilter im Planer),
* nach der Planung als Abnahme des fertigen Plans (``pruefe_plan``).

Damit kann kein Weg am Ausschluss- und Proteincheck vorbeiführen – auch nicht
für Rezepte, die später per Claude API dazukommen.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from .config import MAX_TAGE_JE_SLOT, Config, Exclusion
from .models import Ingredient, MonthPlan, Recipe

SEVERITY_FEHLER = "fehler"
SEVERITY_WARNUNG = "warnung"


@dataclass(frozen=True, slots=True)
class Issue:
    """Ein Regelverstoß. ``code`` ist stabil und wird in Tests benutzt."""

    code: str
    nachricht: str
    severity: str = SEVERITY_FEHLER
    rezept: str = ""
    slot: str = ""

    def __str__(self) -> str:
        teile = [self.code]
        if self.rezept:
            teile.append(self.rezept)
        if self.slot:
            teile.append(self.slot)
        return f"[{'/'.join(teile)}] {self.nachricht}"


def ist_fehler(issues: list[Issue]) -> bool:
    return any(i.severity == SEVERITY_FEHLER for i in issues)


def nur_fehler(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == SEVERITY_FEHLER]


# ------------------------------------------------------------------ Textsuche


def normalisieren(text: str) -> str:
    """Kleinschreiben und Umlaute vereinheitlichen, damit 'Blauschimmelkäse'
    und 'Blauschimmelkaese' gleich behandelt werden."""
    text = text.lower().replace("ß", "ss")
    text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    # Restliche Diakritika entfernen (z. B. é in Sauce Béarnaise).
    zerlegt = unicodedata.normalize("NFKD", text)
    return "".join(c for c in zerlegt if not unicodedata.combining(c))


def _treffer_positionen(text: str, begriff: str) -> list[tuple[int, int]]:
    positionen: list[tuple[int, int]] = []
    start = 0
    while True:
        idx = text.find(begriff, start)
        if idx < 0:
            return positionen
        positionen.append((idx, idx + len(begriff)))
        start = idx + 1


def ausschluss_treffer(text: str, exclusions: tuple[Exclusion, ...]) -> list[tuple[str, str]]:
    """Alle Ausschlusstreffer in einem Text als (Gruppe, Begriff).

    Eine Ausnahme hebt einen Treffer nur auf, wenn der Treffer vollständig
    innerhalb des Ausnahmebegriffs liegt. ``Camembert`` bleibt damit erlaubt,
    ``Blauschimmel-Camembert`` würde trotzdem auffallen.
    """
    norm = normalisieren(text)
    gefunden: list[tuple[str, str]] = []
    for gruppe in exclusions:
        ausnahme_spannen: list[tuple[int, int]] = []
        for ausnahme in gruppe.ausnahmen:
            ausnahme_spannen.extend(_treffer_positionen(norm, normalisieren(ausnahme)))
        for begriff in gruppe.begriffe:
            for start, ende in _treffer_positionen(norm, normalisieren(begriff)):
                abgedeckt = any(a <= start and ende <= b for a, b in ausnahme_spannen)
                if not abgedeckt:
                    gefunden.append((gruppe.gruppe, begriff))
                    break
    return gefunden


# ------------------------------------------------------------- Zutatenprüfung


def pruefe_zutat(zutat: Ingredient, config: Config) -> list[Issue]:
    """Eine Zutat gegen die Ausschlussliste und die Gewürzmischungsregel prüfen."""
    issues: list[Issue] = []
    for text in zutat.such_begriffe():
        for gruppe, begriff in ausschluss_treffer(text, config.ausschluesse):
            issues.append(
                Issue(
                    code="ausschluss",
                    nachricht=(
                        f"Zutat {zutat.slug!r} ({zutat.name}) trifft Ausschluss "
                        f"{gruppe!r} über {begriff!r}"
                    ),
                    rezept="",
                )
            )
    if config.gewuerzmischungen_pruefen and zutat.gewuerzmischung and zutat.fenchelfrei is not True:
        issues.append(
            Issue(
                code="gewuerzmischung_ungeprueft",
                nachricht=(
                    f"Gewürzmischung {zutat.slug!r} ist nicht als fenchelfrei markiert. "
                    "Entweder fenchelfrei: true setzen oder die Einzelgewürze ausschreiben."
                ),
            )
        )
    # Mehrfachtreffer derselben Zutat auf eine Meldung je Gruppe eindampfen.
    gesehen: set[str] = set()
    eindeutig: list[Issue] = []
    for i in issues:
        if i.nachricht in gesehen:
            continue
        gesehen.add(i.nachricht)
        eindeutig.append(i)
    return eindeutig


# -------------------------------------------------------------- Rezeptprüfung


def pruefe_rezept_ausschluesse(
    rezept: Recipe, zutaten: dict[str, Ingredient], config: Config
) -> list[Issue]:
    """Rezeptname, Arbeitsschritte und alle Zutaten jeder Variante prüfen."""
    issues: list[Issue] = []

    for feld, text in [("name", rezept.name), *[("step", s) for s in rezept.steps]]:
        for gruppe, begriff in ausschluss_treffer(text, config.ausschluesse):
            issues.append(
                Issue(
                    code="ausschluss",
                    nachricht=f"{feld} enthält {begriff!r} (Gruppe {gruppe!r}): {text!r}",
                    rezept=rezept.slug,
                )
            )

    varianten: list[str | None] = [None, *rezept.variants.keys()]
    for variante in varianten:
        for v in rezept.variants.values():
            for text in v.zusatz_steps:
                for gruppe, begriff in ausschluss_treffer(text, config.ausschluesse):
                    issues.append(
                        Issue(
                            code="ausschluss",
                            nachricht=f"Variantenschritt enthält {begriff!r} (Gruppe {gruppe!r})",
                            rezept=rezept.slug,
                        )
                    )
        for zeile in rezept.zutaten_fuer(variante):
            zutat = zutaten.get(zeile.item)
            if zutat is None:
                issues.append(
                    Issue(
                        code="zutat_unbekannt",
                        nachricht=f"Zutat {zeile.item!r} hat keine Stammdaten in ingredients.yaml",
                        rezept=rezept.slug,
                    )
                )
                continue
            for issue in pruefe_zutat(zutat, config):
                issues.append(
                    Issue(
                        code=issue.code,
                        nachricht=issue.nachricht,
                        severity=issue.severity,
                        rezept=rezept.slug,
                    )
                )
            if zeile.hinweis:
                for gruppe, begriff in ausschluss_treffer(zeile.hinweis, config.ausschluesse):
                    issues.append(
                        Issue(
                            code="ausschluss",
                            nachricht=f"Zutatenhinweis enthält {begriff!r} (Gruppe {gruppe!r})",
                            rezept=rezept.slug,
                        )
                    )

    gesehen: set[tuple[str, str]] = set()
    eindeutig: list[Issue] = []
    for i in issues:
        schluessel = (i.code, i.nachricht)
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        eindeutig.append(i)
    return eindeutig


def pruefe_rezept_protein(rezept: Recipe, config: Config) -> list[Issue]:
    """Jedes Rezept, das für einen Protein-Slot zugelassen ist, muss dort das
    Minimum erreichen – notfalls über seine Protein-Variante."""
    issues: list[Issue] = []
    for slot in config.protein_slots:
        if not rezept.erlaubt_in_slot(slot):
            continue
        variante = rezept.variant_fuer(slot, config.protein_slots)
        protein = rezept.protein_fuer(variante)
        if protein < config.protein_min_g:
            issues.append(
                Issue(
                    code="protein_zu_niedrig",
                    nachricht=(
                        f"{protein:.0f} g Protein je Portion in Slot {slot} "
                        f"(Variante {variante or 'basis'}), Minimum ist "
                        f"{config.protein_min_g:.0f} g. Protein-Variante ergänzen "
                        f"oder {slot} aus slots entfernen."
                    ),
                    rezept=rezept.slug,
                    slot=slot,
                )
            )
    return issues


def empfindliche_zutaten(
    rezept: Recipe, variante: str | None, zutaten: dict[str, Ingredient]
) -> list[str]:
    """Namen aller empfindlichen Frischwaren einer Rezeptvariante."""
    treffer: list[str] = []
    for zeile in rezept.zutaten_fuer(variante):
        if zeile.optional:
            continue
        zutat = zutaten.get(zeile.item)
        if zutat is not None and zutat.empfindlich:
            treffer.append(zutat.name)
    return treffer


def pruefe_rezept_haltbarkeit(
    rezept: Recipe, zutaten: dict[str, Ingredient], config: Config
) -> list[Issue]:
    """Rezepte mit empfindlicher Frischware dürfen nicht in späte Slots (MI)."""
    issues: list[Issue] = []
    spaete_slots = [s for s in rezept.slots if s not in config.frischware_slots]
    for slot in spaete_slots:
        variante = rezept.variant_fuer(slot, config.protein_slots)
        heikel = empfindliche_zutaten(rezept, variante, zutaten)
        if heikel:
            issues.append(
                Issue(
                    code="empfindliche_zutat_spaeter_slot",
                    nachricht=(
                        f"Slot {slot} liegt zu weit nach dem Einkaufstag für "
                        f"{', '.join(sorted(set(heikel)))}"
                    ),
                    rezept=rezept.slug,
                    slot=slot,
                )
            )
    return issues


def pruefe_rezept(rezept: Recipe, zutaten: dict[str, Ingredient], config: Config) -> list[Issue]:
    """Vollständige Prüfung eines Rezepts. Muss leer sein, bevor gespeichert
    oder eingeplant wird."""
    return [
        *pruefe_rezept_ausschluesse(rezept, zutaten, config),
        *pruefe_rezept_protein(rezept, config),
        *pruefe_rezept_haltbarkeit(rezept, zutaten, config),
    ]


def pruefe_slots(config: Config) -> list[Issue]:
    """Die Tagesabdeckung der Slots prüfen.

    Ein Slot über zwei Tagen hieße, dasselbe Gericht drei Tage hintereinander
    zu essen. Die Grenze steht in der Konfiguration und wird hier geprüft,
    damit ein Tippfehler in config.yaml nicht still zu längeren Resten führt.
    """
    issues: list[Issue] = []
    for slot in config.slot_namen:
        tage = config.slot_tage(slot)
        if tage < 1:
            issues.append(
                Issue(
                    code="slot_tage_ungueltig",
                    nachricht=f"Slot {slot} deckt {tage} Tage ab – mindestens einer muss es sein",
                    slot=slot,
                )
            )
        elif tage > MAX_TAGE_JE_SLOT:
            issues.append(
                Issue(
                    code="slot_zu_viele_tage",
                    nachricht=(
                        f"Slot {slot} deckt {tage} Tage ab, erlaubt sind höchstens "
                        f"{MAX_TAGE_JE_SLOT} – so lange soll niemand dasselbe essen"
                    ),
                    slot=slot,
                )
            )
    return issues


def pruefe_stammdaten(
    rezepte: dict[str, Recipe], zutaten: dict[str, Ingredient], config: Config
) -> list[Issue]:
    """Alle Zutaten und Rezepte des Repos prüfen."""
    issues: list[Issue] = pruefe_slots(config)
    for zutat in zutaten.values():
        issues.extend(pruefe_zutat(zutat, config))
    for rezept in rezepte.values():
        issues.extend(pruefe_rezept(rezept, zutaten, config))
    return issues


# ----------------------------------------------------------------- Planprüfung


def _ist_fischstaebchen(rezept: Recipe) -> bool:
    if rezept.hat_tag("fisch"):
        return True
    return any("fischstaebchen" in z.item for z in rezept.ingredients)


def pruefe_plan(
    plan: MonthPlan,
    rezepte: dict[str, Recipe],
    zutaten: dict[str, Ingredient],
    config: Config,
) -> list[Issue]:
    """Den fertigen Monatsplan gegen alle Akzeptanzkriterien prüfen."""
    issues: list[Issue] = []
    gekocht = [m for m in plan.alle_meals if not m.aus_gefriertruhe]

    # Kriterium 1: keine ausgeschlossene Zutat im gesamten Plan.
    for meal in plan.alle_meals:
        rezept = rezepte.get(meal.recipe_slug)
        if rezept is None:
            issues.append(
                Issue(
                    code="rezept_unbekannt",
                    nachricht=f"Plan verweist auf unbekanntes Rezept {meal.recipe_slug!r}",
                    rezept=meal.recipe_slug,
                    slot=meal.slot,
                )
            )
            continue
        for issue in pruefe_rezept_ausschluesse(rezept, zutaten, config):
            issues.append(
                Issue(
                    code=issue.code,
                    nachricht=issue.nachricht,
                    severity=issue.severity,
                    rezept=rezept.slug,
                    slot=meal.slot,
                )
            )

        # Kriterium 2: Protein in den Vorkoch-Slots.
        if meal.slot in config.protein_slots:
            protein = rezept.protein_fuer(meal.variant)
            if protein < config.protein_min_g:
                issues.append(
                    Issue(
                        code="protein_zu_niedrig",
                        nachricht=(
                            f"{rezept.name} am {meal.datum} liefert {protein:.0f} g Protein, "
                            f"Minimum ist {config.protein_min_g:.0f} g"
                        ),
                        rezept=rezept.slug,
                        slot=meal.slot,
                    )
                )

        # Slot-Zulässigkeit und Saison.
        if not rezept.erlaubt_in_slot(meal.slot):
            issues.append(
                Issue(
                    code="slot_unzulaessig",
                    nachricht=f"{rezept.name} ist für Slot {meal.slot} nicht freigegeben",
                    rezept=rezept.slug,
                    slot=meal.slot,
                )
            )
        if not rezept.in_saison(meal.datum.month):
            issues.append(
                Issue(
                    code="ausserhalb_saison",
                    nachricht=f"{rezept.name} ist im Monat {meal.datum.month} nicht in Saison",
                    rezept=rezept.slug,
                    slot=meal.slot,
                )
            )

        # Haltbarkeit der Frischware bezogen auf den tatsächlichen Slot.
        if meal.slot not in config.frischware_slots:
            heikel = empfindliche_zutaten(rezept, meal.variant, zutaten)
            if heikel:
                issues.append(
                    Issue(
                        code="empfindliche_zutat_spaeter_slot",
                        nachricht=(
                            f"{rezept.name} in Slot {meal.slot} braucht "
                            f"{', '.join(sorted(set(heikel)))}"
                        ),
                        rezept=rezept.slug,
                        slot=meal.slot,
                    )
                )

    # Kriterium 3: Wiederholung und Mindestabstand. Gezählt wird, was auf dem
    # Tisch steht – eine Portion aus der Gefriertruhe ist dasselbe Gericht.
    nach_rezept: dict[str, list] = defaultdict(list)
    for meal in plan.alle_meals:
        nach_rezept[meal.recipe_slug].append(meal)
    for slug, meals in nach_rezept.items():
        if len(meals) > config.max_pro_monat:
            issues.append(
                Issue(
                    code="zu_oft_im_monat",
                    nachricht=(
                        f"{slug} steht {len(meals)}× im Monat, erlaubt sind {config.max_pro_monat}×"
                    ),
                    rezept=slug,
                )
            )
        termine = sorted(m.datum for m in meals)
        for frueher, spaeter in zip(termine, termine[1:], strict=False):
            abstand = (spaeter - frueher).days
            if abstand < config.min_abstand_tage:
                issues.append(
                    Issue(
                        code="abstand_zu_kurz",
                        nachricht=(
                            f"{slug} am {frueher} und {spaeter} – nur {abstand} Tage Abstand, "
                            f"mindestens {config.min_abstand_tage} verlangt"
                        ),
                        rezept=slug,
                    )
                )

    # Kein Gericht zweimal in derselben Woche.
    for woche in plan.weeks:
        zaehler: dict[str, int] = defaultdict(int)
        for meal in woche.meals:
            zaehler[meal.recipe_slug] += 1
        for slug, anzahl in zaehler.items():
            if anzahl > 1:
                issues.append(
                    Issue(
                        code="doppelt_in_woche",
                        nachricht=f"{slug} steht {anzahl}× in der Woche ab {woche.start}",
                        rezept=slug,
                    )
                )

    # Kriterium 4: jedes Wochenende mindestens ein süßes oder zünftiges
    # Gericht. Nur eine Warnung, kein Fehler: der Planer baut das Highlight
    # beim Würfeln aktiv ein, aber als Fehler würde es jedes Verschieben
    # verbieten, bei dem das einzige süße Gericht das Wochenende verlässt.
    # Ein Plan von Hand umsortieren zu können wiegt mehr als der Pfannkuchen
    # am Samstag.
    for woche in plan.weeks:
        wochenende = [m for m in woche.meals if m.slot in ("SA", "SO")]
        if not wochenende:
            continue
        hat_highlight = False
        for meal in wochenende:
            rezept = rezepte.get(meal.recipe_slug)
            if rezept and any(rezept.hat_tag(t) for t in config.wochenende_pflicht_tags):
                hat_highlight = True
                break
        if not hat_highlight:
            issues.append(
                Issue(
                    code="wochenende_ohne_highlight",
                    nachricht=(
                        f"Wochenende ab {woche.start} hat kein Gericht mit "
                        f"{' oder '.join(config.wochenende_pflicht_tags)}"
                    ),
                    severity=SEVERITY_WARNUNG,
                )
            )

    # Fischstäbchen-Obergrenze.
    fisch = [
        m for m in plan.alle_meals if (r := rezepte.get(m.recipe_slug)) and _ist_fischstaebchen(r)
    ]
    if len(fisch) > config.fischstaebchen_max_pro_monat:
        issues.append(
            Issue(
                code="fisch_zu_oft",
                nachricht=(
                    f"Fischstäbchen {len(fisch)}× im Monat, erlaubt sind "
                    f"{config.fischstaebchen_max_pro_monat}×"
                ),
            )
        )

    # Core-Anteil: Zielwert, deshalb nur eine Warnung.
    if gekocht:
        core = sum(1 for m in gekocht if (r := rezepte.get(m.recipe_slug)) and r.core)
        anteil = core / len(gekocht)
        if anteil < config.core_anteil - 0.15:
            issues.append(
                Issue(
                    code="core_anteil_niedrig",
                    nachricht=(f"Nur {anteil:.0%} Kernrezepte, Ziel sind {config.core_anteil:.0%}"),
                    severity=SEVERITY_WARNUNG,
                )
            )

    return issues
