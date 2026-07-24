#!/usr/bin/env bash
set -euo pipefail

AIOA_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
AIOA_LOCAL_BIN="${HOME}/.local/bin"
AIOA_FAILURES=0

pass() {
  printf 'PASS: %s\n' "$1"
}

warn() {
  printf 'WARN: %s\n' "$1" >&2
}

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  AIOA_FAILURES=$((AIOA_FAILURES + 1))
}

require_command() {
  local command_name="$1"
  if command -v "${command_name}" >/dev/null 2>&1; then
    pass "${command_name} is available at $(command -v "${command_name}")"
  else
    fail "${command_name} is not available"
  fi
}

verify_local_command() {
  local command_name="$1"
  local expected_path="${AIOA_LOCAL_BIN}/${command_name}"
  local resolved_path

  if ! resolved_path="$(command -v "${command_name}" 2>/dev/null)"; then
    fail "${command_name} is not available"
    return
  fi

  if [[ "$(readlink -f -- "${resolved_path}")" != "$(readlink -f -- "${expected_path}")" ]]; then
    fail "${command_name} resolves to ${resolved_path}, expected ${expected_path}"
    return
  fi

  pass "${command_name} resolves to ${expected_path}"
}

export PATH="${AIOA_LOCAL_BIN}:${PATH}"

printf 'AIOA Memory Patch toolchain verification\n'
printf 'Repository: %s\n' "${AIOA_REPO_ROOT}"
printf 'Architecture: %s\n' "$(uname -m)"

for command_name in git gh curl unzip xz tar jq gpg less make python3 strings sha256sum; do
  require_command "${command_name}"
done

for command_name in uv uvx aws ccloud cockroach-sql; do
  verify_local_command "${command_name}"
done

if [[ "$(uv --version 2>/dev/null || true)" == uv\ 0.11.31* ]]; then
  pass "$(uv --version)"
else
  fail "uv 0.11.31 is required"
fi

if [[ "$(uvx --version 2>/dev/null || true)" == uvx\ 0.11.31* ]]; then
  pass "$(uvx --version)"
else
  fail "uvx 0.11.31 is required"
fi

if AIOA_PYTHON_PATH="$(uv python find 3.12 2>/dev/null)" &&
  AIOA_PYTHON_VERSION="$(uv run --python 3.12 python --version 2>&1)" &&
  [[ "${AIOA_PYTHON_VERSION}" == Python\ 3.12.* ]]; then
  pass "${AIOA_PYTHON_VERSION} at ${AIOA_PYTHON_PATH}"
else
  fail "a usable CPython 3.12 is required"
fi

AIOA_AWS_VERSION="$(aws --version 2>&1 || true)"
if [[ "${AIOA_AWS_VERSION}" == aws-cli/2.* ]]; then
  pass "${AIOA_AWS_VERSION}"
else
  fail "AWS CLI major version 2 is required"
fi

# `ccloud version` retrieves remote client configuration before printing its
# version. To keep verification strictly offline, inspect the pinned binary.
if strings "${AIOA_LOCAL_BIN}/ccloud" | grep -Fx '0.6.12' >/dev/null; then
  pass "ccloud embedded version is 0.6.12 (offline verification)"
else
  fail "ccloud 0.6.12 is required"
fi

if [[ "$(sha256sum "${AIOA_LOCAL_BIN}/ccloud" | cut -d' ' -f1)" == \
  "944c7a35f9fe6b166dea991040399ac4e1cf0c754d0514fd57d7c4333c5d4cb2" ]]; then
  pass "ccloud binary SHA-256 matches the pinned 0.6.12 artifact"
else
  fail "ccloud binary SHA-256 does not match the pinned 0.6.12 artifact"
fi

AIOA_SQL_VERSION="$(cockroach-sql --version 2>&1 || true)"
if grep -Eq '^Build Tag:[[:space:]]+v26\.2\.3$' <<<"${AIOA_SQL_VERSION}"; then
  pass "cockroach-sql build tag is v26.2.3"
else
  fail "cockroach-sql build tag v26.2.3 is required"
fi

printf '%s\n' "${AIOA_SQL_VERSION}"
gh --version | sed -n '1p'
AIOA_GH_AUTH_OUTPUT="$(gh auth status 2>&1 || true)"
if grep -Fq 'Logged in to github.com account luciferprosun' <<<"${AIOA_GH_AUTH_OUTPUT}" &&
  ! grep -Fq 'Failed to log in' <<<"${AIOA_GH_AUTH_OUTPUT}"; then
  pass "GitHub CLI authentication is valid"
else
  warn "GitHub CLI authentication is not valid; manual login is deferred"
fi

AIOA_REQUIRED_FILES=(
  ".gitignore"
  "docs/operations/LOCAL_TOOLCHAIN_BOOTSTRAP_1A.md"
  "docs/architecture/PROJECT_BOUNDARY.md"
  "scripts/bootstrap_toolchain.sh"
  "scripts/verify_toolchain.sh"
  "tooling/versions.env"
)

for relative_path in "${AIOA_REQUIRED_FILES[@]}"; do
  if [[ -f "${AIOA_REPO_ROOT}/${relative_path}" ]]; then
    pass "required repository file exists: ${relative_path}"
  else
    fail "required repository file is missing: ${relative_path}"
  fi
done

printf 'No AWS or CockroachDB Cloud API command was executed by this verifier.\n'

if ((AIOA_FAILURES > 0)); then
  printf 'Verification failed with %d mismatch(es).\n' "${AIOA_FAILURES}" >&2
  exit 1
fi

printf 'Toolchain verification succeeded.\n'
