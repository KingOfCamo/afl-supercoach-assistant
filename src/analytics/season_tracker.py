from __future__ import annotations

"""Season tracker: per-round results, captain analysis, trade ledger, coach rating."""

import logging
from typing import Optional

from sqlalchemy import select, desc, func
from sqlalchemy.orm import Session

from src.models.database import (
    SeasonResult, MyTeamSlot, Player, SupercoachScore, Trade, get_session,
)

logger = logging.getLogger(__name__)


def calculate_round_result(
    session: Session,
    user_id: int,
    season: int,
    round_num: int,
) -> Optional[int]:
    """Calculate and store a user's team score for a completed round."""

    # Get field players
    slots = (
        session.execute(
            select(MyTeamSlot, Player)
            .join(Player, MyTeamSlot.player_id == Player.id)
            .where(MyTeamSlot.user_id == user_id)
        )
        .all()
    )

    total_score = 0
    captain_id = None
    captain_name = None
    captain_score = 0
    best_id = None
    best_name = None
    best_score = 0
    field_count = 0

    for slot, player in slots:
        if slot.position_slot.startswith("BENCH"):
            continue

        sc = session.execute(
            select(SupercoachScore)
            .where(
                SupercoachScore.player_id == player.id,
                SupercoachScore.season == season,
                SupercoachScore.round == round_num,
            )
        ).scalar_one_or_none()

        score = sc.score if sc and sc.score else 0

        if slot.is_captain:
            captain_id = player.id
            captain_name = player.name
            captain_score = score
            total_score += score * 2
        else:
            total_score += score

        if score > 0:
            field_count += 1

        if score > best_score:
            best_score = score
            best_id = player.id
            best_name = player.name

    captain_points = captain_score * 2
    captain_was_optimal = (captain_id == best_id) if captain_id and best_id else False

    # Count trades this round
    trades_count = session.execute(
        select(func.count(Trade.id))
        .where(Trade.season == season, Trade.round == round_num)
    ).scalar() or 0

    # Upsert
    existing = session.execute(
        select(SeasonResult)
        .where(SeasonResult.season == season, SeasonResult.round == round_num)
    ).scalar_one_or_none()

    if existing:
        existing.team_score = total_score
        existing.captain_id = captain_id
        existing.captain_name = captain_name
        existing.captain_score = captain_score
        existing.captain_points = captain_points
        existing.optimal_captain_id = best_id
        existing.optimal_captain_name = best_name
        existing.optimal_captain_score = best_score
        existing.captain_was_optimal = captain_was_optimal
        existing.field_players = field_count
        existing.trades_used = trades_count
    else:
        session.add(SeasonResult(
            season=season,
            round=round_num,
            team_score=total_score,
            captain_id=captain_id,
            captain_name=captain_name,
            captain_score=captain_score,
            captain_points=captain_points,
            optimal_captain_id=best_id,
            optimal_captain_name=best_name,
            optimal_captain_score=best_score,
            captain_was_optimal=captain_was_optimal,
            field_players=field_count,
            trades_used=trades_count,
        ))

    session.commit()
    logger.info("Round %d result: %d pts, captain %s (%d)", round_num, total_score, captain_name, captain_score)
    return total_score


def get_season_tracker_data(
    session: Session,
    user_id: int,
    season: int,
) -> dict:
    """Get full season performance data."""

    # Per-round results
    results = (
        session.execute(
            select(SeasonResult)
            .where(SeasonResult.season == season)
            .order_by(SeasonResult.round)
        )
        .scalars()
        .all()
    )

    rounds_played = len(results)
    total_score = sum(r.team_score or 0 for r in results)
    avg_score = round(total_score / rounds_played) if rounds_played else 0
    best = max(results, key=lambda r: r.team_score or 0) if results else None
    worst = min(results, key=lambda r: r.team_score or 0) if results else None

    # Captain stats
    captain_correct = sum(1 for r in results if r.captain_was_optimal)
    captain_hit_rate = round(captain_correct / rounds_played * 100) if rounds_played else 0
    points_left = sum(
        ((r.optimal_captain_score or 0) - (r.captain_score or 0)) * 2
        for r in results if not r.captain_was_optimal
    )

    # Trade ledger
    trades = (
        session.execute(
            select(Trade)
            .where(Trade.season == season)
            .order_by(Trade.round)
        )
        .scalars()
        .all()
    )

    trade_ledger = []
    for t in trades:
        p_out = session.get(Player, t.player_out_id)
        p_in = session.get(Player, t.player_in_id)

        # Avg scores after the trade
        in_avg = session.execute(
            select(func.avg(SupercoachScore.score))
            .where(
                SupercoachScore.player_id == t.player_in_id,
                SupercoachScore.season == season,
                SupercoachScore.round > t.round,
                SupercoachScore.score.isnot(None),
            )
        ).scalar() or 0

        out_avg = session.execute(
            select(func.avg(SupercoachScore.score))
            .where(
                SupercoachScore.player_id == t.player_out_id,
                SupercoachScore.season == season,
                SupercoachScore.round > t.round,
                SupercoachScore.score.isnot(None),
            )
        ).scalar() or 0

        games = session.execute(
            select(func.count(SupercoachScore.id))
            .where(
                SupercoachScore.player_id == t.player_in_id,
                SupercoachScore.season == season,
                SupercoachScore.round > t.round,
                SupercoachScore.score.isnot(None),
            )
        ).scalar() or 0

        verdict = "too_early" if games < 2 else ("win" if in_avg > out_avg else "loss" if out_avg > in_avg else "even")

        trade_ledger.append({
            "round": t.round,
            "out_name": p_out.name if p_out else "?",
            "in_name": p_in.name if p_in else "?",
            "price_out": t.price_out,
            "price_in": t.price_in,
            "in_avg_since": round(in_avg, 1),
            "out_avg_since": round(out_avg, 1),
            "games_since": games,
            "verdict": verdict,
            "was_boost": t.was_boost,
        })

    trades_won = sum(1 for t in trade_ledger if t["verdict"] == "win")
    trades_lost = sum(1 for t in trade_ledger if t["verdict"] == "loss")
    trades_pending = sum(1 for t in trade_ledger if t["verdict"] == "too_early")

    return {
        "season": season,
        "summary": {
            "rounds_played": rounds_played,
            "total_score": total_score,
            "average_score": avg_score,
            "best_round": {"round": best.round, "score": best.team_score} if best else None,
            "worst_round": {"round": worst.round, "score": worst.team_score} if worst else None,
            "trades_used": len(trades),
            "trades_remaining": 30 - len(trades),
        },
        "round_scores": [
            {
                "round": r.round,
                "score": r.team_score,
                "captain": r.captain_name,
                "captain_score": r.captain_points,
                "field_players": r.field_players,
            }
            for r in results
        ],
        "captain": {
            "hit_rate": captain_hit_rate,
            "correct": captain_correct,
            "total": rounds_played,
            "points_left_on_table": points_left,
            "history": [
                {
                    "round": r.round,
                    "picked": r.captain_name,
                    "picked_score": r.captain_score,
                    "picked_doubled": r.captain_points,
                    "optimal": r.optimal_captain_name,
                    "optimal_score": r.optimal_captain_score,
                    "was_correct": r.captain_was_optimal,
                }
                for r in results
            ],
        },
        "trades": {
            "total": len(trades),
            "won": trades_won,
            "lost": trades_lost,
            "pending": trades_pending,
            "ledger": trade_ledger,
        },
    }
