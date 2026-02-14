from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


NAV_ITEMS = [
    ("home", "h  Home"),
    ("projections", "p  Projections"),
    ("captain", "c  Captain"),
    ("trades", "t  Trades"),
    ("live", "l  Live Scores"),
    ("player", "s  Player Search"),
    ("dvp", "d  DVP Rankings"),
    ("rookies", "r  Rookies"),
    ("team", "m  My Team"),
    ("ai_chat", "a  AI Chat"),
    ("data", "x  Data & Settings"),
]


class Sidebar(Vertical):
    """Navigation sidebar with menu items for each dashboard screen."""

    DEFAULT_CSS = """
    Sidebar {
        dock: left;
        width: 28;
        background: $panel;
        border-right: solid $accent;
        padding: 0;
    }
    """

    def __init__(self, season: int = 2026, current_round: int = 1, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._season = season
        self._round = current_round

    def compose(self) -> ComposeResult:
        yield Static(
            " SuperCoach\n    AI Assistant",
            id="sidebar-title",
        )
        yield Static(
            f"  {self._season} | Round {self._round}",
            id="sidebar-info",
        )
        yield OptionList(
            *[Option(label, id=screen_id) for screen_id, label in NAV_ITEMS],
            id="nav-list",
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Navigate to the selected screen."""
        screen_id = event.option.id
        if screen_id:
            self.app.switch_screen(screen_id)
