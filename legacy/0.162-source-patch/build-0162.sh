#!/usr/bin/env bash
set -euo pipefail

readonly upstream_tag="rust-v0.162.0-alpha.17.2"
readonly upstream_commit="740e5af33c71225640e0c1c1555c514c2c93ab74"
readonly toolchain="1.95.0"
repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir=""
output_path="${repository_root}/dist/codex-v2-plaintext-0.162.0-alpha.17.2"
check_only=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --source-dir) source_dir="$2"; shift 2 ;;
    --output) output_path="$2"; shift 2 ;;
    --check-only) check_only=true; shift ;;
    -h|--help)
      echo 'Usage: build-0162.sh --source-dir DIR [--output FILE] [--check-only]'
      echo 'Use a clean checkout of rust-v0.162.0-alpha.17.2; existing work is never reset.'
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [ -z "${source_dir}" ]; then
  echo 'Pass --source-dir pointing to a clean checkout of the pinned tag.' >&2
  exit 1
fi
source_dir="$(cd "${source_dir}" && pwd)"
if [ "$(git -C "${source_dir}" rev-parse HEAD)" != "${upstream_commit}" ]; then
  echo "Source does not match ${upstream_tag} (${upstream_commit})" >&2
  exit 1
fi
if [ -n "$(git -C "${source_dir}" status --porcelain)" ]; then
  echo 'Refusing to patch an existing modified checkout.' >&2
  exit 1
fi
patch_file="${repository_root}/patches/0.162.0-alpha.17.2/0001-opt-in-v2-plaintext-messages.patch"
git -C "${source_dir}" apply --check "${patch_file}"
if ${check_only}; then exit 0; fi
git -C "${source_dir}" apply "${patch_file}"

(
  cd "${source_dir}/codex-rs"
  cargo +"${toolchain}" test -p codex-core --lib plaintext
  cargo +"${toolchain}" build --release -p codex-cli --bin codex
)
mkdir -p "$(dirname "${output_path}")"
install -m 755 "${source_dir}/codex-rs/target/release/codex" "${output_path}"
strip --strip-unneeded "${output_path}"
sha256sum "${output_path}"
