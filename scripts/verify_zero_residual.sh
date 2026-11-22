#!/usr/bin/env bash
# =============================================================================
# Cloud Spanner Enterprise SDLC Workshop — Zero-Residual Post-Teardown Audit
# =============================================================================
# Audits a GCP project after teardown to confirm ZERO residual workshop
# resources remain across Bangkok (`asia-southeast3`) and Singapore
# (`asia-southeast1`):
#   1. Cloud Spanner instances (`$SPANNER_INSTANCE`, `$SPANNER_SECONDARY_INSTANCE`)
#      and any remaining backups on either instance
#   2. Cloud Run MCP Toolbox services (`${SERVICE_PREFIX}-impact-analyst`,
#      `${SERVICE_PREFIX}-adr-curator`, `${SERVICE_PREFIX}-security-auditor`)
#   3. Secret Manager secrets (`${SECRET_PREFIX}-impact-analyst`,
#      `${SECRET_PREFIX}-adr-curator`, `${SECRET_PREFIX}-security-auditor`)
#   4. All 7 workshop Service Accounts (`sa-impact-analyst`, `sa-adr-curator`,
#      `sa-security-auditor`, `sa-adk-agent-runner`, `sa-fgac-readonly`,
#      `sa-fgac-adr-writer`, `sa-fgac-security-auditor`)
#   5. Singapore Cloud DLP inspect template (`$DLP_TEMPLATE_ID`) &
#      Model Armor regional template (`$MODEL_ARMOR_TEMPLATE_ID`)
#   6. Workshop GCS staging bucket (`gs://${GCS_BUCKET}`)
#
# Exits 0 ONLY when 0 residual resources remain (or in `--dry-run` preview mode).
#
# Usage:
#   bash scripts/verify_zero_residual.sh --dry-run
#   bash scripts/verify_zero_residual.sh --project my-sandbox-proj
#   bash scripts/verify_zero_residual.sh --project my-sandbox-proj \
#     --instance sdlc-kg-enterprise --dr-instance sdlc-kg-dr-singapore
# =============================================================================
set -euo pipefail

if ! command -v gcloud >/dev/null 2>&1; then
  for _sdk_dir in "${HOME}/google-cloud-sdk/bin" "${HOME}/.local/bin"; do
    if [[ -x "${_sdk_dir}/gcloud" ]]; then
      export PATH="${_sdk_dir}:${PATH}"
      break
    fi
  done
fi

export CLOUDSDK_CORE_DISABLE_PROMPTS=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Load .env without overriding already-exported shell variables (safely expands ${VAR})
if [[ -f "${REPO_ROOT}/.env" ]]; then
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

# Locked defaults aligned with WORKSHOP_SPEC.json & setup scripts
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-${SPANNER_PROJECT:-}}"
REGION="${GOOGLE_CLOUD_REGION:-${SPANNER_REGION:-asia-southeast3}}"
SECONDARY_REGION="${GOOGLE_CLOUD_SECONDARY_REGION:-asia-southeast1}"
MODEL_ARMOR_LOCATION="${MODEL_ARMOR_LOCATION:-}"
SPANNER_INSTANCE="${SPANNER_INSTANCE:-sdlc-kg-enterprise}"
SPANNER_SECONDARY_INSTANCE="${SPANNER_SECONDARY_INSTANCE:-sdlc-kg-dr-singapore}"
SERVICE_PREFIX="${SERVICE_PREFIX:-sdlc-mcp}"
SECRET_PREFIX="${SECRET_PREFIX:-sdlc-tools}"
DLP_TEMPLATE_ID="${DLP_TEMPLATE_ID:-sdlc-pci-secret-inspector}"
MODEL_ARMOR_TEMPLATE_ID="${MODEL_ARMOR_TEMPLATE_ID:-sdlc-agent-guardrail-sg}"
GCS_BUCKET="${GCS_BUCKET:-}"
DRY_RUN="${DRY_RUN:-false}"

usage() {
  cat <<'EOF'
Usage: bash scripts/verify_zero_residual.sh [OPTIONS]

Audits a GCP project after workshop teardown and exits 0 ONLY when zero
residual workshop resources remain across Bangkok & Singapore.

Options:
  --dry-run                    Print all audit commands without calling GCP APIs
  --project <id>               GCP Project ID (default: $GOOGLE_CLOUD_PROJECT)
  --instance <id>              Primary Spanner instance ID (default: sdlc-kg-enterprise)
  --dr-instance <id>           Secondary Singapore DR Spanner instance (default: sdlc-kg-dr-singapore)
  --service-prefix <prefix>    Cloud Run MCP service prefix (default: sdlc-mcp)
  --secret-prefix <prefix>     Secret Manager secret prefix (default: sdlc-tools)
  --region <region>            Primary region (default: asia-southeast3 Bangkok)
  --secondary-region <region>  Secondary AI/DR region (default: asia-southeast1 Singapore)
  --bucket <name>              Workshop GCS staging bucket name (default: <project>-sdlc-kg-<region>)
  --dlp-template <id>          Cloud DLP inspect template ID (default: sdlc-pci-secret-inspector)
  --model-armor-template <id>  Model Armor template ID (default: sdlc-agent-guardrail-sg)
  -h, --help                   Show this help message
EOF
}

require_arg() {
  if [[ "$2" -lt 2 ]]; then
    echo "[ERROR] Option $1 requires a value." >&2
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --project)
      require_arg "$1" "$#"
      PROJECT_ID="$2"
      shift 2
      ;;
    --instance)
      require_arg "$1" "$#"
      SPANNER_INSTANCE="$2"
      shift 2
      ;;
    --dr-instance)
      require_arg "$1" "$#"
      SPANNER_SECONDARY_INSTANCE="$2"
      shift 2
      ;;
    --service-prefix)
      require_arg "$1" "$#"
      SERVICE_PREFIX="$2"
      shift 2
      ;;
    --secret-prefix)
      require_arg "$1" "$#"
      SECRET_PREFIX="$2"
      shift 2
      ;;
    --region)
      require_arg "$1" "$#"
      REGION="$2"
      shift 2
      ;;
    --secondary-region)
      require_arg "$1" "$#"
      SECONDARY_REGION="$2"
      shift 2
      ;;
    --bucket)
      require_arg "$1" "$#"
      GCS_BUCKET="$2"
      shift 2
      ;;
    --dlp-template)
      require_arg "$1" "$#"
      DLP_TEMPLATE_ID="$2"
      shift 2
      ;;
    --model-armor-template)
      require_arg "$1" "$#"
      MODEL_ARMOR_TEMPLATE_ID="$2"
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

if [[ -z "${MODEL_ARMOR_LOCATION}" ]]; then
  MODEL_ARMOR_LOCATION="${SECONDARY_REGION}"
fi
MODEL_ARMOR_ENDPOINT="https://modelarmor.${MODEL_ARMOR_LOCATION}.rep.googleapis.com/"

if [[ -z "${PROJECT_ID}" ]]; then
  if [[ "${DRY_RUN}" == "true" ]]; then
    PROJECT_ID="demo-project"
  else
    if ! command -v gcloud >/dev/null 2>&1; then
      echo "[ERROR] gcloud CLI not found on PATH." >&2
      exit 1
    fi
    PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
    if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
      echo "[ERROR] GOOGLE_CLOUD_PROJECT is not set. Pass --project <id> or set .env." >&2
      exit 1
    fi
  fi
fi

if [[ -z "${GCS_BUCKET}" ]]; then
  GCS_BUCKET="${PROJECT_ID}-sdlc-kg-${REGION}"
fi
GCS_BUCKET="${GCS_BUCKET#gs://}"
GCS_BUCKET="${GCS_BUCKET%%/*}"

PERSONAS=("impact-analyst" "adr-curator" "security-auditor")
WORKSHOP_SAS=(
  "sa-impact-analyst"
  "sa-adr-curator"
  "sa-security-auditor"
  "sa-adk-agent-runner"
  "sa-fgac-readonly"
  "sa-fgac-adr-writer"
  "sa-fgac-security-auditor"
)

RESIDUAL_COUNT=0
AUDIT_ERRORS=0
RESIDUAL_ITEMS=()

record_clean() {
  echo "  [CLEAN]    $1: $2"
}

record_residual() {
  RESIDUAL_COUNT=$((RESIDUAL_COUNT + 1))
  RESIDUAL_ITEMS+=("$1: $2")
  echo "  [RESIDUAL] $1: $2"
}

record_error() {
  AUDIT_ERRORS=$((AUDIT_ERRORS + 1))
  echo "  [ERROR]    $1: $2" >&2
}

is_absent_error() {
  local err_text="$1"
  grep -qiE "NOT_FOUND|not found|not be found|cannot be found|does not exist|may not exist|already deleted|404|Cannot find|Unknown service account|BucketNotFoundException|matched no objects|SERVICE_DISABLED|has not been used|has not been enabled|is not enabled" <<< "${err_text}"
}

check_resource_absent() {
  local category="$1"
  local label="$2"
  shift 2

  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '  [DRY-RUN]'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi

  local err_out
  if err_out="$("$@" 2>&1 >/dev/null)"; then
    record_residual "${category}" "${label}"
  elif is_absent_error "${err_out}"; then
    record_clean "${category}" "${label}"
  else
    record_error "${category}" "${label} (${err_out%%$'\n'*})"
  fi
}

echo "=============================================================================="
echo "Cloud Spanner Enterprise Workshop — Zero-Residual Teardown Audit"
echo "  Mode              : $([[ "${DRY_RUN}" == "true" ]] && echo "DRY-RUN" || echo "LIVE GCP")"
echo "  Project           : ${PROJECT_ID}"
echo "  Regions Audited   : ${REGION} (Bangkok) & ${SECONDARY_REGION} (Singapore)"
echo "  Spanner Instances : ${SPANNER_INSTANCE}, ${SPANNER_SECONDARY_INSTANCE}"
echo "  Cloud Run Prefix  : ${SERVICE_PREFIX}-*"
echo "  Secret Prefix     : ${SECRET_PREFIX}-*"
echo "  DLP / Model Armor : ${DLP_TEMPLATE_ID} / ${MODEL_ARMOR_TEMPLATE_ID} (${MODEL_ARMOR_LOCATION})"
echo "  GCS Staging Bucket: gs://${GCS_BUCKET}"
echo "=============================================================================="

ACCESS_TOKEN=""
if [[ "${DRY_RUN}" != "true" ]]; then
  if ! command -v gcloud >/dev/null 2>&1; then
    echo "[ERROR] gcloud CLI is required for live zero-residual audit." >&2
    exit 1
  fi
  if ! ACCESS_TOKEN="$(gcloud auth print-access-token 2>/dev/null)" || [[ -z "${ACCESS_TOKEN}" ]]; then
    echo "[ERROR] Unable to obtain gcloud access token. Run 'gcloud auth login' first." >&2
    exit 1
  fi
fi

# ------------------------------------------------------------------------------
# 1. Cloud Spanner Instances & Backups (Primary Bangkok + Secondary Singapore DR)
# ------------------------------------------------------------------------------
echo ""
echo "[1/6] Auditing Cloud Spanner instances & backups..."
for inst in "${SPANNER_INSTANCE}" "${SPANNER_SECONDARY_INSTANCE}"; do
  [[ -z "${inst}" ]] && continue
  check_resource_absent "Spanner Instance" "${inst}" \
    gcloud spanner instances describe "${inst}" --project="${PROJECT_ID}"

  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '  [DRY-RUN]'
    printf ' %q' gcloud spanner backups list --project="${PROJECT_ID}" --instance="${inst}" --format="value(name.basename())"
    printf '\n'
  else
    if local_bkps="$(gcloud spanner backups list --project="${PROJECT_ID}" --instance="${inst}" --format="value(name.basename())" 2>&1)"; then
      if [[ -n "${local_bkps}" ]]; then
        while IFS= read -r bkp; do
          [[ -z "${bkp}" ]] && continue
          record_residual "Spanner Backup" "${inst}/${bkp}"
        done <<< "${local_bkps}"
      else
        record_clean "Spanner Backups" "${inst} (0 backups)"
      fi
    elif is_absent_error "${local_bkps}"; then
      record_clean "Spanner Backups" "${inst} (instance absent)"
    else
      record_error "Spanner Backups" "${inst} (${local_bkps%%$'\n'*})"
    fi
  fi
done

# ------------------------------------------------------------------------------
# 2. Cloud Run MCP Toolbox Services
# ------------------------------------------------------------------------------
echo ""
echo "[2/6] Auditing Cloud Run MCP Toolbox services..."
CLOUD_RUN_REGIONS=("${REGION}")
if [[ "${SECONDARY_REGION}" != "${REGION}" ]]; then
  CLOUD_RUN_REGIONS+=("${SECONDARY_REGION}")
fi

for run_reg in "${CLOUD_RUN_REGIONS[@]}"; do
  for persona in "${PERSONAS[@]}"; do
    svc_name="${SERVICE_PREFIX}-${persona}"
    check_resource_absent "Cloud Run (${run_reg})" "${svc_name}" \
      gcloud run services describe "${svc_name}" --project="${PROJECT_ID}" --region="${run_reg}"
  done
done

# ------------------------------------------------------------------------------
# 3. Secret Manager Secrets
# ------------------------------------------------------------------------------
echo ""
echo "[3/6] Auditing Secret Manager persona secrets..."
for persona in "${PERSONAS[@]}"; do
  secret_name="${SECRET_PREFIX}-${persona}"
  check_resource_absent "Secret Manager" "${secret_name}" \
    gcloud secrets describe "${secret_name}" --project="${PROJECT_ID}"
done

# ------------------------------------------------------------------------------
# 4. Workshop Service Accounts (All 7 Layer-1 & Layer-2 SAs)
#    Note: `gcloud iam service-accounts describe` returns PERMISSION_DENIED
#    "(or it may not exist)" on deleted SAs to prevent enumeration, so live mode
#    checks authoritative `gcloud iam service-accounts list` membership instead.
# ------------------------------------------------------------------------------
echo ""
echo "[4/6] Auditing all 7 workshop Service Accounts..."
if [[ "${DRY_RUN}" == "true" ]]; then
  for sa_name in "${WORKSHOP_SAS[@]}"; do
    sa_email="${sa_name}@${PROJECT_ID}.iam.gserviceaccount.com"
    check_resource_absent "Service Account" "${sa_email}" \
      gcloud iam service-accounts describe "${sa_email}" --project="${PROJECT_ID}"
  done
else
  EXISTING_SAS=""
  if EXISTING_SAS="$(gcloud iam service-accounts list --project="${PROJECT_ID}" --format="value(email)" 2>&1)"; then
    for sa_name in "${WORKSHOP_SAS[@]}"; do
      sa_email="${sa_name}@${PROJECT_ID}.iam.gserviceaccount.com"
      if grep -Fxq "${sa_email}" <<< "${EXISTING_SAS}"; then
        record_residual "Service Account" "${sa_email}"
      else
        record_clean "Service Account" "${sa_email}"
      fi
    done
  else
    record_error "Service Accounts" "Failed to list service accounts (${EXISTING_SAS%%$'\n'*})"
  fi
fi

# ------------------------------------------------------------------------------
# 5. Singapore Cloud DLP Inspect Template & Model Armor Template
# ------------------------------------------------------------------------------
echo ""
echo "[5/6] Auditing Cloud DLP & Model Armor templates (${MODEL_ARMOR_LOCATION})..."

DLP_URL="https://dlp.googleapis.com/v2/projects/${PROJECT_ID}/locations/${MODEL_ARMOR_LOCATION}/inspectTemplates/${DLP_TEMPLATE_ID}"
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "  [DRY-RUN] curl -sS -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer <TOKEN>' -H 'x-goog-user-project: ${PROJECT_ID}' ${DLP_URL}"
else
  TMP_DLP_BODY="$(mktemp /tmp/dlp_audit.XXXXXX.json)"
  trap 'rm -f "${TMP_DLP_BODY}"' EXIT
  DLP_HTTP_CODE="$(curl -sS -o "${TMP_DLP_BODY}" -w "%{http_code}" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "x-goog-user-project: ${PROJECT_ID}" \
    "${DLP_URL}" || echo "000")"
  case "${DLP_HTTP_CODE}" in
    200)
      record_residual "Cloud DLP Template (${MODEL_ARMOR_LOCATION})" "${DLP_TEMPLATE_ID}"
      ;;
    404)
      record_clean "Cloud DLP Template (${MODEL_ARMOR_LOCATION})" "${DLP_TEMPLATE_ID}"
      ;;
    403)
      if grep -qiE "SERVICE_DISABLED|has not been used|is disabled" "${TMP_DLP_BODY}" 2>/dev/null; then
        record_clean "Cloud DLP Template (${MODEL_ARMOR_LOCATION})" "${DLP_TEMPLATE_ID} (DLP API disabled)"
      else
        record_error "Cloud DLP Template (${MODEL_ARMOR_LOCATION})" "${DLP_TEMPLATE_ID} (HTTP 403 permission denied)"
      fi
      ;;
    *)
      record_error "Cloud DLP Template (${MODEL_ARMOR_LOCATION})" "${DLP_TEMPLATE_ID} (HTTP ${DLP_HTTP_CODE})"
      ;;
  esac
  rm -f "${TMP_DLP_BODY}"
fi

check_resource_absent "Model Armor Template (${MODEL_ARMOR_LOCATION})" "${MODEL_ARMOR_TEMPLATE_ID}" \
  env "CLOUDSDK_API_ENDPOINT_OVERRIDES_MODELARMOR=${MODEL_ARMOR_ENDPOINT}" \
  gcloud model-armor templates describe "${MODEL_ARMOR_TEMPLATE_ID}" \
  --project="${PROJECT_ID}" \
  --location="${MODEL_ARMOR_LOCATION}"

# ------------------------------------------------------------------------------
# 6. Workshop GCS Staging Bucket
# ------------------------------------------------------------------------------
echo ""
echo "[6/6] Auditing workshop GCS staging bucket..."
check_resource_absent "GCS Bucket" "gs://${GCS_BUCKET}" \
  gcloud storage buckets describe "gs://${GCS_BUCKET}" --project="${PROJECT_ID}"

# ------------------------------------------------------------------------------
# Final Verdict
# ------------------------------------------------------------------------------
echo ""
echo "=============================================================================="
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Zero-residual audit plan complete across all 6 resource categories (0 live calls)."
  echo "=============================================================================="
  exit 0
fi

if (( RESIDUAL_COUNT == 0 && AUDIT_ERRORS == 0 )); then
  echo "[PASS] ZERO residual workshop resources remain in project '${PROJECT_ID}' (${REGION} & ${SECONDARY_REGION})."
  echo "=============================================================================="
  exit 0
fi

echo "[FAIL] Teardown audit failed: ${RESIDUAL_COUNT} residual resource(s), ${AUDIT_ERRORS} audit error(s)." >&2
if (( RESIDUAL_COUNT > 0 )); then
  echo "Residual resources still present:" >&2
  for item in "${RESIDUAL_ITEMS[@]}"; do
    echo "  - ${item}" >&2
  done
  echo "Re-run full teardown: make cleanup-all-gcp" >&2
fi
echo "=============================================================================="
exit 1
