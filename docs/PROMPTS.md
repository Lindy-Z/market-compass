# LLM Prompt Templates / LLM 提示词模板

> **Versioned, human-readable mirror of `src/reasoning/prompts.py`.**
> Code is the runtime truth; this file is the review artifact. They MUST
> stay in sync — `tests/test_prompts.py` enforces that every prompt name
> and version in the registry appears here.
>
> **`src/reasoning/prompts.py` 是运行时真理,本文件是评审产物。**
> 二者必须保持一致,`tests/test_prompts.py` 自动校验每个 prompt 的名称与版本号。

---

## Versioning policy / 版本策略

Each prompt has a semver-ish `MAJOR.MINOR.PATCH`:
- **PATCH** — whitespace / comment-only edits.
- **MINOR** — wording changes that affect behavior but NOT the output schema.
- **MAJOR** — output schema changes (downstream parsers must adapt).

Any non-PATCH bump goes with a `docs/CHANGELOG.md` entry. Schema-changing
bumps (MAJOR) deserve an ADR.

每次非 PATCH 升版需在 CHANGELOG 留痕,涉及输出 schema 的 MAJOR 升版需 ADR。

---

## Inventory / 清单

| Name | Version | Tier | Purpose |
|---|---|---|---|
| `classify.triage` | 0.2.0 | cheap | Track + importance + deal-size in one call |
| `summarize.bilingual` | 0.1.0 | cheap | EN ~100w + ZH ~100字 + key_numbers |
| `causal_chain.five_step` | 0.1.0 | cheap (strong if importance≥70) | Bilingual 5-step causal chain |
| `synthesize.weekly` | 0.1.0 | strong | Saturday cross-track synthesis + handoff prompt |
| `trigger.deep_analysis` | 0.1.0 | strong | Deep dive on high-impact triggers |
| `meta.feed_prompt` | 0.1.0 | strong | Generates a paste-ready prompt for frontier models |

---

## 1. `classify.triage` (v0.2.0, cheap)

**Purpose**: Single cheap-tier call that classifies an item to a track,
scores its importance 0-100, and extracts deal size when applicable. Runs
on EVERY ingested item — keep tight.

**单次廉价模型调用**完成信息轨分类、重要性打分、并购金额提取。每条入库 item 都会跑,
所以系统消息要紧凑。

### What changed in v0.2.0 / v0.2.0 变更

After v0.1.0 was used to classify ~385 real items, two systematic biases
appeared:

1. **`na_fx` was under-fired** — FX-print headlines (`"USD/JPY drops from
   160 to 155 on BoJ intervention"`) went to `macro` because the model
   weighted the cause (intervention) over the print (the FX move).
2. **Importance anchored at 75** — 14 of 15 top items clustered at exactly
   75; nothing crossed 80, nothing reached 90+.

Fix: expanded tie-breaker examples to include FX-print, yield-move, and
commodity-print cases; restructured importance band so the model reads
"USE THE FULL 0-100 RANGE" up front, with concrete examples per band.

v0.1.0 之后的真实运行 (385 条) 暴露两点系统偏差: na_fx 严重过少、
重要性集中在 75。v0.2.0 加了 FX 价位 / 收益率移动 / 商品价位的 tie-breaker
样例,并把"用满 0-100 区间"显式放在最前。

### Inputs

```
title:        string
source:       string  (source_id, e.g. "reuters", "fed-press-all")
body_excerpt: string  (~500 chars; ingestion truncates)
```

### Output schema

```json
{
  "track": "macro|na_fx|deals|other",
  "track_confidence": 0.0,
  "importance": 0,
  "deal_size_usd_billions": null,
  "reason": "short string"
}
```

### Track definitions (in the system message)

- `macro` — central bank actions, monetary/fiscal policy, geopolitics, supply shocks, cross-border flows
- `na_fx` — North American equity markets, US Treasury yields, USD-pair FX moves, market-direction signals
- `deals` — M&A, takeovers, IPOs, major corporate restructuring (≥$1B implied or stated)
- `other` — earnings without market move, sports, weather, etc.

### Importance bands (v0.2.0 — finer-grained calibration)

| Range | Tier | Examples |
|---|---|---|
| 90-100 | once-a-quarter event | deal ≥ $5B; CB surprise hike/cut/intervention; war onset; market move > 5%; named regulatory regime change |
| 80-89 | highly significant | deal $1-5B with strategic angle; scheduled CB statement with material guidance change; market move 2-5%; major FX intervention; serious geopolitical escalation |
| 70-79 | lead-item-worthy | deal $1B+ standard M&A; scheduled CB statement (no surprise); meaningful geopolitical headline |
| 50-69 | solid background context | supports a trend story |
| 30-49 | brief mention only | |
| 0-29 | archive only | not in the daily brief |

> **The system message explicitly tells the model: "USE THE FULL 0-100 RANGE — DO NOT ANCHOR AT 75."** This was added after v0.1.0 produced a 14-item cluster at exactly 75.

### Tie-breakers (system-message snippets, v0.2.0)

> **The PRINT in the headline wins over the cause.**

- "Fed cuts rates" → `macro` (policy is the headline)
- "S&P 500 falls 2% on Fed cut" → `na_fx` (price move is the headline)
- "USD/JPY drops from 160 to 155 on BoJ intervention" → `na_fx` (FX print is the headline)
- "Treasury 10Y yield jumps 12bp on hot CPI" → `na_fx` (yield move is the headline)
- "BoJ FX intervention pushes yen 5 figures stronger" → `na_fx` (the move is the lede)
- "BoJ raises policy rate by 25bp" → `macro` (policy is the headline)
- "Oil up 5% as Iran tensions escalate" → `na_fx` (commodity print leads); `macro` if framing is geopolitics-first
- "Microsoft to acquire X for $10B" → `deals` (importance 90+ since ≥$5B)
- "8-K: Departure of director / change in bylaws" → `other` (procedural filing)
- Earnings beat/miss without broader implications → `other`

---

## 2. `summarize.bilingual` (v0.1.0, cheap)

**Purpose**: Two summaries (English + Simplified Chinese) plus extracted
key numerical facts. Output goes into `items.summary_en` / `items.summary_zh`;
`key_numbers` feeds the trigger detector.

输出双语摘要并抽取数字事实,直接写入 `items` 表;`key_numbers` 给触发器使用。

### Inputs

```
title:   string
source:  string
pub_ts:  ISO-8601 UTC string
body:    string  (full body; longer feeds may be truncated by the caller)
```

### Output schema

```json
{
  "summary_en": "80-120 words",
  "summary_zh": "80-120 汉字, idiomatic prose, NOT a literal translation",
  "key_numbers": ["$12.4B deal value", "10Y UST +8bp to 4.21%"]
}
```

### Constraints (system message)

- 80-120 words EN / 80-120 汉字 ZH; each must read naturally in its own language (not a literal translation)
- Preserve EXACTLY: numbers, percentages, basis points, currency amounts, dates, names, tickers
- Do NOT round numerical values
- No speculation or opinion

---

## 3. `causal_chain.five_step` (v0.1.0, cheap default)

**Purpose**: Apply the project's signature 5-step causal-chain template to
one item. Default tier is cheap (Haiku 4.5); the LLM client routes to
strong (Opus 4.7) when `importance >= 70`. See ADR-0010.

项目核心模板: 五步因果链。默认便宜档,重要性 ≥70 时升档到强模型。

### Inputs

```
title:          string
source:         string
pub_ts:         ISO-8601 UTC string
body:           string
track:          "macro|na_fx|deals|other"
importance:     int 0-100
context_block:  free-text (current FRED levels, recent CB stance, ...) or "" if unavailable
```

### Output schema

```json
{
  "event":          {"en": "string", "zh": "字符串"},
  "first_order":    {"en": "string", "zh": "字符串"},
  "asset_reaction": {"en": "string", "zh": "字符串"},
  "second_order":   {"en": "string", "zh": "字符串"},
  "cross_market":   {"en": "string", "zh": "字符串"},
  "confidence":     0.0,
  "caveats":        ["string", "..."]
}
```

### The five steps / 五步

| Step | EN | ZH | What it answers |
|---|---|---|---|
| 1 | event | 事件 | What happened, factual, one sentence |
| 2 | first_order | 一阶机制 | Direct propagation channel: rates / flows / earnings / policy / supply |
| 3 | asset_reaction | 资产反应 | Which assets moved or should move; direction + rough magnitude |
| 4 | second_order | 二阶效应 | Downstream implications: who else is affected, what positioning shifts follow |
| 5 | cross_market | 跨市场传导 | How it propagates across asset classes / geographies; tail-risk note when relevant |

### Style rules

- Each step ≤3 sentences in EACH language
- Causal language ("X causes Y because Z"), not correlational
- State direction AND rough magnitude when possible: "10Y UST yields up ~8bp" beats "yields rose"
- Tail risks must be prefixed `tail-risk:` / `尾部风险:` so they're greppable
- No hedge fillers ("perhaps", "could potentially", "或许") — confidence is a separate field

---

## 4. `synthesize.weekly` (v0.1.0, strong)

**Purpose**: Saturday rollup. Identifies the week's dominant theme,
surfaces top items per track, names cross-track linkages, and **generates
a paste-ready handoff prompt** for deeper frontier-model analysis.

每周六综合: 找出主线、按轨整理、跨轨关联,并生成可粘贴到前沿模型做深度分析的 prompt。

### Inputs

```
week_ending:        YYYY-MM-DD (UTC)
items_json:         JSON array of classified items (id, track, importance, headline, summary_en, summary_zh)
market_levels_json: JSON of latest FRED observations
```

### Output schema

```json
{
  "week_ending": "YYYY-MM-DD",
  "dominant_theme": {"en": "string", "zh": "字符串"},
  "by_track": {
    "macro": [{"item_id": 0, "headline": "string", "implication": {"en": "...", "zh": "..."}}],
    "na_fx": [],
    "deals": []
  },
  "cross_track_linkages": [
    {"from_item_id": 0, "to_item_id": 0, "reasoning": {"en": "...", "zh": "..."}}
  ],
  "handoff_prompt": "self-contained string for frontier model"
}
```

### Notes

- Only include cross-track linkages that are causally meaningful, not coincidental.
- Headlines stay in their original language; only `implication` and `reasoning` are bilingual.
- The `handoff_prompt` may be English-only; frontier models handle CN, but EN is more deterministic for reasoning.

---

## 5. `trigger.deep_analysis` (v0.1.0, strong)

**Purpose**: Fires on high-impact events (deal ≥ $5B, central-bank surprise,
geopolitical flag, market move > 2%). Produces a deep analytical note with
counterfactual, historical comparables, and EXPLICITLY-hedged tradeable
observations.

触发深度分析: 反事实、历史可比、以及明确加注"研究非建议"的可执行观察。

### Inputs

```
trigger_kind:          "deal_size" | "cb_surprise" | "geo" | "market_move"
title, source, pub_ts: per item
body, track:           per item
importance:            int 0-100
market_levels_json:    JSON of latest FRED observations
recent_history_json:   JSON of recent related items (may be empty array)
```

### Output schema

```json
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
    {"label": "string", "what_happened": {"en": "...", "zh": "..."}, "asset_reaction": {"en": "...", "zh": "..."}, "duration": "string"}
  ],
  "tradeable_observations": [{"en": "...", "zh": "..."}],
  "risks_to_thesis": [{"en": "...", "zh": "..."}],
  "confidence": 0.0
}
```

### Hard rule

Every entry in `tradeable_observations` MUST end with `(research only, not advice)` /
`(仅供研究,非投资建议)`. The system message enforces this and the test suite verifies
the disclaimer string appears in the prompt.

每条 `tradeable_observations` 必须以 "research only, not advice" / "仅供研究,非投资建议" 收尾。

---

## 6. `meta.feed_prompt` (v0.1.0, strong)

**Purpose**: Generates a self-contained prompt that Lindy can paste into a
frontier reasoning model (Opus 4.7, GPT-5.4 Pro, etc.) for an ad-hoc
deeper analytical brief. Output is meant for HUMAN consumption, not
downstream automation.

生成可粘贴到前沿模型做即兴深度分析的 prompt;面向人类使用,不进入自动化下游。

### Inputs

```
week_summary_json: JSON output of synthesize.weekly
research_question: free-text (may be empty; default task = "next-week positioning views with rationale")
```

### Output schema

```json
{
  "prompt_text": "string with internal newlines"
}
```

### Required structure of the generated prompt

The output `prompt_text` must:

1. Set role — senior macro / cross-asset strategist with buy-side experience.
2. Embed the week's structured context **inline** (frontier model can't see external data).
3. State the analytical task crisply — drawn from `research_question`, or a sensible default.
4. Specify output structure — numbered sections, bullet density, ~800-1200 words.
5. End with the literal cue `Begin your analysis.` on its own line.

If `research_question` is in Chinese, the generated prompt restates the task in both languages.

---

## Sync rule / 同步规则

- Add a new prompt → register in `src/reasoning/prompts.py` AND add a section here AND bump the inventory table.
- Bump a version → update both files; add a CHANGELOG line.
- Tests in `tests/test_prompts.py` will fail if you forget either side.

新增/升版 prompt 必须同时改 `prompts.py` 与本文件,测试会拦截不一致。

---

_Last updated: 2026-04-23_
