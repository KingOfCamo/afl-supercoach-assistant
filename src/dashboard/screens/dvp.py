from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Select, Static
from textual import work

from src.dashboard.widgets.sidebar import Sidebar


POSITION_OPTIONS = [
    ("All", "All"),
    ("DEF", "DEF"),
    ("MID", "MID"),
    ("RUC", "RUC"),
    ("FWD", "FWD"),
]


class DvpScreen(Screen):
    """Difficulty vs Position rankings."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar(id="sidebar")
            with VerticalScroll(id="content"):
                yield Static("DVP Rankings", classes="screen-title")
                with Horizontal(classes="controls"):
                    yield Label("Round: ")
                    yield Input(value="1", id="round-input", type="integer")
                    yield Label("Position: ")
                    yield Select(
                        [(label, value) for label, value in POSITION_OPTIONS],
                        value="All",
                        id="pos-select",
                    )
                    yield Button("Load", id="btn-load", variant="primary")
                yield Static("", id="dvp-info")
                yield DataTable(id="dvp-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self._setup_table()
        self._set_round_from_config()
        self._load_dvp()

    def _set_round_from_config(self) -> None:
        from src.utils.config import get_config

        config = get_config()
        self.query_one("#round-input", Input).value = str(config.current_round)

    def _setup_table(self) -> None:
        table = self.query_one("#dvp-table", DataTable)
        table.add_columns("Rank", "Team", "Position", "Avg Pts Conceded", "Games", "Matchup")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-load":
            self._load_dvp()

    @work(thread=True)
    def _load_dvp(self) -> None:
        try:
            round_num = int(self.query_one("#round-input", Input).value or "1")
            pos_filter = self.query_one("#pos-select", Select).value

            from src.analytics.dvp import compute_dvp
            from src.utils.config import get_config

            config = get_config()
            entries = compute_dvp(config.season, round_num)

            if not entries:
                self.call_from_thread(
                    self.query_one("#dvp-info", Static).update,
                    "No DVP data available. Need completed fixtures and scores.",
                )
                return

            if pos_filter and pos_filter != "All":
                entries = [e for e in entries if e.position == pos_filter]

            rows = []
            for e in entries:
                matchup = "Easiest" if e.dvp_rank >= 16 else "Hardest" if e.dvp_rank <= 3 else ""
                rows.append((
                    str(e.dvp_rank),
                    e.team,
                    e.position,
                    f"{e.avg_points_conceded:.1f}",
                    str(e.games_sampled),
                    matchup,
                ))

            self.call_from_thread(
                self.query_one("#dvp-info", Static).update,
                f"Showing {len(entries)} entries for R{round_num}",
            )

            def _fill() -> None:
                table = self.query_one("#dvp-table", DataTable)
                table.clear()
                for row in rows:
                    table.add_row(*row)

            self.call_from_thread(_fill)

        except Exception as e:
            self.call_from_thread(self.notify, f"Error: {e}", severity="error")
