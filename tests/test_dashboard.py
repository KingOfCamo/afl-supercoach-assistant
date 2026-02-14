from __future__ import annotations

"""Tests for the interactive Textual TUI dashboard."""

import pytest
from typer.testing import CliRunner

from src.cli.main import app

runner = CliRunner()


class TestDashboardImports:
    """Test that all dashboard modules import correctly."""

    def test_import_app(self):
        from src.dashboard.app import SuperCoachApp, run_dashboard

        assert SuperCoachApp is not None
        assert run_dashboard is not None

    def test_import_sidebar(self):
        from src.dashboard.widgets.sidebar import NAV_ITEMS, Sidebar

        assert Sidebar is not None
        assert len(NAV_ITEMS) == 11

    def test_import_stat_card(self):
        from src.dashboard.widgets.stat_card import StatCard

        assert StatCard is not None

    def test_import_player_table(self):
        from src.dashboard.widgets.player_table import PlayerTable

        assert PlayerTable is not None

    def test_import_home_screen(self):
        from src.dashboard.screens.home import HomeScreen

        assert HomeScreen is not None

    def test_import_projections_screen(self):
        from src.dashboard.screens.projections import ProjectionsScreen

        assert ProjectionsScreen is not None

    def test_import_captain_screen(self):
        from src.dashboard.screens.captain import CaptainScreen

        assert CaptainScreen is not None

    def test_import_trades_screen(self):
        from src.dashboard.screens.trades import TradesScreen

        assert TradesScreen is not None

    def test_import_live_screen(self):
        from src.dashboard.screens.live import LiveScreen

        assert LiveScreen is not None

    def test_import_player_screen(self):
        from src.dashboard.screens.player import PlayerScreen

        assert PlayerScreen is not None

    def test_import_dvp_screen(self):
        from src.dashboard.screens.dvp import DvpScreen

        assert DvpScreen is not None

    def test_import_rookies_screen(self):
        from src.dashboard.screens.rookies import RookiesScreen

        assert RookiesScreen is not None

    def test_import_team_screen(self):
        from src.dashboard.screens.team import TeamScreen

        assert TeamScreen is not None

    def test_import_ai_chat_screen(self):
        from src.dashboard.screens.ai_chat import AiChatScreen

        assert AiChatScreen is not None

    def test_import_data_screen(self):
        from src.dashboard.screens.data import DataScreen

        assert DataScreen is not None


class TestDashboardApp:
    """Test the main app configuration."""

    def test_app_has_all_screens(self):
        from src.dashboard.app import SuperCoachApp

        app = SuperCoachApp.__new__(SuperCoachApp)
        expected_screens = {
            "home", "projections", "captain", "trades", "live",
            "player", "dvp", "rookies", "team", "ai_chat", "data",
        }
        assert set(SuperCoachApp.SCREENS.keys()) == expected_screens

    def test_app_has_bindings(self):
        from src.dashboard.app import SuperCoachApp

        binding_keys = [b.key for b in SuperCoachApp.BINDINGS]
        assert "h" in binding_keys  # Home
        assert "p" in binding_keys  # Projections
        assert "c" in binding_keys  # Captain
        assert "q" in binding_keys  # Quit

    def test_app_css_path_exists(self):
        from src.dashboard.app import CSS_PATH

        assert CSS_PATH.exists()
        assert CSS_PATH.suffix == ".tcss"

    def test_nav_items_match_screens(self):
        from src.dashboard.app import SuperCoachApp
        from src.dashboard.widgets.sidebar import NAV_ITEMS

        screen_ids = set(SuperCoachApp.SCREENS.keys())
        nav_ids = {item[0] for item in NAV_ITEMS}
        assert nav_ids == screen_ids


class TestDashboardCLI:
    """Test the CLI entry point."""

    def test_help_shows_dashboard(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "dashboard" in result.output

    def test_dashboard_help(self):
        result = runner.invoke(app, ["dashboard", "--help"])
        assert result.exit_code == 0
        assert "Launch the interactive TUI dashboard" in result.output


class TestWidgets:
    """Test widget construction."""

    def test_stat_card_creation(self):
        from src.dashboard.widgets.stat_card import StatCard

        card = StatCard(title="Test", value="42")
        assert card._title == "Test"
        assert card._value == "42"

    def test_player_table_creation(self):
        from src.dashboard.widgets.player_table import PlayerTable

        table = PlayerTable(
            columns=[("name", "Name"), ("team", "Team")],
            show_search=True,
        )
        assert table._columns == [("name", "Name"), ("team", "Team")]
        assert table._show_search is True

    def test_sidebar_creation(self):
        from src.dashboard.widgets.sidebar import Sidebar

        sidebar = Sidebar(season=2026, current_round=5)
        assert sidebar._season == 2026
        assert sidebar._round == 5
