#!/usr/bin/env bash
set -euo pipefail

readonly UPSTREAM_REPOSITORY="https://github.com/openai/codex.git"
readonly UPSTREAM_TAG="rust-v0.147.0"
readonly UPSTREAM_COMMIT="be6e8eac029b183056b7e4402879f15d2c85f61b"
readonly RUST_TOOLCHAIN="1.95.0"

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
patch_file="${repository_root}/patches/0.147.0/0001-deliver-non-openai-subagent-tasks-as-user-input.patch"
output_path="${repository_root}/dist/codex-deepseek-subagent-fix-linux-x86_64"
work_dir=""

usage() {
  cat <<'EOF'
Usage: scripts/build-release.sh [--output PATH] [--work-dir PATH]

Build the patched Linux x86_64 Codex binary from the pinned upstream source.
The upstream tag and commit are checked before the patch is applied.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) output_path="$2"; shift 2 ;;
    --work-dir) work_dir="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

for required_command in cargo git install rustup; do
  command -v "${required_command}" >/dev/null 2>&1 || {
    echo "missing required command: ${required_command}" >&2
    exit 1
  }
done

if [ "$(uname -s)" != "Linux" ] || [ "$(uname -m)" != "x86_64" ]; then
  echo "only Linux x86_64 builds are currently supported" >&2
  exit 1
fi

if [ -z "${work_dir}" ]; then
  work_dir="$(mktemp -d)"
  trap 'rm -rf "${work_dir}"' EXIT
fi

source_dir="${work_dir}/codex"
git clone --filter=blob:none --depth 1 --branch "${UPSTREAM_TAG}" "${UPSTREAM_REPOSITORY}" "${source_dir}"

actual_commit="$(git -C "${source_dir}" rev-parse HEAD)"
if [ "${actual_commit}" != "${UPSTREAM_COMMIT}" ]; then
  echo "upstream commit mismatch: expected ${UPSTREAM_COMMIT}, got ${actual_commit}" >&2
  exit 1
fi

git -C "${source_dir}" apply --check "${patch_file}"
git -C "${source_dir}" apply "${patch_file}"

(
  cd "${source_dir}/codex-rs"
  cargo +"${RUST_TOOLCHAIN}" build --locked --release -p codex-cli
)

mkdir -p "$(dirname "${output_path}")"
install -m 755 "${source_dir}/codex-rs/target/release/codex" "${output_path}"
sha256sum "${output_path}"
