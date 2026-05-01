"""
Tests for src/processing/triage.py — classifier+triage runner.

Inject a ``FakeAnthropicClient`` into a real ``LLMClient`` so we exercise
the full path (prompt → call → JSON extract → validate → DB write) without
network or real spend.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable, Optional

import pytest

from processing.triage import (
    VALID_TRACKS,
    TriageResult,
    TriageRunSummary,
    apply_triage,
    run_pending_triage,
    triage_one,
)
from reasoning.llm_client import (
    BudgetExceededError,
    CostMeter,
    LLMClient,
)
from storage.db import get_connection, init_db

API_KEY = "test"  # short on purpose — see ADR-0013 + install-hooks.sh
ISO_TS = "2026-04-23T12:00:00Z"


# =============================================================================
# Fake Anthropic client (mirrors test_llm_client.py shape, kept local)
# =============================================================================

class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Usage:
    def __init__(self, input_tokens: int = 100, output_tokens: int = 50) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class _Message:
    def __init__(self, text: str, usage: Optional[_Usage] = None) -> None:
        self.content = [_Block(text)]
        self.usage = usage or _Usage()


class _Messages:
    def __init__(self, responder: Any) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if callable(self._responder):
            r = self._responder(**kwargs)
            if isinstance(r, Exception):
                raise r
            return r
        return self._responder


class FakeAnthropicClient:
    def __init__(self, responder: Any) -> None:
        self.messages = _Messages(responder)


def _ok(payload: dict[str, Any]) -> _Message:
    return _Message(json.dumps(payload))


def _make_client(responder: Any, **client_kwargs: Any) -> LLMClient:
    """Build an LLMClient backed by a FakeAnthropicClient."""
    fake = FakeAnthropicClient(responder)
    return LLMClient(api_key=API_KEY, client=fake, **client_kwargs)


# =============================================================================
# DB seeding helpers
# =============================================================================

def _insert_item(
    conn: sqlite3.Connection,
    *,
    title: str,
    body: str = "body",
    source: str = "test-source",
    pub_ts: str = ISO_TS,
    meta: Optional[dict[str, Any]] = None,
    track: Optional[str] = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO items (content_hash, source, title, body, pub_ts, "
        "fetched_ts, track, meta) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"hash-{title[:50]}-{pub_ts}",
            source, title, body, pub_ts, pub_ts, track,
            json.dumps(meta) if meta else None,
        ),
    )
    return int(cur.lastrowid)


# =============================================================================
# triage_one — happy path + validation
# =============================================================================

def test_triage_one_happy_path() -> None:
    payload = {
        "track": "macro",
        "track_confidence": 0.92,
        "importance": 78,
        "deal_size_usd_billions": None,
        "reason": "Fed FOMC statement",
    }
    client = _make_client(_ok(payload))
    item = {"id": 1, "title": "Fed leaves rates unchanged",
            "source": "fed-press-all", "body": "The Federal Reserve..."}

    result = triage_one(client, item)

    assert result.ok is True
    assert result.item_id == 1
    assert result.track == "macro"
    assert result.importance == 78
    assert result.track_confidence == pytest.approx(0.92)
    assert result.deal_size_usd_billions is None
    assert result.reason == "Fed FOMC statement"
    assert result.error is None


def test_triage_one_extracts_deal_size() -> None:
    payload = {
        "track": "deals",
        "track_confidence": 0.99,
        "importance": 92,
        "deal_size_usd_billions": 12.4,
        "reason": "Microsoft to acquire X for $12.4B",
    }
    client = _make_client(_ok(payload))
    item = {"id": 1, "title": "MSFT acquires X for $12.4B",
            "source": "finnhub-merger", "body": "Microsoft today announced..."}

    result = triage_one(client, item)

    assert result.ok is True
    assert result.deal_size_usd_billions == pytest.approx(12.4)


def test_triage_one_truncates_body_to_excerpt_chars() -> None:
    long_body = "x" * 10_000
    client = _make_client(_ok({
        "track": "other", "track_confidence": 0.5, "importance": 10,
        "deal_size_usd_billions": None, "reason": "test",
    }))
    item = {"id": 1, "title": "t", "source": "s", "body": long_body}

    triage_one(client, item, body_excerpt_chars=200)

    sent_user_msg = client._client.messages.calls[0]["messages"][0]["content"]
    # body excerpt in user msg must be at most ~200 chars
    # (plus the rest of the template — but the body portion specifically)
    assert "x" * 201 not in sent_user_msg


def test_triage_one_handles_unparseable_response() -> None:
    client = _make_client(_Message("Sorry, I can't classify that."))
    item = {"id": 1, "title": "t", "source": "s", "body": "b"}

    result = triage_one(client, item)

    assert result.ok is False
    assert result.error is not None
    assert "Sorry" in result.raw_text


def test_triage_one_rejects_invalid_track_value() -> None:
    payload = {"track": "weather", "track_confidence": 0.5,
               "importance": 50, "deal_size_usd_billions": None,
               "reason": "x"}
    client = _make_client(_ok(payload))
    item = {"id": 1, "title": "t", "source": "s", "body": "b"}

    result = triage_one(client, item)

    assert result.ok is False
    assert "track" in (result.error or "")
    # Tracks: only the four valid ones documented in VALID_TRACKS
    for t in VALID_TRACKS:
        assert t != "weather"


def test_triage_one_rejects_importance_out_of_range() -> None:
    payload = {"track": "macro", "track_confidence": 0.9,
               "importance": 150, "deal_size_usd_billions": None,
               "reason": "x"}
    client = _make_client(_ok(payload))
    item = {"id": 1, "title": "t", "source": "s", "body": "b"}

    result = triage_one(client, item)

    assert result.ok is False
    assert "importance" in (result.error or "")


def test_triage_one_rejects_non_int_importance() -> None:
    payload = {"track": "macro", "track_confidence": 0.9,
               "importance": 75.5, "deal_size_usd_billions": None,
               "reason": "x"}
    client = _make_client(_ok(payload))
    item = {"id": 1, "title": "t", "source": "s", "body": "b"}

    result = triage_one(client, item)

    assert result.ok is False
    assert "importance" in (result.error or "")


def test_triage_one_rejects_array_payload() -> None:
    """LLM might return [{...}] instead of {...}; reject."""
    client = _make_client(_Message(json.dumps([{"track": "macro"}])))
    item = {"id": 1, "title": "t", "source": "s", "body": "b"}

    result = triage_one(client, item)

    assert result.ok is False
    assert "object" in (result.error or "").lower()


def test_triage_one_records_cost_even_on_failure() -> None:
    """A failed parse still cost us tokens — meter must reflect that."""
    client = _make_client(_Message("not json"))
    item = {"id": 1, "title": "t", "source": "s", "body": "b"}

    result = triage_one(client, item)

    assert result.ok is False
    assert result.cost_usd > 0  # default fake usage produces non-zero cost


# =============================================================================
# apply_triage — DB write semantics
# =============================================================================

def test_apply_triage_writes_track_and_importance() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(conn, title="Fed leaves rates")
        result = TriageResult(
            item_id=item_id, ok=True,
            track="macro", importance=78, track_confidence=0.9,
            reason="Fed action", deal_size_usd_billions=None,
        )
        apply_triage(conn, result)

        row = conn.execute(
            "SELECT track, importance FROM items WHERE id = ?", (item_id,)
        ).fetchone()

    assert row["track"] == "macro"
    assert row["importance"] == 78


def test_apply_triage_merges_into_existing_meta() -> None:
    """ingestion already wrote meta = {feed_url: ...}; triage must preserve."""
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(
            conn, title="Microsoft acquires X",
            meta={"feed_url": "https://finnhub.io/...", "finnhub_id": 999},
        )
        result = TriageResult(
            item_id=item_id, ok=True,
            track="deals", importance=92, track_confidence=0.99,
            reason="acquisition", deal_size_usd_billions=12.4,
        )
        apply_triage(conn, result)

        row = conn.execute(
            "SELECT meta FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        meta = json.loads(row["meta"])

    # ingestion-supplied keys preserved
    assert meta["feed_url"] == "https://finnhub.io/..."
    assert meta["finnhub_id"] == 999
    # triage-supplied keys added
    assert meta["deal_size_usd_billions"] == 12.4
    assert meta["triage_reason"] == "acquisition"
    assert meta["track_confidence"] == pytest.approx(0.99)


def test_apply_triage_handles_null_existing_meta() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(conn, title="t", meta=None)
        result = TriageResult(
            item_id=item_id, ok=True, track="macro", importance=50,
            reason="x", track_confidence=0.5,
        )
        apply_triage(conn, result)

        row = conn.execute(
            "SELECT meta FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        meta = json.loads(row["meta"])

    assert meta["triage_reason"] == "x"


def test_apply_triage_skips_none_fields() -> None:
    """Don't overwrite existing meta keys with None values."""
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(
            conn, title="t",
            meta={"deal_size_usd_billions": 5.0},  # pre-existing value
        )
        result = TriageResult(
            item_id=item_id, ok=True,
            track="other", importance=10,
            deal_size_usd_billions=None,  # triage didn't extract one
            reason="test", track_confidence=None,
        )
        apply_triage(conn, result)

        row = conn.execute(
            "SELECT meta FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        meta = json.loads(row["meta"])

    # Pre-existing deal_size preserved (not overwritten with None)
    assert meta["deal_size_usd_billions"] == 5.0
    # New keys merged in
    assert meta["triage_reason"] == "test"


def test_apply_triage_noop_on_failed_result() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(conn, title="t")
        result = TriageResult(item_id=item_id, ok=False, error="parse fail")
        apply_triage(conn, result)

        row = conn.execute(
            "SELECT track, importance FROM items WHERE id = ?", (item_id,)
        ).fetchone()

    # track / importance still NULL
    assert row["track"] is None
    assert row["importance"] is None


def test_apply_triage_handles_corrupt_existing_meta() -> None:
    """If items.meta isn't valid JSON, don't crash — start fresh."""
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(conn, title="t")
        # Corrupt meta directly
        conn.execute("UPDATE items SET meta = ? WHERE id = ?",
                     ("not valid json", item_id))
        result = TriageResult(
            item_id=item_id, ok=True, track="macro", importance=50,
            reason="x",
        )
        apply_triage(conn, result)

        row = conn.execute(
            "SELECT meta FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        meta = json.loads(row["meta"])

    assert meta["triage_reason"] == "x"


# =============================================================================
# run_pending_triage — selection + limit + dry_run
# =============================================================================

def test_run_pending_processes_only_untracked() -> None:
    payload = {"track": "macro", "track_confidence": 0.9,
               "importance": 50, "deal_size_usd_billions": None,
               "reason": "x"}
    client = _make_client(_ok(payload))

    with get_connection(":memory:") as conn:
        init_db(conn)
        already = _insert_item(conn, title="already classified", track="other")
        pending_a = _insert_item(conn, title="pending A")
        pending_b = _insert_item(conn, title="pending B")

        summary = run_pending_triage(conn, client)

        rows = list(conn.execute("SELECT id, track FROM items ORDER BY id"))

    assert summary.items_processed == 2
    assert summary.items_succeeded == 2
    # The pre-classified item stayed put
    assert next(r for r in rows if r["id"] == already)["track"] == "other"
    assert next(r for r in rows if r["id"] == pending_a)["track"] == "macro"
    assert next(r for r in rows if r["id"] == pending_b)["track"] == "macro"


def test_run_pending_honors_limit() -> None:
    payload = {"track": "macro", "track_confidence": 0.9,
               "importance": 50, "deal_size_usd_billions": None,
               "reason": "x"}
    client = _make_client(_ok(payload))

    with get_connection(":memory:") as conn:
        init_db(conn)
        for i in range(5):
            _insert_item(conn, title=f"item {i}", pub_ts=f"2026-04-{20+i}T00:00:00Z")
        summary = run_pending_triage(conn, client, limit=2)

        triaged = conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE track IS NOT NULL"
        ).fetchone()["c"]

    assert summary.items_processed == 2
    assert triaged == 2  # 3 still pending


def test_run_pending_processes_oldest_first() -> None:
    """Backlog is drained oldest-pub_ts first so we don't starve old items."""
    payload = {"track": "macro", "track_confidence": 0.9,
               "importance": 50, "deal_size_usd_billions": None,
               "reason": "x"}
    client = _make_client(_ok(payload))

    with get_connection(":memory:") as conn:
        init_db(conn)
        newest = _insert_item(conn, title="newest", pub_ts="2026-05-01T00:00:00Z")
        oldest = _insert_item(conn, title="oldest", pub_ts="2026-01-01T00:00:00Z")
        middle = _insert_item(conn, title="middle", pub_ts="2026-03-01T00:00:00Z")

        run_pending_triage(conn, client, limit=1)

        triaged_id = conn.execute(
            "SELECT id FROM items WHERE track IS NOT NULL"
        ).fetchone()["id"]

    assert triaged_id == oldest


def test_run_pending_dry_run_skips_llm_and_db() -> None:
    """dry_run=True: no SDK call, no DB write, but counts in summary."""
    fake = FakeAnthropicClient(_ok({"track": "macro"}))
    client = LLMClient(api_key=API_KEY, client=fake)

    with get_connection(":memory:") as conn:
        init_db(conn)
        for i in range(3):
            _insert_item(conn, title=f"item {i}",
                         pub_ts=f"2026-04-{10+i}T00:00:00Z")

        summary = run_pending_triage(conn, client, dry_run=True)

        triaged = conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE track IS NOT NULL"
        ).fetchone()["c"]

    assert summary.items_processed == 3
    assert summary.items_skipped_dry_run == 3
    assert summary.items_succeeded == 0
    assert summary.total_cost_usd == 0
    assert triaged == 0
    assert fake.messages.calls == []  # no SDK call


def test_run_pending_tallies_by_track() -> None:
    """Different items get different tracks; summary tracks the breakdown."""
    track_sequence = iter(["macro", "macro", "deals", "na_fx", "other"])

    def responder(**kwargs: Any) -> Any:
        track = next(track_sequence)
        return _ok({
            "track": track, "track_confidence": 0.9,
            "importance": 50, "deal_size_usd_billions": None,
            "reason": "test",
        })

    client = _make_client(responder)

    with get_connection(":memory:") as conn:
        init_db(conn)
        for i in range(5):
            _insert_item(conn, title=f"item {i}",
                         pub_ts=f"2026-04-{10+i}T00:00:00Z")
        summary = run_pending_triage(conn, client)

    assert summary.by_track == {"macro": 2, "deals": 1, "na_fx": 1, "other": 1}
    assert summary.items_succeeded == 5
    assert summary.items_failed == 0


def test_run_pending_isolates_per_item_failures() -> None:
    """Item 2 returns garbage; rest succeed; run continues."""
    call_index = {"i": 0}

    def responder(**kwargs: Any) -> Any:
        call_index["i"] += 1
        if call_index["i"] == 2:
            return _Message("not valid json at all")
        return _ok({
            "track": "macro", "track_confidence": 0.9,
            "importance": 50, "deal_size_usd_billions": None,
            "reason": "x",
        })

    client = _make_client(responder)

    with get_connection(":memory:") as conn:
        init_db(conn)
        for i in range(3):
            _insert_item(conn, title=f"item {i}",
                         pub_ts=f"2026-04-{10+i}T00:00:00Z")
        summary = run_pending_triage(conn, client)

        triaged = conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE track IS NOT NULL"
        ).fetchone()["c"]

    assert summary.items_processed == 3
    assert summary.items_succeeded == 2
    assert summary.items_failed == 1
    assert triaged == 2
    assert len(summary.failures) == 1


def test_run_pending_propagates_budget_exceeded() -> None:
    """Hard-cap breach mid-run aborts cleanly."""
    meter = CostMeter(soft_cap_usd=15.0, hard_cap_usd=20.0)
    meter.record(20.0)  # already at hard cap
    client = _make_client(
        _ok({"track": "macro"}),
        cost_meter=meter,
    )

    with get_connection(":memory:") as conn:
        init_db(conn)
        _insert_item(conn, title="item")

        with pytest.raises(BudgetExceededError):
            run_pending_triage(conn, client)


def test_run_pending_empty_queue_returns_zero_summary() -> None:
    client = _make_client(_ok({"track": "macro"}))

    with get_connection(":memory:") as conn:
        init_db(conn)
        summary = run_pending_triage(conn, client)

    assert summary.items_processed == 0
    assert summary.items_succeeded == 0


def test_run_pending_calls_progress_callback() -> None:
    payload = {"track": "macro", "track_confidence": 0.9,
               "importance": 50, "deal_size_usd_billions": None,
               "reason": "x"}
    client = _make_client(_ok(payload))
    seen: list[tuple[int, int]] = []

    with get_connection(":memory:") as conn:
        init_db(conn)
        for i in range(2):
            _insert_item(conn, title=f"item {i}",
                         pub_ts=f"2026-04-{10+i}T00:00:00Z")

        run_pending_triage(
            conn, client,
            on_progress=lambda idx, total, result: seen.append((idx, total)),
        )

    assert seen == [(1, 2), (2, 2)]
