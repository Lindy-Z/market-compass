"""
market-compass processing — bilingual summary runner
==============================================================================
双语摘要执行器 — 对已分类 items 跑 summarize.bilingual,生成 EN/中文双语摘要
+ key_numbers 抽取。

Selection / 选取规则
-------------------
- WHERE summary_en IS NULL  (未摘要的)
- AND track IS NOT NULL     (已分类的 — triage 必须先跑)
- AND track NOT IN ('other')  default — track='other' 平均重要性 ~15,
                              不进 daily brief, 摘要它纯属浪费
                              (--include-other 可覆盖)
- AND importance >= min_importance  optional further filter

Persistence / 持久化
-------------------
- items.summary_en / items.summary_zh: dedicated columns
- items.meta.key_numbers: merged into JSON blob (preserves ingestion-set keys
  + triage-set keys like deal_size_usd_billions, triage_reason)
- items.processed_ts: NOT set here. Reasoning is "complete" only after the
  causal-chain runner (Phase 3.4) finishes. Until then processed_ts stays
  NULL.

Validation / 输出校验
-------------------
The prompt asks for {summary_en, summary_zh, key_numbers}. We reject:
- summary_en missing or empty
- summary_zh missing, empty, or contains zero CJK characters (catches
  the failure mode where the model returns English in both fields)
- key_numbers present but not a list

Failed items are NOT persisted; they get retried on the next run.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from reasoning.llm_client import (
    BudgetExceededError,
    LLMClient,
    LLMResponse,
)
from reasoning.prompts import SUMMARIZE_BILINGUAL

log = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Constants / 常量
# -----------------------------------------------------------------------------

#: How many characters of body to send. Higher = better summary quality but
#: more input tokens. 3000 chars ≈ 750 tokens for English; per-item input
#: cost on Haiku 4.5 is dominated by this.
DEFAULT_BODY_INPUT_CHARS: int = 3000

#: Tracks excluded from the default summary pass. 'other' items are
#: archive-only (avg importance ~15 in v0.2.0 corpus) and never appear
#: in a daily brief, so summarizing them is wasted spend.
EXCLUDED_TRACKS_DEFAULT: frozenset[str] = frozenset({"other"})


# -----------------------------------------------------------------------------
# CJK detection / CJK 字符检测
# -----------------------------------------------------------------------------

def _has_cjk(text: str) -> bool:
    """
    True iff ``text`` contains at least one CJK Unified Ideograph.

    Sanity check that ``summary_zh`` is actually Chinese. Catches the
    common LLM failure mode where the model returns English text in both
    summary fields (e.g. when prompted in a non-Chinese conversation).

    粗略校验 summary_zh 真的是中文。常见失败模式: 模型在两个字段里都返回英文,
    用此检查可以拦下。
    """
    return any('一' <= c <= '鿿' for c in text)


# -----------------------------------------------------------------------------
# Dataclasses
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class SummaryResult:
    """One item's summary outcome.

    ``ok=True`` means the LLM returned parseable JSON AND validation passed.
    Failed items are NOT written to the DB — they're retried on the next run.
    """
    item_id: int
    ok: bool
    summary_en: Optional[str] = None
    summary_zh: Optional[str] = None
    key_numbers: Optional[list[str]] = None
    raw_text: str = ""
    error: Optional[str] = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0


@dataclass
class SummaryRunSummary:
    """Aggregate stats for one ``run_pending_summary()`` invocation."""
    items_processed: int = 0
    items_succeeded: int = 0
    items_failed: int = 0
    items_skipped_dry_run: int = 0
    total_cost_usd: float = 0.0
    duration_seconds: float = 0.0
    by_track: dict[str, int] = field(default_factory=dict)
    failures: list[tuple[int, str]] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------

def _validate_payload(payload: Any) -> tuple[bool, Optional[str]]:
    """Check that the parsed JSON has the shape ``summarize.bilingual`` promises."""
    if not isinstance(payload, dict):
        return False, f"expected JSON object, got {type(payload).__name__}"

    summary_en = payload.get("summary_en")
    if not isinstance(summary_en, str) or not summary_en.strip():
        return False, "summary_en missing or empty"

    summary_zh = payload.get("summary_zh")
    if not isinstance(summary_zh, str) or not summary_zh.strip():
        return False, "summary_zh missing or empty"
    if not _has_cjk(summary_zh):
        return False, "summary_zh contains no CJK characters (model likely returned EN)"

    key_numbers = payload.get("key_numbers")
    if key_numbers is not None and not isinstance(key_numbers, list):
        return (
            False,
            f"key_numbers must be list or null, got {type(key_numbers).__name__}",
        )
    # If list, ensure all elements are strings (per prompt contract).
    if isinstance(key_numbers, list):
        for i, kn in enumerate(key_numbers):
            if not isinstance(kn, str):
                return (
                    False,
                    f"key_numbers[{i}] must be string, got {type(kn).__name__}",
                )

    return True, None


# -----------------------------------------------------------------------------
# Single-item summary
# -----------------------------------------------------------------------------

def summarize_one(
    client: LLMClient,
    item: dict[str, Any],
    *,
    body_input_chars: int = DEFAULT_BODY_INPUT_CHARS,
) -> SummaryResult:
    """
    Run ``summarize.bilingual`` on one item; return result. Does NOT
    write to DB.

    Args:
        client: configured ``LLMClient``.
        item: dict-like row with ``id``, ``title``, ``source``, ``pub_ts``,
            ``body``. Other keys are ignored.
        body_input_chars: how many characters of body to include in the
            prompt. Excess truncated.

    Returns:
        ``SummaryResult``. ``ok=False`` carries a human-readable ``error``.
    """
    item_id = int(item["id"])
    title = item.get("title") or ""
    source = item.get("source") or ""
    pub_ts = item.get("pub_ts") or ""
    body = item.get("body") or ""
    body_truncated = body[:body_input_chars]

    start = time.monotonic()
    response: LLMResponse = client.call(
        SUMMARIZE_BILINGUAL,
        title=title,
        source=source,
        pub_ts=pub_ts,
        body=body_truncated,
    )
    duration = time.monotonic() - start

    if response.parse_error or response.parsed is None:
        return SummaryResult(
            item_id=item_id, ok=False,
            raw_text=response.raw_text,
            error=response.parse_error or "no parsed JSON",
            cost_usd=response.cost_usd,
            duration_seconds=duration,
        )

    valid, validation_err = _validate_payload(response.parsed)
    if not valid:
        return SummaryResult(
            item_id=item_id, ok=False,
            raw_text=response.raw_text,
            error=validation_err,
            cost_usd=response.cost_usd,
            duration_seconds=duration,
        )

    payload = response.parsed
    return SummaryResult(
        item_id=item_id, ok=True,
        summary_en=payload["summary_en"].strip(),
        summary_zh=payload["summary_zh"].strip(),
        key_numbers=payload.get("key_numbers") or [],
        raw_text=response.raw_text,
        cost_usd=response.cost_usd,
        duration_seconds=duration,
    )


# -----------------------------------------------------------------------------
# Persistence
# -----------------------------------------------------------------------------

def apply_summary(
    conn: sqlite3.Connection,
    result: SummaryResult,
) -> None:
    """
    Persist a successful ``SummaryResult`` to ``items``.

    - ``items.summary_en`` / ``items.summary_zh``: dedicated columns.
    - ``items.meta.key_numbers``: merged into JSON blob (existing keys
      preserved — ingestion's ``feed_url``, ``finnhub_id``, etc., and
      triage's ``deal_size_usd_billions``, ``triage_reason``,
      ``track_confidence`` all stay).
    - ``items.processed_ts``: not touched (left for causal_chain runner).

    No-op when ``result.ok is False`` — failed items get retried.

    成功的结果写入 items;失败的不写,下轮重试。
    key_numbers 合并进 meta,不覆盖 ingestion / triage 已有的键。
    """
    if not result.ok:
        return

    row = conn.execute(
        "SELECT meta FROM items WHERE id = ?", (result.item_id,)
    ).fetchone()
    if row is None:
        log.warning("apply_summary: item id=%d not found", result.item_id)
        return

    existing_meta_json = row["meta"]
    meta: dict[str, Any] = {}
    if existing_meta_json:
        try:
            loaded = json.loads(existing_meta_json)
            if isinstance(loaded, dict):
                meta = loaded
        except (json.JSONDecodeError, TypeError):
            log.warning(
                "apply_summary: items.meta for id=%d not valid JSON; starting fresh",
                result.item_id,
            )

    if result.key_numbers is not None:
        meta["key_numbers"] = result.key_numbers

    conn.execute(
        "UPDATE items SET summary_en = ?, summary_zh = ?, meta = ? "
        "WHERE id = ?",
        (
            result.summary_en,
            result.summary_zh,
            json.dumps(meta, ensure_ascii=False),
            result.item_id,
        ),
    )


# -----------------------------------------------------------------------------
# Selection
# -----------------------------------------------------------------------------

def _select_pending(
    conn: sqlite3.Connection,
    *,
    limit: Optional[int] = None,
    min_importance: int = 0,
    excluded_tracks: frozenset[str] = EXCLUDED_TRACKS_DEFAULT,
) -> list[dict[str, Any]]:
    """Fetch un-summarized, classified items.

    Order: oldest pub_ts first, mirrors triage runner's backlog-drain
    pattern.
    """
    where_parts = [
        "summary_en IS NULL",
        "track IS NOT NULL",
        "importance >= :min_imp",
    ]
    params: dict[str, Any] = {"min_imp": min_importance}

    if excluded_tracks:
        placeholders: list[str] = []
        for i, t in enumerate(sorted(excluded_tracks)):
            ph = f":excl{i}"
            placeholders.append(ph)
            params[f"excl{i}"] = t
        where_parts.append(f"track NOT IN ({', '.join(placeholders)})")

    sql = (
        "SELECT id, title, source, body, pub_ts, track, importance "
        "FROM items WHERE " + " AND ".join(where_parts) +
        " ORDER BY pub_ts ASC"
    )
    if limit is not None and limit > 0:
        sql += " LIMIT :limit_n"
        params["limit_n"] = limit

    return [dict(row) for row in conn.execute(sql, params)]


# -----------------------------------------------------------------------------
# Multi-item runner
# -----------------------------------------------------------------------------

def run_pending_summary(
    conn: sqlite3.Connection,
    client: LLMClient,
    *,
    limit: Optional[int] = None,
    dry_run: bool = False,
    min_importance: int = 0,
    include_other: bool = False,
    body_input_chars: int = DEFAULT_BODY_INPUT_CHARS,
    on_progress: Optional[Callable[[int, int, Optional[SummaryResult]], None]] = None,
) -> SummaryRunSummary:
    """
    End-to-end runner for the daily / on-demand summary pass.

    Args:
        conn: open SQLite connection.
        client: configured ``LLMClient``.
        limit: cap on items processed; ``None`` = no cap.
        dry_run: skip both the LLM call AND DB write.
        min_importance: only summarize items with ``importance >= N``.
            Default 0 (no filter); pass e.g. 30 to skip the brief-mention
            band.
        include_other: by default, ``track='other'`` items are skipped
            (they don't appear in the brief). Set ``True`` to include.
        body_input_chars: passed through to :func:`summarize_one`.
        on_progress: optional callback ``(idx, total, result)`` per item.

    Returns:
        :class:`SummaryRunSummary`.
    """
    excluded = frozenset() if include_other else EXCLUDED_TRACKS_DEFAULT
    pending = _select_pending(
        conn,
        limit=limit,
        min_importance=min_importance,
        excluded_tracks=excluded,
    )
    summary = SummaryRunSummary(items_processed=len(pending))

    if not pending:
        return summary

    start_run = time.monotonic()

    for idx, item in enumerate(pending):
        if dry_run:
            log.info(
                "dry-run: would summarize item id=%d title=%r",
                item["id"], (item.get("title") or "")[:80],
            )
            summary.items_skipped_dry_run += 1
            if on_progress is not None:
                on_progress(idx + 1, len(pending), None)
            continue

        try:
            result = summarize_one(
                client, item, body_input_chars=body_input_chars,
            )
        except BudgetExceededError:
            log.warning(
                "budget exceeded mid-run after %d items; aborting",
                summary.items_succeeded + summary.items_failed,
            )
            raise
        except Exception as e:
            log.exception("summarize_one raised on item id=%d", item["id"])
            summary.items_failed += 1
            summary.failures.append((int(item["id"]), repr(e)))
            if on_progress is not None:
                on_progress(idx + 1, len(pending), None)
            continue

        summary.total_cost_usd += result.cost_usd

        if result.ok:
            apply_summary(conn, result)
            summary.items_succeeded += 1
            track = item.get("track") or "?"
            summary.by_track[track] = summary.by_track.get(track, 0) + 1
        else:
            summary.items_failed += 1
            summary.failures.append((result.item_id, result.error or "unknown"))

        if on_progress is not None:
            on_progress(idx + 1, len(pending), result)

    summary.duration_seconds = time.monotonic() - start_run
    return summary


__all__ = [
    "DEFAULT_BODY_INPUT_CHARS",
    "EXCLUDED_TRACKS_DEFAULT",
    "SummaryResult",
    "SummaryRunSummary",
    "apply_summary",
    "run_pending_summary",
    "summarize_one",
]
