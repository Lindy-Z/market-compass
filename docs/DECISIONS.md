# Architecture Decision Records (ADRs) / 架构决策记录

> This file is the canonical "why" archive. Every non-trivial architectural
> or design decision lives here, with date, rationale, and alternatives
> considered. Commits that make non-trivial changes must also update this
> file in the same commit.
>
> 本文件是"为什么"的权威档案。每一项非平凡的架构或设计决策都必须在此记录,
> 包括日期、理由和考察过的替代方案。涉及非平凡改动的提交必须同步更新本文件。

**Format**: lightly adapted [MADR](https://adr.github.io/madr/) — ID, title,
status, context, decision, consequences, alternatives.

---

## ADR-0001 — Repository name: `market-compass`

- **Date**: 2026-04-22
- **Status**: accepted
- **Deciders**: Lindy

### Context / 背景
We needed a name that (a) captured the three-track macro/markets/deals focus,
(b) was neutral enough to survive scope drift, (c) was easy to type and
search, and (d) was already the working folder name on the owner's machine.

### Decision / 决策
Use **`market-compass`** (中文: 市场罗盘) as the canonical repo and package
name.

### Consequences / 后果
- Package path: `market_compass/` (underscored for Python module rules).
- Domain-agnostic enough to cover future expansion into sector or equity
  tracks if the project grows.

### Alternatives considered / 备选
- `macro-pulse` (市场脉搏) — rejected as too macro-centric; the project also
  covers M&A and tech/AI deals.
- `daily-alpha-brief` (每日 Alpha 简报) — rejected as too buy-side branded;
  this is a research tool, not a signal product.

---

## ADR-0002 — Dual delivery: Telegram primary, Email fallback

- **Date**: 2026-04-22
- **Status**: accepted
- **Deciders**: Lindy

### Context / 背景
Daily reliability matters more than channel count. A single channel creates
a single point of failure (API outage, token revocation, rate limit).

### Decision / 决策
Implement **both** Telegram (primary) and SMTP email (redundant fallback) in
parallel. The daily job tries Telegram first; if it fails, it falls back to
email. The email path also receives the weekly synthesis (richer formatting).

### Consequences / 后果
- Delivery module must abstract a common `Notifier` interface with two
  concrete implementations and a composite retry/fallback wrapper.
- Two secret sets to manage (`TELEGRAM_*` and `SMTP_*`).
- Slightly more surface area for secret leakage — mitigated by strict
  `.env` hygiene and the pre-commit hook.

### Alternatives considered / 备选
- Telegram only — simpler, but fragile.
- Email only — universal, but inferior latency and formatting for a
  morning brief.

---

## ADR-0003 — Scheduler: GitHub Actions cron

- **Date**: 2026-04-22
- **Status**: accepted
- **Deciders**: Lindy

### Context / 背景
We need a zero-cost, version-controlled scheduler with good observability
and no maintenance burden. Monthly budget is ≤ $20, so any VPS cost needs
strong justification.

### Decision / 决策
Use **GitHub Actions scheduled workflows** (`on: schedule: - cron: ...`)
for the daily 7am push and the Saturday weekly synthesis.

### Consequences / 后果
- Workflow files under `.github/workflows/` become part of the audit trail.
- Secrets live in GitHub Actions repository secrets (never in the repo).
- Some cold-start latency (~30–60s) — acceptable for a morning brief.
- Cron is UTC-only; we must handle local-time anchoring in the app
  (`LOCAL_TZ` env var + code-side offset).
- Cap: free tier is 2,000 minutes/month on private repos, unlimited on
  public repos. Our repo is public, so this is a non-issue.

### Alternatives considered / 备选
- Oracle Cloud Always Free VPS — richer control but adds ops burden
  (patching, SSH, monitoring).
- Existing VPS — none available.
- Cloud Functions / Lambda — adds deployment tooling we don't need yet.

---

## ADR-0004 — Bilingual documentation policy

- **Date**: 2026-04-22
- **Status**: accepted
- **Deciders**: Lindy

### Context / 背景
Owner is fluent in both Chinese and English and prefers interleaved usage.
The project's daily output is itself bilingual, so the development artifacts
should match.

### Decision / 决策
Every human-readable artifact (READMEs, docs/*.md, non-trivial commit
bodies, session logs) is **bilingual**. Code identifiers, string literals
in code, and log messages remain English-only for tooling compatibility.
User-facing strings (push messages) are bilingual by construction.

### Consequences / 后果
- Higher documentation effort per artifact.
- Better long-term maintainability for the owner.
- Downstream readers (if any open-source contributors appear) can read
  whichever side is more comfortable.

### Alternatives considered / 备选
- English-only with CN-only README mirror — rejected; loses bilingual
  context inside sections like DECISIONS.md where rationale benefits from
  native-language nuance.

---

## ADR-0005 — License: MIT

- **Date**: 2026-04-22
- **Status**: proposed (pending Lindy's confirmation)
- **Deciders**: Lindy

### Context / 背景
This is a public, personal research tool. License should be
permissive, widely understood, and compatible with future incorporation
into other projects the owner builds.

### Decision / 决策
Adopt the **MIT License**.

### Consequences / 后果
- Maximum reuse freedom for others.
- No copyleft obligations.
- Compatible with dependencies in the stack (Apache-2.0, BSD, MIT all OK).

### Alternatives considered / 备选
- Apache-2.0 — adds explicit patent grant; value is low here (no patentable
  novelty).
- No license — strictly less free; default US/international copyright
  would make reuse illegal.
- AGPL — unnecessarily viral for a research tool.

### Open question / 待确认
If Lindy prefers Apache-2.0 or anything else, flag and swap before the
first remote push.

---

## ADR-0006 — Secret-scanning: lightweight custom pre-commit hook

- **Date**: 2026-04-22
- **Status**: accepted
- **Deciders**: Lindy

### Context / 背景
The brief specifies `git-secrets` "or equivalent." Installing `git-secrets`
requires a system-level AWS Labs dependency on every machine that clones
the repo. For a personal project we can ship a self-contained shell hook
that scans staged diffs with a tight regex set — no external binary, no
Homebrew tap.

### Decision / 决策
Ship `scripts/install-hooks.sh` that writes a `.git/hooks/pre-commit` with
inline regex checks for:

- Anthropic keys: `sk-ant-[A-Za-z0-9_-]{20,}`
- OpenAI keys: `sk-(proj-|live-)?[A-Za-z0-9_-]{20,}`
- AWS access keys: `AKIA[0-9A-Z]{16}`
- Telegram bot tokens: `[0-9]{8,12}:[A-Za-z0-9_-]{30,}`
- Private-key PEM blocks: `-----BEGIN [A-Z ]*PRIVATE KEY-----`
- Generic high-entropy strings labeled `password|secret|token=` when
  followed by long non-placeholder values.

The hook allows override via `git commit --no-verify` with a committed
justification in `docs/DECISIONS.md` (discouraged — only for false positives).

### Consequences / 后果
- No external install step.
- Easily auditable (one shell file).
- Regex set is conservative; false negatives possible for exotic key
  formats. Mitigation: whenever new secret types are introduced (e.g.,
  a new provider), add a pattern and document here.

### Alternatives considered / 备选
- AWS Labs `git-secrets` — heavier install, broader coverage but overkill.
- `detect-secrets` (Yelp) — Python dependency and a baseline file that
  needs maintenance.
- GitHub push protection / secret scanning — catches after the commit;
  we want to catch before.

---

## ADR-0007 — Storage: SQLite + content-hash dedup (Phase 2 design)

- **Date**: 2026-04-22
- **Status**: proposed (will be revisited at Phase 2 kickoff)
- **Deciders**: Lindy

### Context / 背景
The archive must survive restarts, support fast dedup, and fit inside
GitHub Actions ephemeral runners + local workstations. News items are a
small workload (thousands/day max); no need for a server DB.

### Decision / 决策
Use **SQLite** for the item archive, keyed by a SHA-256 content hash
(title + body-first-2KB + source + pub_date). For any time-series column
we decide to archive separately (market closes, FX fixes), use **Parquet**
files partitioned by month.

### Consequences / 后果
- Single-file archive; easy to copy, back up, or inspect with `sqlite3`.
- Parquet keeps pandas workflows fast if we ever do retrospective
  analytics.
- GitHub Actions jobs must upload/download the archive as a workflow
  artifact OR sync to an object store — to be decided in Phase 2.

### Alternatives considered / 备选
- Postgres — overkill for the workload.
- Flat JSONL files — fine for append-only but painful for dedup
  lookups.

---

## ADR-0008 — No heavy LLM framework; direct API calls

- **Date**: 2026-04-22
- **Status**: accepted
- **Deciders**: Lindy

### Context / 背景
LangChain, LlamaIndex, DSPy, etc. add significant complexity, tight
coupling to framework abstractions, and non-trivial upgrade costs. For a
small personal pipeline with a well-defined prompt surface, direct
`httpx`/SDK calls are clearer and easier to debug.

### Decision / 决策
Call provider APIs directly (`anthropic` SDK for Claude, `openai` SDK for
fallback). Wrap them in a thin internal `LLMClient` that handles retries,
model routing (cheap/strong), and cost accounting.

### Consequences / 后果
- We own the retry + fallback logic (simple).
- Prompts are plain strings/templates in `docs/PROMPTS.md` and
  `src/reasoning/prompts.py`; version them explicitly.
- If we later need a framework, the abstraction boundary is small
  enough to swap.

### Alternatives considered / 备选
- LangChain — rejected; abstraction overhead not worth it at this scale.
- LiteLLM — considered for unified provider switching; may revisit if
  we ever need more than 2 providers.

---

## ADR-0009 — Defer initial commits to local machine via `bootstrap.sh`

- **Date**: 2026-04-23
- **Status**: accepted
- **Deciders**: Lindy (via scaffold session)

### Context / 背景
The scaffold session ran inside a FUSE bindfs sandbox mount over the user's
workspace folder. During the pre-commit hook self-test (planting a fake
`sk-ant-` key, staging it, expecting the hook to block), a subsequent
cleanup failed because the mount is configured with write-but-no-unlink
semantics — `rm`, `find -delete`, and `mv` all return `EPERM` even for files
just created. The side effect: `.git/index.lock` from the interrupted
`git add` is stuck, so no further index updates (including `git commit`)
can complete from the sandbox.

### Decision / 决策
Rather than work around with `GIT_DIR` tricks or a dummy worktree, ship
`scripts/bootstrap.sh` that Lindy runs on her Mac. It:

1. Refuses to run if tracked files already exist (safety gate).
2. Deletes the stale `.git/` (Mac-side `rm` has no restriction).
3. Re-initializes `git init -b main` and writes `user.name` / `user.email`
   only if they're not globally set.
4. Installs the pre-commit hook.
5. Makes the four Conventional Commits in order: scaffold → hooks → root
   docs → memory system.
6. Tags `v0.1.0`.

### Consequences / 后果
- Reproducible, idempotent local setup. Lindy can re-run if she starts
  from scratch.
- Clean split: the sandbox produces the *content*; the local machine
  commits the *history*.
- Slightly more steps in the first-time setup (one `bash` invocation and
  one `gh repo create`).

### Alternatives considered / 备选
- `GIT_DIR=/tmp/...` to write the commits elsewhere, then export a
  bundle — works but creates an opaque handoff artifact.
- A dummy worktree pointing at `/tmp` — same problem plus more plumbing.
- Do nothing; let Lindy figure out commits herself — abandons repeatability.

---

## ADR-0010 — LLM tier selection: Haiku 4.5 (cheap) + Opus 4.7 (strong)

- **Date**: 2026-04-23
- **Status**: accepted
- **Deciders**: Lindy
- **Supersedes**: the provisional tier defaults in ADR-0008 commentary

### Context / 背景
Needed to pin the default models for the two LLM tiers before building
the reasoning engine. Budget ceiling is $20/month (soft target ≤ $15).
Task-side requirements: structured JSON output, bilingual (中英) fluency,
5-step causal-chain reasoning, reliable classification.

### Workload assumptions / 用量假设

| Tier | Calls | Input/call | Output/call | Monthly input | Monthly output |
|------|-------|------------|-------------|---------------|----------------|
| Cheap — daily extraction, classify, summary, per-item causal chain | ~40/day × 30 = 1,200 | ~1.5K | ~500 | ~1.8M | ~600K |
| Strong — weekly synthesis + event-triggered deep dives | 4 weekly + ~5 triggered = ~9 | ~10K avg | ~2.5K avg | ~85K | ~22K |

### Candidate cost table (standard pricing, no caching)

**Cheap tier**:
| Model | In $/MTok | Out $/MTok | Monthly |
|---|---|---|---|
| Claude Haiku 4.5 | 1.00 | 5.00 | **~$4.80** |
| Claude Haiku 3.5 | 0.80 | 4.00 | ~$3.84 |
| gpt-5-mini | 0.25 | 2.00 | ~$1.65 |
| gpt-4o-mini | 0.15 | 0.60 | ~$0.63 |

**Strong tier**:
| Model | In $/MTok | Out $/MTok | Monthly |
|---|---|---|---|
| Claude Sonnet 4.6 | 3.00 | 15.00 | ~$0.59 |
| **Claude Opus 4.7** | **5.00** | **25.00** | **~$0.98** |
| gpt-5.4 | 2.50 | 15.00 | ~$0.54 |
| gpt-5-pro | 15.00 | 120.00 | ~$3.92 |

### Decision / 决策
- `LLM_CHEAP_MODEL = claude-haiku-4-5-20251001`
- `LLM_STRONG_MODEL = claude-opus-4-7`

Projected total: **~$6/month**, well under the $15 soft target.

### Rationale / 理由
1. **Single-SDK simplicity** — one provider = one key, one tokenizer,
   one prompt-cache strategy, one bill. The ~$3/month savings from a
   dual-provider (OpenAI cheap + Anthropic strong) setup is not worth
   the dual-plumbing cost for a personal project.
2. **Cheap-tier quality floor** — Haiku 4.5 has more reliable JSON
   structuring and Chinese naturalness than mini-class OpenAI models
   at the same output scale. Worth ~$3/month over gpt-5-mini.
3. **Strong-tier "don't save money here"** — strong-tier volume is so
   small (~107K total tokens/month) that the Opus 4.7 vs Sonnet 4.6
   delta is ~$0.40/month. For weekly synthesis and event-triggered deep
   dives — the places where causal-chain depth and cross-market
   reasoning matter most — we take the flagship.

### Consequences / 后果
- `src/reasoning/llm_client.py` (Phase 2) builds on the `anthropic` SDK
  only.
- Cost-accounting in `runs.llm_cost_usd` uses Anthropic's pricing
  schedule (hardcode a small price table keyed on model name).
- If monthly spend is observed to exceed $10 (well before the $15 soft
  target), revisit this ADR before any corrective action.

### Alternatives considered / 备选
- **Dual provider (gpt-5-mini cheap + Opus 4.7 strong)** — saves
  ~$3/month; rejected as poor effort/reward ratio.
- **Haiku 4.5 + Sonnet 4.6** — saves ~$0.40/month on strong tier;
  rejected in favor of the flagship because strong-tier is where
  quality compounds (weekly synthesis + triggers feed the handoff
  prompt for deeper frontier-model analysis).
- **Haiku 3.5 cheap** — saves ~$1/month; rejected because the older
  model's structured-output and bilingual reliability is measurably
  weaker.

### Open follow-ups / 后续待办
- In Phase 2 `2.13` (dry-run / smoke test), run the same 20 test items
  through both `claude-haiku-4-5` and `gpt-5-mini`; if output quality
  gap is smaller than expected, re-open this ADR.
- Verify `claude-opus-4-7` is the exact API model string via Anthropic
  docs before the first production run (ADR pending correction if the
  string is e.g. `claude-opus-4-7-YYYYMMDD`).

---

## ADR-0011 — Schema migration policy: additive-only via `IF NOT EXISTS`

- **Date**: 2026-04-23
- **Status**: accepted
- **Deciders**: Lindy

### Context / 背景
Phase 2 added a `feed_state` table for ingestion ETag caching, bumping
`SCHEMA_VERSION` from 1 to 2. We needed a policy for how schema changes
are handled going forward without dragging in a heavy migration framework
(Alembic, yoyo) for a personal project.

### Decision / 决策
- **Additive-only changes** (new tables, new indexes, new columns with
  defaults that allow `NULL`) are made by editing `src/storage/schema.sql`
  with `CREATE TABLE / INDEX / etc. IF NOT EXISTS` and bumping
  `SCHEMA_VERSION` in `src/storage/db.py`. No migration script needed —
  `init_db()` is idempotent and will create only the missing objects on
  existing DBs.
- **Non-additive changes** (column drops, NOT-NULL adds without default,
  type changes, table renames) require a `migrations/` directory with
  numbered SQL files (`0001_v1_to_v2.sql`, …) applied by a small migrator
  in `db.py`. Until we hit a non-additive change, we don't build that
  scaffolding.
- `init_db()` always **refuses** to open a DB whose `user_version` is
  newer than the code's `SCHEMA_VERSION` — protects against an old code
  silently corrupting a newer DB.

累加式改动: 在 schema.sql 用 `IF NOT EXISTS` 加表/索引/可空列,递增 `SCHEMA_VERSION`,
不需要迁移脚本。非累加式改动: 引入 `migrations/` 目录加编号 SQL 与小型迁移器,
直到真正需要再写。`init_db()` 永远拒绝打开 user_version 高于代码版本的库。

### Consequences / 后果
- Schema bumps are cheap and frequent in early phases.
- The `init_db()` version-refusal guard means CI / local dev against a
  newer DB fails loudly rather than silently corrupting state.
- We owe ourselves a real migration framework the first time we need
  to drop or retype a column.

### Alternatives considered / 备选
- **Alembic from day 1** — too much ceremony for personal-scale work.
- **`yoyo-migrations`** — lighter than Alembic but still adds a CLI and
  a tracking table; deferred until we actually need it.
- **No version tracking** — would let stale code happily overwrite
  newer state. Rejected as a foot-gun.

---

## ADR-0012 — RSS source selection: free-stack-only (Path A)

- **Date**: 2026-04-23
- **Status**: accepted
- **Deciders**: Lindy
- **Depends on**: budget ceiling in project brief ($20/mo hard, $15 soft)

### Context / 背景
Phase 1 plan listed Reuters / Bloomberg / WSJ / FT / NYT biz / Nikkei /
Caixin as RSS sources. Reality check (web research, 2026-04-23):

- **Reuters** retired public RSS in June 2020. Only paths today are
  Google News RSS proxy or third-party feed generators — both fragile
  and ToS-questionable.
- **Bloomberg** never offered public RSS.
- **WSJ / FT** are paywalled; RSS that exists serves headlines but
  full-article access requires a subscription cookie, not feasible from
  a server-side fetcher.
- **Nikkei Asia** offers a headlines-only RSS at `asia.nikkei.com/rss/feed/nar`
  (full articles paywalled).
- **Caixin Global** — the URL surfaced in research returned HTTP 406;
  treated as unverified.

User indicated willingness to pay within budget. Cheapest credible paid
news API is **Marketaux Basic at $29/mo**, which alone exceeds the $20
ceiling. User explicitly opted to stay within budget.

### Decision / 决策
**Path A — all-free stack**:

| Track | Source | URL | Status (smoke-tested 2026-04-23) |
|---|---|---|---|
| Macro / CB | Federal Reserve press releases | `federalreserve.gov/feeds/press_all.xml` | ✅ verified |
| Macro / CB | ECB press releases | `ecb.europa.eu/rss/press.xml` | ✅ verified |
| Macro / CB | Bank of England news | `bankofengland.co.uk/rss/news` | ✅ verified |
| Macro / CB | Bank of Japan news | `boj.or.jp/en/rss/whatsnew.xml` | ✅ verified |
| Macro / Gov | US Treasury press | `home.treasury.gov/news/press-releases/feed` | ❌ **404** — moved with Drupal migration; needs new URL |
| Macro / Multi | BIS press releases | `bis.org/list/pressreleases/rss.xml` | ❌ **404** — RSS index lives at `/rss/index.htm` |
| Macro / Multi | IMF news | `imf.org/en/News/RSS?Language=ENG` | ❌ **redirect cancelled** — try `external/np/cpid/rss.aspx` |
| Markets/Macro | Economist — Finance & Economics | `economist.com/finance-and-economics/rss.xml` | ✅ verified (RSS open; full-text paywalled) |
| Macro/World | Economist — The world this week | `economist.com/the-world-this-week/rss.xml` | ✅ verified |
| Markets/Deals | Economist — Business | `economist.com/business/rss.xml` | ✅ verified |
| Macro | Economist — Leaders | `economist.com/leaders/rss.xml` | ✅ verified |
| Asia | Nikkei Asia headlines | `asia.nikkei.com/rss/feed/nar` | ✅ verified (headlines only; articles paywalled) |
| China bilingual | Caixin Global | `caixinglobal.com/rss/` | ❌ **403** — site blocks non-browser UAs; gateway path also returned 406 |
| Markets cross-source | Marketaux **free tier** (100 reqs/day) | API, not RSS | TBD (Phase 2.x) |

**Smoke-test outcome 2026-04-23**: 9 of 13 feeds verified live. The 4
failing entries (US Treasury, BIS, IMF, Caixin Global) are moved to a
"Known broken / candidates" block in `src/ingestion/feed_config.py`
with the actual error and a research starting point, so they can be
revived later without re-discovering the same failure mode.

Plus already-planned sources: Finnhub free, SEC EDGAR (deals), GDELT
(geopolitical), FRED (macro time-series).

Per-feed degradation is built into `src/ingestion/rss.py`: an unverified
or temporarily-broken feed records its error in `feed_state.last_status`
and the run continues. The `Notes` column above tracks our certainty
state at the moment of writing.

### Consequences / 后果
- **Total incremental data cost: $0/month.** Project still ≈$6/month
  (LLM only).
- ~6–24h delay on breaking M&A vs. a paid newsfeed. Acceptable for a
  morning-brief product. If quality proves insufficient after 1–2 months
  of operation, revisit and re-open this ADR with a budget-bump proposal
  (likely +$29/mo for Marketaux Basic, requiring a separate ADR raising
  the ceiling).
- Reuters / Bloomberg / WSJ / FT explicitly **dropped** from the feed
  list. Their absence is documented here so future Lindy doesn't wonder
  why they're missing.
- Caixin coverage is partial until a working URL is found. Mitigation:
  rely on Nikkei Asia + Economist for Asia coverage in the meantime.

### Alternatives considered / 备选
- **Path B — add Marketaux Basic at $29/mo**: would require raising the
  $20 hard ceiling to ~$40 in a separate ADR. Rejected by user.
- **Reuters via Google News RSS proxy**: ToS-questionable, fragile to
  Google rendering changes. Rejected.
- **Web-scraping paywalled sites**: violates ToS, brittle, ethically
  shaky. Rejected.

---

## ADR-0013 — Secrets discipline post-incident

- **Date**: 2026-04-23
- **Status**: accepted
- **Deciders**: Lindy (after the incident)

### Context / 背景
During Phase 3.1 setup, three live API keys (Anthropic, FRED, Finnhub)
were pasted directly into the chat with the assistant. Chat content is
non-private — it lands in conversation logs, can be reviewed by
operators, and may flow into LLM training corpora unless explicitly
opted out. The Anthropic key in particular allows real spending on
the owner's account, so the incident is treated as a leak, not a
near-miss.

阶段 3.1 起步时, 三把真实 API key 被直接粘贴到与助手的 chat 中。chat 不是
私密通道,内容可能进入日志、被运营审阅、被训练数据收录。Anthropic key 尤其
直接对应账户费用,因此按"已泄漏"处置,不按"差点泄漏"处置。

### Resolution / 处置
1. All three keys revoked + rotated within minutes of detection.
2. Anthropic billing dashboard checked — no unauthorized usage observed.
3. New keys filled into local `.env` only; never sent over chat / email
   / Slack / any shared surface.
4. Pre-commit hook upgraded — see Decision below.

### Decision / 决策

**Operating rule (binding on both Lindy and the assistant)**:
- API keys are **never** pasted into chat, email, Slack, code review,
  shared docs, or any LLM input box. Period.
- Keys flow into the project via exactly one channel: a local edit of
  `.env` (which is `.gitignore`-blocked) on the owner's machine.
- The assistant **never asks Lindy to share a key with it**. Instead,
  it asks Lindy to confirm "key is now set in local `.env`". The
  assistant has no need to see the key's value to write or test code.
- If the assistant accidentally outputs a real-looking key in any
  message, treat it as compromised and rotate immediately.

**Defense-in-depth additions to the pre-commit hook**:
- Added pattern `FRED API key in URL` matching `[?&]api_key=[a-f0-9]{32}`
  — catches accidental commits of a constructed FRED URL with the real
  key baked in.
- Added pattern `Finnhub-context token` requiring the literal substring
  "finnhub" near a 20+ char lowercase-alphanumeric value. Avoids false
  positives on generic 40-char hashes (which are common — see
  ``content_hash`` in `processing/dedup.py`) while still catching the
  realistic exposure paths (`FINNHUB_TOKEN = "..."`, `finnhub_api_key:
  "..."`, etc.).
- Added pattern `Finnhub header token` matching the
  `X-Finnhub-Token: <value>` header form used by `ingestion/finnhub.py`.

操作铁律 (对 Lindy 和助手双方均有约束):
- API key **绝不**进 chat / 邮件 / Slack / 代码评审 / 共享文档 / 任何 LLM 输入。
- Key 进入项目的唯一渠道: 本地编辑 `.env` (已被 .gitignore 屏蔽)。
- 助手永远不向 Lindy 索要 key 值,只需要 Lindy 确认"已填入本地 .env"。助手
  写代码与测试时根本不需要看到 key 本身。
- 助手任何输出中若出现疑似真 key,立即按泄漏处置 + rotate。

钩子防御加固: 新增 FRED 的 URL 模式、Finnhub 的 env-var 上下文模式、
Finnhub 的 header 模式; 三者均做了误报抑制 (例如要求"finnhub"上下文
才匹配 40 字符串,避免误伤项目里的 SHA-256 哈希)。

### Consequences / 后果
- The hook is the LAST line of defense, not the first. The owner's
  fingers are the first; this ADR's operating rule covers them.
- New ADR template: any future incident → ADR with cause + decision +
  technical guard added. Don't fix a security incident silently.
- Onboarding a future contributor (if this ever stops being
  Lindy-only): point them at this ADR before they have any keys.

### Alternatives considered / 备选
- **Move all keys to a secrets manager (1Password, AWS Secrets Manager,
  GitHub Actions encrypted secrets)** — overkill for a personal project
  with one operator; revisit if the project grows users. GitHub Actions
  encrypted secrets WILL be used for the production cron (Phase 2.12);
  the local-`.env`-only rule applies to development.
- **Disable chat-based assistant interaction** — rejected; the assistant
  is too useful for development. The discipline rule + hook are the
  right level.
- **Aggressive entropy-based hook (block any high-entropy 32+ char
  string in any commit)** — too many false positives (hashes,
  identifiers, base64-encoded test fixtures). The targeted patterns we
  added are surgical enough.

---

## ADR-0014 — Pre-commit hook: context-aware secret detection

- **Date**: 2026-04-23
- **Status**: accepted
- **Deciders**: Lindy (after the third false-positive round)
- **Refines**: ADR-0013 (secrets discipline)

### Context / 背景
The secret-scanning pre-commit hook produced **three rounds of
false positives** in rapid succession during Phase 3.x development:

| Round | Trigger | Symptom |
|---|---|---|
| Commit M | Anthropic-shape fixture in test file (`API_KEY` assigned a `sk-ant-<25-char value>` form) | test fixture string was 20+ chars |
| Commit N | `api_key = os.environ.get(<env-var-name>, ...)` | code expression on RHS, not a literal |
| Commit O | `API_KEY = "test_<word>_key_<short hex>"` (in `tests/test_fred.py`) | test fixture with intermediate words between `test_` and `_key` |

Each round was patched with a narrower regex tweak:
- Round 1: shorten test fixtures + add `placeholder` / `dry-run` /
  `not-real` / `fake` / `dummy` to the exclusion list.
- Round 2: require leading quote on RHS so `os.environ.get(...)` etc.
  doesn't match.
- Round 3: ?

Continuing the regex-tweak pattern is whack-a-mole. The underlying
problem is that the heuristic has no **context**: it can't tell a test
fixture from a real key by looking at the line alone.

三轮误报本质同源: 启发式只看行文本,不看上下文 (文件路径、调用形式、显式 pragma)。
继续靠"加正则排除项"是治标。

### Decision / 决策
The pre-commit hook now uses **layered, context-aware detection**:

**Layer 1 — Strict patterns (every file, no exceptions)**

Specific known-shape regexes (Anthropic `sk-ant-`, OpenAI `sk-`, AWS
`AKIA`, Telegram bot tokens, Google `AIza`, PEM blocks, FRED URL,
Finnhub-context, X-Finnhub-Token header). These represent unambiguous
real-key shapes. Even a "test" file with `KEY = "sk-ant-real_looking..."`
gets blocked — the cost of cleaning a leaked Anthropic key dominates
the friction.

**Layer 2 — High-entropy heuristic (CODE FILES only)**

Pattern: `<secret-named-var> = "<20+-char-quoted-literal>"`. Skipped for
two whole file classes:

1. **Test fixtures**: `tests/`, `test/`, `**/tests/`, `**/spec/`. Tests
   legitimately use fixture-shaped strings.
2. **Documentation**: `docs/`, `**/docs/`, and any file with extension
   `.md`, `.markdown`, `.rst`, `.txt`. Docs legitimately describe key
   shapes (this very ADR contains examples of what the hook catches).

Layer 1 still applies to docs, so a real `sk-ant-...` accidentally
pasted into a README still gets caught.

The heuristic also requires a **leading quote** on the RHS so it
ignores Python expressions like `api_key = os.environ.get(...)`,
`token = config["..."]`, `password = input(...)`.

**Layer 3 — Inline `# noqa: secret` pragma (any file)**

Lines ending with `# noqa: secret` (case-insensitive whitespace) are
skipped by the heuristic. Use sparingly for the rare legitimate
non-test fixture that the exclusion list doesn't catch; the explicit
opt-in shows up in code review and signals intent.

**Exclusion list, broader (still applies in non-test files):**

`your_..._here`, `xxxxx`, `changeme`, `replace_me`, `example`, `...`,
`placeholder`, `dry-run`, `not-real`, `fake`, `dummy`, `fixture`,
`mock`, `stub`, `test_<word>_(key|token|value|placeholder|fixture|api|password|secret)`,
`os.environ`, `os.getenv`, `getenv(`. All case-insensitive.

三层防御:
  1. 严格模式 — 已知特征 (sk-ant-, AKIA, 等),所有文件无差别拦截。
  2. 高熵启发式 — 仅代码文件 (排除 tests/、docs/、*.md 等文档);要求 RHS 以引号开头,避免误判表达式。
  3. 行内 pragma `# noqa: secret` — 显式逐行跳过,适用于罕见合理情况。

### End-to-end tested / 端到端测试 (10/10 green)

Verified against a fresh git repo with the actual hook in
`.git/hooks/pre-commit`:

> **Note on placeholders below**: descriptions use angle-bracket forms
> like `sk-ant-<...>` and `AKIA<...>` so this ADR itself doesn't trigger
> the Layer 1 patterns it's documenting. Real tests use the literal
> shapes (which then correctly fire Layer 1).

| Case | Expected | Actual |
|---|---|---|
| `tests/test_fred.py` with `API_KEY = "test_<word>_key_<chars>"` | allow | ✓ allow |
| `scripts/run_triage.py` with `api_key = os.environ.get(<envvar>, ...)` | allow | ✓ allow |
| `src/real.py` with `api_key = "<32-hex production-shaped value>"` | block | ✓ block |
| `src/real.py` with key + `# noqa: secret` pragma | allow | ✓ allow |
| `tests/test_x.py` with `KEY = "sk-ant-<25-char tail>"` | block (Layer 1 still fires in test files) | ✓ block |
| `scripts/run_triage.py` with `api_key = "dry-run"` | allow | ✓ allow |
| `src/api.py` with `api_key = "test_<word>_key_<chars>"` | allow (broader exclusion catches it in non-test too) | ✓ allow |
| `tests/test_aws.py` with `config = {"api_key": "AKIA<16-uppercase>"}` | block (Layer 1) | ✓ block |
| `src/auth.py` with `token = "Bearer_<long hex>"` | block | ✓ block |
| EXACT line that just blocked Commit O retry | allow | ✓ allow |

### Consequences / 后果
- **The structural change is recorded here** so future maintainers
  don't roll it back to the simpler "regex over flat diff" form when
  they encounter an apparent false-positive class. The answer is
  always to tune within these three layers, not to abandon them.
- **Test fixtures live in `tests/`** by convention. Putting a fixture
  outside `tests/` triggers the heuristic — that's the right default;
  use the pragma to override.
- **The pragma `# noqa: secret` is part of the project's vocabulary**
  going forward. Document in onboarding docs once we have any.

### Alternatives considered / 备选
- **Drop the high-entropy heuristic entirely** — would eliminate all
  false positives but lose the "catch a random pasted key" protection.
  Rejected: protection is worth keeping, just made smarter.
- **Use `git-secrets` or `detect-secrets`** — more sophisticated
  third-party tools. Rejected (per ADR-0006) because the custom hook
  is auditable in one shell file and our project scale doesn't justify
  the dependency.
- **Move keys to a secrets manager (Vault, 1Password, etc.)** — the
  hook becomes irrelevant. Reasonable for a team / production system;
  overkill for a personal project (per ADR-0013). Revisit if the
  project grows users.

---

<!-- Template for new ADRs / 新 ADR 模板 -->

<!--
## ADR-NNNN — <Title>

- **Date**: YYYY-MM-DD
- **Status**: proposed | accepted | superseded by ADR-XXXX | deprecated
- **Deciders**: <names>

### Context / 背景
### Decision / 决策
### Consequences / 后果
### Alternatives considered / 备选
-->
