# `tools.yaml` Schema, Spanner Tools, Embeddings & Authenticated Parameters

Complete configuration reference for authoring hardened **MCP Toolbox for Databases** `tools.yaml` files for **Cloud Spanner**—covering flat multi-document v1 syntax, all 5 Spanner tool types, secretless Vertex AI regional embeddings, parameter guardrails, OIDC authenticated parameters, MCP 2026-07-28 secure parameters, OAuth 2.1 scopes, and per-persona isolation.

---

## 1. `tools.yaml` Structure: Flat Multi-Document v1 vs Legacy Nested Map

- **Flat Multi-Document v1 (Recommended):** Separate each resource with `---` and declare top-level `kind:` (`source`, `tool`, `group`, `toolset`, `authService`, `embeddingModel`, `prompt`, `resource`, `resourceTemplate`), `name:`, and `type:`.
- **Legacy Nested Map Format:** Top-level maps (`sources:`, `embeddingModels:`, `authServices:`, `tools:`, `toolsets:`) are still accepted, and can be auto-migrated in place (writes a `.bak` backup; non-top-level comments are not preserved):
  ```bash
  toolbox migrate --config tools.yaml --dry-run
  toolbox migrate --config tools.yaml
  ```
- **Environment Variable Interpolation:** `${ENV_NAME}` (fails startup if unset) or `${ENV_NAME:default_val}` (fallback when unset). Never hardcode secrets or tokens in `tools.yaml`.
- **`kind: group` vs `kind: toolset` Gotchas:**
  1. Every `kind: toolset` is internally loaded as a tools-only `group`.
  2. A `description:` field on `kind: toolset` is **dropped with a warning**; prefer `kind: group` to surface `description` via `groups/list` and bundle `tools`, `prompts`, `resources`, and `resourceTemplates`.
  3. Unrecognized fields fail startup immediately.
  4. Reusing the same `name` across a `toolset` and a `group` causes a duplicate-name startup failure.
- **Local CLI Smoke Testing & Agent Skill Packaging:**
  ```bash
  # Invoke a curated tool directly without launching an IDE/MCP client
  toolbox --config tools.yaml invoke get_my_active_orders '{"status": "SHIPPED"}'

  # Generate a portable SKILL.md + Node.js wrapper package from a persona group
  toolbox --config tools.yaml skills-generate \
    --group customer_readonly_persona \
    --output-dir ./generated-skills \
    --invocation-mode npx
  ```

---

## 2. Spanner `kind: source` (`type: spanner`) — Exact Fields & Critical Omissions

```yaml
kind: source
name: spanner-primary-bkk
type: spanner
project: ${SPANNER_PROJECT}
instance: ${SPANNER_INSTANCE}           # e.g., regional-asia-southeast3 (Enterprise)
database: ${SPANNER_DATABASE}
dialect: ${SPANNER_DIALECT:googlesql}   # googlesql (default) | postgresql
# useClientOAuth: false                 # Applies ONLY to spanner-search-catalog (Dataplex)!
```

| Field | Type | Required | Operational Rules & Gotchas |
| :--- | :--- | :---: | :--- |
| `type` | `string` | Yes | Must be `"spanner"`. |
| `project` | `string` | Yes (Cloud) | Required for Cloud Spanner; defaults to `"default"` for Spanner Omni. |
| `instance` | `string` | Yes (Cloud) | Required for Cloud Spanner; defaults to `"default"` for Spanner Omni. |
| `database` | `string` | Yes | Target Spanner database ID. |
| `dialect` | `string` | No | `googlesql` (default) or `postgresql`. |
| `useClientOAuth` | `bool` | No | Passed only to the Dataplex catalog manager (`spanner-search-catalog`); Spanner database client always initializes with ADC! |
| `instanceType` | `string` | No | `cloud` (default) or `omni` (deploy-anywhere Spanner Omni; no ADC/IAM or Knowledge Catalog). |
| `omniEndpoint`, `omniUsePlainText`, `omniCaCertificateFile`, `omniClientCertificateFile`, `omniClientKeyFile`, `omniUsername`, `omniPassword` | various | Only if `omni` | Setting any `omni*` field when `instanceType != "omni"` fails validation. `omniUsePlainText` cannot combine with TLS certs or username/password. |

> **CRITICAL SPANNER SOURCE GOTCHA:** Unlike Cloud SQL, AlloyDB, MySQL, and BigQuery sources, the `type: spanner` source has **NO `readOnly` field** and **NO `databaseRole` field** (`ClientConfig` never attaches an FGAC database role).
> - **Enforce read-only access** by setting **`readOnly: true` on every read tool** **AND** running the Cloud Run MCP Toolbox container under a database-level `roles/spanner.databaseReader` Service Account.
> - **Never add `databaseRole` / `database_role`** inside `kind: source` (`type: spanner`)—unknown fields fail Toolbox startup. When you require native Spanner FGAC `database_role` isolation over MCP, use the **Spanner Managed Remote MCP Server** (`create_session.fineGrainedAccessRole`) or a custom Spanner SDK / ADK `SpannerToolset` backend.

---

## 3. All 5 Spanner Tool Types & Dialect Syntax

### 1. `spanner-sql` (Curated Parameterized SQL — Mandatory for Production Agents)
- **Fields:** `type: spanner-sql`, `source`, `description` (required), `statement` (required), `readOnly` (bool, **defaults to `false`**!), `parameters`, `templateParameters`, `authRequired`, `scopesRequired`, `annotations`, `ui`.
- **Transaction & Annotation Semantics:**
  - `readOnly: true` -> executes via `Single().Query(ctx, stmt)` (single-use read-only transaction, zero lock acquisition) and defaults MCP annotation `readOnlyHint: true`.
  - `readOnly: false` (default!) -> executes inside a `ReadWriteTransaction` and defaults MCP annotation `destructiveHint: true`. **Always set `readOnly: true` explicitly** on `SELECT` and `GRAPH_TABLE` tools or queries against `roles/spanner.databaseReader` SAs will fail with `PERMISSION_DENIED`.
- **Dialect Parameter Syntax:**
  - **GoogleSQL:** Named parameters (`@customer_id`, `@status`) mapped by parameter name.
  - **PostgreSQL:** Positional parameters (`$1`, `$2`) mapped strictly in the declaration order listed under `parameters:`.
  - **Array Parameters:** JSON arrays are automatically converted into typed Go slices (`[]string`, `[]int64`, etc.) required by Spanner.

### 2. Vertex AI `embeddingModel` + `embeddedBy` + `valueFromParam` (Enterprise+)
Automatically converts natural-language query or document strings into vector arrays (`ARRAY<FLOAT32>` / `ARRAY<FLOAT64>`) server-side inside Toolbox—so the LLM never handles raw float arrays.

```yaml
# Production Enterprise Mode: Secretless Vertex AI Regional ADC (RECOMMENDED)
# Requires roles/aiplatform.user on the Cloud Run Toolbox Service Account.
# NEVER use location: global for gemini-embedding-001!
kind: embeddingModel
name: vertex-embed-768
type: gemini
model: gemini-embedding-001
project: ${GOOGLE_CLOUD_PROJECT}
location: ${VERTEX_AI_LOCATION:asia-southeast1}   # Singapore (~25 ms RTT from Bangkok)
dimension: 768
# Local non-production prototype alternative ONLY (mutually exclusive with project/location):
# apiKey: ${GOOGLE_API_KEY}
---
kind: tool
name: search_knowledge_semantic
type: spanner-sql
source: spanner-primary-bkk
readOnly: true
description: Semantic search over knowledge base articles using cosine distance.
statement: |
  SELECT doc_id, title, COSINE_DISTANCE(embedding, @query_vec) AS distance
  FROM {table_name}
  WHERE embedding IS NOT NULL
  ORDER BY distance
  LIMIT @top_k
parameters:
  - name: query_vec
    type: string
    description: Natural language search query to embed
    embeddedBy: vertex-embed-768
  - name: top_k
    type: integer
    description: Number of matches to return
    default: 5
    minValue: 1
    maxValue: 20
---
kind: tool
name: insert_knowledge_doc
type: spanner-sql
source: spanner-primary-bkk
readOnly: false
description: Inserts a document and auto-generates its embedding server-side.
statement: |
  INSERT INTO {table_name} (doc_id, title, content, embedding)
  VALUES (@doc_id, @title, @content, @content_embedding)
parameters:
  - name: doc_id
    type: string
    description: Unique document ID
  - name: title
    type: string
    description: Document title
  - name: content
    type: string
    description: Full document body
  - name: content_embedding
    type: string
    description: Auto-derived from content (LLM does not duplicate text)
    valueFromParam: content
    embeddedBy: vertex-embed-768
```

### 3. `spanner-execute-sql` (Dynamic Raw SQL — Build-Time Developer HITL ONLY)
- Fields: `type: spanner-execute-sql`, `source`, `description` (required), `readOnly` (bool, default `false`). Accepts single LLM parameter `sql`.
- **NEVER include `spanner-execute-sql` in any production customer-facing `tools.yaml`.**

### 4. `spanner-list-tables` (Read-Only Schema Introspection)
- Fields: `type: spanner-list-tables`, `source`, `description`, `authRequired`.
- Optional LLM parameters: `table_names` (comma-separated, default `""`), `output_format` (`"simple"` or `"detailed"`, default `"detailed"`).
- Dialect-aware (`googlesql` or `postgresql`); returns JSON describing columns, PK/FK/CHECK/UNIQUE constraints, and indexes (including `interleaved_in_table` and `storing_columns`).

### 5. `spanner-list-graphs` & `spanner-search-catalog`
- **`spanner-list-graphs` (GoogleSQL + Enterprise/Enterprise Plus ONLY):** Optional `graph_names` and `output_format` (`"simple"`/`"detailed"`). Returns JSON with `node_tables`, `edge_tables`, `labels`, and `property_declarations`. Fails on PostgreSQL dialect.
- **`spanner-search-catalog` (Cloud Spanner ONLY, not Omni):** Searches Knowledge Catalog (Dataplex). Parameters: required `prompt`, optional `databaseIds`, `projectIds`, `types` (`DATABASE`, `TABLE`, `VIEW`), `pageSize` (default `5`). Requires Dataplex IAM on Toolbox SA (or client OAuth when `useClientOAuth: true`).

---

## 4. Parameter Validation & Optional-Parameter Gotchas

- **Supported Types:** `string`, `integer`, `float`, `boolean`, `array` (requires `items:`), `map` (optional `valueType:`).
- **Validation Guardrails:**
  - `allowedValues: [...]` — strict allowlist (**supports regex** patterns, e.g., UUID or enum validation).
  - `excludedValues: [...]` — denylist (**supports regex**).
  - `minValue` / `maxValue` — numeric bounds on `integer` and `float` (always cap `LIMIT` params!).
  - `escape` — `"single-quotes"`, `"double-quotes"`, `"backticks"`, or `"square-brackets"` for strings in `templateParameters` only. **Security rule:** avoid `templateParameters` in production; escaping alone does **not** prevent SQL injection—if dynamic identifiers are unavoidable, enforce strict `allowedValues` allowlists.
- **CRITICAL Optional-Parameter Trap (`default: null`):**
  - Every parameter is **`required: true` by default**.
  - Supplying a non-null `default:` makes the parameter optional.
  - **`default: null`, `default: ~`, or an empty `default:` does NOT make a parameter optional!** YAML parses it as absent, so calls omitting it fail with `parameter "..." is required`. Set **`required: false`** to pass SQL `NULL` when omitted (`default: ""` is a valid empty-string default).
  - Array `items` ignore both `default` and `required`.

---

## 5. Out-of-Band Context Isolation: Preventing Multi-Tenant ID Spoofing

Never accept `customer_id`, `tenant_id`, or `caller_email` as a normal LLM-supplied parameter—prompt injection can easily swap it for another tenant's ID. Choose among three out-of-band binding mechanisms:

| Mechanism | How Value Is Supplied | Visible to LLM? | Best Fit | Protocol & Validation Constraints |
| :--- | :--- | :---: | :--- | :--- |
| **1. Authenticated Parameters** (`authServices:` on param) | Decoded server-side by Toolbox from cryptographically verified OIDC JWT (`<name>_token` header in Native mode, or `Authorization: Bearer` in MCP Auth mode) | **NO** — stripped from LLM tool schema | **Gold standard for multi-tenant end-user isolation** (`WHERE customer_sub = @sub` or `WHERE email = @caller_email`) | Requires `kind: authService` (`type: google` or `type: generic`). Native mode (`mcpEnabled: false`) requires JWT (opaque tokens unsupported). |
| **2. Secure Parameters** (`secure: true` on param) | Bound out-of-band by host app code (`bind_secure_param` / `secure_params={...}`), sent in JSON-RPC `secureArguments` | **NO** — published in `secureInputSchema` (omitted from `inputSchema`) | Trusted backend runtime context (`tenant_id`, `org_id`) after backend already authenticated caller | Requires **MCP `2026-07-28`** + extension **`com.google.cloud/toolbox.v1`** + SDK `toolbox-core`/`toolbox-adk >= 1.4.0`. Always required; **cannot** combine with `authServices`, `default`, or `required: false`. Prompt-injected collision returns `isError: true`; missing/misplaced returns `-32602`; un-negotiated client returns `-32021`; cross-mixing `bind_param`/`bind_secure_param` raises `ValueError`. |
| **3. Client `bound_params` & URL Param Binding** | SDK `bound_params={...}` or HTTP query string `?region=asia-southeast3` | **NO** — URL-bound params stripped from `tools/list` | Non-sensitive routing/scoping metadata (`env`, `region`) | **SECURITY WARNING:** **NEVER** pass secrets, passwords, API keys, or auth tokens via URL query binding (leaks into LB/proxy logs and browser history!). |

---

## 6. Full Multi-Tenant `tools.yaml` Blueprint & Persona Isolation

```yaml
kind: source
name: primary-spanner-bkk
type: spanner
project: ${SPANNER_PROJECT}
instance: ${SPANNER_INSTANCE}
database: ${SPANNER_DATABASE}
dialect: googlesql
---
# Mode 1: Toolbox Native Auth (JWT passed in header `google-webapp-auth_token`)
kind: authService
name: google-webapp-auth
type: google
clientId: ${GOOGLE_OAUTH_CLIENT_ID}
---
# Mode 2: Server-Wide MCP OAuth 2.1 Resource Server (`Authorization: Bearer <token>`)
# Auto-serves `/.well-known/oauth-protected-resource` PRM (set TOOLBOX_URL or --mcp-prm-file)
kind: authService
name: corp-okta-mcp-auth
type: generic
authorizationServer: https://{idp_domain}/oauth2/default
audience: api://spanner-mcp-prod
mcpEnabled: true
scopesRequired:
  - openid
  - spanner:read
---
# Read-Only Tool: Verified OIDC subject claim prevents cross-tenant data exfiltration
kind: tool
name: get_my_active_orders
type: spanner-sql
source: primary-spanner-bkk
readOnly: true
description: Retrieves active orders strictly for the authenticated end user.
statement: |
  SELECT order_id, status, total_amount, created_at
  FROM Orders
  WHERE customer_sub = @customer_sub AND status = @status
  ORDER BY created_at DESC
  LIMIT @max_rows
authRequired:
  - google-webapp-auth
parameters:
  - name: customer_sub
    type: string
    description: Verified OIDC subject claim (auto-populated; invisible to LLM)
    authServices:
      - name: google-webapp-auth
        field: sub
  - name: status
    type: string
    description: Order lifecycle status filter
    default: ACTIVE
    allowedValues: ["ACTIVE", "PROCESSING", "SHIPPED", "DELIVERED"]
  - name: max_rows
    type: integer
    description: Maximum orders returned
    default: 10
    minValue: 1
    maxValue: 50
---
# Write-Scoped Tool: Requires OAuth 2.1 write scope (HTTP 403 + WWW-Authenticate if missing)
kind: tool
name: cancel_my_order
type: spanner-sql
source: primary-spanner-bkk
readOnly: false
description: Cancels a PROCESSING order belonging strictly to the authenticated caller.
statement: |
  UPDATE Orders
  SET status = 'CANCELLED', updated_at = PENDING_COMMIT_TIMESTAMP()
  WHERE order_id = @order_id
    AND customer_sub = @customer_sub
    AND status = 'PROCESSING'
authRequired:
  - corp-okta-mcp-auth
scopesRequired:
  - spanner:write:orders
parameters:
  - name: order_id
    type: string
    description: UUID of the order to cancel
    allowedValues:
      - "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
  - name: customer_sub
    type: string
    description: Verified OIDC subject claim
    authServices:
      - name: corp-okta-mcp-auth
        field: sub
---
# Secure Parameter Example (requires MCP 2026-07-28 + SDK >= 1.4.0)
kind: tool
name: get_tenant_quota_status
type: spanner-sql
source: primary-spanner-bkk
readOnly: true
description: Returns quota status for the backend-bound tenant.
statement: |
  SELECT tenant_id, tier, daily_limit, used_today
  FROM TenantQuotas
  WHERE tenant_id = @tenant_id
parameters:
  - name: tenant_id
    type: string
    description: Backend-bound tenant ID (invisible to LLM)
    secure: true
---
kind: group
name: customer_readonly_persona
description: Strictly read-only order lookup tools for Tier-1 customer bots.
tools:
  - get_my_active_orders
---
kind: group
name: customer_selfservice_writer_persona
description: Scoped self-service order cancellation tools for verified users.
tools:
  - get_my_active_orders
  - cancel_my_order
```

### Persona YAML Slicing Pattern (Blast-Radius Isolation)
Do **not** mount one monolithic `tools.yaml` containing both read and write tools on a single write-privileged Cloud Run service. Render **per-persona minimal YAML files** (e.g., `tools.ro-persona.yaml` containing ONLY read tools + `roles/spanner.databaseReader` SA; `tools.writer-persona.yaml` containing ONLY scoped write tools + `roles/spanner.databaseUser` SA) and deploy separate Cloud Run services per persona.

### Five Critical `authService` Pitfalls
1. **Never point `type: generic` at Google's `tokeninfo` (`https://oauth2.googleapis.com/tokeninfo`):** Generic OIDC requires RFC 7662's `active` claim, which Google's `tokeninfo` omits—validation always fails. Always use `type: google` for Google OAuth tokens (handles local JWT verification via `clientId`/`audience` and opaque tokens via `tokeninfo`).
2. **Disallowed Fields When `mcpEnabled: false`:** On `type: google`, `audience` and `scopesRequired` are rejected when `mcpEnabled: false`. On `type: generic`, `scopesRequired`, `introspectionEndpoint`, `introspectionMethod`, and `introspectionParamName` are rejected when `mcpEnabled: false`.
3. **Header Difference Between Modes:** Native mode (`mcpEnabled: false`) expects `<authService_name>_token` (JWT only). MCP Auth mode (`mcpEnabled: true`) expects `Authorization: Bearer <token>` (JWT or opaque) and auto-provisions `/.well-known/oauth-protected-resource` (requires `TOOLBOX_URL`/`--toolbox-url`, or `--mcp-prm-file`).
4. **Per-App OAuth Client IDs:** When multiple apps call a shared Toolbox server, register a distinct `authService` (OAuth Client ID) per app.
5. **Dynamic Token Refresh in Python SDKs:** Pass dynamic callables in `auth_token_getters={"google-webapp-auth": lambda ctx: ctx.state.get("auth_token")}` (in `toolbox-adk`) instead of static strings so expired ID tokens refresh automatically.

---

## 7. `tools.yaml` Anti-Patterns & Public References

- **NEVER** put `databaseRole` or `readOnly` on `kind: source` (`type: spanner`).
- **NEVER** omit `readOnly: true` on `spanner-sql` SELECT/GQL tools (defaults to R/W transaction!).
- **NEVER** use `default: null` expecting an optional parameter (use `required: false`).
- **NEVER** use `location: global` or commit a static `apiKey` for production `gemini-embedding-001`.
- **NEVER** expose `spanner-execute-sql` or unconstrained `templateParameters` to end-user agents.

**Public Documentation:**
- [MCP Toolbox Configuration & Spanner Tools](https://github.com/googleapis/genai-toolbox)
- [Prebuilt Tools with MCP Toolbox for Databases](https://docs.cloud.google.com/spanner/docs/pre-built-tools-with-mcp-toolbox)
- [Secure Agent Interactions with Spanner & MCP](https://docs.cloud.google.com/spanner/docs/secure-agent-interactions-mcp)
