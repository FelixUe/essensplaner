#!/usr/bin/env python3
"""Weboberfläche zum Entwickeln: mit Reload und auf Wegwerf-Plänen.

``essensplaner serve`` ist der Dauerbetrieb: waitress, dazu der Scheduler im
selben Prozess, auf den echten Plänen. Zum Entwickeln taugt das schlecht —
jede Änderung braucht einen Neustart, der Scheduler erzeugt nebenbei Pläne,
und wer in der Oberfläche etwas ausprobiert, schreibt in die Dateien, die
der Server später ausliefert.

Dieses Skript startet dieselbe Flask-App, aber:

* **mit Reload** – auch auf ``config.yaml`` und die Rezept-YAMLs, nicht nur
  auf den Python-Code. Gerade hier steckt das Verhalten in den Daten.
* **ohne Scheduler** – es werden keine Pläne erzeugt und keine Mails
  verschickt, auch nicht versehentlich.
* **auf einer Kopie der Pläne** unter ``.preview/plans/`` (gitignored). Die
  echten Pläne bleiben unberührt; ``--neu`` wirft die Kopie weg und legt sie
  frisch an.
* **auf 127.0.0.1**, nicht auf allen Schnittstellen, und mit
  ``ESSENSPLANER_WEB_UNSICHER=true``. Ohne das ist das Sitzungs-Cookie
  secure-only, kommt über ``http://`` nie an und die Anmeldung scheitert
  wortlos.

Der Port ist bewusst nicht 8080: so kann daneben eine echte Instanz laufen.

Aufruf::

    python tools/devserver.py                  # http://127.0.0.1:5001, Passwort "preview"
    python tools/devserver.py --neu            # Plankopie vorher zurücksetzen
    python tools/devserver.py --echte-plaene   # auf plans/ statt auf der Kopie
    python tools/devserver.py --port 5002
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SANDBOX = REPO / ".preview" / "plans"


def _plankopie(neu: bool) -> Path:
    """Die echten Pläne einmal nach ``.preview/plans/`` spiegeln."""
    if neu and SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    if not SANDBOX.exists():
        SANDBOX.mkdir(parents=True)
        quelle = REPO / "plans"
        kopiert = 0
        for datei in sorted(quelle.glob("*.json")):
            shutil.copy2(datei, SANDBOX / datei.name)
            kopiert += 1
        print(f"Plankopie angelegt: {kopiert} Datei(en) nach {SANDBOX}")
    return SANDBOX


def _beobachtete_dateien() -> list[str]:
    """Daten, bei deren Änderung neu geladen werden soll.

    Der Reloader von Werkzeug sieht von sich aus nur importierte Module. Das
    Verhalten dieses Projekts steckt aber zur Hälfte in YAML – ohne diese
    Liste zeigt die Oberfläche nach einer Rezeptänderung weiter den alten
    Stand, und man sucht den Fehler im Code.
    """
    dateien = [REPO / "config.yaml", REPO / "data" / "ingredients.yaml"]
    dateien += sorted((REPO / "data" / "recipes").glob("*.yaml"))
    return [str(p) for p in dateien if p.exists()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=5001, help="Standard: 5001")
    parser.add_argument(
        "--passwort",
        default=os.environ.get("ESSENSPLANER_WEB_PASSWORT") or "preview",
        help='Anmeldung an der Oberfläche. Standard: "preview"',
    )
    parser.add_argument(
        "--echte-plaene",
        action="store_true",
        help="auf plans/ arbeiten statt auf der Kopie – Änderungen sind dann echt",
    )
    parser.add_argument("--neu", action="store_true", help="die Plankopie vorher zurücksetzen")
    args = parser.parse_args()

    # Muss vor erstelle_app() stehen: die App liest beides beim Bauen aus der
    # Umgebung, nicht bei jedem Aufruf.
    os.environ["ESSENSPLANER_WEB_UNSICHER"] = "true"
    os.environ.setdefault("ESSENSPLANER_WEB_SECRET", "nur-fuer-die-entwicklung")

    if args.echte_plaene:
        print("ACHTUNG: Änderungen gehen in die echten Pläne unter plans/.")
    else:
        os.environ["ESSENSPLANER_PLANS_DIR"] = str(_plankopie(args.neu))

    from essensplaner.config import load_config
    from essensplaner.runner import Kontext
    from essensplaner.web import erstelle_app

    config = load_config(None)
    app = erstelle_app(Kontext.laden(config), passwort=args.passwort)

    beobachtet = _beobachtete_dateien()
    print(f"Oberfläche: http://127.0.0.1:{args.port}  (Passwort: {args.passwort})")
    print(f"Reload auch auf {len(beobachtet)} Datendatei(en). Kein Scheduler, keine Mails.")

    app.run(
        host="127.0.0.1",
        port=args.port,
        debug=True,
        use_reloader=True,
        extra_files=beobachtet,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
