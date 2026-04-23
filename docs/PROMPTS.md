# LLM Prompt Templates / LLM 提示词模板

> Versioned, plain-text home for every prompt the system sends to an LLM.
> The in-code copy under `src/reasoning/prompts.py` must be kept in sync
> with this file — the code path is the runtime truth, but **this file is
> the review artifact**. Changes to prompts land with a bumped version
> (`v0.1.0 → v0.2.0`) and an entry in `docs/DECISIONS.md` if the change
> alters behavior in a non-trivial way.
>
> 每个提示词的带版本、纯文本归档。代码中的 `src/reasoning/prompts.py` 必须与
> 本文件保持一致 — 代码为运行时真理,本文件为评审产物。非平凡改动要升版本并
> 同步更新 `docs/DECISIONS.md`。

---

## Inventory / 清单

| ID | Version | Purpose | Model tier |
|----|---------|---------|------------|
| `classify.track` | draft | Route item to track (macro / na_fx / deals / other) | cheap |
| `score.importance` | draft | 0–100 importance score | cheap |
| `summarize.bilingual` | draft | ~100 EN / ~100 CN summary | cheap |
| `causal_chain.five_step` | draft | 5-step causal chain | strong (single items) / cheap (batch) |
| `synthesize.weekly` | draft | Saturday cross-track synthesis | strong |
| `trigger.deep_analysis` | draft | Event-triggered deep dive | strong |
| `meta.feed_prompt` | draft | Ready-to-feed prompt for user's frontier-model analysis | strong |

All prompts are drafts until Phase 2 authors them in code. This file
reserves the structure and naming convention now so early decisions are
easy to find later.

---

## `classify.track` (draft) / 分类:信息轨 (草案)

**Purpose**: Given a news item's title + first paragraph, return one of
`macro | na_fx | deals | other`.

**Inputs**: `title`, `body_first_paragraph`, `source`, `published_ts`.

**Output shape**: JSON `{"track": "...", "confidence": 0.0-1.0, "reason_short": "..."}`.

Template TBD — will be authored in Phase 2.

---

## `summarize.bilingual` (draft) / 双语摘要 (草案)

**Purpose**: Produce ~100-word English summary AND ~100-character Chinese
summary, preserving named entities and numerical facts exactly.

**Output shape**:

```json
{
  "summary_en": "...",
  "summary_zh": "...",
  "key_numbers": ["$12.4B deal", "10Y UST +8bp"]
}
```

Template TBD.

---

## `causal_chain.five_step` (draft) / 因果链:五步模板 (草案)

**Purpose**: Apply the 5-step causal reasoning template to a qualifying
item.

**Output shape**:

```json
{
  "event":              "...",   // 事件
  "first_order":        "...",   // 一阶机制
  "asset_reaction":     "...",   // 资产反应
  "second_order":       "...",   // 二阶效应
  "cross_market":       "...",   // 跨市场传导
  "confidence":         0.0-1.0,
  "caveats":            ["..."]
}
```

**Style guide**:
- State direction AND rough magnitude when possible; say "unknown" if
  truly unobservable.
- Prefer causal language over correlational.
- Flag tail risks explicitly — e.g. "if the central bank extends, add
  yen carry unwind risk."
- Keep each step concise (≤ 3 sentences).

Template TBD.

---

## `synthesize.weekly` (draft) / 周度综合 (草案)

**Purpose**: On Saturday, ingest the week's items (all tracks), output a
cross-track synthesis + a ready-to-feed prompt the owner can paste into a
frontier model for deeper analysis.

**Output shape**:

```json
{
  "week_ending":     "YYYY-MM-DD",
  "dominant_theme":  "...",
  "by_track": {
    "macro":  [{ "headline": "...", "implication": "..." }],
    "na_fx":  [...],
    "deals":  [...]
  },
  "cross_track_linkages": [
    { "from": "macro.item_id", "to": "na_fx.item_id", "reasoning": "..." }
  ],
  "handoff_prompt":  "You are a senior macro strategist. Given the following weekly brief, ..."
}
```

Template TBD.

---

## `trigger.deep_analysis` (draft) / 事件触发深度分析 (草案)

**Purpose**: Fire on qualifying events (deal ≥ $5B, CB surprise,
geopolitical flag, market > 2%). Produce an in-depth single-item analysis
using the strong model.

Template TBD.

---

_Last updated: 2026-04-23_
