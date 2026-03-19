from __future__ import annotations

"""Trade War Room data aggregator and problem detection.

Assembles all context needed for the Trade War Room page in a single call:
team data, problems, injuries, bye coverage, fixtures, scoring history.
"""

import logging
from typing import Optional

from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from src.models.database import (
    MyTeamSlot, Player, Injury, SupercoachScore, DfsPlayerStats,
    Fixture, ByeRound, Trade, get_session,
)
from src.utils.teams import normalize_team

logger = logging.getLogger(__name__)


def get_warroom_data(
    session: Session,
    user_id: int,
    season: int,
    round_num: int,
) -> dict:
    """Aggregate all data for the Trade War Room."""

    # 1. Get team slots with player data
    slots = (
        session.execute(
            select(MyTeamSlot, Player)
            .join(Player, MyTeamSlot.player_id == Player.id)
            .where(MyTeamSlot.user_id == user_id)
            .order_by(MyTeamSlot.position_slot)
        )
        .all()
    )

    team = []
    salary_total = 0
    for slot, player in slots:
        # Latest score
        latest = session.execute(
            select(SupercoachScore)
            .where(SupercoachScore.player_id == player.id)
            .order_by(desc(SupercoachScore.season), desc(SupercoachScore.round))
            .limit(1)
        ).scalar_one_or_none()

        # DFS stats for salary
        dfs = session.execute(
            select(DfsPlayerStats)
            .where(DfsPlayerStats.player_id == player.id)
            .order_by(desc(DfsPlayerStats.season))
            .limit(1)
        ).scalar_one_or_none()

        price = (latest.price if latest and latest.price else None) or (
            dfs.salary if dfs else None
        )
        if price:
            salary_total += price

        team.append({
            "player_id": player.id,
            "player_name": player.name,
            "team": player.team,
            "position": player.position,
            "position_slot": slot.position_slot,
            "price": price,
            "last_score": latest.score if latest else None,
            "breakeven": latest.breakeven if latest else None,
            "sc_avg": round(dfs.sc_avg, 1) if dfs and dfs.sc_avg else None,
        })

    # 2. Trades used this season
    trades_used = session.execute(
        select(Trade).where(Trade.season == season)
    ).scalars().all()

    from src.utils.config import get_config
    config = get_config()
    total_trades = config.trades_remaining + len(trades_used)
    budget_remaining = 10_000_000 - salary_total

    # 3. Problem detection
    problems = detect_problems(session, team, round_num, season)

    # 4. Injuries affecting team
    player_ids = [p["player_id"] for p in team]
    injuries = []
    if player_ids:
        inj_rows = session.execute(
            select(Injury, Player)
            .join(Player, Injury.player_id == Player.id)
            .where(Injury.player_id.in_(player_ids))
        ).all()
        for inj, player in inj_rows:
            injuries.append({
                "player_name": player.name,
                "team": player.team,
                "position": player.position,
                "injury_type": inj.injury_type,
                "estimated_return": inj.estimated_return,
                "status": inj.status,
            })

    # 5. Bye data for next 5 rounds
    bye_data = {}
    for r in range(round_num, min(round_num + 5, 25)):
        bye_teams = session.execute(
            select(ByeRound.team)
            .where(ByeRound.season == season, ByeRound.round == r)
        ).scalars().all()
        if bye_teams:
            bye_data[str(r)] = list(bye_teams)

    # 6. Trade history
    trade_history = []
    for t in trades_used:
        p_out = session.get(Player, t.player_out_id)
        p_in = session.get(Player, t.player_in_id)
        trade_history.append({
            "round": t.round,
            "player_out_name": p_out.name if p_out else "Unknown",
            "player_in_name": p_in.name if p_in else "Unknown",
            "price_out": t.price_out,
            "price_in": t.price_in,
            "reason": t.reason,
        })

    # 7. Ownership data
    ownership = {}
    try:
        from src.models.database import Ownership
        for p in team:
            own = session.execute(
                select(Ownership)
                .where(Ownership.player_id == p["player_id"], Ownership.season == season)
                .order_by(Ownership.round.desc())
                .limit(1)
            ).scalar_one_or_none()
            if own:
                ownership[str(p["player_id"])] = {
                    "pct": own.ownership_pct,
                    "change": own.ownership_change,
                }
    except Exception:
        pass  # ownership table may not exist yet

    return {
        "round": round_num,
        "season": season,
        "team": team,
        "trades_remaining": config.trades_remaining,
        "trades_used": len(trades_used),
        "total_trades": total_trades,
        "boosts_remaining": config.boosts_remaining,
        "budget_remaining": budget_remaining,
        "salary_cap_total": 10_000_000,
        "problems": problems,
        "injuries": injuries,
        "bye_data": bye_data,
        "trade_history": trade_history,
        "ownership": ownership,
    }


def detect_problems(
    session: Session,
    team: list[dict],
    round_num: int,
    season: int,
) -> list[dict]:
    """Scan team for issues: injuries, underperformers, upcoming byes."""
    problems = []

    for player in team:
        if player["position_slot"].startswith("BENCH"):
            continue

        pid = player["player_id"]

        # Critical: Injured
        inj = session.execute(
            select(Injury).where(
                Injury.player_id == pid,
                Injury.status.in_(["OUT", "DOUBTFUL", "TEST"]),
            )
        ).scalar_one_or_none()

        if inj and inj.status == "OUT":
            problems.append({
                "player_id": pid,
                "name": player["player_name"],
                "team": player["team"],
                "position": player["position"],
                "price": player["price"],
                "severity": "critical",
                "type": "injured",
                "detail": f"{inj.injury_type or 'Unknown'} — {inj.estimated_return or 'TBC'}",
                "recommendation": "Must trade — not playing",
            })
            continue

        if inj and inj.status in ("DOUBTFUL", "TEST"):
            problems.append({
                "player_id": pid,
                "name": player["player_name"],
                "team": player["team"],
                "position": player["position"],
                "price": player["price"],
                "severity": "warning",
                "type": "doubtful",
                "detail": f"{inj.injury_type or 'Unknown'} — status: {inj.status}",
                "recommendation": "Wait for team selections",
            })

        # Warning: Underperforming (last 3 scores below breakeven)
        recent = session.execute(
            select(SupercoachScore)
            .where(SupercoachScore.player_id == pid, SupercoachScore.season == season)
            .order_by(desc(SupercoachScore.round))
            .limit(3)
        ).scalars().all()

        valid_scores = [s for s in recent if s.score is not None and s.breakeven is not None]
        if len(valid_scores) >= 2:
            avg_score = sum(s.score for s in valid_scores) / len(valid_scores)
            avg_be = sum(s.breakeven for s in valid_scores) / len(valid_scores)
            if avg_score < avg_be - 15:
                problems.append({
                    "player_id": pid,
                    "name": player["player_name"],
                    "team": player["team"],
                    "position": player["position"],
                    "price": player["price"],
                    "severity": "warning",
                    "type": "underperforming",
                    "detail": f"Avg {avg_score:.0f} vs BE {avg_be:.0f} (last {len(valid_scores)} rounds)",
                    "recommendation": "Consider trading — price dropping",
                })

        # Info: Upcoming bye
        for r in range(round_num, round_num + 3):
            bye = session.execute(
                select(ByeRound).where(
                    ByeRound.season == season,
                    ByeRound.round == r,
                    ByeRound.team == normalize_team(player["team"]),
                )
            ).scalar_one_or_none()
            if bye:
                label = "This round" if r == round_num else f"Round {r}"
                problems.append({
                    "player_id": pid,
                    "name": player["player_name"],
                    "team": player["team"],
                    "position": player["position"],
                    "price": player["price"],
                    "severity": "info",
                    "type": "upcoming_bye",
                    "detail": f"Bye {label}",
                    "recommendation": f"Ensure bench coverage for Round {r}",
                })

    severity_order = {"critical": 0, "warning": 1, "info": 2}
    problems.sort(key=lambda p: severity_order.get(p["severity"], 3))
    return problems


def build_trade_prompt(data: dict) -> str:
    """Build the detailed prompt from War Room data for the AI."""
    team_text = "MY TEAM:\n"
    for p in data["team"]:
        slot = p.get("position_slot", "?")
        name = p.get("player_name", "?")
        team = p.get("team", "?")
        pos = p.get("position", "?")
        price = p.get("price", 0)
        last = p.get("last_score", "N/A")
        be = p.get("breakeven", "N/A")
        avg = p.get("sc_avg", "N/A")
        team_text += f"  {slot}: {name} ({team}, {pos}) ${price or 0:,} | Last: {last} | BE: {be} | Avg: {avg}\n"

    problems_text = "PROBLEMS DETECTED:\n"
    emoji_map = {"critical": "RED", "warning": "YELLOW", "info": "INFO"}
    for p in data["problems"]:
        problems_text += f"  [{emoji_map.get(p['severity'], '?')}] {p['name']} — {p['type']}: {p['detail']}\n"
    if not data["problems"]:
        problems_text += "  No urgent problems detected.\n"

    injury_text = "TEAM INJURIES:\n"
    for inj in data["injuries"]:
        injury_text += f"  {inj['player_name']} ({inj['team']}) — {inj.get('injury_type', '?')} — {inj.get('status', '?')}\n"
    if not data["injuries"]:
        injury_text += "  No current injuries.\n"

    bye_text = "UPCOMING BYES:\n"
    for rnd, teams in data["bye_data"].items():
        bye_text += f"  Round {rnd}: {', '.join(teams)}\n"
    if not data["bye_data"]:
        bye_text += "  No byes in upcoming rounds.\n"

    history_text = "TRADE HISTORY:\n"
    for t in data["trade_history"]:
        history_text += f"  R{t.get('round', '?')}: {t.get('player_out_name', '?')} -> {t.get('player_in_name', '?')}\n"
    if not data["trade_history"]:
        history_text += "  No trades yet this season.\n"

    # Ownership data
    ownership_text = "OWNERSHIP DATA:\n"
    ownership = data.get("ownership", {})
    if ownership:
        for p in data["team"]:
            own = ownership.get(str(p["player_id"]), {})
            pct = own.get("pct")
            chg = own.get("change")
            pct_str = f"{pct:.0f}%" if pct else "N/A"
            chg_str = f" ({'+' if chg > 0 else ''}{chg:.1f}%)" if chg else ""
            ownership_text += f"  {p['player_name']}: {pct_str}{chg_str}\n"
    else:
        ownership_text += "  No ownership data available.\n"

    return f"""Analyse my SuperCoach team for Round {data['round']} and recommend trades.

{team_text}
TRADE RESOURCES:
  Trades remaining: {data['trades_remaining']}/{data['total_trades']}
  Boosts remaining: {data['boosts_remaining']}
  Budget remaining: ${data['budget_remaining']:,}

{problems_text}
{injury_text}
{bye_text}
{ownership_text}
{history_text}

What trades should I make this round? If I shouldn't trade, tell me that too."""


TRADE_ADVISOR_SYSTEM_PROMPT = """\
You are an elite AFL SuperCoach trade advisor with deep knowledge of scoring, pricing mechanics, and strategy.

RULES:
1. Never recommend more trades than available (usually 2/round, 3 in bye rounds 12-16)
2. Every trade must be within salary cap
3. Consider the FULL remaining season — trade conservation matters
4. Flag when NOT trading is the right call
5. For each recommendation: player out, player in, price difference, projected uplift, bye impact, risk
6. Check position eligibility
7. Priority: injuries > underperformers losing money > bye issues > upgrades
8. Consider ownership when recommending trades:
   - High ownership (>30%) = template, safe but no rank gain
   - Low ownership (<10%) = POD (Point of Difference), high risk/reward
   - Rising ownership = bandwagon loading, recommend getting on early
   - Falling ownership = potential panic sell to exploit
   - Always mention ownership % for trade-in targets

RESPONSE FORMAT:
## URGENT TRADES
(Must do this round — injuries, definite outs)

## RECOMMENDED TRADES
(Should do, priority order with full reasoning)

## HOLD
(Players that look bad but should be kept, with reasoning)

## WATCHLIST
(Players to monitor as trade-in targets next round)

For each trade: Player OUT -> Player IN, price delta, why out, why in, risk, confidence (High/Medium/Low)."""


WEEKLY_BRIEFING_SYSTEM_PROMPT = """\
You are an elite AFL SuperCoach analyst delivering a personalised weekly round briefing. Write in a confident, punchy style like a premium sports newsletter.

Structure with these EXACT sections using markdown headers:

## Team Health
Quick snapshot: how many playing, projected total, any concerns.

## Captain Pick
Top captain choice with data reasoning. Include a VC and a punt pick.
For each: projected score, opponent, DVP rank, ownership %, and 1-2 sentence justification.

## Alerts
Injuries, late outs, team selection surprises. Classify each as:
- CRITICAL (must act now)
- WARNING (monitor)
- OPPORTUNITY (someone else's loss is your gain)

## Trade of the Week
The single best trade. Player out, player in, price impact, why.
Keep to one trade with clear reasoning. Reference the Trade War Room for more.

## Ownership Radar
Biggest ownership movers. Flag anyone crossing template threshold (30%).
Identify one POD worth considering.

## Bye Watch
Only include if byes affect current or next round.
Show affected players, whether emergencies cover, bye readiness.

## Key Matchups
List round's games with one-line previews.
Highlight which of the user's players are in featured games.

RULES:
- Be specific — use actual player names, scores, prices, ownership from the data
- Give clear recommendations with confidence levels, don't hedge everything
- Keep it punchy — 2-3 minutes to read
- Use the data provided, don't make up statistics
- Reference the Trade War Room when discussing trades"""


def build_weekly_briefing_prompt(warroom_data: dict, round_num: int) -> str:
    """Build the comprehensive prompt for the weekly briefing."""
    team_text = f"MY TEAM (Round {round_num}):\n"
    field_projected = 0

    for p in warroom_data["team"]:
        slot = p.get("position_slot", "")
        is_field = not slot.startswith("BENCH")
        proj = p.get("last_score") or p.get("sc_avg") or 0
        if is_field:
            field_projected += proj

        own = warroom_data.get("ownership", {}).get(str(p.get("player_id")), {})
        own_str = f" | Own: {own.get('pct', '?')}%" if own.get("pct") else ""

        team_text += f"  {slot}: {p.get('player_name')} ({p.get('team')}, {p.get('position')}) ${p.get('price', 0) or 0:,} | Score: {p.get('last_score', 'N/A')} | BE: {p.get('breakeven', 'N/A')}{own_str}\n"

    problems_text = "PROBLEMS:\n"
    for prob in warroom_data.get("problems", []):
        problems_text += f"  {prob['severity'].upper()}: {prob['name']} — {prob['detail']}\n"
    if not warroom_data.get("problems"):
        problems_text += "  None.\n"

    injury_text = "INJURIES:\n"
    for inj in warroom_data.get("injuries", []):
        injury_text += f"  {inj['player_name']} ({inj['team']}) — {inj.get('injury_type', '?')} — {inj.get('status', '?')}\n"
    if not warroom_data.get("injuries"):
        injury_text += "  None.\n"

    bye_text = "BYES:\n"
    for rnd, teams in warroom_data.get("bye_data", {}).items():
        bye_text += f"  Round {rnd}: {', '.join(teams)}\n"
    if not warroom_data.get("bye_data"):
        bye_text += "  No byes upcoming.\n"

    ownership_text = "OWNERSHIP MOVERS:\n"
    own_data = warroom_data.get("ownership", {})
    if own_data:
        for p in warroom_data["team"]:
            own = own_data.get(str(p.get("player_id")), {})
            if own.get("change"):
                chg = own["change"]
                ownership_text += f"  {p['player_name']}: {'+' if chg > 0 else ''}{chg:.1f}%\n"

    return f"""Generate my personalised SuperCoach weekly briefing for Round {round_num}.

{team_text}
TRADE RESOURCES:
  Trades: {warroom_data.get('trades_remaining', '?')}/{warroom_data.get('total_trades', 30)}
  Boosts: {warroom_data.get('boosts_remaining', '?')}
  Budget: ${warroom_data.get('budget_remaining', 0):,}

{problems_text}
{injury_text}
{bye_text}
{ownership_text}

Projected field total: ~{field_projected:.0f} pts"""
