from __future__ import annotations

"""Player search and detail endpoints."""

from fastapi import APIRouter, Query
from sqlalchemy import desc, select

from src.models.database import (
    DfsPlayerStats,
    Injury,
    MyTeamSlot,
    Player,
    SupercoachScore,
    get_session,
)

router = APIRouter()


@router.get("/search")
def search_players(
    q: str = Query("", min_length=0),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Search players by name with autocomplete data."""
    if not q or len(q) < 2:
        return {"players": []}

    session = get_session()
    try:
        # Get players matching query
        players = (
            session.execute(
                select(Player)
                .where(Player.name.ilike(f"%{q}%"), Player.is_active == True)  # noqa: E712
                .order_by(Player.name)
                .limit(limit)
            )
            .scalars()
            .all()
        )

        # Get current team player IDs
        team_ids = set(
            session.execute(select(MyTeamSlot.player_id)).scalars().all()
        )

        results = []
        for p in players:
            # Get DFS data for salary/avg
            dfs = session.execute(
                select(DfsPlayerStats)
                .where(DfsPlayerStats.player_id == p.id)
                .order_by(desc(DfsPlayerStats.season))
                .limit(1)
            ).scalar_one_or_none()

            results.append({
                "id": p.id,
                "name": p.name,
                "team": p.team,
                "position": p.position,
                "salary": dfs.salary if dfs else None,
                "sc_avg": round(dfs.sc_avg, 1) if dfs and dfs.sc_avg else None,
                "is_on_team": p.id in team_ids,
            })

        return {"players": results}
    finally:
        session.close()


@router.get("/{player_id}")
def get_player(player_id: int) -> dict:
    """Get full player detail with scores, DFS stats, injury, and projection."""
    from src.analytics.projections import project_player
    from src.utils.config import get_config

    config = get_config()

    session = get_session()
    try:
        player = session.get(Player, player_id)
        if player is None:
            return {"error": "Player not found"}

        # Recent scores
        scores = (
            session.execute(
                select(SupercoachScore)
                .where(SupercoachScore.player_id == player_id)
                .order_by(desc(SupercoachScore.season), desc(SupercoachScore.round))
                .limit(10)
            )
            .scalars()
            .all()
        )

        # DFS stats
        dfs = session.execute(
            select(DfsPlayerStats)
            .where(DfsPlayerStats.player_id == player_id)
            .order_by(desc(DfsPlayerStats.season))
            .limit(1)
        ).scalar_one_or_none()

        # Injury
        injury = session.execute(
            select(Injury).where(Injury.player_id == player_id)
        ).scalar_one_or_none()

        # Projection
        proj = project_player(player_id, config.current_round)

        return {
            "id": player.id,
            "name": player.name,
            "team": player.team,
            "position": player.position,
            "scores": [
                {
                    "season": s.season,
                    "round": s.round,
                    "score": s.score,
                    "price": s.price,
                }
                for s in scores
            ],
            "dfs": {
                "salary": dfs.salary,
                "sc_avg": dfs.sc_avg,
                "ownership_pct": dfs.ownership_pct,
                "cba_pct": dfs.cba_pct,
                "games_played": dfs.games_played,
                "last_5_avg": dfs.last_5_avg,
                "prev_year_avg": dfs.prev_year_avg,
            }
            if dfs
            else None,
            "injury": {
                "injury_type": injury.injury_type,
                "estimated_return": injury.estimated_return,
                "status": injury.status,
            }
            if injury
            else None,
            "projection": {
                "projected_score": proj.projected_score,
                "floor": proj.floor,
                "ceiling": proj.ceiling,
                "opponent": proj.opponent,
                "dvp_rank": proj.dvp_rank,
                "confidence": proj.confidence,
            }
            if proj
            else None,
        }
    finally:
        session.close()
