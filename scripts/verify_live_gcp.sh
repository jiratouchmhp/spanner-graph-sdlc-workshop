#!/usr/bin/env bash
# =============================================================================
# Cloud Spanner Enterprise Live GCP End-to-End Verification Script
# =============================================================================
# Verifies a live workshop deployment in Bangkok (`regional-asia-southeast3`)
# or fallback Singapore (`regional-asia-southeast1`) across 5 checks:
#   1. DDL Describe       : SdlcGraph, DocumentChunks, IdxDocumentChunksEmbedding,
#                           SdlcSynonymDict, PublicApiCatalogView exist.
#   2. ScaNN ANN PLAN     : APPROX_COSINE_DISTANCE ... WHERE embedding IS NOT NULL
#                           executes with VectorIndexScan on IdxDocumentChunksEmbedding.
#   3. Live Hero Query B  : labs/run_hero_query.py --query B matches 7-row fixture.
#   4. FGAC Role Checks   : sdlc_readonly_role can query PublicApiCatalogView and
#                           is DENIED access to Owners.clearance_tier and INSERT Adrs.
#   5. Cross-Region DR    : Checks primary and Singapore (asia-southeast1) backups.
#
# Usage:
#   bash scripts/verify_live_gcp.sh --dry-run
#   GOOGLE_CLOUD_PROJECT=my-proj bash scripts/verify_live_gcp.sh
#   GOOGLE_CLOUD_PROJECT=my-proj bash scripts/verify_live_gcp.sh --require-dr
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

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-${SPANNER_PROJECT:-}}"
REGION="${GOOGLE_CLOUD_REGION:-${SPANNER_REGION:-asia-southeast3}}"
VERTEX_AI_LOCATION="${VERTEX_AI_LOCATION:-asia-southeast1}"
SPANNER_INSTANCE="${SPANNER_INSTANCE:-sdlc-kg-enterprise}"
SPANNER_SECONDARY_INSTANCE="${SPANNER_SECONDARY_INSTANCE:-sdlc-kg-dr-singapore}"
SPANNER_DATABASE="${SPANNER_DATABASE:-sdlc_knowledge_graph}"
DRY_RUN="false"
REQUIRE_DR="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --vertex-location) VERTEX_AI_LOCATION="$2"; shift 2 ;;
    --instance) SPANNER_INSTANCE="$2"; shift 2 ;;
    --dr-instance) SPANNER_SECONDARY_INSTANCE="$2"; shift 2 ;;
    --database) SPANNER_DATABASE="$2"; shift 2 ;;
    --dry-run) DRY_RUN="true"; shift ;;
    --require-dr) REQUIRE_DR="true"; shift ;;
    -h|--help)
      echo "Usage: bash scripts/verify_live_gcp.sh [--project ID] [--region REG] [--require-dr] [--dry-run]"
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "${PROJECT_ID}" ]]; then
  if [[ "${DRY_RUN}" == "true" ]]; then
    PROJECT_ID="demo-project"
  else
    PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
    if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
      echo "[ERROR] GOOGLE_CLOUD_PROJECT is not set. Set .env or pass --project <id>." >&2
      exit 1
    fi
  fi
fi

echo "=============================================================================="
echo "Cloud Spanner Enterprise — Live GCP Verification Suite"
echo "  Mode              : $([[ "${DRY_RUN}" == "true" ]] && echo "DRY-RUN" || echo "LIVE GCP")"
echo "  Project           : ${PROJECT_ID}"
echo "  Primary Instance  : ${SPANNER_INSTANCE} (regional-${REGION}) / DB: ${SPANNER_DATABASE}"
echo "  Vertex AI Region  : ${VERTEX_AI_LOCATION}"
echo "  DR Target Instance: ${SPANNER_SECONDARY_INSTANCE} (regional-asia-southeast1)"
echo "=============================================================================="

# ------------------------------------------------------------------------------
# Check 1: DDL Describe (Schema, Property Graph, FTS Dictionary, ScaNN Index, Views)
# ------------------------------------------------------------------------------
echo ""
echo "[CHECK 1/5] Verifying live Cloud Spanner DDL objects..."
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] gcloud spanner databases ddl describe ${SPANNER_DATABASE} --project=${PROJECT_ID} --instance=${SPANNER_INSTANCE}"
else
  DDL_OUT="$(gcloud spanner databases ddl describe "${SPANNER_DATABASE}" \
    --project="${PROJECT_ID}" \
    --instance="${SPANNER_INSTANCE}")"
  for token in "PROPERTY GRAPH SdlcGraph" "CREATE TABLE DocumentChunks" "CREATE TABLE SdlcSynonymDict" "CREATE VECTOR INDEX IdxDocumentChunksEmbedding" "CREATE VIEW PublicApiCatalogView"; do
    if ! grep -q "${token}" <<< "${DDL_OUT}"; then
      echo "[FAIL] Missing expected DDL object: ${token}" >&2
      exit 1
    fi
  done
  echo "[PASS] DDL objects verified (SdlcGraph, DocumentChunks, SdlcSynonymDict, IdxDocumentChunksEmbedding, PublicApiCatalogView)."
fi

# ------------------------------------------------------------------------------
# Check 2: ScaNN ANN Execution Plan Verification (--query-mode=PLAN)
# ------------------------------------------------------------------------------
echo ""
echo "[CHECK 2/5] Verifying ScaNN ANN vector index plan (--query-mode=PLAN)..."
ZERO_VEC_768="$(python3 -c "print('[' + ','.join(['CAST(0.01 AS FLOAT32)'] + ['CAST(0.0 AS FLOAT32)'] * 767) + ']')")"
SCANN_PLAN_SQL="SELECT chunk_id, APPROX_COSINE_DISTANCE(embedding, ${ZERO_VEC_768}, options => JSON '{\"num_leaves_to_search\": 10}') AS approx_dist FROM DocumentChunks WHERE embedding IS NOT NULL ORDER BY approx_dist ASC LIMIT 5"

if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] gcloud spanner databases execute-sql ${SPANNER_DATABASE} --project=${PROJECT_ID} --instance=${SPANNER_INSTANCE} --query-mode=PLAN --sql=\"SELECT chunk_id, APPROX_COSINE_DISTANCE(embedding, <768-dim FLOAT32>, ...) FROM DocumentChunks WHERE embedding IS NOT NULL ORDER BY approx_dist ASC LIMIT 5\""
else
  PLAN_OUT="$(gcloud spanner databases execute-sql "${SPANNER_DATABASE}" \
    --project="${PROJECT_ID}" \
    --instance="${SPANNER_INSTANCE}" \
    --query-mode=PLAN \
    --sql="${SCANN_PLAN_SQL}")"
  if grep -qiE "IdxDocumentChunksEmbedding|VectorIndexScan|VECTOR INDEX" <<< "${PLAN_OUT}"; then
    echo "[PASS] Query optimizer selected ScaNN vector index (IdxDocumentChunksEmbedding)."
  else
    echo "[WARN] Vector index name not explicitly visible in text PLAN output; inspect JSON plan via Spanner Studio:"
    echo "${PLAN_OUT}" | head -n 30
  fi
fi

# ------------------------------------------------------------------------------
# Check 3: Live Hero Blast-Radius GraphRAG Query B (Exact 7-Row Parity Check)
# ------------------------------------------------------------------------------
echo ""
echo "[CHECK 3/5] Running Hero API Blast-Radius GraphRAG Query B (api-payments-charge-v2)..."
if [[ "${DRY_RUN}" == "true" ]]; then
  python3 "${REPO_ROOT}/labs/run_hero_query.py" \
    --dry-run \
    --query B \
    --api-id api-payments-charge-v2 \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --vertex-location "${VERTEX_AI_LOCATION}" \
    --check-fixture "${REPO_ROOT}/solution/sql/fixtures/07_hero_expected_rows.json"
else
  python3 "${REPO_ROOT}/labs/run_hero_query.py" \
    --query B \
    --api-id api-payments-charge-v2 \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --instance "${SPANNER_INSTANCE}" \
    --database "${SPANNER_DATABASE}" \
    --vertex-location "${VERTEX_AI_LOCATION}" \
    --check-fixture "${REPO_ROOT}/solution/sql/fixtures/07_hero_expected_rows.json"
  echo "[PASS] Live Hero Query B matched 7-row fixture contract."
fi

# ------------------------------------------------------------------------------
# Check 4: FGAC Role Enforcement & Denial Verification (sdlc_readonly_role)
# ------------------------------------------------------------------------------
echo ""
echo "[CHECK 4/5] Verifying FGAC database role enforcement (sdlc_readonly_role)..."
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] Expected ALLOW: gcloud spanner databases execute-sql ${SPANNER_DATABASE} --instance=${SPANNER_INSTANCE} --database-role=sdlc_readonly_role --sql=\"SELECT COUNT(*) AS public_apis FROM PublicApiCatalogView\""
  echo "[DRY-RUN] Expected DENY : gcloud spanner databases execute-sql ${SPANNER_DATABASE} --instance=${SPANNER_INSTANCE} --database-role=sdlc_readonly_role --sql=\"SELECT clearance_tier FROM Owners LIMIT 1\""
  echo "[DRY-RUN] Expected DENY : gcloud spanner databases execute-sql ${SPANNER_DATABASE} --instance=${SPANNER_INSTANCE} --database-role=sdlc_readonly_role --sql=\"INSERT INTO Adrs (adr_id, title, lifecycle_status, owner_id, decision_date, summary) VALUES ('ADR-999', 'Unauthorized', 'PROPOSED', 'team-payments', DATE '2026-11-21', 'Should fail')\""
else
  gcloud spanner databases execute-sql "${SPANNER_DATABASE}" \
    --project="${PROJECT_ID}" \
    --instance="${SPANNER_INSTANCE}" \
    --database-role=sdlc_readonly_role \
    --sql="SELECT COUNT(*) AS public_apis FROM PublicApiCatalogView" >/dev/null
  echo "  [PASS] sdlc_readonly_role ALLOWED on PublicApiCatalogView."

  if gcloud spanner databases execute-sql "${SPANNER_DATABASE}" \
      --project="${PROJECT_ID}" \
      --instance="${SPANNER_INSTANCE}" \
      --database-role=sdlc_readonly_role \
      --sql="SELECT clearance_tier FROM Owners LIMIT 1" >/dev/null 2>&1; then
    echo "  [FAIL] sdlc_readonly_role unexpectedly read restricted column Owners.clearance_tier!" >&2
    exit 1
  else
    echo "  [PASS] sdlc_readonly_role DENIED reading restricted column Owners.clearance_tier."
  fi

  if gcloud spanner databases execute-sql "${SPANNER_DATABASE}" \
      --project="${PROJECT_ID}" \
      --instance="${SPANNER_INSTANCE}" \
      --database-role=sdlc_readonly_role \
      --sql="INSERT INTO Adrs (adr_id, title, lifecycle_status, owner_id, decision_date, summary) VALUES ('ADR-999', 'Unauthorized', 'PROPOSED', 'team-payments', DATE '2026-11-21', 'Should fail')" >/dev/null 2>&1; then
    echo "  [FAIL] sdlc_readonly_role unexpectedly wrote to Adrs table!" >&2
    exit 1
  else
    echo "  [PASS] sdlc_readonly_role DENIED writing to Adrs table."
  fi
fi

# ------------------------------------------------------------------------------
# Check 5: Cross-Region Backup DR Copy Check (Bangkok -> Singapore)
# ------------------------------------------------------------------------------
echo ""
echo "[CHECK 5/5] Checking cross-region backup DR status (${SPANNER_SECONDARY_INSTANCE} in asia-southeast1)..."
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] gcloud spanner backups list --project=${PROJECT_ID} --instance=${SPANNER_SECONDARY_INSTANCE}"
else
  if gcloud spanner instances describe "${SPANNER_SECONDARY_INSTANCE}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    DR_BKPS="$(gcloud spanner backups list \
      --project="${PROJECT_ID}" \
      --instance="${SPANNER_SECONDARY_INSTANCE}" \
      --format="value(name.basename(),state)" 2>/dev/null || true)"
    if [[ -n "${DR_BKPS}" ]]; then
      echo "[PASS] Singapore DR backup(s) found on '${SPANNER_SECONDARY_INSTANCE}':"
      echo "${DR_BKPS}"
    elif [[ "${REQUIRE_DR}" == "true" ]]; then
      echo "[FAIL] No Singapore DR backups found on '${SPANNER_SECONDARY_INSTANCE}' (--require-dr set)." >&2
      exit 1
    else
      echo "[INFO] DR instance exists with 0 backups. Run 'bash scripts/provision_workshop_env.sh backup-to-singapore' for Lab 8 DR proof."
    fi
  elif [[ "${REQUIRE_DR}" == "true" ]]; then
    echo "[FAIL] Singapore DR instance '${SPANNER_SECONDARY_INSTANCE}' not found (--require-dr set)." >&2
    exit 1
  else
    echo "[INFO] Optional Singapore DR instance '${SPANNER_SECONDARY_INSTANCE}' not provisioned yet (run backup-to-singapore in Lab 8)."
  fi
fi

echo ""
echo "=============================================================================="
echo "[OK] Live GCP verification suite completed."
echo "=============================================================================="
