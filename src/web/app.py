from __future__ import annotations

"""FastAPI application factory for the SuperCoach web dashboard."""

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.models.database import init_db

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

    # Enable CORS so GitHub Pages frontend can call the local API
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://kingofcamo.github.io",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from src.web.routes.players import router as players_router
    from src.web.routes.team import router as team_router
    from src.web.routes.analytics import router as analytics_router
    from src.web.routes.ai import router as ai_router

    application.include_router(players_router, prefix="/api/players", tags=["players"])
    application.include_router(team_router, prefix="/api/team", tags=["team"])
    application.include_router(analytics_router, prefix="/api/analytics", tags=["analytics"])
    application.include_router(ai_router, prefix="/api/ai", tags=["ai"])

    @application.get("/api/config")
    def get_config_endpoint() -> dict:
        from src.utils.config import get_config

        config = get_config()
        return {
            "season": config.season,
            "current_round": config.current_round,
            "trades_remaining": config.trades_remaining,
            "boosts_remaining": config.boosts_remaining,
        }

    # Serve static files
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @application.get("/")
    def serve_index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    return application


def run_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the uvicorn server."""
    app = create_app()
    uvicorn.run(app, host=host, port=port)
