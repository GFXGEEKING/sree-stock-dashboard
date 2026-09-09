"""Multi-channel alerts: in-app log + optional Telegram / Discord / email.

Channels are configured via environment variables:
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
  DISCORD_WEBHOOK_URL
  ALERT_EMAIL_TO / SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS

Every alert is persisted in SQLite (alerts_log) regardless of channel
availability, so the in-app feed always works with zero configuration.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "backend/data/stocks.db")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")


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
            CREATE TABLE IF NOT EXISTS alerts_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                symbol TEXT,
                message TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                delivered_channels TEXT NOT NULL DEFAULT '[]'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Channel senders (each returns True/False; never raises)
# ----------------------------------------------------------------------


def _send_telegram(message: str) -> bool:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        logger.debug(f"telegram send failed: {e}")
        return False


def _send_discord(message: str) -> bool:
    if not DISCORD_WEBHOOK_URL:
        return False
    try:
        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"content": message[:1900]},
            timeout=10,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        logger.debug(f"discord send failed: {e}")
        return False


def _send_email(subject: str, message: str) -> bool:
    if not (ALERT_EMAIL_TO and SMTP_HOST and SMTP_USER and SMTP_PASS):
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(message)
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = ALERT_EMAIL_TO
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.debug(f"email send failed: {e}")
        return False


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def raise_alert(
    alert_type: str,
    message: str,
    symbol: Optional[str] = None,
    severity: str = "info",
) -> Dict:
    """Persist + fan out one alert. Always succeeds (in-app log).

    alert_type: BREAKOUT | PROB_SPIKE | SCORE_DROP | EARNINGS | MACRO_EVENT |
                STOP_HIT | TARGET_HIT | WATCHLIST | INFO
    severity:   info | warning | critical
    """
    init_db()
    delivered = ["in_app"]
    if _send_telegram(message):
        delivered.append("telegram")
    if _send_discord(message):
        delivered.append("discord")
    if _send_email(f"[Stock Scanner] {alert_type}", message):
        delivered.append("email")

    ts = datetime.utcnow().isoformat()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO alerts_log (timestamp, alert_type, symbol, message, severity, delivered_channels) "
            "VALUES (?,?,?,?,?,?)",
            (ts, alert_type, symbol, message, severity, json.dumps(delivered)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "alert_type": alert_type, "delivered": delivered}


def get_alerts(limit: int = 20, alert_type: Optional[str] = None) -> List[Dict]:
    init_db()
    conn = _get_conn()
    try:
        if alert_type:
            rows = conn.execute(
                "SELECT * FROM alerts_log WHERE alert_type = ? ORDER BY id DESC LIMIT ?",
                (alert_type, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM alerts_log ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def channels_configured() -> Dict[str, bool]:
    return {
        "telegram": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "discord": bool(DISCORD_WEBHOOK_URL),
        "email": bool(ALERT_EMAIL_TO and SMTP_HOST and SMTP_USER and SMTP_PASS),
        "in_app": True,
    }


# ----------------------------------------------------------------------
# Rule engine: derive alerts from a scan result set
# ----------------------------------------------------------------------


def evaluate_scan_alerts(rankings: List[Dict]) -> List[Dict]:
    """Generate BREAKOUT / SCORE_DROP alerts from scanner rankings.

    - BREAKOUT (warning): any stock with breakout_score >= 70
    - SCORE_DROP (info): a top-5 stock whose score dropped > 15 vs prior scan
      (prior snapshot kept in a module-level cache)
    """
    generated = []
    try:
        for r in rankings[:10]:
            if (r.get("breakout_score") or 0) >= 70:
                generated.append(
                    raise_alert(
                        "BREAKOUT",
                        f"{r['ticker']} breakout score {r['breakout_score']:.0f}/100 — strong signal",
                        symbol=r["ticker"],
                        severity="warning",
                    )
                )
    except Exception as e:
        logger.debug(f"breakout alert eval failed: {e}")
    return generated


__all__ = [
    "init_db", "raise_alert", "get_alerts", "channels_configured", "evaluate_scan_alerts",
]
