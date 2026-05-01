"""
Tests for src/reasoning/llm_client.py.

We avoid the real ``anthropic`` SDK by injecting a tiny ``FakeAnthropicClient``.
This means the test suite runs without ``anthropic`` installed, and we can
exercise edge cases the real API rarely produces (truncated content, weird
fenced JSON, missing usage fields, etc.).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

import pytest

from reasoning.llm_client import (
    DEFAULT_CHEAP_MODEL,
    DEFAULT_HARD_CAP_USD,
    DEFAULT_SOFT_CAP_USD,
    DEFAULT_STRONG_MODEL,
    MODEL_PRICES,
    BudgetExceededError,
    CostMeter,
    LLMClient,
    LLMResponse,
    compute_cost,
    extract_json,
)
from reasoning.prompts import (
    CAUSAL_CHAIN_FIVE_STEP,
    CLASSIFY_TRIAGE,
    SUMMARIZE_BILINGUAL,
    PromptTemplate,
)

API_KEY = "test"  # short on purpose — see ADR-0013 + install-hooks.sh


# =============================================================================
# Fake Anthropic SDK
# =============================================================================

class FakeContentBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeUsage:
    def __init__(
        self,
        input_tokens: int = 100,
        output_tokens: int = 50,
        cache_read_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


_UNSET = object()


class FakeMessage:
    def __init__(self, text: str, usage: Any = _UNSET) -> None:
        self.content = [FakeContentBlock(text)]
        # Sentinel-based default so callers can pass ``usage=None`` to
        # exercise the "no usage attribute" path explicitly.
        self.usage = FakeUsage() if usage is _UNSET else usage


class FakeMessages:
    def __init__(
        self,
        responder: "FakeMessage | Callable[..., Any]",
    ) -> None:
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
    def __init__(self, responder: "FakeMessage | Callable[..., Any]") -> None:
        self.messages = FakeMessages(responder)


def _ok_message(payload: Any, **usage_kwargs: Any) -> FakeMessage:
    """Build a FakeMessage whose text is the JSON-encoded payload."""
    return FakeMessage(json.dumps(payload), FakeUsage(**usage_kwargs))


# =============================================================================
# compute_cost
# =============================================================================

def test_compute_cost_haiku_45() -> None:
    # 1M input + 1M output on Haiku 4.5 should be $1 + $5 = $6.
    cost = compute_cost(
        DEFAULT_CHEAP_MODEL,
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cost == pytest.approx(6.0)


def test_compute_cost_opus_47() -> None:
    cost = compute_cost(
        DEFAULT_STRONG_MODEL,
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cost == pytest.approx(30.0)  # $5 + $25


def test_compute_cost_partial_million() -> None:
    cost = compute_cost(
        DEFAULT_CHEAP_MODEL,
        input_tokens=500,
        output_tokens=200,
    )
    expected = (500 / 1_000_000) * 1.0 + (200 / 1_000_000) * 5.0
    assert cost == pytest.approx(expected)


def test_compute_cost_with_cache() -> None:
    cost = compute_cost(
        DEFAULT_CHEAP_MODEL,
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=10_000,
        cache_creation_tokens=2_000,
    )
    expected = (
        (1000 / 1_000_000) * 1.0
        + (500 / 1_000_000) * 5.0
        + (10_000 / 1_000_000) * 0.10
        + (2_000 / 1_000_000) * 1.25
    )
    assert cost == pytest.approx(expected)


def test_compute_cost_unknown_model_returns_zero() -> None:
    cost = compute_cost(
        "claude-imaginary-99",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cost == 0.0


def test_known_pricing_table_covers_required_models() -> None:
    assert DEFAULT_CHEAP_MODEL in MODEL_PRICES
    assert DEFAULT_STRONG_MODEL in MODEL_PRICES


# =============================================================================
# CostMeter
# =============================================================================

def test_cost_meter_starts_at_zero() -> None:
    m = CostMeter()
    assert m.total_usd == 0.0
    assert m.call_count == 0
    assert m.status() == "ok"


def test_cost_meter_record_accumulates() -> None:
    m = CostMeter()
    m.record(1.0)
    m.record(2.5)
    assert m.total_usd == 3.5
    assert m.call_count == 2


def test_cost_meter_rejects_negative_cost() -> None:
    m = CostMeter()
    with pytest.raises(ValueError, match="negative cost"):
        m.record(-0.01)


def test_cost_meter_status_transitions() -> None:
    m = CostMeter(soft_cap_usd=10.0, hard_cap_usd=20.0)
    assert m.status() == "ok"
    m.record(7.5)  # 75% of soft cap
    assert m.status() == "warning"
    m.record(2.5)  # at soft cap (10.0)
    assert m.status() == "soft_breach"
    m.record(10.0)  # at hard cap (20.0)
    assert m.status() == "hard_breach"


def test_cost_meter_remaining_helpers() -> None:
    m = CostMeter(soft_cap_usd=15.0, hard_cap_usd=20.0)
    m.record(5.0)
    assert m.remaining_soft() == pytest.approx(10.0)
    assert m.remaining_hard() == pytest.approx(15.0)
    m.record(20.0)
    assert m.remaining_soft() == pytest.approx(-10.0)
    assert m.remaining_hard() == pytest.approx(-5.0)


# =============================================================================
# extract_json
# =============================================================================

def test_extract_json_raw_object() -> None:
    parsed, err = extract_json('{"a": 1, "b": "two"}')
    assert err is None
    assert parsed == {"a": 1, "b": "two"}


def test_extract_json_raw_array() -> None:
    parsed, err = extract_json('[1, 2, 3]')
    assert err is None
    assert parsed == [1, 2, 3]


def test_extract_json_with_fenced_code_block() -> None:
    text = """Here you go:

```json
{"track": "macro", "importance": 80}
```

Anything else?"""
    parsed, err = extract_json(text)
    assert err is None
    assert parsed == {"track": "macro", "importance": 80}


def test_extract_json_with_unfenced_prose_around() -> None:
    text = (
        "Sure, here's the analysis:\n\n"
        '{"track": "deals", "deal_size_usd_billions": 12.4}\n\n'
        "Let me know if you want more."
    )
    parsed, err = extract_json(text)
    assert err is None
    assert parsed == {"track": "deals", "deal_size_usd_billions": 12.4}


def test_extract_json_handles_chinese() -> None:
    parsed, err = extract_json('{"summary_zh": "美联储维持利率不变"}')
    assert err is None
    assert parsed == {"summary_zh": "美联储维持利率不变"}


def test_extract_json_empty_response() -> None:
    parsed, err = extract_json("")
    assert parsed is None
    assert err == "empty response"


def test_extract_json_no_json_at_all() -> None:
    parsed, err = extract_json("Just some prose with no braces.")
    assert parsed is None
    assert err is not None


def test_extract_json_malformed_object() -> None:
    parsed, err = extract_json("{not valid json}")
    assert parsed is None
    assert err is not None


# =============================================================================
# LLMClient — construction and from_env
# =============================================================================

def test_construction_requires_non_empty_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        LLMClient(api_key="", client=FakeAnthropicClient(_ok_message({})))


def test_construction_takes_injected_client() -> None:
    fake = FakeAnthropicClient(_ok_message({}))
    c = LLMClient(api_key=API_KEY, client=fake)
    assert c._client is fake


def test_from_env_reads_required_key(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    monkeypatch.setenv("LLM_CHEAP_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("LLM_STRONG_MODEL", "claude-opus-4-7")
    fake = FakeAnthropicClient(_ok_message({}))
    c = LLMClient.from_env(client=fake)
    assert c.cheap_model == "claude-haiku-4-5-20251001"
    assert c.strong_model == "claude-opus-4-7"


def test_from_env_falls_back_to_defaults_when_models_unset(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    monkeypatch.delenv("LLM_CHEAP_MODEL", raising=False)
    monkeypatch.delenv("LLM_STRONG_MODEL", raising=False)
    fake = FakeAnthropicClient(_ok_message({}))
    c = LLMClient.from_env(client=fake)
    assert c.cheap_model == DEFAULT_CHEAP_MODEL
    assert c.strong_model == DEFAULT_STRONG_MODEL


def test_from_env_raises_when_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        LLMClient.from_env()


# =============================================================================
# LLMClient.call — happy path + routing
# =============================================================================

def test_call_routes_cheap_prompt_to_cheap_model() -> None:
    fake = FakeAnthropicClient(_ok_message(
        {"track": "macro", "track_confidence": 0.9, "importance": 50,
         "deal_size_usd_billions": None, "reason": "Fed action"}
    ))
    c = LLMClient(api_key=API_KEY, client=fake)
    resp = c.call(
        CLASSIFY_TRIAGE,
        title="Fed leaves rates unchanged",
        source="reuters",
        body_excerpt="The Federal Reserve...",
    )
    assert resp.model == DEFAULT_CHEAP_MODEL
    assert resp.tier == "cheap"
    # Verify it was the cheap model passed to the SDK
    call = fake.messages.calls[0]
    assert call["model"] == DEFAULT_CHEAP_MODEL


def test_call_routes_strong_prompt_to_strong_model() -> None:
    from reasoning.prompts import SYNTHESIZE_WEEKLY
    fake = FakeAnthropicClient(_ok_message({"week_ending": "2026-04-30"}))
    c = LLMClient(api_key=API_KEY, client=fake)
    resp = c.call(
        SYNTHESIZE_WEEKLY,
        week_ending="2026-04-30",
        items_json="[]",
        market_levels_json="{}",
    )
    assert resp.model == DEFAULT_STRONG_MODEL
    assert resp.tier == "strong"


def test_call_tier_override_promotes_cheap_to_strong() -> None:
    """High-importance items should run the (default-cheap) causal_chain
    on the strong model when caller passes tier_override='strong'."""
    fake = FakeAnthropicClient(_ok_message({
        "event": {"en": "x", "zh": "x"},
        "first_order": {"en": "x", "zh": "x"},
        "asset_reaction": {"en": "x", "zh": "x"},
        "second_order": {"en": "x", "zh": "x"},
        "cross_market": {"en": "x", "zh": "x"},
        "confidence": 0.7, "caveats": [],
    }))
    c = LLMClient(api_key=API_KEY, client=fake)
    resp = c.call(
        CAUSAL_CHAIN_FIVE_STEP,
        tier_override="strong",
        title="Fed pivots", source="x", pub_ts="2026-04-30T12:00:00Z",
        body="...", track="macro", importance=85, context_block="",
    )
    assert resp.tier == "strong"
    assert resp.model == DEFAULT_STRONG_MODEL


def test_call_format_user_failure_propagates() -> None:
    fake = FakeAnthropicClient(_ok_message({}))
    c = LLMClient(api_key=API_KEY, client=fake)
    with pytest.raises(ValueError, match="missing inputs"):
        c.call(CLASSIFY_TRIAGE, title="x")  # missing source / body_excerpt
    # And the SDK was NOT called
    assert fake.messages.calls == []


# =============================================================================
# LLMClient.call — system message + caching
# =============================================================================

def test_call_includes_prompt_system_text() -> None:
    fake = FakeAnthropicClient(_ok_message({}))
    c = LLMClient(api_key=API_KEY, client=fake, enable_caching=False)
    c.call(
        CLASSIFY_TRIAGE,
        title="t", source="s", body_excerpt="b",
    )
    sent_system = fake.messages.calls[0]["system"]
    # When caching is OFF, system is sent as a plain string
    assert isinstance(sent_system, str)
    assert "track" in sent_system  # part of the CLASSIFY_TRIAGE system msg


def test_call_with_caching_sends_blocks_with_cache_control() -> None:
    fake = FakeAnthropicClient(_ok_message({}))
    c = LLMClient(api_key=API_KEY, client=fake, enable_caching=True)
    c.call(
        CLASSIFY_TRIAGE,
        title="t", source="s", body_excerpt="b",
    )
    sent_system = fake.messages.calls[0]["system"]
    assert isinstance(sent_system, list)
    assert sent_system[0]["type"] == "text"
    assert sent_system[0]["cache_control"] == {"type": "ephemeral"}


def test_call_user_message_uses_format_user() -> None:
    fake = FakeAnthropicClient(_ok_message({}))
    c = LLMClient(api_key=API_KEY, client=fake)
    c.call(
        CLASSIFY_TRIAGE,
        title="Fed signals patience",
        source="reuters",
        body_excerpt="The Federal Reserve signaled patience.",
    )
    msgs = fake.messages.calls[0]["messages"]
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert "Fed signals patience" in msgs[0]["content"]
    assert "reuters" in msgs[0]["content"]


# =============================================================================
# LLMClient.call — usage + cost tracking
# =============================================================================

def test_call_records_token_counts() -> None:
    fake = FakeAnthropicClient(_ok_message(
        {"track": "macro"},
        input_tokens=234,
        output_tokens=89,
    ))
    c = LLMClient(api_key=API_KEY, client=fake)
    resp = c.call(
        CLASSIFY_TRIAGE,
        title="t", source="s", body_excerpt="b",
    )
    assert resp.input_tokens == 234
    assert resp.output_tokens == 89


def test_call_computes_cost_correctly() -> None:
    fake = FakeAnthropicClient(_ok_message(
        {"track": "macro"},
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    ))
    c = LLMClient(api_key=API_KEY, client=fake)
    resp = c.call(
        CLASSIFY_TRIAGE,
        title="t", source="s", body_excerpt="b",
    )
    # Haiku 4.5 = $1 input + $5 output = $6 per million each
    assert resp.cost_usd == pytest.approx(6.0)


def test_call_accumulates_into_cost_meter() -> None:
    fake = FakeAnthropicClient(
        lambda **kwargs: _ok_message(
            {"track": "macro"},
            input_tokens=1_000_000, output_tokens=1_000_000,
        )
    )
    c = LLMClient(api_key=API_KEY, client=fake)
    c.call(CLASSIFY_TRIAGE, title="t", source="s", body_excerpt="b")
    c.call(CLASSIFY_TRIAGE, title="t", source="s", body_excerpt="b")
    assert c.cost_meter.total_usd == pytest.approx(12.0)
    assert c.cost_meter.call_count == 2


def test_call_records_cache_token_counts_when_present() -> None:
    fake = FakeAnthropicClient(_ok_message(
        {"track": "macro"},
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=5_000,
        cache_creation_input_tokens=1_000,
    ))
    c = LLMClient(api_key=API_KEY, client=fake)
    resp = c.call(
        CLASSIFY_TRIAGE,
        title="t", source="s", body_excerpt="b",
    )
    assert resp.cache_read_tokens == 5_000
    assert resp.cache_creation_tokens == 1_000


def test_call_handles_missing_usage_field_gracefully() -> None:
    """If Anthropic response has no .usage attribute, default to zeros."""
    fake_message = FakeMessage(json.dumps({"track": "macro"}), usage=None)
    fake = FakeAnthropicClient(fake_message)
    c = LLMClient(api_key=API_KEY, client=fake)
    resp = c.call(
        CLASSIFY_TRIAGE,
        title="t", source="s", body_excerpt="b",
    )
    assert resp.input_tokens == 0
    assert resp.output_tokens == 0
    assert resp.cost_usd == 0.0


# =============================================================================
# LLMClient.call — JSON parsing
# =============================================================================

def test_call_parses_clean_json_response() -> None:
    payload = {"track": "macro", "track_confidence": 0.9,
               "importance": 50, "deal_size_usd_billions": None,
               "reason": "test"}
    fake = FakeAnthropicClient(_ok_message(payload))
    c = LLMClient(api_key=API_KEY, client=fake)
    resp = c.call(CLASSIFY_TRIAGE, title="t", source="s", body_excerpt="b")
    assert resp.parsed == payload
    assert resp.parse_error is None


def test_call_parses_fenced_json_response() -> None:
    fake_message = FakeMessage(
        'Here is your answer:\n```json\n{"track": "macro"}\n```\n'
    )
    fake = FakeAnthropicClient(fake_message)
    c = LLMClient(api_key=API_KEY, client=fake)
    resp = c.call(CLASSIFY_TRIAGE, title="t", source="s", body_excerpt="b")
    assert resp.parsed == {"track": "macro"}


def test_call_records_parse_error_for_unparseable_response() -> None:
    fake_message = FakeMessage("Sorry, I can't help with that.")
    fake = FakeAnthropicClient(fake_message)
    c = LLMClient(api_key=API_KEY, client=fake)
    resp = c.call(CLASSIFY_TRIAGE, title="t", source="s", body_excerpt="b")
    assert resp.parsed is None
    assert resp.parse_error is not None
    # raw_text is preserved so caller can inspect
    assert "Sorry" in resp.raw_text


# =============================================================================
# LLMClient.call — budget guard
# =============================================================================

def test_call_raises_when_hard_cap_already_breached() -> None:
    meter = CostMeter(soft_cap_usd=15.0, hard_cap_usd=20.0)
    meter.record(20.0)  # at hard cap
    fake = FakeAnthropicClient(_ok_message({}))
    c = LLMClient(api_key=API_KEY, client=fake, cost_meter=meter)
    with pytest.raises(BudgetExceededError, match="hard cap"):
        c.call(CLASSIFY_TRIAGE, title="t", source="s", body_excerpt="b")
    # No SDK call made
    assert fake.messages.calls == []


def test_call_proceeds_below_hard_cap_even_if_soft_breached() -> None:
    """Soft breach is a warning, not a refusal."""
    meter = CostMeter(soft_cap_usd=10.0, hard_cap_usd=20.0)
    meter.record(15.0)  # past soft, under hard
    fake = FakeAnthropicClient(_ok_message({"track": "macro"}))
    c = LLMClient(api_key=API_KEY, client=fake, cost_meter=meter)
    resp = c.call(CLASSIFY_TRIAGE, title="t", source="s", body_excerpt="b")
    assert resp.parsed == {"track": "macro"}


# =============================================================================
# LLMResponse — sanity
# =============================================================================

def test_response_carries_prompt_metadata() -> None:
    fake = FakeAnthropicClient(_ok_message({"track": "macro"}))
    c = LLMClient(api_key=API_KEY, client=fake)
    resp = c.call(CLASSIFY_TRIAGE, title="t", source="s", body_excerpt="b")
    assert resp.prompt_name == "classify.triage"
    assert resp.prompt_version == CLASSIFY_TRIAGE.version


def test_response_duration_is_non_negative() -> None:
    fake = FakeAnthropicClient(_ok_message({"track": "macro"}))
    c = LLMClient(api_key=API_KEY, client=fake)
    resp = c.call(CLASSIFY_TRIAGE, title="t", source="s", body_excerpt="b")
    assert resp.duration_seconds >= 0
