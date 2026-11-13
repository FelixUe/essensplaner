"""Eingebauter Scheduler für den Dauerbetrieb im Container.

In Coolify kann man beides haben: zwei Scheduled Tasks, die
``essensplaner send-weekly`` bzw. ``essensplaner generate-month`` aufrufen,
oder diesen Prozess als normalen Service laufen lassen. Letzteres ist
portabler – beim Umzug ins Homelab läuft derselbe Container ohne externen
Cron weiter.

Termine (Zeitzone aus der Konfiguration, Default Europe/Berlin):

* ``send-weekly``    jeden Freitag zur konfigurierten Uhrzeit, Default 07:00
* ``generate-month`` am vorletzten Freitag des Monats, 30 Minuten später

Jede Aufgabe wird pro Termin höchstens einmal ausgeführt. Der Zustand liegt
in ``plans/state.json``, damit ein Neustart nicht zu einer zweiten Mail führt.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import kalender
from .config import Config
from .runner import (
    Kontext,
    generate_month,
    lade_state,
    send_weekly,
    speichere_state,
)

log = logging.getLogger("essensplaner.scheduler")

#: Abstand zwischen zwei Prüfungen.
TICK_SEKUNDEN = 30

#: Die Monatsaufgabe läuft so viele Minuten nach der Wochenmail.
MONATS_VERSATZ_MINUTEN = 30


def _zone(config: Config) -> tzinfo:
    """Die konfigurierte Zeitzone, sonst UTC.

    ``zoneinfo`` braucht eine IANA-Datenbank. Die liegt auf Linux meist im
    System, fehlt aber auf Windows und in slim-Images – deshalb ist tzdata
    eine Abhängigkeit des Pakets. Schlägt trotzdem alles fehl, wird mit
    ``timezone.utc`` weitergearbeitet statt den Dienst zu beenden.
    """
    try:
        return ZoneInfo(config.zeitzone)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        log.warning("Zeitzone %r nicht auflösbar (fehlt tzdata?), nutze UTC", config.zeitzone)
        return UTC


def _uhrzeit(config: Config) -> tuple[int, int]:
    roh = config.wochenmail_uhrzeit
    try:
        stunde, minute = roh.split(":")
        return int(stunde), int(minute)
    except (ValueError, AttributeError):
        log.warning("Uhrzeit %r unlesbar, nutze 07:00", roh)
        return 7, 0


class Scheduler:
    """Prüft im Takt, ob eine Aufgabe fällig ist, und führt sie aus."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.zone = _zone(config)
        self.stunde, self.minute = _uhrzeit(config)

    # ------------------------------------------------------------ Fälligkeit

    def _nach_uhrzeit(self, jetzt: datetime, versatz_minuten: int = 0) -> bool:
        termin = jetzt.replace(
            hour=self.stunde, minute=self.minute, second=0, microsecond=0
        ) + timedelta(minutes=versatz_minuten)
        # Ein Fenster von zwei Stunden, damit ein Neustart am Morgen die Mail
        # noch auslöst, ein Neustart am Abend aber nicht.
        return termin <= jetzt < termin + timedelta(hours=2)

    def wochenmail_faellig(self, jetzt: datetime, state: dict[str, str]) -> date | None:
        heute = jetzt.date()
        if heute.weekday() != kalender.FREITAG:
            return None
        if not self._nach_uhrzeit(jetzt):
            return None
        if state.get("send-weekly") == heute.isoformat():
            return None
        return heute

    def monatsplan_faellig(self, jetzt: datetime, state: dict[str, str]) -> str | None:
        heute = jetzt.date()
        if heute.weekday() != kalender.FREITAG:
            return None
        try:
            if heute != kalender.vorletzter_freitag(heute.year, heute.month):
                return None
        except ValueError:
            return None
        if not self._nach_uhrzeit(jetzt, MONATS_VERSATZ_MINUTEN):
            return None
        jahr, nr = kalender.folgemonat(heute.year, heute.month)
        monat = kalender.monat_str(jahr, nr)
        if state.get("generate-month") == monat:
            return None
        return monat

    # ----------------------------------------------------------------- Lauf

    def tick(self, jetzt: datetime | None = None) -> list[str]:
        """Einmal prüfen und fällige Aufgaben ausführen."""
        jetzt = jetzt or datetime.now(self.zone)
        state = lade_state(self.config)
        erledigt: list[str] = []

        monat = self.monatsplan_faellig(jetzt, state)
        if monat:
            try:
                ctx = Kontext.laden(self.config)
                ergebnis = generate_month(
                    ctx,
                    monat=monat,
                    heute=jetzt.date(),
                    mail_senden=self.config.smtp.konfiguriert,
                )
                log.info("%s", ergebnis)
                state["generate-month"] = monat
                speichere_state(self.config, state)
                erledigt.append(str(ergebnis))
            except Exception as exc:  # noqa: BLE001
                # Ein Fehler darf den Scheduler nicht beenden – sonst fällt
                # auch die Wochenmail aus.
                log.exception("generate-month für %s fehlgeschlagen: %s", monat, exc)

        tag = self.wochenmail_faellig(jetzt, state)
        if tag:
            try:
                ctx = Kontext.laden(self.config)
                ergebnis = send_weekly(ctx, tag=tag, mail_senden=self.config.smtp.konfiguriert)
                log.info("%s", ergebnis)
                state["send-weekly"] = tag.isoformat()
                speichere_state(self.config, state)
                erledigt.append(str(ergebnis))
            except Exception as exc:  # noqa: BLE001
                log.exception("send-weekly für %s fehlgeschlagen: %s", tag, exc)

        return erledigt

    def run(self, einmal: bool = False) -> None:
        """Dauerlauf. Beendet sich nur bei SIGTERM/SIGINT oder mit ``einmal``."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        )
        jetzt = datetime.now(self.zone)
        log.info(
            "Scheduler gestartet. Zeitzone %s, Wochenmail Freitag %02d:%02d, "
            "Monatsplan am vorletzten Freitag %02d:%02d. Jetzt ist %s.",
            self.config.zeitzone,
            self.stunde,
            self.minute,
            (self.stunde + (self.minute + MONATS_VERSATZ_MINUTEN) // 60) % 24,
            (self.minute + MONATS_VERSATZ_MINUTEN) % 60,
            jetzt.strftime("%Y-%m-%d %H:%M %Z"),
        )
        if self.config.smtp.konfiguriert:
            log.info("Empfänger: %s", ", ".join(self.config.mail_empfaenger) or "(keine)")
        else:
            # Ohne SMTP wird trotzdem geplant – nur eben nichts verschickt.
            # Die Pläne stehen in der Weboberfläche, das reicht als Betrieb.
            log.info(
                "Kein SMTP konfiguriert: Pläne werden erzeugt, aber keine Mails "
                "verschickt. Der Plan steht in der Weboberfläche."
            )

        while True:
            self.tick()
            if einmal:
                return
            time.sleep(TICK_SEKUNDEN)
