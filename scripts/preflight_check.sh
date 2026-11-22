#!/usr/bin/env bash
# =============================================================================
# Cloud Spanner Enterprise SDLC Knowledge Graph Workshop — Preflight Check
# =============================================================================
# Fast (< 5s) attendee environment readiness check (`make preflight`).
# Never hangs on interactive prompts (`CLOUDSDK_CORE_DISABLE_PROMPTS=1`).
#
# Checks:
#   1. Python >= 3.10 & `.venv` PEP-668-safe virtualenv status
#   2. `gcloud` CLI availability, active account auth & ADC status
#   3. Active GCP project safety (warns if unset, placeholder, or shared/prod)
#   4. Locked regional env defaults: Bangkok (`asia-southeast3`) primary +
#      Singapore (`asia-southeast1`) Vertex AI / Model Armor / DR (INV-09)
#   5. MCP Toolbox native binary or `npx` availability
#   6. Docker availability (with clear Zero-Docker path confirmation)
#
# Modes:
#   Default (offline): Non-blocking checklist; exits 0 for immediate offline work.
#   --strict-gcp     : Exits non-zero if Python < 3.10, `gcloud` or ADC is not
#                      authenticated, project is unset/placeholder, or INV-09 fails.
#
# Usage:
#   bash scripts/preflight_check.sh
#   bash scripts/preflight_check.sh --strict-gcp
#   bash scripts/preflight_check.sh --project my-sandbox-proj --strict-gcp
# =============================================================================
set -euo pipefail

export CLOUDSDK_CORE_DISABLE_PROMPTS=1

# Auto-detect standalone Google Cloud SDK installs in $HOME if not already on PATH
if ! command -v gcloud >/dev/null 2>&1; then
  for _sdk_dir in "${HOME}/google-cloud-sdk/bin" "${HOME}/.local/bin"; do
    if [[ -x "${_sdk_dir}/gcloud" ]]; then
      export PATH="${_sdk_dir}:${PATH}"
      break
    fi
  done
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Load .env without overriding already-exported shell variables (safely expands ${VAR})
ENV_FILE_PRESENT="false"
if [[ -f "${REPO_ROOT}/.env" ]]; then
  ENV_FILE_PRESENT="true"
  while IFS= read -r raw_line || [[ -n "${raw_line}" ]]; do
    line="${raw_line%$'\r'}"
    [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# || "${line}" != *=* ]] && continue
    line="${line#export }"
    key="${line%%=*}"
    val="${line#*=}"
    key="${key//[[:space:]]/}"
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    if [[ "${val}" == \"*\" && "${val}" == *\" && ${#val} -ge 2 ]]; then
      val="${val:1:${#val}-2}"
    elif [[ "${val}" == \'*\' && "${val}" == *\' && ${#val} -ge 2 ]]; then
      val="${val:1:${#val}-2}"
    fi
    exp_iter=0
    while [[ "${val}" =~ \$\{([A-Za-z_][A-Za-z0-9_]*)\} && ${exp_iter} -lt 10 ]]; do
      ref_name="${BASH_REMATCH[1]}"
      ref_val="${!ref_name:-}"
      token="\${${ref_name}}"
      if [[ -z "${ref_val}" || "${ref_val}" == *"${token}"* ]]; then
        break
      fi
      val="${val%%"${token}"*}${ref_val}${val#*"${token}"}"
      exp_iter=$((exp_iter + 1))
    done
    [[ "${val}" =~ \$\{[A-Za-z_][A-Za-z0-9_]*\} ]] && continue
    if [[ -z "${!key:-}" ]]; then
      export "${key}=${val}"
    fi
  done < "${REPO_ROOT}/.env"
fi

STRICT_GCP="false"
PROJECT_OVERRIDE=""
REGION="${GOOGLE_CLOUD_REGION:-${SPANNER_REGION:-asia-southeast3}}"
SECONDARY_REGION="${GOOGLE_CLOUD_SECONDARY_REGION:-asia-southeast1}"
VERTEX_AI_LOCATION="${VERTEX_AI_LOCATION:-${VERTEX_AI_EMBEDDING_LOCATION:-asia-southeast1}}"
MODEL_ARMOR_LOCATION="${MODEL_ARMOR_LOCATION:-asia-southeast1}"
SPANNER_INSTANCE="${SPANNER_INSTANCE:-sdlc-kg-enterprise}"
SPANNER_DATABASE="${SPANNER_DATABASE:-sdlc_knowledge_graph}"
SPANNER_PROCESSING_UNITS="${SPANNER_PROCESSING_UNITS:-100}"

usage() {
  cat <<'EOF'
Usage: bash scripts/preflight_check.sh [OPTIONS]

Options:
  --strict-gcp              Exit non-zero if gcloud/ADC is unauthenticated,
                            active project is unset/placeholder, or Python < 3.10
  --project <id>            Override GCP project ID to check
  --region <region>         Primary Spanner region (default: asia-southeast3)
  --secondary-region <reg>  Secondary AI/DR region (default: asia-southeast1)
  --vertex-location <loc>   Regional Vertex AI endpoint (default: asia-southeast1)
  -h, --help                Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --strict-gcp)
      STRICT_GCP="true"
      shift
      ;;
    --project)
      if [[ $# -lt 2 ]]; then
        echo "[ERROR] --project requires a value." >&2
        exit 1
      fi
      PROJECT_OVERRIDE="$2"
      shift 2
      ;;
    --region)
      if [[ $# -lt 2 ]]; then
        echo "[ERROR] --region requires a value." >&2
        exit 1
      fi
      REGION="$2"
      shift 2
      ;;
    --secondary-region)
      if [[ $# -lt 2 ]]; then
        echo "[ERROR] --secondary-region requires a value." >&2
        exit 1
      fi
      SECONDARY_REGION="$2"
      shift 2
      ;;
    --vertex-location)
      if [[ $# -lt 2 ]]; then
        echo "[ERROR] --vertex-location requires a value." >&2
        exit 1
      fi
      VERTEX_AI_LOCATION="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

PASS_COUNT=0
WARN_COUNT=0
INFO_COUNT=0
STRICT_BLOCKERS=0

log_pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  echo "  [PASS] $*"
}

log_warn() {
  WARN_COUNT=$((WARN_COUNT + 1))
  echo "  [WARN] $*"
}

log_info() {
  INFO_COUNT=$((INFO_COUNT + 1))
  echo "  [INFO] $*"
}

log_fail_strict() {
  STRICT_BLOCKERS=$((STRICT_BLOCKERS + 1))
  if [[ "${STRICT_GCP}" == "true" ]]; then
    echo "  [FAIL] $*"
  else
    WARN_COUNT=$((WARN_COUNT + 1))
    echo "  [WARN] $*"
  fi
}

run_timed() {
  local secs="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "${secs}" "$@"
  else
    "$@"
  fi
}

echo "=============================================================================="
echo "Cloud Spanner Enterprise SDLC Workshop — Preflight Readiness Check"
echo "  Mode              : $([[ "${STRICT_GCP}" == "true" ]] && echo "STRICT GCP (--strict-gcp)" || echo "OFFLINE-SAFE (non-blocking)")"
echo "  Workspace         : ${REPO_ROOT}"
echo "=============================================================================="

# ------------------------------------------------------------------------------
# 1. Python >= 3.10 & .venv Status
# ------------------------------------------------------------------------------
echo ""
echo "[1/6] Python Runtime & Virtualenv (.venv)"

PYTHON_BIN="${PYTHON:-}"
VENV_PYTHON="${REPO_ROOT}/.venv/bin/python"
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${VENV_PYTHON}" ]]; then
    PYTHON_BIN="${VENV_PYTHON}"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  fi
fi

if [[ -z "${PYTHON_BIN}" ]] || { ! command -v "${PYTHON_BIN}" >/dev/null 2>&1 && [[ ! -x "${PYTHON_BIN}" ]]; }; then
  log_fail_strict "Python 3.10+ not found on PATH. Install Python >= 3.10 before running workshop labs."
else
  PY_VER_INFO="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{int(sys.version_info >= (3, 10))}")' 2>/dev/null || echo "unknown|0")"
  PY_VER="${PY_VER_INFO%%|*}"
  PY_OK="${PY_VER_INFO##*|}"
  if [[ "${PY_OK}" == "1" ]]; then
    log_pass "Python ${PY_VER} (${PYTHON_BIN}) satisfies >= 3.10"
  else
    log_fail_strict "Python ${PY_VER} (${PYTHON_BIN}) is below required 3.10 minimum"
  fi
fi

if [[ -x "${VENV_PYTHON}" ]]; then
  VENV_VER="$("${VENV_PYTHON}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")' 2>/dev/null || echo "unknown")"
  log_pass "PEP-668-safe virtualenv ready at .venv/ (Python ${VENV_VER})"
  MISSING_PKGS="$("${VENV_PYTHON}" - <<'PYEOF' 2>/dev/null || echo "check_failed"
missing = []
for mod in ("google.cloud.spanner", "google.genai", "yaml"):
    try:
        __import__(mod)
    except Exception:
        missing.append(mod)
print(",".join(missing))
PYEOF
)"
  if [[ -z "${MISSING_PKGS}" ]]; then
    log_pass "Live GCP Python SDKs installed in .venv/ (google-cloud-spanner, google-genai, pyyaml)"
  else
    log_info "Optional live GCP SDKs not yet installed in .venv/ (${MISSING_PKGS}) — run 'make install' before live GCP labs."
  fi
else
  log_info ".venv/ not created yet — all offline stdlib labs & tests work immediately; run 'make install' (PEP-668 safe) before live GCP labs."
fi

# ------------------------------------------------------------------------------
# 2. gcloud CLI Availability, Account Auth & ADC Status
# ------------------------------------------------------------------------------
echo ""
echo "[2/6] Google Cloud CLI (gcloud) & Authentication"

GCLOUD_AVAILABLE="false"
if command -v gcloud >/dev/null 2>&1; then
  GCLOUD_AVAILABLE="true"
  GCLOUD_PATH="$(command -v gcloud)"
  log_pass "gcloud CLI available (${GCLOUD_PATH})"

  ACTIVE_ACCOUNT="$(run_timed 10 gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | head -n 1 || true)"
  if [[ -n "${ACTIVE_ACCOUNT}" ]]; then
    log_pass "gcloud active account: ${ACTIVE_ACCOUNT}"
    if [[ "${STRICT_GCP}" == "true" ]]; then
      if run_timed 15 gcloud auth print-access-token >/dev/null 2>&1; then
        log_pass "gcloud CLI access token verified active"
      else
        log_fail_strict "gcloud CLI credentials need reauthentication — run 'gcloud auth login ${ACTIVE_ACCOUNT}'."
      fi
    fi
  else
    log_fail_strict "No active gcloud account — run 'gcloud auth login' before live GCP labs."
  fi

  # Fast ADC check (non-blocking offline; validates live token in --strict-gcp)
  ADC_FILE="${GOOGLE_APPLICATION_CREDENTIALS:-${CLOUDSDK_CONFIG:-${HOME}/.config/gcloud}/application_default_credentials.json}"
  if [[ "${STRICT_GCP}" == "true" ]]; then
    if run_timed 15 gcloud auth application-default print-access-token >/dev/null 2>&1; then
      log_pass "Application Default Credentials (ADC) verified active"
    else
      log_fail_strict "Application Default Credentials (ADC) not authenticated — run 'gcloud auth application-default login'."
    fi
  else
    if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" && -r "${GOOGLE_APPLICATION_CREDENTIALS}" ]]; then
      log_pass "ADC configured via GOOGLE_APPLICATION_CREDENTIALS (${GOOGLE_APPLICATION_CREDENTIALS})"
    elif [[ -r "${ADC_FILE}" && -s "${ADC_FILE}" ]]; then
      log_pass "ADC credentials file present (${ADC_FILE})"
    elif run_timed 8 gcloud auth application-default print-access-token >/dev/null 2>&1; then
      log_pass "ADC active via workload/metadata identity"
    else
      log_fail_strict "ADC not configured yet — run 'gcloud auth application-default login' before live Vertex AI / Spanner Python runs."
    fi
  fi
else
  log_fail_strict "gcloud CLI not installed on PATH — offline labs work without it; install Google Cloud SDK before live GCP labs."
fi

# ------------------------------------------------------------------------------
# 3. Active GCP Project Safety Check
# ------------------------------------------------------------------------------
echo ""
echo "[3/6] Active GCP Project & Sandbox Isolation"

ACTIVE_PROJECT=""
PROJECT_SOURCE=""

if [[ -n "${PROJECT_OVERRIDE}" ]]; then
  ACTIVE_PROJECT="${PROJECT_OVERRIDE}"
  PROJECT_SOURCE="--project flag"
elif [[ -n "${GOOGLE_CLOUD_PROJECT:-}" && ! "${GOOGLE_CLOUD_PROJECT}" =~ \$\{[A-Za-z_][A-Za-z0-9_]*\} ]]; then
  ACTIVE_PROJECT="${GOOGLE_CLOUD_PROJECT}"
  PROJECT_SOURCE="GOOGLE_CLOUD_PROJECT"
elif [[ -n "${SPANNER_PROJECT:-}" && ! "${SPANNER_PROJECT}" =~ \$\{[A-Za-z_][A-Za-z0-9_]*\} ]]; then
  ACTIVE_PROJECT="${SPANNER_PROJECT}"
  PROJECT_SOURCE="SPANNER_PROJECT"
elif [[ "${GCLOUD_AVAILABLE}" == "true" ]]; then
  GCLOUD_PROJ="$(run_timed 3 gcloud config get-value project 2>/dev/null | head -n 1 || true)"
  if [[ -n "${GCLOUD_PROJ}" && "${GCLOUD_PROJ}" != "(unset)" ]]; then
    ACTIVE_PROJECT="${GCLOUD_PROJ}"
    PROJECT_SOURCE="gcloud config"
  fi
fi

if [[ -z "${ACTIVE_PROJECT}" || "${ACTIVE_PROJECT}" == "(unset)" || "${ACTIVE_PROJECT}" == "your-gcp-project-id" || "${ACTIVE_PROJECT}" == "YOUR_GCP_PROJECT_ID" || "${ACTIVE_PROJECT}" == "demo-project" || "${ACTIVE_PROJECT}" == REPLACE_WITH_* ]]; then
  log_fail_strict "Active GCP project is unset or placeholder ('${ACTIVE_PROJECT:-unset}'). Copy .env.example to .env and set GOOGLE_CLOUD_PROJECT=<your-sandbox-project>."
else
  proj_lower="${ACTIVE_PROJECT,,}"
  if [[ "${ACTIVE_PROJECT}" == *:* ]] || [[ "${proj_lower}" =~ (^|[-_.])(shared|prod|production|corp|workstation|devbox|default|staging)([-_.]|$) ]]; then
    log_warn "Active project '${ACTIVE_PROJECT}' (${PROJECT_SOURCE}) looks like a shared, corporate, production, or workstation-default project! Switch to an isolated workshop sandbox project before running 'make provision-gcp'."
  else
    log_pass "Active GCP project: ${ACTIVE_PROJECT} (source: ${PROJECT_SOURCE})"
  fi
fi

if [[ "${ENV_FILE_PRESENT}" == "true" ]]; then
  log_pass "Local .env configuration file detected"
else
  log_info "No .env file found yet — run 'cp .env.example .env' and set GOOGLE_CLOUD_PROJECT before live GCP labs."
fi

# ------------------------------------------------------------------------------
# 4. Bangkok (asia-southeast3) & Singapore (asia-southeast1) Regional Defaults
# ------------------------------------------------------------------------------
echo ""
echo "[4/6] Regional Architecture Defaults (Bangkok Primary + Singapore AI/DR)"

if [[ "${REGION}" == "asia-southeast3" ]]; then
  log_pass "Primary Spanner & Cloud Run region: ${REGION} (Bangkok — regional-asia-southeast3, Thailand PDPA resident)"
elif [[ "${REGION}" == "asia-southeast1" ]]; then
  log_info "Primary Spanner region overridden to fallback: ${REGION} (Singapore — regional-asia-southeast1)"
else
  log_warn "Primary Spanner region set to non-default '${REGION}' (workshop locked default: asia-southeast3 Bangkok)"
fi

if [[ "${SECONDARY_REGION}" == "asia-southeast1" ]]; then
  log_pass "Secondary DR & companion region: ${SECONDARY_REGION} (Singapore — regional-asia-southeast1)"
else
  log_info "Secondary DR region overridden to '${SECONDARY_REGION}' (workshop default: asia-southeast1 Singapore)"
fi

vertex_lower="${VERTEX_AI_LOCATION,,}"
if [[ "${vertex_lower}" == "global" || "${vertex_lower}" == "us" || "${vertex_lower}" == "eu" ]]; then
  log_fail_strict "INV-09 violation: VERTEX_AI_LOCATION='${VERTEX_AI_LOCATION}' is invalid for gemini-embedding-001. Set VERTEX_AI_LOCATION=asia-southeast1."
else
  log_pass "Vertex AI regional endpoint: ${VERTEX_AI_LOCATION} (INV-09 compliant for gemini-embedding-001) | Model Armor: ${MODEL_ARMOR_LOCATION}"
fi

log_info "Sandbox target: instance=${SPANNER_INSTANCE} | db=${SPANNER_DATABASE} | PUs=${SPANNER_PROCESSING_UNITS} (~\$1.15/8h workshop)"

# ------------------------------------------------------------------------------
# 5. MCP Toolbox Binary or npx Availability
# ------------------------------------------------------------------------------
echo ""
echo "[5/6] MCP Toolbox for Databases (Native Binary or npx)"

LOCAL_TOOLBOX="${REPO_ROOT}/solution/mcp/bin/toolbox"
if [[ -x "${LOCAL_TOOLBOX}" ]]; then
  log_pass "Native MCP Toolbox binary installed: solution/mcp/bin/toolbox"
elif command -v toolbox >/dev/null 2>&1; then
  log_pass "Native MCP Toolbox binary found on PATH: $(command -v toolbox)"
elif command -v npx >/dev/null 2>&1; then
  log_pass "npx available for MCP Toolbox (npx -y @toolbox-sdk/server); native binary also installable via 'bash solution/mcp/setup_local_toolbox.sh'"
else
  log_info "Neither native MCP Toolbox binary nor npx found yet — 'make demo-mcp-mock' runs fully offline; run 'bash solution/mcp/setup_local_toolbox.sh' for Zero-Docker native MCP Toolbox."
fi

# ------------------------------------------------------------------------------
# 6. Docker Availability (Zero-Docker Path Fully Supported)
# ------------------------------------------------------------------------------
echo ""
echo "[6/6] Docker Availability (Optional — Zero-Docker Path Supported)"

if ! command -v docker >/dev/null 2>&1; then
  log_info "Docker not installed — Zero-Docker path fully supported via make lab1-no-docker & native MCP Toolbox!"
else
  if run_timed 2 docker info >/dev/null 2>&1; then
    log_pass "Docker installed and daemon running (optional Lab 1 local Neo4j CE supported; Zero-Docker path also available via make lab1-no-docker)"
  else
    log_info "Docker CLI installed but daemon not reachable — Zero-Docker path fully supported via make lab1-no-docker & native MCP Toolbox!"
  fi
fi

# ------------------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------------------
echo ""
echo "=============================================================================="
echo "Preflight Summary: ${PASS_COUNT} passed | ${WARN_COUNT} warning(s) | ${INFO_COUNT} info | ${STRICT_BLOCKERS} live-GCP blocker(s)"
if [[ "${STRICT_GCP}" == "true" && ${STRICT_BLOCKERS} -gt 0 ]]; then
  echo "[FAIL] Preflight failed in --strict-gcp mode (${STRICT_BLOCKERS} blocker(s) must be resolved for live GCP)."
  echo "=============================================================================="
  exit 1
fi
echo "[OK] Ready for offline labs now: make test | make lab1-no-docker | make demo-mcp-mock"
if [[ ${STRICT_BLOCKERS} -gt 0 ]]; then
  echo "     Before live GCP labs, resolve the warning(s) above and verify with:"
  echo "       bash scripts/preflight_check.sh --strict-gcp"
fi
echo "=============================================================================="
exit 0
