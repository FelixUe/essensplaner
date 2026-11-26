"""Den Monatsplan in Mealies Speiseplan schreiben.

``essensplaner mealie-plan`` trägt jede geplante Mahlzeit als Eintrag in
Mealies Speiseplan ein. Der Essensplaner bleibt die Quelle: was dort
getauscht oder verschoben wird, steht beim nächsten Lauf auch in Mealie.

Gekocht wird abends, zwei Slots decken den Folgetag mittags ab (``tage``
in der Konfiguration). Daraus werden zwei Einträge: ``dinner`` am Kochtag,
``lunch`` am Tag danach. So steht im Speiseplan, was gegessen wird, und
nicht nur, was am Herd steht.

Regeln, damit der Lauf beliebig oft laufen darf:

* Eigene Einträge erkennt er an :data:`MARKE` im Textfeld. Nur sie werden
  geändert oder gelöscht.
* Steht an einem Platz schon genau dieses Rezept – auch von Hand
  eingetragen –, bleibt der Eintrag, wie er ist. Doppelt kommt nichts.
* Von Hand eingetragene andere Gerichte bleiben stehen. Mealie erlaubt
  mehrere Einträge je Tag; der Essensplaner legt seinen daneben.

Rezepte, die es in Mealie noch nicht gibt, meldet der Bericht. Sie kommen
mit ``essensplaner mealie-sync`` hinein – erst danach kann der Speiseplan
auf sie zeigen.

Nur Standardbibliothek, wie der Rest der Mealie-Anbindung.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .mealie import EXTRA_SLUG, SYNC_TAG, Transport
from .models import MonthPlan

log = logging.getLogger(__name__)

PLAENE = "/api/households/mealplans"
REZEPTE = "/api/recipes"

#: Steht im Textfeld jedes Eintrags, der dem Essensplaner gehört.
MARKE = "Essensplaner"

#: Mealies Mahlzeiten. Gekocht wird abends, aufgewärmt wird mittags.
ABEND = "dinner"
MITTAG = "lunch"


@dataclass(frozen=True, slots=True)
class Eintrag:
    """Ein Platz im Speiseplan: Tag, Mahlzeit, Gericht."""

    datum: date
    art: str
    slug: str
    name: str

    @property
    def platz(self) -> tuple[str, str]:
        return (self.datum.isoformat(), self.art)

    def __str__(self) -> str:
        mahlzeit = "mittags" if self.art == MITTAG else "abends"
        return f"{self.datum} {mahlzeit}: {self.name}"


@dataclass
class SpeiseplanBericht:
    von: date | None = None
    bis: date | None = None
    angelegt: list[str] = field(default_factory=list)
    aktualisiert: list[str] = field(default_factory=list)
    geloescht: list[str] = field(default_factory=list)
    unveraendert: int = 0
    #: Im Plan, aber in Mealie nicht vorhanden – erst mealie-sync laufen lassen.
    fehlende_rezepte: list[str] = field(default_factory=list)


def eintraege(plan: MonthPlan) -> list[Eintrag]:
    """Der Monatsplan als Speiseplan-Einträge, nach Tag sortiert."""
    ergebnis: list[Eintrag] = []
    for meal in plan.alle_meals:
        ergebnis.append(Eintrag(meal.datum, ABEND, meal.recipe_slug, meal.recipe_name))
        for weiter in range(1, meal.tage):
            ergebnis.append(
                Eintrag(
                    meal.datum + timedelta(days=weiter),
                    MITTAG,
                    meal.recipe_slug,
                    meal.recipe_name,
                )
            )
    # Mittags vor abends: so steht der Tag auch im Bericht in der Reihenfolge,
    # in der er gegessen wird.
    return sorted(ergebnis, key=lambda e: (e.datum, e.art != MITTAG))


class SpeiseplanSync:
    """Schreibt die Einträge eines Zeitraums in Mealies Speiseplan."""

    def __init__(self, transport: Transport, dry_run: bool = False) -> None:
        self.t = transport
        self.dry_run = dry_run

    def rezept_ids(self) -> dict[str, dict]:
        """Essensplaner-Slug → Mealie-Rezept (``id`` und ``name``).

        Dieselbe Zuordnung wie beim Rezept-Sync: Sync-Tag an der Übersicht,
        Kurzname in ``extras``. Der Speiseplan braucht aber die Id und nicht
        den Mealie-Slug – deshalb der eigene Durchgang.
        """
        ergebnis: dict[str, dict] = {}
        seite = self.t("GET", f"{REZEPTE}?page=1&perPage=-1")
        for zusammenfassung in seite.get("items", []):
            tags = {t["name"].lower() for t in zusammenfassung.get("tags") or []}
            if SYNC_TAG.lower() not in tags:
                continue
            voll = self.t("GET", f"{REZEPTE}/{zusammenfassung['slug']}")
            slug = (voll.get("extras") or {}).get(EXTRA_SLUG)
            if slug:
                ergebnis[slug] = {"id": voll["id"], "name": voll.get("name", slug)}
        return ergebnis

    def vorhandene(self, von: date, bis: date) -> list[dict]:
        seite = self.t(
            "GET",
            f"{PLAENE}?page=1&perPage=-1&start_date={von.isoformat()}&end_date={bis.isoformat()}",
        )
        return list(seite.get("items", []))

    def synchronisiere(self, von: date, bis: date, soll: list[Eintrag]) -> SpeiseplanBericht:
        bericht = SpeiseplanBericht(von=von, bis=bis)
        rezepte = self.rezept_ids()
        vorhanden = self.vorhandene(von, bis)

        # Was schon genau so dasteht, egal von wem: nicht anfassen.
        schon_da = {(e["date"], e["entryType"], e.get("recipeId")) for e in vorhanden}
        # Eigene Einträge, nach Platz – nur sie dürfen geändert und gelöscht werden.
        eigene: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for e in vorhanden:
            if (e.get("text") or "") == MARKE:
                eigene[(e["date"], e["entryType"])].append(e)

        behalten: set[object] = set()
        anlegen: list[dict] = []
        aendern: list[dict] = []

        for eintrag in soll:
            rezept = rezepte.get(eintrag.slug)
            if rezept is None:
                if eintrag.slug not in bericht.fehlende_rezepte:
                    bericht.fehlende_rezepte.append(eintrag.slug)
                continue
            datum, art = eintrag.platz
            if (datum, art, rezept["id"]) in schon_da:
                bericht.unveraendert += 1
                for e in eigene[eintrag.platz]:
                    if e.get("recipeId") == rezept["id"]:
                        behalten.add(e["id"])
                        break
                continue
            frei = [e for e in eigene[eintrag.platz] if e["id"] not in behalten]
            if frei:
                alt = frei[0]
                behalten.add(alt["id"])
                aendern.append({**alt, "recipeId": rezept["id"], "title": "", "text": MARKE})
                bericht.aktualisiert.append(str(eintrag))
            else:
                anlegen.append(
                    {
                        "date": datum,
                        "entryType": art,
                        "title": "",
                        "text": MARKE,
                        "recipeId": rezept["id"],
                    }
                )
                bericht.angelegt.append(str(eintrag))

        loeschen = [e for liste in eigene.values() for e in liste if e["id"] not in behalten]
        bericht.geloescht = [
            f"{e['date']} {e['entryType']}: {(e.get('recipe') or {}).get('name', '?')}"
            for e in loeschen
        ]
        if self.dry_run:
            return bericht

        for daten in anlegen:
            self.t("POST", PLAENE, daten)
        for daten in aendern:
            self.t("PUT", f"{PLAENE}/{daten['id']}", daten)
        for e in loeschen:
            self.t("DELETE", f"{PLAENE}/{e['id']}")

        log.info(
            "Mealie-Speiseplan %s bis %s: %d neu, %d geändert, %d gelöscht, %d unverändert",
            von,
            bis,
            len(bericht.angelegt),
            len(bericht.aktualisiert),
            len(bericht.geloescht),
            bericht.unveraendert,
        )
        if bericht.fehlende_rezepte:
            log.warning(
                "Speiseplan: %d Rezepte fehlen in Mealie (erst mealie-sync): %s",
                len(bericht.fehlende_rezepte),
                ", ".join(bericht.fehlende_rezepte[:5]),
            )
        return bericht
