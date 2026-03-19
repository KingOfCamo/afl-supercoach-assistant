from __future__ import annotations

"""Cash cow tracker: track cheap players generating cash through price rises."""

import logging
from typing import Optional

from sqlalchemy import select, desc, func
from sqlalchemy.orm import Session

from src.models.database import (
    MyTeamSlot, Player, SupercoachScore, DfsPlayerStats, Ownership, ByeRound,
    get_session,
)
from src.utils.teams import normalize_team

logger = logging.getLogger(__name__)

MAX_COW_PRICE = 350000


def get_cash_cow_data(
    session: Session,
    user_id: int,
    season: int,
    round_num: int,
) -> dict:
    """Get comprehensive cash cow tracker data."""

    # 1. My cows — team players bought under max price
    slots = (
        session.execute(
            select(MyTeamSlot, Player)
            .join(Player, MyTeamSlot.player_id == Player.id)
            .where(MyTeamSlot.user_id == user_id)
        )
        .all()
    )

    all_team_ids = {p.id for _, p in slots}
    my_cows = []
    total_cash = 0

    for slot, player in slots:
        # Get price data
        dfs = session.execute(
            select(DfsPlayerStats)
            .where(DfsPlayerStats.player_id == player.id)
            .order_by(desc(DfsPlayerStats.season))
            .limit(1)
        ).scalar_one_or_none()

        scores = (
            session.execute(
                select(SupercoachScore)
                .where(SupercoachScore.player_id == player.id, SupercoachScore.season == season, SupercoachScore.score.isnot(None))
                .order_by(SupercoachScore.round)
            )
            .scalars()
            .all()
        )

        latest = scores[-1] if scores else None
        current_price = (latest.price if latest and latest.price else None) or (dfs.salary if dfs else None)
        purchase_price = slot.added_price or (scores[0].price if scores and scores[0].price else None) or (dfs.salary if dfs else None)

        if not purchase_price or purchase_price > MAX_COW_PRICE:
            continue

        cash = (current_price or 0) - (purchase_price or 0)
        total_cash += cash

        all_scores = [s.score for s in scores if s.score is not None]
        avg = sum(all_scores) / len(all_scores) if all_scores else 0
        be = latest.breakeven if latest else None

        price_history = [{"round": s.round, "price": s.price} for s in scores if s.price]

        peak = _estimate_peak(all_scores, current_price)

        my_cows.append({
            "player_id": player.id,
            "name": player.name,
            "team": player.team,
            "position": player.position,
            "slot": slot.position_slot,
            "purchase_price": purchase_price,
            "current_price": current_price,
            "cash_generated": cash,
            "avg_score": round(avg, 1),
            "breakeven": be,
            "scoring_above_be": round(avg - be, 1) if be else None,
            "games_played": len(all_scores),
            "price_history": price_history,
            "peak_estimate": peak,
        })

    my_cows.sort(key=lambda c: c["cash_generated"], reverse=True)

    # 2. Best available cows — not in team
    available_query = (
        session.execute(
            select(
                Player.id, Player.name, Player.team, Player.position,
                func.avg(SupercoachScore.score).label("avg_score"),
                func.count(SupercoachScore.score).label("games"),
            )
            .join(SupercoachScore, SupercoachScore.player_id == Player.id)
            .where(
                SupercoachScore.season == season,
                SupercoachScore.score.isnot(None),
                Player.is_active == True,
            )
            .group_by(Player.id)
            .having(func.avg(SupercoachScore.score) >= 50)
            .order_by(func.avg(SupercoachScore.score).desc())
            .limit(80)
        )
        .all()
    )

    best_available = []
    for pid, name, team, position, avg_score, games in available_query:
        if pid in all_team_ids:
            continue

        # Get current price
        latest_price = session.execute(
            select(SupercoachScore.price)
            .where(SupercoachScore.player_id == pid, SupercoachScore.season == season, SupercoachScore.price.isnot(None))
            .order_by(desc(SupercoachScore.round))
            .limit(1)
        ).scalar_one_or_none()

        dfs_price = session.execute(
            select(DfsPlayerStats.salary)
            .where(DfsPlayerStats.player_id == pid)
            .order_by(desc(DfsPlayerStats.season))
            .limit(1)
        ).scalar_one_or_none()

        price = latest_price or dfs_price
        if not price or price > MAX_COW_PRICE or price <= 0:
            continue

        # Latest BE
        be = session.execute(
            select(SupercoachScore.breakeven)
            .where(SupercoachScore.player_id == pid, SupercoachScore.season == season, SupercoachScore.breakeven.isnot(None))
            .order_by(desc(SupercoachScore.round))
            .limit(1)
        ).scalar_one_or_none()

        proj_cash = _estimate_cash_gen(avg_score, price, be)

        # Ownership
        own = session.execute(
            select(Ownership.ownership_pct)
            .where(Ownership.player_id == pid, Ownership.season == season)
            .order_by(desc(Ownership.round))
            .limit(1)
        ).scalar_one_or_none()

        # Next bye
        team_name = normalize_team(team)
        next_bye = session.execute(
            select(ByeRound.round)
            .where(ByeRound.season == season, ByeRound.team == team_name, ByeRound.round >= round_num)
            .order_by(ByeRound.round)
            .limit(1)
        ).scalar_one_or_none()

        best_available.append({
            "player_id": pid,
            "name": name,
            "team": team,
            "position": position,
            "price": price,
            "avg_score": round(avg_score, 1),
            "games": games,
            "breakeven": be,
            "projected_cash": proj_cash,
            "ownership_pct": own,
            "next_bye": next_bye,
        })

    best_available.sort(key=lambda c: c["projected_cash"], reverse=True)

    # 3. Sell alerts
    sell_alerts = []
    for cow in my_cows:
        peak = cow["peak_estimate"]
        if peak and peak.get("near_peak"):
            sell_alerts.append({**cow, "alert_reason": peak.get("reason", "Approaching peak")})
        elif cow["scoring_above_be"] is not None and cow["scoring_above_be"] < -10:
            sell_alerts.append({**cow, "alert_reason": f"Averaging {abs(cow['scoring_above_be']):.0f} below BE — price dropping"})

    # 4. Leaderboard — top cash generators across all players
    leaderboard = []
    lb_query = (
        session.execute(
            select(
                Player.id, Player.name, Player.team, Player.position,
                func.min(SupercoachScore.price).label("start_price"),
                func.max(SupercoachScore.price).label("current_price"),
                func.avg(SupercoachScore.score).label("avg_score"),
            )
            .join(SupercoachScore, SupercoachScore.player_id == Player.id)
            .where(
                SupercoachScore.season == season,
                SupercoachScore.price.isnot(None),
                SupercoachScore.price > 0,
                SupercoachScore.price <= MAX_COW_PRICE,
            )
            .group_by(Player.id)
            .order_by((func.max(SupercoachScore.price) - func.min(SupercoachScore.price)).desc())
            .limit(30)
        )
        .all()
    )

    for pid, name, team, position, start_price, current_price, avg_score in lb_query:
        cash = (current_price or 0) - (start_price or 0)
        if cash > 0:
            leaderboard.append({
                "name": name,
                "team": team,
                "position": position,
                "start_price": start_price,
                "current_price": current_price,
                "cash_generated": cash,
                "avg_score": round(avg_score, 1) if avg_score else 0,
            })

    return {
        "round": round_num,
        "my_cows": {
            "players": my_cows,
            "total_cash_generated": total_cash,
            "cow_count": len(my_cows),
        },
        "best_available": best_available[:15],
        "sell_alerts": sell_alerts,
        "leaderboard": leaderboard,
    }


def _estimate_peak(scores: list[int], current_price: Optional[int]) -> dict:
    """Estimate when a cash cow will peak."""
    if not scores or len(scores) < 3 or not current_price:
        return {"near_peak": False, "reason": "Too early"}

    recent_3 = scores[-3:]
    prev = scores[-6:-3] if len(scores) >= 6 else scores[:3]
    recent_avg = sum(recent_3) / len(recent_3)
    prev_avg = sum(prev) / len(prev) if prev else recent_avg
    trending_down = recent_avg < prev_avg - 10

    if current_price >= 500000:
        return {"near_peak": True, "reason": "Above $500K — approaching ceiling", "rounds_until_peak": 1}
    if current_price >= 400000 and trending_down:
        return {"near_peak": True, "reason": "Above $400K and scores declining", "rounds_until_peak": 0}
    if trending_down and current_price >= 300000:
        return {"near_peak": True, "reason": "Scores declining — may have peaked", "rounds_until_peak": 0}

    weekly_rise = max(0, (recent_avg - 70)) * 5500
    est_peak = min(current_price + weekly_rise * 4, 550000)
    rounds = max(1, int((est_peak - current_price) / max(weekly_rise, 1))) if weekly_rise > 0 else 8

    return {"near_peak": False, "reason": f"Projected ~${est_peak:,.0f} in ~{rounds} rounds", "rounds_until_peak": min(rounds, 8)}


def _estimate_cash_gen(avg_score: float, price: int, breakeven: Optional[int]) -> int:
    """Estimate future cash generation."""
    above_be = avg_score - (breakeven or 70)
    if above_be <= 0:
        return 0
    weekly = above_be * 5500
    weeks = max(1, min(6, int((500000 - price) / max(weekly, 1))))
    return round(weekly * weeks, -3)
