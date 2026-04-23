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
# Use -U0 so only added/changed lines are considered.
diff_content="$(git diff --cached -U0 --no-color 2>/dev/null || true)"

# Keep pattern list below compact and labeled. Each entry: "LABEL|REGEX".
patterns=(
  "Anthropic API key|sk-ant-[A-Za-z0-9_-]{20,}"
  "OpenAI API key|sk-(proj-|live-)?[A-Za-z0-9_-]{30,}"
  "AWS access key ID|AKIA[0-9A-Z]{16}"
  "AWS session/secret-looking string|aws_secret_access_key[\"'[:space:]]*[=:][\"'[:space:]]*[A-Za-z0-9/+=]{30,}"
  "Telegram bot token|[0-9]{8,12}:[A-Za-z0-9_-]{30,}"
  "Google API key|AIza[0-9A-Za-z_-]{30,}"
  "PEM private key block|-----BEGIN [A-Z ]*PRIVATE KEY-----"
  "Generic bearer token|bearer[[:space:]]+[A-Za-z0-9._-]{20,}"
)

# Only look at added lines (those starting with '+'), skip diff headers.
added_lines="$(echo "${diff_content}" | grep -E '^\+' | grep -Ev '^\+\+\+' || true)"

if [[ -n "${added_lines}" ]]; then
  for entry in "${patterns[@]}"; do
    label="${entry%%|*}"
    regex="${entry#*|}"
    hits="$(echo "${added_lines}" | grep -En -- "${regex}" || true)"
    if [[ -n "${hits}" ]]; then
      echo "${RED}✗ Blocked: pattern matched [${label}]${NC}"
      echo "${hits}" | head -n 5 | sed 's/^/    /'
      fail=1
    fi
  done

  # Heuristic: label=...value pairs that look like secrets, not placeholders.
  # Catches things like `PASSWORD=actual_value_here_with_length`
  # but explicitly excludes `your_..._here`, `xxx`, `changeme`, empty, etc.
  hits="$(echo "${added_lines}" \
    | grep -E -i '^\+[^#]*\b(password|secret|token|api[_-]?key)\s*[:=]\s*[^[:space:]]{20,}' \
    | grep -Ev 'your_[a-z_]+_here|x{5,}|changeme|REPLACE_ME|example|\.\.\.|PLACEHOLDER' || true)"
  if [[ -n "${hits}" ]]; then
    echo "${RED}✗ Blocked: high-entropy secret-like assignment${NC}"
    echo "${hits}" | head -n 5 | sed 's/^/    /'
    echo "  ${YEL}Looks like a real credential. If it is not, rename the variable or shorten the value.${NC}"
    echo "  ${YEL}若为误报,请改名或缩短取值后再提交。${NC}"
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
