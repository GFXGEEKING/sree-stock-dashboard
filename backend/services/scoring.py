"""Composite Breakout Score (0-100) for stocks.

Implements the 9 weighted components from the spec:
  momentum (0-30) + trend (0-20) + volatility (0-15) + volume (0-15)
  + RSI (0-10) + MACD (0-5) + fundamentals (0-10) + sector momentum (+5/-3)
  + sentiment/earnings surprise bonus.

Filter thresholds (pre-score): min market cap, min volume, min price, max beta.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------
# Filter thresholds (beginner-safe defaults)
# ----------------------------------------------------------------------
MIN_MARKET_CAP_M = 500.0      # 500M EUR/USD
MIN_AVG_VOLUME_30D = 200_000
MIN_PRICE = 1.0
MAX_BETA = 3.0


# ----------------------------------------------------------------------
# Helper indicators
# ----------------------------------------------------------------------

def _sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=max(2, n // 2)).mean()


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False, min_periods=1).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).rolling(n, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series) -> tuple:
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    line = ema12 - ema26
    signal = _ema(line, 9)
    hist = line - signal
    return line, signal, hist


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat(
        [(high - low).abs(), (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(n, min_periods=n).mean()
    plus_di = 100 * pd.Series(plus_dm, index=close.index).rolling(n, min_periods=n).sum() / atr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=close.index).rolling(n, min_periods=n).sum() / atr.replace(0, np.nan)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    return dx.rolling(n, min_periods=n).mean()


def _atr(high, low, close, n: int = 14) -> pd.Series:
    tr = pd.concat(
        [(high - low).abs(), (high - close.shift()).abs(), (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume.fillna(0)).cumsum()


def _returns(close: pd.Series, lookbacks: List[int]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if close is None or close.empty:
        return {f"ret_{n}d": 0.0 for n in lookbacks}
    last = float(close.iloc[-1])
    for n in lookbacks:
        if len(close) > n:
            out[f"ret_{n}d"] = (last / float(close.iloc[-n - 1])) - 1.0
        else:
            out[f"ret_{n}d"] = 0.0
    return out


# ----------------------------------------------------------------------
# Sub-score components
# ----------------------------------------------------------------------

def score_momentum(close: pd.Series) -> float:
    """0-30 points from 1m/3m/6m returns and 52w position."""
    rets = _returns(close, [21, 63, 126])
    r1, r3, r6 = rets["ret_21d"], rets["ret_63d"], rets["ret_126d"]
    if len(close) < 252:
        hi, lo = close.max(), close.min()
    else:
        hi, lo = close.tail(252).max(), close.tail(252).min()
    if hi == lo:
        pos52 = 0.5
    else:
        pos52 = (float(close.iloc[-1]) - float(lo)) / (float(hi) - float(lo))
    raw = (r1 * 1.0 + r3 * 0.5 + r6 * 0.3) / 4.0 + pos52 * 10
    return float(np.clip(raw, 0, 30))


def score_trend(close: pd.Series, high: pd.Series, low: pd.Series) -> float:
    """0-20 points: SMA50, SMA200, golden cross, ADX."""
    pts = 0.0
    if len(close) >= 50:
        sma50 = _sma(close, 50).iloc[-1]
        if float(close.iloc[-1]) > float(sma50):
            pts += 5
    if len(close) >= 200:
        sma200 = _sma(close, 200).iloc[-1]
        if float(close.iloc[-1]) > float(sma200):
            pts += 5
    if len(close) >= 200:
        sma50 = _sma(close, 50).iloc[-1]
        sma200 = _sma(close, 200).iloc[-1]
        if float(sma50) > float(sma200):
            pts += 5
    if len(close) >= 30:
        a = _adx(high, low, close).iloc[-1]
        if not math.isnan(a) and a > 25:
            pts += 5
    return float(np.clip(pts, 0, 20))


def score_volatility(close: pd.Series, high: pd.Series, low: pd.Series) -> float:
    """0-15 points: ATR contraction / Bollinger bandwidth contraction."""
    pts = 0.0
    if len(close) >= 60:
        atr = _atr(high, low, close, 14)
        atr_recent = atr.tail(20).mean()
        atr_avg = atr.tail(60).mean()
        if atr_avg > 0 and atr_recent < atr_avg:
            pts += 7
        # Bollinger bandwidth
        sma = _sma(close, 20)
        sd = close.rolling(20).std()
        bw = ((sma + 2 * sd) - (sma - 2 * sd)) / sma
        bw_recent = bw.tail(20).mean()
        bw_avg = bw.tail(60).mean()
        if bw_avg > 0 and bw_recent < bw_avg:
            pts += 8
    return float(np.clip(pts, 0, 15))


def score_volume(close: pd.Series, volume: pd.Series) -> float:
    """0-15 points: volume ratio + OBV slope."""
    pts = 0.0
    if len(close) >= 30 and volume is not None and not volume.empty:
        avg30 = float(volume.tail(30).mean())
        recent = float(volume.iloc[-1])
        if avg30 > 0 and recent > 1.5 * avg30:
            pts += 8
        obv = _obv(close, volume)
        if len(obv) >= 30:
            slope = (float(obv.iloc[-1]) - float(obv.iloc[-30])) / max(abs(float(obv.iloc[-30])), 1)
            if slope > 0.02:
                pts += 7
    return float(np.clip(pts, 0, 15))


def score_rsi(close: pd.Series) -> float:
    """0-10 points: RSI in healthy zone, divergence, overbought penalty."""
    pts = 0.0
    if len(close) < 30:
        return 0.0
    rsi = _rsi(close)
    last = float(rsi.iloc[-1]) if not math.isnan(rsi.iloc[-1]) else 50.0
    if 50 <= last <= 70:
        pts += 3
    if last > 80:
        pts -= 2
    # Bullish divergence: price made lower low, RSI made higher low
    if len(close) >= 60:
        px_recent = close.tail(20)
        px_prior = close.tail(60).head(40)
        rsi_recent = rsi.tail(20)
        rsi_prior = rsi.tail(60).head(40)
        if px_recent.min() < px_prior.min():
            if rsi_recent.min() > rsi_prior.min():
                pts += 5
        if px_recent.max() > px_prior.max():
            if rsi_recent.max() < rsi_prior.max():
                pts -= 3
    return float(np.clip(pts, -5, 10))


def score_macd(close: pd.Series) -> float:
    """0-5 points: bullish/bearish MACD crossover."""
    if len(close) < 35:
        return 0.0
    line, signal, _ = _macd(close)
    if len(line) < 2 or len(signal) < 2:
        return 0.0
    l0, l1 = float(line.iloc[-1]), float(line.iloc[-2])
    s0, s1 = float(signal.iloc[-1]), float(signal.iloc[-2])
    pts = 0.0
    if l1 <= s1 and l0 > s0:
        pts += 5
    if l1 >= s1 and l0 < s0:
        pts -= 3
    return float(np.clip(pts, -3, 5))


def score_fundamentals(info: Dict[str, Any]) -> float:
    """0-10 points: market cap, P/E, avg volume proxy."""
    pts = 0.0
    mc = info.get("marketCap") or info.get("market_cap") or 0
    if isinstance(mc, (int, float)) and mc > 2e9:
        pts += 3
    avg_vol = info.get("averageVolume") or info.get("averageVolume10days") or 0
    if isinstance(avg_vol, (int, float)) and avg_vol > 5_000_000:
        pts += 3
    pe = info.get("trailingPE") or info.get("forwardPE")
    if pe is None or (isinstance(pe, (int, float)) and (pe < 30 or pe < 0)):
        pts += 2
    eg = info.get("earningsGrowth") or info.get("revenueGrowth")
    if isinstance(eg, (int, float)) and eg > 0:
        pts += 2
    return float(np.clip(pts, 0, 10))


def score_sector_etf(sector_change_3m: Optional[float], benchmark_change_3m: Optional[float]) -> float:
    """+5 / -3 bonus if sector ETF outperforming / underperforming benchmark."""
    if sector_change_3m is None or benchmark_change_3m is None:
        return 0.0
    diff = sector_change_3m - benchmark_change_3m
    if diff > 0.02:
        return 5.0
    if diff < -0.02:
        return -3.0
    return 0.0


def score_earnings_surprise(info: Dict[str, Any]) -> float:
    """-2..+2 from earningsSurprises / recent beat."""
    s = info.get("earningsSurprise") or info.get("epsSurprise")
    if isinstance(s, (int, float)):
        return float(np.clip(s * 10, -2, 2))
    return 0.0


# ----------------------------------------------------------------------
# Pre-score filter
# ----------------------------------------------------------------------

def passes_filter(info: Dict[str, Any], df: pd.DataFrame) -> tuple:
    """Return (ok: bool, reason: str)."""
    mc = info.get("marketCap") or 0
    avg_vol = info.get("averageVolume") or 0
    price = float(df["Close"].iloc[-1]) if not df.empty else 0
    if isinstance(mc, (int, float)) and mc < MIN_MARKET_CAP_M * 1e6:
        return False, f"market_cap too low ({mc/1e6:.0f}M)"
    if isinstance(avg_vol, (int, float)) and avg_vol < MIN_AVG_VOLUME_30D:
        return False, f"avg_volume too low ({avg_vol})"
    if price < MIN_PRICE:
        return False, f"price too low ({price:.2f})"
    beta = info.get("beta")
    if isinstance(beta, (int, float)) and beta > MAX_BETA:
        return False, f"beta too high ({beta:.2f})"
    return True, ""


# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------

def score_stock(
    symbol: str,
    df: pd.DataFrame,
    info: Optional[Dict[str, Any]] = None,
    sector_change_3m: Optional[float] = None,
    benchmark_change_3m: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute the composite breakout score (0-100) plus per-component breakdown.

    Returns dict with: symbol, total, components, components_total, filter_ok, reason
    """
    info = info or {}
    if df is None or df.empty or len(df) < 30:
        return {
            "symbol": symbol,
            "total": 0.0,
            "components": {},
            "components_total": 0.0,
            "filter_ok": False,
            "reason": "insufficient data",
        }

    ok, reason = passes_filter(info, df)
    if not ok:
        return {
            "symbol": symbol,
            "total": 0.0,
            "components": {},
            "components_total": 0.0,
            "filter_ok": False,
            "reason": reason,
        }

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"] if "Volume" in df.columns else pd.Series(dtype=float)

    comps = {
        "momentum": round(score_momentum(close), 2),
        "trend": round(score_trend(close, high, low), 2),
        "volatility": round(score_volatility(close, high, low), 2),
        "volume": round(score_volume(close, vol), 2),
        "rsi": round(score_rsi(close), 2),
        "macd": round(score_macd(close), 2),
        "fundamentals": round(score_fundamentals(info), 2),
        "sector_momentum": round(score_sector_etf(sector_change_3m, benchmark_change_3m), 2),
        "earnings_surprise": round(score_earnings_surprise(info), 2),
    }
    total = float(np.clip(sum(comps.values()), 0, 100))
    return {
        "symbol": symbol,
        "total": round(total, 2),
        "components": comps,
        "components_total": round(sum(comps.values()), 2),
        "filter_ok": True,
        "reason": "",
    }


__all__ = [
    "score_stock",
    "score_momentum",
    "score_trend",
    "score_volatility",
    "score_volume",
    "score_rsi",
    "score_macd",
    "score_fundamentals",
    "score_sector_etf",
    "score_earnings_surprise",
    "passes_filter",
    "MIN_MARKET_CAP_M",
    "MIN_AVG_VOLUME_30D",
    "MIN_PRICE",
    "MAX_BETA",
]
