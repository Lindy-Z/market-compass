# Changelog / 变更日志

All notable changes to this project will be documented in this file.
本文件记录所有值得留痕的变更。

Format adheres to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
version numbers follow [Semantic Versioning](https://semver.org/).
格式遵循 Keep a Changelog, 版本号遵循 SemVer。

## [Unreleased]

### Added / 新增
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
