"""Die Kommandozeile – das ist, was Coolify als Scheduled Task aufruft."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from essensplaner.cli import cli

from .conftest import REPO


@pytest.fixture
def run(tmp_path, monkeypatch):
    """CLI mit Plan-Ordner in tmp_path, damit nichts ins Repo geschrieben wird."""
    monkeypatch.setenv("ESSENSPLANER_PLANS_DIR", str(tmp_path / "plans"))
    monkeypatch.delenv("SMTP_HOST", raising=False)
    runner = CliRunner()

    def _run(*args: str):
        return runner.invoke(cli, ["--config", str(REPO / "config.yaml"), *args])

    return _run


def test_validate_laeuft_sauber_durch(run):
    ergebnis = run("validate")
    assert ergebnis.exit_code == 0, ergebnis.output
    assert "Alles in Ordnung" in ergebnis.output


def test_validate_zeigt_die_slotdeckung(run):
    ergebnis = run("validate")
    for slot in ("FR", "SA", "SO", "MO", "MI"):
        assert f"{slot}:" in ergebnis.output


def test_generate_month_dry_run(run):
    ergebnis = run("generate-month", "2026-12", "--seed", "3", "--dry-run")
    assert ergebnis.exit_code == 0, ergebnis.output
    assert "Plan gespeichert" in ergebnis.output
    assert "dry-run" in ergebnis.output
    assert "Kernrezepte" in ergebnis.output


def test_generate_month_ohne_smtp_schlaegt_fehl_wenn_gesendet_werden_soll(run):
    ergebnis = run("generate-month", "2026-12", "--seed", "3")
    assert ergebnis.exit_code == 1
    assert "SMTP" in ergebnis.output


def test_generate_month_mit_kaputtem_monat(run):
    ergebnis = run("generate-month", "Dezember", "--dry-run")
    assert ergebnis.exit_code == 1
    assert "JJJJ-MM" in ergebnis.output or "Monat" in ergebnis.output


def test_show_zeigt_den_plan(run):
    run("generate-month", "2026-12", "--seed", "3", "--dry-run")
    ergebnis = run("show", "2026-12")
    assert ergebnis.exit_code == 0, ergebnis.output
    assert "Dezember 2026" in ergebnis.output
    assert "Woche" in ergebnis.output


def test_show_ohne_plan_meldet_das(run):
    ergebnis = run("show", "2099-01")
    assert ergebnis.exit_code == 1
    assert "Kein Plan" in ergebnis.output


def test_send_weekly_dry_run(run):
    run("generate-month", "2026-12", "--seed", "3", "--dry-run")
    ergebnis = run("send-weekly", "--date", "2026-12-04", "--dry-run")
    assert ergebnis.exit_code == 0, ergebnis.output
    assert "Gerendert" in ergebnis.output
    assert "Empfänger" in ergebnis.output


def test_send_weekly_mit_kaputtem_datum(run):
    ergebnis = run("send-weekly", "--date", "04.12.2026", "--dry-run")
    assert ergebnis.exit_code == 1
    assert "JJJJ-MM-TT" in ergebnis.output


def test_shopping_zeigt_beide_listen(run):
    run("generate-month", "2026-12", "--seed", "3", "--dry-run")
    ergebnis = run("shopping", "2026-12")
    assert ergebnis.exit_code == 0, ergebnis.output
    assert "Vorratsliste" in ergebnis.output
    assert "Frischeliste" in ergebnis.output


def test_shopping_fuer_eine_woche(run):
    run("generate-month", "2026-12", "--seed", "3", "--dry-run")
    ergebnis = run("shopping", "2026-12", "--week", "2026-12-04")
    assert ergebnis.exit_code == 0, ergebnis.output
    assert "Frischeliste" in ergebnis.output
    assert "Vorratsliste" not in ergebnis.output


def test_preview_schreibt_html_und_text(run, tmp_path):
    ziel = tmp_path / "vorschau.html"
    ergebnis = run("preview", "weekly", "--monat", "2026-12", "--out", str(ziel))
    assert ergebnis.exit_code == 0, ergebnis.output
    assert ziel.is_file()
    assert ziel.with_suffix(".txt").is_file()
    assert "<!DOCTYPE html>" in ziel.read_text(encoding="utf-8")


def test_preview_monthly(run, tmp_path):
    ziel = tmp_path / "monat.html"
    ergebnis = run("preview", "monthly", "--monat", "2026-12", "--out", str(ziel))
    assert ergebnis.exit_code == 0, ergebnis.output
    assert "Vorratsliste" in ziel.read_text(encoding="utf-8")


def test_serve_einmal_laeuft_ohne_fehler(run):
    ergebnis = run("serve", "--once")
    assert ergebnis.exit_code == 0, ergebnis.output


def test_hilfe_nennt_alle_unterbefehle(run):
    ergebnis = run("--help")
    for befehl in (
        "validate",
        "generate-month",
        "send-weekly",
        "show",
        "shopping",
        "preview",
        "serve",
    ):
        assert befehl in ergebnis.output


def test_fehlende_konfigurationsdatei_wird_gemeldet(tmp_path):
    runner = CliRunner()
    ergebnis = runner.invoke(cli, ["--config", str(tmp_path / "weg.yaml"), "validate"])
    assert ergebnis.exit_code == 1
    assert "nicht gefunden" in ergebnis.output
