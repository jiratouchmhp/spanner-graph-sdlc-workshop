---
name: spanner-mcp-toolbox-and-agent-security
metadata:
  version: "1.0.0"
  category: "Cloud Spanner Enterprise"
description: >-
  Guides MCP Toolbox for Databases, Antigravity/ADK connectivity, Spanner FGAC
  roles, and agent guardrails. Use when configuring Antigravity MCP
  Store/mcp_config.json, Gemini CLI, ADK ToolboxToolset/SpannerToolset, Spanner
  Remote MCP (create_session.fineGrainedAccessRole), or DB Insights MCP;
  authoring tools.yaml (type: spanner without databaseRole, readOnly: true,
  Vertex AI embeddingModels, OIDC authServices, secure params, persona Cloud Run
  isolation); enforcing FGAC GRANT/REVOKE, conditional databaseRoleUser IAM, IAM
  Deny CEL, agent secure_context, Model Armor, CMEK, VPC-SC, or Audit Logs.
  Don't use for CREATE VIEW/table DDL (spanner-schema-and-data-modeling), Graph
  GQL (spanner-graph-modeling-and-gql), FTS/Vector queries
  (spanner-search-ai-and-graphrag), platform ops (spanner-enterprise-platform-ops),
  migrations (spanner-migration-and-neo4j-playbook), or sizing
  (spanner-performance-observability-and-sizing).
---

# Cloud Spanner MCP Toolbox & Enterprise Agent Security

Opinionated production guide for connecting AI agents and developer IDEs (Antigravity, Gemini CLI, ADK, LangChain) to **Cloud Spanner** via **MCP Toolbox for Databases**, **Spanner Managed Remote MCP**, and **Database Insights MCP**—backed by defense-in-depth IAM, Fine-Grained Access Control (FGAC), OIDC authenticated parameters, Parameterized Views, Model Armor, CMEK, VPC-SC, and Audit Logging.

---

## Live-Environment Safety Confirmation Gate

Before executing any command that mutates live cloud infrastructure or database security state, **STOP and request explicit user confirmation** showing the target `{project_id}`, `{instance_id}`, `{database_id}`, and exact blast radius:

- **Database DDL / RBAC mutations:** `CREATE ROLE`, `DROP ROLE`, `GRANT`, `REVOKE`, `CREATE OR REPLACE VIEW` (replaces and **wipes** prior `GRANT SELECT ON VIEW` grants).
- **IAM & Org Policy mutations:** `add-iam-policy-binding`, `remove-iam-policy-binding`, IAM Deny Policies (`gcloud iam policies create`), or edition org policies.
- **Runtime deployments & screening:** `gcloud run deploy`, Secret Manager mutations, or `gcloud model-armor floorsettings update` (applies **project-wide** to all integrated services and triggers cross-jurisdictional routing outside regions without Model Armor).
- **Secret hygiene:** Never commit static API keys, OAuth client secrets, bearer tokens, or service account JSON keys into `tools.yaml`, `mcp_config.json`, or source repositories. Prefer Workload Identity / ADC and Secret Manager.

---

## Core Architecture Invariants (Non-Negotiable)

1. **Separate the Two Spanner Authorization Layers:**
   - **Layer 1 — Self-Hosted MCP Toolbox (`type: spanner`):** The Toolbox Spanner source does **not** support `databaseRole` or source-level `readOnly`. Enforce least privilege with a dedicated Cloud Run Service Account holding database-level `roles/spanner.databaseReader` (or `roles/spanner.databaseUser` for write-scoped personas), per-persona sliced `tools.yaml` deployments, explicit `readOnly: true` on every read tool, and OIDC `authServices` / `secure: true` parameters. Granting *only* FGAC roles to a Toolbox SA fails with `PERMISSION_DENIED`.
   - **Layer 2 — Dedicated FGAC Service Accounts (`database_role`-aware clients only):** For Spanner Remote MCP (`create_session.fineGrainedAccessRole`), `gcloud --database-role`, Spanner SDKs, and ADK `SpannerToolset`, grant **only** `roles/spanner.fineGrainedAccessUser` + conditional `roles/spanner.databaseRoleUser` and **revoke all database-level IAM roles** so callers cannot bypass FGAC by omitting `database_role`.
2. **Never Ship Prebuilt Configs or Raw SQL to Production End-User Agents:** `--prebuilt spanner`, `--prebuilt spanner-postgres`, and `spanner-execute-sql` are strictly for **build-time human-in-the-loop (HITL)** developer IDEs. Production autonomous agents must use the **Action-Selector pattern** with curated parameterized `spanner-sql` tools.
3. **Explicit `readOnly: true` on Every Read Tool:** `spanner-sql` defaults to `readOnly: false` (opens a `ReadWriteTransaction` and emits `destructiveHint: true`), which wastes lock overhead and fails against read-only SAs.
4. **Secretless Regional Vertex AI Embeddings:** In `tools.yaml` `embeddingModel` (`type: gemini`), configure `project` + regional `location` (e.g., companion `asia-southeast1` when Vertex AI is unavailable in primary `asia-southeast3`; **never** `global` for `gemini-embedding-001`) using Workload Identity (`roles/aiplatform.user`)—reserve static `apiKey` strictly for local non-production prototypes.
5. **Out-of-Band Identity & Tenant Binding:** Bind tenant/user filters via OIDC `authServices` (JWT claim decoded server-side, stripped from LLM schema) or `secure: true` parameters (`MCP 2026-07-28`), or Spanner Parameterized Views (`SECURE_CONTEXT()`). Never let the LLM supply its own `customer_id`/`tenant_id`, and never pass credentials via URL query binding.
6. **Mandatory IAM Condition on `roles/spanner.databaseRoleUser`:** Omitting `--condition` restricting `resource.type == "spanner.googleapis.com/DatabaseRole"` and `resource.name.endsWith("/{role_name}")` grants access to **all** database roles in the database.
7. **Remote MCP `create_session` `readOnlyHint: false` Trap:** In `https://spanner.googleapis.com/mcp`, `create_session` has `readOnlyHint: false`. Any IAM Deny Policy blocking `tool.isReadOnly == false` **must** exempt `create_session` using `!(api.getAttribute('mcp.googleapis.com/tool.name', '') == 'create_session')`, or read-only agents cannot run `execute_sql_readonly`.
8. **Zero Grants on `public` & Re-Runnable View Grants:** Never grant base-table access to the `public` system role (leaks to all current and future roles). Always re-apply `GRANT SELECT ON VIEW` after every `CREATE OR REPLACE VIEW`. Parameterized views lack a PostgreSQL `security_barrier`—never expose raw ad-hoc SQL to untrusted users even over parameterized views.
9. **Hardened Cloud Run & CLI Networking (`v1.13.1+`):** Always pass canonical `--config` (legacy `--tools-file` is deprecated and warns). In `v1.13.1+`, legacy `/api/toolset` and `/api/tool/.../invoke` HTTP routes return **`HTTP 410 Gone`** — always use Streamable HTTP JSON-RPC 2.0 on **`/mcp`** and **`/mcp/{toolset}`**. Deploy MCP Toolbox with `--no-allow-unauthenticated`, `--port=8080`, `--address=0.0.0.0`, exact `--allowed-hosts` and `--allowed-origins` (mitigates DNS rebinding and silences wildcard `*` warnings even in `--stdio` mode), `--logging-format=JSON`, `--telemetry-gcp`, `--sql-commenter`, `--disable-reload`, and `--disable-version-check`. Do **not** put Cloud Run MCP Toolbox behind IAP (unsupported).
10. **Regional Data Residency & PDPA Alignment (Bangkok Primary + Singapore Companion):** Keep Spanner (`regional-asia-southeast3`, single regional instance), Cloud Run MCP Toolbox, user-managed Secret Manager, GCS/Dataflow, CMEK keys, and same-region PITR/backups strictly in **Bangkok (`asia-southeast3`)**. Route to **Singapore (`asia-southeast1`, ~25 ms RTT)** **only** for companion GCP services unavailable in Bangkok: Vertex AI (`gemini-embedding-001`, `gemini-2.5-flash`), Model Armor (`modelarmor.asia-southeast1.rep.googleapis.com`), and Cloud DLP / Sensitive Data Protection—and explicitly assess Thailand PDPA impact before enabling Model Armor floor settings on managed remote MCP servers (cross-jurisdictional routing).
11. **Spanner MCP SQL & Automation Hygiene:** Never project nullable `ARRAY<STRUCT<...>>` scalar subqueries (`(SELECT ARRAY_AGG(...) FROM ...)`) in `spanner-sql` tools — use `ARRAY(SELECT AS STRUCT ...)` (guarantees non-null `[]`) to avoid `Unimplemented: Unsupported query shape: null-valued array of struct`. Prefix multi-referenced FTS `SCORE()` CTEs with `@{optimizer_version=latest}`. In provisioning scripts, add a 3–8s IAM propagation retry loop after creating Service Accounts before calling `gcloud spanner databases add-iam-policy-binding`, and match `could not be found` for idempotent `gcloud run services delete` 404 checks.

---

## Integration Pattern Decision Matrix

| Integration Pattern | Server / Transport | Best Fit | Auth Model | Native FGAC `database_role` | Read-Only Control |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **1. MCP Toolbox — Custom `tools.yaml`** | Self-hosted Cloud Run / GKE / local (`--stdio` or `/mcp`) | **Production multi-tenant agents** (ADK, LangChain, Genkit) | Cloud Run IAM (`roles/run.invoker`) + OIDC `authService` (`<name>_token` or `Authorization: Bearer`) | **No** (use DB-level SA IAM + curated SQL + OIDC/`secure` params) | `readOnly: true` per tool + `roles/spanner.databaseReader` SA |
| **2. MCP Toolbox — Prebuilt (`--prebuilt spanner`)** | Local binary or `npx @toolbox-sdk/server --stdio` | **Build-time developer IDEs only** (trusted HITL) | Local ADC (`gcloud auth application-default login`) | No | Exposes both R/W & R/O SQL; enforce via developer IAM |
| **3. Spanner Remote MCP** (`https://spanner.googleapis.com/mcp`) | Google-managed Streamable HTTP | Managed admin, DDL, schema inspection, enterprise MCP clients | OAuth 2.0 + `roles/mcp.toolUser` + Spanner IAM (**no API keys**) | **Yes** (`create_session.fineGrainedAccessRole`) | `execute_sql_readonly` + IAM Allow/Deny CEL + Model Armor |
| **4. Database Insights MCP** (`https://databaseinsights.googleapis.com/mcp`) | Google-managed Streamable HTTP | SRE / DBA observability via Cloud Monitoring PromQL | OAuth 2.0 + `roles/mcp.toolUser` + `roles/monitoring.viewer` + `roles/databaseinsights.viewer` | N/A (metrics plane) | Strictly read-only (`get_system_metrics`) |
| **5. Direct ADK `SpannerToolset`** | In-process Python (`google.adk.tools.spanner`) | Python ADK apps needing direct SDK control without MCP hop | `SpannerCredentialsConfig` (ADC, SA, or OAuth2) | Via Spanner client / IAM | `SpannerToolSettings(capabilities=[Capabilities.DATA_READ])` |

---

## Quickstart Blueprints

### 1. Antigravity `mcp_config.json` (Local Custom `tools.yaml` + Remote Cloud Run)

In Antigravity **Agent Panel > Menu > MCP Servers > Manage MCP Servers > View raw config** (or install the built-in **Spanner** MCP Store server for instant HITL dev access):

```json
{
  "mcpServers": {
    "spanner-local-curated": {
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
        "SPANNER_DIALECT": "googlesql",
        "GOOGLE_CLOUD_PROJECT": "{project_id}",
        "VERTEX_AI_LOCATION": "asia-southeast1"
      }
    },
    "spanner-cloudrun-prod": {
      "type": "http",
      "url": "https://{toolbox_service_host}/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_BEARER_TOKEN}"
      }
    }
  }
}
```

### 2. Hardened Production `tools.yaml` Skeleton (Flat Multi-Document v1)

```yaml
kind: source
name: primary-spanner
type: spanner
project: ${SPANNER_PROJECT}
instance: ${SPANNER_INSTANCE}       # e.g., regional-asia-southeast3 (Enterprise)
database: ${SPANNER_DATABASE}
dialect: ${SPANNER_DIALECT:googlesql}
---
kind: embeddingModel
name: vertex-embed-768
type: gemini
model: gemini-embedding-001
project: ${GOOGLE_CLOUD_PROJECT}
location: ${VERTEX_AI_LOCATION:asia-southeast1}   # Regional endpoint; never "global"
dimension: 768
---
kind: authService
name: corp-google-oidc
type: google
clientId: ${GOOGLE_OAUTH_CLIENT_ID}
---
kind: tool
name: get_my_orders
type: spanner-sql
source: primary-spanner
readOnly: true
description: Lists orders belonging exclusively to the authenticated caller.
statement: |
  SELECT order_id, status, total_amount, created_at
  FROM {table_name}
  WHERE customer_email = @caller_email AND status = @status
  ORDER BY created_at DESC
  LIMIT @max_rows
authRequired:
  - corp-google-oidc
parameters:
  - name: caller_email
    type: string
    description: Verified OIDC email claim (server-injected; invisible to LLM)
    authServices:
      - name: corp-google-oidc
        field: email
  - name: status
    type: string
    description: Order status filter
    default: ACTIVE
    allowedValues: ["^ACTIVE$", "^PROCESSING$", "^SHIPPED$", "^DELIVERED$"]
  - name: max_rows
    type: integer
    description: Maximum rows to return
    default: 10
    minValue: 1
    maxValue: 50
---
kind: group
name: customer_readonly_persona
description: Strictly read-only order lookup tools for Tier-1 agents.
tools:
  - get_my_orders
```

### 3. Dedicated FGAC Role & Conditional IAM Binding (Layer 2 Clients)

```bash
# 1. Create role and grant column-scoped read privilege (exact case required in GRANT!)
gcloud spanner databases ddl update "{database_id}" --instance="{instance_id}" \
  --ddl='CREATE ROLE support_agent_ro;
         GRANT SELECT(order_id, status, created_at) ON TABLE Orders TO ROLE support_agent_ro;'

# 2. Enable FGAC once per Service Account (ensure DB-level IAM roles are REVOKED!)
gcloud spanner databases add-iam-policy-binding "{database_id}" --instance="{instance_id}" \
  --role=roles/spanner.fineGrainedAccessUser \
  --member="serviceAccount:{service_account}@{project_id}.iam.gserviceaccount.com" \
  --condition=None

# 3. Bind ONLY the target database role via MANDATORY IAM condition
gcloud spanner databases add-iam-policy-binding "{database_id}" --instance="{instance_id}" \
  --role=roles/spanner.databaseRoleUser \
  --member="serviceAccount:{service_account}@{project_id}.iam.gserviceaccount.com" \
  --condition='expression=(resource.type == "spanner.googleapis.com/DatabaseRole" && resource.name.endsWith("/support_agent_ro")),title=SupportAgentRole,description=Restrict SA to support_agent_ro'
```

---

## Edition & Regional Security Boundaries (Bangkok Primary + Singapore Companion)

- **All Editions (Standard / Enterprise / Enterprise Plus):** IAM, FGAC roles, Invoker/Definer/Parameterized views, CMEK/EKM, VPC-SC, Cloud Audit Logs, 7-day PITR, Standard Backups, Remote Spanner MCP, DB Insights MCP, MCP Toolbox.
- **Enterprise & Enterprise Plus Only:** Spanner Graph (`spanner-list-graphs` tool, GoogleSQL only), Vector Search (`embeddedBy` queries), Full-Text Search, Incremental Backups (`$0.0315/GiB-mo` same-region Bangkok), Managed Autoscaler.
- **Enterprise Plus Only:** Dual/Multi-region configs, up to 99.999% SLA, Geo-partitioning (requires a regional CMEK key per replica/partition region—no multi-region KMS keys).
- **Edition Governance:** Enforce `constraints/spanner.managed.restrictCloudSpannerEditions` org policy to prevent edition sprawl and monitor `instance/edition/feature_usage` (60s sampling, up to 120s delay).
- **Regional Architecture Note:** No built-in dual-region config pairs `asia-southeast3` + `asia-southeast1`. Run a **single regional** Spanner Enterprise instance (`regional-asia-southeast3`, 3-zone sync quorum, 99.99% SLA, `$1.2915/node-hr`), Cloud Run Toolbox, user-managed Secret Manager, KMS keys, and same-region PITR/backups in **Bangkok (`asia-southeast3`)**. Use **Singapore (`asia-southeast1`, ~25 ms RTT)** **only** for companion GCP services unavailable in Bangkok: Vertex AI, Model Armor (`modelarmor.asia-southeast1.rep.googleapis.com`), and Cloud DLP.

---

## Deep-Dive Reference Router

| When Your Task Involves... | Read This Reference |
| :--- | :--- |
| Antigravity MCP Store/`mcp_config.json`, Gemini CLI, VS Code/Cursor/Claude/ChatGPT setup, 15-tool Remote Spanner MCP inventory, DB Insights MCP, Cloud Run deployment, DNS rebinding, ADK `ToolboxToolset` / `SpannerToolset` | [references/mcp-toolbox-and-antigravity-setup.md](references/mcp-toolbox-and-antigravity-setup.md) |
| Flat v1 vs legacy `tools.yaml`, `type: spanner` fields, all 5 Spanner tool types, Vertex AI `embeddingModel` + `embeddedBy`, `allowedValues` / `default: null` gotchas, OIDC `authServices`, `secure: true` params (`MCP 2026-07-28`), MCP OAuth 2.1 PRM, per-persona YAML slicing | [references/tools-yaml-and-authenticated-params.md](references/tools-yaml-and-authenticated-params.md) |
| Autonomous agent threat patterns, Layer-1 vs Layer-2 IAM, `create_session` IAM Deny CEL, FGAC DDL privilege matrix (GoogleSQL & PG), system roles, `gcloud asset` audit, Parameterized Views (`SECURE_CONTEXT()`), Model Armor & Cloud DLP regional/PDPA matrix, CMEK, VPC-SC, Audit Logs, PITR/backups | [references/fgac-iam-and-agent-guardrails.md](references/fgac-iam-and-agent-guardrails.md) |

---

## Sibling Skill Routing

- **`spanner-schema-and-data-modeling`**: Table, PK, index, FK, `CREATE VIEW` / `SECURE_CONTEXT()` view DDL syntax, TTL, Locality Groups, Change Streams DDL.
- **`spanner-graph-modeling-and-gql`**: `CREATE PROPERTY GRAPH`, ISO GQL traversals, Data Boost graph algorithms, Studio visualizations.
- **`spanner-search-ai-and-graphrag`**: Vertex AI `CREATE MODEL`, FTS tokenizers/dictionaries, KNN/ScaNN vector search, RRF/RSF hybrid GraphRAG queries.
- **`spanner-enterprise-platform-ops`**: Editions/CUDs, instance configs, dialects, KV/Queues, CLI/Emulator, autoscaler deployment, PITR/backups ops.
- **`spanner-migration-and-neo4j-playbook`**: 7-stage migrations, Neo4j APOC export, openCypher-to-GQL translation, Elasticsearch-to-FTS migration.
- **`spanner-performance-observability-and-sizing`**: Execution plans, Query/Lock Insights (`SPANNER_SYS`), Key Visualizer, pre-splitting, CPU targets, TCO sizing.

---

## Public Documentation

- [Secure Agent Interactions with Spanner & MCP](https://docs.cloud.google.com/spanner/docs/secure-agent-interactions-mcp)
- [Prebuilt Tools with MCP Toolbox for Databases](https://docs.cloud.google.com/spanner/docs/pre-built-tools-with-mcp-toolbox)
- [Use the Spanner Remote MCP Server](https://docs.cloud.google.com/spanner/docs/use-spanner-mcp)
- [Control MCP Server Use with IAM](https://docs.cloud.google.com/mcp/control-mcp-use-iam)
- [Spanner Fine-Grained Access Control (FGAC)](https://docs.cloud.google.com/spanner/docs/fgac-about)
- [MCP Toolbox for Databases (GitHub)](https://github.com/googleapis/genai-toolbox)
- [Spanner Building Blocks & ADK/MCP Samples](https://github.com/GoogleCloudPlatform/spanner-bb-samples)
