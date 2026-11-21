#!/usr/bin/env bash
# Launch MCP Toolbox for Databases locally for a single least-privilege persona.
#
# Defaults to `--stdio` transport using the read-only `impact-analyst` persona
# YAML (`personas/tools.impact-analyst.yaml`). Prefers the local binary installed
# by `setup_local_toolbox.sh`, falling back cleanly to `npx -y @toolbox-sdk/server`.
#
# Usage:
#   bash solution/mcp/run_toolbox_stdio.sh                          # stdio, impact-analyst
#   TOOLBOX_PERSONA=security-auditor bash solution/mcp/run_toolbox_stdio.sh
#   bash solution/mcp/run_toolbox_stdio.sh --http                   # local HTTP w/ DNS-rebinding lock
#   bash solution/mcp/run_toolbox_stdio.sh --dry-run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Load optional untracked .env if present (never commit secrets).
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +a
fi

PERSONA="${TOOLBOX_PERSONA:-impact-analyst}"
MODE="stdio"
DRY_RUN="false"
LOCAL_PORT="${TOOLBOX_PORT:-5000}"

for arg in "$@"; do
  case "${arg}" in
    --stdio) MODE="stdio" ;;
    --http) MODE="http" ;;
    --dry-run) DRY_RUN="true" ;;
    -h|--help)
      echo "Usage: $0 [--stdio | --http] [--dry-run]"
      echo "Env: TOOLBOX_PERSONA=impact-analyst|adr-curator|security-auditor|all (default: impact-analyst)"
      exit 0
      ;;
    *)
      echo "Unknown argument: ${arg}" >&2
      exit 1
      ;;
  esac
done

case "${PERSONA}" in
  impact-analyst|adr-curator|security-auditor)
    TOOLS_FILE="${SCRIPT_DIR}/personas/tools.${PERSONA}.yaml"
    ;;
  all)
    TOOLS_FILE="${SCRIPT_DIR}/tools.yaml"
    ;;
  *)
    echo "Invalid TOOLBOX_PERSONA='${PERSONA}'. Expected impact-analyst, adr-curator, security-auditor, or all." >&2
    exit 1
    ;;
esac

if [[ ! -f "${TOOLS_FILE}" ]]; then
  echo "Persona config not found: ${TOOLS_FILE}. Run: python3 solution/mcp/render_persona_tools.py" >&2
  exit 1
fi

# Locked Bangkok primary + Singapore Vertex AI defaults (never 'global' for embeddings).
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-${SPANNER_PROJECT:-}}"
export SPANNER_PROJECT="${SPANNER_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}"
export SPANNER_INSTANCE="${SPANNER_INSTANCE:-sdlc-kg-enterprise}"
export SPANNER_DATABASE="${SPANNER_DATABASE:-sdlc_knowledge_graph}"
export SPANNER_DIALECT="${SPANNER_DIALECT:-googlesql}"
export VERTEX_AI_LOCATION="${VERTEX_AI_LOCATION:-asia-southeast1}"
export GOOGLE_OAUTH_CLIENT_ID="${GOOGLE_OAUTH_CLIENT_ID:-demo-oauth-client-id.apps.googleusercontent.com}"

if [[ "${VERTEX_AI_LOCATION}" == "global" ]]; then
  echo "[ERROR] VERTEX_AI_LOCATION cannot be 'global' for gemini-embedding-001; use 'asia-southeast1'." >&2
  exit 1
fi

if [[ -x "${SCRIPT_DIR}/bin/toolbox" ]]; then
  CMD=("${SCRIPT_DIR}/bin/toolbox")
  # Both --tools-file and v1 --config are supported; use --tools-file for broad binary compatibility.
  CONFIG_FLAG="--tools-file"
else
  CMD=("npx" "-y" "@toolbox-sdk/server")
  CONFIG_FLAG="--config"
fi

if [[ "${MODE}" == "stdio" ]]; then
  ARGS=("${CONFIG_FLAG}" "${TOOLS_FILE}" "--stdio")
else
  # SECURITY: Never leave --allowed-hosts="*" even on 127.0.0.1 (prevents DNS-rebinding attacks).
  ARGS=(
    "${CONFIG_FLAG}" "${TOOLS_FILE}"
    "--address=127.0.0.1"
    "--port=${LOCAL_PORT}"
    "--allowed-hosts=localhost,127.0.0.1"
    "--allowed-origins=http://localhost:${LOCAL_PORT},http://127.0.0.1:${LOCAL_PORT}"
    "--logging-format=JSON"
    "--sql-commenter"
  )
fi

if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Persona : ${PERSONA}"
  echo "[DRY-RUN] Mode    : ${MODE}"
  echo "[DRY-RUN] Command : ${CMD[*]} ${ARGS[*]}"
  exit 0
fi

exec "${CMD[@]}" "${ARGS[@]}"
