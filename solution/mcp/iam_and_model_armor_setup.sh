#!/usr/bin/env bash
# =============================================================================
# Module 6: Dual-Layer Per-Agent RBAC, Isolated Bangkok Cloud Run MCP Toolbox,
#           Spanner FGAC Conditional IAM, Model Armor (Singapore), Cloud DLP,
#           and Cloud Data Access Audit Logs
# =============================================================================
#
# CRITICAL AUTHORIZATION ARCHITECTURE (TWO SEPARATE LAYERS):
#
#   Layer 1 — Self-Hosted Cloud Run MCP Toolbox (Bangkok: asia-southeast3)
#     MCP Toolbox's `type: spanner` source creates Spanner sessions via ADC
#     without passing a Spanner `database_role`. Therefore, each Cloud Run
#     persona runs under a dedicated Service Account holding least-privilege
#     DATABASE-LEVEL IAM combined with a sliced per-persona YAML mounted from
#     Secret Manager, explicit `readOnly: true`, OIDC `authServices`, and
#     `--no-allow-unauthenticated`:
#       - sa-impact-analyst    -> roles/spanner.databaseReader (4 readOnly tools)
#       - sa-adr-curator       -> roles/spanner.databaseUser   (4 read + 1 PROPOSED ADR write)
#       - sa-security-auditor  -> roles/spanner.databaseReader (4 readOnly compliance tools)
#
#   Layer 2 — Dedicated FGAC Service Accounts (`database_role`-aware clients ONLY)
#     Used by Spanner Managed Remote MCP (`https://spanner.googleapis.com/mcp`
#     via `create_session.fineGrainedAccessRole`), Cloud Spanner SDKs, ADK
#     `SpannerToolset`, and `gcloud spanner databases execute-sql --database-role`:
#       - sa-fgac-readonly          -> sdlc_readonly_role
#       - sa-fgac-adr-writer        -> sdlc_adr_writer_role
#       - sa-fgac-security-auditor  -> sdlc_security_auditor_role
#     These accounts hold ONLY `roles/spanner.fineGrainedAccessUser` +
#     CONDITIONAL `roles/spanner.databaseRoleUser` and ZERO database-level IAM
#     roles so callers can NEVER bypass FGAC by omitting `database_role`.
#
# REGIONAL TOPOLOGY & THAILAND PDPA NOTICE:
#   - Primary Region (Bangkok): `asia-southeast3` (`regional-asia-southeast3`)
#     Hosts Cloud Spanner Enterprise (3-zone sync quorum, 99.99% SLA, Thailand
#     PDPA residency) and all 3 isolated Cloud Run MCP Toolbox services.
#   - Secondary / AI & DR Region (Singapore): `asia-southeast1` (~25 ms RTT)
#     Hosts Vertex AI (`gemini-embedding-001`, `gemini-2.5-flash`), Model Armor
#     regional template (`modelarmor.asia-southeast1.rep.googleapis.com`),
#     Cloud DLP inspect templates, and optional cross-region DR backup copies.
#   - PDPA Notice: Model Armor is NOT currently available in `asia-southeast3`.
#     Routing prompts/responses to `asia-southeast1` crosses the Thailand border;
#     document cross-jurisdictional transit approval in PDPA compliance reviews.
#
# Usage:
#   bash solution/mcp/iam_and_model_armor_setup.sh --dry-run
#   GOOGLE_CLOUD_PROJECT=my-proj bash solution/mcp/iam_and_model_armor_setup.sh
#   GOOGLE_CLOUD_PROJECT=my-proj bash solution/mcp/iam_and_model_armor_setup.sh --enable-audit-logs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +a
fi

DRY_RUN="${DRY_RUN:-false}"
ENABLE_AUDIT_LOGS="false"

for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN="true" ;;
    --enable-audit-logs) ENABLE_AUDIT_LOGS="true" ;;
    -h|--help)
      echo "Usage: $0 [--dry-run] [--enable-audit-logs]"
      echo "Env: GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_REGION (asia-southeast3),"
      echo "     VERTEX_AI_LOCATION (asia-southeast1), SPANNER_INSTANCE, SPANNER_DATABASE"
      exit 0
      ;;
    *)
      echo "Unknown argument: ${arg}" >&2
      exit 1
      ;;
  esac
done

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo 'YOUR_GCP_PROJECT_ID')}"
REGION="${GOOGLE_CLOUD_REGION:-asia-southeast3}"
DR_REGION="${GOOGLE_CLOUD_SECONDARY_REGION:-asia-southeast1}"
VERTEX_AI_LOCATION="${VERTEX_AI_LOCATION:-asia-southeast1}"
MODEL_ARMOR_LOCATION="${MODEL_ARMOR_LOCATION:-asia-southeast1}"
MODEL_ARMOR_ENDPOINT="https://modelarmor.${MODEL_ARMOR_LOCATION}.rep.googleapis.com/"
SPANNER_INSTANCE="${SPANNER_INSTANCE:-sdlc-kg-enterprise}"
SPANNER_DATABASE="${SPANNER_DATABASE:-sdlc_knowledge_graph}"
GOOGLE_OAUTH_CLIENT_ID="${GOOGLE_OAUTH_CLIENT_ID:-demo-oauth-client-id.apps.googleusercontent.com}"
# Pin an immutable SHA256 digest in production releases:
TOOLBOX_IMAGE="${TOOLBOX_IMAGE:-us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:latest}"
MODEL_ARMOR_TEMPLATE_ID="${MODEL_ARMOR_TEMPLATE_ID:-sdlc-agent-guardrail-sg}"
DLP_TEMPLATE_ID="${DLP_TEMPLATE_ID:-sdlc-pci-secret-inspector}"

if [[ "${VERTEX_AI_LOCATION}" == "global" ]]; then
  echo "[ERROR] VERTEX_AI_LOCATION must be regional (e.g., asia-southeast1), never 'global' for gemini-embedding-001." >&2
  exit 1
fi

run_cmd() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '[DRY-RUN]'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

ensure_sa() {
  local sa_name="$1"
  local display_name="$2"
  local sa_email="${sa_name}@${PROJECT_ID}.iam.gserviceaccount.com"
  if [[ "${DRY_RUN}" == "true" ]]; then
    run_cmd gcloud iam service-accounts create "${sa_name}" \
      --project="${PROJECT_ID}" \
      --display-name="${display_name}"
  else
    if ! gcloud iam service-accounts describe "${sa_email}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
      gcloud iam service-accounts create "${sa_name}" \
        --project="${PROJECT_ID}" \
        --display-name="${display_name}"
    else
      echo "[OK] Service account already exists: ${sa_email}"
    fi
  fi
}

upsert_secret_from_file() {
  local secret_name="$1"
  local file_path="$2"
  if [[ "${DRY_RUN}" == "true" ]]; then
    run_cmd gcloud secrets create "${secret_name}" \
      --project="${PROJECT_ID}" \
      --replication-policy="user-managed" \
      --locations="${REGION}" \
      --data-file="${file_path}"
  else
    if ! gcloud secrets describe "${secret_name}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
      gcloud secrets create "${secret_name}" \
        --project="${PROJECT_ID}" \
        --replication-policy="user-managed" \
        --locations="${REGION}" \
        --data-file="${file_path}"
    else
      gcloud secrets versions add "${secret_name}" \
        --project="${PROJECT_ID}" \
        --data-file="${file_path}"
    fi
  fi
}

echo "==========================================================================="
echo "Module 6 Security & MCP Toolbox Setup"
echo "  Project ID           : ${PROJECT_ID}"
echo "  Primary Region       : ${REGION} (Bangkok — Spanner + Cloud Run MCP Toolbox)"
echo "  Secondary / AI / DR  : ${DR_REGION} (Singapore — Vertex AI + Model Armor + DR)"
echo "  Spanner Target       : ${SPANNER_INSTANCE}/${SPANNER_DATABASE}"
echo "  Dry-Run Mode         : ${DRY_RUN}"
echo "==========================================================================="

# -----------------------------------------------------------------------------
# Step 0: Enable Required APIs & Render Isolated Per-Persona YAML Files
# -----------------------------------------------------------------------------
echo ""
echo "[Step 0] Enabling required Google Cloud APIs & verifying persona YAMLs..."
run_cmd gcloud services enable \
  run.googleapis.com \
  secretmanager.googleapis.com \
  spanner.googleapis.com \
  aiplatform.googleapis.com \
  modelarmor.googleapis.com \
  dlp.googleapis.com \
  iam.googleapis.com \
  cloudasset.googleapis.com \
  --project="${PROJECT_ID}"

run_cmd python3 "${SCRIPT_DIR}/render_persona_tools.py"

# -----------------------------------------------------------------------------
# Step 1: Layer 1 — Provision 3 Cloud Run MCP Toolbox Persona Service Accounts
#         + 1 ADK Agent Runner Service Account
# -----------------------------------------------------------------------------
echo ""
echo "[Step 1] Provisioning Layer 1 Cloud Run MCP Toolbox Persona Service Accounts..."

ensure_sa "sa-impact-analyst" "MCP Toolbox Impact Analyst (Read-Only)"
ensure_sa "sa-adr-curator" "MCP Toolbox ADR Curator (Scoped PROPOSED Write)"
ensure_sa "sa-security-auditor" "MCP Toolbox Security Auditor (Read-Only Compliance)"
ensure_sa "sa-adk-agent-runner" "ADK Multi-Agent Orchestrator Caller SA"

# Grant least-privilege DATABASE-LEVEL IAM for Layer 1 Cloud Run MCP Toolbox SAs
# (Required because type: spanner creates ADC sessions without database_role).
declare -A TOOLBOX_DB_ROLES=(
  ["sa-impact-analyst"]="roles/spanner.databaseReader"
  ["sa-adr-curator"]="roles/spanner.databaseUser"
  ["sa-security-auditor"]="roles/spanner.databaseReader"
)

for sa_name in "sa-impact-analyst" "sa-adr-curator" "sa-security-auditor"; do
  sa_email="${sa_name}@${PROJECT_ID}.iam.gserviceaccount.com"
  db_role="${TOOLBOX_DB_ROLES[${sa_name}]}"

  # Database-level Spanner IAM (never project-level roles/spanner.admin!)
  run_cmd gcloud spanner databases add-iam-policy-binding "${SPANNER_DATABASE}" \
    --project="${PROJECT_ID}" \
    --instance="${SPANNER_INSTANCE}" \
    --member="serviceAccount:${sa_email}" \
    --role="${db_role}" \
    --condition=None

  # Secretless regional Vertex AI embeddings (asia-southeast1) via Workload Identity
  run_cmd gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${sa_email}" \
    --role="roles/aiplatform.user" \
    --condition=None

  # Secret Manager read access for mounted persona tools.yaml
  run_cmd gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${sa_email}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None
done

# Grant ADK runner SA Vertex AI (gemini-2.5-flash) and Model Armor caller roles
ADK_SA_EMAIL="sa-adk-agent-runner@${PROJECT_ID}.iam.gserviceaccount.com"
run_cmd gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${ADK_SA_EMAIL}" \
  --role="roles/aiplatform.user" \
  --condition=None
run_cmd gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${ADK_SA_EMAIL}" \
  --role="roles/modelarmor.user" \
  --condition=None

# -----------------------------------------------------------------------------
# Step 2: Deploy 3 Isolated Cloud Run MCP Toolbox Services in Bangkok
#         (asia-southeast3 — co-located with regional-asia-southeast3 Spanner)
# -----------------------------------------------------------------------------
echo ""
echo "[Step 2] Deploying 3 isolated Cloud Run MCP Toolbox services in ${REGION} (Bangkok)..."
echo "         NOTE: Cloud Run IAP is unsupported by MCP Toolbox — leave IAP disabled."
echo "         NOTE: If combining Cloud Run IAM + MCP OAuth (mcpEnabled: true), send"
echo "               the Cloud Run IAM ID token in X-Serverless-Authorization."

deploy_persona_service() {
  local persona="$1"
  local sa_name="$2"
  local svc_name="sdlc-mcp-${persona}"
  local secret_name="sdlc-tools-${persona}"
  local yaml_file="${SCRIPT_DIR}/personas/tools.${persona}.yaml"
  local sa_email="${sa_name}@${PROJECT_ID}.iam.gserviceaccount.com"

  # Store sliced persona YAML in Bangkok-resident Secret Manager
  upsert_secret_from_file "${secret_name}" "${yaml_file}"

  # Pass 1: Initial deployment bound strictly to 0.0.0.0:8080 with --no-allow-unauthenticated
  run_cmd gcloud run deploy "${svc_name}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --image="${TOOLBOX_IMAGE}" \
    --service-account="${sa_email}" \
    --no-allow-unauthenticated \
    --set-env-vars="SPANNER_PROJECT=${PROJECT_ID},SPANNER_INSTANCE=${SPANNER_INSTANCE},SPANNER_DATABASE=${SPANNER_DATABASE},SPANNER_DIALECT=googlesql,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},VERTEX_AI_LOCATION=${VERTEX_AI_LOCATION},GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID}" \
    --set-secrets="/app/tools.yaml=${secret_name}:latest" \
    --args="--config=/app/tools.yaml,--address=0.0.0.0,--port=8080"

  # Pass 2: Lock down DNS rebinding (--allowed-hosts) & CORS (--allowed-origins),
  #         enable JSON logs, Cloud Monitoring telemetry, SQLCommenter, immutable config
  local svc_url svc_host
  if [[ "${DRY_RUN}" == "true" ]]; then
    svc_url="https://${svc_name}-PROJECT_HASH-as.a.run.app"
  else
    svc_url="$(gcloud run services describe "${svc_name}" \
      --project="${PROJECT_ID}" \
      --region="${REGION}" \
      --format='value(status.url)')"
  fi
  svc_host="${svc_url#https://}"

  run_cmd gcloud run deploy "${svc_name}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --image="${TOOLBOX_IMAGE}" \
    --service-account="${sa_email}" \
    --no-allow-unauthenticated \
    --set-env-vars="SPANNER_PROJECT=${PROJECT_ID},SPANNER_INSTANCE=${SPANNER_INSTANCE},SPANNER_DATABASE=${SPANNER_DATABASE},SPANNER_DIALECT=googlesql,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},VERTEX_AI_LOCATION=${VERTEX_AI_LOCATION},GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID},TOOLBOX_URL=${svc_url}" \
    --set-secrets="/app/tools.yaml=${secret_name}:latest" \
    --args="--config=/app/tools.yaml,--address=0.0.0.0,--port=8080,--allowed-origins=${svc_url},--allowed-hosts=${svc_host},--logging-format=JSON,--telemetry-gcp,--sql-commenter,--disable-reload"

  # Restrict Cloud Run invocation strictly to the ADK agent runner identity
  run_cmd gcloud run services add-iam-policy-binding "${svc_name}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --member="serviceAccount:${ADK_SA_EMAIL}" \
    --role="roles/run.invoker"
}

deploy_persona_service "impact-analyst" "sa-impact-analyst"
deploy_persona_service "adr-curator" "sa-adr-curator"
deploy_persona_service "security-auditor" "sa-security-auditor"

# -----------------------------------------------------------------------------
# Optional DR Notes for Singapore (asia-southeast1):
#   Cloud Spanner has no pre-built Bangkok + Singapore dual-region config.
#   For regional DR failover to Singapore (regional-asia-southeast1):
#     1. Copy Bangkok backups to Singapore via `gcloud spanner backups copy`
#        ($0.08/GiB Asia cross-region egress + $0.03432/GiB-mo Singapore backup).
#     2. Re-run this script with GOOGLE_CLOUD_REGION=asia-southeast1 pointing at
#        the restored Singapore DR instance (regional-asia-southeast1, $1.40712/node-hr).
#     3. CRITICAL: Spanner backups preserve SQL FGAC roles/grants, but do NOT
#        include IAM policies — always re-run Step 3 below after restoring!
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Step 3: Layer 2 — Provision Dedicated Spanner FGAC Service Accounts
#         (ZERO database-level IAM roles; conditional roles/spanner.databaseRoleUser)
# -----------------------------------------------------------------------------
echo ""
echo "[Step 3] Configuring Layer 2 dedicated Spanner FGAC Service Accounts..."
echo "         (For Spanner Remote MCP create_session.fineGrainedAccessRole,"
echo "          Cloud Spanner SDKs, ADK SpannerToolset, and gcloud --database-role)"

declare -A FGAC_SA_TO_ROLE=(
  ["sa-fgac-readonly"]="sdlc_readonly_role"
  ["sa-fgac-adr-writer"]="sdlc_adr_writer_role"
  ["sa-fgac-security-auditor"]="sdlc_security_auditor_role"
)

for fgac_sa in "sa-fgac-readonly" "sa-fgac-adr-writer" "sa-fgac-security-auditor"; do
  fgac_role="${FGAC_SA_TO_ROLE[${fgac_sa}]}"
  fgac_email="${fgac_sa}@${PROJECT_ID}.iam.gserviceaccount.com"

  ensure_sa "${fgac_sa}" "Spanner FGAC Identity (${fgac_role})"

  # Step 3A: Enable FGAC access (grants spanner.databases.useRoleBasedAccess + public)
  run_cmd gcloud spanner databases add-iam-policy-binding "${SPANNER_DATABASE}" \
    --project="${PROJECT_ID}" \
    --instance="${SPANNER_INSTANCE}" \
    --member="serviceAccount:${fgac_email}" \
    --role="roles/spanner.fineGrainedAccessUser" \
    --condition=None

  # Step 3B: Bind ONLY the specific FGAC database role via MANDATORY IAM Condition
  # CAUTION: Omitting --condition grants access to ALL database roles!
  run_cmd gcloud spanner databases add-iam-policy-binding "${SPANNER_DATABASE}" \
    --project="${PROJECT_ID}" \
    --instance="${SPANNER_INSTANCE}" \
    --member="serviceAccount:${fgac_email}" \
    --role="roles/spanner.databaseRoleUser" \
    --condition="expression=(resource.type == \"spanner.googleapis.com/DatabaseRole\" && resource.name.endsWith(\"/${fgac_role}\")),title=RestrictTo_${fgac_role},description=Bind ${fgac_sa} exclusively to FGAC role ${fgac_role}"
done

echo ""
echo "[Step 3 Audit] Verify NO unconditioned roles/spanner.databaseRoleUser bindings exist:"
echo "  gcloud asset search-all-iam-policies \\"
echo "    --scope=\"projects/${PROJECT_ID}\" \\"
echo "    --query=\"roles:roles/spanner.databaseRoleUser AND resource=//spanner.googleapis.com/projects/${PROJECT_ID}/instances/${SPANNER_INSTANCE}/databases/${SPANNER_DATABASE}\" \\"
echo "    --flatten=\"policy.bindings[].members[]\""
echo ""
echo "[Step 3 Remote MCP Guardrail] If granting roles/mcp.toolUser on https://spanner.googleapis.com/mcp"
echo "  to sa-fgac-readonly, ALWAYS exempt 'create_session' (whose readOnlyHint is false) in IAM Deny CEL:"
echo "    resource.service == 'spanner.googleapis.com'"
echo "    && api.getAttribute('mcp.googleapis.com/tool.isReadOnly', false) == false"
echo "    && !(api.getAttribute('mcp.googleapis.com/tool.name', '') == 'create_session')"

# -----------------------------------------------------------------------------
# Step 4: Cloud DLP (Sensitive Data Protection) Inspection Template (Singapore)
# -----------------------------------------------------------------------------
echo ""
echo "[Step 4] Creating Cloud DLP inspect template (${DLP_TEMPLATE_ID}) in ${MODEL_ARMOR_LOCATION}..."

DLP_PAYLOAD="$(cat <<'EOF'
{
  "templateId": "sdlc-pci-secret-inspector",
  "inspectTemplate": {
    "displayName": "Acme SDLC PCI PAN & Credential Inspector",
    "description": "Detects raw PANs, credentials, emails, and tok_pci_v2 vault token leaks in agent prompts and responses.",
    "inspectConfig": {
      "infoTypes": [
        {"name": "CREDIT_CARD_NUMBER"},
        {"name": "EMAIL_ADDRESS"},
        {"name": "AUTH_TOKEN"},
        {"name": "GCP_CREDENTIALS"}
      ],
      "customInfoTypes": [
        {
          "infoType": {"name": "ACME_PCI_VAULT_TOKEN"},
          "likelihood": "VERY_LIKELY",
          "regex": {"pattern": "tok_pci_v2_[0-9a-fA-F]{32}"}
        }
      ],
      "minLikelihood": "POSSIBLE",
      "includeQuote": false
    }
  }
}
EOF
)"

if [[ "${DRY_RUN}" == "true" ]]; then
  echo "[DRY-RUN] POST https://dlp.googleapis.com/v2/projects/${PROJECT_ID}/locations/${MODEL_ARMOR_LOCATION}/inspectTemplates"
  echo "${DLP_PAYLOAD}"
else
  ACCESS_TOKEN="$(gcloud auth print-access-token)"
  curl -fsSL -X POST \
    "https://dlp.googleapis.com/v2/projects/${PROJECT_ID}/locations/${MODEL_ARMOR_LOCATION}/inspectTemplates" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -H "x-goog-user-project: ${PROJECT_ID}" \
    -d "${DLP_PAYLOAD}" || echo "[INFO] Cloud DLP inspect template ${DLP_TEMPLATE_ID} may already exist."
fi

# -----------------------------------------------------------------------------
# Step 5: Model Armor Regional Template in Singapore (asia-southeast1)
#         Endpoint: modelarmor.asia-southeast1.rep.googleapis.com
# -----------------------------------------------------------------------------
echo ""
echo "[Step 5] Configuring Model Armor regional template (${MODEL_ARMOR_TEMPLATE_ID}) in ${MODEL_ARMOR_LOCATION}..."
echo "         PDPA NOTICE: Model Armor is not available in asia-southeast3 (Bangkok)."
echo "         Singapore (asia-southeast1) supports RAI, SDP, and pi_and_jailbreak under DRZ"
echo "         (~25 ms RTT from Bangkok; malicious_uri/csam are unsupported under DRZ in asia-southeast1)."

run_cmd gcloud config set api_endpoint_overrides/modelarmor "${MODEL_ARMOR_ENDPOINT}"
run_cmd gcloud model-armor templates create "${MODEL_ARMOR_TEMPLATE_ID}" \
  --project="${PROJECT_ID}" \
  --location="${MODEL_ARMOR_LOCATION}" \
  --basic-config-filter-enforcement=enabled \
  --pi-and-jailbreak-filter-settings-enforcement=enabled \
  --pi-and-jailbreak-filter-settings-confidence-level=medium-and-above \
  --rai-settings-filters='[{"filterType":"DANGEROUS","confidenceLevel":"MEDIUM_AND_ABOVE"},{"filterType":"HARASSMENT","confidenceLevel":"MEDIUM_AND_ABOVE"},{"filterType":"HATE_SPEECH","confidenceLevel":"MEDIUM_AND_ABOVE"},{"filterType":"SEXUALLY_EXPLICIT","confidenceLevel":"MEDIUM_AND_ABOVE"}]'

# Optional Mode A (Project-Wide Floor Settings for Google-Managed Remote MCP):
# WARNING: Applies project-wide across all integrated services and routes cross-jurisdictionally
# outside Thailand — obtain PDPA sign-off and configure a regional Cloud Logging sink first!
#   gcloud model-armor floorsettings update \
#     --full-uri="projects/${PROJECT_ID}/locations/global/floorSetting" \
#     --enable-floor-setting-enforcement=TRUE \
#     --add-integrated-services=GOOGLE_MCP_SERVER \
#     --google-mcp-server-enforcement-type=INSPECT_AND_BLOCK \
#     --enable-google-mcp-server-cloud-logging

# -----------------------------------------------------------------------------
# Step 6: Cloud Data Access Audit Logs (spanner.googleapis.com + MCP)
# -----------------------------------------------------------------------------
echo ""
echo "[Step 6] Cloud Spanner & MCP Data Access Audit Logs configuration..."

AUDIT_SNIPPET="$(cat <<'EOF'
{
  "auditConfigs": [
    {
      "service": "spanner.googleapis.com",
      "auditLogConfigs": [
        {"logType": "ADMIN_READ"},
        {"logType": "DATA_READ"},
        {"logType": "DATA_WRITE"}
      ]
    },
    {
      "service": "spanner.googleapis.com/mcp",
      "auditLogConfigs": [
        {"logType": "DATA_READ"}
      ]
    }
  ]
}
EOF
)"

echo "Recommended project IAM auditConfigs (DATA_READ/DATA_WRITE are disabled by default):"
echo "${AUDIT_SNIPPET}"

if [[ "${ENABLE_AUDIT_LOGS}" == "true" ]]; then
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY-RUN] Would merge auditConfigs into project ${PROJECT_ID} IAM policy via gcloud projects set-iam-policy."
  else
    TMP_POLICY="$(mktemp /tmp/spanner_iam_policy.XXXXXX.json)"
    TMP_PATCHED="$(mktemp /tmp/spanner_iam_policy_patched.XXXXXX.json)"
    trap 'rm -f "${TMP_POLICY}" "${TMP_PATCHED}"' EXIT
    gcloud projects get-iam-policy "${PROJECT_ID}" --format=json > "${TMP_POLICY}"
    python3 - "${TMP_POLICY}" "${TMP_PATCHED}" <<'PYEOF'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
with open(src, "r", encoding="utf-8") as fh:
    policy = json.load(fh)
audit_configs = [
    c for c in policy.get("auditConfigs", [])
    if c.get("service") not in {"spanner.googleapis.com", "spanner.googleapis.com/mcp"}
]
audit_configs.extend([
    {
        "service": "spanner.googleapis.com",
        "auditLogConfigs": [
            {"logType": "ADMIN_READ"},
            {"logType": "DATA_READ"},
            {"logType": "DATA_WRITE"},
        ],
    },
    {
        "service": "spanner.googleapis.com/mcp",
        "auditLogConfigs": [
            {"logType": "DATA_READ"},
        ],
    },
])
policy["auditConfigs"] = audit_configs
with open(dst, "w", encoding="utf-8") as fh:
    json.dump(policy, fh, indent=2)
PYEOF
    gcloud projects set-iam-policy "${PROJECT_ID}" "${TMP_PATCHED}"
    echo "[OK] Enabled Data Access Audit Logs for spanner.googleapis.com and spanner.googleapis.com/mcp."
  fi
else
  echo "[INFO] Pass --enable-audit-logs to apply auditConfigs to project ${PROJECT_ID} IAM policy."
fi

echo ""
echo "[Step 6 Alert Query] Cloud Logging filter to detect unexpected agent Spanner writes:"
echo '  protoPayload.serviceName="spanner.googleapis.com"'
echo '  AND (protoPayload.methodName="google.spanner.v1.Spanner.Commit"'
echo '       OR protoPayload.methodName="google.spanner.v1.Spanner.ExecuteBatchDml")'
echo '  AND protoPayload.authenticationInfo.principalEmail=~".*@.*\\.iam\\.gserviceaccount\\.com"'
echo ""
echo "[DONE] Module 6 IAM, Cloud Run MCP Toolbox, FGAC, Model Armor, DLP & Audit setup complete."
