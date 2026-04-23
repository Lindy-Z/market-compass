# market-compass 🧭

> **每日宏观与市场情报推送,附带因果链推理。**
> **Daily macro & market intelligence, delivered with causal reasoning.**

一个个人研究系统: 抓取、过滤、对全球宏观新闻、北美市场、主要外汇和重磅交易进行推理,每日晨间推送一份中英双语简报,并给出因果链分析。

> 🇬🇧 [English-primary version: README.md](./README.md)

---

## 功能概述 / What it does

三条信息轨 + 一个推理引擎:

1. **全球宏观 (主轨)** — 地缘政治、央行动作、供给冲击、跨境资本流动。
2. **北美市场与主要外汇** — 标普 500、美债收益率、美元指数、EUR/USD、USD/JPY、USD/CNY、黄金的方向性信号 (方向足矣,数字是加分项)。
3. **重磅交易与大新闻** — 50 亿美元以上并购、重大政策转变、科技与 AI 结构性事件。

### 产出物 / Deliverables

- **每日 7 点推送** — 每条消息约 100 词 EN + 约 100 字 CN,附带因果链: `事件 → 一阶机制 → 资产反应 → 二阶效应 → 跨市场传导`。
- **周六周度综合** — 跨轨整合,附带可直接喂给前沿模型的深度分析 prompt 模板。
- **事件触发深度分析** — 在交易规模、央行意外、地缘旗标、或市场波动 > 2% 时自动触发。

---

## 设计约束 / Design constraints

| 约束 | 目标 |
| --- | --- |
| 月度成本 | **≤ 20 美元** |
| 新闻源 | 全部免费 (RSS、Finnhub 免费档、FRED、SEC EDGAR、GDELT) |
| 托管 | GitHub Actions 定时任务 (免费) |
| LLM | 分级使用 — 便宜模型做抽取,强模型做综合与触发分析 |
| 密钥 | 永远不进入 git 历史 |

---

## 技术栈 / Tech stack

- **Python 3.11+** (pandas、httpx、pydantic、feedparser)
- **SQLite** 做归档与基于内容哈希的去重; **Parquet** 可选,用于时序列
- **Telegram Bot** (主) + **SMTP Email** (冗余兜底)
- **GitHub Actions cron** 定时调度
- **LLM 编排**通过直接 API 调用,不引入重型框架

---

## 目录结构 / Layout

```text
market-compass/
├── README.md                # 英文为主的总览
├── README.zh-CN.md          # 本文件,中文为主
├── LICENSE                  # MIT
├── .gitignore               # 激进的密钥与数据隔离
├── .env.example             # 环境变量占位模板 (真实值绝不入库)
├── docs/
│   ├── ARCHITECTURE.md      # 系统设计 (双语)
│   ├── DECISIONS.md         # ADR 风格的决策记录
│   ├── WORKFLOW.md          # 唯一的任务清单 + 更新流程
│   ├── CHANGELOG.md         # Keep-a-Changelog 格式
│   ├── PROMPTS.md           # 版本化的 LLM prompt 模板
│   └── SESSION_LOG.md       # 每次会话的状态报告
├── src/
│   ├── ingestion/           # RSS + API 轮询
│   ├── processing/          # 去重、分类、路由
│   ├── reasoning/           # LLM 编排与因果链生成
│   ├── delivery/            # Telegram + Email 推送
│   └── storage/             # SQLite schema 与归档
├── tests/
├── scripts/                 # 一次性工具脚本、git hooks 安装器
└── .github/workflows/       # 定时任务与 CI
```

---

## 快速开始 / Quickstart

> ⚠️ 当前处于**第一阶段 (脚手架)**,第二阶段将加入新闻抓取、推理引擎与推送模块。

```bash
# 1) 克隆
git clone https://github.com/<your-user>/market-compass.git
cd market-compass

# 2) 安装 pre-commit 密钥扫描
bash scripts/install-hooks.sh

# 3) 本地复制 env 模板,填入真实密钥 (绝不提交 .env)
cp .env.example .env

# 4) 创建 Python 虚拟环境
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
# pip install -r requirements.txt   # 第二阶段添加
```

---

## 安全立场 / Security posture

- `.gitignore` 激进屏蔽 `.env*`、`*.key`、`*.pem`、`data/`、`*.db`、`logs/`、缓存、以及操作系统与 IDE 产物。
- Pre-commit hook 扫描暂存区的常见密钥模式 (`sk-ant-`、`sk-proj-`、AWS `AKIA`、Telegram bot token、PEM 段)。
- `.env.example` 是唯一会随仓库分发的 `.env*` 文件,真实配置保留在本地。
- 如果怀疑泄漏: **先轮换密钥,再清理 git 历史** (Rotate first, clean history second.)

---

## 状态 / Status

查看 [`docs/WORKFLOW.md`](./docs/WORKFLOW.md) 获取权威任务清单,[`docs/CHANGELOG.md`](./docs/CHANGELOG.md) 获取变更历史。

---

## 许可证 / License

MIT — 见 [`LICENSE`](./LICENSE)。
