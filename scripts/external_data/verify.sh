#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=scripts/external_data/common.sh
source "${SCRIPT_DIR}/common.sh"

aioa_repository_guard
aioa_load_local_config
aioa_verify_mounted_volume
aioa_verify_marker
aioa_verify_required_tree

marker="$(aioa_marker_path)"
python3 -m json.tool "${marker}" >/dev/null
aioa_pass "marker is valid JSON"

tracked_local_state="$(git -C "${AIOA_REPO_ROOT}" ls-files -- \
  ".local" "*.inventory.json" "*.journal.json")"
[[ -z "${tracked_local_state}" ]] ||
  aioa_die "machine-local state is tracked by Git"
aioa_pass "machine-local configuration, journals, and inventories are untracked"

printf 'DEVICE_PATH=%s\n' "${AIOA_VERIFIED_DEVICE_PATH}"
printf 'DEVICE_UUID=%s\n' "${AIOA_EXTERNAL_DEVICE_UUID}"
printf 'FILESYSTEM=%s\n' "${AIOA_VERIFIED_FILESYSTEM}"
printf 'MOUNT_OPTIONS=%s\n' "${AIOA_VERIFIED_MOUNT_OPTIONS}"
printf 'AVAILABLE_BYTES=%s\n' "${AIOA_VERIFIED_AVAILABLE_BYTES}"
printf '%s\n' 'External data volume verification succeeded.'
