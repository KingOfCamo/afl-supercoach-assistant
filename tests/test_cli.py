from __future__ import annotations

import os
import tempfile

import pytest
from typer.testing import CliRunner

from src.cli.main import app
from src.models.database import reset_engine
from src.utils.config import reset_config

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Use a temp database for each test to avoid polluting real data."""
    reset_config()
    reset_engine()

    db_path = str(tmp_path / "test_supercoach.db")
    monkeypatch.setenv("DATABASE_URL_OVERRIDE", db_path)

    # Patch get_config to use a temp DB
    from src.utils import config as config_module

    original_load = config_module.load_config

    def patched_load():
        cfg = original_load()
        cfg.database.path = db_path
        return cfg

    monkeypatch.setattr(config_module, "load_config", patched_load)
    monkeypatch.setattr(config_module, "_config", None)

    yield

    reset_engine()
    reset_config()


def test_help():
    """CLI should show help."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "AFL SuperCoach AI Assistant" in result.output


def test_db_init():
    """sc db init should create the database."""
    result = runner.invoke(app, ["db", "init"])
    assert result.exit_code == 0
    assert "Database initialized" in result.output


def test_team_show_empty():
    """sc team show should handle empty team gracefully."""
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["team", "show"])
    assert result.exit_code == 0
    assert "No team imported" in result.output


def test_player_not_found():
    """sc player should handle missing player."""
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["player", "nonexistentplayer12345"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_injuries_empty():
    """sc injuries should handle no data gracefully."""
    runner.invoke(app, ["db", "init"])
    result = runner.invoke(app, ["injuries"])
    assert result.exit_code == 0
    assert "No injury data" in result.output
