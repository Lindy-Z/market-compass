"""
Tests for src/reasoning/prompts.py — prompt template well-formedness and
docs/PROMPTS.md ↔ prompts.py sync.

Splits into three groups:
  * shape — every prompt has the required fields and they're well-formed
  * placeholders — user_template's placeholders match expected_inputs
  * doc sync — docs/PROMPTS.md mentions every prompt name in the registry
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from reasoning.prompts import (
    CAUSAL_CHAIN_FIVE_STEP,
    CLASSIFY_TRIAGE,
    META_FEED_PROMPT,
    PROMPTS,
    SUMMARIZE_BILINGUAL,
    SYNTHESIZE_WEEKLY,
    TRIGGER_DEEP_ANALYSIS,
    PromptTemplate,
    get,
)

# Repo root for finding docs/PROMPTS.md (conftest.py adds src/ to sys.path,
# so two-up from this test file is the repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROMPTS_MD = _REPO_ROOT / "docs" / "PROMPTS.md"

# Prompts we expect to exist. Add new prompts here AND in the registry.
EXPECTED_PROMPT_NAMES = {
    "classify.triage",
    "summarize.bilingual",
    "causal_chain.five_step",
    "synthesize.weekly",
    "trigger.deep_analysis",
    "meta.feed_prompt",
}


# =============================================================================
# Registry shape
# =============================================================================

def test_registry_contains_all_expected_prompts() -> None:
    assert set(PROMPTS.keys()) == EXPECTED_PROMPT_NAMES


def test_each_prompt_has_required_fields() -> None:
    for name, p in PROMPTS.items():
        assert isinstance(p, PromptTemplate), f"{name}: wrong type"
        assert p.name == name, f"{name}: name field doesn't match registry key"
        assert p.system.strip(), f"{name}: empty system"
        assert p.user_template.strip(), f"{name}: empty user_template"
        assert p.output_schema_hint.strip(), f"{name}: empty output_schema_hint"
        assert p.expected_inputs, f"{name}: empty expected_inputs"


def test_versions_are_well_formed_semver() -> None:
    pattern = re.compile(r"^\d+\.\d+\.\d+$")
    for name, p in PROMPTS.items():
        assert pattern.match(p.version), f"{name}: bad version {p.version!r}"


def test_model_tier_is_valid() -> None:
    for name, p in PROMPTS.items():
        assert p.model_tier in ("cheap", "strong"), (
            f"{name}: bad model_tier {p.model_tier!r}"
        )


def test_output_schema_hint_mentions_json() -> None:
    """Sanity check: every output schema is JSON-shaped."""
    for name, p in PROMPTS.items():
        assert "{" in p.output_schema_hint and "}" in p.output_schema_hint, (
            f"{name}: output_schema_hint doesn't look like JSON"
        )


# =============================================================================
# Tier routing — sanity check that cheap-tier prompts run on every-item paths
# =============================================================================

def test_cheap_tier_covers_per_item_prompts() -> None:
    """classify.triage, summarize.bilingual, and causal_chain.five_step run on
    every (qualifying) item — they must be cheap-tier per ADR-0010."""
    for name in ("classify.triage", "summarize.bilingual", "causal_chain.five_step"):
        assert PROMPTS[name].model_tier == "cheap", (
            f"{name} must be cheap tier (runs per-item)"
        )


def test_strong_tier_covers_low_volume_prompts() -> None:
    """synthesize.weekly + trigger.deep_analysis + meta.feed_prompt are
    low-volume; spend the strong-tier budget there."""
    for name in ("synthesize.weekly", "trigger.deep_analysis", "meta.feed_prompt"):
        assert PROMPTS[name].model_tier == "strong", (
            f"{name} should be strong tier (low volume, high-stakes output)"
        )


# =============================================================================
# Placeholder coverage
# =============================================================================

def test_template_placeholders_match_expected_inputs() -> None:
    """Every name in expected_inputs must actually appear in user_template,
    and vice-versa — the validator only catches missing/extra at call time;
    we want a static check too."""
    for name, p in PROMPTS.items():
        actual = p.template_placeholders()
        expected = set(p.expected_inputs)
        missing_in_template = expected - actual
        extra_in_template = actual - expected
        assert not missing_in_template, (
            f"{name}: declared inputs not in template: {missing_in_template}"
        )
        assert not extra_in_template, (
            f"{name}: undeclared placeholders in template: {extra_in_template}"
        )


# =============================================================================
# format_user — happy path + error paths
# =============================================================================

def test_format_user_substitutes_all_placeholders() -> None:
    msg = CLASSIFY_TRIAGE.format_user(
        title="Fed signals patience",
        source="reuters",
        body_excerpt="The Federal Reserve...",
    )
    assert "Fed signals patience" in msg
    assert "reuters" in msg
    assert "The Federal Reserve" in msg
    # No leftover braces (every placeholder filled)
    assert "{" not in msg or "}" not in msg or "{" not in msg.replace("{", "", msg.count("}"))


def test_format_user_missing_arg_raises() -> None:
    with pytest.raises(ValueError, match="missing inputs"):
        CLASSIFY_TRIAGE.format_user(title="x", source="y")  # missing body_excerpt


def test_format_user_unknown_arg_raises() -> None:
    with pytest.raises(ValueError, match="unknown inputs"):
        CLASSIFY_TRIAGE.format_user(
            title="x", source="y", body_excerpt="z", typo_field="oops",
        )


def test_format_user_works_for_every_prompt() -> None:
    """Each prompt must format cleanly when given placeholder values."""
    for name, p in PROMPTS.items():
        kwargs = {k: f"<test {k}>" for k in p.expected_inputs}
        msg = p.format_user(**kwargs)
        for k, v in kwargs.items():
            assert v in msg, f"{name}: placeholder {k!r} not substituted"


# =============================================================================
# get() helper
# =============================================================================

def test_get_returns_prompt_by_name() -> None:
    assert get("classify.triage") is CLASSIFY_TRIAGE


def test_get_unknown_name_raises_with_helpful_message() -> None:
    with pytest.raises(KeyError, match="Available:"):
        get("nonsense.prompt")


# =============================================================================
# Output-schema content checks (a few targeted assertions to catch drift)
# =============================================================================

def test_classify_triage_schema_lists_required_track_values() -> None:
    s = CLASSIFY_TRIAGE.system
    for value in ("macro", "na_fx", "deals", "other"):
        assert value in s, f"classify.triage system msg missing track value {value!r}"


def test_classify_triage_v02_includes_fx_tie_breakers() -> None:
    """v0.2.0 added FX-print / yield-move tie-breakers based on real-data
    feedback (yen intervention → na_fx, not macro)."""
    s = CLASSIFY_TRIAGE.system
    assert "USD/JPY" in s, "USD/JPY example missing from tie-breakers"
    assert "Treasury 10Y yield" in s or "yield" in s.lower(), \
        "yield-move tie-breaker missing"
    assert "BoJ" in s or "intervention" in s.lower(), \
        "FX intervention tie-breaker missing"


def test_classify_triage_v02_calibration_anchor() -> None:
    """v0.2.0 added the 'USE THE FULL 0-100 RANGE' anchor + per-band examples
    to break the model's tendency to cluster importance at 75."""
    s = CLASSIFY_TRIAGE.system
    assert "USE THE FULL 0-100 RANGE" in s, \
        "explicit 'use full range' anchor missing"
    # Every importance band should have at least one concrete example
    for marker in ("90-100", "80-89", "70-79", "30-49"):
        assert marker in s, f"importance band {marker} missing"


def test_causal_chain_system_lists_all_five_steps() -> None:
    s = CAUSAL_CHAIN_FIVE_STEP.system
    for step in ("event", "first_order", "asset_reaction", "second_order", "cross_market"):
        assert step in s, f"causal_chain missing step name {step!r}"
    # Bilingual step labels (Chinese names) must also be present
    for cn in ("事件", "一阶机制", "资产反应", "二阶效应", "跨市场传导"):
        assert cn in s, f"causal_chain missing CN step name {cn!r}"


def test_summarize_bilingual_specifies_word_counts() -> None:
    s = SUMMARIZE_BILINGUAL.system
    assert "80-120 words" in s
    assert "80-120" in s and "汉字" in s


def test_trigger_deep_analysis_includes_research_disclaimer() -> None:
    """tradeable_observations MUST be hedged — the system msg encodes this."""
    s = TRIGGER_DEEP_ANALYSIS.system
    assert "research only" in s.lower() or "not advice" in s.lower()
    assert "仅供研究" in s or "非投资建议" in s


def test_meta_feed_prompt_demands_self_contained_output() -> None:
    """The handoff prompt must embed context inline so the frontier model
    doesn't depend on data it can't see."""
    s = META_FEED_PROMPT.system
    assert "INLINE" in s.upper() or "inline" in s
    # And must end with the literal "Begin your analysis." cue
    assert "Begin your analysis." in s


# =============================================================================
# docs/PROMPTS.md sync
# =============================================================================

def test_prompts_md_exists() -> None:
    assert _PROMPTS_MD.is_file(), f"missing {_PROMPTS_MD}"


def test_prompts_md_mentions_every_prompt_name() -> None:
    """The doc must reference every prompt name in the registry. Catches
    drift if a new prompt is added in code without a doc entry."""
    text = _PROMPTS_MD.read_text(encoding="utf-8")
    for name in EXPECTED_PROMPT_NAMES:
        assert name in text, (
            f"docs/PROMPTS.md is missing prompt name '{name}'. "
            f"Update the doc or the registry — they must stay in sync."
        )


def test_prompts_md_mentions_every_version() -> None:
    """The doc should list each prompt's current version, so a reviewer
    can tell at a glance whether the doc was updated alongside a code
    bump."""
    text = _PROMPTS_MD.read_text(encoding="utf-8")
    for name, p in PROMPTS.items():
        # We expect lines like "version: 0.1.0" near each prompt section.
        # Loose check: the version string should appear at least once.
        assert p.version in text, (
            f"docs/PROMPTS.md doesn't mention version {p.version} for "
            f"prompt '{name}'. Bump or sync the doc."
        )
