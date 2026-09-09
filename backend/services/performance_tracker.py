"""Portfolio performance tracking (daily P&L, equity curve, returns).

"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "backend/data/stocks.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS performance_log (
                date TEXT PRIMARY KEY,
                portfolio_value REAL NOT NULL,
                cash REAL NOT NULL,
                pnl REAL NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def log_performance(
    date: str,
    portfolio_value: float,
    cash: float,
    pnl: float,
) -> None:
    init_db()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO performance_log (date, portfolio_value, cash, pnl) "
            "VALUES (?, ?, ?, ?)",
            (date, float(portfolio_value), float(cash), float(pnl)),
        )
        conn.commit()
    finally:
        conn.close()


def get_performance_history() -> List[Dict]:
    init_db()
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM performance_log ORDER BY date").fetchall()
        return [
            {"date": d, "portfolio_value": v, "cash": c, "pnl": p}
            for d, v, c, p in rows
        ]
    finally:
        conn.close()


def latest_portfolio_value() -> Optional[float]:
    rows = get_performance_history()
    return float(rows[-1]["portfolio_value"]) if rows else None


def cumulative_pnl() -> float:
    rows = get_performance_history()
    return float(rows[-1]["pnl"]) if rows else 0.0


# ----------------------------------------------------------------------
# Risk & performance metrics (Phase 2)
# ----------------------------------------------------------------------

import math
import numpy as np


def daily_returns() -> List[float]:
    """Day-over-day portfolio value returns from the performance log."""
    rows = get_performance_history()
    rets: List[float] = []
    for i in range(1, len(rows)):
        prev = rows[i - 1]["portfolio_value"]
        cur = rows[i]["portfolio_value"]
        if prev and prev > 0:
            rets.append((cur / prev) - 1.0)
    return rets


def risk_metrics() -> Dict:
    """Sharpe / Sortino / max drawdown / annualized volatility.

    Sharpe assumes 252 trading days, risk-free rate 0 (paper account).
    Returns Nones when there is not enough data yet.
    """
    rows = get_performance_history()
    rets = daily_returns()
    out = {
        "n_days": len(rows),
        "sharpe": None,
        "sortino": None,
        "volatility_annual_pct": None,
        "max_drawdown_pct": 0.0,
    }
    if not rets:
        return out
    r = np.array(rets, dtype=float)
    mean = float(r.mean())
    std = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    downside = r[r < 0]
    dstd = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    if std > 0:
        out["sharpe"] = round((mean / std) * math.sqrt(252), 2)
        out["volatility_annual_pct"] = round(std * math.sqrt(252) * 100.0, 2)
    if dstd > 0:
        out["sortino"] = round((mean / dstd) * math.sqrt(252), 2)
    vals = [row["portfolio_value"] for row in rows]
    if vals:
        peak = vals[0]
        maxdd = 0.0
        for v in vals:
            peak = max(peak, v)
            if peak > 0:
                maxdd = max(maxdd, (peak - v) / peak)
        out["max_drawdown_pct"] = round(maxdd * 100.0, 2)
    return out


def trade_metrics(trades: List[Dict]) -> Dict:
    """Expectancy / profit factor / win-rate from closed trade rows."""
    closed = [t for t in trades if t.get("status") == "CLOSED" and t.get("pnl_eur") is not None]
    if not closed:
        return {
            "closed_trades": 0, "win_rate_pct": 0.0, "expectancy_eur": 0.0,
            "profit_factor": None, "avg_win_eur": 0.0, "avg_loss_eur": 0.0,
            "avg_hold_days": 0.0, "best_eur": 0.0, "worst_eur": 0.0,
        }
    wins = [t for t in closed if (t.get("pnl_eur") or 0) > 0]
    losses = [t for t in closed if (t.get("pnl_eur") or 0) <= 0]
    gross_win = sum((t["pnl_eur"] or 0) for t in wins)
    gross_loss = abs(sum((t["pnl_eur"] or 0) for t in losses))
    n = len(closed)
    win_rate = len(wins) / n
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = -gross_loss / len(losses) if losses else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    return {
        "closed_trades": n,
        "win_rate_pct": round(win_rate * 100.0, 1),
        "expectancy_eur": round(expectancy, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_win_eur": round(avg_win, 2),
        "avg_loss_eur": round(avg_loss, 2),
        "avg_hold_days": round(sum((t.get("hold_days") or 0) for t in closed) / n, 1),
        "best_eur": round(max((t["pnl_eur"] or 0) for t in closed), 2),
        "worst_eur": round(min((t["pnl_eur"] or 0) for t in closed), 2),
    }


def portfolio_metrics() -> Dict:
    """Combine the daily log metrics with trade-level metrics + live snapshot."""
    from . import paper_trade as pt  # lazy to avoid import cycles

    port = pt.get_portfolio()
    hist = get_performance_history()
    return {
        "daily_log": hist[-30:],
        "risk": risk_metrics(),
        "trades": trade_metrics(port.get("trade_history", [])),
        "current": {
            "equity": port["stats"]["equity"],
            "total_return_pct": port["stats"]["total_return_pct"],
            "unrealized_pnl": port["stats"]["unrealized_pnl"],
            "realized_pnl": port["stats"]["realized_pnl"],
            "open_positions": port["stats"]["open_positions"],
        },
    }


__all__ = [
    "init_db", "log_performance", "get_performance_history",
    "latest_portfolio_value", "cumulative_pnl", "DB_PATH",
    "daily_returns", "risk_metrics", "trade_metrics", "portfolio_metrics",
]
