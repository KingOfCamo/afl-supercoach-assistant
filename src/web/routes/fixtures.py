from __future__ import annotations

"""AFL fixture endpoints — fetches match data from AFL.com.au API."""

import logging
from typing import Optional

from fastapi import APIRouter, Query

from src.scrapers.afl_lineups import AFL_V2_BASE, COMP_SEASON_IDS, AflLineupScraper
from src.utils.config import get_config

router = APIRouter()
logger = logging.getLogger(__name__)


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
            "home_team": home_team.get("name", ""),
            "home_abbr": home_team.get("abbr", ""),
            "home_score": home.get("score", {}).get("totalScore"),
            "away_team": away_team.get("name", ""),
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
