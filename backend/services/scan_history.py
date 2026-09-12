"""Scan history: persist rankings snapshots for score evolution + ML training.

Every snapshot (default every 30 min via the scheduler, or on dashboard
loads) stores each ranked ticker's scores. Later snapshots of the same
trading day are REPLACED so one row = one ticker per day — the clean
basis for:
  * score evolution charts (was 62 last week, 71 today),
  * SCORE_DROP alerts (top-5 name dropped >15 pts),
  * ML training labels (score vs forward return).

Forward-return labels are computed lazily from daily_candles when the ML
scorer requests training data.
"""
from __future__ import annotations

import json
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
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scan_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                region TEXT,
                ticker TEXT NOT NULL,
                name TEXT,
                rank INTEGER,
                breakout_score REAL,
                composite_score REAL,
                components_json TEXT,
                price REAL
            );
            CREATE INDEX IF NOT EXISTS idx_snap_date ON scan_snapshots(date);
            CREATE INDEX IF NOT EXISTS idx_snap_ticker ON scan_snapshots(ticker);
            """
        )
        conn.commit()
    finally:
        conn.close()


def save_snapshot(rankings: List[Dict], region: Optional[str] = "all") -> Dict:
    """Persist one rankings list. Replaces same-day rows for the region."""
    if not rankings:
        return {"saved": 0}
    init_db()
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    conn = _get_conn()
    try:
        conn.execute(
            "DELETE FROM scan_snapshots WHERE date = ? AND region = ?",
            (today, region),
        )
        rows = []
        for r in rankings:
            comps = r.get("breakout_components") or {}
            rows.append((
                today,
                now.isoformat(),
                region,
                r.get("ticker"),
                r.get("name"),
                r.get("rank"),
                r.get("breakout_score") or 0.0,
                r.get("composite_score") or 0.0,
                json.dumps(comps) if comps else None,
                r.get("current_price") or r.get("price"),
            ))
        conn.executemany(
            "INSERT INTO scan_snapshots "
            "(date, timestamp, region, ticker, name, rank, breakout_score, composite_score, components_json, price) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        return {"saved": len(rows), "date": today, "region": region}
    finally:
        conn.close()


def snapshot_from_cache() -> Dict:
    """Snapshot the strategy module's in-memory rankings cache (no refetch)."""
    from ..strategy import _CACHE  # in-memory rankings cache

    saved = 0
    for region, results in _CACHE.items():
        if results:
            res = save_snapshot(results, region=region)
            saved += res.get("saved", 0)
    return {"saved": saved, "regions": len(_CACHE)}


def get_history(ticker: Optional[str] = None, days: int = 90) -> List[Dict]:
    """Chronological score history (one row per ticker per day)."""
    init_db()
    conn = _get_conn()
    try:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM scan_snapshots WHERE ticker = ? ORDER BY date DESC LIMIT ?",
                ((ticker or "").upper(), int(days) * 50),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scan_snapshots ORDER BY date DESC, rank ASC LIMIT ?",
                (int(days) * 50,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def score_change(ticker: str, days: int = 7) -> Optional[Dict]:
    """Score change for a ticker between the two most recent distinct days."""
    rows = get_history(ticker, days=days)
    if len(rows) < 2:
        return None
    by_date: Dict[str, float] = {}
    for r in rows:
        d = r.get("date")
        if d:
            by_date[d] = r.get("breakout_score") or 0.0
    dates = sorted(by_date.keys())
    if len(dates) < 2:
        return None
    prev, cur = by_date[dates[-2]], by_date[dates[-1]]
    return {
        "ticker": ticker.upper(),
        "prev_date": dates[-2],
        "prev_score": prev,
        "cur_date": dates[-1],
        "cur_score": cur,
        "delta": round(cur - prev, 2),
    }


def get_distinct_days(limit: int = 60) -> List[str]:
    init_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT date FROM scan_snapshots ORDER BY date DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [r["date"] for r in rows]
    finally:
        conn.close()

def get_training_data(horizon_days: int = 10) -> List[Dict]:
    """Build ML training rows: features (score components) → forward return.

    For each (ticker, date) snapshot, the label is the N-trading-day forward
    return computed from daily_candles. Rows with missing candles are
    skipped; only rows whose horizon has fully completed are included.
    """
    init_db()
    conn = _get_conn()
    training_rows: List[Dict] = []
    try:
        snaps = conn.execute(
            "SELECT * FROM scan_snapshots ORDER BY date ASC"
        ).fetchall()
        candles: Dict[str, Dict[str, float]] = {}
        for s in snaps:
            sym = s["ticker"]
            if sym not in candles:
                crows = conn.execute(
                    "SELECT date, close FROM daily_candles WHERE symbol = ? ORDER BY date",
                    (sym,),
                ).fetchall()
                candles[sym] = {r["date"]: r["close"] for r in crows}
            closes = candles.get(sym, {})
            dates_sorted = sorted(closes.keys())
            idx = {d: i for i, d in enumerate(dates_sorted)}
            base_date = s["date"]
            if base_date not in idx:
                continue
            i = idx[base_date]
            j = i + horizon_days
            if j >= len(dates_sorted):
                continue  # horizon not complete yet
            p0, p1 = closes[dates_sorted[i]], closes[dates_sorted[j]]
            if not p0 or p1 is None:
                continue
            fwd = (p1 / p0) - 1.0
            comps = {}
            try:
                comps = json.loads(s["components_json"] or "{}")
            except Exception:
                comps = {}
            row = {
                "ticker": sym,
                "date": base_date,
                "horizon_days": horizon_days,
                "forward_return": round(fwd * 100.0, 4),
                "breakout_score": s["breakout_score"] or 0.0,
                "composite_score": s["composite_score"] or 0.0,
                **{f"comp_{k}": v for k, v in comps.items()},
            }
            training_rows.append(row)
    finally:
        conn.close()
    return training_rows


def pick_performance(days: int = 30) -> Dict:
    """How did graded picks actually perform AFTER being listed?

    For every snapshot row (one ticker per day, last `days` days), grade the
    pick from its breakout score, then measure the price change from the pick
    to the latest cached close (requiring >= 2 candles after the pick date so
    the pick has had time to move). Aggregated per grade bucket.

    Note: this measures raw price change since listing — NOT a stop-managed
    trade — so it answers "does the grade predict direction?", not P&L.
    """
    from . import pick_grader

    init_db()
    conn = _get_conn()
    try:
        snaps = conn.execute(
            "SELECT date, ticker, breakout_score, composite_score, price "
            "FROM scan_snapshots WHERE date < ? ORDER BY date ASC",
            (datetime.utcnow().strftime("%Y-%m-%d"),),
        ).fetchall()
        # Keep only the newest snapshot per (date, ticker) — re-scans overwrite,
        # but be safe if duplicates exist from before that rule.
        seen = set()
        picks = []
        for s in snaps:
            key = (s["date"], s["ticker"])
            if key in seen:
                continue
            seen.add(key)
            picks.append(s)

        candles: Dict[str, Dict[str, float]] = {}
        per_grade: Dict[str, Dict] = {}
        n_total = 0
        for s in picks:
            sym = s["ticker"]
            if sym not in candles:
                crows = conn.execute(
                    "SELECT date, close FROM daily_candles WHERE symbol = ? ORDER BY date",
                    (sym,),
                ).fetchall()
                candles[sym] = [(r["date"], r["close"]) for r in crows]
            series = candles.get(sym, [])
            # closes strictly after the pick date
            after = [c for d, c in series if d > s["date"] and c]
            if len(after) < 2 or not s["price"]:
                continue
            ret = (after[-1] / float(s["price"])) - 1.0
            score = s["breakout_score"] or s["composite_score"] or 0
            grade = pick_grader.grade_from_score(score)
            g = per_grade.setdefault(grade, {
                "n_picks": 0, "wins": 0, "sum_return": 0.0,
                "best_pct": None, "worst_pct": None,
            })
            g["n_picks"] += 1
            n_total += 1
            if ret > 0:
                g["wins"] += 1
            g["sum_return"] += ret
            rp = round(ret * 100.0, 2)
            g["best_pct"] = rp if g["best_pct"] is None else max(g["best_pct"], rp)
            g["worst_pct"] = rp if g["worst_pct"] is None else min(g["worst_pct"], rp)

        grades_out = {}
        for grade in pick_grader.GRADES:
            g = per_grade.get(grade)
            if not g:
                continue
            n = g["n_picks"]
            grades_out[grade] = {
                "n_picks": n,
                "avg_return_pct": round(g["sum_return"] / n * 100.0, 2) if n else 0.0,
                "win_rate_pct": round(g["wins"] / n * 100.0, 1) if n else 0.0,
                "best_pct": g["best_pct"],
                "worst_pct": g["worst_pct"],
            }
        return {
            "days": days,
            "n_picks_tracked": n_total,
            "grades": grades_out,
            "note": "Raw price change since listing (no stop management) — measures whether the grade predicts direction.",
        }
    finally:
        conn.close()


__all__ = [
    "init_db", "save_snapshot", "snapshot_from_cache", "get_history",
    "score_change", "get_distinct_days", "get_training_data",
]

