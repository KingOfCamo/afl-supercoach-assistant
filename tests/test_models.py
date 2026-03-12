from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import create_engine, select, event
from sqlalchemy.orm import sessionmaker

from src.models.database import (
    Base,
    DfsPlayerStats,
    Injury,
    MyTeamSlot,
    Player,
    SupercoachScore,
    Trade,
    User,
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


def test_create_tables(db_session):
    """All tables should be created."""
    tables = Base.metadata.tables.keys()
    assert "players" in tables
    assert "supercoach_scores" in tables
    assert "match_stats" in tables
    assert "injuries" in tables
    assert "my_team" in tables
    assert "trades" in tables
    assert "dfs_player_stats" in tables
    assert "fixtures" in tables
    assert "team_dvp" in tables
    assert "player_projections" in tables


def test_insert_player(db_session):
    """Should insert and retrieve a player."""
    player = Player(name="Caleb Serong", team="Fremantle", position="MID")
    db_session.add(player)
    db_session.commit()

    result = db_session.execute(select(Player).where(Player.name == "Caleb Serong")).scalar_one()
    assert result.team == "Fremantle"
    assert result.position == "MID"
    assert result.is_active is True


def test_insert_score(db_session):
    """Should insert a score linked to a player."""
    player = Player(name="Test Player", team="Test Team")
    db_session.add(player)
    db_session.flush()

    score = SupercoachScore(
        player_id=player.id, season=2024, round=1, score=120, price=500000, value=24.0
    )
    db_session.add(score)
    db_session.commit()

    result = db_session.execute(
        select(SupercoachScore).where(SupercoachScore.player_id == player.id)
    ).scalar_one()
    assert result.score == 120
    assert result.price == 500000


def test_unique_score_constraint(db_session):
    """Should not allow duplicate scores for same player/season/round."""
    player = Player(name="Test Player", team="Test Team")
    db_session.add(player)
    db_session.flush()

    score1 = SupercoachScore(player_id=player.id, season=2024, round=1, score=100)
    db_session.add(score1)
    db_session.commit()

    score2 = SupercoachScore(player_id=player.id, season=2024, round=1, score=110)
    db_session.add(score2)
    with pytest.raises(Exception):
        db_session.commit()


def test_player_injury_relationship(db_session):
    """Should link injury to player."""
    player = Player(name="Injured Player", team="Test Team")
    db_session.add(player)
    db_session.flush()

    injury = Injury(
        player_id=player.id,
        injury_type="Hamstring",
        estimated_return="Round 5",
        status="OUT",
    )
    db_session.add(injury)
    db_session.commit()

    result = db_session.execute(
        select(Injury).where(Injury.player_id == player.id)
    ).scalar_one()
    assert result.injury_type == "Hamstring"
    assert result.status == "OUT"


def test_my_team_slot(db_session):
    """Should create team slots with captain/vc flags."""
    user = User(email="test@test.com", password_hash="hash", display_name="Test")
    db_session.add(user)
    db_session.flush()

    player = Player(name="Captain Player", team="Test Team")
    db_session.add(player)
    db_session.flush()

    slot = MyTeamSlot(
        user_id=user.id,
        player_id=player.id,
        position_slot="MID1",
        is_captain=True,
    )
    db_session.add(slot)
    db_session.commit()

    result = db_session.execute(
        select(MyTeamSlot).where(MyTeamSlot.player_id == player.id)
    ).scalar_one()
    assert result.position_slot == "MID1"
    assert result.is_captain is True
    assert result.is_vice_captain is False


def test_dfs_player_stats(db_session):
    """Should create DFS player stats linked to a player."""
    player = Player(
        name="Marcus Bontempelli",
        team="Western Bulldogs",
        position="MID",
        champion_data_id="CD_I297373",
    )
    db_session.add(player)
    db_session.flush()

    dfs = DfsPlayerStats(
        player_id=player.id,
        season=2026,
        salary=706800,
        ownership_pct=0.201216,
        games_played=18,
        sc_avg=130.6,
        sc_max=177,
        scores_100_plus=15,
        scores_120_plus=13,
        cba_pct=0.671,
        points_per_minute=1.257,
        reg_avg=130.6,
        last_5_avg=147.4,
        prev_year_avg=123.75,
        prev_2yr_avg=129.65,
    )
    db_session.add(dfs)
    db_session.commit()

    result = db_session.execute(
        select(DfsPlayerStats).where(DfsPlayerStats.player_id == player.id)
    ).scalar_one()
    assert result.salary == 706800
    assert result.sc_avg == 130.6
    assert result.scores_120_plus == 13
    assert result.season == 2026

    # Check relationship
    assert player.champion_data_id == "CD_I297373"
    assert len(player.dfs_stats) == 1


def test_dfs_unique_constraint(db_session):
    """Should not allow duplicate DFS stats for same player/season."""
    player = Player(name="Test Player", team="Test Team")
    db_session.add(player)
    db_session.flush()

    dfs1 = DfsPlayerStats(player_id=player.id, season=2026, salary=500000)
    db_session.add(dfs1)
    db_session.commit()

    dfs2 = DfsPlayerStats(player_id=player.id, season=2026, salary=600000)
    db_session.add(dfs2)
    with pytest.raises(Exception):
        db_session.commit()
