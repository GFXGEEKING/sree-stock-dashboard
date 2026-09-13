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


def _r_multiple(t: Dict) -> Optional[float]:
    """Risk multiple for one closed trade (P&L / initial risk at entry).

    R = net P&L / (|entry - stop| * shares). Longs: stop below entry.
    Shorts (side == 'SELL'): stop above entry. Returns None when the info
    needed to define initial risk is missing (e.g. no stop set).
    """
    pnl = t.get("pnl_eur")
    entry = t.get("entry_price")
    stop = t.get("stop_loss")
    shares = t.get("shares")
    if pnl is None or not entry or not stop or not shares or shares <= 0:
        return None  # no stop = no well-defined initial risk
    side = str(t.get("side", "BUY")).upper()
    if side == "SELL" or side == "SHORT":
        risk_per_share = (stop or 0) - float(entry)
    else:
        risk_per_share = float(entry) - (stop or 0)
    total_risk = risk_per_share * float(shares)
    if total_risk <= 0:
        return None
    return float(pnl) / total_risk


def edge_decay(trades: List[Dict], recent_n: int = 20) -> Dict:
    """Compare the most recent N closed trades against the lifetime average.

    Flags a warning when the recent win-rate trails the lifetime win-rate by a
    meaningful margin AND there are enough recent trades to be meaningful.
    """
    closed = [t for t in trades if t.get("status") == "CLOSED" and t.get("pnl_eur") is not None]
    closed_sorted = sorted(closed, key=lambda x: (x.get("exit_date") or "", x.get("id") or 0))
    recent = closed_sorted[-recent_n:]
    if not closed:
        return {"triggered": False, "n_recent": 0, "recent_win_rate_pct": 0.0,
                "lifetime_win_rate_pct": 0.0, "message": "No closed trades yet."}

    def _wr(tlist):
        if not tlist:
            return 0.0
        wins = sum(1 for t in tlist if (t.get("pnl_eur") or 0) > 0)
        return round(wins / len(tlist) * 100.0, 1)

    lifetime_wr = _wr(closed)
    recent_wr = _wr(recent)
    n_recent = len(recent)
    drop = lifetime_wr - recent_wr
    triggered = n_recent >= 10 and drop >= 15.0
    message = (
        f"Recent {n_recent} trades win-rate {recent_wr}% trails lifetime "
        f"{lifetime_wr}% by {drop:.0f} pts — edge may be decaying."
        if triggered else
        f"Recent {n_recent} trade win-rate {recent_wr}% vs lifetime {lifetime_wr}% — stable."
    )
    return {"triggered": triggered, "n_recent": n_recent,
            "recent_win_rate_pct": recent_wr, "lifetime_win_rate_pct": lifetime_wr,
            "message": message}


def trade_metrics(trades: List[Dict]) -> Dict:
    """Expectancy / profit factor / win-rate from closed trade rows."""
    closed = [t for t in trades if t.get("status") == "CLOSED" and t.get("pnl_eur") is not None]
    if not closed:
        return {
            "closed_trades": 0, "win_rate_pct": 0.0, "expectancy_eur": 0.0,
            "profit_factor": None, "avg_win_eur": 0.0, "avg_loss_eur": 0.0,
            "avg_hold_days": 0.0, "best_eur": 0.0, "worst_eur": 0.0,
            "avg_r_multiple": None, "edge_decay": edge_decay(closed),
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

    # Risk multiples (only where initial risk is well-defined by a stop)
    r_vals = [_r_multiple(t) for t in closed]
    r_vals = [r for r in r_vals if r is not None]
    avg_r = round(sum(r_vals) / len(r_vals), 2) if r_vals else None

    out = {
        "closed_trades": n,
        "win_rate_pct": round(win_rate * 100.0, 1),
        "expectancy_eur": round(expectancy, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_win_eur": round(avg_win, 2),
        "avg_loss_eur": round(avg_loss, 2),
        "avg_hold_days": round(sum((t.get("hold_days") or 0) for t in closed) / n, 1),
        "best_eur": round(max((t["pnl_eur"] or 0) for t in closed), 2),
        "worst_eur": round(min((t["pnl_eur"] or 0) for t in closed), 2),
        "avg_r_multiple": avg_r,
        "edge_decay": edge_decay(closed),
    }
    return out


def _benchmark_overlay(limit: int = 30) -> Dict:
    """Account-vs-S&P-500 daily return overlay built from the performance log.

    Normalizes the account portfolio value and the ^GSPC close to % change from
    the account's first logged day. Fails soft (returns empty series) if the
    log or the benchmark feed is unavailable so the UI degrades gracefully.
    """
    from . import benchmark as bm
    rows = get_performance_history()
    rows = sorted(rows, key=lambda r: r.get("date") or "")
    if len(rows) < 2:
        return {"label": "S&P 500", "account_return_pct": None,
                "benchmark_return_pct": None, "outperform_pct": None,
                "window_days": 0, "series": [], "available": False}
    start_date = rows[0]["date"]
    end_date = rows[-1]["date"]
    acc = [(r["date"], r["portfolio_value"]) for r in rows]

    bench_series = bm.fetch_benchmark("US", period="1y")
    if bench_series is None or bench_series.empty:
        return {"label": "S&P 500", "account_return_pct": None,
                "benchmark_return_pct": None, "outperform_pct": None,
                "window_days": 0, "series": [], "available": False}

    bm_dates = [d.strftime("%Y-%m-%d") for d in bench_series.index]
    bm_vals = list(bench_series.values)
    bm_map = dict(zip(bm_dates, bm_vals))
    bm_acc = [(bm_dates[i], v) for i, v in enumerate(bm_vals)
              if start_date <= bm_dates[i] <= end_date]
    if not bm_acc:
        return {"label": "S&P 500", "account_return_pct": None,
                "benchmark_return_pct": None, "outperform_pct": None,
                "window_days": 0, "series": [], "available": False}
    # Rebase both to % return from start of the overlay window
    acc_start = acc[0][1]
    bm_start = bm_acc[0][1]
    if not acc_start or bm_start <= 0:
        return {"label": "S&P 500", "account_return_pct": None,
                "benchmark_return_pct": None, "outperform_pct": None,
                "window_days": 0, "series": [], "available": False}

    # Merge account rows onto benchmark dates; interpolate account values.
    # Build a daily series of {date, account_return_pct, benchmark_return_pct}
    acc_by_date = dict(acc)
    bm_only_dates = sorted({d for d, _ in bm_acc})
    series = []
    # account has fewer rows than daily benchmark; sample up to `limit` points
    step = max(1, len(bm_only_dates) // limit)
    for d in bm_only_dates[::step]:
        bv = bm_map[d]
        av = acc_by_date.get(d)
        # fill account value gap with the most recent known value
        if av is None:
            for rd, rv in acc:
                if rd <= d:
                    av = rv
                else:
                    break
        if av is None:
            continue
        series.append({
            "date": d,
            "account_return_pct": round((av / acc_start - 1.0) * 100.0, 2),
            "benchmark_return_pct": round((bv / bm_start - 1.0) * 100.0, 2),
        })

    account_return = round((acc[-1][1] / acc_start - 1.0) * 100.0, 2)
    bm_end = bm_acc[-1][1]
    benchmark_return = round((bm_end / bm_start - 1.0) * 100.0, 2)
    return {
        "label": "S&P 500",
        "account_return_pct": account_return,
        "benchmark_return_pct": benchmark_return,
        "outperform_pct": round(account_return - benchmark_return, 2),
        "window_days": (datetime.strptime(end_date, "%Y-%m-%d")
                        - datetime.strptime(start_date, "%Y-%m-%d")).days,
        "series": series,
        "available": True,
    }


def open_position_metrics(open_positions: List[Dict]) -> Dict:
    """Aggregate unrealized stats across OPEN positions (pure function).

    Measures floating P&L and spread of the currently held picks so the
    performance summary covers open as well as closed trades.
    """
    op = open_positions or []
    upnl = [float(p.get("unrealized_pnl") or 0.0) for p in op]
    upct = [float(p["unrealized_pnl_pct"]) for p in op
            if p.get("unrealized_pnl_pct") is not None]
    exposure = sum(
        float(p.get("current_price") or p.get("entry_price") or 0) * float(p.get("shares") or 0)
        for p in op
    )
    winners = sum(1 for v in upnl if v > 0)
    return {
        "open_positions": len(op),
        "unrealized_pnl": round(sum(upnl), 2),
        "avg_move_pct": round(sum(upct) / len(upct), 2) if upct else None,
        "best_pct": round(max(upct), 2) if upct else None,
        "worst_pct": round(min(upct), 2) if upct else None,
        "open_winners": winners,
        "exposure_eur": round(exposure, 2),
    }


def portfolio_metrics() -> Dict:
    """Combine the daily log metrics with trade-level metrics + live snapshot."""
    from . import paper_trade as pt  # lazy to avoid import cycles

    # Mirror /api/paper/portfolio: enrich open positions with latest cached
    # prices so unrealized stats reflect reality (cache-first, per-symbol
    # fallback — never scans the whole universe).
    prices: Dict[str, float] = {}
    try:
        from . import data_fetcher as df_mod
        for p in pt.get_open_positions():
            sym = p.get("symbol")
            if not sym or sym in prices:
                continue
            try:
                frame = df_mod.fetch_single(sym)
                if frame is not None and not frame.empty:
                    prices[sym] = float(frame["Close"].iloc[-1])
            except Exception as e:
                logger.debug(f"price lookup failed for {sym}: {e}")
    except Exception as e:
        logger.debug(f"open-position price enrichment skipped: {e}")

    port = pt.get_portfolio(current_prices=prices)
    hist = get_performance_history()
    return {
        "daily_log": hist[-30:],
        "risk": risk_metrics(),
        "trades": trade_metrics(port.get("trade_history", [])),
        "open": open_position_metrics(port.get("open_positions", [])),
        "benchmark": _benchmark_overlay(),
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
