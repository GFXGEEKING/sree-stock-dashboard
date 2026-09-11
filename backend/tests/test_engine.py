"""Tests for the upgraded paper trading engine + agent helpers.

Run:  cd <project root> && backend/venv/bin/python -m pytest backend/tests -v

Uses a temp DB via the DB_PATH env var so the real portfolio is never
touched.
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project-root importability no matter where pytest is launched
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TMP_DB = str(Path(__file__).parent / "_test_stocks.db")
os.environ["DB_PATH"] = TMP_DB  # must be set before service imports

import pytest  # noqa: E402

from backend.services import paper_trade as pt  # noqa: E402
from backend.services import performance_tracker as perf  # noqa: E402
from backend.services import correlation_filter as cf  # noqa: E402
from backend.services import news_sentiment as ns  # noqa: E402
from backend.services import ml_scorer  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """Wipe all tables between tests for isolation."""
    pt.init_db()
    perf.init_db()
    conn = sqlite3.connect(TMP_DB)
    try:
        # Ensure optional tables exist before wiping (fresh test DB)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS daily_candles ("
            "symbol TEXT, date DATE, open REAL, high REAL, low REAL, close REAL, "
            "adj_close REAL, volume INTEGER, PRIMARY KEY (symbol, date))"
        )
        for t in ("paper_positions", "paper_trades", "paper_events",
                  "performance_log", "daily_candles"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("UPDATE paper_account SET cash = 10000, starting_balance = 10000")
        conn.commit()
    finally:
        conn.close()
    yield


# ----------------------------------------------------------------------
# Paper engine
# ----------------------------------------------------------------------

def test_open_and_close_position_roundtrip():
    res = pt.open_position("AAPL", shares=10, price=100.0, stop_loss=94.0, target_price=112.0)
    assert res["ok"], res
    assert res["fill_price"] > 100.0  # slippage on buy
    pos = pt.get_open_positions({"AAPL": 105.0})
    assert len(pos) == 1
    assert pos[0]["unrealized_pnl"] > 0
    closed = pt.close_position(res["position_id"], 110.0)
    assert closed["ok"]
    assert closed["reason"] == "manual_close"
    assert closed["pnl_eur"] > 0


def test_open_rejects_stop_above_price():
    res = pt.open_position("AAPL", shares=1, price=100.0, stop_loss=105.0)
    assert not res["ok"]
    assert "below" in res["error"]


def test_open_rejects_insufficient_cash():
    res = pt.open_position("AAPL", shares=10_000, price=100.0)
    assert not res["ok"]
    assert "insufficient cash" in res["error"]


def test_stop_hit_with_intraday_low():
    res = pt.open_position("AAPL", shares=10, price=100.0, stop_loss=94.0)
    assert res["ok"]
    # Day traded down to 92 (below stop 94) but closed at 96:
    # the old engine would have missed it; the new one fills at the stop.
    day = {"AAPL": {"open": 99.0, "high": 99.5, "low": 92.0, "last": 96.0}}
    closed = pt.check_stops_and_targets({"AAPL": 96.0}, day_stats=day)
    assert len(closed) == 1
    assert closed[0]["reason"] == "stop_hit"
    # Fill = stop level less the 0.05% sell slippage
    assert closed[0]["exit_fill"] == pytest.approx(94.0 * 0.9995, abs=0.01)


def test_stop_gap_down_fills_at_open():
    res = pt.open_position("AAPL", shares=10, price=100.0, stop_loss=94.0)
    assert res["ok"]
    # Gapped down at the open to 90 — realistic fill is the open (less slippage).
    day = {"AAPL": {"open": 90.0, "high": 91.0, "low": 89.0, "last": 90.5}}
    closed = pt.check_stops_and_targets({"AAPL": 90.5}, day_stats=day)
    assert closed[0]["exit_fill"] == pytest.approx(90.0 * 0.9995, abs=0.01)

def test_target_hit_with_intraday_high():
    res = pt.open_position("AAPL", shares=10, price=100.0, target_price=112.0)
    assert res["ok"]
    day = {"AAPL": {"open": 101.0, "high": 113.0, "low": 100.5, "last": 108.0}}
    closed = pt.check_stops_and_targets({"AAPL": 108.0}, day_stats=day)
    assert len(closed) == 1
    assert closed[0]["reason"] == "target_hit"
    # Fill = target level less the 0.05% sell slippage
    assert closed[0]["exit_fill"] == pytest.approx(112.0 * 0.9995, abs=0.01)


def test_no_false_exit_when_price_quiet():
    res = pt.open_position("AAPL", shares=10, price=100.0, stop_loss=94.0, target_price=112.0)
    assert res["ok"]
    day = {"AAPL": {"open": 100.0, "high": 103.0, "low": 98.0, "last": 101.0}}
    closed = pt.check_stops_and_targets({"AAPL": 101.0}, day_stats=day)
    assert closed == []


def test_trailing_stop_ratchets_up_never_down():
    res = pt.open_position("AAPL", shares=10, price=100.0, stop_loss=92.0, trailing_percent=5.0)
    assert res["ok"]
    # Price rises to 110 → new stop should be 110*0.95 = 104.5
    updates = pt.apply_trailing_stops({"AAPL": 110.0})
    assert len(updates) == 1
    assert updates[0]["new_stop"] == pytest.approx(104.5, abs=0.01)
    # Price falls back to 105 → stop must NOT drop
    updates = pt.apply_trailing_stops({"AAPL": 105.0})
    assert updates == []
    pos = pt.get_open_positions({"AAPL": 105.0})
    assert pos[0]["stop_loss"] == pytest.approx(104.5, abs=0.01)


def test_time_exit():
    res = pt.open_position("MSFT", shares=1, price=300.0, max_days=20)
    assert res["ok"]
    old = (datetime.utcnow() - timedelta(days=25)).isoformat()
    conn = sqlite3.connect(TMP_DB)
    conn.execute("UPDATE paper_positions SET entry_date = ? WHERE symbol = 'MSFT'", (old,))
    conn.commit()
    conn.close()
    closed = pt.apply_time_exits({"MSFT": 305.0})
    assert any(c["reason"] == "time_stop" for c in closed)


# ----------------------------------------------------------------------
# Performance metrics
# ----------------------------------------------------------------------

def test_risk_metrics_from_daily_log():
    perf.log_performance("2026-09-01", 10000, 10000, 0)
    perf.log_performance("2026-09-02", 10100, 10100, 100)
    perf.log_performance("2026-09-03", 9900, 9900, -100)
    perf.log_performance("2026-09-04", 10050, 10050, 50)
    m = perf.risk_metrics()
    assert m["n_days"] == 4
    assert m["max_drawdown_pct"] > 0
    assert m["sharpe"] is not None


def test_trade_metrics_from_trades():
    r1 = pt.open_position("AAPL", shares=10, price=100.0)
    pt.close_position(r1["position_id"], 110.0)
    r2 = pt.open_position("MSFT", shares=10, price=200.0)
    pt.close_position(r2["position_id"], 190.0)
    port = pt.get_portfolio()
    tm = perf.trade_metrics(port["trade_history"])
    assert tm["closed_trades"] == 2
    assert tm["win_rate_pct"] == 50.0


# ----------------------------------------------------------------------
# Correlation + news + ML helpers
# ----------------------------------------------------------------------

def test_correlation_blocks_identical_symbol():
    res = cf.entry_allowed("AAPL", ["AAPL"])
    assert not res["allowed"]
    assert res["reason"] == "position already open"


def test_correlation_no_data_not_blocking():
    res = cf.entry_allowed("ZZZZ.X", ["AAPL"])
    assert res["allowed"] or "already" in res.get("reason", "")


def test_news_lexicon_scoring():
    assert ns._score_text("Company beats expectations, stock surges") > 0
    assert ns._score_text("Company misses forecasts, shares plunge") < 0
    assert ns._score_text("Regular quarterly meeting held") == 0.0
    assert ns.sentiment_bonus(0.5) == 1.0
    assert ns.sentiment_bonus(-1.0) == -2.0


def test_ml_cold_start_heuristic():
    pred = ml_scorer.predict({"breakout_score": 80.0})
    assert pred["model"] == "heuristic"
    assert 0.3 < pred["probability"] <= 0.9
    blended = ml_scorer.blend_score(80.0, pred["probability"])
    assert 40.0 <= blended <= 100.0


# ----------------------------------------------------------------------
# Agent Phase 1 hardening: volatility regime + liquidity gates
# ----------------------------------------------------------------------

from backend.services import agent as ag  # noqa: E402


@pytest.fixture
def clean_state():
    return {
        "equity": 10000.0, "cash": 10000.0, "held_symbols": [],
        "guardrails_ok": True, "guardrail_reasons": [],
        "market_regime_ok": True, "volatility_regime_ok": True,
        "avg_volumes": {},  # pre-computed avg volumes (tests inject here)
    }


def _liq_candidate(**kw):
    base = {"ticker": "TEST", "current_price": 150.0, "blended_score": 85.0,
            "ml_probability": 0.75, "avg_volume": 5_000_000}
    base.update(kw)
    return base


def test_liquid_candidate_passes_all_gates(clean_state):
    clean_state["avg_volumes"] = {}
    d = ag.decide_entry(_liq_candidate(ticker="TESTL", avg_volume=5_000_000), clean_state)
    # earnings/correlation filters may hit the network for unknown symbols;
    # if they pass, the liquidity gate must NOT be the blocker
    if d["decision"] == "SKIP":
        assert not any("liquidity" in r for r in d["reasons"]), d["reasons"]
    else:
        assert d["decision"] == "ENTRY"


def test_illiquid_candidate_blocked_by_liquidity_gate(clean_state):
    clean_state["avg_volumes"] = {}
    d = ag.decide_entry(_liq_candidate(ticker="ILLIQ.X", avg_volume=100_000), clean_state)
    assert d["decision"] == "SKIP"
    assert any("liquidity" in r for r in d["reasons"]), d["reasons"]


def test_unknown_volume_fails_closed(clean_state):
    clean_state["avg_volumes"] = {"NOVOL.X": None}
    cand = _liq_candidate(ticker="NOVOL.X")
    cand.pop("avg_volume")
    d = ag.decide_entry(cand, clean_state)
    assert d["decision"] == "SKIP"
    assert any("insufficient volume data" in r for r in d["reasons"]), d["reasons"]


def test_volatility_regime_false_blocks_entry(clean_state):
    clean_state["avg_volumes"] = {}
    st = dict(clean_state, volatility_regime_ok=False)
    d = ag.decide_entry(_liq_candidate(ticker="VIXB.X", avg_volume=5_000_000), st)
    assert d["decision"] == "SKIP"
    assert any("volatility regime" in r for r in d["reasons"]), d["reasons"]


def test_avg_volume_from_cache(clean_state):
    import time as _time
    conn = sqlite3.connect(TMP_DB)
    try:
        today = datetime.utcnow().date()
        for k in range(30):
            d = (today - timedelta(days=k)).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO daily_candles (symbol, date, open, high, low, close, "
                "adj_close, volume) VALUES (?,?,?,?,?,?,?,?)",
                ("LIQ.X", d, 100, 101, 99, 100, 100, 1_000_000 + k),
            )
        conn.commit()
    finally:
        conn.close()
    av = ag._avg_volume_from_cache("LIQ.X")
    assert av is not None and av >= 1_000_000, av


def test_avg_volume_unknown_symbol_returns_none():
    assert ag._avg_volume_from_cache("ZZNOVOL.X") is None


