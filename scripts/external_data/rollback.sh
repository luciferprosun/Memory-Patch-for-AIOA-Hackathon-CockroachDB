#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
readonly INVENTORY_TOOL="${SCRIPT_DIR}/inventory.py"
# shellcheck source=scripts/external_data/common.sh
source "${SCRIPT_DIR}/common.sh"

mode="dry-run"
confirm_project=""
confirm_uuid=""
migration_id=""

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
    --migration-id)
      (($# >= 2)) || aioa_die "--migration-id requires a value"
      migration_id="$2"
      shift 2
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

[[ "${migration_id}" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$ ]] ||
  aioa_die "a valid --migration-id is required"

aioa_repository_guard
aioa_load_local_config
aioa_verify_mounted_volume
aioa_verify_marker
aioa_verify_required_tree

if [[ "${mode}" == "apply" ]]; then
  [[ "${confirm_project}" == "${AIOA_PROJECT_ID}" ]] ||
    aioa_die "apply requires --confirm-project ${AIOA_PROJECT_ID}"
  [[ "${confirm_uuid}" == "${AIOA_EXTERNAL_DEVICE_UUID}" ]] ||
    aioa_die "apply requires --confirm-uuid with the current UUID"
fi

journal_directory="${AIOA_REPO_ROOT}/.local/external-data-journals/${migration_id}"
journal_path="${journal_directory}/journal.json"
[[ -d "${journal_directory}" && ! -L "${journal_directory}" ]] ||
  aioa_die "migration journal directory is missing or unsafe"
[[ -f "${journal_path}" && ! -L "${journal_path}" ]] ||
  aioa_die "migration journal is missing or unsafe"

mapfile -d '' -t journal_fields < <(
  python3 - "${journal_path}" "${migration_id}" <<'PY'
import json
import os
import sys

journal_path, expected_id = sys.argv[1:]
with open(journal_path, "r", encoding="utf-8") as handle:
    journal = json.load(handle)
if journal.get("schema_version") != "1.0.0":
    raise SystemExit("ERROR: unsupported journal schema")
if journal.get("migration_id") != expected_id:
    raise SystemExit("ERROR: migration ID does not match journal")
required = (
    "project_id",
    "external_uuid",
    "repository",
    "source_path",
    "destination_path",
    "rollback_path",
    "source_inventory",
    "rollback_inventory",
    "status",
)
for key in required:
    value = journal.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"ERROR: missing journal field: {key}")
    sys.stdout.buffer.write(value.encode("utf-8") + b"\0")
PY
)

((${#journal_fields[@]} == 9)) ||
  aioa_die "journal field validation failed"
project="${journal_fields[0]}"
external_uuid="${journal_fields[1]}"
repository="${journal_fields[2]}"
source_path="${journal_fields[3]}"
destination_path="${journal_fields[4]}"
rollback_path="${journal_fields[5]}"
source_inventory="${journal_fields[6]}"
rollback_inventory="${journal_fields[7]}"
journal_status="${journal_fields[8]}"

[[ "${project}" == "${AIOA_PROJECT_ID}" ]] ||
  aioa_die "journal project mismatch"
[[ "${external_uuid}" == "${AIOA_EXTERNAL_DEVICE_UUID}" ]] ||
  aioa_die "journal UUID mismatch"
[[ "$(aioa_canonical_path "${repository}")" == "${AIOA_REPO_ROOT}" ]] ||
  aioa_die "journal repository mismatch"
case "${journal_status}" in
  complete-hold-retained | complete | rolled-back) ;;
  *) aioa_die "journal status is not rollback-ready: ${journal_status}" ;;
esac

source_parent="$(aioa_canonical_path "$(dirname -- "${source_path}")")"
[[ "$(aioa_path_is_within "${source_parent}" "${AIOA_REPO_ROOT}")" == "yes" ]] ||
  aioa_die "journal source escapes repository"
aioa_assert_external_descendant "${destination_path}"
aioa_assert_external_descendant "${rollback_path}"

[[ -L "${source_path}" ]] ||
  aioa_die "application-facing source is not the expected symlink"
[[ "$(readlink -f -- "${source_path}")" == "$(aioa_canonical_path "${destination_path}")" ]] ||
  aioa_die "application-facing symlink target differs from journal"
[[ -d "${rollback_path}" && ! -L "${rollback_path}" ]] ||
  aioa_die "rollback copy is missing or unsafe"
[[ -f "${source_inventory}" && ! -L "${source_inventory}" ]] ||
  aioa_die "source inventory is missing"
[[ -f "${rollback_inventory}" && ! -L "${rollback_inventory}" ]] ||
  aioa_die "rollback inventory is missing"

rollback_check_directory="$(mktemp -d /tmp/aioa-rollback-verify.XXXXXX)"
fresh_rollback_inventory="${rollback_check_directory}/rollback.current.inventory.json"
cleanup_rollback_check() {
  if [[ -n "${rollback_check_directory:-}" &&
        "${rollback_check_directory}" == /tmp/aioa-rollback-verify.* &&
        -d "${rollback_check_directory}" &&
        ! -L "${rollback_check_directory}" ]]; then
    if [[ -f "${fresh_rollback_inventory:-}" &&
          ! -L "${fresh_rollback_inventory}" ]]; then
      unlink -- "${fresh_rollback_inventory}"
    fi
    rmdir -- "${rollback_check_directory}"
  fi
}
trap cleanup_rollback_check EXIT

python3 "${INVENTORY_TOOL}" create \
  "${rollback_path}" \
  --allowed-root "${rollback_path}" \
  --output "${fresh_rollback_inventory}"
python3 "${INVENTORY_TOOL}" compare \
  "${source_inventory}" "${fresh_rollback_inventory}"

restore_path="${source_path}.aioa-rollback-${migration_id}"
link_archive_root="${AIOA_REPO_ROOT}/.local/external-data-links"
link_archive_directory="${link_archive_root}/${migration_id}"
link_archive="${link_archive_directory}/application-link"
[[ ! -e "${restore_path}" && ! -L "${restore_path}" ]] ||
  aioa_die "temporary restore path already exists"
[[ ! -e "${link_archive}" && ! -L "${link_archive}" ]] ||
  aioa_die "archived link path already exists"

printf 'MIGRATION_ID=%s\n' "${migration_id}"
printf 'APPLICATION_PATH=%s\n' "${source_path}"
printf 'ROLLBACK_SOURCE=%s\n' "${rollback_path}"
printf 'EXTERNAL_FINAL_RETAINED=%s\n' "${destination_path}"

if [[ "${mode}" == "dry-run" ]]; then
  printf '%s\n' 'Rollback dry run succeeded; no path was changed.'
  exit 0
fi

mkdir -m 0750 -- "${restore_path}"
cp -a -- "${rollback_path}/." "${restore_path}/"
restore_inventory="${journal_directory}/restore.inventory.json"
python3 "${INVENTORY_TOOL}" create \
  "${restore_path}" \
  --allowed-root "${restore_path}" \
  --output "${restore_inventory}"
python3 "${INVENTORY_TOOL}" compare "${source_inventory}" "${restore_inventory}"

if [[ ! -e "${link_archive_root}" ]]; then
  mkdir -m 0700 -- "${link_archive_root}"
fi
[[ -d "${link_archive_root}" && ! -L "${link_archive_root}" ]] ||
  aioa_die "local archived-link root is unsafe"
mkdir -m 0700 -- "${link_archive_directory}"
mv -- "${source_path}" "${link_archive}"
if ! mv -- "${restore_path}" "${source_path}"; then
  mv -- "${link_archive}" "${source_path}"
  aioa_die "local restore promotion failed; application symlink restored"
fi

restored_inventory="${journal_directory}/restored.inventory.json"
python3 "${INVENTORY_TOOL}" create \
  "${source_path}" \
  --allowed-root "${source_path}" \
  --output "${restored_inventory}"
python3 "${INVENTORY_TOOL}" compare "${source_inventory}" "${restored_inventory}"

python3 - "${journal_path}" <<'PY'
import datetime
import json
import os
import sys

journal_path = sys.argv[1]
with open(journal_path, "r", encoding="utf-8") as handle:
    journal = json.load(handle)
now = (
    datetime.datetime.now(datetime.timezone.utc)
    .replace(microsecond=0)
    .isoformat()
    .replace("+00:00", "Z")
)
journal["status"] = "rolled-back"
journal.setdefault("events", []).append({"status": "rolled-back", "at_utc": now})
temporary = journal_path + ".tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(journal, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(temporary, 0o600)
os.replace(temporary, journal_path)
PY

printf '%s\n' \
  'Rollback succeeded; the external final copy and rollback copy were retained.'
