from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static
from textual import work

from src.dashboard.widgets.sidebar import Sidebar


class TeamScreen(Screen):
    """My SuperCoach team display and management."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar(id="sidebar")
            with VerticalScroll(id="content"):
                yield Static("My Team", classes="screen-title")
                yield DataTable(id="team-table", cursor_type="row")

                yield Static("Import Team", classes="screen-title")
                with Horizontal(classes="controls"):
                    yield Input(placeholder="Path to team CSV...", id="csv-input")
                    yield Button("Import", id="btn-import", variant="primary")
                yield Static("", id="import-status")

                yield Static("Bye Round Coverage", classes="screen-title")
                yield Static("", id="bye-info")
        yield Footer()

    def on_mount(self) -> None:
        self._setup_table()
        self._load_team()

    def _setup_table(self) -> None:
        table = self.query_one("#team-table", DataTable)
        table.add_columns("Slot", "Player", "Team", "Price", "Last Score", "Avg", "Role")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-import":
            self._import_team()

    @work(thread=True)
    def _load_team(self) -> None:
        try:
            from sqlalchemy import desc, select

            from src.models.database import MyTeamSlot, Player, SupercoachScore, get_session

            session = get_session()
            try:
                slots = (
                    session.execute(select(MyTeamSlot).order_by(MyTeamSlot.position_slot))
                    .scalars()
                    .all()
                )

                if not slots:
                    self.call_from_thread(
                        self.query_one("#import-status", Static).update,
                        "No team imported. Use the import section below.",
                    )
                    return

                rows = []
                for slot in slots:
                    player = session.get(Player, slot.player_id)
                    if not player:
                        continue

                    latest = session.execute(
                        select(SupercoachScore)
                        .where(SupercoachScore.player_id == player.id)
                        .order_by(desc(SupercoachScore.season), desc(SupercoachScore.round))
                        .limit(1)
                    ).scalar_one_or_none()

                    scores = (
                        session.execute(
                            select(SupercoachScore)
                            .where(SupercoachScore.player_id == player.id)
                            .order_by(desc(SupercoachScore.season), desc(SupercoachScore.round))
                            .limit(5)
                        )
                        .scalars()
                        .all()
                    )

                    valid = [s.score for s in scores if s.score is not None]
                    avg = sum(valid) / len(valid) if valid else 0

                    role = ""
                    if slot.is_captain:
                        role = "C"
                    elif slot.is_vice_captain:
                        role = "VC"
                    elif slot.is_emergency:
                        role = "EMG"

                    price_str = f"${latest.price:,}" if latest and latest.price else "-"
                    score_str = str(latest.score) if latest and latest.score is not None else "DNP"

                    rows.append((slot.position_slot, player.name, player.team, price_str, score_str, f"{avg:.0f}", role))

            finally:
                session.close()

            # Load bye info
            self._load_byes()

            def _fill() -> None:
                table = self.query_one("#team-table", DataTable)
                table.clear()
                for row in rows:
                    table.add_row(*row)

            self.call_from_thread(_fill)

        except Exception as e:
            self.call_from_thread(self.notify, f"Error: {e}", severity="error")

    @work(thread=True)
    def _load_byes(self) -> None:
        try:
            from sqlalchemy import select

            from src.models.database import Fixture, MyTeamSlot, Player, get_session
            from src.utils.config import get_config
            from src.utils.teams import normalize_team

            config = get_config()
            session = get_session()
            try:
                fixtures = session.execute(
                    select(Fixture).where(Fixture.season == config.season)
                ).scalars().all()

                if not fixtures:
                    self.call_from_thread(
                        self.query_one("#bye-info", Static).update,
                        "No fixture data. Run 'sc scrape squiggle' first.",
                    )
                    return

                round_teams: dict[int, set[str]] = {}
                for f in fixtures:
                    rnd = f.round
                    if rnd not in round_teams:
                        round_teams[rnd] = set()
                    round_teams[rnd].add(f.home_team)
                    round_teams[rnd].add(f.away_team)

                all_teams = set()
                for teams in round_teams.values():
                    all_teams.update(teams)

                bye_rounds: dict[int, set[str]] = {}
                for rnd, teams in sorted(round_teams.items()):
                    missing = all_teams - teams
                    if missing:
                        bye_rounds[rnd] = missing

                if not bye_rounds:
                    self.call_from_thread(
                        self.query_one("#bye-info", Static).update,
                        "No bye rounds found in the fixture.",
                    )
                    return

                slots = session.execute(select(MyTeamSlot)).scalars().all()
                lines = []

                for rnd, bye_teams in sorted(bye_rounds.items()):
                    affected = []
                    for slot in slots:
                        player = session.get(Player, slot.player_id)
                        if player and normalize_team(player.team) in bye_teams:
                            affected.append(player.name)

                    if affected:
                        lines.append(f"Round {rnd}: {len(affected)} on bye - {', '.join(affected)}")
                    else:
                        lines.append(f"Round {rnd}: No team players on bye")

                self.call_from_thread(
                    self.query_one("#bye-info", Static).update,
                    "\n".join(lines),
                )

            finally:
                session.close()

        except Exception as e:
            self.call_from_thread(
                self.query_one("#bye-info", Static).update, f"Error loading byes: {e}"
            )

    @work(thread=True)
    def _import_team(self) -> None:
        try:
            csv_path = self.query_one("#csv-input", Input).value.strip()
            if not csv_path:
                self.call_from_thread(
                    self.query_one("#import-status", Static).update,
                    "Enter a CSV file path.",
                )
                return

            import csv
            from pathlib import Path

            from sqlalchemy import select

            from src.models.database import MyTeamSlot, Player, get_session, init_db

            path = Path(csv_path)
            if not path.exists():
                self.call_from_thread(
                    self.query_one("#import-status", Static).update,
                    f"File not found: {csv_path}",
                )
                return

            init_db()
            session = get_session()
            try:
                for existing in session.execute(select(MyTeamSlot)).scalars().all():
                    session.delete(existing)

                count = 0
                with open(path, "r") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        player_name = row.get("player_name", "").strip()
                        if not player_name:
                            continue

                        player = session.execute(
                            select(Player).where(Player.name.ilike(f"%{player_name}%"))
                        ).scalar_one_or_none()

                        if player is None:
                            player = Player(name=player_name, team="Unknown")
                            session.add(player)
                            session.flush()

                        slot = MyTeamSlot(
                            player_id=player.id,
                            position_slot=row.get("position_slot", f"BENCH{count + 1}").strip(),
                            is_captain=row.get("is_captain", "false").strip().lower() == "true",
                            is_vice_captain=row.get("is_vice_captain", "false").strip().lower() == "true",
                        )
                        session.add(slot)
                        count += 1

                session.commit()
                self.call_from_thread(
                    self.query_one("#import-status", Static).update,
                    f"Imported {count} players!",
                )
            except Exception as e:
                session.rollback()
                self.call_from_thread(
                    self.query_one("#import-status", Static).update,
                    f"Import error: {e}",
                )
            finally:
                session.close()

            # Reload the team table
            self._load_team()

        except Exception as e:
            self.call_from_thread(
                self.query_one("#import-status", Static).update, f"Error: {e}"
            )
