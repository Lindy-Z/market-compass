"""
Tests for src/processing/causal_chain.py — bilingual 5-step causal chain runner.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable, Optional

import pytest

from processing.causal_chain import (
    DEFAULT_STRONG_THRESHOLD,
    REQUIRED_STEPS,
    CausalChainResult,
    apply_chain,
    chain_one,
    run_pending_causal_chain,
    _resolve_tier,
    _validate_payload,
    _validate_step,
)
from reasoning.llm_client import (
    BudgetExceededError,
    CostMeter,
    DEFAULT_CHEAP_MODEL,
    DEFAULT_STRONG_MODEL,
    LLMClient,
)
from storage.db import get_connection, init_db

API_KEY = "test"
ISO_TS = "2026-04-23T12:00:00Z"


# =============================================================================
# Fake Anthropic client
# =============================================================================

class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Usage:
    def __init__(self, input_tokens: int = 500, output_tokens: int = 300) -> None:
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


def _bilingual(en: str, zh: str) -> dict[str, str]:
    return {"en": en, "zh": zh}


def _good_chain(confidence: float = 0.7, caveats: Optional[list[str]] = None) -> dict[str, Any]:
    """A valid 5-step bilingual payload."""
    return {
        "event": _bilingual(
            "The Fed held rates at 5.25%-5.50%.",
            "美联储维持利率在 5.25%-5.50%。",
        ),
        "first_order": _bilingual(
            "Rate path stays anchored; near-term funding cost unchanged.",
            "利率路径保持锚定,短期融资成本不变。",
        ),
        "asset_reaction": _bilingual(
            "10Y UST flat; DXY +0.1%; SPX little changed.",
            "10 年期美债平稳;美元指数 +0.1%;标普 500 基本无变化。",
        ),
        "second_order": _bilingual(
            "Carry trades extend; risk assets benefit from dovish hold.",
            "套息交易延续;风险资产受益于鸽派维持。",
        ),
        "cross_market": _bilingual(
            "EM FX bid; tail-risk: surprise hawkish minutes could reverse.",
            "新兴市场货币受买盘;尾部风险:意外鹰派会议纪要可能反转。",
        ),
        "confidence": confidence,
        "caveats": caveats or [],
    }


def _ok(payload: dict[str, Any]) -> _Message:
    return _Message(json.dumps(payload, ensure_ascii=False))


def _make_client(responder: Any, **kwargs: Any) -> LLMClient:
    return LLMClient(api_key=API_KEY, client=FakeAnthropicClient(responder), **kwargs)


# =============================================================================
# DB helpers
# =============================================================================

def _insert_item(
    conn: sqlite3.Connection,
    *,
    title: str,
    body: str = "Body text for chain.",
    source: str = "test-source",
    pub_ts: str = ISO_TS,
    track: Optional[str] = "macro",
    importance: Optional[int] = 50,
    summary_en: Optional[str] = "Existing summary in English.",
    summary_zh: Optional[str] = "已有的中文摘要。",
    causal_chain: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO items (content_hash, source, title, body, pub_ts, "
        "fetched_ts, track, importance, summary_en, summary_zh, "
        "causal_chain, meta) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"hash-{title[:50]}-{pub_ts}",
            source, title, body, pub_ts, pub_ts,
            track, importance,
            summary_en, summary_zh,
            causal_chain,
            json.dumps(meta) if meta else None,
        ),
    )
    return int(cur.lastrowid)


# =============================================================================
# Tier resolution
# =============================================================================

def test_resolve_tier_low_importance_is_cheap() -> None:
    assert _resolve_tier(50, threshold=70) == "cheap"


def test_resolve_tier_at_threshold_is_strong() -> None:
    assert _resolve_tier(70, threshold=70) == "strong"


def test_resolve_tier_above_threshold_is_strong() -> None:
    assert _resolve_tier(85, threshold=70) == "strong"


def test_resolve_tier_none_importance_is_cheap() -> None:
    assert _resolve_tier(None, threshold=70) == "cheap"


# =============================================================================
# Validation
# =============================================================================

def test_validate_step_accepts_well_formed() -> None:
    ok, err = _validate_step("event", _bilingual("x", "中"))
    assert ok and err is None


def test_validate_step_rejects_non_dict() -> None:
    ok, err = _validate_step("event", "string")
    assert not ok and "object" in err


def test_validate_step_rejects_missing_en() -> None:
    ok, err = _validate_step("event", {"zh": "中"})
    assert not ok and "en" in err


def test_validate_step_rejects_missing_zh() -> None:
    ok, err = _validate_step("event", {"en": "english"})
    assert not ok and "zh" in err


def test_validate_step_rejects_zh_without_cjk() -> None:
    ok, err = _validate_step("event", _bilingual("x", "no chinese here"))
    assert not ok and "CJK" in err


def test_validate_payload_accepts_good_chain() -> None:
    valid, err = _validate_payload(_good_chain())
    assert valid and err is None


def test_validate_payload_rejects_missing_step() -> None:
    bad = _good_chain()
    del bad["asset_reaction"]
    valid, err = _validate_payload(bad)
    assert not valid and "asset_reaction" in err


def test_validate_payload_rejects_confidence_out_of_range() -> None:
    bad = _good_chain(confidence=1.5)
    valid, err = _validate_payload(bad)
    assert not valid and "confidence" in err


def test_validate_payload_rejects_non_numeric_confidence() -> None:
    bad = _good_chain()
    bad["confidence"] = "high"
    valid, err = _validate_payload(bad)
    assert not valid and "confidence" in err


def test_validate_payload_rejects_non_list_caveats() -> None:
    bad = _good_chain()
    bad["caveats"] = "should be list"
    valid, err = _validate_payload(bad)
    assert not valid and "caveats" in err


def test_validate_payload_rejects_non_string_caveat() -> None:
    bad = _good_chain(caveats=["string ok", 42])
    valid, err = _validate_payload(bad)
    assert not valid and "caveats" in err


def test_validate_payload_required_steps_constant_matches_prompt() -> None:
    """REQUIRED_STEPS must match the 5 steps in the prompt template."""
    assert REQUIRED_STEPS == (
        "event", "first_order", "asset_reaction",
        "second_order", "cross_market",
    )


# =============================================================================
# chain_one — happy path + tier routing
# =============================================================================

def test_chain_one_happy_path_low_importance_uses_cheap() -> None:
    client = _make_client(_ok(_good_chain()))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS,
            "body": "body", "track": "macro", "importance": 50}

    result = chain_one(client, item)

    assert result.ok is True
    assert result.tier_used == "cheap"
    assert result.model_used == DEFAULT_CHEAP_MODEL
    assert result.confidence == 0.7
    assert result.chain is not None
    for step in REQUIRED_STEPS:
        assert step in result.chain


def test_chain_one_high_importance_uses_strong() -> None:
    client = _make_client(_ok(_good_chain(confidence=0.85)))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS,
            "body": "body", "track": "macro", "importance": 85}

    result = chain_one(client, item)

    assert result.ok is True
    assert result.tier_used == "strong"
    assert result.model_used == DEFAULT_STRONG_MODEL


def test_chain_one_at_exact_threshold_uses_strong() -> None:
    client = _make_client(_ok(_good_chain()))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS,
            "body": "body", "track": "macro", "importance": 70}

    result = chain_one(client, item)

    assert result.tier_used == "strong"


def test_chain_one_custom_threshold() -> None:
    client = _make_client(_ok(_good_chain()))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS,
            "body": "body", "track": "macro", "importance": 60}

    # Lower threshold to 50 → 60 should now be strong
    result = chain_one(client, item, importance_strong_threshold=50)

    assert result.tier_used == "strong"


def test_chain_one_truncates_body() -> None:
    long_body = "x" * 20000
    client = _make_client(_ok(_good_chain()))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS,
            "body": long_body, "track": "macro", "importance": 50}

    chain_one(client, item, body_input_chars=2000)

    sent = client._client.messages.calls[0]["messages"][0]["content"]
    # No 3000 contiguous x's should survive
    assert "x" * 3000 not in sent


def test_chain_one_passes_context_block() -> None:
    client = _make_client(_ok(_good_chain()))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS,
            "body": "b", "track": "macro", "importance": 50}

    chain_one(client, item, context_block="10Y UST: 4.21% (-3bp d/d)")

    sent = client._client.messages.calls[0]["messages"][0]["content"]
    assert "10Y UST: 4.21%" in sent


def test_chain_one_handles_unparseable_response() -> None:
    client = _make_client(_Message("Sorry, I can't generate a chain."))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS,
            "body": "b", "track": "macro", "importance": 50}

    result = chain_one(client, item)

    assert result.ok is False
    assert result.error is not None


def test_chain_one_handles_invalid_chain_structure() -> None:
    bad = _good_chain()
    del bad["cross_market"]  # missing step
    client = _make_client(_ok(bad))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS,
            "body": "b", "track": "macro", "importance": 50}

    result = chain_one(client, item)

    assert result.ok is False
    assert "cross_market" in (result.error or "")


def test_chain_one_records_cost_on_failure() -> None:
    client = _make_client(_Message("not json"))
    item = {"id": 1, "title": "t", "source": "s", "pub_ts": ISO_TS,
            "body": "b", "track": "macro", "importance": 50}

    result = chain_one(client, item)

    assert not result.ok
    assert result.cost_usd > 0


# =============================================================================
# apply_chain — DB write
# =============================================================================

def test_apply_chain_writes_causal_chain_column() -> None:
    chain = {step: _bilingual(f"en {step}", f"中 {step}") for step in REQUIRED_STEPS}
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(conn, title="Fed holds")
        result = CausalChainResult(
            item_id=item_id, ok=True,
            chain=chain, confidence=0.8, caveats=["minor caveat"],
            tier_used="cheap",
        )
        apply_chain(conn, result)

        row = conn.execute(
            "SELECT causal_chain FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        stored = json.loads(row["causal_chain"])

    for step in REQUIRED_STEPS:
        assert stored[step]["en"] == f"en {step}"
        assert stored[step]["zh"] == f"中 {step}"


def test_apply_chain_sets_processed_ts() -> None:
    chain = {step: _bilingual("en", "中") for step in REQUIRED_STEPS}
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(conn, title="t")
        result = CausalChainResult(
            item_id=item_id, ok=True, chain=chain,
            confidence=0.5, caveats=[],
        )
        apply_chain(conn, result)

        row = conn.execute(
            "SELECT processed_ts FROM items WHERE id = ?", (item_id,)
        ).fetchone()

    assert row["processed_ts"] is not None
    assert row["processed_ts"].endswith("Z")
    assert "T" in row["processed_ts"]


def test_apply_chain_merges_confidence_and_caveats_into_meta() -> None:
    chain = {step: _bilingual("en", "中") for step in REQUIRED_STEPS}
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(
            conn, title="Acquisition",
            meta={"feed_url": "https://x.com/feed",
                  "deal_size_usd_billions": 12.4,
                  "key_numbers": ["$12.4B"]},
        )
        result = CausalChainResult(
            item_id=item_id, ok=True, chain=chain,
            confidence=0.85, caveats=["regulatory uncertainty"],
        )
        apply_chain(conn, result)

        row = conn.execute(
            "SELECT meta FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        meta = json.loads(row["meta"])

    # ingestion + triage + summary keys preserved
    assert meta["feed_url"] == "https://x.com/feed"
    assert meta["deal_size_usd_billions"] == 12.4
    assert meta["key_numbers"] == ["$12.4B"]
    # chain keys added
    assert meta["chain_confidence"] == 0.85
    assert meta["chain_caveats"] == ["regulatory uncertainty"]


def test_apply_chain_no_caveats_skipped() -> None:
    """Empty caveats list shouldn't write a meta key."""
    chain = {step: _bilingual("en", "中") for step in REQUIRED_STEPS}
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(conn, title="t")
        result = CausalChainResult(
            item_id=item_id, ok=True, chain=chain,
            confidence=0.5, caveats=[],
        )
        apply_chain(conn, result)

        row = conn.execute(
            "SELECT meta FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        meta = json.loads(row["meta"])

    assert "chain_confidence" in meta
    assert "chain_caveats" not in meta


def test_apply_chain_noop_on_failed_result() -> None:
    with get_connection(":memory:") as conn:
        init_db(conn)
        item_id = _insert_item(conn, title="t")
        result = CausalChainResult(item_id=item_id, ok=False, error="parse fail")
        apply_chain(conn, result)

        row = conn.execute(
            "SELECT causal_chain, processed_ts FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()

    assert row["causal_chain"] is None
    assert row["processed_ts"] is None


# =============================================================================
# run_pending_causal_chain — selection
# =============================================================================

def test_run_pending_requires_summary() -> None:
    """Items without summary_en aren't ready for chain generation."""
    client = _make_client(_ok(_good_chain()))

    with get_connection(":memory:") as conn:
        init_db(conn)
        no_summary = _insert_item(conn, title="no summary",
                                   summary_en=None, summary_zh=None)
        with_summary = _insert_item(conn, title="with summary")

        summary = run_pending_causal_chain(conn, client)

        rows = list(conn.execute(
            "SELECT id, causal_chain FROM items ORDER BY id"
        ))

    assert summary.items_processed == 1
    assert next(r for r in rows if r["id"] == no_summary)["causal_chain"] is None
    assert next(r for r in rows if r["id"] == with_summary)["causal_chain"] is not None


def test_run_pending_excludes_other_track_by_default() -> None:
    client = _make_client(_ok(_good_chain()))

    with get_connection(":memory:") as conn:
        init_db(conn)
        _insert_item(conn, title="macro item", track="macro")
        _insert_item(conn, title="other item", track="other")

        summary = run_pending_causal_chain(conn, client)

    assert summary.items_processed == 1


def test_run_pending_skips_already_chained() -> None:
    client = _make_client(_ok(_good_chain()))

    with get_connection(":memory:") as conn:
        init_db(conn)
        _insert_item(conn, title="done", causal_chain='{"existing": "chain"}')
        _insert_item(conn, title="pending")

        summary = run_pending_causal_chain(conn, client)

    assert summary.items_processed == 1


def test_run_pending_processes_high_importance_first() -> None:
    """High-importance items get processed before low; brief lead items
    are ready even if the run is interrupted."""
    client = _make_client(_ok(_good_chain()))

    with get_connection(":memory:") as conn:
        init_db(conn)
        low = _insert_item(conn, title="low", importance=20,
                           pub_ts="2026-04-01T00:00:00Z")
        high = _insert_item(conn, title="high", importance=85,
                            pub_ts="2026-05-01T00:00:00Z")
        mid = _insert_item(conn, title="mid", importance=50,
                           pub_ts="2026-04-15T00:00:00Z")

        run_pending_causal_chain(conn, client, limit=1)

        chained_id = conn.execute(
            "SELECT id FROM items WHERE causal_chain IS NOT NULL"
        ).fetchone()["id"]

    assert chained_id == high


def test_run_pending_dry_run_no_llm_no_db() -> None:
    fake = FakeAnthropicClient(_ok(_good_chain()))
    client = LLMClient(api_key=API_KEY, client=fake)

    with get_connection(":memory:") as conn:
        init_db(conn)
        for i in range(3):
            _insert_item(conn, title=f"item {i}",
                         pub_ts=f"2026-04-{10+i:02d}T00:00:00Z")

        summary = run_pending_causal_chain(conn, client, dry_run=True)

        chained = conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE causal_chain IS NOT NULL"
        ).fetchone()["c"]

    assert summary.items_processed == 3
    assert summary.items_skipped_dry_run == 3
    assert summary.total_cost_usd == 0
    assert chained == 0
    assert fake.messages.calls == []


def test_run_pending_tallies_by_tier() -> None:
    """Mix of low + high importance — summary should report both tiers."""
    client = _make_client(_ok(_good_chain()))

    with get_connection(":memory:") as conn:
        init_db(conn)
        _insert_item(conn, title="cheap1", importance=30,
                     pub_ts="2026-04-01T00:00:00Z")
        _insert_item(conn, title="cheap2", importance=50,
                     pub_ts="2026-04-02T00:00:00Z")
        _insert_item(conn, title="strong1", importance=80,
                     pub_ts="2026-04-03T00:00:00Z")

        summary = run_pending_causal_chain(conn, client)

    assert summary.by_tier == {"cheap": 2, "strong": 1}


def test_run_pending_isolates_per_item_failures() -> None:
    call_index = {"i": 0}

    def responder(**kwargs: Any) -> Any:
        call_index["i"] += 1
        if call_index["i"] == 2:
            return _Message("not parseable")
        return _ok(_good_chain())

    client = _make_client(responder)

    with get_connection(":memory:") as conn:
        init_db(conn)
        for i in range(3):
            _insert_item(conn, title=f"item {i}",
                         pub_ts=f"2026-04-{10+i:02d}T00:00:00Z")

        summary = run_pending_causal_chain(conn, client)

        chained = conn.execute(
            "SELECT COUNT(*) AS c FROM items WHERE causal_chain IS NOT NULL"
        ).fetchone()["c"]

    assert summary.items_succeeded == 2
    assert summary.items_failed == 1
    assert chained == 2


def test_run_pending_propagates_budget_exceeded() -> None:
    meter = CostMeter(soft_cap_usd=15.0, hard_cap_usd=20.0)
    meter.record(20.0)
    client = _make_client(_ok(_good_chain()), cost_meter=meter)

    with get_connection(":memory:") as conn:
        init_db(conn)
        _insert_item(conn, title="item")

        with pytest.raises(BudgetExceededError):
            run_pending_causal_chain(conn, client)


def test_run_pending_min_importance_filters() -> None:
    client = _make_client(_ok(_good_chain()))

    with get_connection(":memory:") as conn:
        init_db(conn)
        _insert_item(conn, title="low", importance=20,
                     pub_ts="2026-04-01T00:00:00Z")
        _insert_item(conn, title="med", importance=55,
                     pub_ts="2026-04-02T00:00:00Z")
        _insert_item(conn, title="high", importance=85,
                     pub_ts="2026-04-03T00:00:00Z")

        summary = run_pending_causal_chain(conn, client, min_importance=50)

    assert summary.items_processed == 2  # med + high


def test_run_pending_calls_progress_callback() -> None:
    client = _make_client(_ok(_good_chain()))
    seen: list[tuple[int, int]] = []

    with get_connection(":memory:") as conn:
        init_db(conn)
        for i in range(2):
            _insert_item(conn, title=f"item {i}",
                         pub_ts=f"2026-04-{10+i:02d}T00:00:00Z")

        run_pending_causal_chain(
            conn, client,
            on_progress=lambda idx, total, result: seen.append((idx, total)),
        )

    assert seen == [(1, 2), (2, 2)]
