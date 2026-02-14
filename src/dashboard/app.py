from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding

from src.dashboard.screens.ai_chat import AiChatScreen
from src.dashboard.screens.captain import CaptainScreen
from src.dashboard.screens.data import DataScreen
from src.dashboard.screens.dvp import DvpScreen
from src.dashboard.screens.home import HomeScreen
from src.dashboard.screens.live import LiveScreen
from src.dashboard.screens.player import PlayerScreen
from src.dashboard.screens.projections import ProjectionsScreen
from src.dashboard.screens.rookies import RookiesScreen
from src.dashboard.screens.team import TeamScreen
from src.dashboard.screens.trades import TradesScreen

CSS_PATH = Path(__file__).parent / "styles" / "dashboard.tcss"


class SuperCoachApp(App):
    """AFL SuperCoach AI Assistant — Interactive Dashboard."""

    TITLE = "SuperCoach AI Assistant"
    SUB_TITLE = "AFL Fantasy Dashboard"
    CSS_PATH = CSS_PATH

    SCREENS = {
        "home": HomeScreen,
        "projections": ProjectionsScreen,
        "captain": CaptainScreen,
        "trades": TradesScreen,
        "live": LiveScreen,
        "player": PlayerScreen,
        "dvp": DvpScreen,
        "rookies": RookiesScreen,
        "team": TeamScreen,
        "ai_chat": AiChatScreen,
        "data": DataScreen,
    }

    BINDINGS = [
        Binding("h", "switch_screen('home')", "Home", show=True),
        Binding("p", "switch_screen('projections')", "Projections", show=True),
        Binding("c", "switch_screen('captain')", "Captain", show=True),
        Binding("t", "switch_screen('trades')", "Trades", show=True),
        Binding("l", "switch_screen('live')", "Live", show=True),
        Binding("s", "switch_screen('player')", "Search", show=True),
        Binding("d", "switch_screen('dvp')", "DVP", show=True),
        Binding("r", "switch_screen('rookies')", "Rookies", show=False),
        Binding("m", "switch_screen('team')", "My Team", show=False),
        Binding("a", "switch_screen('ai_chat')", "AI Chat", show=False),
        Binding("x", "switch_screen('data')", "Data", show=False),
        Binding("q", "quit", "Quit", show=True),
    ]

    def on_mount(self) -> None:
        """Initialize the app: ensure DB exists and push home screen."""
        from src.models.database import init_db

        init_db()
        self.push_screen("home")

    def action_switch_screen(self, screen_name: str) -> None:
        """Navigate to the given screen, installing lazily if needed."""
        self.switch_screen(screen_name)


def run_dashboard() -> None:
    """Entry point for the dashboard."""
    app = SuperCoachApp()
    app.run()
