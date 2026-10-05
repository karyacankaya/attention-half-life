"""Unit tests on synthetic curves whose half-life is known in advance."""

import math

import numpy as np
import pandas as pd
import pytest

from src.halflife import (
    compute_baseline,
    compute_metrics,
    normalized_curve,
)

EVENT_DAY = 40  # index of the event inside the synthetic series


def synthetic(
    half_life: float,
    baseline: float = 100.0,
    height: float = 10_000.0,
    days: int = 400,
    noise: float = 0.0,
    seed: int = 0,
) -> tuple[pd.Series, pd.Timestamp]:
    """Flat baseline, then an exponential decay with a known half-life from EVENT_DAY on."""
    dates = pd.date_range("2024-01-01", periods=days, freq="D")
    t = np.arange(days, dtype=float)
    views = np.full(days, float(baseline))
    after = t >= EVENT_DAY
    views[after] += height * 0.5 ** ((t[after] - EVENT_DAY) / half_life)
    if noise:
        rng = np.random.default_rng(seed)
        views = np.clip(views + rng.normal(0, noise, days), 0, None)
    return pd.Series(views, index=dates), dates[EVENT_DAY]


@pytest.mark.parametrize("true_hl", [2, 5, 10, 30])
def test_recovers_known_half_life(true_hl):
    views, event = synthetic(true_hl)
    m = compute_metrics(views, event)
    assert m["status"] == "ok"
    assert abs(m["half_life"] - true_hl) <= 1


def test_robust_to_noise():
    views, event = synthetic(10, noise=50)
    m = compute_metrics(views, event)
    assert abs(m["half_life"] - 10) <= 1


def test_baseline_is_prior_median():
    views, event = synthetic(5, baseline=250)
    assert compute_baseline(views, event) == pytest.approx(250)


def test_new_article_gets_baseline_one():
    views, event = synthetic(5)
    new_article = views[event - pd.Timedelta(days=3):]  # only 3 days of history
    assert compute_baseline(new_article, event) == 1.0


def test_censored_when_attention_never_halves():
    dates = pd.date_range("2024-01-01", periods=200, freq="D")
    views = pd.Series(100.0, index=dates)
    views.iloc[EVENT_DAY:] = 5_000.0  # jumps up and stays up
    m = compute_metrics(views, dates[EVENT_DAY])
    assert m["censored"] is True
    assert math.isnan(m["half_life"])


def test_no_peak_when_nothing_happens():
    dates = pd.date_range("2024-01-01", periods=120, freq="D")
    views = pd.Series(100.0, index=dates)
    m = compute_metrics(views, dates[EVENT_DAY])
    assert m["status"] == "no_peak"


def test_peak_found_after_slow_rise():
    views, event = synthetic(5)
    # the real peak happens 4 days after the reported event date
    m = compute_metrics(views, event - pd.Timedelta(days=4))
    assert 3 <= m["rise_days"] <= 5


def test_counts_secondary_peak():
    views, event = synthetic(3)
    revival = pd.date_range(event + pd.Timedelta(days=60), periods=5, freq="D")
    views[revival] += 6_000  # a 5-day revival worth ~60% of the main peak
    m = compute_metrics(views, event)
    assert m["secondary_peaks"] == 1
    assert abs(m["half_life"] - 3) <= 1  # main half-life unaffected


def test_exponential_curve_prefers_exponential_fit():
    views, event = synthetic(7)
    m = compute_metrics(views, event)
    assert m["best_fit"] == "exponential"
    assert m["exp_tau"] * math.log(2) == pytest.approx(7, abs=1.5)


def test_power_law_curve_prefers_power_law_fit():
    dates = pd.date_range("2024-01-01", periods=400, freq="D")
    t = np.arange(400, dtype=float)
    views = np.full(400, 100.0)
    after = t >= EVENT_DAY
    views[after] += 10_000 * (1 + (t[after] - EVENT_DAY) / 2) ** -1.2
    m = compute_metrics(pd.Series(views, index=dates), dates[EVENT_DAY])
    assert m["best_fit"] == "power_law"


def test_normalized_curve_peaks_at_one_on_day_zero():
    views, event = synthetic(5)
    curve = normalized_curve(views, event)
    assert curve.idxmax() == 0
    assert curve.max() == pytest.approx(1.0)
