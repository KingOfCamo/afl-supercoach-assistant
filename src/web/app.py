from __future__ import annotations

"""FastAPI application factory for the SuperCoach web dashboard."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.models.database import init_db
from src.web.middleware.authenticate import get_current_user

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB and start background sync scheduler on startup."""
    init_db()

    # Start the data sync scheduler
    from src.sync.scheduler import get_scheduler
    from src.sync.tasks import (
        sync_aflcomau_injuries,
        sync_fanfooty,
        sync_footywire_injuries,
        sync_footywire_scores,
        sync_squiggle,
        sync_supercoach_players,
        sync_supercoach_round_data,
    )

    scheduler = get_scheduler()

    scheduler.add_job(
        sync_supercoach_players,
        "interval", hours=6,
        id="supercoach_api",
        name="SuperCoach API Players",
    )
    scheduler.add_job(
        sync_supercoach_round_data,
        "interval", minutes=30,
        id="supercoach_round",
        name="SuperCoach Round Data",
    )
    scheduler.add_job(
        sync_footywire_scores,
        "interval", minutes=30,
        id="footywire_scores",
        name="FootyWire Scores",
    )
    scheduler.add_job(
        sync_footywire_injuries,
        "interval", hours=4,
        id="footywire_injuries",
        name="FootyWire Injuries",
    )
    scheduler.add_job(
        sync_aflcomau_injuries,
        "interval", hours=4,
        id="aflcomau_injuries",
        name="AFL.com.au Injuries",
    )
    scheduler.add_job(
        sync_fanfooty,
        "interval", minutes=15,
        id="fanfooty",
        name="FanFooty Scores",
    )
    scheduler.add_job(
        sync_squiggle,
        "interval", hours=1,
        id="squiggle",
        name="Squiggle Fixtures",
    )

    scheduler.start()
    logger.info("Data sync scheduler started with %d jobs", len(scheduler.get_jobs()))

    yield

    scheduler.shutdown(wait=False)
    logger.info("Data sync scheduler shut down")


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    application = FastAPI(
        title="SuperCoach AI Dashboard",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — allow GitHub Pages, localhost, and Railway domain
    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    origins = [
        "https://kingofcamo.github.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    if railway_domain:
        origins.append(f"https://{railway_domain}")

    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Auth routes (no authentication required) ---
    from src.web.routes.auth import router as auth_router

    application.include_router(auth_router, prefix="/api/auth", tags=["auth"])

    # --- Protected API routes (JWT required) ---
    from src.web.routes.players import router as players_router
    from src.web.routes.team import router as team_router
    from src.web.routes.analytics import router as analytics_router
    from src.web.routes.ai import router as ai_router

    application.include_router(
        players_router,
        prefix="/api/players",
        tags=["players"],
        dependencies=[Depends(get_current_user)],
    )
    application.include_router(
        team_router,
        prefix="/api/team",
        tags=["team"],
        dependencies=[Depends(get_current_user)],
    )
    application.include_router(
        analytics_router,
        prefix="/api/analytics",
        tags=["analytics"],
        dependencies=[Depends(get_current_user)],
    )
    application.include_router(
        ai_router,
        prefix="/api/ai",
        tags=["ai"],
        dependencies=[Depends(get_current_user)],
    )

    from src.web.routes.sync import router as sync_router

    application.include_router(
        sync_router,
        prefix="/api/sync",
        tags=["sync"],
        dependencies=[Depends(get_current_user)],
    )

    @application.get("/api/config")
    def get_config_endpoint(user: dict = Depends(get_current_user)) -> dict:
        from src.utils.config import get_config

        config = get_config()
        return {
            "season": config.season,
            "current_round": config.current_round,
            "trades_remaining": config.trades_remaining,
            "boosts_remaining": config.boosts_remaining,
        }

    # --- Static pages ---

    @application.get("/login")
    @application.get("/login.html")
    def serve_login() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "login.html"))

    # Serve static files (CSS, JS, etc.)
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @application.get("/")
    def serve_index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    return application


def run_server(host: str = "0.0.0.0", port: int = None) -> None:
    """Start the uvicorn server. Reads PORT from env for Railway."""
    if port is None:
        port = int(os.environ.get("PORT", 8000))
    app = create_app()
    uvicorn.run(app, host=host, port=port)
