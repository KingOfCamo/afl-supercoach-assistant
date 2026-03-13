from __future__ import annotations

"""Sync status and manual trigger endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Query

from src.sync.scheduler import get_scheduler, sync_status

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/status")
def get_sync_status() -> dict:
    """Get the last sync time and status for each data source."""
    scheduler = get_scheduler()

    # Build next-run lookup from scheduler jobs
    jobs_info = {}
    for job in scheduler.get_jobs():
        jobs_info[job.id] = {
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        }

    sources = {}
    for source_name, status in sync_status.items():
        source_info = dict(status)
        if source_name in jobs_info:
            source_info["next_scheduled_run"] = jobs_info[source_name]["next_run"]
        sources[source_name] = source_info

    return {
        "scheduler_running": scheduler.running,
        "sources": sources,
    }


@router.post("/trigger")
async def trigger_sync(
    background_tasks: BackgroundTasks,
    source: Optional[str] = Query(None, description="Specific source to sync, or all"),
) -> dict:
    """Manually trigger a data sync. Runs in background."""
    from src.sync.tasks import (
        sync_aflcomau_injuries,
        sync_all,
        sync_fanfooty,
        sync_footywire_injuries,
        sync_footywire_scores,
        sync_squiggle,
        sync_supercoach_players,
        sync_supercoach_round_data,
    )

    task_map = {
        "supercoach_api": sync_supercoach_players,
        "supercoach_round": sync_supercoach_round_data,
        "footywire_scores": sync_footywire_scores,
        "footywire_injuries": sync_footywire_injuries,
        "aflcomau_injuries": sync_aflcomau_injuries,
        "fanfooty": sync_fanfooty,
        "squiggle": sync_squiggle,
    }

    if source:
        if source not in task_map:
            return {"error": f"Unknown source: {source}. Valid: {list(task_map.keys())}"}
        # Manual triggers always bypass should_skip
        background_tasks.add_task(task_map[source], force=True)
        return {"message": f"Triggered sync for {source}", "status": "accepted"}
    else:
        background_tasks.add_task(sync_all, force=True)
        return {"message": "Triggered full sync of all sources", "status": "accepted"}
