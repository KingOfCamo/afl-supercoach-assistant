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


# ──────────────────────────────────────────────────────────────
# FootyWire Fallback — server-rendered HTML, no JS needed
# ──────────────────────────────────────────────────────────────

FOOTYWIRE_TEAM_MAP: Dict[str, str] = {
    "Adelaide": "Adelaide",
    "Brisbane Lions": "Brisbane",
    "Carlton": "Carlton",
    "Collingwood": "Collingwood",
    "Essendon": "Essendon",
    "Fremantle": "Fremantle",
    "Geelong": "Geelong",
    "Gold Coast": "Gold Coast",
    "GWS": "GWS",
    "Hawthorn": "Hawthorn",
    "Melbourne": "Melbourne",
    "North Melbourne": "North Melbourne",
    "Port Adelaide": "Port Adelaide",
    "Richmond": "Richmond",
    "St Kilda": "St Kilda",
    "Sydney": "Sydney",
    "West Coast": "West Coast",
    "Western Bulldogs": "Western Bulldogs",
}

POSITION_LABELS = {"FB", "HB", "C", "HF", "FF", "Fol"}


async def scrape_footywire_selections(season: int, round_num: int) -> int:
    """Scrape team selections from FootyWire and upsert into lineup_status.

    FootyWire page structure (per match):
      - td.tbtitle  "Home v Away (Venue)"
      - Next tr contains 4 inner tables:
          [0] Home interchange + emergencies + ins/outs
          [1] Position grid (12 rows: 6 home + 6 away, 4 cells each)
          [2] Stats (ignored)
          [3] Away interchange + emergencies + ins/outs

    Returns number of records written.
    """
    from bs4 import BeautifulSoup
    import re

    init_db()

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(
            "https://www.footywire.com/afl/footy/afl_team_selections",
            headers={"User-Agent": "Mozilla/5.0 (compatible; SuperCoachAI/1.0)"},
        )
    print(f"[LINEUP-FW] status={resp.status_code} len={len(resp.text)}", flush=True)
    if resp.status_code != 200:
        logger.error("FootyWire returned %d", resp.status_code)
        return 0

    soup = BeautifulSoup(resp.text, "html.parser")
    all_entries: List[Dict[str, Any]] = []

    match_titles = soup.find_all("td", class_="tbtitle")
    print(f"[LINEUP-FW] found {len(match_titles)} match titles", flush=True)
    for mt in match_titles:
        title = mt.get_text(strip=True)
        if " v " not in title:
            continue

        parts = title.split("(")[0].strip().split(" v ")
        home_team = parts[0].strip()
        away_team = parts[1].strip() if len(parts) > 1 else ""
        home_norm = normalize_team(home_team)
        away_norm = normalize_team(away_team)

        # Navigate to the content row
        title_row = mt.find_parent("tr")
        if not title_row:
            continue
        content_row = title_row.find_next_sibling("tr")
        if not content_row:
            continue

        inner_tables = content_row.find_all("table")
        if len(inner_tables) < 4:
            logger.warning("Expected 4 inner tables for %s, got %d", title, len(inner_tables))
            continue

        # ── Table 0: Home interchange / emergencies / ins / outs ──
        home_bench = _parse_bench_table(inner_tables[0])
        # ── Table 3: Away interchange / emergencies / ins / outs ──
        away_bench = _parse_bench_table(inner_tables[3])

        # ── Table 1: Position grid ──
        # Rows alternate: even rows = home team, odd rows = away team
        pos_table = inner_tables[1]
        pos_rows = pos_table.find_all("tr")

        for i, row in enumerate(pos_rows):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            pos_label = cells[0].get_text(strip=True)
            is_home = (i % 2 == 0)
            team = home_norm if is_home else away_norm
            opponent = away_norm if is_home else home_norm
            for cell in cells[1:]:
                name = _extract_player_name(cell)
                if name:
                    all_entries.append({
                        "name": name,
                        "team": team,
                        "status": "NAMED",
                        "match_position": pos_label,
                        "opponent": opponent,
                    })

        # Add interchange players (NAMED, position=INT)
        for name in home_bench.get("interchange", []):
            if name:
                all_entries.append({
                    "name": name, "team": home_norm, "status": "NAMED",
                    "match_position": "INT", "opponent": away_norm,
                })
        for name in away_bench.get("interchange", []):
            if name:
                all_entries.append({
                    "name": name, "team": away_norm, "status": "NAMED",
                    "match_position": "INT", "opponent": home_norm,
                })

        # Add emergencies
        for name in home_bench.get("emergencies", []):
            if name:
                all_entries.append({
                    "name": name, "team": home_norm, "status": "EMERGENCY",
                    "match_position": "EMERG", "opponent": away_norm,
                })
        for name in away_bench.get("emergencies", []):
            if name:
                all_entries.append({
                    "name": name, "team": away_norm, "status": "EMERGENCY",
                    "match_position": "EMERG", "opponent": home_norm,
                })

    if not all_entries:
        logger.info("FootyWire: no lineup entries found")
        return 0

    # ── Upsert into DB (same pattern as AFL API scraper) ──
    session = get_session()
    count = 0
    try:
        all_players = session.execute(select(Player)).scalars().all()
        # Build lookup: lowercase surname -> list of players
        by_surname: Dict[str, List[Player]] = {}
        for p in all_players:
            parts = p.name.lower().split()
            if parts:
                surname = parts[-1]
                by_surname.setdefault(surname, []).append(p)

        for entry in all_entries:
            player = _match_fw_name(entry["name"], entry["team"], by_surname, all_players)
            if not player:
                logger.debug("No match for %s (%s)", entry["name"], entry["team"])
                continue

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
                existing.opponent = entry.get("opponent")
            else:
                session.add(LineupStatus(
                    player_id=player.id,
                    season=season,
                    round=round_num,
                    status=entry["status"],
                    match_position=entry["match_position"],
                    opponent=entry.get("opponent"),
                ))
            count += 1

        session.commit()
        logger.info("FootyWire: %d lineup entries for round %d", count, round_num)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return count


def _extract_player_name(cell) -> Optional[str]:
    """Extract full player name from a FootyWire table cell.

    FootyWire abbreviates hyphenated names in display text
    (e.g. 'N W-Milera' for 'Nasiah Wanganeen-Milera') but the
    <a href> contains the full name as a slug.

    Strategy: prefer href-based extraction, fall back to cell text.
    """
    import re
    link = cell.find("a", href=True)
    if link:
        href = link.get("href", "")
        # href format: "pp-st-kilda-saints--nasiah-wanganeen-milera"
        # Extract the player slug after the "--"
        if "--" in href:
            slug = href.split("--")[-1]  # "nasiah-wanganeen-milera"
            # Convert slug to name: "nasiah-wanganeen-milera" -> "Nasiah Wanganeen-Milera"
            # Split on single hyphens that separate words, but keep
            # compound surnames (e.g. wanganeen-milera)
            name_parts = slug.split("-")
            # Reconstruct: title-case each part, rejoin with spaces
            # But we need to detect compound surnames — use the display
            # text as a guide for hyphenation
            display = link.get_text(strip=True)
            if display:
                # Use display text hyphen positions as guide
                # "N W-Milera" -> we know there's a hyphen between W and Milera
                # Count hyphens in display text to figure out word grouping
                full_name = " ".join(p.title() for p in name_parts)
                # Re-introduce hyphens for compound surnames
                # Simple heuristic: if display text has a hyphen between
                # non-initial parts, the full name likely has one too
                if "-" in display:
                    # Find which parts should be hyphenated
                    display_parts = display.replace("-", " ").split()
                    if len(display_parts) >= 2:
                        # The surname in display might be "W-Milera"
                        # Map back to full name parts
                        pass  # The title-cased version is close enough
                return full_name

    # Fallback: use cell text
    text = cell.get_text(strip=True)
    return text if text else None


def _parse_bench_table(table) -> Dict[str, List[str]]:
    """Parse a FootyWire interchange/emergencies/ins/outs table.

    Returns {"interchange": [...], "emergencies": [...], "ins": [...], "outs": [...]}.
    Uses href-based name extraction for accuracy on hyphenated names.
    """
    result: Dict[str, List[str]] = {
        "interchange": [], "emergencies": [], "ins": [], "outs": [],
    }
    cells = table.find_all("td")
    section = "interchange"
    section_map = {
        "Interchange": "interchange",
        "Emergencies": "emergencies",
        "Ins": "ins",
        "Outs": "outs",
    }
    for cell in cells:
        text = cell.get_text(strip=True)
        if text in section_map:
            section = section_map[text]
            continue
        name = _extract_player_name(cell)
        if name and section in result:
            result[section].append(name)
    return result


def _match_fw_name(
    fw_name: str,
    team: str,
    by_surname: Dict[str, List["Player"]],
    all_players: List["Player"],
) -> Optional["Player"]:
    """Match a FootyWire name like 'J Sicily' to a Player record.

    FootyWire uses initial + surname (e.g. 'J Newcombe').
    DB uses full name (e.g. 'Jai Newcombe').
    """
    parts = fw_name.strip().split()
    if len(parts) < 2:
        return None

    initial = parts[0].rstrip(".").upper()
    surname = parts[-1].lower()
    norm_team = normalize_team(team)

    # Find all players with this surname
    candidates = by_surname.get(surname, [])

    # Filter by team first
    team_matches = [p for p in candidates if normalize_team(p.team) == norm_team]
    if len(team_matches) == 1:
        return team_matches[0]

    # Multiple on same team — match by initial
    for p in team_matches:
        if p.name.upper().startswith(initial):
            return p

    # Try full first name match (href gives full name like "Timothy English")
    if len(parts) >= 2 and len(parts[0]) > 1:
        first_name_lower = parts[0].lower()
        for p in team_matches:
            p_first = p.name.split()[0].lower() if p.name.split() else ""
            # Match full first name or common shortenings
            if p_first == first_name_lower or p_first.startswith(first_name_lower) or first_name_lower.startswith(p_first):
                return p

    # Try all players (cross-team) — last resort
    for p in candidates:
        if p.name.upper().startswith(initial):
            return p

    # Handle special cases like "M D'Ambrosio"
    if "'" in fw_name:
        full_surname = " ".join(parts[1:]).lower()
        for p in all_players:
            if p.name.lower().endswith(full_surname) and normalize_team(p.team) == norm_team:
                return p

    # Handle multi-word surnames from href extraction (e.g. "Nasiah Wanganeen Milera")
    # DB may store as "Nasiah Wanganeen-Milera" — try hyphenated combinations
    if len(parts) >= 3:
        first_name = parts[0]
        # Try joining last 2 parts with hyphen: "Wanganeen-Milera"
        hyph_surname = "-".join(parts[-2:]).lower()
        for p in all_players:
            if normalize_team(p.team) == norm_team and p.name.lower().endswith(hyph_surname):
                return p
        # Try joining last 3 parts
        if len(parts) >= 4:
            hyph_surname = "-".join(parts[-3:]).lower()
            for p in all_players:
                if normalize_team(p.team) == norm_team and p.name.lower().endswith(hyph_surname):
                    return p
        # Try full name as substring match
        full_lower = fw_name.lower()
        for p in all_players:
            p_lower = p.name.lower().replace("-", " ")
            if p_lower == full_lower and normalize_team(p.team) == norm_team:
                return p

    return None


# ── FanFooty Teamsheets (backup source) ──


async def scrape_fanfooty_teamsheets(season: int, round_num: int) -> int:
    """Scrape team selections from FanFooty teamsheets page.

    FanFooty serves server-rendered HTML with team names in <strong>/<b> tags
    and player names in <a> tags. Position lines: FB:, HB:, C:, HF:, FF:, Fol:, Int:, Emg:

    Returns number of records written.
    """
    from bs4 import BeautifulSoup
    import re

    init_db()

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(
            "https://www.fanfooty.com.au/game/teamsheets.php",
            headers={"User-Agent": "Mozilla/5.0 (compatible; SuperCoachAI/1.0)"},
        )
    print(f"[LINEUP-FF] status={resp.status_code} len={len(resp.text)}", flush=True)
    if resp.status_code != 200:
        return 0

    soup = BeautifulSoup(resp.text, "html.parser")
    all_entries: List[Dict[str, Any]] = []

    _team_map = {
        "hawthorn": "Hawthorn", "sydney": "Sydney", "adelaide": "Adelaide",
        "western bulldogs": "Western Bulldogs", "brisbane": "Brisbane Lions",
        "brisbane lions": "Brisbane Lions", "carlton": "Carlton",
        "collingwood": "Collingwood", "essendon": "Essendon",
        "fremantle": "Fremantle", "geelong": "Geelong",
        "geelong cats": "Geelong", "gold coast": "Gold Coast",
        "gold coast suns": "Gold Coast", "gws": "GWS",
        "gws giants": "GWS", "g. w. sydney": "GWS",
        "melbourne": "Melbourne", "north melbourne": "North Melbourne",
        "port adelaide": "Port Adelaide", "richmond": "Richmond",
        "st kilda": "St Kilda", "west coast": "West Coast",
        "west coast eagles": "West Coast", "w. bulldogs": "Western Bulldogs",
    }

    html_str = str(soup)
    team_pattern = re.compile(r'<(?:strong|b)>(.*?)</(?:strong|b)>', re.IGNORECASE)
    team_matches = list(team_pattern.finditer(html_str))

    found_teams = []
    for m in team_matches:
        raw = m.group(1).strip()
        norm = _team_map.get(raw.lower())
        if not norm:
            try:
                norm = normalize_team(raw)
            except Exception:
                continue
        if norm:
            found_teams.append((m, norm))

    print(f"[LINEUP-FF] found {len(found_teams)} team headers", flush=True)

    pos_prefixes = ["FB:", "HB:", "C:", "HF:", "FF:", "Fol:", "Int:", "Emg:"]

    for idx, (m, team_norm) in enumerate(found_teams):
        start = m.end()
        end = found_teams[idx + 1][0].start() if idx + 1 < len(found_teams) else len(html_str)
        block_soup = BeautifulSoup(html_str[start:end], "html.parser")
        block_text = block_soup.get_text(separator="\n")
        lines = [l.strip() for l in block_text.split("\n") if l.strip()]

        for line in lines:
            for prefix in pos_prefixes:
                if line.startswith(prefix):
                    status = "EMERGENCY" if prefix == "Emg:" else "NAMED"
                    player_text = line[len(prefix):].strip()
                    for pname in player_text.split(","):
                        clean = re.sub(r'<[^>]+>', '', pname).strip().rstrip(",").strip()
                        if clean and len(clean) > 2:
                            all_entries.append({
                                "name": clean,
                                "team": team_norm,
                                "status": status,
                                "match_position": prefix.rstrip(":"),
                                "opponent": "",
                            })
                    break

    print(f"[LINEUP-FF] parsed {len(all_entries)} entries", flush=True)
    if not all_entries:
        return 0

    # Upsert into DB
    session = get_session()
    count = 0
    try:
        all_players = session.execute(select(Player)).scalars().all()
        by_surname: Dict[str, List[Player]] = {}
        for p in all_players:
            name_parts = p.name.lower().split()
            if name_parts:
                by_surname.setdefault(name_parts[-1], []).append(p)

        for entry in all_entries:
            player = _match_fw_name(entry["name"], entry["team"], by_surname, all_players)
            if not player:
                # FanFooty uses full names — try exact match
                for p in all_players:
                    if p.name.lower() == entry["name"].lower():
                        player = p
                        break
            if not player:
                logger.debug("FanFooty no match: %s (%s)", entry["name"], entry["team"])
                continue

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
                existing.opponent = entry.get("opponent", "")
            else:
                session.add(LineupStatus(
                    player_id=player.id,
                    season=season,
                    round=round_num,
                    status=entry["status"],
                    match_position=entry["match_position"],
                    opponent=entry.get("opponent", ""),
                ))
            count += 1

        session.commit()
        print(f"[LINEUP-FF] stored {count} entries for round {round_num}", flush=True)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return count
