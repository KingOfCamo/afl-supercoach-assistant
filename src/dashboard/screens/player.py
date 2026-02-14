from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static
from textual import work

from src.dashboard.widgets.sidebar import Sidebar


class PlayerScreen(Screen):
    """Player search and profile viewer."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar(id="sidebar")
            with VerticalScroll(id="content"):
                yield Static("Player Search", classes="screen-title")
                with Horizontal(classes="controls"):
                    yield Input(placeholder="Enter player name...", id="search-input")
                    yield Button("Search", id="btn-search", variant="primary")
                    yield Button("AI Analysis", id="btn-ai", variant="success")
                yield Static("", id="player-profile", classes="player-profile")
                yield Static("Recent Scores", classes="screen-title")
                yield DataTable(id="scores-table", cursor_type="row")
                yield Static("DFS Australia Stats", classes="screen-title")
                yield DataTable(id="dfs-table", cursor_type="row")
                yield Static("", id="ai-response", classes="ai-panel")
        yield Footer()

    def on_mount(self) -> None:
        scores_table = self.query_one("#scores-table", DataTable)
        scores_table.add_columns("Season", "Round", "Score", "Price", "Value")

        dfs_table = self.query_one("#dfs-table", DataTable)
        dfs_table.add_columns("Metric", "Value")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-search":
            self._search_player()
        elif event.button.id == "btn-ai":
            self._ask_ai()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            self._search_player()

    @work(thread=True)
    def _search_player(self) -> None:
        try:
            name = self.query_one("#search-input", Input).value.strip()
            if not name:
                return

            from sqlalchemy import desc, select

            from src.models.database import (
                DfsPlayerStats,
                Injury,
                Player,
                SupercoachScore,
                get_session,
            )

            session = get_session()
            try:
                player = session.execute(
                    select(Player).where(Player.name.ilike(f"%{name}%"))
                ).scalar_one_or_none()

                if player is None:
                    self.call_from_thread(
                        self.query_one("#player-profile", Static).update,
                        f"Player '{name}' not found.",
                    )
                    return

                # Build profile text
                injury = session.execute(
                    select(Injury).where(Injury.player_id == player.id)
                ).scalar_one_or_none()

                injury_text = (
                    f"  INJURED: {injury.injury_type} - Return: {injury.estimated_return}"
                    if injury
                    else ""
                )

                from src.analytics.projections import project_player
                from src.utils.config import get_config

                config = get_config()
                proj = project_player(player.id, config.current_round)
                proj_text = (
                    f"  Projection R{config.current_round}: {proj.projected_score:.0f} "
                    f"(floor {proj.floor:.0f}, ceiling {proj.ceiling:.0f})"
                    if proj
                    else ""
                )

                profile = (
                    f"{player.name} | {player.team} | {player.position or 'Unknown'}\n"
                    f"{injury_text}\n{proj_text}"
                )
                self.call_from_thread(
                    self.query_one("#player-profile", Static).update, profile
                )

                # Recent scores
                scores = (
                    session.execute(
                        select(SupercoachScore)
                        .where(SupercoachScore.player_id == player.id)
                        .order_by(desc(SupercoachScore.season), desc(SupercoachScore.round))
                        .limit(10)
                    )
                    .scalars()
                    .all()
                )

                score_rows = []
                for s in scores:
                    score_rows.append((
                        str(s.season),
                        str(s.round),
                        str(s.score) if s.score is not None else "DNP",
                        f"${s.price:,}" if s.price else "-",
                        f"{s.value:.1f}" if s.value else "-",
                    ))

                # DFS stats
                dfs = session.execute(
                    select(DfsPlayerStats)
                    .where(DfsPlayerStats.player_id == player.id)
                    .order_by(desc(DfsPlayerStats.season))
                    .limit(1)
                ).scalar_one_or_none()

                dfs_rows = []
                if dfs:
                    if dfs.salary:
                        dfs_rows.append(("Salary", f"${dfs.salary:,}"))
                    if dfs.sc_avg is not None:
                        dfs_rows.append(("SC Average", f"{dfs.sc_avg:.1f}"))
                    if dfs.ownership_pct is not None:
                        dfs_rows.append(("Ownership", f"{dfs.ownership_pct * 100:.1f}%"))
                    if dfs.cba_pct is not None:
                        dfs_rows.append(("CBA%", f"{dfs.cba_pct * 100:.1f}%"))
                    if dfs.points_per_minute is not None:
                        dfs_rows.append(("PPM", f"{dfs.points_per_minute:.3f}"))
                    if dfs.last_5_avg is not None:
                        dfs_rows.append(("Last 5 Avg", f"{dfs.last_5_avg:.1f}"))
                    if dfs.prev_year_avg is not None:
                        dfs_rows.append(("2024 Avg", f"{dfs.prev_year_avg:.1f}"))
                    if dfs.games_played is not None:
                        dfs_rows.append(("Games", str(dfs.games_played)))

            finally:
                session.close()

            def _fill() -> None:
                st = self.query_one("#scores-table", DataTable)
                st.clear()
                for row in score_rows:
                    st.add_row(*row)

                dt = self.query_one("#dfs-table", DataTable)
                dt.clear()
                for row in dfs_rows:
                    dt.add_row(*row)

            self.call_from_thread(_fill)

        except Exception as e:
            self.call_from_thread(self.notify, f"Error: {e}", severity="error")

    @work(thread=True)
    def _ask_ai(self) -> None:
        try:
            name = self.query_one("#search-input", Input).value.strip()
            if not name:
                return

            self.call_from_thread(
                self.query_one("#ai-response", Static).update,
                "Asking Claude to analyze player...",
            )
            from src.ai.advisor import SuperCoachAdvisor

            advisor = SuperCoachAdvisor()
            result = advisor.analyze_player(name)
            self.call_from_thread(self.query_one("#ai-response", Static).update, result)
        except Exception as e:
            self.call_from_thread(
                self.query_one("#ai-response", Static).update, f"AI Error: {e}"
            )
