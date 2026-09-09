# Best of the Market Stock Dashboard

A full-stack application that scans, streams, and forecasts top stocks across Germany, Europe, and USA — with a realistic paper trading engine and an autonomous, backtestable trading agent.

## Quick Start (one command)

```bash
./install.sh     # Installs Python + Node dependencies
./run.sh         # Starts backend (8000) and frontend (5173)
```

Then open: **http://localhost:5173/** — To stop: `./stop.sh`

## Prerequisites
- Ubuntu 20.04+ / Debian 11+ (or any Linux with `apt-get`)
- `sudo` access (for installing system packages on the first run)
- Internet connection (for `pip install`, `npm install`, and live yfinance data)

## Project Structure
```
newproject2/
├── install.sh / run.sh / stop.sh
├── requirements.txt
├── backend/
│   ├── app.py                  # FastAPI entrypoint + all routes
│   ├── strategy.py             # Stock scoring + rankings (rules + ML blend)
│   ├── data_stream.py          # Live price fetching (yfinance)
│   ├── forecast.py             # 7-day price forecasting
│   ├── services/
│   │   ├── paper_trade.py      # Paper engine: fills, stops, trailing, time exits
│   │   ├── scheduler.py        # APScheduler: 24/7 position monitor + snapshots
│   │   ├── agent.py            # Autonomous trading agent (4 modes)
│   │   ├── backtest.py         # Walk-forward backtester (same rules as agent)
│   │   ├── ml_scorer.py        # GradientBoosting P(positive forward return)
│   │   ├── scan_history.py     # Score history + ML training labels
│   │   ├── performance_tracker.py  # Sharpe/Sortino/DD/expectancy metrics
│   │   ├── correlation_filter.py   # Reject correlated entries
│   │   ├── event_filter.py         # Earnings blackout window
│   │   ├── news_sentiment.py       # Headline sentiment (lexicon, no API keys)
│   │   ├── data_fetcher.py         # SQLite-cached yfinance fetcher
│   │   ├── alerts.py / watchlist.py / benchmark.py / market_hours.py
│   │   └── position_sizer.py
│   └── tests/                   # pytest suite (temp DB, safe to run anytime)
└── frontend/
    └── src/
        ├── App.jsx                    # Scanner dashboard + tabs
        └── components/
            ├── PaperTradingPanel.jsx  # Manual paper trading
            └── AgentPanel.jsx        # Agent control center
```

## Features

### Scanner
- **Multi-region stock selection**: Germany (DAX 40), Europe (Euro Stoxx 50), USA (S&P 500)
- **9-component breakout score (0–100)**: momentum, trend, volatility, volume, RSI, MACD, fundamentals, live sector-ETF vs benchmark momentum, earnings surprise
- **ML probability overlay**: GradientBoosting model (trained on your own scan history + forward returns) blended 60/40 with the rule score; falls back to a calibrated heuristic until enough data accumulates
- **Score history**: every scan is snapshotted to SQLite (score evolution, drop alerts, ML training data)
- **7-day forecasts** with confidence bands; interactive leaderboard + live charts


### Paper Trading (realistic simulation)
- EUR account (€10k), €1 commission, 0.05% slippage, FX conversion for USD/GBP stocks
- **Server-side 24/7 monitoring** (APScheduler, every 2 min): stops and targets fire even with no browser open
- **Intraday-aware fills**: stops fill at the trigger level, or at the open when the session gaps through
- **Trailing stops** (ratchet up, never down), **time stops**, ATR-based stop planning
- **Risk & performance metrics**: Sharpe, Sortino, max drawdown, expectancy, profit factor, win rate
- Risk-based position sizing with Kelly reference; trade journal (source/rationale/score at entry)

### Autonomous Agent
- **Deterministic decision core** (rules + ML — no LLM in the trade loop, so every decision is auditable and backtestable)
- **4 modes**: `OFF` → `SIGNAL_ONLY` (alerts only) → `SEMI_AUTO` (agent proposes, you approve in the UI) → `FULL_AUTO` (trades within guardrails)
- **Entry gates**: score ≥ 70, ML ≥ 0.60, market regime (S&P > 200d MA), correlation < 0.7 vs open positions, earnings blackout (5 days), position limits
- **Guardrails**: max 5 positions, 1.5% risk/trade, 10% max position, daily-loss halt (3%), drawdown pause (10%)
- **Exits**: 2×ATR stop, 2R target, 8% trailing, 20-day time stop, score-collapse exit
- **Full decision log**: every action — including "decided NOT to trade" and why — is persisted and shown in the UI
- **Backtester**: replays the same rules over cached candles (`POST /api/agent/backtest`)

> ⚠️ **Honest expectations**: no agent can guarantee profits. This one enforces discipline and risk caps, and its paper results are measured honestly. Validate in SIGNAL_ONLY for 4–6 weeks before trusting SEMI_AUTO, and complete ~90 days of paper trading before considering anything live.

## Running the Application
1. `./run.sh`
2. Open http://localhost:5173/
3. Scanner tab → pick a region, click stocks for live prices + forecasts
4. Paper Trading tab → place virtual orders (one-click prefill from the Top 25)
5. Agent tab → choose a mode, run cycles, approve/reject proposals

## Testing

```bash
backend/venv/bin/python -m pytest backend/tests -v   # safe: uses a temp DB
```

## API Endpoints (full list at /docs)
- Scanner: `GET /api/dashboard`, `GET /api/strategy/rankings`, `GET /api/scan/history`, `GET /api/scan/score-change?ticker=…`, `GET /api/ml/status`, `POST /api/ml/retrain`, `GET /api/news/sentiment?symbol=…`
- Paper: `GET /api/paper/portfolio|metrics|equity-curve|events`, `POST /api/paper/open|close|reset|suggest-size`
- Agent: `GET /api/agent/status|log|queue`, `POST /api/agent/mode|cycle/run|approve/{id}|reject/{id}|backtest`
- System: `GET /api/scheduler/status`, `POST /api/scheduler/monitor/run`, `GET /api/health`
- Watchlist/Alerts: `GET|POST /api/watchlist…`, `GET /api/alerts…`

## Key environment variables (optional)
- `SCHEDULER_ENABLED=0` — disable the background scheduler
- `MONITOR_INTERVAL_MIN` — stop/target check cadence (default 2)
- `AGENT_MODE` — default agent mode (default OFF; change via the UI/API)
- `AGENT_MIN_SCORE`, `AGENT_RISK_PER_TRADE_PCT`, … — agent thresholds (see `backend/services/agent.py`)

## Notes
- First scan of a region takes a few minutes (populates the SQLite candle cache); subsequent scans are fast.
- Logs: `.runtime/backend.log` / `.runtime/frontend.log` · PIDs: `.runtime/*.pid`
- Data is delayed ~15 min via yfinance — fine for swing trading, not for HFT.
- Manual setup alternative:
  ```bash
  python3 -m venv backend/venv
  backend/venv/bin/pip install -r requirements.txt
  backend/venv/bin/python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
  cd frontend && npm install && npm run dev -- --host 0.0.0.0 --port 5173
  ```
