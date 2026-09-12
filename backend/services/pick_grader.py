"""Pick grading: STRONG/GOOD/FAIR/WEAK/POOR + human-readable "why this pick".

Turns the scanner ranking row's quantitative fields (blended score, ML
probability, RSI zone, momentum, forecast trend, breakout components) into:
  * a letter grade bucket, and
  * a short list of plain-language reasons.
Pure functions - no I/O, fully sandbox-testable.
"""
from __future__ import annotations

from typing import Dict, List

GRADES = ("STRONG", "GOOD", "FAIR", "WEAK", "POOR")


def grade_from_score(score) -> str:
    """Bucket a blended/breakout score (0-100) into a grade."""
    try:
        s = float(score or 0)
    except (TypeError, ValueError):
        s = 0.0
    if s >= 75:
        return "STRONG"
    if s >= 65:
        return "GOOD"
    if s >= 55:
        return "FAIR"
    if s >= 45:
        return "WEAK"
    return "POOR"


def _rsi_reason(rsi) -> str:
    try:
        r = float(rsi)
    except (TypeError, ValueError):
        return ""
    if r < 30:
        return f"RSI {r:.0f} - oversold, potential bounce"
    if r < 50:
        return f"RSI {r:.0f} - neutral zone"
    if r < 70:
        return f"RSI {r:.0f} - bullish momentum zone"
    return f"RSI {r:.0f} - overbought, stretched"


def _prob_reason(prob) -> str:
    try:
        p = float(prob)
    except (TypeError, ValueError):
        return ""
    pct = p * 100.0
    if p >= 0.65:
        return f"High {pct:.0f}% probability target hit"
    if p >= 0.55:
        return f"Solid {pct:.0f}% probability target hit"
    if p >= 0.45:
        return f"Moderate {pct:.0f}% probability target hit"
    return f"Low {pct:.0f}% probability target hit"


def _signed(v, digits=1) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    return f"{'+' if x >= 0 else ''}{x:.{digits}f}%"


def build_why(row: Dict) -> List[str]:
    """Build 3-5 short reason strings from a ranking row's real fields."""
    why: List[str] = []
    comps = row.get("breakout_components") or {}

    prob = row.get("ml_probability")
    if prob is not None:
        reason = _prob_reason(prob)
        if reason:
            why.append(reason)

    trend = (row.get("forecast") or {}).get("trend")
    if trend is not None:
        why.append(f"7d forecast {_signed(trend)}")

    m3 = row.get("momentum_3m")
    if m3 is not None:
        x = float(m3)
        word = "Strong" if x >= 10 else "Steady" if x >= 3 else "Soft" if x >= 0 else "Negative"
        why.append(f"{word} 3m momentum ({_signed(m3)})")

    rsi_reason = _rsi_reason(row.get("rsi_14"))
    if rsi_reason:
        why.append(rsi_reason)

    if float(comps.get("trend", 0) or 0) >= 18:
        why.append("strong trend structure")
    if float(comps.get("volume", 0) or 0) >= 10:
        why.append("volume confirmation")
    if float(comps.get("sector_momentum", 0) or 0) >= 5:
        why.append("supportive sector backdrop")
    if float(comps.get("fundamentals", 0) or 0) >= 8:
        why.append("quality fundamentals")

    return why[:5]


def grade_pick(row: Dict) -> Dict:
    """Full grade payload for one scanner ranking row."""
    score = row.get("blended_score") or row.get("breakout_score") or row.get("composite_score") or 0
    grade = grade_from_score(score)
    why = build_why(row)
    return {
        "grade": grade,
        "score": round(float(score or 0), 1),
        "why": why,
        "summary": " \u00b7 ".join(why),
    }


__all__ = ["GRADES", "grade_from_score", "build_why", "grade_pick"]
