from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static
from textual import work

from src.dashboard.widgets.sidebar import Sidebar


class CaptainScreen(Screen):
    """Captain and Vice-Captain rankings."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar(id="sidebar")
            with VerticalScroll(id="content"):
                yield Static("Captain Rankings", classes="screen-title")
                with Horizontal(classes="controls"):
                    yield Label("Round: ")
                    yield Input(value="1", id="round-input", type="integer")
                    yield Button("Load", id="btn-load", variant="primary")
                    yield Button("Ask AI", id="btn-ai", variant="success")
                yield DataTable(id="captain-table", cursor_type="row")
                yield Static("", id="ai-response", classes="ai-panel")
        yield Footer()

    def on_mount(self) -> None:
        self._setup_table()
        self._set_round_from_config()
        self._load_captains()

    def _set_round_from_config(self) -> None:
        from src.utils.config import get_config

        config = get_config()
        self.query_one("#round-input", Input).value = str(config.current_round)

    def _setup_table(self) -> None:
        table = self.query_one("#captain-table", DataTable)
        table.add_columns("#", "Player", "Team", "Pos", "Proj", "Floor", "Ceiling", "Consist.", "Score", "Opponent", "DVP")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-load":
            self._load_captains()
        elif event.button.id == "btn-ai":
            self._ask_ai()

    @work(thread=True)
    def _load_captains(self) -> None:
        try:
            round_num = int(self.query_one("#round-input", Input).value or "1")

            from src.analytics.captain import rank_captain_options

            candidates = rank_captain_options(round_num, top_n=10)

            def _fill() -> None:
                table = self.query_one("#captain-table", DataTable)
                table.clear()
                for i, c in enumerate(candidates, 1):
                    table.add_row(
                        str(i),
                        c.player_name,
                        c.team,
                        c.position or "-",
                        f"{c.projected_score:.0f}",
                        f"{c.floor:.0f}",
                        f"{c.ceiling:.0f}",
                        f"{c.consistency:.0%}",
                        f"{c.captain_score:.0f}",
                        c.opponent or "-",
                        str(c.dvp_rank) if c.dvp_rank else "-",
                    )
                if not candidates:
                    self.notify("No captain candidates. Import your team first.", severity="warning")

            self.call_from_thread(_fill)
        except Exception as e:
            self.call_from_thread(self.notify, f"Error: {e}", severity="error")

    @work(thread=True)
    def _ask_ai(self) -> None:
        try:
            round_num = int(self.query_one("#round-input", Input).value or "1")
            self.call_from_thread(
                self.query_one("#ai-response", Static).update,
                "Asking Claude for captain advice...",
            )
            from src.ai.advisor import SuperCoachAdvisor

            advisor = SuperCoachAdvisor()
            result = advisor.get_captain_advice(round_num)
            self.call_from_thread(self.query_one("#ai-response", Static).update, result)
        except Exception as e:
            self.call_from_thread(
                self.query_one("#ai-response", Static).update, f"AI Error: {e}"
            )
