"""Den Wocheneinkauf in eine Mealie-Einkaufsliste schreiben.

``essensplaner mealie-einkauf`` überträgt denselben Zettel, den die
Oberfläche unter „Einkauf“ zeigt – Frischware, Vorratszutaten, Grundeinkauf
und Einträge nur für diese Woche – in die Mealie-Liste „Wocheneinkauf“. Was
in der Oberfläche abgewählt ist, fehlt. Abgehakt wird dann in Mealie.

Jede Zeile ist eine Notiz ohne Mealie-Lebensmittel und mit Menge 0: dann
zeigt Mealie genau den Text des Zettels („2 Dosen Kidneybohnen (Bedarf
480 g)“), statt eine eigene Menge davorzusetzen. Die Abteilung wird zum
Label, damit die Liste in Ladenreihenfolge gruppiert. Was ein anderes
Programm braucht (Kurzname, Menge, Gerichte), steht maschinenlesbar in
``extras`` – das ist der Vertrag mit dem Angebotsscanner.

Regeln, damit der Sync beliebig oft laufen darf:

* Eigene Einträge erkennt er an ``extras.essensplaner_key``. Alles andere auf
  der Liste – von Hand oder vom Angebotsscanner – bleibt unangetastet.
* Eigene Einträge einer **anderen** Woche werden gelöscht, abgehakt oder
  nicht: der Einkauf ist vorbei.
* In der laufenden Woche: Fehlendes wird angelegt, Geändertes aktualisiert,
  nicht mehr Gebrauchtes gelöscht. **Abgehakte Einträge bleiben, wie sie
  sind** – das ist schon im Wagen.
* Die Notiz gehört dem Essensplaner nur bis zum Angebotszeichen
  :data:`ANGEBOT_ZEICHEN`. Was der Angebotsscanner dahinter schreibt
  („🏷 Penny 0,79 € …“), überlebt jedes Update.

Mealie legt Einträge mit gleichem Text beim Anlegen zusammen und zählt die
Menge hoch. Deshalb fasst der Sync gleichlautende Zeilen vorher selbst
zusammen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from .mealie import MealieSync, Transport
from .shopping import ShoppingItem, ShoppingList, zettel_posten

log = logging.getLogger(__name__)

#: Name der Mealie-Liste, wenn ``MEALIE_EINKAUFSLISTE`` nichts anderes sagt.
LISTE = "Wocheneinkauf"
#: Ab diesem Zeichen gehört die Notiz dem Angebotsscanner.
ANGEBOT_ZEICHEN = "🏷"
#: Vorsilbe aller Schlüssel, die der Essensplaner in ``extras`` setzt.
X = "essensplaner_"
KEY = X + "key"
WOCHE = X + "woche"

ITEMS = "/api/households/shopping/items"
LISTEN = "/api/households/shopping/lists"


@dataclass(slots=True)
class Eintrag:
    """Eine Zeile, wie sie in Mealie stehen soll."""

    key: str
    note: str
    abteilung: str
    extras: dict[str, str]


@dataclass
class EinkaufBericht:
    liste: str = LISTE
    woche: date | None = None
    angelegt: list[str] = field(default_factory=list)
    aktualisiert: list[str] = field(default_factory=list)
    geloescht: list[str] = field(default_factory=list)
    #: Abgehakt und deshalb nicht angefasst.
    abgehakt: list[str] = field(default_factory=list)
    unveraendert: int = 0


def _zahl(wert: float) -> str:
    return str(int(wert)) if float(wert).is_integer() else f"{wert:g}"


def eintraege(
    woche: date,
    frisch: ShoppingList,
    vorrat: ShoppingList,
    grund_zeilen: list[str],
    abgewaehlt: set[str] | None = None,
    extra: list[dict[str, str]] | None = None,
) -> list[Eintrag]:
    """Der Zettel der Oberfläche als Mealie-Zeilen, ohne Abgewähltes."""
    items: dict[str, ShoppingItem] = {f"frisch:{i.slug}": i for i in frisch.items}
    items.update({f"vorrat:{i.slug}": i for i in vorrat.items})

    ergebnis: list[Eintrag] = []
    nach_text: dict[str, Eintrag] = {}
    for _, posten in zettel_posten(frisch, vorrat, grund_zeilen, abgewaehlt, extra):
        for p in posten:
            if p.abgewaehlt:
                continue
            teile = [p.text]
            if p.gerichte:
                teile.append("für " + ", ".join(sorted(p.gerichte)))
            if p.hinweis and p.quelle != "extra":
                teile.append(p.hinweis)
            note = " · ".join(teile)

            extras = {
                KEY: p.key,
                WOCHE: woche.isoformat(),
                X + "quelle": p.quelle,
                X + "abteilung": p.abteilung,
                X + "text": p.text,
            }
            item = items.get(p.key)
            if item is not None:
                extras |= {
                    X + "slug": item.slug,
                    X + "name": item.name,
                    X + "menge": _zahl(round(item.menge, 2)),
                    X + "einheit": item.einheit,
                    X + "frisch": "ja" if p.quelle == "frisch" else "nein",
                    X + "gerichte": " + ".join(sorted(item.gerichte)),
                }

            doppelt = nach_text.get(note.casefold())
            if doppelt is not None:
                # Mealie würde die beiden ohnehin zusammenlegen.
                log.info("Einkauf: %r steht doppelt auf dem Zettel, einmal genügt", note)
                continue
            eintrag = Eintrag(key=p.key, note=note, abteilung=p.abteilung, extras=extras)
            nach_text[note.casefold()] = eintrag
            ergebnis.append(eintrag)
    return ergebnis


def eigener_teil(note: str | None) -> str:
    """Der Teil der Notiz vor dem Angebotszeichen."""
    return (note or "").split(ANGEBOT_ZEICHEN, 1)[0].rstrip(" ·")


def _mit_angebot(neu: str, alt: str | None) -> str:
    """Die neue Notiz, mit dem Angebotsteil der alten, falls es einen gab."""
    alt = alt or ""
    if ANGEBOT_ZEICHEN not in alt:
        return neu
    return f"{neu} · {ANGEBOT_ZEICHEN}{alt.split(ANGEBOT_ZEICHEN, 1)[1]}"


class EinkaufSync:
    """Schreibt die Einträge einer Woche in eine Mealie-Einkaufsliste."""

    def __init__(self, transport: Transport, liste: str = LISTE, dry_run: bool = False) -> None:
        self.t = transport
        self.liste_name = liste
        self.dry_run = dry_run
        # Für die Labels (= Abteilungen) dieselbe Logik wie beim Rezept-Sync.
        self._mealie = MealieSync(transport, dry_run=dry_run)

    def _liste(self) -> dict | None:
        seite = self.t("GET", f"{LISTEN}?page=1&perPage=-1")
        for liste in seite.get("items", []):
            if liste.get("name", "").casefold() == self.liste_name.casefold():
                return self.t("GET", f"{LISTEN}/{liste['id']}")
        if self.dry_run:
            return None
        log.info("Mealie: Einkaufsliste %r angelegt", self.liste_name)
        neu = self.t("POST", LISTEN, {"name": self.liste_name})
        return self.t("GET", f"{LISTEN}/{neu['id']}")

    def _label_id(self, abteilung: str) -> str | None:
        return self._mealie.label(abteilung).get("id")

    def synchronisiere(self, woche: date, soll: list[Eintrag]) -> EinkaufBericht:
        bericht = EinkaufBericht(liste=self.liste_name, woche=woche)
        self._mealie.lade_labels()
        liste = self._liste()
        vorhanden = (liste or {}).get("listItems") or []
        liste_id = (liste or {}).get("id")

        eigene: dict[str, dict] = {}
        loeschen: list[dict] = []
        for item in vorhanden:
            extras = item.get("extras") or {}
            key = extras.get(KEY)
            if not key:
                continue  # von Hand oder vom Angebotsscanner
            if extras.get(WOCHE) != woche.isoformat() or key in eigene:
                loeschen.append(item)
            else:
                eigene[key] = item

        soll_keys = {e.key for e in soll}
        for key, item in eigene.items():
            if key not in soll_keys:
                if item.get("checked"):
                    bericht.abgehakt.append(key)
                else:
                    loeschen.append(item)

        anlegen: list[dict] = []
        aendern: list[dict] = []
        for e in soll:
            item = eigene.get(e.key)
            label_id = self._label_id(e.abteilung)
            if item is None:
                anlegen.append(
                    {
                        "shoppingListId": liste_id,
                        "note": e.note,
                        "quantity": 0,
                        "labelId": label_id,
                        "extras": e.extras,
                    }
                )
                bericht.angelegt.append(e.key)
                continue
            if item.get("checked"):
                bericht.abgehakt.append(e.key)
                continue
            gleich = (
                eigener_teil(item.get("note")) == e.note
                and item.get("labelId") == label_id
                and {k: v for k, v in (item.get("extras") or {}).items() if k.startswith(X)}
                == e.extras
            )
            if gleich:
                bericht.unveraendert += 1
                continue
            fremde_extras = {
                k: v for k, v in (item.get("extras") or {}).items() if not k.startswith(X)
            }
            aendern.append(
                {
                    **item,
                    "note": _mit_angebot(e.note, item.get("note")),
                    "quantity": 0,
                    "labelId": label_id,
                    "extras": {**fremde_extras, **e.extras},
                }
            )
            bericht.aktualisiert.append(e.key)

        bericht.geloescht = [(i.get("extras") or {}).get(KEY, "?") for i in loeschen]
        if self.dry_run:
            return bericht

        if loeschen:
            ids = "&".join(f"ids={i['id']}" for i in loeschen)
            self.t("DELETE", f"{ITEMS}?{ids}")
        # Einzeln anlegen statt create-bulk: so bleibt jede Zeile für sich,
        # und ein Fehler nennt die Zeile, an der es hängt.
        for daten in anlegen:
            self.t("POST", ITEMS, daten)
        if aendern:
            self.t("PUT", ITEMS, aendern)
        log.info(
            "Mealie-Einkauf %s: %d neu, %d geändert, %d gelöscht, %d abgehakt",
            woche,
            len(bericht.angelegt),
            len(bericht.aktualisiert),
            len(bericht.geloescht),
            len(bericht.abgehakt),
        )
        return bericht
