"""Cached client for the Wikimedia Pageviews API.

Every response is stored in a local SQLite cache (``data/cache/pageviews.sqlite``),
so the same request is never sent twice and interrupted runs can simply be restarted.

Example (command line):
    python -m src.fetch "2023 Kahramanmaraş depremleri" --project tr.wikipedia \
        --start 2023-01-01 --end 2023-06-30
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

API_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews"

# Wikimedia asks every client to identify itself. Set ATTENTION_UA to override,
# e.g. export ATTENTION_UA="attention-half-life/0.1 (GitHub: your-username)"
USER_AGENT = os.environ.get(
    "ATTENTION_UA", "attention-half-life/0.1 (GitHub: <your-github-username>)"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = REPO_ROOT / "data" / "cache" / "pageviews.sqlite"

MIN_INTERVAL_S = 0.2  # at most ~5 requests per second
MAX_RETRIES = 5


class ArticleNotFound(Exception):
    """Raised when the API has no pageview data for an article (wrong title or no views)."""


class PageviewsClient:
    """Small Pageviews API client with a SQLite response cache and polite rate limiting."""

    def __init__(self, cache_path: Path | str = DEFAULT_CACHE, user_agent: str = USER_AGENT):
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.cache_path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS responses ("
            " url TEXT PRIMARY KEY, status INTEGER, body TEXT, fetched_at TEXT)"
        )
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last_request = 0.0

    # ------------------------------------------------------------------ http
    def get_json(self, url: str) -> tuple[int, dict]:
        """Return (status, json) for a URL, using the cache when possible.

        200 and 404 responses are cached (a 404 means "no data", which will not change
        for a past date range). Other errors are retried with exponential backoff.
        """
        row = self.db.execute(
            "SELECT status, body FROM responses WHERE url = ?", (url,)
        ).fetchone()
        if row is not None:
            return row[0], json.loads(row[1])

        for attempt in range(MAX_RETRIES):
            wait = MIN_INTERVAL_S - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = self.session.get(url, timeout=30)
            except requests.RequestException:
                time.sleep(2**attempt)
                continue

            if resp.status_code in (200, 404):
                body = resp.text if resp.text else "{}"
                self.db.execute(
                    "INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?)",
                    (url, resp.status_code, body, datetime.now().isoformat()),
                )
                self.db.commit()
                return resp.status_code, json.loads(body)

            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2**attempt)
                continue

            resp.raise_for_status()

        raise RuntimeError(f"Giving up after {MAX_RETRIES} attempts: {url}")

    # ------------------------------------------------------------- pageviews
    def daily_views(
        self,
        title: str,
        project: str,
        start: date | str,
        end: date | str,
        access: str = "all-access",
    ) -> pd.Series:
        """Daily human (agent=user) pageviews for one article.

        Days the API omits inside the returned range are filled with 0. Days before the
        first returned date are *not* added, so a brand-new article keeps a short history
        (the half-life code uses that to detect "no baseline").

        Raises:
            ArticleNotFound: if the API has no data for this title in this range.
        """
        start_s = pd.Timestamp(start).strftime("%Y%m%d")
        end_s = pd.Timestamp(end).strftime("%Y%m%d")
        article = quote(title.replace(" ", "_"), safe="")
        url = (
            f"{API_BASE}/per-article/{project}/{access}/user/"
            f"{article}/daily/{start_s}/{end_s}"
        )
        status, payload = self.get_json(url)
        items = payload.get("items", []) if status == 200 else []
        if not items:
            raise ArticleNotFound(f"No pageview data for '{title}' on {project}")

        df = pd.DataFrame(items)
        idx = pd.to_datetime(df["timestamp"].str[:8], format="%Y%m%d")
        series = pd.Series(df["views"].to_numpy(dtype=float), index=idx, name=title)
        series = series.groupby(level=0).sum().sort_index()
        full = pd.date_range(series.index.min(), series.index.max(), freq="D")
        return series.reindex(full, fill_value=0.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch daily pageviews for one article.")
    parser.add_argument("title", help='Article title, e.g. "2023 Kahramanmaraş depremleri"')
    parser.add_argument("--project", default="tr.wikipedia")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    client = PageviewsClient()
    views = client.daily_views(args.title, args.project, args.start, args.end)
    print(views.to_string())
    print(f"\nPeak: {int(views.max())} views on {views.idxmax().date()}")


if __name__ == "__main__":
    main()
