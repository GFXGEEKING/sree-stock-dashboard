"""Strategy module: multi-region stock universe, scoring, and rankings.

v1.1 — Top-25 ready:
* expanded universe (curated real symbols from services/universe.py)
* parallel scoring via ThreadPoolExecutor (8 workers)
* 5-minute in-memory result cache to stay kind to yfinance rate limits
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf

from .services import universe as uni
from .services import scoring as scoring_engine
from .services import market_hours

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Universe: curated real yfinance symbols per region (subset of the
# 3,271-ticker master list in services/universe.py)
# ----------------------------------------------------------------------

# Delisted / renamed symbols that yfinance no longer serves
_KNOWN_BAD = {"1COV.DE", "DAI.DE"}


def _syms(rows, n=None):
    out = [r[0] for r in rows if r[0] not in _KNOWN_BAD]
    return out[:n] if n else out


_GERMANY = _syms(uni.DAX40)                                     # ~38 real symbols
_EUROPE = (
    _syms(uni.FTSE100, 15)
    + _syms(uni.CAC40, 10)
    + _syms(uni.AEX25, 5)
    + _syms(uni.IBEX35, 5)
    + _syms(uni.FTSEMIB, 5)
)                                                                # ~40
_USA = _syms(uni.SP500_TOP, 40)                                  # 40
_ALL = _GERMANY[:15] + _EUROPE[:15] + _USA[:15]                 # 45

STOCK_UNIVERSE = {
    "Germany": _GERMANY,
    "Europe": _EUROPE,
    "USA": _USA,
}

ALL_TICKERS = _ALL  # used when region is None or "All"

_TICKER_REGION: Dict[str, str] = {}
for _r, _ts in STOCK_UNIVERSE.items():
    for _t in _ts:
        _TICKER_REGION[_t] = _r
for _t in _GERMANY[:15]:
    _TICKER_REGION[_t] = "Germany"
for _t in _EUROPE[:15]:
    _TICKER_REGION[_t] = "Europe"
for _t in _USA[:15]:
    _TICKER_REGION[_t] = "USA"

# ----------------------------------------------------------------------
# Result cache (so the 60s auto-refresh doesn't hammer yfinance)
# ----------------------------------------------------------------------

_CACHE_TTL = float(os.getenv("RANK_CACHE_SECONDS", "300"))
_CACHE: Dict[str, List[Dict[str, Any]]] = {}
_CACHE_TS: Dict[str, float] = {}
_cache_lock = threading.Lock()


# ----------------------------------------------------------------------
# Names
# ----------------------------------------------------------------------

_SHORT_NAMES = {
    "ALV.DE": "Allianz", "SAP.DE": "SAP SE", "SY1.DE": "Symrise",
    "BMW.DE": "BMW", "BAYN.DE": "Bayer", "DBK.DE": "Deutsche Bank",
    "DB1.DE": "Deutsche Boerse", "FRE.DE": "Fresenius", "MTX.DE": "MTU Aero",
    "ASML.AS": "ASML", "TTE.PA": "TotalEnergies", "AZN.L": "AstraZeneca",
    "AAPL": "Apple", "MSFT": "Microsoft", "GOOG": "Alphabet",
    "AMZN": "Amazon", "TSLA": "Tesla", "NVDA": "NVIDIA",
    "META": "Meta", "BRK-B": "Berkshire", "JPM": "JPMorgan",
    "JNJ": "Johnson & Johnson",
}


def get_stock_name(ticker: str) -> str:
    if ticker in _SHORT_NAMES:
        return _SHORT_NAMES[ticker]
    m = uni.TICKER_META.get(ticker)
    if m and m.get("name"):
        return m["name"]
    return ticker


# ----------------------------------------------------------------------
# Scoring helpers
# ----------------------------------------------------------------------


def _compute_momentum(prices: pd.Series, months: int) -> float:
    """Compute momentum as % change over `months` trading months (~21 days/month)."""
    prices = prices.dropna()
    if len(prices) < 2:
        return 0.0
    days_per_month = 21.0
    lookback = int(months * days_per_month)
    actual_lookback = min(lookback, len(prices) - 1)
    if actual_lookback < 1:
        return 0.0
    start = float(prices.iloc[-actual_lookback - 1])
    end = float(prices.iloc[-1])
    if start == 0:
        return 0.0
    return (end - start) / start


def _value_metrics(info: Dict[str, Any]) -> tuple:
    pe = info.get("forwardPE", 0) or 0
    roe = info.get("returnOnEquity", 0) or 0
    if roe and roe > 1:
        roe = roe / 100.0
    return pe, roe


# ----------------------------------------------------------------------
# Sector ETF + benchmark context (feeds the dead score_sector_etf comp.)
# ----------------------------------------------------------------------

# yfinance sector names -> liquid sector ETFs (3-month momentum proxy)
_SECTOR_ETFS = {
    "Technology": "XLK", "Financial Services": "XLF", "Healthcare": "XLV",
    "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP", "Industrials": "XLI",
    "Basic Materials": "XLB", "Energy": "XLE", "Real Estate": "XLRE",
    "Communication Services": "XLC", "Utilities": "XLU",
}

_REGION_BENCHMARK = {"Germany": "DE", "Europe": "EU", "USA": "US"}

_context_cache: Dict[str, Any] = {"ts": 0.0, "sectors": {}, "benchmarks": {}}
_CONTEXT_TTL_S = 3600  # refresh sector/benchmark changes hourly


def _sector_benchmark_context() -> Dict[str, Any]:
    """3-month changes for sector ETFs + regional benchmarks (cached 1h)."""
    import time as _t

    now = _t.time()
    if now - float(_context_cache.get("ts") or 0) < _CONTEXT_TTL_S:
        return _context_cache
    try:
        import numpy as np
        import yfinance as yf
        from datetime import timedelta as _td

        def _chg3m(sym: str):
            try:
                h = yf.Ticker(sym).history(period="6mo")
                if h is None or len(h) < 70:
                    return None
                c = h["Close"].dropna()
                if len(c) < 64:
                    return None
                return float(c.iloc[-1] / c.iloc[-64] - 1.0)
            except Exception:
                return None

        sectors = {}
        for name, etf in _SECTOR_ETFS.items():
            chg = _chg3m(etf)
            if chg is not None:
                sectors[name] = chg
        benchmarks = {}
        for region, code in _REGION_BENCHMARK.items():
            from .services.benchmark import BENCHMARKS

            chg = _chg3m(BENCHMARKS[code])
            if chg is not None:
                benchmarks[region] = chg
        _context_cache.update(
            {"ts": now, "sectors": sectors, "benchmarks": benchmarks}
        )
    except Exception as e:
        logger.debug(f"sector/benchmark context failed: {e}")
        _context_cache["ts"] = now  # don't retry-hammer; try again in 1h
    return _context_cache


def score_stock(ticker: str, region: str, hist: pd.DataFrame, info: Dict[str, Any]) -> Dict[str, Any]:
    name = get_stock_name(ticker)
    close = hist["Close"] if not hist.empty else pd.Series()
    close = close.dropna() if not close.empty else close

    mom_3m = _compute_momentum(close, 3) if not close.empty else 0.0
    mom_6m = _compute_momentum(close, 6) if not close.empty else 0.0
    forward_pe, roe = _value_metrics(info)

    # Composite: 50% momentum avg, 30% PE-inverse, 20% ROE
    mom_avg = (mom_3m + mom_6m) / 2.0
    pe_term = (1.0 / min(forward_pe, 50.0)) if forward_pe and forward_pe > 0 else 0.0
    roe_norm = max(0.0, min(1.0, roe)) if roe else 0.0
    composite = 0.5 * mom_avg + 0.3 * pe_term + 0.2 * roe_norm

    daily_change = 0.0
    last_price = None
    if not close.empty and len(close) > 1:
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        last_price = last
        if prev != 0:
            daily_change = (last - prev) / prev * 100.0
    elif not close.empty:
        last_price = float(close.iloc[-1])

    return {
        "ticker": ticker,
        "region": region,
        "name": name,
        "momentum_3m": round(mom_3m * 100, 2),
        "momentum_6m": round(mom_6m * 100, 2),
        "forward_pe": float(forward_pe) if forward_pe else 0.0,
        "roe": round(roe * 100, 2) if roe else 0.0,
        "composite_score": round(float(composite), 4),
        "daily_change_pct": round(float(daily_change), 2),
        "price": round(last_price, 2) if last_price else None,
        "current_price": round(last_price, 2) if last_price else None,
        "rank": 0,  # assigned later
    }


def enrich_with_breakout_score(
    entry: Dict[str, Any],
    hist: pd.DataFrame,
    info: Dict[str, Any],
) -> Dict[str, Any]:
    """Add the 100-point breakout score + component breakdown to a scored entry.

    Falls back gracefully: if the scoring engine can't compute (thin data),
    the legacy composite score is kept and breakout fields show 0.

    v2: sector ETF + benchmark changes feed the previously-dead
    score_sector_etf component; ML probability + blended score added.
    """
    try:
        ctx = _sector_benchmark_context()
        sector_name = info.get("sector")
        sector_chg = ctx.get("sectors", {}).get(sector_name)
        bench_chg = ctx.get("benchmarks", {}).get(entry.get("region"))
        result = scoring_engine.score_stock(
            entry["ticker"], hist, info=info,
            sector_change_3m=sector_chg,
            benchmark_change_3m=bench_chg,
        )
        components = result.get("components", {})
        entry["breakout_score"] = result.get("total", 0.0)
        entry["breakout_components"] = components
        entry["signal"] = _signal_from_score(result.get("total", 0.0))
        # RSI value is useful for the UI
        entry["rsi_14"] = _rsi_value(hist)
    except Exception as e:
        logger.debug(f"breakout scoring failed for {entry['ticker']}: {e}")
        entry["breakout_score"] = 0.0
        entry["breakout_components"] = {}
        entry["signal"] = "weak"
        entry["rsi_14"] = None
    return entry


def _signal_from_score(score: float) -> str:
    if score >= 70:
        return "strong"
    if score >= 50:
        return "moderate"
    return "weak"


def _rsi_value(hist: pd.DataFrame, n: int = 14) -> Optional[float]:
    """Extract the latest RSI-14 from a history frame (or None)."""
    try:
        close = hist["Close"].dropna()
        if len(close) < n + 1:
            return None
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(n, min_periods=n).mean()
        loss = (-delta.clip(upper=0)).rolling(n, min_periods=n).mean()
        if loss.iloc[-1] == 0:
            return 100.0 if gain.iloc[-1] > 0 else 50.0
        rs = gain.iloc[-1] / loss.iloc[-1]
        return round(float(100 - (100 / (1 + rs))), 1)
    except Exception:
        return None


# ----------------------------------------------------------------------
# Ranking (parallel + cached)
# ----------------------------------------------------------------------


def _score_one(ticker: str, region: str) -> Optional[Dict[str, Any]]:
    """Score one ticker via the data_fetcher SQLite cache (24h TTL).

    v2: candles now flow through data_fetcher, which populates the
    daily_candles cache — the data source for the backtester, correlation
    filter and ML training labels. Uses a 2h freshness window for the
    scoring path so intraday re-scans see today's forming candle.
    """
    try:
        from .services import data_fetcher as _df

        hist = None
        info: Dict[str, Any] = {}
        try:
            if _df._is_cache_fresh(ticker, 2.0):
                hist = _df._load_cached_candles(ticker)
                info = _df._load_info(ticker) or {}
        except Exception:
            hist = None
        if hist is None or hist.empty:
            hist = _df.fetch_single(ticker, force_refresh=True)
            info = _df._load_info(ticker) or {}
        if hist is None or hist.empty:
            return None
        entry = score_stock(ticker, region, hist, info)
        # Tier-1 upgrade: 100-point breakout score + components
        entry = enrich_with_breakout_score(entry, hist, info)
        return entry
    except Exception as e:
        logger.warning(f"Could not score {ticker}: {e}")
        return None


def _region_tickers(key: str) -> List[str]:
    """Resolve the ticker list for a region cache-key."""
    if key in ("all", ""):
        return ALL_TICKERS
    for name in ("Germany", "Europe", "USA"):
        if key == name.lower():
            return STOCK_UNIVERSE[name]
    return ALL_TICKERS


def rank_stocks(region: Optional[str] = None, limit: int = 25) -> Dict[str, Any]:
    """Score and rank stocks for a given region (or all).

    Results are cached for RANK_CACHE_SECONDS (default 300s) so the
    frontend's 60s auto-refresh stays well within yfinance rate limits.
    """
    key = (region or "all").lower()
    now = time.time()

    with _cache_lock:
        if key in _CACHE and (now - _CACHE_TS.get(key, 0)) < _CACHE_TTL:
            results = list(_CACHE[key])
            return {
                "region": region or "all",
                "total_universe": len(_region_tickers(key)),
                "scanned": len(results),
                "rankings": results[:limit],
                "cached": True,
            }

    tickers = _region_tickers(key)
    logger.info(f"rank_stocks: region={key}, scanning {len(tickers)} tickers…")

    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {
            ex.submit(_score_one, t, _TICKER_REGION.get(t, region or "Unknown")): t
            for t in tickers
        }
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    results.sort(
        key=lambda x: (
            x.get("breakout_score") or 0.0,
            x.get("composite_score") or 0.0,
        ),
        reverse=True,
    )
    for i, r in enumerate(results, 1):
        r["rank"] = i

    # v2 upgrades: ML probability + blended score on every ranked entry
    try:
        from .services import ml_scorer as _ml
        for r in results:
            _ml.enrich(r)
    except Exception as e:
        logger.debug(f"ml enrichment skipped: {e}")

    with _cache_lock:
        _CACHE[key] = list(results)
        _CACHE_TS[key] = time.time()

    # v2: persist a same-day snapshot (score history + ML training data)
    try:
        from .services import scan_history as _sh
        _sh.save_snapshot(results[:50], region=key)
    except Exception as e:
        logger.debug(f"scan snapshot skipped: {e}")

    return {
        "region": region or "all",
        "total_universe": len(tickers),
        "scanned": len(results),
        "rankings": results[:limit],
    }
