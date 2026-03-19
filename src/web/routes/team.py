from __future__ import annotations

"""Team management CRUD endpoints."""

import csv
import io
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import desc, select

from src.models.database import (
    DfsPlayerStats,
    Injury,
    LineupStatus,
    MyTeamSlot,
    Player,
    SupercoachScore,
    get_session,
)
from src.web.middleware.authenticate import get_current_user

router = APIRouter()

VALID_SLOTS = (
    [f"DEF{i}" for i in range(1, 7)]
    + [f"MID{i}" for i in range(1, 9)]
    + [f"RUC{i}" for i in range(1, 3)]
    + [f"FWD{i}" for i in range(1, 7)]
    + [f"BENCH{i}" for i in range(1, 9)]
    + ["FLEX1"]
)


class SlotRequest(BaseModel):
    player_id: int
    position_slot: str


class CaptainRequest(BaseModel):
    captain_id: int
    vice_captain_id: Optional[int] = None


class EmergencyEntry(BaseModel):
    player_id: int
    emergency_position: str  # DEF, MID, RUC, FWD


class EmergencyRequest(BaseModel):
    """Set bench emergencies with position assignments.

    SuperCoach 2026 rules: max 4 emergencies, max 2 per position line,
    bench players only, FLEX has no emergency. Highest-scoring emergency
    in matching position activates when a field player doesn't play.
    """
    emergencies: List[EmergencyEntry]


class SwapRequest(BaseModel):
    slot_a: str  # e.g. "MID3"
    slot_b: str  # e.g. "BENCH2"


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


def _clear_emergency_if_on_field(session, slot: "MyTeamSlot") -> bool:
    """Clear emergency status if player is now in a field slot (not BENCH).

    Must be called AFTER any swap/move that changes position_slot.
    """
    if slot.is_emergency and not slot.position_slot.startswith("BENCH"):
        slot.is_emergency = False
        slot.emergency_order = None
        slot.emergency_position = None
        return True
    return False


def _enforce_emergency_integrity(session, user_id: int) -> int:
    """Bulk cleanup: clear emergency on any non-bench player."""
    slots = (
        session.execute(
            select(MyTeamSlot).where(
                MyTeamSlot.user_id == user_id,
                MyTeamSlot.is_emergency == True,
            )
        )
        .scalars()
        .all()
    )
    cleared = 0
    for s in slots:
        if not s.position_slot.startswith("BENCH"):
            s.is_emergency = False
            s.emergency_order = None
            s.emergency_position = None
            cleared += 1
    return cleared


@router.get("")
def get_team(user: dict = Depends(get_current_user)) -> dict:
    """Get current team with player stats."""
    user_id = user["user_id"]
    session = get_session()
    try:
        slots = (
            session.execute(
                select(MyTeamSlot)
                .where(MyTeamSlot.user_id == user_id)
                .order_by(MyTeamSlot.position_slot)
            )
            .scalars()
            .all()
        )

        result_slots = []
        salary_total = 0

        for slot in slots:
            player = session.get(Player, slot.player_id)
            if not player:
                continue

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

            # Season average
            scores = (
                session.execute(
                    select(SupercoachScore)
                    .where(
                        SupercoachScore.player_id == player.id,
                        SupercoachScore.score.isnot(None),
                    )
                    .order_by(desc(SupercoachScore.season), desc(SupercoachScore.round))
                    .limit(5)
                )
                .scalars()
                .all()
            )
            valid = [s.score for s in scores if s.score is not None]
            avg = sum(valid) / len(valid) if valid else 0

            # Active injury
            injury = session.execute(
                select(Injury)
                .where(Injury.player_id == player.id)
                .order_by(desc(Injury.updated_at))
                .limit(1)
            ).scalar_one_or_none()

            # Lineup status for current round (auto-detect)
            from src.utils.config import get_config
            config = get_config()
            try:
                from src.analytics.byes import detect_current_round
                current_rnd = detect_current_round(session, config.season)
            except Exception:
                current_rnd = config.current_round

            lineup = session.execute(
                select(LineupStatus).where(
                    LineupStatus.player_id == player.id,
                    LineupStatus.season == config.season,
                    LineupStatus.round == current_rnd,
                )
            ).scalar_one_or_none()

            # Check if player's team has a bye this round
            from src.models.database import ByeRound
            bye = session.execute(
                select(ByeRound).where(
                    ByeRound.season == config.season,
                    ByeRound.round == current_rnd,
                    ByeRound.team == player.team,
                )
            ).scalar_one_or_none()

            salary = (latest.price if latest and latest.price else None) or (
                dfs.salary if dfs else None
            )
            if salary:
                salary_total += salary

            result_slots.append({
                "id": slot.id,
                "player_id": player.id,
                "player_name": player.name,
                "team": player.team,
                "position": player.position,
                "position_slot": slot.position_slot,
                "is_captain": slot.is_captain,
                "is_vice_captain": slot.is_vice_captain,
                "is_emergency": slot.is_emergency,
                "emergency_order": slot.emergency_order,
                "emergency_position": slot.emergency_position,
                "salary": salary,
                "sc_avg": round(dfs.sc_avg, 1) if dfs and dfs.sc_avg else None,
                "last_score": latest.score if latest else None,
                "season_avg": round(avg, 1) if avg else None,
                "injury": {
                    "type": injury.injury_type,
                    "return": injury.estimated_return,
                    "status": injury.status,
                } if injury else None,
                "projected_score": latest.projected_score if latest else None,
                "lineup_status": lineup.status if lineup else None,
                "lineup_position": lineup.match_position if lineup else None,
                "lineup_opponent": lineup.opponent if lineup else None,
                "is_on_bye": bye is not None,
            })

        return {
            "slots": result_slots,
            "salary_total": salary_total,
            "player_count": len(result_slots),
        }
    finally:
        session.close()


@router.post("/slot")
def add_slot(request: SlotRequest, user: dict = Depends(get_current_user)) -> dict:
    """Add a player to a position slot."""
    user_id = user["user_id"]
    if request.position_slot not in VALID_SLOTS:
        raise HTTPException(status_code=400, detail=f"Invalid slot: {request.position_slot}")

    session = get_session()
    try:
        # Check slot not occupied for this user
        existing = session.execute(
            select(MyTeamSlot).where(
                MyTeamSlot.user_id == user_id,
                MyTeamSlot.position_slot == request.position_slot,
            )
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail=f"Slot {request.position_slot} is already occupied")

        # Check player not already on this user's team
        on_team = session.execute(
            select(MyTeamSlot).where(
                MyTeamSlot.user_id == user_id,
                MyTeamSlot.player_id == request.player_id,
            )
        ).scalar_one_or_none()
        if on_team:
            raise HTTPException(status_code=409, detail="Player is already on your team")

        # Check player exists
        player = session.get(Player, request.player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")

        slot = MyTeamSlot(
            user_id=user_id,
            player_id=request.player_id,
            position_slot=request.position_slot,
        )
        session.add(slot)
        session.commit()

        return {"success": True, "slot_id": slot.id, "player_name": player.name}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.delete("/slot/{slot_id}")
def remove_slot(slot_id: int, user: dict = Depends(get_current_user)) -> dict:
    """Remove a player from the team."""
    user_id = user["user_id"]
    session = get_session()
    try:
        slot = session.get(MyTeamSlot, slot_id)
        if not slot:
            raise HTTPException(status_code=404, detail="Slot not found")
        if slot.user_id != user_id:
            raise HTTPException(status_code=403, detail="Not your team slot")

        session.delete(slot)
        session.commit()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.put("/captain")
def set_captain(request: CaptainRequest, user: dict = Depends(get_current_user)) -> dict:
    """Set captain and optionally vice-captain."""
    user_id = user["user_id"]
    session = get_session()
    try:
        # Clear existing captain/VC for this user only
        all_slots = session.execute(
            select(MyTeamSlot).where(MyTeamSlot.user_id == user_id)
        ).scalars().all()
        for s in all_slots:
            s.is_captain = False
            s.is_vice_captain = False

        # Set captain
        captain_slot = session.execute(
            select(MyTeamSlot).where(
                MyTeamSlot.user_id == user_id,
                MyTeamSlot.player_id == request.captain_id,
            )
        ).scalar_one_or_none()
        if not captain_slot:
            raise HTTPException(status_code=400, detail="Captain must be on your team")
        captain_slot.is_captain = True

        # Set vice-captain
        if request.vice_captain_id:
            vc_slot = session.execute(
                select(MyTeamSlot).where(
                    MyTeamSlot.user_id == user_id,
                    MyTeamSlot.player_id == request.vice_captain_id,
                )
            ).scalar_one_or_none()
            if not vc_slot:
                raise HTTPException(status_code=400, detail="Vice-captain must be on your team")
            vc_slot.is_vice_captain = True

        session.commit()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.put("/emergency")
def set_emergencies(
    request: EmergencyRequest, user: dict = Depends(get_current_user)
) -> dict:
    """Set bench emergencies with position assignments.

    SuperCoach 2026 rules: max 4, max 2 per position line, bench only,
    no FLEX. Highest-scoring emergency activates when field player DNPs.
    """
    user_id = user["user_id"]
    proposed = request.emergencies

    if len(proposed) > 4:
        raise HTTPException(status_code=400, detail="Maximum 4 emergencies allowed")

    # Validate max 2 per position line
    from collections import Counter
    pos_counts = Counter(e.emergency_position for e in proposed)
    for pos, count in pos_counts.items():
        if count > 2:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum 2 emergencies for {pos}, got {count}",
            )
    if any(e.emergency_position == "FLEX" for e in proposed):
        raise HTTPException(status_code=400, detail="FLEX cannot have emergencies")

    session = get_session()
    try:
        # Clear existing emergencies
        all_slots = (
            session.execute(
                select(MyTeamSlot).where(MyTeamSlot.user_id == user_id)
            )
            .scalars()
            .all()
        )
        for s in all_slots:
            s.is_emergency = False
            s.emergency_order = None
            s.emergency_position = None

        # Set new emergencies
        for i, entry in enumerate(proposed):
            slot = session.execute(
                select(MyTeamSlot).where(
                    MyTeamSlot.user_id == user_id,
                    MyTeamSlot.player_id == entry.player_id,
                )
            ).scalar_one_or_none()
            if not slot:
                raise HTTPException(
                    status_code=400,
                    detail=f"Player {entry.player_id} is not on your team",
                )
            if not slot.position_slot.startswith("BENCH"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Emergencies must be bench players",
                )
            # Validate position eligibility
            player = session.get(Player, entry.player_id)
            if player and player.position:
                player_positions = [p.strip().upper() for p in player.position.split("/")]
                if entry.emergency_position not in player_positions:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{player.name} ({player.position}) cannot be {entry.emergency_position} emergency",
                    )
            slot.is_emergency = True
            slot.emergency_order = i + 1
            slot.emergency_position = entry.emergency_position

        session.commit()
        return {"success": True, "count": len(proposed)}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.post("/clear")
def clear_team(user: dict = Depends(get_current_user)) -> dict:
    """Clear the entire team for this user."""
    user_id = user["user_id"]
    session = get_session()
    try:
        slots = session.execute(
            select(MyTeamSlot).where(MyTeamSlot.user_id == user_id)
        ).scalars().all()
        for s in slots:
            session.delete(s)
        session.commit()
        return {"success": True, "removed": len(slots)}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.post("/import-csv")
async def import_csv(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
) -> dict:
    """Import team from CSV upload."""
    user_id = user["user_id"]
    content = await file.read()
    text = content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))

    session = get_session()
    try:
        # Clear existing team for this user
        for existing in session.execute(
            select(MyTeamSlot).where(MyTeamSlot.user_id == user_id)
        ).scalars().all():
            session.delete(existing)

        count = 0
        for row in reader:
            player_name = row.get("player_name", "").strip()
            if not player_name:
                continue

            # Find player (substring match)
            player = session.execute(
                select(Player).where(Player.name.ilike(f"%{player_name}%"))
            ).scalar_one_or_none()

            if player is None:
                # Create stub player
                player = Player(name=player_name, team="Unknown")
                session.add(player)
                session.flush()

            slot = MyTeamSlot(
                user_id=user_id,
                player_id=player.id,
                position_slot=row.get("position_slot", f"BENCH{count + 1}").strip(),
                is_captain=row.get("is_captain", "false").strip().lower() == "true",
                is_vice_captain=row.get("is_vice_captain", "false").strip().lower() == "true",
            )
            session.add(slot)
            count += 1

        session.commit()
        return {"success": True, "imported": count}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.post("/swap")
def swap_players(request: SwapRequest, user: dict = Depends(get_current_user)) -> dict:
    """Swap two players between slots (field ↔ bench, field ↔ field, etc.).

    Validates position eligibility before executing.
    """
    user_id = user["user_id"]
    session = get_session()
    try:
        # Find both slots
        slot_a = session.execute(
            select(MyTeamSlot).where(
                MyTeamSlot.user_id == user_id,
                MyTeamSlot.position_slot == request.slot_a,
            )
        ).scalar_one_or_none()

        slot_b = session.execute(
            select(MyTeamSlot).where(
                MyTeamSlot.user_id == user_id,
                MyTeamSlot.position_slot == request.slot_b,
            )
        ).scalar_one_or_none()

        if not slot_a or not slot_b:
            raise HTTPException(
                status_code=400,
                detail=f"Both slots must be occupied. "
                       f"{'Slot ' + request.slot_a + ' empty. ' if not slot_a else ''}"
                       f"{'Slot ' + request.slot_b + ' empty.' if not slot_b else ''}",
            )

        player_a = session.get(Player, slot_a.player_id)
        player_b = session.get(Player, slot_b.player_id)

        if not player_a or not player_b:
            raise HTTPException(status_code=400, detail="Player data missing")

        # Validate position eligibility
        pos_b = _get_slot_position(request.slot_b)
        pos_a = _get_slot_position(request.slot_a)

        if not _player_fits_slot(player_a.position, pos_b):
            raise HTTPException(
                status_code=400,
                detail=f"{player_a.name} ({player_a.position or '?'}) cannot play {pos_b} slot",
            )
        if not _player_fits_slot(player_b.position, pos_a):
            raise HTTPException(
                status_code=400,
                detail=f"{player_b.name} ({player_b.position or '?'}) cannot play {pos_a} slot",
            )

        # Execute swap
        slot_a.position_slot, slot_b.position_slot = request.slot_b, request.slot_a

        # Clear emergency status if player moved to field
        a_cleared = _clear_emergency_if_on_field(session, slot_a)
        b_cleared = _clear_emergency_if_on_field(session, slot_b)
        session.commit()

        return {
            "status": "ok",
            "slot_a": {"player_name": player_a.name, "new_slot": request.slot_b},
            "slot_b": {"player_name": player_b.name, "new_slot": request.slot_a},
            "emergency_cleared": a_cleared or b_cleared,
        }
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.get("/optimise")
def get_optimised_lineup(
    round_num: int = None,
    user: dict = Depends(get_current_user),
) -> dict:
    """Get AI-optimised lineup suggestions for a round.

    Returns swap suggestions but does NOT execute them.
    """
    from src.analytics.lineup_optimiser import optimise_lineup
    from src.utils.config import get_config

    config = get_config()
    target_round = round_num or config.current_round

    # Auto-detect round
    session = get_session()
    try:
        from src.analytics.byes import detect_current_round
        detected = detect_current_round(session, config.season)
        if detected > 0 and round_num is None:
            target_round = detected
    except Exception:
        pass

    try:
        result = optimise_lineup(session, user["user_id"], config.season, target_round)
        return result
    finally:
        session.close()


@router.get("/emergency/suggest")
def suggest_emergencies(
    round_num: int = None,
    user: dict = Depends(get_current_user),
) -> dict:
    """Suggest optimal emergency nominations for a round.

    Prioritises covering positions with at-risk field players (bye/injured),
    then fills remaining slots with highest projected bench players.
    """
    from src.analytics.projections import project_player
    from src.analytics.byes import get_bye_teams, detect_current_round
    from src.utils.config import get_config
    from src.utils.teams import normalize_team

    config = get_config()
    session = get_session()
    try:
        target_round = round_num or config.current_round
        try:
            detected = detect_current_round(session, config.season)
            if detected > 0 and round_num is None:
                target_round = detected
        except Exception:
            pass

        bye_teams = get_bye_teams(session, config.season, target_round)

        # Get all team slots
        slots = (
            session.execute(
                select(MyTeamSlot, Player)
                .join(Player, MyTeamSlot.player_id == Player.id)
                .where(MyTeamSlot.user_id == user["user_id"])
            )
            .all()
        )

        # Find at-risk field players by position
        at_risk: dict[str, list] = {"DEF": [], "MID": [], "RUC": [], "FWD": []}
        for slot, player in slots:
            if slot.position_slot.startswith("BENCH") or slot.position_slot.startswith("FLEX"):
                continue
            pos = _get_slot_position(slot.position_slot)
            if pos not in at_risk:
                continue
            reason = None
            if normalize_team(player.team) in bye_teams or player.team in bye_teams:
                reason = "BYE"
            else:
                inj = session.execute(
                    select(Injury).where(Injury.player_id == player.id)
                ).scalar_one_or_none()
                if inj and inj.status in ("OUT", "DOUBTFUL"):
                    reason = inj.status
            if reason:
                at_risk[pos].append({"player_name": player.name, "slot": slot.position_slot, "reason": reason})

        # Get bench players with projections (exclude those on bye)
        bench_candidates = []
        for slot, player in slots:
            if not slot.position_slot.startswith("BENCH"):
                continue
            is_bye = normalize_team(player.team) in bye_teams or player.team in bye_teams
            if is_bye:
                continue
            proj = project_player(player.id, target_round, season=config.season)
            proj_score = proj.projected_score if proj else 0.0
            positions = [p.strip().upper() for p in (player.position or "").split("/") if p.strip()]
            bench_candidates.append({
                "player_id": player.id,
                "player_name": player.name,
                "positions": positions,
                "projected": round(proj_score, 1),
            })
        bench_candidates.sort(key=lambda x: x["projected"], reverse=True)

        # Greedy suggest: cover at-risk positions first, then fill with best remaining
        suggestions = []
        used_ids = set()
        pos_counts = {"DEF": 0, "MID": 0, "RUC": 0, "FWD": 0}

        # Pass 1: cover at-risk positions
        for pos in ("DEF", "MID", "RUC", "FWD"):
            if not at_risk[pos]:
                continue
            needed = min(len(at_risk[pos]), 2)
            for cand in bench_candidates:
                if len(suggestions) >= 4 or pos_counts[pos] >= needed:
                    break
                if cand["player_id"] in used_ids:
                    continue
                if pos not in cand["positions"]:
                    continue
                suggestions.append({
                    "player_id": cand["player_id"],
                    "player_name": cand["player_name"],
                    "emergency_position": pos,
                    "projected": cand["projected"],
                    "covers": at_risk[pos],
                })
                used_ids.add(cand["player_id"])
                pos_counts[pos] += 1

        # Pass 2: fill remaining with highest projected
        for cand in bench_candidates:
            if len(suggestions) >= 4:
                break
            if cand["player_id"] in used_ids:
                continue
            for pos in cand["positions"]:
                if pos in pos_counts and pos_counts[pos] < 2:
                    suggestions.append({
                        "player_id": cand["player_id"],
                        "player_name": cand["player_name"],
                        "emergency_position": pos,
                        "projected": cand["projected"],
                        "covers": at_risk.get(pos, []),
                    })
                    used_ids.add(cand["player_id"])
                    pos_counts[pos] += 1
                    break

        return {"round": target_round, "suggestions": suggestions}
    finally:
        session.close()
