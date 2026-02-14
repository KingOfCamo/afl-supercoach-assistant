from __future__ import annotations

"""Breakeven analysis for SuperCoach players.

Breakeven = the score a player needs to maintain their current price.
Prices update weekly based on a 3-game rolling average vs breakeven.

Key concepts:
- Price rises when 3-game avg > breakeven
- Price drops when 3-game avg < breakeven
- Bigger gap = faster price movement
"""

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import desc, select

from src.models.database import Player, SupercoachScore, get_session

logger = logging.getLogger(__name__)


@dataclass
class BreakevenResult:
    """Breakeven analysis result for a single player."""

    player_id: int
    player_name: str
    team: str
    position: Optional[str]
    current_price: int
    breakeven: Optional[int]
    rolling_3_avg: Optional[float]
    season_avg: Optional[float]
    price_direction: str  # "rising", "falling", "steady", "unknown"
    projected_change: int  # estimated weekly price change in $
    games_played: int


def compute_breakeven(
    player_id: int,
    season: Optional[int] = None,
) -> Optional[BreakevenResult]:
    """Compute breakeven analysis for a single player.

    Args:
        player_id: Database player ID.
        season: Season year (defaults to config season).

    Returns:
        BreakevenResult or None if insufficient data.
    """
    from src.utils.config import get_config

    config = get_config()
    target_season = season or config.season

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
                    SupercoachScore.season == target_season,
                )
                .order_by(desc(SupercoachScore.round))
            )
            .scalars()
            .all()
        )

        if not scores:
            return None

        latest = scores[0]
        valid_scores = [s.score for s in scores if s.score is not None]

        if not valid_scores:
            return None

        current_price = latest.price or 0
        breakeven = latest.breakeven

        # Compute rolling 3-game average
        last_3 = [s.score for s in scores[:3] if s.score is not None]
        rolling_3_avg = sum(last_3) / len(last_3) if last_3 else None

        # Season average
        season_avg = sum(valid_scores) / len(valid_scores)

        # Determine price direction
        if breakeven is not None and rolling_3_avg is not None:
            diff = rolling_3_avg - breakeven
            if diff > 5:
                price_direction = "rising"
            elif diff < -5:
                price_direction = "falling"
            else:
                price_direction = "steady"

            # Rough projected change: ~$3500 per point above/below BE
            projected_change = int(diff * 3500)
        else:
            price_direction = "unknown"
            projected_change = 0

        return BreakevenResult(
            player_id=player.id,
            player_name=player.name,
            team=player.team,
            position=player.position,
            current_price=current_price,
            breakeven=breakeven,
            rolling_3_avg=rolling_3_avg,
            season_avg=season_avg,
            price_direction=price_direction,
            projected_change=projected_change,
            games_played=len(valid_scores),
        )
    finally:
        session.close()


def compute_all_breakevens(
    season: Optional[int] = None,
) -> list[BreakevenResult]:
    """Compute breakeven analysis for all players with score data.

    Returns list sorted by projected price change (biggest risers first).
    """
    from src.utils.config import get_config

    config = get_config()
    target_season = season or config.season

    session = get_session()
    try:
        # Get all players with scores this season
        player_ids = (
            session.execute(
                select(SupercoachScore.player_id)
                .where(SupercoachScore.season == target_season)
                .distinct()
            )
            .scalars()
            .all()
        )
    finally:
        session.close()

    results = []
    for pid in player_ids:
        result = compute_breakeven(pid, season=target_season)
        if result is not None:
            results.append(result)

    # Sort by projected change descending (biggest risers first)
    results.sort(key=lambda r: r.projected_change, reverse=True)
    return results
