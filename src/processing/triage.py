"""
market-compass processing — triage runner
==============================================================================
分流执行器 — 对未分类的 items 跑 ``classify.triage`` 提示词,写入 track /
importance / deal_size。

For each item where ``track IS NULL``:
  1. Build the user message from title + source + body excerpt.
  2. Call the LLM (cheap-tier Haiku 4.5 by default).
  3. Validate the parsed JSON against the expected schema.
  4. UPDATE ``items.track`` + ``items.importance`` and merge
     ``deal_size_usd_billions`` / ``triage_reason`` / ``track_confidence``
     into ``items.meta`` (preserving any keys the ingestion layer wrote).

设计要点:
  - **deal_size 暂存于 meta JSON**, 不单独建列。Phase 3.5 触发器若需要索引化
    再考虑迁移 (ADR-0011 累加式策略允许)。
  - **dry_run 模式不调用 LLM, 也不写 DB**。供"花真金白银前先看一眼 prompt
    会被填成什么样"使用。
  - **每条 item 独立处理**: 一条挂了不影响其余。失败原因写到 ``TriageResult.error``,
    DB 不写,下次 run 还会再试。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from reasoning.llm_client import (
    BudgetExceededError,
    LLMClient,
    LLMResponse,
)
from reasoning.prompts import CLASSIFY_TRIAGE

log = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Constants / 常量
# -----------------------------------------------------------------------------

DEFAULT_BODY_EXCERPT_CHARS: int = 500

#: Valid track values from the prompt + schema.
VALID_TRACKS: frozenset[str] = frozenset({"macro", "na_fx", "deals", "other"})


# -----------------------------------------------------------------------------
# Result / 结果数据类
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class TriageResult:
    """One item's triage outcome.

    ``ok`` is True only if the LLM returned parseable JSON AND it passed
    validation (track in enum, importance in 0-100). Failed items are
    NOT written to the DB — they'll be retried on the next run.
    """
    item_id: int
    ok: bool
    track: Optional[str] = None
    importance: Optional[int] = None
    deal_size_usd_billions: Optional[float] = None
    track_confidence: Optional[float] = None
    reason: Optional[str] = None
    raw_text: str = ""
    error: Optional[str] = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0


@dataclass
class TriageRunSummary:
    """Aggregate stats for one ``run_pending_triage()`` invocation."""
    items_processed: int = 0
    items_succeeded: int = 0
    items_failed: int = 0
    items_skipped_dry_run: int = 0
    total_cost_usd: float = 0.0
    duration_seconds: float = 0.0
    by_track: dict[str, int] = field(default_factory=dict)
    failures: list[tuple[int, str]] = field(default_factory=list)
        # list of (item_id, error)


# -----------------------------------------------------------------------------
# Validation / 校验
# -----------------------------------------------------------------------------

def _validate_payload(payload: Any) -> tuple[bool, Optional[str]]:
    """Check that the parsed JSON has the shape ``classify.triage`` promises."""
    if not isinstance(payload, dict):
        return False, f"expected JSON object, got {type(payload).__name__}"

    track = payload.get("track")
    if track not in VALID_TRACKS:
        return False, f"track {track!r} not in {sorted(VALID_TRACKS)}"

    importance = payload.get("importance")
    if not isinstance(importance, int) or not (0 <= importance <= 100):
        return False, f"importance must be int 0-100, got {importance!r}"

    track_confidence = payload.get("track_confidence")
    if track_confidence is not None and not isinstance(track_confidence, (int, float)):
        return False, f"track_confidence must be number or null, got {track_confidence!r}"

    deal_size = payload.get("deal_size_usd_billions")
    if deal_size is not None and not isinstance(deal_size, (int, float)):
        return False, f"deal_size_usd_billions must be number or null, got {deal_size!r}"

    return True, None


# -----------------------------------------------------------------------------
# Single-item triage / 单条 triage
# -----------------------------------------------------------------------------

def triage_one(
    client: LLMClient,
    item: dict[str, Any],
    *,
    body_excerpt_chars: int = DEFAULT_BODY_EXCERPT_CHARS,
) -> TriageResult:
    """
    Run ``classify.triage`` on one item; return result. Does NOT write to DB.

    Args:
        client: configured ``LLMClient``.
        item: dict-like item row with at least ``id``, ``title``, ``source``,
            ``body``. Other keys are ignored.
        body_excerpt_chars: how many characters of body to include in the
            prompt. Excess is truncated.

    Returns:
        ``TriageResult``. ``ok=False`` on parse / validation failure;
        ``error`` then carries a human-readable reason.
    """
    item_id = int(item["id"])
    title = item.get("title") or ""
    source = item.get("source") or ""
    body = item.get("body") or ""
    body_excerpt = body[:body_excerpt_chars]

    start = time.monotonic()
    response: LLMResponse = client.call(
        CLASSIFY_TRIAGE,
        title=title,
        source=source,
        body_excerpt=body_excerpt,
    )
    duration = time.monotonic() - start

    if response.parse_error or response.parsed is None:
        return TriageResult(
            item_id=item_id,
            ok=False,
            raw_text=response.raw_text,
            error=response.parse_error or "no parsed JSON",
            cost_usd=response.cost_usd,
            duration_seconds=duration,
        )

    valid, validation_err = _validate_payload(response.parsed)
    if not valid:
        return TriageResult(
            item_id=item_id,
            ok=False,
            raw_text=response.raw_text,
            error=validation_err,
            cost_usd=response.cost_usd,
            duration_seconds=duration,
        )

    payload = response.parsed
    return TriageResult(
        item_id=item_id,
        ok=True,
        track=payload["track"],
        importance=int(payload["importance"]),
        deal_size_usd_billions=payload.get("deal_size_usd_billions"),
        track_confidence=payload.get("track_confidence"),
        reason=payload.get("reason"),
        raw_text=response.raw_text,
        cost_usd=response.cost_usd,
        duration_seconds=duration,
    )


# -----------------------------------------------------------------------------
# Persistence / 持久化
# -----------------------------------------------------------------------------

def apply_triage(
    conn: sqlite3.Connection,
    result: TriageResult,
) -> None:
    """
    Persist a successful ``TriageResult`` to ``items``.

    - ``items.track`` and ``items.importance`` go to their dedicated columns.
    - ``deal_size_usd_billions`` / ``triage_reason`` / ``track_confidence``
      merge into ``items.meta`` as JSON (existing keys preserved).

    No-op when ``result.ok is False`` — failed items stay un-triaged so
    they get retried on the next run.

    成功的 TriageResult 写入 items;失败的不写,下轮重试。
    deal_size 与 reason / confidence 合并到 items.meta JSON, 不覆盖已有键。
    """
    if not result.ok:
        return

    row = conn.execute(
        "SELECT meta FROM items WHERE id = ?", (result.item_id,)
    ).fetchone()
    if row is None:
        log.warning("apply_triage: item id=%d not found", result.item_id)
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
                "apply_triage: items.meta for id=%d not valid JSON; "
                "starting fresh", result.item_id,
            )

    # Merge triage-derived fields. Skip None values so we don't overwrite
    # with nulls when the prompt didn't extract a deal size, etc.
    if result.deal_size_usd_billions is not None:
        meta["deal_size_usd_billions"] = result.deal_size_usd_billions
    if result.reason is not None:
        meta["triage_reason"] = result.reason
    if result.track_confidence is not None:
        meta["track_confidence"] = result.track_confidence

    conn.execute(
        "UPDATE items SET track = ?, importance = ?, meta = ? WHERE id = ?",
        (result.track, result.importance, json.dumps(meta, ensure_ascii=False),
         result.item_id),
    )


# -----------------------------------------------------------------------------
# Multi-item runner / 批量执行器
# -----------------------------------------------------------------------------

def _select_pending(
    conn: sqlite3.Connection,
    *,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Fetch un-triaged items (track IS NULL), oldest-pub_ts first.

    Oldest-first means we process the backlog in a deterministic order.
    最旧先做, 防止 backlog 累积到一边。
    """
    sql = (
        "SELECT id, title, source, body, pub_ts FROM items "
        "WHERE track IS NULL ORDER BY pub_ts ASC"
    )
    params: tuple[Any, ...] = ()
    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params = (limit,)

    return [dict(row) for row in conn.execute(sql, params)]


def run_pending_triage(
    conn: sqlite3.Connection,
    client: LLMClient,
    *,
    limit: Optional[int] = None,
    dry_run: bool = False,
    body_excerpt_chars: int = DEFAULT_BODY_EXCERPT_CHARS,
    on_progress: Optional[Any] = None,
) -> TriageRunSummary:
    """
    End-to-end runner for the daily / on-demand triage pass.

    Args:
        conn: open SQLite connection.
        client: configured ``LLMClient``.
        limit: cap on items processed in this run; ``None`` = no cap.
        dry_run: if ``True``, skip the LLM call AND the DB write — log
            what WOULD be sent. No spend, no state change.
        body_excerpt_chars: passed through to :func:`triage_one`.
        on_progress: optional callable ``(idx, total, result)`` invoked
            after each item; useful for CLI tick output.

    Returns:
        :class:`TriageRunSummary` with per-track tallies, total cost, and
        a list of (item_id, error) for any failures.
    """
    pending = _select_pending(conn, limit=limit)
    summary = TriageRunSummary(items_processed=len(pending))

    if not pending:
        return summary

    start_run = time.monotonic()

    for idx, item in enumerate(pending):
        if dry_run:
            log.info(
                "dry-run: would triage item id=%d title=%r",
                item["id"], (item.get("title") or "")[:80],
            )
            summary.items_skipped_dry_run += 1
            if on_progress is not None:
                on_progress(idx + 1, len(pending), None)
            continue

        try:
            result = triage_one(
                client, item, body_excerpt_chars=body_excerpt_chars,
            )
        except BudgetExceededError:
            log.warning(
                "budget exceeded mid-run after %d items; aborting",
                summary.items_succeeded + summary.items_failed,
            )
            raise
        except Exception as e:  # one bad item must not kill the run
            log.exception("triage_one raised on item id=%d", item["id"])
            summary.items_failed += 1
            summary.failures.append((int(item["id"]), repr(e)))
            if on_progress is not None:
                on_progress(idx + 1, len(pending), None)
            continue

        summary.total_cost_usd += result.cost_usd

        if result.ok:
            apply_triage(conn, result)
            summary.items_succeeded += 1
            track = result.track or "?"
            summary.by_track[track] = summary.by_track.get(track, 0) + 1
        else:
            summary.items_failed += 1
            summary.failures.append((result.item_id, result.error or "unknown"))

        if on_progress is not None:
            on_progress(idx + 1, len(pending), result)

    summary.duration_seconds = time.monotonic() - start_run
    return summary


__all__ = [
    "DEFAULT_BODY_EXCERPT_CHARS",
    "VALID_TRACKS",
    "TriageResult",
    "TriageRunSummary",
    "triage_one",
    "apply_triage",
    "run_pending_triage",
]
