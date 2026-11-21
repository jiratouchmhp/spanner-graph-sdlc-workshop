#!/usr/bin/env bash
# Download the official MCP Toolbox for Databases binary (googleapis/genai-toolbox)
# for local stdio/HTTP testing and binary-based IDE integrations.
#
# Note: Antigravity IDE (MCP Store or `npx -y @toolbox-sdk/server`) and Gemini
# CLI (`gemini extensions install https://github.com/gemini-cli-extensions/spanner`)
# do NOT require manually downloading this binary.
#
# Usage:
#   bash solution/mcp/setup_local_toolbox.sh
#   bash solution/mcp/setup_local_toolbox.sh --dry-run
#   TOOLBOX_VERSION=v1.13.1 bash solution/mcp/setup_local_toolbox.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="${TOOLBOX_VERSION:-v1.13.1}"
BIN_DIR="${TOOLBOX_BIN_DIR:-${SCRIPT_DIR}/bin}"
TARGET_BIN="${BIN_DIR}/toolbox"
DRY_RUN="false"

for arg in "$@"; do
  case "${arg}" in
    --dry-run)
      DRY_RUN="true"
      ;;
    -h|--help)
      echo "Usage: $0 [--dry-run]"
      echo "Env overrides: TOOLBOX_VERSION (default: ${VERSION}), TOOLBOX_BIN_DIR (default: ${BIN_DIR})"
      exit 0
      ;;
    *)
      echo "Unknown argument: ${arg}" >&2
      exit 1
      ;;
  esac
done

detect_platform() {
  local raw_os raw_arch os arch
  raw_os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  raw_arch="$(uname -m)"

  case "${raw_os}" in
    linux) os="linux" ;;
    darwin) os="darwin" ;;
    *)
      echo "Unsupported OS '${raw_os}'. Supported: linux, darwin (use official Windows binary on Windows)." >&2
      exit 1
      ;;
  esac

  case "${raw_arch}" in
    x86_64|amd64) arch="amd64" ;;
    arm64|aarch64)
      if [[ "${os}" == "darwin" ]]; then
        arch="arm64"
      else
        arch="amd64"
        echo "[WARN] Linux arm64 detected; verify official release availability or use 'npx -y @toolbox-sdk/server'." >&2
      fi
      ;;
    *)
      echo "Unsupported architecture '${raw_arch}'." >&2
      exit 1
      ;;
  esac

  echo "${os}/${arch}"
}

PLATFORM="$(detect_platform)"
DOWNLOAD_URL="https://storage.googleapis.com/mcp-toolbox-for-databases/${VERSION}/${PLATFORM}/toolbox"

echo "MCP Toolbox Version : ${VERSION}"
echo "Detected Platform   : ${PLATFORM}"
echo "Download URL        : ${DOWNLOAD_URL}"
echo "Install Destination : ${TARGET_BIN}"

if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Skipping binary download."
  exit 0
fi

mkdir -p "${BIN_DIR}"
curl -fsSL -o "${TARGET_BIN}" "${DOWNLOAD_URL}"
chmod +x "${TARGET_BIN}"

echo "[OK] Installed MCP Toolbox binary:"
"${TARGET_BIN}" --version
