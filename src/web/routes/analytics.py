from __future__ import annotations

"""Analytics endpoints: projections, captain, trades, injuries."""

import dataclasses

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from src.models.database import Injury, MyTeamSlot, Player, get_session
from src.utils.config import get_config
from src.web.middleware.authenticate import get_current_user

router = APIRouter()


@router.get("/projections")
def get_projections(
    round_num: int = Query(None, alias="round"),
    team_only: bool = Query(True),
    user: dict = Depends(get_current_user),
) -> dict:
    """Get score projections for a round."""
    from src.analytics.projections import project_round

    config = get_config()
    r = round_num or config.current_round

    results = project_round(r, team_only=team_only, save=False, user_id=user["user_id"])

    projections = []
    for p in results:
        d = dataclasses.asdict(p)
        projections.append(d)

    total = sum(p.projected_score for p in results)

    return {
        "round": r,
        "projections": projections,
        "total_projected": round(total, 1),
    }


@router.get("/captain")
def get_captain(
    round_num: int = Query(None, alias="round"),
    top_n: int = Query(10),
    user: dict = Depends(get_current_user),
) -> dict:
    """Get captain rankings."""
    from src.analytics.captain import rank_captain_options

    config = get_config()
    r = round_num or config.current_round

    candidates = rank_captain_options(r, top_n=top_n, user_id=user["user_id"])

    return {
        "round": r,
        "candidates": [dataclasses.asdict(c) for c in candidates],
    }


@router.get("/trades")
def get_trades(
    round_num: int = Query(None, alias="round"),
    budget: int = Query(0),
    user: dict = Depends(get_current_user),
) -> dict:
    """Get trade recommendations."""
    from src.analytics.trade_engine import suggest_trades

    config = get_config()
    r = round_num or config.current_round

    recommendations = suggest_trades(r, budget=budget, user_id=user["user_id"])

    return {
        "round": r,
        "recommendations": [dataclasses.asdict(rec) for rec in recommendations],
    }


@router.get("/injuries")
def get_injuries(user: dict = Depends(get_current_user)) -> dict:
    """Get injuries for team players."""
    user_id = user["user_id"]
    session = get_session()
    try:
        # Get this user's team player IDs
        team_ids = set(
            session.execute(
                select(MyTeamSlot.player_id).where(MyTeamSlot.user_id == user_id)
            ).scalars().all()
        )

        injuries = session.execute(
            select(Injury).order_by(Injury.updated_at.desc())
        ).scalars().all()

        team_injuries = []
        all_injuries = []

        for inj in injuries:
            player = session.get(Player, inj.player_id)
            entry = {
                "player_name": player.name if player else "Unknown",
                "team": player.team if player else "-",
                "injury_type": inj.injury_type,
                "estimated_return": inj.estimated_return,
                "status": inj.status,
            }
            all_injuries.append(entry)
            if inj.player_id in team_ids:
                team_injuries.append(entry)

        return {
            "team_injuries": team_injuries,
            "all_injuries_count": len(all_injuries),
        }
    finally:
        session.close()
