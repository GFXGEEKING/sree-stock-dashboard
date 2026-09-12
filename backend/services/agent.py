"""Autonomous trading agent for the paper trading account.

Design principles (see the agent architecture doc):
  * The decision core is DETERMINISTIC rules + ML probability — no LLM in
    the buy/sell loop (LLMs can't be backtested and drift).
  * The same decide() logic powers backtest.py, so live behavior matches
    the tested behavior.
  * Operating modes with hard progression:
        OFF -> SIGNAL_ONLY (alerts, no trades)
            -> SEMI_AUTO (proposes trades, waits for user approval)
            -> FULL_AUTO (opens/closes automatically within guardrails)
  * Guardrails: max open positions, max position %, risk per trade,
    daily loss halt, drawdown pause, correlation gate, earnings blackout.
  * Every decision — including "decided NOT to trade" — is logged to the
    agent_log table so the UI can show the reasoning trail.

Runtime: the scheduler calls run_cycle_if_enabled() every 15 minutes.
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

# --- Configuration (env-overridable, beginner-safe defaults) ----------
AGENT_MODE = os.getenv("AGENT_MODE", "OFF").upper()  # OFF|SIGNAL_ONLY|SEMI_AUTO|FULL_AUTO
MIN_SCORE = float(os.getenv("AGENT_MIN_SCORE", "70"))
MIN_ML_PROB = float(os.getenv("AGENT_MIN_ML_PROB", "0.60"))
MAX_OPEN_POSITIONS = int(os.getenv("AGENT_MAX_OPEN_POSITIONS", "5"))
RISK_PER_TRADE_PCT = float(os.getenv("AGENT_RISK_PER_TRADE_PCT", "1.5"))
MAX_POSITION_PCT = float(os.getenv("AGENT_MAX_POSITION_PCT", "10"))
MAX_SECTOR_PCT = float(os.getenv("AGENT_MAX_SECTOR_PCT", "20"))
STOP_ATR_MULT = float(os.getenv("AGENT_STOP_ATR_MULT", "2.0"))
TARGET_RR = float(os.getenv("AGENT_TARGET_RR", "2.0"))
TRAILING_PCT = float(os.getenv("AGENT_TRAILING_PCT", "8"))
TIME_STOP_DAYS = int(os.getenv("AGENT_TIME_STOP_DAYS", "20"))
DAILY_LOSS_HALT_PCT = float(os.getenv("AGENT_DAILY_LOSS_HALT_PCT", "3"))
DRAWDOWN_PAUSE_PCT = float(os.getenv("AGENT_DRAWDOWN_PAUSE_PCT", "10"))
SCORE_EXIT_LEVEL = float(os.getenv("AGENT_SCORE_EXIT_LEVEL", "35"))
MIN_AVG_VOLUME = int(os.getenv("AGENT_MIN_AVG_VOLUME", "500000"))  # 30d avg shares/day
VIX_HIGH = float(os.getenv("AGENT_VIX_HIGH", "35"))  # panic regime — no new entries
VIX_CHOP = float(os.getenv("AGENT_VIX_CHOP", "20"))  # 20d VIX stdev threshold


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
            CREATE TABLE IF NOT EXISTS agent_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                mode TEXT NOT NULL,
                action TEXT NOT NULL,
                symbol TEXT,
                details TEXT,
                executed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS agent_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                symbol TEXT NOT NULL,
                shares INTEGER,
                price REAL,
                stop_loss REAL,
                target_price REAL,
                trailing_percent REAL,
                max_days INTEGER,
                rationale TEXT,
                status TEXT NOT NULL DEFAULT 'PENDING'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

# ----------------------------------------------------------------------
# Config persistence (agent_config overrides env defaults)
# ----------------------------------------------------------------------

def get_mode() -> str:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT value FROM agent_config WHERE key = 'mode'").fetchone()
        return (row["value"] if row else AGENT_MODE) or "OFF"
    except Exception:
        return AGENT_MODE
    finally:
        conn.close()


def set_mode(mode: str) -> Dict:
    mode = (mode or "").upper()
    if mode not in ("OFF", "SIGNAL_ONLY", "SEMI_AUTO", "FULL_AUTO"):
        return {"ok": False, "error": f"unknown mode {mode}"}
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO agent_config (key, value, updated_at) VALUES ('mode', ?, ?)",
            (mode, datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    _log("mode_change", None, {"new_mode": mode}, executed=1)
    return {"ok": True, "mode": mode}


def _cfg(key: str, default: str) -> str:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM agent_config WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default
    except Exception:
        return default
    finally:
        conn.close()


def _log(action: str, symbol: Optional[str], details: Dict, executed: int = 0) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO agent_log (timestamp, mode, action, symbol, details, executed) "
            "VALUES (?,?,?,?,?,?)",
            (
                datetime.utcnow().isoformat(),
                get_mode(),
                action,
                symbol,
                json.dumps(details, default=str),
                int(executed),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_log(limit: int = 50) -> List[Dict]:
    init_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM agent_log ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["details"] = json.loads(d.get("details") or "{}")
            except Exception:
                pass
            out.append(d)
        return out
    finally:
        conn.close()


def get_queue(status: Optional[str] = None) -> List[Dict]:
    init_db()
    conn = _get_conn()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM agent_queue WHERE status = ? ORDER BY id DESC LIMIT 50",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_queue ORDER BY id DESC LIMIT 50"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _set_queue_status(item_id: int, status: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE agent_queue SET status = ? WHERE id = ?", (status, int(item_id))
        )
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Guardrails
# ----------------------------------------------------------------------

def _daily_pnl_pct() -> Optional[float]:
    """Realized+unrealized P&L change since today's first mark (or None)."""
    from . import paper_trade as pt
    from . import performance_tracker as perf

    try:
        hist = perf.get_performance_history()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        port = pt.get_portfolio()
        equity = port["stats"]["equity"]
        base = None
        for row in reversed(hist):  # find the last snapshot before today
            if row["date"] < today:
                base = row["portfolio_value"]
                break
        if base is None:
            base = port["stats"]["starting_balance"]
        if not base:
            return None
        return ((equity / base) - 1.0) * 100.0
    except Exception as e:
        logger.debug(f"daily pnl calc failed: {e}")
        return None


def _drawdown_pct() -> Optional[float]:
    """Current drawdown from the highest recorded equity mark."""
    from . import paper_trade as pt
    from . import performance_tracker as perf

    try:
        hist = perf.get_performance_history()
        port = pt.get_portfolio()
        equity = port["stats"]["equity"]
        values = [row["portfolio_value"] for row in hist] + [equity]
        if not values:
            return None
        peak = max(values)
        if not peak:
            return None
        return ((peak - equity) / peak) * 100.0
    except Exception as e:
        logger.debug(f"drawdown calc failed: {e}")
        return None


def guardrails_ok() -> Dict:
    """Evaluate all risk gates. {ok, reasons: [...]}"""
    from . import paper_trade as pt

    reasons = []
    positions = pt.get_open_positions()
    if len(positions) >= MAX_OPEN_POSITIONS:
        reasons.append(f"max open positions reached ({len(positions)}/{MAX_OPEN_POSITIONS})")
    dd = _drawdown_pct()
    if dd is not None and dd >= DRAWDOWN_PAUSE_PCT:
        reasons.append(f"drawdown {dd:.1f}% ≥ {DRAWDOWN_PAUSE_PCT}% — paused")
    dpl = _daily_pnl_pct()
    if dpl is not None and dpl <= -DAILY_LOSS_HALT_PCT:
        reasons.append(f"daily loss {dpl:.1f}% ≥ {DAILY_LOSS_HALT_PCT}% — halted for today")
    return {"ok": not reasons, "reasons": reasons}


# ----------------------------------------------------------------------
# Signal sources
# ----------------------------------------------------------------------

def _latest_rankings() -> List[Dict]:
    """Pull the freshest rankings from the strategy cache (no refetch)."""
    from ..strategy import _CACHE

    merged: List[Dict] = []
    seen = set()
    for results in _CACHE.values():
        for r in results:
            t = r.get("ticker")
            if t and t not in seen:
                seen.add(t)
                merged.append(r)
    merged.sort(key=lambda r: r.get("blended_score") or r.get("breakout_score") or 0, reverse=True)
    return merged


def _market_regime_ok() -> Optional[bool]:
    """None = unknown (allow), True = risk-on, False = risk-off.

    Regime filter: the S&P 500 must be above its 200-day MA for the agent
    to open NEW positions. Existing positions are managed normally.
    """
    try:
        import yfinance as yf

        hist = yf.Ticker("^GSPC").history(period="1y")
        if hist is None or hist.empty or len(hist) < 200:
            return None
        close = hist["Close"].dropna()
        sma200 = close.rolling(200).mean().iloc[-1]
        return bool(close.iloc[-1] > sma200)
    except Exception as e:
        logger.debug(f"regime check failed: {e}")
        return None


def _volatility_regime_ok() -> Optional[bool]:
    """None = unknown (allow), True = tradable volatility, False = blocked.

    Two-part volatility filter:
      * Level: VIX >= VIX_HIGH (panic) → block new entries.
      * Stability: 20-day stdev of VIX daily returns > VIX_CHOP/100
        (regime whipsaw) → block new entries.
    """
    try:
        import yfinance as yf

        hist = yf.Ticker("^VIX").history(period="6mo")
        if hist is None or hist.empty:
            return None
        close = hist["Close"].dropna()
        if len(close) < 40:
            return None
        level = float(close.iloc[-1])
        vol20 = float(close.pct_change().dropna().rolling(20).std().iloc[-1] or 0.0)
        if level >= VIX_HIGH or vol20 * 100 >= VIX_CHOP:
            logger.info(f"volatility regime blocked: VIX={level:.1f}, 20d vol={vol20*100:.1f}%")
            return False
        return True
    except Exception as e:
        logger.debug(f"volatility regime check failed: {e}")
        return None


def _avg_volume_from_cache(symbol: str) -> Optional[int]:
    """30-day average daily volume from cached candles (no network)."""
    try:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT volume FROM daily_candles WHERE symbol = ? ORDER BY date DESC LIMIT 30",
                ((symbol or "").upper(),),
            ).fetchall()
        finally:
            conn.close()
        vols = [int(r["volume"] or 0) for r in rows if r["volume"] is not None]
        if len(vols) < 10:
            return None
        return int(sum(vols) / len(vols))
    except Exception as e:
        logger.debug(f"avg volume failed for {symbol}: {e}")
        return None


# ----------------------------------------------------------------------
# Entry decision (deterministic — shared with the backtester)
# ----------------------------------------------------------------------

def _plan_stop_target(price: float, atr: Optional[float]) -> Dict:
    """Stop = 2×ATR below entry (fallback 6%); target = 2R+ reward."""
    if atr and atr > 0:
        stop = round(price - STOP_ATR_MULT * atr, 4)
    else:
        stop = round(price * 0.94, 4)
    risk = price - stop
    target = round(price + TARGET_RR * risk, 4) if risk > 0 else None
    return {"stop_loss": stop, "target_price": target, "risk_per_share": round(risk, 4)}


def _size_position(equity: float, price: float, risk_per_share: float) -> int:
    """Risk-based sizing, capped at MAX_POSITION_PCT of equity."""
    if price <= 0 or risk_per_share <= 0:
        return 0
    risk_budget = equity * (RISK_PER_TRADE_PCT / 100.0)
    shares_risk = int(risk_budget / risk_per_share)
    shares_cap = int((equity * (MAX_POSITION_PCT / 100.0)) / price)
    return max(0, min(shares_risk, shares_cap))


def _atr_from_cache(symbol: str) -> Optional[float]:
    """Latest ATR(14) from cached daily candles (no network)."""
    try:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT high, low, close FROM daily_candles WHERE symbol = ? ORDER BY date DESC LIMIT 15",
                ((symbol or "").upper(),),
            ).fetchall()
        finally:
            conn.close()
        if len(rows) < 15:
            return None
        trs = []
        prev_close = None
        for r in reversed(rows):  # oldest first
            h, l, c = float(r["high"]), float(r["low"]), float(r["close"])
            if prev_close is not None:
                trs.append(max(h - l, abs(h - prev_close), abs(l - prev_close)))
            prev_close = c
        if len(trs) < 10:
            return None
        return sum(trs) / len(trs)
    except Exception as e:
        logger.debug(f"atr from cache failed for {symbol}: {e}")
        return None


def decide_entry(candidate: Dict, portfolio_state: Dict) -> Dict:
    """Deterministic entry decision for one candidate.

    Returns {decision: "ENTRY"|"SKIP", reasons: [...], order: {...}|None}.
    Pure function of (candidate, portfolio_state) — shared with backtest.
    """
    reasons = []
    symbol = candidate.get("ticker")
    price = candidate.get("current_price") or candidate.get("price")

    # 1. Score gates
    score = candidate.get("blended_score") or candidate.get("breakout_score") or 0.0
    if score < MIN_SCORE:
        reasons.append(f"score {score:.0f} < {MIN_SCORE:.0f}")
    ml_prob = candidate.get("ml_probability")
    if ml_prob is not None and ml_prob < MIN_ML_PROB:
        reasons.append(f"ml_probability {ml_prob:.2f} < {MIN_ML_PROB}")

    # 2. Market regime
    regime = portfolio_state.get("market_regime_ok")
    if regime is False:
        reasons.append("market regime risk-off (S&P below 200d MA)")

    # 2b. Volatility regime (panic VIX / whipsaw → no new entries)
    if portfolio_state.get("volatility_regime_ok") is False:
        reasons.append("volatility regime: VIX extreme or unstable (new entries blocked)")

    # 2c. Liquidity gate — must be able to exit the position cleanly
    avg_vol = candidate.get("avg_volume")
    if avg_vol is None:
        avg_vol = (portfolio_state.get("avg_volumes") or {}).get(symbol)
    if avg_vol is None:
        avg_vol = _avg_volume_from_cache(symbol)
    if avg_vol is not None and int(avg_vol) < MIN_AVG_VOLUME:
        reasons.append(f"liquidity: 30d avg volume {int(avg_vol):,} < {MIN_AVG_VOLUME:,}")
    if avg_vol is None:
        reasons.append("liquidity: insufficient volume data (avg volume unknown)")

    # 3. Guardrails
    if not portfolio_state.get("guardrails_ok", True):
        reasons.extend(portfolio_state.get("guardrail_reasons", []))

    # 4. Correlation gate
    held = portfolio_state.get("held_symbols", [])
    from . import correlation_filter as cf

    corr = cf.entry_allowed(symbol, held)
    if not corr.get("allowed"):
        reasons.append(corr.get("reason") or "correlation gate")

    # 5. Earnings blackout
    from . import event_filter as ef

    ev = ef.entry_allowed(symbol)
    if not ev.get("allowed"):
        reasons.append(ev.get("reason") or "earnings blackout")

    # 6. Sizing
    if not price or price <= 0:
        reasons.append("no valid price")
        return {"decision": "SKIP", "reasons": reasons, "order": None}
    atr = _atr_from_cache(symbol)
    plan = _plan_stop_target(float(price), atr)
    shares = _size_position(
        portfolio_state.get("equity", 0.0), float(price), plan["risk_per_share"]
    )
    if shares <= 0:
        reasons.append("position size computed as 0 (risk/cap limits)")
    if (shares * float(price)) < 200:  # engine min trade
        reasons.append("order value below €200 minimum")
    if portfolio_state.get("cash", 0.0) < shares * float(price):
        reasons.append("insufficient cash")

    if reasons:
        return {"decision": "SKIP", "reasons": reasons, "order": None}

    rationale = (
        f"Agent entry: score {score:.0f}"
        + (f", ML {ml_prob:.2f}" if ml_prob is not None else "")
        + f", ATR stop {STOP_ATR_MULT}x, target {TARGET_RR}R, risk {RISK_PER_TRADE_PCT}%"
    )
    return {
        "decision": "ENTRY",
        "reasons": ["all gates passed"],
        "order": {
            "symbol": symbol,
            "shares": shares,
            "price": float(price),
            "stop_loss": plan["stop_loss"],
            "target_price": plan["target_price"],
            "trailing_percent": TRAILING_PCT,
            "max_days": TIME_STOP_DAYS,
            "rationale": rationale,
            "entry_score": score,
        },
    }


# ----------------------------------------------------------------------
# Exit decision (score-drop exit; stops/targets handled by monitor job)
# ----------------------------------------------------------------------

def decide_exits(portfolio_state: Dict) -> List[Dict]:
    """Score-drop exit: close when a held symbol's score collapses."""
    exits = []
    rankings_by_symbol = {
        r.get("ticker"): r for r in portfolio_state.get("rankings", [])
    }
    for p in portfolio_state.get("positions", []):
        sym = p["symbol"]
        r = rankings_by_symbol.get(sym)
        if not r:
            continue
        score = r.get("blended_score") or r.get("breakout_score") or 0.0
        if score < SCORE_EXIT_LEVEL:
            exits.append({
                "position_id": p["id"],
                "symbol": sym,
                "reason": f"score_exit: score {score:.0f} < {SCORE_EXIT_LEVEL:.0f}",
                "score": score,
            })
    return exits


# ----------------------------------------------------------------------
# Cycle
# ----------------------------------------------------------------------

def run_cycle_if_enabled() -> Dict:
    mode = get_mode()
    if mode == "OFF":
        return {"ok": True, "ran": False, "mode": mode, "reason": "agent OFF"}
    return run_cycle(mode)


def run_cycle(mode: Optional[str] = None) -> Dict:
    """One full decision cycle: observe → decide entries/exits → execute."""
    from . import paper_trade as pt
    from . import alerts as alerts_svc
    from . import ml_scorer

    mode = (mode or get_mode()).upper()
    init_db()
    summary = {
        "ok": True, "mode": mode, "entries": [], "exits": [], "skipped": [],
        "signals": [],
    }
    try:
        # 1. OBSERVE
        ml_scorer.maybe_retrain()  # no-op unless enough new labeled rows
        rankings = _latest_rankings()
        for r in rankings[:25]:
            ml_scorer.enrich(r)  # ensure ml fields even when the cache was cold
        positions = pt.get_open_positions()
        port = pt.get_portfolio()
        g = guardrails_ok()
        portfolio_state = {
            "equity": port["stats"]["equity"],
            "cash": port["stats"]["cash"],
            "held_symbols": [p["symbol"] for p in positions],
            "positions": positions,
            "guardrails_ok": g["ok"],
            "guardrail_reasons": g["reasons"],
            "market_regime_ok": _market_regime_ok(),
            "volatility_regime_ok": _volatility_regime_ok(),
            "rankings": rankings,
        }

        # 2. EXIT decisions (score-drop) — always evaluated
        for ex in decide_exits(portfolio_state):
            price_row = next(
                (r for r in rankings if r.get("ticker") == ex["symbol"]), None
            )
            price = (price_row or {}).get("current_price") or (price_row or {}).get("price")
            if not price:
                continue
            if mode == "FULL_AUTO":
                res = pt.close_position(ex["position_id"], float(price), reason=ex["reason"])
                executed = bool(res.get("ok"))
                _log("exit", ex["symbol"], {**ex, "result": "closed" if executed else res}, executed=executed)
                summary["exits"].append({**ex, "executed": executed})
                if executed:
                    alerts_svc.raise_alert(
                        "AGENT_EXIT", f"🤖 Agent closed {ex['symbol']}: {ex['reason']}",
                        symbol=ex["symbol"], severity="info",
                    )
            else:
                _log("exit_proposal", ex["symbol"], ex, executed=0)
                summary["signals"].append({**ex, "kind": "exit"})
                if mode == "SIGNAL_ONLY":
                    alerts_svc.raise_alert(
                        "AGENT_EXIT_SIGNAL",
                        f"🤖 Agent would exit {ex['symbol']}: {ex['reason']} (mode={mode})",
                        symbol=ex["symbol"], severity="info",
                    )

        # 3. ENTRY decisions — only if guardrails allow
        for candidate in rankings[:10]:  # top 10 by blended score
            decision = decide_entry(candidate, portfolio_state)
            if decision["decision"] != "ENTRY":
                if candidate.get("ticker") in portfolio_state["held_symbols"]:
                    continue  # held positions aren't "skips"
                summary["skipped"].append({
                    "ticker": candidate.get("ticker"),
                    "reasons": decision["reasons"][:3],
                })
                continue
            order = decision["order"]
            if mode == "FULL_AUTO":
                res = pt.open_position(
                    symbol=order["symbol"],
                    shares=order["shares"],
                    price=order["price"],
                    stop_loss=order["stop_loss"],
                    target_price=order["target_price"],
                    trailing_percent=order["trailing_percent"],
                    max_days=order["max_days"],
                    source="agent",
                    rationale=order["rationale"],
                    entry_score=order["entry_score"],
                )
                executed = bool(res.get("ok"))
                _log("entry", order["symbol"], {**order, "result": res}, executed=executed)
                summary["entries"].append({**order, "executed": executed, "result": res})
                if executed:
                    alerts_svc.raise_alert(
                        "AGENT_ENTRY",
                        f"🤖 Agent opened {order['shares']} {order['symbol']} @ {order['price']:.2f} "
                        f"(stop {order['stop_loss']:.2f}, target {order['target_price']:.2f})",
                        symbol=order["symbol"], severity="info",
                    )
                    portfolio_state["held_symbols"].append(order["symbol"])
            elif mode == "SEMI_AUTO":
                qid = _enqueue_proposal(order)
                _log("entry_proposal", order["symbol"], {**order, "queue_id": qid}, executed=0)
                summary["signals"].append({**order, "kind": "entry", "queue_id": qid})
            else:  # SIGNAL_ONLY
                _log("entry_signal", order["symbol"], order, executed=0)
                summary["signals"].append({**order, "kind": "entry"})
                alerts_svc.raise_alert(
                    "AGENT_ENTRY_SIGNAL",
                    f"🤖 Agent signal: BUY {order['shares']} {order['symbol']} @ {order['price']:.2f} "
                    f"— {order['rationale']} (mode=SIGNAL_ONLY)",
                    symbol=order["symbol"], severity="info",
                )

        _log("cycle", None, {
            "entries": len(summary["entries"]), "exits": len(summary["exits"]),
            "skipped": len(summary["skipped"]), "signals": len(summary["signals"]),
        }, executed=1)
    except Exception as e:
        summary["ok"] = False
        summary["error"] = str(e)
        logger.error(f"agent cycle error: {e}", exc_info=True)
        _log("error", None, {"error": str(e)}, executed=0)
    return summary



def _enqueue_proposal(order: Dict) -> int:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO agent_queue "
            "(created_at, kind, symbol, shares, price, stop_loss, target_price, trailing_percent, max_days, rationale) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.utcnow().isoformat(), "entry",
                order["symbol"], order["shares"], order["price"],
                order["stop_loss"], order["target_price"],
                order["trailing_percent"], order["max_days"], order["rationale"],
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def approve_proposal(item_id: int) -> Dict:
    """SEMI_AUTO: user approves a queued proposal → execute it."""
    from . import paper_trade as pt

    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM agent_queue WHERE id = ? AND status = 'PENDING'", (int(item_id),)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"ok": False, "error": "no pending proposal with that id"}
    order = dict(row)
    res = pt.open_position(
        symbol=order["symbol"], shares=order["shares"], price=order["price"],
        stop_loss=order["stop_loss"], target_price=order["target_price"],
        trailing_percent=order["trailing_percent"], max_days=order["max_days"],
        source="agent_approved", rationale=order.get("rationale"),
    )
    _set_queue_status(item_id, "EXECUTED" if res.get("ok") else "REJECTED")
    _log("proposal_approved", order["symbol"], {"id": item_id, "result": res}, executed=bool(res.get("ok")))
    return res


def reject_proposal(item_id: int) -> Dict:
    _set_queue_status(item_id, "REJECTED")
    _log("proposal_rejected", None, {"id": item_id}, executed=0)
    return {"ok": True}


def filters() -> Dict:
    """Live status of hardening filters (VIX regime + liquidity)."""
    try:
        vol_ok = _volatility_regime_ok()
        vix_level = None
        vix_chop = None
        try:
            import yfinance as yf
            hist = yf.Ticker("^VIX").history(period="6mo")["Close"].dropna()
            if len(hist) >= 1:
                vix_level = float(hist.iloc[-1])
            if len(hist) >= 40:
                vix_chop = float(hist.pct_change().dropna().rolling(20).std().iloc[-1] * 100)
        except Exception:
            pass
        return {
            "volatility_regime_ok": vol_ok,
            "vix_level": vix_level,
            "vix_high_threshold": VIX_HIGH,
            "vix_chop_threshold": VIX_CHOP,
            "vix_chop_current": vix_chop,
            "min_avg_volume": MIN_AVG_VOLUME,
        }
    except Exception as e:
        logger.debug(f"filters status failed: {e}")
        return {
            "volatility_regime_ok": None,
            "vix_level": None,
            "vix_high_threshold": VIX_HIGH,
            "vix_chop_threshold": VIX_CHOP,
            "vix_chop_current": None,
            "min_avg_volume": MIN_AVG_VOLUME,
            "error": str(e),
        }


def status() -> Dict:
    from . import ml_scorer

    g = guardrails_ok()
    return {
        "mode": get_mode(),
        "guardrails": g,
        "drawdown_pct": _drawdown_pct(),
        "daily_pnl_pct": _daily_pnl_pct(),
        "ml": ml_scorer.model_status(),
        "config": {
            "min_score": MIN_SCORE, "min_ml_prob": MIN_ML_PROB,
            "max_open_positions": MAX_OPEN_POSITIONS,
            "risk_per_trade_pct": RISK_PER_TRADE_PCT,
            "max_position_pct": MAX_POSITION_PCT,
            "stop_atr_mult": STOP_ATR_MULT, "target_rr": TARGET_RR,
            "trailing_pct": TRAILING_PCT, "time_stop_days": TIME_STOP_DAYS,
            "daily_loss_halt_pct": DAILY_LOSS_HALT_PCT,
            "drawdown_pause_pct": DRAWDOWN_PAUSE_PCT,
            "score_exit_level": SCORE_EXIT_LEVEL,
            "min_avg_volume": MIN_AVG_VOLUME,
            "vix_high": VIX_HIGH, "vix_chop": VIX_CHOP,
        },
    }


__all__ = [
    "init_db", "get_mode", "set_mode", "run_cycle", "run_cycle_if_enabled",
    "decide_entry", "decide_exits", "approve_proposal", "reject_proposal",
    "get_log", "get_queue", "status", "filters",
]

