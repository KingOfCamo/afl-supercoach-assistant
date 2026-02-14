from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static
from textual import work

from src.dashboard.widgets.sidebar import Sidebar


class RookiesScreen(Screen):
    """Top underpriced rookies and cash cows."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar(id="sidebar")
            with VerticalScroll(id="content"):
                yield Static("Rookies & Cash Cows", classes="screen-title")
                with Horizontal(classes="controls"):
                    yield Label("Max Price: $")
                    yield Input(value="300000", id="price-input", type="integer")
                    yield Label("Top: ")
                    yield Input(value="20", id="top-input", type="integer")
                    yield Button("Load", id="btn-load", variant="primary")
                yield DataTable(id="rookies-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self._setup_table()
        self._load_rookies()

    def _setup_table(self) -> None:
        table = self.query_one("#rookies-table", DataTable)
        table.add_columns("#", "Player", "Team", "Pos", "Price", "SC Avg", "Projected", "Value", "Own%", "VFL Avg")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-load":
            self._load_rookies()

    @work(thread=True)
    def _load_rookies(self) -> None:
        try:
            max_price = int(self.query_one("#price-input", Input).value or "300000")
            top_n = int(self.query_one("#top-input", Input).value or "20")

            from sqlalchemy import desc, select

            from src.analytics.projections import project_player
            from src.models.database import DfsPlayerStats, Player, get_session
            from src.utils.config import get_config

            config = get_config()
            session = get_session()
            try:
                cheap_players = session.execute(
                    select(DfsPlayerStats, Player)
                    .join(Player, DfsPlayerStats.player_id == Player.id)
                    .where(
                        DfsPlayerStats.salary.isnot(None),
                        DfsPlayerStats.salary <= max_price,
                        DfsPlayerStats.salary > 100000,
                        Player.is_active == True,  # noqa: E712
                    )
                    .order_by(desc(DfsPlayerStats.sc_avg))
                ).all()
            finally:
                session.close()

            rows_data = []
            for dfs, player in cheap_players:
                proj = project_player(player.id, config.current_round)
                projected = proj.projected_score if proj else 0
                value = (projected / (dfs.salary / 100000)) if dfs.salary and projected else 0

                rows_data.append({
                    "name": player.name,
                    "team": player.team,
                    "position": player.position or "-",
                    "salary": dfs.salary,
                    "sc_avg": dfs.sc_avg or 0,
                    "projected": projected,
                    "value": value,
                    "ownership": dfs.ownership_pct,
                    "vfl_avg": dfs.vfl_avg,
                })

            rows_data.sort(key=lambda x: x["value"], reverse=True)

            def _fill() -> None:
                table = self.query_one("#rookies-table", DataTable)
                table.clear()
                for i, r in enumerate(rows_data[:top_n], 1):
                    own = f"{r['ownership'] * 100:.1f}%" if r["ownership"] else "-"
                    vfl = f"{r['vfl_avg']:.0f}" if r["vfl_avg"] else "-"
                    table.add_row(
                        str(i),
                        r["name"],
                        r["team"],
                        r["position"],
                        f"${r['salary']:,}",
                        f"{r['sc_avg']:.0f}" if r["sc_avg"] else "-",
                        f"{r['projected']:.0f}" if r["projected"] else "-",
                        f"{r['value']:.1f}",
                        own,
                        vfl,
                    )

            self.call_from_thread(_fill)

        except Exception as e:
            self.call_from_thread(self.notify, f"Error: {e}", severity="error")
