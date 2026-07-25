#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=scripts/external_data/common.sh
source "${SCRIPT_DIR}/common.sh"

mode="dry-run"
confirm_project=""
confirm_uuid=""

while (($# > 0)); do
  case "$1" in
    --dry-run)
      mode="dry-run"
      shift
      ;;
    --apply)
      mode="apply"
      shift
      ;;
    --confirm-project)
      (($# >= 2)) || aioa_die "--confirm-project requires a value"
      confirm_project="$2"
      shift 2
      ;;
    --confirm-uuid)
      (($# >= 2)) || aioa_die "--confirm-uuid requires a value"
      confirm_uuid="$2"
      shift 2
      ;;
    *)
      aioa_die "unknown argument: $1"
      ;;
  esac
done

aioa_repository_guard
aioa_load_local_config
aioa_verify_mounted_volume

if [[ "${mode}" == "apply" ]]; then
  [[ "${confirm_project}" == "${AIOA_PROJECT_ID}" ]] ||
    aioa_die "apply requires --confirm-project ${AIOA_PROJECT_ID}"
  [[ "${confirm_uuid}" == "${AIOA_EXTERNAL_DEVICE_UUID}" ]] ||
    aioa_die "apply requires the currently configured UUID"
fi

marker="$(aioa_marker_path)"
if [[ -e "${marker}" || -L "${marker}" ]]; then
  aioa_verify_marker
  marker_state="existing-valid"
else
  marker_state="create"
fi

print_directory_action() {
  local path="$1"
  if [[ -d "${path}" && ! -L "${path}" ]]; then
    printf 'EXISTS: %s\n' "${path}"
  elif [[ -e "${path}" || -L "${path}" ]]; then
    aioa_die "refusing to replace non-directory path: ${path}"
  else
    printf '%s: %s\n' "$([[ "${mode}" == "apply" ]] && printf CREATE || printf WOULD_CREATE)" "${path}"
  fi
}

external_container="${AIOA_EXTERNAL_MOUNTPOINT}/AIOA_DATA"
print_directory_action "${external_container}"
print_directory_action "${AIOA_EXTERNAL_DATA_ROOT}"
while IFS= read -r relative_path; do
  print_directory_action "${AIOA_EXTERNAL_DATA_ROOT}/${relative_path}"
done < <(aioa_required_directories)

if [[ "${marker_state}" == "existing-valid" ]]; then
  printf 'EXISTS_VALID: %s\n' "${marker}"
else
  printf '%s: %s\n' "$([[ "${mode}" == "apply" ]] && printf CREATE || printf WOULD_CREATE)" "${marker}"
fi

if [[ "${mode}" == "dry-run" ]]; then
  printf '%s\n' 'Dry run complete; no files or directories were created.'
  exit 0
fi

if [[ ! -e "${external_container}" ]]; then
  mkdir -m 0750 -- "${external_container}"
fi
[[ -d "${external_container}" && ! -L "${external_container}" ]] ||
  aioa_die "external project container is unsafe"
if [[ ! -e "${AIOA_EXTERNAL_DATA_ROOT}" ]]; then
  mkdir -m 0750 -- "${AIOA_EXTERNAL_DATA_ROOT}"
fi
[[ -d "${AIOA_EXTERNAL_DATA_ROOT}" && ! -L "${AIOA_EXTERNAL_DATA_ROOT}" ]] ||
  aioa_die "external root creation failed safely"

while IFS= read -r relative_path; do
  absolute_path="${AIOA_EXTERNAL_DATA_ROOT}/${relative_path}"
  if [[ ! -e "${absolute_path}" ]]; then
    mkdir -m 0750 -- "${absolute_path}"
  fi
  [[ -d "${absolute_path}" && ! -L "${absolute_path}" ]] ||
    aioa_die "unsafe path encountered after creation: ${absolute_path}"
done < <(aioa_required_directories)

if [[ "${marker_state}" == "create" ]]; then
  python3 - \
    "${marker}" \
    "${AIOA_PROJECT_ID}" \
    "${AIOA_EXTERNAL_DEVICE_UUID}" \
    "${AIOA_EXTERNAL_DEVICE_LABEL}" \
    "${AIOA_EXTERNAL_FILESYSTEM_TYPE}" \
    "${AIOA_EXPECTED_REMOTE}" \
    "${AIOA_CREATION_HEAD}" <<'PY'
import datetime
import json
import os
import sys
import tempfile

marker_path, project, uuid, label, filesystem, remote, repository_head = sys.argv[1:]
payload = {
    "schema_version": "1.0.0",
    "project_id": project,
    "purpose": "external-data-volume",
    "device_uuid": uuid,
    "device_label": label or None,
    "filesystem_type": filesystem,
    "created_at_utc": (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    ),
    "repository_remote": remote,
    "repository_head_at_creation": repository_head,
}
directory = os.path.dirname(marker_path)
descriptor, temporary_path = tempfile.mkstemp(
    prefix=".aioa-external-volume.", suffix=".tmp", dir=directory
)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary_path, 0o640)
    if os.path.lexists(marker_path):
        raise SystemExit("ERROR: marker appeared concurrently; refusing to overwrite it")
    os.link(temporary_path, marker_path)
finally:
    if os.path.exists(temporary_path):
        os.unlink(temporary_path)
PY
fi

aioa_verify_marker
aioa_verify_required_tree
printf '%s\n' 'External data volume preparation succeeded.'
