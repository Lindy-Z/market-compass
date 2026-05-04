#!/usr/bin/env bash
# =============================================================================
# install-hooks.sh — install a pre-commit secret scanner
# =============================================================================
# Writes a custom pre-commit hook to .git/hooks/pre-commit that scans the
# staged diff for common secret patterns (Anthropic, OpenAI, AWS, Telegram,
# PEM private keys) and blocks commits that match.
#
# 自定义 pre-commit 钩子:扫描暂存区的常见密钥模式,命中即阻止提交。
#
# Usage / 使用:
#   bash scripts/install-hooks.sh
#
# To intentionally override (strongly discouraged):
#   git commit --no-verify -m "..."
# Record the reason in docs/DECISIONS.md.
# =============================================================================

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
hook_dst="${repo_root}/.git/hooks/pre-commit"

mkdir -p "$(dirname "${hook_dst}")"

cat > "${hook_dst}" <<'HOOK_EOF'
#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# market-compass pre-commit hook — secret scanner
# Auto-installed by scripts/install-hooks.sh.
# -----------------------------------------------------------------------------
#
# Scans the staged diff (-U0) against a conservative pattern set.
# Any hit blocks the commit and prints the offending file + pattern label.
#
# False positive? Narrow the diff or commit with `--no-verify` AND record
# the justification in docs/DECISIONS.md.
# -----------------------------------------------------------------------------

set -uo pipefail

RED=$'\033[0;31m'
YEL=$'\033[0;33m'
GRN=$'\033[0;32m'
NC=$'\033[0m'

fail=0

# --- 1) Block staged .env* (except .env.example) ---------------------------
staged_envs="$(git diff --cached --name-only --diff-filter=A 2>/dev/null \
  | grep -E '(^|/)\.env($|\.)' | grep -Ev '(^|/)\.env\.example$' || true)"
if [[ -n "${staged_envs}" ]]; then
  echo "${RED}✗ Blocked: attempt to commit .env file(s):${NC}"
  echo "${staged_envs}" | sed 's/^/    /'
  echo "  ${YEL}Only .env.example may be committed. Remove real env files.${NC}"
  echo "  ${YEL}仅 .env.example 可入库,真实 .env 文件请勿提交。${NC}"
  fail=1
fi

# --- 2) Block obvious private key files by name -----------------------------
staged_keys="$(git diff --cached --name-only --diff-filter=A 2>/dev/null \
  | grep -E '\.(key|pem|p12|pfx)$|(^|/)(credentials|service_account)[^/]*\.(json|ya?ml)$|(^|/)id_rsa($|\.)' || true)"
if [[ -n "${staged_keys}" ]]; then
  echo "${RED}✗ Blocked: attempt to commit key/credentials file(s):${NC}"
  echo "${staged_keys}" | sed 's/^/    /'
  echo "  ${YEL}密钥/凭据文件禁止入库。${NC}"
  fail=1
fi

# --- 3) Pattern scan over the staged diff ----------------------------------
#
# Architecture:
#   STRICT PATTERNS (Anthropic / OpenAI / AWS / Telegram / Google / FRED-URL
#   / Finnhub / PEM / Bearer / etc.) apply to ALL files including tests.
#   Real keys with these recognizable shapes are too dangerous to bypass
#   anywhere — even a "test" file with a real `sk-ant-...` key is a leak.
#
#   HIGH-ENTROPY HEURISTIC (`<secret-named-var> = "<long-literal>"`) applies
#   only to NON-TEST files. Test fixtures legitimately contain quoted
#   strings shaped like keys (e.g. `API_KEY = "test_fred_key_xyz123"`);
#   blocking them is friction without protection benefit. The strict
#   patterns still apply, so a real key shape would still be caught.
#
#   PRAGMA OVERRIDE: any line ending with `# noqa: secret` is skipped by
#   the heuristic. Use sparingly for the rare legitimate non-test fixture
#   that the exclusion list doesn't catch.
#
# 设计:
#   严格模式 (sk-ant-, AKIA, ...) 对所有文件生效,包括 tests/。
#   高熵启发式 (<secret>="..." 形式) 仅对非测试文件生效;测试夹具里的
#   "假 key" 是合法的, 拦它们只增加摩擦不增加安全。
#   行尾加 `# noqa: secret` 注释可逐行跳过启发式 (谨慎使用)。

# Get list of staged files, partition by category. The Layer 2 heuristic
# is skipped for two file classes:
#   - test fixtures (tests/, test/, **/tests/, **/spec/)
#   - documentation (docs/, **/docs/, *.md, *.markdown, *.rst, *.txt)
# The Layer 1 strict patterns still apply to ALL files, so a real
# sk-ant-... key accidentally pasted into a README still gets blocked.
# Code files (.py / .js / .sh / etc.) outside the above are scanned
# normally.
staged_all="$(git diff --cached --name-only --diff-filter=AMR 2>/dev/null || true)"
staged_non_test="$(echo "${staged_all}" \
  | grep -vE '(^|/)(tests?|spec|docs)(/|$)' \
  | grep -vE '\.(md|markdown|rst|txt)$' \
  || true)"

# Full diff (for strict patterns — applies to all files)
diff_all="$(git diff --cached -U0 --no-color 2>/dev/null || true)"
all_added="$(echo "${diff_all}" | grep -E '^\+' | grep -Ev '^\+\+\+' || true)"

# Diff scoped to non-test files only (for the fuzzy heuristic).
# NB: macOS ships bash 3.2 by default, where 'mapfile' is unavailable
# (it's bash 4+). We use a portable while-read-loop and an explicit
# empty-array declaration so 'set -u' is happy when the array is empty.
non_test_added=""
non_test_files_arr=()
if [[ -n "${staged_non_test}" ]]; then
  while IFS= read -r line; do
    if [[ -n "$line" ]]; then
      non_test_files_arr+=("$line")
    fi
  done <<< "${staged_non_test}"
fi
if [[ ${#non_test_files_arr[@]} -gt 0 ]]; then
  diff_nt="$(git diff --cached -U0 --no-color -- "${non_test_files_arr[@]}" 2>/dev/null || true)"
  non_test_added="$(echo "${diff_nt}" | grep -E '^\+' | grep -Ev '^\+\+\+' || true)"
fi

# Strip lines marked with the explicit override pragma.
all_added="$(echo "${all_added}" | grep -Ev '#[[:space:]]*noqa:[[:space:]]*secret' || true)"
non_test_added="$(echo "${non_test_added}" | grep -Ev '#[[:space:]]*noqa:[[:space:]]*secret' || true)"

# --- Strict patterns (apply to ALL files) ---
# Each entry: "LABEL|REGEX". Added FRED + Finnhub patterns post-incident
# (ADR-0013).
patterns=(
  "Anthropic API key|sk-ant-[A-Za-z0-9_-]{20,}"
  "OpenAI API key|sk-(proj-|live-)?[A-Za-z0-9_-]{30,}"
  "AWS access key ID|AKIA[0-9A-Z]{16}"
  "AWS session/secret-looking string|aws_secret_access_key[\"'[:space:]]*[=:][\"'[:space:]]*[A-Za-z0-9/+=]{30,}"
  "Telegram bot token|[0-9]{8,12}:[A-Za-z0-9_-]{30,}"
  "Google API key|AIza[0-9A-Za-z_-]{30,}"
  "PEM private key block|-----BEGIN [A-Z ]*PRIVATE KEY-----"
  "Generic bearer token|bearer[[:space:]]+[A-Za-z0-9._-]{20,}"
  "FRED API key in URL|[?&]api_key=[a-f0-9]{32}"
  "Finnhub-context token|[Ff][Ii][Nn][Nn][Hh][Uu][Bb][_a-zA-Z]*[[:space:]]*[=:][[:space:]]*[\"']?[a-z0-9]{20,}"
  "Finnhub header token|X-Finnhub-Token[\"'[:space:]]*[=:,][\"'[:space:]]*[a-z0-9]{20,}"
)

if [[ -n "${all_added}" ]]; then
  for entry in "${patterns[@]}"; do
    label="${entry%%|*}"
    regex="${entry#*|}"
    hits="$(echo "${all_added}" | grep -En -- "${regex}" || true)"
    if [[ -n "${hits}" ]]; then
      echo "${RED}✗ Blocked: pattern matched [${label}]${NC}"
      echo "${hits}" | head -n 5 | sed 's/^/    /'
      fail=1
    fi
  done
fi

# --- High-entropy heuristic (NON-TEST FILES ONLY) ---
# Fires when a line looks like `<secret-named-var> = "<20+-char-literal>"`.
# Requires a leading quote on the RHS so it doesn't match expressions like
# `api_key = os.environ.get(...)`.
#
# Exclusion list (case-insensitive) covers common placeholder / test
# fixture markers. The `test[_-][a-z0-9_-]*(key|...)` pattern accepts
# intermediate words, so `test_fred_key`, `test_anthropic_token`, etc.
# all match. `fixture` / `mock` / `stub` are also recognized as
# fixture markers when they appear anywhere in the line.
if [[ -n "${non_test_added}" ]]; then
  hits="$(echo "${non_test_added}" \
    | grep -E -i '^\+[^#]*\b(password|secret|token|api[_-]?key)\s*[:=]\s*["'"'"'][^[:space:]]{20,}' \
    | grep -Ev -i 'your_[a-z_]+_here|x{5,}|changeme|replace[_-]?me|example|\.\.\.|placeholder|dry[-_]run|not[-_]real|fake|dummy|fixture|\bmock\b|\bstub\b|test[_-][a-z0-9_-]*(key|token|value|placeholder|fixture|api|password|secret)|os\.environ|os\.getenv|getenv\(' || true)"
  if [[ -n "${hits}" ]]; then
    echo "${RED}✗ Blocked: high-entropy secret-like assignment${NC}"
    echo "${hits}" | head -n 5 | sed 's/^/    /'
    echo "  ${YEL}Looks like a real credential. Three escape hatches:${NC}"
    echo "  ${YEL}  1. Move to tests/ (heuristic skipped there).${NC}"
    echo "  ${YEL}  2. Add ' # noqa: secret' to the line.${NC}"
    echo "  ${YEL}  3. Rename / shorten so the value reads as a fixture.${NC}"
    echo "  ${YEL}若为误报,可移到 tests/、加 # noqa: secret 注释,或改名 / 缩短值。${NC}"
    fail=1
  fi
fi

if [[ "${fail}" -ne 0 ]]; then
  echo
  echo "${RED}pre-commit: SECRETS DETECTED — commit aborted.${NC}"
  echo "${RED}pre-commit: 发现疑似密钥,提交被拒绝。${NC}"
  echo "  To override in a true emergency: git commit --no-verify"
  echo "  紧急情况可用 --no-verify 绕过,但必须在 docs/DECISIONS.md 记录原因。"
  exit 1
fi

echo "${GRN}✓ pre-commit: secret scan clean${NC}"
exit 0
HOOK_EOF

chmod +x "${hook_dst}"

echo "✓ Installed pre-commit hook at .git/hooks/pre-commit"
echo "  Test it with:  git commit -m 'test'  (it runs automatically)"
echo "  中文: 钩子已安装,下次提交将自动触发密钥扫描。"
