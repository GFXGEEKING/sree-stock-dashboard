"""7-day forecasting module using statsmodels/Linear Regression with confidence intervals."""

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

try:
    from prophet import Prophet
    HAS_PROPHET = True
except Exception:
    HAS_PROPHET = False

try:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    HAS_STATSMODELS = True
except Exception:
    HAS_STATSMODELS = False


def _prepare_prophet_df(series: pd.Series) -> pd.DataFrame:
    """Prepare series for Prophet: needs ds (date), y (value) columns."""
    df = pd.DataFrame({
        "ds": series.index if isinstance(series.index, pd.DatetimeIndex) else pd.date_range(
            periods=len(series), freq="D"
        ),
        "y": series.values
    })
    df["ds"] = pd.to_datetime(df["ds"])
    return df


def forecast_linear_regression(series: pd.Series, days: int = 7) -> Dict[str, Any]:
    """Simple linear regression trend forecast with confidence bands."""
    # Drop NaN to handle incomplete latest trading day
    series = series.dropna() if hasattr(series, "dropna") else series
    if len(series) < 2:
        last = float(series.iloc[-1]) if len(series) == 1 else 0.0
        return {"prices": [], "upper": [], "lower": [], "trend": 0.0, "last_price": round(last, 2), "model": "linear_regression"}

    x = np.arange(len(series), dtype=float)
    y = series.values.astype(float)
    
    # Fit y = slope * x + intercept using polyfit (properly handles intercept)
    slope, intercept = np.polyfit(x, y, 1)
    
    # Forecast future days
    future_x = np.arange(len(series), len(series) + days, dtype=float)
    predictions = intercept + slope * future_x
    
    # Simple confidence interval based on residual std
    fitted = intercept + slope * x
    residuals = y - fitted
    std_res = float(np.std(residuals)) if len(residuals) > 0 else 1.0
    
    z = 1.96  # 95% confidence
    upper = predictions + z * std_res
    lower = predictions - z * std_res
    
    last_price = float(series.iloc[-1])

    # Build future date labels (skip weekends so the chart looks like trading days)
    last_date = series.index[-1] if isinstance(series.index, pd.DatetimeIndex) else pd.Timestamp.today()
    if not isinstance(last_date, pd.Timestamp):
        last_date = pd.Timestamp(last_date)
    future_dates = []
    cursor = last_date
    added = 0
    while added < days:
        cursor = cursor + timedelta(days=1)
        if cursor.weekday() < 5:  # Mon-Fri only
            future_dates.append(cursor.strftime("%Y-%m-%d"))
            added += 1

    return {
        "dates": future_dates,
        "prices": [round(float(p), 2) for p in predictions],
        "upper": [round(float(u), 2) for u in upper],
        "lower": [round(float(l), 2) for l in lower],
        "trend": round(float(slope) * 7, 2),
        "last_price": round(last_price, 2),
        "model": "linear_regression"
    }


def forecast_prophet(series: pd.Series, days: int = 7) -> Dict[str, Any]:
    """Prophet time-series forecast with confidence intervals."""
    if not HAS_PROPHET:
        return {"error": "Prophet not available", "model": "prophet_missing"}
    
    df = _prepare_prophet_df(series)
    if df.empty or len(df) < 2:
        return {"prices": [], "upper": [], "lower": [], "trend": 0.0, "model": "prophet_empty"}
    
    try:
        model = Prophet(
            changepoint_prior_scale=0.05,
            seasonality_prior_scale=10,
            yearly_seasonality=False,
            weekly_seasonality=False,
            daily_seasonality=False
        )
        model.fit(df)
        
        future = model.make_future_dataframe(periods=days, freq="D")
        forecast = model.predict(future)
        
        predictions = forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(days)

        return {
            "dates": [d.strftime("%Y-%m-%d") for d in predictions["ds"]],
            "prices": [round(float(p), 2) for p in predictions["yhat"]],
            "upper": [round(float(u), 2) for u in predictions["yhat_upper"]],
            "lower": [round(float(l), 2) for l in predictions["yhat_lower"]],
            "trend": round(float(predictions["yhat"].mean() - df["y"].mean()), 2),
            "model": "prophet"
        }
    except Exception as e:
        logger.error(f"Prophet forecast error: {e}")
        return {"error": str(e), "model": "prophet_error"}


def forecast_statsmodels(series: pd.Series, days: int = 7) -> Dict[str, Any]:
    """Statsmodels Exponential Smoothing forecast."""
    if not HAS_STATSMODELS:
        return {"error": "Statsmodels not available", "model": "statsmodels_missing"}
    
    if len(series) < 2:
        return {"prices": [], "upper": [], "lower": [], "trend": 0.0, "model": "statsmodels_empty"}
    
    try:
        model = ExponentialSmoothing(
            series.values, trend="add", seasonal=None
        ).fit()
        
        forecast_result = model.forecast(days)
        last_price = float(series.iloc[-1])
        
        errors = model.resid
        std_err = float(np.std(errors)) if len(errors) > 0 else 1.0
        
        z = 1.96
        upper = forecast_result + z * std_err
        lower = forecast_result - z * std_err

        # Generate future date labels (trading days only)
        last_date = series.index[-1] if isinstance(series.index, pd.DatetimeIndex) else pd.Timestamp.today()
        if not isinstance(last_date, pd.Timestamp):
            last_date = pd.Timestamp(last_date)
        future_dates = []
        cursor = last_date
        added = 0
        while added < days:
            cursor = cursor + timedelta(days=1)
            if cursor.weekday() < 5:
                future_dates.append(cursor.strftime("%Y-%m-%d"))
                added += 1

        return {
            "dates": future_dates,
            "prices": [round(float(p), 2) for p in forecast_result],
            "upper": [round(float(u), 2) for u in upper],
            "lower": [round(float(l), 2) for l in lower],
            "trend": round(float(forecast_result.mean() - last_price), 2),
            "model": "statsmodels"
        }
    except Exception as e:
        logger.error(f"Statsmodels forecast error: {e}")
        return {"error": str(e), "model": "statsmodels_error"}


def forecast(series: pd.Series, days: int = 7, method: str = "auto") -> Dict[str, Any]:
    """Run forecast using the best available method."""
    if method == "linear_regression" or (not HAS_PROPHET and not HAS_STATSMODELS):
        return forecast_linear_regression(series, days)
    if HAS_PROPHET:
        return forecast_prophet(series, days)
    if HAS_STATSMODELS:
        return forecast_statsmodels(series, days)
    return forecast_linear_regression(series, days)