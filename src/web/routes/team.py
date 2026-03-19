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


class EmergencyRequest(BaseModel):
    """Set 4 bench emergencies in priority order.

    Each entry is a player_id. Order matters: index 0 = E1 (highest priority).
    SuperCoach rules: exactly 4 emergencies from bench, one per position line
    (DEF, MID, RUC, FWD). When an on-field player DNPs, the highest-priority
    emergency matching that position auto-subs in.
    """
    emergencies: List[int]  # list of player_ids in priority order (max 4)


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
    """Set bench emergencies in priority order.

    SuperCoach rules:
    - Max 4 emergencies from bench players
    - Each covers their position line (DEF, MID, RUC, FWD)
    - Priority: E1 > E2 > E3 > E4
    - When an on-field player DNPs, highest-priority emergency
      matching that position auto-subs in with their score.
    """
    user_id = user["user_id"]
    if len(request.emergencies) > 4:
        raise HTTPException(status_code=400, detail="Maximum 4 emergencies allowed")

    session = get_session()
    try:
        # Clear existing emergencies for this user
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

        # Set new emergencies
        for i, player_id in enumerate(request.emergencies):
            slot = session.execute(
                select(MyTeamSlot).where(
                    MyTeamSlot.user_id == user_id,
                    MyTeamSlot.player_id == player_id,
                )
            ).scalar_one_or_none()
            if not slot:
                raise HTTPException(
                    status_code=400,
                    detail=f"Player {player_id} is not on your team",
                )
            if not slot.position_slot.startswith("BENCH"):
                raise HTTPException(
                    status_code=400,
                    detail="Emergencies must be bench players",
                )
            slot.is_emergency = True
            slot.emergency_order = i + 1  # 1-based

        session.commit()
        return {"success": True, "count": len(request.emergencies)}
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
        session.commit()

        return {
            "status": "ok",
            "slot_a": {"player_name": player_a.name, "new_slot": request.slot_b},
            "slot_b": {"player_name": player_b.name, "new_slot": request.slot_a},
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
