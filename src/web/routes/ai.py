from __future__ import annotations

"""AI advisor endpoints — all wrapped in asyncio.to_thread for non-blocking."""

import asyncio
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.utils.config import get_config
from src.web.middleware.authenticate import get_current_user

router = APIRouter()


class ChatRequest(BaseModel):
    message: str


class AnalyzeRequest(BaseModel):
    player_name: str


class TradeWarRoomRequest(BaseModel):
    round: int
    season: int = 2026


class TradeChatMessage(BaseModel):
    role: str
    content: str


class TradeChatRequest(BaseModel):
    round: int
    season: int = 2026
    question: str
    history: list[TradeChatMessage] = []


def _get_advisor(user_id: int):
    """Create a SuperCoachAdvisor instance scoped to a user."""
    from src.ai.advisor import SuperCoachAdvisor

    return SuperCoachAdvisor(user_id=user_id)


@router.get("/weekly")
async def get_weekly(user: dict = Depends(get_current_user)) -> dict:
    """Get weekly AI advice."""
    try:
        advisor = _get_advisor(user["user_id"])
        result = await asyncio.to_thread(advisor.get_weekly_advice)
        return {"response": result, "generated_at": datetime.utcnow().isoformat()}
    except ValueError as e:
        return {"response": str(e), "generated_at": datetime.utcnow().isoformat()}


@router.get("/captain")
async def get_captain_advice(
    round_num: int = Query(None, alias="round"),
    user: dict = Depends(get_current_user),
) -> dict:
    """Get AI captain recommendation."""
    config = get_config()
    r = round_num or config.current_round

    try:
        advisor = _get_advisor(user["user_id"])
        result = await asyncio.to_thread(advisor.get_captain_advice, r)
        return {"response": result, "generated_at": datetime.utcnow().isoformat()}
    except ValueError as e:
        return {"response": str(e), "generated_at": datetime.utcnow().isoformat()}


@router.get("/trades")
async def get_trade_advice(
    round_num: int = Query(None, alias="round"),
    user: dict = Depends(get_current_user),
) -> dict:
    """Get AI trade recommendation."""
    config = get_config()
    r = round_num or config.current_round

    try:
        advisor = _get_advisor(user["user_id"])
        result = await asyncio.to_thread(advisor.get_trade_advice, r)
        return {"response": result, "generated_at": datetime.utcnow().isoformat()}
    except ValueError as e:
        return {"response": str(e), "generated_at": datetime.utcnow().isoformat()}


@router.post("/chat")
async def chat(request: ChatRequest, user: dict = Depends(get_current_user)) -> dict:
    """Free-form AI chat."""
    try:
        advisor = _get_advisor(user["user_id"])
        result = await asyncio.to_thread(advisor.chat, request.message)
        return {"response": result, "generated_at": datetime.utcnow().isoformat()}
    except ValueError as e:
        return {"response": str(e), "generated_at": datetime.utcnow().isoformat()}


@router.post("/analyze-player")
async def analyze_player(
    request: AnalyzeRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Get AI analysis for a specific player."""
    try:
        advisor = _get_advisor(user["user_id"])
        result = await asyncio.to_thread(advisor.analyze_player, request.player_name)
        return {"response": result, "generated_at": datetime.utcnow().isoformat()}
    except ValueError as e:
        return {"response": str(e), "generated_at": datetime.utcnow().isoformat()}


@router.post("/trade-warroom")
async def ai_trade_warroom(
    request: TradeWarRoomRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """AI trade recommendations with full team context."""
    from src.analytics.trade_warroom import get_warroom_data, build_trade_prompt, TRADE_ADVISOR_SYSTEM_PROMPT
    from src.analytics.byes import detect_current_round
    from src.models.database import get_session

    config = get_config()
    session = get_session()
    try:
        r = request.round
        try:
            detected = detect_current_round(session, config.season)
            if detected > 0:
                r = detected
        except Exception:
            pass

        warroom_data = get_warroom_data(session, user["user_id"], request.season, r)
    finally:
        session.close()

    prompt = build_trade_prompt(warroom_data)

    try:
        advisor = _get_advisor(user["user_id"])
        result = await asyncio.to_thread(
            advisor._call_claude,
            prompt,
            system_override=TRADE_ADVISOR_SYSTEM_PROMPT,
        )
        return {
            "recommendations": result,
            "round": r,
            "problems_count": len(warroom_data["problems"]),
            "generated_at": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {
            "recommendations": f"Error generating recommendations: {str(e)}",
            "round": r,
            "problems_count": 0,
            "generated_at": datetime.utcnow().isoformat(),
        }


@router.post("/trade-chat")
async def ai_trade_chat(
    request: TradeChatRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Free-form trade chat with full team context."""
    from src.analytics.trade_warroom import get_warroom_data, build_trade_prompt, TRADE_ADVISOR_SYSTEM_PROMPT
    from src.models.database import get_session

    config = get_config()
    session = get_session()
    try:
        warroom_data = get_warroom_data(session, user["user_id"], request.season, request.round)
    finally:
        session.close()

    context = build_trade_prompt(warroom_data)

    # Build conversation with team context as first message
    full_prompt = f"Team context:\n\n{context}\n\nUser question: {request.question}"

    try:
        advisor = _get_advisor(user["user_id"])
        result = await asyncio.to_thread(
            advisor._call_claude,
            full_prompt,
            system_override=TRADE_ADVISOR_SYSTEM_PROMPT,
        )
        return {
            "answer": result,
            "question": request.question,
            "generated_at": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {
            "answer": f"Error: {str(e)}",
            "question": request.question,
            "generated_at": datetime.utcnow().isoformat(),
        }
