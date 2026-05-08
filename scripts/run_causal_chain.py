#!/usr/bin/env python3
"""
Run causal-chain generation on triaged + summarized items.
对已分类、已摘要的 items 跑五步因果链生成。

Usage / 用法:

    # Preview only — no LLM call, no DB write, $0 spend.
    python3 scripts/run_causal_chain.py --dry-run

    # First real run — capped at 5 items (~$0.02 mostly cheap-tier).
    python3 scripts/run_causal_chain.py --limit 5 --verbose

    # Production: drain backlog, high-importance items get strong tier.
    python3 scripts/run_causal_chain.py

    # Force lower threshold for strong-tier promotion (default 70).
    python3 scripts/run_causal_chain.py --strong-threshold 60

    # Re-generate everything (e.g. after prompt-version bump).
    python3 scripts/run_causal_chain.py --reset

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

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from util.env import load_dotenv  # noqa: E402

load_dotenv(_HERE.parent / ".env")

from processing.causal_chain import (  # noqa: E402
    DEFAULT_STRONG_THRESHOLD,
    run_pending_causal_chain,
)
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
            event_zh = (result.chain.get("event", {}).get("zh", "")
                        if result.chain else "")[:40]
            tier_label = (
                f"{YELLOW}strong{NC}" if result.tier_used == "strong"
                else f"{DIM}cheap{NC}"
            )
            print(
                f"  [{idx}/{total}] {GREEN}✓{NC} id={result.item_id:>5d}  "
                f"tier={tier_label}  conf={result.confidence:.2f}  "
                f"cost=${result.cost_usd:.5f}  zh=\"{event_zh}…\""
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
                        help="Clear causal_chain + processed_ts from already-"
                             "chained items. Forces re-generation.")
    parser.add_argument("--include-other", action="store_true",
                        help="Include track='other' items (default skip — "
                             "archive-only, never in brief).")
    parser.add_argument("--min-importance", type=int, default=0,
                        help="Only chain items with importance >= N.")
    parser.add_argument("--strong-threshold", type=int,
                        default=DEFAULT_STRONG_THRESHOLD,
                        help=f"Items at importance >= N use the strong-tier "
                             f"model. Default {DEFAULT_STRONG_THRESHOLD} per ADR-0010.")
    parser.add_argument("--context-block", default="",
                        help="Free-text market context appended to every "
                             "prompt (e.g. \"10Y UST: 4.21%%, DXY: 102.3\").")
    args = parser.parse_args()

    db_path = (
        args.db_path
        or os.environ.get("MARKET_COMPASS_DB", "data/market_compass.db")
    )

    print(f"{CYAN}market-compass causal-chain runner{NC}")
    print(f"  DB: {db_path}")
    print(f"  dry-run: {args.dry_run}")
    print(f"  limit: {args.limit if args.limit else '(none)'}")
    print(f"  min-importance: {args.min_importance}")
    print(f"  strong-threshold: {args.strong_threshold}")
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
                "UPDATE items SET causal_chain = NULL, processed_ts = NULL "
                "WHERE causal_chain IS NOT NULL"
            )
            print(
                f"  {YELLOW}--reset:{NC} cleared causal_chain + processed_ts "
                f"for {cur.rowcount} already-chained items"
            )

        try:
            summary = run_pending_causal_chain(
                conn, client,
                limit=args.limit,
                dry_run=args.dry_run,
                min_importance=args.min_importance,
                importance_strong_threshold=args.strong_threshold,
                include_other=args.include_other,
                context_block=args.context_block,
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

    if summary.by_tier:
        print(f"\n  By tier:")
        for tier, count in sorted(summary.by_tier.items()):
            print(f"    {tier:<10s} {count:>3d}")

    if summary.by_track:
        print(f"\n  By track:")
        for track, count in sorted(summary.by_track.items()):
            print(f"    {track:<10s} {count:>3d}")

    if summary.failures:
        print(f"\n  {YELLOW}Failures:{NC}")
        for item_id, err in summary.failures[:20]:
            print(f"    id={item_id:>5d}  {err}")
        if len(summary.failures) > 20:
            print(f"    ... and {len(summary.failures) - 20} more")

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
