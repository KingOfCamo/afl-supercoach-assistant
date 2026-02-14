from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static, Switch, Label
from textual import work

from src.dashboard.widgets.sidebar import Sidebar


class ProjectionsScreen(Screen):
    """Score projections for upcoming round."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar(id="sidebar")
            with VerticalScroll(id="content"):
                yield Static("Score Projections", classes="screen-title")
                with Horizontal(classes="controls"):
                    yield Label("Round: ")
                    yield Input(value="1", id="round-input", type="integer")
                    yield Label("Team only: ")
                    yield Switch(value=True, id="team-toggle")
                    yield Button("Load", id="btn-load", variant="primary")
                    yield Button("Ask AI", id="btn-ai", variant="success")
                yield DataTable(id="proj-table", cursor_type="row")
                yield Static("", id="ai-response", classes="ai-panel")
        yield Footer()

    def on_mount(self) -> None:
        self._setup_table()
        self._set_round_from_config()
        self._load_projections()

    def _set_round_from_config(self) -> None:
        from src.utils.config import get_config

        config = get_config()
        self.query_one("#round-input", Input).value = str(config.current_round)

    def _setup_table(self) -> None:
        table = self.query_one("#proj-table", DataTable)
        table.add_columns("#", "Player", "Team", "Pos", "Projected", "Floor", "Ceiling", "DVP Adj", "Conf.", "Opponent")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-load":
            self._load_projections()
        elif event.button.id == "btn-ai":
            self._ask_ai()

    @work(thread=True)
    def _load_projections(self) -> None:
        try:
            round_num = int(self.query_one("#round-input", Input).value or "1")
            team_only = self.query_one("#team-toggle", Switch).value

            from src.analytics.projections import project_round

            results = project_round(round_num, team_only=team_only, save=False)

            def _fill() -> None:
                table = self.query_one("#proj-table", DataTable)
                table.clear()
                for i, r in enumerate(results[:50], 1):
                    dvp = f"{r.dvp_adjustment:+.0f}" if r.dvp_adjustment else "-"
                    table.add_row(
                        str(i),
                        r.player_name,
                        r.team,
                        r.position or "-",
                        f"{r.projected_score:.0f}",
                        f"{r.floor:.0f}",
                        f"{r.ceiling:.0f}",
                        dvp,
                        f"{r.confidence:.0%}",
                        r.opponent or "-",
                    )

            self.call_from_thread(_fill)
        except Exception as e:
            self.call_from_thread(self.notify, f"Error: {e}", severity="error")

    @work(thread=True)
    def _ask_ai(self) -> None:
        try:
            self.call_from_thread(
                self.query_one("#ai-response", Static).update,
                "Asking Claude...",
            )
            from src.ai.advisor import SuperCoachAdvisor

            advisor = SuperCoachAdvisor()
            result = advisor.get_weekly_advice()

            self.call_from_thread(
                self.query_one("#ai-response", Static).update,
                result,
            )
        except Exception as e:
            self.call_from_thread(
                self.query_one("#ai-response", Static).update,
                f"AI Error: {e}",
            )
