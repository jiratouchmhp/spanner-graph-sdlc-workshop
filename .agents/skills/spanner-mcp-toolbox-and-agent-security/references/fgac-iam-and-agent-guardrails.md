# Spanner FGAC, Per-Agent IAM, MCP Guardrails, Parameterized Views & Model Armor

Defense-in-depth security architecture for autonomous Cloud Spanner agents: agent threat mitigations, two-layer IAM separation, IAM Allow/Deny CEL for managed remote MCP servers, Spanner FGAC DDL & system roles, Parameterized Views (`SECURE_CONTEXT()`), Model Armor regional/PDPA architecture, CMEK, VPC-SC, Cloud Audit Logs, and PITR/DR recovery.

---

## 1. Autonomous Agent Threat Model & Mandatory Guardrail Patterns

- **Human-in-the-Middle (HitM):** Agent proposes actions; human approves each call. Primary risk: approval fatigue leading to rubber-stamped destructive DML/DDL.
- **Agent-Only (AO):** Autonomous execution without human approval. Primary threats: **direct prompt injection**, **indirect prompt injection** (malicious instructions embedded in retrieved database rows or tool error strings), **unauthorized tool chaining**, and **naive error handling** leaking schema/data.
- **Five Mandatory AO Architectural Controls:**
  1. **Action-Selector Pattern:** Eliminate open-ended plan-and-execute raw SQL (`execute_sql`). Restrict the LLM to selecting among hard-coded, parameterized `spanner-sql` tools whose query structure cannot be modified.
  2. **Dual-LLM Pattern:** Separate the primary *action LLM* from a secondary *guardrail LLM* that pre-screens prompts for malicious intent and post-screens responses for policy violations or PII leakage.
  3. **Strict Data/Instruction Separation:** Wrap untrusted user input and retrieved Spanner rows in explicit XML delimiters (e.g., `<db_record>...</db_record>`) with system instructions forbidding execution of directives inside data tags.
  4. **Static Tool Allowlisting (No Dynamic Registration):** Never permit agents to register new tools or modify permissions at runtime; enforce static allowlists (`coreTools` in Gemini CLI, per-persona `kind: group` in MCP Toolbox, IAM Allow/Deny CEL on remote MCP).
  5. **Strict Session & Memory Isolation:** Never share conversation memory, scratchpad state, or session caches across tenants or end users.

---

## 2. Per-Agent Identity & Two-Layer Spanner IAM Architecture

- **One Dedicated Identity per Agent Role:** Provision a unique Service Account—or **Vertex AI Agent Engine Agent Identity** (SPIFFE X.509-bound cryptographic identity; cannot be shared, cannot be impersonated, prohibits long-lived JSON keys)—per agent persona. Use **Workload Identity Federation** for on-prem or multi-cloud agents; never export SA JSON keys.
- **Separation of Duties:** Split agents requiring read access to sensitive customer data and write access to operational tables into separate identities / sub-agents.
- **CRITICAL — Explicitly Separate the Two Spanner IAM Layers:**
  1. **Layer 1 — Self-Hosted MCP Toolbox Service Accounts (no `database_role`):** Because MCP Toolbox (`type: spanner`) opens sessions without `database_role`, grant **database-level IAM** (`roles/spanner.databaseReader` for read-only personas; `roles/spanner.databaseUser` only for write-scoped personas) + per-persona sliced `tools.yaml` + OIDC/`secure` params. Never assign FGAC-only IAM to a Toolbox SA.
  2. **Layer 2 — Dedicated FGAC Service Accounts (`database_role`-aware clients ONLY):** Used by **Spanner Managed Remote MCP** (`create_session.fineGrainedAccessRole`), `gcloud spanner databases execute-sql --database-role=...`, Spanner SDKs, and ADK `SpannerToolset`. Grant **ONLY** `roles/spanner.fineGrainedAccessUser` + conditional `roles/spanner.databaseRoleUser`. **Revoke all database-level IAM roles** (`databaseReader`, `databaseUser`, `databaseAdmin`) so callers cannot bypass FGAC by omitting `database_role`.

### Predefined Spanner IAM Roles & Permission Gotchas
- `roles/spanner.admin` / `roles/spanner.databaseAdmin`: Full resource/DDL/IAM control—**never** grant to runtime agents.
- `roles/spanner.databaseUser`: Full read AND write across the entire database. **Do not confuse with `roles/spanner.databaseRoleUser`!**
- `roles/spanner.databaseReader`: Read-only across entire database. **Write-Session Pool Gotcha:** Older Spanner client libraries pre-allocate read-write sessions (`write-sessions-fraction`), causing intermittent `PERMISSION_DENIED` on `databaseReader` SAs unless set to `0` or using multiplexed sessions.
- `roles/spanner.viewer`: View instance/database metadata in Console (no data access; useful at project level for human FGAC Console users).
- `roles/spanner.fineGrainedAccessUser`: Grants `spanner.databases.useRoleBasedAccess` and default `public` role (zero privileges initially).
- `roles/spanner.databaseRoleUser`: Grants `spanner.databaseRoles.use`—**MUST ALWAYS include an IAM Condition** scoping `resource.name`, or the principal can assume **every** database role!
- **Custom Read-Only Role Minimum Permissions:** `spanner.databases.select`, `spanner.databases.beginReadOnlyTransaction`, `spanner.sessions.create` (always required), `spanner.sessions.delete` (also required for `CALL cancel_query`), plus `spanner.databases.useDataBoost` and `spanner.databases.partitionQuery` for Data Boost reads.
- `DatabaseRole` resources support `testIamPermissions` **only**—attach IAM policies with conditions on the parent **Database**.

---

## 3. IAM Allow & Deny CEL Guardrails for Managed Remote MCP Servers

Every call to `https://spanner.googleapis.com/mcp` or `https://databaseinsights.googleapis.com/mcp` checks **both** `mcp.tools.call` (`roles/mcp.toolUser` at project level) **and** underlying resource IAM.

- **Supported CEL Attributes:**
  - **Deny Policies (`mcp.googleapis.com/tools.call`):** `api.getAttribute('mcp.googleapis.com/tool.isReadOnly', false)`, `api.getAttribute('mcp.googleapis.com/tool.name', '')`, `resource.service`, `request.auth.oauth.client_id`.
  - **Allow Policies (`roles/mcp.toolUser`):** `resource.service`, `api.getAttribute('mcp.googleapis.com/tool.name', '')` (`request.auth.oauth.client_id` is **not** supported in Allow policies).

### The `create_session` `readOnlyHint: false` Trap (Exact Deny Policy Fix)
In Spanner Remote MCP, `create_session` has `readOnlyHint: false`. A naive Deny rule blocking `tool.isReadOnly == false` breaks read-only agents because they cannot open a session for `execute_sql_readonly`. Exempt `create_session` explicitly (and use `!(expr == val)` rather than `!=` in IAM Deny CEL):

```json
{
  "rules": [
    {
      "denyRule": {
        "deniedPrincipals": [
          "principal://iam.googleapis.com/projects/-/serviceAccounts/{ro_agent_sa}@{project_id}.iam.gserviceaccount.com"
        ],
        "deniedPermissions": ["mcp.googleapis.com/tools.call"],
        "denialCondition": {
          "title": "Deny Spanner write MCP tools while allowing create_session for R/O queries",
          "expression": "resource.service == 'spanner.googleapis.com' && api.getAttribute('mcp.googleapis.com/tool.isReadOnly', false) == false && !(api.getAttribute('mcp.googleapis.com/tool.name', '') == 'create_session')"
        }
      }
    }
  ]
}
```
```bash
gcloud iam policies create spanner-mcp-readonly-deny \
  --attachment-point="cloudresourcemanager.googleapis.com/projects/{project_id}" \
  --kind=denypolicies \
  --policy-file=policy.json
```

### Complementary Strict Tool Allowlist on `roles/mcp.toolUser`
```json
{
  "bindings": [
    {
      "role": "roles/mcp.toolUser",
      "members": ["serviceAccount:{ro_agent_sa}@{project_id}.iam.gserviceaccount.com"],
      "condition": {
        "title": "Allow strictly read-only Spanner and DB Insights MCP tools",
        "expression": "(resource.service == 'spanner.googleapis.com' && (api.getAttribute('mcp.googleapis.com/tool.name', '') == 'create_session' || api.getAttribute('mcp.googleapis.com/tool.name', '') == 'execute_sql_readonly' || api.getAttribute('mcp.googleapis.com/tool.name', '') == 'get_database_ddl' || api.getAttribute('mcp.googleapis.com/tool.name', '') == 'list_databases')) || (resource.service == 'databaseinsights.googleapis.com' && api.getAttribute('mcp.googleapis.com/tool.name', '') == 'get_system_metrics')"
      }
    }
  ]
}
```

- **OAuth Client ID Lock-Down (Deny Policy):** `"expression": "!(request.auth.oauth.client_id == '{approved_oauth_client_id}')"` blocks all unapproved OAuth clients.
- **Mandatory MCP IAM Limits:** (1) MCP CEL attributes apply **only** to `mcp.tools.call` (cannot mix with other permissions in one deny rule); (2) cannot combine IAM Tags with `resource.service` or MCP attributes; (3) `resource.service` and `tool.name` are **unavailable in Cloud Console**—manage via `gcloud`; (4) `tools/list` still displays denied tools (enforcement fires on `tools/call`); (5) applies **only** to Google Cloud managed remote MCP servers (not self-hosted MCP Toolbox).

---

## 4. Spanner FGAC: SQL RBAC, System Roles, Provisioning & Auditing

- **Quota:** Up to **`100` database roles per database**. Spanner never stores DB passwords—identity is always IAM.
- **Co-Existence Rule:** When a caller specifies `database_role` (SDK, `gcloud --database-role`, or Remote MCP `create_session.fineGrainedAccessRole`), Spanner checks **only FGAC privileges** and never falls back to database-level IAM. Conversely, omitting `database_role` falls back to database-level IAM—and Cloud Console hides the role selector if a human holds any DB-level IAM role. **Always revoke DB-level IAM after FGAC cutover!**
- **Exact-Case DDL Gotcha:** Schema object names inside `GRANT`/`REVOKE` must match exact creation case (e.g., `Orders`), even though GoogleSQL queries resolve identifiers case-insensitively.

### Complete FGAC Privilege Matrix (GoogleSQL & PostgreSQL)

| Object | Privileges | GoogleSQL DDL | PostgreSQL DDL | Key Nuances & Security Gotchas |
| :--- | :--- | :--- | :--- | :--- |
| **Role** | Membership | `CREATE ROLE r; GRANT ROLE base TO ROLE r;` | `CREATE ROLE r; GRANT base TO r;` | No hierarchy depth limit, but deep/wide graphs add query latency. |
| **Named Schema** | `USAGE` | `GRANT USAGE ON SCHEMA analytics TO ROLE r;` | `GRANT USAGE ON SCHEMA analytics TO r;` | Non-default schemas need `USAGE` **and** object grant; bulk `ALL` grants do not auto-apply to future objects. |
| **Table** | `SELECT`, `INSERT`, `UPDATE`, `DELETE` | `GRANT SELECT, INSERT ON TABLE Orders TO ROLE r;` | `GRANT SELECT, INSERT ON TABLE orders TO r;` | Interleaved parent grants do **not** cascade—**except GoogleSQL `DELETE` with `ON DELETE CASCADE`**, which deletes children! `UPDATE`/`DELETE` also need `SELECT` on `WHERE` columns. |
| **Column** | `SELECT`, `INSERT`, `UPDATE` | `GRANT SELECT(id, status) ON TABLE Orders TO ROLE r;` | `GRANT SELECT(id, status) ON TABLE orders TO r;` | **GOTCHA:** Column-restricted roles fail on **`SELECT *`** (must list columns; `COUNT(*)` works with >=1 column). Cannot grant `INSERT`/`UPDATE` on generated columns; `SELECT` on generated column does not expose base columns; column-only tables are omitted from `INFORMATION_SCHEMA.TABLE_PRIVILEGES`. |
| **View** | `SELECT` only | `GRANT SELECT ON VIEW SafeOrders TO ROLE r;` | `GRANT SELECT ON TABLE safe_orders TO r;` | PG uses **`ON TABLE`** for views. Definer's rights views require `SELECT` only on the view. |
| **Change Stream** | `SELECT` + `EXECUTE` | `GRANT SELECT ON CHANGE STREAM cs TO ROLE r; GRANT EXECUTE ON TABLE FUNCTION READ_cs TO ROLE r;` | `GRANT SELECT ON CHANGE STREAM cs TO r; GRANT EXECUTE ON FUNCTION spanner.read_json_cs TO r;` | **Both required.** Stream readers see tracked data even without table `SELECT`—revoke both! For FGAC Dataflow jobs, metadata DB **must be a separate DB** (Dataflow SA needs DB-level R/W on metadata DB). |
| **ML Model** | `EXECUTE` | `GRANT EXECUTE ON MODEL embed_model TO ROLE r;` | *(GoogleSQL ML)* | Required for `ML.PREDICT` / `SAFE.ML.PREDICT`. |
| **Sequence** | `SELECT`, `UPDATE` | `GRANT SELECT, UPDATE ON SEQUENCE seq TO ROLE r;` | `GRANT SELECT, UPDATE ON SEQUENCE seq TO r;` | Needed alongside column `INSERT`/`UPDATE`. |
| **Queue** | `INSERT`/`SELECT`/`DELETE`; `EXECUTE` on TVFs | `GRANT EXECUTE ON TABLE FUNCTION RECEIVE_Q TO ROLE r;` | `GRANT EXECUTE ON FUNCTION spanner.receive_q TO r;` | Consumer needs only `EXECUTE` on TVF. GoogleSQL `RECEIVE_`/`RENEWLEASE_` names are **case-sensitive**; PG `spanner.` is a name prefix (not a schema; `ALL FUNCTIONS IN SCHEMA spanner` fails). |

### Four Predefined System Roles
1. **`public`**: Inherited automatically by every FGAC user and role; starts with zero privileges. **NEVER grant base table privileges to `public`** (leaks to all current and future roles). Cannot be dropped or granted to a role.
2. **`spanner_info_reader`**: Unfiltered `INFORMATION_SCHEMA` visibility. Omit it for agents so `INFORMATION_SCHEMA` is **row-filtered** to hide restricted PII tables from schema introspection.
3. **`spanner_sys_reader`**: Grants `SELECT` on `SPANNER_SYS` introspection tables (required for Key Visualizer / Query Insights for FGAC-only operators).
4. **`spanner_secure_context_reader`**: Allows calling `SECURE_CONTEXT()` directly in ad-hoc SQL for debugging. **NEVER grant to agents or end users!**

### Auditing Unconditioned Bindings, Safe Role Teardown & Backup Caveats
```bash
# Test FGAC role access explicitly via --database-role
gcloud spanner databases execute-sql "{database_id}" --instance="{instance_id}" \
  --database-role=support_agent_ro \
  --sql="SELECT order_id, status FROM Orders LIMIT 5;"

# Audit for MISSING IAM conditions on roles/spanner.databaseRoleUser (any blank EXPRESSION = ALL roles!)
gcloud asset search-all-iam-policies \
  --scope="projects/{project_id}" \
  --query="roles:roles/spanner.databaseRoleUser AND resource=//spanner.googleapis.com/projects/{project_id}/instances/{instance_id}/databases/{database_id}" \
  --flatten="policy.bindings[].members[]"

# List all database roles unfiltered (or inspect via SQL: GoogleSQL INFORMATION_SCHEMA.ROLES / PG information_schema.enabled_roles)
gcloud spanner databases roles list --database="{database_id}" --instance="{instance_id}"
```
- **Safe 3-Step Teardown (Prevents Role-Name Reuse Escalation):** (1) `REVOKE` all privileges; (2) `gcloud spanner databases remove-iam-policy-binding` matching condition expression, title, and description **verbatim**; (3) `DROP ROLE {role_name};`.
- **Backup / Restore / Export Limits:** Spanner backups preserve SQL roles and `GRANT`s but **do NOT preserve IAM policies**—always re-apply `fineGrainedAccessUser` + conditional `databaseRoleUser` after restore! Dataflow/Avro import/export omits roles and grants entirely. Cloud Console's **Data** tab on TABLE pages is unavailable for FGAC-only users.

---

## 5. Definer's Rights & Parameterized Views (`SECURE_CONTEXT()`)

- **Quotas & Basics:** Up to **`5,000` views per database**; max nesting depth `10`; read-only (no DML, no Read API, no indexes/hints on view reference). Explicitly `CAST()` output columns and qualify table names for schema stability.
- **`CREATE OR REPLACE VIEW` Wipe Gotcha:** Replacing a view drops and recreates it—**all `GRANT SELECT ON VIEW` grants are wiped**! Maintain a standalone idempotent re-grant script and run it after every view replacement.
- **Invoker's vs Definer's Rights:**
  - `SQL SECURITY INVOKER`: Caller must hold `SELECT` on both the view **and** all underlying base tables/columns.
  - `SQL SECURITY DEFINER`: Caller needs `SELECT` **only on the view** (pair with FGAC; otherwise database-level IAM already exposes base tables).
- **Parameterized Views (`SQL SECURITY DEFINER` + `SECURE_CONTEXT()`):** Ideal for constraining NL2SQL or agent queries to a single tenant/user without creating per-user IAM identities. Application code injects an out-of-band `secure_context` map on the Spanner RPC (`(*request.mutable_secure_context())["app_end_userid"] = "303"` or the equivalent Python, Java, Go, or C# SDK map); neither the LLM nor SQL can inspect or mutate it.

```sql
CREATE OR REPLACE VIEW SecureCustomerOrders
SQL SECURITY DEFINER AS (
  SELECT
    CAST(Orders.order_id AS STRING) AS order_id,
    CAST(Orders.status AS STRING) AS status,
    CAST(Orders.total_amount AS NUMERIC) AS total_amount
  FROM Orders
  WHERE Orders.customer_id = CAST(SECURE_CONTEXT('app_end_userid') AS INT64)
);
-- Re-apply grant after every CREATE OR REPLACE VIEW:
GRANT SELECT ON VIEW SecureCustomerOrders TO ROLE support_agent_ro;
-- Verify support_agent_ro and `public` have ZERO grants on base table `Orders`!
```

- **Performance & Security Caveats:**
  1. Filter on a **leading primary key or index key column** with `SECURE_CONTEXT()` so Spanner pushes filtering down to storage.
  2. `SECURE_CONTEXT()` takes only a **string literal key** and always returns a `STRING` (serialize multi-attribute context such as multiple roles as JSON and extract with JSON functions).
  3. Disallowed inside generated columns, default column expressions, and UDF bodies.
  4. **Data Boost Incompatible:** Definer's rights views fail on Spanner Data Boost queries.
  5. **Query Mode:** Must execute in `NORMAL` mode (`PLAN`/`PROFILE` fails for FGAC callers lacking base-table privileges).
  6. **NO `security_barrier` (Side-Channel Inference Risk!):** Crafted predicates like `WHERE 1 / (score - 20) = 1` can trigger divide-by-zero errors revealing hidden rows outside the view predicate. Always pair parameterized views with curated `spanner-sql` tools + Model Armor rather than raw untrusted SQL.

---

## 6. Model Armor & Regional Data Residency (Bangkok + Singapore)

### Mode A — Project-Wide Floor Settings for Managed Remote MCP Servers
Managed via global endpoint `modelarmor.googleapis.com`; screens **only** `tools/call` (request + response), `prompts/get` (request + response), and **MCP tool execution errors** (mitigates indirect prompt injection via error payloads). Does **not** screen `tools/list`, `resources/*`, `notifications/*`, streaming SSE payloads, or protocol errors. Applies project-wide across all integrated services (Vertex AI + MCP) using fixed `v1` filters.

```bash
gcloud model-armor floorsettings update \
  --full-uri="projects/{project_id}/locations/global/floorSetting" \
  --enable-floor-setting-enforcement=TRUE \
  --add-integrated-services=GOOGLE_MCP_SERVER \
  --google-mcp-server-enforcement-type=INSPECT_AND_BLOCK \
  --enable-google-mcp-server-cloud-logging \
  --malicious-uri-filter-settings-enforcement=ENABLED \
  --add-rai-settings-filters='[{"confidenceLevel": "MEDIUM_AND_ABOVE", "filterType": "DANGEROUS"}]'
# Disable MCP scanning: --remove-integrated-services=GOOGLE_MCP_SERVER
```

- **Operational Warnings:** (1) Omit prompt-injection filters on MCP floor settings unless traffic carries free-form natural language (prevents false positives on structured SQL/JSON); (2) cross-project agent + Spanner setups with floor settings on both projects invoke Model Armor **twice**; (3) `--enable-google-mcp-server-cloud-logging` logs **full payloads (including PII)**—configure a regional Cloud Logging sink first, and inspect via `resource.type="model-armor_managed_service"` + `logName="projects/{project_id}/logs/modelarmor.googleapis.com%2Fdetection"`.
- **CRITICAL Thailand PDPA / `asia-southeast3` Finding:** Model Armor is **not available in `asia-southeast3` (Bangkok)**; nearest APAC region is **`asia-southeast1` (Singapore)**. Both Spanner and Database Insights remote MCP floor settings use **Cross-Jurisdictional Routing**, sending payloads out of Thailand—verify PDPA legal approval before enabling on regulated Bangkok databases.

### Mode B — Regional Template Screening in ADK / LangChain (For MCP Toolbox Agents)
Self-hosted MCP Toolbox does not call Model Armor internally—invoke it in ADK `before_model_callback`/`after_model_callback`, LangChain `ModelArmorMiddleware`, or an API Gateway using `roles/modelarmor.user` (runtime) and `roles/modelarmor.admin` (template authoring).
- **Singapore Regional Endpoint:** `modelarmor.asia-southeast1.rep.googleapis.com` (`projects/{project_id}/locations/asia-southeast1/templates/{template_id}`).
- **Verified `asia-southeast1` Capabilities Under Strict Data Residency (`limited-support`, `at_rest/in_use/in_transit: Yes`):**
  - **Supported:** Responsible AI (`rai`), Sensitive Data Protection (`sdp` basic secrets + Advanced DLP `DeidentifyTemplate`), Prompt Injection & Jailbreak (`pi_and_jailbreak`).
  - **Not Supported under DRZ in `asia-southeast1`:** `malicious_uri`, `csam`, `topicality`, multi-language translation, image scanning, antivirus (`gavs`).

---

## 7. CMEK, Network Perimeter, Audit Logs, Request Tags & Recovery

- **CMEK / EKM (Zero Spanner Surcharge or SLA Impact):**
  - Match KMS keyring region strictly to Spanner region: **`asia-southeast3`** KMS key for Bangkok primary (`regional-asia-southeast3`), and **`asia-southeast1`** KMS key for Singapore DR backup copies. Grant `roles/cloudkms.cryptoKeyEncrypterDecrypter` to Spanner Service Agent `service-{project_number}@gcp-sa-spanner.iam.gserviceaccount.com` (provision via `gcloud beta services identity create --service=spanner.googleapis.com`).
  - **Availability Hazards:** Key disable/destroy propagates up to **3 hours** (5-min KMS polling); during KMS outage Spanner runs best-effort up to **1 hour** before returning `FAILED_PRECONDITION`; **databases left CMEK-disabled > 7 consecutive days are AUTOMATICALLY PERMANENTLY DELETED**. Keep old keys during in-place rotation until `encryptionInfo` shows only new keys, and retain old keys for existing backups.
- **Network Perimeter & Edition Policy:** Spanner supports **VPC Service Controls (VPC-SC)** and **Private Service Connect (PSC)** (no static public IPs; no PSA). Restrict edition sprawl with org policy `constraints/spanner.managed.restrictCloudSpannerEditions` (note: managed autoscaler can still update non-compliant instances; customer autoscalers are blocked).
- **Cloud Audit Logs (Enable Data Access Explicitly!):** `ADMIN_WRITE` is on by default, but **`DATA_READ` and `DATA_WRITE` are OFF by default**—enable both for `spanner.googleapis.com` AND `DATA_READ` for managed MCP servers (filter MCP traffic with `protoPayload.serviceName="spanner.googleapis.com/mcp"`). Note `AuditLog.metadata.processingDurationSeconds` records backend + API latency for `Commit`/`ExecuteSql` (omitted on streaming/partitioned RPCs). Alert on unexpected agent writes:
  ```text
  protoPayload.serviceName="spanner.googleapis.com"
  AND (protoPayload.methodName="google.spanner.v1.Spanner.Commit" OR protoPayload.methodName="google.spanner.v1.Spanner.ExecuteBatchDml")
  AND protoPayload.authenticationInfo.principalEmail=~".*@.*\\.iam\\.gserviceaccount\\.com"
  ```
- **Request Tags & Application Logging:** Remote Spanner MCP auto-tags `mcp_execute_sql_readonly`, `mcp_execute_sql`, `mcp_commit` (inspect via `SPANNER_SYS.QUERY_STATS_TOP_10MINUTE` / `TXN_STATS_TOP_10MINUTE` / `LOCK_STATS_TOP_10MINUTE`). Tags cap at **50 ASCII chars** (`32–126`, leading `_` stripped, case-sensitive)—**never embed PII in tags**! Log tool name, raw arguments, HitM vs AO mode, and end-user session ID at the application layer.
- **Last-Line Data Recovery:** Rely on **7-day PITR** (all editions, microsecond recovery from compromised write agents), **Enterprise Incremental Backups** (`$0.0315/GiB-mo` Bangkok), and **cross-region DR backup copies** via `gcloud spanner backups copy` to `regional-asia-southeast1` (`$0.08/GiB` Asia egress + `$0.03432/GiB-mo` Singapore storage). Always re-apply FGAC IAM bindings after backup restore.

---

## 8. Security Anti-Patterns & Public References

- **NEVER** bind `roles/spanner.databaseRoleUser` without a `resource.name.endsWith("/{role_name}")` IAM Condition.
- **NEVER** leave `roles/spanner.databaseReader` or `roles/spanner.databaseUser` on an FGAC Service Account after cutover.
- **NEVER** block `tool.isReadOnly == false` in Spanner Remote MCP IAM Deny CEL without exempting `create_session`.
- **NEVER** grant base table access to `public`, or grant `spanner_secure_context_reader` to agents.
- **NEVER** run `CREATE OR REPLACE VIEW` without re-applying `GRANT SELECT ON VIEW`.
- **NEVER** forget that CMEK disablement > 7 consecutive days permanently deletes the Spanner database.

**Public Documentation:**
- [Spanner Fine-Grained Access Control (FGAC)](https://docs.cloud.google.com/spanner/docs/fgac-about)
- [Spanner Parameterized Views](https://docs.cloud.google.com/spanner/docs/parameterized-views)
- [Control MCP Server Use with IAM](https://docs.cloud.google.com/mcp/control-mcp-use-iam)
- [Model Armor Integration with Google Cloud MCP](https://docs.cloud.google.com/model-armor/model-armor-mcp-google-cloud-integration)
- [Spanner CMEK & Audit Logging](https://docs.cloud.google.com/spanner/docs/cmek)
