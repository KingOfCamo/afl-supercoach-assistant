from __future__ import annotations

"""AFL SuperCoach analytics engine.

Public API:
    compute_breakeven    — Breakeven analysis for a player
    compute_dvp          — Team DVP rankings for all positions
    get_opponent_dvp     — Single matchup DVP lookup
    project_player       — Project a single player's score (heuristic)
    project_round        — Batch project all players for a round (heuristic)
    rank_captain_options — Captain rankings for your team
    suggest_trades       — Trade recommendations
    train_model          — Train ML projection model
    ml_project_player    — Project score using ML model
    ml_project_round     — Batch ML projection
    get_live_round       — Live scoring tracker
    get_round_summary    — Post-round summary stats
"""

from src.analytics.breakevens import compute_breakeven
from src.analytics.captain import rank_captain_options
from src.analytics.dvp import compute_dvp, get_opponent_dvp
from src.analytics.live_scores import get_live_round, get_round_summary
from src.analytics.ml_model import ml_project_player, ml_project_round, train_model
from src.analytics.projections import project_player, project_round
from src.analytics.trade_engine import suggest_trades

__all__ = [
    "compute_breakeven",
    "compute_dvp",
    "get_live_round",
    "get_opponent_dvp",
    "get_round_summary",
    "ml_project_player",
    "ml_project_round",
    "project_player",
    "project_round",
    "rank_captain_options",
    "suggest_trades",
    "train_model",
]
