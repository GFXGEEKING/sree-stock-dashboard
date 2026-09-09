"""Benchmark index fetching and relative-strength helpers.

Provides access to the ^GSPC / ^STOXX50E / ^GDAXI / ^FTSE benchmarks,
plus relative-strength (RS) ratios against them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Benchmark mapping by region
BENCHMARKS: Dict[str, str] = {
    "DE": "^GDAXI",
    "EU": "^STOXX50E",
    "UK": "^FTSE",
    "US": "^GSPC",
}


# ----------------------------------------------------------------------
# Fetching
# ----------------------------------------------------------------------

def fetch_benchmark(
    region: str = "US",
    period: str = "1y",
) -> Optional[pd.Series]:
    """Fetch benchmark close series for region."""
    sym = BENCHMARKS.get((region or "US").upper(), "^GSPC")
    try:
        t = yf.Ticker(sym)
        hist = t.history(period=period)
        if hist is None or hist.empty:
            return None
        return hist["Close"]
    except Exception as e:
        logger.warning(f"Could not fetch {sym}: {e}")
        return None


def fetch_benchmarks(
    regions: Optional[list] = None,
    period: str = "1y",
) -> Dict[str, pd.Series]:
    """Fetch several benchmarks at once."""
    regions = regions or list(BENCHMARKS.keys())
    out: Dict[str, pd.Series] = {}
    for r in regions:
        s = fetch_benchmark(r, period)
        if s is not None:
            out[r] = s
    return out


def benchmark_change(
    region: str = "US",
    lookback_days: int = 63,
    period: str = "1y",
) -> float:
    """Return benchmark % change over lookback_days."""
    s = fetch_benchmark(region, period)
    if s is None or len(s) < lookback_days:
        return 0.0
    return float((s.iloc[-1] / s.iloc[-lookback_days]) - 1.0)


def fetch_all_benchmark_changes(
    lookback_days: int = 63,
    period: str = "1y",
) -> Dict[str, float]:
    """Return {region: pct_change} for all benchmarks."""
    out: Dict[str, float] = {}
    for r in BENCHMARKS:
        out[r] = benchmark_change(r, lookback_days, period)
    return out


# ----------------------------------------------------------------------
# Relative strength
# ----------------------------------------------------------------------

def relative_strength(
    stock_series: pd.Series,
    benchmark_series: pd.Series,
) -> float:
    """Return the slope of RS ratio (stock / benchmark).

    A positive RS ratio slope means stock outperforming benchmark.
    """
    if stock_series is None or benchmark_series is None:
        return 0.0
    n = min(len(stock_series), len(benchmark_series))
    if n < 20:
        return 0.0
    s = stock_series.iloc[-n:]
    b = benchmark_series.iloc[-n:]
    rs = s / b.replace(0, float("nan"))
    rs = rs.dropna()
    if len(rs) < 20:
        return 0.0
    # slope of RS over last 20 days
    x = list(range(len(rs)))
    y = list(rs.values)
    slope = float(__import__("numpy").polyfit(x, y, 1)[0])
    return slope


__all__ = [
    "BENCHMARKS",
    "fetch_benchmark",
    "fetch_benchmarks",
    "benchmark_change",
    "fetch_all_benchmark_changes",
    "relative_strength",
]
