# Antigravity IDE — Cloud Spanner MCP Configuration Guide

Connect **Antigravity IDE** to the **Acme Commerce Cloud Spanner Enterprise SDLC Knowledge Graph** (`sdlc-kg-enterprise` / `sdlc_knowledge_graph` in Bangkok `asia-southeast3`, with Vertex AI `gemini-embedding-001` in Singapore `asia-southeast1`).

> **Zero Secrets Policy:** Never commit static API keys, OAuth client secrets, service account JSON keys, or raw bearer tokens into `.antigravity/mcp_config.json`. All local servers use **Application Default Credentials (ADC)** (`gcloud auth application-default login`), and remote Cloud Run endpoints authenticate via short-lived OIDC tokens.

---

## Method 1: Official Antigravity Built-In MCP Store (Fastest for Dev / HITL)

You do **not** need to download the MCP Toolbox binary manually when using Antigravity's built-in MCP Store:

1. Open **Antigravity IDE** and open the editor's **Agent Panel**.
2. Click the **Menu** icon and select **MCP Servers**.
3. Locate **Spanner** in the available servers catalog and click **Install**.
4. Complete the on-screen Google Cloud Project authorization flow.
   - Antigravity automatically refreshes ADC credentials (unlike standalone 1-hour `gcloud` bearer tokens).
   - Select project `${GOOGLE_CLOUD_PROJECT}`, instance `sdlc-kg-enterprise`, database `sdlc_knowledge_graph`, dialect `googlesql`.

> **Build-Time HITL Warning:** Built-in / `--prebuilt spanner` servers expose both `execute_sql` (read/write) and `execute_sql_readonly` alongside `list_tables`, `list_graphs`, and `search_catalog`. They are strictly intended for **trusted human-in-the-loop (HITL) developer workflows**—never expose prebuilt raw-SQL tools to autonomous end-user agents.

---

## Method 2: Custom `mcp_config.json` via `npx -y @toolbox-sdk/server` (Recommended for Curated Workshop Tools)

To expose ONLY the least-privilege, read-only **Impact Analyst** persona tools (`personas/tools.impact-analyst.yaml` — 4 curated parameterized `spanner-sql` tools, zero raw `execute_sql`):

1. Authenticate local ADC once:
   ```bash
   gcloud auth application-default login
   ```
2. Render concrete workspace paths and your GCP project ID into `.antigravity/mcp_config.json` (or preview with `--dry-run`):
   ```bash
   python3 solution/mcp/render_mcp_config.py \
     --project "${GOOGLE_CLOUD_PROJECT}" \
     --persona impact-analyst \
     --mode npx \
     --dry-run
   ```
3. In Antigravity **Agent Panel > Menu > MCP Servers > Manage MCP Servers > View raw config** (`mcp_config.json`), paste the rendered `"mcpServers"` entry:
   ```json
   {
     "mcpServers": {
       "sdlc-spanner-impact-analyst-npx": {
         "command": "npx",
         "args": [
           "-y",
           "@toolbox-sdk/server",
           "--config",
           "/abs/path/to/spanner-graph-sdlc-workshop/solution/mcp/personas/tools.impact-analyst.yaml",
           "--stdio"
         ],
         "env": {
           "SPANNER_PROJECT": "YOUR_GCP_PROJECT_ID",
           "SPANNER_INSTANCE": "sdlc-kg-enterprise",
           "SPANNER_DATABASE": "sdlc_knowledge_graph",
           "SPANNER_DIALECT": "googlesql",
           "GOOGLE_CLOUD_PROJECT": "YOUR_GCP_PROJECT_ID",
           "VERTEX_AI_LOCATION": "asia-southeast1",
           "GOOGLE_OAUTH_CLIENT_ID": "${GOOGLE_OAUTH_CLIENT_ID}"
         }
       }
     }
   }
   ```

---

## Method 3: Local `./toolbox` Binary (`--tools-file ... --stdio`)

If you prefer running the compiled Go binary without Node.js/`npx` (also required by binary-only MCP clients):

1. Download the official MCP Toolbox binary (`v1.13.1`) into `solution/mcp/bin/toolbox`:
   ```bash
   bash solution/mcp/setup_local_toolbox.sh
   ```
2. Smoke-test stdio or hardened local HTTP transport (which enforces `--allowed-hosts="localhost,127.0.0.1"` to prevent browser DNS-rebinding attacks):
   ```bash
   bash solution/mcp/run_toolbox_stdio.sh --dry-run
   TOOLBOX_PERSONA=impact-analyst bash solution/mcp/run_toolbox_stdio.sh --http
   ```
3. Configure Antigravity's `mcp_config.json` with `"command": "/abs/path/to/solution/mcp/bin/toolbox"` and `"args": ["--tools-file", "/abs/path/to/solution/mcp/personas/tools.impact-analyst.yaml", "--stdio"]` (v1 CLI also accepts canonical `--config`).

---

## Method 4: Remote Bangkok (`asia-southeast3`) Cloud Run MCP Toolbox & Spanner Remote MCP

### A. Self-Hosted Persona Cloud Run Services (`solution/mcp/iam_and_model_armor_setup.sh`)

Deploy the three isolated persona services (`sdlc-mcp-impact-analyst`, `sdlc-mcp-adr-curator`, `sdlc-mcp-security-auditor`) in **`asia-southeast3` (Bangkok)** with `--no-allow-unauthenticated`, `--address=0.0.0.0 --port=8080`, exact `--allowed-hosts` / `--allowed-origins`, and Secret Manager mounted persona YAMLs:

```bash
# Mint a short-lived Cloud Run IAM OIDC ID token (expires in 1 hour)
export MCP_BEARER_TOKEN="$(python3 solution/mcp/mint_oidc_token.py --audience https://sdlc-mcp-impact-analyst-PROJECT_HASH-as.a.run.app)"
```

Then connect using the `"sdlc-spanner-cloudrun-impact-analyst"` `"type": "http"` block in `.antigravity/mcp_config.json`.

- **Cloud Run Gotchas:**
  - **IAP Unsupported:** MCP Toolbox does not support Cloud Run behind Identity-Aware Proxy (IAP)—keep IAP disabled.
  - **Dual-Auth Header Conflict:** If combining Cloud Run IAM (`roles/run.invoker`) with MCP Toolbox OAuth 2.1 (`mcpEnabled: true` on `Authorization: Bearer`), send the Cloud Run IAM identity token in **`X-Serverless-Authorization`**.

### B. Spanner Managed Remote MCP Server (`https://spanner.googleapis.com/mcp`)

Because MCP Toolbox's `type: spanner` source does **not** accept `databaseRole`, Layer 2 database-native FGAC (`sdlc_readonly_role`, `sdlc_adr_writer_role`, `sdlc_security_auditor_role`) is exercised by `database_role`-aware clients such as the Google-managed **Spanner Remote MCP Server** (`create_session.fineGrainedAccessRole`), Cloud Spanner SDKs, ADK `SpannerToolset`, and `gcloud spanner databases execute-sql --database-role=...`.

- Requires OAuth 2.0 + `roles/mcp.toolUser` + Layer 2 FGAC Service Account (`sa-fgac-*` holding ONLY `roles/spanner.fineGrainedAccessUser` + conditional `roles/spanner.databaseRoleUser`, zero DB-level IAM). API keys are rejected.
- Any IAM Deny Policy blocking non-read-only Spanner Remote MCP tools **must** exempt `create_session` (`!(api.getAttribute('mcp.googleapis.com/tool.name', '') == 'create_session')`), because `create_session` carries `readOnlyHint: false`.

---

## Other Developer Clients (Quick Reference)

| Client | Configuration Path / Command | Root JSON Key |
| :--- | :--- | :--- |
| **Antigravity IDE** | Built-in MCP Store or `.antigravity/mcp_config.json` | `"mcpServers"` |
| **Gemini CLI (`v0.6.0+`)** | `gemini extensions install https://github.com/gemini-cli-extensions/spanner` | Extension (`~/.gemini/extensions/`) |
| **Gemini Code Assist** | `.gemini/settings.json` | `"mcpServers"` |
| **Cursor** | `.cursor/mcp.json` | `"mcpServers"` |
| **Claude Desktop** | Settings > Developer > Edit Config | `"mcpServers"` |
| **VS Code (Copilot)** | `.vscode/mcp.json` | **`"servers"`** *(not `"mcpServers"`)* |

---

## Public Documentation

- [Prebuilt Tools with MCP Toolbox for Databases](https://docs.cloud.google.com/spanner/docs/pre-built-tools-with-mcp-toolbox)
- [Secure Agent Interactions with Spanner & MCP](https://docs.cloud.google.com/spanner/docs/secure-agent-interactions-mcp)
- [Use the Spanner Remote MCP Server](https://docs.cloud.google.com/spanner/docs/use-spanner-mcp)
- [MCP Toolbox for Databases (GitHub)](https://github.com/googleapis/genai-toolbox)
