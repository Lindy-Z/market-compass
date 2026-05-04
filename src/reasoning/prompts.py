"""
market-compass — versioned LLM prompt templates
==============================================================================
Mirrors ``docs/PROMPTS.md``. Code is the runtime truth; the doc is the
review artifact. They MUST stay in sync — ``tests/test_prompts.py``
enforces that the prompt names match.

代码是运行时真理,docs/PROMPTS.md 是评审产物,二者名称必须一致;
``tests/test_prompts.py`` 自动校验。

Versioning policy / 版本策略
-------------------------
- Each prompt has a ``version`` (semver-ish ``"MAJOR.MINOR.PATCH"``).
- Bump PATCH for whitespace / comment-only edits.
- Bump MINOR for behavior-affecting wording changes that don't break
  the output schema.
- Bump MAJOR when the output schema changes (parsers must adapt).
- Any non-PATCH bump should be accompanied by a CHANGELOG entry
  (and ADR if the change is non-trivial).

Usage / 用法
------------

    from reasoning.prompts import PROMPTS

    p = PROMPTS["classify.triage"]
    user_msg = p.format_user(
        title="Fed leaves rates unchanged",
        source="reuters",
        body_excerpt="The Federal Reserve left its target range at 5.25-5.50%.",
    )
    # Then send {system: p.system, messages: [{role: user, content: user_msg}]}
"""
from __future__ import annotations

import string
from dataclasses import dataclass, field
from typing import Literal

ModelTier = Literal["cheap", "strong"]


# =============================================================================
# Dataclass
# =============================================================================

@dataclass(frozen=True)
class PromptTemplate:
    """One versioned prompt.

    Args:
        name: canonical dotted name, e.g. ``"classify.triage"``.
            Must match the heading in PROMPTS.md.
        version: semver string ``"MAJOR.MINOR.PATCH"``.
        model_tier: ``"cheap"`` (Haiku 4.5) or ``"strong"`` (Opus 4.7),
            per ADR-0010. Drives model routing in the LLM client.
        system: System message sent as Anthropic's ``system=`` parameter.
            Stays constant across calls within the same prompt — eligible
            for prompt caching by the LLM client.
        user_template: ``str.format``-style template for the user message.
            Placeholders must be NAMED (``{title}``), not positional.
        expected_inputs: every placeholder name the template uses. Used
            by :meth:`format_user` to validate caller-supplied kwargs
            *before* spending an API call on bad inputs.
        output_schema_hint: short, human-readable description of the
            expected output. Mirrored verbatim into the system message
            in most prompts (so it lives in one place).
        notes: free-form notes / regression criteria.
    """

    name: str
    version: str
    model_tier: ModelTier
    system: str
    user_template: str
    expected_inputs: tuple[str, ...]
    output_schema_hint: str
    notes: str = ""

    def format_user(self, **kwargs: object) -> str:
        """
        Render the user message by substituting all ``expected_inputs``.

        Raises:
            ValueError: if any expected input is missing, or if any
                supplied kwarg isn't an expected placeholder (typo guard).
        """
        provided = set(kwargs.keys())
        expected = set(self.expected_inputs)
        missing = expected - provided
        unknown = provided - expected
        if missing:
            raise ValueError(
                f"prompt '{self.name}' missing inputs: "
                f"{sorted(missing)}"
            )
        if unknown:
            raise ValueError(
                f"prompt '{self.name}' got unknown inputs: "
                f"{sorted(unknown)} (typo? extra arg?)"
            )
        return self.user_template.format(**kwargs)

    def template_placeholders(self) -> set[str]:
        """Extract every ``{name}`` placeholder actually in ``user_template``."""
        return {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(self.user_template)
            if field_name is not None and field_name != ""
        }


# =============================================================================
# 1. classify.triage  (cheap)
# -----------------------------------------------------------------------------
# Combines track classification + importance score + deal-size extraction
# in ONE cheap-tier call to save tokens / money. Runs on every item.
# 一次性返回信息轨、重要性、并购金额 (若有), 节省单条调用成本。
# =============================================================================

CLASSIFY_TRIAGE = PromptTemplate(
    name="classify.triage",
    version="0.2.0",
    model_tier="cheap",
    system="""You are a triage classifier for a financial-news pipeline. Given a news item (headline + body excerpt + source), output a single JSON object with these fields and no other text:

- track: one of "macro" | "na_fx" | "deals" | "other"
    macro    = central bank policy decisions, monetary/fiscal policy, geopolitics, supply shocks, cross-border capital flows where the POLICY or EVENT is the lede
    na_fx    = North American equity moves, US Treasury yield moves, FX-pair price moves, commodity price moves where the PRICE PRINT is the lede
    deals    = M&A, takeovers, IPOs, major corporate restructuring (>=$1B implied or stated)
    other    = company earnings without broader implications, sports, weather, anything else

- track_confidence: float 0.0-1.0

- importance: int 0-100. **USE THE FULL 0-100 RANGE — DO NOT ANCHOR AT 75.** Calibration:
    90-100 = once-a-quarter event: deal >= $5B; central-bank surprise hike/cut/intervention; war onset; market move > 5%; named regulatory regime change
    80-89  = highly significant: deal $1-5B with strategic angle; scheduled CB statement with material guidance change; market move 2-5%; major FX intervention; serious geopolitical escalation
    70-79  = lead-item-worthy: deal $1B+ standard M&A; scheduled CB statement (no surprise); meaningful geopolitical headline; >$30B FX intervention is borderline 80
    50-69  = solid background context; supports a trend story
    30-49  = brief mention only
    0-29   = archive only, not in the daily brief

- deal_size_usd_billions: float | null (only set when the item states or strongly implies a deal size)

- reason: short string (<= 30 words) describing the classification

Tie-breakers (THE PRINT IN THE HEADLINE WINS over the cause):
- "Fed cuts rates"                                        -> macro    (policy is the headline)
- "S&P 500 falls 2% on Fed cut"                           -> na_fx    (price move is the headline)
- "USD/JPY drops from 160 to 155 on BoJ intervention"     -> na_fx    (FX print is the headline)
- "Treasury 10Y yield jumps 12bp on hot CPI"              -> na_fx    (yield move is the headline)
- "BoJ FX intervention pushes yen 5 figures stronger"     -> na_fx    (the move is the lede)
- "Yen rallies sharply as BoJ intervenes; ~$30bn deployed"-> na_fx    (price action + size; intervention itself is borderline 80-89)
- "BoJ raises policy rate by 25bp"                        -> macro    (policy is the headline)
- "Oil up 5% as Iran tensions escalate"                   -> na_fx    (commodity print leads); macro if the article framing is geopolitics-first
- "Microsoft to acquire X for $10B"                       -> deals    (importance 90+ since >=$5B)
- "8-K: Departure of director / change in bylaws"         -> other    (procedural filing; routine 8-K)
- Earnings beat/miss without broader market implications  -> other

Be decisive. Do not output "unknown". Output exactly one JSON object — no preamble, no markdown fences, no commentary.""",
    user_template=(
        "Title: {title}\n"
        "Source: {source}\n"
        "Body excerpt (first ~500 chars):\n"
        "{body_excerpt}"
    ),
    expected_inputs=("title", "source", "body_excerpt"),
    output_schema_hint=(
        '{"track":"macro|na_fx|deals|other","track_confidence":0.0-1.0,'
        '"importance":0-100,"deal_size_usd_billions":number|null,"reason":"string"}'
    ),
    notes=(
        "Run on EVERY ingested item. Latency-sensitive — keep system msg tight. "
        "Eligible for prompt-caching since the system msg is constant."
    ),
)


# =============================================================================
# 2. summarize.bilingual  (cheap)
# =============================================================================

SUMMARIZE_BILINGUAL = PromptTemplate(
    name="summarize.bilingual",
    version="0.1.0",
    model_tier="cheap",
    system="""You are a bilingual financial-news summarizer. Given one news item, produce two summaries — one English, one Simplified Chinese — plus a list of key numerical facts.

Constraints:
- English summary: 80-120 words.
- Chinese summary: 80-120 Chinese characters (汉字), idiomatic prose, NOT a literal translation of the English. Each version should read naturally in its own language.
- Preserve EXACTLY: numbers, percentages, basis points, currency amounts, dates, company / person / agency names, tickers, ISIN / CIK / etc. Do NOT round numerical values.
- Do NOT speculate, predict, or add opinion. Pure summary of what the article says.
- If the body is in a language other than English, translate faithfully.

Output exactly one JSON object, no other text, no markdown fences:
{
  "summary_en": "string, 80-120 words",
  "summary_zh": "string, 80-120 汉字",
  "key_numbers": ["string", ...]   // each entry a short fact, e.g. "$12.4B deal value", "10Y UST +8bp to 4.21%"
}""",
    user_template=(
        "Title: {title}\n"
        "Source: {source}\n"
        "Published: {pub_ts}\n"
        "Body:\n{body}"
    ),
    expected_inputs=("title", "source", "pub_ts", "body"),
    output_schema_hint=(
        '{"summary_en":"string","summary_zh":"string","key_numbers":["string",...]}'
    ),
    notes=(
        "Output goes into items.summary_en / items.summary_zh. "
        "key_numbers feeds the trigger detector (deal_size, market-move thresholds)."
    ),
)


# =============================================================================
# 3. causal_chain.five_step  (cheap default; strong for items with importance>=70)
# -----------------------------------------------------------------------------
# The 5-step template per ARCHITECTURE.md and the project brief.
# 五步因果链, 双语输出。
# =============================================================================

CAUSAL_CHAIN_FIVE_STEP = PromptTemplate(
    name="causal_chain.five_step",
    version="0.1.0",
    model_tier="cheap",
    system="""You are a senior macro / cross-asset strategist trained to reason about causation in financial markets. Given a news item plus optional current-market context, produce a 5-step causal chain in the bilingual format specified below.

The five steps are NON-NEGOTIABLE — each must be present, each must be <= 3 sentences in EACH language, and each must contain BOTH English and Chinese:

  1. event (事件)            — what happened, one sentence, factual.
  2. first_order (一阶机制)   — the direct propagation channel: rates / flows / earnings / policy / supply.
  3. asset_reaction (资产反应) — which assets moved or should move; direction + rough magnitude;
                                 use "unknown" only when truly unobservable.
  4. second_order (二阶效应)  — downstream implications: who else is affected, what positioning shifts follow.
  5. cross_market (跨市场传导) — how it propagates across asset classes / geographies; explicit tail-risk note when relevant.

Style rules (apply to BOTH languages):
- Causal language ("X causes Y because Z"), not correlational ("X and Y both moved").
- State direction AND rough magnitude when possible: "10Y UST yields up ~8bp" beats "yields rose".
- For tail risks, prefix with "tail-risk:" / "尾部风险:" so they're greppable.
- No hedge fillers ("perhaps", "could potentially", "或许"). Confidence is a separate field — calibrate there.
- Plain prose; no bullet lists inside a step.

Output exactly one JSON object, no other text, no markdown fences:
{
  "event":          {"en": "string", "zh": "字符串"},
  "first_order":    {"en": "string", "zh": "字符串"},
  "asset_reaction": {"en": "string", "zh": "字符串"},
  "second_order":   {"en": "string", "zh": "字符串"},
  "cross_market":   {"en": "string", "zh": "字符串"},
  "confidence":     0.0-1.0,
  "caveats":        ["string", ...]
}""",
    user_template=(
        "Item title: {title}\n"
        "Source: {source}\n"
        "Published: {pub_ts}\n"
        "Body: {body}\n"
        "Track: {track}\n"
        "Importance (0-100): {importance}\n"
        "{context_block}"
    ),
    expected_inputs=(
        "title", "source", "pub_ts", "body", "track", "importance", "context_block",
    ),
    output_schema_hint=(
        '{"event":{"en":"...","zh":"..."},"first_order":{...},'
        '"asset_reaction":{...},"second_order":{...},"cross_market":{...},'
        '"confidence":0.0-1.0,"caveats":["..."]}'
    ),
    notes=(
        "context_block is a free-text block the caller fills in with current "
        "FRED levels (or empty string if unavailable). Default to cheap tier; "
        "the LLM client should route to strong tier when importance>=70."
    ),
)


# =============================================================================
# 4. synthesize.weekly  (strong)
# -----------------------------------------------------------------------------
# Saturday cross-track synthesis + handoff prompt for frontier-model deep dive.
# 周六综合 + 前沿模型深度分析 prompt 生成。
# =============================================================================

SYNTHESIZE_WEEKLY = PromptTemplate(
    name="synthesize.weekly",
    version="0.1.0",
    model_tier="strong",
    system="""You are a senior macro / cross-asset strategist composing the weekly brief for a buy-side researcher. Given the week's classified news items + current market levels, produce:

1. A "dominant_theme" — the single strongest signal of the week (one sentence each in English and Chinese).
2. Per-track summaries (macro / na_fx / deals): top 3-5 items each, with the implication for positioning or watchlists. Include the original item_id so the reader can drill in.
3. cross_track_linkages — explicit pairs where one track's event has implications in another (e.g., a macro CB pivot driving an na_fx move). Only include linkages that are causally meaningful, not coincidental.
4. handoff_prompt — a self-contained prompt the user can paste into a frontier reasoning model for a deeper analytical brief. It should:
     - Set role: "You are a senior macro / cross-asset strategist with buy-side experience..."
     - Embed the relevant week's structured context inline.
     - State the analytical task crisply.
     - Specify output structure.
     - End with "Begin your analysis."
   The handoff prompt may be English-only (frontier models handle CN, but EN is more deterministic).

Bilingual on dominant_theme, implication, and cross_track_linkages.reasoning. Headlines stay in their original language. Tickers and numbers as-is.

Output exactly one JSON object, no other text, no markdown fences:
{
  "week_ending": "YYYY-MM-DD",
  "dominant_theme": {"en": "string", "zh": "字符串"},
  "by_track": {
    "macro": [{"item_id": int, "headline": "string", "implication": {"en": "...", "zh": "..."}}],
    "na_fx": [...same shape...],
    "deals": [...same shape...]
  },
  "cross_track_linkages": [
    {"from_item_id": int, "to_item_id": int, "reasoning": {"en": "...", "zh": "..."}}
  ],
  "handoff_prompt": "string"
}""",
    user_template=(
        "Week ending (UTC): {week_ending}\n\n"
        "Classified items (JSON array — each has id, track, importance, headline, summary_en, summary_zh):\n"
        "{items_json}\n\n"
        "Current market levels (JSON — latest FRED observations):\n"
        "{market_levels_json}"
    ),
    expected_inputs=("week_ending", "items_json", "market_levels_json"),
    output_schema_hint=(
        '{"week_ending":"YYYY-MM-DD","dominant_theme":{...},"by_track":{...},'
        '"cross_track_linkages":[...],"handoff_prompt":"string"}'
    ),
    notes=(
        "Runs once a week (Saturday). Strong-tier model; cost is small "
        "since volume is ~107K tokens/month total (ADR-0010)."
    ),
)


# =============================================================================
# 5. trigger.deep_analysis  (strong)
# -----------------------------------------------------------------------------
# Fires on high-impact events: deal >= $5B, central-bank surprise,
# geopolitical flag, market move > 2%.
# =============================================================================

TRIGGER_DEEP_ANALYSIS = PromptTemplate(
    name="trigger.deep_analysis",
    version="0.1.0",
    model_tier="strong",
    system="""You are a senior strategist responding to a high-impact trigger event. Produce a deep analytical note. Each prose field must be bilingual (English + Simplified Chinese).

Required structure:

1. event_summary (factual, what happened, source attribution).
2. causal_chain — the SAME 5-step structure as causal_chain.five_step
   (event / first_order / asset_reaction / second_order / cross_market),
   but each step may be up to 5 sentences in each language. Depth over breadth.
3. counterfactual — what would the next 1-week price action look like ABSENT this event?
   Helps isolate the marginal impact of the trigger.
4. historical_comparables — 1-3 prior episodes that rhyme. For each: what happened,
   how the asset reaction unfolded, and how long the regime persisted. Be specific
   about dates and magnitudes.
5. tradeable_observations — 2-3 expressions that fit the chain. EXPLICITLY hedged:
   each one must end with "(research only, not advice)" / "(仅供研究,非投资建议)".
6. risks_to_thesis — what would invalidate the call. Concrete, observable signals.

Style: causal language; direction + magnitude; tail-risk prefix; no hedge fillers.
Numbers and tickers stay verbatim across languages.

Output exactly one JSON object, no other text, no markdown fences:
{
  "event_summary": {"en": "...", "zh": "..."},
  "causal_chain": {
    "event":          {"en": "...", "zh": "..."},
    "first_order":    {"en": "...", "zh": "..."},
    "asset_reaction": {"en": "...", "zh": "..."},
    "second_order":   {"en": "...", "zh": "..."},
    "cross_market":   {"en": "...", "zh": "..."}
  },
  "counterfactual": {"en": "...", "zh": "..."},
  "historical_comparables": [
    {"label": "string", "what_happened": {"en":"...","zh":"..."}, "asset_reaction": {"en":"...","zh":"..."}, "duration": "string"}
  ],
  "tradeable_observations": [{"en": "...", "zh": "..."}],
  "risks_to_thesis": [{"en": "...", "zh": "..."}],
  "confidence": 0.0-1.0
}""",
    user_template=(
        "Trigger kind: {trigger_kind}\n"
        "Item title: {title}\n"
        "Source: {source}\n"
        "Published: {pub_ts}\n"
        "Body: {body}\n"
        "Track: {track}\n"
        "Importance (0-100): {importance}\n\n"
        "Current market levels (JSON):\n{market_levels_json}\n\n"
        "Recent related items (JSON; may be empty):\n{recent_history_json}"
    ),
    expected_inputs=(
        "trigger_kind", "title", "source", "pub_ts", "body", "track",
        "importance", "market_levels_json", "recent_history_json",
    ),
    output_schema_hint=(
        '{"event_summary":{...},"causal_chain":{...},"counterfactual":{...},'
        '"historical_comparables":[...],"tradeable_observations":[...],'
        '"risks_to_thesis":[...],"confidence":0.0-1.0}'
    ),
    notes=(
        "Fires ~5x/month per ADR-0010 cost model. Strong tier; per-call cost "
        "is small even on Opus 4.7 (typical input+output ~7K tokens)."
    ),
)


# =============================================================================
# 6. meta.feed_prompt  (strong)
# -----------------------------------------------------------------------------
# Generates a self-contained prompt the user pastes into a frontier model
# (Opus 4.7 / GPT-5.4 / etc.) for ad-hoc deeper analysis.
# 生成可粘贴到前沿模型的 handoff prompt。
# =============================================================================

META_FEED_PROMPT = PromptTemplate(
    name="meta.feed_prompt",
    version="0.1.0",
    model_tier="strong",
    system="""You are a prompt designer for a senior buy-side strategist. Given (a) the past week's market context (key levels, themes, trigger events) and (b) an optional research question, produce a single self-contained prompt the user can paste into a frontier reasoning model to obtain a high-quality analytical brief.

The generated prompt MUST:
- Set role: a senior macro / cross-asset strategist with buy-side experience.
- Embed the week's structured context INLINE (the frontier model can't see external data).
- State the analytical task crisply (drawn from research_question if provided; otherwise a sensible default such as "produce next-week positioning views with rationale").
- Specify output structure: numbered sections, bullet density, ~800-1200 words total.
- End with the literal cue "Begin your analysis." on its own line.

The generated prompt may be English-only — frontier models handle Chinese fine, but English is more deterministic for reasoning chains. If the research_question is in Chinese, restate the task in both languages inside the generated prompt.

The output is ONE JSON object whose only field is the generated prompt as a single string — preserve the prompt's internal newlines in the JSON string (\\n).

Output exactly one JSON object, no other text, no markdown fences:
{
  "prompt_text": "string"
}""",
    user_template=(
        "Week summary (JSON output of synthesize.weekly):\n"
        "{week_summary_json}\n\n"
        "Research question (may be empty):\n"
        "{research_question}"
    ),
    expected_inputs=("week_summary_json", "research_question"),
    output_schema_hint='{"prompt_text":"string"}',
    notes=(
        "Output is meant for HUMAN consumption (Lindy pastes it elsewhere). "
        "Light usage — once a week max."
    ),
)


# =============================================================================
# Registry / 注册表
# =============================================================================

#: Public registry. Look up by name. Frozen.
PROMPTS: dict[str, PromptTemplate] = {
    p.name: p
    for p in (
        CLASSIFY_TRIAGE,
        SUMMARIZE_BILINGUAL,
        CAUSAL_CHAIN_FIVE_STEP,
        SYNTHESIZE_WEEKLY,
        TRIGGER_DEEP_ANALYSIS,
        META_FEED_PROMPT,
    )
}


def get(name: str) -> PromptTemplate:
    """Lookup helper that raises ``KeyError`` with a friendly message."""
    if name not in PROMPTS:
        available = ", ".join(sorted(PROMPTS.keys()))
        raise KeyError(
            f"unknown prompt '{name}'. Available: {available}"
        )
    return PROMPTS[name]


__all__ = [
    "ModelTier",
    "PromptTemplate",
    "PROMPTS",
    "get",
    "CLASSIFY_TRIAGE",
    "SUMMARIZE_BILINGUAL",
    "CAUSAL_CHAIN_FIVE_STEP",
    "SYNTHESIZE_WEEKLY",
    "TRIGGER_DEEP_ANALYSIS",
    "META_FEED_PROMPT",
]
