from __future__ import annotations

"""Player search and detail endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select

import math

from src.models.database import (
    ByeRound,
    DfsPlayerStats,
    Fixture,
    Injury,
    MatchStats,
    MyTeamSlot,
    Ownership,
    Player,
    SupercoachScore,
    TeamDVP,
    get_session,
)
from src.utils.config import get_config
from src.utils.teams import normalize_team
from src.web.middleware.authenticate import get_current_user

router = APIRouter()


@router.get("/search")
def search_players(
    q: str = Query("", min_length=0),
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
) -> dict:
    """Search players by name with autocomplete data."""
    if not q or len(q) < 2:
        return {"players": []}

    user_id = user["user_id"]
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

        # Get current user's team player IDs
        team_ids = set(
            session.execute(
                select(MyTeamSlot.player_id).where(MyTeamSlot.user_id == user_id)
            ).scalars().all()
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


@router.get("/compare")
def compare_players(
    ids: str = Query(..., description="Comma-separated player IDs (2-3)"),
    round_num: int = Query(None, alias="round"),
    user: dict = Depends(get_current_user),
) -> dict:
    """Compare 2-3 players side by side with full stats."""
    from sqlalchemy import func
    from src.analytics.byes import detect_current_round

    player_ids = [int(x.strip()) for x in ids.split(",") if x.strip()]
    if len(player_ids) < 2 or len(player_ids) > 3:
        return {"error": "Provide 2 or 3 player IDs", "players": []}

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

        comparisons = []
        for pid in player_ids:
            player = session.get(Player, pid)
            if not player:
                continue

            # Season scores
            scores = (
                session.execute(
                    select(SupercoachScore)
                    .where(SupercoachScore.player_id == pid, SupercoachScore.season == config.season, SupercoachScore.score.isnot(None))
                    .order_by(SupercoachScore.round)
                )
                .scalars()
                .all()
            )
            all_scores = [s.score for s in scores if s.score is not None]
            season_avg = sum(all_scores) / len(all_scores) if all_scores else 0
            last_3 = all_scores[-3:] if len(all_scores) >= 3 else all_scores
            last_5 = all_scores[-5:] if len(all_scores) >= 5 else all_scores
            last_3_avg = sum(last_3) / len(last_3) if last_3 else 0
            last_5_avg = sum(last_5) / len(last_5) if last_5 else 0

            # Consistency
            consistency = None
            if len(all_scores) >= 3 and season_avg > 0:
                std_dev = math.sqrt(sum((s - season_avg) ** 2 for s in all_scores) / len(all_scores))
                consistency = max(0, min(100, round(100 - (std_dev / season_avg * 100))))

            # Price info
            latest_sc = scores[-1] if scores else None
            dfs = session.execute(
                select(DfsPlayerStats).where(DfsPlayerStats.player_id == pid).order_by(desc(DfsPlayerStats.season)).limit(1)
            ).scalar_one_or_none()
            price = (latest_sc.price if latest_sc and latest_sc.price else None) or (dfs.salary if dfs else None)
            breakeven = latest_sc.breakeven if latest_sc else None

            # Price trend
            price_rows = [s for s in scores if s.price is not None]
            price_trend = 0
            if len(price_rows) >= 2:
                price_trend = (price_rows[-1].price or 0) - (price_rows[max(-4, -len(price_rows))].price or 0)

            # Upcoming fixtures
            team_name = normalize_team(player.team)
            fixtures = (
                session.execute(
                    select(Fixture)
                    .where(Fixture.season == config.season, Fixture.round.between(r, r + 4))
                    .where((Fixture.home_team == team_name) | (Fixture.away_team == team_name))
                    .order_by(Fixture.round)
                )
                .scalars()
                .all()
            )

            fixture_list = []
            for f in fixtures:
                is_home = f.home_team == team_name
                opponent = f.away_team if is_home else f.home_team
                # DVP stars (simple: check TeamDVP rank)
                dvp = session.execute(
                    select(TeamDVP.dvp_rank)
                    .where(TeamDVP.team == opponent, TeamDVP.season == config.season)
                    .where(TeamDVP.position == (player.position or "MID").split("/")[0])
                    .order_by(desc(TeamDVP.round))
                    .limit(1)
                ).scalar_one_or_none()
                stars = 3
                if dvp:
                    stars = 5 if dvp >= 15 else 4 if dvp >= 11 else 3 if dvp >= 7 else 2 if dvp >= 4 else 1

                fixture_list.append({
                    "round": f.round, "opponent": opponent, "is_home": is_home,
                    "dvp_stars": stars, "is_bye": False,
                })

            # Check for bye rounds in the range
            for rnd in range(r, r + 5):
                if not any(fx["round"] == rnd for fx in fixture_list):
                    bye = session.execute(
                        select(ByeRound).where(ByeRound.season == config.season, ByeRound.round == rnd, ByeRound.team == team_name)
                    ).scalar_one_or_none()
                    if bye:
                        fixture_list.append({"round": rnd, "opponent": None, "is_home": False, "dvp_stars": 0, "is_bye": True})
            fixture_list.sort(key=lambda f: f["round"])

            # Ownership
            own = session.execute(
                select(Ownership).where(Ownership.player_id == pid, Ownership.season == config.season)
                .order_by(desc(Ownership.round)).limit(1)
            ).scalar_one_or_none()

            # Next bye
            next_bye = session.execute(
                select(ByeRound.round).where(ByeRound.season == config.season, ByeRound.team == team_name, ByeRound.round >= r)
                .order_by(ByeRound.round).limit(1)
            ).scalar_one_or_none()

            # CBA/TOG
            cba = session.execute(
                select(func.avg(MatchStats.cba_pct), func.avg(MatchStats.time_on_ground_pct))
                .where(MatchStats.player_id == pid, MatchStats.season == config.season)
            ).one()

            comparisons.append({
                "id": player.id,
                "name": player.name,
                "team": player.team,
                "position": player.position,
                "price": price,
                "scoring": {
                    "season_avg": round(season_avg, 1),
                    "last_3_avg": round(last_3_avg, 1),
                    "last_5_avg": round(last_5_avg, 1),
                    "high": max(all_scores) if all_scores else 0,
                    "low": min(all_scores) if all_scores else 0,
                    "consistency": consistency,
                    "games_played": len(all_scores),
                    "all_scores": all_scores,
                },
                "pricing": {
                    "price": price,
                    "breakeven": breakeven,
                    "price_trend_3wk": price_trend,
                },
                "fixtures": fixture_list[:5],
                "advanced": {
                    "cba_pct": round(cba[0], 1) if cba[0] else None,
                    "tog_pct": round(cba[1], 1) if cba[1] else None,
                    "ownership_pct": own.ownership_pct if own else None,
                    "ownership_change": own.ownership_change if own else None,
                    "next_bye": next_bye,
                },
            })

        return {"players": comparisons, "round": r}
    finally:
        session.close()
