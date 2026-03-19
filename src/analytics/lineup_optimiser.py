from __future__ import annotations

"""AI-powered lineup optimiser.

Analyses a user's 30-player squad and recommends the highest-scoring
legal starting 22 for a given round, factoring in byes and injuries.
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.database import MyTeamSlot, Player, Injury

logger = logging.getLogger(__name__)


def optimise_lineup(
    session: Session,
    user_id: int,
    season: int,
    round_num: int,
) -> dict:
    """Find the highest-scoring legal starting 22 from a 30-player squad.

    Returns swap suggestions (does not execute them).
    """
    from src.analytics.projections import project_player
    from src.analytics.byes import get_bye_teams

    bye_teams = get_bye_teams(session, season, round_num)

    # Get all user slots with player data
    slots = (
        session.execute(
            select(MyTeamSlot, Player)
            .join(Player, MyTeamSlot.player_id == Player.id)
            .where(MyTeamSlot.user_id == user_id)
            .order_by(MyTeamSlot.position_slot)
        )
        .all()
    )

    if not slots:
        return {
            "round": round_num,
            "swaps": [],
            "current_total": 0,
            "optimal_total": 0,
            "improvement": 0,
        }

    # Get injuries for all players
    player_ids = [p.id for _, p in slots]
    injury_map = {}
    if player_ids:
        injuries = (
            session.execute(select(Injury).where(Injury.player_id.in_(player_ids)))
            .scalars()
            .all()
        )
        for inj in injuries:
            injury_map[inj.player_id] = inj

    # Build player entries with projections
    entries = []
    for slot, player in slots:
        proj_result = project_player(player.id, round_num, season=season)
        proj_score = proj_result.projected_score if proj_result else 0.0

        is_bye = player.team in bye_teams
        is_injured = player.id in injury_map
        is_bench = slot.position_slot.startswith("BENCH") or slot.position_slot.startswith("FLEX")

        reason = None
        if is_bye:
            reason = "BYE"
            proj_score = 0.0
        elif is_injured:
            reason = "INJURED"
            proj_score = 0.0

        entries.append({
            "player_id": player.id,
            "player_name": player.name,
            "team": player.team,
            "position": player.position or "",
            "current_slot": slot.position_slot,
            "is_on_field": not is_bench,
            "projected": round(proj_score, 1),
            "forced_bench": is_bye or is_injured,
            "reason": reason,
        })

    # Separate forced bench from available
    available = [e for e in entries if not e["forced_bench"]]
    forced_bench = [e for e in entries if e["forced_bench"]]

    # Sort available by projected score descending
    available.sort(key=lambda x: x["projected"], reverse=True)

    # Greedy position fill
    needed = {"DEF": 6, "MID": 8, "RUC": 2, "FWD": 6}
    optimal_field = {}
    overflow = []

    for entry in available:
        placed = False
        positions = [p.strip().upper() for p in entry["position"].split("/") if p.strip()]
        for pos in positions:
            if needed.get(pos, 0) > 0:
                slot_num = needed[pos]
                # Calculate correct slot number (count down from max)
                max_for_pos = {"DEF": 6, "MID": 8, "RUC": 2, "FWD": 6}[pos]
                slot_idx = max_for_pos - slot_num + 1
                slot_name = f"{pos}{slot_idx}"
                optimal_field[slot_name] = entry
                needed[pos] -= 1
                placed = True
                break
        if not placed:
            overflow.append(entry)

    # Fill FLEX with highest remaining
    if overflow:
        optimal_field["FLEX1"] = overflow.pop(0)

    # Optimal bench = forced + overflow
    optimal_bench = forced_bench + overflow

    # Build current field map
    current_field = {}
    current_bench = {}
    for entry in entries:
        if entry["is_on_field"]:
            current_field[entry["current_slot"]] = entry
        else:
            current_bench[entry["current_slot"]] = entry

    # Generate swap suggestions by comparing current vs optimal
    swaps = []

    # Find players that should be on field but are on bench
    optimal_field_ids = {e["player_id"] for e in optimal_field.values()}
    current_field_ids = {e["player_id"] for e in current_field.values()}

    should_come_on = optimal_field_ids - current_field_ids
    should_go_off = current_field_ids - optimal_field_ids

    # Match each "go off" with a "come on"
    go_off_list = []
    for entry in entries:
        if entry["player_id"] in should_go_off:
            go_off_list.append(entry)
    go_off_list.sort(key=lambda x: x["projected"])

    come_on_list = []
    for entry in entries:
        if entry["player_id"] in should_come_on:
            come_on_list.append(entry)
    come_on_list.sort(key=lambda x: x["projected"], reverse=True)

    for come_on in come_on_list:
        if not go_off_list:
            break
        # Find best match: a go-off player whose slot the come-on player can fill
        best_match = None
        for i, go_off in enumerate(go_off_list):
            # Check if come_on can play in go_off's slot
            slot_pos = _get_slot_position(go_off["current_slot"])
            if _player_fits_slot(come_on["position"], slot_pos):
                # Check if go_off can go to bench (always true)
                best_match = i
                break

        if best_match is not None:
            go_off = go_off_list.pop(best_match)
            impact = round(come_on["projected"] - go_off["projected"], 1)
            reason = go_off["reason"] or "OUTSCORED"

            swaps.append({
                "out_player": go_off["player_name"],
                "out_team": go_off["team"],
                "out_position": go_off["position"],
                "out_slot": go_off["current_slot"],
                "out_projected": go_off["projected"],
                "out_reason": reason,
                "in_player": come_on["player_name"],
                "in_team": come_on["team"],
                "in_position": come_on["position"],
                "in_slot": come_on["current_slot"],
                "in_projected": come_on["projected"],
                "impact": impact,
                "slot_a": go_off["current_slot"],
                "slot_b": come_on["current_slot"],
            })

    # Sort by impact descending
    swaps.sort(key=lambda s: s["impact"], reverse=True)

    current_total = round(sum(e["projected"] for e in current_field.values()), 1)
    optimal_total = round(sum(e["projected"] for e in optimal_field.values()), 1)

    return {
        "round": round_num,
        "swaps": swaps,
        "current_total": current_total,
        "optimal_total": optimal_total,
        "improvement": round(optimal_total - current_total, 1),
    }


def _get_slot_position(slot_name: str) -> str:
    """Map slot name to required position line."""
    for prefix in ("DEF", "MID", "RUC", "FWD"):
        if slot_name.startswith(prefix):
            return prefix
    if slot_name.startswith("FLEX"):
        return "FLEX"
    return "BENCH"


def _player_fits_slot(player_position: str, slot_position: str) -> bool:
    """Check if a player's position(s) allow them to fill a given slot."""
    if slot_position in ("BENCH", "FLEX"):
        return True
    if not player_position:
        return False
    player_positions = [p.strip().upper() for p in player_position.split("/")]
    return slot_position in player_positions
