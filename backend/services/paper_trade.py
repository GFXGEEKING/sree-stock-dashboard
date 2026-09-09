"""Paper trading engine: virtual portfolio, simulated fills, P&L tracking.

Design (per spec section 9):
- Starting balance: EUR 10,000 (configurable via CAPITAL_EUR)
- Commission: EUR 1.00 per trade (Trade Republic model)
- Slippage: 0.05% on entry and exit (liquid stocks)
- Whole shares only, min trade EUR 200
- Long positions with optional stop-loss and target
- Exit reasons: manual_close / stop_hit / target_hit
- Every event logged to SQLite for the performance tracker
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "backend/data/stocks.db")

# Config defaults
STARTING_BALANCE = float(os.getenv("CAPITAL_EUR", "10000"))
COMMISSION_EUR = float(os.getenv("COMMISSION_EUR", "1.0"))
SLIPPAGE_PCT = float(os.getenv("SLIPPAGE_PCT", "0.05")) / 100.0
MIN_TRADE_EUR = float(os.getenv("MIN_TRADE_EUR", "200"))


def _fx_to_eur(amount: float, currency: str) -> float:
    """Convert an amount in `currency` to EUR (1:1 for EUR, safe fallback)."""
    try:
        from .fx_rates import to_eur
        return to_eur(amount, currency)
    except Exception:
        return float(amount)


def _symbol_currency(symbol: str) -> str:
    """Look up the trading currency for a ticker (EUR default)."""
    try:
        from .universe import TICKER_CURRENCY
        return TICKER_CURRENCY.get(symbol, "EUR")
    except Exception:
        return "EUR"


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
            CREATE TABLE IF NOT EXISTS paper_account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cash REAL NOT NULL,
                starting_balance REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paper_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL DEFAULT 'long',
                shares INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                entry_date TEXT NOT NULL,
                stop_loss REAL,
                target_price REAL,
                currency TEXT DEFAULT 'EUR',
                status TEXT NOT NULL DEFAULT 'OPEN'
            );

            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                shares INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                entry_date TEXT NOT NULL,
                exit_date TEXT,
                exit_reason TEXT,
                cost_eur REAL NOT NULL,
                proceeds_eur REAL,
                commission_eur REAL NOT NULL,
                pnl_eur REAL,
                pnl_pct REAL,
                hold_days REAL,
                status TEXT NOT NULL DEFAULT 'OPEN'
            );

            CREATE TABLE IF NOT EXISTS paper_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                symbol TEXT,
                message TEXT,
                details TEXT
            );
            """
        )
# Add new columns for trailing stop if they don't exist (for existing databases)
        try:
            conn.execute("ALTER TABLE paper_positions ADD COLUMN trailing_percent REAL")
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            conn.execute("ALTER TABLE paper_positions ADD COLUMN highest_price REAL")
        except sqlite3.OperationalError:
            pass  # Column already exists
        for col in ("source", "rationale", "entry_score"):
            try:
                conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
        try:
            conn.execute("ALTER TABLE paper_positions ADD COLUMN max_days INTEGER")
        except sqlite3.OperationalError:
            pass  # Column already exists
        conn.commit()
        # Ensure account row exists
        row = conn.execute("SELECT 1 FROM paper_account WHERE id = 1").fetchone()
        if not row:
            now = datetime.utcnow().isoformat()
            conn.execute(
                "INSERT INTO paper_account (id, cash, starting_balance, created_at, updated_at) "
                "VALUES (1, ?, ?, ?, ?)",
                (STARTING_BALANCE, STARTING_BALANCE, now, now),
            )
            conn.commit()
    finally:
        conn.close()


def _log_event(event_type: str, symbol: str, message: str, details: str = "") -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO paper_events (timestamp, event_type, symbol, message, details) "
            "VALUES (?,?,?,?,?)",
            (datetime.utcnow().isoformat(), event_type, symbol, message, details),
        )
        conn.commit()
    finally:
        conn.close()


def _apply_slippage(price: float, direction: str) -> float:
    """direction 'buy' -> pay slightly more; 'sell' -> receive slightly less."""
    if not price or price <= 0:
        return price
    if direction == "buy":
        return round(price * (1 + SLIPPAGE_PCT), 4)
    return round(price * (1 - SLIPPAGE_PCT), 4)


# ----------------------------------------------------------------------
# Account
# ----------------------------------------------------------------------


def get_account() -> Dict:
    init_db()
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM paper_account WHERE id = 1").fetchone()
        if not row:
            now = datetime.utcnow().isoformat()
            return {
                "cash": STARTING_BALANCE,
                "starting_balance": STARTING_BALANCE,
                "created_at": now,
                "updated_at": now,
            }
        return dict(row)
    finally:
        conn.close()


def reset_account(starting_balance: Optional[float] = None) -> Dict:
    """Wipe all positions/trades/events and reset cash."""
    balance = float(starting_balance or STARTING_BALANCE)
    init_db()
    conn = _get_conn()
    try:
        now = datetime.utcnow().isoformat()
        conn.executescript(
            "DELETE FROM paper_positions; DELETE FROM paper_trades; DELETE FROM paper_events;"
        )
        conn.execute(
            "UPDATE paper_account SET cash = ?, starting_balance = ?, updated_at = ? WHERE id = 1",
            (balance, balance, now),
        )
        conn.commit()
    finally:
        conn.close()
    _log_event("reset", None, f"Paper account reset to €{balance:,.2f}")
    return get_account()


# ----------------------------------------------------------------------
# Core trade actions
# ----------------------------------------------------------------------


def open_position(
    symbol: str,
    shares: int,
    price: float,
    stop_loss: Optional[float] = None,
    target_price: Optional[float] = None,
    currency: str = "EUR",
    side: str = "long",
    trailing_percent: Optional[float] = None,
    max_days: Optional[int] = None,
    source: str = "user",
    rationale: Optional[str] = None,
    entry_score: Optional[float] = None,
) -> Dict:
    """Open a simulated long position.

    - Applies slippage on entry
    - Charges commission
    - Enforces: whole shares, min trade size, sufficient cash
    - Journal fields (source/rationale/entry_score) record WHY the trade
      was taken — used by the agent and the trade-review UI.
    """
    init_db()
    symbol = (symbol or "").upper().strip()
    shares = int(shares or 0)
    if not symbol:
        return {"ok": False, "error": "symbol required"}
    if shares <= 0:
        return {"ok": False, "error": "shares must be a positive whole number"}
    if not price or price <= 0:
        return {"ok": False, "error": "invalid entry price"}
    if stop_loss and price and float(stop_loss) >= float(price):
        return {"ok": False, "error": "stop_loss must be below entry price for a long"}

    fill_price = _apply_slippage(float(price), "buy")

    # FX fix: auto-detect trading currency and convert the order value to EUR
    # so USD/GBP positions are charged correctly to the EUR cash balance.
    trade_ccy = (currency or "").upper().strip() or _symbol_currency(symbol)
    cost_local = shares * fill_price
    cost_eur = _fx_to_eur(cost_local, trade_ccy)

    conn = _get_conn()
    try:
        acct = conn.execute("SELECT cash FROM paper_account WHERE id = 1").fetchone()
        cash = float(acct["cash"]) if acct else STARTING_BALANCE

        if cost_eur + COMMISSION_EUR > cash:
            return {
                "ok": False,
                "error": f"insufficient cash: need €{cost_eur + COMMISSION_EUR:,.2f}, have €{cash:,.2f}",
            }
        if cost_eur < MIN_TRADE_EUR:
            return {
                "ok": False,
                "error": f"minimum trade size is €{MIN_TRADE_EUR:,.0f} (order value €{cost_eur:,.2f})",
            }

        now = datetime.utcnow().isoformat()
        cur = conn.execute(
            "INSERT INTO paper_positions "
            "(symbol, side, shares, entry_price, entry_date, stop_loss, target_price, "
            "currency, status, trailing_percent, highest_price, max_days, source, rationale, entry_score) "
            "VALUES (?,?,?,?,?,?,?,?, 'OPEN', ?, ?, ?, ?, ?, ?)",
            (symbol, side, shares, fill_price, now, stop_loss, target_price, trade_ccy,
             trailing_percent, fill_price, max_days, source, rationale,
             str(entry_score) if entry_score is not None else None),
        )
        pos_id = cur.lastrowid
        conn.execute(
            "INSERT INTO paper_trades "
            "(position_id, symbol, side, shares, entry_price, entry_date, cost_eur, commission_eur, status) "
            "VALUES (?,?,?,?,?,?,?,?, 'OPEN')",
            (pos_id, symbol, side, shares, fill_price, now, cost_eur, COMMISSION_EUR),
        )
        conn.execute(
            "UPDATE paper_account SET cash = cash - ?, updated_at = ? WHERE id = 1",
            (cost_eur + COMMISSION_EUR, now),
        )
        conn.commit()
    finally:
        conn.close()

    _log_event(
        "open",
        symbol,
        f"BUY {shares} {symbol} @ {fill_price:.2f} {trade_ccy} (cost €{cost_eur:,.2f} + €{COMMISSION_EUR:.2f} fee)",
        f"stop={stop_loss} target={target_price}",
    )
    return {
        "ok": True,
        "position_id": pos_id,
        "symbol": symbol,
        "shares": shares,
        "fill_price": round(fill_price, 4),
        "requested_price": price,
        "currency": trade_ccy,
        "cost_eur": round(cost_eur, 2),
        "commission_eur": COMMISSION_EUR,
        "stop_loss": stop_loss,
        "target_price": target_price,
    }


def close_position(
    position_id: int,
    exit_price: Optional[float] = None,
    reason: str = "manual_close",
) -> Dict:
    """Close an open position at exit_price (slippage applied on sell)."""
    init_db()
    conn = _get_conn()
    try:
        pos = conn.execute(
            "SELECT * FROM paper_positions WHERE id = ? AND status = 'OPEN'", (position_id,)
        ).fetchone()
        if not pos:
            return {"ok": False, "error": f"no open position with id {position_id}"}
        if not exit_price or exit_price <= 0:
            return {"ok": False, "error": "valid exit_price required"}

        fill = _apply_slippage(float(exit_price), "sell")
        entry = pos["entry_price"]
        trade_ccy = pos["currency"] if "currency" in pos.keys() else "EUR"

        # FX-aware accounting: all EUR figures derived via conversion
        proceeds_eur = _fx_to_eur(pos["shares"] * fill, trade_ccy)
        entry_cost_eur = _fx_to_eur(pos["shares"] * entry, trade_ccy)
        pnl_gross_eur = proceeds_eur - entry_cost_eur
        # Commission was charged at entry; charge again on exit
        pnl_net = pnl_gross_eur - COMMISSION_EUR
        # Recompute total commission for the round trip on the trade row
        row = conn.execute(
            "SELECT commission_eur FROM paper_trades WHERE position_id = ? AND status = 'OPEN'",
            (position_id,),
        ).fetchone()
        entry_comm = float(row["commission_eur"]) if row else COMMISSION_EUR
        total_comm = entry_comm + COMMISSION_EUR

        entry_dt = datetime.fromisoformat(pos["entry_date"])
        now = datetime.utcnow()
        hold_days = round((now - entry_dt).total_seconds() / 86400.0, 2)

        conn.execute(
            "UPDATE paper_positions SET status = 'CLOSED' WHERE id = ?", (position_id,)
        )
        conn.execute(
            "UPDATE paper_trades SET exit_price = ?, exit_date = ?, exit_reason = ?, "
            "proceeds_eur = ?, commission_eur = ?, pnl_eur = ?, pnl_pct = ?, hold_days = ?, status = 'CLOSED' "
            "WHERE position_id = ? AND status = 'OPEN'",
            (
                fill,
                now.isoformat(),
                reason,
                proceeds_eur,
                total_comm,
                pnl_net,
                (pnl_gross_eur / entry_cost_eur) * 100.0 if entry_cost_eur else 0.0,
                hold_days,
                position_id,
            ),
        )
        conn.execute(
            "UPDATE paper_account SET cash = cash + ?, updated_at = ? WHERE id = 1",
            (proceeds_eur - COMMISSION_EUR, now.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    _log_event(
        "close",
        pos["symbol"],
        f"SELL {pos['shares']} {pos['symbol']} @ {fill:.2f} {trade_ccy} — P&L €{pnl_net:,.2f} ({reason})",
        f"position_id={position_id}",
    )
    return {
        "ok": True,
        "position_id": position_id,
        "symbol": pos["symbol"],
        "shares": pos["shares"],
        "exit_fill": round(fill, 4),
        "currency": trade_ccy,
        "proceeds_eur": round(proceeds_eur, 2),
        "commission_eur": round(total_comm, 2),
        "pnl_eur": round(pnl_net, 2),
        "pnl_pct": round((pnl_gross_eur / entry_cost_eur) * 100.0, 2) if entry_cost_eur else 0.0,
        "hold_days": hold_days,
        "reason": reason,
    }


# ----------------------------------------------------------------------
# Portfolio views
# ----------------------------------------------------------------------


def get_open_positions(current_prices: Optional[Dict[str, float]] = None) -> List[Dict]:
    """Open positions enriched with live prices + unrealized P&L."""
    init_db()
    prices = current_prices or {}
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM paper_positions WHERE status = 'OPEN' ORDER BY entry_date DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            live = prices.get(d["symbol"], d["entry_price"])
            ccy = d.get("currency") or "EUR"
            d["current_price"] = live
            # Values in trading currency
            d["market_value_local"] = round(d["shares"] * live, 2)
            d["cost_basis_local"] = round(d["shares"] * d["entry_price"], 2)
            # Values in EUR (base currency)
            d["market_value"] = round(_fx_to_eur(d["market_value_local"], ccy), 2)
            d["cost_basis"] = round(_fx_to_eur(d["cost_basis_local"], ccy), 2)
            d["unrealized_pnl"] = round(
                _fx_to_eur((live - d["entry_price"]) * d["shares"], ccy), 2
            )
            d["unrealized_pnl_pct"] = (
                round(((live / d["entry_price"]) - 1) * 100.0, 2) if d["entry_price"] else 0.0
            )
            # Stop / target distance
            if d.get("stop_loss") and live:
                d["distance_to_stop_pct"] = round((live / d["stop_loss"] - 1) * 100.0, 2)
            if d.get("target_price") and live:
                d["distance_to_target_pct"] = round((d["target_price"] / live - 1) * 100.0, 2)
            # Hold days
            try:
                d["hold_days"] = round(
                    (datetime.utcnow() - datetime.fromisoformat(d["entry_date"])).total_seconds() / 86400.0, 2
                )
            except Exception:
                d["hold_days"] = 0.0
            out.append(d)
        return out
    finally:
        conn.close()


def get_trade_history(limit: int = 100) -> List[Dict]:
    init_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_events(limit: int = 50) -> List[Dict]:
    init_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM paper_events ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_portfolio(current_prices: Optional[Dict[str, float]] = None) -> Dict:
    """Full portfolio snapshot: account, positions, history, stats."""
    acct = get_account()
    positions = get_open_positions(current_prices)
    history = get_trade_history(limit=200)

    closed = [t for t in history if t["status"] == "CLOSED"]
    wins = [t for t in closed if (t["pnl_eur"] or 0) > 0]
    losses = [t for t in closed if (t["pnl_eur"] or 0) <= 0]

    total_comm = sum(t.get("commission_eur") or 0 for t in history)
    realized = sum(t.get("pnl_eur") or 0 for t in closed)
    unrealized = sum(p.get("unrealized_pnl") or 0 for p in positions)
    positions_value = sum(p.get("market_value") or 0 for p in positions)
    equity = acct["cash"] + positions_value

    # Max drawdown from realized equity sequence (approx: cumulative realized P&L)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in reversed(closed):
        cum += t.get("pnl_eur") or 0
        peak = max(peak, cum)
        dd = peak - cum
        max_dd = max(max_dd, dd)

    stats = {
        "starting_balance": acct["starting_balance"],
        "cash": round(acct["cash"], 2),
        "positions_value": round(positions_value, 2),
        "equity": round(equity, 2),
        "total_pnl": round(realized + unrealized, 2),
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(unrealized, 2),
        "total_return_pct": (
            round(((equity / acct["starting_balance"]) - 1) * 100.0, 2)
            if acct["starting_balance"]
            else 0.0
        ),
        "open_positions": len(positions),
        "closed_trades": len(closed),
        "win_rate": round(len(wins) / len(closed) * 100.0, 1) if closed else 0.0,
        "avg_win_eur": round(sum(t["pnl_eur"] for t in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_eur": round(sum(t["pnl_eur"] for t in losses) / len(losses), 2) if losses else 0.0,
        "best_trade_eur": round(max((t["pnl_eur"] for t in closed), default=0.0), 2),
        "worst_trade_eur": round(min((t["pnl_eur"] for t in closed), default=0.0), 2),
        "avg_hold_days": (
            round(sum(t.get("hold_days") or 0 for t in closed) / len(closed), 1) if closed else 0.0
        ),
        "max_drawdown_eur": round(max_dd, 2),
        "total_commission_eur": round(total_comm, 2),
    }

    return {
        "account": {k: acct[k] for k in ("cash", "starting_balance", "created_at", "updated_at")},
        "stats": stats,
        "open_positions": positions,
        "trade_history": history,
        "events": get_events(limit=30),
        "config": {
            "commission_eur": COMMISSION_EUR,
            "slippage_pct": SLIPPAGE_PCT * 100.0,
            "min_trade_eur": MIN_TRADE_EUR,
            "currency": "EUR",
        },
    }


# ----------------------------------------------------------------------
# Auto stop/target check (call on refresh)
# ----------------------------------------------------------------------


def _exit_fill_price(
    pos_stop: Optional[float],
    pos_target: Optional[float],
    entry_price: float,
    day: Optional[Dict],
    last: float,
) -> Optional[tuple]:
    """Realistic exit-fill model (Phase 2).

    Uses the day's OHLC when available:
      - gap-through logic: if the session OPENED beyond the stop, fill at the
        open (worse than the stop price) — this is what really happens;
      - otherwise fill at the trigger price (stop/target level).
    Returns (fill_price, reason) or None when no exit is triggered.
    """
    if not day:
        return None
    o, h, l, c = day.get("open"), day.get("high"), day.get("low"), day.get("last")
    if not all(isinstance(v, (int, float)) for v in (o, h, l, c)):
        return None
    if pos_stop:
        # If the day's LOW traded at/below the stop, the stop was hit
        if l <= pos_stop:
            fill = min(pos_stop, o) if o < pos_stop else pos_stop  # gap-down fill
            return (fill, "stop_hit")
    if pos_target:
        if h >= pos_target:
            fill = max(pos_target, o) if o > pos_target else pos_target  # gap-up fill
            return (fill, "target_hit")
    _ = entry_price, last  # unused but kept for signature clarity
    return None


def apply_trailing_stops(current_prices: Dict[str, float]) -> List[Dict]:
    """Ratchet stop-losses up for positions with trailing_percent set.

    Tracks the position high-water mark (highest_price) and moves the stop
    to (high * (1 - trailing_pct)) whenever that exceeds the current stop.
    Never lowers a stop.
    """
    init_db()
    updates: List[Dict] = []
    pending_events: List[tuple] = []  # (symbol, message, details) logged after commit
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM paper_positions WHERE status = 'OPEN' AND trailing_percent IS NOT NULL"
        ).fetchall()
        for pos in rows:
            sym = pos["symbol"]
            price = current_prices.get(sym)
            if not price or price <= 0:
                continue
            tp = float(pos["trailing_percent"] or 0)
            if tp <= 0:
                continue
            high = max(float(pos["highest_price"] or pos["entry_price"]), float(price))
            new_stop = round(high * (1 - tp / 100.0), 4)
            old_stop = pos["stop_loss"]
            if old_stop is not None and new_stop <= float(old_stop):
                continue  # never lower the stop
            if new_stop >= float(price):
                new_stop = round(float(price) * (1 - tp / 100.0), 4)
            conn.execute(
                "UPDATE paper_positions SET highest_price = ?, stop_loss = ? WHERE id = ?",
                (high, new_stop, pos["id"]),
            )
            updates.append({
                "position_id": pos["id"], "symbol": sym,
                "old_stop": old_stop, "new_stop": new_stop, "high": high,
            })
            pending_events.append((
                sym,
                f"Trailing stop raised {float(old_stop or 0):.2f} → {new_stop:.2f} (high {high:.2f})",
                f"position_id={pos['id']}",
            ))
        if updates:
            conn.commit()
    finally:
        conn.close()
    # Log events only after the write transaction is committed (avoids
    # holding the DB write lock while a second connection tries to write).
    for sym, message, details in pending_events:
        _log_event("trailing_stop", sym, message, details)
    return updates


def apply_time_exits(current_prices: Dict[str, float]) -> List[Dict]:
    """Close positions older than their max_days (time stop)."""
    closed = []
    for p in get_open_positions(current_prices):
        md = p.get("max_days")
        price = current_prices.get(p["symbol"])
        if not md or not price:
            continue
        if (p.get("hold_days") or 0) >= float(md):
            res = close_position(p["id"], price, reason="time_stop")
            if res.get("ok"):
                closed.append(res)
    return closed


def check_stops_and_targets(
    current_prices: Dict[str, float],
    day_stats: Optional[Dict[str, Dict[str, float]]] = None,
) -> List[Dict]:
    """Close positions whose stop-loss or target was hit.

    Phase 2 upgrade: when day_stats (open/high/low) are provided the check
    is intraday-aware — a stop breached intraday and recovered is still
    filled (at the stop level, or at the open if the session gapped
    through it). Falls back to last-price-only checks like the old model.
    """
    day_stats = day_stats or {}
    closed = []
    for p in get_open_positions(current_prices):
        price = current_prices.get(p["symbol"])
        if price is None:
            continue
        day = day_stats.get(p["symbol"])
        exit_hit = _exit_fill_price(p.get("stop_loss"), p.get("target_price"), p["entry_price"], day, price)
        if exit_hit:
            fill, reason = exit_hit
            res = close_position(p["id"], fill, reason=reason)
        else:
            # legacy last-price fallback (no OHLC available)
            if p.get("stop_loss") and price <= p["stop_loss"]:
                res = close_position(p["id"], price, reason="stop_hit")
            elif p.get("target_price") and price >= p["target_price"]:
                res = close_position(p["id"], price, reason="target_hit")
            else:
                continue
        if res.get("ok"):
            closed.append(res)
    return closed


__all__ = [
    "init_db",
    "get_account",
    "reset_account",
    "open_position",
    "close_position",
    "get_open_positions",
    "get_trade_history",
    "get_events",
    "get_portfolio",
    "check_stops_and_targets",
    "apply_trailing_stops",
    "apply_time_exits",
    "STARTING_BALANCE",
    "COMMISSION_EUR",
]
