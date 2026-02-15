from __future__ import annotations

"""Sync player names and teams from a CSV player list.

Reads a CSV with PLAYER,TEAM columns (supports section headers like
'Midfielders', 'Rucks', 'Forwards' that split the file into position groups).

For each player:
  1. Exact name match -> update team (and position if from a section)
  2. No match -> create new player with name, team, position
  3. Existing players not in the CSV -> mark as is_active=False

Also normalises inconsistent team names (e.g. 'Blues' -> 'Carlton').
"""

import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select

from src.models.database import Player, get_session, init_db

logger = logging.getLogger(__name__)

# Map common alternate team names to our standard names
TEAM_ALIASES: Dict[str, str] = {
    # FootyWire nickname variants
    "Blues": "Carlton",
    "Bombers": "Essendon",
    "Bulldogs": "Western Bulldogs",
    "Cats": "Geelong",
    "Crows": "Adelaide",
    "Demons": "Melbourne",
    "Dockers": "Fremantle",
    "Eagles": "West Coast",
    "Giants": "GWS",
    "Hawks": "Hawthorn",
    "Kangaroos": "North Melbourne",
    "Lions": "Brisbane",
    "Magpies": "Collingwood",
    "Power": "Port Adelaide",
    "Saints": "St Kilda",
    "Suns": "Gold Coast",
    "Swans": "Sydney",
    "Tigers": "Richmond",
    # Common variations
    "Geelong Cats": "Geelong",
    "Brisbane Lions": "Brisbane",
    "Gold Coast Suns": "Gold Coast",
    "GWS Giants": "GWS",
    "Greater Western Sydney": "GWS",
    "Sydney Swans": "Sydney",
    "Western": "Western Bulldogs",
    "West Coast Eagles": "West Coast",
    "North": "North Melbourne",
    "Port": "Port Adelaide",
}

# The 18 canonical team names
VALID_TEAMS = {
    "Adelaide", "Brisbane", "Carlton", "Collingwood", "Essendon",
    "Fremantle", "Geelong", "Gold Coast", "GWS", "Hawthorn",
    "Melbourne", "North Melbourne", "Port Adelaide", "Richmond",
    "St Kilda", "Sydney", "West Coast", "Western Bulldogs",
}

# Section headers in the CSV that indicate position groups
SECTION_POSITIONS: Dict[str, str] = {
    "Defenders": "DEF",
    "Midfielders": "MID",
    "Rucks": "RUC",
    "Forwards": "FWD",
}


def _normalise_team(raw: str) -> str:
    """Normalise a team name to our standard format."""
    raw = raw.strip()
    if raw in VALID_TEAMS:
        return raw
    if raw in TEAM_ALIASES:
        return TEAM_ALIASES[raw]
    return raw


def _parse_csv(file_path: str) -> List[Tuple[str, str, Optional[str]]]:
    """Parse the player CSV into (name, team, position) tuples.

    The CSV has section headers like 'Midfielders,' that indicate
    position groups. Players listed after a section header get that
    position assigned (unless they already have one from DFS data).
    """
    path = Path(file_path)
    players: List[Tuple[str, str, Optional[str]]] = []
    current_position: Optional[str] = None
    seen_names: set = set()

    with open(str(path), "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or not row[0].strip():
                continue

            first_col = row[0].strip()

            # Check for section header (e.g. "Midfielders" or "Rucks")
            if first_col in SECTION_POSITIONS:
                current_position = SECTION_POSITIONS[first_col]
                continue

            # Skip the repeated PLAYER,TEAM header rows
            if first_col == "PLAYER":
                continue

            # Skip comment/description rows
            if first_col.startswith("Midfielders who have"):
                continue

            # Must have at least 2 columns: PLAYER, TEAM
            if len(row) < 2 or not row[1].strip():
                continue

            name = first_col
            team = _normalise_team(row[1].strip())

            # Deduplicate (some players appear in multiple position sections)
            key = name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)

            players.append((name, team, current_position))

    return players


def sync_player_list(file_path: str) -> dict:
    """Sync the player database from a CSV player list.

    Returns a summary dict with counts of actions taken.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    init_db()
    session = get_session()

    try:
        csv_players = _parse_csv(file_path)
        logger.info("Parsed %d players from CSV", len(csv_players))

        # First: normalise ALL existing player team names
        all_players = session.execute(select(Player)).scalars().all()
        teams_fixed = 0
        for p in all_players:
            normalised = _normalise_team(p.team)
            if normalised != p.team:
                p.team = normalised
                teams_fixed += 1

        if teams_fixed:
            logger.info("Normalised %d team names", teams_fixed)

        updated = 0
        created = 0
        csv_names_lower = set()

        for name, team, position in csv_players:
            csv_names_lower.add(name.lower())

            # Look for exact name match
            existing = session.execute(
                select(Player).where(Player.name == name)
            ).scalars().all()

            if existing:
                # Update team for all matches (usually just one)
                for p in existing:
                    changed = False
                    if p.team != team:
                        p.team = team
                        changed = True
                    # Only set position from CSV if player has no position
                    if position and not p.position:
                        p.position = position
                        changed = True
                    if not p.is_active:
                        p.is_active = True
                        changed = True
                    if changed:
                        updated += 1
            else:
                # Create new player
                player = Player(
                    name=name,
                    team=team,
                    position=position,
                    is_active=True,
                )
                session.add(player)
                created += 1

        # Mark players not in CSV as inactive (but don't delete them)
        deactivated = 0
        for p in all_players:
            if p.name.lower() not in csv_names_lower and p.is_active:
                p.is_active = False
                deactivated += 1

        session.commit()

        summary = {
            "csv_total": len(csv_players),
            "teams_normalised": teams_fixed,
            "updated": updated,
            "created": created,
            "deactivated": deactivated,
        }
        logger.info(
            "Sync complete: %d from CSV, %d updated, %d created, %d deactivated",
            len(csv_players), updated, created, deactivated,
        )
        return summary

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
