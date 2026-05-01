#!/usr/bin/env python3
"""
Run classifier+triage on pending items / 对未分类 items 跑分流。

Usage / 用法:

    # Preview only — no LLM call, no DB write, $0 spend.
    python3 scripts/run_triage.py --dry-run

    # First real run — cap at 10 items (~$0.005 on Haiku 4.5).
    python3 scripts/run_triage.py --limit 10 --verbose

    # Production: drain the backlog.
    python3 scripts/run_triage.py

Env vars consulted:
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

# Path bootstrap so this script can run standalone
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from util.env import load_dotenv  # noqa: E402

# Load .env from project root before reading os.environ.
load_dotenv(_HERE.parent / ".env")

from processing.triage import run_pending_triage, TriageResult  # noqa: E402
from reasoning.llm_client import (  # noqa: E402
    BudgetExceededError,
    CostMeter,
    LLMClient,
)
from storage.db import get_connection, init_db  # noqa: E402


# ANSI / 颜色
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
CYAN = "\033[0;36m"
DIM = "\033[2m"
NC = "\033[0m"


def _make_progress_callback(verbose: bool):
    def cb(idx: int, total: int, result) -> None:
        if not verbose:
            # tick mark only
            sys.stdout.write(".")
            sys.stdout.flush()
            if idx == total:
                sys.stdout.write("\n")
            return
        if result is None:
            print(f"  [{idx}/{total}] (dry-run)")
            return
        if result.ok:
            print(
                f"  [{idx}/{total}] {GREEN}✓{NC} id={result.item_id:>5d}  "
                f"track={result.track:<7s}  imp={result.importance:>3d}  "
                f"cost=${result.cost_usd:.5f}"
            )
        else:
            print(
                f"  [{idx}/{total}] {RED}✗{NC} id={result.item_id:>5d}  "
                f"err={result.error}"
            )
    return cb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Cap on items processed in this run.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip LLM calls AND DB writes; preview the run.",
    )
    parser.add_argument(
        "--db-path", default=None,
        help="Override DB path (also honored via MARKET_COMPASS_DB env).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print per-item details instead of tick marks.",
    )
    parser.add_argument(
        "--soft-cap-usd", type=float, default=15.0,
        help="Cost-meter soft cap. Default $15/mo per ADR-0010.",
    )
    parser.add_argument(
        "--hard-cap-usd", type=float, default=20.0,
        help="Cost-meter hard cap. Default $20/mo per project brief.",
    )
    args = parser.parse_args()

    db_path = (
        args.db_path
        or os.environ.get("MARKET_COMPASS_DB", "data/market_compass.db")
    )

    print(f"{CYAN}market-compass triage runner{NC}")
    print(f"  DB: {db_path}")
    print(f"  dry-run: {args.dry_run}")
    print(f"  limit: {args.limit if args.limit else '(none)'}")

    # Build the LLM client (skipped in dry-run since we won't call it).
    if args.dry_run:
        # We still need a client object for the runner's signature, even
        # though it won't be called. Short fake key — dry-run short-circuits
        # before the SDK is touched. (Kept under 20 chars for the hook.)
        client = LLMClient(
            api_key="dry-run",
            cost_meter=CostMeter(args.soft_cap_usd, args.hard_cap_usd),
            client=object(),  # never used
        )
    else:
        try:
            client = LLMClient.from_env(
                cost_meter=CostMeter(args.soft_cap_usd, args.hard_cap_usd),
            )
        except ValueError as e:
            print(f"{RED}✗ {e}{NC}", file=sys.stderr)
            print(
                f"{YELLOW}  Set ANTHROPIC_API_KEY in .env, or pass --dry-run.{NC}",
                file=sys.stderr,
            )
            return 2

    with get_connection(db_path) as conn:
        init_db(conn)

        try:
            summary = run_pending_triage(
                conn, client,
                limit=args.limit,
                dry_run=args.dry_run,
                on_progress=_make_progress_callback(args.verbose),
            )
        except BudgetExceededError as e:
            print(f"\n{RED}✗ {e}{NC}", file=sys.stderr)
            return 3

    # Summary
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
