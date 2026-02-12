from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class ScrapingConfig:
    rate_limit_seconds: float = 2.0
    max_retries: int = 3
    user_agent: str = "AFL-SuperCoach-Assistant/0.1"
    footywire_base_url: str = "https://www.footywire.com/afl/footy"
    footywire_supercoach_url: str = "https://www.footywire.com/afl/footy/supercoach_round"
    footywire_injury_url: str = "https://www.footywire.com/afl/footy/injury_list"


@dataclass
class AIConfig:
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    api_key: str = ""


@dataclass
class DatabaseConfig:
    path: str = "data/supercoach.db"

    @property
    def url(self) -> str:
        return f"sqlite:///{self.path}"


@dataclass
class DisplayConfig:
    table_style: str = "rounded"
    max_recent_scores: int = 5


@dataclass
class Config:
    season: int = 2025
    current_round: int = 1
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    scraping: ScrapingConfig = field(default_factory=ScrapingConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    project_root: Path = field(default_factory=Path)


_config: Config | None = None


def find_project_root() -> Path:
    """Walk up from this file to find the directory containing pyproject.toml."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    raise FileNotFoundError("Could not find project root (no pyproject.toml found)")


def load_config() -> Config:
    """Load config from config.toml + .env. Cached after first call."""
    global _config
    if _config is not None:
        return _config

    root = find_project_root()

    # Load .env (override=True so .env values take precedence)
    load_dotenv(root / ".env", override=True)

    # Load config.toml
    config_path = root / "config.toml"
    toml_data: dict = {}
    if config_path.exists():
        with open(config_path, "rb") as f:
            toml_data = tomllib.load(f)

    general = toml_data.get("general", {})
    db_section = toml_data.get("database", {})
    scraping_section = toml_data.get("scraping", {})
    fw_section = scraping_section.pop("footywire", {})
    ai_section = toml_data.get("ai", {})
    display_section = toml_data.get("display", {})

    db_config = DatabaseConfig(
        path=str(root / db_section.get("path", "data/supercoach.db")),
    )

    scraping_config = ScrapingConfig(
        rate_limit_seconds=scraping_section.get("rate_limit_seconds", 2.0),
        max_retries=scraping_section.get("max_retries", 3),
        user_agent=scraping_section.get("user_agent", "AFL-SuperCoach-Assistant/0.1"),
        footywire_base_url=fw_section.get("base_url", ScrapingConfig.footywire_base_url),
        footywire_supercoach_url=fw_section.get(
            "supercoach_url", ScrapingConfig.footywire_supercoach_url
        ),
        footywire_injury_url=fw_section.get("injury_url", ScrapingConfig.footywire_injury_url),
    )

    ai_config = AIConfig(
        model=ai_section.get("model", "claude-sonnet-4-20250514"),
        max_tokens=ai_section.get("max_tokens", 4096),
        api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
    )

    display_config = DisplayConfig(
        table_style=display_section.get("table_style", "rounded"),
        max_recent_scores=display_section.get("max_recent_scores", 5),
    )

    _config = Config(
        season=general.get("season", 2025),
        current_round=general.get("current_round", 1),
        database=db_config,
        scraping=scraping_config,
        ai=ai_config,
        display=display_config,
        project_root=root,
    )
    return _config


def get_config() -> Config:
    """Get the current config, loading if necessary."""
    return load_config()


def reset_config() -> None:
    """Reset cached config. Useful for testing."""
    global _config
    _config = None
