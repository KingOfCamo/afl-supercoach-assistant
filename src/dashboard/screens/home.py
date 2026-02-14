from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static
from textual import work

from src.dashboard.widgets.sidebar import Sidebar
from src.dashboard.widgets.stat_card import StatCard


class HomeScreen(Screen):
    """Dashboard home screen with overview stats and team summary."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar(id="sidebar")
            with VerticalScroll(id="content"):
                yield Static("Dashboard Overview", classes="screen-title")
                with Horizontal(classes="stat-grid"):
                    yield StatCard(title="Round", value="...", id="card-round")
                    yield StatCard(title="Trades Left", value="...", id="card-trades")
                    yield StatCard(title="Proj. Total", value="...", id="card-projected")
                with Horizontal(classes="stat-grid"):
                    yield StatCard(title="Top Captain", value="...", id="card-captain")
                    yield StatCard(title="Injuries", value="...", id="card-injuries")
                    yield StatCard(title="Last Round", value="...", id="card-last-round")
                yield Static("Your Team", classes="screen-title")
                yield DataTable(id="team-table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self._setup_table()
        self._load_data()

    def _setup_table(self) -> None:
        table = self.query_one("#team-table", DataTable)
        table.add_columns("Slot", "Player", "Team", "Pos", "Projected", "Role")

    @work(thread=True)
    def _load_data(self) -> None:
        try:
            from src.utils.config import get_config

            config = get_config()
            round_num = config.current_round

            self.call_from_thread(
                self.query_one("#card-round", StatCard).update_value,
                f"R{round_num}",
            )
            self.call_from_thread(
                self.query_one("#card-trades", StatCard).update_value,
                str(config.trades_remaining),
            )

            # Projected total
            from src.analytics.projections import project_round

            results = project_round(round_num, team_only=True, save=False)
            total = sum(r.projected_score for r in results)
            self.call_from_thread(
                self.query_one("#card-projected", StatCard).update_value,
                f"{total:.0f}",
            )

            # Top captain
            from src.analytics.captain import rank_captain_options

            captains = rank_captain_options(round_num, top_n=1)
            cap_text = captains[0].player_name if captains else "N/A"
            self.call_from_thread(
                self.query_one("#card-captain", StatCard).update_value,
                cap_text,
            )

            # Injury count for team
            from sqlalchemy import select

            from src.models.database import Injury, MyTeamSlot, get_session

            session = get_session()
            try:
                team_ids = set(
                    session.execute(select(MyTeamSlot.player_id)).scalars().all()
                )
                injury_count = (
                    session.execute(
                        select(Injury).where(Injury.player_id.in_(team_ids))
                    )
                    .scalars()
                    .all()
                )
                self.call_from_thread(
                    self.query_one("#card-injuries", StatCard).update_value,
                    str(len(injury_count)),
                )
            finally:
                session.close()

            # Last round summary
            from src.analytics.live_scores import get_round_summary

            if round_num > 1:
                summary = get_round_summary(round_num - 1)
                last_total = summary["total_score"] if summary else 0
            else:
                last_total = 0
            self.call_from_thread(
                self.query_one("#card-last-round", StatCard).update_value,
                str(last_total) if last_total else "N/A",
            )

            # Team table
            rows = []
            for r in results:
                rows.append((
                    "",
                    r.player_name,
                    r.team,
                    r.position or "-",
                    f"{r.projected_score:.0f}",
                    "",
                ))

            def _fill_table() -> None:
                table = self.query_one("#team-table", DataTable)
                table.clear()
                for row in rows:
                    table.add_row(*row)

            self.call_from_thread(_fill_table)

        except Exception as e:
            self.call_from_thread(
                self.notify,
                f"Error loading data: {e}",
                severity="error",
            )
