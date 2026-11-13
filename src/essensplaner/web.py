"""Weboberfläche: den Monatsplan ansehen und Gerichte tauschen.

Der Planer würfelt einen regelkonformen Monat, der Scheduler verschickt ihn.
Was gefehlt hat: eingreifen zu können, wenn am Samstag Besuch kommt. Diese
Oberfläche zeigt den ganzen Monat und erlaubt genau eine Änderung je Slot –
ein anderes Gericht wählen. Angeboten werden nur Gerichte, die dort erlaubt
sind, und der geänderte Plan muss anschließend dieselbe Prüfung bestehen wie
ein frisch gewürfelter (siehe :mod:`essensplaner.editing`).

Die Seite steht im Internet, sobald Coolify eine Domain davorhängt. Deshalb:

* Ohne gesetztes Passwort startet sie gar nicht erst. Eine offene Seite, auf
  der Fremde den Essensplan umbauen, ist schlimmer als keine Seite.
* Anmeldung über ein Formular, Sitzung in einem signierten Cookie.
* Nach mehreren Fehlversuchen eine Pause, damit das Passwort nicht
  durchprobiert werden kann.
* Jedes Formular trägt ein Token gegen fremde Seiten, die im Namen des
  angemeldeten Browsers Gerichte tauschen könnten.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import date, datetime

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from . import data, kalender, katalog, shopping, units
from .editing import (
    AenderungsFehler,
    finde_mahlzeit,
    gericht_tauschen,
    plaetze_tauschen,
)
from .katalog import KatalogFehler
from .models import MonthPlan
from .planner import Planner, PlanungsFehler
from .rules import nur_fehler, pruefe_rezept
from .runner import Kontext

log = logging.getLogger(__name__)

#: Standardport im Container. Coolify legt die Domain davor.
STANDARD_PORT = 8080

#: Nach so vielen Fehlversuchen von derselben Adresse ist für
#: ``SPERRE_SEKUNDEN`` Schluss.
MAX_FEHLVERSUCHE = 5
SPERRE_SEKUNDEN = 300


class WebFehler(Exception):
    """Die Oberfläche kann so nicht starten."""


@dataclass(slots=True)
class _Anmeldeversuche:
    """Fehlversuche je Absenderadresse, nur im Arbeitsspeicher.

    Ein Neustart setzt das zurück. Das ist in Ordnung: die Sperre soll
    Durchprobieren bremsen, nicht einen Angreifer dauerhaft aussperren.
    """

    versuche: dict[str, tuple[int, float]] = field(default_factory=dict)

    def gesperrt_bis(self, adresse: str) -> float:
        anzahl, zeitpunkt = self.versuche.get(adresse, (0, 0.0))
        if anzahl < MAX_FEHLVERSUCHE:
            return 0.0
        bis = zeitpunkt + SPERRE_SEKUNDEN
        return bis if bis > time.monotonic() else 0.0

    def fehlschlag(self, adresse: str) -> None:
        anzahl, _ = self.versuche.get(adresse, (0, 0.0))
        self.versuche[adresse] = (anzahl + 1, time.monotonic())

    def erfolg(self, adresse: str) -> None:
        self.versuche.pop(adresse, None)


def _passwort_aus_umgebung() -> str:
    return os.environ.get("ESSENSPLANER_WEB_PASSWORT", "").strip()


def _secret_aus_umgebung() -> str:
    gesetzt = os.environ.get("ESSENSPLANER_WEB_SECRET", "").strip()
    if gesetzt:
        return gesetzt
    # Ohne festen Wert sind alle Sitzungen nach einem Rollout ungültig.
    # Das ist nur lästig, nicht unsicher – deshalb kein harter Fehler.
    log.warning(
        "ESSENSPLANER_WEB_SECRET ist nicht gesetzt. Es wird ein zufälliger Wert "
        "erzeugt; nach jedem Neustart muss man sich neu anmelden."
    )
    return secrets.token_urlsafe(32)


def web_aktiv() -> bool:
    """Ob die Oberfläche überhaupt laufen soll."""
    return bool(_passwort_aus_umgebung())


def web_port() -> int:
    try:
        return int(os.environ.get("ESSENSPLANER_WEB_PORT", STANDARD_PORT))
    except ValueError:
        log.warning("ESSENSPLANER_WEB_PORT ist keine Zahl, nutze %d", STANDARD_PORT)
        return STANDARD_PORT


# ------------------------------------------------------------- Darstellung


def _monat_titel(monat: str) -> str:
    jahr, nr = kalender.parse_monat(monat)
    namen = (
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    )
    return f"{namen[nr - 1]} {jahr}"


def _nachbarmonat(monat: str, schritt: int) -> str:
    jahr, nr = kalender.parse_monat(monat)
    gesamt = (jahr * 12 + nr - 1) + schritt
    return kalender.monat_str(gesamt // 12, gesamt % 12 + 1)


def _aktueller_monat(heute: date | None = None) -> str:
    """Der Monat, zu dem die laufende Planungswoche gehört.

    Nicht einfach der Kalendermonat von heute: die Woche ab dem letzten
    Freitag kann schon zum Folgemonat gehören. Dann soll die Startseite auch
    dorthin führen und nicht auf einen Monat, der gerade ausläuft.
    """
    tag = heute or date.today()
    return kalender.monat_der_woche(kalender.letzter_freitag_bis(tag))


# ------------------------------------------------------------------- App


def erstelle_app(ctx: Kontext, passwort: str | None = None) -> Flask:
    """Die Flask-App bauen.

    ``ctx`` enthält Rezepte und Zutaten; die liegen im Image und ändern sich
    zur Laufzeit nicht. Die Pläne dagegen werden bei jedem Aufruf frisch
    gelesen – der Scheduler schreibt sie im selben Prozess.
    """
    geheim = passwort if passwort is not None else _passwort_aus_umgebung()
    if not geheim:
        raise WebFehler(
            "ESSENSPLANER_WEB_PASSWORT ist nicht gesetzt. Ohne Passwort startet "
            "die Oberfläche nicht – sie stünde sonst offen im Netz."
        )

    app = Flask(__name__, template_folder="templates/web", static_folder=None)
    app.config.update(
        SECRET_KEY=_secret_aus_umgebung(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Hinter Coolify läuft TLS. Lokal ohne HTTPS würde ein Secure-Cookie
        # nie ankommen, deshalb schaltbar.
        SESSION_COOKIE_SECURE=os.environ.get("ESSENSPLANER_WEB_UNSICHER", "") != "true",
        MAX_CONTENT_LENGTH=64 * 1024,
    )

    # Rezepte aus dem Image bilden die Grundlage; eigene kommen aus dem
    # Volume dazu und koennen sie ueberschreiben. Der Katalog laedt neu,
    # sobald sich etwas aendert - sonst zeigte die Oberflaeche den Stand
    # vom Containerstart.
    basis_rezepte = {
        slug: rezept
        for slug, rezept in ctx.rezepte.items()
        if not katalog.ist_eigenes(ctx.config, rezept)
    }

    class _Katalog:
        def __init__(self) -> None:
            self.neu_laden()

        def neu_laden(self) -> None:
            self.rezepte = katalog.alle_rezepte(ctx.config, basis_rezepte)
            self.gestrichen = katalog.lade_gestrichen(ctx.config)
            self.eigene = {
                slug
                for slug, rezept in self.rezepte.items()
                if katalog.ist_eigenes(ctx.config, rezept)
            }
            self.planner = Planner(
                self.rezepte, ctx.zutaten, ctx.config, gestrichen=self.gestrichen
            )

    kat = _Katalog()
    versuche = _Anmeldeversuche()

    # ---------------------------------------------------------- Hilfsmittel

    def angemeldet() -> bool:
        return session.get("angemeldet") is True

    def csrf_token() -> str:
        token = session.get("csrf")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf"] = token
        return token

    def csrf_pruefen() -> None:
        gesendet = request.form.get("csrf", "")
        erwartet = session.get("csrf", "")
        if not erwartet or not hmac.compare_digest(gesendet, erwartet):
            abort(400, "Formular abgelaufen. Bitte die Seite neu laden.")

    def plan_holen(monat: str) -> MonthPlan | None:
        with data.PLAN_SPERRE:
            if not data.plan_vorhanden(ctx.config.plans_dir, monat):
                return None
            return data.load_plan(ctx.config.plans_dir, monat)

    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["monat_titel"] = _monat_titel
    app.jinja_env.filters["datum_lang"] = kalender.formatiere_datum
    app.jinja_env.filters["datum_kurz"] = kalender.formatiere_kurz

    # ------------------------------------------------------------- Zugang

    @app.before_request
    def zugang_pruefen():
        offen = {"anmelden", "gesund"}
        if request.endpoint in offen or angemeldet():
            return None
        return redirect(url_for("anmelden", weiter=request.path))

    @app.get("/gesund")
    def gesund():
        return {"status": "ok", "monate": len(list(ctx.config.plans_dir.glob("*.json")))}

    @app.route("/anmelden", methods=["GET", "POST"])
    def anmelden():
        weiter = request.args.get("weiter") or url_for("start")
        # Nur eigene Pfade, damit die Seite nicht auf fremde Adressen weiterleitet.
        if not weiter.startswith("/"):
            weiter = url_for("start")

        if request.method == "POST":
            adresse = request.remote_addr or "unbekannt"
            gesperrt = versuche.gesperrt_bis(adresse)
            if gesperrt:
                rest = int(gesperrt - time.monotonic()) + 1
                flash(f"Zu viele Fehlversuche. Bitte {rest} Sekunden warten.", "fehler")
                return render_template("anmeldung.html.j2", weiter=weiter), 429

            eingabe = request.form.get("passwort", "")
            if hmac.compare_digest(eingabe, geheim):
                versuche.erfolg(adresse)
                session.clear()
                session["angemeldet"] = True
                session.permanent = True
                return redirect(weiter)

            versuche.fehlschlag(adresse)
            log.warning("Fehlgeschlagene Anmeldung von %s", adresse)
            flash("Passwort stimmt nicht.", "fehler")

        return render_template("anmeldung.html.j2", weiter=weiter)

    @app.post("/abmelden")
    def abmelden():
        csrf_pruefen()
        session.clear()
        flash("Abgemeldet.", "hinweis")
        return redirect(url_for("anmelden"))

    # -------------------------------------------------------------- Plan

    @app.get("/")
    def start():
        return redirect(url_for("monat_ansehen", monat=_aktueller_monat()))

    @app.get("/monat/<monat>")
    def monat_ansehen(monat: str):
        try:
            kalender.parse_monat(monat)
        except ValueError:
            abort(404)
        plan = plan_holen(monat)
        statistik = kat.planner.statistik(plan) if plan else None
        return render_template(
            "monat.html.j2",
            monat=monat,
            plan=plan,
            statistik=statistik,
            rezepte=kat.rezepte,
            config=ctx.config,
            tauschkarte=_tauschkarte(plan),
            voriger=_nachbarmonat(monat, -1),
            naechster=_nachbarmonat(monat, 1),
            heute=date.today(),
        )

    def _tauschkarte(plan: MonthPlan | None) -> dict[str, list[str]]:
        """Zu jedem Platz die Plätze, mit denen er tauschen darf.

        Die Monatsseite lässt Gerichte mit der Maus oder dem Finger auf einen
        anderen Tag ziehen. Welche Tage dabei in Frage kommen, entscheidet
        derselbe Code wie auf der Änderungsseite – die Karte wird hier
        einmal gerechnet und mitgeschickt, damit beim Ziehen sofort zu sehen
        ist, wohin es gehen kann. Der Server prüft es beim Ablegen trotzdem
        noch einmal: die Karte ist eine Anzeige, keine Zusage.
        """
        if plan is None:
            return {}
        return {
            f"{m.datum}_{m.slot}": [
                f"{p.datum}_{p.slot}" for p in kat.planner.tauschpartner(plan, m.datum, m.slot)
            ]
            for m in plan.alle_meals
        }

    @app.post("/monat/<monat>/erzeugen")
    def monat_erzeugen(monat: str):
        csrf_pruefen()
        try:
            kalender.parse_monat(monat)
        except ValueError:
            abort(404)
        with data.PLAN_SPERRE:
            if data.plan_vorhanden(ctx.config.plans_dir, monat):
                flash("Für diesen Monat gibt es bereits einen Plan.", "fehler")
                return redirect(url_for("monat_ansehen", monat=monat))
            try:
                plan = kat.planner.plan_monat(monat)
            except PlanungsFehler as exc:
                flash(f"Kein Plan möglich: {exc}", "fehler")
                return redirect(url_for("monat_ansehen", monat=monat))
            data.save_plan(plan, ctx.config.plans_dir)
        flash(f"Plan für {_monat_titel(monat)} erzeugt.", "erfolg")
        return redirect(url_for("monat_ansehen", monat=monat))

    @app.get("/monat/<monat>/woche/<freitag>/einkauf")
    def einkauf(monat: str, freitag: str):
        """Was für eine Woche eingekauft werden muss.

        Frischware und Vorratszutaten getrennt: die Frischware wird am
        Freitag gekauft, die Vorratszutaten kann man vorziehen. Beide
        Listen stehen hier zusammen, weil ohne Monatsmail niemand sonst
        die Vorratszutaten nennt.
        """
        plan = plan_holen(monat)
        if plan is None:
            abort(404)
        try:
            start = date.fromisoformat(freitag)
        except ValueError:
            abort(404)
        woche = plan.woche_ab(start) or plan.woche_fuer(start)
        if woche is None:
            abort(404)
        zeilen, eigen = shopping.lade_grundeinkauf(ctx.config)
        frisch = shopping.frischeliste(woche, kat.rezepte, ctx.zutaten, ctx.config, ctx.bestand)
        vorrat = shopping.wochen_pantry_liste(
            woche, kat.rezepte, ctx.zutaten, ctx.config, ctx.bestand
        )
        return render_template(
            "einkauf.html.j2",
            monat=monat,
            woche=woche,
            frisch=frisch,
            vorrat=vorrat,
            grundeinkauf=shopping.grundeinkauf_gruppen(zeilen),
            grundeinkauf_eigen=eigen,
            grundeinkauf_doppelt=shopping.grundeinkauf_doppelt(zeilen, frisch, vorrat),
            config=ctx.config,
        )

    @app.get("/monat/<monat>/<datum>/<slot>")
    def tauschen_zeigen(monat: str, datum: str, slot: str):
        plan = plan_holen(monat)
        if plan is None:
            abort(404)
        try:
            tag = date.fromisoformat(datum)
        except ValueError:
            abort(404)
        woche = plan.woche_fuer(tag)
        aktuell = woche.meal(slot) if woche else None
        if aktuell is None:
            abort(404)
        alternativen = kat.planner.alternativen(plan, tag, slot)
        # Die Merkliste wird bei jedem Aufruf frisch gelesen: sie ändert sich
        # beim Tauschen selbst, und zwei offene Tabs sollen nicht auseinander
        # laufen.
        vorgemerkt_slugs = katalog.lade_merkliste(ctx.config)
        moeglich = {r.slug for r in alternativen}
        vorgemerkt = [
            kat.rezepte[s] for s in vorgemerkt_slugs if s in moeglich and s in kat.rezepte
        ]
        vorgemerkt_hier_nicht = [
            kat.rezepte[s]
            for s in vorgemerkt_slugs
            if s not in moeglich and s in kat.rezepte and s != aktuell.recipe_slug
        ]
        return render_template(
            "tauschen.html.j2",
            monat=monat,
            meal=aktuell,
            rezept=kat.rezepte.get(aktuell.recipe_slug),
            alternativen=[r for r in alternativen if r.slug not in vorgemerkt_slugs],
            vorgemerkt=vorgemerkt,
            vorgemerkt_hier_nicht=vorgemerkt_hier_nicht,
            schon_vorgemerkt=aktuell.recipe_slug in vorgemerkt_slugs,
            partner=kat.planner.tauschpartner(plan, tag, slot),
            config=ctx.config,
        )

    @app.post("/monat/<monat>/<datum>/<slot>")
    def tauschen(monat: str, datum: str, slot: str):
        csrf_pruefen()
        try:
            tag = date.fromisoformat(datum)
        except ValueError:
            abort(404)
        neuer_slug = request.form.get("rezept", "")

        with data.PLAN_SPERRE:
            plan = plan_holen(monat)
            if plan is None:
                abort(404)
            bisher = finde_mahlzeit(plan, tag, slot)
            alter_slug = bisher.recipe_slug if bisher else ""
            try:
                geaendert = gericht_tauschen(plan, tag, slot, neuer_slug, kat.planner)
            except AenderungsFehler as exc:
                flash(str(exc), "fehler")
                return redirect(url_for("tauschen_zeigen", monat=monat, datum=datum, slot=slot))
            data.save_plan(geaendert, ctx.config.plans_dir)

        name = kat.rezepte[neuer_slug].name
        log.info("Gericht getauscht: %s %s -> %s", datum, slot, neuer_slug)

        # Was eingeplant wurde, ist von der Merkliste erledigt. Das bisherige
        # Gericht kommt nur auf die Liste, wenn das ausdrücklich angekreuzt
        # war – sonst sammelte sich dort alles an, was je getauscht wurde.
        nachsatz = ""
        if neuer_slug in katalog.lade_merkliste(ctx.config):
            katalog.vergessen(ctx.config, neuer_slug)
            nachsatz = " Von der Merkliste genommen."
        if request.form.get("merken") == "on" and alter_slug in kat.rezepte:
            try:
                katalog.merken(ctx.config, alter_slug)
            except KatalogFehler as exc:
                nachsatz += f" {exc}"
            else:
                nachsatz += f" {kat.rezepte[alter_slug].name} steht jetzt auf der Merkliste."

        flash(f"{slot} am {kalender.formatiere_kurz(tag)}: jetzt {name}.{nachsatz}", "erfolg")
        return redirect(url_for("monat_ansehen", monat=monat) + f"#slot-{datum}-{slot}")

    @app.post("/monat/<monat>/<datum>/<slot>/verschieben")
    def verschieben(monat: str, datum: str, slot: str):
        """Zwei Termine die Plätze tauschen lassen.

        Kein freies Verschieben: ein Slot dürfte dabei nicht leer bleiben.
        Deshalb wechseln immer zwei Gerichte miteinander, und beide müssen
        auf dem neuen Platz erlaubt sein.
        """
        csrf_pruefen()
        try:
            tag = date.fromisoformat(datum)
        except ValueError:
            abort(404)
        zurueck = redirect(url_for("tauschen_zeigen", monat=monat, datum=datum, slot=slot))

        ziel_datum_text, _, ziel_slot = (request.form.get("ziel") or "").partition("_")
        try:
            ziel_tag = date.fromisoformat(ziel_datum_text)
        except ValueError:
            flash("Kein gültiger Tag zum Tauschen ausgewählt.", "fehler")
            return zurueck

        with data.PLAN_SPERRE:
            plan = plan_holen(monat)
            if plan is None:
                abort(404)
            hier = finde_mahlzeit(plan, tag, slot)
            dort = finde_mahlzeit(plan, ziel_tag, ziel_slot)
            try:
                geaendert = plaetze_tauschen(plan, tag, slot, ziel_tag, ziel_slot, kat.planner)
            except AenderungsFehler as exc:
                flash(str(exc), "fehler")
                return zurueck
            data.save_plan(geaendert, ctx.config.plans_dir)

        log.info("Plätze getauscht: %s %s <-> %s %s", datum, slot, ziel_datum_text, ziel_slot)
        flash(
            f"Getauscht: {hier.recipe_name} ist jetzt am "
            f"{kalender.formatiere_kurz(ziel_tag)}, {dort.recipe_name} am "
            f"{kalender.formatiere_kurz(tag)}",
            "erfolg",
        )
        return redirect(url_for("monat_ansehen", monat=monat) + f"#slot-{datum}-{slot}")

    # ---------------------------------------------------------- Merkliste

    @app.get("/merkliste")
    def merkliste():
        slugs = katalog.lade_merkliste(ctx.config)
        return render_template(
            "merkliste.html.j2",
            gerichte=[kat.rezepte[s] for s in slugs if s in kat.rezepte],
            verschwunden=[s for s in slugs if s not in kat.rezepte],
            gestrichen=kat.gestrichen,
            config=ctx.config,
        )

    @app.post("/merkliste/<slug>/merken")
    def merkliste_merken(slug: str):
        csrf_pruefen()
        if slug not in kat.rezepte:
            abort(404)
        try:
            katalog.merken(ctx.config, slug)
        except KatalogFehler as exc:
            flash(str(exc), "fehler")
        else:
            flash(
                f"{kat.rezepte[slug].name} steht auf der Merkliste. "
                "Du findest es beim Ändern eines Tages ganz oben.",
                "erfolg",
            )
        return redirect(request.form.get("weiter") or url_for("merkliste"))

    @app.post("/merkliste/<slug>/vergessen")
    def merkliste_vergessen(slug: str):
        csrf_pruefen()
        katalog.vergessen(ctx.config, slug)
        name = kat.rezepte[slug].name if slug in kat.rezepte else slug
        flash(f"{name} ist nicht mehr vorgemerkt.", "hinweis")
        return redirect(request.form.get("weiter") or url_for("merkliste"))

    # ------------------------------------------------------- Grundeinkauf

    @app.route("/grundeinkauf", methods=["GET", "POST"])
    def grundeinkauf():
        """Was unabhängig vom Plan sowieso mit soll."""
        zeilen, eigen = shopping.lade_grundeinkauf(ctx.config)
        # Wer einmal gespeichert hat, kommt an die Grundnahrungsmittel sonst
        # nicht mehr heran – deshalb der Weg zurück zum Vorschlag. Er füllt
        # nur das Feld; gespeichert wird erst mit dem Knopf.
        vorschlag = request.args.get("vorschlag") == "1"
        if vorschlag:
            zeilen = list(shopping.STANDARD_GRUNDEINKAUF)
            eigen = False
        text = chr(10).join(zeilen)
        if request.method == "POST":
            csrf_pruefen()
            text = request.form.get("zeilen", "")
            try:
                zeilen = shopping.speichere_grundeinkauf(ctx.config, text)
            except shopping.EinkaufFehler as exc:
                flash(str(exc), "fehler")
            else:
                eigen = True
                text = chr(10).join(zeilen)
                flash(f"Grundeinkauf gespeichert: {len(zeilen)} Zeilen.", "erfolg")
        return render_template(
            "grundeinkauf.html.j2",
            text=text,
            gruppen=shopping.grundeinkauf_gruppen(zeilen),
            eigen=eigen,
            vorschlag=vorschlag,
            config=ctx.config,
        )

    # ---------------------------------------------------------- Rezepte

    def _zutaten_ansicht(rezept, variante: str | None):
        """Zutatenzeilen einer Variante, fertig formatiert."""
        zeilen = []
        for zeile in rezept.zutaten_fuer(variante):
            zutat = ctx.zutaten.get(zeile.item)
            zeilen.append(
                {
                    "menge": units.formatiere_rezept(zeile.qty, zeile.unit),
                    "name": zutat.name if zutat else zeile.item,
                    "abteilung": zutat.department if zutat else "?",
                    "optional": zeile.optional,
                    "hinweis": zeile.hinweis,
                }
            )
        return zeilen

    @app.get("/rezepte")
    def rezepte_liste():
        suche = (request.args.get("q") or "").strip().casefold()
        alle = sorted(kat.rezepte.values(), key=lambda r: r.name.lower())
        if suche:
            alle = [r for r in alle if suche in r.name.casefold() or suche in r.slug]
        return render_template(
            "rezepte.html.j2",
            rezepte=alle,
            gesamt=len(kat.rezepte),
            gestrichen=kat.gestrichen,
            eigene=kat.eigene,
            vorgemerkt=set(katalog.lade_merkliste(ctx.config)),
            abgelehnt=kat.planner.abgelehnt,
            suche=request.args.get("q") or "",
            config=ctx.config,
        )

    @app.route("/rezepte/neu", methods=["GET", "POST"])
    def rezept_neu():
        formular = {
            "name": "",
            "slots": [],
            "prep_minutes": "30",
            "base_servings": "4",
            "tags": "",
            "core": False,
            "zutaten": "",
            "schritte": "",
        }
        if request.method == "POST":
            csrf_pruefen()
            formular = {
                "name": request.form.get("name", ""),
                "slots": request.form.getlist("slots"),
                "prep_minutes": request.form.get("prep_minutes", "30"),
                "base_servings": request.form.get("base_servings", "4"),
                "tags": request.form.get("tags", ""),
                "core": request.form.get("core") == "on",
                "zutaten": request.form.get("zutaten", ""),
                "schritte": request.form.get("schritte", ""),
            }
            try:
                rezept = katalog.rezept_bauen(
                    name=formular["name"],
                    slots=formular["slots"],
                    zutaten_text=formular["zutaten"],
                    schritte_text=formular["schritte"],
                    zutaten=ctx.zutaten,
                    prep_minutes=int(formular["prep_minutes"] or 0),
                    base_servings=int(formular["base_servings"] or 0),
                    core=formular["core"],
                    tags=formular["tags"],
                )
                if rezept.slug in kat.rezepte:
                    raise KatalogFehler(
                        f"Es gibt schon ein Rezept mit dem Kurznamen {rezept.slug!r}."
                    )
                # Dieselbe Prüfung wie beim Laden der Stammdaten: ein Rezept,
                # das eine harte Regel bricht, kommt gar nicht erst ins Volume.
                fehler = nur_fehler(pruefe_rezept(rezept, ctx.zutaten, ctx.config))
                if fehler:
                    raise KatalogFehler("; ".join(f.nachricht for f in fehler))
                katalog.speichere_eigenes(ctx.config, rezept)
                kat.neu_laden()
            except (KatalogFehler, ValueError) as exc:
                flash(str(exc), "fehler")
            else:
                flash(f"{rezept.name} angelegt.", "erfolg")
                return redirect(url_for("rezept_ansehen", slug=rezept.slug))

        return render_template(
            "rezept_neu.html.j2",
            formular=formular,
            slots=list(ctx.config.slot_namen),
            zutaten=sorted(ctx.zutaten.values(), key=lambda z: z.name.lower()),
            config=ctx.config,
        )

    @app.get("/rezepte/<slug>")
    def rezept_ansehen(slug: str):
        rezept = kat.rezepte.get(slug)
        if rezept is None:
            abort(404)
        varianten = [
            {
                "name": "basis",
                "hinweis": "",
                "zutaten": _zutaten_ansicht(rezept, None),
                "steps": list(rezept.steps),
                "protein": rezept.protein_fuer(None),
            }
        ]
        for name in sorted(rezept.variants):
            varianten.append(
                {
                    "name": name,
                    "hinweis": rezept.variants[name].hinweis,
                    "zutaten": _zutaten_ansicht(rezept, name),
                    "steps": list(rezept.steps_fuer(name)),
                    "protein": rezept.protein_fuer(name),
                }
            )
        return render_template(
            "rezept.html.j2",
            rezept=rezept,
            varianten=varianten,
            ist_gestrichen=slug in kat.gestrichen,
            ist_eigenes=slug in kat.eigene,
            ist_vorgemerkt=slug in katalog.lade_merkliste(ctx.config),
            probleme=[i.nachricht for i in kat.planner.abgelehnt.get(slug, [])],
            config=ctx.config,
        )

    @app.post("/rezepte/<slug>/streichen")
    def rezept_streichen(slug: str):
        csrf_pruefen()
        if slug not in kat.rezepte:
            abort(404)
        katalog.streichen(ctx.config, slug)
        kat.neu_laden()
        log.info("Rezept gestrichen: %s", slug)
        flash(
            f"{kat.rezepte[slug].name} wird nicht mehr eingeplant. "
            "Bestehende Pläne bleiben, wie sie sind.",
            "erfolg",
        )
        return redirect(url_for("rezept_ansehen", slug=slug))

    @app.post("/rezepte/<slug>/aufnehmen")
    def rezept_aufnehmen(slug: str):
        csrf_pruefen()
        if slug not in kat.rezepte:
            abort(404)
        katalog.aufnehmen(ctx.config, slug)
        kat.neu_laden()
        flash(f"{kat.rezepte[slug].name} wird wieder eingeplant.", "erfolg")
        return redirect(url_for("rezept_ansehen", slug=slug))

    @app.post("/rezepte/<slug>/loeschen")
    def rezept_loeschen(slug: str):
        csrf_pruefen()
        if slug not in kat.eigene:
            # Rezepte aus dem Repo liegen schreibgeschützt im Image; für sie
            # ist Streichen der einzige Weg.
            abort(404)
        name = kat.rezepte[slug].name
        katalog.loesche_eigenes(ctx.config, slug)
        katalog.aufnehmen(ctx.config, slug)
        katalog.vergessen(ctx.config, slug)
        kat.neu_laden()
        log.info("Eigenes Rezept gelöscht: %s", slug)
        flash(f"{name} wurde gelöscht.", "erfolg")
        return redirect(url_for("rezepte_liste"))

    @app.errorhandler(404)
    def nicht_gefunden(_):
        return render_template("fehler.html.j2", code=404, text="Seite gibt es nicht."), 404

    @app.errorhandler(400)
    def falsche_anfrage(exc):
        text = getattr(exc, "description", "Anfrage nicht verstanden.")
        return render_template("fehler.html.j2", code=400, text=text), 400

    return app


def starte(ctx: Kontext, port: int | None = None) -> None:
    """Die Oberfläche mit einem richtigen WSGI-Server ausliefern.

    Flasks eingebauter Server ist ausdrücklich nicht für den Betrieb
    gedacht; waitress ist reines Python und läuft deshalb auch auf ARM
    ohne Übersetzen.
    """
    from waitress import serve as waitress_serve

    app = erstelle_app(ctx)
    p = port or web_port()
    log.info("Weboberfläche auf Port %d, gestartet %s", p, datetime.now().isoformat(" ", "seconds"))
    waitress_serve(app, host="0.0.0.0", port=p, threads=4, ident="Essensplaner")
