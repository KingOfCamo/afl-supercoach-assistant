from __future__ import annotations

import pytest

from src.utils.teams import CANONICAL_TEAMS, TEAM_ALIASES, normalize_team


class TestCanonicalTeams:
    """Test the canonical team set."""

    def test_has_18_teams(self):
        assert len(CANONICAL_TEAMS) == 18

    def test_known_teams_present(self):
        for team in [
            "Adelaide", "Brisbane", "Carlton", "Collingwood", "Essendon",
            "Fremantle", "Geelong", "Gold Coast", "GWS", "Hawthorn",
            "Melbourne", "North Melbourne", "Port Adelaide", "Richmond",
            "St Kilda", "Sydney", "West Coast", "Western Bulldogs",
        ]:
            assert team in CANONICAL_TEAMS


class TestNormalizeTeam:
    """Test the normalize_team function."""

    def test_canonical_passthrough(self):
        """Canonical names should pass through unchanged."""
        assert normalize_team("Adelaide") == "Adelaide"
        assert normalize_team("Melbourne") == "Melbourne"
        assert normalize_team("Western Bulldogs") == "Western Bulldogs"

    def test_full_name(self):
        """Full names like 'Adelaide Crows' should map to canonical."""
        assert normalize_team("Adelaide Crows") == "Adelaide"
        assert normalize_team("Melbourne Demons") == "Melbourne"
        assert normalize_team("GWS Giants") == "GWS"

    def test_nickname(self):
        """Nicknames like 'Crows' should map to canonical."""
        assert normalize_team("Crows") == "Adelaide"
        assert normalize_team("Demons") == "Melbourne"
        assert normalize_team("Bulldogs") == "Western Bulldogs"

    def test_three_letter_code(self):
        """DFS Australia 3-letter codes should resolve."""
        assert normalize_team("ADE") == "Adelaide"
        assert normalize_team("MEL") == "Melbourne"
        assert normalize_team("WBD") == "Western Bulldogs"
        assert normalize_team("GCS") == "Gold Coast"

    def test_two_letter_code(self):
        """Two-letter codes should resolve."""
        assert normalize_team("AD") == "Adelaide"
        assert normalize_team("GW") == "GWS"
        assert normalize_team("WB") == "Western Bulldogs"

    def test_squiggle_names(self):
        """Names as returned by Squiggle API."""
        assert normalize_team("Greater Western Sydney") == "GWS"
        assert normalize_team("Brisbane Lions") == "Brisbane"
        assert normalize_team("Sydney Swans") == "Sydney"

    def test_empty_passthrough(self):
        """Empty/None should pass through."""
        assert normalize_team("") == ""

    def test_unknown_passthrough(self):
        """Unknown names should pass through unchanged."""
        assert normalize_team("Unknown Team") == "Unknown Team"

    def test_whitespace_stripped(self):
        """Leading/trailing whitespace should be stripped."""
        assert normalize_team("  Melbourne  ") == "Melbourne"
        assert normalize_team(" Crows ") == "Adelaide"

    def test_footscray(self):
        """Historical name 'Footscray' should map to Western Bulldogs."""
        assert normalize_team("Footscray") == "Western Bulldogs"

    def test_all_aliases_map_to_canonical(self):
        """Every alias should map to a canonical team."""
        for alias, canonical in TEAM_ALIASES.items():
            assert canonical in CANONICAL_TEAMS, f"Alias '{alias}' maps to non-canonical '{canonical}'"
