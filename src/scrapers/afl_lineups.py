from __future__ import annotations

"""AFL.com.au team lineup scraper via the official AFL REST API.

Uses the unauthenticated AFL v2 API for match listings, and the
authenticated CFS API (token via WMCTok) for full match rosters.

Data flow:
1. GET competitions -> compseasons -> rounds to find roundId
2. GET matches for the round (unauthenticated v2 API)
3. POST WMCTok to get auth token
4. GET matchRoster/full/{providerId} for each match
5. Parse players into NAMED / EMERGENCY / OMITTED statuses
6. Upsert into lineup_status table
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select

from src.models.database import LineupStatus, Player, get_session, init_db
from src.utils.teams import normalize_team

logger = logging.getLogger(__name__)

AFL_V2_BASE = "https://aflapi.afl.com.au/afl/v2"
AFL_CFS_BASE = "https://api.afl.com.au/cfs/afl"

# Map AFL v2 compSeasonId by year
COMP_SEASON_IDS = {
    2024: 62,
    2025: 73,
    2026: 85,
}

# AFL team abbreviations -> our standard team names
AFL_TEAM_MAP: Dict[str, str] = {
    "ADE": "Adelaide",
    "BRL": "Brisbane",
    "CAR": "Carlton",
    "COL": "Collingwood",
    "ESS": "Essendon",
    "FRE": "Fremantle",
    "GEE": "Geelong",
    "GCS": "Gold Coast",
    "GWS": "GWS",
    "HAW": "Hawthorn",
    "MEL": "Melbourne",
    "NME": "North Melbourne",
    "NM": "North Melbourne",
    "PTA": "Port Adelaide",
    "PA": "Port Adelaide",
    "RIC": "Richmond",
    "STK": "St Kilda",
    "SK": "St Kilda",
    "SYD": "Sydney",
    "WBD": "Western Bulldogs",
    "WB": "Western Bulldogs",
    "WCE": "West Coast",
    "WC": "West Coast",
}


class AflLineupScraper:
    """Scrapes team lineups from the official AFL API."""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._token: Optional[str] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _get_token(self) -> str:
        """Get a fresh auth token from AFL CFS API."""
        client = await self._get_client()
        resp = await client.post(
            f"{AFL_CFS_BASE}/WMCTok",
            json={},
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["token"]
        logger.info("Got AFL API token")
        return self._token

    async def _get_round_id(self, season: int, round_num: int) -> Optional[int]:
        """Get the AFL roundId for a given season and round number."""
        comp_season_id = COMP_SEASON_IDS.get(season)
        if not comp_season_id:
            logger.warning("No compSeasonId mapping for season %d", season)
            return None

        client = await self._get_client()
        resp = await client.get(
            f"{AFL_V2_BASE}/compseasons/{comp_season_id}/rounds",
            params={"pageSize": 50},
        )
        resp.raise_for_status()
        data = resp.json()

        for r in data.get("rounds", []):
            if r.get("roundNumber") == round_num:
                return r.get("id")

        logger.warning("Round %d not found for season %d", round_num, season)
        return None

    async def get_matches_for_round(
        self, season: int, round_num: int
    ) -> List[Dict[str, Any]]:
        """Get all matches for a given round from the unauthenticated v2 API."""
        comp_season_id = COMP_SEASON_IDS.get(season)
        if not comp_season_id:
            return []

        client = await self._get_client()
        resp = await client.get(
            f"{AFL_V2_BASE}/matches",
            params={
                "competitionId": 1,
                "compSeasonId": comp_season_id,
                "roundNumber": round_num,
                "pageSize": 50,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("matches", [])

    async def get_match_roster(
        self, provider_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get full match roster for a specific match."""
        if not self._token:
            await self._get_token()

        client = await self._get_client()
        resp = await client.get(
            f"{AFL_CFS_BASE}/matchRoster/full/{provider_id}",
            headers={"x-media-mis-token": self._token},
        )

        if resp.status_code == 401 or resp.status_code == 403:
            # Token expired, refresh and retry
            await self._get_token()
            resp = await client.get(
                f"{AFL_CFS_BASE}/matchRoster/full/{provider_id}",
                headers={"x-media-mis-token": self._token},
            )

        resp.raise_for_status()
        return resp.json()

    def _parse_roster_team(
        self, team_data: Dict[str, Any], opponent_name: str
    ) -> List[Dict[str, str]]:
        """Parse a team's roster into player lineup entries.

        Returns list of dicts with: name, team, status, match_position
        """
        team_info = team_data.get("teamName", {})
        team_abbr = team_info.get("teamAbbr", "")
        team_name = AFL_TEAM_MAP.get(team_abbr, team_info.get("teamName", ""))

        players = []
        positions = team_data.get("positions", [])

        for p in positions:
            player = p.get("player", {})
            pname = player.get("playerName", {})
            first = pname.get("givenName", "")
            last = pname.get("surname", "")
            pos = p.get("position", "")

            if not first or not last:
                continue

            full_name = f"{first} {last}"

            # EMERG position = emergency, everything else = NAMED
            status = "EMERGENCY" if pos == "EMERG" else "NAMED"

            players.append({
                "name": full_name,
                "team": team_name,
                "status": status,
                "match_position": pos,
                "opponent": opponent_name,
            })

        return players

    async def scrape_round_lineups(
        self, season: int, round_num: int
    ) -> int:
        """Scrape all team lineups for a round and upsert into DB.

        Returns number of player lineup records processed.
        """
        init_db()

        matches = await self.get_matches_for_round(season, round_num)
        if not matches:
            logger.info("No matches found for season %d round %d", season, round_num)
            return 0

        logger.info(
            "Found %d matches for season %d round %d",
            len(matches), season, round_num,
        )

        # Collect all player lineup entries
        all_entries: List[Dict[str, Any]] = []
        named_team_set: set = set()  # teams that have released lineups

        for match in matches:
            provider_id = match.get("providerId", "")
            status = match.get("status", "")

            home_team = match.get("home", {}).get("team", {})
            away_team = match.get("away", {}).get("team", {})
            home_name = home_team.get("name", "")
            away_name = away_team.get("name", "")

            # Only fetch roster if teams have been announced
            if status in ("UNCONFIRMED_TEAMS", "CONCLUDED", "LIVE", "PLAYING"):
                try:
                    roster = await self.get_match_roster(provider_id)
                    if not roster:
                        continue

                    match_roster = roster.get("matchRoster", {})

                    # Parse home team
                    home_data = match_roster.get("homeTeam", {})
                    home_status = home_data.get("teamStatus", "")
                    if home_status in (
                        "PROVISIONAL_TEAM", "CONFIRMED_TEAM", "FINAL_TEAM"
                    ):
                        entries = self._parse_roster_team(home_data, away_name)
                        for e in entries:
                            e["match_id"] = provider_id
                        all_entries.extend(entries)
                        named_team_set.add(home_name)

                    # Parse away team
                    away_data = match_roster.get("awayTeam", {})
                    away_status = away_data.get("teamStatus", "")
                    if away_status in (
                        "PROVISIONAL_TEAM", "CONFIRMED_TEAM", "FINAL_TEAM"
                    ):
                        entries = self._parse_roster_team(away_data, home_name)
                        for e in entries:
                            e["match_id"] = provider_id
                        all_entries.extend(entries)
                        named_team_set.add(away_name)

                except Exception as e:
                    logger.error(
                        "Failed to get roster for %s (%s vs %s): %s",
                        provider_id, home_name, away_name, e,
                    )
                    continue

        if not all_entries:
            logger.info("No lineup data available yet for round %d", round_num)
            return 0

        # Upsert into database
        session = get_session()
        count = 0
        try:
            # Build player name lookup
            all_players = session.execute(select(Player)).scalars().all()
            by_name: Dict[str, Player] = {}
            for p in all_players:
                by_name[p.name.lower()] = p

            for entry in all_entries:
                player = by_name.get(entry["name"].lower())
                if not player:
                    # Try partial match (first+last)
                    parts = entry["name"].split()
                    if len(parts) >= 2:
                        for dbp in all_players:
                            db_parts = dbp.name.lower().split()
                            if (
                                len(db_parts) >= 2
                                and db_parts[0] == parts[0].lower()
                                and db_parts[-1] == parts[-1].lower()
                            ):
                                player = dbp
                                break

                if not player:
                    logger.debug(
                        "Player not found in DB: %s (%s)",
                        entry["name"], entry["team"],
                    )
                    continue

                # Upsert lineup status
                existing = session.execute(
                    select(LineupStatus).where(
                        LineupStatus.player_id == player.id,
                        LineupStatus.season == season,
                        LineupStatus.round == round_num,
                    )
                ).scalar_one_or_none()

                if existing:
                    existing.status = entry["status"]
                    existing.match_position = entry["match_position"]
                    existing.match_id = entry.get("match_id")
                    existing.opponent = entry.get("opponent")
                else:
                    ls = LineupStatus(
                        player_id=player.id,
                        season=season,
                        round=round_num,
                        status=entry["status"],
                        match_position=entry["match_position"],
                        match_id=entry.get("match_id"),
                        opponent=entry.get("opponent"),
                    )
                    session.add(ls)

                count += 1

            session.commit()
            logger.info(
                "Scraped %d lineup entries for round %d (%d teams announced)",
                count, round_num, len(named_team_set),
            )

        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        return count
