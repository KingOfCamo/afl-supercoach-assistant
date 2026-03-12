from __future__ import annotations

"""Player score projections for upcoming rounds.

Phase 2a uses a heuristic model (not ML). Works with 0-5 rounds of data.

With in-season data:
    base = (0.6 * rolling_5_avg) + (0.3 * season_avg) + (0.1 * dfs_prev_year_avg)
    projected = base * dvp_factor
    floor = projected * 0.7
    ceiling = projected * 1.4

Pre-Round-1 fallback (no in-season scores):
    base = (0.5 * dfs.sc_avg) + (0.3 * dfs.prev_year_avg) + (0.2 * dfs.last_5_avg)

DVP factor: linear interpolation from rank 1 (hardest, -12%) to rank 18 (easiest, +12%).
"""

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import desc, select

from src.models.database import (
    DfsPlayerStats,
    MyTeamSlot,
    Player,
    PlayerProjection,
    SupercoachScore,
    get_session,
)
from src.analytics.dvp import get_next_opponent, get_opponent_dvp, _map_position
from src.utils.teams import normalize_team

logger = logging.getLogger(__name__)

METHOD = "rolling_avg_dvp_v1"


@dataclass
class ProjectionResult:
    """Projection output for a single player."""

    player_id: int
    player_name: str
    team: str
    position: Optional[str]
    projected_score: float
    floor: float
    ceiling: float
    dvp_adjustment: float  # +/- from matchup
    confidence: float  # 0.0-1.0
    opponent: Optional[str]
    dvp_rank: Optional[int]
    method: str


def _dvp_factor(dvp_rank: Optional[int]) -> float:
    """Convert DVP rank to a multiplicative factor.

    Rank 1 (hardest) = 0.88 (-12%)
    Rank 9-10 (neutral) = 1.0
    Rank 18 (easiest) = 1.12 (+12%)
    """
    if dvp_rank is None:
        return 1.0
    adjustment = (dvp_rank - 9.5) / 8.5 * 0.12
    return 1.0 + adjustment


def project_player(
    player_id: int,
    round_num: int,
    season: Optional[int] = None,
) -> Optional[ProjectionResult]:
    """Project a single player's score for a given round.

    Uses in-season data if available, falls back to DFS pre-season data.
    Applies DVP adjustment based on opponent matchup.
    """
    from src.utils.config import get_config

    config = get_config()
    target_season = season or config.season

    session = get_session()
    try:
        player = session.get(Player, player_id)
        if player is None:
            return None

        # Get in-season scores
        scores = (
            session.execute(
                select(SupercoachScore)
                .where(
                    SupercoachScore.player_id == player_id,
                    SupercoachScore.season == target_season,
                    SupercoachScore.score.isnot(None),
                )
                .order_by(desc(SupercoachScore.round))
            )
            .scalars()
            .all()
        )

        valid_scores = [s.score for s in scores if s.score is not None]

        # Get DFS data for fallback / supplementary
        dfs = session.execute(
            select(DfsPlayerStats)
            .where(DfsPlayerStats.player_id == player_id)
            .order_by(desc(DfsPlayerStats.season))
            .limit(1)
        ).scalar_one_or_none()

        # Compute base score
        base = _compute_base(valid_scores, dfs)
        if base is None:
            return None

        # Get DVP adjustment
        dvp_pos = _map_position(player.position)
        opponent = get_next_opponent(player.team, target_season, round_num)
        dvp_rank = None
        dvp_adj = 0.0

        if opponent and dvp_pos:
            dvp_entry = get_opponent_dvp(opponent, dvp_pos, target_season, round_num)
            if dvp_entry:
                dvp_rank = dvp_entry.dvp_rank

        factor = _dvp_factor(dvp_rank)
        projected = base * factor
        dvp_adj = projected - base

        # Floor/ceiling
        floor = projected * 0.7
        ceiling = projected * 1.4

        # Confidence: higher with more data
        if len(valid_scores) >= 5:
            confidence = 0.8
        elif len(valid_scores) >= 3:
            confidence = 0.6
        elif len(valid_scores) >= 1:
            confidence = 0.4
        elif dfs is not None:
            confidence = 0.3
        else:
            confidence = 0.1

        return ProjectionResult(
            player_id=player.id,
            player_name=player.name,
            team=player.team,
            position=player.position,
            projected_score=round(projected, 1),
            floor=round(floor, 1),
            ceiling=round(ceiling, 1),
            dvp_adjustment=round(dvp_adj, 1),
            confidence=confidence,
            opponent=opponent,
            dvp_rank=dvp_rank,
            method=METHOD,
        )
    finally:
        session.close()


def _compute_base(
    valid_scores: list[int],
    dfs: Optional[DfsPlayerStats],
) -> Optional[float]:
    """Compute the base projected score before DVP adjustment."""
    if valid_scores:
        rolling_5 = valid_scores[:5]
        rolling_avg = sum(rolling_5) / len(rolling_5)
        season_avg = sum(valid_scores) / len(valid_scores)

        dfs_prev = 0.0
        if dfs and dfs.prev_year_avg:
            dfs_prev = dfs.prev_year_avg
        elif dfs and dfs.sc_avg:
            dfs_prev = dfs.sc_avg

        if dfs_prev > 0:
            return (0.6 * rolling_avg) + (0.3 * season_avg) + (0.1 * dfs_prev)
        else:
            return (0.7 * rolling_avg) + (0.3 * season_avg)

    elif dfs:
        components = []
        weights = []

        if dfs.sc_avg and dfs.sc_avg > 0:
            components.append(dfs.sc_avg)
            weights.append(0.5)
        if dfs.prev_year_avg and dfs.prev_year_avg > 0:
            components.append(dfs.prev_year_avg)
            weights.append(0.3)
        if dfs.last_5_avg and dfs.last_5_avg > 0:
            components.append(dfs.last_5_avg)
            weights.append(0.2)

        if not components:
            return None

        total_weight = sum(weights)
        return sum(c * (w / total_weight) for c, w in zip(components, weights))

    return None


def project_round(
    round_num: int,
    season: Optional[int] = None,
    team_only: bool = False,
    save: bool = True,
    user_id: Optional[int] = None,
) -> list[ProjectionResult]:
    """Batch project all players (or just your team) for a round.

    Args:
        round_num: Round to project.
        season: Season year (defaults to config).
        team_only: If True, only project players on your team.
        save: Whether to persist projections to DB.
        user_id: Filter team by this user (required for multi-user team_only).

    Returns:
        List of ProjectionResults sorted by projected score descending.
    """
    from src.utils.config import get_config

    config = get_config()
    target_season = season or config.season

    session = get_session()
    try:
        if team_only:
            query = select(MyTeamSlot.player_id)
            if user_id is not None:
                query = query.where(MyTeamSlot.user_id == user_id)
            player_ids = session.execute(query).scalars().all()
        else:
            score_pids = set(
                session.execute(
                    select(SupercoachScore.player_id)
                    .where(SupercoachScore.season == target_season)
                    .distinct()
                )
                .scalars()
                .all()
            )
            dfs_pids = set(
                session.execute(
                    select(DfsPlayerStats.player_id).distinct()
                )
                .scalars()
                .all()
            )
            player_ids = list(score_pids | dfs_pids)
    finally:
        session.close()

    results = []
    for pid in player_ids:
        result = project_player(pid, round_num, season=target_season)
        if result is not None:
            results.append(result)

    results.sort(key=lambda r: r.projected_score, reverse=True)

    if save and results:
        _save_projections(results, target_season, round_num)

    return results


def _save_projections(results: list[ProjectionResult], season: int, round_num: int) -> None:
    """Persist projection results to PlayerProjection table."""
    session = get_session()
    try:
        for r in results:
            existing = session.execute(
                select(PlayerProjection).where(
                    PlayerProjection.player_id == r.player_id,
                    PlayerProjection.season == season,
                    PlayerProjection.round == round_num,
                )
            ).scalar_one_or_none()

            if existing:
                existing.projected_score = r.projected_score
                existing.floor = r.floor
                existing.ceiling = r.ceiling
                existing.dvp_adjustment = r.dvp_adjustment
                existing.confidence = r.confidence
                existing.method = r.method
            else:
                proj = PlayerProjection(
                    player_id=r.player_id,
                    season=season,
                    round=round_num,
                    projected_score=r.projected_score,
                    floor=r.floor,
                    ceiling=r.ceiling,
                    dvp_adjustment=r.dvp_adjustment,
                    confidence=r.confidence,
                    method=r.method,
                )
                session.add(proj)

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
