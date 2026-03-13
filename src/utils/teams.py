from __future__ import annotations

"""Canonical AFL team names and normalisation.

All data sources (FootyWire, DFS Australia, Squiggle, SuperCoach) use different
team name formats. This module provides a single normalize_team() function that
maps any known variant to our canonical team name.
"""

CANONICAL_TEAMS = {
    "Adelaide",
    "Brisbane",
    "Carlton",
    "Collingwood",
    "Essendon",
    "Fremantle",
    "Geelong",
    "Gold Coast",
    "GWS",
    "Hawthorn",
    "Melbourne",
    "North Melbourne",
    "Port Adelaide",
    "Richmond",
    "St Kilda",
    "Sydney",
    "West Coast",
    "Western Bulldogs",
}

# Maps all known variants to canonical name.
# Sources: FootyWire (full names + nicknames), DFS Australia (3-letter codes),
# Squiggle API (mixed), SuperCoach (mixed).
TEAM_ALIASES = {
    # Adelaide
    "Adelaide Crows": "Adelaide",
    "Crows": "Adelaide",
    "ADE": "Adelaide",
    "AD": "Adelaide",
    # Brisbane
    "Brisbane Lions": "Brisbane",
    "Lions": "Brisbane",
    "BRL": "Brisbane",
    "BL": "Brisbane",
    # Carlton
    "Carlton Blues": "Carlton",
    "Blues": "Carlton",
    "CAR": "Carlton",
    "CA": "Carlton",
    # Collingwood
    "Collingwood Magpies": "Collingwood",
    "Magpies": "Collingwood",
    "COL": "Collingwood",
    "CW": "Collingwood",
    # Essendon
    "Essendon Bombers": "Essendon",
    "Bombers": "Essendon",
    "ESS": "Essendon",
    "ES": "Essendon",
    # Fremantle
    "Fremantle Dockers": "Fremantle",
    "Dockers": "Fremantle",
    "FRE": "Fremantle",
    "FR": "Fremantle",
    # Geelong
    "Geelong Cats": "Geelong",
    "Cats": "Geelong",
    "GEE": "Geelong",
    "GE": "Geelong",
    # Gold Coast
    "Gold Coast Suns": "Gold Coast",
    "Gold Coast SUNS": "Gold Coast",
    "Suns": "Gold Coast",
    "SUNS": "Gold Coast",
    "GCS": "Gold Coast",
    "GC": "Gold Coast",
    # GWS
    "GWS Giants": "GWS",
    "GWS GIANTS": "GWS",
    "Greater Western Sydney": "GWS",
    "Greater Western Sydney Giants": "GWS",
    "Giants": "GWS",
    "GIANTS": "GWS",
    "GW": "GWS",
    # Hawthorn
    "Hawthorn Hawks": "Hawthorn",
    "Hawks": "Hawthorn",
    "HAW": "Hawthorn",
    "HW": "Hawthorn",
    # Melbourne
    "Melbourne Demons": "Melbourne",
    "Demons": "Melbourne",
    "MEL": "Melbourne",
    "ME": "Melbourne",
    # North Melbourne
    "North Melbourne Kangaroos": "North Melbourne",
    "Kangaroos": "North Melbourne",
    "NTH": "North Melbourne",
    "NM": "North Melbourne",
    # Port Adelaide
    "Port Adelaide Power": "Port Adelaide",
    "Power": "Port Adelaide",
    "PTA": "Port Adelaide",
    "PA": "Port Adelaide",
    # Richmond
    "Richmond Tigers": "Richmond",
    "Tigers": "Richmond",
    "RIC": "Richmond",
    "RI": "Richmond",
    # St Kilda
    "St Kilda Saints": "St Kilda",
    "Saints": "St Kilda",
    "STK": "St Kilda",
    "SK": "St Kilda",
    # Sydney
    "Sydney Swans": "Sydney",
    "Swans": "Sydney",
    "SYD": "Sydney",
    "SY": "Sydney",
    # West Coast
    "West Coast Eagles": "West Coast",
    "Eagles": "West Coast",
    "WCE": "West Coast",
    "WC": "West Coast",
    # Western Bulldogs
    "Western Bulldogs Bulldogs": "Western Bulldogs",
    "Bulldogs": "Western Bulldogs",
    "Footscray": "Western Bulldogs",
    "WBD": "Western Bulldogs",
    "WB": "Western Bulldogs",
}


def normalize_team(name: str) -> str:
    """Normalize any team name variant to our canonical form.

    Examples:
        normalize_team("Adelaide Crows") -> "Adelaide"
        normalize_team("MEL") -> "Melbourne"
        normalize_team("Western Bulldogs") -> "Western Bulldogs"
        normalize_team("Unknown") -> "Unknown"  # passthrough
    """
    if not name:
        return name
    cleaned = name.strip()
    if cleaned in CANONICAL_TEAMS:
        return cleaned
    return TEAM_ALIASES.get(cleaned, cleaned)
