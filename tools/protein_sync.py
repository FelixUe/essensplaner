"""Proteinangaben der Rezepte mit der Nachrechnung abgleichen.

``protein_per_serving_g`` ist der Wert, mit dem der Planer arbeitet. Wer
Zutaten oder Mengen ändert, muss ihn nachziehen – dieses Werkzeug nimmt
die Arbeit ab und schreibt den aus ingredients.yaml gerechneten Wert in
die YAML-Dateien zurück. Es ersetzt nur die betroffenen Zahlen und lässt
Kommentare, Reihenfolge und Formatierung unangetastet.

    python tools/protein_sync.py            # nur anzeigen
    python tools/protein_sync.py --write    # Werte schreiben
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from essensplaner import data, nutrition  # noqa: E402
from essensplaner.config import load_config  # noqa: E402

BASIS_RE = re.compile(r"^(protein_per_serving_g:\s*)(-?[\d.]+)\s*$")
VARIANTE_RE = re.compile(r"^(\s{4}protein_per_serving_g:\s*)(-?[\d.]+)\s*$")
VARIANTE_NAME_RE = re.compile(r"^\s{2}([A-Za-z_][\w-]*):\s*$")


def gerundet(wert: float) -> int:
    return int(round(wert))


def sync_datei(pfad: Path, werte: dict[str | None, int], schreiben: bool) -> list[str]:
    """Die Proteinzeilen einer Rezeptdatei auf ``werte`` setzen.

    ``werte`` ist {None: basiswert, "protein": wert, "family": wert}.
    """
    zeilen = pfad.read_text(encoding="utf-8").splitlines(keepends=True)
    neu: list[str] = []
    aenderungen: list[str] = []
    in_variants = False
    aktuelle_variante: str | None = None

    for zeile in zeilen:
        if zeile.startswith("variants:"):
            in_variants = True
            neu.append(zeile)
            continue
        if in_variants:
            treffer = VARIANTE_NAME_RE.match(zeile.rstrip("\n"))
            if treffer:
                aktuelle_variante = treffer.group(1)

        m = BASIS_RE.match(zeile.rstrip("\n"))
        if m and not in_variants:
            soll = werte.get(None)
            if soll is not None and float(m.group(2)) != soll:
                aenderungen.append(f"  basis: {m.group(2)} -> {soll}")
                neu.append(f"{m.group(1)}{soll}\n")
                continue

        m = VARIANTE_RE.match(zeile.rstrip("\n"))
        if m and aktuelle_variante is not None:
            soll = werte.get(aktuelle_variante)
            if soll is not None and float(m.group(2)) != soll:
                aenderungen.append(f"  {aktuelle_variante}: {m.group(2)} -> {soll}")
                neu.append(f"{m.group(1)}{soll}\n")
                continue

        neu.append(zeile)

    if aenderungen and schreiben:
        pfad.write_text("".join(neu), encoding="utf-8")
    return aenderungen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Werte in die YAML-Dateien schreiben")
    args = parser.parse_args()

    config = load_config()
    zutaten = data.load_ingredients(config.ingredients_file)
    rezepte = data.load_recipes(config.recipes_dir)

    gesamt = 0
    for slug, rezept in sorted(rezepte.items()):
        werte: dict[str | None, int] = {
            None: gerundet(nutrition.protein_berechnet(rezept, zutaten, None))
        }
        for name in rezept.variants:
            werte[name] = gerundet(nutrition.protein_berechnet(rezept, zutaten, name))

        pfad = config.recipes_dir / f"{slug}.yaml"
        aenderungen = sync_datei(pfad, werte, args.write)
        if aenderungen:
            gesamt += len(aenderungen)
            print(f"{slug}:")
            for a in aenderungen:
                print(a)

    if not gesamt:
        print("Alle Proteinangaben stimmen mit der Nachrechnung überein.")
    elif args.write:
        print(f"\n{gesamt} Wert(e) geschrieben.")
    else:
        print(f"\n{gesamt} Abweichung(en). Mit --write übernehmen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
