from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup
from sqlalchemy import select

from src.models.database import get_session, Player, SupercoachScore, Injury
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


def _parse_salary(text: str) -> int | None:
    """Parse salary string like '$521,800' to integer 521800."""
    if not text:
        return None
    cleaned = text.strip().replace("$", "").replace(",", "")
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_score(text: str) -> int | None:
    """Parse score string, handling DNP and emergency markers."""
    if not text or text.strip() in ("", "-", "DNP", "EMG"):
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def _parse_value(text: str) -> float | None:
    """Parse value string like '27.3'."""
    if not text or text.strip() in ("", "-"):
        return None
    try:
        return float(text.strip())
    except ValueError:
        return None


AFL_TEAMS = {
    "Adelaide Crows", "Brisbane Lions", "Carlton Blues", "Collingwood Magpies",
    "Essendon Bombers", "Fremantle Dockers", "Geelong Cats", "Gold Coast Suns",
    "GWS Giants", "Hawthorn Hawks", "Melbourne Demons", "North Melbourne Kangaroos",
    "Port Adelaide Power", "Richmond Tigers", "St Kilda Saints",
    "Sydney Swans", "West Coast Eagles", "Western Bulldogs",
    # Short forms used by FootyWire
    "Adelaide", "Brisbane", "Carlton", "Collingwood", "Essendon", "Fremantle",
    "Geelong", "Gold Coast", "GWS", "Hawthorn", "Melbourne", "North Melbourne",
    "Port Adelaide", "Richmond", "St Kilda", "Sydney", "West Coast", "Western Bulldogs",
    "Crows", "Lions", "Blues", "Magpies", "Bombers", "Dockers", "Cats", "Suns",
    "Giants", "Hawks", "Demons", "Kangaroos", "Power", "Tigers", "Saints",
    "Swans", "Eagles", "Bulldogs",
}


class FootyWireScraper(BaseScraper):
    """Scraper for FootyWire SuperCoach scores and injury list."""

    async def scrape(self, **kwargs: Any) -> int:
        """Scrape SuperCoach round scores. kwargs: season, round, scrape_injuries."""
        season = kwargs.get("season", self.config.season)
        round_num = kwargs.get("round", self.config.current_round)
        scrape_injuries = kwargs.get("scrape_injuries", True)

        total = 0
        total += await self.scrape_supercoach_round(season, round_num)

        if scrape_injuries:
            total += await self.scrape_injury_list()

        return total

    async def scrape_supercoach_round(self, season: int, round_num: int) -> int:
        """Scrape a single round of SuperCoach scores from FootyWire."""
        url = (
            f"{self.config.scraping.footywire_supercoach_url}"
            f"?year={season}&round={round_num}&p=&s=T"
        )
        logger.info(f"Scraping SuperCoach scores: season={season} round={round_num}")

        html = await self.fetch(url)
        soup = BeautifulSoup(html, "lxml")

        # Find the data table: look for the table where rows have 7 cells
        # with a player link in the second cell (href starts with "pu-")
        rows = []
        for table in soup.find_all("table"):
            table_rows = table.find_all("tr")
            if len(table_rows) < 10:
                continue
            # Check first data row for the expected structure
            for tr in table_rows[:5]:
                cells = tr.find_all("td")
                if len(cells) == 7:
                    link = cells[1].find("a")
                    if link and link.get("href", "").startswith("pu-"):
                        # Found the data table — get all rows
                        rows = [
                            r for r in table_rows
                            if len(r.find_all("td")) == 7
                        ]
                        break
            if rows:
                break

        if not rows:
            logger.warning(f"No player rows found for {season} R{round_num}")
            return 0

        count = 0
        session = get_session()
        try:
            for row in rows:
                data = self._parse_supercoach_row(row)
                if data is None:
                    continue

                player = self._upsert_player(session, data)
                self._upsert_score(session, player, season, round_num, data)
                count += 1

            session.commit()
            logger.info(f"Scraped {count} players for {season} R{round_num}")
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        return count

    def _parse_supercoach_row(self, row: Any) -> dict[str, Any] | None:
        """Parse a single table row into a dict of player data."""
        cells = row.find_all("td")
        if len(cells) < 7:
            return None

        # Extract player link and name
        player_link = cells[1].find("a")
        if not player_link:
            return None

        player_name = player_link.get_text(strip=True)
        # Clean injury markers from name
        player_name = player_name.replace("*Injured*", "").strip()

        footywire_id = player_link.get("href", "")

        # Extract team
        team_cell = cells[2]
        team_link = team_cell.find("a")
        team = team_link.get_text(strip=True) if team_link else team_cell.get_text(strip=True)

        return {
            "name": player_name,
            "team": team,
            "footywire_id": footywire_id,
            "current_salary": _parse_salary(cells[3].get_text()),
            "round_salary": _parse_salary(cells[4].get_text()),
            "round_score": _parse_score(cells[5].get_text()),
            "round_value": _parse_value(cells[6].get_text()),
        }

    def _upsert_player(self, session: Any, data: dict[str, Any]) -> Player:
        """Find or create a player by footywire_id."""
        stmt = select(Player).where(Player.footywire_id == data["footywire_id"])
        player = session.execute(stmt).scalar_one_or_none()

        if player is None:
            player = Player(
                name=data["name"],
                team=data["team"],
                footywire_id=data["footywire_id"],
            )
            session.add(player)
            session.flush()
        else:
            player.team = data["team"]
            player.name = data["name"]

        return player

    def _upsert_score(
        self,
        session: Any,
        player: Player,
        season: int,
        round_num: int,
        data: dict[str, Any],
    ) -> None:
        """Insert or update a SuperCoach score record."""
        stmt = select(SupercoachScore).where(
            SupercoachScore.player_id == player.id,
            SupercoachScore.season == season,
            SupercoachScore.round == round_num,
        )
        score = session.execute(stmt).scalar_one_or_none()

        score_val = data["round_score"]
        dnp = score_val is None

        if score is None:
            score = SupercoachScore(
                player_id=player.id,
                season=season,
                round=round_num,
                score=score_val,
                price=data["round_salary"],
                value=data["round_value"],
                did_not_play=dnp,
            )
            session.add(score)
        else:
            score.score = score_val
            score.price = data["round_salary"]
            score.value = data["round_value"]
            score.did_not_play = dnp

    async def scrape_injury_list(self) -> int:
        """Scrape the current injury list from FootyWire."""
        url = self.config.scraping.footywire_injury_url
        logger.info("Scraping FootyWire injury list")

        html = await self.fetch(url)
        soup = BeautifulSoup(html, "lxml")

        session = get_session()
        count = 0
        try:
            # FootyWire injury page structure:
            # - Team header rows: single cell with "Team Name (N Players)"
            # - Sub-header rows: "Player", "Injury", "Returning"
            # - Data rows: 3 cells with player name, injury type, return timeline
            # All nested inside a main table
            all_rows = soup.find_all("tr")

            current_team = None
            for row in all_rows:
                cells = row.find_all("td")

                # Team header: single cell like "Brisbane Lions (8 Players)"
                if len(cells) == 1:
                    text = cells[0].get_text(strip=True)
                    # Match team headers with player count pattern
                    team_match = re.match(r"^(.+?)\s*\(\d+\s*Player", text)
                    if team_match:
                        current_team = team_match.group(1).strip()
                    continue

                # Data rows: exactly 3 cells
                if len(cells) != 3:
                    continue

                # Only process rows after we've found a team header
                if current_team is None:
                    continue

                player_name = cells[0].get_text(strip=True)
                injury_type = cells[1].get_text(strip=True)
                estimated_return = cells[2].get_text(strip=True)

                # Skip header rows
                if not player_name or player_name.lower() == "player":
                    continue
                if injury_type.lower() == "injury":
                    continue

                # Skip junk: footer text, nav elements, team grid rows
                if len(player_name) > 40:
                    continue
                if player_name in AFL_TEAMS:
                    # This is a team-grid nav row, not a player
                    continue
                if any(
                    junk in player_name.lower()
                    for junk in ("footywire", "©", "remember me", "match period")
                ):
                    continue

                # Find matching player in DB by name
                player = session.execute(
                    select(Player).where(Player.name.ilike(f"%{player_name}%")).limit(1)
                ).scalar_one_or_none()

                if player is None:
                    # Create a stub player
                    player = Player(name=player_name, team=current_team or "Unknown")
                    session.add(player)
                    session.flush()

                # Upsert injury (one active injury per player)
                existing = session.execute(
                    select(Injury).where(Injury.player_id == player.id).limit(1)
                ).scalar_one_or_none()

                if existing:
                    existing.injury_type = injury_type
                    existing.estimated_return = estimated_return
                    existing.status = "OUT"
                else:
                    injury = Injury(
                        player_id=player.id,
                        injury_type=injury_type,
                        estimated_return=estimated_return,
                        status="OUT",
                    )
                    session.add(injury)

                count += 1

            session.commit()
            logger.info(f"Scraped {count} injuries")
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        return count

    async def scrape_multiple_rounds(self, season: int, start_round: int, end_round: int) -> int:
        """Scrape multiple rounds sequentially (respecting rate limits)."""
        total = 0
        for r in range(start_round, end_round + 1):
            total += await self.scrape_supercoach_round(season, r)
        return total
