# AFL SuperCoach AI Assistant

## Project Overview
Python CLI tool for AFL SuperCoach analysis. Scrapes FootyWire data, imports DFS Australia spreadsheets, stores in SQLite, provides AI-powered recommendations via Claude API.

## Tech Stack
- Python 3.9+ (code uses `from __future__ import annotations` for modern type hints)
- SQLAlchemy 2.0 ORM + SQLite
- httpx (async HTTP) + BeautifulSoup4 + lxml (HTML parsing)
- Typer + Rich (CLI)
- Anthropic Python SDK (AI advisor)

## Project Structure
- `src/` — All source code (package root)
- `src/models/database.py` — SQLAlchemy models and engine setup (7 tables: Player, SupercoachScore, MatchStats, Injury, MyTeamSlot, Trade, DfsPlayerStats)
- `src/scrapers/` — Web scrapers (base class + footywire)
- `src/importers/` — File importers (DFS Australia spreadsheet)
- `src/ai/` — Claude API integration
- `src/cli/main.py` — Typer CLI app
- `src/utils/config.py` — Configuration loading
- `data/` — SQLite database (gitignored .db files)
- `tests/` — Pytest tests

## Key Commands
```bash
sc db init                          # Initialize database
sc scrape footywire -s 2024 -r 1    # Scrape round scores
sc import dfs-australia <file.xlsx> # Import DFS Australia spreadsheet
sc team show                        # Display current team
sc team import team.csv             # Import team from CSV
sc player <name>                    # Player profile and stats (incl. DFS data)
sc player <name> --ai               # Player profile + AI analysis
sc advice                           # AI weekly analysis
sc chat "question"                  # Free-form AI chat
sc injuries                         # Show current injury list
```

## Conventions
- All files use `from __future__ import annotations` for Python 3.9 compat
- Config from `config.toml` + `.env` (secrets)
- Database path: `data/supercoach.db`
- Rate limiting: 2s between HTTP requests
- All scrapers extend `BaseScraper`
- CLI uses Typer sub-commands with Rich console output

## Running
```bash
pip install -e ".[dev]"
sc db init
sc scrape footywire --season 2024 --round 1
```

## Testing
```bash
pytest
```
