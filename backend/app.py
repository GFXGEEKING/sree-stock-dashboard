"""Main FastAPI application for the Best of the Market dashboard."""

import asyncio
import math
import logging
from typing import Any, Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .strategy import rank_stocks
from .data_stream import data_streamer
from .forecast import forecast
from .services import paper_trade as pt
from .services import market_hours
from .services import data_fetcher
from .services import watchlist as wl
from .services import alerts as alerts_svc
from .services import scheduler as bg_scheduler
from .services import performance_tracker as perf
from .services import scan_history
from .services import ml_scorer
from .services import news_sentiment
from .services import correlation_filter as corr_filter
from .services import event_filter
from .services import agent as trading_agent
from .services import backtest as backtester

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=4)


def remove_nan(obj: Any) -> Any:
    """Recursively replace NaN/Inf values with None for JSON serialization."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    elif isinstance(obj, dict):
        return {k: remove_nan(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [remove_nan(item) for item in obj]
    return obj


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.utcnow().isoformat()


app = FastAPI(
    title="Best of the Market Dashboard",
    description="Multi-region stock scanning, live streaming, and 7-day forecasting",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        # Production (same-origin via nginx proxy, listed for belt-and-braces)
        "https://sreestocktrading.duckdns.org",
        "http://sreestocktrading.duckdns.org",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def start_background_services():
    """Init DBs and start the APScheduler background loop.

    This is what makes paper-trading stops/targets execute even when no
    browser tab is open (the single biggest v1 gap).
    """
    try:
        data_fetcher.init_db()
        pt.init_db()
        perf.init_db()
        scan_history.init_db()
        trading_agent.init_db()
        bg_scheduler.start()
        logger.info("background services initialized")
    except Exception as e:
        logger.error(f"background init failed: {e}", exc_info=True)


@app.on_event("shutdown")
async def stop_background_services():
    try:
        bg_scheduler.stop()
    except Exception:
        pass


@app.get("/", tags=["Status"])
async def root():
    """Friendly landing page that points users to the real endpoints."""
    return {
        "name": "Best of the Market Dashboard API",
        "version": "1.0.0",
        "status": "ok",
        "frontend": "http://localhost:5173/",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "endpoints": {
            "health": "/api/health",
            "regions": "/api/regions",
            "dashboard": "/api/dashboard?region=USA",
            "forecast": "/api/forecast/AAPL?days=7",
            "refresh": "POST /api/data/refresh",
        },
    }


@app.get("/api/health", tags=["Status"])
async def health():
    import datetime
    return {"status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat()}


@app.get("/api/regions", tags=["Regions"])
async def regions():
    return {
        "regions": data_streamer.get_region_tickers(),
        "total": data_streamer.total_ticker_count,
    }


@app.post("/api/data/refresh", tags=["Data"])
async def refresh_data(background_tasks: BackgroundTasks):
    background_tasks.add_task(data_streamer.update_all)
    return {"message": "Data refresh started"}


@app.get("/api/data/price/{ticker}", tags=["Data"])
async def ticker_price(ticker: str):
    data = await data_streamer.get_latest(ticker)
    if not data or not data.get("success"):
        raise HTTPException(404, f"No data for {ticker}")
    return {
        "ticker": ticker,
        "price": data.get("price"),
        "daily_change_pct": data.get("daily_change_pct"),
        "last_updated": data.get("last_updated"),
    }


@app.get("/api/strategy/rankings", tags=["Strategy"])
async def strategy_rankings(region: Optional[str] = None, limit: int = 25):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        executor, lambda: rank_stocks(region=region, limit=limit)
    )


@app.get("/api/forecast/{ticker}", tags=["Forecast"])
async def ticker_forecast(ticker: str, days: int = 7):
    try:
        hist_data = await data_streamer.get_history(ticker, days=30)
        if hist_data is None or hist_data.empty:
            raise HTTPException(404, f"No history for {ticker}")
        fc = forecast(hist_data, days=days)
        return {"ticker": ticker, "forecast_days": days, "forecast": fc}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Forecast error for {ticker}: {e}")
        raise HTTPException(500, str(e))


@app.get("/api/dashboard", tags=["Dashboard"])
async def dashboard(region: Optional[str] = None, limit: int = 25):
    try:
        loop = asyncio.get_event_loop()
        rankings = await loop.run_in_executor(
            executor, lambda: rank_stocks(region=region, limit=limit)
        )
        enriched = []
        for entry in rankings.get("rankings", [])[:limit]:
            t = entry.get("ticker", "")
            try:
                price_data = await data_streamer.get_latest(t)
                fc = None
                if price_data and price_data.get("success"):
                    hist = await data_streamer.get_history(t, days=180)
                    if hist is not None and not hist.empty:
                        fc = forecast(hist, days=7)
                enriched.append({
                    **entry,
                    "current_price": price_data.get("price") if price_data else None,
                    "daily_change": price_data.get("daily_change_pct") if price_data else None,
                    "forecast": fc,
                })
            except Exception as e:
                logger.error(f"Error processing ticker {t}: {e}")
                enriched.append({
                    **entry,
                    "current_price": None,
                    "daily_change": None,
                    "forecast": None,
                    "error": str(e),
                })

        import datetime
        result = {
            "rankings": enriched,
            "region": region or "all",
            "generated_at": datetime.datetime.utcnow().isoformat(),
            "total_returned": len(enriched),
        }
        return remove_nan(result)
    except Exception as e:
        logger.error(f"Dashboard endpoint error: {e}", exc_info=True)
        raise HTTPException(500, f"Dashboard processing failed: {str(e)}")


# ======================================================================
# PAPER TRADING PLATFORM
# ======================================================================

class PaperOpenRequest(BaseModel):
    symbol: str
    shares: int
    price: float
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None
    # Optional explicit currency; when omitted the trading currency is
    # auto-detected from the ticker universe (USD for AAPL, GBP for .L, …)
    currency: Optional[str] = None


class PaperCloseRequest(BaseModel):
    position_id: int
    exit_price: float
    reason: str = "manual_close"


@app.get("/api/paper/portfolio", tags=["Paper Trading"])
async def paper_portfolio():
    """Full paper trading portfolio: account, positions, history, stats.

    Also runs the stop-loss / target monitor: any open position whose
    latest cached price has breached its stop or target is auto-closed
    (exit_reason: stop_hit / target_hit).
    """
    try:
        loop = asyncio.get_event_loop()

        def _build_with_monitor():
            # 1. Latest known price per held symbol (cache-first, one-symbol
            #    fetch fallback — never scans the whole universe)
            symbols = [p["symbol"] for p in pt.get_open_positions()]
            prices = {}
            for sym in symbols:
                try:
                    df = data_fetcher.fetch_single(sym)
                    if df is not None and not df.empty:
                        prices[sym] = float(df["Close"].iloc[-1])
                except Exception as e:
                    logger.debug(f"price lookup failed for {sym}: {e}")
            # 2. Auto-close anything that hit stop / target
            auto_closed = pt.check_stops_and_targets(prices)
            # 3. Return the enriched snapshot
            snapshot = pt.get_portfolio(current_prices=prices)
            snapshot["auto_exits"] = auto_closed
            snapshot["live_prices_used"] = prices
            return snapshot

        result = await loop.run_in_executor(executor, _build_with_monitor)
        return remove_nan(result)
    except Exception as e:
        logger.error(f"paper portfolio error: {e}", exc_info=True)
        raise HTTPException(500, f"Paper portfolio failed: {e}")


@app.post("/api/paper/open", tags=["Paper Trading"])
async def paper_open(req: PaperOpenRequest):
    """Open a simulated long position (whole shares, slippage + commission applied)."""
    try:
        result = pt.open_position(
            symbol=req.symbol,
            shares=req.shares,
            price=req.price,
            stop_loss=req.stop_loss,
            target_price=req.target_price,
            currency=req.currency or "",
        )
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "order rejected"))
        return remove_nan({**result, "message": f"Bought {req.shares} {req.symbol.upper()}"})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"paper open error: {e}", exc_info=True)
        raise HTTPException(500, f"Paper open failed: {e}")


@app.post("/api/paper/close", tags=["Paper Trading"])
async def paper_close(req: PaperCloseRequest):
    """Close an open paper position at the given exit price."""
    try:
        result = pt.close_position(
            position_id=req.position_id,
            exit_price=req.exit_price,
            reason=req.reason,
        )
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "close rejected"))
        return remove_nan(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"paper close error: {e}", exc_info=True)
        raise HTTPException(500, f"Paper close failed: {e}")


@app.post("/api/paper/reset", tags=["Paper Trading"])
async def paper_reset(starting_balance: Optional[float] = None):
    """Reset the paper account (wipes positions, trades and events)."""
    try:
        result = pt.reset_account(starting_balance)
        return remove_nan({**result, "message": "Paper account reset"})
    except Exception as e:
        logger.error(f"paper reset error: {e}", exc_info=True)
        raise HTTPException(500, f"Paper reset failed: {e}")


@app.get("/api/paper/events", tags=["Paper Trading"])
async def paper_events(limit: int = 30):
    """Recent paper-trading activity log (fills, stops, resets)."""
    try:
        events = pt.get_events(limit=limit)
        return remove_nan({"events": events})
    except Exception as e:
        raise HTTPException(500, f"Paper events failed: {e}")


# ======================================================================
# POSITION SIZING SUGGESTION
# ======================================================================

class SizeSuggestionRequest(BaseModel):
    symbol: str
    price: float
    stop_loss: float
    target_price: Optional[float] = None
    risk_per_trade_pct: float = 1.5
    max_position_pct: float = 10.0


@app.post("/api/paper/suggest-size", tags=["Paper Trading"])
async def paper_suggest_size(req: SizeSuggestionRequest):
    """Suggest a share count using risk-based + volatility-aware sizing.

    Guardrails (beginner-safe defaults):
    - risk per trade: 1.5% of equity (configurable)
    - max position: 10% of equity
    - also reports the Kelly / half-Kelly fraction from realized win-rate
    """
    try:
        loop = asyncio.get_event_loop()

        def _suggest():
            port = pt.get_portfolio()
            equity = port["stats"]["equity"]
            price = float(req.price)
            stop = float(req.stop_loss)

            if stop >= price:
                return {"ok": False, "error": "stop_loss must be below entry price for a long"}

            risk_per_share = price - stop
            risk_budget_eur = equity * (req.risk_per_trade_pct / 100.0)

            # Risk-based sizing
            shares_risk = int(risk_budget_eur / risk_per_share) if risk_per_share > 0 else 0

            # Cap by max position %
            max_value_eur = equity * (req.max_position_pct / 100.0)
            shares_cap = int(max_value_eur / price) if price > 0 else 0

            shares = max(0, min(shares_risk, shares_cap))

            # Kelly from realized win-rate + avg win/loss (fallback 50%/1:2)
            stats = port["stats"]
            closed = stats.get("closed_trades") or 0
            if closed >= 5:
                win_rate = (stats.get("win_rate") or 50.0) / 100.0
                avg_win = stats.get("avg_win_eur") or 0.0
                avg_loss = abs(stats.get("avg_loss_eur") or 0.0) or 1.0
                b = avg_win / avg_loss if avg_loss else 2.0
            else:
                win_rate, b = 0.5, 2.0
            kelly = max(0.0, (b * win_rate - (1 - win_rate)) / b) if b else 0.0
            half_kelly = kelly / 2.0

            # R:R ratio
            rr = None
            if req.target_price:
                reward = float(req.target_price) - price
                rr = round(reward / risk_per_share, 2) if risk_per_share > 0 else None

            size_eur = shares * price
            risk_eur = shares * risk_per_share
            return {
                "ok": True,
                "symbol": req.symbol.upper(),
                "shares": shares,
                "size_eur": round(size_eur, 2),
                "risk_eur": round(risk_eur, 2),
                "risk_pct": round((risk_eur / equity) * 100.0, 2) if equity else 0.0,
                "size_pct": round((size_eur / equity) * 100.0, 2) if equity else 0.0,
                "rr_ratio": rr,
                "rr_acceptable": (rr is None) or (rr >= 2.0),
                "kelly_frac": round(kelly * 100.0, 1),
                "half_kelly_frac": round(half_kelly * 100.0, 1),
                "equity_eur": round(equity, 2),
                "notes": (
                    f"Sized so a stop-out loses ~€{risk_eur:,.0f} ({req.risk_per_trade_pct}% of equity). "
                    f"Capped at {req.max_position_pct}% position size."
                ),
            }

        result = await loop.run_in_executor(executor, _suggest)
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "suggestion failed"))
        return remove_nan(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"suggest-size error: {e}", exc_info=True)
        raise HTTPException(500, f"Size suggestion failed: {e}")


# ======================================================================
# EQUITY CURVE
# ======================================================================

@app.get("/api/paper/equity-curve", tags=["Paper Trading"])
async def paper_equity_curve():
    """Reconstruct the realized equity curve from trade history.

    Returns one point per closed trade (chronological), including start
    (€10,000) and the current open-equity point.
    """
    try:
        loop = asyncio.get_event_loop()

        def _curve():
            port = pt.get_portfolio()
            start = port["stats"]["starting_balance"]
            closed = [t for t in port["trade_history"] if t["status"] == "CLOSED"]
            closed.reverse()  # chronological

            points = [{"date": None, "equity": round(start, 2), "label": "Start"}]
            equity = start
            for t in closed:
                equity += t.get("pnl_eur") or 0
                points.append({
                    "date": t.get("exit_date"),
                    "equity": round(equity, 2),
                    "label": f"{t['symbol']} {'+' if (t.get('pnl_eur') or 0) >= 0 else ''}{t.get('pnl_eur')}",
                })
            # Current point (with unrealized)
            points.append({
                "date": port["account"]["updated_at"],
                "equity": port["stats"]["equity"],
                "label": "Now",
            })
            return {
                "points": points,
                "starting_balance": start,
                "current_equity": port["stats"]["equity"],
                "total_return_pct": port["stats"]["total_return_pct"],
                "max_drawdown_eur": port["stats"]["max_drawdown_eur"],
            }

        result = await loop.run_in_executor(executor, _curve)
        return remove_nan(result)
    except Exception as e:
        logger.error(f"equity-curve error: {e}", exc_info=True)
        raise HTTPException(500, f"Equity curve failed: {e}")


# ======================================================================
# SCHEDULER STATUS
# ======================================================================

@app.get("/api/scheduler/status", tags=["System"])
async def scheduler_status():
    """Background scheduler health + last run times of each job."""
    return bg_scheduler.status()


@app.post("/api/scheduler/monitor/run", tags=["System"])
async def scheduler_monitor_run():
    """Manually trigger the position monitor (stop/target/trailing check)."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, bg_scheduler.monitor_positions)
    return remove_nan(result)


# ======================================================================
# WATCHLIST (service existed but had no endpoints)
# ======================================================================

@app.get("/api/watchlist", tags=["Watchlist"])
async def watchlist_get(watchlist: str = "default"):
    return {"items": wl.get_symbols(watchlist)}


@app.post("/api/watchlist/add", tags=["Watchlist"])
async def watchlist_add(symbol: str, watchlist: str = "default",
                        alert_above: Optional[float] = None,
                        alert_below: Optional[float] = None):
    res = wl.add_symbol(symbol, watchlist, alert_above, alert_below)
    return res


@app.delete("/api/watchlist/remove", tags=["Watchlist"])
async def watchlist_remove(symbol: str, watchlist: str = "default"):
    return wl.remove_symbol(symbol, watchlist)


# ======================================================================
# ALERTS (service existed but had no endpoints)
# ======================================================================

@app.get("/api/alerts", tags=["Alerts"])
async def alerts_get(limit: int = 30, alert_type: Optional[str] = None):
    return {"alerts": alerts_svc.get_alerts(limit=limit, alert_type=alert_type)}


@app.get("/api/alerts/channels", tags=["Alerts"])
async def alerts_channels():
    return alerts_svc.channels_configured()


# ======================================================================
# PERFORMANCE METRICS
# ======================================================================

@app.get("/api/paper/metrics", tags=["Paper Trading"])
async def paper_metrics():
    """Sharpe/Sortino/drawdown/expectancy/profit-factor for the paper account."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(executor, perf.portfolio_metrics)
    return remove_nan(result)


# ======================================================================
# SCAN HISTORY + ML
# ======================================================================

@app.get("/api/scan/history", tags=["Scanner"])
async def scan_history_route(ticker: Optional[str] = None, days: int = 90):
    loop = asyncio.get_event_loop()

    def _hist():
        if ticker:
            return {"ticker": ticker.upper(), "history": scan_history.get_history(ticker, days)}
        return {
            "days": scan_history.get_distinct_days(limit=days),
            "history": scan_history.get_history(days=days),
        }

    return await loop.run_in_executor(executor, _hist)


@app.get("/api/scan/score-change", tags=["Scanner"])
async def scan_score_change(ticker: str, days: int = 7):
    loop = asyncio.get_event_loop()
    res = await loop.run_in_executor(
        executor, lambda: scan_history.score_change(ticker, days) or {}
    )
    return res


@app.post("/api/ml/retrain", tags=["Scanner"])
async def ml_retrain(force: bool = False):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: ml_scorer.maybe_retrain(force=force))


@app.get("/api/ml/status", tags=["Scanner"])
async def ml_status():
    return ml_scorer.model_status()


@app.get("/api/news/sentiment", tags=["Scanner"])
async def news_sentiment_route(symbol: str):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: news_sentiment.get_sentiment(symbol))


@app.get("/api/markets/status", tags=["Markets"])
async def markets_status():
    """Open/closed status for XETRA, LSE, Euronext, BME, Milan, NYSE, NASDAQ."""
    try:
        statuses = market_hours.all_market_status()
        return remove_nan({"markets": statuses, "server_time": _now_iso()})
    except Exception as e:
        raise HTTPException(500, f"Market status failed: {e}")


# ======================================================================
# AGENT (Phase 3)
# ======================================================================

class AgentModeRequest(BaseModel):
    mode: str  # OFF | SIGNAL_ONLY | SEMI_AUTO | FULL_AUTO


@app.get("/api/agent/status", tags=["Agent"])
async def agent_status():
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, trading_agent.status)


@app.post("/api/agent/mode", tags=["Agent"])
async def agent_set_mode(req: AgentModeRequest):
    return trading_agent.set_mode(req.mode)


@app.post("/api/agent/cycle/run", tags=["Agent"])
async def agent_run_cycle():
    """Trigger one agent decision cycle manually (respects the set mode)."""
    loop = asyncio.get_event_loop()
    return remove_nan(await loop.run_in_executor(executor, trading_agent.run_cycle_if_enabled))


@app.get("/api/agent/log", tags=["Agent"])
async def agent_log(limit: int = 50):
    return {"log": trading_agent.get_log(limit=limit)}


@app.get("/api/agent/queue", tags=["Agent"])
async def agent_queue(status: Optional[str] = None):
    return {"queue": trading_agent.get_queue(status=status)}


@app.post("/api/agent/approve/{item_id}", tags=["Agent"])
async def agent_approve(item_id: int):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, lambda: trading_agent.approve_proposal(item_id))


@app.post("/api/agent/reject/{item_id}", tags=["Agent"])
async def agent_reject(item_id: int):
    return trading_agent.reject_proposal(item_id)


# ======================================================================
# BACKTEST
# ======================================================================

class BacktestRequest(BaseModel):
    symbols: List[str]
    cash: float = 10_000.0
    max_open: int = 5
    risk_pct: float = 0.015
    # NOTE: backtests recompute scores from OHLCV only (no fundamentals/
    # sector/earnings components), so a live score of ~70 maps to a proxy
    # score of ~45. Default reflects that scale.
    min_score: float = 45.0


@app.post("/api/agent/backtest", tags=["Agent"])
async def agent_backtest(req: BacktestRequest):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        executor,
        lambda: backtester.run_backtest(
            symbols=[s.upper() for s in req.symbols],
            cash=req.cash,
            max_open=req.max_open,
            risk_pct=req.risk_pct,
            min_score=req.min_score,
        ),
    )
    return remove_nan(result)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)