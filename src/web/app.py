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

    # Derive bye rounds from existing fixture data if not yet populated
    try:
        from src.models.database import ByeRound, Fixture, get_session as _gs
        from src.analytics.byes import derive_bye_rounds
        from src.utils.config import get_config as _gc
        from sqlalchemy import select, func

        _session = _gs()
        try:
            _cfg = _gc()
            bye_count = _session.execute(
                select(func.count(ByeRound.id)).where(ByeRound.season == _cfg.season)
            ).scalar() or 0
            fixture_count = _session.execute(
                select(func.count(Fixture.id)).where(Fixture.season == _cfg.season)
            ).scalar() or 0

            if bye_count == 0 and fixture_count > 0:
                count = derive_bye_rounds(_session, _cfg.season)
                logger.info("Auto-derived %d bye entries from %d fixtures on startup", count, fixture_count)
        finally:
            _session.close()
    except Exception as e:
        logger.warning("Could not auto-derive bye rounds on startup: %s", e)

    # Clean up corrupted emergency data (field players with emergency flag)
    try:
        from src.web.routes.team import _enforce_emergency_integrity
        from src.models.database import get_session as _gs2, User
        _s2 = _gs2()
        try:
            users = _s2.execute(select(User)).scalars().all()
            for u in users:
                cleared = _enforce_emergency_integrity(_s2, u.id)
                if cleared:
                    logger.info("Cleared %d invalid emergencies for user %d", cleared, u.id)
            _s2.commit()
        finally:
            _s2.close()
    except Exception as e:
        logger.warning("Could not enforce emergency integrity on startup: %s", e)

    # Start the data sync scheduler
    from src.sync.scheduler import get_scheduler
    from src.sync.tasks import (
        sync_afl_lineups,
        sync_afl_news_injuries,
        sync_aflcomau_injuries,
        sync_bye_rounds,
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
    scheduler.add_job(
        sync_afl_lineups,
        "interval", hours=2,
        id="afl_lineups",
        name="AFL Team Lineups",
    )
    scheduler.add_job(
        sync_afl_news_injuries,
        "interval", hours=4,
        id="afl_news_injuries",
        name="AFL News Injuries",
    )
    scheduler.add_job(
        sync_bye_rounds,
        "interval", hours=6,
        id="bye_rounds",
        name="Bye Round Derivation",
    )

    scheduler.start()
    logger.info("Data sync scheduler started with %d jobs", len(scheduler.get_jobs()))

    # Run initial sync on startup (background, non-blocking)
    import asyncio

    async def _initial_sync():
        """Run all sync tasks once on startup after a short delay."""
        await asyncio.sleep(10)  # Let the server finish starting
        logger.info("Running initial data sync...")
        from src.sync.tasks import sync_all
        try:
            await sync_all()
            logger.info("Initial data sync complete")
        except Exception as e:
            logger.error("Initial data sync failed: %s", e)

    asyncio.create_task(_initial_sync())

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
    from src.web.routes.fixtures import router as fixtures_router

    application.include_router(
        sync_router,
        prefix="/api/sync",
        tags=["sync"],
        dependencies=[Depends(get_current_user)],
    )
    application.include_router(
        fixtures_router,
        prefix="/api/fixtures",
        tags=["fixtures"],
        dependencies=[Depends(get_current_user)],
    )

    @application.get("/api/config")
    def get_config_endpoint(user: dict = Depends(get_current_user)) -> dict:
        from src.utils.config import get_config

        config = get_config()

        # Auto-detect current round from fixture data
        detected_round = config.current_round
        bye_alerts = []
        try:
            from src.models.database import ByeRound, get_session as _get_session
            from src.analytics.byes import detect_current_round
            from sqlalchemy import select, func

            session = _get_session()
            try:
                detected = detect_current_round(session, config.season)
                if detected > 0:
                    detected_round = detected

                # Generate bye alerts based on detected round
                for offset in (0, 1, 2):
                    rnd = detected_round + offset
                    bye_count = session.execute(
                        select(func.count(ByeRound.id)).where(
                            ByeRound.season == config.season,
                            ByeRound.round == rnd,
                        )
                    ).scalar() or 0
                    if bye_count > 0 and offset > 0:
                        bye_alerts.append(
                            f"Bye round in {offset} round{'s' if offset > 1 else ''} "
                            f"(Round {rnd}) — {bye_count} teams on bye"
                        )
                    elif bye_count > 0 and offset == 0:
                        bye_alerts.append(
                            f"This is a bye round — {bye_count} teams on bye"
                        )
            finally:
                session.close()
        except Exception:
            pass  # Tables may not exist yet

        return {
            "season": config.season,
            "current_round": detected_round,
            "trades_remaining": config.trades_remaining,
            "boosts_remaining": config.boosts_remaining,
            "bye_alerts": bye_alerts,
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
