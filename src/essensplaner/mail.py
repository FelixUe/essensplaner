"""Mails rendern und verschicken.

Zwei Mails, beide als HTML mit Plain-Text-Fallback:

* Wochenmail, jeden Freitag: die Gerichte von Freitag bis Donnerstag mit
  Kochtag, dazu die Frischeliste nach Abteilung sortiert und die Rezepte
  als Kurzanleitung.
* Monatsmail, am letzten Freitag vor Monatsbeginn: Übersicht aller Gerichte
  des Monats plus die Vorratsliste.

Die Zugangsdaten kommen ausschließlich aus der Umgebung (siehe SmtpConfig).
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from jinja2 import Environment, PackageLoader, StrictUndefined

from . import kalender, shopping
from .config import Config
from .models import Ingredient, MonthPlan, Recipe, WeekPlan


class MailFehler(Exception):
    """Die Mail konnte nicht gerendert oder nicht verschickt werden."""


@dataclass(slots=True)
class RenderedMail:
    """Eine fertige Mail, noch nicht verschickt."""

    betreff: str
    html: str
    text: str
    empfaenger: tuple[str, ...]

    def to_message(self, von: str) -> EmailMessage:
        if not von:
            raise MailFehler("Kein Absender konfiguriert (mail.von bzw. MAIL_FROM)")
        if not self.empfaenger:
            raise MailFehler("Keine Empfänger konfiguriert (mail.empfaenger bzw. MAIL_TO)")
        msg = EmailMessage()
        msg["Subject"] = self.betreff
        msg["From"] = von
        msg["To"] = ", ".join(self.empfaenger)
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=von.split("@")[-1] or None)
        msg.set_content(self.text)
        msg.add_alternative(self.html, subtype="html")
        return msg


# ------------------------------------------------------------ Anzeigemodelle


@dataclass(slots=True)
class MealView:
    """Eine Mahlzeit, aufbereitet für die Mail."""

    slot: str
    kochtag: str
    datum_lang: str
    datum_kurz: str
    name: str
    #: Welche Mahlzeiten dieses Gericht abdeckt, mit der Zahl der Tage,
    #: sobald es mehr als einer ist.
    deckt_ab: str
    tage: int
    portionen_text: str
    prep_minutes: int
    protein_text: str
    variante_hinweis: str
    tags: tuple[str, ...]
    zutaten: tuple[str, ...]
    steps: tuple[str, ...]
    batch_hinweis: str


def _deckt_ab_text(deckt_ab: str, tage: int) -> str:
    """Die Abdeckung mit Tageszahl, wenn sie über einen Tag hinausgeht.

    "Montag + Dienstag Mittag" liest sich sonst leicht als ein Tag mit
    zwei Mahlzeiten. Bei Ein-Tages-Slots bleibt der Zusatz weg, dort
    wäre er nur Rauschen.
    """
    if tage > 1:
        return f"{deckt_ab} ({tage} Tage)"
    return deckt_ab


def _portionen_text(portionen: float, config: Config) -> str:
    if abs(portionen - config.familien_portionen) < 0.01:
        return f"Familie ({config.erwachsene} Erw. + {config.kinder} Kinder)"
    ganz = int(portionen) if abs(portionen - int(portionen)) < 0.01 else portionen
    return f"{ganz} Erwachsenenportionen"


def _meal_views(
    woche: WeekPlan,
    rezepte: dict[str, Recipe],
    zutaten: dict[str, Ingredient],
    config: Config,
    mit_rezept: bool,
) -> list[MealView]:
    views: list[MealView] = []
    for meal in sorted(woche.meals, key=lambda m: m.datum):
        rezept = rezepte.get(meal.recipe_slug)
        if rezept is None:
            continue
        variante = meal.variant
        hinweis = ""
        if variante and variante in rezept.variants:
            hinweis = rezept.variants[variante].hinweis
        faktor = meal.portionen / rezept.base_servings
        if meal.batch_cook:
            faktor *= 2

        zeilen: tuple[str, ...] = ()
        if mit_rezept:
            zeilen = tuple(
                _zutat_zeile(z.item, z.qty * faktor, z.unit, z.optional, z.hinweis, zutaten)
                for z in rezept.zutaten_fuer(variante)
            )

        batch = ""
        if meal.batch_cook:
            batch = "Doppelte Menge kochen, eine Portion einfrieren."
        elif meal.aus_gefriertruhe:
            batch = "Portion aus der Gefriertruhe auftauen, nicht neu kochen."

        views.append(
            MealView(
                slot=meal.slot,
                kochtag=config.slot_kochtag(meal.slot),
                datum_lang=kalender.formatiere_datum(meal.datum),
                datum_kurz=kalender.formatiere_kurz(meal.datum),
                name=rezept.name,
                deckt_ab=_deckt_ab_text(
                    meal.deckt_ab or config.slot_deckt_ab(meal.slot),
                    meal.tage or config.slot_tage(meal.slot),
                ),
                tage=meal.tage or config.slot_tage(meal.slot),
                portionen_text=_portionen_text(meal.portionen, config),
                prep_minutes=rezept.prep_minutes,
                protein_text=f"{rezept.protein_fuer(variante):.0f} g Protein je Portion",
                variante_hinweis=hinweis,
                tags=rezept.tags,
                zutaten=zeilen,
                steps=rezept.steps_fuer(variante) if mit_rezept else (),
                batch_hinweis=batch,
            )
        )
    return views


def _zutat_zeile(
    slug: str,
    menge: float,
    einheit: str,
    optional: bool,
    hinweis: str,
    zutaten: dict[str, Ingredient],
) -> str:
    from . import units

    zutat = zutaten.get(slug)
    name = zutat.name if zutat else slug
    zeile = f"{units.formatiere_rezept(menge, einheit)} {name}"
    if optional:
        zeile += " (optional)"
    if hinweis:
        zeile += f" – {hinweis}"
    return zeile


# ------------------------------------------------------------------ Renderer


def _autoescape(template_name: str | None) -> bool:
    """Nur HTML maskieren.

    ``select_autoescape`` auf die Endung ``.j2`` anzuwenden würde auch die
    Text-Templates maskieren – dann steht "Obst &amp; Gemüse" in der
    Plain-Text-Mail.
    """
    if not template_name:
        return False
    return template_name.endswith((".html.j2", ".html", ".htm", ".xml"))


def _environment() -> Environment:
    env = Environment(
        loader=PackageLoader("essensplaner", "templates"),
        autoescape=_autoescape,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["datum"] = kalender.formatiere_datum
    env.filters["kurz"] = kalender.formatiere_kurz
    return env


class MailRenderer:
    """Baut die beiden Mails aus Plan, Rezepten und Zutaten."""

    def __init__(
        self,
        config: Config,
        rezepte: dict[str, Recipe],
        zutaten: dict[str, Ingredient],
        bestand: dict[str, tuple[float, str]] | None = None,
    ) -> None:
        self.config = config
        self.rezepte = rezepte
        self.zutaten = zutaten
        self.bestand = bestand or {}
        self.env = _environment()

    def wochenmail(self, woche: WeekPlan, auch_vorrat: bool = False) -> RenderedMail:
        """Die Freitagsmail für eine Planungswoche."""
        frisch = shopping.frischeliste(woche, self.rezepte, self.zutaten, self.config, self.bestand)
        vorrat = None
        if auch_vorrat:
            vorrat = shopping.wochen_pantry_liste(
                woche, self.rezepte, self.zutaten, self.config, self.bestand
            )
        meals = _meal_views(woche, self.rezepte, self.zutaten, self.config, mit_rezept=True)
        betreff = self.config.betreff_woche.format(
            von=kalender.formatiere_kurz(woche.start),
            bis=kalender.formatiere_kurz(woche.ende),
        )
        ctx = {
            "betreff": betreff,
            "woche": woche,
            "von": kalender.formatiere_datum(woche.start),
            "bis": kalender.formatiere_datum(woche.ende),
            "meals": meals,
            "frisch": frisch,
            "vorrat": vorrat,
        }
        return RenderedMail(
            betreff=betreff,
            html=self.env.get_template("weekly.html.j2").render(**ctx),
            text=self.env.get_template("weekly.txt.j2").render(**ctx),
            empfaenger=self.config.mail_empfaenger,
        )

    def monatsmail(self, plan: MonthPlan, vorschau: bool = False) -> RenderedMail:
        """Die Monatsmail mit Übersicht und Vorratsliste."""
        vorrat = shopping.vorratsliste(plan, self.rezepte, self.zutaten, self.config, self.bestand)
        wochen = [
            {
                "start": w.start,
                "ende": w.ende,
                "meals": _meal_views(w, self.rezepte, self.zutaten, self.config, mit_rezept=False),
            }
            for w in plan.weeks
        ]
        label = kalender.monats_label(plan.monat)
        betreff = self.config.betreff_monat.format(monat=label)
        if vorschau:
            betreff = f"[Vorschau] {betreff}"
        ctx = {
            "betreff": betreff,
            "plan": plan,
            "monat_label": label,
            "wochen": wochen,
            "vorrat": vorrat,
            "vorschau": vorschau,
        }
        return RenderedMail(
            betreff=betreff,
            html=self.env.get_template("monthly.html.j2").render(**ctx),
            text=self.env.get_template("monthly.txt.j2").render(**ctx),
            empfaenger=self.config.mail_empfaenger,
        )


# ------------------------------------------------------------------- Versand


def sende(mail: RenderedMail, config: Config) -> None:
    """Die Mail über den konfigurierten SMTP-Server verschicken."""
    smtp = config.smtp
    if not smtp.konfiguriert:
        raise MailFehler(
            "SMTP ist nicht konfiguriert. SMTP_HOST, SMTP_USER und SMTP_PASSWORD "
            "setzen (in Coolify als Secrets) oder --dry-run benutzen."
        )
    msg = mail.to_message(config.mail_von)
    try:
        if smtp.ssl:
            with smtplib.SMTP_SSL(smtp.host, smtp.port, timeout=30) as server:
                if smtp.user:
                    server.login(smtp.user, smtp.password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as server:
                server.ehlo()
                if smtp.starttls:
                    server.starttls()
                    server.ehlo()
                if smtp.user:
                    server.login(smtp.user, smtp.password)
                server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailFehler(f"Versand über {smtp.host}:{smtp.port} fehlgeschlagen: {exc}") from exc
