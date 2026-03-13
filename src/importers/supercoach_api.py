from __future__ import annotations

"""Import official player data from the SuperCoach API.

Fetches the public players endpoint from supercoach.heraldsun.com.au
and syncs names, teams, positions, and previous season stats into
the local database.  This is the most authoritative source for
correct player names and current club assignments.

Usage:
    from src.importers.supercoach_api import sync_from_supercoach_api
    result = sync_from_supercoach_api(season=2026)
"""

import json
import logging
from typing import Dict, List, Optional

import httpx
from sqlalchemy import select

from src.models.database import DfsPlayerStats, Player, SupercoachScore, get_session, init_db

logger = logging.getLogger(__name__)

API_URL = (
    "https://supercoach.heraldsun.com.au/{season}/api/afl/classic/v1/"
    "players-cf?round=0&embed=player_stats,positions"
)

ROUND_API_URL = (
    "https://supercoach.heraldsun.com.au/{season}/api/afl/classic/v1/"
    "players-cf?round={round_num}&embed=player_stats,positions"
)

# SuperCoach team abbreviations -> our standard names
SC_TEAM_MAP: Dict[str, str] = {
    "ADE": "Adelaide",
    "BRL": "Brisbane",
    "CAR": "Carlton",
    "COL": "Collingwood",
    "ESS": "Essendon",
    "FRE": "Fremantle",
    "GCS": "Gold Coast",
    "GEE": "Geelong",
    "GWS": "GWS",
    "HAW": "Hawthorn",
    "MEL": "Melbourne",
    "NTH": "North Melbourne",
    "PTA": "Port Adelaide",
    "RIC": "Richmond",
    "STK": "St Kilda",
    "SYD": "Sydney",
    "WBD": "Western Bulldogs",
    "WCE": "West Coast",
}


def _fetch_players(season: int) -> List[dict]:
    """Fetch all players from the SuperCoach API."""
    url = API_URL.format(season=season)
    logger.info("Fetching players from %s", url)

    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url)
        resp.raise_for_status()
        players = resp.json()

    logger.info("Fetched %d players from SuperCoach API", len(players))
    return players


def _resolve_team(sc_player: dict) -> str:
    """Get the standard team name from a SuperCoach player dict."""
    team_data = sc_player.get("team", {})
    abbrev = team_data.get("abbrev", "")
    return SC_TEAM_MAP.get(abbrev, team_data.get("name", "Unknown"))


def _resolve_position(sc_player: dict) -> Optional[str]:
    """Get position string from SuperCoach player positions list.

    Handles dual-position players (e.g. DEF/MID).
    """
    positions = sc_player.get("positions", [])
    if not positions:
        return None

    # Sort by 'sort' field to get primary position first
    sorted_pos = sorted(positions, key=lambda p: p.get("sort", 99))
    pos_codes = [p.get("position", "") for p in sorted_pos if p.get("position")]

    if not pos_codes:
        return None

    return "/".join(pos_codes)


def sync_from_supercoach_api(season: int = 2026) -> dict:
    """Sync player database from the official SuperCoach API.

    For each player from the API:
      1. Match by feed_id (Champion Data ID) if available
      2. Match by exact name
      3. Match by fuzzy name (last name)
      4. Create new player if no match found

    Updates: name, team, position, previous season stats.

    Returns a summary dict with counts.
    """
    init_db()
    session = get_session()

    try:
        sc_players = _fetch_players(season)

        # Build lookup indexes from existing DB players
        all_db_players = session.execute(select(Player)).scalars().all()

        # Index by name (lowercase) for matching
        by_name: Dict[str, List[Player]] = {}
        for p in all_db_players:
            key = p.name.lower()
            by_name.setdefault(key, []).append(p)

        # Index by last name for fuzzy matching
        by_last_name: Dict[str, List[Player]] = {}
        for p in all_db_players:
            parts = p.name.split()
            if parts:
                lname = parts[-1].lower()
                by_last_name.setdefault(lname, []).append(p)

        updated = 0
        created = 0
        name_corrections: List[str] = []
        sc_player_ids = set()

        for sc_p in sc_players:
            first = sc_p.get("first_name", "").strip()
            last = sc_p.get("last_name", "").strip()
            if not first or not last:
                continue

            sc_name = f"{first} {last}"
            sc_team = _resolve_team(sc_p)
            sc_pos = _resolve_position(sc_p)
            sc_feed_id = sc_p.get("feed_id")
            sc_prev_avg = sc_p.get("previous_average")
            sc_prev_games = sc_p.get("previous_games")

            # Extract real starting price from player_stats embed
            sc_price = None
            player_stats = sc_p.get("player_stats", [])
            if player_stats and isinstance(player_stats, list):
                # Round 0 stats contain the starting price
                for ps in player_stats:
                    if ps.get("price"):
                        sc_price = ps["price"]
                        break

            # Try to match existing player
            matched = None

            # 1. Exact name match
            candidates = by_name.get(sc_name.lower(), [])
            if candidates:
                # Prefer same team
                for c in candidates:
                    if c.team == sc_team:
                        matched = c
                        break
                if not matched:
                    matched = candidates[0]

            # 2. Fuzzy: match by last name + first initial
            if not matched:
                last_candidates = by_last_name.get(last.lower(), [])
                for c in last_candidates:
                    db_first = c.name.split()[0] if c.name.split() else ""
                    # Same first initial and same team -> likely a name variant
                    if db_first and first and db_first[0].lower() == first[0].lower():
                        if c.team == sc_team or _resolve_team(sc_p) == c.team:
                            matched = c
                            break

            if matched:
                sc_player_ids.add(matched.id)
                changed = False

                # Fix incorrect names
                if matched.name != sc_name:
                    old_name = matched.name
                    matched.name = sc_name
                    name_corrections.append(f"{old_name} -> {sc_name}")
                    changed = True

                # Update team
                if matched.team != sc_team:
                    matched.team = sc_team
                    changed = True

                # Update position
                if sc_pos and matched.position != sc_pos:
                    matched.position = sc_pos
                    changed = True

                # Ensure active
                if not matched.is_active:
                    matched.is_active = True
                    changed = True

                # Update DFS stats with SuperCoach API data
                dfs = session.execute(
                    select(DfsPlayerStats).where(
                        DfsPlayerStats.player_id == matched.id,
                        DfsPlayerStats.season == season,
                    )
                ).scalar_one_or_none()

                if dfs is None:
                    # No DFS record at all — create one
                    dfs = DfsPlayerStats(
                        player_id=matched.id,
                        season=season,
                    )
                    session.add(dfs)

                # Update SC avg from API if we don't have one
                if sc_prev_avg and not dfs.sc_avg:
                    dfs.sc_avg = sc_prev_avg
                    changed = True

                # Update games if missing
                if sc_prev_games and not dfs.games_played:
                    dfs.games_played = sc_prev_games
                    changed = True

                # Use real starting price from API
                if sc_price and (not dfs.salary or dfs.salary != sc_price):
                    dfs.salary = sc_price
                    changed = True

                if changed:
                    updated += 1
            else:
                # Create new player
                new_player = Player(
                    name=sc_name,
                    team=sc_team,
                    position=sc_pos,
                    is_active=True,
                )
                session.add(new_player)
                session.flush()
                sc_player_ids.add(new_player.id)
                created += 1

                # Create DFS stats stub with previous season data and price
                if sc_prev_avg or sc_prev_games or sc_price:
                    dfs = DfsPlayerStats(
                        player_id=new_player.id,
                        season=season,
                        sc_avg=sc_prev_avg,
                        games_played=sc_prev_games,
                        salary=sc_price,
                    )
                    session.add(dfs)

        # Deactivate players not in SuperCoach
        deactivated = 0
        for p in all_db_players:
            if p.id not in sc_player_ids and p.is_active:
                p.is_active = False
                deactivated += 1

        session.commit()

        summary = {
            "api_total": len(sc_players),
            "updated": updated,
            "created": created,
            "deactivated": deactivated,
            "name_corrections": name_corrections,
        }

        logger.info(
            "SuperCoach API sync complete: %d from API, %d updated, %d created, "
            "%d deactivated, %d names corrected",
            len(sc_players), updated, created, deactivated, len(name_corrections),
        )
        return summary

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sync_round_from_supercoach_api(season: int = 2026, round_num: int = 1) -> dict:
    """Sync per-round data (scores, prices, breakevens) from the SuperCoach API.

    Fetches round=N data and upserts SupercoachScore records.
    """
    init_db()
    session = get_session()

    try:
        url = ROUND_API_URL.format(season=season, round_num=round_num)
        logger.info("Fetching round %d data from %s", round_num, url)

        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            sc_players = resp.json()

        logger.info("Fetched %d players for round %d", len(sc_players), round_num)

        # Build name lookup from DB
        all_db_players = session.execute(select(Player)).scalars().all()
        by_name: Dict[str, Player] = {}
        for p in all_db_players:
            by_name[p.name.lower()] = p

        updated = 0
        skipped = 0

        for sc_p in sc_players:
            first = sc_p.get("first_name", "").strip()
            last = sc_p.get("last_name", "").strip()
            if not first or not last:
                skipped += 1
                continue

            sc_name = f"{first} {last}"
            player = by_name.get(sc_name.lower())
            if player is None:
                skipped += 1
                continue

            # Extract round stats from player_stats embed
            player_stats = sc_p.get("player_stats", [])
            if not player_stats:
                continue

            # Find stats for this round, or use latest entry
            round_stats = None
            for ps in player_stats:
                if ps.get("round") == round_num:
                    round_stats = ps
                    break
            if round_stats is None:
                round_stats = player_stats[0] if player_stats else None
            if round_stats is None:
                continue

            # SC API uses 'points' not 'score', and 'ba' for breakeven
            score = round_stats.get("points")
            price = round_stats.get("price")
            breakeven = round_stats.get("breakeven") or round_stats.get("ba")

            # Skip if no useful data
            if score is None and price is None and breakeven is None:
                continue

            # Upsert SupercoachScore
            existing = session.execute(
                select(SupercoachScore).where(
                    SupercoachScore.player_id == player.id,
                    SupercoachScore.season == season,
                    SupercoachScore.round == round_num,
                )
            ).scalar_one_or_none()

            if existing:
                changed = False
                if score is not None and existing.score != score:
                    existing.score = score
                    changed = True
                if price is not None and existing.price != price:
                    existing.price = price
                    changed = True
                if breakeven is not None and existing.breakeven != breakeven:
                    existing.breakeven = breakeven
                    changed = True
                if changed:
                    updated += 1
            else:
                new_score = SupercoachScore(
                    player_id=player.id,
                    season=season,
                    round=round_num,
                    score=score,
                    price=price,
                    breakeven=breakeven,
                )
                session.add(new_score)
                updated += 1

        session.commit()

        summary = {
            "round": round_num,
            "api_total": len(sc_players),
            "updated": updated,
            "skipped": skipped,
        }
        logger.info(
            "SuperCoach round %d sync: %d updated, %d skipped",
            round_num, updated, skipped,
        )
        return summary

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
