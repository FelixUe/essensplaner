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

from . import data, kalender, shopping
from .editing import AenderungsFehler, gericht_tauschen
from .models import MonthPlan
from .planner import Planner, PlanungsFehler
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

    planner = Planner(ctx.rezepte, ctx.zutaten, ctx.config)
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
        statistik = planner.statistik(plan) if plan else None
        return render_template(
            "monat.html.j2",
            monat=monat,
            plan=plan,
            statistik=statistik,
            rezepte=ctx.rezepte,
            config=ctx.config,
            voriger=_nachbarmonat(monat, -1),
            naechster=_nachbarmonat(monat, 1),
            heute=date.today(),
        )

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
                plan = planner.plan_monat(monat)
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
        return render_template(
            "einkauf.html.j2",
            monat=monat,
            woche=woche,
            frisch=shopping.frischeliste(woche, ctx.rezepte, ctx.zutaten, ctx.config, ctx.bestand),
            vorrat=shopping.wochen_pantry_liste(
                woche, ctx.rezepte, ctx.zutaten, ctx.config, ctx.bestand
            ),
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
        alternativen = planner.alternativen(plan, tag, slot)
        return render_template(
            "tauschen.html.j2",
            monat=monat,
            meal=aktuell,
            rezept=ctx.rezepte.get(aktuell.recipe_slug),
            alternativen=alternativen,
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
            try:
                geaendert = gericht_tauschen(plan, tag, slot, neuer_slug, planner)
            except AenderungsFehler as exc:
                flash(str(exc), "fehler")
                return redirect(url_for("tauschen_zeigen", monat=monat, datum=datum, slot=slot))
            data.save_plan(geaendert, ctx.config.plans_dir)

        name = ctx.rezepte[neuer_slug].name
        log.info("Gericht getauscht: %s %s -> %s", datum, slot, neuer_slug)
        flash(f"{slot} am {kalender.formatiere_kurz(tag)}: jetzt {name}.", "erfolg")
        return redirect(url_for("monat_ansehen", monat=monat) + f"#slot-{datum}-{slot}")

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
