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


@router.get("/live")
def get_live(
    round_num: int = Query(None, alias="round"),
    user: dict = Depends(get_current_user),
) -> dict:
    """Get live scoring summary for a round."""
    from src.analytics.live_scores import get_live_round

    config = get_config()
    r = round_num or config.current_round

    summary = get_live_round(r, user_id=user["user_id"])

    return {
        "round": summary.round_num,
        "season": summary.season,
        "total_live_score": summary.total_live_score,
        "projected_total": summary.projected_total,
        "games_complete": summary.games_complete,
        "games_in_progress": summary.games_in_progress,
        "games_upcoming": summary.games_upcoming,
        "captain_name": summary.captain_name,
        "captain_score": summary.captain_score,
        "players": [
            {
                "player_id": p.player_id,
                "player_name": p.player_name,
                "team": p.team,
                "position": p.position,
                "position_slot": p.position_slot,
                "is_captain": p.is_captain,
                "is_vice_captain": p.is_vice_captain,
                "is_emergency": p.is_emergency,
                "live_score": p.live_score,
                "projected_final": p.projected_final,
                "match_status": p.match_status,
                "opponent": p.opponent,
            }
            for p in summary.players
        ],
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
            "all_injuries": all_injuries,
            "all_injuries_count": len(all_injuries),
        }
    finally:
        session.close()


@router.get("/bye-impact")
def get_bye_impact_endpoint(
    round_num: int = Query(None, alias="round"),
    user: dict = Depends(get_current_user),
) -> dict:
    """Get bye round impact analysis for the user's team."""
    from src.analytics.byes import get_bye_impact

    config = get_config()
    r = round_num or config.current_round
    session = get_session()
    try:
        return get_bye_impact(session, user["user_id"], config.season, r)
    finally:
        session.close()


@router.get("/bye-planner")
def get_bye_planner_endpoint(
    user: dict = Depends(get_current_user),
) -> dict:
    """Get full bye round planner data: matrix, risk score, summaries."""
    from src.analytics.byes import get_bye_planner_data

    config = get_config()
    session = get_session()
    try:
        return get_bye_planner_data(session, user["user_id"], config.season)
    finally:
        session.close()


@router.get("/trade-warroom")
def get_trade_warroom(
    round_num: int = Query(None, alias="round"),
    user: dict = Depends(get_current_user),
) -> dict:
    """Get aggregated Trade War Room data: team, problems, byes, injuries, history."""
    from src.analytics.trade_warroom import get_warroom_data
    from src.analytics.byes import detect_current_round

    config = get_config()
    session = get_session()
    try:
        r = round_num or config.current_round
        try:
            detected = detect_current_round(session, config.season)
            if detected > 0 and round_num is None:
                r = detected
        except Exception:
            pass
        return get_warroom_data(session, user["user_id"], config.season, r)
    finally:
        session.close()


@router.get("/ownership/movers")
def get_ownership_movers(
    round_num: int = Query(None, alias="round"),
    limit: int = Query(10),
    user: dict = Depends(get_current_user),
) -> dict:
    """Top ownership gainers and losers for a round."""
    from src.models.database import Ownership, Player
    from src.analytics.byes import detect_current_round

    config = get_config()
    session = get_session()
    try:
        r = round_num or config.current_round
        try:
            detected = detect_current_round(session, config.season)
            if detected > 0 and round_num is None:
                r = detected
        except Exception:
            pass

        rows = (
            session.execute(
                select(Ownership, Player)
                .join(Player, Ownership.player_id == Player.id)
                .where(Ownership.season == config.season, Ownership.round == r)
                .where(Ownership.ownership_change.isnot(None))
                .order_by(Ownership.ownership_change.desc())
            )
            .all()
        )

        gainers = []
        losers = []
        for own, player in rows:
            entry = {
                "player_name": player.name,
                "team": player.team,
                "position": player.position,
                "ownership_pct": own.ownership_pct,
                "ownership_change": own.ownership_change,
            }
            if own.ownership_change and own.ownership_change > 0:
                gainers.append(entry)
            elif own.ownership_change and own.ownership_change < 0:
                losers.append(entry)

        return {
            "round": r,
            "gainers": gainers[:limit],
            "losers": losers[-limit:] if losers else [],
        }
    finally:
        session.close()


@router.get("/ownership/pods")
def get_ownership_pods(
    min_avg: float = Query(80),
    max_ownership: float = Query(15),
    position: str = Query("all"),
    user: dict = Depends(get_current_user),
) -> dict:
    """Find POD players: high average, low ownership."""
    from src.models.database import Ownership, SupercoachScore
    from sqlalchemy import func

    config = get_config()
    session = get_session()
    try:
        # Get players with scores this season
        query = (
            select(
                Player.id, Player.name, Player.team, Player.position,
                func.avg(SupercoachScore.score).label("avg_score"),
            )
            .join(SupercoachScore, SupercoachScore.player_id == Player.id)
            .where(SupercoachScore.season == config.season, SupercoachScore.score.isnot(None))
            .group_by(Player.id)
            .having(func.avg(SupercoachScore.score) >= min_avg)
            .order_by(func.avg(SupercoachScore.score).desc())
            .limit(30)
        )
        if position != "all":
            query = query.where(Player.position.ilike(f"%{position}%"))

        results = session.execute(query).all()

        pods = []
        for pid, name, team, pos, avg_score in results:
            # Get latest ownership
            own = session.execute(
                select(Ownership.ownership_pct)
                .where(Ownership.player_id == pid, Ownership.season == config.season)
                .order_by(Ownership.round.desc())
                .limit(1)
            ).scalar_one_or_none()

            own_pct = own if own else 0.0
            if own_pct <= max_ownership:
                pods.append({
                    "player_name": name,
                    "team": team,
                    "position": pos,
                    "avg_score": round(avg_score, 1),
                    "ownership_pct": own_pct,
                })

        return {"pods": pods[:20]}
    finally:
        session.close()


@router.get("/ownership/template")
def get_template_players(
    threshold: float = Query(30),
    user: dict = Depends(get_current_user),
) -> dict:
    """Players owned by >threshold% of coaches."""
    from src.models.database import Ownership

    config = get_config()
    session = get_session()
    try:
        rows = (
            session.execute(
                select(Ownership, Player)
                .join(Player, Ownership.player_id == Player.id)
                .where(
                    Ownership.season == config.season,
                    Ownership.ownership_pct >= threshold,
                )
                .order_by(Ownership.ownership_pct.desc())
            )
            .all()
        )

        templates = []
        seen = set()
        for own, player in rows:
            if player.id in seen:
                continue
            seen.add(player.id)
            templates.append({
                "player_name": player.name,
                "team": player.team,
                "position": player.position,
                "ownership_pct": own.ownership_pct,
            })

        return {"template_players": templates}
    finally:
        session.close()
