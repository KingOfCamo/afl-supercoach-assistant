from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static
from textual import work

from src.dashboard.widgets.sidebar import Sidebar


class DataScreen(Screen):
    """Data management: scraping, imports, ML training, DB status."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Sidebar(id="sidebar")
            with VerticalScroll(id="content"):
                yield Static("Data & Settings", classes="screen-title")

                # DB Status
                yield Static("Database Status", classes="screen-title")
                yield Static("Loading...", id="db-status")

                # Scraping
                yield Static("Scrape Data", classes="screen-title")
                with Vertical(classes="data-section"):
                    with Horizontal(classes="data-row"):
                        yield Label("Season: ")
                        yield Input(value="2026", id="scrape-season", type="integer")
                        yield Label("Round: ")
                        yield Input(placeholder="all", id="scrape-round")
                    with Horizontal(classes="data-row"):
                        yield Button("Squiggle Fixtures", id="btn-squiggle", variant="primary")
                        yield Button("FootyWire Scores", id="btn-footywire", variant="primary")
                        yield Button("FanFooty Scores", id="btn-fanfooty", variant="primary")
                    yield Static("", id="scrape-status")

                # Import
                yield Static("Import DFS Australia", classes="screen-title")
                with Vertical(classes="data-section"):
                    with Horizontal(classes="data-row"):
                        yield Input(placeholder="Path to .xlsx file...", id="dfs-path")
                        yield Label("Season: ")
                        yield Input(value="2026", id="dfs-season", type="integer")
                        yield Button("Import", id="btn-dfs-import", variant="primary")
                    yield Static("", id="import-status")

                # ML Model
                yield Static("ML Model", classes="screen-title")
                with Vertical(classes="data-section"):
                    with Horizontal(classes="data-row"):
                        yield Button("Train Model", id="btn-ml-train", variant="success")
                    yield Static("", id="ml-status")
                    yield DataTable(id="ml-features", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        features_table = self.query_one("#ml-features", DataTable)
        features_table.add_columns("Feature", "Importance")
        self._load_db_status()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-squiggle":
            self._scrape_squiggle()
        elif event.button.id == "btn-footywire":
            self._scrape_footywire()
        elif event.button.id == "btn-fanfooty":
            self._scrape_fanfooty()
        elif event.button.id == "btn-dfs-import":
            self._import_dfs()
        elif event.button.id == "btn-ml-train":
            self._train_ml()

    @work(thread=True)
    def _load_db_status(self) -> None:
        try:
            from sqlalchemy import func, select

            from src.models.database import (
                DfsPlayerStats,
                Fixture,
                MyTeamSlot,
                Player,
                SupercoachScore,
                get_session,
            )

            session = get_session()
            try:
                players = session.execute(select(func.count(Player.id))).scalar()
                scores = session.execute(select(func.count(SupercoachScore.id))).scalar()
                fixtures = session.execute(select(func.count(Fixture.id))).scalar()
                dfs = session.execute(select(func.count(DfsPlayerStats.id))).scalar()
                team = session.execute(select(func.count(MyTeamSlot.id))).scalar()
            finally:
                session.close()

            # Check if ML model exists
            from pathlib import Path

            from src.utils.config import get_config

            config = get_config()
            model_path = Path(config.database.path).parent / "ml_model.pkl"
            ml_exists = model_path.exists()

            status = (
                f"Players: {players} | Scores: {scores} | Fixtures: {fixtures}\n"
                f"DFS Stats: {dfs} | Team Slots: {team}\n"
                f"ML Model: {'Trained' if ml_exists else 'Not trained'}"
            )

            self.call_from_thread(
                self.query_one("#db-status", Static).update, status
            )

        except Exception as e:
            self.call_from_thread(
                self.query_one("#db-status", Static).update, f"Error: {e}"
            )

    @work(thread=True)
    def _scrape_squiggle(self) -> None:
        try:
            season = int(self.query_one("#scrape-season", Input).value or "2026")
            round_str = self.query_one("#scrape-round", Input).value.strip()
            round_num = int(round_str) if round_str else None

            self.call_from_thread(
                self.query_one("#scrape-status", Static).update,
                "Fetching Squiggle fixtures...",
            )

            from src.models.database import init_db
            from src.scrapers.squiggle import SquiggleScraper

            init_db()

            async def _run() -> int:
                scraper = SquiggleScraper()
                try:
                    return await scraper.scrape(season=season, round=round_num)
                finally:
                    await scraper.close()

            count = asyncio.run(_run())

            self.call_from_thread(
                self.query_one("#scrape-status", Static).update,
                f"Loaded {count} Squiggle fixtures.",
            )
            self._load_db_status()

        except Exception as e:
            self.call_from_thread(
                self.query_one("#scrape-status", Static).update, f"Error: {e}"
            )

    @work(thread=True)
    def _scrape_footywire(self) -> None:
        try:
            season = int(self.query_one("#scrape-season", Input).value or "2026")
            round_str = self.query_one("#scrape-round", Input).value.strip()
            round_num = int(round_str) if round_str else 1

            self.call_from_thread(
                self.query_one("#scrape-status", Static).update,
                f"Scraping FootyWire {season} R{round_num}...",
            )

            from src.models.database import init_db
            from src.scrapers.footywire import FootyWireScraper

            init_db()

            async def _run() -> int:
                scraper = FootyWireScraper()
                try:
                    return await scraper.scrape(season=season, round=round_num)
                finally:
                    await scraper.close()

            count = asyncio.run(_run())

            self.call_from_thread(
                self.query_one("#scrape-status", Static).update,
                f"Scraped {count} FootyWire records.",
            )
            self._load_db_status()

        except Exception as e:
            self.call_from_thread(
                self.query_one("#scrape-status", Static).update, f"Error: {e}"
            )

    @work(thread=True)
    def _scrape_fanfooty(self) -> None:
        try:
            season = int(self.query_one("#scrape-season", Input).value or "2026")
            round_str = self.query_one("#scrape-round", Input).value.strip()
            round_num = int(round_str) if round_str else 1

            self.call_from_thread(
                self.query_one("#scrape-status", Static).update,
                f"Scraping FanFooty {season} R{round_num}...",
            )

            from src.models.database import init_db
            from src.scrapers.fanfooty import FanFootyScraper

            init_db()

            async def _run() -> int:
                scraper = FanFootyScraper()
                try:
                    return await scraper.scrape(season=season, round=round_num)
                finally:
                    await scraper.close()

            count = asyncio.run(_run())

            self.call_from_thread(
                self.query_one("#scrape-status", Static).update,
                f"Scraped {count} FanFooty scores.",
            )
            self._load_db_status()

        except Exception as e:
            self.call_from_thread(
                self.query_one("#scrape-status", Static).update, f"Error: {e}"
            )

    @work(thread=True)
    def _import_dfs(self) -> None:
        try:
            file_path = self.query_one("#dfs-path", Input).value.strip()
            season = int(self.query_one("#dfs-season", Input).value or "2026")

            if not file_path:
                self.call_from_thread(
                    self.query_one("#import-status", Static).update,
                    "Enter a file path.",
                )
                return

            self.call_from_thread(
                self.query_one("#import-status", Static).update,
                f"Importing DFS Australia for {season}...",
            )

            from src.importers.dfs_australia import import_dfs_spreadsheet

            count = import_dfs_spreadsheet(file_path, season=season)

            self.call_from_thread(
                self.query_one("#import-status", Static).update,
                f"Imported {count} players from DFS Australia.",
            )
            self._load_db_status()

        except Exception as e:
            self.call_from_thread(
                self.query_one("#import-status", Static).update, f"Error: {e}"
            )

    @work(thread=True)
    def _train_ml(self) -> None:
        try:
            self.call_from_thread(
                self.query_one("#ml-status", Static).update,
                "Training ML model...",
            )

            from src.analytics.ml_model import train_model

            result = train_model()

            if "error" in result:
                self.call_from_thread(
                    self.query_one("#ml-status", Static).update,
                    result["error"],
                )
                return

            status = (
                f"Model trained!\n"
                f"Rounds: {result['rounds_used']} | Samples: {result['samples']}\n"
                f"Train R\u00b2: {result['train_r2']:.4f} | Test R\u00b2: {result['test_r2']:.4f}"
            )
            self.call_from_thread(
                self.query_one("#ml-status", Static).update, status
            )

            # Show feature importances
            if result.get("feature_importances"):

                def _fill_fi() -> None:
                    table = self.query_one("#ml-features", DataTable)
                    table.clear()
                    sorted_fi = sorted(
                        result["feature_importances"].items(),
                        key=lambda x: x[1],
                        reverse=True,
                    )
                    for name, imp in sorted_fi:
                        bar = "\u2588" * int(imp * 40)
                        table.add_row(name, f"{imp:.4f} {bar}")

                self.call_from_thread(_fill_fi)

        except Exception as e:
            self.call_from_thread(
                self.query_one("#ml-status", Static).update, f"Error: {e}"
            )
