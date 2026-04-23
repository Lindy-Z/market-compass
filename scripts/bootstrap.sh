#!/usr/bin/env bash
# =============================================================================
# bootstrap.sh — one-time local setup for market-compass
# =============================================================================
# Run this on your own machine (macOS / Linux) after cloning or pulling the
# scaffolded files down. It is idempotent-safe for a fresh checkout and will
# refuse to run destructively against a populated repo.
#
# What it does / 本脚本会做:
#   1. Clean up a stuck .git/index.lock from the scaffold session (if present).
#   2. Re-init git on `main` with your user.name/user.email.
#   3. Install the pre-commit secret scanner.
#   4. Make 4 Conventional Commits in order (scaffold, hooks, root docs,
#      memory system) — each passes through the hook.
#   5. Tag v0.1.0.
#
# Requires / 需要:
#   - git ≥ 2.28 (for default branch config)
#   - bash
#
# Usage / 使用:
#   cd /path/to/market-compass
#   bash scripts/bootstrap.sh
#
# To create the GitHub remote after this script succeeds:
#   gh repo create market-compass --public --source=. --remote=origin --push --description="Daily macro & market intelligence, delivered with causal reasoning / 每日宏观与市场情报推送"
#   git push --tags
# =============================================================================

set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "${here}/.." && pwd)"
cd "${root}"

say() { printf '\033[0;36m[bootstrap]\033[0m %s\n' "$*"; }
die() { printf '\033[0;31m[bootstrap] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# -----------------------------------------------------------------------------
# Safety gate / 安全闸门
# -----------------------------------------------------------------------------
if [[ -d .git ]]; then
  tracked_count="$(git ls-files 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "${tracked_count}" != "0" ]]; then
    die "This looks like a populated repo (${tracked_count} tracked files).
       Bootstrap refuses to overwrite history.
       此仓库已有 ${tracked_count} 个受跟踪文件,为避免覆盖历史,已中止。
       If you really want to start over: rm -rf .git && re-run this script."
  fi
fi

# -----------------------------------------------------------------------------
# 1) Reset git dir / 重置 git 目录
# -----------------------------------------------------------------------------
say "Removing any stale .git and re-initializing..."
rm -rf .git
git init -b main >/dev/null

# Preserve user's global identity if present; otherwise fall back to a local one
if ! git config --get user.name >/dev/null 2>&1; then
  git config user.name  "Lindy"
fi
if ! git config --get user.email >/dev/null 2>&1; then
  git config user.email "vilniuslynn@gmail.com"
fi

# Clean up sandbox artifacts the scaffold session left behind
rm -f .hook_selftest.tmp .gitignore.tmp_test .git/TEST_CREATE

# -----------------------------------------------------------------------------
# 2) Install pre-commit secret scanner / 安装 pre-commit 密钥扫描
# -----------------------------------------------------------------------------
say "Installing pre-commit secret scanner..."
bash scripts/install-hooks.sh

# -----------------------------------------------------------------------------
# 3) Commits — four small Conventional Commits / 四个小型 Conventional Commits
# -----------------------------------------------------------------------------

commit1_paths=(
  .gitignore
  src
  tests
  .github
)
commit2_paths=(
  scripts/install-hooks.sh
  scripts/bootstrap.sh
)
commit3_paths=(
  README.md
  README.zh-CN.md
  LICENSE
  .env.example
)
commit4_paths=(
  docs
)

say "Commit 1/4 — chore: scaffold repo structure + .gitignore"
git add -- "${commit1_paths[@]}"
git commit -m "chore: scaffold repo structure + gitignore / 脚手架与 .gitignore" -m \
"- Full directory tree under src/, tests/, scripts/, .github/workflows/
- Aggressive .gitignore excluding .env*, keys, data, logs, caches

目录脚手架完成,.gitignore 激进屏蔽密钥、数据、日志与缓存。

Refs: WORKFLOW #1.1, #1.3"

say "Commit 2/4 — chore(hooks): add pre-commit secret scanner + bootstrap"
git add -- "${commit2_paths[@]}"
git commit -m "chore(hooks): add pre-commit secret scanner + bootstrap / 新增 pre-commit 密钥扫描与一键初始化脚本" -m \
"- scripts/install-hooks.sh installs .git/hooks/pre-commit
- scripts/bootstrap.sh performs one-time local setup
- Patterns: sk-ant-, sk-proj-, AKIA, Telegram bot tokens, PEM blocks

Pre-commit 钩子扫描 Anthropic/OpenAI/AWS/Telegram/PEM 常见模式。
bootstrap.sh 为 Lindy 的本地机器一次性初始化流水线。

Refs: WORKFLOW #1.4, ADR-0006"

say "Commit 3/4 — docs: add READMEs + LICENSE + .env.example"
git add -- "${commit3_paths[@]}"
git commit -m "docs: add READMEs + LICENSE + .env.example / 添加 README、许可证与 .env 模板" -m \
"- Bilingual README.md + Chinese-primary README.zh-CN.md
- MIT LICENSE (see ADR-0005; pending explicit confirm)
- .env.example with placeholders for LLM, Telegram, SMTP, Finnhub, FRED, EDGAR

双语 README;MIT 许可证;环境变量模板。

Refs: WORKFLOW #1.2, ADR-0004, ADR-0005"

say "Commit 4/4 — docs: seed memory system and update protocol"
git add -- "${commit4_paths[@]}"
git commit -m "docs: seed memory system and update-management protocol / 初始化记忆系统与更新管理流程" -m \
"- docs/DECISIONS.md — 8 ADRs seeded
- docs/WORKFLOW.md — canonical task list + branch / commit / PR protocol
- docs/CHANGELOG.md — Keep-a-Changelog with [0.1.0] entry
- docs/ARCHITECTURE.md — system design, data flow, storage schema draft
- docs/PROMPTS.md — versioned prompt template inventory
- docs/SESSION_LOG.md — per-session status report seed

记忆系统六件套完成,作为'下一步做什么'的权威来源。

Refs: WORKFLOW #1.5, #1.6"

# -----------------------------------------------------------------------------
# 4) Tag / 打标签
# -----------------------------------------------------------------------------
say "Tagging v0.1.0..."
git tag -a v0.1.0 -m "v0.1.0 — Phase 1 scaffold / 第一阶段脚手架"

# -----------------------------------------------------------------------------
# 5) Summary / 摘要
# -----------------------------------------------------------------------------
say "Done ✓"
echo
echo "--- git log (brief) ---"
git log --oneline --decorate
echo
echo "--- next step / 下一步 ---"
echo "Create the GitHub remote and push:"
echo
echo "  gh repo create market-compass --public \\"
echo "      --source=. --remote=origin --push \\"
echo "      --description=\"Daily macro & market intelligence, delivered with causal reasoning / 每日宏观与市场情报推送\""
echo "  git push --tags"
echo
echo "If you do not have 'gh' installed, create the repo in the GitHub UI"
echo "then run:"
echo "  git remote add origin git@github.com:<YOUR_USER>/market-compass.git"
echo "  git push -u origin main"
echo "  git push --tags"
