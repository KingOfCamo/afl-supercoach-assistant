from __future__ import annotations

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


class TestNewCommands:
    """Smoke tests for Phase 2a CLI commands."""

    def test_captain_empty_team(self):
        """sc captain should handle empty team gracefully."""
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["captain"])
        assert result.exit_code == 0
        assert "No captain candidates" in result.output

    def test_dvp_no_data(self):
        """sc dvp should handle missing data gracefully."""
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["dvp"])
        assert result.exit_code == 0
        assert "No DVP data" in result.output

    def test_projections_no_data(self):
        """sc projections should handle missing data gracefully."""
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["projections"])
        assert result.exit_code == 0
        assert "No projections" in result.output

    def test_trade_suggest_no_data(self):
        """sc trade suggest should handle gracefully."""
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["trade", "suggest"])
        assert result.exit_code == 0
        assert "No trades recommended" in result.output or "solid" in result.output

    def test_rookies_no_data(self):
        """sc rookies should handle missing data."""
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["rookies"])
        assert result.exit_code == 0
        assert "No rookies" in result.output

    def test_byes_no_fixtures(self):
        """sc byes should handle missing fixture data."""
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["byes"])
        assert result.exit_code == 0
        assert "No fixture data" in result.output

    def test_help_shows_new_commands(self):
        """All new commands should appear in help."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "captain" in result.output
        assert "compare" in result.output
        assert "dvp" in result.output
        assert "projections" in result.output
        assert "rookies" in result.output
        assert "byes" in result.output

    def test_scrape_help(self):
        """Scrape subcommands should include squiggle."""
        result = runner.invoke(app, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "squiggle" in result.output
        assert "footywire" in result.output

    def test_trade_help(self):
        """Trade subcommands should show suggest and analyze."""
        result = runner.invoke(app, ["trade", "--help"])
        assert result.exit_code == 0
        assert "suggest" in result.output
        assert "analyze" in result.output
