"""Die beiden Aufgaben, die der Planer regelmäßig erledigt.

``generate_month`` erzeugt den Plan für einen Monat und schickt eine
Vorschau. ``send_weekly`` verschickt freitags die Wochenmail und – wenn in
dieser Woche ein neuer Monat beginnt – zusätzlich die Monatsmail mit der
Vorratsliste.

Beide Funktionen sind von CLI und Scheduler aus gleich aufrufbar und geben
zurück, was passiert ist, damit der Aufrufer es protokollieren kann.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import data, kalender, katalog, mail, rules, shopping
from .config import Config
from .models import Ingredient, MonthPlan, Recipe
from .planner import Planner, PlanungsFehler

log = logging.getLogger(__name__)


class RunnerFehler(Exception):
    """Ein Lauf konnte nicht ausgeführt werden."""


@dataclass(slots=True)
class LaufErgebnis:
    """Was ein Lauf getan hat – für Logausgabe und Tests."""

    aktion: str
    monat: str = ""
    woche_start: date | None = None
    plan_erzeugt: bool = False
    plan_pfad: Path | None = None
    mails: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)
    versendet: bool = False

    def __str__(self) -> str:
        teile = [self.aktion]
        if self.monat:
            teile.append(self.monat)
        if self.woche_start:
            teile.append(f"Woche ab {self.woche_start}")
        if self.plan_erzeugt:
            teile.append("Plan erzeugt")
        if self.mails:
            teile.append(
                ("versendet: " if self.versendet else "gerendert: ") + ", ".join(self.mails)
            )
        return " | ".join(teile)


@dataclass(slots=True)
class Kontext:
    """Geladene Stammdaten – einmal laden, mehrfach verwenden."""

    config: Config
    zutaten: dict[str, Ingredient]
    rezepte: dict[str, Recipe]
    bestand: dict[str, tuple[float, str]]
    #: Slugs, die nicht mehr eingeplant werden sollen. Sie bleiben in
    #: ``rezepte`` stehen, damit bestehende Plaene und deren Einkaufszettel
    #: weiter stimmen - nur der Planer waehlt sie nicht mehr aus.
    gestrichen: set[str] = field(default_factory=set)

    @classmethod
    def laden(cls, config: Config, streng: bool = True) -> Kontext:
        zutaten = data.load_ingredients(config.ingredients_file)
        rezepte = katalog.alle_rezepte(config, data.load_recipes(config.recipes_dir))
        if streng:
            fehler = rules.nur_fehler(rules.pruefe_stammdaten(rezepte, zutaten, config))
            if fehler:
                meldungen = "\n".join(f"  - {f}" for f in fehler[:20])
                raise RunnerFehler(
                    f"Stammdaten verletzen {len(fehler)} harte Regel(n):\n{meldungen}"
                )
        bestand = shopping.lade_bestand(config.data_dir / "bestand.yaml")
        return cls(
            config=config,
            zutaten=zutaten,
            rezepte=rezepte,
            bestand=bestand,
            gestrichen=katalog.lade_gestrichen(config),
        )

    def planner(self) -> Planner:
        return Planner(self.rezepte, self.zutaten, self.config, gestrichen=self.gestrichen)

    def renderer(self) -> mail.MailRenderer:
        return mail.MailRenderer(self.config, self.rezepte, self.zutaten, self.bestand)


# ----------------------------------------------------------------- Zustand


def _state_pfad(config: Config) -> Path:
    return config.plans_dir / "state.json"


def lade_state(config: Config) -> dict[str, str]:
    pfad = _state_pfad(config)
    if not pfad.is_file():
        return {}
    try:
        with pfad.open(encoding="utf-8") as fh:
            daten = json.load(fh)
        return {str(k): str(v) for k, v in daten.items()} if isinstance(daten, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def speichere_state(config: Config, state: dict[str, str]) -> None:
    config.plans_dir.mkdir(parents=True, exist_ok=True)
    with _state_pfad(config).open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")


# ------------------------------------------------------------ Plan erzeugen


def plan_laden_oder_erzeugen(
    ctx: Kontext, monat: str, seed: int | None = None
) -> tuple[MonthPlan, bool]:
    """Den Plan eines Monats holen. Erzeugt ihn, wenn er noch nicht existiert."""
    if data.plan_vorhanden(ctx.config.plans_dir, monat):
        return data.load_plan(ctx.config.plans_dir, monat), False
    plan = ctx.planner().plan_monat(monat, seed=seed)
    data.save_plan(plan, ctx.config.plans_dir)
    return plan, True


def generate_month(
    ctx: Kontext,
    monat: str | None = None,
    seed: int | None = None,
    force: bool = False,
    mail_senden: bool = True,
    heute: date | None = None,
) -> LaufErgebnis:
    """Plan für einen Monat erzeugen und eine Vorschau verschicken.

    Ohne ``monat`` wird der Folgemonat geplant – so läuft die Aufgabe am
    vorletzten Freitag für den kommenden Monat.
    """
    heute = heute or date.today()
    if monat is None:
        jahr, nr = kalender.folgemonat(heute.year, heute.month)
        monat = kalender.monat_str(jahr, nr)
    kalender.parse_monat(monat)  # validiert das Format

    ergebnis = LaufErgebnis(aktion="generate-month", monat=monat)

    if data.plan_vorhanden(ctx.config.plans_dir, monat) and not force:
        ergebnis.hinweise.append(f"Plan für {monat} existiert bereits – mit --force neu erzeugen.")
        return ergebnis

    try:
        plan = ctx.planner().plan_monat(monat, seed=seed)
    except PlanungsFehler as exc:
        details = "\n".join(f"  - {d}" for d in exc.details)
        raise RunnerFehler(f"{exc}\n{details}" if details else str(exc)) from exc

    # Gürtel und Hosenträger: der erzeugte Plan wird noch einmal komplett
    # gegen alle harten Regeln geprüft, bevor er gespeichert wird.
    verstoesse = rules.nur_fehler(rules.pruefe_plan(plan, ctx.rezepte, ctx.zutaten, ctx.config))
    if verstoesse:
        meldungen = "\n".join(f"  - {v}" for v in verstoesse[:20])
        raise RunnerFehler(f"Erzeugter Plan verletzt harte Regeln:\n{meldungen}")

    ergebnis.plan_pfad = data.save_plan(plan, ctx.config.plans_dir)
    ergebnis.plan_erzeugt = True

    nachricht = ctx.renderer().monatsmail(plan, vorschau=True)
    ergebnis.mails.append(nachricht.betreff)
    if mail_senden:
        mail.sende(nachricht, ctx.config)
        ergebnis.versendet = True

    return ergebnis


# --------------------------------------------------------- Wochenmail


def send_reminder(
    ctx: Kontext,
    freitag: date | None = None,
    mail_senden: bool = True,
) -> LaufErgebnis:
    """Die Erinnerung an den Einkauf von morgen, mit dem fertigen Zettel.

    Anders als ``send_weekly`` erzeugt sie **keinen** Plan, wenn für den
    Monat noch keiner da ist. Eine Erinnerung ist ein Hinweis, kein Anlass,
    nachts um halb sieben einen Monatsplan zu würfeln – fehlt der Plan, sagt
    das Ergebnis das, und am Freitag erzeugt ihn die Wochenmail.
    """
    freitag = freitag or kalender.naechster_freitag(date.today(), inklusiv=False)
    monat = kalender.monat_der_woche(freitag)

    ergebnis = LaufErgebnis(aktion="erinnerung", monat=monat, woche_start=freitag)

    if not data.plan_vorhanden(ctx.config.plans_dir, monat):
        ergebnis.hinweise.append(
            f"Für {monat} gibt es noch keinen Plan – keine Erinnerung verschickt."
        )
        return ergebnis

    plan = data.load_plan(ctx.config.plans_dir, monat)
    woche = plan.woche_ab(freitag) or plan.woche_fuer(freitag)
    if woche is None:
        raise RunnerFehler(
            f"Im Plan {monat} gibt es keine Woche ab {freitag}. "
            f"Vorhandene Wochen: {', '.join(str(w.start) for w in plan.weeks)}"
        )

    # Dieselbe Entscheidung wie in der Wochenmail: beginnt mit dieser Woche
    # ein Monat, kommt die Vorratsliste getrennt, sonst muss sie hier mit.
    nachricht = ctx.renderer().erinnerungsmail(
        woche, auch_vorrat=kalender.startet_monatswoche(freitag) is None
    )
    ergebnis.mails.append(nachricht.betreff)
    if mail_senden:
        mail.sende(nachricht, ctx.config)
    ergebnis.versendet = mail_senden
    return ergebnis


def einkauf_nach_mealie(
    ctx: Kontext,
    freitag: date | None = None,
    transport=None,
    dry_run: bool = False,
):
    """Den Zettel für den Einkauf am ``freitag`` in die Mealie-Liste schreiben.

    Ohne ``freitag`` ist der nächste Freitag gemeint, heute eingeschlossen.
    Es ist derselbe Zettel wie in der Oberfläche: mit Vorratszutaten der
    Woche, ohne Abgewähltes.

    Fehlt der Monatsplan, wird er wie bei der Wochenmail erzeugt – nur nicht
    im Dry-Run: ein Probelauf darf nichts anlegen, auch keinen Plan.
    """
    from .mealie import HttpTransport
    from .mealie_einkauf import EinkaufSync, eintraege

    freitag = freitag or kalender.naechster_freitag(date.today())
    monat = kalender.monat_der_woche(freitag)
    if dry_run and not data.plan_vorhanden(ctx.config.plans_dir, monat):
        raise RunnerFehler(
            f"Für {monat} gibt es noch keinen Plan – der Dry-Run legt keinen an. "
            f"Erst `essensplaner generate-month {monat}` oder ohne --dry-run."
        )
    plan, erzeugt = plan_laden_oder_erzeugen(ctx, monat)
    if erzeugt:
        log.info("Für %s gab es noch keinen Plan – er wurde jetzt erzeugt.", monat)
    woche = plan.woche_ab(freitag) or plan.woche_fuer(freitag)
    if woche is None:
        raise RunnerFehler(f"Im Plan {monat} gibt es keine Woche ab {freitag}.")

    config = ctx.config
    frisch = shopping.frischeliste(woche, ctx.rezepte, ctx.zutaten, config, ctx.bestand)
    vorrat = shopping.wochen_pantry_liste(woche, ctx.rezepte, ctx.zutaten, config, ctx.bestand)
    zeilen, _ = shopping.lade_grundeinkauf(config)
    schluessel = woche.start.isoformat()
    soll = eintraege(
        woche.start,
        frisch,
        vorrat,
        zeilen,
        shopping.lade_abwahl(config, schluessel),
        shopping.lade_extra(config, schluessel),
    )
    if transport is None:
        if not config.mealie_konfiguriert:
            raise RunnerFehler("MEALIE_URL und MEALIE_TOKEN müssen gesetzt sein.")
        transport = HttpTransport(config.mealie_url, config.mealie_token)
    sync = EinkaufSync(transport, liste=config.mealie_einkaufsliste, dry_run=dry_run)
    return sync.synchronisiere(woche.start, soll)


def plan_nach_mealie(
    ctx: Kontext,
    monat: str | None = None,
    transport=None,
    dry_run: bool = False,
):
    """Den Monatsplan in Mealies Speiseplan schreiben.

    Ohne ``monat`` ist der laufende Monat gemeint. Fehlt der Plan, wird er
    erzeugt – im Dry-Run nicht: ein Probelauf legt nichts an.

    Der Zeitraum ergibt sich aus den Einträgen selbst, nicht aus dem
    Kalendermonat. Eine Woche reicht über den Monatswechsel, und gelöscht
    werden soll nur dort, wo dieser Plan auch zuständig ist.
    """
    from .mealie import HttpTransport
    from .mealie_speiseplan import SpeiseplanSync, eintraege

    heute = date.today()
    monat = monat or kalender.monat_str(heute.year, heute.month)
    if dry_run and not data.plan_vorhanden(ctx.config.plans_dir, monat):
        raise RunnerFehler(
            f"Für {monat} gibt es noch keinen Plan – der Dry-Run legt keinen an. "
            f"Erst `essensplaner generate-month {monat}` oder ohne --dry-run."
        )
    plan, erzeugt = plan_laden_oder_erzeugen(ctx, monat)
    if erzeugt:
        log.info("Für %s gab es noch keinen Plan – er wurde jetzt erzeugt.", monat)

    soll = eintraege(plan)
    if not soll:
        raise RunnerFehler(f"Der Plan {monat} enthält keine Mahlzeiten.")
    von = min(e.datum for e in soll)
    bis = max(e.datum for e in soll)

    if transport is None:
        if not ctx.config.mealie_konfiguriert:
            raise RunnerFehler("MEALIE_URL und MEALIE_TOKEN müssen gesetzt sein.")
        transport = HttpTransport(ctx.config.mealie_url, ctx.config.mealie_token)
    sync = SpeiseplanSync(transport, dry_run=dry_run)
    return sync.synchronisiere(von, bis, soll)


def send_weekly(
    ctx: Kontext,
    tag: date | None = None,
    mail_senden: bool = True,
    seed: int | None = None,
) -> LaufErgebnis:
    """Die Wochenmail für die Woche verschicken, die an diesem Freitag beginnt.

    Fällt in diese Woche ein Monatserster, geht zusätzlich die Monatsmail mit
    der Vorratsliste raus – so wie es die Spec vorsieht.
    """
    tag = tag or date.today()
    freitag = kalender.letzter_freitag_bis(tag)
    monat = kalender.monat_der_woche(freitag)

    ergebnis = LaufErgebnis(aktion="send-weekly", monat=monat, woche_start=freitag)

    plan, erzeugt = plan_laden_oder_erzeugen(ctx, monat, seed=seed)
    if erzeugt:
        ergebnis.plan_erzeugt = True
        ergebnis.plan_pfad = data.plan_pfad(ctx.config.plans_dir, monat)
        ergebnis.hinweise.append(f"Für {monat} gab es noch keinen Plan – er wurde jetzt erzeugt.")

    woche = plan.woche_ab(freitag)
    if woche is None:
        # Kann vorkommen, wenn ein Plan von Hand verändert wurde.
        woche = plan.woche_fuer(freitag)
    if woche is None:
        raise RunnerFehler(
            f"Im Plan {monat} gibt es keine Woche ab {freitag}. "
            f"Vorhandene Wochen: {', '.join(str(w.start) for w in plan.weeks)}"
        )

    renderer = ctx.renderer()
    monats_start = kalender.startet_monatswoche(freitag)

    # Ohne vorausgehende Monatsmail muss der Wocheneinkauf die Vorratszutaten
    # mit abdecken, sonst fehlt plötzlich das Mehl.
    wochenmail = renderer.wochenmail(woche, auch_vorrat=monats_start is None)
    nachrichten = [wochenmail]

    if monats_start:
        nachrichten.insert(0, renderer.monatsmail(plan, vorschau=False))

    for nachricht in nachrichten:
        ergebnis.mails.append(nachricht.betreff)
        if mail_senden:
            mail.sende(nachricht, ctx.config)
    ergebnis.versendet = mail_senden
    return ergebnis
