"""Event filter: block new entries close to scheduled earnings dates.

yfinance exposes next earnings dates via Ticker.earnings_dates (calendar
days ahead). Trading a breakout signal into an earnings release is a
different bet than the momentum thesis — the agent (and optionally the
user via the API) refuses to open positions within the blackout window.

Cache: in-memory 12h per symbol (earnings dates rarely change intraday).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "backend/data/stocks.db")
DEFAULT_BLACKOUT_DAYS = 5      # no entries within N days before earnings
CACHE_TTL_S = 12 * 3600

_CACHE: Dict[str, tuple] = {}  # symbol -> (timestamp, [iso dates])


def _next_earnings_dates(symbol: str) -> List[str]:
    """Upcoming earnings dates for a symbol from yfinance (best effort)."""
    import yfinance as yf

    sym = (symbol or "").upper().strip()
    now = time.time()
    if sym in _CACHE:
        ts, dates = _CACHE[sym]
        if now - ts < CACHE_TTL_S:
            return dates
    dates: List[str] = []
    try:
        t = yf.Ticker(sym)
        ed = t.earnings_dates
        if ed is not None and not ed.empty:
            today = datetime.utcnow().date()
            for idx in ed.index:
                try:
                    d = idx.date() if hasattr(idx, "date") else idx
                    d = d if isinstance(d, datetime.date) else None
                except Exception:
                    d = None
                if d and d >= today:
                    dates.append(d.isoformat())
    except Exception as e:
        logger.debug(f"earnings_dates fetch failed for {sym}: {e}")
    dates = sorted(dates)[:4]
    _CACHE[sym] = (time.time(), dates)
    return dates


def days_until_next_earnings(symbol: str) -> Optional[int]:
    """Days from today to the next earnings date (None if unknown/none)."""
    dates = _next_earnings_dates(symbol)
    if not dates:
        return None
    today = datetime.utcnow().date()
    for iso in dates:
        d = datetime.fromisoformat(iso).date()
        if d >= today:
            return (d - today).days
    return None


def entry_allowed(
    symbol: str,
    blackout_days: int = DEFAULT_BLACKOUT_DAYS,
) -> Dict:
    """Gate: allowed unless earnings fall within the blackout window."""
    days = days_until_next_earnings(symbol)
    if days is None:
        # Unknown date — allow but flag (better than blocking on missing data)
        return {"allowed": True, "days_to_earnings": None, "note": "no earnings date known"}
    if days <= blackout_days:
        return {
            "allowed": False,
            "days_to_earnings": days,
            "reason": f"earnings in {days}d (within {blackout_days}d blackout)",
        }
    return {"allowed": True, "days_to_earnings": days}


def bulk_check(symbols: List[str], blackout_days: int = DEFAULT_BLACKOUT_DAYS) -> Dict[str, Dict]:
    return {s: entry_allowed(s, blackout_days) for s in symbols}


__all__ = [
    "days_until_next_earnings", "entry_allowed", "bulk_check",
    "DEFAULT_BLACKOUT_DAYS",
]

