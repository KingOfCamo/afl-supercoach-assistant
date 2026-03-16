from __future__ import annotations

"""Background sync task wrappers for each data source.

Each function follows the pattern:
1. Check should_skip() for off-day throttling
2. record_start() to mark running
3. Call the existing importer/scraper
4. record_success() or record_error()

Sync importers (blocking) run via asyncio.to_thread().
Async scrapers run directly.
"""

import asyncio
import logging
from typing import Any, Dict

from src.sync.scheduler import record_error, record_start, record_success, should_skip
from src.utils.config import get_config

logger = logging.getLogger(__name__)


# ── SuperCoach API: Players ──


async def sync_supercoach_players() -> None:
    """Sync player names, teams, positions from the SuperCoach API."""
    source = "supercoach_players"
    if should_skip(source, off_day_hours=6.0):
        return

    record_start(source)
    try:
        from src.importers.supercoach_api import sync_from_supercoach_api

        config = get_config()
        result = await asyncio.to_thread(sync_from_supercoach_api, season=config.season)
        count = result.get("updated", 0) + result.get("created", 0)
        record_success(source, count)
        logger.info("sync_supercoach_players: %d updated/created", count)
    except Exception as e:
        record_error(source, str(e))
        logger.error("sync_supercoach_players failed: %s", e)


# ── SuperCoach API: Round Data ──


async def sync_supercoach_round_data() -> None:
    """Sync per-round scores, prices, breakevens from the SuperCoach API."""
    source = "supercoach_round"
    if should_skip(source, off_day_hours=4.0):
        return

    record_start(source)
    try:
        from src.importers.supercoach_api import sync_round_from_supercoach_api

        config = get_config()
        result = await asyncio.to_thread(
            sync_round_from_supercoach_api,
            season=config.season,
            round_num=config.current_round,
        )
        count = result.get("updated", 0)
        record_success(source, count)
        logger.info("sync_supercoach_round_data: %d updated", count)
    except Exception as e:
        record_error(source, str(e))
        logger.error("sync_supercoach_round_data failed: %s", e)


# ── FootyWire: Scores ──


async def sync_footywire_scores() -> None:
    """Scrape SuperCoach scores from FootyWire for current round."""
    source = "footywire_scores"
    if should_skip(source, off_day_hours=4.0):
        return

    record_start(source)
    try:
        from src.scrapers.footywire import FootyWireScraper

        config = get_config()
        scraper = FootyWireScraper()
        try:
            count = await scraper.scrape_supercoach_round(config.season, config.current_round)
        finally:
            await scraper.close()

        if count > 0:
            record_success(source, count)
        else:
            record_success(source, 0)
        logger.info("sync_footywire_scores: %d records", count)
    except Exception as e:
        record_error(source, str(e))
        logger.error("sync_footywire_scores failed: %s", e)


# ── FootyWire: Injuries ──


async def sync_footywire_injuries() -> None:
    """Scrape injury list from FootyWire."""
    source = "footywire_injuries"
    if should_skip(source, off_day_hours=4.0):
        return

    record_start(source)
    try:
        from src.scrapers.footywire import FootyWireScraper

        scraper = FootyWireScraper()
        try:
            count = await scraper.scrape_injury_list()
        finally:
            await scraper.close()

        record_success(source, count)
        logger.info("sync_footywire_injuries: %d records", count)
    except Exception as e:
        record_error(source, str(e))
        logger.error("sync_footywire_injuries failed: %s", e)


# ── AFL.com.au: Injuries ──


async def sync_aflcomau_injuries() -> None:
    """Scrape injury list from AFL.com.au."""
    source = "aflcomau_injuries"
    if should_skip(source, off_day_hours=4.0):
        return

    record_start(source)
    try:
        from src.scrapers.aflcomau import AflComAuScraper

        scraper = AflComAuScraper()
        try:
            count = await scraper.scrape_injury_list()
        finally:
            await scraper.close()

        record_success(source, count)
        logger.info("sync_aflcomau_injuries: %d records", count)
    except Exception as e:
        record_error(source, str(e))
        logger.error("sync_aflcomau_injuries failed: %s", e)


# ── FanFooty: Live Scores ──


async def sync_fanfooty() -> None:
    """Scrape live SuperCoach scores from FanFooty."""
    source = "fanfooty"
    # Aggressive on match days (15min interval), skip quickly off-day
    if should_skip(source, off_day_hours=4.0):
        return

    record_start(source)
    try:
        from src.scrapers.fanfooty import FanFootyScraper

        config = get_config()
        scraper = FanFootyScraper()
        try:
            count = await scraper.scrape_round(config.season, config.current_round)
        finally:
            await scraper.close()

        # Don't record 0-count as success (allows faster retry)
        if count > 0:
            record_success(source, count)
        else:
            logger.warning("sync_fanfooty: 0 scores returned")
            record_success(source, 0)
        logger.info("sync_fanfooty: %d records", count)
    except Exception as e:
        record_error(source, str(e))
        logger.error("sync_fanfooty failed: %s", e)


# ── Squiggle: Fixtures ──


async def sync_squiggle() -> None:
    """Scrape fixtures and results from Squiggle API."""
    source = "squiggle"
    if should_skip(source, off_day_hours=6.0):
        return

    record_start(source)
    try:
        from src.scrapers.squiggle import SquiggleScraper

        config = get_config()
        scraper = SquiggleScraper()
        try:
            count = await scraper.scrape_fixtures(config.season)
        finally:
            await scraper.close()

        record_success(source, count)
        logger.info("sync_squiggle: %d fixtures", count)
    except Exception as e:
        record_error(source, str(e))
        logger.error("sync_squiggle failed: %s", e)


# ── AFL Lineups ──


async def sync_afl_lineups() -> None:
    """Scrape team lineups from AFL.com.au."""
    source = "afl_lineups"
    if should_skip(source, off_day_hours=4.0):
        return

    record_start(source)
    try:
        from src.scrapers.afl_lineups import AflLineupScraper

        config = get_config()
        scraper = AflLineupScraper()
        try:
            count = await scraper.scrape_round_lineups(config.season, config.current_round)
        finally:
            await scraper.close()

        record_success(source, count)
        logger.info("sync_afl_lineups: %d entries", count)
    except Exception as e:
        record_error(source, str(e))
        logger.error("sync_afl_lineups failed: %s", e)


# ── AFL News Injuries ──


async def sync_afl_news_injuries() -> None:
    """Scrape AFL.com.au news articles for early injury detection."""
    source = "afl_news_injuries"
    if should_skip(source, off_day_hours=4.0):
        return

    record_start(source)
    try:
        from src.scrapers.afl_news import AflNewsScraper

        scraper = AflNewsScraper()
        try:
            count = await scraper.scrape_news_injuries()
        finally:
            await scraper.close()

        record_success(source, count)
        logger.info("sync_afl_news_injuries: %d records", count)
    except Exception as e:
        record_error(source, str(e))
        logger.error("sync_afl_news_injuries failed: %s", e)


# ── Run all sources ──


async def sync_all() -> Dict[str, Any]:
    """Run all sync tasks sequentially. Returns per-source summary."""
    from src.sync.scheduler import sync_status

    tasks = [
        ("squiggle", sync_squiggle),
        ("supercoach_players", sync_supercoach_players),
        ("supercoach_round", sync_supercoach_round_data),
        ("footywire_scores", sync_footywire_scores),
        ("footywire_injuries", sync_footywire_injuries),
        ("aflcomau_injuries", sync_aflcomau_injuries),
        ("fanfooty", sync_fanfooty),
        ("afl_lineups", sync_afl_lineups),
        ("afl_news_injuries", sync_afl_news_injuries),
    ]

    for name, fn in tasks:
        try:
            await fn()
        except Exception as e:
            logger.error("sync_all: %s failed: %s", name, e)

    return dict(sync_status)
