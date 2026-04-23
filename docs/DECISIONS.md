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
