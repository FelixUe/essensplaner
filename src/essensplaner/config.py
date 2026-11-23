"""Konfiguration aus config.yaml plus Secrets aus der Umgebung."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Projektwurzel, wenn das Paket aus dem Repo heraus läuft.
REPO_ROOT = Path(__file__).resolve().parents[2]


#: Mehr Esstage als das deckt kein Slot ab. Montag und Mittwoch reichen
#: bis in den Folgetag, weiter nicht – länger soll niemand dasselbe essen.
MAX_TAGE_JE_SLOT = 2


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "ja"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class Exclusion:
    """Eine harte Ausschlussgruppe (z. B. Blauschimmel, Salbei, Fenchel)."""

    gruppe: str
    begriffe: tuple[str, ...]
    ausnahmen: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SmtpConfig:
    """Mailversand. Alle Werte kommen aus der Umgebung, nie aus dem Repo."""

    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    starttls: bool = True
    ssl: bool = False

    @property
    def konfiguriert(self) -> bool:
        """Ob ein brauchbarer Mailserver eingetragen ist.

        Geprueft wird nicht nur, ob etwas dasteht, sondern ob es ein
        Hostname sein kann. Grund: manche Oberflaechen setzen bei einer
        nicht ausgefuellten Pflichtvariablen deren Hinweistext als Wert ein
        ("SMTP_HOST muss gesetzt sein"). Mit der blossen Pruefung auf
        "nicht leer" haelt der Planer das fuer einen Mailserver und
        scheitert dann jeden Freitag an der Namensaufloesung, statt den
        Versand einfach zu lassen.
        """
        host = self.host.strip()
        return bool(host) and not any(z.isspace() for z in host)

    @classmethod
    def from_env(cls) -> SmtpConfig:
        return cls(
            host=os.environ.get("SMTP_HOST", ""),
            port=_env_int("SMTP_PORT", 587),
            user=os.environ.get("SMTP_USER", ""),
            password=os.environ.get("SMTP_PASSWORD", ""),
            starttls=_env_bool("SMTP_STARTTLS", True),
            ssl=_env_bool("SMTP_SSL", False),
        )


@dataclass(slots=True)
class Config:
    """Gesamte Konfiguration des Planers."""

    raw: dict[str, Any] = field(default_factory=dict)
    repo_root: Path = REPO_ROOT
    smtp: SmtpConfig = field(default_factory=SmtpConfig.from_env)

    # ---------------------------------------------------------------- Pfade
    @property
    def data_dir(self) -> Path:
        return Path(os.environ.get("ESSENSPLANER_DATA_DIR", self.repo_root / "data"))

    @property
    def recipes_dir(self) -> Path:
        return self.data_dir / "recipes"

    @property
    def pending_dir(self) -> Path:
        return self.recipes_dir / "_pending"

    @property
    def ingredients_file(self) -> Path:
        return self.data_dir / "ingredients.yaml"

    @property
    def plans_dir(self) -> Path:
        return Path(os.environ.get("ESSENSPLANER_PLANS_DIR", self.repo_root / "plans"))

    # ------------------------------------------------------------- Haushalt
    @property
    def erwachsene(self) -> int:
        return int(self.raw.get("haushalt", {}).get("erwachsene", 2))

    @property
    def kinder(self) -> int:
        return int(self.raw.get("haushalt", {}).get("kinder", 1))

    @property
    def kind_portionsfaktor(self) -> float:
        return float(self.raw.get("haushalt", {}).get("kind_portionsfaktor", 0.5))

    @property
    def familien_portionen(self) -> float:
        """Erwachsenenportionen für einen Familien-Slot (Default 2 + 1*0,5 = 2,5)."""
        return self.erwachsene + self.kinder * self.kind_portionsfaktor

    # ---------------------------------------------------------------- Slots
    @property
    def slots(self) -> dict[str, dict[str, Any]]:
        return self.raw.get("slots", {})

    @property
    def slot_namen(self) -> tuple[str, ...]:
        return tuple(self.slots.keys())

    def slot_portionen(self, slot: str) -> float:
        """Zu kochende Erwachsenenportionen für einen Slot."""
        cfg = self.slots.get(slot, {})
        if "portionen" in cfg:
            return float(cfg["portionen"])
        if cfg.get("esser") == "familie":
            return self.familien_portionen
        return float(self.erwachsene)

    def slot_deckt_ab(self, slot: str) -> str:
        return str(self.slots.get(slot, {}).get("deckt_ab", ""))

    def slot_tage(self, slot: str) -> int:
        """Esstage, die ein Gericht in diesem Slot abdeckt.

        Der Wert steht als Zahl in config.yaml, statt aus "deckt_ab"
        gelesen werden zu müssen: "Montag + Dienstag Mittag" sind zwei
        Tage, "Freitag Abend" ist einer. Fehlt die Angabe, gilt ein Tag –
        der Normalfall und nie zu viel.
        """
        return int(self.slots.get(slot, {}).get("tage", 1))

    def slot_kochtag(self, slot: str) -> str:
        return str(self.slots.get(slot, {}).get("kochtag", slot))

    # ----------------------------------------------------------- Ernährung
    @property
    def protein_min_g(self) -> float:
        return float(self.raw.get("ernaehrung", {}).get("protein_min_g", 30))

    @property
    def protein_slots(self) -> tuple[str, ...]:
        return tuple(self.raw.get("ernaehrung", {}).get("protein_slots", ["MO", "MI"]))

    @property
    def frischware_slots(self) -> tuple[str, ...]:
        """Slots, in denen empfindliche Frischware noch zulässig ist."""
        return tuple(
            self.raw.get("ernaehrung", {}).get("frischware_slots", ["FR", "SA", "SO", "MO"])
        )

    @property
    def fischstaebchen_max_pro_monat(self) -> int:
        return int(self.raw.get("ernaehrung", {}).get("fischstaebchen_max_pro_monat", 1))

    # ------------------------------------------------------------- Planung
    @property
    def max_pro_monat(self) -> int:
        return int(self.raw.get("planung", {}).get("max_pro_monat", 3))

    @property
    def min_abstand_tage(self) -> int:
        return int(self.raw.get("planung", {}).get("min_abstand_tage", 7))

    @property
    def core_anteil(self) -> float:
        return float(self.raw.get("planung", {}).get("core_anteil", 0.6))

    @property
    def freezer(self) -> bool:
        return _env_bool(
            "ESSENSPLANER_FREEZER", bool(self.raw.get("planung", {}).get("freezer", False))
        )

    @property
    def wochenende_pflicht_tags(self) -> tuple[str, ...]:
        return tuple(
            self.raw.get("planung", {}).get("wochenende_pflicht_tags", ["suess", "zuenftig"])
        )

    @property
    def seed(self) -> int | None:
        wert = self.raw.get("planung", {}).get("seed")
        env = os.environ.get("ESSENSPLANER_SEED")
        if env:
            try:
                return int(env)
            except ValueError:
                return wert
        return int(wert) if wert is not None else None

    # --------------------------------------------------------- Ausschlüsse
    @property
    def ausschluesse(self) -> tuple[Exclusion, ...]:
        roh = self.raw.get("ausschluesse", {})
        return tuple(
            Exclusion(
                gruppe=name,
                begriffe=tuple(str(b).lower() for b in daten.get("begriffe", [])),
                ausnahmen=tuple(str(a).lower() for a in daten.get("ausnahmen", [])),
            )
            for name, daten in roh.items()
        )

    @property
    def gewuerzmischungen_pruefen(self) -> bool:
        return bool(self.raw.get("gewuerzmischungen_pruefen", True))

    # ---------------------------------------------------------------- Mail
    @property
    def mail_von(self) -> str:
        return os.environ.get("MAIL_FROM") or str(self.raw.get("mail", {}).get("von", ""))

    @property
    def mail_empfaenger(self) -> tuple[str, ...]:
        env = os.environ.get("MAIL_TO", "")
        if env.strip():
            return tuple(a.strip() for a in env.split(",") if a.strip())
        return tuple(self.raw.get("mail", {}).get("empfaenger", []))

    @property
    def zeitzone(self) -> str:
        return os.environ.get("TZ") or str(
            self.raw.get("mail", {}).get("zeitzone", "Europe/Berlin")
        )

    @property
    def wochenmail_uhrzeit(self) -> str:
        return str(self.raw.get("mail", {}).get("wochenmail_uhrzeit", "07:00"))

    @property
    def betreff_woche(self) -> str:
        return str(
            self.raw.get("mail", {}).get("betreff_woche", "Essensplan & Einkaufsliste {von}–{bis}")
        )

    @property
    def betreff_monat(self) -> str:
        return str(
            self.raw.get("mail", {}).get("betreff_monat", "Monatsplan {monat} + Vorratsliste")
        )

    @property
    def erinnerung_tag(self) -> str:
        """Wochentag der Erinnerung, leer heißt: keine Erinnerung.

        Als Name und nicht als Zahl, weil in einer Konfiguration "Donnerstag"
        steht und nicht "3" – und weil man beim Lesen sofort sieht, ob der
        Tag zum Einkaufsrhythmus passt.
        """
        return str(self.raw.get("mail", {}).get("erinnerung_tag", "Donnerstag")).strip()

    @property
    def erinnerung_uhrzeit(self) -> str:
        return str(self.raw.get("mail", {}).get("erinnerung_uhrzeit", "18:00"))

    @property
    def betreff_erinnerung(self) -> str:
        return str(
            self.raw.get("mail", {}).get("betreff_erinnerung", "Morgen einkaufen: {von}–{bis}")
        )

    # -------------------------------------------------------------- Mealie
    @property
    def mealie_einkauf_tag(self) -> str:
        """Wochentag, an dem der Zettel nach Mealie geht; leer schaltet es ab.

        Sonntag, damit der Angebotsscanner am Abend danach die Angebote ab
        Montag dazuschreiben kann – lange vor dem Einkauf am Freitag.
        """
        return str(self.raw.get("mealie", {}).get("einkauf_tag", "Sonntag")).strip()

    @property
    def mealie_einkauf_uhrzeit(self) -> str:
        return str(self.raw.get("mealie", {}).get("einkauf_uhrzeit", "18:00"))

    @property
    def mealie_einkaufsliste(self) -> str:
        return os.environ.get("MEALIE_EINKAUFSLISTE") or str(
            self.raw.get("mealie", {}).get("einkaufsliste", "Wocheneinkauf")
        )

    @property
    def mealie_url(self) -> str:
        return os.environ.get("MEALIE_URL", "").strip()

    @property
    def mealie_token(self) -> str:
        return os.environ.get("MEALIE_TOKEN", "").strip()

    @property
    def mealie_konfiguriert(self) -> bool:
        return bool(self.mealie_url and self.mealie_token)

    # ------------------------------------------------------------------ AI
    @property
    def ai_aktiv(self) -> bool:
        return _env_bool("ESSENSPLANER_AI", bool(self.raw.get("ai", {}).get("aktiv", False)))

    @property
    def ai_modell(self) -> str:
        return os.environ.get("ANTHROPIC_MODEL") or str(
            self.raw.get("ai", {}).get("modell", "claude-opus-5")
        )

    @property
    def ai_vorschlaege_pro_monat(self) -> int:
        return int(self.raw.get("ai", {}).get("vorschlaege_pro_monat", 3))

    @property
    def anthropic_api_key(self) -> str:
        return os.environ.get("ANTHROPIC_API_KEY", "")


def default_config_path() -> Path:
    """Pfad der Konfigurationsdatei, überschreibbar per ESSENSPLANER_CONFIG."""
    env = os.environ.get("ESSENSPLANER_CONFIG")
    if env:
        return Path(env)
    return REPO_ROOT / "config.yaml"


def load_config(pfad: Path | str | None = None) -> Config:
    """config.yaml laden. Fehlende Datei ist ein Fehler, sie gehört ins Repo."""
    p = Path(pfad) if pfad else default_config_path()
    if not p.is_file():
        raise FileNotFoundError(f"Konfigurationsdatei nicht gefunden: {p}")
    with p.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Config(raw=raw, repo_root=p.resolve().parent, smtp=SmtpConfig.from_env())
