#!/usr/bin/env bash
# ==============================================================================
# Path A Production-Scale Migration: GCS Staging + Sequential Dataflow Bulk Load
# Template: GCS_Text_to_Cloud_Spanner (Nodes Stage 1 -> WAIT -> Edges Stage 2)
#
# Regional Residency & Fallback Contract:
#   - Primary Cloud Storage bucket & Spanner target: Bangkok (asia-southeast3)
#     for Thailand PDPA primary operational data residency.
#   - Primary Dataflow execution region: --region=asia-southeast3
#   - Secondary fallback region: --region=asia-southeast1 (Singapore, ~25ms RTT)
#     used automatically if asia-southeast3 lacks Dataflow worker quota or if
#     --fallback-region is explicitly requested.
#
# Usage:
#   bash solution/pipelines/dataflow_migration.sh --dry-run
#   GOOGLE_CLOUD_PROJECT=my-proj GCS_BUCKET=my-proj-sdlc-staging-bkk \
#     bash solution/pipelines/dataflow_migration.sh
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Load .env defaults if present (without clobbering already-exported env vars; safely expands ${VAR})
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

# Locked workshop defaults (Bangkok primary + Singapore fallback)
GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-${SPANNER_PROJECT:-}}"
SPANNER_REGION="${SPANNER_REGION:-${GOOGLE_CLOUD_REGION:-asia-southeast3}}"
DATAFLOW_REGION="${DATAFLOW_REGION:-${SPANNER_REGION}}"
DATAFLOW_FALLBACK_REGION="${DATAFLOW_FALLBACK_REGION:-asia-southeast1}"
SPANNER_INSTANCE="${SPANNER_INSTANCE:-sdlc-kg-enterprise}"
SPANNER_DATABASE="${SPANNER_DATABASE:-sdlc_knowledge_graph}"
SPANNER_PROCESSING_UNITS="${SPANNER_PROCESSING_UNITS:-100}"
GCS_BUCKET="${GCS_BUCKET:-${GOOGLE_CLOUD_PROJECT:-demo-project}-sdlc-kg-${SPANNER_REGION}}"
GCS_PREFIX="${GCS_PREFIX:-neo4j-export}"
DATAFLOW_TEMPLATE_GCS="${DATAFLOW_TEMPLATE_GCS:-gs://dataflow-templates/latest/GCS_Text_to_Cloud_Spanner}"
WORKER_MACHINE_TYPE="${WORKER_MACHINE_TYPE:-n1-standard-2}"
DRY_RUN="${DRY_RUN:-false}"
USE_FALLBACK_REGION="false"
STAGING_DIR=""
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-20}"

# Cap Dataflow worker vCPUs to 4x-6x Spanner nodes (1 worker for 100-PU sandbox)
if [[ -z "${DATAFLOW_MAX_WORKERS:-}" ]]; then
  if (( SPANNER_PROCESSING_UNITS < 1000 )); then
    DATAFLOW_MAX_WORKERS=1
  else
    # n1-standard-2 has 2 vCPUs; 5x nodes / 2 vCPUs ~= 2.5 workers per 1000 PU
    NODES=$(( (SPANNER_PROCESSING_UNITS + 999) / 1000 ))
    DATAFLOW_MAX_WORKERS=$(( NODES * 2 ))
  fi
fi

usage() {
  cat <<EOF
Usage: bash solution/pipelines/dataflow_migration.sh [OPTIONS]

Options:
  --dry-run                Validate corpus and print exact GCS + Dataflow commands
                           without invoking gcloud or modifying cloud resources.
  --project <id>           GCP project ID (default: \$GOOGLE_CLOUD_PROJECT).
  --region <region>        Primary Spanner & Dataflow region (default: asia-southeast3).
  --fallback-region <reg>  Dataflow fallback region (default: asia-southeast1).
  --force-fallback         Start Dataflow directly in the fallback region (asia-southeast1).
  --bucket <name>          Regional GCS staging bucket name (without gs://).
  --prefix <path>          GCS object prefix (default: neo4j-export).
  --staging-dir <dir>      Local staging directory for headerless CSVs & manifests.
  --max-workers <n>        Max Dataflow workers (default: 1 for 100 PU; 2*nodes prod).
  -h, --help               Show this help message.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --project)
      GOOGLE_CLOUD_PROJECT="$2"
      shift 2
      ;;
    --region)
      SPANNER_REGION="$2"
      DATAFLOW_REGION="$2"
      shift 2
      ;;
    --fallback-region)
      DATAFLOW_FALLBACK_REGION="$2"
      shift 2
      ;;
    --force-fallback)
      USE_FALLBACK_REGION="true"
      shift
      ;;
    --bucket)
      GCS_BUCKET="$2"
      shift 2
      ;;
    --prefix)
      GCS_PREFIX="$2"
      shift 2
      ;;
    --staging-dir)
      STAGING_DIR="$2"
      shift 2
      ;;
    --max-workers)
      DATAFLOW_MAX_WORKERS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "${USE_FALLBACK_REGION}" == "true" ]]; then
  DATAFLOW_REGION="${DATAFLOW_FALLBACK_REGION}"
fi

EFFECTIVE_PROJECT="${GOOGLE_CLOUD_PROJECT:-demo-project}"
GCS_URI_PREFIX="gs://${GCS_BUCKET}/${GCS_PREFIX}"
GCS_TEMP_LOCATION="gs://${GCS_BUCKET}/dataflow-temp"
MANIFEST_NODES_URI="${GCS_URI_PREFIX}/manifest_nodes.json"
MANIFEST_EDGES_URI="${GCS_URI_PREFIX}/manifest_edges.json"

# Sanitize Dataflow job names: [a-z]([-a-z0-9]{0,61}[a-z0-9])? (no underscores!)
SAFE_INSTANCE="$(printf '%s' "${SPANNER_INSTANCE}" | tr '[:upper:]_' '[:lower:]-')"
SAFE_DATABASE="$(printf '%s' "${SPANNER_DATABASE}" | tr '[:upper:]_' '[:lower:]-')"
RUN_TS="$(date -u +%Y%m%d%H%M%S)"
# Prefix cloud-spanner-import-... surfaces jobs inside the Spanner Console UI
JOB_BASE="cloud-spanner-import-${SAFE_INSTANCE}-${SAFE_DATABASE}"
JOB_BASE="${JOB_BASE:0:40}"
JOB_BASE="${JOB_BASE%-}"
NODES_JOB_NAME="${JOB_BASE}-nodes-${RUN_TS}"
EDGES_JOB_NAME="${JOB_BASE}-edges-${RUN_TS}"

run_cmd() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '[DRY-RUN] '
    printf '%q ' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

wait_for_dataflow_job() {
  local job_id="$1"
  local region="$2"
  local stage_label="$3"

  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY-RUN] Poll gcloud dataflow jobs describe ${job_id} --project=${EFFECTIVE_PROJECT} --region=${region} until JOB_STATE_DONE"
    return 0
  fi

  echo "[INFO] Waiting for ${stage_label} job (${job_id}) in ${region} to reach JOB_STATE_DONE..."
  while true; do
    local state
    state="$(gcloud dataflow jobs describe "${job_id}" \
      --project="${EFFECTIVE_PROJECT}" \
      --region="${region}" \
      --format="value(currentState)")"
    echo "[INFO] ${stage_label} (${job_id}) state: ${state}"
    case "${state}" in
      JOB_STATE_DONE)
        return 0
        ;;
      JOB_STATE_FAILED|JOB_STATE_CANCELLED|JOB_STATE_UPDATED)
        echo "[ERROR] ${stage_label} job ${job_id} terminated in state ${state}. Aborting before downstream stage!" >&2
        return 1
        ;;
      *)
        sleep "${POLL_INTERVAL_SECONDS}"
        ;;
    esac
  done
}

launch_dataflow_stage() {
  local stage_label="$1"
  local job_name="$2"
  local manifest_uri="$3"

  local -a base_args=(
    gcloud dataflow jobs run "${job_name}"
    --project="${EFFECTIVE_PROJECT}"
    --gcs-location="${DATAFLOW_TEMPLATE_GCS}"
    --staging-location="${GCS_TEMP_LOCATION}"
    --worker-machine-type="${WORKER_MACHINE_TYPE}"
    --max-workers="${DATAFLOW_MAX_WORKERS}"
    --parameters
    "instanceId=${SPANNER_INSTANCE},databaseId=${SPANNER_DATABASE},importManifest=${manifest_uri}"
  )

  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "# ${stage_label} (primary region=${DATAFLOW_REGION}; automatic fallback=${DATAFLOW_FALLBACK_REGION})"
    run_cmd "${base_args[@]}" --region="${DATAFLOW_REGION}"
    wait_for_dataflow_job "<${stage_label}_JOB_ID>" "${DATAFLOW_REGION}" "${stage_label}"
    return 0
  fi

  local active_region="${DATAFLOW_REGION}"
  local job_id=""
  echo "[INFO] Launching ${stage_label} Dataflow job '${job_name}' in region ${active_region}..."
  if ! job_id="$("${base_args[@]}" --region="${active_region}" --format="value(id)")"; then
    if [[ "${active_region}" != "${DATAFLOW_FALLBACK_REGION}" ]]; then
      echo "[WARN] Primary region ${active_region} failed for ${stage_label}; retrying in fallback region ${DATAFLOW_FALLBACK_REGION} (note cross-region traffic to ${SPANNER_REGION})..." >&2
      active_region="${DATAFLOW_FALLBACK_REGION}"
      job_id="$("${base_args[@]}" --region="${active_region}" --format="value(id)")"
    else
      echo "[ERROR] Failed to launch ${stage_label} Dataflow job in ${active_region}." >&2
      return 1
    fi
  fi

  wait_for_dataflow_job "${job_id}" "${active_region}" "${stage_label}"
}

echo "=============================================================================="
echo "Cloud Spanner Enterprise Path A Bulk Migration (GCS_Text_to_Cloud_Spanner)"
echo "  Mode:             $([[ "${DRY_RUN}" == "true" ]] && echo "DRY-RUN" || echo "LIVE")"
echo "  Project:          ${EFFECTIVE_PROJECT}"
echo "  Spanner Target:   ${SPANNER_INSTANCE}/${SPANNER_DATABASE} (regional-${SPANNER_REGION})"
echo "  GCS Staging:      ${GCS_URI_PREFIX} (regional bucket in ${SPANNER_REGION})"
echo "  Dataflow Region:  ${DATAFLOW_REGION} (fallback: ${DATAFLOW_FALLBACK_REGION})"
echo "  Worker Sizing:    ${WORKER_MACHINE_TYPE}, --max-workers=${DATAFLOW_MAX_WORKERS} (Spanner PUs=${SPANNER_PROCESSING_UNITS})"
echo "=============================================================================="

if [[ "${DRY_RUN}" != "true" && -z "${GOOGLE_CLOUD_PROJECT}" ]]; then
  echo "[ERROR] GOOGLE_CLOUD_PROJECT (or --project) must be set for live execution." >&2
  exit 1
fi

# ------------------------------------------------------------------------------
# Step 1: Validate canonical Path A corpus & stage headerless CSVs + manifests
# ------------------------------------------------------------------------------
echo "[STEP 1/4] Validating Path A corpus and building headerless CSV manifests..."
if [[ "${DRY_RUN}" == "true" && -z "${STAGING_DIR}" ]]; then
  # Dry-run without --staging-dir performs full corpus validation without writing disk files
  python3 "${REPO_ROOT}/data/structured/stage_apoc_export.py" \
    --gcs-prefix "${GCS_URI_PREFIX}"
else
  if [[ -z "${STAGING_DIR}" ]]; then
    STAGING_DIR="$(mktemp -d -t acme-spanner-staging-XXXXXX)"
    trap 'rm -rf "${STAGING_DIR}"' EXIT
  fi
  python3 "${REPO_ROOT}/data/structured/stage_apoc_export.py" \
    --output-dir "${STAGING_DIR}" \
    --gcs-prefix "${GCS_URI_PREFIX}"
fi

# ------------------------------------------------------------------------------
# Step 2: Ensure regional GCS staging bucket (asia-southeast3) & upload files
# ------------------------------------------------------------------------------
echo "[STEP 2/4] Ensuring regional GCS staging bucket in ${SPANNER_REGION} and uploading manifests..."
if [[ "${DRY_RUN}" == "true" ]]; then
  run_cmd gcloud storage buckets create "gs://${GCS_BUCKET}" \
    --project="${EFFECTIVE_PROJECT}" \
    --location="${SPANNER_REGION}" \
    --uniform-bucket-level-access
  run_cmd gcloud storage cp --recursive \
    "${STAGING_DIR:-/tmp/acme-spanner-staging}/nodes" \
    "${STAGING_DIR:-/tmp/acme-spanner-staging}/edges" \
    "${STAGING_DIR:-/tmp/acme-spanner-staging}/manifest_nodes.json" \
    "${STAGING_DIR:-/tmp/acme-spanner-staging}/manifest_edges.json" \
    "${GCS_URI_PREFIX}/"
else
  if ! gcloud storage buckets describe "gs://${GCS_BUCKET}" --project="${EFFECTIVE_PROJECT}" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${GCS_BUCKET}" \
      --project="${EFFECTIVE_PROJECT}" \
      --location="${SPANNER_REGION}" \
      --uniform-bucket-level-access
  fi
  gcloud storage cp --recursive \
    "${STAGING_DIR}/nodes" \
    "${STAGING_DIR}/edges" \
    "${STAGING_DIR}/manifest_nodes.json" \
    "${STAGING_DIR}/manifest_edges.json" \
    "${GCS_URI_PREFIX}/"
fi

# ------------------------------------------------------------------------------
# Step 3: Stage 1 Dataflow Import — ALL 5 Node Tables (must finish before edges!)
# ------------------------------------------------------------------------------
echo "[STEP 3/4] Stage 1 Dataflow Import (Nodes: Owners, Services, Apis, Adrs, Requirements)..."
launch_dataflow_stage "STAGE-1-NODES" "${NODES_JOB_NAME}" "${MANIFEST_NODES_URI}"

# ------------------------------------------------------------------------------
# Step 4: Stage 2 Dataflow Import — ALL 6 Explicit Edge Tables (after Stage 1 DONE)
# ------------------------------------------------------------------------------
echo "[STEP 4/4] Stage 2 Dataflow Import (Edges: 6 explicit M:N tables)..."
launch_dataflow_stage "STAGE-2-EDGES" "${EDGES_JOB_NAME}" "${MANIFEST_EDGES_URI}"

echo "[OK] Sequential Nodes -> Edges Dataflow migration workflow complete."
echo "     Next step: run python3 solution/pipelines/unstructured_graphrag_ingest.py"
echo "     followed by python3 solution/pipelines/apply_postload.py (indexes + ANALYZE)."
