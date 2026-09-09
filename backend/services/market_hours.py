"""Multi-region market hours and status.

Tracks whether each major exchange is open right now and computes
countdown to next open / close.
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Each market: name -> (timezone, open_weekday, close_weekday, open, close)
# Times expressed in their own local time.
MARKETS: Dict[str, Dict] = {
    "XETRA": {
        "name": "XETRA / Frankfurt",
        "region": "DE",
        "tz": "Europe/Berlin",
        "open": dtime(9, 0),
        "close": dtime(17, 30),
        "country_flag": "🇩🇪",
    },
    "LSE": {
        "name": "London Stock Exchange",
        "region": "UK",
        "tz": "Europe/London",
        "open": dtime(8, 0),
        "close": dtime(16, 30),
        "country_flag": "🇬🇧",
    },
    "EPA": {
        "name": "Euronext Paris",
        "region": "FR",
        "tz": "Europe/Paris",
        "open": dtime(9, 0),
        "close": dtime(17, 30),
        "country_flag": "🇫🇷",
    },
    "AMS": {
        "name": "Euronext Amsterdam",
        "region": "NL",
        "tz": "Europe/Amsterdam",
        "open": dtime(9, 0),
        "close": dtime(17, 30),
        "country_flag": "🇳🇱",
    },
    "BME": {
        "name": "BME Madrid",
        "region": "ES",
        "tz": "Europe/Madrid",
        "open": dtime(9, 0),
        "close": dtime(17, 30),
        "country_flag": "🇪🇸",
    },
    "MIL": {
        "name": "Borsa Italiana",
        "region": "IT",
        "tz": "Europe/Rome",
        "open": dtime(9, 0),
        "close": dtime(17, 30),
        "country_flag": "🇮🇹",
    },
    "NYSE": {
        "name": "New York Stock Exchange",
        "region": "US",
        "tz": "America/New_York",
        "open": dtime(9, 30),
        "close": dtime(16, 0),
        "country_flag": "🇺🇸",
    },
    "NASDAQ": {
        "name": "NASDAQ",
        "region": "US",
        "tz": "America/New_York",
        "open": dtime(9, 30),
        "close": dtime(16, 0),
        "country_flag": "🇺🇸",
    },
}

# Public holidays (rough - skipping dynamic ones). For 2024-2026.
_HOLIDAYS = {
    "XETRA": [
        "2024-01-01", "2024-03-29", "2024-04-01", "2024-05-01",
        "2024-10-03", "2024-12-24", "2024-12-25", "2024-12-26", "2024-12-31",
        "2025-01-01", "2025-04-18", "2025-04-21", "2025-05-01",
        "2025-10-03", "2025-12-24", "2025-12-25", "2025-12-26", "2025-12-31",
        "2026-01-01", "2026-04-03", "2026-04-06", "2026-05-01",
        "2026-10-03", "2026-12-24", "2026-12-25", "2026-12-26",
    ],
    "LSE": [
        "2024-01-01", "2024-03-29", "2024-04-01", "2024-05-06",
        "2024-08-26", "2024-12-25", "2024-12-26",
        "2025-01-01", "2025-04-18", "2025-04-21", "2025-05-05",
        "2025-08-25", "2025-12-25", "2025-12-26",
        "2026-01-01", "2026-04-03", "2026-04-06", "2026-05-04",
        "2026-08-31", "2026-12-25", "2026-12-28",
    ],
    "EPA": [
        "2024-01-01", "2024-04-01", "2024-05-01", "2024-12-25",
        "2025-01-01", "2025-04-21", "2025-05-01", "2025-12-25",
        "2026-01-01", "2026-04-06", "2026-05-01", "2026-12-25",
    ],
    "AMS": [
        "2024-01-01", "2024-03-29", "2024-04-01", "2024-05-01",
        "2024-12-25", "2024-12-26",
        "2025-01-01", "2025-04-18", "2025-04-21", "2025-05-05",
        "2025-12-25", "2025-12-26",
        "2026-01-01", "2026-04-03", "2026-04-06", "2026-12-25", "2026-12-26",
    ],
    "BME": [
        "2024-01-01", "2024-01-06", "2024-03-29", "2024-05-01", "2024-10-12",
        "2024-11-01", "2024-12-06", "2024-12-25",
        "2025-01-01", "2025-01-06", "2025-04-18", "2025-05-01", "2025-10-12",
        "2025-11-01", "2025-12-06", "2025-12-25",
        "2026-01-01", "2026-01-06", "2026-04-03", "2026-05-01", "2026-10-12",
        "2026-11-01", "2026-12-06", "2026-12-25",
    ],
    "MIL": [
        "2024-01-01", "2024-04-01", "2024-05-01", "2024-12-25", "2024-12-26",
        "2025-01-01", "2025-04-21", "2025-05-01", "2025-12-25", "2025-12-26",
        "2026-01-01", "2026-04-06", "2026-05-01", "2026-12-25", "2026-12-26",
    ],
    "NYSE": [
        "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
        "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
        "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
        "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
        "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    ],
    "NASDAQ": [
        "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
        "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
        "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
        "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
        "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    ],
}


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------


def _local_now(tz_name: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        # Fallback: use UTC offset
        offsets = {
            "Europe/Berlin": 1, "Europe/London": 0, "Europe/Paris": 1,
            "Europe/Amsterdam": 1, "Europe/Madrid": 1, "Europe/Rome": 1,
            "America/New_York": -5,
        }
        off = offsets.get(tz_name, 0)
        tz = timezone(timedelta(hours=off))
    return datetime.now(tz)


def _is_holiday(market: str, dt: datetime) -> bool:
    return dt.strftime("%Y-%m-%d") in _HOLIDAYS.get(market, [])


def market_status(market: str = "XETRA") -> Dict:
    """Return current open/closed state for a market."""
    if market not in MARKETS:
        return {"market": market, "error": "unknown market"}
    cfg = MARKETS[market]
    now = _local_now(cfg["tz"])
    weekday = now.weekday()  # 0=Mon
    open_t = cfg["open"]
    close_t = cfg["close"]
    is_weekend = weekday >= 5
    is_holiday = _is_holiday(market, now)
    in_session = (
        not is_weekend
        and not is_holiday
        and open_t <= now.time() < close_t
    )
    # Countdown
    countdown = None
    if not in_session:
        # Find next open
        if is_weekend:
            days_to_mon = 7 - weekday
        else:
            days_to_mon = 0
        # Naive: estimate next open
        if in_session is False and now.time() < open_t and not is_weekend and not is_holiday:
            target = now.replace(hour=open_t.hour, minute=open_t.minute, second=0, microsecond=0)
        else:
            # Tomorrow morning
            target = (now + timedelta(days=1)).replace(
                hour=open_t.hour, minute=open_t.minute, second=0, microsecond=0
            )
        countdown = (target - now).total_seconds()
    else:
        # Time until close
        target = now.replace(hour=close_t.hour, minute=close_t.minute, second=0, microsecond=0)
        countdown = (target - now).total_seconds()
    return {
        "market": market,
        "name": cfg["name"],
        "region": cfg["region"],
        "country_flag": cfg["country_flag"],
        "is_open": bool(in_session),
        "is_weekend": bool(is_weekend),
        "is_holiday": bool(is_holiday),
        "local_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "open_time": open_t.strftime("%H:%M"),
        "close_time": close_t.strftime("%H:%M"),
        "countdown_seconds": int(countdown) if countdown is not None else None,
        "countdown_human": _humanize(int(countdown)) if countdown is not None else None,
    }


def _humanize(seconds: int) -> str:
    if seconds is None:
        return ""
    if seconds < 0:
        return "closed"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h > 24:
        d = h // 24
        h = h % 24
        return f"{d}d {h}h {m}m"
    return f"{h}h {m}m"


def all_market_status() -> List[Dict]:
    return [market_status(m) for m in MARKETS.keys()]


def market_for_exchange(exchange: str) -> str:
    """Map yfinance exchange code -> our market key."""
    e = (exchange or "").upper()
    if e in ("XETRA", "FRA", "GER", "DEU"):
        return "XETRA"
    if e in ("LSE", "LON", "GB", "UK"):
        return "LSE"
    if e in ("EPA", "PAR", "FR"):
        return "EPA"
    if e in ("AMS", "AEB", "NL"):
        return "AMS"
    if e in ("BME", "MCE", "MAD", "ES"):
        return "BME"
    if e in ("MIL", "MI", "IT"):
        return "MIL"
    if e in ("NMS", "NAS", "NASDAQ", "NYQ", "NYSE"):
        return "NYSE" if e == "NYQ" else "NASDAQ"
    return "XETRA"


__all__ = [
    "MARKETS",
    "market_status",
    "all_market_status",
    "market_for_exchange",
]
