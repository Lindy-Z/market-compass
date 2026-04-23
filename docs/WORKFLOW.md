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
| 1.7 | First commits + branch `main` + tag v0.1.0 | 首次提交与打标签 | `blocked` | Deferred to Lindy's Mac via `scripts/bootstrap.sh` (sandbox FS blocks unlink, stuck .git/index.lock). See ADR-0009 and SESSION_LOG 2026-04-23. |
| 1.8 | Create GitHub remote (public) | 创建 GitHub 远端 (公开) | `blocked` | Blocked on 1.7. After bootstrap runs, Lindy runs `gh repo create market-compass --public --source=. --remote=origin --push` then `git push --tags`. |

### Phase 2 — Data plane / 第二阶段 — 数据层

| # | Task | 任务 | Status | Notes |
|---|------|------|--------|-------|
| 2.1 | SQLite schema (items, runs, deliveries, triggers) | SQLite schema | `todo` | Design in ARCHITECTURE; review before impl. |
| 2.2 | Content-hash dedup utility | 基于内容哈希的去重 | `todo` | SHA-256 over `title + body[:2048] + source + date`. |
| 2.3 | Ingestion: RSS (Reuters, Bloomberg, WSJ, FT, NYT biz, Nikkei, Caixin) | RSS 抓取 | `todo` | Respect robots/ToS; cache ETags. |
| 2.4 | Ingestion: Finnhub free tier (market news + deals) | Finnhub 抓取 | `todo` | 60 req/min cap. |
| 2.5 | Ingestion: FRED API (select macro series) | FRED 抓取 | `todo` | Daily series only. |
| 2.6 | Ingestion: SEC EDGAR (8-K / 13D / 425 filings) | SEC EDGAR 抓取 | `todo` | User-Agent header required. |
| 2.7 | Ingestion: GDELT event stream (geopolitical flags) | GDELT 抓取 | `todo` | Filter by event code + tone. |
| 2.8 | Classifier: track router (macro / NA+FX / deals) | 分类器: 信息轨路由 | `todo` | Cheap-LLM prompt; fallback to rules. |
| 2.9 | Delivery: Telegram bot sender | Telegram 推送 | `todo` | Markdown V2; escape rules. |
| 2.10 | Delivery: SMTP email sender | SMTP 邮件推送 | `todo` | HTML + plaintext multipart. |
| 2.11 | Delivery: Composite (primary + fallback + retry) | 复合推送器 (主备 + 重试) | `todo` | Abstraction lives in `src/delivery/notifier.py`. |
| 2.12 | GitHub Actions cron: daily 07:00 local | GitHub Actions 每日定时 | `todo` | UTC offset handled in job script. |
| 2.13 | Dry-run mode + local smoke test | Dry-run 与本地冒烟测试 | `todo` | `DRY_RUN=true` env var. |

### Phase 3+ / 第三阶段及以后

Planned collaboratively at Phase 2 close. Headline items:

- Reasoning engine + causal-chain template.
- Saturday weekly synthesis (cross-track, richer model).
- Event-triggered deep analysis (deal size, central-bank surprises,
  market > 2% moves).
- Evaluation harness (gold-label briefs, regression tests on prompts).

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
