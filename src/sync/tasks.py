from __future__ import annotations

"""Sync task functions that wrap existing importers/scrapers.

Each task is isolated — a failure in one source does not block others.
Tasks use smart skip logic to reduce polling on non-match days.
"""

import asyncio
import logging
from typing import Dict

from src.sync.scheduler import record_error, record_start, record_success, should_skip
from src.utils.config import get_config

logger = logging.getLogger(__name__)

SOURCE_SUPERCOACH_API = "supercoach_api"
SOURCE_SUPERCOACH_ROUND = "supercoach_round"
SOURCE_FOOTYWIRE_SCORES = "footywire_scores"
SOURCE_FOOTYWIRE_INJURIES = "footywire_injuries"
SOURCE_AFLCOMAU_INJURIES = "aflcomau_injuries"
SOURCE_FANFOOTY = "fanfooty"
SOURCE_SQUIGGLE = "squiggle"


async def sync_supercoach_players(force: bool = False) -> None:
    """Sync player data from the official SuperCoach API (round=0)."""
    source = SOURCE_SUPERCOACH_API
    # Always runs on 6h interval — no skip logic needed
    record_start(source)
    try:
        from src.importers.supercoach_api import sync_from_supercoach_api

        config = get_config()
        result = await asyncio.to_thread(sync_from_supercoach_api, config.season)
        total = result.get("updated", 0) + result.get("created", 0)
        record_success(source, total)
        logger.info("SuperCoach API sync: %d players processed", total)
    except Exception as e:
        record_error(source, str(e))
        logger.error("SuperCoach API sync failed: %s", e, exc_info=True)


async def sync_supercoach_round_data(force: bool = False) -> None:
    """Sync per-round data (scores, prices, breakevens) from SuperCoach API."""
    source = SOURCE_SUPERCOACH_ROUND
    if not force and should_skip(source, 4.0):
        logger.debug("Skipping %s — not match day and ran recently", source)
        return
    record_start(source)
    try:
        from src.importers.supercoach_api import sync_round_from_supercoach_api

        config = get_config()
        result = await asyncio.to_thread(
            sync_round_from_supercoach_api, config.season, config.current_round
        )
        record_success(source, result.get("updated", 0))
        logger.info("SuperCoach round data sync: %s", result)
    except Exception as e:
        record_error(source, str(e))
        logger.error("SuperCoach round sync failed: %s", e, exc_info=True)


async def sync_footywire_scores(force: bool = False) -> None:
    """Scrape SuperCoach scores from FootyWire for current round."""
    source = SOURCE_FOOTYWIRE_SCORES
    if not force and should_skip(source, 4.0):
        logger.debug("Skipping %s — not match day and ran recently", source)
        return
    record_start(source)
    try:
        from src.scrapers.footywire import FootyWireScraper

        config = get_config()
        scraper = FootyWireScraper()
        try:
            count = await scraper.scrape_supercoach_round(
                config.season, config.current_round
            )
            record_success(source, count)
            logger.info("FootyWire scores: %d players scraped", count)
        finally:
            await scraper.close()
    except Exception as e:
        record_error(source, str(e))
        logger.error("FootyWire scores failed: %s", e, exc_info=True)


async def sync_footywire_injuries(force: bool = False) -> None:
    """Scrape current injury list from FootyWire."""
    source = SOURCE_FOOTYWIRE_INJURIES
    # Always runs on 4h interval — no skip logic needed
    record_start(source)
    try:
        from src.scrapers.footywire import FootyWireScraper

        scraper = FootyWireScraper()
        try:
            count = await scraper.scrape_injury_list()
            record_success(source, count)
            logger.info("FootyWire injuries: %d injuries scraped", count)
        finally:
            await scraper.close()
    except Exception as e:
        record_error(source, str(e))
        logger.error("FootyWire injuries failed: %s", e, exc_info=True)


async def sync_aflcomau_injuries(force: bool = False) -> None:
    """Scrape current injury list from AFL.com.au (official source)."""
    source = SOURCE_AFLCOMAU_INJURIES
    # Runs on 4h interval — no skip logic needed
    record_start(source)
    try:
        from src.scrapers.aflcomau import AflComAuScraper

        scraper = AflComAuScraper()
        try:
            count = await scraper.scrape_injury_list()
            record_success(source, count)
            logger.info("AFL.com.au injuries: %d injuries scraped", count)
        finally:
            await scraper.close()
    except Exception as e:
        record_error(source, str(e))
        logger.error("AFL.com.au injuries failed: %s", e, exc_info=True)


async def sync_fanfooty(force: bool = False) -> None:
    """Scrape SuperCoach scores from FanFooty for current round."""
    source = SOURCE_FANFOOTY
    if not force and should_skip(source, 4.0):
        logger.debug("Skipping %s — not match day and ran recently", source)
        return
    record_start(source)
    try:
        from src.scrapers.fanfooty import FanFootyScraper

        config = get_config()
        scraper = FanFootyScraper()
        try:
            count = await scraper.scrape_round(config.season, config.current_round)
            record_success(source, count)
            logger.info("FanFooty scores: %d players scraped", count)
        finally:
            await scraper.close()
    except Exception as e:
        record_error(source, str(e))
        logger.error("FanFooty scores failed: %s", e, exc_info=True)


async def sync_squiggle(force: bool = False) -> None:
    """Scrape fixtures from Squiggle API."""
    source = SOURCE_SQUIGGLE
    if not force and should_skip(source, 6.0):
        logger.debug("Skipping %s — not match day and ran recently", source)
        return
    record_start(source)
    try:
        from src.scrapers.squiggle import SquiggleScraper

        config = get_config()
        scraper = SquiggleScraper()
        try:
            count = await scraper.scrape_fixtures(config.season)
            record_success(source, count)
            logger.info("Squiggle fixtures: %d fixtures synced", count)
        finally:
            await scraper.close()
    except Exception as e:
        record_error(source, str(e))
        logger.error("Squiggle fixtures failed: %s", e, exc_info=True)


async def sync_all(force: bool = False) -> Dict[str, str]:
    """Run all sync tasks sequentially. Used for manual trigger."""
    results: Dict[str, str] = {}
    tasks = [
        ("supercoach_api", sync_supercoach_players),
        ("squiggle", sync_squiggle),
        ("footywire_scores", sync_footywire_scores),
        ("footywire_injuries", sync_footywire_injuries),
        ("aflcomau_injuries", sync_aflcomau_injuries),
        ("fanfooty", sync_fanfooty),
        ("supercoach_round", sync_supercoach_round_data),
    ]
    for name, task_fn in tasks:
        try:
            await task_fn(force=force)
            results[name] = "success"
        except Exception as e:
            results[name] = f"error: {e}"
    return results
