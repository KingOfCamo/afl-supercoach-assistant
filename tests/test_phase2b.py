from __future__ import annotations

"""Tests for Phase 2b: ML model, live scores, FanFooty scraper, CLI commands."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from src.cli.main import app
from src.models.database import (
    Base,
    DfsPlayerStats,
    Fixture,
    MyTeamSlot,
    Player,
    SupercoachScore,
)

runner = CliRunner()


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)

    def _enable_fk(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(engine, "connect", _enable_fk)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def sample_data(db_session):
    """Create sample player data with scores and fixtures."""
    player = Player(name="Test Player", team="Melbourne", position="MID")
    db_session.add(player)
    db_session.flush()

    # Add 5 rounds of scores
    for r in range(1, 6):
        score = SupercoachScore(
            player_id=player.id,
            season=2026,
            round=r,
            score=90 + r * 5,  # 95, 100, 105, 110, 115
            price=500000 + r * 5000,
            breakeven=100,
        )
        db_session.add(score)

    # Add DFS data
    dfs = DfsPlayerStats(
        player_id=player.id,
        season=2026,
        salary=600000,
        sc_avg=105.0,
        prev_year_avg=98.0,
        last_5_avg=110.0,
        ownership_pct=0.15,
        cba_pct=0.6,
        points_per_minute=1.1,
        games_played=5,
    )
    db_session.add(dfs)

    # Add fixtures
    fixture = Fixture(
        season=2026,
        round=6,
        home_team="Melbourne",
        away_team="Collingwood",
        venue="MCG",
        is_complete=False,
        squiggle_id=99001,
    )
    db_session.add(fixture)

    # Mark rounds 1-5 as complete
    for r in range(1, 6):
        f = Fixture(
            season=2026,
            round=r,
            home_team="Melbourne",
            away_team="Collingwood",
            venue="MCG",
            is_complete=True,
            squiggle_id=99000 + r + 1,
        )
        db_session.add(f)

    # Add team slot
    slot = MyTeamSlot(
        player_id=player.id,
        position_slot="MID1",
        is_captain=True,
    )
    db_session.add(slot)

    db_session.commit()
    return player


class TestMLModel:
    """Test ML model module."""

    def test_feature_names_defined(self):
        from src.analytics.ml_model import FEATURE_NAMES

        assert len(FEATURE_NAMES) == 15
        assert "rolling_3_avg" in FEATURE_NAMES
        assert "dvp_rank" in FEATURE_NAMES
        assert "is_home" in FEATURE_NAMES

    def test_features_to_array(self):
        import numpy as np
        from src.analytics.ml_model import FEATURE_NAMES, _features_to_array

        features = {name: float(i) for i, name in enumerate(FEATURE_NAMES)}
        arr = _features_to_array(features)
        assert isinstance(arr, np.ndarray)
        assert len(arr) == 15
        assert arr[0] == 0.0  # first feature

    def test_model_version(self):
        from src.analytics.ml_model import MODEL_VERSION

        assert MODEL_VERSION == "gbr_v1"

    def test_min_rounds_constant(self):
        from src.analytics.ml_model import MIN_ROUNDS_TO_TRAIN

        assert MIN_ROUNDS_TO_TRAIN == 3

    def test_ml_projection_result_dataclass(self):
        from src.analytics.ml_model import MLProjectionResult

        result = MLProjectionResult(
            player_id=1,
            player_name="Test",
            team="Test",
            position="MID",
            projected_score=105.0,
            floor=73.5,
            ceiling=147.0,
            confidence=0.8,
            opponent="Collingwood",
            dvp_rank=15,
            method="ml_gbr_v1",
        )
        assert result.projected_score == 105.0
        assert result.method == "ml_gbr_v1"
        assert result.confidence == 0.8


class TestLiveScores:
    """Test live scoring module."""

    def test_live_player_score_dataclass(self):
        from src.analytics.live_scores import LivePlayerScore

        lps = LivePlayerScore(
            player_id=1,
            player_name="Test Player",
            team="Melbourne",
            position="MID",
            position_slot="MID1",
            is_captain=True,
            is_vice_captain=False,
            is_emergency=False,
            live_score=95,
            projected_final=110.0,
            match_status="in_progress",
            opponent="Collingwood",
        )
        assert lps.live_score == 95
        assert lps.is_captain is True
        assert lps.match_status == "in_progress"

    def test_live_round_summary_defaults(self):
        from src.analytics.live_scores import LiveRoundSummary

        summary = LiveRoundSummary(round_num=1, season=2026)
        assert summary.total_live_score == 0
        assert summary.projected_total == 0.0
        assert summary.players == []
        assert summary.games_complete == 0

    def test_live_round_summary_with_data(self):
        from src.analytics.live_scores import LivePlayerScore, LiveRoundSummary

        p1 = LivePlayerScore(
            player_id=1,
            player_name="Player 1",
            team="Melbourne",
            position="MID",
            position_slot="MID1",
            is_captain=True,
            is_vice_captain=False,
            is_emergency=False,
            live_score=120,
            projected_final=120.0,
            match_status="complete",
            opponent="Collingwood",
        )
        summary = LiveRoundSummary(
            round_num=5,
            season=2026,
            players=[p1],
            total_live_score=240,
            projected_total=240.0,
            games_complete=1,
        )
        assert summary.total_live_score == 240  # captain doubles
        assert summary.projected_total == 240.0


class TestFanFootyScraper:
    """Test FanFooty scraper module."""

    def test_scraper_class_exists(self):
        from src.scrapers.fanfooty import FanFootyScraper

        # Just verify the class can be imported
        assert FanFootyScraper is not None

    def test_parse_player_row_valid(self):
        from unittest.mock import MagicMock
        from src.scrapers.fanfooty import FanFootyScraper

        scraper = FanFootyScraper.__new__(FanFootyScraper)

        # Create mock cells
        cell1 = MagicMock()
        cell1.get_text.return_value = "Caleb Serong"
        cell2 = MagicMock()
        cell2.get_text.return_value = "Fremantle"
        cell3 = MagicMock()
        cell3.get_text.return_value = "125"

        result = scraper._parse_player_row([cell1, cell2, cell3])
        assert result is not None
        assert result[0] == "Caleb Serong"
        assert result[1] == "Fremantle"
        assert result[2] == 125

    def test_parse_player_row_invalid_score(self):
        from unittest.mock import MagicMock
        from src.scrapers.fanfooty import FanFootyScraper

        scraper = FanFootyScraper.__new__(FanFootyScraper)

        cell1 = MagicMock()
        cell1.get_text.return_value = "Some Header"
        cell2 = MagicMock()
        cell2.get_text.return_value = "Team"
        cell3 = MagicMock()
        cell3.get_text.return_value = "Score"  # Not a number

        result = scraper._parse_player_row([cell1, cell2, cell3])
        assert result is None

    def test_parse_player_row_header(self):
        from unittest.mock import MagicMock
        from src.scrapers.fanfooty import FanFootyScraper

        scraper = FanFootyScraper.__new__(FanFootyScraper)

        cell1 = MagicMock()
        cell1.get_text.return_value = "Player"
        cell2 = MagicMock()
        cell2.get_text.return_value = "Team"
        cell3 = MagicMock()
        cell3.get_text.return_value = "100"

        result = scraper._parse_player_row([cell1, cell2, cell3])
        assert result is None  # Header row should be skipped


class TestSquiggleLadder:
    """Test Squiggle ladder fetching."""

    def test_squiggle_scraper_has_fetch_ladder(self):
        """Verify fetch_ladder method exists on SquiggleScraper."""
        from src.scrapers.squiggle import SquiggleScraper

        assert hasattr(SquiggleScraper, "fetch_ladder")


class TestCLIPhase2b:
    """Smoke tests for Phase 2b CLI commands."""

    @pytest.fixture(autouse=True)
    def isolated_db(self, tmp_path, monkeypatch):
        """Use a temp database for each test."""
        from src.models.database import reset_engine
        from src.utils.config import reset_config

        reset_config()
        reset_engine()

        db_path = str(tmp_path / "test_supercoach.db")
        monkeypatch.setenv("DATABASE_URL_OVERRIDE", db_path)

        from src.utils import config as config_module

        original_load = config_module.load_config

        def patched_load():
            cfg = original_load()
            cfg.database.path = db_path
            return cfg

        monkeypatch.setattr(config_module, "load_config", patched_load)
        monkeypatch.setattr(config_module, "_config", None)

        yield

        reset_engine()
        reset_config()

    def test_ml_train_no_data(self):
        """sc ml train should handle no data gracefully."""
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["ml", "train"])
        assert result.exit_code == 0
        assert "Need at least" in result.output or "Insufficient" in result.output

    def test_ml_projections_no_data(self):
        """sc ml projections should handle missing data."""
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["ml", "projections"])
        assert result.exit_code == 0
        assert "No projections" in result.output

    def test_live_no_team(self):
        """sc live should handle no team."""
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["live"])
        assert result.exit_code == 0
        assert "No team" in result.output

    def test_round_summary_no_team(self):
        """sc round-summary should handle no team."""
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["round-summary"])
        assert result.exit_code == 0
        assert "No team" in result.output or "No score" in result.output

    def test_help_shows_new_commands(self):
        """All new Phase 2b commands should appear in help."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "live" in result.output
        assert "round-summary" in result.output
        assert "ladder" in result.output
        assert "ml" in result.output

    def test_ml_help(self):
        """ML subcommands should show train and projections."""
        result = runner.invoke(app, ["ml", "--help"])
        assert result.exit_code == 0
        assert "train" in result.output
        assert "projections" in result.output

    def test_scrape_help_shows_fanfooty(self):
        """Scrape should include fanfooty option."""
        result = runner.invoke(app, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "fanfooty" in result.output
        assert "squiggle" in result.output
        assert "footywire" in result.output
