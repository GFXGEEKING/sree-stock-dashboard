"""Correlation filter: block entries that duplicate existing portfolio risk.

Computes pairwise Pearson correlation of daily returns between a candidate
symbol and every open paper position (plus cash-adjusted watchlist), using
cached daily_candles from data_fetcher. A candidate whose correlation with
any open position exceeds the threshold is rejected — the agent uses this to
avoid stacking the same bet three times under different tickers.
"""
from __future__ import annotations

import logging
import math
import sqlite3
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "backend/data/stocks.db")
DEFAULT_THRESHOLD = 0.70          # reject above this correlation
DEFAULT_LOOKBACK_DAYS = 60
MIN_OVERLAP_DAYS = 30


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def _closes(symbol: str, lookback: int) -> List[Optional[float]]:
    """Most recent N close prices for a symbol (oldest first) or []."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT date, close FROM daily_candles WHERE symbol = ? ORDER BY date DESC LIMIT ?",
            ((symbol or "").upper(), int(lookback)),
        ).fetchall()
        return [r["close"] for r in reversed(rows)]
    finally:
        conn.close()


def _to_returns(closes: List[float]) -> List[float]:
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1]:
            out.append((closes[i] / closes[i - 1]) - 1.0)
    return out


def _pearson(a: List[float], b: List[float]) -> Optional[float]:
    n = min(len(a), len(b))
    if n < 5:
        return None
    a, b = a[-n:], b[-n:]
    ma = sum(a) / n
    mb = sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / math.sqrt(va * vb)


def correlation_with_positions(
    candidate: str,
    held_symbols: List[str],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> Dict:
    """Max correlation of candidate vs held symbols + per-symbol detail.

    Symbols with insufficient overlapping data are skipped (not blocking).
    """
    details = []
    max_corr = None
    candidate = (candidate or "").upper()
    # Identity check FIRST — "already held" must block regardless of data
    for sym in held_symbols:
        if (sym or "").upper() == candidate:
            return {"ok": True, "max_corr": 1.0, "details": [{"symbol": sym, "corr": 1.0}],
                    "blocked": True, "reason": "already held"}
    cand_closes = _closes(candidate, lookback_days)
    if not cand_closes:
        return {"ok": True, "max_corr": None, "details": [], "blocked": False,
                "reason": "no candle data for candidate"}
    cand_returns = _to_returns([c for c in cand_closes if c is not None])
    for sym in held_symbols:
        closes = _closes(sym, lookback_days)
        closes = [c for c in closes if c is not None]
        if len(closes) < MIN_OVERLAP_DAYS:
            continue
        corr = _pearson(cand_returns, _to_returns(closes))
        if corr is None:
            continue
        details.append({"symbol": sym, "corr": round(corr, 3)})
        if max_corr is None or corr > max_corr:
            max_corr = corr
    return {
        "ok": True,
        "max_corr": round(max_corr, 3) if max_corr is not None else None,
        "details": details,
        "blocked": False,
    }


def entry_allowed(
    candidate: str,
    held_symbols: List[str],
    threshold: float = DEFAULT_THRESHOLD,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> Dict:
    """Full gate for the agent: reject if correlated above threshold."""
    res = correlation_with_positions(candidate, held_symbols, lookback_days)
    if not res.get("ok"):
        return {"allowed": False, "reason": "correlation check failed"}
    if res.get("reason") == "already held":
        return {"allowed": False, "reason": "position already open"}
    mc = res.get("max_corr")
    if mc is not None and mc > threshold:
        worst = max(res["details"], key=lambda d: d["corr"]) if res["details"] else None
        return {
            "allowed": False,
            "reason": (
                f"correlation {mc:.2f} with {worst['symbol']} exceeds {threshold}"
                if worst else f"correlation {mc:.2f} exceeds {threshold}"
            ),
            "max_corr": mc,
            "details": res["details"],
        }
    return {"allowed": True, "max_corr": mc, "details": res["details"]}


__all__ = [
    "correlation_with_positions", "entry_allowed",
    "DEFAULT_THRESHOLD", "DEFAULT_LOOKBACK_DAYS",
]

