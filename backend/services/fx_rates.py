"""FX rate fetching and currency conversion.

All prices stored in their original currency.  P&L, totals and position
sizing are reported in the base currency (default EUR).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

import yfinance as yf

from .universe import TICKER_CURRENCY

logger = logging.getLogger(__name__)

DB_PATH = "backend/data/stocks.db"
BASE_CURRENCY = "EUR"
CACHE_TTL_HOURS = 6

_FALLBACK_RATES_EUR: Dict[str, float] = {
    "EURUSD": 1.09,
    "EURGBP": 0.86,
    "EUREUR": 1.0,
}
_FALLBACK_RATES_GBP: Dict[str, float] = {
    "GBPUSD": 1.27,
    "GBPEUR": 1.16,
    "GBPGBP": 1.0,
}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fx_rates (
                pair TEXT PRIMARY KEY,
                rate REAL NOT NULL,
                last_update TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------
# FETCHING
# ----------------------------------------------------------------------


def _fetch_yf_pair(pair: str) -> Optional[float]:
    try:
        t = yf.Ticker(pair)
        hist = t.history(period="5d")
        if hist is None or hist.empty:
            return None
        return float(hist["Close"].dropna().iloc[-1])
    except Exception as e:
        logger.debug(f"FX fetch failed for {pair}: {e}")
        return None


def fetch_fx_rates(force: bool = False) -> Dict[str, float]:
    init_db()
    rates: Dict[str, float] = {}
    pairs = {
        "EURUSD": "EURUSD=X",
        "EURGBP": "EURGBP=X",
        "GBPUSD": "GBPUSD=X",
    }
    conn = _get_conn()
    try:
        now = datetime.utcnow()
        for name, yf_sym in pairs.items():
            cached = None
            row = conn.execute(
                "SELECT rate, last_update FROM fx_rates WHERE pair = ?", (name,)
            ).fetchone()
            if row and not force:
                try:
                    age = now - datetime.fromisoformat(row[1])
                    if age < timedelta(hours=CACHE_TTL_HOURS):
                        cached = row[0]
                except Exception:
                    pass
            if cached is not None:
                rates[name] = float(cached)
                continue
            fetched = _fetch_yf_pair(yf_sym)
            if fetched is None:
                if name == "EURUSD":
                    fetched = _FALLBACK_RATES_EUR["EURUSD"]
                elif name == "EURGBP":
                    fetched = _FALLBACK_RATES_EUR["EURGBP"]
                elif name == "GBPUSD":
                    fetched = _FALLBACK_RATES_GBP["GBPUSD"]
            conn.execute(
                "INSERT OR REPLACE INTO fx_rates (pair, rate, last_update) "
                "VALUES (?, ?, ?)",
                (name, float(fetched), now.isoformat()),
            )
            rates[name] = float(fetched)
            time.sleep(0.2)
        conn.commit()
    finally:
        conn.close()
    return rates


def get_rates() -> Dict[str, float]:
    init_db()
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT pair, rate FROM fx_rates").fetchall()
        if not rows:
            return fetch_fx_rates(force=True)
        return {p: float(r) for p, r in rows}
    finally:
        conn.close()


# ----------------------------------------------------------------------
# CONVERSION
# ----------------------------------------------------------------------


def to_eur(amount, currency, rates=None):
    """Convert amount denominated in currency to EUR."""
    if amount is None:
        return 0.0
    cur = (currency or "EUR").upper()
    if cur == "EUR":
        return float(amount)
    rates = rates or get_rates()
    if cur == "USD":
        eurusd = rates.get("EURUSD") or _FALLBACK_RATES_EUR["EURUSD"]
        return float(amount) / float(eurusd)
    if cur == "GBP":
        eurgbp = rates.get("EURGBP") or _FALLBACK_RATES_EUR["EURGBP"]
        return float(amount) / float(eurgbp)
    return float(amount)


def to_base(amount, currency, base=BASE_CURRENCY):
    return to_eur(amount, currency) if base == "EUR" else float(amount)


def from_eur(amount_eur, target_currency, rates=None):
    if amount_eur is None:
        return 0.0
    cur = (target_currency or "EUR").upper()
    if cur == "EUR":
        return float(amount_eur)
    rates = rates or get_rates()
    if cur == "USD":
        eurusd = rates.get("EURUSD") or _FALLBACK_RATES_EUR["EURUSD"]
        return float(amount_eur) * float(eurusd)
    if cur == "GBP":
        eurgbp = rates.get("EURGBP") or _FALLBACK_RATES_EUR["EURGBP"]
        return float(amount_eur) * float(eurgbp)
    return float(amount_eur)


def ticker_currency(symbol):
    return TICKER_CURRENCY.get(symbol, "EUR")


def currency_for_region(region):
    return {"DE": "EUR", "EU": "EUR", "US": "USD"}.get((region or "").upper(), "EUR")


__all__ = [
    "init_db",
    "fetch_fx_rates",
    "get_rates",
    "to_eur",
    "from_eur",
    "to_base",
    "ticker_currency",
    "currency_for_region",
    "BASE_CURRENCY",
    "DB_PATH",
]
