#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=scripts/external_data/common.sh
source "${SCRIPT_DIR}/common.sh"

require_marker=false
case "${1:-}" in
  "") ;;
  --require-marker) require_marker=true ;;
  *)
    printf 'Usage: %s [--require-marker]\n' "$0" >&2
    exit 2
    ;;
esac

for command_name in bash python3 git findmnt lsblk df stat readlink sed \
  cp mv ln sha256sum find; do
  aioa_require_command "${command_name}"
done
aioa_pass "required commands"

aioa_repository_guard
aioa_load_local_config
aioa_pass "machine-local external-storage configuration"
aioa_verify_mounted_volume

reserve_bytes="$(aioa_safety_reserve_bytes)"
((AIOA_VERIFIED_AVAILABLE_BYTES > reserve_bytes)) ||
  aioa_die "free space ${AIOA_VERIFIED_AVAILABLE_BYTES} does not exceed safety reserve ${reserve_bytes}"
aioa_pass "free space exceeds safety reserve (${reserve_bytes} bytes)"

marker="$(aioa_marker_path)"
if [[ -e "${marker}" || -L "${marker}" ]]; then
  aioa_verify_marker
elif [[ "${require_marker}" == true ]]; then
  aioa_die "external marker is required but absent: ${marker}"
else
  aioa_pass "marker is absent and may be initialized by prepare.sh"
fi

if [[ -e "${AIOA_EXTERNAL_DATA_ROOT}" ]]; then
  [[ -d "${AIOA_EXTERNAL_DATA_ROOT}" && ! -L "${AIOA_EXTERNAL_DATA_ROOT}" ]] ||
    aioa_die "external root is not a regular directory"
  [[ "$(aioa_path_is_within "${AIOA_EXTERNAL_DATA_ROOT}" "${AIOA_EXTERNAL_MOUNTPOINT}")" == "yes" ]] ||
    aioa_die "external root escapes the selected mount"
fi

if [[ -n "$(git -C "${AIOA_REPO_ROOT}" status --short)" ]]; then
  aioa_warn "repository has local changes; preflight permits this for toolkit validation"
else
  aioa_pass "repository worktree is clean"
fi

printf 'DEVICE_PATH=%s\n' "${AIOA_VERIFIED_DEVICE_PATH}"
printf 'DEVICE_UUID=%s\n' "${AIOA_EXTERNAL_DEVICE_UUID}"
printf 'DEVICE_LABEL=%s\n' "${AIOA_EXTERNAL_DEVICE_LABEL}"
printf 'FILESYSTEM_TYPE=%s\n' "${AIOA_VERIFIED_FILESYSTEM}"
printf 'MOUNTPOINT=%s\n' "${AIOA_EXTERNAL_MOUNTPOINT}"
printf 'MOUNT_OPTIONS=%s\n' "${AIOA_VERIFIED_MOUNT_OPTIONS}"
printf 'AVAILABLE_BYTES=%s\n' "${AIOA_VERIFIED_AVAILABLE_BYTES}"
printf 'EXTERNAL_DATA_ROOT=%s\n' "${AIOA_EXTERNAL_DATA_ROOT}"
printf '%s\n' 'Preflight succeeded.'
