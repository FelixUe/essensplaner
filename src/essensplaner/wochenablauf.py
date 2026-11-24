"""Der Wochenablauf: Vorschlag per ntfy, Zettel fertig, Angebote dazu.

1. Mittwochabend (``ablauf.vorschlag_tag``/``vorschlag_uhrzeit``) kommt ein
   Push mit den Gerichten für die Woche ab Freitag. Ein Tipp öffnet die
   Woche in der Oberfläche, dort lässt sich tauschen.
2. Auf dem Einkaufszettel wählt man ab, was noch da ist, und tippt
   „Zettel fertig“. Der Zettel geht nach Mealie, danach stößt der
   Essensplaner einen Lauf des Angebotsscanners an.
3. Ein zweiter Push meldet den fertigen Zettel samt Angeboten.

Wer nicht auf „fertig“ tippt, bekommt den Zettel am Rückfalltermin
(``mealie.einkauf_tag``, Default Donnerstag 18:00) automatisch.

Alles ist optional: ohne ``NTFY_TOPIC`` kein Push, ohne
``ANGEBOTSSCANNER_URL`` kein Scanner-Lauf. Das Topic ist bei ntfy.sh die
einzige Zugangskontrolle und wird deshalb wie ein Passwort behandelt: es
steht nur in der Umgebung und nie im Log.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from . import kalender
from .models import WeekPlan

log = logging.getLogger(__name__)

NTFY_STANDARD = "https://ntfy.sh"
SCANNER_TIMEOUT_S = 15 * 60

#: (url, daten, kopfzeilen, timeout) -> (status, antwort)
Sender = Callable[[str, dict, dict[str, str], float], tuple[int, dict]]


def _post_json(url: str, daten: dict, kopf: dict[str, str], timeout: float) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(daten).encode(),
        headers={"Content-Type": "application/json", **kopf},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as antwort:  # noqa: S310
            roh = antwort.read()
            status = antwort.status
    except urllib.error.HTTPError as exc:
        roh, status = exc.read(), exc.code
    try:
        inhalt = json.loads(roh) if roh else {}
    except ValueError:
        inhalt = {}
    return status, inhalt if isinstance(inhalt, dict) else {}


# ------------------------------------------------------------------ ntfy


@dataclass(slots=True)
class Push:
    titel: str
    text: str
    klick: str | None = None
    aktionen: list[tuple[str, str]] = field(default_factory=list)  # (Beschriftung, URL)
    tags: list[str] = field(default_factory=list)

    def als_json(self, topic: str) -> dict:
        daten: dict = {"topic": topic, "title": self.titel, "message": self.text}
        if self.klick:
            daten["click"] = self.klick
        if self.aktionen:
            daten["actions"] = [
                {"action": "view", "label": label, "url": url, "clear": True}
                for label, url in self.aktionen
            ]
        if self.tags:
            daten["tags"] = self.tags
        return daten


def ntfy_konfiguriert() -> bool:
    return bool(os.environ.get("NTFY_TOPIC", "").strip())


def sende_push(push: Push, sender: Sender = _post_json) -> bool:
    """Schickt den Push. Fehler landen im Log (ohne Topic), nie als Ausnahme."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    server = os.environ.get("NTFY_SERVER", "").strip().rstrip("/") or NTFY_STANDARD
    kopf = {}
    if token := os.environ.get("NTFY_TOKEN", "").strip():
        kopf["Authorization"] = f"Bearer {token}"
    try:
        status, _ = sender(server, push.als_json(topic), kopf, 15.0)
    except (OSError, ValueError) as exc:
        log.warning("ntfy nicht erreichbar: %s", type(exc).__name__)
        return False
    if status >= 300:
        log.warning("ntfy antwortet mit %s", status)
        return False
    return True


# --------------------------------------------------------------- Adressen


def basis_url() -> str:
    """Adresse der Oberfläche für Links im Push, z. B. http://essen.home.arpa."""
    return os.environ.get("ESSENSPLANER_BASIS_URL", "").strip().rstrip("/")


def wochen_url(freitag: date) -> str | None:
    basis = basis_url()
    if not basis:
        return None
    return f"{basis}/monat/{kalender.monat_der_woche(freitag)}/woche/{freitag.isoformat()}"


def einkauf_url(freitag: date) -> str | None:
    woche = wochen_url(freitag)
    return f"{woche}/einkauf" if woche else None


# --------------------------------------------------------------- Vorschlag


def vorschlag_push(woche: WeekPlan, kochtag: Callable[[str], str]) -> Push:
    """Die Gerichte der Woche als Push: eine Zeile je Mahlzeit."""
    zeilen = []
    for meal in woche.meals:
        tage = f" ({meal.tage} Tage)" if meal.tage > 1 else ""
        zeilen.append(f"{kochtag(meal.slot)[:2]} {meal.recipe_name}{tage}")
    aktionen = []
    if url := wochen_url(woche.start):
        aktionen.append(("Gerichte ändern", url))
    if url := einkauf_url(woche.start):
        aktionen.append(("Einkaufszettel", url))
    return Push(
        titel=f"Essen ab Freitag, {woche.start:%d.%m.}",
        text="\n".join(zeilen) + "\n\nPasst das? Danach den Einkaufszettel prüfen.",
        klick=wochen_url(woche.start),
        aktionen=aktionen,
        tags=["fork_and_knife"],
    )


# ------------------------------------------------------------- Scanner


def scanner_konfiguriert() -> bool:
    return bool(os.environ.get("ANGEBOTSSCANNER_URL", "").strip())


def scanner_lauf(sender: Sender = _post_json) -> dict | None:
    """Einen Lauf des Angebotsscanners anstoßen und auf das Ergebnis warten.

    Gibt die Zusammenfassung des Scanners zurück, ``None`` wenn er nicht
    konfiguriert oder nicht erreichbar ist.
    """
    url = os.environ.get("ANGEBOTSSCANNER_URL", "").strip().rstrip("/")
    if not url:
        return None
    kopf = {}
    if token := os.environ.get("ANGEBOTSSCANNER_AUSLOESER_TOKEN", "").strip():
        kopf["Authorization"] = f"Bearer {token}"
    try:
        status, antwort = sender(f"{url}/lauf", {}, kopf, SCANNER_TIMEOUT_S)
    except (OSError, ValueError) as exc:
        log.warning("Angebotsscanner nicht erreichbar: %s", type(exc).__name__)
        return None
    if status >= 300:
        log.warning("Angebotsscanner antwortet mit %s", status)
    zusammenfassung = antwort.get("zusammenfassung")
    return zusammenfassung if isinstance(zusammenfassung, dict) else None


# ------------------------------------------------------------ Zettel fertig


def _angebot_zeile(angebot: dict) -> str:
    teile = [str(angebot["artikel"])]
    if preis := angebot.get("preis"):
        teile.append(f"{float(preis):.2f} €".replace(".", ","))
    if markt := angebot.get("markt"):
        teile.append(str(markt))
    return " · ".join(teile)


def posten_aus_bericht(bericht) -> int:
    """Wie viele Zeilen des Essensplaners jetzt in Mealie offen stehen."""
    return len(bericht.angelegt) + len(bericht.aktualisiert) + bericht.unveraendert


def fertig_push(
    freitag: date, posten: int, scanner: dict | None, automatisch: bool = False
) -> Push:
    """Meldung nach dem Übertragen: wie viele Posten, was der Scanner fand."""
    zeilen = [f"{posten} Posten in der Mealie-Liste."]
    if scanner and scanner.get("status") == "ok":
        anzahl = int(scanner.get("anzahl") or 0)
        ersparnis = float(scanner.get("ersparnis_euro") or 0)
        if anzahl:
            text = f"{anzahl} Angebote dazu"
            if ersparnis:
                text += f", etwa {ersparnis:.2f} € gespart".replace(".", ",")
            zeilen.append(text + ".")
            for angebot in (scanner.get("angebote") or [])[:6]:
                if isinstance(angebot, dict) and angebot.get("artikel"):
                    zeilen.append(f"🏷 {_angebot_zeile(angebot)}")
        else:
            zeilen.append("Keine passenden Angebote diese Woche.")
    elif scanner is not None:
        zeilen.append("Der Angebotsscanner hatte ein Problem, Details in Home Assistant.")
    elif scanner_konfiguriert():
        zeilen.append("Der Angebotsscanner war nicht erreichbar.")
    if automatisch:
        zeilen.append("Automatisch übertragen, weil niemand auf „fertig“ getippt hat.")
    aktionen = [("Zettel ansehen", url)] if (url := einkauf_url(freitag)) else []
    return Push(
        titel=f"Einkaufszettel für Freitag, {freitag:%d.%m.} fertig",
        text="\n".join(zeilen),
        klick=einkauf_url(freitag),
        aktionen=aktionen,
        tags=["shopping_cart"],
    )


def nach_dem_uebertragen(
    freitag: date,
    posten: int,
    automatisch: bool = False,
    sender: Sender = _post_json,
) -> dict | None:
    """Scanner anstoßen und den fertigen Zettel melden. Wirft nie."""
    try:
        scanner = scanner_lauf(sender)
    except Exception:  # noqa: BLE001
        log.exception("Scanner-Lauf fehlgeschlagen")
        scanner = None
    sende_push(fertig_push(freitag, posten, scanner, automatisch), sender)
    return scanner
