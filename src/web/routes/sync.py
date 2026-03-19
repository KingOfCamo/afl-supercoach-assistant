from __future__ import annotations

"""Sync status and manual trigger endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Query

from src.sync.scheduler import get_scheduler, sync_status
from src.sync.tasks import (
    sync_afl_lineups,
    sync_afl_news_injuries,
    sync_aflcomau_injuries,
    sync_all,
    sync_bye_rounds,
    sync_fanfooty,
    sync_footywire_injuries,
    sync_footywire_scores,
    sync_ownership,
    sync_squiggle,
    sync_supercoach_players,
    sync_supercoach_round_data,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Map source names to task functions
SOURCE_MAP = {
    "supercoach_players": sync_supercoach_players,
    "supercoach_round": sync_supercoach_round_data,
    "footywire_scores": sync_footywire_scores,
    "footywire_injuries": sync_footywire_injuries,
    "aflcomau_injuries": sync_aflcomau_injuries,
    "fanfooty": sync_fanfooty,
    "squiggle": sync_squiggle,
    "afl_lineups": sync_afl_lineups,
    "afl_news_injuries": sync_afl_news_injuries,
    "bye_rounds": sync_bye_rounds,
    "ownership": sync_ownership,
}


@router.get("/status")
def get_sync_status() -> dict:
    """Return current sync status for all sources plus next scheduled runs."""
    scheduler = get_scheduler()

    # Build next-run times from scheduler jobs
    next_runs = {}
    for job in scheduler.get_jobs():
        next_runs[job.id] = (
            job.next_run_time.isoformat() if job.next_run_time else None
        )

    return {
        "sources": dict(sync_status),
        "next_scheduled_runs": next_runs,
    }


@router.post("/scores", status_code=200)
async def sync_scores_endpoint() -> dict:
    """Sync scores + fixtures + bye data. Called by the SYNC button."""
    results = {}
    try:
        await sync_squiggle()
        results["squiggle"] = "ok"
    except Exception as e:
        results["squiggle"] = str(e)

    try:
        await sync_bye_rounds()
        results["bye_rounds"] = "ok"
    except Exception as e:
        results["bye_rounds"] = str(e)

    try:
        await sync_supercoach_round_data()
        results["supercoach_round"] = "ok"
    except Exception as e:
        results["supercoach_round"] = str(e)

    try:
        await sync_fanfooty()
        results["fanfooty"] = "ok"
    except Exception as e:
        results["fanfooty"] = str(e)

    return {"status": "complete", "results": results}


@router.post("/trigger", status_code=202)
async def trigger_sync(
    background_tasks: BackgroundTasks,
    source: Optional[str] = Query(None, description="Source to sync, or omit for all"),
) -> dict:
    """Manually trigger a sync. Runs in background, returns 202 immediately."""
    if source:
        fn = SOURCE_MAP.get(source)
        if fn is None:
            return {"error": f"Unknown source: {source}", "valid_sources": list(SOURCE_MAP.keys())}
        background_tasks.add_task(fn)
        return {"status": "triggered", "source": source}
    else:
        background_tasks.add_task(sync_all)
        return {"status": "triggered", "source": "all"}
