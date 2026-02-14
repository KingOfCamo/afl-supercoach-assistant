from __future__ import annotations

"""Trade recommendation engine for SuperCoach.

Identifies underperformers on your team and suggests replacements.

Trade-out scoring (identify worst performers):
- 3-game rolling avg below breakeven by 15+ points
- Injuries with extended timeline
- Price trending down

Trade-in scoring (best replacements):
- 40% projected points
- 25% price appreciation potential
- 20% fixture difficulty (DVP)
- 15% ownership (lower = more POD value)
"""

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import desc, select

from src.models.database import (
    DfsPlayerStats,
    Injury,
    MyTeamSlot,
    Player,
    SupercoachScore,
    get_session,
)
from src.analytics.breakevens import compute_breakeven
from src.analytics.dvp import _map_position
from src.analytics.projections import project_player

logger = logging.getLogger(__name__)


@dataclass
class TradeOutCandidate:
    """A player recommended to trade out."""

    player_id: int
    player_name: str
    team: str
    position: Optional[str]
    current_price: int
    rolling_3_avg: Optional[float]
    breakeven: Optional[int]
    gap: float  # how far below BE (negative = underperforming)
    is_injured: bool
    injury_detail: Optional[str]
    reason: str


@dataclass
class TradeInCandidate:
    """A player recommended to trade in."""

    player_id: int
    player_name: str
    team: str
    position: Optional[str]
    current_price: int
    projected_score: float
    price_direction: str
    projected_change: int
    ownership_pct: Optional[float]
    trade_score: float  # composite score for ranking
    opponent: Optional[str]
    dvp_rank: Optional[int]


@dataclass
class TradeRecommendation:
    """A matched trade-out/trade-in pair."""

    trade_out: TradeOutCandidate
    trade_in: TradeInCandidate
    net_price: int  # price_in - price_out (negative = saves money)
    projected_gain: float  # projected score improvement


def _find_underperformers(
    season: int,
    round_num: int,
    threshold: float = 15.0,
) -> list[TradeOutCandidate]:
    """Find team players who are underperforming their breakeven."""
    session = get_session()
    try:
        slots = session.execute(select(MyTeamSlot)).scalars().all()
    finally:
        session.close()

    candidates = []
    for slot in slots:
        be = compute_breakeven(slot.player_id, season=season)
        if be is None:
            continue

        # Check injury
        session2 = get_session()
        try:
            injury = session2.execute(
                select(Injury).where(Injury.player_id == slot.player_id)
            ).scalar_one_or_none()
        finally:
            session2.close()

        is_injured = injury is not None
        injury_detail = None
        if injury:
            injury_detail = f"{injury.injury_type} - {injury.estimated_return}"

        # Compute gap (negative = underperforming)
        gap = 0.0
        if be.rolling_3_avg is not None and be.breakeven is not None:
            gap = be.rolling_3_avg - be.breakeven

        # Determine reason
        reasons = []
        if gap < -threshold:
            reasons.append(f"Avg {be.rolling_3_avg:.0f} is {abs(gap):.0f}pts below BE {be.breakeven}")
        if is_injured:
            reasons.append(f"Injured: {injury_detail}")
        if be.price_direction == "falling":
            reasons.append(f"Price falling (est {be.projected_change:+,})")

        if not reasons:
            continue

        candidates.append(
            TradeOutCandidate(
                player_id=be.player_id,
                player_name=be.player_name,
                team=be.team,
                position=be.position,
                current_price=be.current_price,
                rolling_3_avg=be.rolling_3_avg,
                breakeven=be.breakeven,
                gap=gap,
                is_injured=is_injured,
                injury_detail=injury_detail,
                reason="; ".join(reasons),
            )
        )

    # Sort by gap ascending (worst performers first)
    candidates.sort(key=lambda c: c.gap)
    return candidates


def _find_replacements(
    position: Optional[str],
    budget: int,
    season: int,
    round_num: int,
    exclude_ids: set[int],
    top_n: int = 5,
) -> list[TradeInCandidate]:
    """Find the best trade-in candidates for a given position and budget."""
    session = get_session()
    try:
        query = select(Player).where(Player.is_active == True)  # noqa: E712
        players = session.execute(query).scalars().all()
    finally:
        session.close()

    target_dvp = _map_position(position) if position else None

    candidates = []
    for player in players:
        if player.id in exclude_ids:
            continue

        # Check position compatibility
        if target_dvp:
            player_dvp = _map_position(player.position)
            if player_dvp != target_dvp:
                continue

        # Get projection
        proj = project_player(player.id, round_num, season=season)
        if proj is None:
            continue

        # Get breakeven for price direction
        be = compute_breakeven(player.id, season=season)

        # Get DFS ownership data and current price
        session2 = get_session()
        try:
            dfs = session2.execute(
                select(DfsPlayerStats)
                .where(DfsPlayerStats.player_id == player.id)
                .order_by(desc(DfsPlayerStats.season))
                .limit(1)
            ).scalar_one_or_none()

            latest_score = session2.execute(
                select(SupercoachScore)
                .where(
                    SupercoachScore.player_id == player.id,
                    SupercoachScore.season == season,
                )
                .order_by(desc(SupercoachScore.round))
                .limit(1)
            ).scalar_one_or_none()
        finally:
            session2.close()

        current_price = 0
        if latest_score and latest_score.price:
            current_price = latest_score.price
        elif dfs and dfs.salary:
            current_price = dfs.salary

        if current_price > budget:
            continue

        price_direction = be.price_direction if be else "unknown"
        projected_change = be.projected_change if be else 0
        ownership = dfs.ownership_pct if dfs else None

        # Composite trade-in score
        pts_score = proj.projected_score / 150.0
        price_score = max(0, projected_change) / 20000.0
        dvp_score = (proj.dvp_rank or 9) / 18.0
        pod_score = 1.0 - (ownership or 0.15)

        trade_score = (
            0.40 * pts_score
            + 0.25 * price_score
            + 0.20 * dvp_score
            + 0.15 * pod_score
        )

        candidates.append(
            TradeInCandidate(
                player_id=player.id,
                player_name=player.name,
                team=player.team,
                position=player.position,
                current_price=current_price,
                projected_score=proj.projected_score,
                price_direction=price_direction,
                projected_change=projected_change,
                ownership_pct=ownership,
                trade_score=round(trade_score, 3),
                opponent=proj.opponent,
                dvp_rank=proj.dvp_rank,
            )
        )

    candidates.sort(key=lambda c: c.trade_score, reverse=True)
    return candidates[:top_n]


def suggest_trades(
    round_num: int,
    budget: int = 0,
    season: Optional[int] = None,
    max_trades: int = 2,
) -> list[TradeRecommendation]:
    """Generate trade recommendations.

    Args:
        round_num: Target round.
        budget: Available salary cap space (0 = no extra budget).
        season: Season year (defaults to config).
        max_trades: Max number of trades to suggest (default 2 per round).

    Returns:
        List of TradeRecommendation objects.
    """
    from src.utils.config import get_config

    config = get_config()
    target_season = season or config.season

    underperformers = _find_underperformers(target_season, round_num)

    if not underperformers:
        logger.info("No clear underperformers found on your team.")
        return []

    session = get_session()
    try:
        team_ids = set(
            session.execute(select(MyTeamSlot.player_id)).scalars().all()
        )
    finally:
        session.close()

    recommendations = []
    for trade_out in underperformers[:max_trades]:
        replacement_budget = trade_out.current_price + budget

        replacements = _find_replacements(
            position=trade_out.position,
            budget=replacement_budget,
            season=target_season,
            round_num=round_num,
            exclude_ids=team_ids,
            top_n=3,
        )

        if replacements:
            best = replacements[0]

            out_proj = project_player(trade_out.player_id, round_num, season=target_season)
            out_score = out_proj.projected_score if out_proj else (trade_out.rolling_3_avg or 0)

            recommendations.append(
                TradeRecommendation(
                    trade_out=trade_out,
                    trade_in=best,
                    net_price=best.current_price - trade_out.current_price,
                    projected_gain=round(best.projected_score - out_score, 1),
                )
            )

    return recommendations
