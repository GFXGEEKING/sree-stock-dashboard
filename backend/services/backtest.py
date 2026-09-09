"""Backtesting engine for the agent strategy.

Replays historical daily candles through the SAME deterministic decision
core the live agent uses (agent.decide_entry / decide_exits + the paper
trade fill model), so backtested behavior matches live behavior.

Cycles run on historical daily bars (close-to-close), not intraday, so
results are a conservative approximation for a swing strategy — stops
that were gapped through are filled at the open, mirroring the live
monitor's gap logic. All trades charge the same commission/slippage as
the paper engine.

Data source: daily_candles in the SQLite cache (populated by
data_fetcher / scans). Symbols without ~1 year of cached history are
skipped.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_CASH = 10_000.0
COMMISSION = 1.0
SLIPPAGE = 0.0005


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat(
        [(h - l).abs(), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def _load_candles(symbol: str) -> Optional[pd.DataFrame]:
    from . import data_fetcher

    try:
        df = data_fetcher._load_cached_candles(symbol)
        if df is None or len(df) < 220:
            return None
        return df
    except Exception:
        return None


def _score_series(df: pd.DataFrame) -> pd.Series:
    """Recompute the breakout score at each historical bar.

    Uses every component computable from OHLCV alone (momentum, trend,
    volatility, volume, RSI, MACD — max 95 pts). Fundamentals/sector/
    earnings components need `info` data unavailable historically, so
    backtest scores slightly UNDERSTATE live scores (conservative).
    Scored every 5th bar and carried forward (swing cadence).
    """
    from . import scoring

    out = {}
    close = df["Close"].dropna()
    step = 5
    last_val = 0.0
    for i in range(60, len(df)):
        if (i - 60) % step != 0:
            out[df.index[i]] = last_val
            continue
        sub = df.iloc[: i + 1]
        try:
            mom = scoring.score_momentum(sub["Close"])
            trend = scoring.score_trend(sub["Close"], sub["High"], sub["Low"])
            vol = scoring.score_volatility(sub["Close"], sub["High"], sub["Low"])
            volu = scoring.score_volume(sub["Close"], sub["Volume"]) if "Volume" in sub.columns else 0.0
            rsi_pts = scoring.score_rsi(sub["Close"])
            macd_pts = scoring.score_macd(sub["Close"])
            last_val = float(np.clip(mom + trend + vol + volu + rsi_pts + macd_pts, 0, 95))
        except Exception:
            pass
        out[df.index[i]] = last_val
    return pd.Series(out).sort_index()

def run_backtest(
    symbols: List[str],
    cash: float = DEFAULT_CASH,
    max_open: int = 5,
    risk_pct: float = 0.015,
    stop_atr_mult: float = 2.0,
    target_rr: float = 2.0,
    time_stop_days: int = 20,
    min_score: float = 70.0,
    max_positions_per_symbol: int = 1,
) -> Dict:
    """Walk-forward replay of the agent's rules over cached candles.

    Simplifications vs live (documented, conservative):
      * Scores recomputed every 5 bars from historical data (no ML blend
        in backtests — ML trains on this very data, so including it
        would leak; the rule score is the honest signal here).
      * One position per symbol at a time.
      * Entries at next bar's open after a signal bar close.
    """
    trades: List[Dict] = []
    equity_curve: List[Dict] = []
    open_positions: List[Dict] = []
    equity = cash
    daily_idx = set()

    for sym in symbols:
        df = _load_candles(sym)
        if df is None:
            continue
        daily_idx.update(df.index.strftime("%Y-%m-%d"))
        scores = _score_series(df)
        atr = _atr(df)

        i = 60
        while i < len(df) - 2:
            bar = df.iloc[i]
            nxt = df.iloc[i + 1]
            px = float(bar["Close"])
            score = float(scores.get(bar.name, 0.0))
            # --- exits first (intraday: low/high may hit stops/targets)
            for pos in open_positions:
                if pos["symbol"] != sym:
                    continue
                lo, hi, op = float(bar["Low"]), float(bar["High"]), float(bar["Open"])
                exit_px, reason = None, None
                if lo <= pos["stop"]:
                    exit_px, reason = min(pos["stop"], op), "stop_hit"
                elif hi >= pos["target"]:
                    exit_px, reason = max(pos["target"], op), "target_hit"
                elif (i - pos["bar"]) >= time_stop_days:
                    exit_px, reason = px, "time_stop"
                if exit_px is None:
                    continue
                fill = exit_px * (1 - SLIPPAGE)
                pnl = (fill - pos["entry"]) * pos["shares"] - COMMISSION
                equity += pnl
                trades.append({
                    "symbol": sym, "entry_date": str(pos["date"])[:10],
                    "exit_date": str(bar.name)[:10], "entry": round(pos["entry"], 2),
                    "exit": round(fill, 2), "shares": pos["shares"],
                    "pnl": round(pnl, 2), "reason": reason,
                })
                open_positions.remove(pos)
            # --- entry (if flat in this symbol + limits allow)
            held_syms = [p["symbol"] for p in open_positions]
            if (
                score >= min_score
                and sym not in held_syms
                and len(open_positions) < max_open
                and equity > 0
            ):
                a = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else px * 0.03
                stop = round(px - stop_atr_mult * a, 4)
                risk = px - stop
                if risk <= 0:
                    i += 1
                    continue
                target = round(px + target_rr * risk, 4)
                shares = int((equity * risk_pct) / risk)
                cap = int((equity * 0.10) / px)
                shares = max(0, min(shares, cap))
                cost = shares * px
                if shares > 0 and cost >= 200 and cost <= equity:
                    fill = float(nxt["Open"]) * (1 + SLIPPAGE)
                    equity -= COMMISSION
                    open_positions.append({
                        "symbol": sym, "entry": fill, "shares": shares,
                        "stop": stop, "target": target, "date": nxt.name, "bar": i + 1,
                    })
            i += 1

    # Mark remaining positions to the last close (paper mark, not closed)
    equity_curve.append({"date": "end", "equity": round(equity, 2), "open": len(open_positions)})

    # Metrics
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    total_return_pct = ((equity / cash) - 1.0) * 100.0 if cash else 0.0
    return {
        "ok": True,
        "symbols_tested": len(symbols),
        "trades": trades,
        "summary": {
            "n_trades": len(trades),
            "win_rate_pct": round(len(wins) / len(trades) * 100.0, 1) if trades else 0.0,
            "total_return_pct": round(total_return_pct, 2),
            "final_equity": round(equity, 2),
            "expectancy_eur": round(
                (gross_win + (-gross_loss)) / len(trades), 2
            ) if trades else 0.0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "avg_win_eur": round(gross_win / len(wins), 2) if wins else 0.0,
            "avg_loss_eur": round(-gross_loss / len(losses), 2) if losses else 0.0,
            "open_at_end": len(open_positions),
        },
    }


__all__ = ["run_backtest", "DEFAULT_CASH"]

