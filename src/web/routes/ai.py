from __future__ import annotations

"""AI advisor endpoints — all wrapped in asyncio.to_thread for non-blocking."""

import asyncio
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from src.utils.config import get_config

router = APIRouter()


class ChatRequest(BaseModel):
    message: str


class AnalyzeRequest(BaseModel):
    player_name: str


def _get_advisor():
    """Create a SuperCoachAdvisor instance."""
    from src.ai.advisor import SuperCoachAdvisor

    return SuperCoachAdvisor()


@router.get("/weekly")
async def get_weekly() -> dict:
    """Get weekly AI advice."""
    try:
        advisor = _get_advisor()
        result = await asyncio.to_thread(advisor.get_weekly_advice)
        return {"response": result, "generated_at": datetime.utcnow().isoformat()}
    except ValueError as e:
        return {"response": str(e), "generated_at": datetime.utcnow().isoformat()}


@router.get("/captain")
async def get_captain_advice(
    round_num: int = Query(None, alias="round"),
) -> dict:
    """Get AI captain recommendation."""
    config = get_config()
    r = round_num or config.current_round

    try:
        advisor = _get_advisor()
        result = await asyncio.to_thread(advisor.get_captain_advice, r)
        return {"response": result, "generated_at": datetime.utcnow().isoformat()}
    except ValueError as e:
        return {"response": str(e), "generated_at": datetime.utcnow().isoformat()}


@router.get("/trades")
async def get_trade_advice(
    round_num: int = Query(None, alias="round"),
) -> dict:
    """Get AI trade recommendation."""
    config = get_config()
    r = round_num or config.current_round

    try:
        advisor = _get_advisor()
        result = await asyncio.to_thread(advisor.get_trade_advice, r)
        return {"response": result, "generated_at": datetime.utcnow().isoformat()}
    except ValueError as e:
        return {"response": str(e), "generated_at": datetime.utcnow().isoformat()}


@router.post("/chat")
async def chat(request: ChatRequest) -> dict:
    """Free-form AI chat."""
    try:
        advisor = _get_advisor()
        result = await asyncio.to_thread(advisor.chat, request.message)
        return {"response": result, "generated_at": datetime.utcnow().isoformat()}
    except ValueError as e:
        return {"response": str(e), "generated_at": datetime.utcnow().isoformat()}


@router.post("/analyze-player")
async def analyze_player(request: AnalyzeRequest) -> dict:
    """Get AI analysis for a specific player."""
    try:
        advisor = _get_advisor()
        result = await asyncio.to_thread(advisor.analyze_player, request.player_name)
        return {"response": result, "generated_at": datetime.utcnow().isoformat()}
    except ValueError as e:
        return {"response": str(e), "generated_at": datetime.utcnow().isoformat()}
