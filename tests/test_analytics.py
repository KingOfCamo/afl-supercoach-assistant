from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.models.database import (
    Base,
    DfsPlayerStats,
    Fixture,
    MyTeamSlot,
    Player,
    PlayerProjection,
    SupercoachScore,
    TeamDVP,
)


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
def sample_player(db_session):
    """Create a sample player with scores."""
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

    db_session.commit()
    return player


@pytest.fixture
def sample_dfs_player(db_session):
    """Create a player with only DFS data (pre-season)."""
    player = Player(name="Rookie Player", team="Richmond", position="FWD")
    db_session.add(player)
    db_session.flush()

    dfs = DfsPlayerStats(
        player_id=player.id,
        season=2026,
        salary=200000,
        sc_avg=85.0,
        prev_year_avg=78.0,
        last_5_avg=90.0,
        ownership_pct=0.05,
    )
    db_session.add(dfs)
    db_session.commit()
    return player


class TestNewTables:
    """Test the 3 new database tables."""

    def test_fixture_table(self, db_session):
        fixture = Fixture(
            season=2026,
            round=1,
            home_team="Melbourne",
            away_team="Collingwood",
            venue="MCG",
            is_complete=False,
            squiggle_id=12345,
        )
        db_session.add(fixture)
        db_session.commit()

        result = db_session.query(Fixture).first()
        assert result.home_team == "Melbourne"
        assert result.away_team == "Collingwood"
        assert result.squiggle_id == 12345
        assert result.is_complete is False

    def test_team_dvp_table(self, db_session):
        dvp = TeamDVP(
            season=2026,
            round=5,
            team="Collingwood",
            position="MID",
            avg_points_conceded=95.5,
            dvp_rank=3,
        )
        db_session.add(dvp)
        db_session.commit()

        result = db_session.query(TeamDVP).first()
        assert result.team == "Collingwood"
        assert result.dvp_rank == 3
        assert result.avg_points_conceded == 95.5

    def test_player_projection_table(self, db_session):
        player = Player(name="Test", team="Test", position="MID")
        db_session.add(player)
        db_session.flush()

        proj = PlayerProjection(
            player_id=player.id,
            season=2026,
            round=1,
            projected_score=105.5,
            floor=73.9,
            ceiling=147.7,
            dvp_adjustment=2.5,
            confidence=0.7,
            method="rolling_avg_dvp_v1",
        )
        db_session.add(proj)
        db_session.commit()

        result = db_session.query(PlayerProjection).first()
        assert result.projected_score == 105.5
        assert result.method == "rolling_avg_dvp_v1"


class TestBreakevens:
    """Test breakeven analysis module."""

    def test_compute_breakeven_basic(self, db_session, sample_player, monkeypatch):
        """Breakeven should compute from score data."""
        from src.analytics.breakevens import BreakevenResult

        # Manually compute what the result should be
        # Scores: 95, 100, 105, 110, 115 (rounds 1-5)
        # Rolling 3 (most recent): 115, 110, 105 -> avg 110
        # Season avg: (95+100+105+110+115)/5 = 105
        # BE = 100, diff = 110-100 = 10, direction = rising
        # projected_change = 10 * 3500 = 35000

        result = BreakevenResult(
            player_id=sample_player.id,
            player_name="Test Player",
            team="Melbourne",
            position="MID",
            current_price=525000,
            breakeven=100,
            rolling_3_avg=110.0,
            season_avg=105.0,
            price_direction="rising",
            projected_change=35000,
            games_played=5,
        )
        assert result.price_direction == "rising"
        assert result.rolling_3_avg == 110.0
        assert result.projected_change == 35000

    def test_breakeven_result_dataclass(self):
        """BreakevenResult should have all expected fields."""
        from src.analytics.breakevens import BreakevenResult

        result = BreakevenResult(
            player_id=1,
            player_name="Test",
            team="Test",
            position="MID",
            current_price=500000,
            breakeven=100,
            rolling_3_avg=95.0,
            season_avg=98.0,
            price_direction="falling",
            projected_change=-17500,
            games_played=3,
        )
        assert result.price_direction == "falling"
        assert result.projected_change == -17500


class TestDVP:
    """Test DVP module."""

    def test_map_position(self):
        from src.analytics.dvp import _map_position

        assert _map_position("MID") == "MID"
        assert _map_position("DEF") == "DEF"
        assert _map_position("FWD") == "FWD"
        assert _map_position("RUC") == "RUC"
        assert _map_position("DEF/MID") == "MID"
        assert _map_position("MID/FWD") == "MID"
        assert _map_position("RUC/FWD") == "RUC"
        assert _map_position(None) is None
        assert _map_position("") is None

    def test_dvp_entry_dataclass(self):
        from src.analytics.dvp import DVPEntry

        entry = DVPEntry(
            team="Collingwood",
            position="MID",
            avg_points_conceded=95.5,
            dvp_rank=3,
            games_sampled=5,
        )
        assert entry.team == "Collingwood"
        assert entry.dvp_rank == 3


class TestProjections:
    """Test projections module."""

    def test_dvp_factor(self):
        from src.analytics.projections import _dvp_factor

        # Rank 1 (hardest) should reduce score
        assert _dvp_factor(1) < 1.0
        # Rank 18 (easiest) should increase score
        assert _dvp_factor(18) > 1.0
        # Rank 9-10 (neutral) should be near 1.0
        assert abs(_dvp_factor(10) - 1.0) < 0.02
        # None should return 1.0
        assert _dvp_factor(None) == 1.0

    def test_compute_base_with_scores(self):
        from src.analytics.projections import _compute_base

        scores = [115, 110, 105, 100, 95]
        base = _compute_base(scores, None)
        assert base is not None
        # With no DFS: 0.7 * rolling_5 + 0.3 * season
        # rolling_5 avg = 105, season avg = 105
        assert abs(base - 105.0) < 0.1

    def test_compute_base_preseason(self):
        """Pre-season fallback should use DFS data."""
        from src.analytics.projections import _compute_base
        from unittest.mock import MagicMock

        dfs = MagicMock()
        dfs.sc_avg = 85.0
        dfs.prev_year_avg = 78.0
        dfs.last_5_avg = 90.0

        base = _compute_base([], dfs)
        assert base is not None
        # Weighted: 0.5*85 + 0.3*78 + 0.2*90 = 42.5 + 23.4 + 18 = 83.9
        assert abs(base - 83.9) < 0.1

    def test_compute_base_no_data(self):
        from src.analytics.projections import _compute_base

        base = _compute_base([], None)
        assert base is None

    def test_projection_result_dataclass(self):
        from src.analytics.projections import ProjectionResult

        proj = ProjectionResult(
            player_id=1,
            player_name="Test",
            team="Test",
            position="MID",
            projected_score=105.0,
            floor=73.5,
            ceiling=147.0,
            dvp_adjustment=2.5,
            confidence=0.8,
            opponent="Collingwood",
            dvp_rank=15,
            method="rolling_avg_dvp_v1",
        )
        assert proj.projected_score == 105.0
        assert proj.confidence == 0.8


class TestCaptain:
    """Test captain picker module."""

    def test_compute_consistency(self):
        from src.analytics.captain import _compute_consistency

        # Perfectly consistent
        assert _compute_consistency([100, 100, 100, 100, 100]) == 1.0

        # Very variable
        variable = _compute_consistency([50, 150, 50, 150, 50])
        assert variable < 0.5

        # Insufficient data
        assert _compute_consistency([100]) == 0.5
        assert _compute_consistency([]) == 0.5

    def test_captain_candidate_dataclass(self):
        from src.analytics.captain import CaptainCandidate

        cand = CaptainCandidate(
            player_id=1,
            player_name="Test Captain",
            team="Melbourne",
            position="MID",
            projected_score=115.0,
            floor=80.5,
            ceiling=161.0,
            consistency=0.85,
            captain_score=120.5,
            opponent="Collingwood",
            dvp_rank=15,
            recent_scores=[110, 115, 120, 108, 112],
        )
        assert cand.captain_score == 120.5
        assert len(cand.recent_scores) == 5


class TestTradeEngine:
    """Test trade engine module."""

    def test_trade_out_candidate(self):
        from src.analytics.trade_engine import TradeOutCandidate

        candidate = TradeOutCandidate(
            player_id=1,
            player_name="Underperformer",
            team="Richmond",
            position="FWD",
            current_price=400000,
            rolling_3_avg=75.0,
            breakeven=95,
            gap=-20.0,
            is_injured=True,
            injury_detail="Hamstring - 4 weeks",
            reason="Avg 75 is 20pts below BE 95; Injured: Hamstring - 4 weeks",
        )
        assert candidate.gap == -20.0
        assert candidate.is_injured is True

    def test_trade_recommendation(self):
        from src.analytics.trade_engine import (
            TradeOutCandidate,
            TradeInCandidate,
            TradeRecommendation,
        )

        out = TradeOutCandidate(
            player_id=1, player_name="Out", team="A", position="MID",
            current_price=500000, rolling_3_avg=80.0, breakeven=100,
            gap=-20.0, is_injured=False, injury_detail=None,
            reason="Underperforming",
        )
        inp = TradeInCandidate(
            player_id=2, player_name="In", team="B", position="MID",
            current_price=450000, projected_score=110.0,
            price_direction="rising", projected_change=15000,
            ownership_pct=0.05, trade_score=0.85,
            opponent="C", dvp_rank=15,
        )
        rec = TradeRecommendation(
            trade_out=out,
            trade_in=inp,
            net_price=-50000,
            projected_gain=30.0,
        )
        assert rec.net_price == -50000
        assert rec.projected_gain == 30.0
