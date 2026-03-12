from __future__ import annotations

"""Team management CRUD endpoints."""

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import desc, select

from src.models.database import (
    DfsPlayerStats,
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
                "salary": salary,
                "sc_avg": round(dfs.sc_avg, 1) if dfs and dfs.sc_avg else None,
                "last_score": latest.score if latest else None,
                "season_avg": round(avg, 1) if avg else None,
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
