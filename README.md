# market-compass 🧭

> **Daily macro & market intelligence, delivered with causal reasoning.**
> **每日宏观与市场情报推送,附带因果链推理。**

A personal research system that fetches, filters, and reasons over global
macro news, North American markets, major FX, and headline deals — then
delivers a bilingual morning brief (中英双语) with chain-of-causation analysis.

> 🇨🇳 [中文为主版本见 README.zh-CN.md](./README.zh-CN.md)

---

## What it does / 功能概述

Three information tracks, one reasoning engine:

1. **Global Macro** — geopolitical events, central bank actions, supply
   shocks, cross-border capital flows.
   **全球宏观** — 地缘政治、央行动作、供给冲击、跨境资本流动。
2. **NA Markets + Major FX** — directional signals for SPX, UST yields,
   DXY, EUR/USD, USD/JPY, USD/CNY, gold.
   **北美市场与主要外汇** — 标普、美债收益率、美元指数、欧元/美元、美元/日元、美元/人民币、黄金的方向性信号。
3. **Headline Deals** — M&A ≥ $5B, major policy shifts, tech/AI structural
   events.
   **重磅交易与大新闻** — 50 亿美元以上并购、重大政策转变、科技与 AI 结构性事件。

### Deliverables / 产出

- **7am daily push** — bilingual summaries (~100 words EN / ~100 字 CN per item)
  with causal chains: `事件 → 一阶机制 → 资产反应 → 二阶效应 → 跨市场传导`.
- **Saturday weekly synthesis** — cross-track integration + a ready-to-feed
  prompt template for deeper analysis with a frontier model.
- **Event-triggered deep dive** — fires on deal size, central-bank
  surprises, geopolitical flags, or market moves > 2%.

---

## Design constraints / 设计约束

| Constraint | Target | 约束 | 目标 |
| --- | --- | --- | --- |
| Monthly cost | **≤ $20 USD** | 月度成本 | ≤ 20 美元 |
| News sources | $0 (RSS, Finnhub free, FRED, SEC EDGAR, GDELT) | 新闻源 | 全部免费 |
| Hosting | GitHub Actions cron (free) | 托管 | GitHub Actions 定时任务 |
| LLM | Tiered — cheap for extraction, strong for synthesis | LLM | 分级使用,便宜模型做抽取,强模型做综合与触发分析 |
| Secrets | Zero in git history. Ever. | 密钥 | 永不入库 |

---

## Tech stack / 技术栈

- **Python 3.11+** (pandas, httpx, pydantic, feedparser)
- **SQLite** for archive + content-hash dedup; **Parquet** optional for
  time-series columns
- **Telegram Bot** (primary) + **SMTP email** (redundant fallback)
- **GitHub Actions cron** for scheduling
- **LLM orchestration** via direct API calls (no heavy framework)

---

## Repository layout / 目录结构

```text
market-compass/
├── README.md                # bilingual overview (this file)
├── README.zh-CN.md          # Chinese-primary version
├── LICENSE                  # MIT
├── .gitignore               # aggressive secret & data hygiene
├── .env.example             # placeholder env vars (real values NEVER committed)
├── docs/
│   ├── ARCHITECTURE.md      # system design, bilingual
│   ├── DECISIONS.md         # ADR-style decision log
│   ├── WORKFLOW.md          # canonical task list + update protocol
│   ├── CHANGELOG.md         # Keep-a-Changelog format
│   ├── PROMPTS.md           # versioned LLM prompt templates
│   └── SESSION_LOG.md       # per-session status reports
├── src/
│   ├── ingestion/           # RSS + API polling
│   ├── processing/          # dedup, classification, routing
│   ├── reasoning/           # LLM orchestration, causal chain builder
│   ├── delivery/            # Telegram + email
│   └── storage/             # SQLite schema + archive
├── tests/
├── scripts/                 # one-off utilities, git hooks installer
└── .github/workflows/       # cron jobs, CI
```

---

## Quickstart / 快速开始

> ⚠️ Phase 1 is scaffolding only. Ingestion, reasoning, and delivery
> come in Phase 2.
> ⚠️ 当前处于第一阶段(脚手架),第二阶段将加入新闻抓取、推理与推送。

```bash
# 1) Clone
git clone https://github.com/<your-user>/market-compass.git
cd market-compass

# 2) Install git hooks (secret scanner) / 安装 pre-commit 密钥扫描
bash scripts/install-hooks.sh

# 3) Copy env template and fill in your keys LOCALLY / 本地复制环境变量模板并填入密钥
cp .env.example .env
# edit .env — DO NOT COMMIT IT / 编辑 .env,绝对不要提交

# 4) Create a virtualenv / 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
# pip install -r requirements.txt   # coming in Phase 2 / 第二阶段添加
```

---

## Security posture / 安全立场

- `.gitignore` aggressively excludes `.env*`, `*.key`, `*.pem`, `data/`,
  `*.db`, `logs/`, caches, and OS/IDE artifacts.
- A pre-commit hook scans staged diffs for common secret patterns
  (`sk-ant-`, `sk-proj-`, AWS `AKIA`, Telegram bot tokens, PEM blocks).
- `.env.example` is the ONLY `.env*` file that ships. Real values live
  outside git.
- If you ever suspect a leak: **rotate the key first, clean git history
  second.** 先轮换密钥,再清理历史。

---

## Status / 当前状态

See [`docs/WORKFLOW.md`](./docs/WORKFLOW.md) for the canonical task list
and [`docs/CHANGELOG.md`](./docs/CHANGELOG.md) for release history.

`docs/WORKFLOW.md` 是唯一的任务清单来源,`docs/CHANGELOG.md` 记录版本变更。

---

## License / 许可证

MIT — see [`LICENSE`](./LICENSE).
