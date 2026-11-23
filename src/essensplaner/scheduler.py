"""Eingebauter Scheduler für den Dauerbetrieb im Container.

In Coolify kann man beides haben: zwei Scheduled Tasks, die
``essensplaner send-weekly`` bzw. ``essensplaner generate-month`` aufrufen,
oder diesen Prozess als normalen Service laufen lassen. Letzteres ist
portabler – beim Umzug ins Homelab läuft derselbe Container ohne externen
Cron weiter.

Termine (Zeitzone aus der Konfiguration, Default Europe/Berlin):

* ``send-weekly``    jeden Freitag zur konfigurierten Uhrzeit, Default 07:00
* ``generate-month`` am vorletzten Freitag des Monats, 30 Minuten später
* ``mealie-einkauf`` am konfigurierten Tag (Default Sonntag 18:00), nur mit
  ``MEALIE_URL``/``MEALIE_TOKEN``: der Zettel für den nächsten Freitag

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
    einkauf_nach_mealie,
    generate_month,
    lade_state,
    send_reminder,
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


def _parse_uhrzeit(roh: str, fallback: tuple[int, int], was: str) -> tuple[int, int]:
    try:
        stunde, minute = roh.split(":")
        return int(stunde), int(minute)
    except (ValueError, AttributeError):
        log.warning("%s %r unlesbar, nutze %02d:%02d", was, roh, *fallback)
        return fallback


def _uhrzeit(config: Config) -> tuple[int, int]:
    return _parse_uhrzeit(config.wochenmail_uhrzeit, (7, 0), "Uhrzeit")


class Scheduler:
    """Prüft im Takt, ob eine Aufgabe fällig ist, und führt sie aus."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.zone = _zone(config)
        self.stunde, self.minute = _uhrzeit(config)
        # None heißt: keine Erinnerung. Ein leerer oder unbekannter Tag in der
        # Konfiguration schaltet sie ab, statt den Scheduler scheitern zu
        # lassen – die Wochenmail ist wichtiger als die Erinnerung.
        self.erinnerung_tag = kalender.wochentag_nummer(self.config.erinnerung_tag)
        if self.erinnerung_tag is None and self.config.erinnerung_tag:
            log.warning(
                "erinnerung_tag %r ist kein Wochentag – die Erinnerung bleibt aus.",
                self.config.erinnerung_tag,
            )
        self.erinnerung_stunde, self.erinnerung_minute = _parse_uhrzeit(
            self.config.erinnerung_uhrzeit, (18, 0), "Erinnerungszeit"
        )
        self.einkauf_tag = kalender.wochentag_nummer(self.config.mealie_einkauf_tag)
        if self.einkauf_tag is None and self.config.mealie_einkauf_tag:
            log.warning(
                "mealie.einkauf_tag %r ist kein Wochentag – der Mealie-Einkauf bleibt aus.",
                self.config.mealie_einkauf_tag,
            )
        self.einkauf_stunde, self.einkauf_minute = _parse_uhrzeit(
            self.config.mealie_einkauf_uhrzeit, (18, 0), "Mealie-Einkaufszeit"
        )

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

    def erinnerung_faellig(self, jetzt: datetime, state: dict[str, str]) -> date | None:
        """Der Freitag, dessen Einkauf die Erinnerung ankündigt – oder ``None``.

        Die Erinnerung läuft am Abend davor und meint deshalb die Woche, die
        erst beginnt. Sie hat eine eigene Uhrzeit, weil sie abends kommt und
        nicht zum Frühstück: deshalb ein eigenes Zeitfenster statt des
        Versatzes, mit dem die Monatsmail an der Wochenmail hängt.
        """
        if self.erinnerung_tag is None:
            return None
        heute = jetzt.date()
        if heute.weekday() != self.erinnerung_tag:
            return None
        termin = jetzt.replace(
            hour=self.erinnerung_stunde, minute=self.erinnerung_minute, second=0, microsecond=0
        )
        if not termin <= jetzt < termin + timedelta(hours=2):
            return None
        freitag = kalender.naechster_freitag(heute, inklusiv=False)
        if state.get("erinnerung") == freitag.isoformat():
            return None
        return freitag

    def mealie_einkauf_faellig(self, jetzt: datetime, state: dict[str, str]) -> date | None:
        """Der Freitag, dessen Zettel jetzt nach Mealie gehört – oder ``None``."""
        if self.einkauf_tag is None or not self.config.mealie_konfiguriert:
            return None
        heute = jetzt.date()
        if heute.weekday() != self.einkauf_tag:
            return None
        termin = jetzt.replace(
            hour=self.einkauf_stunde, minute=self.einkauf_minute, second=0, microsecond=0
        )
        if not termin <= jetzt < termin + timedelta(hours=2):
            return None
        freitag = kalender.naechster_freitag(heute)
        if state.get("mealie-einkauf") == freitag.isoformat():
            return None
        return freitag

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

        freitag = self.erinnerung_faellig(jetzt, state)
        if freitag:
            try:
                ctx = Kontext.laden(self.config)
                ergebnis = send_reminder(
                    ctx, freitag=freitag, mail_senden=self.config.smtp.konfiguriert
                )
                log.info("%s", ergebnis)
                state["erinnerung"] = freitag.isoformat()
                speichere_state(self.config, state)
                erledigt.append(str(ergebnis))
            except Exception as exc:  # noqa: BLE001
                # Wie oben: ein Fehler hier darf den Scheduler nicht beenden.
                # Die Erinnerung ist Beiwerk, die Freitagsmail ist es nicht.
                log.exception("Erinnerung für %s fehlgeschlagen: %s", freitag, exc)

        freitag = self.mealie_einkauf_faellig(jetzt, state)
        if freitag:
            try:
                ctx = Kontext.laden(self.config)
                bericht = einkauf_nach_mealie(ctx, freitag=freitag)
                text = (
                    f"mealie-einkauf {freitag}: {len(bericht.angelegt)} neu, "
                    f"{len(bericht.aktualisiert)} geändert, {len(bericht.geloescht)} gelöscht"
                )
                log.info("%s", text)
                state["mealie-einkauf"] = freitag.isoformat()
                speichere_state(self.config, state)
                erledigt.append(text)
            except Exception as exc:  # noqa: BLE001
                # Mealie ist Beiwerk: ein Ausfall dort darf die Mails nicht stoppen.
                log.exception("mealie-einkauf für %s fehlgeschlagen: %s", freitag, exc)

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
        if self.erinnerung_tag is None:
            log.info("Erinnerung aus (erinnerung_tag ist nicht gesetzt).")
        else:
            log.info(
                "Erinnerung %s %02d:%02d für den Einkauf am Folgetag.",
                kalender.WOCHENTAGE[self.erinnerung_tag],
                self.erinnerung_stunde,
                self.erinnerung_minute,
            )
        if self.config.mealie_konfiguriert and self.einkauf_tag is not None:
            log.info(
                "Mealie-Einkauf %s %02d:%02d in die Liste %r.",
                kalender.WOCHENTAGE[self.einkauf_tag],
                self.einkauf_stunde,
                self.einkauf_minute,
                self.config.mealie_einkaufsliste,
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
