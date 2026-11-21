#!/usr/bin/env bash
# =============================================================================
# Cloud Spanner Enterprise SDLC Knowledge Graph Workshop — Environment Lifecycle
# =============================================================================
# Manages the workshop Cloud Spanner Enterprise sandbox in Bangkok primary
# (`regional-asia-southeast3`, Thailand PDPA resident, 100 PU, ~$1.15/8-hr lab)
# with optional Singapore (`asia-southeast1`) Vertex AI, DR backup copy, and
# quota fallback support.
#
# Subcommands:
#   create               Provision 100-PU Enterprise instance (--default-backup-schedule-type=NONE),
#                        create GoogleSQL database, and apply base schema DDL (01 + optional 02/04).
#   apply-postload       Run 4-stage post-load orchestrator (03a -> 03b asia-southeast1 -> 03 -> ANALYZE).
#   backup-to-singapore  Create a Bangkok backup and copy it cross-region to Singapore (regional-asia-southeast1).
#   cleanup              Delete backups (primary + optional Singapore DR) and remove sandbox instance(s).
#
# Usage:
#   bash scripts/provision_workshop_env.sh create --dry-run
#   bash scripts/provision_workshop_env.sh create --region asia-southeast1 --dry-run
#   bash scripts/provision_workshop_env.sh apply-postload --dry-run
#   bash scripts/provision_workshop_env.sh backup-to-singapore --dry-run
#   bash scripts/provision_workshop_env.sh cleanup --dry-run
# =============================================================================
set -euo pipefail

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

# Locked defaults aligned with WORKSHOP_SPEC.json
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-${SPANNER_PROJECT:-}}"
REGION="${GOOGLE_CLOUD_REGION:-${SPANNER_REGION:-asia-southeast3}}"
SECONDARY_REGION="${GOOGLE_CLOUD_SECONDARY_REGION:-asia-southeast1}"
VERTEX_AI_LOCATION="${VERTEX_AI_LOCATION:-${VERTEX_AI_EMBEDDING_LOCATION:-asia-southeast1}}"
SPANNER_INSTANCE="${SPANNER_INSTANCE:-sdlc-kg-enterprise}"
SPANNER_SECONDARY_INSTANCE="${SPANNER_SECONDARY_INSTANCE:-sdlc-kg-dr-singapore}"
SPANNER_DATABASE="${SPANNER_DATABASE:-sdlc_knowledge_graph}"
SPANNER_EDITION="${SPANNER_EDITION:-ENTERPRISE}"
SPANNER_PROCESSING_UNITS="${SPANNER_PROCESSING_UNITS:-100}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
DRY_RUN="${DRY_RUN:-false}"
INCLUDE_DYNAMIC_GRAPH="false"
INCLUDE_FGAC="true"
AUTO_APPROVE="false"

usage() {
  cat <<'EOF'
Usage: bash scripts/provision_workshop_env.sh <command> [OPTIONS]

Commands:
  create               Provision Cloud Spanner Enterprise sandbox + GoogleSQL DB + base DDL
  apply-postload       Execute 4-stage post-load (03a synonyms -> 03b Vertex AI -> 03 indexes -> ANALYZE)
  backup-to-singapore  Create Bangkok backup & copy cross-region to Singapore (regional-asia-southeast1)
  cleanup              Delete backups and delete workshop Spanner instance(s) cleanly

Options:
  --project <id>             GCP Project ID (default: $GOOGLE_CLOUD_PROJECT)
  --region <region>          Primary region: asia-southeast3 (default Bangkok) or asia-southeast1 (fallback)
  --secondary-region <reg>   DR backup copy region (default: asia-southeast1 Singapore)
  --vertex-location <loc>    Regional Vertex AI location (default: asia-southeast1; NEVER global)
  --instance <id>            Primary Spanner instance ID (default: sdlc-kg-enterprise)
  --dr-instance <id>         Secondary Singapore DR instance ID (default: sdlc-kg-dr-singapore)
  --database <id>            Spanner database ID (default: sdlc_knowledge_graph)
  --processing-units <pu>    Processing units (default: 100 sandbox; production requires >= 1000)
  --with-dynamic-graph       Also apply 02_path_b_schemaless_comparison.sql during create
  --skip-fgac                Skip applying 04_fgac_roles_views.sql during create
  --dry-run                  Print all commands without mutating GCP resources
  --yes                      Skip interactive confirmation prompt for cleanup
  -h, --help                 Show this help message
EOF
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 1
fi

COMMAND="$1"
shift

case "${COMMAND}" in
  create|apply-postload|backup-to-singapore|cleanup) ;;
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
    --project)
      PROJECT_ID="$2"
      shift 2
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    --secondary-region)
      SECONDARY_REGION="$2"
      shift 2
      ;;
    --vertex-location)
      VERTEX_AI_LOCATION="$2"
      shift 2
      ;;
    --instance)
      SPANNER_INSTANCE="$2"
      shift 2
      ;;
    --dr-instance)
      SPANNER_SECONDARY_INSTANCE="$2"
      shift 2
      ;;
    --database)
      SPANNER_DATABASE="$2"
      shift 2
      ;;
    --processing-units)
      SPANNER_PROCESSING_UNITS="$2"
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

INSTANCE_CONFIG="regional-${REGION}"
DR_INSTANCE_CONFIG="regional-${SECONDARY_REGION}"

run_cmd() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '[DRY-RUN]'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

print_banner() {
  echo "=============================================================================="
  echo "Cloud Spanner Enterprise Workshop — ${COMMAND}"
  echo "  Mode              : $([[ "${DRY_RUN}" == "true" ]] && echo "DRY-RUN" || echo "LIVE GCP")"
  echo "  Project           : ${PROJECT_ID}"
  echo "  Primary Region    : ${REGION} (${INSTANCE_CONFIG})"
  echo "  Secondary / AI/DR : ${SECONDARY_REGION} (${DR_INSTANCE_CONFIG} | Vertex AI: ${VERTEX_AI_LOCATION})"
  echo "  Instance / DB     : ${SPANNER_INSTANCE} / ${SPANNER_DATABASE}"
  echo "  Edition / PUs     : ${SPANNER_EDITION} / ${SPANNER_PROCESSING_UNITS} PU"
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

  echo "[1/4] Enabling required Google Cloud APIs (Spanner + Vertex AI)..."
  run_cmd gcloud services enable \
    spanner.googleapis.com \
    aiplatform.googleapis.com \
    --project="${PROJECT_ID}"

  echo "[2/4] Provisioning Cloud Spanner ${SPANNER_EDITION} instance (${INSTANCE_CONFIG}, ${SPANNER_PROCESSING_UNITS} PU, backup schedule NONE)..."
  if [[ "${DRY_RUN}" == "true" ]] || ! gcloud spanner instances describe "${SPANNER_INSTANCE}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    run_cmd gcloud spanner instances create "${SPANNER_INSTANCE}" \
      --project="${PROJECT_ID}" \
      --config="${INSTANCE_CONFIG}" \
      --description="Acme Commerce SDLC Knowledge Graph Workshop (${REGION})" \
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

  echo "[4/4] Applying base schema DDL (01_path_a_schematized.sql)..."
  run_cmd gcloud spanner databases ddl update "${SPANNER_DATABASE}" \
    --project="${PROJECT_ID}" \
    --instance="${SPANNER_INSTANCE}" \
    --ddl-file="${REPO_ROOT}/solution/sql/01_path_a_schematized.sql"

  if [[ "${INCLUDE_DYNAMIC_GRAPH}" == "true" ]]; then
    echo "[4b] Applying schemaless comparison DDL (02_path_b_schemaless_comparison.sql)..."
    run_cmd gcloud spanner databases ddl update "${SPANNER_DATABASE}" \
      --project="${PROJECT_ID}" \
      --instance="${SPANNER_INSTANCE}" \
      --ddl-file="${REPO_ROOT}/solution/sql/02_path_b_schemaless_comparison.sql"
  fi

  if [[ "${INCLUDE_FGAC}" == "true" ]]; then
    echo "[4c] Applying FGAC roles & views DDL (04_fgac_roles_views.sql)..."
    run_cmd gcloud spanner databases ddl update "${SPANNER_DATABASE}" \
      --project="${PROJECT_ID}" \
      --instance="${SPANNER_INSTANCE}" \
      --ddl-file="${REPO_ROOT}/solution/sql/04_fgac_roles_views.sql"
  fi

  echo ""
  echo "[OK] Sandbox provisioned. Next steps:"
  echo "  1. python3 solution/pipelines/neo4j_to_spanner_loader.py --project ${PROJECT_ID} --region ${REGION}"
  echo "  2. python3 solution/pipelines/unstructured_graphrag_ingest.py --project ${PROJECT_ID} --region ${REGION} --vertex-location ${VERTEX_AI_LOCATION}"
  echo "  3. bash scripts/provision_workshop_env.sh apply-postload --project ${PROJECT_ID} --region ${REGION} --vertex-location ${VERTEX_AI_LOCATION}"
}

cmd_apply_postload() {
  print_banner
  local -a postload_args=(
    python3 "${REPO_ROOT}/solution/pipelines/apply_postload.py"
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
  echo "[POST-LOAD] Running 4-stage orchestrator (03a -> 03b ${VERTEX_AI_LOCATION} -> 03 batched LROs -> ANALYZE)..."
  "${postload_args[@]}"
}

cmd_backup_to_singapore() {
  print_banner
  local ts
  ts="$(date -u +%Y%m%d-%H%M%S)"
  local src_backup="bkk-sdlc-bkp-${ts}"
  local dr_backup="sg-dr-sdlc-bkp-${ts}"
  local exp_date
  exp_date="$(python3 -c "import datetime; print((datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=${BACKUP_RETENTION_DAYS})).strftime('%Y-%m-%dT%H:%M:%SZ'))")"

  echo "[INFO] Southeast Asia DR Pattern: Cloud Spanner has no pre-built Bangkok+Singapore dual-region."
  echo "       Copying Bangkok backup to ${DR_INSTANCE_CONFIG} costs \$0.08/GiB Asia egress + \$0.03432/GiB-mo DR storage."
  echo "       REMINDER: Backups preserve SQL FGAC roles/grants, but NOT IAM bindings — re-apply IAM after restore."
  echo ""

  echo "[1/3] Creating source backup '${src_backup}' in ${INSTANCE_CONFIG} (${SPANNER_INSTANCE})..."
  run_cmd gcloud spanner backups create "${src_backup}" \
    --project="${PROJECT_ID}" \
    --instance="${SPANNER_INSTANCE}" \
    --database="${SPANNER_DATABASE}" \
    --expiration-date="${exp_date}"

  echo "[2/3] Ensuring destination Singapore DR instance '${SPANNER_SECONDARY_INSTANCE}' (${DR_INSTANCE_CONFIG})..."
  if [[ "${DRY_RUN}" == "true" ]] || ! gcloud spanner instances describe "${SPANNER_SECONDARY_INSTANCE}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    run_cmd gcloud spanner instances create "${SPANNER_SECONDARY_INSTANCE}" \
      --project="${PROJECT_ID}" \
      --config="${DR_INSTANCE_CONFIG}" \
      --description="Acme Commerce SDLC DR Backup Target (${SECONDARY_REGION})" \
      --edition="${SPANNER_EDITION}" \
      --processing-units=100 \
      --default-backup-schedule-type=NONE
  else
    echo "[OK] DR destination instance '${SPANNER_SECONDARY_INSTANCE}' already exists."
  fi

  echo "[3/3] Copying backup cross-region: ${SPANNER_INSTANCE}/${src_backup} -> ${SPANNER_SECONDARY_INSTANCE}/${dr_backup}..."
  run_cmd gcloud spanner backups copy \
    --project="${PROJECT_ID}" \
    --source-instance="${SPANNER_INSTANCE}" \
    --source-backup="${src_backup}" \
    --destination-instance="${SPANNER_SECONDARY_INSTANCE}" \
    --destination-backup="${dr_backup}" \
    --expiration-date="${exp_date}"

  echo "[OK] Cross-region DR backup copy complete (${dr_backup} in ${DR_INSTANCE_CONFIG})."
}

delete_instance_backups_and_instance() {
  local inst="$1"
  if [[ "${DRY_RUN}" == "true" ]]; then
    run_cmd gcloud spanner backups list --project="${PROJECT_ID}" --instance="${inst}" --format="value(name)"
    run_cmd gcloud spanner instances update "${inst}" --project="${PROJECT_ID}" --no-enable-drop-protection
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

  echo "[CLEANUP] Ensuring drop protection is disabled on '${inst}'..."
  gcloud spanner instances update "${inst}" \
    --project="${PROJECT_ID}" \
    --no-enable-drop-protection >/dev/null 2>&1 || true

  echo "[CLEANUP] Deleting Cloud Spanner instance '${inst}'..."
  gcloud spanner instances delete "${inst}" --project="${PROJECT_ID}" --quiet
  echo "[OK] Deleted instance '${inst}'."
}

cmd_cleanup() {
  print_banner
  if [[ "${DRY_RUN}" != "true" && "${AUTO_APPROVE}" != "true" ]]; then
    echo "WARNING: This permanently deletes '${SPANNER_INSTANCE}' (and '${SPANNER_SECONDARY_INSTANCE}' if present)"
    echo "         plus all backups in project '${PROJECT_ID}'."
    read -r -p "Type 'DELETE' to confirm teardown: " confirm
    if [[ "${confirm}" != "DELETE" ]]; then
      echo "Aborted teardown."
      exit 1
    fi
  fi

  delete_instance_backups_and_instance "${SPANNER_SECONDARY_INSTANCE}"
  delete_instance_backups_and_instance "${SPANNER_INSTANCE}"

  echo "[OK] Workshop Cloud Spanner teardown complete. Verify zero residual billing in Cloud Console."
}

case "${COMMAND}" in
  create) cmd_create ;;
  apply-postload) cmd_apply_postload ;;
  backup-to-singapore) cmd_backup_to_singapore ;;
  cleanup) cmd_cleanup ;;
esac
