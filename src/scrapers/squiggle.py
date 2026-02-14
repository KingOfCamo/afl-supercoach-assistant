from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select

from src.models.database import Fixture, get_session
from src.scrapers.base import BaseScraper
from src.utils.teams import normalize_team

logger = logging.getLogger(__name__)


class SquiggleScraper(BaseScraper):
    """Scraper for the Squiggle AFL API (JSON).

    API docs: https://api.squiggle.com.au
    Requires a descriptive User-Agent header.
    """

    async def _get_client(self):
        """Override to use Squiggle-specific user agent."""
        import httpx

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": self.config.scraping.squiggle_user_agent},
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )
        return self._client

    async def fetch_json(self, url: str) -> dict:
        """Fetch a URL and parse JSON response."""
        text = await self.fetch(url)
        return json.loads(text)

    async def scrape(self, **kwargs: Any) -> int:
        """Scrape fixtures from Squiggle. kwargs: season, round."""
        season = kwargs.get("season", self.config.season)
        round_num = kwargs.get("round")

        total = 0
        if round_num:
            total += await self.scrape_fixtures(season, round_num=round_num)
        else:
            total += await self.scrape_fixtures(season)
        return total

    async def scrape_fixtures(self, season: int, round_num: int | None = None) -> int:
        """Scrape AFL fixtures/results from Squiggle API.

        Args:
            season: AFL season year.
            round_num: Optional specific round. If None, fetches entire season.

        Returns:
            Number of fixtures upserted.
        """
        base_url = self.config.scraping.squiggle_base_url
        url = f"{base_url}/?q=games;year={season}"
        if round_num is not None:
            url += f";round={round_num}"

        logger.info(f"Fetching Squiggle fixtures: season={season} round={round_num or 'all'}")

        data = await self.fetch_json(url)
        games = data.get("games", [])

        if not games:
            logger.warning(f"No games returned from Squiggle for {season}")
            return 0

        count = 0
        session = get_session()
        try:
            for game in games:
                count += self._upsert_fixture(session, game, season)
            session.commit()
            logger.info(f"Upserted {count} fixtures for {season}")
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        return count

    def _upsert_fixture(self, session: Any, game: dict, season: int) -> int:
        """Insert or update a single fixture from Squiggle game data."""
        squiggle_id = game.get("id")
        if squiggle_id is None:
            return 0

        home_team = normalize_team(game.get("hteam", ""))
        away_team = normalize_team(game.get("ateam", ""))
        round_num = game.get("round", 0)

        if not home_team or not away_team:
            logger.warning(f"Skipping game {squiggle_id}: missing team info")
            return 0

        # Check if fixture exists by squiggle_id
        existing = session.execute(
            select(Fixture).where(Fixture.squiggle_id == squiggle_id)
        ).scalar_one_or_none()

        # Parse date string from Squiggle (format: "2026-03-13 19:40:00")
        game_date = None
        date_str = game.get("date")
        if date_str:
            from datetime import datetime
            try:
                game_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                pass

        home_score = game.get("hscore")
        away_score = game.get("ascore")
        is_complete = game.get("complete", 0) == 100

        if existing:
            existing.home_team = home_team
            existing.away_team = away_team
            existing.venue = game.get("venue")
            existing.date = game_date
            existing.home_score = home_score
            existing.away_score = away_score
            existing.is_complete = is_complete
        else:
            fixture = Fixture(
                season=season,
                round=round_num,
                home_team=home_team,
                away_team=away_team,
                venue=game.get("venue"),
                date=game_date,
                home_score=home_score,
                away_score=away_score,
                is_complete=is_complete,
                squiggle_id=squiggle_id,
            )
            session.add(fixture)

        return 1

    async def fetch_ladder(self, season: int, round_num: int | None = None) -> list[dict]:
        """Fetch AFL ladder from Squiggle API.

        Args:
            season: AFL season year.
            round_num: Optional round to get ladder as-of.

        Returns:
            List of dicts with team standings sorted by rank.
        """
        base_url = self.config.scraping.squiggle_base_url
        url = f"{base_url}/?q=standings;year={season}"
        if round_num is not None:
            url += f";round={round_num}"

        logger.info(f"Fetching Squiggle ladder: season={season} round={round_num or 'latest'}")

        data = await self.fetch_json(url)
        standings = data.get("standings", [])

        ladder = []
        for team in standings:
            ladder.append({
                "rank": team.get("rank", 0),
                "team": normalize_team(team.get("name", "")),
                "played": team.get("played", 0),
                "wins": team.get("wins", 0),
                "losses": team.get("losses", 0),
                "draws": team.get("draws", 0),
                "pts": team.get("pts", 0),
                "percentage": team.get("percentage", 0.0),
                "for": team.get("for", 0),
                "against": team.get("against", 0),
            })

        ladder.sort(key=lambda t: t["rank"])
        return ladder
