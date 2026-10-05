"""Compute half-life metrics for every event in data/events.csv and draw the figures.

Usage:
    python -m src.analyze                       # all events, default paths
    python -m src.analyze --events my_events.csv

Outputs:
    data/results.csv                    one row per event with all metrics
    figures/forgetting_curves.png       normalized attention curve per event
    figures/half_life_by_event.png      half-life of every event, sorted
"""

from __future__ import annotations

import argparse
import math
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
import pandas as pd

from src.fetch import REPO_ROOT, ArticleNotFound, PageviewsClient
from src.halflife import HALF, compute_metrics, normalized_curve

DAY = pd.Timedelta(days=1)
LEAD_DAYS = 60       # history fetched before the event (baseline needs 30)
FOLLOW_DAYS = 365    # history fetched after the event

# Chart styling (validated reference palette, light mode)
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
GRID = "#e4e3df"
SERIES = "#2a78d6"


def load_events(path: Path) -> pd.DataFrame:
    """Read the event list and parse dates."""
    events = pd.read_csv(path, dtype=str)
    events["event_date"] = pd.to_datetime(events["event_date"])
    return events


def fetch_window(client: PageviewsClient, row: pd.Series) -> pd.Series:
    """Fetch views from 60 days before to 365 days after the event (capped at yesterday).

    The Pageviews API counts views per *title*, so an article that was renamed keeps its
    early views under the old title. Several titles can be given separated by "|"
    (e.g. "New title|Old title"); their daily views are summed. At least one must exist.
    """
    start = row.event_date - LEAD_DAYS * DAY
    end = min(row.event_date + FOLLOW_DAYS * DAY, pd.Timestamp(date.today()) - DAY)
    parts = []
    for title in (t.strip() for t in row.title.split("|") if t.strip()):
        try:
            parts.append(client.daily_views(title, row.project, start, end))
        except ArticleNotFound:
            continue
    if not parts:
        raise ArticleNotFound(row.title)
    return pd.concat(parts, axis=1).fillna(0).sum(axis=1).asfreq("D", fill_value=0)


def run(events_path: Path, results_path: Path, figures_dir: Path) -> pd.DataFrame:
    """Fetch, measure and plot every event. Returns the results table."""
    events = load_events(events_path)
    client = PageviewsClient()
    rows, curves = [], {}

    for row in events.itertuples(index=False):
        print(f"• {row.event_id:<35} ", end="", flush=True)
        try:
            views = fetch_window(client, row)
        except ArticleNotFound:
            print("NOT FOUND — check the title in events.csv")
            rows.append({**row._asdict(), "status": "not_found"})
            continue
        metrics = compute_metrics(views, row.event_date)
        rows.append({**row._asdict(), **metrics})
        curve = normalized_curve(views, row.event_date)
        if curve is not None:
            curves[row.event_id] = curve
        hl = metrics["half_life"]
        print(f"half-life = {hl:.0f} d" if not math.isnan(hl) else metrics["status"])

    results = pd.DataFrame(rows)
    results["event_date"] = pd.to_datetime(results["event_date"]).dt.date
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(results_path, index=False, float_format="%.4g")
    print(f"\nSaved {results_path}")

    figures_dir.mkdir(parents=True, exist_ok=True)
    ok = results[results["status"] == "ok"]
    if not ok.empty:
        plot_curves(ok, curves, figures_dir / "forgetting_curves.png")
        plot_half_lives(results, figures_dir / "half_life_by_event.png")
        print(f"Saved figures to {figures_dir}/")
        summary = ok.groupby("category")["half_life"].agg(["median", "count"])
        print("\nMedian half-life by category (days):")
        print(summary.sort_values("median").to_string())
    return results


def _style(ax: plt.Axes) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_SECONDARY, labelsize=8, length=0)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def plot_curves(ok: pd.DataFrame, curves: dict, out: Path) -> None:
    """Small multiples: each event's attention, peak = day 0 = 1.0, with its half-life."""
    ok = ok.sort_values("half_life")
    n = len(ok)
    cols = min(5, n)
    nrows = math.ceil(n / cols)
    fig, axes = plt.subplots(
        nrows, cols, figsize=(2.6 * cols, 2.1 * nrows), sharex=True, sharey=True,
        squeeze=False, facecolor=SURFACE,
    )
    for ax, row in zip(axes.flat, ok.itertuples()):
        curve = curves[row.event_id]
        _style(ax)
        ax.plot(curve.index, curve.to_numpy(), color=SERIES, linewidth=2)
        ax.axhline(HALF, color=INK_SECONDARY, linewidth=0.8, linestyle=(0, (3, 3)))
        ax.axvline(row.half_life, color=INK_SECONDARY, linewidth=0.8)
        ax.set_title(row.title.split("|")[0], fontsize=8.5, color=INK, loc="left")
        ax.text(0.97, 0.9, f"t½ = {row.half_life:.0f} d", transform=ax.transAxes,
                ha="right", fontsize=8, color=INK)
        ax.set_xlim(-7, 60)
        ax.set_ylim(0, 1.08)
    for ax in list(axes.flat)[n:]:
        ax.set_visible(False)
    fig.supxlabel("Days since peak", fontsize=9, color=INK_SECONDARY)
    fig.supylabel("Attention (share of peak)", fontsize=9, color=INK_SECONDARY)
    fig.suptitle("How fast attention fades — dashed line = half of the peak",
                 fontsize=11, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(out, dpi=160, facecolor=SURFACE)
    plt.close(fig)


def plot_half_lives(results: pd.DataFrame, out: Path) -> None:
    """Horizontal dot chart: half-life of every event, labelled with its category."""
    data = results[results["status"].isin(["ok", "censored"])].copy()
    data["plot_value"] = data["half_life"].fillna(FOLLOW_DAYS)
    data = data.sort_values("plot_value", ascending=False)
    labels = [f"{t.split('|')[0]}  ·  {c}" for t, c in zip(data["title"], data["category"])]

    fig, ax = plt.subplots(figsize=(8, 0.38 * len(data) + 1.2), facecolor=SURFACE)
    _style(ax)
    ax.grid(axis="y", visible=False)
    y = range(len(data))
    ok = data["status"] == "ok"
    ax.scatter(data.loc[ok, "plot_value"], [i for i, k in zip(y, ok) if k],
               s=60, color=SERIES, edgecolor=SURFACE, linewidth=2, zorder=3)
    ax.scatter(data.loc[~ok, "plot_value"], [i for i, k in zip(y, ok) if not k],
               s=60, facecolor=SURFACE, edgecolor=SERIES, linewidth=2, zorder=3)
    for i, v, k in zip(y, data["plot_value"], ok):
        ax.text(v * 1.08, i, f"{v:.0f} d" if k else "> 1 year", va="center",
                fontsize=8, color=INK)
    ax.set_yticks(list(y), labels, fontsize=8.5, color=INK)
    ax.set_xscale("log")
    ticks = [t for t in (1, 2, 5, 10, 20, 50, 100, 200, 365) if t <= data["plot_value"].max() * 2]
    ax.set_xticks(ticks, [str(t) for t in ticks])
    ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax.set_xlabel("Half-life in days (log scale)", fontsize=9, color=INK_SECONDARY)
    ax.set_title("Days until attention halves", fontsize=11, color=INK, loc="left")
    ax.set_xlim(left=max(0.8, data["plot_value"].min() * 0.7),
                right=data["plot_value"].max() * 2)
    fig.tight_layout()
    fig.savefig(out, dpi=160, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the attention half-life of events.")
    parser.add_argument("--events", type=Path, default=REPO_ROOT / "data" / "events.csv")
    parser.add_argument("--results", type=Path, default=REPO_ROOT / "data" / "results.csv")
    parser.add_argument("--figures", type=Path, default=REPO_ROOT / "figures")
    args = parser.parse_args()
    run(args.events, args.results, args.figures)


if __name__ == "__main__":
    main()
