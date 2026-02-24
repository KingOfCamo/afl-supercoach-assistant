from __future__ import annotations

"""FastAPI application factory for the SuperCoach web dashboard."""

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

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB on startup."""
    init_db()
    yield


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
