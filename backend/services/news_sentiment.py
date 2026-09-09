"""News sentiment: yfinance headline scoring with a lexicon fallback.

Uses yfinance's built-in news when available. Each headline gets a
simple polarity score from a finance-tuned lexicon (no external API
keys, no model downloads).

Output: -1.0 .. +1.0 aggregated sentiment for a ticker, cached in
memory for 30 minutes to stay within rate limits.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

CACHE_TTL_S = 30 * 60
_MAX_HEADLINES = 10

_CACHE: Dict[str, tuple] = {}  # ticker -> (timestamp, sentiment)

# Finance-tuned polarity lexicon (short, fast, no deps)
_POSITIVE = {
    "beat": 2, "beats": 2, "surge": 2, "surges": 2, "soar": 2, "soars": 2,
    "rally": 2, "record": 2, "upgrade": 2, "upgraded": 2, "outperform": 2,
    "buy": 1, "growth": 1, "profit": 1, "gains": 1, "gain": 1, "rise": 1,
    "rises": 1, "jump": 1, "jumps": 1, "strong": 1, "bullish": 2,
    "dividend": 1, "buyback": 1, "expansion": 1, "breakthrough": 2,
    "approval": 1, "wins": 1, "win": 1, "contract": 1, "raises": 1,
    "tops": 2, "higher": 1, "optimistic": 1, "rebound": 1, "boost": 1,
}
_NEGATIVE = {
    "miss": -2, "misses": -2, "plunge": -2, "plunges": -2, "crash": -3,
    "sink": -2, "sinks": -2, "downgrade": -2, "underperform": -2, "sell": -1,
    "loss": -1, "losses": -1, "fall": -1, "falls": -1, "drop": -1,
    "drops": -1, "weak": -1, "bearish": -2, "cut": -1, "cuts": -1,
    "layoff": -2, "layoffs": -2, "probe": -2, "investigation": -2,
    "lawsuit": -2, "recall": -2, "warning": -2, "warns": -2, "fraud": -3,
    "bankruptcy": -3, "lower": -1, "decline": -1, "slump": -2,
    "tumble": -2, "tumbles": -2, "guidance": -1, "delisted": -3,
}


def _score_text(text: str) -> float:
    """Lexicon polarity for one headline, normalized to -1..+1."""
    if not text:
        return 0.0
    words = str(text).lower().replace("-", " ").split()
    score = 0.0
    hits = 0
    for w in words:
        w = w.strip(".,!?;:()'\"")
        if w in _POSITIVE:
            score += _POSITIVE[w]
            hits += 1
        elif w in _NEGATIVE:
            score += _NEGATIVE[w]
            hits += 1
    if hits == 0:
        return 0.0
    return max(-1.0, min(1.0, score / (3.0 * hits)))


def _fetch_headlines(symbol: str) -> List[str]:
    """Latest yfinance headlines for a ticker (best effort)."""
    import yfinance as yf

    try:
        news = yf.Ticker(symbol).news or []
        out = []
        for item in news[:_MAX_HEADLINES]:
            # yfinance news items have varied shapes across versions
            title = None
            if isinstance(item, dict):
                title = item.get("title") or item.get("content", {}).get("title")
            if title:
                out.append(str(title))
        return out
    except Exception as e:
        logger.debug(f"news fetch failed for {symbol}: {e}")
        return []



def get_sentiment(symbol: str, force_refresh: bool = False) -> Dict:
    """Aggregated sentiment -1..+1 for a ticker (cached 30 min)."""
    sym = (symbol or "").upper().strip()
    now = time.time()
    if not force_refresh and sym in _CACHE:
        ts, val = _CACHE[sym]
        if now - ts < CACHE_TTL_S:
            return {
                "symbol": sym, "sentiment": val, "headlines": 0, "cached": True,
            }
    headlines = _fetch_headlines(sym)
    if not headlines:
        _CACHE[sym] = (now, 0.0)
        return {"symbol": sym, "sentiment": 0.0, "headlines": 0, "cached": False}
    scores = [_score_text(h) for h in headlines]
    # Weighted average (most recent headline first — yfinance returns newest first)
    weights = [1.0 / (i + 1) for i in range(len(scores))]
    weighted = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
    sentiment = round(max(-1.0, min(1.0, weighted)), 3)
    _CACHE[sym] = (now, sentiment)
    return {
        "symbol": sym,
        "sentiment": sentiment,
        "headlines": len(headlines),
        "cached": False,
        "sample": headlines[:3],
    }


def sentiment_bonus(sentiment: Optional[float], cap: float = 2.0) -> float:
    """Map sentiment to a small scoring bonus (-cap..+cap, like the
    earnings_surprise component)."""
    if sentiment is None:
        return 0.0
    return round(max(-cap, min(cap, sentiment * cap)), 2)


def bulk_sentiment(symbols: List[str]) -> Dict[str, Dict]:
    return {s: get_sentiment(s) for s in symbols}


def clear_cache() -> None:
    _CACHE.clear()


__all__ = [
    "get_sentiment", "bulk_sentiment", "sentiment_bonus", "clear_cache",
    "_score_text",
]
