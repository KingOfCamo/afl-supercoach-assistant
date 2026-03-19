from __future__ import annotations

"""AFL fixture endpoints — fetches match data from AFL.com.au API."""

import logging
from typing import Optional

from fastapi import APIRouter, Query

from src.scrapers.afl_lineups import AFL_V2_BASE, COMP_SEASON_IDS, AflLineupScraper
from src.utils.config import get_config
from src.utils.teams import normalize_team

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/db-round")
def get_db_round_fixtures(
    round_num: Optional[int] = Query(None, description="Round number"),
    season: Optional[int] = Query(None, description="Season year"),
) -> dict:
    """Get fixtures from the local DB (Squiggle data) with bye info.

    Faster than hitting the AFL API and includes bye team data.
    """
    from src.models.database import Fixture, ByeRound, get_session
    from sqlalchemy import select

    config = get_config()
    target_season = season or config.season
    target_round = round_num or config.current_round

    # Auto-detect current round if not specified
    if round_num is None:
        try:
            from src.analytics.byes import detect_current_round
            session = get_session()
            try:
                detected = detect_current_round(session, target_season)
                if detected > 0:
                    target_round = detected
            finally:
                session.close()
        except Exception:
            pass

    session = get_session()
    try:
        fixtures = (
            session.execute(
                select(Fixture)
                .where(Fixture.season == target_season, Fixture.round == target_round)
                .order_by(Fixture.date)
            )
            .scalars()
            .all()
        )

        matches = []
        for f in fixtures:
            matches.append({
                "home_team": f.home_team,
                "away_team": f.away_team,
                "venue": f.venue,
                "date": f.date.isoformat() if f.date else None,
                "home_score": f.home_score,
                "away_score": f.away_score,
                "is_complete": f.is_complete,
                "status": "CONCLUDED" if f.is_complete else "SCHEDULED",
            })

        # Get bye teams for this round
        bye_teams = (
            session.execute(
                select(ByeRound.team)
                .where(ByeRound.season == target_season, ByeRound.round == target_round)
                .order_by(ByeRound.team)
            )
            .scalars()
            .all()
        )

        # Get total rounds available
        from sqlalchemy import func
        max_round = session.execute(
            select(func.max(Fixture.round)).where(Fixture.season == target_season)
        ).scalar() or 1

        return {
            "season": target_season,
            "round": target_round,
            "max_round": max_round,
            "matches": matches,
            "bye_teams": list(bye_teams),
        }
    finally:
        session.close()


@router.get("/round")
async def get_round_fixtures(
    round_num: Optional[int] = Query(None, description="Round number (defaults to current)"),
    season: Optional[int] = Query(None, description="Season year (defaults to current)"),
) -> dict:
    """Get all fixtures for a round from the AFL.com.au API.

    Returns match details including teams, venue, date/time, and scores.
    """
    config = get_config()
    target_season = season or config.season
    target_round = round_num or config.current_round

    comp_season_id = COMP_SEASON_IDS.get(target_season)
    if not comp_season_id:
        return {"error": f"No data for season {target_season}", "matches": []}

    scraper = AflLineupScraper()
    try:
        raw_matches = await scraper.get_matches_for_round(target_season, target_round)
    except Exception as e:
        logger.error("Failed to fetch fixtures from AFL API: %s", e)
        return {"error": str(e), "matches": []}
    finally:
        await scraper.close()

    matches = []
    for m in raw_matches:
        home = m.get("home", {})
        away = m.get("away", {})
        home_team = home.get("team", {})
        away_team = away.get("team", {})
        venue = m.get("venue", {})

        matches.append({
            "id": m.get("id"),
            "status": m.get("status", ""),
            "home_team": normalize_team(home_team.get("name", "")),
            "home_abbr": home_team.get("abbr", ""),
            "home_score": home.get("score", {}).get("totalScore"),
            "away_team": normalize_team(away_team.get("name", "")),
            "away_abbr": away_team.get("abbr", ""),
            "away_score": away.get("score", {}).get("totalScore"),
            "venue": venue.get("name", ""),
            "venue_state": venue.get("state", ""),
            "date": m.get("utcStartTime", ""),
            "local_start_time": m.get("compSeason", {}).get("name", ""),
        })

    return {
        "season": target_season,
        "round": target_round,
        "matches": matches,
    }
