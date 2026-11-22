# MCP Toolbox, Remote Spanner MCP & Antigravity / ADK Setup

Complete deployment and client-integration reference for connecting developer IDEs (**Antigravity**, **Gemini CLI**, **Cursor**, **VS Code**, **Claude**, **Codex**) and production agent frameworks (**ADK**, **LangChain**, **Genkit**) to **Cloud Spanner**.

---

## 1. Choosing the Right Spanner MCP Integration Pattern

| Pattern | Server & Transport | Target Workload | Auth Mechanism | FGAC `database_role` | Security Posture |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **1. MCP Toolbox — Custom `tools.yaml`** | Self-hosted binary/container (Cloud Run, GKE, local); `/mcp` Streamable HTTP/SSE or `--stdio` | **Production runtime agents** (multi-tenant bots, ADK/LangChain) | Cloud Run IAM (`roles/run.invoker`) + OIDC `authService` (`<name>_token` or `Authorization: Bearer`) | **Not supported** in Spanner source `ClientConfig` — enforce via DB-level SA IAM + curated SQL + OIDC/`secure` params | **Highest for end-user apps** (curated parameterized `spanner-sql`, `readOnly: true`, per-persona Cloud Run services) |
| **2. MCP Toolbox — Prebuilt (`--prebuilt spanner` / `spanner-postgres`)** | Local binary or `npx -y @toolbox-sdk/server --stdio` | **Build-time developer IDEs ONLY** (trusted HITL) | Local ADC (`gcloud auth application-default login`) | No | Exposes both `execute_sql` (R/W) and `execute_sql_readonly` (R/O); **never** expose to untrusted end users |
| **3. Spanner Remote MCP Server** (`https://spanner.googleapis.com/mcp`) | Google-managed Streamable HTTP (`tools/list`, `tools/call`) | Managed provisioning, DDL updates, schema inspection, SQL/GQL, enterprise MCP connectors | OAuth 2.0 + `roles/mcp.toolUser` + Spanner IAM (**API keys rejected**) | **Yes** (`create_session.fineGrainedAccessRole`) | Separate `execute_sql_readonly` vs `execute_sql`; IAM Allow/Deny CEL + Model Armor floor settings |
| **4. Database Insights Remote MCP** (`https://databaseinsights.googleapis.com/mcp`) | Google-managed Streamable HTTP | SRE / DBA observability & telemetry via Cloud Monitoring PromQL | OAuth 2.0 + `roles/mcp.toolUser` + `roles/monitoring.viewer` + `roles/databaseinsights.viewer` | N/A (metrics plane) | Strictly read-only (`get_system_metrics`) |
| **5. Direct ADK `SpannerToolset`** | In-process Python (`google.adk.tools.spanner`) | Python ADK services needing direct SDK control without MCP server hop | `SpannerCredentialsConfig` (ADC, SA, or OAuth2 `spanner.admin`/`spanner.data`) | Controlled via Spanner client / IAM | `SpannerToolSettings(capabilities=[Capabilities.DATA_READ])` caps toolset to reads |

> **Prebuilt Warning:** Prebuilt configs (`--prebuilt spanner`, `--prebuilt spanner-postgres`) and dynamic `spanner-execute-sql` tools are built strictly for **developer build-time workflows** with a trusted human approving every call. Never deploy them behind an untrusted customer chat interface.

---

## 2. Antigravity IDE Setup (Two Supported Paths)

You do **not** need to manually download the MCP Toolbox binary for Antigravity, Gemini CLI, or Gemini Code Assist.

### Method A — Built-in MCP Store (Fastest for Dev/HITL)
1. Open Antigravity and open the editor's **Agent Panel**.
2. Click the **Menu** icon and select **MCP Servers**.
3. Locate **Spanner** in the catalog and click **Install**.
4. Complete the Google Cloud Project authorization prompt. Spanner tools are immediately ready, with ADC tokens refreshed automatically.

### Method B — Custom `mcp_config.json` (Curated Local or Remote Cloud Run)
Open **Agent Panel > Menu > MCP Servers > Manage MCP Servers > View raw config** (`mcp_config.json`):

```json
{
  "mcpServers": {
    "spanner-prebuilt-dev": {
      "command": "npx",
      "args": ["-y", "@toolbox-sdk/server", "--prebuilt", "spanner", "--stdio"],
      "env": {
        "SPANNER_PROJECT": "{project_id}",
        "SPANNER_INSTANCE": "{instance_id}",
        "SPANNER_DATABASE": "{database_id}",
        "SPANNER_DIALECT": "googlesql"
      }
    },
    "spanner-curated-local": {
      "command": "npx",
      "args": [
        "-y",
        "@toolbox-sdk/server",
        "--config",
        "/abs/path/to/tools.yaml",
        "--stdio",
        "--allowed-hosts=localhost,127.0.0.1",
        "--allowed-origins=http://localhost:5000,http://127.0.0.1:5000",
        "--disable-version-check"
      ],
      "env": {
        "SPANNER_PROJECT": "{project_id}",
        "SPANNER_INSTANCE": "{instance_id}",
        "SPANNER_DATABASE": "{database_id}",
        "GOOGLE_CLOUD_PROJECT": "{project_id}",
        "VERTEX_AI_LOCATION": "asia-southeast1"
      }
    },
    "spanner-cloudrun-remote": {
      "type": "http",
      "url": "https://{toolbox_service_host}/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_BEARER_TOKEN}"
      }
    }
  }
}
```

- `SPANNER_DIALECT` accepts `googlesql` (default) or `postgresql`.
- For PostgreSQL prebuilt dev workflows, pass `--prebuilt spanner-postgres`.

---

## 3. Gemini CLI & Multi-Client Configuration Matrix

### Gemini CLI — Option 1: Dedicated Spanner Extension (Recommended; Requires `v0.6.0+`)
```bash
gemini --version   # Verify >= 0.6.0
gemini extensions install https://github.com/gemini-cli-extensions/spanner

export SPANNER_PROJECT="{project_id}"
export SPANNER_INSTANCE="{instance_id}"
export SPANNER_DATABASE="{database_id}"
export SPANNER_DIALECT="googlesql"   # googlesql | postgresql
gemini
# Inside Gemini CLI: run /extensions to verify
```

### Gemini CLI — Option 2: Managed Spanner Remote MCP (`gemini-extension.json`)
Place in `~/.gemini/extensions/{ext_name}/gemini-extension.json` (ADC tokens refresh automatically, unlike 1-hour `gcloud` bearer tokens):

```json
{
  "name": "spanner",
  "version": "1.0.0",
  "mcpServers": {
    "Spanner MCP Server": {
      "httpUrl": "https://spanner.googleapis.com/mcp",
      "authProviderType": "google_credentials",
      "oauth": {
        "scopes": ["https://www.googleapis.com/auth/cloud-platform"]
      },
      "timeout": 30000,
      "headers": {
        "x-goog-user-project": "{project_id}"
      }
    }
  }
}
```

Run `/mcp` inside `gemini` to verify `Spanner MCP Server` status and exposed tools.

### Installing the Standalone Binary (For Binary-Based IDEs; Min `v0.15.0`)
```bash
# OS/arch values: linux/amd64, darwin/arm64, darwin/amd64, windows/amd64
export VERSION="v1.13.1"
curl -L -o toolbox "https://storage.googleapis.com/mcp-toolbox-for-databases/${VERSION}/linux/amd64/toolbox"
chmod +x toolbox
./toolbox --version
```

### Client Config Paths, Root Keys & OAuth Redirect URIs

| Client | Config Path / Install Command | JSON Root Key / Notes |
| :--- | :--- | :--- |
| **Antigravity** | Built-in MCP Store or `mcp_config.json` | `"mcpServers"` |
| **Gemini Code Assist** | `.gemini/settings.json` | `"mcpServers"` |
| **Cursor** | `.cursor/mcp.json` | `"mcpServers"` |
| **Claude Desktop** | Settings > Developer > Edit Config | `"mcpServers"` |
| **Cline / Windsurf** | MCP Servers > Configure | `"mcpServers"` |
| **VS Code (Copilot)** | `.vscode/mcp.json` | **`"servers"`** (**NOT** `"mcpServers"`!) |
| **Claude Code** | Export `SPANNER_*`, run `claude`, then `/plugin install spanner@claude-plugins-official` | Verify with `/plugin list` and `/reload-plugins` |
| **Codex CLI** | `codex plugin marketplace add GoogleCloudPlatform/data-cloud-plugins` then `codex plugin install spanner@data-cloud-plugins` | Uses `SPANNER_*` env vars |
| **Claude.ai (Remote MCP)** | Google Auth Platform > OAuth 2.0 Web App Client | Redirect URI: `https://claude.ai/api/mcp/auth_callback` (DCR/OCIMD unsupported by GCP remote MCP) |
| **ChatGPT (Remote MCP)** | Google Auth Platform > OAuth 2.0 Web App Client (Developer mode) | JS Origin: `https://chatgpt.com`; Redirect URI: `https://chatgpt.com/connector_platform_oauth_redirect` |

---

## 4. Complete Tool Inventories

### A. Local MCP Toolbox Prebuilt Configs

| Flag | Exposed Tools | Groups / Toolsets | Dialect & Edition Notes |
| :--- | :--- | :--- | :--- |
| `--prebuilt spanner` | `execute_sql` (R/W), `execute_sql_readonly` (R/O), `list_tables`, `list_graphs`, `search_catalog` | Group `data` (4 tools without `search_catalog`); Toolset `data_with_discovery` (all 5) | `list_graphs` requires **GoogleSQL** + **Enterprise / Enterprise Plus** |
| `--prebuilt spanner-postgres` | `execute_sql` (R/W), `execute_sql_readonly` (R/O), `list_tables` (`public` schema CTE), `search_catalog` | Toolset `data` (3 tools); `data_with_discovery` (all 4) | **No `list_graphs` tool** (Spanner Graph is GoogleSQL-only) |

- Combine prebuilts with custom configs (`--prebuilt spanner --config custom.yaml`), load multiple (`--prebuilt spanner,bigquery`), or scope to one group (`--prebuilt=spanner/data`).

### B. Spanner Managed Remote MCP Server (`https://spanner.googleapis.com/mcp`)

Automatically enabled with `spanner.googleapis.com`; inherits standard Spanner quotas. Requires `roles/mcp.toolUser` (`mcp.tools.call`) + Spanner IAM + OAuth 2.0 scopes (`https://www.googleapis.com/auth/spanner.admin`, `https://www.googleapis.com/auth/spanner.data`, or `cloud-platform`). **Never accepts API keys.**

| Tool Name | Purpose | `readOnlyHint` | `destructiveHint` | `idempotentHint` | Critical Operational Notes |
| :--- | :--- | :---: | :---: | :---: | :--- |
| `list_configs` / `get_config` | Inspect instance configs | `true` | `false` | `true` | Paginated via `page_token` |
| `list_instances` / `get_instance` | Inspect instances | `true` | `false` | `true` | Paginated via `page_token` |
| `create_instance` / `update_instance` | Provision/scale instance (LRO) | `false` | `false` | `false` | `update_instance` must include name & config; poll `get_operation` |
| `list_databases` | List databases in instance | `true` | `false` | `true` | Paginated via `page_token` |
| `create_database` | Create DB with optional `extraStatements` (LRO) | `false` | `false` | `false` | Poll with `get_operation` |
| `get_database_ddl` | Read current schema DDL | `true` | `false` | `true` | Safe for read-only inspection |
| `update_database_schema` | Apply DDL changes (LRO) | `false` | **`true`** | `false` | **Only** tool for DDL (`execute_sql` rejects DDL) |
| `get_operation` | Poll LRO completion | `true` | `false` | `true` | LROs may take minutes |
| `create_session` | Create multiplexed session | **`false`** | `false` | `false` | **Accepts `"fineGrainedAccessRole"`!** Reuse across concurrent queries. **GOTCHA: `readOnlyHint` is `false`.** |
| `execute_sql_readonly` | Single-use R/O DQL | `true` | `false` | `true` | Autotagged **`mcp_execute_sql_readonly`**. Omit `transaction` field; rejects DML. |
| `execute_sql` | DQL or DML | `false` | **`true`** | `false` | Autotagged **`mcp_execute_sql`**. Monotonic `seqno`; returns `precommitToken`; pass `INT64` params as `stringValue` with explicit `type`. |
| `commit` | Commit R/W transaction | `false` | **`true`** | `false` | Autotagged **`mcp_commit`**. Pass latest `precommitToken` (highest `seqNum`); **must retry `commit`** if `CommitResponse` returns a newer `precommitToken`. |

- **Session & Transaction Discipline:** Reuse one multiplexed session per database; keep R/W transactions short (`execute_sql` -> `commit`); split multi-table seeds into one commit per table; use scripts or Dataflow for bulk loads.
- **Unauthenticated Tool Discovery (`tools/list` requires no auth):**
```bash
curl -sS -X POST https://spanner.googleapis.com/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/list' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

### C. Database Insights Managed Remote MCP (`https://databaseinsights.googleapis.com/mcp`)
- **APIs:** `spanner.googleapis.com`, `dbinsights.googleapis.com`.
- **IAM:** `roles/mcp.toolUser` + `roles/monitoring.viewer` (`monitoring.timeseries.list`) + `roles/databaseinsights.viewer` (`queryMetrics.fetch`, `systemMetrics.fetch`). OAuth scope: `cloud-platform`.
- **Tool:** `get_system_metrics` (`readOnlyHint: true`, `idempotentHint: true`, `destructiveHint: false`, `openWorldHint: false`).
  - Inputs: `parent` (`projects/{project_id}/locations/{location}`), `resource` (`{project_id}`), `promqlQuery` (e.g., `spanner.googleapis.com/instance/cpu/utilization`), optional RFC3339 `startTime` and `endTime`. Returns `{ "promqlResult": "..." }`.

---

## 5. Production Cloud Run Deployment & Hardening

Co-locate Cloud Run MCP Toolbox and user-managed Secret Manager secrets in the same primary region as your single-region Spanner instance (e.g., Bangkok `asia-southeast3`) for minimal round-trip latency and PDPA data locality, while calling companion GCP services unavailable in Bangkok—Vertex AI embeddings/LLMs, Model Armor, and Cloud DLP—in `asia-southeast1` (~25 ms RTT). Deploy **one Cloud Run service per agent persona** with its own sliced `tools.yaml` and dedicated Service Account:

```bash
export PROJECT_ID="{project_id}"
export REGION="asia-southeast3"          # Single primary DB/App region (Bangkok)
export VERTEX_REGION="asia-southeast1"   # Companion region for Vertex AI (unavailable in Bangkok; never global)
export IMAGE="us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:latest" # Pin digest in prod!

# 1. Create dedicated least-privilege runtime SA for this read-only persona
gcloud iam service-accounts create toolbox-ro-persona \
  --display-name="MCP Toolbox Read-Only Persona SA"

# 2. Grant Secret Manager + Vertex AI User + DATABASE-level Spanner Reader (Layer 1 IAM)
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:toolbox-ro-persona@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:toolbox-ro-persona@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
gcloud spanner databases add-iam-policy-binding "{database_id}" \
  --instance="{instance_id}" \
  --member="serviceAccount:toolbox-ro-persona@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/spanner.databaseReader"

# 3. Store sliced persona tools.yaml in regional user-managed Secret Manager (never bake into image)
gcloud secrets create tools-ro-persona \
  --replication-policy="user-managed" \
  --locations="$REGION" \
  --data-file=tools.ro-persona.yaml

# 4. Initial deploy to obtain deterministic Cloud Run URL (MUST bind 0.0.0.0:8080!)
gcloud run deploy toolbox-ro-persona \
  --image="$IMAGE" \
  --service-account="toolbox-ro-persona@${PROJECT_ID}.iam.gserviceaccount.com" \
  --region="$REGION" \
  --no-allow-unauthenticated \
  --set-env-vars="SPANNER_PROJECT=${PROJECT_ID},SPANNER_INSTANCE={instance_id},SPANNER_DATABASE={database_id},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},VERTEX_AI_LOCATION=${VERTEX_REGION}" \
  --set-secrets="/app/tools.yaml=tools-ro-persona:latest" \
  --args="--config=/app/tools.yaml","--address=0.0.0.0","--port=8080","--allowed-hosts=localhost","--allowed-origins=http://localhost","--disable-reload","--disable-version-check"

# 5. Lock down DNS rebinding, CORS, structured logging, GCP telemetry & SQLCommenter
export TOOLBOX_URL="$(gcloud run services describe toolbox-ro-persona --region="$REGION" --format='value(status.url)')"
export TOOLBOX_HOST="${TOOLBOX_URL#https://}"

gcloud run deploy toolbox-ro-persona \
  --image="$IMAGE" \
  --service-account="toolbox-ro-persona@${PROJECT_ID}.iam.gserviceaccount.com" \
  --region="$REGION" \
  --no-allow-unauthenticated \
  --set-env-vars="SPANNER_PROJECT=${PROJECT_ID},SPANNER_INSTANCE={instance_id},SPANNER_DATABASE={database_id},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},VERTEX_AI_LOCATION=${VERTEX_REGION},TOOLBOX_URL=${TOOLBOX_URL}" \
  --set-secrets="/app/tools.yaml=tools-ro-persona:latest" \
  --args="--config=/app/tools.yaml","--address=0.0.0.0","--port=8080","--allowed-origins=${TOOLBOX_URL}","--allowed-hosts=${TOOLBOX_HOST}","--logging-format=JSON","--telemetry-gcp","--sql-commenter","--disable-reload","--disable-version-check"

# 6. Restrict Cloud Run invocation to the specific caller agent SA
gcloud run services add-iam-policy-binding toolbox-ro-persona \
  --region="$REGION" \
  --member="serviceAccount:{agent_sa}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

### Cloud Run & CLI Hardening Gotchas
1. **Port Mismatch:** Toolbox defaults to `127.0.0.1:5000`; Cloud Run routes to `$PORT=8080` and fails startup unless `--address=0.0.0.0 --port=8080` are passed.
2. **IAP Unsupported:** MCP Toolbox does **not** support Cloud Run services behind Identity-Aware Proxy (IAP)—disable IAP on the service.
3. **Dual-Auth Header Conflict:** When Cloud Run enforces IAM (`roles/run.invoker`) **and** Toolbox enforces MCP OAuth (`mcpEnabled: true` on `Authorization: Bearer`), send the Cloud Run IAM identity token in **`X-Serverless-Authorization`** so both layers validate cleanly.
4. **DNS Rebinding ("Local Fallacy") & `--stdio` Wildcard Warnings (`v1.13.1`):** Never leave `--allowed-hosts="*"` or `--allowed-origins="*"` (the defaults!), even on `127.0.0.1` or `--stdio`—a malicious browser tab can DNS-rebind to local Toolbox, and `v1.13.1` logs wildcard `WARN`s for both flags even in `--stdio` mode unless explicitly restricted. Always set `--allowed-hosts="localhost,127.0.0.1"` and `--allowed-origins="http://localhost:5000,http://127.0.0.1:5000"` locally, or the exact FQDN/URL in Cloud Run.
5. **TLS, Config & Version-Check Flags (`v1.13.1`):** Direct self-hosted HTTP requires `--tls-cert` and `--tls-key` (enforces TLS 1.2+). Always use `--config` (legacy `--tools-file` is **deprecated** in `v1.13.1` and prints a warning); `--config`, `--configs` (comma-separated merge), and `--config-folder` are **mutually exclusive**. Use `--poll-interval=<sec>` on network mounts where inotify drops, `--disable-reload` in immutable production containers, and `--disable-version-check` to disable outbound GitHub release checks on startup. `--http-max-request-bytes` defaults to `10485760` (10 MiB).
6. **Observability:** `--sql-commenter` prepends `traceparent`, `server`, `tool.name`, `db.system.name`, and `_meta["dev.mcp-toolbox/telemetry"]` to SQL statements; `--telemetry-gcp` exports to Cloud Monitoring; `--telemetry-otlp` exports OTLP.
7. **Legacy `/api/*` Removal (`HTTP 410 Gone` in `v1.13.1+`):** MCP Toolbox `v1.13.1+` disables legacy `/api/toolset` and `/api/tool/{name}/invoke` routes by default (returning `HTTP 410 Gone`). Always call standard Streamable HTTP JSON-RPC 2.0 endpoints at **`POST /mcp`** (all tools) or **`POST /mcp/{toolset}`** (`initialize`, `tools/list`, `tools/call`).
8. **IAM Propagation & Teardown CLI Gotchas:** Newly created IAM Service Accounts require a **3–8s propagation retry loop** (backoff on `Service account ... does not exist`) before `gcloud spanner databases add-iam-policy-binding` succeeds consistently. For idempotent cleanup scripts, note that `gcloud run services delete` prints **`could not be found`** (not `NOT_FOUND`) on 404.

---

## 6. Connecting ADK Agents (`ToolboxToolset` vs `SpannerToolset`)

### Pattern A — ADK `ToolboxToolset` to Hardened Cloud Run (`pip install "google-adk[toolbox]"`)
```python
from google.adk.agents import Agent
from toolbox_adk import CredentialStrategy, ToolboxToolset
from toolbox_core import TelemetryAttributes

# Mints OIDC ID token via ADC/Workload Identity for Cloud Run roles/run.invoker
creds = CredentialStrategy.workload_identity(
    target_audience="https://{toolbox_service_host}"
)

toolset = ToolboxToolset(
    server_url="https://{toolbox_service_host}",
    toolset_name="customer_readonly_persona",  # Load ONLY this persona's group/toolset
    credentials=creds,
    telemetry_enabled=True,
    telemetry_attributes=lambda ctx: TelemetryAttributes(
        llm_model="gemini-2.5-flash",
        user_id=ctx.state.get("user_id"),
        agent_id="support-ro-agent",
    ),
)

# Set GOOGLE_GENAI_USE_VERTEXAI=True and GOOGLE_CLOUD_LOCATION=asia-southeast1
root_agent = Agent(
    name="support_ro_agent",
    model="gemini-2.5-flash",
    instruction="Answer customer order queries strictly via the provided tools.",
    tools=[toolset],
)
```

- **`CredentialStrategy` Factories:** `workload_identity(target_audience=...)`, `user_identity(client_id=..., client_secret=..., scopes=["openid", ...])` (**`"openid"` scope is mandatory** for ID token), `api_key(key=..., header_name="X-API-Key")`, `manual_token(...)`, `manual_credentials(...)`, `toolbox_identity()` (local dev only), `from_adk_auth_config(...)`, `from_adk_credentials(...)`.
- **Protocol Pinning:** Defaults to `Protocol.MCP` (`2026-07-28`); supports `MCP_v20251125`, `MCP_v20250618`, `MCP_v20250326`, `MCP_v20241105`.

### Pattern B — Direct In-Process ADK `SpannerToolset`
```python
import google.auth
from google.adk.tools.spanner.settings import Capabilities, SpannerToolSettings
from google.adk.tools.spanner.spanner_credentials import SpannerCredentialsConfig
from google.adk.tools.spanner.spanner_toolset import SpannerToolset

adc, _ = google.auth.default()
spanner_toolset = SpannerToolset(
    credentials_config=SpannerCredentialsConfig(credentials=adc),
    spanner_tool_settings=SpannerToolSettings(capabilities=[Capabilities.DATA_READ]),
)
```

---

## 7. Setup Anti-Patterns & Public References

- **NEVER** pass deprecated `--tools-file` on `v1.13.1+` (use `--config`), and **NEVER** call legacy `/api/toolset` or `/api/tool/{name}/invoke` routes (`HTTP 410 Gone` in `v1.13.1+` — use `/mcp` or `/mcp/{toolset}` JSON-RPC 2.0).
- **NEVER** deploy `--allow-unauthenticated` on Cloud Run MCP Toolbox.
- **NEVER** leave `--allowed-hosts="*"` or `--allowed-origins="*"` in shared or local browser-adjacent deployments.
- **NEVER** use API keys against `https://spanner.googleapis.com/mcp` or `https://databaseinsights.googleapis.com/mcp` (OAuth 2.0 + IAM only).
- **NEVER** put Cloud Run MCP Toolbox behind IAP.
- **NEVER** use `"mcpServers"` in `.vscode/mcp.json` (VS Code Copilot requires `"servers"`).

**Public Documentation:**
- [MCP Toolbox for Databases — Deploy to Cloud Run](https://github.com/googleapis/genai-toolbox)
- [Use Spanner Remote MCP Server](https://docs.cloud.google.com/spanner/docs/use-spanner-mcp)
- [Use Database Insights MCP Server](https://docs.cloud.google.com/spanner/docs/use-database-insights-mcp)
- [Gemini CLI Spanner Extension](https://github.com/gemini-cli-extensions/spanner)
- [Spanner ADK & MCP Samples](https://github.com/GoogleCloudPlatform/spanner-bb-samples)
