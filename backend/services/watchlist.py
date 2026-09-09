"""User watchlists: persistent ticker lists with quick scores and alerts.

SQLite-backed (single default list + named lists).
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
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS watchlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS watchlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
                symbol TEXT NOT NULL,
                added_at TEXT NOT NULL,
                alert_price_above REAL,
                alert_price_below REAL,
                UNIQUE (watchlist_id, symbol)
            );

            INSERT OR IGNORE INTO watchlists (name, created_at) VALUES ('default', ?);
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO watchlists (name, created_at) VALUES (?, ?)",
            ("default", datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _get_or_create_list(watchlist: str) -> int:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT id FROM watchlists WHERE name = ?", (watchlist,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO watchlists (name, created_at) VALUES (?, ?)",
            (watchlist, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def add_symbol(symbol: str, watchlist: str = "default",
               alert_above: Optional[float] = None,
               alert_below: Optional[float] = None) -> Dict:
    init_db()
    symbol = (symbol or "").upper().strip()
    if not symbol:
        return {"ok": False, "error": "symbol required"}
    wid = _get_or_create_list(watchlist)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO watchlist_items "
            "(watchlist_id, symbol, added_at, alert_price_above, alert_price_below) "
            "VALUES (?,?,?,?,?)",
            (wid, symbol, datetime.utcnow().isoformat(), alert_above, alert_below),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "watchlist": watchlist, "symbol": symbol}


def remove_symbol(symbol: str, watchlist: str = "default") -> Dict:
    init_db()
    wid = _get_or_create_list(watchlist)
    conn = _get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM watchlist_items WHERE watchlist_id = ? AND symbol = ?",
            (wid, (symbol or "").upper().strip()),
        )
        conn.commit()
        return {"ok": True, "removed": cur.rowcount > 0, "symbol": symbol}
    finally:
        conn.close()


def get_symbols(watchlist: str = "default") -> List[Dict]:
    init_db()
    wid = _get_or_create_list(watchlist)
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT symbol, added_at, alert_price_above, alert_price_below "
            "FROM watchlist_items WHERE watchlist_id = ? ORDER BY id DESC",
            (wid,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_watchlists() -> List[Dict]:
    init_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT w.id, w.name, COUNT(i.id) AS items
               FROM watchlists w LEFT JOIN watchlist_items i ON i.watchlist_id = w.id
               GROUP BY w.id ORDER BY w.id"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


__all__ = [
    "init_db", "add_symbol", "remove_symbol", "get_symbols", "list_watchlists",
]
