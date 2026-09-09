"""ML scorer: probability that a breakout score leads to a positive forward return.

Trains a scikit-learn GradientBoostingClassifier on the scan-history
snapshots + daily candle forward returns (see scan_history.get_training_data).

Design constraints:
  * Cold-start safe: with < MIN_TRAIN rows, predict via the heuristic
    mapping of the rule score and mark model="heuristic" so the UI shows
    it clearly.
  * No look-ahead: training rows only include snapshots whose forward
    horizon has fully completed.
  * Deterministic: fixed random_state; model cached in memory and
    retrained only when enough NEW labeled rows appear.
  * The ML probability is advisory — blended into the final score, never
    a substitute for the rule-based components.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    from sklearn.ensemble import GradientBoostingClassifier
    HAS_SKLEARN = True
except Exception:
    HAS_SKLEARN = False

MIN_TRAIN_ROWS = 60          # below this, heuristic fallback
RETRAIN_EVERY_ROWS = 40      # retrain after this many new labeled rows
HORIZON_DAYS = 10            # forward-return horizon for labels
BLEND_WEIGHT_ML = 0.40       # 60% rules / 40% ML in the blended score

_MODEL = None                 # cached fitted model
_TRAIN_ROWS_LAST = 0          # rows the current model was trained on
_FEATURES = [
    "breakout_score", "composite_score",
    "comp_momentum", "comp_trend", "comp_volatility", "comp_volume",
    "comp_rsi", "comp_macd", "comp_fundamentals",
    "comp_sector_momentum", "comp_earnings_surprise",
]


def _heuristic_prob(score: float) -> float:
    """Cold-start mapping from rule score to probability."""
    if score is None:
        return 0.5
    # calibrated to roughly match typical hit-rates of momentum scores
    return float(np.clip(0.35 + (float(score) / 100.0) * 0.40, 0.05, 0.90))


def _rows_to_xy(rows: List[Dict]):
    X = np.array(
        [[float(r.get(f) or 0.0) for f in _FEATURES] for r in rows],
        dtype=float,
    )
    y = np.array([1 if (r.get("forward_return") or 0) > 0 else 0 for r in rows])
    return X, y


def maybe_retrain(force: bool = False) -> Dict:
    """(Re)train the model if enough labeled rows have accumulated."""
    global _MODEL, _TRAIN_ROWS_LAST
    if not HAS_SKLEARN:
        return {"trained": False, "reason": "scikit-learn not installed"}
    try:
        from . import scan_history

        rows = scan_history.get_training_data(horizon_days=HORIZON_DAYS)
        if len(rows) < MIN_TRAIN_ROWS:
            return {"trained": False, "reason": f"insufficient data ({len(rows)}/{MIN_TRAIN_ROWS} rows)"}
        if (
            not force
            and _MODEL is not None
            and len(rows) - _TRAIN_ROWS_LAST < RETRAIN_EVERY_ROWS
        ):
            return {"trained": False, "reason": "model fresh", "rows": len(rows)}
        X, y = _rows_to_xy(rows)
        if len(np.unique(y)) < 2:
            return {"trained": False, "reason": "single-class labels"}
        model = GradientBoostingClassifier(
            n_estimators=120, max_depth=3, learning_rate=0.08, random_state=42,
        )
        model.fit(X, y)
        _MODEL = model
        _TRAIN_ROWS_LAST = len(rows)
        return {
            "trained": True,
            "rows": len(rows),
            "positive_rate": round(float(y.mean()), 3),
        }
    except Exception as e:
        logger.error(f"ml retrain failed: {e}", exc_info=True)
        return {"trained": False, "reason": str(e)}


def predict(features: Dict[str, float]) -> Dict:
    """P(positive forward return) for one scored stock.

    Accepts {breakout_score, composite_score, comp_*: ...}. Falls back to
    the heuristic when no model is available.
    """
    score = float(features.get("breakout_score") or 0.0)
    if _MODEL is None or not HAS_SKLEARN:
        prob = _heuristic_prob(score)
        return {"probability": round(prob, 3), "model": "heuristic"}
    try:
        x = np.array([[float(features.get(f) or 0.0) for f in _FEATURES]])
        prob = float(_MODEL.predict_proba(x)[0][1])
        return {"probability": round(prob, 3), "model": "gradient_boosting"}
    except Exception as e:
        logger.debug(f"ml predict failed: {e}")
        prob = _heuristic_prob(score)
        return {"probability": round(prob, 3), "model": "heuristic_fallback"}


def blend_score(breakout_score: float, prob: float) -> float:
    """Blend rule score with ML probability into a final 0-100 score."""
    b = float(breakout_score or 0.0)
    p = float(np.clip(prob, 0, 1))
    return round((1 - BLEND_WEIGHT_ML) * b + BLEND_WEIGHT_ML * (p * 100.0), 2)


def enrich(entry: Dict) -> Dict:
    """Add ml_probability + blended score to a rankings entry."""
    try:
        comps = entry.get("breakout_components") or {}
        features = {
            "breakout_score": entry.get("breakout_score") or 0.0,
            "composite_score": entry.get("composite_score") or 0.0,
            **{f"comp_{k}": v for k, v in comps.items()},
        }
        pred = predict(features)
        entry["ml_probability"] = pred["probability"]
        entry["ml_model"] = pred["model"]
        entry["blended_score"] = blend_score(
            entry.get("breakout_score") or 0.0, pred["probability"]
        )
    except Exception as e:
        logger.debug(f"ml enrich failed for {entry.get('ticker')}: {e}")
        entry["ml_probability"] = None
        entry["blended_score"] = entry.get("breakout_score") or 0.0
    return entry


def model_status() -> Dict:
    return {
        "sklearn_available": HAS_SKLEARN,
        "model_loaded": _MODEL is not None,
        "trained_rows": _TRAIN_ROWS_LAST,
        "min_train_rows": MIN_TRAIN_ROWS,
        "horizon_days": HORIZON_DAYS,
        "blend_weight_ml": BLEND_WEIGHT_ML,
    }


__all__ = [
    "maybe_retrain", "predict", "blend_score", "enrich", "model_status",
    "HORIZON_DAYS", "MIN_TRAIN_ROWS",
]

