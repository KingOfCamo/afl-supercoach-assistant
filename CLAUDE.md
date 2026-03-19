# CLAUDE.md — SuperCoach AI Assistant Project Context

Claude Code: Read this file on every session. It contains the complete architecture of this project.

## Stack

- Backend: Python 3.9+, FastAPI, SQLAlchemy, uvicorn
- Frontend: Vanilla JavaScript + HTML/CSS (no framework — 4 static files)
- Database: SQLite locally (data/supercoach.db), PostgreSQL on Railway (production)
- AI: Anthropic Claude API via SDK
- Entry point: src/web/app.py → create_app() factory

## Folder Structure

```
src/
├── ai/                    # Claude API integration
├── analytics/             # Projections, trade engine, captain, DVP, byes, lineup optimiser
├── cli/                   # CLI commands (Typer)
├── dashboard/             # Rich TUI dashboard (terminal)
├── importers/             # SuperCoach API, DFS Australia spreadsheet importers
├── models/                # SQLAlchemy models & DB schema
├── scrapers/              # Web scrapers (FootyWire, FanFooty, Squiggle, AFL.com.au)
├── sync/                  # APScheduler background sync (scheduler.py, tasks.py)
├── utils/                 # Shared utilities
│   └── teams.py           # normalize_team() — CRITICAL for team name matching
└── web/                   # FastAPI app
    ├── app.py             # create_app() factory, startup events, route registration
    ├── middleware/         # JWT authentication
    ├── routes/            # API route handlers (team, players, analytics, sync, fixtures, ai, auth)
    ├── static/            # Frontend files served as static
    │   ├── app.js         # Main frontend JavaScript (ALL UI logic lives here)
    │   ├── index.html     # Main dashboard page
    │   ├── login.html     # Auth page
    │   └── style.css      # All styles
data/
└── supercoach.db          # Local SQLite database
docs/                      # GitHub Pages mirror of static files (separate asset paths)
```

## Database Schema (13 tables)

### Key Tables

**players** — ~1,093 rows
- Team names: MIXED formats ("Adelaide" AND "Crows", "Blues", etc.)
- Always use normalize_team() from src/utils/teams.py when comparing

**my_team** — user's team slots (DEF1-6, MID1-8, RUC1-2, FWD1-6, FLEX1, BENCH1-8)
- Local SQLite missing `user_id` column — Railway PostgreSQL has it
- Has is_captain, is_vice_captain, is_emergency, emergency_order columns

**fixtures** — ~207 rows (full 2026 season from Squiggle API)
- Team names: CANONICAL format only ("Adelaide", "Brisbane", "Geelong", etc.)

**bye_rounds** — derived from fixtures (teams not playing in a round)
- Auto-populates on startup if empty but fixtures exist
- season, round, team columns

**injuries** — ~140 rows (scraped from FootyWire, AFL.com.au, news articles)

**supercoach_scores** — per-round player scores, prices, breakevens

**lineup_status** — NAMED/EMERGENCY/OMITTED per player per round

**users** — JWT-authenticated dashboard users

## Team Name Handling

CRITICAL: Players table has mixed formats. Fixtures table uses canonical names. Always use:

```python
from src.utils.teams import normalize_team
```

Maps all variants ("Crows" → "Adelaide", "Blues" → "Carlton", "Geelong Cats" → "Geelong") to canonical form.

## API Routes (39 endpoints)

### Auth
- POST /api/auth/register, /api/auth/login

### Team Management
- GET /api/team — get current team with player data, bye status, lineup status
- POST /api/team/slot — assign player to slot
- DELETE /api/team/slot/{id} — remove player
- POST /api/team/swap — swap two players between slots (position validated)
- GET /api/team/optimise — AI-powered lineup optimisation suggestions
- PUT /api/team/captain — set captain/VC
- PUT /api/team/emergency — set 4 bench emergencies (max 2 per position line)
- POST /api/team/clear — clear team
- POST /api/team/import-csv — import from CSV

### Players
- GET /api/players/search?q= — search by name
- GET /api/players/{id} — player detail

### Analytics
- GET /api/analytics/projections — score projections (DVP-adjusted)
- GET /api/analytics/captain — captain rankings
- GET /api/analytics/trades — trade recommendations (includes bye impact)
- GET /api/analytics/live — live scoring
- GET /api/analytics/injuries — injury list
- GET /api/analytics/bye-impact — bye round impact on team
- GET /api/analytics/bye-planner — bye coverage matrix + risk score

### Fixtures
- GET /api/fixtures/db-round — fixtures from local DB with bye teams + round nav
- GET /api/fixtures/round — fixtures from AFL.com.au API

### Sync
- GET /api/sync/status — sync job status
- POST /api/sync/scores — sync fixtures + byes + scores (SYNC button calls this)
- POST /api/sync/trigger — trigger specific or all sync jobs

### AI
- GET /api/ai/weekly, /api/ai/captain, /api/ai/trades
- POST /api/ai/chat, /api/ai/analyze-player

### Config
- GET /api/config — season, auto-detected current round, trades remaining, bye alerts

## Frontend Architecture

4 files in src/web/static/:
- **index.html** — 4 sections: MY TEAM, DASHBOARD, AI INSIGHTS, BYE PLANNER
- **app.js** — ALL frontend logic: team rendering (field view + bench), player search, SYNC, round navigation, click-to-swap, AI optimise panel, bye impact bar
- **style.css** — dark theme, player cards, status badges, swap mode styles, optimise overlay
- **login.html** — JWT auth page

### Team View
- Field view: DEF×6, MID×8 (2 rows of 4), RUC×2, FWD×6, FLEX×1 on pitch layout
- Bench sidebar: BENCH×8 with emergency badges (E1-E4)
- Card shows: name, team dot, team abbrev, score, salary, lineup status icon
- Status icons: Named (green tick), Played (green circle), Live (blue dot), DNP (red X), Injured (orange warning), Emergency (orange E), BYE (slate pill)

### Key JS Functions
- `App.Team.loadTeam()` → fetches GET /api/team, renders cards
- `App.Team.syncAndRefreshScores()` → POST /api/sync/scores, re-fetch config + team
- `App.Team.handleCardClick()` → swap mode (click source → click target → execute)
- `App.Team.openOptimiser()` → GET /api/team/optimise, shows overlay with swap suggestions
- `App.Team.loadFixtures()` → GET /api/fixtures/db-round with round nav arrows
- `App.Byes.loadAll()` → GET /api/analytics/bye-planner, renders matrix + risk score

## Background Sync (APScheduler)

10 sync jobs running on intervals:
- supercoach_players (6h), supercoach_round (30m), footywire_scores (30m)
- footywire_injuries (4h), aflcomau_injuries (4h), afl_news_injuries (4h)
- fanfooty (15m), squiggle (1h), afl_lineups (2h), bye_rounds (6h)

All jobs skip on off-days (match day detection from fixtures table).
Initial sync_all() runs 10s after startup.

## Conventions

- All Python files use `from __future__ import annotations` for 3.9 compat
- Team names: ALWAYS normalise with normalize_team()
- Config: config.toml + .env (ANTHROPIC_API_KEY, DATABASE_URL, JWT_SECRET)
- Local dev: `uvicorn src.web.app:create_app --factory --reload`
- Production: Railway with PostgreSQL (DATABASE_URL env var)
- Testing: `pytest`

## Key CLI Commands

```bash
sc db init                          # Initialize database
sc scrape footywire -s 2026 -r 1    # Scrape round scores
sc import dfs-australia <file.xlsx> # Import DFS spreadsheet
sc team show                        # Display team in terminal
sc advice                           # AI weekly analysis
sc chat "question"                  # AI chat
```
