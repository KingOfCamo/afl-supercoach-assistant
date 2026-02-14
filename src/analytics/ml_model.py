from __future__ import annotations

"""ML-based score projection model using scikit-learn.

Trains a GradientBoosting regressor on player features to predict SuperCoach
scores. Falls back to the heuristic model (projections.py) when insufficient
training data exists.

Features used:
- Rolling averages (3-game, 5-game, season)
- Breakeven and price trend
- DVP rank of upcoming opponent
- Position category
- CBA%, PPM, ownership from DFS data
- Home/away indicator
- Games played this season
- Score variance (consistency)

Requires at least 3 completed rounds of data to train. Until then, the
heuristic model in projections.py is used as fallback.
"""

import logging
import math
import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from sqlalchemy import desc, select

from src.analytics.dvp import _map_position, get_next_opponent, get_opponent_dvp
from src.models.database import (
    DfsPlayerStats,
    Fixture,
    MyTeamSlot,
    Player,
    SupercoachScore,
    get_session,
)

logger = logging.getLogger(__name__)

# Minimum rounds of data needed to train the model
MIN_ROUNDS_TO_TRAIN = 3
MODEL_VERSION = "gbr_v1"


@dataclass
class MLProjectionResult:
    """Projection from the ML model."""

    player_id: int
    player_name: str
    team: str
    position: Optional[str]
    projected_score: float
    floor: float
    ceiling: float
    confidence: float
    opponent: Optional[str]
    dvp_rank: Optional[int]
    method: str  # "ml_gbr_v1" or "heuristic_fallback"


def _model_path() -> Path:
    """Return path to the saved model file."""
    from src.utils.config import get_config

    config = get_config()
    data_dir = Path(config.database.path).parent
    return data_dir / "ml_model.pkl"


def _build_features_for_round(
    player_id: int,
    season: int,
    round_num: int,
    session: object,
) -> Optional[dict]:
    """Build feature dict for a player in a specific round.

    Returns None if insufficient data to build features.
    """
    player = session.get(Player, player_id)  # type: ignore[union-attr]
    if player is None:
        return None

    # Get all scores before this round
    scores = (
        session.execute(  # type: ignore[union-attr]
            select(SupercoachScore)
            .where(
                SupercoachScore.player_id == player_id,
                SupercoachScore.season == season,
                SupercoachScore.round < round_num,
                SupercoachScore.score.isnot(None),
            )
            .order_by(desc(SupercoachScore.round))
        )
        .scalars()
        .all()
    )

    valid_scores = [s.score for s in scores if s.score is not None]
    if not valid_scores:
        return None

    # Rolling averages
    rolling_3 = valid_scores[:3]
    rolling_5 = valid_scores[:5]
    rolling_3_avg = sum(rolling_3) / len(rolling_3) if rolling_3 else 0
    rolling_5_avg = sum(rolling_5) / len(rolling_5) if rolling_5 else 0
    season_avg = sum(valid_scores) / len(valid_scores) if valid_scores else 0

    # Score variance
    if len(valid_scores) >= 2:
        mean = sum(valid_scores) / len(valid_scores)
        variance = sum((s - mean) ** 2 for s in valid_scores) / len(valid_scores)
        std_dev = math.sqrt(variance)
    else:
        std_dev = 0.0

    # Latest breakeven and price
    latest = scores[0] if scores else None
    breakeven = latest.breakeven if latest and latest.breakeven else 0
    price = latest.price if latest and latest.price else 0

    # Games played this season
    games_played = len(valid_scores)

    # Position encoding
    dvp_pos = _map_position(player.position)
    pos_encoding = {"DEF": 0, "MID": 1, "RUC": 2, "FWD": 3}.get(dvp_pos or "", 1)

    # DVP rank of opponent
    opponent = get_next_opponent(player.team, season, round_num)
    dvp_rank = 9  # neutral default
    if opponent and dvp_pos:
        dvp_entry = get_opponent_dvp(opponent, dvp_pos, season, round_num)
        if dvp_entry:
            dvp_rank = dvp_entry.dvp_rank or 9

    # Home/away
    is_home = 0
    fixture = session.execute(  # type: ignore[union-attr]
        select(Fixture)
        .where(
            Fixture.season == season,
            Fixture.round == round_num,
        )
    ).scalars().all()
    for f in fixture:
        if f.home_team == player.team:
            is_home = 1
            break
        if f.away_team == player.team:
            is_home = 0
            break

    # DFS data
    dfs = session.execute(  # type: ignore[union-attr]
        select(DfsPlayerStats)
        .where(DfsPlayerStats.player_id == player_id)
        .order_by(desc(DfsPlayerStats.season))
        .limit(1)
    ).scalar_one_or_none()

    cba_pct = dfs.cba_pct if dfs and dfs.cba_pct else 0.0
    ppm = dfs.points_per_minute if dfs and dfs.points_per_minute else 0.0
    ownership = dfs.ownership_pct if dfs and dfs.ownership_pct else 0.0
    prev_year_avg = dfs.prev_year_avg if dfs and dfs.prev_year_avg else 0.0

    # Score trend (recent vs season)
    trend = rolling_3_avg - season_avg if season_avg > 0 else 0.0

    return {
        "rolling_3_avg": rolling_3_avg,
        "rolling_5_avg": rolling_5_avg,
        "season_avg": season_avg,
        "std_dev": std_dev,
        "breakeven": breakeven,
        "price": price / 100000.0,  # normalize to ~1-10 range
        "games_played": games_played,
        "pos_encoding": pos_encoding,
        "dvp_rank": dvp_rank,
        "is_home": is_home,
        "cba_pct": cba_pct,
        "ppm": ppm,
        "ownership": ownership,
        "prev_year_avg": prev_year_avg,
        "trend": trend,
    }


FEATURE_NAMES = [
    "rolling_3_avg",
    "rolling_5_avg",
    "season_avg",
    "std_dev",
    "breakeven",
    "price",
    "games_played",
    "pos_encoding",
    "dvp_rank",
    "is_home",
    "cba_pct",
    "ppm",
    "ownership",
    "prev_year_avg",
    "trend",
]


def _features_to_array(features: dict) -> np.ndarray:
    """Convert feature dict to numpy array in consistent order."""
    return np.array([features[f] for f in FEATURE_NAMES])


def train_model(season: Optional[int] = None) -> dict:
    """Train the ML projection model on available season data.

    Builds training examples from completed rounds: for each player in each
    completed round, build features from data prior to that round and use
    the actual score as the target.

    Returns:
        dict with keys: 'rounds_used', 'samples', 'train_r2', 'test_r2', 'model_path'
    """
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.model_selection import train_test_split

    from src.utils.config import get_config

    config = get_config()
    target_season = season or config.season

    session = get_session()
    try:
        # Find completed rounds
        completed_rounds = set()
        fixtures = session.execute(
            select(Fixture)
            .where(
                Fixture.season == target_season,
                Fixture.is_complete == True,  # noqa: E712
            )
        ).scalars().all()

        for f in fixtures:
            completed_rounds.add(f.round)

        completed_rounds = sorted(completed_rounds)

        if len(completed_rounds) < MIN_ROUNDS_TO_TRAIN:
            return {
                "error": f"Need at least {MIN_ROUNDS_TO_TRAIN} completed rounds. "
                f"Have {len(completed_rounds)}.",
                "rounds_used": len(completed_rounds),
                "samples": 0,
            }

        # Build training data
        X_data = []
        y_data = []

        # Get all players with scores
        player_ids = set(
            session.execute(
                select(SupercoachScore.player_id)
                .where(SupercoachScore.season == target_season)
                .distinct()
            ).scalars().all()
        )

        for round_num in completed_rounds:
            if round_num < 2:
                continue  # Need at least 1 round of prior data

            for pid in player_ids:
                features = _build_features_for_round(pid, target_season, round_num, session)
                if features is None:
                    continue

                # Get actual score for this round (target)
                actual = session.execute(
                    select(SupercoachScore.score)
                    .where(
                        SupercoachScore.player_id == pid,
                        SupercoachScore.season == target_season,
                        SupercoachScore.round == round_num,
                        SupercoachScore.score.isnot(None),
                    )
                ).scalar_one_or_none()

                if actual is None:
                    continue

                X_data.append(_features_to_array(features))
                y_data.append(actual)

    finally:
        session.close()

    if len(X_data) < 50:
        return {
            "error": f"Insufficient training samples ({len(X_data)}). Need at least 50.",
            "rounds_used": len(completed_rounds),
            "samples": len(X_data),
        }

    X = np.array(X_data)
    y = np.array(y_data)

    # Train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    # Train GradientBoosting with conservative hyperparameters
    model = GradientBoostingRegressor(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.1,
        min_samples_leaf=10,
        subsample=0.8,
        random_state=42,
    )
    model.fit(X_train, y_train)

    train_r2 = model.score(X_train, y_train)
    test_r2 = model.score(X_test, y_test)

    # Save model
    model_info = {
        "model": model,
        "version": MODEL_VERSION,
        "season": target_season,
        "rounds_used": len(completed_rounds),
        "samples": len(X_data),
        "train_r2": train_r2,
        "test_r2": test_r2,
        "feature_names": FEATURE_NAMES,
    }

    path = _model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(model_info, f)

    logger.info(
        "ML model trained: %d samples, %d rounds, R2=%.3f (test)",
        len(X_data),
        len(completed_rounds),
        test_r2,
    )

    return {
        "rounds_used": len(completed_rounds),
        "samples": len(X_data),
        "train_r2": round(train_r2, 4),
        "test_r2": round(test_r2, 4),
        "model_path": str(path),
        "feature_importances": dict(
            zip(FEATURE_NAMES, [round(fi, 4) for fi in model.feature_importances_])
        ),
    }


def _load_model() -> Optional[dict]:
    """Load a previously trained model from disk."""
    path = _model_path()
    if not path.exists():
        return None

    try:
        with open(path, "rb") as f:
            model_info = pickle.load(f)

        if model_info.get("version") != MODEL_VERSION:
            logger.warning("Model version mismatch. Retrain with 'sc ml train'.")
            return None

        return model_info
    except Exception as e:
        logger.error("Failed to load ML model: %s", e)
        return None


def ml_project_player(
    player_id: int,
    round_num: int,
    season: Optional[int] = None,
) -> Optional[MLProjectionResult]:
    """Project a single player's score using the ML model.

    Falls back to heuristic model if ML model is not available.
    """
    from src.utils.config import get_config

    config = get_config()
    target_season = season or config.season

    model_info = _load_model()

    session = get_session()
    try:
        player = session.get(Player, player_id)
        if player is None:
            return None

        features = _build_features_for_round(
            player_id, target_season, round_num, session
        )
    finally:
        session.close()

    if features is None or model_info is None:
        # Fall back to heuristic
        from src.analytics.projections import project_player

        heuristic = project_player(player_id, round_num, season=target_season)
        if heuristic is None:
            return None

        return MLProjectionResult(
            player_id=heuristic.player_id,
            player_name=heuristic.player_name,
            team=heuristic.team,
            position=heuristic.position,
            projected_score=heuristic.projected_score,
            floor=heuristic.floor,
            ceiling=heuristic.ceiling,
            confidence=heuristic.confidence,
            opponent=heuristic.opponent,
            dvp_rank=heuristic.dvp_rank,
            method="heuristic_fallback",
        )

    model = model_info["model"]
    X = _features_to_array(features).reshape(1, -1)
    predicted = model.predict(X)[0]

    # Floor/ceiling from model uncertainty: use standard deviation of tree predictions
    tree_predictions = np.array(
        [tree.predict(X)[0] for tree in model.estimators_.ravel()]
    )
    pred_std = tree_predictions.std()
    floor = max(0, predicted - 1.5 * pred_std)
    ceiling = predicted + 1.5 * pred_std

    # Confidence based on model R2 and data availability
    test_r2 = model_info.get("test_r2", 0.3)
    base_confidence = min(0.9, max(0.3, test_r2 + 0.1))
    games = features.get("games_played", 0)
    if games >= 10:
        confidence = base_confidence
    elif games >= 5:
        confidence = base_confidence * 0.9
    elif games >= 3:
        confidence = base_confidence * 0.7
    else:
        confidence = base_confidence * 0.5

    # Get opponent info
    opponent = get_next_opponent(player.team, target_season, round_num)
    dvp_pos = _map_position(player.position)
    dvp_rank = None
    if opponent and dvp_pos:
        dvp_entry = get_opponent_dvp(opponent, dvp_pos, target_season, round_num)
        if dvp_entry:
            dvp_rank = dvp_entry.dvp_rank

    return MLProjectionResult(
        player_id=player.id,
        player_name=player.name,
        team=player.team,
        position=player.position,
        projected_score=round(predicted, 1),
        floor=round(floor, 1),
        ceiling=round(ceiling, 1),
        confidence=round(confidence, 3),
        opponent=opponent,
        dvp_rank=dvp_rank,
        method=f"ml_{MODEL_VERSION}",
    )


def ml_project_round(
    round_num: int,
    season: Optional[int] = None,
    team_only: bool = False,
) -> list[MLProjectionResult]:
    """Batch ML projection for all players (or team only) in a round."""
    from src.utils.config import get_config

    config = get_config()
    target_season = season or config.season

    session = get_session()
    try:
        if team_only:
            player_ids = (
                session.execute(select(MyTeamSlot.player_id))
                .scalars()
                .all()
            )
        else:
            score_pids = set(
                session.execute(
                    select(SupercoachScore.player_id)
                    .where(SupercoachScore.season == target_season)
                    .distinct()
                )
                .scalars()
                .all()
            )
            dfs_pids = set(
                session.execute(
                    select(DfsPlayerStats.player_id).distinct()
                )
                .scalars()
                .all()
            )
            player_ids = list(score_pids | dfs_pids)
    finally:
        session.close()

    results = []
    for pid in player_ids:
        result = ml_project_player(pid, round_num, season=target_season)
        if result is not None:
            results.append(result)

    results.sort(key=lambda r: r.projected_score, reverse=True)
    return results
