#!/usr/bin/env python3
"""
Real ingestion pipeline — fetch from every source, dedup, persist to DB.
真正的抓取流水线 — 拉取所有源,去重,写库。

Difference vs ``smoke_test_sources.py``: that one only verifies CONNECTIVITY
(it doesn't INSERT). This one actually populates the ``items`` table (and
``observations`` for FRED).

Usage / 用法:

    # Default: drain every source whose key is configured.
    python3 scripts/run_ingest.py

    # Verbose per-feed report:
    python3 scripts/run_ingest.py --verbose

    # Skip specific sources:
    python3 scripts/run_ingest.py --no-finnhub --no-fred

    # Use a custom DB:
    python3 scripts/run_ingest.py --db-path data/test.db

Sources whose API keys aren't set are SKIPPED with a clear message; the
script never crashes on missing keys.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Path bootstrap so this script runs standalone
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from util.env import load_dotenv  # noqa: E402

# Load .env BEFORE reading any os.environ.* in this process.
load_dotenv(_HERE.parent / ".env")

from ingestion import edgar, finnhub, fred, rss  # noqa: E402
from ingestion.feed_config import FEEDS as RSS_FEEDS  # noqa: E402
from processing.dedup import filter_new  # noqa: E402
from storage.db import get_connection, init_db  # noqa: E402


# ANSI / 颜色
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
CYAN = "\033[0;36m"
DIM = "\033[2m"
NC = "\033[0m"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _persist_items(conn, items: Iterable[dict[str, Any]]) -> int:
    """Run a batch of normalized items through dedup, INSERT new ones.

    Returns the number actually inserted (post-dedup).
    """
    fetched_ts = _now_utc_iso()
    n_inserted = 0
    for item in filter_new(conn, items):
        meta = item.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        conn.execute(
            "INSERT INTO items (content_hash, source, source_url, title, "
            "body, pub_ts, fetched_ts, meta) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item["content_hash"],
                item["source"],
                item.get("source_url"),
                item["title"],
                item.get("body"),
                item["pub_ts"],
                fetched_ts,
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        n_inserted += 1
    return n_inserted


def _ingest_rss(conn, verbose: bool) -> tuple[int, int]:
    """Returns (total_fetched, total_new_inserted)."""
    print(f"{CYAN}=== RSS — {len(RSS_FEEDS)} feeds ==={NC}")
    total_fetched, total_inserted = 0, 0
    for feed in RSS_FEEDS:
        outcome = rss.fetch_one(feed, conn)
        if outcome.status == 200:
            n = _persist_items(conn, outcome.items)
            total_fetched += len(outcome.items)
            total_inserted += n
            if verbose or n > 0:
                print(
                    f"  {GREEN}✓{NC} {feed.source_id:24s} "
                    f"fetched={len(outcome.items):3d}  new={n:3d}"
                )
        elif outcome.status == 304:
            if verbose:
                print(f"  {GREEN}✓{NC} {feed.source_id:24s} 304 not-modified")
        else:
            print(
                f"  {RED}✗{NC} {feed.source_id:24s} status={outcome.status}  "
                f"err={outcome.error}"
            )
    return total_fetched, total_inserted


def _ingest_edgar(conn, verbose: bool) -> tuple[int, int]:
    print(f"{CYAN}=== SEC EDGAR — {len(edgar.EDGAR_FEEDS)} form-types ==={NC}")
    ua = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
    if not ua:
        print(f"  {YELLOW}⚠ SEC_EDGAR_USER_AGENT not set — skipping{NC}")
        return 0, 0

    total_fetched, total_inserted = 0, 0
    try:
        outcomes = edgar.fetch_all(conn)
    except RuntimeError as e:
        print(f"  {RED}✗ {e}{NC}")
        return 0, 0
    for o in outcomes:
        if o.status == 200:
            n = _persist_items(conn, o.items)
            total_fetched += len(o.items)
            total_inserted += n
            if verbose or n > 0:
                print(
                    f"  {GREEN}✓{NC} {o.feed.source_id:24s} "
                    f"fetched={len(o.items):3d}  new={n:3d}"
                )
        else:
            print(
                f"  {RED}✗{NC} {o.feed.source_id:24s} status={o.status}  "
                f"err={o.error}"
            )
    return total_fetched, total_inserted


def _ingest_finnhub(conn, verbose: bool) -> tuple[int, int]:
    print(f"{CYAN}=== Finnhub — {len(finnhub.FINNHUB_FEEDS)} categories ==={NC}")
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not api_key:
        print(f"  {YELLOW}⚠ FINNHUB_API_KEY not set — skipping{NC}")
        return 0, 0

    total_fetched, total_inserted = 0, 0
    outcomes = finnhub.fetch_all(conn, api_key=api_key, throttle_seconds=0)
    for o in outcomes:
        if o.status == 200:
            n = _persist_items(conn, o.items)
            total_fetched += len(o.items)
            total_inserted += n
            if verbose or n > 0:
                print(
                    f"  {GREEN}✓{NC} {o.feed.source_id:24s} "
                    f"fetched={len(o.items):3d}  new={n:3d}"
                )
        else:
            print(
                f"  {RED}✗{NC} {o.feed.source_id:24s} status={o.status}  "
                f"err={o.error}"
            )
    return total_fetched, total_inserted


def _ingest_fred(conn, verbose: bool) -> int:
    """FRED writes to ``observations``, not ``items``. Returns rows written."""
    print(f"{CYAN}=== FRED — {len(fred.FRED_SERIES)} series ==={NC}")
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        print(f"  {YELLOW}⚠ FRED_API_KEY not set — skipping{NC}")
        return 0

    total_rows = 0
    outcomes = fred.fetch_all(conn, api_key=api_key)
    for o in outcomes:
        if o.status == 200:
            total_rows += o.rows_written
            if verbose:
                print(
                    f"  {GREEN}✓{NC} {o.series.series_id:24s} "
                    f"rows={o.rows_written:3d}  {o.series.label}"
                )
        else:
            print(
                f"  {RED}✗{NC} {o.series.series_id:24s} status={o.status}  "
                f"err={o.error}"
            )
    if not verbose:
        print(f"  total rows written: {total_rows}")
    return total_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-rss", action="store_true",
                        help="skip RSS feeds")
    parser.add_argument("--no-edgar", action="store_true",
                        help="skip SEC EDGAR feeds")
    parser.add_argument("--no-finnhub", action="store_true",
                        help="skip Finnhub")
    parser.add_argument("--no-fred", action="store_true",
                        help="skip FRED")
    parser.add_argument("--db-path", default=None,
                        help="DB path; also honored via MARKET_COMPASS_DB env.")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="print one line per feed even if 0 new items")
    args = parser.parse_args()

    db_path = (
        args.db_path
        or os.environ.get("MARKET_COMPASS_DB", "data/market_compass.db")
    )

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"{CYAN}market-compass ingest runner{NC}")
    print(f"  DB: {db_path}")
    print(
        f"  Keys: "
        f"FRED={'set' if os.environ.get('FRED_API_KEY','').strip() else f'{YELLOW}NOT SET{NC}'}  "
        f"FINNHUB={'set' if os.environ.get('FINNHUB_API_KEY','').strip() else f'{YELLOW}NOT SET{NC}'}  "
        f"EDGAR_UA={'set' if os.environ.get('SEC_EDGAR_USER_AGENT','').strip() else f'{YELLOW}NOT SET{NC}'}"
    )
    print()

    rss_fetched = rss_inserted = 0
    edgar_fetched = edgar_inserted = 0
    finnhub_fetched = finnhub_inserted = 0
    fred_rows = 0

    with get_connection(db_path) as conn:
        init_db(conn)

        if not args.no_rss:
            rss_fetched, rss_inserted = _ingest_rss(conn, args.verbose)
            print()
        if not args.no_edgar:
            edgar_fetched, edgar_inserted = _ingest_edgar(conn, args.verbose)
            print()
        if not args.no_finnhub:
            finnhub_fetched, finnhub_inserted = _ingest_finnhub(conn, args.verbose)
            print()
        if not args.no_fred:
            fred_rows = _ingest_fred(conn, args.verbose)
            print()

        # Final DB stats
        item_count = conn.execute(
            "SELECT COUNT(*) AS c FROM items"
        ).fetchone()["c"]
        triaged_count = conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE track IS NOT NULL"
        ).fetchone()["c"]
        obs_count = conn.execute(
            "SELECT COUNT(*) AS c FROM observations"
        ).fetchone()["c"]

    print(f"{CYAN}=== Summary ==={NC}")
    print(
        f"  RSS:      fetched={rss_fetched:5d}  new={rss_inserted:5d}"
    )
    print(
        f"  EDGAR:    fetched={edgar_fetched:5d}  new={edgar_inserted:5d}"
    )
    print(
        f"  Finnhub:  fetched={finnhub_fetched:5d}  new={finnhub_inserted:5d}"
    )
    print(
        f"  FRED:     observation rows written={fred_rows:5d}"
    )
    total_new = rss_inserted + edgar_inserted + finnhub_inserted
    print(f"\n  Total new items inserted: {total_new}")
    print(f"  Items in DB:              {item_count}  ({triaged_count} triaged)")
    print(f"  FRED observations in DB:  {obs_count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
