"""Position sizing strategies.

Supports fixed-fractional, risk-based, and volatility-adjusted sizing.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------

DEFAULT_MAX_POSITION_PCT = 0.05       # 5% of portfolio per name
DEFAULT_MAX_SECTOR_PCT = 0.20         # 20% per sector
DEFAULT_RISK_PER_TRADE = 0.02         # 2% of portfolio at risk


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _atr(close: pd.Series, high: pd.Series, low: pd.Series, n: int = 14) -> pd.Series:
    tr = pd.concat(
        [(high - low).abs(), (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


# ----------------------------------------------------------------------
# Strategies
# ----------------------------------------------------------------------

def fixed_fractional(
    portfolio_value: float,
    price: float,
    max_pct: float = DEFAULT_MAX_POSITION_PCT,
) -> int:
    """Return number of shares capped at max_pct of portfolio."""
    if price <= 0:
        return 0
    max_value = portfolio_value * max_pct
    shares = int(max_value / price)
    return max(shares, 0)


def risk_based(
    portfolio_value: float,
    price: float,
    entry_price: float,
    stop_loss: float,
    risk_per_trade: float = DEFAULT_RISK_PER_TRADE,
) -> int:
    """Size so that a stop-out at stop_loss loses <= risk_per_trade of portfolio."""
    if price <= 0 or stop_loss <= 0 or entry_price <= stop_loss:
        return 0
    risk_per_share = entry_price - stop_loss
    if risk_per_share <= 0:
        return 0
    risk_value = portfolio_value * risk_per_trade
    shares = int(risk_value / risk_per_share)
    return max(shares, 0)


def volatility_adjusted(
    portfolio_value: float,
    price: float,
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    atr_multiplier: float = 2.0,
    target_risk_pct: float = 0.02,
) -> int:
    """Size inversely proportional to recent volatility (ATR)."""
    if len(close) < 20 or price <= 0:
        return fixed_fractional(portfolio_value, price)
    atr = float(_atr(close, high, low).iloc[-1])
    if atr <= 0:
        return fixed_fractional(portfolio_value, price)
    risk_value = portfolio_value * target_risk_pct
    shares = int(risk_value / (atr * atr_multiplier))
    return max(shares, 0)


def kelly_criterion(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    portfolio_value: float,
    price: float,
    max_fraction: float = 0.25,
) -> int:
    """Full Kelly then half-Kelly for safety."""
    if avg_loss == 0 or avg_loss <= 0:
        return 0
    b = avg_win / abs(avg_loss)
    p = win_rate
    kelly = (b * p - (1 - p)) / b if b > 0 else 0
    kelly = max(kelly, 0)  # never short
    fraction = kelly * 0.5  # half-Kelly
    fraction = min(fraction, max_fraction)
    value = portfolio_value * fraction
    return int(value / price) if price > 0 else 0


def compute_position(
    portfolio_value: float,
    symbol: str,
    price: float,
    score: float,
    close: Optional[pd.Series] = None,
    high: Optional[pd.Series] = None,
    low: Optional[pd.Series] = None,
    strategy: str = "risk_based",
    **kwargs,
) -> Dict:
    """Compute optimal position size based on score and strategy.

    Higher scores get larger allocations (up to max).
    """
    # Base allocation scales with score: 0-100 maps to 0 - max_pct
    max_pct = DEFAULT_MAX_POSITION_PCT
    base_pct = (score / 100.0) * max_pct
    shares = int((portfolio_value * base_pct) / price) if price > 0 else 0

    # Apply strategy overlay
    if strategy == "risk_based" and close is not None:
        entry = price
        stop = kwargs.get("stop_loss") or float(close.iloc[-1] * 0.90)
        shares = risk_based(
            portfolio_value, price, entry, stop,
            kwargs.get("risk_per_trade", DEFAULT_RISK_PER_TRADE),
        )
    elif strategy == "volatility" and close is not None and high is not None and low is not None:
        shares = volatility_adjusted(
            portfolio_value, price, close, high, low,
            kwargs.get("atr_multiplier", 2.0),
            kwargs.get("target_risk_pct", DEFAULT_RISK_PER_TRADE),
        )

    allocation = shares * price if shares > 0 else 0.0
    allocation_pct = allocation / portfolio_value if portfolio_value > 0 else 0.0

    return {
        "symbol": symbol,
        "price": price,
        "shares": shares,
        "allocation": round(allocation, 2),
        "allocation_pct": round(allocation_pct, 4),
        "score": score,
        "strategy": strategy,
    }


__all__ = [
    "fixed_fractional",
    "risk_based",
    "volatility_adjusted",
    "kelly_criterion",
    "compute_position",
    "DEFAULT_MAX_POSITION_PCT",
    "DEFAULT_MAX_SECTOR_PCT",
    "DEFAULT_RISK_PER_TRADE",
]
