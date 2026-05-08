"""
market-compass processing — causal chain runner
==============================================================================
项目核心: 五步因果链生成器 / The project's signature output: bilingual 5-step
causal chains.

Selection / 选取规则
-------------------
- WHERE causal_chain IS NULL          (not yet generated)
- AND track IS NOT NULL                (must be triaged)
- AND track != 'other'                 (other = archive only, not in brief)
- AND summary_en IS NOT NULL           (must be summarized — chain depends
                                        on the bilingual summary)
- AND importance >= min_importance     (optional further filter)

The summary is a prerequisite because chain prose references the same
facts the summary captured. Running the chain on un-summarized items
would force the model to extract numbers/entities twice.

Tier routing / 模型路由 (ADR-0010)
-----------------------------------
- Default: cheap (Haiku 4.5) — most items
- importance >= 70: strong (Opus 4.7) — lead items + triggers, where
  chain depth and cross-market reasoning quality matter most
- ``importance_strong_threshold`` configurable per call

Persistence / 持久化
-------------------
- items.causal_chain: full JSON blob (event/first_order/.../caveats)
- items.processed_ts: SET to current UTC ISO-8601 — chain completion
  marks the item as "fully processed" and ready for the daily brief
- items.meta.confidence + items.meta.caveats: merged into JSON, preserving
  ingestion / triage / summary keys

Validation / 输出校验
-------------------
All 5 steps must be present and structurally complete:
- {event, first_order, asset_reaction, second_order, cross_market}
- each is a dict with non-empty 'en' (str) and 'zh' (str with CJK chars)
- 'confidence' is float in [0, 1]
- 'caveats' is list of strings (may be empty)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from reasoning.llm_client import (
    BudgetExceededError,
    LLMClient,
    LLMResponse,
    ModelTier,
)
from reasoning.prompts import CAUSAL_CHAIN_FIVE_STEP

log = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Constants / 常量
# -----------------------------------------------------------------------------

#: Five steps in the bilingual causal chain template (per the prompt).
REQUIRED_STEPS: tuple[str, ...] = (
    "event",
    "first_order",
    "asset_reaction",
    "second_order",
    "cross_market",
)

#: Default importance threshold above which the runner promotes the chain
#: call to the strong-tier model (Opus 4.7). Items below run on cheap
#: tier (Haiku 4.5). See ADR-0010.
DEFAULT_STRONG_THRESHOLD: int = 70

#: Default — exclude track='other' (archive items don't go in brief).
EXCLUDED_TRACKS_DEFAULT: frozenset[str] = frozenset({"other"})

#: How many chars of body to include in the chain prompt. Higher than
#: triage's 500-char excerpt because chain reasoning benefits from full
#: article context.
DEFAULT_BODY_INPUT_CHARS: int = 4000


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _has_cjk(text: str) -> bool:
    """True iff text contains at least one CJK Unified Ideograph."""
    return any('一' <= c <= '鿿' for c in text)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -----------------------------------------------------------------------------
# Dataclasses
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class CausalChainResult:
    """One item's causal-chain outcome.

    ``ok=True`` means the LLM returned a structurally valid 5-step chain.
    ``chain`` is the parsed payload (the dict you'd serialize to
    ``items.causal_chain``).
    """
    item_id: int
    ok: bool
    chain: Optional[dict[str, Any]] = None
    confidence: Optional[float] = None
    caveats: list[str] = field(default_factory=list)
    raw_text: str = ""
    error: Optional[str] = None
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    tier_used: Optional[ModelTier] = None
    model_used: Optional[str] = None


@dataclass
class CausalChainRunSummary:
    """Aggregate stats for one ``run_pending_causal_chain()`` invocation."""
    items_processed: int = 0
    items_succeeded: int = 0
    items_failed: int = 0
    items_skipped_dry_run: int = 0
    total_cost_usd: float = 0.0
    duration_seconds: float = 0.0
    by_tier: dict[str, int] = field(default_factory=dict)
    by_track: dict[str, int] = field(default_factory=dict)
    failures: list[tuple[int, str]] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------

def _validate_step(name: str, value: Any) -> tuple[bool, Optional[str]]:
    """Check one step has the required ``{en, zh}`` shape."""
    if not isinstance(value, dict):
        return False, f"step '{name}' must be object, got {type(value).__name__}"
    en = value.get("en")
    if not isinstance(en, str) or not en.strip():
        return False, f"step '{name}'.en missing or empty"
    zh = value.get("zh")
    if not isinstance(zh, str) or not zh.strip():
        return False, f"step '{name}'.zh missing or empty"
    if not _has_cjk(zh):
        return False, f"step '{name}'.zh contains no CJK characters"
    return True, None


def _validate_payload(payload: Any) -> tuple[bool, Optional[str]]:
    """Validate the full 5-step bilingual payload."""
    if not isinstance(payload, dict):
        return False, f"expected JSON object, got {type(payload).__name__}"

    for step in REQUIRED_STEPS:
        if step not in payload:
            return False, f"missing required step: '{step}'"
        ok, err = _validate_step(step, payload[step])
        if not ok:
            return False, err

    confidence = payload.get("confidence")
    if confidence is None or not isinstance(confidence, (int, float)):
        return False, "confidence missing or not numeric"
    if not (0.0 <= float(confidence) <= 1.0):
        return False, f"confidence out of [0, 1]: {confidence}"

    caveats = payload.get("caveats", [])
    if not isinstance(caveats, list):
        return False, f"caveats must be list, got {type(caveats).__name__}"
    for i, c in enumerate(caveats):
        if not isinstance(c, str):
            return False, f"caveats[{i}] must be string"

    return True, None


# -----------------------------------------------------------------------------
# Tier resolution
# -----------------------------------------------------------------------------

def _resolve_tier(
    importance: Optional[int],
    threshold: int,
) -> ModelTier:
    """Cheap tier by default; strong when importance crosses the threshold."""
    if importance is not None and importance >= threshold:
        return "strong"
    return "cheap"


# -----------------------------------------------------------------------------
# Single-item run
# -----------------------------------------------------------------------------

def chain_one(
    client: LLMClient,
    item: dict[str, Any],
    *,
    importance_strong_threshold: int = DEFAULT_STRONG_THRESHOLD,
    body_input_chars: int = DEFAULT_BODY_INPUT_CHARS,
    context_block: str = "",
) -> CausalChainResult:
    """
    Run ``causal_chain.five_step`` on one item.

    Args:
        client: configured ``LLMClient``.
        item: dict-like row with at least ``id``, ``title``, ``source``,
            ``pub_ts``, ``body``, ``track``, ``importance``.
        importance_strong_threshold: items with ``importance >= N`` are
            promoted to strong tier.
        body_input_chars: body truncation length.
        context_block: optional free-text market-context block (e.g. "10Y
            UST: 4.21%, +3bp d/d"). Empty string by default; Phase 3.4.x
            can plumb FRED levels in.

    Returns:
        :class:`CausalChainResult`.
    """
    item_id = int(item["id"])
    importance = item.get("importance")
    tier = _resolve_tier(importance, importance_strong_threshold)

    title = item.get("title") or ""
    source = item.get("source") or ""
    pub_ts = item.get("pub_ts") or ""
    body = item.get("body") or ""
    body_truncated = body[:body_input_chars]
    track = item.get("track") or "other"

    start = time.monotonic()
    response: LLMResponse = client.call(
        CAUSAL_CHAIN_FIVE_STEP,
        tier_override=tier,
        title=title,
        source=source,
        pub_ts=pub_ts,
        body=body_truncated,
        track=track,
        importance=str(importance if importance is not None else "unknown"),
        context_block=context_block,
    )
    duration = time.monotonic() - start

    if response.parse_error or response.parsed is None:
        return CausalChainResult(
            item_id=item_id,
            ok=False,
            raw_text=response.raw_text,
            error=response.parse_error or "no parsed JSON",
            cost_usd=response.cost_usd,
            duration_seconds=duration,
            tier_used=tier,
            model_used=response.model,
        )

    valid, validation_err = _validate_payload(response.parsed)
    if not valid:
        return CausalChainResult(
            item_id=item_id,
            ok=False,
            raw_text=response.raw_text,
            error=validation_err,
            cost_usd=response.cost_usd,
            duration_seconds=duration,
            tier_used=tier,
            model_used=response.model,
        )

    payload = response.parsed
    return CausalChainResult(
        item_id=item_id,
        ok=True,
        chain={k: payload[k] for k in REQUIRED_STEPS},
        confidence=float(payload["confidence"]),
        caveats=list(payload.get("caveats", [])),
        raw_text=response.raw_text,
        cost_usd=response.cost_usd,
        duration_seconds=duration,
        tier_used=tier,
        model_used=response.model,
    )


# -----------------------------------------------------------------------------
# Persistence
# -----------------------------------------------------------------------------

def apply_chain(
    conn: sqlite3.Connection,
    result: CausalChainResult,
) -> None:
    """
    Persist a successful ``CausalChainResult`` to ``items``.

    - ``items.causal_chain``: full JSON blob (5 steps × {en, zh}).
    - ``items.processed_ts``: set to current UTC — marks the item as
      "fully processed", ready for daily brief assembly.
    - ``items.meta.confidence`` + ``items.meta.caveats``: merged into
      JSON, preserving ingestion / triage / summary keys.

    No-op when ``result.ok is False`` — failed items get retried.

    成功的 chain 写入 items.causal_chain;同时把 processed_ts 设为当前时间,
    标记本条已"完整处理",可进 daily brief。confidence + caveats 合并入 meta。
    """
    if not result.ok or result.chain is None:
        return

    row = conn.execute(
        "SELECT meta FROM items WHERE id = ?", (result.item_id,)
    ).fetchone()
    if row is None:
        log.warning("apply_chain: item id=%d not found", result.item_id)
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
                "apply_chain: items.meta for id=%d not valid JSON; starting fresh",
                result.item_id,
            )

    if result.confidence is not None:
        meta["chain_confidence"] = result.confidence
    if result.caveats:
        meta["chain_caveats"] = result.caveats

    conn.execute(
        "UPDATE items SET causal_chain = ?, processed_ts = ?, meta = ? "
        "WHERE id = ?",
        (
            json.dumps(result.chain, ensure_ascii=False),
            _now_utc_iso(),
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
    """Fetch items ready for chain generation.

    Prerequisites: triaged (track non-null) AND summarized (summary_en
    non-null) AND chain not yet generated.

    Order: importance DESC, pub_ts ASC. High-importance items get
    processed first so the brief has its lead items ready even if the
    run is interrupted partway.
    """
    where_parts = [
        "causal_chain IS NULL",
        "track IS NOT NULL",
        "summary_en IS NOT NULL",
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
        " ORDER BY importance DESC, pub_ts ASC"
    )
    if limit is not None and limit > 0:
        sql += " LIMIT :limit_n"
        params["limit_n"] = limit

    return [dict(row) for row in conn.execute(sql, params)]


# -----------------------------------------------------------------------------
# Multi-item runner
# -----------------------------------------------------------------------------

def run_pending_causal_chain(
    conn: sqlite3.Connection,
    client: LLMClient,
    *,
    limit: Optional[int] = None,
    dry_run: bool = False,
    min_importance: int = 0,
    importance_strong_threshold: int = DEFAULT_STRONG_THRESHOLD,
    include_other: bool = False,
    body_input_chars: int = DEFAULT_BODY_INPUT_CHARS,
    context_block: str = "",
    on_progress: Optional[Callable[[int, int, Optional[CausalChainResult]], None]] = None,
) -> CausalChainRunSummary:
    """
    End-to-end runner for the daily / on-demand causal-chain pass.

    Args:
        conn: open SQLite connection.
        client: configured ``LLMClient``.
        limit: cap on items processed.
        dry_run: skip both LLM call and DB write.
        min_importance: filter ``importance >= N``.
        importance_strong_threshold: items at or above this importance
            are routed to strong tier (Opus 4.7) per ADR-0010.
        include_other: include ``track='other'`` items (default: skip).
        body_input_chars: body truncation length.
        context_block: market-context free text passed into every prompt.
        on_progress: optional callback (idx, total, result).

    Returns:
        :class:`CausalChainRunSummary`.
    """
    excluded = frozenset() if include_other else EXCLUDED_TRACKS_DEFAULT
    pending = _select_pending(
        conn,
        limit=limit,
        min_importance=min_importance,
        excluded_tracks=excluded,
    )
    summary = CausalChainRunSummary(items_processed=len(pending))

    if not pending:
        return summary

    start_run = time.monotonic()

    for idx, item in enumerate(pending):
        if dry_run:
            log.info(
                "dry-run: would chain item id=%d importance=%s",
                item["id"], item.get("importance"),
            )
            summary.items_skipped_dry_run += 1
            if on_progress is not None:
                on_progress(idx + 1, len(pending), None)
            continue

        try:
            result = chain_one(
                client, item,
                importance_strong_threshold=importance_strong_threshold,
                body_input_chars=body_input_chars,
                context_block=context_block,
            )
        except BudgetExceededError:
            log.warning(
                "budget exceeded mid-run after %d items; aborting",
                summary.items_succeeded + summary.items_failed,
            )
            raise
        except Exception as e:
            log.exception("chain_one raised on item id=%d", item["id"])
            summary.items_failed += 1
            summary.failures.append((int(item["id"]), repr(e)))
            if on_progress is not None:
                on_progress(idx + 1, len(pending), None)
            continue

        summary.total_cost_usd += result.cost_usd

        if result.ok:
            apply_chain(conn, result)
            summary.items_succeeded += 1
            tier = result.tier_used or "?"
            summary.by_tier[tier] = summary.by_tier.get(tier, 0) + 1
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
    "DEFAULT_STRONG_THRESHOLD",
    "EXCLUDED_TRACKS_DEFAULT",
    "REQUIRED_STEPS",
    "CausalChainResult",
    "CausalChainRunSummary",
    "apply_chain",
    "chain_one",
    "run_pending_causal_chain",
]
