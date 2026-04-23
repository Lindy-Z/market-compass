# Architecture / 系统设计

> Living document. Read top-to-bottom to understand how the pieces fit;
> read section-by-section when making changes.
>
> 活文档。自上而下阅读理解全貌,逐节阅读进行改动。

---

## 1. System at a glance / 系统概览

```text
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  Ingestion   │──▶│  Processing  │──▶│   Reasoning  │──▶│   Delivery   │
│  (RSS/APIs)  │   │ (dedup/class)│   │   (LLM)      │   │ (TG + Email) │
└──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
        │                  │                  │                  │
        ▼                  ▼                  ▼                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         Storage (SQLite + Parquet)                   │
│          items · runs · deliveries · triggers · archives             │
└──────────────────────────────────────────────────────────────────────┘
```

- **Ingestion** — pull-based, cron-triggered. Polls RSS and free-tier
  APIs; writes raw items to storage.
- **Processing** — normalize schema, dedup by content hash, classify to
  track (macro / NA+FX / deals), score importance.
- **Reasoning** — LLM generates bilingual summary + causal chain per
  qualifying item; aggregates into the morning brief.
- **Delivery** — formats and sends to Telegram (primary) and Email
  (fallback); records delivery status.
- **Storage** — one SQLite file (`data/market_compass.db`) for relational
  state; Parquet optional for bulk time-series.

---

## 2. Data flow — daily cycle / 数据流 — 每日周期

```text
06:45 local  │  Cron fires GitHub Actions `daily.yml`
06:46–06:52  │  Fetch: RSS sweeps, Finnhub, FRED, SEC EDGAR, GDELT
06:52–06:53  │  Normalize + dedup (SHA-256 content hash)
06:53–06:55  │  Classify to track + importance-score with cheap LLM
06:55–06:58  │  Reasoning pass: summaries + causal chains
06:58–06:59  │  Format bilingual brief (Markdown V2 for Telegram)
06:59–07:00  │  Deliver via Telegram, fallback Email, record status
07:00        │  Session log appended; cost meter incremented
```

Weekly (Sat 08:00 local): same pipeline but reasoning uses the strong
model and produces the synthesis + deeper-analysis prompt template.

Event-triggered: a separate workflow watches the ingest stream for
qualifying triggers (deal ≥ $5B, CB surprise, geopolitical flag,
market > 2% move). When fired, it runs reasoning with the strong model
on the single event.

---

## 3. Module responsibilities / 模块职责

### `src/ingestion/`

- One module per source (`rss.py`, `finnhub.py`, `fred.py`, `edgar.py`,
  `gdelt.py`).
- Contract: each exposes `fetch(since: datetime) -> Iterable[RawItem]`.
- Cooperative caching via ETag / `If-Modified-Since`.
- Rate-limit-aware with exponential backoff.

### `src/processing/`

- `normalize.py` — coerce every source's shape into a canonical `Item`.
- `dedup.py` — content hash over `title + body[:2048] + source + date`;
  lookup in SQLite; reject duplicates.
- `classify.py` — route to track; importance score 0–100.
- `score.py` — numerical importance based on track-specific signals
  (deal size, event-tone, surprise vs. consensus for CB actions, etc.).

### `src/reasoning/`

- `prompts.py` — prompt templates (mirrored in `docs/PROMPTS.md`).
- `causal_chain.py` — applies the 5-step template to each item.
- `synthesize.py` — weekly/event-triggered deep passes.
- `llm_client.py` — thin wrapper around Anthropic/OpenAI SDKs with
  retry, model routing, cost accounting.

### `src/delivery/`

- `formatter.py` — Markdown V2 for Telegram; multipart HTML for email.
- `telegram.py` — `Notifier` implementation via Bot API.
- `email.py` — SMTP submission.
- `notifier.py` — composite: try primary, fall back on failure,
  record outcome.

### `src/storage/`

- `schema.sql` — DDL for tables.
- `db.py` — connection factory, migrations.
- `archive.py` — retention policy enforcement; Parquet rollups.

---

## 4. Storage schema (draft) / 存储结构(草案)

```sql
-- Items: one row per deduplicated news/data item
CREATE TABLE items (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  content_hash  TEXT    UNIQUE NOT NULL,
  source        TEXT    NOT NULL,
  source_url    TEXT,
  title         TEXT    NOT NULL,
  body          TEXT,
  pub_ts        TEXT    NOT NULL,           -- ISO-8601 UTC
  fetched_ts    TEXT    NOT NULL,
  track         TEXT    CHECK (track IN ('macro','na_fx','deals','other')),
  importance    INTEGER CHECK (importance BETWEEN 0 AND 100),
  summary_en    TEXT,
  summary_zh    TEXT,
  causal_chain  TEXT,                       -- JSON blob
  meta          TEXT                        -- JSON blob for extras
);

CREATE INDEX idx_items_pub_ts ON items(pub_ts);
CREATE INDEX idx_items_track  ON items(track);

-- Runs: one row per daily/weekly/trigger execution
CREATE TABLE runs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  run_type        TEXT    CHECK (run_type IN ('daily','weekly','trigger')),
  started_ts      TEXT    NOT NULL,
  finished_ts     TEXT,
  items_in        INTEGER,
  items_delivered INTEGER,
  llm_cost_usd    REAL,
  status          TEXT    CHECK (status IN ('ok','partial','failed'))
);

-- Deliveries: one row per push attempt (primary + fallback)
CREATE TABLE deliveries (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     INTEGER REFERENCES runs(id),
  channel    TEXT    CHECK (channel IN ('telegram','email')),
  ok         INTEGER NOT NULL,              -- 0/1
  error      TEXT,
  attempt    INTEGER NOT NULL,
  ts         TEXT    NOT NULL
);

-- Triggers: event-trigger audit trail
CREATE TABLE triggers (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id    INTEGER REFERENCES items(id),
  kind       TEXT    NOT NULL,              -- 'deal_size','cb_surprise',...
  fired_ts   TEXT    NOT NULL,
  run_id     INTEGER REFERENCES runs(id)
);
```

Schema is subject to change through Phase 2; finalize before first real
run.

---

## 5. Causal-chain template / 因果链模板

Every qualifying item is processed with this 5-step structure:

1. **事件 (Event)** — what happened, one sentence, citation.
2. **一阶机制 (First-order mechanism)** — the direct channel (rates, flows,
   earnings, policy).
3. **资产反应 (Asset reaction)** — which assets moved / should move,
   direction + rough magnitude if observable.
4. **二阶效应 (Second-order effects)** — downstream implications: who
   else is affected, what positioning adjustments follow.
5. **跨市场传导 (Cross-market transmission)** — how it propagates across
   asset classes and geographies; explicit tail-risk note if relevant.

Full prompt lives in `docs/PROMPTS.md` (versioned).

---

## 6. Cost model / 成本模型

Budget ceiling: **$20/month**. Expected line items:

| Item | Unit | Est. monthly |
|------|------|--------------|
| GitHub Actions (public repo) | free | $0 |
| News APIs (RSS, Finnhub free, FRED, EDGAR, GDELT) | free | $0 |
| LLM — cheap (daily extraction + classify, ~60 items/day × 30 days) | pay-per-token | ~$3–6 |
| LLM — strong (weekly synthesis + ~5 triggered deep analyses/month) | pay-per-token | ~$5–10 |
| Telegram Bot API | free | $0 |
| SMTP (Gmail app password) | free | $0 |

Target run-rate: **≤ $15/month**, leaving 25% headroom for spikes.

Accounting: each `runs` row records `llm_cost_usd`; a monthly rollup job
in Phase 2 will flag if rolling run-rate > $15.

---

## 7. Failure modes & mitigations / 失效模式与缓解

| Failure | Mitigation |
|---------|------------|
| Telegram API outage | Email fallback; `deliveries` table captures retries. |
| LLM API outage | Alt-provider fallback (OpenAI); raw items still archived so we can backfill summaries. |
| Feed source downtime | Per-source `ok` flag; brief flags "sources offline" in header. |
| GitHub Actions cron drift | Cron fires slightly late sometimes; acceptable for a 7am brief. Add a `missed-run` catch-up check in Phase 3 if needed. |
| Secret leak | Pre-commit hook + `.gitignore` + runtime scan in CI; rotate-first playbook in README. |
| Runaway LLM cost | Per-run cost cap; daily hard-stop at $1; monthly soft-stop at $15 (notifies before proceeding). |

---

_Last updated: 2026-04-23_
