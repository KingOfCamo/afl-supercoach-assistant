from __future__ import annotations

from datetime import datetime, date
from typing import List, Optional

from sqlalchemy import (
    create_engine,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Date,
    ForeignKey,
    UniqueConstraint,
    Text,
    event,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    Session,
    sessionmaker,
)

from src.utils.config import get_config


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    team: Mapped[str] = mapped_column(String(50), index=True)
    position: Mapped[Optional[str]] = mapped_column(String(20))
    dob: Mapped[Optional[date]] = mapped_column(Date)
    height_cm: Mapped[Optional[int]] = mapped_column(Integer)
    weight_kg: Mapped[Optional[int]] = mapped_column(Integer)
    footywire_id: Mapped[Optional[str]] = mapped_column(String(200), unique=True, index=True)
    champion_data_id: Mapped[Optional[str]] = mapped_column(String(50), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    scores: Mapped[List["SupercoachScore"]] = relationship(back_populates="player")
    match_stats: Mapped[List["MatchStats"]] = relationship(back_populates="player")
    injuries: Mapped[List["Injury"]] = relationship(back_populates="player")
    team_slots: Mapped[List["MyTeamSlot"]] = relationship(back_populates="player")
    dfs_stats: Mapped[List["DfsPlayerStats"]] = relationship(back_populates="player")
    projections: Mapped[List["PlayerProjection"]] = relationship(back_populates="player")


class SupercoachScore(Base):
    __tablename__ = "supercoach_scores"
    __table_args__ = (
        UniqueConstraint(
            "player_id", "season", "round", name="uq_sc_score_player_season_round"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    season: Mapped[int] = mapped_column(Integer)
    round: Mapped[int] = mapped_column(Integer)
    score: Mapped[Optional[int]] = mapped_column(Integer)
    price: Mapped[Optional[int]] = mapped_column(Integer)
    breakeven: Mapped[Optional[int]] = mapped_column(Integer)
    value: Mapped[Optional[float]] = mapped_column(Float)
    projected_score: Mapped[Optional[int]] = mapped_column(Integer)
    was_emergency: Mapped[bool] = mapped_column(Boolean, default=False)
    did_not_play: Mapped[bool] = mapped_column(Boolean, default=False)

    player: Mapped["Player"] = relationship(back_populates="scores")


class MatchStats(Base):
    __tablename__ = "match_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "match_id", name="uq_match_stats_player_match"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    match_id: Mapped[Optional[str]] = mapped_column(String(50))
    season: Mapped[Optional[int]] = mapped_column(Integer)
    round: Mapped[Optional[int]] = mapped_column(Integer)
    disposals: Mapped[Optional[int]] = mapped_column(Integer)
    kicks: Mapped[Optional[int]] = mapped_column(Integer)
    handballs: Mapped[Optional[int]] = mapped_column(Integer)
    marks: Mapped[Optional[int]] = mapped_column(Integer)
    tackles: Mapped[Optional[int]] = mapped_column(Integer)
    hitouts: Mapped[Optional[int]] = mapped_column(Integer)
    goals: Mapped[Optional[int]] = mapped_column(Integer)
    behinds: Mapped[Optional[int]] = mapped_column(Integer)
    contested_poss: Mapped[Optional[int]] = mapped_column(Integer)
    uncontested_poss: Mapped[Optional[int]] = mapped_column(Integer)
    clearances: Mapped[Optional[int]] = mapped_column(Integer)
    inside_50s: Mapped[Optional[int]] = mapped_column(Integer)
    rebound_50s: Mapped[Optional[int]] = mapped_column(Integer)
    cba_count: Mapped[Optional[int]] = mapped_column(Integer)
    cba_pct: Mapped[Optional[float]] = mapped_column(Float)
    time_on_ground_pct: Mapped[Optional[float]] = mapped_column(Float)
    kick_ins: Mapped[Optional[int]] = mapped_column(Integer)

    player: Mapped["Player"] = relationship(back_populates="match_stats")


class Injury(Base):
    __tablename__ = "injuries"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    injury_type: Mapped[Optional[str]] = mapped_column(String(100))
    estimated_return: Mapped[Optional[str]] = mapped_column(String(100))
    status: Mapped[Optional[str]] = mapped_column(String(50))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship(back_populates="injuries")


class MyTeamSlot(Base):
    __tablename__ = "my_team"

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    position_slot: Mapped[str] = mapped_column(String(20))
    is_captain: Mapped[bool] = mapped_column(Boolean, default=False)
    is_vice_captain: Mapped[bool] = mapped_column(Boolean, default=False)
    is_emergency: Mapped[bool] = mapped_column(Boolean, default=False)
    added_round: Mapped[Optional[int]] = mapped_column(Integer)
    added_price: Mapped[Optional[int]] = mapped_column(Integer)

    player: Mapped["Player"] = relationship(back_populates="team_slots")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(Integer)
    round: Mapped[int] = mapped_column(Integer)
    player_out_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    player_in_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    price_out: Mapped[Optional[int]] = mapped_column(Integer)
    price_in: Mapped[Optional[int]] = mapped_column(Integer)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    was_boost: Mapped[bool] = mapped_column(Boolean, default=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player_out: Mapped["Player"] = relationship(foreign_keys=[player_out_id])
    player_in: Mapped["Player"] = relationship(foreign_keys=[player_in_id])


class DfsPlayerStats(Base):
    """Pre-season stats from DFS Australia spreadsheet.

    Stores salary, ownership, advanced metrics (CBA%, PPM),
    historical averages, and state league stats.
    One row per player per season.
    """

    __tablename__ = "dfs_player_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "season", name="uq_dfs_player_season"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    season: Mapped[int] = mapped_column(Integer)

    # SuperCoach pricing & ownership
    salary: Mapped[Optional[int]] = mapped_column(Integer)
    ownership_pct: Mapped[Optional[float]] = mapped_column(Float)

    # 2025 AFL SuperCoach stats
    games_played: Mapped[Optional[int]] = mapped_column(Integer)
    sc_avg: Mapped[Optional[float]] = mapped_column(Float)
    sc_max: Mapped[Optional[int]] = mapped_column(Integer)
    scores_100_plus: Mapped[Optional[int]] = mapped_column(Integer)
    scores_120_plus: Mapped[Optional[int]] = mapped_column(Integer)

    # Advanced metrics
    cba_pct: Mapped[Optional[float]] = mapped_column(Float)
    points_per_minute: Mapped[Optional[float]] = mapped_column(Float)
    reg_avg: Mapped[Optional[float]] = mapped_column(Float)
    last_5_avg: Mapped[Optional[float]] = mapped_column(Float)
    finals_avg: Mapped[Optional[float]] = mapped_column(Float)

    # Historical SC averages
    prev_year_avg: Mapped[Optional[float]] = mapped_column(Float)
    prev_2yr_avg: Mapped[Optional[float]] = mapped_column(Float)

    # VFL Fantasy stats
    vfl_games: Mapped[Optional[int]] = mapped_column(Integer)
    vfl_avg: Mapped[Optional[float]] = mapped_column(Float)
    vfl_max: Mapped[Optional[int]] = mapped_column(Integer)
    vfl_100_plus: Mapped[Optional[int]] = mapped_column(Integer)

    # WAFL Fantasy stats
    wafl_games: Mapped[Optional[int]] = mapped_column(Integer)
    wafl_avg: Mapped[Optional[float]] = mapped_column(Float)
    wafl_max: Mapped[Optional[int]] = mapped_column(Integer)
    wafl_100_plus: Mapped[Optional[int]] = mapped_column(Integer)

    # SANFL Fantasy stats
    sanfl_games: Mapped[Optional[int]] = mapped_column(Integer)
    sanfl_avg: Mapped[Optional[float]] = mapped_column(Float)
    sanfl_max: Mapped[Optional[int]] = mapped_column(Integer)
    sanfl_100_plus: Mapped[Optional[int]] = mapped_column(Integer)

    # SANFL U18s Fantasy stats
    sanfl_u18_games: Mapped[Optional[int]] = mapped_column(Integer)
    sanfl_u18_avg: Mapped[Optional[float]] = mapped_column(Float)
    sanfl_u18_max: Mapped[Optional[int]] = mapped_column(Integer)
    sanfl_u18_100_plus: Mapped[Optional[int]] = mapped_column(Integer)

    # Coates Talent League Fantasy stats
    ctl_games: Mapped[Optional[int]] = mapped_column(Integer)
    ctl_avg: Mapped[Optional[float]] = mapped_column(Float)
    ctl_max: Mapped[Optional[int]] = mapped_column(Integer)
    ctl_100_plus: Mapped[Optional[int]] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship(back_populates="dfs_stats")


class Fixture(Base):
    """AFL fixture/schedule. One row per game."""

    __tablename__ = "fixtures"
    __table_args__ = (
        UniqueConstraint(
            "season", "round", "home_team", name="uq_fixture_season_round_home"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(Integer, index=True)
    round: Mapped[int] = mapped_column(Integer, index=True)
    home_team: Mapped[str] = mapped_column(String(50), index=True)
    away_team: Mapped[str] = mapped_column(String(50), index=True)
    venue: Mapped[Optional[str]] = mapped_column(String(100))
    date: Mapped[Optional[datetime]] = mapped_column(DateTime)
    home_score: Mapped[Optional[int]] = mapped_column(Integer)
    away_score: Mapped[Optional[int]] = mapped_column(Integer)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    squiggle_id: Mapped[Optional[int]] = mapped_column(Integer, unique=True)


class TeamDVP(Base):
    """Difficulty vs Position: points conceded by a team to each position.

    dvp_rank 1 = hardest (fewest points conceded), 18 = easiest (most conceded).
    Computed from SupercoachScore + Fixture data.
    """

    __tablename__ = "team_dvp"
    __table_args__ = (
        UniqueConstraint("season", "round", "team", "position", name="uq_dvp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(Integer)
    round: Mapped[int] = mapped_column(Integer)
    team: Mapped[str] = mapped_column(String(50), index=True)
    position: Mapped[str] = mapped_column(String(10))
    avg_points_conceded: Mapped[Optional[float]] = mapped_column(Float)
    dvp_rank: Mapped[Optional[int]] = mapped_column(Integer)


class PlayerProjection(Base):
    """Projected score for a player in a given round."""

    __tablename__ = "player_projections"
    __table_args__ = (
        UniqueConstraint("player_id", "season", "round", name="uq_projection"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    season: Mapped[int] = mapped_column(Integer)
    round: Mapped[int] = mapped_column(Integer)
    projected_score: Mapped[Optional[float]] = mapped_column(Float)
    floor: Mapped[Optional[float]] = mapped_column(Float)
    ceiling: Mapped[Optional[float]] = mapped_column(Float)
    dvp_adjustment: Mapped[Optional[float]] = mapped_column(Float)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    method: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    player: Mapped["Player"] = relationship(back_populates="projections")


# --- Engine and session management ---

_engine = None
_SessionLocal = None


def _enable_wal_mode(dbapi_conn, connection_record):
    """Enable WAL mode and foreign keys for SQLite."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine():
    global _engine
    if _engine is None:
        config = get_config()
        _engine = create_engine(config.database.url, echo=False)
        event.listen(_engine, "connect", _enable_wal_mode)
    return _engine


def get_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


def init_db():
    """Create all tables. Safe to call multiple times."""
    engine = get_engine()
    Base.metadata.create_all(engine)


def reset_engine():
    """Reset engine and session factory. Useful for testing."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
