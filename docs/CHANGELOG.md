# Changelog / 变更日志

All notable changes to this project will be documented in this file.
本文件记录所有值得留痕的变更。

Format adheres to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
version numbers follow [Semantic Versioning](https://semver.org/).
格式遵循 Keep a Changelog, 版本号遵循 SemVer。

## [Unreleased]

### Added / 新增
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
