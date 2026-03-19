from __future__ import annotations

"""Bye round derivation and analysis.

Derives bye rounds from the fixture table (teams not playing in a round)
and provides impact analysis for team management during bye rounds.
"""

import logging
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from src.models.database import ByeRound, Fixture, MyTeamSlot, Player, Injury
from src.utils.teams import CANONICAL_TEAMS

logger = logging.getLogger(__name__)


def derive_bye_rounds(session: Session, season: int) -> int:
    """Derive bye rounds from fixture data and store in bye_rounds table.

    For each round, any team from CANONICAL_TEAMS not appearing as
    home_team or away_team has a bye.

    Returns count of bye entries created.
    """
    fixtures = (
        session.execute(
            select(Fixture).where(Fixture.season == season)
        )
        .scalars()
        .all()
    )

    # Group games by round
    rounds: dict[int, set[str]] = {}
    for f in fixtures:
        if f.round not in rounds:
            rounds[f.round] = set()
        rounds[f.round].add(f.home_team)
        rounds[f.round].add(f.away_team)

    # Delete existing bye data for this season and re-derive
    session.execute(
        delete(ByeRound).where(ByeRound.season == season)
    )

    count = 0
    for round_num, playing_teams in sorted(rounds.items()):
        # Full round = 9 games = 18 teams, no byes
        if len(playing_teams) >= 18:
            continue

        bye_teams = CANONICAL_TEAMS - playing_teams
        for team in sorted(bye_teams):
            session.add(ByeRound(season=season, round=round_num, team=team))
            count += 1

    session.commit()
    logger.info("Derived %d bye entries for season %d", count, season)
    return count


def get_bye_teams(session: Session, season: int, round_num: int) -> set[str]:
    """Get the set of teams on bye for a specific round."""
    rows = (
        session.execute(
            select(ByeRound.team).where(
                ByeRound.season == season,
                ByeRound.round == round_num,
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


def get_player_bye_rounds(session: Session, team: str, season: int) -> list[int]:
    """Get all bye round numbers for a given team in a season."""
    rows = (
        session.execute(
            select(ByeRound.round)
            .where(ByeRound.season == season, ByeRound.team == team)
            .order_by(ByeRound.round)
        )
        .scalars()
        .all()
    )
    return list(rows)


def get_bye_impact(
    session: Session,
    user_id: int,
    season: int,
    round_num: int,
) -> dict:
    """Get bye impact analysis for a user's team in a specific round.

    Returns dict with playing/bye counts, affected players, bench coverage.
    """
    bye_teams = get_bye_teams(session, season, round_num)
    if not bye_teams:
        return {
            "round": round_num,
            "has_byes": False,
            "total_on_field": 22,
            "playing": 22,
            "on_bye": 0,
            "bye_players": [],
            "available_bench": [],
            "coverage_gaps": [],
        }

    # Get user's team slots with player data
    slots = (
        session.execute(
            select(MyTeamSlot, Player)
            .join(Player, MyTeamSlot.player_id == Player.id)
            .where(MyTeamSlot.user_id == user_id)
            .order_by(MyTeamSlot.position_slot)
        )
        .all()
    )

    on_field_bye = []
    on_field_playing = []
    bench_available = []
    bench_on_bye = []

    for slot, player in slots:
        is_bench = slot.position_slot.startswith("BENCH") or slot.position_slot.startswith("FLEX")
        is_on_bye = player.team in bye_teams

        # Get position line (DEF/MID/RUC/FWD) from slot name
        pos_line = slot.position_slot.rstrip("0123456789")

        entry = {
            "player_id": player.id,
            "player_name": player.name,
            "team": player.team,
            "position": player.position,
            "position_slot": slot.position_slot,
            "position_line": pos_line,
            "is_emergency": slot.is_emergency,
            "emergency_order": slot.emergency_order,
        }

        if is_bench:
            if is_on_bye:
                bench_on_bye.append(entry)
            else:
                bench_available.append(entry)
        else:
            if is_on_bye:
                on_field_bye.append(entry)
            else:
                on_field_playing.append(entry)

    # Check position coverage gaps
    bye_lines = set()
    for p in on_field_bye:
        bye_lines.add(p["position_line"])

    available_lines = set()
    for p in bench_available:
        # Bench players can cover based on their player position
        if p["position"]:
            for pos in p["position"].split("/"):
                available_lines.add(pos)

    coverage_gaps = []
    for line in bye_lines:
        if line not in available_lines and line not in ("FLEX",):
            coverage_gaps.append(f"No bench player available to cover {line}")

    return {
        "round": round_num,
        "has_byes": True,
        "total_on_field": len(on_field_playing) + len(on_field_bye),
        "playing": len(on_field_playing),
        "on_bye": len(on_field_bye),
        "bye_players": on_field_bye,
        "available_bench": bench_available,
        "bench_on_bye": bench_on_bye,
        "coverage_gaps": coverage_gaps,
    }


def compute_bye_risk_score(
    session: Session,
    user_id: int,
    season: int,
) -> dict:
    """Compute a 0-100 bye readiness score and per-round summaries.

    Lower score = more risk. Higher = better prepared.
    """
    # Find all rounds that have byes
    bye_round_nums = (
        session.execute(
            select(ByeRound.round)
            .where(ByeRound.season == season)
            .distinct()
            .order_by(ByeRound.round)
        )
        .scalars()
        .all()
    )

    if not bye_round_nums:
        return {"score": 100, "round_summaries": {}, "bye_rounds": []}

    round_summaries = {}
    risk_deductions = 0

    for rnd in bye_round_nums:
        impact = get_bye_impact(session, user_id, season, rnd)
        playing = impact["playing"]
        on_bye = impact["on_bye"]

        danger = playing < 18
        warning = playing < 20

        round_summaries[rnd] = {
            "playing": playing,
            "on_bye": on_bye,
            "danger": danger,
            "warning": warning,
            "coverage_gaps": impact["coverage_gaps"],
        }

        if danger:
            risk_deductions += 20
        elif warning:
            risk_deductions += 10

        risk_deductions += len(impact["coverage_gaps"]) * 5

    score = max(0, 100 - risk_deductions)

    return {
        "score": score,
        "round_summaries": round_summaries,
        "bye_rounds": list(bye_round_nums),
    }


def get_bye_planner_data(
    session: Session,
    user_id: int,
    season: int,
) -> dict:
    """Get full bye planner data: player matrix, summaries, risk score."""
    risk_data = compute_bye_risk_score(session, user_id, season)
    bye_rounds = risk_data["bye_rounds"]

    if not bye_rounds:
        return {
            "bye_rounds": [],
            "players": [],
            "round_summaries": {},
            "bye_risk_score": 100,
        }

    # Get all bye teams per round
    bye_teams_by_round: dict[int, set[str]] = {}
    for rnd in bye_rounds:
        bye_teams_by_round[rnd] = get_bye_teams(session, season, rnd)

    # Get user's team
    slots = (
        session.execute(
            select(MyTeamSlot, Player)
            .join(Player, MyTeamSlot.player_id == Player.id)
            .where(MyTeamSlot.user_id == user_id)
            .order_by(MyTeamSlot.position_slot)
        )
        .all()
    )

    # Get active injuries for these players
    player_ids = [p.id for _, p in slots]
    injuries = {}
    if player_ids:
        injury_rows = (
            session.execute(
                select(Injury).where(Injury.player_id.in_(player_ids))
            )
            .scalars()
            .all()
        )
        for inj in injury_rows:
            injuries[inj.player_id] = inj

    players = []
    for slot, player in slots:
        round_status = {}
        for rnd in bye_rounds:
            if player.team in bye_teams_by_round[rnd]:
                round_status[str(rnd)] = "bye"
            elif player.id in injuries:
                round_status[str(rnd)] = "injured"
            else:
                round_status[str(rnd)] = "playing"

        players.append({
            "player_id": player.id,
            "player_name": player.name,
            "team": player.team,
            "position": player.position,
            "position_slot": slot.position_slot,
            "is_on_field": not (
                slot.position_slot.startswith("BENCH")
                or slot.position_slot.startswith("FLEX")
            ),
            "round_status": round_status,
        })

    return {
        "bye_rounds": bye_rounds,
        "players": players,
        "round_summaries": risk_data["round_summaries"],
        "bye_risk_score": risk_data["score"],
    }
