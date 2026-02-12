from __future__ import annotations

from src.utils.config import find_project_root, load_config, reset_config


def test_find_project_root():
    """Project root should contain pyproject.toml."""
    root = find_project_root()
    assert (root / "pyproject.toml").exists()


def test_load_config():
    """Config should load with expected defaults."""
    reset_config()
    config = load_config()
    assert config.season == 2025
    assert config.current_round == 1
    assert config.scraping.rate_limit_seconds == 2.0
    assert config.scraping.max_retries == 3
    assert config.ai.model == "claude-sonnet-4-20250514"
    assert "supercoach.db" in config.database.path
    assert config.database.url.startswith("sqlite:///")
    reset_config()


def test_config_display_defaults():
    """Display config should have expected defaults."""
    reset_config()
    config = load_config()
    assert config.display.max_recent_scores == 5
    assert config.display.table_style == "rounded"
    reset_config()
