"""
Tests for src/processing/summary.py — bilingual summary runner.

Inject a ``FakeAnthropicClient`` so we exercise the full path
(prompt → call → JSON extract → validate → DB write) without network.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable, Optional

import pytest

from processing.summary import (
    EXCLUDED_TRACKS_DEFAULT,
    SummaryResult,
    SummaryRunSummary,
    apply_summary,
    run_pending_summary,
    summarize_one,
    _has_cjk,
    _validate_payload,
)
from reasoning.llm_client import (
    BudgetExceededError,
    CostMeter,
    LLMClient,
)
from storage.db import get_connection, init_db

API_KEY = "test"  # short on purpose
ISO_TS = "2026-04-23T12:00:00Z"


# =============================================================================
# Fake Anthropic client
# =============================================================================

class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Usage:
    def __init__(self, input_tokens: int = 200, output_tokens: int = 80) -> None:
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
    return _Message(json.dumps(payload, ensure_ascii=False))


def _make_client(responder: Any, **kwargs: Any) -> LLMClient:
    return LLMClient(api_key=API_KEY, client=FakeAnthropicClient(responder), **kwargs)


# =============================================================================
# DB seeding helpers
# =============================================================================

def _insert_item(
    conn: sqlite3.Connection,
    *,
    title: str,
    body: str = "Body text for summary",
    source: str = "test-source",
    pub_ts: str = ISO_TS,
    track: Optional[str] = "macro",
    importance: Optional[int] = 50,
    summary_en: Optional[str] = None,
    summary_zh: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO items (content_hash, source, title, body, pub_ts, "
        "fetched_ts, track, importance, summary_en, summary_zh, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"hash-{title[:50]}-{pub_ts}",
            source, title, body, pub_ts, pub_ts,
            track, importance,
            summary_en, summary_zh,
            json.dumps(meta) if meta else None,
        ),
    )
    return int(cur.lastrowid)


# =============================================================================
# _has_cjk / _validate_payload
# =============================================================================

def test_has_cjk_detects_chinese() -> None:
    assert _has_cjk("美联储维持利率")
    assert _has_cjk("hello 中 world")  # mixed


def test_has_cjk_returns_false_for_english_only() -> None:
    assert not _has_cjk("The Federal Reserve holds rates")
    assert not _has_cjk("")
    assert not _has_cjk("123 456")


def test_validate_rejects_non_dict() -> None:
    valid, err = _validate_payload(["not", "a", "dict"])
    assert not valid
    assert "object" in err.lower()


def test_validate_rejects_missing_summary_en() -> None:
    valid, err = _validate_payload({"summary_zh": "中文"})
    assert not valid
    assert "summary_en" in err


def test_validate_rejects_empty_summary_en() -> None:
    valid, err = _validate_payload({"summary_en": "  ", "summary_zh": "中文摘要"})
    assert not valid
    assert "summary_en" in err


def test_validate_rejects_summary_zh_without_cjk() -> None:
    valid, err = _validate_payload({
        "summary_en": "english text",
        "summary_zh": "this is not actually chinese",  # no CJK
    })
    assert not valid
    assert "CJK" in err or "Chinese" in err.lower()


def test_validate_rejects_non_list_key_numbers() -> None:
    valid, err = _validate_payload({
        "summary_en": "english",
        "summary_zh": "中文摘要",
        "key_numbers": "should be list",
    })
    assert not valid
    assert "key_numbers" in err


def test_validate_rejects_non_string_in_key_numbers() -> None:
    valid, err = _validate_payload({
        "summary_en": "english",
        "summary_zh": "中文摘要",
        "key_numbers": ["ok", 42, "also ok"],
    })
    assert not valid
    assert "key_numbers" in err


def test_validate_accepts_well_formed_payload() -> None:
    valid, err = _validate_payload({
        "summary_en": "The Fed held rates steady citing inflation patience.",
        "summary_zh": "美联储维持利率不变,强调对通胀保持耐心。",
        "key_numbers": ["target range 5.25%-5.50%"],
    })
    assert valid
    assert err is None


def test_validate_accepts_empty_key_numbers_list() -> None:
    valid, _ = _validate_payload({
        "summary_en": "english",
        "summary_zh": "中文",
        "key_numbers": [],
    })
    assert valid


def test_validate_accepts_null_key_numbers() -> None:
    valid, _ = _validate_payload({
        "summary_en": "english",
        "summary_zh": "中文",
        "key_numbers": None,
    })
    assert valid


# =============================================================================
# summarize_one — happy path + validation
# =============================================================================

def test_summarize_one_happy_path() -> None:
    payload = {
        "summary_en": "The Federal Reserve held rates steady at 5.25%-5.50% citing patience.",
        "summary_zh": "美联储维持利率在 5.25%-5.50% 不变,强调对通胀保持耐心。",
        "key_numbers": ["target range 5.25%-5.50%", "no policy change"],
    }
    client = _make_client(_ok(payload))
    item = {"id": 1, "title": "Fed leaves rates unchanged",
            "source": "fed", "pub_ts": ISO_TS, "body": "..."}

    result = summarize_one(client, item)

    assert result.ok is True
    assert result.item_id == 1
    assert "Federal Reserve" in result.summary_en
    assert "美联储" in result.summary_zh
    assert result.key_numbers == ["target range 5.25%-5.50%", "no policy change"]
    assert result.error is None
    assert result.cost_usd > 0


def test_summarize_one_truncates_body() -> None:
    long_body = "x" * 10000
    client = _make_client(_ok({
        "summary_en": "summary here",
        "summary_zh": "中文摘要在这里",
        "key_numbers": [],
    }))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS, "body": long_body}

    summarize_one(client, item, body_input_chars=500)

    sent = client._client.messages.calls[0]["messages"][0]["content"]
    # Body excerpt in user msg should be ≤ 500 chars (plus surrounding template).
    # An easy check: the user msg should not contain 600+ x's in a row.
    assert "x" * 600 not in sent


def test_summarize_one_handles_unparseable_response() -> None:
    client = _make_client(_Message("Sorry, I can't summarize that."))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS, "body": "b"}

    result = summarize_one(client, item)

    assert result.ok is False
    assert result.error is not None
    assert "Sorry" in result.raw_text


def test_summarize_one_rejects_english_only_in_zh_field() -> None:
    """Common LLM failure: returns English in both fields. Validator catches."""
    client = _make_client(_ok({
        "summary_en": "english summary text",
        "summary_zh": "english text again",  # no CJK
        "key_numbers": [],
    }))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS, "body": "b"}

    result = summarize_one(client, item)

    assert result.ok is False
    assert "CJK" in (result.error or "") or "Chinese" in (result.error or "").lower()


def test_summarize_one_strips_whitespace_from_summaries() -> None:
    client = _make_client(_ok({
        "summary_en": "  summary with leading/trailing spaces  ",
        "summary_zh": "  中文摘要前后有空格  ",
        "key_numbers": [],
    }))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS, "body": "b"}

    result = summarize_one(client, item)

    assert result.ok is True
    assert result.summary_en == "summary with leading/trailing spaces"
    assert result.summary_zh == "中文摘要前后有空格"


def test_summarize_one_records_cost_even_on_failure() -> None:
    client = _make_client(_Message("not json"))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS, "body": "b"}

    result = summarize_one(client, item)

    assert result.ok is False
    assert result.cost_usd > 0


# =============================================================================
# apply_summary — DB write
# =============================================================================

def test_apply_summary_writes_columns() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(conn, title="Fed holds rates")
        result = SummaryResult(
            item_id=item_id, ok=True,
            summary_en="The Fed held rates.",
            summary_zh="美联储维持利率不变。",
            key_numbers=["5.25%-5.50%"],
        )
        apply_summary(conn, result)

        row = conn.execute(
            "SELECT summary_en, summary_zh FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()

    assert row["summary_en"] == "The Fed held rates."
    assert row["summary_zh"] == "美联储维持利率不变。"


def test_apply_summary_merges_key_numbers_into_meta() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(
            conn, title="Acquisition",
            meta={"feed_url": "https://x.com/feed",
                  "deal_size_usd_billions": 12.4,  # set by triage
                  "triage_reason": "M&A announcement"},
        )
        result = SummaryResult(
            item_id=item_id, ok=True,
            summary_en="X acquires Y for $12.4B",
            summary_zh="X 以 124 亿美元收购 Y",
            key_numbers=["$12.4B deal value", "stock-and-cash mix"],
        )
        apply_summary(conn, result)

        row = conn.execute(
            "SELECT meta FROM items WHERE id = ?", (item_id,),
        ).fetchone()
        meta = json.loads(row["meta"])

    # ingestion + triage keys preserved
    assert meta["feed_url"] == "https://x.com/feed"
    assert meta["deal_size_usd_billions"] == 12.4
    assert meta["triage_reason"] == "M&A announcement"
    # summary key added
    assert meta["key_numbers"] == ["$12.4B deal value", "stock-and-cash mix"]


def test_apply_summary_handles_null_existing_meta() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(conn, title="t", meta=None)
        result = SummaryResult(
            item_id=item_id, ok=True,
            summary_en="english", summary_zh="中文",
            key_numbers=["fact"],
        )
        apply_summary(conn, result)

        row = conn.execute(
            "SELECT meta FROM items WHERE id = ?", (item_id,),
        ).fetchone()
        meta = json.loads(row["meta"])

    assert meta["key_numbers"] == ["fact"]


def test_apply_summary_handles_corrupt_existing_meta() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(conn, title="t")
        conn.execute("UPDATE items SET meta = ? WHERE id = ?",
                     ("not valid json", item_id))
        result = SummaryResult(
            item_id=item_id, ok=True,
            summary_en="english", summary_zh="中文",
            key_numbers=["fact"],
        )
        apply_summary(conn, result)

        row = conn.execute(
            "SELECT meta FROM items WHERE id = ?", (item_id,),
        ).fetchone()
        meta = json.loads(row["meta"])

    assert meta == {"key_numbers": ["fact"]}


def test_apply_summary_noop_on_failed_result() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(conn, title="t")
        result = SummaryResult(item_id=item_id, ok=False, error="parse fail")
        apply_summary(conn, result)

        row = conn.execute(
            "SELECT summary_en, summary_zh FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()

    assert row["summary_en"] is None
    assert row["summary_zh"] is None


def test_apply_summary_does_not_touch_processed_ts() -> None:
    """processed_ts is set by the causal_chain runner (Phase 3.4), not summary."""
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(conn, title="t")
        result = SummaryResult(
            item_id=item_id, ok=True,
            summary_en="en", summary_zh="中",
            key_numbers=[],
        )
        apply_summary(conn, result)

        row = conn.execute(
            "SELECT processed_ts FROM items WHERE id = ?", (item_id,),
        ).fetchone()

    assert row["processed_ts"] is None


# =============================================================================
# run_pending_summary — selection + filters
# =============================================================================

def test_run_pending_processes_only_classified_items() -> None:
    """Items where track IS NULL aren't yet triaged → don't summarize."""
    payload = {"summary_en": "en", "summary_zh": "中文",
               "key_numbers": []}
    client = _make_client(_ok(payload))

    with get_connection(":memory:") as conn:
        init_db(conn)
        not_triaged = _insert_item(conn, title="not triaged", track=None, importance=None)
        triaged = _insert_item(conn, title="triaged macro", track="macro")

        summary = run_pending_summary(conn, client)

        rows = list(conn.execute(
            "SELECT id, summary_en FROM items ORDER BY id"
        ))

    assert summary.items_processed == 1
    assert summary.items_succeeded == 1
    # Only the triaged one got a summary
    assert next(r for r in rows if r["id"] == not_triaged)["summary_en"] is None
    assert next(r for r in rows if r["id"] == triaged)["summary_en"] is not None


def test_run_pending_excludes_other_track_by_default() -> None:
    payload = {"summary_en": "en", "summary_zh": "中文", "key_numbers": []}
    client = _make_client(_ok(payload))

    with get_connection(":memory:") as conn:
        init_db(conn)
        macro_id = _insert_item(conn, title="macro item", track="macro",
                                pub_ts="2026-04-01T00:00:00Z")
        other_id = _insert_item(conn, title="other item", track="other",
                                pub_ts="2026-04-02T00:00:00Z")

        summary = run_pending_summary(conn, client)

        rows = list(conn.execute(
            "SELECT id, summary_en FROM items ORDER BY id"
        ))

    assert summary.items_processed == 1
    assert next(r for r in rows if r["id"] == macro_id)["summary_en"] is not None
    assert next(r for r in rows if r["id"] == other_id)["summary_en"] is None


def test_run_pending_include_other_includes_other_track() -> None:
    payload = {"summary_en": "en", "summary_zh": "中文", "key_numbers": []}
    client = _make_client(_ok(payload))

    with get_connection(":memory:") as conn:
        init_db(conn)
        _insert_item(conn, title="macro item", track="macro",
                     pub_ts="2026-04-01T00:00:00Z")
        _insert_item(conn, title="other item", track="other",
                     pub_ts="2026-04-02T00:00:00Z")

        summary = run_pending_summary(conn, client, include_other=True)

    assert summary.items_processed == 2
    assert summary.items_succeeded == 2


def test_run_pending_min_importance_filters() -> None:
    payload = {"summary_en": "en", "summary_zh": "中文", "key_numbers": []}
    client = _make_client(_ok(payload))

    with get_connection(":memory:") as conn:
        init_db(conn)
        _insert_item(conn, title="low", track="macro", importance=20,
                     pub_ts="2026-04-01T00:00:00Z")
        _insert_item(conn, title="med", track="macro", importance=55,
                     pub_ts="2026-04-02T00:00:00Z")
        _insert_item(conn, title="high", track="macro", importance=85,
                     pub_ts="2026-04-03T00:00:00Z")

        summary = run_pending_summary(conn, client, min_importance=50)

    assert summary.items_processed == 2  # med + high


def test_run_pending_skips_already_summarized() -> None:
    payload = {"summary_en": "en", "summary_zh": "中文", "key_numbers": []}
    client = _make_client(_ok(payload))

    with get_connection(":memory:") as conn:
        init_db(conn)
        _insert_item(conn, title="done", track="macro", summary_en="existing",
                     pub_ts="2026-04-01T00:00:00Z")
        _insert_item(conn, title="pending", track="macro",
                     pub_ts="2026-04-02T00:00:00Z")

        summary = run_pending_summary(conn, client)

    assert summary.items_processed == 1


def test_run_pending_processes_oldest_first() -> None:
    payload = {"summary_en": "en", "summary_zh": "中文", "key_numbers": []}
    client = _make_client(_ok(payload))

    with get_connection(":memory:") as conn:
        init_db(conn)
        oldest = _insert_item(conn, title="oldest", track="macro",
                              pub_ts="2026-01-01T00:00:00Z")
        newest = _insert_item(conn, title="newest", track="macro",
                              pub_ts="2026-05-01T00:00:00Z")
        middle = _insert_item(conn, title="middle", track="macro",
                              pub_ts="2026-03-01T00:00:00Z")

        run_pending_summary(conn, client, limit=1)

        summarized_id = conn.execute(
            "SELECT id FROM items WHERE summary_en IS NOT NULL"
        ).fetchone()["id"]

    assert summarized_id == oldest


def test_run_pending_honors_limit() -> None:
    payload = {"summary_en": "en", "summary_zh": "中文", "key_numbers": []}
    client = _make_client(_ok(payload))

    with get_connection(":memory:") as conn:
        init_db(conn)
        for i in range(5):
            _insert_item(conn, title=f"item {i}", track="macro",
                         pub_ts=f"2026-04-{10+i:02d}T00:00:00Z")

        summary = run_pending_summary(conn, client, limit=2)

    assert summary.items_processed == 2


def test_run_pending_dry_run_no_llm_no_db() -> None:
    fake = FakeAnthropicClient(_ok({"summary_en": "en", "summary_zh": "中"}))
    client = LLMClient(api_key=API_KEY, client=fake)

    with get_connection(":memory:") as conn:
        init_db(conn)
        for i in range(3):
            _insert_item(conn, title=f"item {i}", track="macro",
                         pub_ts=f"2026-04-{10+i:02d}T00:00:00Z")

        summary = run_pending_summary(conn, client, dry_run=True)

        summarized = conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE summary_en IS NOT NULL"
        ).fetchone()["c"]

    assert summary.items_processed == 3
    assert summary.items_skipped_dry_run == 3
    assert summary.items_succeeded == 0
    assert summary.total_cost_usd == 0
    assert summarized == 0
    assert fake.messages.calls == []


def test_run_pending_tallies_by_track() -> None:
    track_iter = iter(["macro", "macro", "deals", "na_fx"])

    def responder(**kwargs: Any) -> Any:
        return _ok({"summary_en": "en", "summary_zh": "中文",
                    "key_numbers": []})

    client = _make_client(responder)

    with get_connection(":memory:") as conn:
        init_db(conn)
        for i, t in enumerate(["macro", "macro", "deals", "na_fx"]):
            _insert_item(conn, title=f"item {i}", track=t,
                         pub_ts=f"2026-04-{10+i:02d}T00:00:00Z")

        summary = run_pending_summary(conn, client)

    assert summary.by_track == {"macro": 2, "deals": 1, "na_fx": 1}


def test_run_pending_isolates_per_item_failures() -> None:
    call_index = {"i": 0}

    def responder(**kwargs: Any) -> Any:
        call_index["i"] += 1
        if call_index["i"] == 2:
            return _Message("not parseable as json")
        return _ok({"summary_en": "en", "summary_zh": "中文",
                    "key_numbers": []})

    client = _make_client(responder)

    with get_connection(":memory:") as conn:
        init_db(conn)
        for i in range(3):
            _insert_item(conn, title=f"item {i}", track="macro",
                         pub_ts=f"2026-04-{10+i:02d}T00:00:00Z")

        summary = run_pending_summary(conn, client)

        succeeded = conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE summary_en IS NOT NULL"
        ).fetchone()["c"]

    assert summary.items_processed == 3
    assert summary.items_succeeded == 2
    assert summary.items_failed == 1
    assert succeeded == 2


def test_run_pending_propagates_budget_exceeded() -> None:
    meter = CostMeter(soft_cap_usd=15.0, hard_cap_usd=20.0)
    meter.record(20.0)
    client = _make_client(_ok({"summary_en": "en", "summary_zh": "中"}),
                         cost_meter=meter)

    with get_connection(":memory:") as conn:
        init_db(conn)
        _insert_item(conn, title="item", track="macro")

        with pytest.raises(BudgetExceededError):
            run_pending_summary(conn, client)


def test_run_pending_empty_queue() -> None:
    client = _make_client(_ok({"summary_en": "en", "summary_zh": "中"}))

    with get_connection(":memory:") as conn:
        init_db(conn)
        summary = run_pending_summary(conn, client)

    assert summary.items_processed == 0


def test_run_pending_calls_progress_callback() -> None:
    payload = {"summary_en": "en", "summary_zh": "中文", "key_numbers": []}
    client = _make_client(_ok(payload))
    seen: list[tuple[int, int]] = []

    with get_connection(":memory:") as conn:
        init_db(conn)
        for i in range(2):
            _insert_item(conn, title=f"item {i}", track="macro",
                         pub_ts=f"2026-04-{10+i:02d}T00:00:00Z")

        run_pending_summary(
            conn, client,
            on_progress=lambda idx, total, result: seen.append((idx, total)),
        )

    assert seen == [(1, 2), (2, 2)]
