#!/usr/bin/env python3
"""
Smoke-test every ingestion source end-to-end against real APIs.
全部新闻源的真实端到端冒烟测试。

Reports per-source: status, item / observation count, error string.
Sources whose API key isn't set are SKIPPED with a clear message
(the script never crashes on missing keys).

每个源逐一报告: 状态、条数、错误。缺少密钥的源会被跳过,不会让脚本崩溃。

Usage / 使用:

    # From the project root:
    python3 scripts/smoke_test_sources.py
    # or with a fresh on-disk DB:
    MARKET_COMPASS_DB=data/smoke.db python3 scripts/smoke_test_sources.py

Env vars consulted / 读取的环境变量:
    FRED_API_KEY              — needed for FRED      (skipped if absent)
    FINNHUB_API_KEY           — needed for Finnhub   (skipped if absent)
    SEC_EDGAR_USER_AGENT      — required for EDGAR   (skipped if absent)
    MARKET_COMPASS_DB         — optional DB path; defaults to in-memory
"""
from __future__ import annotations

import os
import sys
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# -----------------------------------------------------------------------------
# Path bootstrap (run as a script, not a package)
# -----------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Now safe to import / 之后再导入
from util.env import load_dotenv  # noqa: E402

# Load .env from project root before reading any os.environ keys.
# .env 加载必须在读取 os.environ 之前。
load_dotenv(_HERE.parent / ".env")

from ingestion import edgar, finnhub, fred, rss  # noqa: E402
from ingestion.feed_config import FEEDS as RSS_FEEDS  # noqa: E402
from storage.db import get_connection, init_db  # noqa: E402


# -----------------------------------------------------------------------------
# Output formatting / 输出格式
# -----------------------------------------------------------------------------

GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
CYAN = "\033[0;36m"
DIM = "\033[2m"
NC = "\033[0m"


def header(text: str) -> None:
    print(f"\n{CYAN}=== {text} ==={NC}")


def ok(line: str) -> None:
    print(f"  {GREEN}✓{NC} {line}")


def warn(line: str) -> None:
    print(f"  {YELLOW}⚠{NC} {line}")


def err(line: str) -> None:
    print(f"  {RED}✗{NC} {line}")


def dim(line: str) -> None:
    print(f"  {DIM}{line}{NC}")


@dataclass
class SourceResult:
    source: str
    ok_count: int = 0
    skip_count: int = 0
    fail_count: int = 0
    failures: list[str] = None  # type: ignore

    def __post_init__(self) -> None:
        if self.failures is None:
            self.failures = []


# -----------------------------------------------------------------------------
# Section runners / 各源测试
# -----------------------------------------------------------------------------

def smoke_rss(conn: sqlite3.Connection) -> SourceResult:
    header(f"RSS — {len(RSS_FEEDS)} feeds")
    res = SourceResult(source="rss")
    for feed in RSS_FEEDS:
        outcome = rss.fetch_one(feed, conn)
        if outcome.status == 200:
            ok(f"{feed.source_id:24s} 200  items={len(outcome.items):3d}  {feed.url}")
            res.ok_count += 1
        elif outcome.status == 304:
            ok(f"{feed.source_id:24s} 304  not-modified")
            res.ok_count += 1
        else:
            err(f"{feed.source_id:24s} {outcome.status:>4}  {outcome.error}")
            res.failures.append(f"{feed.source_id}: {outcome.error}")
            res.fail_count += 1
    return res


def smoke_finnhub(conn: sqlite3.Connection) -> SourceResult:
    header(f"Finnhub — {len(finnhub.FINNHUB_FEEDS)} categories")
    res = SourceResult(source="finnhub")
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not api_key:
        warn("FINNHUB_API_KEY not set — skipping all Finnhub categories")
        res.skip_count = len(finnhub.FINNHUB_FEEDS)
        return res

    # throttle_seconds=0 so this script can be re-run repeatedly during dev
    outcomes = finnhub.fetch_all(conn, api_key=api_key, throttle_seconds=0)
    for o in outcomes:
        if o.status == 200:
            ok(f"{o.feed.source_id:24s} 200  items={len(o.items):3d}")
            res.ok_count += 1
        else:
            err(f"{o.feed.source_id:24s} {o.status:>4}  {o.error}")
            res.failures.append(f"{o.feed.source_id}: {o.error}")
            res.fail_count += 1
    return res


def smoke_fred(conn: sqlite3.Connection) -> SourceResult:
    header(f"FRED — {len(fred.FRED_SERIES)} series")
    res = SourceResult(source="fred")
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        warn("FRED_API_KEY not set — skipping all FRED series")
        res.skip_count = len(fred.FRED_SERIES)
        return res

    outcomes = fred.fetch_all(conn, api_key=api_key, limit=5)
    for o in outcomes:
        if o.status == 200:
            ok(f"{o.series.series_id:24s} 200  rows={o.rows_written:3d}  {o.series.label}")
            res.ok_count += 1
        else:
            err(f"{o.series.series_id:24s} {o.status:>4}  {o.error}")
            res.failures.append(f"{o.series.series_id}: {o.error}")
            res.fail_count += 1
    return res


def smoke_edgar(conn: sqlite3.Connection) -> SourceResult:
    header(f"SEC EDGAR — {len(edgar.EDGAR_FEEDS)} form-types")
    res = SourceResult(source="edgar")
    ua = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
    if not ua:
        warn("SEC_EDGAR_USER_AGENT not set — skipping all EDGAR feeds")
        res.skip_count = len(edgar.EDGAR_FEEDS)
        return res

    try:
        outcomes = edgar.fetch_all(conn)
    except RuntimeError as e:
        err(str(e))
        res.failures.append(str(e))
        res.fail_count = len(edgar.EDGAR_FEEDS)
        return res

    for o in outcomes:
        if o.status == 200:
            ok(f"{o.feed.source_id:24s} 200  items={len(o.items):3d}")
            res.ok_count += 1
        elif o.status == 304:
            ok(f"{o.feed.source_id:24s} 304  not-modified")
            res.ok_count += 1
        else:
            err(f"{o.feed.source_id:24s} {o.status:>4}  {o.error}")
            res.failures.append(f"{o.feed.source_id}: {o.error}")
            res.fail_count += 1
    return res


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    db_path = os.environ.get("MARKET_COMPASS_DB", ":memory:")
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"{CYAN}market-compass smoke test{NC}")
    print(f"  DB: {db_path}")
    print(f"  FRED_API_KEY:        {'set' if os.environ.get('FRED_API_KEY','').strip() else f'{YELLOW}NOT SET{NC}'}")
    print(f"  FINNHUB_API_KEY:     {'set' if os.environ.get('FINNHUB_API_KEY','').strip() else f'{YELLOW}NOT SET{NC}'}")
    print(f"  SEC_EDGAR_USER_AGENT:{'set' if os.environ.get('SEC_EDGAR_USER_AGENT','').strip() else f'{YELLOW}NOT SET{NC}'}")

    with get_connection(db_path) as conn:
        init_db(conn)

        results = [
            smoke_rss(conn),
            smoke_finnhub(conn),
            smoke_fred(conn),
            smoke_edgar(conn),
        ]

    # ---- Summary ------------------------------------------------------------
    header("SUMMARY")
    total_ok = sum(r.ok_count for r in results)
    total_skip = sum(r.skip_count for r in results)
    total_fail = sum(r.fail_count for r in results)

    for r in results:
        line = (
            f"{r.source:10s}  ok={r.ok_count:3d}  "
            f"skip={r.skip_count:3d}  fail={r.fail_count:3d}"
        )
        if r.fail_count > 0:
            err(line)
        elif r.skip_count > 0 and r.ok_count == 0:
            warn(line)
        else:
            ok(line)

    dim("")
    if total_fail == 0 and total_skip == 0:
        ok(f"ALL GREEN — ok={total_ok}")
        return 0
    if total_fail == 0:
        warn(
            f"OK with skips — ok={total_ok}, skip={total_skip}. "
            f"Set the missing API keys to test the rest."
        )
        return 0
    err(f"FAILURES — ok={total_ok}, skip={total_skip}, fail={total_fail}")
    print("\nFailure detail:")
    for r in results:
        for f in r.failures:
            print(f"  - [{r.source}] {f}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
