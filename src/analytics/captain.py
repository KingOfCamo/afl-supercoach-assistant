from __future__ import annotations

"""Captain picker for SuperCoach.

Ranks your team's players for captain selection based on:
- 50% projected score (from projections engine)
- 20% floor (worst-case reliability)
- 15% ceiling (upside potential)
- 15% consistency (inverse of score variance)

Captain scores double points, so maximising this pick is critical.
"""

import logging
import math
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import desc, select

from src.models.database import (
    MyTeamSlot,
    Player,
    SupercoachScore,
    get_session,
)
from src.analytics.projections import project_player

logger = logging.getLogger(__name__)


@dataclass
class CaptainCandidate:
    """A player ranked for captain consideration."""

    player_id: int
    player_name: str
    team: str
    position: Optional[str]
    projected_score: float
    floor: float
    ceiling: float
    consistency: float  # 0.0-1.0 (higher = more consistent)
    captain_score: float  # weighted composite score
    opponent: Optional[str]
    dvp_rank: Optional[int]
    recent_scores: list[int]


def _compute_consistency(scores: list[int]) -> float:
    """Compute consistency score from recent scores.

    Uses coefficient of variation (lower CV = more consistent).
    Returns 0.0-1.0 where 1.0 = perfectly consistent.
    """
    if len(scores) < 2:
        return 0.5  # neutral if insufficient data

    mean = sum(scores) / len(scores)
    if mean == 0:
        return 0.0

    variance = sum((s - mean) ** 2 for s in scores) / len(scores)
    std_dev = math.sqrt(variance)
    cv = std_dev / mean

    # Map CV to 0-1: CV of 0 = consistency 1.0, CV of 0.5+ = consistency 0.0
    consistency = max(0.0, 1.0 - (cv * 2.0))
    return round(consistency, 3)


def rank_captain_options(
    round_num: int,
    season: Optional[int] = None,
    top_n: int = 5,
    user_id: Optional[int] = None,
) -> list[CaptainCandidate]:
    """Rank team players for captain selection.

    Args:
        round_num: The round to pick captain for.
        season: Season year (defaults to config).
        top_n: Number of candidates to return.
        user_id: Filter team by this user (required for multi-user).

    Returns:
        List of CaptainCandidates sorted by captain_score descending.
    """
    from src.utils.config import get_config

    config = get_config()
    target_season = season or config.season

    session = get_session()
    try:
        query = select(MyTeamSlot.player_id)
        if user_id is not None:
            query = query.where(MyTeamSlot.user_id == user_id)
        team_player_ids = session.execute(query).scalars().all()
    finally:
        session.close()

    if not team_player_ids:
        logger.warning("No team players found. Import your team first.")
        return []

    candidates = []
    for pid in team_player_ids:
        candidate = _evaluate_captain(pid, round_num, target_season)
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda c: c.captain_score, reverse=True)
    return candidates[:top_n]


def _evaluate_captain(
    player_id: int,
    round_num: int,
    season: int,
) -> Optional[CaptainCandidate]:
    """Evaluate a single player as a captain candidate."""
    projection = project_player(player_id, round_num, season=season)
    if projection is None:
        return None

    session = get_session()
    try:
        player = session.get(Player, player_id)
        if player is None:
            return None

        scores = (
            session.execute(
                select(SupercoachScore)
                .where(
                    SupercoachScore.player_id == player_id,
                    SupercoachScore.season == season,
                    SupercoachScore.score.isnot(None),
                )
                .order_by(desc(SupercoachScore.round))
                .limit(5)
            )
            .scalars()
            .all()
        )

        recent = [s.score for s in scores if s.score is not None]
    finally:
        session.close()

    consistency = _compute_consistency(recent)

    # Weighted captain score:
    # 50% projection + 20% floor + 15% ceiling + 15% consistency * projected
    captain_score = (
        0.50 * projection.projected_score
        + 0.20 * projection.floor
        + 0.15 * projection.ceiling
        + 0.15 * (consistency * projection.projected_score)
    )

    return CaptainCandidate(
        player_id=player_id,
        player_name=projection.player_name,
        team=projection.team,
        position=projection.position,
        projected_score=projection.projected_score,
        floor=projection.floor,
        ceiling=projection.ceiling,
        consistency=consistency,
        captain_score=round(captain_score, 1),
        opponent=projection.opponent,
        dvp_rank=projection.dvp_rank,
        recent_scores=recent,
    )
