from __future__ import annotations

"""Ownership data scraper — scrapes weekly ownership swings from SuperCoachTalk."""

import logging
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.database import Ownership, Player, get_session
from src.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class OwnershipScraper(BaseScraper):
    """Scrapes ownership swing data from SuperCoachTalk."""

    async def scrape(self, **kwargs) -> int:
        season = kwargs.get("season", self.config.season)
        round_num = kwargs.get("round", self.config.current_round)
        return await self.scrape_ownership_swings(season, round_num)

    async def scrape_ownership_swings(self, season: int, round_num: int) -> int:
        """Scrape weekly ownership swings from SuperCoachTalk.

        Parses "+9.1% Elijah Tsatas" style text from the site.
        Returns count of records upserted.
        """
        url = "https://supercoachtalk.com/"
        try:
            html = await self.fetch(url)
        except Exception as e:
            logger.warning("Failed to fetch SuperCoachTalk: %s", e)
            return 0

        # Parse ownership swing patterns: "+9.1% Elijah Tsatas" or "-5.2% Jack Sinclair"
        pattern = r'([+-]\d+\.?\d*)%\s+([A-Z][a-z]+(?:\s+[A-Za-z\'-]+)+)'
        matches = re.findall(pattern, html)

        if not matches:
            logger.info("No ownership swings found on SuperCoachTalk")
            return 0

        session = get_session()
        try:
            count = 0
            for pct_str, player_name in matches:
                change = float(pct_str)
                player = _find_player(session, player_name.strip())
                if not player:
                    logger.debug("Ownership: no match for '%s'", player_name)
                    continue

                _upsert_ownership(
                    session, player.id, season, round_num,
                    ownership_pct=None, change=change, source="supercoachtalk",
                )
                count += 1

            session.commit()
            logger.info("Scraped %d ownership swings from SuperCoachTalk", count)
            return count
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _find_player(session: Session, name: str) -> Optional[Player]:
    """Find a player by name with fuzzy matching."""
    # Exact match
    player = session.execute(
        select(Player).where(Player.name == name)
    ).scalar_one_or_none()
    if player:
        return player

    # Case-insensitive
    player = session.execute(
        select(Player).where(Player.name.ilike(name))
    ).scalar_one_or_none()
    if player:
        return player

    # Surname match
    parts = name.strip().split()
    if parts:
        surname = parts[-1]
        if len(surname) >= 3:
            results = session.execute(
                select(Player).where(Player.name.ilike(f"% {surname}"))
            ).scalars().all()
            if len(results) == 1:
                return results[0]

    return None


def _upsert_ownership(
    session: Session,
    player_id: int,
    season: int,
    round_num: int,
    ownership_pct: Optional[float] = None,
    change: Optional[float] = None,
    source: str = "scraped",
) -> None:
    """Insert or update ownership data."""
    existing = session.execute(
        select(Ownership).where(
            Ownership.player_id == player_id,
            Ownership.season == season,
            Ownership.round == round_num,
        )
    ).scalar_one_or_none()

    if existing:
        if ownership_pct is not None:
            existing.ownership_pct = ownership_pct
        if change is not None:
            existing.ownership_change = change
        existing.source = source
    else:
        session.add(Ownership(
            player_id=player_id,
            season=season,
            round=round_num,
            ownership_pct=ownership_pct,
            ownership_change=change,
            source=source,
        ))
