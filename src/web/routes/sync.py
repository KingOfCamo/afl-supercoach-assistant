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
    wait: bool = Query(False, description="Wait for sync to complete before responding"),
) -> dict:
    """Manually trigger a data sync.

    With wait=true, runs synchronously and returns results.
    With wait=false (default), runs in background.
    """
    from src.sync.tasks import (
        sync_afl_lineups,
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
        "afl_lineups": sync_afl_lineups,
    }

    if wait:
        # Foreground mode: run and wait for completion
        if source:
            if source not in task_map:
                return {"error": f"Unknown source: {source}. Valid: {list(task_map.keys())}"}
            try:
                await task_map[source](force=True)
                return {"message": f"Sync complete for {source}", "status": "complete"}
            except Exception as e:
                logger.error("Foreground sync failed for %s: %s", source, e)
                return {"message": f"Sync failed for {source}: {e}", "status": "error"}
        else:
            results = await sync_all(force=True)
            return {"message": "Full sync complete", "status": "complete", "results": results}
    else:
        # Background mode (original behavior)
        if source:
            if source not in task_map:
                return {"error": f"Unknown source: {source}. Valid: {list(task_map.keys())}"}
            background_tasks.add_task(task_map[source], force=True)
            return {"message": f"Triggered sync for {source}", "status": "accepted"}
        else:
            background_tasks.add_task(sync_all, force=True)
            return {"message": "Triggered full sync of all sources", "status": "accepted"}


@router.post("/scores")
async def sync_scores_foreground() -> dict:
    """Dedicated endpoint: sync player names + round scores, wait for completion.

    Runs player sync first (to ensure names/feed_ids match), then round sync.
    Returns results immediately — no background task guessing.
    """
    from src.sync.tasks import sync_supercoach_players, sync_supercoach_round_data

    results = {}

    # Step 1: Sync player names first (ensures DB names match SC API)
    try:
        await sync_supercoach_players(force=True)
        results["players"] = "success"
    except Exception as e:
        logger.error("Player sync failed: %s", e)
        results["players"] = f"error: {e}"

    # Step 2: Sync round scores
    try:
        await sync_supercoach_round_data(force=True)
        results["scores"] = "success"
    except Exception as e:
        logger.error("Round sync failed: %s", e)
        results["scores"] = f"error: {e}"

    return {"status": "complete", "results": results}
