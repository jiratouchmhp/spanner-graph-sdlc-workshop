#!/usr/bin/env bash
# =============================================================================
# Cloud Spanner Enterprise SDLC Knowledge Graph Workshop — Environment Lifecycle
# =============================================================================
# Manages the workshop Cloud Spanner Enterprise sandbox in Bangkok primary
# (`regional-asia-southeast3`, single-region 3-zone synchronous quorum,
# 99.99% SLA, Thailand PDPA resident, 100 PU, ~$1.15/8-hr lab, same-region
# backups/PITR only — no second Spanner DR instance), with Singapore
# (`asia-southeast1`, ~25 ms RTT) used ONLY for AI/Security companion services
# unavailable in Bangkok (Vertex AI, Model Armor, Cloud DLP).
#
# Subcommands:
#   create               Provision 100-PU Enterprise instance (--default-backup-schedule-type=NONE),
#                        create GoogleSQL database, and apply base schema DDL (01 + optional 02/04)
#                        from `--track starter|solution` (default: solution).
#   apply-postload       Run 4-stage post-load orchestrator (03a -> 03b asia-southeast1 -> 03 -> ANALYZE)
#                        using `--track starter|solution` for 03_indexes_fts_vector_postload.sql.
#   cleanup              Delete same-region backups and remove the Bangkok sandbox instance cleanly.
#                        Pass `--all` for full teardown (Module 6 Cloud Run/Secrets/IAM/SAs/DLP/Model Armor
#                        + GCS staging bucket + Bangkok Spanner backups/instance + verify_zero_residual.sh).
#
# Usage:
#   bash scripts/provision_workshop_env.sh create --dry-run
#   bash scripts/provision_workshop_env.sh create --track starter --dry-run
#   bash scripts/provision_workshop_env.sh apply-postload --track starter --dry-run
#   bash scripts/provision_workshop_env.sh cleanup --dry-run
#   bash scripts/provision_workshop_env.sh cleanup --all --dry-run
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

# Prefer .venv/bin/python when executable, else python3
PYTHON_BIN="${PYTHON:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

# Locked defaults aligned with strict Bangkok single-region architecture
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-${SPANNER_PROJECT:-}}"
REGION="${GOOGLE_CLOUD_REGION:-${SPANNER_REGION:-asia-southeast3}}"
VERTEX_AI_LOCATION="${VERTEX_AI_LOCATION:-${VERTEX_AI_EMBEDDING_LOCATION:-asia-southeast1}}"
MODEL_ARMOR_LOCATION="${MODEL_ARMOR_LOCATION:-asia-southeast1}"
SPANNER_INSTANCE="${SPANNER_INSTANCE:-sdlc-kg-enterprise}"
SPANNER_DATABASE="${SPANNER_DATABASE:-sdlc_knowledge_graph}"
SPANNER_EDITION="${SPANNER_EDITION:-ENTERPRISE}"
SPANNER_PROCESSING_UNITS="${SPANNER_PROCESSING_UNITS:-100}"
SERVICE_PREFIX="${SERVICE_PREFIX:-sdlc-mcp}"
SECRET_PREFIX="${SECRET_PREFIX:-sdlc-tools}"
GCS_BUCKET="${GCS_BUCKET:-}"
TRACK="${TRACK:-solution}"
DRY_RUN="${DRY_RUN:-false}"
INCLUDE_DYNAMIC_GRAPH="false"
INCLUDE_FGAC="true"
AUTO_APPROVE="false"
CLEANUP_ALL="false"

usage() {
  cat <<'EOF'
Usage: bash scripts/provision_workshop_env.sh <command> [OPTIONS]

Commands:
  create               Provision Bangkok Cloud Spanner Enterprise sandbox + GoogleSQL DB + base DDL
  apply-postload       Execute 4-stage post-load (03a synonyms -> 03b Vertex AI -> 03 indexes -> ANALYZE)
  cleanup              Delete same-region backups and delete the workshop Spanner instance cleanly

Options:
  --track <starter|solution>    SQL track to deploy for 01/04/03 DDL (default: solution)
  --project <id>                GCP Project ID (default: $GOOGLE_CLOUD_PROJECT)
  --region <region>             Primary region for Spanner, Cloud Run, Secrets & GCS (default: asia-southeast3 Bangkok)
  --vertex-location <loc>       Regional Vertex AI location (default: asia-southeast1 Singapore; NEVER global)
  --model-armor-location <loc>  Regional Model Armor & Cloud DLP location (default: asia-southeast1 Singapore)
  --instance <id>               Bangkok Spanner instance ID (default: sdlc-kg-enterprise)
  --database <id>               Spanner database ID (default: sdlc_knowledge_graph)
  --processing-units <pu>       Processing units (default: 100 sandbox; production requires >= 1000)
  --service-prefix <prefix>     Cloud Run MCP service prefix for cleanup --all (default: sdlc-mcp)
  --secret-prefix <prefix>      Secret Manager prefix for cleanup --all (default: sdlc-tools)
  --bucket <name>               GCS staging bucket name for cleanup --all (default: <project>-sdlc-kg-<region>)
  --with-dynamic-graph          Also apply 02_path_b_schemaless_comparison.sql during create
  --skip-fgac                   Skip applying 04_fgac_roles_views.sql during create
  --all                         (cleanup only) Full teardown: Module 6 IAM/Cloud Run/Secrets/SAs/DLP/
                                Model Armor + GCS staging bucket + Bangkok Spanner backups/instance +
                                scripts/verify_zero_residual.sh audit
  --dry-run                     Print all commands without mutating GCP resources
  --yes                         Skip interactive confirmation prompt for cleanup
  -h, --help                    Show this help message
EOF
}

require_arg() {
  if [[ "$2" -lt 2 ]]; then
    echo "[ERROR] Option $1 requires a value." >&2
    exit 1
  fi
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 1
fi

COMMAND="$1"
shift

case "${COMMAND}" in
  create|apply-postload|cleanup) ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    echo "[ERROR] Unknown command: ${COMMAND}" >&2
    usage >&2
    exit 1
    ;;
esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    --track)
      require_arg "$1" "$#"
      TRACK="$2"
      shift 2
      ;;
    --project)
      require_arg "$1" "$#"
      PROJECT_ID="$2"
      shift 2
      ;;
    --region)
      require_arg "$1" "$#"
      REGION="$2"
      shift 2
      ;;
    --vertex-location)
      require_arg "$1" "$#"
      VERTEX_AI_LOCATION="$2"
      shift 2
      ;;
    --model-armor-location)
      require_arg "$1" "$#"
      MODEL_ARMOR_LOCATION="$2"
      shift 2
      ;;
    --instance)
      require_arg "$1" "$#"
      SPANNER_INSTANCE="$2"
      shift 2
      ;;
    --database)
      require_arg "$1" "$#"
      SPANNER_DATABASE="$2"
      shift 2
      ;;
    --processing-units)
      require_arg "$1" "$#"
      SPANNER_PROCESSING_UNITS="$2"
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
    --bucket)
      require_arg "$1" "$#"
      GCS_BUCKET="$2"
      shift 2
      ;;
    --with-dynamic-graph)
      INCLUDE_DYNAMIC_GRAPH="true"
      shift
      ;;
    --skip-fgac)
      INCLUDE_FGAC="false"
      shift
      ;;
    --all)
      CLEANUP_ALL="true"
      shift
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --yes)
      AUTO_APPROVE="true"
      shift
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

case "${TRACK}" in
  starter|solution) ;;
  *)
    echo "[ERROR] Invalid --track '${TRACK}'. Expected 'starter' or 'solution'." >&2
    exit 1
    ;;
esac

if [[ "${CLEANUP_ALL}" == "true" && "${COMMAND}" != "cleanup" ]]; then
  echo "[ERROR] --all is only valid with the 'cleanup' command." >&2
  exit 1
fi

# Enforce INV-09 regional Vertex AI guardrail
if [[ "${VERTEX_AI_LOCATION,,}" == "global" || "${VERTEX_AI_LOCATION,,}" == "us" || "${VERTEX_AI_LOCATION,,}" == "eu" ]]; then
  echo "[ERROR] INV-09 violation: VERTEX_AI_LOCATION='${VERTEX_AI_LOCATION}' is invalid for gemini-embedding-001. Use regional 'asia-southeast1'." >&2
  exit 1
fi

if [[ -z "${PROJECT_ID}" ]]; then
  if [[ "${DRY_RUN}" == "true" ]]; then
    PROJECT_ID="demo-project"
  else
    PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
    if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
      echo "[ERROR] GOOGLE_CLOUD_PROJECT is not set. Copy .env.example to .env or pass --project <id>." >&2
      exit 1
    fi
  fi
fi

if [[ -z "${GCS_BUCKET}" ]]; then
  GCS_BUCKET="${PROJECT_ID}-sdlc-kg-${REGION}"
fi
GCS_BUCKET="${GCS_BUCKET#gs://}"
GCS_BUCKET="${GCS_BUCKET%%/*}"

INSTANCE_CONFIG="regional-${REGION}"
SQL_TRACK_DIR="${REPO_ROOT}/${TRACK}/sql"

run_cmd() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '[DRY-RUN]'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

# Cloud Spanner CreateInstance enforces 4-30 characters on instance.display_name (--description).
format_spanner_description() {
  local preferred="$1"
  local fallback="$2"
  if (( ${#preferred} >= 4 && ${#preferred} <= 30 )); then
    printf '%s' "${preferred}"
  else
    printf '%s' "${fallback:0:30}"
  fi
}

check_starter_sql_ready() {
  local sql_file="$1"
  local lab_hint="$2"
  if [[ ! -f "${sql_file}" ]]; then
    echo "[ERROR] Required SQL file not found: ${sql_file}" >&2
    exit 1
  fi
  if [[ "${TRACK}" == "starter" ]] && grep -qiE "TODO\(\s*Lab\s*[1-8]\s*\)" "${sql_file}"; then
    if [[ "${DRY_RUN}" == "true" ]]; then
      echo "[WARN] ${sql_file#"${REPO_ROOT}/"} still contains unfinished TODO(Lab ...) markers (previewing in --dry-run; solve & run 'make TRACK=starter verify-lab LAB=${lab_hint}' before live apply)." >&2
    else
      echo "[ERROR] ${sql_file#"${REPO_ROOT}/"} still contains unfinished TODO(Lab ...) markers." >&2
      echo "        Complete Lab ${lab_hint} and verify with 'make TRACK=starter verify-lab LAB=${lab_hint}' before deploying to live Spanner." >&2
      exit 1
    fi
  fi
}

print_banner() {
  echo "=============================================================================="
  echo "Cloud Spanner Enterprise Workshop — ${COMMAND}"
  echo "  Mode              : $([[ "${DRY_RUN}" == "true" ]] && echo "DRY-RUN" || echo "LIVE GCP")"
  echo "  Track             : ${TRACK} (${TRACK}/sql)"
  echo "  Project           : ${PROJECT_ID}"
  echo "  Primary Region    : ${REGION} (${INSTANCE_CONFIG} — Spanner, Cloud Run, Secrets, GCS, backups)"
  echo "  Companion AI/Sec  : Vertex AI=${VERTEX_AI_LOCATION} | Model Armor & DLP=${MODEL_ARMOR_LOCATION} (Singapore — unavailable in Bangkok)"
  echo "  Instance / DB     : ${SPANNER_INSTANCE} / ${SPANNER_DATABASE}"
  echo "  Edition / PUs     : ${SPANNER_EDITION} / ${SPANNER_PROCESSING_UNITS} PU"
  if [[ "${COMMAND}" == "cleanup" && "${CLEANUP_ALL}" == "true" ]]; then
    echo "  Teardown Scope    : FULL (--all: Module 6 IAM/Cloud Run/Secrets/SAs/DLP/Model Armor + gs://${GCS_BUCKET} + Spanner + Zero-Residual Audit)"
  fi
  if (( SPANNER_PROCESSING_UNITS < 1000 )); then
    echo "  Sandbox Notice    : ${SPANNER_PROCESSING_UNITS} PU is workshop/dev-only (production requires >= 1,000 PU)."
  fi
  echo "=============================================================================="
}

cmd_create() {
  print_banner
  if [[ "${DRY_RUN}" != "true" && -n "${SPANNER_EMULATOR_HOST:-}" ]]; then
    echo "[WARN] SPANNER_EMULATOR_HOST=${SPANNER_EMULATOR_HOST} is set in shell; gcloud targets live Cloud Spanner." >&2
  fi

  local ddl_01="${SQL_TRACK_DIR}/01_path_a_schematized.sql"
  local ddl_02="${REPO_ROOT}/solution/sql/02_path_b_schemaless_comparison.sql"
  if [[ -f "${SQL_TRACK_DIR}/02_path_b_schemaless_comparison.sql" ]]; then
    ddl_02="${SQL_TRACK_DIR}/02_path_b_schemaless_comparison.sql"
  fi
  local ddl_04="${SQL_TRACK_DIR}/04_fgac_roles_views.sql"

  check_starter_sql_ready "${ddl_01}" "3"
  if [[ "${INCLUDE_FGAC}" == "true" ]]; then
    check_starter_sql_ready "${ddl_04}" "6"
  fi

  echo "[1/4] Enabling required Google Cloud APIs (Spanner + Vertex AI)..."
  run_cmd gcloud services enable \
    spanner.googleapis.com \
    aiplatform.googleapis.com \
    --project="${PROJECT_ID}"

  echo "[2/4] Provisioning Cloud Spanner ${SPANNER_EDITION} instance (${INSTANCE_CONFIG}, ${SPANNER_PROCESSING_UNITS} PU, backup schedule NONE)..."
  local instance_desc
  instance_desc="$(format_spanner_description "SDLC KG (${REGION})" "SDLC KG Workshop")"
  if [[ "${DRY_RUN}" == "true" ]] || ! gcloud spanner instances describe "${SPANNER_INSTANCE}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    run_cmd gcloud spanner instances create "${SPANNER_INSTANCE}" \
      --project="${PROJECT_ID}" \
      --config="${INSTANCE_CONFIG}" \
      --description="${instance_desc}" \
      --edition="${SPANNER_EDITION}" \
      --processing-units="${SPANNER_PROCESSING_UNITS}" \
      --default-backup-schedule-type=NONE
  else
    echo "[OK] Instance '${SPANNER_INSTANCE}' already exists in project '${PROJECT_ID}'."
  fi

  echo "[3/4] Creating GoogleSQL database '${SPANNER_DATABASE}'..."
  if [[ "${DRY_RUN}" == "true" ]] || ! gcloud spanner databases describe "${SPANNER_DATABASE}" --instance="${SPANNER_INSTANCE}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    run_cmd gcloud spanner databases create "${SPANNER_DATABASE}" \
      --project="${PROJECT_ID}" \
      --instance="${SPANNER_INSTANCE}" \
      --database-dialect=GOOGLE_STANDARD_SQL
  else
    echo "[OK] Database '${SPANNER_DATABASE}' already exists on '${SPANNER_INSTANCE}'."
  fi

  local existing_ddl=""
  if [[ "${DRY_RUN}" != "true" ]]; then
    existing_ddl="$(gcloud spanner databases ddl describe "${SPANNER_DATABASE}" \
      --project="${PROJECT_ID}" \
      --instance="${SPANNER_INSTANCE}" 2>/dev/null || true)"
  fi

  echo "[4/4] Applying base schema DDL (${TRACK}/sql/01_path_a_schematized.sql)..."
  if [[ "${DRY_RUN}" != "true" ]] && grep -q "PROPERTY GRAPH SdlcGraph" <<< "${existing_ddl}"; then
    echo "[OK] Base schema DDL (SdlcGraph) already applied on '${SPANNER_DATABASE}'; skipping."
  else
    run_cmd gcloud spanner databases ddl update "${SPANNER_DATABASE}" \
      --project="${PROJECT_ID}" \
      --instance="${SPANNER_INSTANCE}" \
      --ddl-file="${ddl_01}"
    if [[ "${DRY_RUN}" != "true" ]]; then
      existing_ddl="$(gcloud spanner databases ddl describe "${SPANNER_DATABASE}" \
        --project="${PROJECT_ID}" \
        --instance="${SPANNER_INSTANCE}" 2>/dev/null || true)"
    fi
  fi

  if [[ "${INCLUDE_DYNAMIC_GRAPH}" == "true" ]]; then
    echo "[4b] Applying schemaless comparison DDL (${ddl_02#"${REPO_ROOT}/"})..."
    if [[ "${DRY_RUN}" != "true" ]] && grep -q "PROPERTY GRAPH SdlcDynamicGraph" <<< "${existing_ddl}"; then
      echo "[OK] Schemaless comparison DDL (SdlcDynamicGraph) already applied on '${SPANNER_DATABASE}'; skipping."
    else
      run_cmd gcloud spanner databases ddl update "${SPANNER_DATABASE}" \
        --project="${PROJECT_ID}" \
        --instance="${SPANNER_INSTANCE}" \
        --ddl-file="${ddl_02}"
      if [[ "${DRY_RUN}" != "true" ]]; then
        existing_ddl="$(gcloud spanner databases ddl describe "${SPANNER_DATABASE}" \
          --project="${PROJECT_ID}" \
          --instance="${SPANNER_INSTANCE}" 2>/dev/null || true)"
      fi
    fi
  fi

  if [[ "${INCLUDE_FGAC}" == "true" ]]; then
    echo "[4c] Applying FGAC roles & views DDL (${TRACK}/sql/04_fgac_roles_views.sql)..."
    if [[ "${DRY_RUN}" != "true" ]] && grep -q "CREATE ROLE sdlc_security_auditor_role" <<< "${existing_ddl}" && grep -q "CREATE VIEW TeamScopedAssetsView" <<< "${existing_ddl}"; then
      echo "[OK] FGAC roles & views DDL already applied on '${SPANNER_DATABASE}'; skipping."
    else
      run_cmd gcloud spanner databases ddl update "${SPANNER_DATABASE}" \
        --project="${PROJECT_ID}" \
        --instance="${SPANNER_INSTANCE}" \
        --ddl-file="${ddl_04}"
    fi
  fi

  echo "[4d] Configuring Data Boost Graph Algorithm DB IAM (spanner.graphIntelligenceUser + Spanner SA write-back)..."
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY-RUN] Grant database-level roles/spanner.graphIntelligenceUser to active user & roles/spanner.databaseUser + roles/spanner.graphIntelligenceUser to service-<PROJECT_NUMBER>@gcp-sa-spanner.iam.gserviceaccount.com"
  else
    local project_num active_user spanner_sa
    project_num="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)' 2>/dev/null || true)"
    active_user="$(gcloud config get-value account 2>/dev/null || true)"
    if [[ -n "${active_user}" && "${active_user}" != "(unset)" ]]; then
      local member_prefix="user"
      if [[ "${active_user}" == *".gserviceaccount.com" ]]; then
        member_prefix="serviceAccount"
      fi
      gcloud spanner databases add-iam-policy-binding "${SPANNER_DATABASE}" \
        --project="${PROJECT_ID}" \
        --instance="${SPANNER_INSTANCE}" \
        --member="${member_prefix}:${active_user}" \
        --role="roles/spanner.graphIntelligenceUser" \
        --condition=None >/dev/null 2>&1 || echo "[WARN] Could not grant roles/spanner.graphIntelligenceUser to ${active_user} (grant manually before Lab 4 Data Boost)." >&2
    fi
    if [[ -n "${project_num}" ]]; then
      spanner_sa="service-${project_num}@gcp-sa-spanner.iam.gserviceaccount.com"
      gcloud spanner databases add-iam-policy-binding "${SPANNER_DATABASE}" \
        --project="${PROJECT_ID}" \
        --instance="${SPANNER_INSTANCE}" \
        --member="serviceAccount:${spanner_sa}" \
        --role="roles/spanner.databaseUser" \
        --condition=None >/dev/null 2>&1 || true
      gcloud spanner databases add-iam-policy-binding "${SPANNER_DATABASE}" \
        --project="${PROJECT_ID}" \
        --instance="${SPANNER_INSTANCE}" \
        --member="serviceAccount:${spanner_sa}" \
        --role="roles/spanner.graphIntelligenceUser" \
        --condition=None >/dev/null 2>&1 || true
    fi
  fi

  echo ""
  echo "[OK] Sandbox provisioned (track=${TRACK}). Next steps:"
  echo "  1. ${PYTHON_BIN} ${TRACK}/pipelines/neo4j_to_spanner_loader.py --project ${PROJECT_ID} --region ${REGION}"
  echo "  2. ${PYTHON_BIN} ${TRACK}/pipelines/unstructured_graphrag_ingest.py --project ${PROJECT_ID} --region ${REGION} --vertex-location ${VERTEX_AI_LOCATION}"
  echo "  3. bash scripts/provision_workshop_env.sh apply-postload --track ${TRACK} --project ${PROJECT_ID} --region ${REGION} --vertex-location ${VERTEX_AI_LOCATION}"
}

cmd_apply_postload() {
  print_banner
  local idx_file="${SQL_TRACK_DIR}/03_indexes_fts_vector_postload.sql"
  check_starter_sql_ready "${idx_file}" "3"

  local effective_sql_dir="${REPO_ROOT}/solution/sql"
  TMP_POSTLOAD_SQL_DIR=""

  if [[ "${TRACK}" == "starter" ]]; then
    # Stage shared 03a/03b/03c/03d helpers from solution/sql alongside solved starter 03_indexes_fts_vector_postload.sql
    TMP_POSTLOAD_SQL_DIR="$(mktemp -d /tmp/sdlc_postload_starter.XXXXXX)"
    trap 'rm -rf "${TMP_POSTLOAD_SQL_DIR:-}"' EXIT
    for shared_file in \
      "03a_seed_synonyms_and_metadata.sql" \
      "03b_remote_models.sql.tmpl" \
      "03c_optional_reranker_model.sql.tmpl" \
      "03d_backfill_embeddings_pdml.sql"; do
      if [[ -f "${REPO_ROOT}/solution/sql/${shared_file}" ]]; then
        ln -s "${REPO_ROOT}/solution/sql/${shared_file}" "${TMP_POSTLOAD_SQL_DIR}/${shared_file}"
      fi
    done
    ln -s "${idx_file}" "${TMP_POSTLOAD_SQL_DIR}/03_indexes_fts_vector_postload.sql"
    effective_sql_dir="${TMP_POSTLOAD_SQL_DIR}"
  fi

  local -a postload_args=(
    "${PYTHON_BIN}" "${REPO_ROOT}/solution/pipelines/apply_postload.py"
    --sql-dir "${effective_sql_dir}"
    --project "${PROJECT_ID}"
    --region "${REGION}"
    --instance "${SPANNER_INSTANCE}"
    --database "${SPANNER_DATABASE}"
    --vertex-location "${VERTEX_AI_LOCATION}"
    --strict
  )
  if [[ "${DRY_RUN}" == "true" ]]; then
    postload_args+=(--dry-run)
  fi
  echo "[POST-LOAD] Running 4-stage orchestrator (track=${TRACK}: 03a -> 03b ${VERTEX_AI_LOCATION} -> 03 batched LROs -> ANALYZE)..."
  "${postload_args[@]}"

  if [[ -n "${TMP_POSTLOAD_SQL_DIR}" ]]; then
    rm -rf "${TMP_POSTLOAD_SQL_DIR}"
  fi
}

delete_instance_backups_and_instance() {
  local inst="$1"
  if [[ "${DRY_RUN}" == "true" ]]; then
    run_cmd gcloud spanner backups list --project="${PROJECT_ID}" --instance="${inst}" --format="value(name)"
    run_cmd gcloud spanner databases list --project="${PROJECT_ID}" --instance="${inst}" --format="value(name.basename())"
    run_cmd gcloud spanner databases update "${SPANNER_DATABASE}" --project="${PROJECT_ID}" --instance="${inst}" --no-enable-drop-protection
    run_cmd gcloud spanner instances delete "${inst}" --project="${PROJECT_ID}" --quiet
    return 0
  fi

  if ! gcloud spanner instances describe "${inst}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "[OK] Instance '${inst}' does not exist; nothing to delete."
    return 0
  fi

  echo "[CLEANUP] Checking backups on instance '${inst}' (backups block instance deletion)..."
  local backups
  backups="$(gcloud spanner backups list --project="${PROJECT_ID}" --instance="${inst}" --format="value(name.basename())" 2>/dev/null || true)"
  if [[ -n "${backups}" ]]; then
    while IFS= read -r bkp; do
      [[ -z "${bkp}" ]] && continue
      echo "  Deleting backup '${bkp}' on '${inst}'..."
      gcloud spanner backups delete "${bkp}" --project="${PROJECT_ID}" --instance="${inst}" --quiet
    done <<< "${backups}"
  fi

  echo "[CLEANUP] Ensuring database drop protection is disabled on '${inst}'..."
  local dbs
  dbs="$(gcloud spanner databases list --project="${PROJECT_ID}" --instance="${inst}" --format="value(name.basename())" 2>/dev/null || true)"
  if [[ -n "${dbs}" ]]; then
    while IFS= read -r db; do
      [[ -z "${db}" ]] && continue
      gcloud spanner databases update "${db}" \
        --project="${PROJECT_ID}" \
        --instance="${inst}" \
        --no-enable-drop-protection >/dev/null 2>&1 || true
    done <<< "${dbs}"
  fi

  echo "[CLEANUP] Deleting Cloud Spanner instance '${inst}'..."
  gcloud spanner instances delete "${inst}" --project="${PROJECT_ID}" --quiet
  echo "[OK] Deleted instance '${inst}'."
}

delete_gcs_staging_bucket() {
  local bucket_uri="gs://${GCS_BUCKET}"
  if [[ "${DRY_RUN}" == "true" ]]; then
    run_cmd gcloud storage rm --recursive "${bucket_uri}" --project="${PROJECT_ID}"
    return 0
  fi

  if ! gcloud storage buckets describe "${bucket_uri}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "[OK] GCS staging bucket '${bucket_uri}' does not exist; nothing to delete."
    return 0
  fi

  echo "[CLEANUP] Deleting workshop GCS staging bucket '${bucket_uri}'..."
  gcloud storage rm --recursive "${bucket_uri}" --project="${PROJECT_ID}" >/dev/null 2>&1 || true
  if gcloud storage buckets describe "${bucket_uri}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud storage buckets delete "${bucket_uri}" --project="${PROJECT_ID}" --quiet
  fi
  echo "[OK] Deleted GCS staging bucket '${bucket_uri}'."
}

cmd_cleanup() {
  print_banner
  if [[ "${DRY_RUN}" != "true" && "${AUTO_APPROVE}" != "true" ]]; then
    if [[ "${CLEANUP_ALL}" == "true" ]]; then
      echo "WARNING: FULL TEARDOWN (--all) permanently deletes in project '${PROJECT_ID}':"
      echo "         - 3 Cloud Run MCP services (${SERVICE_PREFIX}-* in ${REGION}) & 3 secrets (${SECRET_PREFIX}-*)"
      echo "         - Project IAM bindings & all 7 workshop Service Accounts"
      echo "         - Companion Singapore Cloud DLP & Model Armor templates (${MODEL_ARMOR_LOCATION})"
      echo "         - Workshop GCS staging bucket (gs://${GCS_BUCKET})"
      echo "         - Bangkok Spanner instance '${SPANNER_INSTANCE}' (${INSTANCE_CONFIG}) & all same-region backups"
    else
      echo "WARNING: This permanently deletes '${SPANNER_INSTANCE}' (${INSTANCE_CONFIG})"
      echo "         plus all same-region backups in project '${PROJECT_ID}'."
    fi
    read -r -p "Type 'DELETE' to confirm teardown: " confirm
    if [[ "${confirm}" != "DELETE" ]]; then
      echo "Aborted teardown."
      exit 1
    fi
  fi

  if [[ "${CLEANUP_ALL}" == "true" ]]; then
    local cleanup_rc=0
    echo ""
    echo "[CLEANUP-ALL 1/4] Cleaning up Module 6 IAM, Cloud Run MCP, Secrets, SAs, DLP & Model Armor..."
    local -a iam_cleanup_args=(
      bash "${REPO_ROOT}/solution/mcp/iam_and_model_armor_setup.sh"
      --cleanup
      --yes
      --project "${PROJECT_ID}"
      --instance "${SPANNER_INSTANCE}"
      --database "${SPANNER_DATABASE}"
      --region "${REGION}"
      --service-prefix "${SERVICE_PREFIX}"
      --secret-prefix "${SECRET_PREFIX}"
    )
    if [[ "${DRY_RUN}" == "true" ]]; then
      iam_cleanup_args+=(--dry-run)
    fi
    if ! MODEL_ARMOR_LOCATION="${MODEL_ARMOR_LOCATION}" "${iam_cleanup_args[@]}"; then
      echo "[WARN] Module 6 cleanup returned non-zero; continuing to delete GCS & Spanner instance so no billed resources remain." >&2
      cleanup_rc=1
    fi

    echo ""
    echo "[CLEANUP-ALL 2/4] Cleaning up workshop GCS staging bucket (gs://${GCS_BUCKET})..."
    if ! delete_gcs_staging_bucket; then
      cleanup_rc=1
    fi

    echo ""
    echo "[CLEANUP-ALL 3/4] Cleaning up Bangkok primary Spanner backups and instance ('${SPANNER_INSTANCE}')..."
    if ! delete_instance_backups_and_instance "${SPANNER_INSTANCE}"; then
      cleanup_rc=1
    fi

    echo ""
    echo "[CLEANUP-ALL 4/4] Auditing project for zero residual workshop resources..."
    local -a verify_args=(
      bash "${REPO_ROOT}/scripts/verify_zero_residual.sh"
      --project "${PROJECT_ID}"
      --instance "${SPANNER_INSTANCE}"
      --service-prefix "${SERVICE_PREFIX}"
      --secret-prefix "${SECRET_PREFIX}"
      --region "${REGION}"
      --model-armor-location "${MODEL_ARMOR_LOCATION}"
      --bucket "${GCS_BUCKET}"
    )
    if [[ "${DRY_RUN}" == "true" ]]; then
      verify_args+=(--dry-run)
    fi
    if ! "${verify_args[@]}"; then
      cleanup_rc=1
    fi

    if (( cleanup_rc != 0 )); then
      echo "[FAIL] Full workshop teardown completed with errors; review warnings above." >&2
      return 1
    fi
    echo "[OK] Full workshop GCP teardown (--all) and zero-residual verification complete."
    return 0
  fi

  delete_instance_backups_and_instance "${SPANNER_INSTANCE}"

  echo "[OK] Workshop Cloud Spanner teardown complete. Run 'bash scripts/provision_workshop_env.sh cleanup --all' to also tear down Lab 6 resources & verify zero residual."
}

case "${COMMAND}" in
  create) cmd_create ;;
  apply-postload) cmd_apply_postload ;;
  cleanup) cmd_cleanup ;;
esac
