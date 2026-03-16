from __future__ import annotations

"""FanFooty scraper for SuperCoach scoring data.

FanFooty (fanfooty.com.au) provides live SuperCoach scoring data during games,
including real-time point breakdowns. This scraper fetches:
- Live/final SuperCoach scores per player per round
- Player-level score breakdowns (kicks, marks, tackles scoring)

This provides an alternative data source to FootyWire, useful for:
- Cross-validation of scores
- Faster score availability during live games
- Historical data gaps
"""

import logging
from typing import Any, Optional

from bs4 import BeautifulSoup
from sqlalchemy import select

from src.models.database import Player, SupercoachScore, get_session, init_db
from src.scrapers.base import BaseScraper
from src.utils.teams import normalize_team

logger = logging.getLogger(__name__)

FANFOOTY_BASE = "https://www.fanfooty.com.au"


class FanFootyScraper(BaseScraper):
    """Scraper for FanFooty SuperCoach data.

    FanFooty provides SuperCoach scoring breakdowns. This scraper parses
    the HTML pages to extract per-player scoring data.
    """

    async def scrape(self, **kwargs: Any) -> int:
        """Scrape SuperCoach scores from FanFooty.

        kwargs:
            season: Season year
            round: Round number
        """
        season = kwargs.get("season", self.config.season)
        round_num = kwargs.get("round", self.config.current_round)

        init_db()
        return await self.scrape_round(season, round_num)

    async def scrape_round(self, season: int, round_num: int) -> int:
        """Scrape SuperCoach scores for a specific round.

        Args:
            season: AFL season year.
            round_num: Round number.

        Returns:
            Number of scores upserted.
        """
        url = f"{FANFOOTY_BASE}/supercoach/{season}/round-{round_num}"
        logger.info(f"Fetching FanFooty scores: {season} R{round_num}")

        try:
            html = await self.fetch(url)
        except Exception as e:
            logger.error(f"Failed to fetch FanFooty: {e}")
            raise  # Let caller handle — don't silently return 0

        return self._parse_scores_page(html, season, round_num)

    def _parse_scores_page(self, html: str, season: int, round_num: int) -> int:
        """Parse the FanFooty scores page HTML.

        FanFooty organises scores by match, with player rows showing:
        - Player name
        - Team
        - SuperCoach score
        - Score breakdown (kicks, marks, etc.)
        """
        if not html or len(html) < 200:
            logger.warning(
                "FanFooty returned empty/short HTML (%d bytes) for %d R%d",
                len(html) if html else 0, season, round_num,
            )
            return 0

        soup = BeautifulSoup(html, "lxml")
        count = 0
        parsed_rows = 0
        unmatched_players: list[str] = []

        session = get_session()
        try:
            # FanFooty uses tables with class "sc-scores" or similar
            tables = soup.find_all("table")

            if not tables:
                # Log preview for debugging HTML structure changes
                preview = html[:500].replace("\n", " ")
                logger.warning(
                    "FanFooty: no tables found in HTML for %d R%d. "
                    "Page structure may have changed. Preview: %s",
                    season, round_num, preview,
                )
                return 0

            for table in tables:
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) < 3:
                        continue

                    result = self._parse_player_row(cells)
                    if result is None:
                        continue

                    parsed_rows += 1
                    name, team, score = result
                    if self._upsert_score(session, name, team, score, season, round_num):
                        count += 1

            session.commit()

            if parsed_rows == 0:
                logger.warning(
                    "FanFooty: found %d tables but 0 valid player rows for %d R%d",
                    len(tables), season, round_num,
                )
            else:
                logger.info(
                    "FanFooty %d R%d: %d upserted out of %d parsed rows",
                    season, round_num, count, parsed_rows,
                )
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        return count

    def _parse_player_row(self, cells: list) -> Optional[tuple[str, str, int]]:
        """Extract player name, team, and score from a table row.

        Returns (name, team, score) or None if row doesn't contain valid data.
        """
        try:
            name_cell = cells[0].get_text(strip=True)
            team_cell = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            score_cell = cells[-1].get_text(strip=True)

            if not name_cell or not score_cell:
                return None

            # Skip header rows
            if name_cell.lower() in ("player", "name", ""):
                return None

            try:
                score = int(score_cell)
            except ValueError:
                return None

            team = normalize_team(team_cell) if team_cell else ""
            return (name_cell, team, score)

        except (IndexError, AttributeError):
            return None

    def _upsert_score(
        self,
        session: object,
        player_name: str,
        team: str,
        score: int,
        season: int,
        round_num: int,
    ) -> bool:
        """Insert or update a player's score. Returns True if upserted."""
        # Try exact name match first
        player = session.execute(  # type: ignore[union-attr]
            select(Player).where(Player.name == player_name)
        ).scalar_one_or_none()

        # Try fuzzy match if exact fails
        if player is None:
            player = session.execute(  # type: ignore[union-attr]
                select(Player).where(Player.name.ilike(f"%{player_name}%"))
            ).scalar_one_or_none()

        if player is None:
            # Try matching by last name + team
            parts = player_name.split()
            if len(parts) >= 2:
                last_name = parts[-1]
                candidates = session.execute(  # type: ignore[union-attr]
                    select(Player).where(Player.name.ilike(f"%{last_name}%"))
                ).scalars().all()

                if team:
                    team_matches = [
                        p for p in candidates
                        if normalize_team(p.team) == team
                    ]
                    if len(team_matches) == 1:
                        player = team_matches[0]

            # Try first initial + last name (e.g. "S. Fisher" -> "Sam Fisher")
            if player is None and len(parts) >= 2:
                initial = parts[0].rstrip(".")
                if len(initial) == 1:
                    last_name = parts[-1]
                    candidates = session.execute(  # type: ignore[union-attr]
                        select(Player).where(
                            Player.name.ilike(f"{initial}%{last_name}%")
                        )
                    ).scalars().all()
                    if team:
                        candidates = [
                            p for p in candidates
                            if normalize_team(p.team) == team
                        ]
                    if len(candidates) == 1:
                        player = candidates[0]

            if player is None:
                logger.warning(f"Player not found: {player_name} ({team})")
                return False

        # Check for existing score
        existing = session.execute(  # type: ignore[union-attr]
            select(SupercoachScore).where(
                SupercoachScore.player_id == player.id,
                SupercoachScore.season == season,
                SupercoachScore.round == round_num,
            )
        ).scalar_one_or_none()

        if existing:
            if existing.score is None or existing.score != score:
                existing.score = score
                return True
            return False
        else:
            new_score = SupercoachScore(
                player_id=player.id,
                season=season,
                round=round_num,
                score=score,
            )
            session.add(new_score)  # type: ignore[union-attr]
            return True

    async def scrape_multiple_rounds(
        self, season: int, start_round: int, end_round: int
    ) -> int:
        """Scrape multiple rounds of FanFooty data."""
        total = 0
        for r in range(start_round, end_round + 1):
            count = await self.scrape_round(season, r)
            total += count
            logger.info(f"FanFooty {season} R{r}: {count} scores")
        return total
