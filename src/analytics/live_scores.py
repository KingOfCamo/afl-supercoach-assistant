from __future__ import annotations

"""Live scoring tracker for round-in-progress monitoring.

Fetches live match data from the Squiggle API (which provides live AFL scores)
and cross-references with your team to show projected round scores.

Features:
- Pulls live match status (in progress, complete, upcoming)
- Computes projected team total based on current + projected remaining scores
- Tracks captain/VC scoring live
- Shows bench coverage for emergencies
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy import desc, select

from src.models.database import (
    Fixture,
    LineupStatus,
    MyTeamSlot,
    Player,
    SupercoachScore,
    get_session,
)

logger = logging.getLogger(__name__)


@dataclass
class LivePlayerScore:
    """Live score state for a single player."""

    player_id: int
    player_name: str
    team: str
    position: Optional[str]
    position_slot: str
    is_captain: bool
    is_vice_captain: bool
    is_emergency: bool
    live_score: Optional[int]  # None if game not started
    projected_final: Optional[float]  # projected end-of-game score
    match_status: str  # "upcoming", "in_progress", "complete"
    opponent: Optional[str]


@dataclass
class LiveRoundSummary:
    """Live summary of your team's round."""

    round_num: int
    season: int
    players: List[LivePlayerScore] = field(default_factory=list)
    total_live_score: int = 0
    projected_total: float = 0.0
    games_complete: int = 0
    games_in_progress: int = 0
    games_upcoming: int = 0
    captain_name: Optional[str] = None
    captain_score: Optional[int] = None


def get_live_round(
    round_num: int,
    season: Optional[int] = None,
    user_id: Optional[int] = None,
) -> LiveRoundSummary:
    """Get live scoring summary for a round.

    Cross-references fixture status with your team to show current
    and projected scores.

    Note: Until live data is available (Squiggle provides post-match only
    for free tier), this uses fixture completion status + historical scores.
    """
    from src.utils.config import get_config

    config = get_config()
    target_season = season or config.season

    session = get_session()
    try:
        query = select(MyTeamSlot).order_by(MyTeamSlot.position_slot)
        if user_id is not None:
            query = query.where(MyTeamSlot.user_id == user_id)
        slots = session.execute(query).scalars().all()

        if not slots:
            return LiveRoundSummary(
                round_num=round_num, season=target_season
            )

        # Build fixture status map: team -> match status
        fixtures = session.execute(
            select(Fixture).where(
                Fixture.season == target_season,
                Fixture.round == round_num,
            )
        ).scalars().all()

        team_status = {}
        team_opponent = {}
        for f in fixtures:
            status = "complete" if f.is_complete else "upcoming"
            # If real scores exist but game not marked complete, it's in progress
            # home_score=0 is the default/unplayed state, not a real score
            if not f.is_complete and f.home_score is not None and f.home_score > 0:
                status = "in_progress"

            team_status[f.home_team] = status
            team_status[f.away_team] = status
            team_opponent[f.home_team] = f.away_team
            team_opponent[f.away_team] = f.home_team

        players = []
        total_live = 0
        projected_total = 0.0
        games_complete = 0
        games_ip = 0
        games_upcoming = 0
        captain_name = None
        captain_score = None

        for slot in slots:
            player = session.get(Player, slot.player_id)
            if player is None:
                continue

            from src.utils.teams import normalize_team

            norm_team = normalize_team(player.team)
            match_status = team_status.get(norm_team, "upcoming")
            opponent = team_opponent.get(norm_team)

            # Fallback: get opponent from lineup data if no fixtures
            if opponent is None:
                lineup_row = session.execute(
                    select(LineupStatus).where(
                        LineupStatus.player_id == player.id,
                        LineupStatus.season == target_season,
                        LineupStatus.round == round_num,
                    )
                ).scalar_one_or_none()
                if lineup_row and lineup_row.opponent:
                    opponent = lineup_row.opponent

            # Get score for this round (if available)
            score_row = session.execute(
                select(SupercoachScore)
                .where(
                    SupercoachScore.player_id == player.id,
                    SupercoachScore.season == target_season,
                    SupercoachScore.round == round_num,
                )
            ).scalar_one_or_none()

            live_score = score_row.score if score_row and score_row.score is not None else None

            # Treat score=0 as "no real score yet" — SC API returns 0 for
            # players whose game hasn't started
            if live_score == 0:
                live_score = None

            # If we have a real score but no fixture data, infer match status
            # (handles Opening Round / missing Squiggle data)
            if live_score is not None and match_status == "upcoming":
                match_status = "complete"

            # Get projected final from projections or rolling avg
            from src.analytics.projections import project_player

            proj = project_player(player.id, round_num, season=target_season)
            projected_final = proj.projected_score if proj else None

            # Use actual score if game is complete
            if match_status == "complete" and live_score is not None:
                projected_final = float(live_score)

            lps = LivePlayerScore(
                player_id=player.id,
                player_name=player.name,
                team=player.team,
                position=player.position,
                position_slot=slot.position_slot,
                is_captain=slot.is_captain,
                is_vice_captain=slot.is_vice_captain,
                is_emergency=slot.is_emergency,
                live_score=live_score,
                projected_final=projected_final,
                match_status=match_status,
                opponent=opponent,
            )
            players.append(lps)

            # Compute totals (skip emergencies/bench unless covering)
            if not slot.is_emergency:
                if live_score is not None:
                    effective = live_score
                    if slot.is_captain:
                        effective *= 2
                        captain_name = player.name
                        captain_score = live_score * 2
                    total_live += effective

                if projected_final is not None:
                    effective_proj = projected_final
                    if slot.is_captain:
                        effective_proj *= 2
                    projected_total += effective_proj

        # Count game statuses
        seen_statuses = set()
        for p in players:
            key = (p.team, p.match_status)
            if key not in seen_statuses:
                seen_statuses.add(key)
                if p.match_status == "complete":
                    games_complete += 1
                elif p.match_status == "in_progress":
                    games_ip += 1
                else:
                    games_upcoming += 1

        return LiveRoundSummary(
            round_num=round_num,
            season=target_season,
            players=players,
            total_live_score=total_live,
            projected_total=round(projected_total, 1),
            games_complete=games_complete,
            games_in_progress=games_ip,
            games_upcoming=games_upcoming,
            captain_name=captain_name,
            captain_score=captain_score,
        )

    finally:
        session.close()


def get_round_summary(
    round_num: int,
    season: Optional[int] = None,
) -> Optional[dict]:
    """Get post-round summary statistics for a completed round.

    Returns a dict with team performance breakdown.
    """
    from src.utils.config import get_config

    config = get_config()
    target_season = season or config.season

    session = get_session()
    try:
        slots = session.execute(select(MyTeamSlot)).scalars().all()
        if not slots:
            return None

        player_scores = []
        total = 0
        captain_points = 0
        bench_points = 0
        dnp_count = 0

        for slot in slots:
            player = session.get(Player, slot.player_id)
            if not player:
                continue

            score_row = session.execute(
                select(SupercoachScore)
                .where(
                    SupercoachScore.player_id == player.id,
                    SupercoachScore.season == target_season,
                    SupercoachScore.round == round_num,
                )
            ).scalar_one_or_none()

            score = score_row.score if score_row and score_row.score is not None else None
            is_bench = slot.position_slot.startswith("BENCH") or slot.is_emergency

            entry = {
                "name": player.name,
                "team": player.team,
                "position": player.position,
                "slot": slot.position_slot,
                "score": score,
                "is_captain": slot.is_captain,
                "is_vice_captain": slot.is_vice_captain,
                "is_bench": is_bench,
            }
            player_scores.append(entry)

            if score is not None:
                if is_bench:
                    bench_points += score
                else:
                    effective = score * 2 if slot.is_captain else score
                    total += effective
                    if slot.is_captain:
                        captain_points = score
            else:
                if not is_bench:
                    dnp_count += 1

        # Get previous round score for comparison
        prev_round = round_num - 1
        prev_total = 0
        if prev_round >= 1:
            for slot in slots:
                prev_score = session.execute(
                    select(SupercoachScore.score)
                    .where(
                        SupercoachScore.player_id == slot.player_id,
                        SupercoachScore.season == target_season,
                        SupercoachScore.round == prev_round,
                    )
                ).scalar_one_or_none()
                if prev_score is not None and not (
                    slot.position_slot.startswith("BENCH") or slot.is_emergency
                ):
                    effective = prev_score * 2 if slot.is_captain else prev_score
                    prev_total += effective

        player_scores.sort(key=lambda p: p["score"] or 0, reverse=True)

        return {
            "round_num": round_num,
            "season": target_season,
            "total_score": total,
            "captain_points": captain_points,
            "bench_points": bench_points,
            "dnp_count": dnp_count,
            "prev_round_total": prev_total,
            "change": total - prev_total if prev_total > 0 else 0,
            "players": player_scores,
            "top_scorer": player_scores[0]["name"] if player_scores and player_scores[0]["score"] else None,
            "top_score": player_scores[0]["score"] if player_scores and player_scores[0]["score"] else 0,
        }

    finally:
        session.close()
