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
requested_candidate=""

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
    --candidate)
      (($# >= 2)) || aioa_die "--candidate requires a repository-relative path"
      requested_candidate="$2"
      shift 2
      ;;
    *)
      aioa_die "unknown argument: $1"
      ;;
  esac
done

readonly -a CANDIDATE_ROWS=(
  "data/corpora|corpora/raw/repository-data-corpora"
  "data/embeddings|embeddings/active/repository-data-embeddings"
  "data/indexes|indexes/active/repository-data-indexes"
  "data/downloads|ingestion/downloads/repository-data-downloads"
  "data/cache|cache/temporary/repository-data-cache"
  "var/cache|cache/temporary/repository-var-cache"
  "var/snapshots|snapshots/application/repository-var-snapshots"
  "var/backups|backups/repository-data/repository-var-backups"
  "artifacts/downloads|ingestion/downloads/repository-artifacts-downloads"
)

aioa_repository_guard
aioa_load_local_config
aioa_verify_mounted_volume
aioa_verify_marker
aioa_verify_required_tree
[[ -x "${INVENTORY_TOOL}" || -f "${INVENTORY_TOOL}" ]] ||
  aioa_die "inventory tool is missing: ${INVENTORY_TOOL}"

if [[ "${mode}" == "apply" ]]; then
  [[ "${confirm_project}" == "${AIOA_PROJECT_ID}" ]] ||
    aioa_die "apply requires --confirm-project ${AIOA_PROJECT_ID}"
  [[ "${confirm_uuid}" == "${AIOA_EXTERNAL_DEVICE_UUID}" ]] ||
    aioa_die "apply requires --confirm-uuid with the current UUID"
  [[ -n "${requested_candidate}" ]] ||
    aioa_die "apply requires one explicit --candidate"
fi

case "${requested_candidate}" in
  /*) aioa_die "--candidate must be repository-relative" ;;
  *..*) aioa_die "--candidate must not contain '..'" ;;
  *) ;;
esac

candidate_destination() {
  local requested="$1"
  local row relative destination
  for row in "${CANDIDATE_ROWS[@]}"; do
    IFS='|' read -r relative destination <<<"${row}"
    if [[ "${relative}" == "${requested}" ]]; then
      printf '%s\n' "${destination}"
      return 0
    fi
  done
  return 1
}

temporary_directory=""
cleanup_temporary_inventory() {
  if [[ -n "${temporary_directory}" &&
        "${temporary_directory}" == /tmp/aioa-external-inventory.* &&
        -d "${temporary_directory}" &&
        ! -L "${temporary_directory}" ]]; then
    if [[ -f "${temporary_directory}/inventory.json" &&
          ! -L "${temporary_directory}/inventory.json" ]]; then
      unlink -- "${temporary_directory}/inventory.json"
    fi
    rmdir -- "${temporary_directory}"
  fi
  temporary_directory=""
}
trap cleanup_temporary_inventory EXIT

inspect_candidate() {
  local relative="$1"
  local destination_relative="$2"
  local source canonical_source tracked inventory_path
  local current_uid unexpected_owner
  local summary_fields files directories symlinks special escaping
  local apparent_bytes allocated_bytes reserve required available

  source="${AIOA_REPO_ROOT}/${relative}"
  printf '\nCANDIDATE: %s\n' "${relative}"

  if [[ ! -e "${source}" && ! -L "${source}" ]]; then
    printf '%s\n' 'RESULT: not present'
    return 3
  fi
  if [[ -L "${source}" || ! -d "${source}" ]]; then
    printf '%s\n' 'RESULT: rejected — not a regular directory'
    return 4
  fi

  canonical_source="$(aioa_canonical_path "${source}")"
  if [[ "${canonical_source}" == "${AIOA_REPO_ROOT}" ||
        "$(aioa_path_is_within "${canonical_source}" "${AIOA_REPO_ROOT}")" != "yes" ]]; then
    printf '%s\n' 'RESULT: rejected — source escapes or equals repository root'
    return 4
  fi

  case "${relative}" in
    .git|.git/*|.github|.github/*|src|src/*|tests|tests/*|docs|docs/*|scripts|scripts/*|config|config/*|.venv|.venv/*|venv|venv/*|node_modules|node_modules/*)
      printf '%s\n' 'RESULT: rejected — excluded source/configuration/runtime path'
      return 4
      ;;
  esac

  tracked="$(git -C "${AIOA_REPO_ROOT}" ls-files -- "${relative}")"
  if [[ -n "${tracked}" ]]; then
    printf 'TRACKED_FILES: %s\n' "$(printf '%s\n' "${tracked}" | wc -l)"
    printf '%s\n' 'RESULT: rejected — contains Git-tracked paths'
    return 4
  fi
  printf '%s\n' 'TRACKED_FILES: 0'

  if ! git -C "${AIOA_REPO_ROOT}" check-ignore -q -- "${relative}"; then
    printf '%s\n' 'RESULT: rejected — path is not ignored; a local symlink could be committed'
    return 4
  fi

  current_uid="$(id -u)"
  unexpected_owner="$(
    find "${canonical_source}" -xdev ! -uid "${current_uid}" -print -quit
  )"
  if [[ -n "${unexpected_owner}" ]]; then
    printf '%s\n' 'RESULT: rejected — candidate contains paths owned by another user'
    return 4
  fi

  if find "${canonical_source}" -xdev \
    \( \
      -name '*.py' -o -name '*.pyi' -o -name '*.sh' -o \
      -name '*.toml' -o -name '*.yaml' -o -name '*.yml' -o \
      -name '*.ini' -o -name '*.cfg' -o -name 'pyproject.toml' -o \
      -name 'requirements*.txt' -o -name '*.lock' -o -name '.env' -o \
      -name 'credentials*' -o -name '*.pem' -o -name '*.key' -o \
      -name 'cockroach-data' -o -name 'cockroach-data-*' -o -name '*.sst' \
    \) -print -quit | grep -q .; then
    printf '%s\n' \
      'RESULT: rejected — candidate contains source, configuration, secret-like, or database-runtime paths'
    return 4
  fi
  if find "${canonical_source}" -xdev -type f -perm /111 -print -quit |
    grep -q .; then
    printf '%s\n' 'RESULT: rejected — candidate contains executable files'
    return 4
  fi

  cleanup_temporary_inventory
  temporary_directory="$(mktemp -d /tmp/aioa-external-inventory.XXXXXX)"
  inventory_path="${temporary_directory}/inventory.json"
  if ! python3 "${INVENTORY_TOOL}" create \
    "${canonical_source}" \
    --allowed-root "${canonical_source}" \
    --output "${inventory_path}"; then
    printf '%s\n' 'RESULT: rejected — deterministic inventory failed'
    return 4
  fi

  mapfile -t summary_fields < <(
    python3 - "${inventory_path}" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    summary = json.load(handle)["summary"]
for key in (
    "files",
    "directories",
    "symlinks",
    "special",
    "escaping_symlinks",
    "apparent_bytes",
    "allocated_bytes",
):
    print(summary[key])
PY
  )
  files="${summary_fields[0]}"
  directories="${summary_fields[1]}"
  symlinks="${summary_fields[2]}"
  special="${summary_fields[3]}"
  escaping="${summary_fields[4]}"
  apparent_bytes="${summary_fields[5]}"
  allocated_bytes="${summary_fields[6]}"

  printf 'FILES=%s DIRECTORIES=%s SYMLINKS=%s\n' \
    "${files}" "${directories}" "${symlinks}"
  printf 'APPARENT_BYTES=%s ALLOCATED_BYTES=%s\n' \
    "${apparent_bytes}" "${allocated_bytes}"

  if ((special > 0)); then
    printf 'RESULT: rejected — inventory contains %s special file(s)\n' "${special}"
    return 4
  fi
  if ((escaping > 0)); then
    printf 'RESULT: rejected — inventory contains %s escaping symlink(s)\n' "${escaping}"
    return 4
  fi

  if ! command -v lsof >/dev/null 2>&1; then
    printf '%s\n' 'RESULT: rejected — active-use check unavailable (lsof missing)'
    return 4
  fi
  if lsof +D "${canonical_source}" 2>/dev/null | sed -n '2p' | grep -q .; then
    printf '%s\n' 'RESULT: rejected — candidate has open files'
    return 4
  fi

  reserve="$(aioa_safety_reserve_bytes)"
  required=$((allocated_bytes * 3 + reserve))
  available="${AIOA_VERIFIED_AVAILABLE_BYTES}"
  printf 'REQUIRED_BYTES=%s AVAILABLE_BYTES=%s SAFETY_RESERVE_BYTES=%s\n' \
    "${required}" "${available}" "${reserve}"
  if ((available < required)); then
    printf '%s\n' 'RESULT: rejected — insufficient safe copy/verify/rollback capacity'
    return 4
  fi

  AIOA_APPROVED_RELATIVE="${relative}"
  AIOA_APPROVED_SOURCE="${canonical_source}"
  AIOA_APPROVED_DESTINATION_RELATIVE="${destination_relative}"
  AIOA_APPROVED_INVENTORY="${inventory_path}"
  AIOA_APPROVED_FILES="${files}"
  AIOA_APPROVED_DIRECTORIES="${directories}"
  AIOA_APPROVED_SYMLINKS="${symlinks}"
  AIOA_APPROVED_APPARENT_BYTES="${apparent_bytes}"
  AIOA_APPROVED_ALLOCATED_BYTES="${allocated_bytes}"
  printf '%s\n' 'RESULT: approved by dry-run safety checks'
  return 0
}

approved_count=0
for row in "${CANDIDATE_ROWS[@]}"; do
  IFS='|' read -r relative destination_relative <<<"${row}"
  if [[ -n "${requested_candidate}" && "${relative}" != "${requested_candidate}" ]]; then
    continue
  fi
  if inspect_candidate "${relative}" "${destination_relative}"; then
    approved_count=$((approved_count + 1))
  else
    inspection_status=$?
    if ((inspection_status != 3 && inspection_status != 4)); then
      aioa_die "candidate inspection failed unexpectedly: ${relative}"
    fi
  fi
done

if [[ -n "${requested_candidate}" ]] &&
  ! candidate_destination "${requested_candidate}" >/dev/null; then
  aioa_die "candidate is outside the approved migration scope: ${requested_candidate}"
fi

if [[ "${mode}" == "dry-run" ]]; then
  printf '\nDRY_RUN_APPROVED_CANDIDATES=%s\n' "${approved_count}"
  printf '%s\n' 'Migration dry run complete; no data was copied or changed.'
  exit 0
fi

((approved_count == 1)) ||
  aioa_die "apply requires exactly one safely approved candidate"

migration_id="$(date -u +%Y%m%dT%H%M%SZ)-$(
  python3 -c 'import secrets; print(secrets.token_hex(4))'
)"
candidate_name="$(
  python3 - "${AIOA_APPROVED_RELATIVE}" <<'PY'
import re
import sys

print(re.sub(r"[^A-Za-z0-9._-]+", "__", sys.argv[1]).strip("._-"))
PY
)"
aioa_require_nonempty candidate_name "${candidate_name}"

journal_root="${AIOA_REPO_ROOT}/.local/external-data-journals"
journal_directory="${journal_root}/${migration_id}"
journal_path="${journal_directory}/journal.json"
source_inventory="${journal_directory}/source.inventory.json"
copy_inventory="${journal_directory}/copy.inventory.json"
final_inventory="${journal_directory}/final.inventory.json"
rollback_inventory="${journal_directory}/rollback.inventory.json"
staging_parent="${AIOA_EXTERNAL_DATA_ROOT}/migration/quarantine/${migration_id}"
staging_path="${staging_parent}/${candidate_name}"
final_path="${AIOA_EXTERNAL_DATA_ROOT}/${AIOA_APPROVED_DESTINATION_RELATIVE}"
local_hold="${AIOA_APPROVED_SOURCE}.aioa-migration-hold-${migration_id}"
rollback_staging="${AIOA_EXTERNAL_DATA_ROOT}/backups/migration-rollback/.${migration_id}.staging"
rollback_path="${AIOA_EXTERNAL_DATA_ROOT}/backups/migration-rollback/${migration_id}"

for external_path in "${staging_parent}" "${staging_path}" "${final_path}" \
  "${rollback_staging}" "${rollback_path}"; do
  aioa_assert_external_descendant "${external_path}"
done
[[ ! -e "${final_path}" && ! -L "${final_path}" ]] ||
  aioa_die "final destination already exists; refusing to overwrite it: ${final_path}"
[[ ! -e "${local_hold}" && ! -L "${local_hold}" ]] ||
  aioa_die "local hold path already exists: ${local_hold}"
[[ ! -e "${rollback_staging}" && ! -L "${rollback_staging}" ]] ||
  aioa_die "rollback staging path already exists"
[[ ! -e "${rollback_path}" && ! -L "${rollback_path}" ]] ||
  aioa_die "rollback destination already exists"

if [[ ! -e "${journal_root}" ]]; then
  mkdir -m 0700 -- "${journal_root}"
fi
[[ -d "${journal_root}" && ! -L "${journal_root}" ]] ||
  aioa_die "local journal root is unsafe"
mkdir -m 0700 -- "${journal_directory}"
cp -- "${AIOA_APPROVED_INVENTORY}" "${source_inventory}"

python3 - \
  "${journal_path}" \
  "${migration_id}" \
  "${AIOA_PROJECT_ID}" \
  "${AIOA_EXTERNAL_DEVICE_UUID}" \
  "${AIOA_REPO_ROOT}" \
  "$(git -C "${AIOA_REPO_ROOT}" rev-parse HEAD)" \
  "${AIOA_APPROVED_SOURCE}" \
  "${final_path}" \
  "${local_hold}" \
  "${rollback_path}" \
  "${source_inventory}" \
  "${copy_inventory}" \
  "${final_inventory}" \
  "${rollback_inventory}" \
  "${AIOA_APPROVED_FILES}" \
  "${AIOA_APPROVED_DIRECTORIES}" \
  "${AIOA_APPROVED_SYMLINKS}" \
  "${AIOA_APPROVED_APPARENT_BYTES}" \
  "${AIOA_APPROVED_ALLOCATED_BYTES}" <<'PY'
import datetime
import json
import os
import sys

(
    journal,
    migration_id,
    project,
    uuid,
    repository,
    repository_head,
    source,
    destination,
    local_hold,
    rollback,
    source_inventory,
    copy_inventory,
    final_inventory,
    rollback_inventory,
    files,
    directories,
    symlinks,
    apparent_bytes,
    allocated_bytes,
) = sys.argv[1:]
now = (
    datetime.datetime.now(datetime.timezone.utc)
    .replace(microsecond=0)
    .isoformat()
    .replace("+00:00", "Z")
)
payload = {
    "schema_version": "1.0.0",
    "migration_id": migration_id,
    "project_id": project,
    "external_uuid": uuid,
    "repository": repository,
    "repository_head": repository_head,
    "source_path": source,
    "canonical_source_path": os.path.realpath(source),
    "destination_path": destination,
    "canonical_destination_path": os.path.realpath(destination),
    "local_hold_path": local_hold,
    "rollback_path": rollback,
    "source_inventory": source_inventory,
    "copy_inventory": copy_inventory,
    "final_inventory": final_inventory,
    "rollback_inventory": rollback_inventory,
    "counts": {
        "files": int(files),
        "directories": int(directories),
        "symlinks": int(symlinks),
    },
    "apparent_bytes": int(apparent_bytes),
    "allocated_bytes": int(allocated_bytes),
    "status": "inventory-complete",
    "events": [{"status": "inventory-complete", "at_utc": now}],
}
temporary = journal + ".tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(temporary, 0o600)
os.replace(temporary, journal)
PY

journal_update() {
  local status="$1"
  python3 - "${journal_path}" "${status}" <<'PY'
import datetime
import json
import os
import sys

journal, status = sys.argv[1:]
with open(journal, "r", encoding="utf-8") as handle:
    payload = json.load(handle)
now = (
    datetime.datetime.now(datetime.timezone.utc)
    .replace(microsecond=0)
    .isoformat()
    .replace("+00:00", "Z")
)
payload["status"] = status
payload.setdefault("events", []).append({"status": status, "at_utc": now})
temporary = journal + ".tmp"
with open(temporary, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
os.chmod(temporary, 0o600)
os.replace(temporary, journal)
PY
}

mkdir -m 0750 -- "${staging_parent}"
mkdir -m 0750 -- "${staging_path}"
cp -a -- "${AIOA_APPROVED_SOURCE}/." "${staging_path}/"
journal_update "copy-complete"

python3 "${INVENTORY_TOOL}" create \
  "${staging_path}" \
  --allowed-root "${staging_path}" \
  --output "${copy_inventory}"
python3 "${INVENTORY_TOOL}" compare "${source_inventory}" "${copy_inventory}"
journal_update "copy-verified"

mv -- "${staging_path}" "${final_path}"
journal_update "promoted"
python3 "${INVENTORY_TOOL}" create \
  "${final_path}" \
  --allowed-root "${final_path}" \
  --output "${final_inventory}"
python3 "${INVENTORY_TOOL}" compare "${source_inventory}" "${final_inventory}"
journal_update "final-verified"

mv -- "${AIOA_APPROVED_SOURCE}" "${local_hold}"
ln -s -- "${final_path}" "${AIOA_APPROVED_SOURCE}"
if [[ "$(readlink -f -- "${AIOA_APPROVED_SOURCE}")" != \
      "$(aioa_canonical_path "${final_path}")" ]]; then
  unlink -- "${AIOA_APPROVED_SOURCE}"
  mv -- "${local_hold}" "${AIOA_APPROVED_SOURCE}"
  aioa_die "application-facing symlink verification failed; source restored"
fi
if ! python3 "${INVENTORY_TOOL}" verify-read \
  "${AIOA_APPROVED_SOURCE}" "${final_inventory}"; then
  unlink -- "${AIOA_APPROVED_SOURCE}"
  mv -- "${local_hold}" "${AIOA_APPROVED_SOURCE}"
  aioa_die "application-facing read verification failed; source restored"
fi
journal_update "application-link-verified"

mkdir -m 0750 -- "${rollback_staging}"
cp -a -- "${local_hold}/." "${rollback_staging}/"
python3 "${INVENTORY_TOOL}" create \
  "${rollback_staging}" \
  --allowed-root "${rollback_staging}" \
  --output "${rollback_inventory}"
python3 "${INVENTORY_TOOL}" compare "${source_inventory}" "${rollback_inventory}"
mv -- "${rollback_staging}" "${rollback_path}"
python3 "${INVENTORY_TOOL}" create \
  "${rollback_path}" \
  --allowed-root "${rollback_path}" \
  --output "${rollback_inventory}"
python3 "${INVENTORY_TOOL}" compare "${source_inventory}" "${rollback_inventory}"
journal_update "complete-hold-retained"

printf 'MIGRATION_ID=%s\n' "${migration_id}"
printf 'SOURCE=%s\n' "${AIOA_APPROVED_SOURCE}"
printf 'FINAL_DESTINATION=%s\n' "${final_path}"
printf 'ROLLBACK_DESTINATION=%s\n' "${rollback_path}"
printf 'LOCAL_HOLD_RETAINED=%s\n' "${local_hold}"
printf '%s\n' \
  'Migration completed with verified final and rollback copies; the local hold was retained for conservative preservation.'
