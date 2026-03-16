from __future__ import annotations

"""Singleton APScheduler instance and sync status tracking.

Provides:
- get_scheduler(): creates/returns the shared AsyncIOScheduler
- is_match_day(): checks fixtures table for games today
- Status tracking helpers for recording sync outcomes
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from src.models.database import Fixture, get_session
from src.utils.config import get_config

logger = logging.getLogger(__name__)

# ── Per-source status tracking (in-memory, resets on restart) ──

SyncStatus = Dict[str, Any]

sync_status: Dict[str, SyncStatus] = {}

_scheduler: Optional[AsyncIOScheduler] = None


def _default_status() -> SyncStatus:
    return {
        "last_run": None,
        "last_success": None,
        "last_error": None,
        "error_message": None,
        "records_synced": 0,
        "is_running": False,
    }


def record_start(source: str) -> None:
    if source not in sync_status:
        sync_status[source] = _default_status()
    sync_status[source]["last_run"] = datetime.utcnow().isoformat()
    sync_status[source]["is_running"] = True


def record_success(source: str, count: int) -> None:
    if source not in sync_status:
        sync_status[source] = _default_status()
    sync_status[source]["last_success"] = datetime.utcnow().isoformat()
    sync_status[source]["records_synced"] = count
    sync_status[source]["is_running"] = False
    sync_status[source]["last_error"] = None
    sync_status[source]["error_message"] = None


def record_error(source: str, msg: str) -> None:
    if source not in sync_status:
        sync_status[source] = _default_status()
    sync_status[source]["last_error"] = datetime.utcnow().isoformat()
    sync_status[source]["error_message"] = msg
    sync_status[source]["is_running"] = False


# ── Scheduler singleton ──


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300,
            },
            timezone="Australia/Melbourne",
        )
    return _scheduler


# ── Match-day detection ──


def is_match_day() -> bool:
    """Check if there are AFL games scheduled for today (Melbourne time)."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

    melb_tz = ZoneInfo("Australia/Melbourne")
    today = datetime.now(melb_tz).date()

    config = get_config()
    session = get_session()
    try:
        fixtures = session.execute(
            select(Fixture).where(
                Fixture.season == config.season,
                Fixture.date.isnot(None),
            )
        ).scalars().all()

        for f in fixtures:
            if f.date is not None:
                fixture_date = f.date.date() if isinstance(f.date, datetime) else f.date
                if fixture_date == today:
                    return True
        return False
    except Exception as e:
        logger.warning("Could not check match day: %s", e)
        return False
    finally:
        session.close()


def should_skip(source: str, off_day_hours: float) -> bool:
    """Return True if not match day AND last success was within off_day_hours."""
    if is_match_day():
        return False

    status = sync_status.get(source)
    if not status or not status.get("last_success"):
        return False

    last_success = datetime.fromisoformat(status["last_success"])
    elapsed = datetime.utcnow() - last_success
    if elapsed < timedelta(hours=off_day_hours):
        logger.debug(
            "Skipping %s: off-day, last success %s ago (threshold %sh)",
            source, elapsed, off_day_hours,
        )
        return True
    return False
