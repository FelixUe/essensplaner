"""Kommandozeile des Essensplaners.

    essensplaner validate                  Stammdaten gegen alle Regeln prüfen
    essensplaner generate-month 2026-12    Plan erzeugen und Vorschau mailen
    essensplaner send-weekly               Freitagsmail verschicken
    essensplaner show 2026-12              Plan im Terminal ansehen
    essensplaner shopping 2026-12          Einkaufslisten im Terminal
    essensplaner preview weekly            Mail als HTML-Datei schreiben
    essensplaner serve                     Scheduler im Container
    essensplaner mealie-sync               Rezepte nach Mealie übertragen

Die Unterbefehle ``generate-month`` und ``send-weekly`` sind die, die in
Coolify als Scheduled Task laufen.
"""

from __future__ import annotations

import sys
import threading
from datetime import date, datetime
from pathlib import Path

import click

from . import data, kalender, rules, shopping
from .config import Config, load_config
from .mail import MailFehler
from .nutrition import pruefe_angaben
from .planner import PlanungsFehler
from .runner import (
    Kontext,
    RunnerFehler,
    generate_month,
    plan_laden_oder_erzeugen,
    send_reminder,
    send_weekly,
)


def _fehler(nachricht: str) -> None:
    click.secho(nachricht, fg="red", err=True)
    sys.exit(1)


def _config(pfad: str | None) -> Config:
    try:
        return load_config(pfad)
    except (FileNotFoundError, OSError) as exc:
        _fehler(str(exc))
        raise  # unerreichbar, hält mypy ruhig


def _kontext(config: Config, streng: bool = True) -> Kontext:
    try:
        return Kontext.laden(config, streng=streng)
    except (RunnerFehler, data.DataError) as exc:
        _fehler(str(exc))
        raise


def _parse_datum(wert: str | None) -> date | None:
    if not wert:
        return None
    try:
        return datetime.strptime(wert, "%Y-%m-%d").date()
    except ValueError:
        _fehler(f"Datum muss im Format JJJJ-MM-TT angegeben werden, nicht {wert!r}")
        raise


@click.group(help=__doc__)
@click.option(
    "--config",
    "config_pfad",
    type=click.Path(dir_okay=False),
    default=None,
    help="Pfad zur config.yaml (Default: ./config.yaml oder ESSENSPLANER_CONFIG).",
)
@click.version_option(package_name="essensplaner")
@click.pass_context
def cli(ctx: click.Context, config_pfad: str | None) -> None:
    ctx.ensure_object(dict)
    ctx.obj["config"] = _config(config_pfad)


# ------------------------------------------------------------------ validate


@cli.command("validate")
@click.option("--protein/--no-protein", default=True, help="Proteinangaben nachrechnen.")
@click.pass_context
def validate_cmd(ctx: click.Context, protein: bool) -> None:
    """Zutaten und Rezepte gegen alle harten Regeln prüfen."""
    config: Config = ctx.obj["config"]
    try:
        zutaten = data.load_ingredients(config.ingredients_file)
        rezepte = data.load_recipes(config.recipes_dir)
    except data.DataError as exc:
        _fehler(str(exc))
        return

    click.echo(f"{len(zutaten)} Zutaten, {len(rezepte)} Rezepte geladen.")

    probleme = 0

    unbekannt = data.unbekannte_zutaten(rezepte, zutaten)
    if unbekannt:
        probleme += len(unbekannt)
        click.secho(f"\n{len(unbekannt)} Zutat(en) ohne Stammdaten:", fg="red")
        for rezept, slug in unbekannt:
            click.echo(f"  {rezept}: {slug}")

    issues = rules.pruefe_stammdaten(rezepte, zutaten, config)
    fehler = rules.nur_fehler(issues)
    warnungen = [i for i in issues if i not in fehler]
    if fehler:
        probleme += len(fehler)
        click.secho(f"\n{len(fehler)} Regelverstoß/-verstöße:", fg="red")
        for i in fehler:
            click.echo(f"  {i}")
    if warnungen:
        click.secho(f"\n{len(warnungen)} Warnung(en):", fg="yellow")
        for i in warnungen:
            click.echo(f"  {i}")

    if protein:
        meldungen = pruefe_angaben(rezepte, zutaten)
        if meldungen:
            probleme += len(meldungen)
            click.secho(
                f"\n{len(meldungen)} Proteinangabe(n) passen nicht zu den Zutaten:", fg="red"
            )
            for m in meldungen:
                click.echo(f"  {m}")
            click.echo("  Mit 'python tools/protein_sync.py --write' übernehmen.")

    # Deckung je Slot zeigen – ein Slot ohne Kandidaten lässt die Planung scheitern.
    click.echo("\nZugelassene Rezepte je Slot:")
    for slot in config.slot_namen:
        passend = [r for r in rezepte.values() if r.erlaubt_in_slot(slot)]
        sauber = [
            r for r in passend if not rules.nur_fehler(rules.pruefe_rezept(r, zutaten, config))
        ]
        farbe = "green" if len(sauber) >= 5 else "yellow"
        click.secho(f"  {slot}: {len(sauber)} von {len(passend)}", fg=farbe)

    if probleme:
        _fehler(f"\n{probleme} Problem(e) gefunden.")
    click.secho("\nAlles in Ordnung.", fg="green")


# ------------------------------------------------------------ generate-month


@cli.command("generate-month")
@click.argument("monat", required=False)
@click.option("--seed", type=int, default=None, help="Seed für einen reproduzierbaren Plan.")
@click.option("--force", is_flag=True, help="Vorhandenen Plan überschreiben.")
@click.option("--dry-run", is_flag=True, help="Nichts verschicken, nur erzeugen und anzeigen.")
@click.pass_context
def generate_month_cmd(
    ctx: click.Context, monat: str | None, seed: int | None, force: bool, dry_run: bool
) -> None:
    """Monatsplan erzeugen und als Vorschau mailen. Ohne MONAT: der Folgemonat."""
    config: Config = ctx.obj["config"]
    kontext = _kontext(config)
    try:
        ergebnis = generate_month(
            kontext, monat=monat, seed=seed, force=force, mail_senden=not dry_run
        )
    except (RunnerFehler, PlanungsFehler, MailFehler, ValueError) as exc:
        _fehler(str(exc))
        return

    for hinweis in ergebnis.hinweise:
        click.secho(hinweis, fg="yellow")
    if ergebnis.plan_erzeugt:
        click.secho(f"Plan gespeichert: {ergebnis.plan_pfad}", fg="green")
        _zeige_plan(kontext, ergebnis.monat)
    if ergebnis.mails:
        verb = "Versendet" if ergebnis.versendet else "Gerendert (dry-run)"
        for betreff in ergebnis.mails:
            click.echo(f"{verb}: {betreff}")


# --------------------------------------------------------------- send-weekly


@cli.command("send-weekly")
@click.option("--date", "tag", default=None, help="Bezugstag JJJJ-MM-TT (Default: heute).")
@click.option("--dry-run", is_flag=True, help="Nichts verschicken, nur rendern.")
@click.pass_context
def send_weekly_cmd(ctx: click.Context, tag: str | None, dry_run: bool) -> None:
    """Die Freitagsmail mit Plan und Frischeliste verschicken."""
    config: Config = ctx.obj["config"]
    kontext = _kontext(config)
    try:
        ergebnis = send_weekly(kontext, tag=_parse_datum(tag), mail_senden=not dry_run)
    except (RunnerFehler, PlanungsFehler, MailFehler, data.DataError) as exc:
        _fehler(str(exc))
        return

    for hinweis in ergebnis.hinweise:
        click.secho(hinweis, fg="yellow")
    verb = "Versendet" if ergebnis.versendet else "Gerendert (dry-run)"
    for betreff in ergebnis.mails:
        click.echo(f"{verb}: {betreff}")
    click.echo(f"Empfänger: {', '.join(config.mail_empfaenger) or '(keine konfiguriert)'}")


# ---------------------------------------------------------------------- show


def _zeige_plan(kontext: Kontext, monat: str) -> None:
    plan = data.load_plan(kontext.config.plans_dir, monat)
    planner = kontext.planner()
    st = planner.statistik(plan)
    click.echo(f"\n{kalender.monats_label(monat)} – {st['wochen']} Wochen, {st['slots']} Slots")
    for woche in plan.weeks:
        click.secho(
            f"\n  Woche {kalender.formatiere_kurz(woche.start)} – "
            f"{kalender.formatiere_kurz(woche.ende)}",
            bold=True,
        )
        for meal in woche.meals:
            rezept = kontext.rezepte.get(meal.recipe_slug)
            protein = rezept.protein_fuer(meal.variant) if rezept else 0
            marker = ""
            if meal.aus_gefriertruhe:
                marker = "  [aus der Truhe]"
            elif meal.batch_cook:
                marker = "  [doppelt kochen]"
            click.echo(
                f"    {meal.slot}  {kalender.formatiere_kurz(meal.datum)}  "
                f"{meal.recipe_name:<42} {protein:>3.0f} g{marker}"
            )
    click.echo(
        f"\n  Kernrezepte: {st['core_anteil']:.0%} (Ziel {kontext.config.core_anteil:.0%}) · "
        f"{st['verschiedene_gerichte']} verschiedene Gerichte · "
        f"Protein MO/MI min. {st['protein_min']:.0f} g"
    )
    haeufig = [f"{name} ({n}×)" for name, n in st["haeufigkeit"].items() if n > 1]
    if haeufig:
        click.echo(f"  Mehrfach: {', '.join(haeufig)}")


@cli.command("show")
@click.argument("monat")
@click.pass_context
def show_cmd(ctx: click.Context, monat: str) -> None:
    """Einen gespeicherten Plan im Terminal ansehen."""
    config: Config = ctx.obj["config"]
    kontext = _kontext(config, streng=False)
    try:
        _zeige_plan(kontext, monat)
    except data.DataError as exc:
        _fehler(str(exc))


# ------------------------------------------------------------------ shopping


@cli.command("shopping")
@click.argument("monat")
@click.option("--week", "woche_start", default=None, help="Freitag einer Woche JJJJ-MM-TT.")
@click.pass_context
def shopping_cmd(ctx: click.Context, monat: str, woche_start: str | None) -> None:
    """Einkaufslisten eines Monats oder einer Woche anzeigen."""
    config: Config = ctx.obj["config"]
    kontext = _kontext(config, streng=False)
    try:
        plan = data.load_plan(config.plans_dir, monat)
    except data.DataError as exc:
        _fehler(str(exc))
        return

    def ausgeben(liste: shopping.ShoppingList) -> None:
        click.secho(f"\n{liste.titel} ({liste.anzahl} Posten)", bold=True)
        for abteilung, items in liste.abteilungen:
            click.secho(f"\n  {abteilung}", fg="green")
            for item in items:
                click.echo(f"    [ ] {item.anzeige}")
        if not liste.items:
            click.echo("  (leer)")

    if woche_start:
        tag = _parse_datum(woche_start)
        woche = plan.woche_ab(tag) or plan.woche_fuer(tag)
        if woche is None:
            _fehler(f"Keine Woche ab {tag} im Plan {monat}.")
            return
        ausgeben(
            shopping.frischeliste(woche, kontext.rezepte, kontext.zutaten, config, kontext.bestand)
        )
    else:
        ausgeben(
            shopping.vorratsliste(plan, kontext.rezepte, kontext.zutaten, config, kontext.bestand)
        )
        for woche in plan.weeks:
            ausgeben(
                shopping.frischeliste(
                    woche, kontext.rezepte, kontext.zutaten, config, kontext.bestand
                )
            )


# --------------------------------------------------------------- erinnerung


@cli.command("send-reminder")
@click.option("--freitag", default=None, help="Einkaufsfreitag JJJJ-MM-TT (Default: der nächste).")
@click.option("--dry-run", is_flag=True, help="Nichts verschicken, nur rendern.")
@click.pass_context
def send_reminder_cmd(ctx: click.Context, freitag: str | None, dry_run: bool) -> None:
    """Die Erinnerung an den Einkauf von morgen verschicken."""
    config: Config = ctx.obj["config"]
    kontext = _kontext(config)
    try:
        ergebnis = send_reminder(kontext, freitag=_parse_datum(freitag), mail_senden=not dry_run)
    except (RunnerFehler, MailFehler, data.DataError) as exc:
        _fehler(str(exc))
        return

    for hinweis in ergebnis.hinweise:
        click.secho(hinweis, fg="yellow")
    verb = "Versendet" if ergebnis.versendet else "Gerendert (dry-run)"
    for betreff in ergebnis.mails:
        click.echo(f"{verb}: {betreff}")
    if ergebnis.mails:
        click.echo(f"Empfänger: {', '.join(config.mail_empfaenger) or '(keine konfiguriert)'}")


# ------------------------------------------------------------------- preview


@cli.command("preview")
@click.argument("art", type=click.Choice(["weekly", "monthly", "reminder"]))
@click.option("--monat", default=None, help="Monat JJJJ-MM (Default: aktueller).")
@click.option("--week", "woche_start", default=None, help="Freitag JJJJ-MM-TT.")
@click.option(
    "--out",
    type=click.Path(dir_okay=False),
    default=None,
    help="Zieldatei für das HTML (Default: preview-<art>.html).",
)
@click.pass_context
def preview_cmd(
    ctx: click.Context, art: str, monat: str | None, woche_start: str | None, out: str | None
) -> None:
    """Eine Mail als HTML-Datei schreiben, ohne sie zu verschicken."""
    config: Config = ctx.obj["config"]
    kontext = _kontext(config, streng=False)
    heute = date.today()
    monat = monat or kalender.monat_str(heute.year, heute.month)

    try:
        plan, erzeugt = plan_laden_oder_erzeugen(kontext, monat)
    except PlanungsFehler as exc:
        _fehler(str(exc))
        return
    if erzeugt:
        click.secho(f"Plan für {monat} neu erzeugt.", fg="yellow")

    renderer = kontext.renderer()
    if art == "monthly":
        nachricht = renderer.monatsmail(plan, vorschau=True)
    else:
        tag = _parse_datum(woche_start) or heute
        woche = plan.woche_ab(kalender.letzter_freitag_bis(tag)) or plan.weeks[0]
        auch_vorrat = kalender.startet_monatswoche(woche.start) is None
        if art == "reminder":
            nachricht = renderer.erinnerungsmail(woche, auch_vorrat=auch_vorrat)
        else:
            nachricht = renderer.wochenmail(woche, auch_vorrat=auch_vorrat)

    ziel = Path(out or f"preview-{art}.html")
    ziel.write_text(nachricht.html, encoding="utf-8")
    ziel.with_suffix(".txt").write_text(nachricht.text, encoding="utf-8")
    click.secho(f"{nachricht.betreff}", bold=True)
    click.echo(f"HTML: {ziel}")
    click.echo(f"Text: {ziel.with_suffix('.txt')}")


# --------------------------------------------------------------------- serve


@cli.command("serve")
@click.option("--once", is_flag=True, help="Nur einmal prüfen und beenden (für Tests).")
@click.option(
    "--web/--kein-web",
    default=None,
    help="Weboberfläche mitstarten. Ohne Angabe: an, sobald ESSENSPLANER_WEB_PASSWORT gesetzt ist.",
)
@click.pass_context
def serve_cmd(ctx: click.Context, once: bool, web: bool | None) -> None:
    """Scheduler und Weboberfläche: der Dauerbetrieb im Container.

    Der Scheduler wartet auf die Termine und verschickt die Mails. Ist ein
    Passwort gesetzt, läuft daneben die Weboberfläche, über die sich der
    Monatsplan ansehen und ändern lässt.

    Beides im selben Prozess, weil beides dieselben Plandateien anfasst –
    getrennte Container müssten sich das Volume teilen und sich beim
    Schreiben abstimmen. Im Prozess reicht eine Sperre.
    """
    from .scheduler import Scheduler
    from .web import WebFehler, starte, web_aktiv

    config: Config = ctx.obj["config"]
    scheduler = Scheduler(config)

    if once:
        # Einmaldurchlauf ist für Tests und den Rauchtest im Image gedacht;
        # eine Weboberfläche, die sofort wieder zugeht, hilft dort nicht.
        scheduler.run(einmal=True)
        return

    mit_web = web_aktiv() if web is None else web
    if not mit_web:
        if web is None:
            click.echo(
                "Weboberfläche aus: ESSENSPLANER_WEB_PASSWORT ist nicht gesetzt.",
                err=True,
            )
        scheduler.run()
        return

    kontext = _kontext(config)
    thread = threading.Thread(target=scheduler.run, name="scheduler", daemon=True)
    thread.start()
    try:
        starte(kontext)
    except WebFehler as exc:
        _fehler(str(exc))


# --------------------------------------------------------------- mealie-sync


@cli.command("mealie-sync")
@click.option("--dry-run", is_flag=True, help="Nur anzeigen, was angelegt/aktualisiert würde.")
@click.pass_context
def mealie_sync_cmd(ctx: click.Context, dry_run: bool) -> None:
    """Alle Rezepte nach Mealie übertragen (einseitig, Essensplaner gewinnt).

    Braucht MEALIE_URL (z. B. http://mealie:9000) und MEALIE_TOKEN
    (API-Token aus dem Mealie-Profil).
    """
    import os

    from .mealie import HttpTransport, MealieFehler, MealieSync

    url = os.environ.get("MEALIE_URL", "").strip()
    token = os.environ.get("MEALIE_TOKEN", "").strip()
    if not url or not token:
        _fehler("MEALIE_URL und MEALIE_TOKEN müssen gesetzt sein.")

    kontext = _kontext(ctx.obj["config"])
    sync = MealieSync(HttpTransport(url, token), dry_run=dry_run)
    try:
        bericht = sync.synchronisiere(kontext.rezepte, kontext.zutaten, kontext.gestrichen)
    except MealieFehler as exc:
        _fehler(str(exc))
        raise

    vorsilbe = "Würde " if dry_run else ""
    click.echo(f"{vorsilbe}neu anlegen: {len(bericht.angelegt)}")
    for slug in bericht.angelegt:
        click.echo(f"  + {slug}")
    click.echo(f"{vorsilbe}aktualisieren: {len(bericht.aktualisiert)}")
    if bericht.verwaist:
        click.echo(
            f"In Mealie, aber nicht mehr im Essensplaner ({len(bericht.verwaist)}, bleiben stehen):"
        )
        for slug in bericht.verwaist:
            click.echo(f"  ? {slug}")


if __name__ == "__main__":
    cli()
