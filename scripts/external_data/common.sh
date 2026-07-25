#!/usr/bin/env bash
set -euo pipefail
umask 027

readonly AIOA_PROJECT_ID="memory-patch-for-aioa"
readonly AIOA_EXPECTED_REMOTE="https://github.com/luciferprosun/Memory-Patch-for-AIOA-Hackathon-CockroachDB"
readonly AIOA_EXPECTED_BRANCH="main"
readonly AIOA_CREATION_HEAD="b3d555ec230a894b541e3570347fcf086511df2a"
readonly AIOA_EXTERNAL_RELATIVE_ROOT="AIOA_DATA/Memory-Patch-for-AIOA"
readonly AIOA_MARKER_NAME=".aioa-external-volume.json"
AIOA_COMMON_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly AIOA_COMMON_DIR
AIOA_REPO_ROOT="$(cd -- "${AIOA_COMMON_DIR}/../.." && pwd -P)"
readonly AIOA_REPO_ROOT
readonly AIOA_LOCAL_CONFIG="${AIOA_REPO_ROOT}/.local/external-data.env"

aioa_die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

aioa_pass() {
  printf 'PASS: %s\n' "$1"
}

aioa_warn() {
  printf 'WARN: %s\n' "$1" >&2
}

aioa_require_command() {
  command -v "$1" >/dev/null 2>&1 ||
    aioa_die "required command is unavailable: $1"
}

aioa_require_nonempty() {
  local variable_name="$1"
  local value="$2"
  [[ -n "${value}" ]] || aioa_die "${variable_name} must not be empty"
}

aioa_canonical_path() {
  python3 - "$1" <<'PY'
import os
import sys

print(os.path.realpath(os.path.abspath(sys.argv[1])))
PY
}

aioa_normalize_remote() {
  local remote="$1"
  remote="${remote%.git}"
  remote="${remote%/}"
  printf '%s\n' "${remote}"
}

aioa_path_is_within() {
  python3 - "$1" "$2" <<'PY'
import os
import sys

path = os.path.realpath(os.path.abspath(sys.argv[1]))
root = os.path.realpath(os.path.abspath(sys.argv[2]))
try:
    print("yes" if os.path.commonpath((path, root)) == root else "no")
except ValueError:
    print("no")
PY
}

aioa_repository_guard() {
  local actual_root remote branch git_dir marker

  aioa_require_command git
  actual_root="$(git -C "${AIOA_REPO_ROOT}" rev-parse --show-toplevel 2>/dev/null)" ||
    aioa_die "repository root cannot be resolved"
  actual_root="$(aioa_canonical_path "${actual_root}")"
  [[ "${actual_root}" == "${AIOA_REPO_ROOT}" ]] ||
    aioa_die "repository mismatch: ${actual_root}"

  remote="$(git -C "${AIOA_REPO_ROOT}" remote get-url origin 2>/dev/null)" ||
    aioa_die "origin remote is missing"
  [[ "$(aioa_normalize_remote "${remote}")" == "${AIOA_EXPECTED_REMOTE}" ]] ||
    aioa_die "origin mismatch: ${remote}"

  branch="$(git -C "${AIOA_REPO_ROOT}" branch --show-current)"
  [[ "${branch}" == "${AIOA_EXPECTED_BRANCH}" ]] ||
    aioa_die "branch mismatch: ${branch:-detached HEAD}"

  git_dir="$(git -C "${AIOA_REPO_ROOT}" rev-parse --git-dir)"
  if [[ "${git_dir}" != /* ]]; then
    git_dir="${AIOA_REPO_ROOT}/${git_dir}"
  fi
  for marker in \
    MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG \
    rebase-merge rebase-apply; do
    [[ ! -e "${git_dir}/${marker}" ]] ||
      aioa_die "Git operation is active: ${marker}"
  done
  [[ -z "$(git -C "${AIOA_REPO_ROOT}" ls-files -u)" ]] ||
    aioa_die "repository contains unmerged index entries"

  aioa_pass "repository identity, branch, and operation state"
}

aioa_load_local_config() {
  [[ -f "${AIOA_LOCAL_CONFIG}" && ! -L "${AIOA_LOCAL_CONFIG}" ]] ||
    aioa_die "missing regular local config: ${AIOA_LOCAL_CONFIG}"

  # shellcheck disable=SC1090
  source "${AIOA_LOCAL_CONFIG}"

  aioa_require_nonempty AIOA_EXTERNAL_MOUNTPOINT "${AIOA_EXTERNAL_MOUNTPOINT:-}"
  aioa_require_nonempty AIOA_EXTERNAL_DATA_ROOT "${AIOA_EXTERNAL_DATA_ROOT:-}"
  aioa_require_nonempty AIOA_EXTERNAL_DEVICE_UUID "${AIOA_EXTERNAL_DEVICE_UUID:-}"
  aioa_require_nonempty AIOA_EXTERNAL_DEVICE_LABEL "${AIOA_EXTERNAL_DEVICE_LABEL:-}"
  aioa_require_nonempty AIOA_EXTERNAL_FILESYSTEM_TYPE "${AIOA_EXTERNAL_FILESYSTEM_TYPE:-}"

  [[ "${AIOA_EXTERNAL_MOUNTPOINT}" == /* ]] ||
    aioa_die "mountpoint must be absolute"
  [[ "${AIOA_EXTERNAL_DATA_ROOT}" == /* ]] ||
    aioa_die "external data root must be absolute"

  local canonical_mount canonical_root expected_root
  canonical_mount="$(aioa_canonical_path "${AIOA_EXTERNAL_MOUNTPOINT}")"
  canonical_root="$(aioa_canonical_path "${AIOA_EXTERNAL_DATA_ROOT}")"
  expected_root="$(aioa_canonical_path \
    "${AIOA_EXTERNAL_MOUNTPOINT}/${AIOA_EXTERNAL_RELATIVE_ROOT}")"

  [[ "${canonical_mount}" != "/" ]] ||
    aioa_die "mountpoint must not be /"
  [[ "${canonical_root}" != "/" ]] ||
    aioa_die "external data root must not be /"
  [[ "${canonical_root}" != "${canonical_mount}" ]] ||
    aioa_die "external data root must not be the mountpoint root"
  [[ "${canonical_root}" == "${expected_root}" ]] ||
    aioa_die "external root must be exactly ${AIOA_EXTERNAL_RELATIVE_ROOT} below the mountpoint"

  AIOA_EXTERNAL_MOUNTPOINT="${canonical_mount}"
  AIOA_EXTERNAL_DATA_ROOT="${canonical_root}"
  export AIOA_EXTERNAL_MOUNTPOINT AIOA_EXTERNAL_DATA_ROOT
  export AIOA_EXTERNAL_DEVICE_UUID AIOA_EXTERNAL_DEVICE_LABEL
  export AIOA_EXTERNAL_FILESYSTEM_TYPE
}

aioa_mount_value() {
  local field="$1"
  local target="$2"
  findmnt --raw --noheadings --output "${field}" --target "${target}" |
    sed -n '1p'
}

aioa_current_device_uuid() {
  local source="$1"
  lsblk --noheadings --output UUID "${source}" 2>/dev/null |
    sed -n '1{s/^[[:space:]]*//;s/[[:space:]]*$//;p}'
}

aioa_current_device_label() {
  local source="$1"
  lsblk --noheadings --output LABEL "${source}" 2>/dev/null |
    sed -n '1{s/^[[:space:]]*//;s/[[:space:]]*$//;p}'
}

aioa_verify_mounted_volume() {
  local mount_target source filesystem options uuid label root_source

  for command_name in findmnt lsblk df stat sed python3; do
    aioa_require_command "${command_name}"
  done

  [[ -d "${AIOA_EXTERNAL_MOUNTPOINT}" && ! -L "${AIOA_EXTERNAL_MOUNTPOINT}" ]] ||
    aioa_die "mountpoint is missing, not a directory, or is a symlink: ${AIOA_EXTERNAL_MOUNTPOINT}"

  mount_target="$(aioa_mount_value TARGET "${AIOA_EXTERNAL_MOUNTPOINT}")"
  mount_target="$(aioa_canonical_path "${mount_target}")"
  [[ "${mount_target}" == "${AIOA_EXTERNAL_MOUNTPOINT}" ]] ||
    aioa_die "configured path is not the filesystem mountpoint: ${mount_target}"

  source="$(aioa_mount_value SOURCE "${AIOA_EXTERNAL_MOUNTPOINT}")"
  filesystem="$(aioa_mount_value FSTYPE "${AIOA_EXTERNAL_MOUNTPOINT}")"
  options="$(aioa_mount_value OPTIONS "${AIOA_EXTERNAL_MOUNTPOINT}")"

  aioa_require_nonempty DEVICE_PATH "${source}"
  [[ "${filesystem}" == "${AIOA_EXTERNAL_FILESYSTEM_TYPE}" ]] ||
    aioa_die "filesystem mismatch: expected ${AIOA_EXTERNAL_FILESYSTEM_TYPE}, found ${filesystem}"
  case ",${options}," in
    *,rw,*) ;;
    *) aioa_die "external volume is not mounted read/write: ${options}" ;;
  esac
  case ",${options}," in
    *,ro,*) aioa_die "external volume reports read-only mount option: ${options}" ;;
    *) ;;
  esac

  uuid="$(aioa_current_device_uuid "${source}")"
  label="$(aioa_current_device_label "${source}")"
  [[ "${uuid}" == "${AIOA_EXTERNAL_DEVICE_UUID}" ]] ||
    aioa_die "UUID mismatch: expected ${AIOA_EXTERNAL_DEVICE_UUID}, found ${uuid:-unknown}"
  [[ "${label}" == "${AIOA_EXTERNAL_DEVICE_LABEL}" ]] ||
    aioa_die "label mismatch: expected ${AIOA_EXTERNAL_DEVICE_LABEL}, found ${label:-unknown}"

  [[ -r "${AIOA_EXTERNAL_MOUNTPOINT}" &&
     -w "${AIOA_EXTERNAL_MOUNTPOINT}" &&
     -x "${AIOA_EXTERNAL_MOUNTPOINT}" ]] ||
    aioa_die "current user lacks read/write/search access to the mountpoint"

  if [[ -e "${AIOA_EXTERNAL_DATA_ROOT}" ]]; then
    [[ -d "${AIOA_EXTERNAL_DATA_ROOT}" && ! -L "${AIOA_EXTERNAL_DATA_ROOT}" ]] ||
      aioa_die "external data root exists but is not a regular directory"
    root_source="$(aioa_mount_value SOURCE "${AIOA_EXTERNAL_DATA_ROOT}")"
    [[ "${root_source}" == "${source}" ]] ||
      aioa_die "external data root is not on the selected filesystem"
  fi

  AIOA_VERIFIED_DEVICE_PATH="${source}"
  AIOA_VERIFIED_FILESYSTEM="${filesystem}"
  AIOA_VERIFIED_MOUNT_OPTIONS="${options}"
  AIOA_VERIFIED_AVAILABLE_BYTES="$(
    df -B1 --output=avail "${AIOA_EXTERNAL_MOUNTPOINT}" |
      sed -n '2{s/[[:space:]]//g;p}'
  )"
  AIOA_VERIFIED_TOTAL_BYTES="$(
    df -B1 --output=size "${AIOA_EXTERNAL_MOUNTPOINT}" |
      sed -n '2{s/[[:space:]]//g;p}'
  )"
  aioa_require_nonempty AVAILABLE_BYTES "${AIOA_VERIFIED_AVAILABLE_BYTES}"
  aioa_require_nonempty TOTAL_BYTES "${AIOA_VERIFIED_TOTAL_BYTES}"
  export AIOA_VERIFIED_DEVICE_PATH AIOA_VERIFIED_FILESYSTEM
  export AIOA_VERIFIED_MOUNT_OPTIONS AIOA_VERIFIED_AVAILABLE_BYTES
  export AIOA_VERIFIED_TOTAL_BYTES

  aioa_pass "mounted volume UUID, label, filesystem, access, and rw state"
}

aioa_marker_path() {
  printf '%s/%s\n' "${AIOA_EXTERNAL_DATA_ROOT}" "${AIOA_MARKER_NAME}"
}

aioa_verify_marker() {
  local marker source_at_root
  marker="$(aioa_marker_path)"
  [[ -f "${marker}" && ! -L "${marker}" ]] ||
    aioa_die "external marker is missing or is not a regular file: ${marker}"

  python3 - \
    "${marker}" \
    "${AIOA_PROJECT_ID}" \
    "${AIOA_EXTERNAL_DEVICE_UUID}" \
    "${AIOA_EXTERNAL_DEVICE_LABEL}" \
    "${AIOA_EXTERNAL_FILESYSTEM_TYPE}" \
    "${AIOA_EXPECTED_REMOTE}" \
    "${AIOA_CREATION_HEAD}" <<'PY'
import json
import sys

marker_path, project, uuid, label, filesystem, remote, creation_head = sys.argv[1:]
try:
    with open(marker_path, "r", encoding="utf-8") as handle:
        marker = json.load(handle)
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"ERROR: invalid external marker: {exc}")

expected = {
    "schema_version": "1.0.0",
    "project_id": project,
    "purpose": "external-data-volume",
    "device_uuid": uuid,
    "device_label": label,
    "filesystem_type": filesystem,
    "repository_remote": remote,
    "repository_head_at_creation": creation_head,
}
for key, value in expected.items():
    if marker.get(key) != value:
        raise SystemExit(
            f"ERROR: marker mismatch for {key}: "
            f"expected {value!r}, found {marker.get(key)!r}"
        )
if not isinstance(marker.get("created_at_utc"), str) or not marker["created_at_utc"]:
    raise SystemExit("ERROR: marker created_at_utc is missing")
PY

  source_at_root="$(aioa_mount_value SOURCE "${AIOA_EXTERNAL_DATA_ROOT}")"
  [[ "${source_at_root}" == "${AIOA_VERIFIED_DEVICE_PATH}" ]] ||
    aioa_die "marker root is not on the verified filesystem"
  aioa_pass "external marker identity and filesystem placement"
}

aioa_required_directories() {
  cat <<'EOF'
corpora
corpora/incoming
corpora/raw
corpora/normalized
corpora/rejected
corpora/manifests
embeddings
embeddings/active
embeddings/staging
embeddings/manifests
indexes
indexes/active
indexes/staging
indexes/manifests
ingestion
ingestion/downloads
ingestion/wheels
ingestion/source-archives
ingestion/build-cache
cache
cache/huggingface
cache/datasets
cache/transformers
cache/pip
cache/xdg
cache/temporary
snapshots
snapshots/application
snapshots/database-export
snapshots/manifests
backups
backups/repository-data
backups/migration-rollback
backups/manifests
migration
migration/journals
migration/inventories
migration/verification
migration/quarantine
logs
reports
EOF
}

aioa_verify_required_tree() {
  local relative_path absolute_path
  [[ -d "${AIOA_EXTERNAL_DATA_ROOT}" && ! -L "${AIOA_EXTERNAL_DATA_ROOT}" ]] ||
    aioa_die "external data root is missing or unsafe"
  while IFS= read -r relative_path; do
    absolute_path="${AIOA_EXTERNAL_DATA_ROOT}/${relative_path}"
    [[ -d "${absolute_path}" && ! -L "${absolute_path}" ]] ||
      aioa_die "required directory is missing or unsafe: ${relative_path}"
  done < <(aioa_required_directories)
  aioa_pass "required external directory tree"
}

aioa_assert_external_descendant() {
  local candidate="$1"
  local canonical
  aioa_require_nonempty external_path "${candidate}"
  canonical="$(aioa_canonical_path "${candidate}")"
  [[ "${canonical}" != "/" ]] || aioa_die "external path must not be /"
  [[ "${canonical}" != "${AIOA_EXTERNAL_MOUNTPOINT}" ]] ||
    aioa_die "external path must not be the mountpoint"
  [[ "${canonical}" != "${AIOA_EXTERNAL_DATA_ROOT}" ]] ||
    aioa_die "operation must not target the external data root itself"
  [[ "$(aioa_path_is_within "${canonical}" "${AIOA_EXTERNAL_DATA_ROOT}")" == "yes" ]] ||
    aioa_die "path escapes the external data root: ${candidate}"
}

aioa_safety_reserve_bytes() {
  local ten_percent minimum
  ten_percent=$((AIOA_VERIFIED_TOTAL_BYTES / 10))
  minimum=$((20 * 1024 * 1024 * 1024))
  if ((ten_percent > minimum)); then
    printf '%s\n' "${ten_percent}"
  else
    printf '%s\n' "${minimum}"
  fi
}
