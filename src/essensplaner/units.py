"""Einheiten zusammenfassen und lesbar ausgeben.

Die Einkaufsliste soll `150 g` und `0,2 kg` derselben Zutat addieren können,
ohne dass im Rezept auf eine Einheit geachtet werden muss. Alles, was sich
nicht umrechnen lässt (Stück, Bund, Dose, EL), bleibt getrennt stehen.
"""

from __future__ import annotations

import math

#: Umrechnung in eine Basiseinheit je Dimension.
_FAKTOREN: dict[str, tuple[str, float]] = {
    "g": ("g", 1.0),
    "gramm": ("g", 1.0),
    "kg": ("g", 1000.0),
    "ml": ("ml", 1.0),
    "l": ("ml", 1000.0),
    "liter": ("ml", 1000.0),
    "cl": ("ml", 10.0),
}

#: Einheiten, die als Stückzahl gelten und nie zusammengerechnet werden.
_STUECK = {
    "stk",
    "stück",
    "dose",
    "dosen",
    "glas",
    "bund",
    "packung",
    "pck",
    "becher",
    "scheibe",
    "scheiben",
    "zehe",
    "zehen",
    "würfel",
    "blatt",
    "rolle",
    "beutel",
}

#: Kleinmengen – werden in der Liste als Hinweis geführt, nicht hochgerechnet.
_PRISEN = {"el", "tl", "prise", "prisen", "msp", "spritzer", "etwas", "nach geschmack"}

_PLURAL = {
    "dose": "Dosen",
    "glas": "Gläser",
    "bund": "Bund",
    "packung": "Packungen",
    "becher": "Becher",
    "scheibe": "Scheiben",
    "zehe": "Zehen",
    "blatt": "Blätter",
    "rolle": "Rollen",
    "beutel": "Beutel",
    "stk": "Stk",
    "würfel": "Würfel",
}

#: Schreibweise für die Ausgabe. Intern wird kleingeschrieben gerechnet,
#: auf dem Einkaufszettel soll aber "1 Bund" und nicht "1 bund" stehen.
_SINGULAR = {
    "dose": "Dose",
    "glas": "Glas",
    "bund": "Bund",
    "packung": "Packung",
    "pck": "Packung",
    "becher": "Becher",
    "scheibe": "Scheibe",
    "zehe": "Zehe",
    "zehen": "Zehen",
    "blatt": "Blatt",
    "rolle": "Rolle",
    "beutel": "Beutel",
    "stk": "Stk",
    "stück": "Stk",
    "würfel": "Würfel",
    "el": "EL",
    "tl": "TL",
    "prise": "Prise",
    "prisen": "Prisen",
    "msp": "Msp",
    "spritzer": "Spritzer",
}


def normalisiere_einheit(einheit: str) -> str:
    return einheit.strip().lower()


def basis_einheit(einheit: str) -> str:
    """Die Einheit, in der intern gerechnet wird."""
    e = normalisiere_einheit(einheit)
    if e in _FAKTOREN:
        return _FAKTOREN[e][0]
    return e


def in_basis(menge: float, einheit: str) -> tuple[float, str]:
    """Eine Menge in ihre Basiseinheit umrechnen."""
    e = normalisiere_einheit(einheit)
    if e in _FAKTOREN:
        basis, faktor = _FAKTOREN[e]
        return menge * faktor, basis
    return menge, e


def ist_kleinmenge(einheit: str) -> bool:
    return normalisiere_einheit(einheit) in _PRISEN


def ist_stueck(einheit: str) -> bool:
    return normalisiere_einheit(einheit) in _STUECK


def kompatibel(a: str, b: str) -> bool:
    """Lassen sich zwei Einheiten verrechnen?"""
    return basis_einheit(a) == basis_einheit(b)


def _zahl(wert: float) -> str:
    """Zahl deutsch formatieren, ohne unnötige Nachkommastellen."""
    if abs(wert - round(wert)) < 0.01:
        return str(int(round(wert)))
    return f"{wert:.1f}".replace(".", ",")


def _einheit_anzeige(einheit: str) -> str:
    e = normalisiere_einheit(einheit)
    return _SINGULAR.get(e, einheit.strip())


def formatiere(menge: float, einheit: str) -> str:
    """Eine Menge lesbar ausgeben, inklusive Umstieg auf die größere Einheit."""
    e = normalisiere_einheit(einheit)
    if ist_kleinmenge(e):
        return f"{_zahl(menge)} {_einheit_anzeige(e)}"
    if e == "g" and menge >= 1000:
        return f"{_zahl(menge / 1000)} kg"
    if e == "ml" and menge >= 1000:
        return f"{_zahl(menge / 1000)} l"
    if e in _PLURAL and menge != 1:
        return f"{_zahl(menge)} {_PLURAL[e]}"
    return f"{_zahl(menge)} {_einheit_anzeige(e)}"


def formatiere_rezept(menge: float, einheit: str) -> str:
    """Eine auf Portionen hochgerechnete Rezeptmenge küchentauglich ausgeben.

    Die Skalierung auf krumme Portionszahlen erzeugt Werte wie 0,375 Bund Petersilie.
    In der Küche wiegt niemand 0,4 Bund ab – daraus wird "etwas Petersilie".
    """
    e = basis_einheit(einheit)
    if e in ("g", "ml"):
        if menge < 100:
            gerundet = round(menge / 5) * 5
        else:
            gerundet = round(menge / 10) * 10
        if gerundet <= 0:
            return "etwas"
        return formatiere(float(gerundet), e)

    # Stück- und Löffelmengen in halben Schritten, darunter unbestimmt.
    halbe = round(menge * 2) / 2
    if halbe < 0.5:
        return "etwas"
    return formatiere(halbe, e)


def runde_einkauf(menge: float, einheit: str) -> float:
    """Eine Frischmenge auf eine Menge runden, die man im Laden kaufen kann."""
    e = basis_einheit(einheit)
    if ist_kleinmenge(e):
        return menge
    if ist_stueck(e):
        return float(math.ceil(menge))
    if e == "g":
        if menge <= 100:
            return float(math.ceil(menge / 10) * 10)
        return float(math.ceil(menge / 50) * 50)
    if e == "ml":
        if menge <= 250:
            return float(math.ceil(menge / 50) * 50)
        return float(math.ceil(menge / 100) * 100)
    return float(math.ceil(menge))


#: Grobe Umrechnung von Löffelmengen, damit die Vorratsliste Packungen
#: ausrechnen kann. Für den Einkauf genau genug: ob 38 EL Öl nun 570 oder
#: 600 ml sind, ändert nichts an der einen Flasche, die gekauft wird.
_KLEINMENGE_ML: dict[str, float] = {
    "el": 15.0,
    "tl": 5.0,
    "prise": 0.5,
    "prisen": 0.5,
    "msp": 0.5,
    "spritzer": 2.0,
}


def kleinmenge_faktor(einheit: str) -> float | None:
    """Milliliter bzw. Gramm je Löffel – None, wenn nicht umrechenbar."""
    return _KLEINMENGE_ML.get(normalisiere_einheit(einheit))


def packung_text(anzahl: float, pack_qty: float, pack_unit: str) -> str:
    """'2 Dosen' oder '2 × 500 g' – was im Regal steht."""
    if abs(pack_qty - 1.0) < 1e-9:
        return formatiere(anzahl, pack_unit)
    return f"{_zahl(anzahl)} × {formatiere(pack_qty, pack_unit)}"


def packungen(menge: float, einheit: str, pack_qty: float, pack_unit: str) -> float | None:
    """Wie viele Packungen für diese Menge nötig sind, aufgerundet.

    None, wenn Menge und Packungsgröße nicht in derselben Einheit stehen.
    """
    if pack_qty <= 0:
        return None
    menge_basis, basis = in_basis(menge, einheit)
    pack_basis, pack_b = in_basis(pack_qty, pack_unit)
    if basis != pack_b or pack_basis <= 0:
        return None
    return float(math.ceil(menge_basis / pack_basis - 1e-9))
