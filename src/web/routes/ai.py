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


class WeeklyBriefingRequest(BaseModel):
    round: int = None
    season: int = 2026
    force: bool = False


class CompareRequest(BaseModel):
    player_ids: list[int]
    round: int = None
    season: int = 2026


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


@router.get("/weekly-briefing")
def get_weekly_briefing(
    round_num: int = None,
    user: dict = Depends(get_current_user),
) -> dict:
    """Get cached weekly briefing for a round."""
    from src.models.database import WeeklyBriefing, get_session
    from src.analytics.byes import detect_current_round
    from sqlalchemy import select, desc

    config = get_config()
    session = get_session()
    try:
        r = round_num or config.current_round
        try:
            detected = detect_current_round(session, config.season)
            if detected > 0 and round_num is None:
                r = detected
        except Exception:
            pass

        cached = session.execute(
            select(WeeklyBriefing)
            .where(WeeklyBriefing.season == config.season, WeeklyBriefing.round == r)
            .order_by(desc(WeeklyBriefing.generated_at))
            .limit(1)
        ).scalar_one_or_none()

        if cached:
            return {
                "briefing": cached.content,
                "round": r,
                "generated_at": cached.generated_at.isoformat() if cached.generated_at else None,
                "exists": True,
            }
        return {"exists": False, "round": r}
    finally:
        session.close()


@router.post("/weekly-briefing")
async def generate_weekly_briefing(
    request: WeeklyBriefingRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Generate a comprehensive round briefing. Caches the result."""
    from src.analytics.trade_warroom import (
        get_warroom_data, build_weekly_briefing_prompt, WEEKLY_BRIEFING_SYSTEM_PROMPT,
    )
    from src.analytics.byes import detect_current_round
    from src.models.database import WeeklyBriefing, get_session
    from sqlalchemy import select, desc

    config = get_config()
    session = get_session()
    try:
        r = request.round or config.current_round
        try:
            detected = detect_current_round(session, config.season)
            if detected > 0 and request.round is None:
                r = detected
        except Exception:
            pass

        # Check cache unless force regenerate
        if not request.force:
            cached = session.execute(
                select(WeeklyBriefing)
                .where(WeeklyBriefing.season == request.season, WeeklyBriefing.round == r)
                .order_by(desc(WeeklyBriefing.generated_at))
                .limit(1)
            ).scalar_one_or_none()
            if cached:
                return {
                    "briefing": cached.content,
                    "round": r,
                    "generated_at": cached.generated_at.isoformat() if cached.generated_at else None,
                    "cached": True,
                }

        # Gather data
        warroom_data = get_warroom_data(session, user["user_id"], request.season, r)
        prompt = build_weekly_briefing_prompt(warroom_data, r)
    finally:
        session.close()

    # Generate via Claude
    try:
        advisor = _get_advisor(user["user_id"])
        briefing_text = await asyncio.to_thread(
            advisor._call_claude,
            prompt,
            system_override=WEEKLY_BRIEFING_SYSTEM_PROMPT,
        )
    except Exception as e:
        return {
            "briefing": f"Error generating briefing: {str(e)}",
            "round": r,
            "generated_at": datetime.utcnow().isoformat(),
            "cached": False,
        }

    # Cache it
    session = get_session()
    try:
        session.add(WeeklyBriefing(
            season=request.season,
            round=r,
            content=briefing_text,
        ))
        session.commit()
    finally:
        session.close()

    return {
        "briefing": briefing_text,
        "round": r,
        "generated_at": datetime.utcnow().isoformat(),
        "cached": False,
    }


@router.post("/compare")
async def ai_compare_verdict(
    request: CompareRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """AI verdict comparing 2-3 players."""
    from src.web.routes.players import compare_players as _compare

    # Get comparison data
    ids_str = ",".join(str(i) for i in request.player_ids)
    comp_data = _compare(ids=ids_str, round_num=request.round, user=user)
    players = comp_data.get("players", [])

    if len(players) < 2:
        return {"verdict": "Not enough player data to compare."}

    prompt = "Compare these SuperCoach players and give a decisive verdict:\n\n"
    for p in players:
        s = p["scoring"]
        pr = p["pricing"]
        a = p["advanced"]
        prompt += f"{p['name']} ({p['team']}, {p['position']}) — ${p.get('price', 0) or 0:,}\n"
        prompt += f"  Avg: {s['season_avg']} | Last 3: {s['last_3_avg']} | Last 5: {s['last_5_avg']} | High/Low: {s['high']}/{s['low']} | Consistency: {s['consistency']}%\n"
        prompt += f"  BE: {pr['breakeven']} | Price trend: ${pr['price_trend_3wk']:+,}\n"
        prompt += f"  CBA%: {a['cba_pct'] or 'N/A'} | TOG%: {a['tog_pct'] or 'N/A'} | Own: {a['ownership_pct'] or 'N/A'}% | Bye: R{a['next_bye'] or '?'}\n\n"

    prompt += "Give a clear verdict in 3-4 sentences. Pick a winner and explain why."

    try:
        advisor = _get_advisor(user["user_id"])
        result = await asyncio.to_thread(
            advisor._call_claude,
            prompt,
            system_override="You are a SuperCoach analyst. Give a brief, decisive verdict comparing players. Be specific with data. No hedging — pick a winner in 3-4 sentences.",
        )
        return {"verdict": result}
    except Exception as e:
        return {"verdict": f"Error: {str(e)}"}
