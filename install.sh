#!/usr/bin/env bash
set -euo pipefail

REPO="CCanxue/codex-deepseek-subagent-fix"
ASSET="codex-deepseek-subagent-fix-linux-x86_64"
CHECKSUM="SHA256SUMS"
VERSION="${VERSION:-latest}"
INSTALL_DIR="${CODEX_INSTALL_DIR:-$HOME/.local/bin}"
CMD_NAME="codex-ds"

usage() {
  cat >&2 <<'EOF'
Usage: install.sh [--version VERSION] [--install-dir DIR] [--name CMD]

安装用于 DeepSeek 子代理的 Codex CLI（打补丁二进制）。
默认安装最新版本到 ~/.local/bin/codex-ds。

选项：
  --version VERSION   上游版本号（如 0.146.0），默认 latest
  --install-dir DIR   安装目录，默认 ~/.local/bin
  --name CMD          命令名，默认 codex-ds
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --name) CMD_NAME="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

case "$(uname -s)" in
  Linux) ;;
  *) echo "unsupported OS: $(uname -s) (only Linux is provided)" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64) ;;
  *) echo "unsupported arch: $(uname -m) (only x86_64 is provided)" >&2; exit 1 ;;
esac

if [ "$VERSION" = "latest" ]; then
  BASE="https://github.com/${REPO}/releases/latest/download"
else
  BASE="https://github.com/${REPO}/releases/download/v${VERSION}-deepseek.1"
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Downloading ${CMD_NAME} ${VERSION} ..."
curl -fL --retry 3 -o "${TMP_DIR}/${ASSET}" "${BASE}/${ASSET}"
curl -fL --retry 3 -o "${TMP_DIR}/${CHECKSUM}" "${BASE}/${CHECKSUM}"

echo "Verifying checksum ..."
cd "${TMP_DIR}"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum -c "${CHECKSUM}" --ignore-missing
else
  shasum -a 256 -c "${CHECKSUM}"
fi

mkdir -p "${INSTALL_DIR}"
install -m 755 "${TMP_DIR}/${ASSET}" "${INSTALL_DIR}/${CMD_NAME}"

echo
echo "Installed: ${INSTALL_DIR}/${CMD_NAME}"
"${INSTALL_DIR}/${CMD_NAME}" --version 2>/dev/null || true
echo
case ":$PATH:" in
  *":${INSTALL_DIR}:"*) ;;
  *) echo "注意：${INSTALL_DIR} 不在 PATH 中，请加入 PATH 或使用完整路径。" ;;
esac
echo "启动：${CMD_NAME}          # TUI"
echo "      ${CMD_NAME} exec ... # 非交互"
echo
echo "首次使用请先配置 DeepSeek（二选一）："
echo "  1) 官方一键脚本（flash / pro 都支持）"
echo "     curl -fsSL https://cdn.deepseek.com/api-docs/codex-deepseek-setup-en.sh | bash"
echo "  2) 或参考仓库 config/ 下的脱敏模板自行填写"
