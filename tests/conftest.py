"""Gemeinsame Fixtures. Getestet wird gegen die echten Stammdaten im Repo –
genau die laufen später auf dem Server."""

from __future__ import annotations

from pathlib import Path

import pytest

from essensplaner import data
from essensplaner.config import load_config
from essensplaner.planner import Planner

REPO = Path(__file__).resolve().parents[1]

#: Monate quer über das Jahr, damit saisonale Rezepte und unterschiedlich
#: lange Monate (4 oder 5 Planungswochen) abgedeckt sind.
TEST_MONATE = (
    "2026-12",
    "2027-01",
    "2027-02",
    "2027-04",
    "2027-05",
    "2027-07",
    "2027-08",
    "2027-10",
)

#: Mehrere Seeds, damit nicht nur ein glücklicher Plan getestet wird.
TEST_SEEDS = (1, 7, 42, 1234)


@pytest.fixture(scope="session")
def config():
    return load_config(REPO / "config.yaml")


@pytest.fixture(scope="session")
def zutaten(config):
    return data.load_ingredients(config.ingredients_file)


@pytest.fixture(scope="session")
def rezepte(config):
    return data.load_recipes(config.recipes_dir)


@pytest.fixture(scope="session")
def planner(rezepte, zutaten, config):
    return Planner(rezepte, zutaten, config)


@pytest.fixture(scope="session")
def plan_cache():
    """Pläne werden pro Testlauf nur einmal erzeugt, nicht pro Test."""
    return {}


@pytest.fixture(scope="session")
def mach_plan(planner, plan_cache):
    def _mach(monat: str, seed: int = 42):
        schluessel = (monat, seed)
        if schluessel not in plan_cache:
            plan_cache[schluessel] = planner.plan_monat(monat, seed=seed)
        return plan_cache[schluessel]

    return _mach


@pytest.fixture
def plan(mach_plan):
    """Ein Standardplan für Tests, die nur irgendeinen Plan brauchen."""
    return mach_plan("2026-12", 42)


@pytest.fixture(autouse=True)
def _ohne_wochenablauf_umgebung(monkeypatch):
    """Kein Test soll versehentlich ntfy.sh oder einen Scanner erreichen."""
    for name in (
        "NTFY_TOPIC",
        "NTFY_SERVER",
        "NTFY_TOKEN",
        "ANGEBOTSSCANNER_URL",
        "ANGEBOTSSCANNER_AUSLOESER_TOKEN",
        "ESSENSPLANER_BASIS_URL",
    ):
        monkeypatch.delenv(name, raising=False)
