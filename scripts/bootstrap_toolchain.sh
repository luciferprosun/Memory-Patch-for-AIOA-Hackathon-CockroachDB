#!/usr/bin/env bash
set -euo pipefail

readonly AIOA_UV_VERSION="0.11.31"
readonly AIOA_UV_INSTALLER_SHA256="bd9a2739c49251c71fd3706ac00b1bb8582ea138433c6e52840de4aba646e46a"
readonly AIOA_CCLOUD_VERSION="0.6.12"
readonly AIOA_CCLOUD_ARCHIVE_SHA256="a0d26bd1dd2f904a8464cadb2f0c062afa8cb68b5aadd9717dd7109dc9ad61b2"
readonly AIOA_CCLOUD_BINARY_SHA256="944c7a35f9fe6b166dea991040399ac4e1cf0c754d0514fd57d7c4333c5d4cb2"
readonly AIOA_COCKROACH_SQL_VERSION="v26.2.3"
readonly AIOA_COCKROACH_SQL_AMD64_SHA256="b917799627b9da4f21532e248ce7f859c6ede7bbda2777110209d15ae9d7d386"
readonly AIOA_LOCAL_BIN="${HOME}/.local/bin"
readonly AIOA_AWS_INSTALL_DIR="${HOME}/.local/aws-cli"
readonly AIOA_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

AIOA_TEMP_DIR=""

cleanup() {
  if [[ -n "${AIOA_TEMP_DIR}" && -d "${AIOA_TEMP_DIR}" ]]; then
    rm -rf -- "${AIOA_TEMP_DIR}"
  fi
}
trap cleanup EXIT

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

download_https() {
  local source_url="$1"
  local destination="$2"
  curl --fail --location --silent --show-error \
    --proto '=https' --tlsv1.2 \
    --output "${destination}" "${source_url}"
}

verify_sha256() {
  local expected_hash="$1"
  local path="$2"
  local actual_hash
  actual_hash="$(sha256sum "${path}" | cut -d' ' -f1)"
  [[ "${actual_hash}" == "${expected_hash}" ]] ||
    die "SHA-256 mismatch for ${path}"
}

configure_user_path() {
  local startup_file managed_start managed_end backup_path timestamp

  mkdir -p "${AIOA_LOCAL_BIN}"
  case ":${PATH}:" in
    *":${AIOA_LOCAL_BIN}:"*) ;;
    *)
      case "${SHELL##*/}" in
        zsh) startup_file="${HOME}/.zshrc" ;;
        bash) startup_file="${HOME}/.bashrc" ;;
        *) startup_file="${HOME}/.profile" ;;
      esac

      managed_start="# BEGIN AIOA MEMORY PATCH TOOLCHAIN"
      managed_end="# END AIOA MEMORY PATCH TOOLCHAIN"
      if [[ -f "${startup_file}" ]] && grep -Fqx "${managed_start}" "${startup_file}"; then
        printf 'PATH managed block already exists in %s\n' "${startup_file}"
      else
        timestamp="$(date +%Y%m%dT%H%M%S%z)"
        backup_path="${startup_file}.aioa-memory-patch.${timestamp}.bak"
        if [[ -f "${startup_file}" ]]; then
          cp -p -- "${startup_file}" "${backup_path}"
        else
          install -m 0644 /dev/null "${backup_path}"
        fi
        {
          printf '\n%s\n' "${managed_start}"
          printf '%s\n' 'export PATH="$HOME/.local/bin:$PATH"'
          printf '%s\n' "${managed_end}"
        } >>"${startup_file}"
        printf 'Updated %s; backup: %s\n' "${startup_file}" "${backup_path}"
      fi
      ;;
  esac
  export PATH="${AIOA_LOCAL_BIN}:${PATH}"
}

install_base_packages() {
  local required_packages missing_packages package_name
  required_packages=(
    ca-certificates curl unzip xz-utils tar jq gnupg less make git
  )
  missing_packages=()

  command -v dpkg-query >/dev/null 2>&1 ||
    die "dpkg-query is required for the Linux Mint/Ubuntu package audit"

  for package_name in "${required_packages[@]}"; do
    if ! dpkg-query -W -f='${Status}' "${package_name}" 2>/dev/null |
      grep -Fx 'install ok installed' >/dev/null; then
      missing_packages+=("${package_name}")
    fi
  done

  if ((${#missing_packages[@]} == 0)); then
    printf 'All required base packages are already installed.\n'
    return
  fi

  command -v apt-get >/dev/null 2>&1 ||
    die "missing packages require apt-get: ${missing_packages[*]}"
  command -v sudo >/dev/null 2>&1 ||
    die "missing packages require sudo: ${missing_packages[*]}"
  sudo -n true >/dev/null 2>&1 ||
    die "non-interactive sudo is unavailable; install with the native package manager: ${missing_packages[*]}"

  sudo -n apt-get install --yes --no-install-recommends "${missing_packages[@]}"
}

detect_architecture() {
  case "$(uname -m)" in
    x86_64 | amd64)
      AIOA_AWS_ARCH="x86_64"
      AIOA_CCLOUD_ARCH="amd64"
      AIOA_COCKROACH_ARCH="amd64"
      ;;
    aarch64 | arm64)
      AIOA_AWS_ARCH="aarch64"
      AIOA_CCLOUD_ARCH="arm64"
      AIOA_COCKROACH_ARCH="arm64"
      ;;
    *)
      die "unsupported architecture: $(uname -m)"
      ;;
  esac
}

install_uv() {
  local installer_path current_version
  if [[ -x "${AIOA_LOCAL_BIN}/uv" ]]; then
    current_version="$("${AIOA_LOCAL_BIN}/uv" --version)"
    if [[ "${current_version}" == uv\ "${AIOA_UV_VERSION}"* ]] &&
      [[ -x "${AIOA_LOCAL_BIN}/uvx" ]]; then
      printf 'Retaining %s\n' "${current_version}"
      return
    fi
    die "a different uv already exists at ${AIOA_LOCAL_BIN}/uv; refusing to overwrite it"
  fi

  installer_path="${AIOA_TEMP_DIR}/uv-installer.sh"
  download_https \
    "https://astral.sh/uv/${AIOA_UV_VERSION}/install.sh" \
    "${installer_path}"
  verify_sha256 "${AIOA_UV_INSTALLER_SHA256}" "${installer_path}"
  grep -Fq 'APP_VERSION="0.11.31"' "${installer_path}" ||
    die "uv installer does not declare the pinned version"
  grep -Fq 'astral-sh/uv/releases/download' "${installer_path}" ||
    die "uv installer does not reference the official Astral release origin"

  env UV_UNMANAGED_INSTALL="${AIOA_LOCAL_BIN}" UV_NO_MODIFY_PATH=1 \
    sh "${installer_path}"
  [[ "$("${AIOA_LOCAL_BIN}/uv" --version)" == uv\ "${AIOA_UV_VERSION}"* ]] ||
    die "uv verification failed"
}

prepare_python() {
  if ! uv python find 3.12 >/dev/null 2>&1; then
    uv python install 3.12
  fi
  [[ "$(uv run --python 3.12 python --version 2>&1)" == Python\ 3.12.* ]] ||
    die "CPython 3.12 verification failed"
}

write_aws_public_key() {
  local key_path="$1"
  # AWS CLI Team signing key copied from the official AWS CLI install guide.
  cat >"${key_path}" <<'AWS_CLI_PUBLIC_KEY'
-----BEGIN PGP PUBLIC KEY BLOCK-----
mQINBF2Cr7UBEADJZHcgusOJl7ENSyumXh85z0TRV0xJorM2B/JL0kHOyigQluUG
ZMLhENaG0bYatdrKP+3H91lvK050pXwnO/R7fB/FSTouki4ciIx5OuLlnJZIxSzx
PqGl0mkxImLNbGWoi6Lto0LYxqHN2iQtzlwTVmq9733zd3XfcXrZ3+LblHAgEt5G
TfNxEKJ8soPLyWmwDH6HWCnjZ/aIQRBTIQ05uVeEoYxSh6wOai7ss/KveoSNBbYz
gbdzoqI2Y8cgH2nbfgp3DSasaLZEdCSsIsK1u05CinE7k2qZ7KgKAUIcT/cR/grk
C6VwsnDU0OUCideXcQ8WeHutqvgZH1JgKDbznoIzeQHJD238GEu+eKhRHcz8/jeG
94zkcgJOz3KbZGYMiTh277Fvj9zzvZsbMBCedV1BTg3TqgvdX4bdkhf5cH+7NtWO
lrFj6UwAsGukBTAOxC0l/dnSmZhJ7Z1KmEWilro/gOrjtOxqRQutlIqG22TaqoPG
fYVN+en3Zwbt97kcgZDwqbuykNt64oZWc4XKCa3mprEGC3IbJTBFqglXmZ7l9ywG
EEUJYOlb2XrSuPWml39beWdKM8kzr1OjnlOm6+lpTRCBfo0wa9F8YZRhHPAkwKkX
XDeOGpWRj4ohOx0d2GWkyV5xyN14p2tQOCdOODmz80yUTgRpPVQUtOEhXQARAQAB
tCFBV1MgQ0xJIFRlYW0gPGF3cy1jbGlAYW1hem9uLmNvbT6JAlQEEwEIAD4CGwMF
CwkIBwIGFQoJCAsCBBYCAwECHgECF4AWIQT7Xbd/1cEYuAURraimMQrMRnJHXAUC
aGveYQUJDMpiLAAKCRCmMQrMRnJHXKBYD/9Ab0qQdGiO5hObchG8xh8Rpb4Mjyf6
0JrVo6m8GNjNj6BHkSc8fuTQJ/FaEhaQxj3pjZ3GXPrXjIIVChmICLlFuRXYzrXc
Pw0lniybypsZEVai5kO0tCNBCCFuMN9RsmmRG8mf7lC4FSTbUDmxG/QlYK+0IV/l
uJkzxWa+rySkdpm0JdqumjegNRgObdXHAQDWlubWQHWyZyIQ2B4U7AxqSpcdJp6I
S4Zds4wVLd1WE5pquYQ8vS2cNlDm4QNg8wTj58e3lKN47hXHMIb6CHxRnb947oJa
pg189LLPR5koh+EorNkA1wu5mAJtJvy5YMsppy2y/kIjp3lyY6AmPT1posgGk70Z
CmToEZ5rbd7ARExtlh76A0cabMDFlEHDIK8RNUOSRr7L64+KxOUegKBfQHb9dADY
qqiKqpCbKgvtWlds909Ms74JBgr2KwZCSY1HaOxnIr4CY43QRqAq5YHOay/mU+6w
hhmdF18vpyK0vfkvvGresWtSXbag7Hkt3XjaEw76BzxQH21EBDqU8WJVjHgU6ru+
DJTs+SxgJbaT3hb/vyjlw0lK+hFfhWKRwgOXH8vqducF95NRSUxtS4fpqxWVaw3Q
V2OWSjbne99A5EPEySzryFTKbMGwaTlAwMCwYevt4YT6eb7NmFhTx0Fis4TalUs+
j+c7Kg92pDx2uQ==
=OBAt
-----END PGP PUBLIC KEY BLOCK-----
AWS_CLI_PUBLIC_KEY
}

install_or_retain_aws_cli_v2() {
  local existing_version archive_path signature_path key_path gnupg_home
  existing_version="$(aws --version 2>&1 || true)"
  if [[ "${existing_version}" == aws-cli/2.* ]]; then
    printf 'Retaining %s\n' "${existing_version}"
    return
  fi

  if [[ -e "${AIOA_LOCAL_BIN}/aws" || -L "${AIOA_LOCAL_BIN}/aws" ]]; then
    die "a non-v2 AWS CLI occupies ${AIOA_LOCAL_BIN}/aws; refusing to overwrite it"
  fi

  archive_path="${AIOA_TEMP_DIR}/awscliv2.zip"
  signature_path="${archive_path}.sig"
  key_path="${AIOA_TEMP_DIR}/aws-cli-public-key.asc"
  gnupg_home="${AIOA_TEMP_DIR}/gnupg"
  mkdir -m 0700 "${gnupg_home}"

  download_https \
    "https://awscli.amazonaws.com/awscli-exe-linux-${AIOA_AWS_ARCH}.zip" \
    "${archive_path}"
  download_https \
    "https://awscli.amazonaws.com/awscli-exe-linux-${AIOA_AWS_ARCH}.zip.sig" \
    "${signature_path}"
  write_aws_public_key "${key_path}"

  gpg --batch --homedir "${gnupg_home}" --import "${key_path}" >/dev/null 2>&1
  gpg --batch --homedir "${gnupg_home}" --with-colons --fingerprint \
    A6310ACC4672475C |
    grep -F 'fpr:::::::::FB5DB77FD5C118B80511ADA8A6310ACC4672475C:' >/dev/null ||
    die "AWS CLI signing-key fingerprint verification failed"
  gpg --batch --homedir "${gnupg_home}" \
    --verify "${signature_path}" "${archive_path}" ||
    die "AWS CLI PGP signature verification failed"

  unzip -q "${archive_path}" -d "${AIOA_TEMP_DIR}"
  "${AIOA_TEMP_DIR}/aws/install" \
    --install-dir "${AIOA_AWS_INSTALL_DIR}" \
    --bin-dir "${AIOA_LOCAL_BIN}"
  [[ "$("${AIOA_LOCAL_BIN}/aws" --version 2>&1)" == aws-cli/2.* ]] ||
    die "AWS CLI v2 verification failed"
}

install_ccloud() {
  local archive_path extracted_dir
  if [[ -x "${AIOA_LOCAL_BIN}/ccloud" ]]; then
    if strings "${AIOA_LOCAL_BIN}/ccloud" |
      grep -Fx "${AIOA_CCLOUD_VERSION}" >/dev/null &&
      [[ "$(sha256sum "${AIOA_LOCAL_BIN}/ccloud" | cut -d' ' -f1)" == \
        "${AIOA_CCLOUD_BINARY_SHA256}" ]]; then
      printf 'Retaining ccloud %s\n' "${AIOA_CCLOUD_VERSION}"
      return
    fi
    die "another ccloud binary exists at ${AIOA_LOCAL_BIN}/ccloud; refusing to overwrite it"
  fi

  archive_path="${AIOA_TEMP_DIR}/ccloud.tar.gz"
  extracted_dir="${AIOA_TEMP_DIR}/ccloud"
  download_https \
    "https://binaries.cockroachdb.com/ccloud/ccloud_linux-${AIOA_CCLOUD_ARCH}_${AIOA_CCLOUD_VERSION}.tar.gz" \
    "${archive_path}"
  verify_sha256 "${AIOA_CCLOUD_ARCHIVE_SHA256}" "${archive_path}"
  tar -tzf "${archive_path}" | grep -E '(^|/)ccloud$' >/dev/null ||
    die "ccloud archive does not contain the expected executable"
  mkdir "${extracted_dir}"
  tar -xzf "${archive_path}" -C "${extracted_dir}"
  install -m 0755 "${extracted_dir}/ccloud" "${AIOA_LOCAL_BIN}/ccloud"
  verify_sha256 "${AIOA_CCLOUD_BINARY_SHA256}" "${AIOA_LOCAL_BIN}/ccloud"
}

install_cockroach_sql() {
  local archive_name archive_path checksum_path extract_dir expected_hash
  if [[ -x "${AIOA_LOCAL_BIN}/cockroach-sql" ]] &&
    "${AIOA_LOCAL_BIN}/cockroach-sql" --version 2>&1 |
    grep -E '^Build Tag:[[:space:]]+v26\.2\.3$' >/dev/null; then
    printf 'Retaining cockroach-sql %s\n' "${AIOA_COCKROACH_SQL_VERSION}"
    return
  fi
  if [[ -e "${AIOA_LOCAL_BIN}/cockroach-sql" ]]; then
    die "another cockroach-sql exists at ${AIOA_LOCAL_BIN}/cockroach-sql; refusing to overwrite it"
  fi

  case "${AIOA_COCKROACH_ARCH}" in
    amd64) expected_hash="${AIOA_COCKROACH_SQL_AMD64_SHA256}" ;;
    *)
      die "no pinned published Cockroach SQL checksum is recorded for ${AIOA_COCKROACH_ARCH}"
      ;;
  esac

  archive_name="cockroach-sql-${AIOA_COCKROACH_SQL_VERSION}.linux-${AIOA_COCKROACH_ARCH}.tgz"
  archive_path="${AIOA_TEMP_DIR}/${archive_name}"
  checksum_path="${archive_path}.sha256sum"
  extract_dir="${AIOA_TEMP_DIR}/cockroach-sql"
  download_https "https://binaries.cockroachdb.com/${archive_name}" "${archive_path}"
  download_https "https://binaries.cockroachdb.com/${archive_name}.sha256sum" "${checksum_path}"
  grep -Fq "${expected_hash}" "${checksum_path}" ||
    die "published Cockroach SQL checksum differs from the pinned checksum"
  verify_sha256 "${expected_hash}" "${archive_path}"
  mkdir "${extract_dir}"
  tar -xzf "${archive_path}" -C "${extract_dir}"
  install -m 0755 \
    "${extract_dir}/${archive_name%.tgz}/cockroach-sql" \
    "${AIOA_LOCAL_BIN}/cockroach-sql"
}

main() {
  [[ "$(uname -s)" == "Linux" ]] || die "this bootstrap supports Linux only"
  configure_user_path
  install_base_packages
  detect_architecture
  AIOA_TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aioa-memory-toolchain.XXXXXX")"

  install_uv
  prepare_python
  install_or_retain_aws_cli_v2
  install_ccloud
  install_cockroach_sql

  bash "${AIOA_REPO_ROOT}/scripts/verify_toolchain.sh"
}

main "$@"
