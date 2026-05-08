#!/usr/bin/env python3
"""
Run bilingual summary on already-triaged items / 对已分类 items 跑双语摘要。

Usage / 用法:

    # Preview only — no LLM call, no DB write, $0 spend.
    python3 scripts/run_summary.py --dry-run

    # First real run — cap at 10 items (~$0.02 on Haiku 4.5).
    python3 scripts/run_summary.py --limit 10 --verbose

    # Production: drain pending summaries.
    python3 scripts/run_summary.py

    # Summary of items above importance threshold only.
    python3 scripts/run_summary.py --min-importance 50

    # Re-summarize everything (e.g. after a prompt-version bump).
    python3 scripts/run_summary.py --reset

    # Include track='other' items (default skip — they don't go in brief).
    python3 scripts/run_summary.py --include-other

Env vars:
    ANTHROPIC_API_KEY    required (unless --dry-run)
    LLM_CHEAP_MODEL      optional; default claude-haiku-4-5-20251001
    LLM_STRONG_MODEL     optional; default claude-opus-4-7
    MARKET_COMPASS_DB    optional DB path; default data/market_compass.db
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Bootstrap path
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from util.env import load_dotenv  # noqa: E402

load_dotenv(_HERE.parent / ".env")

from processing.summary import run_pending_summary  # noqa: E402
from reasoning.llm_client import (  # noqa: E402
    BudgetExceededError,
    CostMeter,
    LLMClient,
)
from storage.db import get_connection, init_db  # noqa: E402


GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
CYAN = "\033[0;36m"
DIM = "\033[2m"
NC = "\033[0m"


def _make_progress_callback(verbose: bool):
    def cb(idx: int, total: int, result) -> None:
        if not verbose:
            sys.stdout.write(".")
            sys.stdout.flush()
            if idx == total:
                sys.stdout.write("\n")
            return
        if result is None:
            print(f"  [{idx}/{total}] (dry-run)")
            return
        if result.ok:
            preview = (result.summary_en or "")[:60].replace("\n", " ")
            zh_preview = (result.summary_zh or "")[:30].replace("\n", " ")
            print(
                f"  [{idx}/{total}] {GREEN}✓{NC} id={result.item_id:>5d}  "
                f"cost=${result.cost_usd:.5f}  "
                f"en=\"{preview}…\"  zh=\"{zh_preview}…\""
            )
        else:
            print(
                f"  [{idx}/{total}] {RED}✗{NC} id={result.item_id:>5d}  "
                f"err={result.error}"
            )
    return cb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap on items processed.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip LLM calls AND DB writes.")
    parser.add_argument("--db-path", default=None,
                        help="Override DB path (also via MARKET_COMPASS_DB env).")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Per-item details vs tick marks.")
    parser.add_argument("--soft-cap-usd", type=float, default=15.0)
    parser.add_argument("--hard-cap-usd", type=float, default=20.0)
    parser.add_argument("--reset", action="store_true",
                        help="Clear summary_en + summary_zh from already-summarized items. "
                             "Forces re-summarization. Cost: 1× full re-run.")
    parser.add_argument("--include-other", action="store_true",
                        help="Don't skip track='other' items (default skips them — "
                             "they're archive-only, never in the brief).")
    parser.add_argument("--min-importance", type=int, default=0,
                        help="Only summarize items with importance >= N. Default 0.")
    args = parser.parse_args()

    db_path = (
        args.db_path
        or os.environ.get("MARKET_COMPASS_DB", "data/market_compass.db")
    )

    print(f"{CYAN}market-compass summary runner{NC}")
    print(f"  DB: {db_path}")
    print(f"  dry-run: {args.dry_run}")
    print(f"  limit: {args.limit if args.limit else '(none)'}")
    print(f"  min-importance: {args.min_importance}")
    print(f"  include-other: {args.include_other}")

    if args.dry_run:
        client = LLMClient(
            api_key="dry-run",
            cost_meter=CostMeter(args.soft_cap_usd, args.hard_cap_usd),
            client=object(),
        )
    else:
        try:
            client = LLMClient.from_env(
                cost_meter=CostMeter(args.soft_cap_usd, args.hard_cap_usd),
            )
        except ValueError as e:
            print(f"{RED}✗ {e}{NC}", file=sys.stderr)
            return 2

    with get_connection(db_path) as conn:
        init_db(conn)

        if args.reset:
            cur = conn.execute(
                "UPDATE items SET summary_en = NULL, summary_zh = NULL "
                "WHERE summary_en IS NOT NULL OR summary_zh IS NOT NULL"
            )
            print(
                f"  {YELLOW}--reset:{NC} cleared summary_en + summary_zh "
                f"for {cur.rowcount} already-summarized items"
            )

        try:
            summary = run_pending_summary(
                conn, client,
                limit=args.limit,
                dry_run=args.dry_run,
                min_importance=args.min_importance,
                include_other=args.include_other,
                on_progress=_make_progress_callback(args.verbose),
            )
        except BudgetExceededError as e:
            print(f"\n{RED}✗ {e}{NC}", file=sys.stderr)
            return 3

    print(f"\n{CYAN}=== Summary ==={NC}")
    print(f"  items processed:   {summary.items_processed}")
    print(f"  succeeded:         {summary.items_succeeded}")
    print(f"  failed:            {summary.items_failed}")
    if args.dry_run:
        print(f"  skipped (dry-run): {summary.items_skipped_dry_run}")
    print(f"  total cost:        ${summary.total_cost_usd:.4f}")
    print(f"  duration:          {summary.duration_seconds:.2f}s")

    if summary.by_track:
        print(f"\n  By track:")
        for track, count in sorted(summary.by_track.items()):
            print(f"    {track:<10s} {count:>3d}")

    if summary.failures:
        print(f"\n  {YELLOW}Failures:{NC}")
        for item_id, err in summary.failures:
            print(f"    id={item_id:>5d}  {err}")

    if not args.dry_run and summary.items_succeeded > 0:
        meter = client.cost_meter
        print(
            f"\n  Cost meter: ${meter.total_usd:.4f} / "
            f"${meter.soft_cap_usd:.0f} soft / "
            f"${meter.hard_cap_usd:.0f} hard  ({meter.status()})"
        )

    return 0 if summary.items_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
