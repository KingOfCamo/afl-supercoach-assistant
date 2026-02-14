from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static
from textual import work

from src.dashboard.widgets.sidebar import Sidebar
from src.dashboard.widgets.stat_card import StatCard


class LiveScreen(Screen):
    """Live scoring tracker for the current round."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar(id="sidebar")
            with VerticalScroll(id="content"):
                yield Static("Live Scores", classes="screen-title")
                with Horizontal(classes="controls"):
                    yield Label("Round: ")
                    yield Input(value="1", id="round-input", type="integer")
                    yield Button("Refresh", id="btn-refresh", variant="primary")
                with Horizontal(classes="stat-grid"):
                    yield StatCard(title="Live Total", value="...", id="card-live-total")
                    yield StatCard(title="Projected", value="...", id="card-projected")
                    yield StatCard(title="Complete", value="...", id="card-complete")
                    yield StatCard(title="Captain", value="...", id="card-captain")
                yield DataTable(id="live-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self._setup_table()
        self._set_round_from_config()
        self._load_live()
        self.set_interval(60, self._load_live)

    def _set_round_from_config(self) -> None:
        from src.utils.config import get_config

        config = get_config()
        self.query_one("#round-input", Input).value = str(config.current_round)

    def _setup_table(self) -> None:
        table = self.query_one("#live-table", DataTable)
        table.add_columns("Slot", "Player", "Team", "Score", "Projected", "Status", "Opponent", "Role")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-refresh":
            self._load_live()

    @work(thread=True)
    def _load_live(self) -> None:
        try:
            round_num = int(self.query_one("#round-input", Input).value or "1")

            from src.analytics.live_scores import get_live_round

            summary = get_live_round(round_num)

            if not summary.players:
                self.call_from_thread(
                    self.notify, "No team imported yet.", severity="warning"
                )
                return

            self.call_from_thread(
                self.query_one("#card-live-total", StatCard).update_value,
                str(summary.total_live_score),
            )
            self.call_from_thread(
                self.query_one("#card-projected", StatCard).update_value,
                f"{summary.projected_total:.0f}",
            )
            self.call_from_thread(
                self.query_one("#card-complete", StatCard).update_value,
                f"{summary.games_complete}/{summary.games_complete + summary.games_in_progress + summary.games_upcoming}",
            )
            cap_text = f"{summary.captain_name} ({summary.captain_score})" if summary.captain_name else "N/A"
            self.call_from_thread(
                self.query_one("#card-captain", StatCard).update_value,
                cap_text,
            )

            rows = []
            for p in summary.players:
                role = "C" if p.is_captain else "VC" if p.is_vice_captain else "EMG" if p.is_emergency else ""
                score_str = str(p.live_score) if p.live_score is not None else "-"
                proj_str = f"{p.projected_final:.0f}" if p.projected_final else "-"

                rows.append((
                    p.position_slot,
                    p.player_name,
                    p.team,
                    score_str,
                    proj_str,
                    p.match_status,
                    p.opponent or "-",
                    role,
                ))

            def _fill() -> None:
                table = self.query_one("#live-table", DataTable)
                table.clear()
                for row in rows:
                    table.add_row(*row)

            self.call_from_thread(_fill)
        except Exception as e:
            self.call_from_thread(self.notify, f"Error: {e}", severity="error")
