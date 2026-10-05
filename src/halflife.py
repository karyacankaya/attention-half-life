"""Attention half-life metrics for a single event.

Method (see CLAUDE.md, "Temel yöntem"):
  1. baseline  = median daily views over the 30 days before the event date
                 (1 if the article has fewer than 7 days of prior history)
  2. excess    = views - baseline, clipped at 0
  3. smoothing = 3-day centered rolling mean
  4. peak      = max smoothed excess within ±7 days of the event date
  5. half-life = days from the peak until smoothed excess first drops below 50% of the
                 peak (censored if that does not happen within 365 days)
  6. extras    = pre-peak rise, secondary peaks (>30% of the main peak after the first
                 drop below 50%)
  7. decay fit = exponential vs power-law on the post-peak curve, compared by AIC
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
from scipy.signal import find_peaks

BASELINE_DAYS = 30
MIN_HISTORY_DAYS = 7
PEAK_WINDOW_DAYS = 7
SMOOTH_DAYS = 3
MAX_FOLLOW_DAYS = 365
HALF = 0.5
SECONDARY_PEAK_SHARE = 0.3
SECONDARY_PEAK_MIN_GAP = 7  # days between distinct secondary peaks

DAY = pd.Timedelta(days=1)


def compute_baseline(views: pd.Series, event_date: pd.Timestamp) -> float:
    """Median daily views over the 30 days before ``event_date``.

    Returns 1.0 when fewer than 7 of those days exist in the series (new article).
    """
    before = views[event_date - BASELINE_DAYS * DAY : event_date - DAY]
    if len(before) < MIN_HISTORY_DAYS:
        return 1.0
    return float(before.median())


def smoothed_excess(views: pd.Series, baseline: float) -> pd.Series:
    """Views above the baseline (negative values clipped to 0), 3-day centered mean."""
    excess = (views - baseline).clip(lower=0)
    return excess.rolling(SMOOTH_DAYS, center=True, min_periods=1).mean()


def find_peak(excess: pd.Series, event_date: pd.Timestamp) -> tuple[pd.Timestamp | None, float]:
    """Day and height of the highest smoothed excess within ±7 days of the event."""
    window = excess[event_date - PEAK_WINDOW_DAYS * DAY : event_date + PEAK_WINDOW_DAYS * DAY]
    if window.empty or window.max() <= 0:
        return None, 0.0
    return window.idxmax(), float(window.max())


def half_life_days(
    excess: pd.Series, peak_day: pd.Timestamp, peak: float
) -> tuple[float, bool, pd.Timestamp | None]:
    """Days from the peak until excess first falls below half of it.

    Returns (half_life, censored, crossing_day). If no crossing happens within 365 days
    (or the data ends first), half_life is NaN and censored is True.
    """
    follow = excess[peak_day : peak_day + MAX_FOLLOW_DAYS * DAY]
    below = follow[follow < HALF * peak]
    if below.empty:
        return math.nan, True, None
    crossing = below.index[0]
    return float((crossing - peak_day).days), False, crossing


def count_secondary_peaks(excess: pd.Series, crossing_day: pd.Timestamp | None, peak: float) -> int:
    """Number of later peaks above 30% of the main peak, after the first drop below 50%."""
    if crossing_day is None:
        return 0
    tail = excess[crossing_day : crossing_day + MAX_FOLLOW_DAYS * DAY].to_numpy()
    if tail.size < 3:
        return 0
    idx, _ = find_peaks(
        tail, height=SECONDARY_PEAK_SHARE * peak, distance=SECONDARY_PEAK_MIN_GAP
    )
    return int(idx.size)


def _exp_model(d: np.ndarray, tau: float) -> np.ndarray:
    return np.exp(-d / tau)


def _power_model(d: np.ndarray, c: float, alpha: float) -> np.ndarray:
    return (1.0 + d / c) ** (-alpha)


def _aic(rss: float, n: int, k: int) -> float:
    return n * math.log(max(rss, 1e-12) / n) + 2 * k


def fit_decay(excess: pd.Series, peak_day: pd.Timestamp, peak: float) -> dict[str, Any]:
    """Fit exponential and power-law decay to the normalized post-peak curve.

    y(d) = excess(peak + d) / peak, for d = 0..365.
      exponential: y = exp(-d / tau)              (1 parameter; half-life = tau * ln 2)
      power-law:   y = (1 + d / c) ** (-alpha)    (2 parameters)
    """
    out: dict[str, Any] = {
        "exp_tau": math.nan, "exp_aic": math.nan,
        "pow_c": math.nan, "pow_alpha": math.nan, "pow_aic": math.nan,
        "best_fit": None,
    }
    post = excess[peak_day : peak_day + MAX_FOLLOW_DAYS * DAY]
    if len(post) < 10 or peak <= 0:
        return out
    d = np.arange(len(post), dtype=float)
    y = post.to_numpy(dtype=float) / peak
    n = len(y)

    try:
        (tau,), _ = curve_fit(_exp_model, d, y, p0=[7.0], bounds=([0.01], [1e4]), maxfev=10000)
        rss = float(np.sum((y - _exp_model(d, tau)) ** 2))
        out.update(exp_tau=float(tau), exp_aic=_aic(rss, n, 1))
    except (RuntimeError, ValueError):
        pass

    try:
        (c, alpha), _ = curve_fit(
            _power_model, d, y, p0=[2.0, 1.0], bounds=([0.01, 0.01], [1e4, 20.0]), maxfev=10000
        )
        rss = float(np.sum((y - _power_model(d, c, alpha)) ** 2))
        out.update(pow_c=float(c), pow_alpha=float(alpha), pow_aic=_aic(rss, n, 2))
    except (RuntimeError, ValueError):
        pass

    if not math.isnan(out["exp_aic"]) and not math.isnan(out["pow_aic"]):
        out["best_fit"] = "exponential" if out["exp_aic"] <= out["pow_aic"] else "power_law"
    return out


def compute_metrics(views: pd.Series, event_date: str | pd.Timestamp) -> dict[str, Any]:
    """All half-life metrics for one event's daily views series.

    Args:
        views: daily views indexed by date (one row per day).
        event_date: the day the event happened.

    Returns:
        dict with baseline, peak_date, peak_views, peak_excess, rise_days, half_life,
        censored, secondary_peaks, decay-fit parameters and a status flag.
    """
    event_date = pd.Timestamp(event_date)
    baseline = compute_baseline(views, event_date)
    excess = smoothed_excess(views, baseline)
    peak_day, peak = find_peak(excess, event_date)

    result: dict[str, Any] = {
        "baseline": baseline,
        "peak_date": None, "peak_views": math.nan, "peak_excess": 0.0,
        "rise_days": math.nan, "half_life": math.nan, "censored": False,
        "secondary_peaks": 0, "status": "no_peak",
    }
    if peak_day is None:
        result.update(fit_decay(excess, event_date, 0.0))
        return result

    hl, censored, crossing = half_life_days(excess, peak_day, peak)
    result.update(
        peak_date=peak_day.date().isoformat(),
        peak_views=float(views.get(peak_day, math.nan)),
        peak_excess=peak,
        rise_days=float((peak_day - event_date).days),
        half_life=hl,
        censored=censored,
        secondary_peaks=count_secondary_peaks(excess, crossing, peak),
        status="censored" if censored else "ok",
    )
    result.update(fit_decay(excess, peak_day, peak))
    return result


def normalized_curve(
    views: pd.Series, event_date: str | pd.Timestamp, days: int = 60
) -> pd.Series | None:
    """Smoothed excess aligned so the peak is day 0 and has value 1 (for plotting)."""
    event_date = pd.Timestamp(event_date)
    excess = smoothed_excess(views, compute_baseline(views, event_date))
    peak_day, peak = find_peak(excess, event_date)
    if peak_day is None:
        return None
    seg = excess[peak_day - 7 * DAY : peak_day + days * DAY] / peak
    seg.index = (seg.index - peak_day).days
    return seg
