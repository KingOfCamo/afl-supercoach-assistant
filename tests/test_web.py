from __future__ import annotations

"""Tests for the web dashboard API endpoints."""

import pytest
from fastapi.testclient import TestClient

from src.models.database import init_db, get_session, Player, DfsPlayerStats, MyTeamSlot, reset_engine
from src.utils.config import reset_config
from src.web.app import create_app


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    """Create a fresh test database for each test."""
    reset_config()
    reset_engine()

    db_path = str(tmp_path / "test_web.db")

    from src.utils import config as config_module

    original_load = config_module.load_config

    def patched_load():
        cfg = original_load()
        cfg.database.path = db_path
        return cfg

    monkeypatch.setattr(config_module, "load_config", patched_load)
    monkeypatch.setattr(config_module, "_config", None)

    init_db()
    yield
    reset_engine()
    reset_config()


@pytest.fixture
def client():
    """Create a FastAPI test client."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def seeded_db():
    """Seed the database with test players."""
    session = get_session()
    try:
        p1 = Player(name="Caleb Serong", team="Fremantle", position="MID", is_active=True)
        p2 = Player(name="Marcus Bontempelli", team="W.Bulldogs", position="MID", is_active=True)
        p3 = Player(name="Nick Daicos", team="Collingwood", position="MID/DEF", is_active=True)
        session.add_all([p1, p2, p3])
        session.flush()

        # Add DFS stats
        dfs1 = DfsPlayerStats(player_id=p1.id, season=2026, salary=582000, sc_avg=107.2, ownership_pct=0.34)
        dfs2 = DfsPlayerStats(player_id=p2.id, season=2026, salary=650000, sc_avg=115.0, ownership_pct=0.45)
        dfs3 = DfsPlayerStats(player_id=p3.id, season=2026, salary=620000, sc_avg=112.5, ownership_pct=0.38)
        session.add_all([dfs1, dfs2, dfs3])

        session.commit()
        return {"p1": p1.id, "p2": p2.id, "p3": p3.id}
    finally:
        session.close()


class TestConfigEndpoint:
    """Test the /api/config endpoint."""

    def test_config_returns_json(self, client):
        response = client.get("/api/config")
        assert response.status_code == 200
        data = response.json()
        assert "season" in data
        assert "current_round" in data
        assert "trades_remaining" in data


class TestPlayerEndpoints:
    """Test the /api/players/* endpoints."""

    def test_search_empty_query(self, client):
        response = client.get("/api/players/search?q=")
        assert response.status_code == 200
        assert response.json()["players"] == []

    def test_search_short_query(self, client):
        response = client.get("/api/players/search?q=a")
        assert response.status_code == 200
        assert response.json()["players"] == []

    def test_search_finds_player(self, client, seeded_db):
        response = client.get("/api/players/search?q=Serong")
        assert response.status_code == 200
        players = response.json()["players"]
        assert len(players) >= 1
        assert players[0]["name"] == "Caleb Serong"
        assert players[0]["salary"] == 582000

    def test_search_no_results(self, client, seeded_db):
        response = client.get("/api/players/search?q=ZZZNOTAPLAYER")
        assert response.status_code == 200
        assert response.json()["players"] == []

    def test_get_player_detail(self, client, seeded_db):
        pid = seeded_db["p1"]
        response = client.get(f"/api/players/{pid}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Caleb Serong"
        assert data["team"] == "Fremantle"
        assert data["dfs"]["salary"] == 582000

    def test_get_player_not_found(self, client):
        response = client.get("/api/players/99999")
        assert response.status_code == 200
        assert "error" in response.json()


class TestTeamEndpoints:
    """Test the /api/team/* endpoints."""

    def test_get_empty_team(self, client):
        response = client.get("/api/team")
        assert response.status_code == 200
        data = response.json()
        assert data["player_count"] == 0
        assert data["slots"] == []

    def test_add_player_to_team(self, client, seeded_db):
        # Add player
        response = client.post("/api/team/slot", json={
            "player_id": seeded_db["p1"],
            "position_slot": "MID1",
        })
        assert response.status_code == 200
        assert response.json()["success"] is True

        # Verify team
        team = client.get("/api/team").json()
        assert team["player_count"] == 1
        assert team["slots"][0]["player_name"] == "Caleb Serong"

    def test_add_player_invalid_slot(self, client, seeded_db):
        response = client.post("/api/team/slot", json={
            "player_id": seeded_db["p1"],
            "position_slot": "INVALID",
        })
        assert response.status_code == 400

    def test_add_player_duplicate_slot(self, client, seeded_db):
        client.post("/api/team/slot", json={
            "player_id": seeded_db["p1"],
            "position_slot": "MID1",
        })
        response = client.post("/api/team/slot", json={
            "player_id": seeded_db["p2"],
            "position_slot": "MID1",
        })
        assert response.status_code == 409

    def test_add_player_already_on_team(self, client, seeded_db):
        client.post("/api/team/slot", json={
            "player_id": seeded_db["p1"],
            "position_slot": "MID1",
        })
        response = client.post("/api/team/slot", json={
            "player_id": seeded_db["p1"],
            "position_slot": "MID2",
        })
        assert response.status_code == 409

    def test_remove_player(self, client, seeded_db):
        add_res = client.post("/api/team/slot", json={
            "player_id": seeded_db["p1"],
            "position_slot": "MID1",
        })
        slot_id = add_res.json()["slot_id"]

        response = client.delete(f"/api/team/slot/{slot_id}")
        assert response.status_code == 200
        assert client.get("/api/team").json()["player_count"] == 0

    def test_set_captain(self, client, seeded_db):
        client.post("/api/team/slot", json={
            "player_id": seeded_db["p1"],
            "position_slot": "MID1",
        })
        client.post("/api/team/slot", json={
            "player_id": seeded_db["p2"],
            "position_slot": "MID2",
        })

        response = client.put("/api/team/captain", json={
            "captain_id": seeded_db["p1"],
            "vice_captain_id": seeded_db["p2"],
        })
        assert response.status_code == 200

        team = client.get("/api/team").json()
        captain = [s for s in team["slots"] if s["is_captain"]]
        vc = [s for s in team["slots"] if s["is_vice_captain"]]
        assert len(captain) == 1
        assert captain[0]["player_name"] == "Caleb Serong"
        assert len(vc) == 1
        assert vc[0]["player_name"] == "Marcus Bontempelli"

    def test_clear_team(self, client, seeded_db):
        client.post("/api/team/slot", json={
            "player_id": seeded_db["p1"],
            "position_slot": "MID1",
        })
        response = client.post("/api/team/clear")
        assert response.status_code == 200
        assert client.get("/api/team").json()["player_count"] == 0


class TestAnalyticsEndpoints:
    """Test the /api/analytics/* endpoints."""

    def test_projections_empty(self, client):
        response = client.get("/api/analytics/projections?round=1&team_only=true")
        assert response.status_code == 200
        data = response.json()
        assert data["projections"] == []

    def test_captain_empty(self, client):
        response = client.get("/api/analytics/captain?round=1")
        assert response.status_code == 200
        assert response.json()["candidates"] == []

    def test_trades_empty(self, client):
        response = client.get("/api/analytics/trades?round=1")
        assert response.status_code == 200
        assert response.json()["recommendations"] == []

    def test_injuries_empty(self, client):
        response = client.get("/api/analytics/injuries")
        assert response.status_code == 200
        data = response.json()
        assert data["team_injuries"] == []
        assert data["all_injuries_count"] == 0


class TestStaticFiles:
    """Test that static files are served correctly."""

    def test_index_html(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "SuperCoach" in response.text

    def test_css_file(self, client):
        response = client.get("/static/style.css")
        assert response.status_code == 200

    def test_js_file(self, client):
        response = client.get("/static/app.js")
        assert response.status_code == 200


class TestWebImports:
    """Test that all web modules import correctly."""

    def test_import_app(self):
        from src.web.app import create_app, run_server
        assert create_app is not None
        assert run_server is not None

    def test_import_players_router(self):
        from src.web.routes.players import router
        assert router is not None

    def test_import_team_router(self):
        from src.web.routes.team import router
        assert router is not None

    def test_import_analytics_router(self):
        from src.web.routes.analytics import router
        assert router is not None

    def test_import_ai_router(self):
        from src.web.routes.ai import router
        assert router is not None
