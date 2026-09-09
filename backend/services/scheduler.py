"""Background scheduler: server-side stop/target monitoring + daily snapshots.

Runs inside the FastAPI/uvicorn process via APScheduler (already a project
dependency). Fixes the biggest paper-trading gap: previously stops/targets
only executed when a browser tab polled /api/paper/portfolio. Now fills
happen even with no UI open.

Jobs:
  1. monitor_positions (every MONITOR_INTERVAL_MIN)
       fetch prices + day stats for held symbols, apply trailing stops,
       execute stop/target/time exits with realistic intraday fills.
  2. daily_snapshot (hourly tick, guarded to once per UTC day)
       persist equity point to performance_log (powers Sharpe/DD metrics).
  3. scan_history_job (every SCAN_SNAPSHOT_MIN)
       persist rankings snapshot so score evolution, SCORE_DROP alerts and
       ML training data accumulate over time.
  4. agent_cycle (every 15 min) — Phase 3 autonomous agent (no-op until
       the agent is enabled by the user).
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

MONITOR_INTERVAL_MIN = float(os.getenv("MONITOR_INTERVAL_MIN", "2"))
SCAN_SNAPSHOT_MIN = float(os.getenv("SCAN_SNAPSHOT_MIN", "30"))
SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "1") not in ("0", "false", "False")

_scheduler = None  # BackgroundScheduler once started
_lock = threading.Lock()
_last_snapshot_day: Optional[str] = None  # once-per-day guard
_status = {
    "running": False,
    "last_monitor_at": None,
    "last_monitor_result": None,
    "last_snapshot_at": None,
    "last_scan_snapshot_at": None,
    "errors": 0,
}


def _any_market_open() -> bool:
    """True if at least one tracked exchange is currently in session.

    Positions may span XETRA/LSE/NYSE so we monitor whenever ANY market
    that could hold our symbols is open (weekends always excluded).
    """
    try:
        from . import market_hours
        return any(m.get("is_open") for m in market_hours.all_market_status())
    except Exception:
        return False


def _fetch_symbol_day(symbol: str) -> Optional[Dict[str, float]]:
    """Last price + today's open/high/low for one symbol (5d history)."""
    import yfinance as yf

    try:
        hist = yf.Ticker(symbol).history(period="5d")
        if hist is None or hist.empty:
            return None
        row = hist.iloc[-1]
        return {
            "last": float(row["Close"]),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
        }
    except Exception as e:
        logger.debug(f"day fetch failed for {symbol}: {e}")
        return None


def _fetch_prices_for(symbols) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for sym in symbols:
        d = _fetch_symbol_day(sym)
        if d and d.get("last"):
            out[sym] = d
    return out


def monitor_positions() -> Dict:
    """Fetch live day stats for held symbols and run the exit engine."""
    from . import paper_trade as pt

    result = {"checked": 0, "closed": [], "trailing_updates": [], "errors": []}
    try:
        positions = pt.get_open_positions()
        if not positions:
            _status["last_monitor_result"] = "no open positions"
            return result
        day = _fetch_prices_for(p["symbol"] for p in positions)
        prices = {s: d["last"] for s, d in day.items()}
        result["checked"] = len(day)
        # Trailing stops first (may move stops up before exit checks)
        result["trailing_updates"] = pt.apply_trailing_stops(prices)
        # Exit engine with intraday-aware fills
        result["closed"] = pt.check_stops_and_targets(prices, day_stats=day)
        # Time-based exits
        result["closed"] += pt.apply_time_exits(prices)
        _status["last_monitor_result"] = (
            f"checked {result['checked']}, closed {len(result['closed'])}"
        )
    except Exception as e:
        _status["errors"] += 1
        result["errors"].append(str(e))
        logger.error(f"monitor_positions failed: {e}", exc_info=True)
    finally:
        _status["last_monitor_at"] = datetime.utcnow().isoformat()
    return result
def daily_snapshot() -> Dict:
    """Persist one equity point into performance_log (once per UTC day)."""
    global _last_snapshot_day
    from . import paper_trade as pt
    from . import performance_tracker as perf

    result = {"ok": False}
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if _last_snapshot_day == today:
            return {"ok": True, "skipped": "already snapshotted today"}
        # Fetch fresh prices for open positions so equity is marked correctly
        positions = pt.get_open_positions()
        day = _fetch_prices_for(p["symbol"] for p in positions)
        prices = {s: d["last"] for s, d in day.items()}
        port = pt.get_portfolio(current_prices=prices)
        perf.log_performance(
            date=today,
            portfolio_value=port["stats"]["equity"],
            cash=port["stats"]["cash"],
            pnl=port["stats"]["total_pnl"],
        )
        _last_snapshot_day = today
        _status["last_snapshot_at"] = datetime.utcnow().isoformat()
        result = {"ok": True, "date": today, "equity": port["stats"]["equity"]}
        logger.info(f"daily snapshot: {result}")
    except Exception as e:
        _status["errors"] += 1
        result = {"error": str(e)}
        logger.error(f"daily_snapshot failed: {e}", exc_info=True)
    return result


def scan_history_job() -> Dict:
    """Persist the current rankings cache into scan_snapshots (best effort)."""
    from . import scan_history

    result = {"ok": False}
    try:
        saved = scan_history.snapshot_from_cache()
        _status["last_scan_snapshot_at"] = datetime.utcnow().isoformat()
        result = {"ok": True, "saved": saved}
    except Exception as e:
        _status["errors"] += 1
        result = {"error": str(e)}
        logger.error(f"scan_history_job failed: {e}", exc_info=True)
    return result


def run_agent_cycle_job() -> Dict:
    """Phase 3: run the autonomous agent decision cycle (if enabled)."""
    from . import agent

    result = {"ok": False}
    try:
        result = agent.run_cycle_if_enabled()
    except Exception as e:
        _status["errors"] += 1
        result = {"ok": False, "error": str(e)}
        logger.error(f"agent cycle failed: {e}", exc_info=True)
    return result


def _make_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    sched = BackgroundScheduler(daemon=True)
    sched.add_job(
        monitor_positions,
        IntervalTrigger(minutes=MONITOR_INTERVAL_MIN),
        id="monitor_positions",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        daily_snapshot,
        IntervalTrigger(minutes=60),  # hourly tick; guarded once-per-day inside
        id="daily_snapshot",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        scan_history_job,
        IntervalTrigger(minutes=SCAN_SNAPSHOT_MIN),
        id="scan_history_job",
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        run_agent_cycle_job,
        IntervalTrigger(minutes=15),
        id="agent_cycle",
        max_instances=1,
        coalesce=True,
    )
    return sched


def start() -> None:
    global _scheduler
    if not SCHEDULER_ENABLED:
        logger.info("scheduler disabled via SCHEDULER_ENABLED=0")
        return
    with _lock:
        if _scheduler is not None:
            return
        _scheduler = _make_scheduler()
        _scheduler.start()
        _status["running"] = True
        logger.info(
            f"scheduler started: monitor={MONITOR_INTERVAL_MIN}min, "
            f"scan_snapshot={SCAN_SNAPSHOT_MIN}min"
        )


def stop() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            try:
                _scheduler.shutdown(wait=False)
            except Exception:
                pass
            _scheduler = None
        _status["running"] = False


def status() -> Dict:
    jobs = []
    if _scheduler is not None:
        try:
            jobs = [
                {"id": j.id, "next_run": str(j.next_run_time)}
                for j in _scheduler.get_jobs()
            ]
        except Exception:
            jobs = []
    return {**_status, "jobs": jobs, "interval_min": MONITOR_INTERVAL_MIN}


__all__ = ["start", "stop", "status", "monitor_positions", "daily_snapshot",
           "scan_history_job", "run_agent_cycle_job"]

