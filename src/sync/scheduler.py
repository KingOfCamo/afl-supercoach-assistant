from __future__ import annotations

"""Singleton APScheduler instance and sync status tracking."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

# In-memory sync status tracking — resets on process restart
sync_status: Dict[str, dict] = {}

_scheduler: Optional[AsyncIOScheduler] = None


def _init_status(source: str) -> None:
    """Ensure a source has a status entry."""
    if source not in sync_status:
        sync_status[source] = {
            "last_run": None,
            "last_success": None,
            "last_error": None,
            "error_message": None,
            "records_synced": 0,
            "is_running": False,
        }


def record_start(source: str) -> None:
    _init_status(source)
    sync_status[source]["is_running"] = True
    sync_status[source]["last_run"] = datetime.now(timezone.utc).isoformat()


def record_success(source: str, records: int) -> None:
    _init_status(source)
    sync_status[source]["is_running"] = False
    sync_status[source]["last_success"] = datetime.now(timezone.utc).isoformat()
    sync_status[source]["records_synced"] = records
    sync_status[source]["last_error"] = None
    sync_status[source]["error_message"] = None


def record_error(source: str, error: str) -> None:
    _init_status(source)
    sync_status[source]["is_running"] = False
    sync_status[source]["last_error"] = datetime.now(timezone.utc).isoformat()
    sync_status[source]["error_message"] = error


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the singleton scheduler."""
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


def is_match_day() -> bool:
    """Check if today has any fixtures for the current round."""
    try:
        from zoneinfo import ZoneInfo

        from sqlalchemy import func, select

        from src.models.database import Fixture, get_session
        from src.utils.config import get_config

        config = get_config()
        melb_tz = ZoneInfo("Australia/Melbourne")
        today = datetime.now(melb_tz).date()

        session = get_session()
        try:
            count = session.execute(
                select(func.count(Fixture.id)).where(
                    Fixture.season == config.season,
                    Fixture.round == config.current_round,
                    func.date(Fixture.date) == today,
                )
            ).scalar()
            return (count or 0) > 0
        finally:
            session.close()
    except Exception as e:
        logger.warning("Could not determine match day: %s", e)
        return False


def should_skip(source: str, off_day_hours: float) -> bool:
    """Return True if we should skip this run.

    Skips when it's not a match day AND the last successful sync
    was within off_day_hours.
    """
    if is_match_day():
        return False
    status = sync_status.get(source, {})
    last_success = status.get("last_success")
    if not last_success:
        return False  # Never ran — should run now
    last_dt = datetime.fromisoformat(last_success)
    return datetime.now(timezone.utc) - last_dt < timedelta(hours=off_day_hours)
