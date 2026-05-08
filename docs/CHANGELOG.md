# Changelog / 变更日志

All notable changes to this project will be documented in this file.
本文件记录所有值得留痕的变更。

Format adheres to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
version numbers follow [Semantic Versioning](https://semver.org/).
格式遵循 Keep a Changelog, 版本号遵循 SemVer。

## [Unreleased]

### Added / 新增
- Phase 3 `3.4` — causal-chain runner (the project's signature output):
  - `src/processing/causal_chain.py` — `chain_one(client, item, *, importance_strong_threshold=70)` runs the `causal_chain.five_step` prompt with **automatic tier routing**: cheap (Haiku 4.5) by default, **strong (Opus 4.7) when `importance >= 70`** per ADR-0010. Validation is structural: all 5 steps (`event` / `first_order` / `asset_reaction` / `second_order` / `cross_market`) × `{en, zh}` non-empty + zh contains CJK; `confidence` is float in `[0, 1]`; `caveats` is list-of-strings. `apply_chain(conn, result)` writes the full payload to `items.causal_chain` JSON, sets `items.processed_ts` to current UTC (item is now "fully processed" — ready for daily brief), and merges `chain_confidence` + `chain_caveats` into `items.meta` preserving all earlier ingestion + triage + summary keys. `run_pending_causal_chain()` selects `WHERE causal_chain IS NULL AND track IS NOT NULL AND track != 'other' AND summary_en IS NOT NULL` (chain depends on the prior summary) ordered `importance DESC, pub_ts ASC` (high-importance first so the brief lead items are ready even if the run is interrupted).
  - `scripts/run_causal_chain.py` — CLI with `--limit`, `--dry-run`, `--reset`, `--include-other`, `--min-importance`, `--strong-threshold` (default 70), `--context-block` (free text for current-market context, eventually FRED levels), `--verbose` (per-item: tier used, confidence, cost, ZH event preview).
  - `tests/test_causal_chain.py` — **40 tests** via `FakeAnthropicClient`-backed real `LLMClient`: tier resolution (low/at/above-threshold/None importance/custom threshold), step validation (well-formed / non-dict / missing en / missing zh / no-CJK in zh), payload validation (good chain / missing step / out-of-range confidence / non-numeric confidence / non-list caveats / non-string caveat / REQUIRED_STEPS matches prompt template), chain_one (cheap-tier routing / strong-tier routing / threshold boundary / custom threshold / body truncation / context_block plumbing / unparseable / invalid-structure / cost-on-failure), apply_chain (writes column / sets processed_ts / merges into meta preserving prior keys / no-caveats-skipped / no-op on failure), run_pending (requires summary / excludes other / skips already-chained / processes high-importance first / dry-run / by_tier tally / per-item failure isolation / budget propagation / min_importance / progress callback).
  - Cost projection on current corpus (~180 chain-eligible items, mostly cheap-tier): ~$0.0038/item × 170 cheap + ~$0.019/item × 10 strong ≈ ~$0.85 per full pass.
  Phase 3.4 因果链执行器完成,40 个测试通过 (累计 314/314)。这是项目的招牌输出 — 五步双语因果链。
- Phase 3 `3.3` — bilingual summary runner:
  - `src/processing/summary.py` — `summarize_one(client, item, *, body_input_chars=3000)` runs the `summarize.bilingual` prompt and validates output: `summary_en` non-empty string, `summary_zh` non-empty string with **at least one CJK character** (catches the failure mode where the model returns English in both fields), `key_numbers` list-of-strings or null. `apply_summary(conn, result)` writes `summary_en` / `summary_zh` to dedicated columns and merges `key_numbers` into `items.meta` JSON, **preserving keys set by ingestion (feed_url, finnhub_id, etc.) and by triage (deal_size_usd_billions, triage_reason, track_confidence)**. Does NOT touch `items.processed_ts` — that's reserved for the causal_chain runner (Phase 3.4) since reasoning isn't "complete" until the chain is generated.
  - `run_pending_summary()` — selects `WHERE summary_en IS NULL AND track IS NOT NULL`, by default excludes `track='other'` (archive-only, avg importance ~15, never in brief). `--include-other` overrides. `min_importance` parameter for further filtering. Oldest-pub_ts-first to drain backlog deterministically (mirrors triage). Per-item failure isolation; propagates `BudgetExceededError`; `dry_run=True` skips both LLM call and DB write.
  - `scripts/run_summary.py` — CLI with `--limit`, `--dry-run`, `--reset` (clears summary fields), `--include-other`, `--min-importance`, `--verbose` (per-item EN/ZH preview), `--soft-cap-usd`, `--hard-cap-usd`. Tick-mark progress by default; verbose shows the first 60 chars of each generated summary.
  - `tests/test_summary.py` — **36 tests** via `FakeAnthropicClient`-backed real `LLMClient`: CJK detection, validation (non-dict / missing fields / empty fields / EN-only in zh-field / non-list key_numbers / non-string elements / well-formed / null key_numbers / empty list), happy path with key_numbers, body truncation, unparseable response, English-only-in-zh-field rejection, whitespace stripping, cost-recorded-on-failure, apply_summary writes columns + merges key_numbers + preserves ingestion+triage keys + handles null/corrupt meta + no-op on failure + leaves processed_ts NULL, run_pending only-classified + excludes-other-default + include-other-flag + min_importance + skips-already-summarized + oldest-first + limit + dry-run + by-track tally + per-item failure isolation + budget propagation + empty queue + progress callback.
  - Cost projection on current corpus (~188 classified non-other items): ~$0.0009/item × 188 = ~$0.17 per full pass.
  Phase 3.3 双语摘要执行器完成,36 个测试通过 (累计 274/274)。验证逻辑包括 summary_zh 必须包含 CJK 字符 (拦下模型在两字段都返回英文的失败模式)。

### Changed / 变更
- **`classify.triage` prompt v0.1.0 → v0.2.0** based on first 385-item run analysis. Two systematic biases observed in v0.1.0:
    1. **`na_fx` was under-fired** (only 8 / 385 items). FX-print headlines like "Japan launches FX intervention, briefly pushing yen to 155 from 160" were sent to `macro` because the model weighted the cause over the print.
    2. **Importance anchored at 75** — 14 of 15 top items clustered exactly at 75; nothing crossed 80, nothing reached 90+ (which would have been trigger-eligible).
  v0.2.0 fixes:
    - **Tie-breakers expanded** with FX-print, yield-move, BoJ-intervention examples; new framing rule "THE PRINT IN THE HEADLINE WINS over the cause".
    - **Importance recalibrated** — band order reversed (90-100 listed first), full-range anchor "USE THE FULL 0-100 RANGE — DO NOT ANCHOR AT 75" placed up-front, concrete examples per band (deal $1-5B → 80-89, scheduled CB → 70-79, etc.).
    - System message length: 2989 chars (was ~1700). Still well under any token concern.
  Mirrored in `docs/PROMPTS.md` with a "What changed in v0.2.0" section and updated tie-breaker / importance-band tables.
  `tests/test_prompts.py` — 2 new content tests verify the FX tie-breakers and the calibration anchor are present.
  `scripts/run_triage.py` — added `--reset` flag that clears `track` + `importance` for already-triaged items, enabling prompt-version A/B re-triages.
  v0.1.0 真实数据反馈后升级到 v0.2.0:加了 FX 价位/收益率移动 tie-breaker,把"用满 0-100 区间"放最前,每档加具体例子。`run_triage.py --reset` 用于重跑同一批 item 做 A/B。
- **Dropped `GOLDAMGBD228NLBM` from `FRED_SERIES`** — FRED returns HTTP 400 since LBMA Gold PM fixing licensing changed. No clean FRED replacement; gold direction now comes from news mentions / Finnhub. Documented in `fred.py` for future re-add.

### Added / 新增
- Phase 2 `2.14` + `2.15` — `.env` auto-loader + end-to-end ingest runner:
  - `src/util/env.py` — minimal `load_dotenv(path, *, override=False, quiet=True)` (~70 lines, no extra deps). Handles `KEY=value`, double/single-quoted values (`SEC_EDGAR_USER_AGENT="market-compass/0.1 (contact: x@y.com)"` works without escaping), unquoted values with shell-special chars (parens), `export KEY=` prefix, comment lines, blank lines. Returns dict of vars actually set; respects existing `os.environ` unless `override=True`. Missing file is silent.
  - `tests/test_env.py` — 15 tests: simple assignment, quoted (single + double), unquoted-with-parens (the realistic SEC_EDGAR_USER_AGENT case), `export` prefix, whitespace tolerance, comment + blank lines, multiple vars, override semantics (default no, opt-in yes), missing-file silent vs. warn, malformed-line skip, realistic `.env` shape end-to-end.
  - `scripts/run_ingest.py` (new) — **the missing piece between fetch and triage**. Calls `rss.fetch_one` / `edgar.fetch_all` / `finnhub.fetch_all` / `fred.fetch_all`, runs items through `processing.dedup.filter_new`, INSERTs into `items` (FRED writes to `observations`). Per-source skip with friendly message when env keys absent; never crashes on missing keys. CLI flags: `--no-{rss,edgar,finnhub,fred}`, `--db-path`, `--verbose`. Final summary block with item / triaged / observation counts.
  - `scripts/{smoke_test_sources,run_triage}.py` — patched to `load_dotenv()` at startup. Resolves the user-reported "FRED_API_KEY: NOT SET despite filling .env" surprise.
  端到端抓取入库 + .env 自动加载;15 个测试通过, 累计 236/236。

- Phase 3 `3.2` — classifier + triage runner:
  - `src/processing/triage.py` — `triage_one(client, item, *, body_excerpt_chars=500)` runs the `classify.triage` prompt and validates output (track in enum, importance int 0-100, deal_size numeric or null). `apply_triage(conn, result)` writes `track` / `importance` to dedicated columns and merges `deal_size_usd_billions` / `triage_reason` / `track_confidence` into `items.meta` JSON, **preserving ingestion-set keys** (`feed_url`, `finnhub_id`, etc.). `run_pending_triage()` selects `WHERE track IS NULL` ORDER BY `pub_ts ASC` (oldest first to drain backlog deterministically), supports `limit=N` and `dry_run=True` (no LLM call, no DB write), isolates per-item failures so one bad LLM response doesn't kill the run, propagates `BudgetExceededError` cleanly.
  - `scripts/run_triage.py` — CLI wrapper. Flags: `--limit N`, `--dry-run`, `--db-path`, `--verbose`, `--soft-cap-usd`, `--hard-cap-usd`. Tick-mark progress by default; `-v` prints per-item track + importance + cost. Final summary block with by-track tally + cost-meter status.
  - `tests/test_triage.py` — 24 tests via `FakeAnthropicClient`-backed real `LLMClient`: happy path with deal extraction, body truncation, unparseable response handling, invalid track / out-of-range importance / non-int importance / array payload all rejected, cost recorded even on failure, apply_triage merges into existing meta + handles null/corrupt meta + skips None fields + no-op on failure, run_pending only processes untracked + oldest-first + honors limit + dry-run skips both LLM and DB + tallies by_track + isolates per-item failures + propagates budget exceeded + empty queue + progress callback.
  Phase 3.2 分流执行器完成,24 个测试通过 (累计 221/221)。
- **Auto-commit helper**: `scripts/commit-pending.sh` regenerated by the assistant each turn, gitignored. Single command (`bash scripts/commit-pending.sh`) replaces the multi-line `git add` + commit message paste pattern. Documented in `docs/WORKFLOW.md` under "Auto-commit helper". Constraint disclosed: the assistant can't `git commit` from its sandbox due to FUSE unlink restrictions, so a Mac-side helper is the closest practical thing to "automatic".
  自动提交助手, 每次 turn 重写, gitignored;沙箱写不了 git 故走 Mac 端的单命令快捷。
- Phase 3 `3.1` — LLM client (Anthropic SDK wrapper + cost tracking):
  - `src/reasoning/llm_client.py` — `LLMClient.call(prompt, tier_override=None, **prompt_kwargs)` routes per `prompt.model_tier` (cheap → Haiku 4.5, strong → Opus 4.7) with optional override. Sends `system` (with `cache_control: ephemeral` when `enable_caching=True`) + single user message; returns `LLMResponse` carrying `raw_text`, `parsed` (JSON-extracted), `parse_error`, model/tier, token counts (input/output/cache_read/cache_creation), `cost_usd`, `duration_seconds`, `prompt_name`/`prompt_version`. `LLMClient.from_env()` reads `ANTHROPIC_API_KEY` + `LLM_CHEAP_MODEL` + `LLM_STRONG_MODEL`.
  - `MODEL_PRICES` table for Haiku 4.5 ($1/$5), Haiku 3.5 ($0.80/$4), Sonnet 4.6 ($3/$15), Opus 4.7 ($5/$25), Opus 4.6 ($5/$25); `compute_cost()` helper. Unknown models log a warning and return cost=0 (fail-soft on new model names).
  - `CostMeter` — soft ($15/mo) / hard ($20/mo) caps. `status()` → `ok | warning | soft_breach | hard_breach`. `BudgetExceededError` raised BEFORE the call when meter is already at hard_breach (no SDK call attempted).
  - `extract_json()` — robust JSON extraction with 4 heuristics: whole-string parse → fenced code block → outermost `{...}` → outermost `[...]`. Returns `(parsed, error)` tuple.
  - `requirements.txt` — adds `anthropic>=0.40,<1.0`.
  - `tests/test_llm_client.py` — 43 tests using `FakeAnthropicClient` (tests pass without `anthropic` installed): cost math (known/unknown models, with/without cache), CostMeter status transitions + remaining helpers + negative-cost rejection, JSON extraction (raw / fenced / unfenced prose / Chinese / empty / no-JSON / malformed), construction guards (empty key) + injection, `from_env` (reads / falls back / raises), tier routing (cheap prompt → cheap model, strong prompt → strong model, override promotes cheap → strong), `format_user` failure short-circuits with NO SDK call, system message format with/without caching, user message uses `format_user`, token-count + cost + cache-token recording, missing-`usage` fallback, JSON parsing of clean / fenced / unparseable responses, hard-cap refusal, soft-cap pass-through.
  Phase 3.1 LLM 客户端完成,43 个测试通过,共 197/197 全绿。
- Phase 3 `3.0` — versioned LLM prompt templates:
  - `src/reasoning/prompts.py` — `PromptTemplate` dataclass (frozen) with `name`, `version` (semver), `model_tier` (`cheap` | `strong`), `system`, `user_template`, `expected_inputs` tuple, `output_schema_hint`, `notes`. `format_user(**kwargs)` validates inputs both ways (missing AND unknown args caught). `PROMPTS` registry dict + `get(name)` helper.
  - **6 prompts authored** (all v0.1.0):
      1. `classify.triage` (cheap) — track + importance + `deal_size_usd_billions` + reason in one call (saves ~50% cost vs. separate classify + score calls).
      2. `summarize.bilingual` (cheap) — 80-120 EN words / 80-120 汉字 + `key_numbers` array.
      3. `causal_chain.five_step` (cheap default; LLM client routes to strong when importance≥70) — bilingual 5-step template (`event/first_order/asset_reaction/second_order/cross_market` × {en,zh}) + `confidence` + `caveats`. Style rules: causal language, direction+magnitude, `tail-risk:` prefix, no hedge fillers.
      4. `synthesize.weekly` (strong) — `dominant_theme` + `by_track` (top items per macro/na_fx/deals with bilingual implications) + `cross_track_linkages` (causal pairs) + paste-ready `handoff_prompt` for frontier-model deep analysis.
      5. `trigger.deep_analysis` (strong) — fires on deal≥$5B / CB surprise / geo flag / market move >2%. Counterfactual + 1-3 historical comparables + 2-3 hedged tradeable_observations + risks_to_thesis. Hard rule (system-message-encoded, test-verified): every tradeable_observation ends with `(research only, not advice)` / `(仅供研究,非投资建议)`.
      6. `meta.feed_prompt` (strong) — generates a self-contained prompt the user pastes into a frontier model. Embeds context inline; ends with literal cue `Begin your analysis.`
  - `docs/PROMPTS.md` — full bilingual mirror with inventory table, per-prompt I/O schemas, style rules, and a sync rule.
  - `tests/test_prompts.py` — 22 tests: registry coverage, semver shape, model-tier values, JSON-shaped output schemas, cheap-tier covers per-item paths, placeholder ↔ expected_inputs match (static), `format_user` happy/missing/unknown paths, `get()` helper, content checks (track values, 5 step labels in CN+EN, word counts, research disclaimer presence, `Begin your analysis.` cue), AND **doc-code sync** (every prompt name + version in `prompts.py` must appear in `PROMPTS.md`).
  Phase 3.0 提示词模板完成,22 个测试通过,代码与文档强制保持一致。
- Phase 2 `2.5` — FRED time-series ingestion + schema v3:
  - **Schema bumped 2 → 3** (additive, ADR-0011): added `observations` table with `(series_id, obs_date)` PK so re-fetches are idempotent and FRED's occasional historical revisions cleanly upsert. Indexed on `obs_date` and `series_id`. Numerical data lives here, not in `items` — see schema comments and `fred.py` docstring for rationale.
  - `src/ingestion/fred.py` — `SeriesConfig` dataclass + `FRED_SERIES` curated list (15 series: UST yield curve 3M-30Y, USD/EUR/JPY/CNY, broad-basket USD index, SPX, VIX, gold London PM fixing, CPI, UNRATE, DFF). `fetch_series()` upserts via `INSERT ... ON CONFLICT DO UPDATE`. `get_latest_observation()` for reasoning-layer queries. `_parse_value()` correctly maps FRED's `"."` missing-value sentinel to SQL NULL. `fetch_all_from_env()` reads `FRED_API_KEY`; empty key short-circuits to a no-HTTP-call 401 outcome.
  - `tests/test_fred.py` — 27 tests: value parsing (incl. "." sentinel, empty, garbage), URL building, observations write, upsert idempotency, revision replacement, `get_latest_observation`, empty-key short-circuit, API-key-in-URL contract (FRED has no header option — documented), 400/500 / malformed JSON / missing observations array / timeout / connection / non-dict payload / dateless skip / non-dict array elements, `fetch_all` continuation past failure, `fetch_all_from_env` env-key handling, catalog well-formedness + brief-required-signals coverage.
  Phase 2.5 FRED 完成 + 数据库 schema 升级到 v3 (观测表),27 个测试通过。
- Phase 2 `2.6` — SEC EDGAR Atom ingestion:
  - `src/ingestion/edgar.py` — wraps `rss.fetch_one` with SEC-required User-Agent. Reads `SEC_EDGAR_USER_AGENT` from env via `_user_agent_from_env()`; **refuses with `RuntimeError`** if missing (SEC explicitly requires identification — silently sending a generic UA gets you throttled). 4 curated form-type feeds: 8-K, SC 13D, 425, DEFM14A. Items flow through the same dedup pipeline as RSS.
  - `tests/test_edgar.py` — 10 tests: refuses-without-env-UA (twice: missing + whitespace-only), explicit `user_agent=` kwarg overrides env check, `fetch_all` propagates env UA to every request, `fetch_all_from_env` refuses without UA, dedup-compatible item output, filer name preserved in title, catalog well-formedness, required-form-type coverage (8-K + SC 13D), non-200 propagation through RSS layer.
  Phase 2.6 EDGAR 完成,10 个测试通过;UA 校验是 SEC 强制要求,缺则拒发请求。
- `scripts/smoke_test_sources.py` — end-to-end real-network smoke test for every ingestion source. Skips API-key-gated sources cleanly. Sandbox run on 2026-04-23 confirmed: **9/9 RSS feeds live, 4/4 EDGAR feeds live**, ~1,400 items pulled. Finnhub + FRED skipped pending Lindy's local API keys.
  端到端冒烟测试脚本; 沙箱运行 9/9 RSS + 4/4 EDGAR 实测通过。
- Phase 2 `2.4` — Finnhub free-tier news ingestion:
  - `src/ingestion/finnhub.py` — `fetch_one()` (single category) and `fetch_all()` (all curated categories). Auth via `X-Finnhub-Token` request header — **never** as URL query param (test `test_api_key_must_not_appear_in_url` enforces). Per-category throttle: skips re-poll within `throttle_seconds` (default 60s) and does NOT update `feed_state` on a skip — the prior real attempt's status/timestamp are preserved. `fetch_all_from_env()` reads `FINNHUB_API_KEY`. Empty key short-circuits to a no-HTTP-call 401 outcome.
  - `FINNHUB_FEEDS` — 3 curated categories (`general` / `forex` / `merger`) wrapped as `FeedConfig`s so storage / dedup / reporting are uniform with RSS.
  - Output: same dedup-compatible item dict shape as RSS (`title`, `body`, `source`, `source_url`, `pub_ts`, `meta`); `meta` carries `finnhub_id` / `finnhub_publisher` / `related_tickers` / `finnhub_category`.
  - `src/storage/feed_state.py` — added `STATUS_THROTTLED = -4` sentinel.
  - `src/storage/__init__.py` — exports `STATUS_THROTTLED`.
  - `src/ingestion/__init__.py` — re-exports `rss` + `finnhub` submodules with usage example in docstring.
  - `tests/test_finnhub.py` — 23 tests with `FakeJSONResponse` + `FakeHTTPClient`: normalization (incl. timestamp conversion verified against stdlib), header-only-auth contract, empty-key short-circuit, throttle window / preserve-state-on-skip / disable-with-zero / let-through-after-window, 401 / 429 / malformed JSON / unexpected dict payload / non-dict array elements / headlineless / dateless / timeout / connection error / dedup compatibility, multi-category fetch_all, fetch_all_from_env reads env var + propagates to header, fetch_all_from_env missing-key short-circuit, catalog well-formedness.
  Phase 2.4 Finnhub 模块完成,23 个测试通过 (含安全契约: API key 必须只在 header 不在 URL)。
- ADR-0011 — schema migration policy (additive-only via `IF NOT EXISTS` + `SCHEMA_VERSION` bump; non-additive deferred until needed).
  ADR-0011 schema 迁移策略 (累加式即可,非累加式以后再补)。
- ADR-0012 — RSS source selection (Path A, all-free stack within budget; Reuters/Bloomberg/WSJ/FT explicitly dropped with reasons).
  ADR-0012 RSS 源选择 (走 Path A 全免费栈;Reuters/Bloomberg/WSJ/FT 明确剔除并记录原因)。
- Phase 2 `2.3` — RSS fetcher with ETag caching + per-feed graceful degradation:
  - `src/ingestion/feed_config.py` — `FeedConfig` dataclass + curated `FEEDS` list (13 feeds: Fed/ECB/BoE/BoJ press, Treasury, BIS, IMF, Economist × 4 sections, Nikkei Asia, Caixin Global). `feed_by_source_id()` lookup helper.
  - `src/ingestion/rss.py` — `fetch_one()` (single feed, sends `If-None-Match`/`If-Modified-Since` from `feed_state`, parses with `feedparser`, normalizes entries to dedup-compatible dicts, persists outcome to `feed_state`), `fetch_all()` (sequential multi-feed with shared httpx Client). Bilingual `User-Agent`. Per-feed timeout / connection-error / parse-error all recorded as negative status sentinels and the run continues.
  - `src/ingestion/__init__.py` — exports public API.
  - `requirements.txt` (new) — `feedparser>=6.0,<7.0` + `httpx>=0.27,<1.0`.
  - `tests/test_rss.py` — 16 tests with `FakeHTTPClient` (mocks httpx) and real `feedparser` parsing of canned RSS bytes: 200 path / 304 / non-200 / timeout / connection error / malformed XML / titleless entries / missing pubDate fallback / etag preservation across 304 / conditional headers on second poll / dedup-API compatibility / multi-feed continuation past failures / catalog well-formedness.
  Phase 2.3 RSS 模块完成,16 个测试通过 (含 mocked HTTP + 真实 feedparser 解析)。
- Phase 2 schema v2 — `feed_state` table for ingestion ETag / Last-Modified / per-feed health caching:
  - `src/storage/schema.sql` — added `feed_state` (PK on `feed_url`) + index on `last_fetched_ts`.
  - `src/storage/feed_state.py` — `FeedState` dataclass with `conditional_get_headers()` helper, `get_feed_state()` / `upsert_feed_state()` (full-replace semantics) / `delete_feed_state()`, `STATUS_TIMEOUT` / `STATUS_CONNECTION_ERROR` / `STATUS_PARSE_ERROR` sentinels for non-HTTP failures.
  - `src/storage/db.py` — `SCHEMA_VERSION` bumped 1 → 2.
  - `src/storage/__init__.py` — exports the feed_state API.
  - `tests/test_feed_state.py` — 14 tests covering dataclass defaults, conditional-GET header construction, get/upsert/delete paths, full-replace semantics (no field-merge), error-clear-on-success behavior, schema-bump idempotency.
  Schema v2 与 feed_state 完成,14 个测试通过。
- ADR-0010 — LLM tier selection: `claude-haiku-4-5-20251001` (cheap) + `claude-opus-4-7` (strong). Projected ≈ $6/month.
  ADR-0010 LLM 模型档选择决定,预计月花费约 6 美元。
- Phase 2 `2.2` — content-hash deduplication:
  - `src/processing/dedup.py` — `content_hash(title, body, source, pub_ts) -> str` (SHA-256 hex, 64 chars, NFC + whitespace normalized, body truncated to 2048 codepoints), `is_duplicate(conn, h) -> bool`, `filter_new(conn, items)` streaming generator that drops DB-archived AND intra-batch duplicates and augments each yielded item with `content_hash`.
  - `tests/test_dedup.py` — 25 tests: determinism, per-field sensitivity, whitespace normalization, NFC unification of composed/decomposed Unicode, None-body handling, codepoint-level body truncation, empty-input rejection, cross-source separation, input-mutation safety, generator streaming, DB lookup. All passing.
  - `src/processing/__init__.py` — exports the dedup API.
  Phase 2 `2.2` 去重模块完成,25 个测试通过。设计决定 (ADR 本轮未新增): `source` 参与哈希, 跨源同事件两条都保留。
- Phase 2 `2.1` — SQLite storage layer:
  - `src/storage/schema.sql` — `items`, `runs`, `deliveries`, `triggers` tables with indexes, CHECK constraints, FK cascade / SET NULL.
  - `src/storage/db.py` — `get_connection()` context manager (commits on clean exit, rolls back on exception, enables WAL + foreign keys), idempotent `init_db()`, `schema_version()` helper, version-refusal safeguard.
  - `conftest.py` — makes `src/` packages importable in tests without `pip install -e .`.
  - `requirements-dev.txt` — `pytest>=8.0`.
  - `tests/test_storage.py` — 17 tests covering schema creation, idempotency, CHECK constraints, UNIQUE on `content_hash`, FK cascade (runs→deliveries, items→triggers), FK SET NULL (runs→triggers.run_id), rollback and commit semantics of the context manager. All passing.
  Phase 2 `2.1` SQLite 存储层完成,17 个测试全部通过。

### Changed / 变更
- `.env.example`: `LLM_STRONG_MODEL` switched from `claude-sonnet-4-6` to `claude-opus-4-7`; comments reference ADR-0010.
  `.env.example` 强模型默认值切换,注释指向 ADR-0010。

### Security / 安全
- **Pre-commit hook: docs/ + `*.md` whitelist for Layer 2 heuristic** — the previous attempt at the context-aware redesign whitelisted only test files. But documentation files are categorically the same: they legitimately describe key shapes (this very ADR-0014 documents the patterns the hook catches, and that documentation kept tripping the hook). Layer 2 now also skips `docs/`, `**/docs/`, and any file with extension `.md`, `.markdown`, `.rst`, `.txt`. Layer 1 strict patterns (sk-ant-, AKIA, etc.) still apply to docs — verified end-to-end: a real `sk-ant-...` shape pasted into a markdown file still blocks. The actual ADR-0014 content that triggered the failure is now confirmed clean against the real hook in a fresh git repo.
  Layer 2 启发式现在也跳过 docs/ 与 .md/.markdown/.rst/.txt 扩展名;Layer 1 仍对所有文件生效 (真实 sk-ant- 粘到 markdown 里依然拦截)。在干净仓库验证了真实 ADR-0014 内容能直接通过。
- **Pre-commit hook bash-3.2 portability + ADR-0014 placeholder examples** — the first attempt at the context-aware redesign used `mapfile` (bash 4+) which broke on macOS's default bash 3.2. Replaced with a portable while-read loop + explicit empty-array declaration + length-gated expansion (works on bash 3.2 through 5+). Separately, ADR-0014's e2e-test table contained literal `sk-ant-...` and `AKIA1234...` strings as documentation; Layer 1 correctly fired on those. Rewrote the table with angle-bracket placeholders (`sk-ant-<25-char tail>`, `AKIA<16-uppercase>`) so the ADR doesn't trigger the patterns it documents. Real test fixtures in `tests/` still use the literal forms (and Layer 1 still catches them there).
  Re-tested 11/11 e2e cases including a markdown doc with safe placeholders (allow) and a markdown doc with literal key shape (block). bash 3.2 portability verified by re-installing the hook and inspecting non-comment lines for bash-4-only constructs.
  hook 改 bash 3.2 兼容; ADR-0014 表格里的真实 sk-ant- / AKIA 例子换成尖括号占位符,避免 ADR 自己撞规则。
- **Pre-commit hook: context-aware detection (ADR-0014)** — after a third false-positive round on a legitimate test fixture (`tests/test_fred.py` with `API_KEY = "test_fred_key_xyz123"`), restructured the hook from "flat regex over the whole diff" to **three layers**:
  1. **Strict patterns** (Anthropic / OpenAI / AWS / Telegram / Google / FRED-URL / Finnhub / PEM / Bearer) apply to ALL files including `tests/`. A real-key shape is too dangerous to bypass anywhere.
  2. **High-entropy heuristic** applies ONLY to non-test files. Test fixtures legitimately use fixture-shaped strings; the strict patterns still catch real key shapes inside tests.
  3. **Inline `# noqa: secret` pragma** allows per-line override for the rare legitimate non-test fixture.
  Exclusion list broadened: `test_<word>_(key|token|value|...)` (catches `test_fred_key_xyz123`), plus `fixture`, `mock`, `stub` standalone.
  End-to-end tested against a fresh git repo with the real hook, 10/10 cases green (including the exact failing line + sk-ant- shape in test files still blocking + the pragma override + the env-read false-positive from the previous round).
  Hook now treats false positives as a design failure to fix (ADR-0014), not a regex to extend. Architecture documented so future iterations don't roll it back.
  钩子结构改造: 三层 (严格模式 / 非测试文件启发式 / 行内 pragma) + 加宽测试夹具排除。10/10 e2e 测试通过。
- **Pre-commit hook structural improvement**: the high-entropy heuristic now requires a leading quote on the RHS (`api_key = "..."` form) so it stops false-positive-firing on Python code that reads from env vars or config dicts (`api_key = os.environ.get(...)`, `token = config["..."]`, `password = input(...)`). Belt-and-suspenders: `os.environ`, `os.getenv`, `getenv(` added to the exclusion list. Self-tested: 8 should-not-fire cases (incl. the exact lines that blocked Commit N's first attempt) + 2 should-fire cases — all green.
  Hook 启发式改为要求 RHS 以引号开头,避免把"读 env 的代码"误判为硬编码密钥;真实硬编码 key 几乎都在引号内,而 .env 文件已被文件名规则拦下。
- **Post-incident hardening (ADR-0013)**: three live API keys (Anthropic, FRED, Finnhub) were pasted into chat during 3.1 setup. All three rotated within minutes; Anthropic billing confirmed clean. Pre-commit hook upgraded with three new patterns:
    1. `FRED API key in URL` — `[?&]api_key=[a-f0-9]{32}` catches the FRED URL form.
    2. `Finnhub-context token` — requires literal "finnhub" near a 20+-char lowercase value, avoiding false positives on the project's own SHA-256 content hashes.
    3. `Finnhub header token` — `X-Finnhub-Token: <value>` form.
  Binding operating rule established: API keys flow into the project ONLY via local `.env` edits; never via chat / email / Slack / shared docs. Self-tested: 3 catches + 2 false-positive guards green.
  事件后加固: 三把 key 已 rotate, hook 新增 FRED URL / Finnhub env-var / Finnhub header 三组模式, 加 SHA-256 等误报隔离, ADR-0013 固化"key 永不入 chat"纪律。

### Fixed / 修复
- **Feed list pruned to 9 verified live feeds** after smoke testing on 2026-04-23. ECB / BoE / BoJ / Economist Business / Economist Leaders confirmed working (5 newly verified beyond the 4 previously confirmed: Fed, Nikkei Asia, Economist Finance, Economist Week). US Treasury (404) / BIS (404) / IMF (redirect cancelled) / Caixin Global (403) moved to a documented "Known broken / candidates" block in `src/ingestion/feed_config.py` with each error and a research starting point. ADR-0012 status table updated.
  RSS 源经冒烟测试缩到 9 个已验证;4 个失败的 (US Treasury / BIS / IMF / Caixin Global) 移至 feed_config.py 末尾的 "Known broken" 段并附带错误信息与下一步研究建议。

---

## [0.1.0] — 2026-04-23

First scaffold. No runtime behavior yet — documentation and hygiene only.
首次脚手架,尚无运行时功能,仅文档与卫生控制。

### Added / 新增
- Repository scaffold under `src/{ingestion,processing,reasoning,delivery,storage}`, `tests/`, `scripts/`, `.github/workflows/`.
  仓库目录脚手架 (`src/`、`tests/`、`scripts/`、`.github/workflows/`)。
- Bilingual `README.md` + `README.zh-CN.md`.
  双语 README (英文主 + 中文主两份)。
- MIT `LICENSE` (pending owner confirmation).
  MIT 许可证 (待所有者确认)。
- `.env.example` template for LLM, Telegram, SMTP, Finnhub, FRED, SEC EDGAR.
  环境变量模板,覆盖 LLM、Telegram、SMTP、Finnhub、FRED、SEC EDGAR。
- Aggressive `.gitignore` (secrets, data, logs, envs, OS/IDE artifacts).
  激进的 `.gitignore`。
- Memory system: `docs/DECISIONS.md` (ADRs), `docs/WORKFLOW.md` (canonical task list + update protocol), `docs/CHANGELOG.md` (this file), `docs/ARCHITECTURE.md` (system design), `docs/PROMPTS.md` (versioned prompt templates), `docs/SESSION_LOG.md` (per-session status).
  记忆系统六件套。
- `scripts/install-hooks.sh` — installs a custom pre-commit secret scanner (Anthropic / OpenAI / AWS / Telegram / PEM patterns).
  自定义 pre-commit 密钥扫描脚本。
- Update-management protocol: branch strategy, Conventional Commits, PR checklist, session rhythm.
  更新管理流程。

### Security / 安全
- Established "no secrets ever in git" invariant via `.gitignore` + pre-commit hook.
  通过 `.gitignore` 和 pre-commit 钩子建立"密钥永不入库"的不变式。

[Unreleased]: https://github.com/OWNER/market-compass/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OWNER/market-compass/releases/tag/v0.1.0
