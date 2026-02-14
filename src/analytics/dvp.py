from __future__ import annotations

"""Difficulty vs Position (DVP) analysis.

Computes how many SuperCoach points each team concedes to each position.
Higher DVP rank (18) = easiest matchup (most points conceded).
Lower DVP rank (1) = hardest matchup (fewest points conceded).

DVP is computed from our own SupercoachScore + Fixture data.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from src.models.database import (
    Fixture,
    Player,
    SupercoachScore,
    TeamDVP,
    get_session,
)
from src.utils.teams import normalize_team

logger = logging.getLogger(__name__)

# Map database positions to DVP categories
POSITION_MAP = {
    "DEF": "DEF",
    "MID": "MID",
    "RUC": "RUC",
    "FWD": "FWD",
    "DEF/MID": "MID",
    "MID/FWD": "MID",
    "DEF/FWD": "DEF",
    "FWD/MID": "FWD",
    "RUC/FWD": "RUC",
}


@dataclass
class DVPEntry:
    """DVP ranking for one team/position combo."""

    team: str
    position: str
    avg_points_conceded: float
    dvp_rank: int  # 1 = hardest, 18 = easiest
    games_sampled: int


def _map_position(pos: Optional[str]) -> Optional[str]:
    """Map a player's position to a DVP category."""
    if not pos:
        return None
    # Handle multi-position players
    cleaned = pos.strip().upper()
    if cleaned in POSITION_MAP:
        return POSITION_MAP[cleaned]
    # Try first part of dual position
    if "/" in cleaned:
        first = cleaned.split("/")[0].strip()
        if first in POSITION_MAP:
            return POSITION_MAP[first]
    return None


def compute_dvp(
    season: int,
    as_of_round: int,
    save: bool = True,
) -> list[DVPEntry]:
    """Compute DVP rankings for all teams and positions.

    Analyses completed fixtures to determine how many SC points each team
    concedes to each position (DEF/MID/RUC/FWD).

    Args:
        season: AFL season year.
        as_of_round: Compute DVP using rounds 1..as_of_round.
        save: Whether to persist results to the TeamDVP table.

    Returns:
        List of DVPEntry objects sorted by position then rank.
    """
    session = get_session()
    try:
        # Get completed fixtures up to as_of_round
        fixtures = (
            session.execute(
                select(Fixture).where(
                    Fixture.season == season,
                    Fixture.round <= as_of_round,
                    Fixture.is_complete == True,  # noqa: E712
                )
            )
            .scalars()
            .all()
        )

        if not fixtures:
            logger.info(f"No completed fixtures for {season} up to R{as_of_round}")
            return []

        # Build a map: for each game, which team was the opponent?
        opponent_map: dict[tuple[int, int, str], str] = {}
        for f in fixtures:
            opponent_map[(f.season, f.round, f.home_team)] = f.away_team
            opponent_map[(f.season, f.round, f.away_team)] = f.home_team

        # Get all scores with player position for completed rounds
        scores_with_players = (
            session.execute(
                select(SupercoachScore, Player)
                .join(Player, SupercoachScore.player_id == Player.id)
                .where(
                    SupercoachScore.season == season,
                    SupercoachScore.round <= as_of_round,
                    SupercoachScore.score.isnot(None),
                )
            )
            .all()
        )

        # Accumulate: opponent_team -> position -> [scores conceded]
        conceded: dict[str, dict[str, list[int]]] = {}

        for sc_score, player in scores_with_players:
            dvp_pos = _map_position(player.position)
            if dvp_pos is None:
                continue

            player_team = normalize_team(player.team)
            key = (season, sc_score.round, player_team)
            opponent = opponent_map.get(key)
            if opponent is None:
                continue

            # The opponent conceded these points to this position
            if opponent not in conceded:
                conceded[opponent] = {}
            if dvp_pos not in conceded[opponent]:
                conceded[opponent][dvp_pos] = []
            conceded[opponent][dvp_pos].append(sc_score.score)

        # Compute averages and rank within each position
        entries: list[DVPEntry] = []
        for position in ["DEF", "MID", "RUC", "FWD"]:
            position_data: list[tuple[str, float, int]] = []
            for team, pos_scores in conceded.items():
                scores_list = pos_scores.get(position, [])
                if not scores_list:
                    continue
                avg = sum(scores_list) / len(scores_list)
                position_data.append((team, avg, len(scores_list)))

            # Sort by avg ascending (fewest conceded = rank 1 = hardest)
            position_data.sort(key=lambda x: x[1])

            for rank, (team, avg, count) in enumerate(position_data, 1):
                entries.append(
                    DVPEntry(
                        team=team,
                        position=position,
                        avg_points_conceded=round(avg, 1),
                        dvp_rank=rank,
                        games_sampled=count,
                    )
                )

        # Optionally save to DB
        if save and entries:
            _save_dvp(session, season, as_of_round, entries)

        return entries
    finally:
        session.close()


def _save_dvp(session: object, season: int, round_num: int, entries: list[DVPEntry]) -> None:
    """Persist DVP entries to the TeamDVP table."""
    for entry in entries:
        existing = session.execute(  # type: ignore[union-attr]
            select(TeamDVP).where(
                TeamDVP.season == season,
                TeamDVP.round == round_num,
                TeamDVP.team == entry.team,
                TeamDVP.position == entry.position,
            )
        ).scalar_one_or_none()

        if existing:
            existing.avg_points_conceded = entry.avg_points_conceded
            existing.dvp_rank = entry.dvp_rank
        else:
            dvp = TeamDVP(
                season=season,
                round=round_num,
                team=entry.team,
                position=entry.position,
                avg_points_conceded=entry.avg_points_conceded,
                dvp_rank=entry.dvp_rank,
            )
            session.add(dvp)  # type: ignore[union-attr]

    session.commit()  # type: ignore[union-attr]


def get_opponent_dvp(
    team: str,
    position: str,
    season: int,
    round_num: int,
) -> Optional[DVPEntry]:
    """Look up DVP for a specific matchup.

    Args:
        team: The team that a player is playing AGAINST.
        position: DVP position category (DEF/MID/RUC/FWD).
        season: Season year.
        round_num: Round to look up DVP for (uses most recent computed).

    Returns:
        DVPEntry or None if not computed yet.
    """
    session = get_session()
    try:
        dvp = session.execute(
            select(TeamDVP)
            .where(
                TeamDVP.team == normalize_team(team),
                TeamDVP.position == position.upper(),
                TeamDVP.season == season,
                TeamDVP.round <= round_num,
            )
            .order_by(TeamDVP.round.desc())
            .limit(1)
        ).scalar_one_or_none()

        if dvp is None:
            return None

        return DVPEntry(
            team=dvp.team,
            position=dvp.position,
            avg_points_conceded=dvp.avg_points_conceded or 0.0,
            dvp_rank=dvp.dvp_rank or 9,
            games_sampled=0,
        )
    finally:
        session.close()


def get_next_opponent(team: str, season: int, round_num: int) -> Optional[str]:
    """Get the opponent for a team in a given round from fixtures.

    Returns the canonical team name of the opponent, or None.
    """
    session = get_session()
    try:
        canonical = normalize_team(team)
        fixture = session.execute(
            select(Fixture).where(
                Fixture.season == season,
                Fixture.round == round_num,
                (Fixture.home_team == canonical) | (Fixture.away_team == canonical),
            )
        ).scalar_one_or_none()

        if fixture is None:
            return None

        if fixture.home_team == canonical:
            return fixture.away_team
        return fixture.home_team
    finally:
        session.close()
