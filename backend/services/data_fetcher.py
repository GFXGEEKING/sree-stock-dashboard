"""yfinance-based data fetcher with SQLite cache and async concurrency.

Designed to handle the full 3,000+ ticker universe while respecting yfinance's
free-tier rate limit (~2,000 requests / hour).  We:

* keep a SQLite cache with a 24h TTL for daily candles (one candle per row
  per symbol) so subsequent scans are < 30s;
* fetch new tickers in parallel via a ThreadPoolExecutor (8 workers);
* apply exponential back-off on 429 / connection errors;
* expose a high-level ``fetch_universe()`` that returns a payload ready to
  feed the scoring engine.
"""

from __future__ import annotations

import os
import time
import json
import sqlite3
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

import pandas as pd
import yfinance as yf

from . import universe as uni

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------

DB_PATH = os.getenv("DB_PATH", "backend/data/stocks.db")
CACHE_TTL_HOURS = float(os.getenv("CACHE_TTL_HOURS", "24"))
FETCH_CONCURRENCY = int(os.getenv("FETCH_CONCURRENCY", "8"))
CANDLE_LIMIT = int(os.getenv("CANDLE_LIMIT", "250"))
RATE_LIMIT_DELAY = float(os.getenv("YFINANCE_DELAY", "0.10"))
MAX_RETRIES = int(os.getenv("YFINANCE_MAX_RETRIES", "3"))

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------
# SCHEMA
# ----------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickers (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    sector TEXT,
    country TEXT,
    currency TEXT,
    exchange TEXT,
    last_updated TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_candles (
    symbol TEXT,
    date DATE,
    open REAL, high REAL, low REAL, close REAL,
    adj_close REAL, volume INTEGER,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS ticker_info (
    symbol TEXT PRIMARY KEY,
    info_json TEXT,
    fetched_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fetch_status (
    symbol TEXT PRIMARY KEY,
    last_success TIMESTAMP,
    last_attempt TIMESTAMP,
    success_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_candles_symbol ON daily_candles(symbol);
CREATE INDEX IF NOT EXISTS idx_candles_date ON daily_candles(date);
"""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db() -> None:
    conn = _get_conn()
    try:
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------
# CACHE HELPERS
# ----------------------------------------------------------------------


def _is_cache_fresh(symbol: str, ttl_hours: float) -> bool:
    conn = _get_conn()
    try:
        st = conn.execute(
            "SELECT last_success FROM fetch_status WHERE symbol = ?", (symbol,)
        ).fetchone()
        if not st or not st[0]:
            return False
        last_fetch = datetime.fromisoformat(st[0])
        return (datetime.utcnow() - last_fetch) < timedelta(hours=ttl_hours)
    finally:
        conn.close()


def _load_cached_candles(symbol: str) -> Optional[pd.DataFrame]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT date, open, high, low, close, adj_close, volume "
            "FROM daily_candles WHERE symbol = ? ORDER BY date",
            (symbol,),
        ).fetchall()
        if not rows:
            return None
        df = pd.DataFrame(
            rows, columns=["Date", "Open", "High", "Low", "Close", "AdjClose", "Volume"]
        )
        df["Date"] = pd.to_datetime(df["Date"])
        df.set_index("Date", inplace=True)
        return df
    finally:
        conn.close()


def _save_candles(symbol: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    conn = _get_conn()
    try:
        rows = []
        for idx, row in df.iterrows():
            d = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
            rows.append(
                (
                    symbol,
                    d,
                    float(row.get("Open", 0) or 0),
                    float(row.get("High", 0) or 0),
                    float(row.get("Low", 0) or 0),
                    float(row.get("Close", 0) or 0),
                    float(row.get("AdjClose", row.get("Close", 0)) or 0),
                    int(row.get("Volume", 0) or 0),
                )
            )
        conn.executemany(
            "INSERT OR REPLACE INTO daily_candles "
            "(symbol, date, open, high, low, close, adj_close, volume) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _save_info(symbol: str, info: Dict[str, Any]) -> None:
    if not info:
        return
    conn = _get_conn()
    try:
        clean = {
            k: v
            for k, v in info.items()
            if isinstance(v, (str, int, float, bool, type(None)))
        }
        conn.execute(
            "INSERT OR REPLACE INTO ticker_info (symbol, info_json, fetched_at) "
            "VALUES (?,?,?)",
            (symbol, json.dumps(clean), datetime.utcnow().isoformat()),
        )
        conn.commit()
    except Exception as e:
        logger.debug(f"Could not cache info for {symbol}: {e}")
    finally:
        conn.close()


def _load_info(symbol: str) -> Dict[str, Any]:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT info_json FROM ticker_info WHERE symbol = ?", (symbol,)
        ).fetchone()
        if not row or not row[0]:
            return {}
        try:
            return json.loads(row[0])
        except Exception:
            return {}
    finally:
        conn.close()


def _record_fetch(symbol: str, success: bool, error: str = "") -> None:
    conn = _get_conn()
    try:
        now = datetime.utcnow().isoformat()
        if success:
            conn.execute(
                "INSERT INTO fetch_status (symbol, last_success, last_attempt, success_count) "
                "VALUES (?,?,?,1) "
                "ON CONFLICT(symbol) DO UPDATE SET "
                "  last_success=?, last_attempt=?, success_count = success_count + 1",
                (symbol, now, now, now, now),
            )
        else:
            conn.execute(
                "INSERT INTO fetch_status (symbol, last_attempt, error_count, last_error) "
                "VALUES (?,?,1,?) "
                "ON CONFLICT(symbol) DO UPDATE SET "
                "  last_attempt=?, error_count = error_count + 1, last_error=?",
                (symbol, now, error, now, error),
            )
        conn.commit()
    finally:
        conn.close()


def _save_universe_meta(tickers: List[Tuple[str, str, str, str, str, str]]) -> None:
    conn = _get_conn()
    try:
        for sym, name, sector, country, ccy, ex in tickers:
            conn.execute(
                "INSERT OR REPLACE INTO tickers "
                "(symbol, name, sector, country, currency, exchange, last_updated) "
                "VALUES (?,?,?,?,?,?,?)",
                (sym, name, sector, country, ccy, ex, datetime.utcnow().isoformat()),
            )
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------
# YFINANCE WORKERS
# ----------------------------------------------------------------------


def _history_compat(t: "yf.Ticker", period: str = "1y") -> pd.DataFrame:
    """yfinance-version-tolerant history call.

    yfinance >= 1.x removed the ``progress`` kwarg; older versions need
    it to silence logs. Try the new signature first, fall back.
    """
    try:
        return t.history(period=period, auto_adjust=False, progress=False)
    except TypeError:
        return t.history(period=period, auto_adjust=False)


def _fetch_one_sync(symbol: str) -> Tuple[str, Optional[pd.DataFrame], Dict[str, Any], str]:
    """Synchronous fetcher - runs inside a worker thread."""
    last_err = ""
    for attempt in range(MAX_RETRIES):
        try:
            t = yf.Ticker(symbol)
            hist = _history_compat(t, "1y")
            if hist is None or hist.empty:
                raise ValueError("empty history")
            keep = [c for c in ("Open", "High", "Low", "Close", "Adj Close", "Volume") if c in hist.columns]
            hist = hist[keep].copy()
            if "Adj Close" in hist.columns:
                hist.rename(columns={"Adj Close": "AdjClose"}, inplace=True)
            hist = hist.tail(CANDLE_LIMIT)
            try:
                info = t.info or {}
            except Exception:
                info = {}
            return symbol, hist, info, ""
        except Exception as e:
            last_err = str(e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(0.5 * (2 ** attempt))
    return symbol, None, {}, last_err

# ----------------------------------------------------------------------
# HIGH-LEVEL API
# ----------------------------------------------------------------------


def fetch_universe(
    region: str = "ALL",
    top_n: int = 0,
    force_refresh: bool = False,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Fetch OHLCV + info for the given region.

    Args:
        region:    "DE" / "EU" / "US" / "ALL"
        top_n:     If > 0, only fetch the first N tickers (for quick scans)
        force_refresh: Bypass cache TTL
        use_cache: Use SQLite cache when available
    """
    init_db()
    tickers = uni.tickers_by_region(region)
    if top_n > 0:
        tickers = tickers[:top_n]

    start = time.time()
    out_candles: Dict[str, pd.DataFrame] = {}
    out_info: Dict[str, Dict[str, Any]] = {}
    out_errors: Dict[str, str] = {}
    cached: List[str] = []
    fetched: List[str] = []

    to_fetch: List[str] = []
    if use_cache and not force_refresh:
        for sym, *_ in tickers:
            if _is_cache_fresh(sym, CACHE_TTL_HOURS):
                df = _load_cached_candles(sym)
                if df is not None and not df.empty:
                    out_candles[sym] = df
                    out_info[sym] = _load_info(sym)
                    cached.append(sym)
                    continue
            to_fetch.append(sym)
    else:
        to_fetch = [s for s, *_ in tickers]

    logger.info(
        f"fetch_universe(region={region}, total={len(tickers)}, "
        f"cached={len(cached)}, to_fetch={len(to_fetch)})"
    )

    if to_fetch:
        with ThreadPoolExecutor(max_workers=FETCH_CONCURRENCY) as ex:
            futures = {ex.submit(_fetch_one_sync, sym): sym for sym in to_fetch}
            for i, fut in enumerate(as_completed(futures)):
                sym, df, info, err = fut.result()
                if df is not None and not df.empty:
                    out_candles[sym] = df
                    out_info[sym] = info
                    _save_candles(sym, df)
                    _save_info(sym, info)
                    _record_fetch(sym, True)
                    fetched.append(sym)
                else:
                    out_errors[sym] = err or "no data"
                    _record_fetch(sym, False, err)
                if RATE_LIMIT_DELAY > 0 and (i + 1) % FETCH_CONCURRENCY == 0:
                    time.sleep(RATE_LIMIT_DELAY)

    _save_universe_meta(tickers)

    duration = round(time.time() - start, 2)
    return {
        "candles": out_candles,
        "info": out_info,
        "errors": out_errors,
        "cached": cached,
        "fetched": fetched,
        "stats": {
            "total": len(tickers),
            "fetched": len(fetched),
            "cached": len(cached),
            "errors": len(out_errors),
            "duration_s": duration,
            "cache_hit_rate": round(len(cached) / max(len(tickers), 1) * 100, 1),
        },
    }

# ----------------------------------------------------------------------
# DB HELPERS
# ----------------------------------------------------------------------


def get_universe_from_db() -> List[Dict[str, str]]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT symbol, name, sector, country, currency, exchange FROM tickers"
        ).fetchall()
        return [
            {
                "symbol": s,
                "name": n,
                "sector": sec,
                "country": c,
                "currency": ccy,
                "exchange": ex,
            }
            for s, n, sec, c, ccy, ex in rows
        ]
    finally:
        conn.close()


def clear_cache(symbol: Optional[str] = None) -> int:
    conn = _get_conn()
    try:
        if symbol:
            cur = conn.execute("DELETE FROM daily_candles WHERE symbol = ?", (symbol,))
            conn.execute("DELETE FROM ticker_info WHERE symbol = ?", (symbol,))
            conn.execute("DELETE FROM fetch_status WHERE symbol = ?", (symbol,))
        else:
            cur = conn.execute("DELETE FROM daily_candles")
            conn.execute("DELETE FROM ticker_info")
            conn.execute("DELETE FROM fetch_status")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_cache_stats() -> Dict[str, Any]:
    conn = _get_conn()
    try:
        candles_count = conn.execute("SELECT COUNT(*) FROM daily_candles").fetchone()[0]
        tickers_count = conn.execute("SELECT COUNT(*) FROM tickers").fetchone()[0]
        info_count = conn.execute("SELECT COUNT(*) FROM ticker_info").fetchone()[0]
        rows = conn.execute(
            "SELECT last_success FROM fetch_status ORDER BY last_success DESC"
        ).fetchall()
        now = datetime.utcnow()
        fresh = 0
        stale = 0
        for (ts,) in rows:
            if not ts:
                continue
            try:
                age_h = (now - datetime.fromisoformat(ts)).total_seconds() / 3600
                if age_h < CACHE_TTL_HOURS:
                    fresh += 1
                else:
                    stale += 1
            except Exception:
                continue
        return {
            "candles_rows": candles_count,
            "tickers": tickers_count,
            "info_rows": info_count,
            "fresh": fresh,
            "stale": stale,
            "ttl_hours": CACHE_TTL_HOURS,
            "db_path": DB_PATH,
        }
    finally:
        conn.close()


def fetch_single(symbol: str, force_refresh: bool = False) -> Optional[pd.DataFrame]:
    """Fetch (or read from cache) the daily candles for ONE symbol.

    Returns the DataFrame or None.  Never touches the whole universe.
    """
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return None
    if not force_refresh and _is_cache_fresh(symbol, CACHE_TTL_HOURS):
        df = _load_cached_candles(symbol)
        if df is not None and not df.empty:
            return df
    _, df, info, err = _fetch_one_sync(symbol)
    if df is not None and not df.empty:
        _save_candles(symbol, df)
        _save_info(symbol, info)
        _record_fetch(symbol, True, "")
        return df
    _record_fetch(symbol, False, err)
    return None


__all__ = [
    "init_db",
    "fetch_universe",
    "fetch_single",
    "get_universe_from_db",
    "clear_cache",
    "get_cache_stats",
    "CACHE_TTL_HOURS",
    "FETCH_CONCURRENCY",
    "CANDLE_LIMIT",
    "DB_PATH",
]
