# Changelog / 变更日志

All notable changes to this project will be documented in this file.
本文件记录所有值得留痕的变更。

Format adheres to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
version numbers follow [Semantic Versioning](https://semver.org/).
格式遵循 Keep a Changelog, 版本号遵循 SemVer。

## [Unreleased]

### Added / 新增
- _nothing yet / 暂无_

### Changed / 变更
- _nothing yet / 暂无_

### Fixed / 修复
- _nothing yet / 暂无_

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
