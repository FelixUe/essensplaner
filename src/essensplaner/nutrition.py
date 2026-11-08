"""Protein aus den Zutaten nachrechnen.

``protein_per_serving_g`` steht im Rezept und ist der verbindliche Wert, mit
dem geplant wird (so sieht es die Spec in Abschnitt 7 vor). Damit diese
Angabe nicht irgendwann von den Zutaten abdriftet, rechnet dieses Modul sie
aus den Stammdaten nach. Die Tests vergleichen beide Werte und schlagen bei
zu großer Abweichung fehl.
"""

from __future__ import annotations

from . import units
from .models import Ingredient, Recipe

#: Erlaubte Abweichung zwischen Rezeptangabe und Nachrechnung.
TOLERANZ = 0.25


def protein_einer_zeile(menge: float, einheit: str, zutat: Ingredient) -> float:
    """Gramm Protein einer Zutatenzeile."""
    if zutat.protein_per_100 <= 0:
        return 0.0
    if units.ist_kleinmenge(einheit):
        # EL/TL/Prise liefern kein nennenswertes Protein.
        return 0.0
    basis_menge, basis = units.in_basis(menge, einheit)
    if basis in ("g", "ml"):
        gramm = basis_menge
    else:
        # Stückeinheiten brauchen ein Gewicht, sonst ist keine Aussage möglich.
        if zutat.stk_gewicht_g is None:
            return 0.0
        gramm = basis_menge * zutat.stk_gewicht_g
    return gramm / 100.0 * zutat.protein_per_100


def protein_berechnet(
    rezept: Recipe, zutaten: dict[str, Ingredient], variante: str | None = None
) -> float:
    """Protein je Erwachsenenportion, aus den Zutaten gerechnet."""
    gesamt = 0.0
    for zeile in rezept.zutaten_fuer(variante):
        if zeile.optional:
            continue
        zutat = zutaten.get(zeile.item)
        if zutat is None:
            continue
        gesamt += protein_einer_zeile(zeile.qty, zeile.unit, zutat)
    if rezept.base_servings <= 0:
        return 0.0
    return gesamt / rezept.base_servings


def abweichung(
    rezept: Recipe, zutaten: dict[str, Ingredient], variante: str | None = None
) -> tuple[float, float, float]:
    """(Angabe, Nachrechnung, relative Abweichung) für eine Rezeptvariante."""
    angabe = rezept.protein_fuer(variante)
    gerechnet = protein_berechnet(rezept, zutaten, variante)
    if gerechnet <= 0:
        return angabe, gerechnet, 0.0 if angabe <= 0 else 1.0
    return angabe, gerechnet, abs(angabe - gerechnet) / gerechnet


def pruefe_angaben(
    rezepte: dict[str, Recipe], zutaten: dict[str, Ingredient], toleranz: float = TOLERANZ
) -> list[str]:
    """Alle Rezepte und Varianten nachrechnen und Abweichungen melden."""
    meldungen: list[str] = []
    for rezept in sorted(rezepte.values(), key=lambda r: r.slug):
        varianten: list[str | None] = [None, *sorted(rezept.variants)]
        for variante in varianten:
            angabe, gerechnet, delta = abweichung(rezept, zutaten, variante)
            if gerechnet <= 0:
                meldungen.append(
                    f"{rezept.slug} ({variante or 'basis'}): Protein lässt sich aus den "
                    "Zutaten nicht nachrechnen – protein_per_100 in ingredients.yaml fehlt"
                )
                continue
            if delta > toleranz:
                meldungen.append(
                    f"{rezept.slug} ({variante or 'basis'}): Angabe {angabe:.0f} g weicht "
                    f"{delta:.0%} von der Nachrechnung {gerechnet:.0f} g ab"
                )
    return meldungen
