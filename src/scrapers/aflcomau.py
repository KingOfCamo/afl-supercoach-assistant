from __future__ import annotations

"""AFL.com.au injury list scraper.

Scrapes the official AFL injury list page which provides comprehensive
injury data for all 18 clubs, including injury type, estimated return,
and 'In the Mix' narrative context.

The page has 18 HTML tables in alphabetical team order.
"""

import logging
import re

from bs4 import BeautifulSoup
from sqlalchemy import select

from src.models.database import Injury, Player, get_session
from src.scrapers.base import BaseScraper
from src.utils.teams import normalize_team

logger = logging.getLogger(__name__)

INJURY_LIST_URL = "https://www.afl.com.au/matches/injury-list"

# Teams in alphabetical order matching the page layout
TEAMS_ALPHABETICAL = [
    "Adelaide",
    "Brisbane",
    "Carlton",
    "Collingwood",
    "Essendon",
    "Fremantle",
    "Geelong",
    "Gold Coast",
    "GWS",
    "Hawthorn",
    "Melbourne",
    "North Melbourne",
    "Port Adelaide",
    "Richmond",
    "St Kilda",
    "Sydney",
    "West Coast",
    "Western Bulldogs",
]


def _parse_status(estimated_return: str) -> str:
    """Derive injury status from estimated return text."""
    est = estimated_return.lower().strip()
    if est in ("test", "testing"):
        return "TEST"
    if est in ("tbc", "tbd"):
        return "TBC"
    if "suspension" in est or "suspend" in est:
        return "SUSPENDED"
    if est in ("season", "indefinite"):
        return "OUT"
    return "OUT"


class AflComAuScraper(BaseScraper):
    """Scrapes injury data from AFL.com.au official injury list."""

    async def scrape(self, **kwargs) -> int:
        """Scrape the AFL.com.au injury list page."""
        return await self.scrape_injury_list()

    async def scrape_injury_list(self) -> int:
        """Scrape all injuries from AFL.com.au and upsert into DB.

        Returns:
            Number of injury records processed.
        """
        html = await self.fetch(INJURY_LIST_URL)
        soup = BeautifulSoup(html, "html.parser")

        article = soup.find("article")
        if not article:
            logger.warning("No article element found on AFL.com.au injury page")
            return 0

        tables = article.find_all("table")
        if len(tables) != 18:
            logger.warning(
                f"Expected 18 injury tables, found {len(tables)}. "
                "AFL.com.au page structure may have changed."
            )

        session = get_session()
        count = 0

        try:
            for i, table in enumerate(tables):
                team = TEAMS_ALPHABETICAL[i] if i < len(TEAMS_ALPHABETICAL) else None
                if team is None:
                    continue

                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) != 3:
                        continue

                    player_name = cells[0].get_text(strip=True)
                    injury_type = cells[1].get_text(strip=True)
                    estimated_return = cells[2].get_text(strip=True)

                    if not player_name or player_name.lower() == "player":
                        continue

                    status = _parse_status(estimated_return)

                    # Find player in DB — match by name, prefer same team
                    # Use normalize_team to handle aliases (e.g. "Swans" vs "Sydney")
                    candidates = session.execute(
                        select(Player).where(
                            Player.name.ilike(f"%{player_name}%"),
                        )
                    ).scalars().all()

                    player = None
                    if candidates:
                        # Prefer player on the same team (normalized)
                        norm_team = normalize_team(team)
                        for c in candidates:
                            if normalize_team(c.team) == norm_team:
                                player = c
                                break
                        # Fallback: first name match
                        if player is None:
                            player = candidates[0]

                    if player is None:
                        # Create stub player
                        player = Player(name=player_name, team=team)
                        session.add(player)
                        session.flush()

                    # Upsert injury (one active injury per player)
                    existing = session.execute(
                        select(Injury).where(
                            Injury.player_id == player.id
                        ).limit(1)
                    ).scalar_one_or_none()

                    if existing:
                        existing.injury_type = injury_type
                        existing.estimated_return = estimated_return
                        existing.status = status
                    else:
                        inj = Injury(
                            player_id=player.id,
                            injury_type=injury_type,
                            estimated_return=estimated_return,
                            status=status,
                        )
                        session.add(inj)

                    count += 1

            # Clear injuries for players no longer on any injury list
            # Get all player IDs we just processed
            injured_player_ids = set()
            for i, table in enumerate(tables):
                if i >= len(TEAMS_ALPHABETICAL):
                    break
                team = TEAMS_ALPHABETICAL[i]
                for row in table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) != 3:
                        continue
                    pname = cells[0].get_text(strip=True)
                    if not pname or pname.lower() == "player":
                        continue
                    p = session.execute(
                        select(Player).where(
                            Player.name.ilike(f"%{pname}%"),
                        ).limit(1)
                    ).scalar_one_or_none()
                    if p:
                        injured_player_ids.add(p.id)

            # Remove stale injuries for players no longer listed
            all_injuries = session.execute(select(Injury)).scalars().all()
            stale_count = 0
            for inj in all_injuries:
                if inj.player_id not in injured_player_ids:
                    session.delete(inj)
                    stale_count += 1

            if stale_count:
                logger.info(f"Removed {stale_count} stale injury records")

            session.commit()
            logger.info(f"Scraped {count} injuries from AFL.com.au")

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        return count
