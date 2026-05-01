# Workflow / 工作流程

> **This file is the single source of truth for "what's next."** It lives at
> the center of the project: every non-trivial commit touches it. Treat it
> like a table of contents for the work.
>
> **本文件是"下一步做什么"的唯一权威来源。** 项目的核心。每个非平凡提交都应同步更新它。

---

## Canonical task list / 权威任务清单

Status legend / 状态图例: `todo` | `in-progress` | `done` | `blocked`

### Phase 1 — Foundation / 第一阶段 — 基础

| # | Task | 任务 | Status | Notes |
|---|------|------|--------|-------|
| 1.1 | Initialize git repo + directory tree | 初始化 git 仓库与目录树 | `done` | Local only; remote pending. |
| 1.2 | Write root docs (README × 2, LICENSE, .env.example) | 根目录文档 | `done` | MIT license pending Lindy's confirm. |
| 1.3 | Aggressive `.gitignore` (secrets, data, logs, envs) | 激进的 .gitignore | `done` | See file; whitelist only `.env.example`. |
| 1.4 | Pre-commit secret scanner hook | Pre-commit 密钥扫描 | `done` | `scripts/install-hooks.sh` — custom, no external binary. |
| 1.5 | Memory system: DECISIONS / WORKFLOW / CHANGELOG / ARCHITECTURE / PROMPTS / SESSION_LOG | 记忆系统 | `done` | Seeded with Phase-1 content. |
| 1.6 | Update-management protocol (branching, commits, PRs) | 更新管理流程 | `done` | Documented below. |
| 1.7 | First commits + branch `main` + tag v0.1.0 | 首次提交与打标签 | `done` | Ran via `scripts/bootstrap.sh` on Lindy's Mac (2026-04-23). Four Conventional Commits + v0.1.0 tag. See ADR-0009. |
| 1.8 | Create GitHub remote (public) | 创建 GitHub 远端 (公开) | `done` | https://github.com/Lindy-Z/market-compass — manual create (no `gh` installed) + `git remote add origin` + `git push -u origin main` + `git push --tags`. |

### Phase 2 — Data plane / 第二阶段 — 数据层

| # | Task | 任务 | Status | Notes |
|---|------|------|--------|-------|
| 2.1 | SQLite schema (items, runs, deliveries, triggers) | SQLite schema | `done` | `src/storage/{schema.sql, db.py}` + 17 passing tests in `tests/test_storage.py`. WAL journal, FK cascade/SET NULL, CHECK constraints, user_version=1. |
| 2.2 | Content-hash dedup utility | 基于内容哈希的去重 | `done` | `src/processing/dedup.py` + 25 tests. `content_hash()` / `is_duplicate()` / `filter_new()`. NFC + whitespace normalization; source is part of hash (cross-source items kept separate by design). |
| 2.3 | Ingestion: RSS (curated free-stack, see ADR-0012) | RSS 抓取 | `done` | `src/ingestion/{feed_config.py, rss.py}` + 16 tests. ETag conditional GET, per-feed graceful degradation, schema-v2 `feed_state` cache. **Smoke-tested 2026-04-23: 9 feeds verified live** (Fed, ECB, BoE, BoJ, Economist × 4 sections, Nikkei Asia). 4 candidates (US Treasury, BIS, IMF, Caixin Global) moved to "Known broken" block in `feed_config.py` with actual error + research notes. Reactivation needs correct URLs. |
| 2.4 | Ingestion: Finnhub free tier (market news + deals) | Finnhub 抓取 | `done` | `src/ingestion/finnhub.py` + 23 tests. Three categories (`general` / `forex` / `merger`). Auth via `X-Finnhub-Token` header (verified-not-in-URL by test). Per-category throttle via `feed_state.last_fetched_ts` + new `STATUS_THROTTLED` sentinel. Same `FetchOutcome` shape and dedup-compatible item dicts as RSS. |
| 2.5 | Ingestion: FRED API (select macro series) | FRED 抓取 | `done` | `src/ingestion/fred.py` + 27 tests. Schema **v3** adds `observations` table (PK series_id+obs_date, INSERT-OR-REPLACE on FRED revisions). 15 curated series (UST yield curve 3M-30Y, USD/EUR/JPY/CNY, DXY-broad index, SPX, VIX, gold London PM, CPI, UNRATE, DFF). Numerical data goes to `observations` not `items`. `get_latest_observation()` for the reasoning layer. |
| 2.6 | Ingestion: SEC EDGAR (8-K / SC 13D / 425 / DEFM14A) | SEC EDGAR 抓取 | `done` | `src/ingestion/edgar.py` + 10 tests. Wraps `rss.fetch_one` with mandatory `SEC_EDGAR_USER_AGENT` (refuses without it). 4 form-type Atom feeds. |
| 2.7 | Ingestion: GDELT event stream (geopolitical flags) | GDELT 抓取 | `todo` | Filter by event code + tone. |
| 2.8 | Classifier: track router (macro / NA+FX / deals) | 分类器: 信息轨路由 | `todo` | Cheap-LLM prompt; fallback to rules. |
| 2.9 | Delivery: Telegram bot sender | Telegram 推送 | `todo` | Markdown V2; escape rules. |
| 2.10 | Delivery: SMTP email sender | SMTP 邮件推送 | `todo` | HTML + plaintext multipart. |
| 2.11 | Delivery: Composite (primary + fallback + retry) | 复合推送器 (主备 + 重试) | `todo` | Abstraction lives in `src/delivery/notifier.py`. |
| 2.12 | GitHub Actions cron: daily 07:00 local | GitHub Actions 每日定时 | `todo` | UTC offset handled in job script. |
| 2.13 | Dry-run mode + local smoke test | Dry-run 与本地冒烟测试 | `todo` | `DRY_RUN=true` env var. |

### Phase 3 — Reasoning engine / 第三阶段 — 推理引擎

| # | Task | 任务 | Status | Notes |
|---|------|------|--------|-------|
| 3.0 | Prompt templates (PROMPTS.md + prompts.py + sync test) | Prompt 模板 | `done` | 6 versioned prompts (`classify.triage`, `summarize.bilingual`, `causal_chain.five_step`, `synthesize.weekly`, `trigger.deep_analysis`, `meta.feed_prompt`); `PromptTemplate` dataclass with `format_user()` validator; 22 tests incl. doc↔code sync. |
| 3.1 | LLM client (Anthropic SDK wrapper, model routing, cost meter) | LLM 客户端 | `done` | `src/reasoning/llm_client.py` + 43 tests. `LLMClient.call(prompt, tier_override=None, **kwargs)` routes per `model_tier`; CostMeter tracks soft ($15) / hard ($20) caps with `BudgetExceededError` on hard breach BEFORE the call. Robust JSON extraction (raw / fenced / embedded). MODEL_PRICES table for Haiku 4.5 / Sonnet 4.6 / Opus 4.7. `enable_caching=True` adds `cache_control: ephemeral` (forward-compat — current prompts under min cache size). FakeAnthropicClient pattern for tests; `anthropic` SDK only required at runtime. |
| 3.2 | Classifier+triage runner (uses `classify.triage`) | 分类与分流 | `done` | `src/processing/triage.py` + 24 tests + `scripts/run_triage.py` CLI. `triage_one()` validates LLM output (track in enum, importance 0-100); `apply_triage()` merges `deal_size` / `reason` / `track_confidence` into `items.meta` JSON without overwriting ingestion-set keys; `run_pending_triage()` selects oldest-first, supports `--limit` and `--dry-run`, isolates per-item failures, propagates `BudgetExceededError`. |
| 3.3 | Summary runner (uses `summarize.bilingual`) | 双语摘要 | `todo` | Populates `summary_en` / `summary_zh` / `key_numbers`. |
| 3.4 | Causal-chain generator (uses `causal_chain.five_step`) | 因果链生成 | `todo` | Strong-tier when importance≥70; populates `causal_chain` JSON. |
| 3.5 | Trigger detector (deal_size / cb_surprise / geo / market_move) | 触发器 | `todo` | Writes to `triggers` table; fires `trigger.deep_analysis`. |
| 3.6 | Weekly synthesis runner (uses `synthesize.weekly`) | 周度综合 | `todo` | Saturday cron; `meta.feed_prompt` chained for handoff prompt. |
| 3.7 | Evaluation harness (gold-label briefs, prompt-regression tests) | 评估框架 | `todo` | Snapshot tests on canned items per prompt version. |

---

## Update-management protocol / 更新管理流程

### Branch strategy / 分支策略

- **`main`** — always deployable. Protected (settings: require PR, require
  linear history, require status checks when CI is added).
  **`main`** — 任何时候都可部署。保护分支:必须走 PR、线性历史、CI 通过。
- **`feat/<short-name>`** — new capability (e.g. `feat/telegram-sender`).
- **`fix/<short-name>`** — bug fix.
- **`docs/<short-name>`** — documentation-only changes.
- **`chore/<short-name>`** — tooling, scaffolding, deps.
- **`refactor/<short-name>`** — internal restructuring, no behavior change.

Short-lived branches (target ≤ 2 days old). Rebase on `main` before PR.

### Commit convention / 提交规范

Use **[Conventional Commits](https://www.conventionalcommits.org/)**.
Bilingual subject for any commit that affects human-readable artifacts;
English-only is acceptable for purely internal plumbing.

Format:

```text
<type>(<scope>): <subject in EN> / <主题中文>

<optional body in EN>
<可选: 中文正文>

Refs: WORKFLOW #2.3, ADR-0006
```

Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `perf`, `style`,
`build`, `ci`.

Scope examples: `ingestion`, `delivery`, `reasoning`, `storage`, `docs`,
`ci`, `hooks`.

Rules:

1. **Memory-update rule** / 记忆更新规则 — any commit that makes a
   non-trivial architectural, process, or behavioral change MUST update
   the relevant memory file in the **same commit**:
   - Architectural/design change → `docs/DECISIONS.md` (new or updated ADR).
   - Task status change → `docs/WORKFLOW.md`.
   - User-facing change → `docs/CHANGELOG.md`.
2. **Small-commit rule** / 小提交规则 — prefer 10 commits of 50 lines over
   1 commit of 500 lines.
3. **Bilingual subject rule** / 双语主题规则 — subjects on any commit that
   touches `README*`, `docs/*`, or produces user-facing text should include
   both languages separated by ` / `.

### PR checklist / PR 清单

Every PR description must tick these boxes before merge:

```markdown
- [ ] Secret scan clean (pre-commit hook passed locally)
- [ ] Tests added or updated (if behavior changed)
- [ ] `docs/WORKFLOW.md` task status updated
- [ ] `docs/DECISIONS.md` has an ADR for any non-trivial design change
- [ ] `docs/CHANGELOG.md` entry added under `[Unreleased]`
- [ ] Commit subjects follow Conventional Commits
- [ ] Branch rebased on latest `main`
- [ ] No `.env*` (except `.env.example`), `*.key`, or data files staged
```

A `.github/pull_request_template.md` will be added in Phase 2 so the
checklist auto-populates.

### Auto-commit helper / 自动提交助手

The assistant cannot run `git commit` from its sandbox (FUSE mount blocks
`unlink`, which breaks `.git/index.lock`). To minimize the friction of
"remember the multi-line `git add` + commit message", each turn the
assistant **regenerates** `scripts/commit-pending.sh`. This script is
gitignored — it never enters the repo, just provides a one-command
shortcut on Lindy's Mac.

助手不能从沙箱跑 `git commit` (FUSE 挂载禁止 `unlink`,会卡住 `.git/index.lock`)。
为减少"记住多行 git add + commit message"的负担,助手每次 turn 重写
`scripts/commit-pending.sh`。该脚本被 .gitignore,不进 repo,只在你 Mac 上当
单条命令的快捷方式。

```bash
# Whenever you want to commit the assistant's pending work:
bash scripts/commit-pending.sh
```

The script:
- Always reflects the current turn's pending change-set + commit message
- Is regenerated (and overwrites) on every turn — leftover state is fine
- Runs `git add` + `git commit` + `git push` in sequence
- Doesn't run `pytest` — re-run that yourself if you want extra confidence

### Session rhythm / 会话节奏

At the end of each working session, write a brief status report to
`docs/SESSION_LOG.md`:

- **Date / 日期**
- **Done / 完成**: what shipped in this session (PRs merged, tasks closed).
- **Blocked / 阻塞**: anything stuck and why; who/what unblocks it.
- **Next / 下一步**: the top 1–3 tasks to pick up next session.
- **Decisions / 决策**: links to any new or updated ADRs.

This is the "breadcrumb trail" that makes resuming cheap — future sessions
start by reading the most recent entry.

---

## Budget guardrail / 预算护栏

Hard ceiling: **$20 USD/month.** Before any commit that adds a recurring
cost:

1. Estimate monthly cost in the commit body.
2. Compare against current run-rate.
3. If projected total exceeds $15/month (75% of ceiling), open a
   DECISIONS entry and request explicit sign-off from Lindy **before
   merging**.

---

_Last updated: 2026-04-23_
