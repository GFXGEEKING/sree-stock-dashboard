"""Data streaming module for live stock prices with yfinance."""

import asyncio
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)

STOCK_UNIVERSE = {
    "Germany": ["ALV.DE", "SAP.DE", "SY1.DE", "BMW.DE", "DAI.DE",
                "BAY.DE", "DB1.DE", "FRE.DE", "LHA.DE", "MTX.DE"],
    "Europe": ["ASML.DE", "ORA.PA", "AZN.L", "ROG.SW", "SIE.DE"],
    "USA": ["AAPL", "MSFT", "GOOG", "AMZN", "TSLA", "NVDA",
            "META", "BRK-B", "JPM", "JNJ"],
}

logger = logging.getLogger(__name__)


class DataStreamer:
    def __init__(self, cache_ttl_minutes: int = 30):
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.last_update: Dict[str, datetime] = {}
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self.rate_limit_delay = 0.5

    def get_region_tickers(self) -> Dict[str, List[str]]:
        return STOCK_UNIVERSE

    @property
    def total_ticker_count(self) -> int:
        return sum(len(t) for t in STOCK_UNIVERSE.values())

    async def _rate_limit(self):
        await asyncio.sleep(self.rate_limit_delay)

    def _fetch(self, ticker: str) -> Dict[str, Any]:
        try:
            obj = yf.Ticker(ticker)
            hist = obj.history(period="30d")
            info = obj.info

            price = None
            change_pct = None
            if "regularMarketPrice" in info and info["regularMarketPrice"] is not None:
                price = info["regularMarketPrice"]
                change_pct = info.get("regularMarketChangePercent")
            if price is None and not hist.empty:
                close_series = hist["Close"].dropna()
                if not close_series.empty:
                    price = float(close_series.iloc[-1])
                    if len(close_series) > 1:
                        prev = float(close_series.iloc[-2])
                        change_pct = (price - prev) / prev * 100.0 if prev else None

            return {
                "ticker": ticker, "price": price, "daily_change_pct": change_pct,
                "last_updated": datetime.utcnow().isoformat(),
                "historical_data": hist, "info": info, "success": True
            }
        except Exception as e:
            logger.error(f"Error fetching {ticker}: {e}")
            return {"ticker": ticker, "price": None, "daily_change_pct": None,
                    "last_updated": datetime.utcnow().isoformat(),
                    "historical_data": pd.DataFrame(), "info": {},
                    "success": False, "error": str(e)}

    async def update_single(self, ticker: str) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch, ticker)

    async def update_all(self) -> Dict[str, Any]:
        all_tickers = [t for region in STOCK_UNIVERSE.values() for t in region]
        results = {}
        for ticker in all_tickers:
            data = await self.update_single(ticker)
            results[ticker] = data
            if data["success"]:
                self.cache[ticker] = data
                self.last_update[ticker] = datetime.utcnow()
        return {"updated_at": datetime.utcnow().isoformat(),
                "total_tickers": len(all_tickers),
                "successfully_updated": sum(1 for v in results.values() if v.get("success")),
                "results": results}

    async def get_latest(self, ticker: str) -> Optional[Dict[str, Any]]:
        if ticker in self.cache and ticker in self.last_update:
            age = datetime.utcnow() - self.last_update[ticker]
            if age < self.cache_ttl:
                return self.cache[ticker]
        fresh = await self.update_single(ticker)
        if fresh.get("success"):
            self.cache[ticker] = fresh
            self.last_update[ticker] = datetime.utcnow()
        return fresh

    async def get_history(self, ticker: str, days: int = 30) -> Optional[pd.Series]:
        data = await self.get_latest(ticker)
        if not data or data["historical_data"].empty:
            return None
        return data["historical_data"]["Close"].tail(days)


data_streamer = DataStreamer()