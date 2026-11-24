# Lab 6: MCP Toolbox, OIDC Auth & Fine-Grained Agent Security

In this lab, you'll lock down multi-agent access to the Acme Commerce Knowledge Graph so every agent persona sees only the tools, rows, and columns it needs—with zero raw SQL tools exposed. You'll author Spanner FGAC views and grants, harden `starter/mcp/tools.yaml` with server-side embeddings, anchored regexes, and OIDC identity binding, and complete the multi-agent RBAC demo to verify prompt injections fail closed. Cloud Spanner, Cloud Run MCP Toolbox, and Secret Manager stay in Bangkok (`asia-southeast3`), while Vertex AI embeddings, Model Armor, and Cloud DLP run in Singapore (`asia-southeast1`).

## What You'll Work On

| Step | File to open (`starter/`) | Where to edit (`TODO` / line) | Track |
| :-: | :--- | :--- | :-: |
| 1 | [`starter/sql/04_fgac_roles_views.sql`](../starter/sql/04_fgac_roles_views.sql) | `PublicApiCatalogView` (`L41`), `sdlc_readonly_role` grants (`L152`), auditor view grant (`L198`) | Track A & B |
| 2 | [`starter/mcp/tools.yaml`](../starter/mcp/tools.yaml) | 17 `TODO(Lab 6)` markers in 4 passes: models/OIDC (`L34`, `L44`), `readOnly`/embeddings/regexes, `@caller_email`, `toolsets` (`L612`) | Track A & B |
| 3 | [`starter/mcp/adk_multi_agent_demo.py`](../starter/mcp/adk_multi_agent_demo.py) | `run_persona_query()` (`L679`, `TODO` at `L690`, stub `raise NotImplementedError` at `L720`) | Track A & B |
| 4 | Offline verification | Run `make TRACK=starter verify-lab LAB=6` | Track A & B |
| 5 | *(Optional — Local / Live Bangkok GCP)* | Native Zero-Docker MCP Toolbox (`v1.13.1`), Antigravity IDE config, and Bangkok Cloud Run + Singapore Model Armor deploy | Track B (Optional) |

- **Estimated time:** ~40 min (Track A Express) / ~75 min (Track B Full)
- **Reference copy (read-only if stuck):** [`solution/sql/04_fgac_roles_views.sql`](../solution/sql/04_fgac_roles_views.sql), [`solution/sql/04b_fgac_grants_rerunnable.sql`](../solution/sql/04b_fgac_grants_rerunnable.sql), [`solution/mcp/tools.yaml`](../solution/mcp/tools.yaml), [`solution/mcp/adk_multi_agent_demo.py`](../solution/mcp/adk_multi_agent_demo.py)
- **Antigravity skill:** [`spanner-mcp-toolbox-and-agent-security`](../.agents/skills/spanner-mcp-toolbox-and-agent-security/SKILL.md) (support: [`spanner-schema-and-data-modeling`](../.agents/skills/spanner-schema-and-data-modeling/SKILL.md))

---

## Quick Background

There's an important Spanner quirk that shapes our security design: **MCP Toolbox for Databases (`type: spanner`) does not accept a `databaseRole` key in `sources`**. It creates Spanner sessions via ADC without passing a SQL role. Because of that, we split authorization into two clean layers:

1. **Layer 1 — Cloud Run MCP Toolbox (`asia-southeast3`):** Deploy one Cloud Run service per persona with its own IAM service account (`roles/spanner.databaseReader` for read-only personas, `roles/spanner.databaseUser` for ADR writers), a sliced `tools.<persona>.yaml` from Bangkok Secret Manager, `readOnly: true` on reads, OIDC `authServices` for `@caller_email`, and Model Armor + Cloud DLP prompt screening in Singapore (`asia-southeast1`).
2. **Layer 2 — FGAC-capable clients (`gcloud --database-role`, Spanner SDKs, Spanner Managed Remote MCP):** Use SQL `CREATE ROLE` / `GRANT` with dedicated `sa-fgac-*` service accounts holding ONLY `roles/spanner.fineGrainedAccessUser` plus a conditional `roles/spanner.databaseRoleUser` (zero DB-level IAM, so callers can't bypass FGAC by omitting a role).

| Persona (`toolset`) | Layer 1: Bangkok Cloud Run SA & IAM | Layer 2: FGAC SA & SQL Role | Allowed Tools |
| :--- | :--- | :--- | :--- |
| `impact-analyst` (`impact-analyst-toolset`) | `sa-impact-analyst` (`databaseReader`) | `sa-fgac-readonly` (`sdlc_readonly_role`) | 4 read-only tools (`analyze_api_blast_radius_graphrag`, `search_sdlc_knowledge_hybrid`, `get_service_ownership_and_slas`, `list_my_team_assets`) |
| `adr-curator` (`adr-curator-toolset`) | `sa-adr-curator` (`databaseUser`) | `sa-fgac-adr-writer` (`sdlc_adr_writer_role`) | Same 4 read tools + `draft_adr_proposal` (`readOnly: false`, inserts `'PROPOSED'` only) |
| `security-auditor` (`security-auditor-toolset`) | `sa-security-auditor` (`databaseReader`) | `sa-fgac-security-auditor` (`sdlc_security_auditor_role`) | 3 shared read tools + `audit_compliance_blast_radius` (`SecurityComplianceImpactView`, read-only) |

---

## Step-by-Step Hands-On

### Step 1: Author FGAC views and grants in `starter/sql/04_fgac_roles_views.sql` (Track A & B)

1. Open [`starter/sql/04_fgac_roles_views.sql`](../starter/sql/04_fgac_roles_views.sql). The 3 database roles (`L33–L35`), `SecurityComplianceImpactView` (`L62`), and `TeamScopedAssetsView` (`L129`) are already filled in.
2. **At line 41 (`PublicApiCatalogView`):** Create `PublicApiCatalogView` with explicit `SQL SECURITY INVOKER` (`INV-05`) and explicit `CAST(...)` on every output column. Join `Apis`, `Services`, and `Owners`, filter `WHERE a.is_public = TRUE`, and project only the non-sensitive columns listed in `L48–L52` so internal APIs and sensitive `Owners` columns (`oncall_pager`, `clearance_tier`, `escalation_lead`) stay hidden.
3. **At line 152 (`sdlc_readonly_role` grants):** Write least-privilege `GRANT SELECT` statements for `sdlc_readonly_role`:
   - Column-scoped `GRANT SELECT (owner_id, team_name, org_unit, slack_channel, email) ON TABLE Owners TO ROLE sdlc_readonly_role;` so sensitive `Owners` columns stay blocked.
   - `GRANT SELECT` on the core Path A & Path B tables (`Services`, `Apis`, `Adrs`, `Requirements`, `DocumentChunks`, edge tables, `SdlcSynonymDict`) and safe views (`ChunkCites*View`, `PublicApiCatalogView`, `TeamScopedAssetsView`). Leave out `AgentQueryAudit`, `SecurityComplianceImpactView`, and all write privileges.
4. **At line 198 (auditor view grant):** Add `GRANT SELECT ON VIEW PublicApiCatalogView TO ROLE sdlc_security_auditor_role;`. *(Production gotcha: running `CREATE OR REPLACE VIEW` wipes existing `GRANT SELECT ON VIEW` bindings, so always re-apply [`solution/sql/04b_fgac_grants_rerunnable.sql`](../solution/sql/04b_fgac_grants_rerunnable.sql) after updating a view.)*

*(Optional Antigravity prompt)*:
> "Help me complete `PublicApiCatalogView` (with `SQL SECURITY INVOKER` and explicit `CAST`s) and the least-privilege grants in `starter/sql/04_fgac_roles_views.sql`."

### Step 2: Harden `starter/mcp/tools.yaml` in 4 passes (Track A & B)

Open [`starter/mcp/tools.yaml`](../starter/mcp/tools.yaml). All 6 SQL queries are already written (and `get_service_ownership_and_slas` at `L55` is a completed example), so you can focus purely on the security controls across 4 short passes:

- **2a. Embedding model & OIDC provider (`L34`, `L44`):**
  - Replace `embeddingModels: {}` (`L42`) with `gemini-embedder` (`type: gemini`, `model: gemini-embedding-001`, `project: ${GOOGLE_CLOUD_PROJECT}`, `location: ${VERTEX_AI_LOCATION}`, `dimension: 768`). Don't add an `apiKey`—Cloud Run authenticates via ADC / Workload Identity against `asia-southeast1` (never `global`).
  - Replace `authServices: {}` (`L49`) with `google-corp-oidc` (`type: google`, `clientId: ${GOOGLE_OAUTH_CLIENT_ID}`).
  - Leave `sources.sdlc-spanner` (`L27–L32`) as-is—never put `databaseRole` there.
- **2b. Read-only flags, server-side embeddings & regex input guards (`L139`, `L320`, `L324`, `L336`, `L415`, `L420`, `L505`, `L517`, `L599`, `L604`):**
  - Set `readOnly: true` on `analyze_api_blast_radius_graphrag` (`L139`), `search_sdlc_knowledge_hybrid` (`L336`), and `audit_compliance_blast_radius` (`L517`). This is required for FTS and for `roles/spanner.databaseReader`.
  - Add the `query_embedding` parameter (`type: string`, `valueFromParam: search_query`, `embeddedBy: gemini-embedder`) on `analyze_api_blast_radius_graphrag` (`L324`) and `search_sdlc_knowledge_hybrid` (`L415`).
  - Add anchored `allowedValues` regexes (`^...$`) on `api_id` (`L320`, `L599`), `doc_type` (`L420`), `adr_id` (`L505`), and `framework` (`L604`). Always anchor with `^` and `$` (use `"^$"` for optional empty strings—in Toolbox `v1.13.1`, bare `""` matches every string).
- **2c. Server-injected OIDC `@caller_email` (`L431`, `L472`, `L485`, `L512`):**
  - On `list_my_team_assets`, add `readOnly: true` and `authRequired: [google-corp-oidc]` (`L431`), then bind `caller_email` via `authServices: [{name: google-corp-oidc, field: email}]` (`L472`).
  - On `draft_adr_proposal`, add `readOnly: false` and `authRequired: [google-corp-oidc]` (`L485`), and bind `caller_email` the same way (`L512`). Because MCP Toolbox strips `authServices`-bound parameters from the LLM tool schema, prompt injection can't forge another team's email.
- **2d. Three isolated persona toolsets (`L612`):**
  - Replace `toolsets: {}` at **line 616** with `impact-analyst-toolset` (4 read-only tools), `adr-curator-toolset` (those 4 + `draft_adr_proposal`), and `security-auditor-toolset` (3 shared read-only tools + `audit_compliance_blast_radius`, zero write tools).

*(Optional Antigravity prompt)*:
> "Walk me through the security TODOs in `starter/mcp/tools.yaml`—regional ADC embeddings, OIDC `caller_email` binding, anchored `allowedValues` regexes, and the three persona toolsets."

### Step 3: Enforce toolset isolation & OIDC fail-closed rules in `starter/mcp/adk_multi_agent_demo.py` (Track A & B)

1. Open [`starter/mcp/adk_multi_agent_demo.py`](../starter/mcp/adk_multi_agent_demo.py) and jump to `run_persona_query()` (`L679`, `TODO` at `L690`, stub `raise NotImplementedError` at `L720`).
2. Follow the 6 steps in the docstring (`L692–L718`):
   - Validate `persona_id in PERSONA_META` and call `load_persona_config(persona_id, repo_root)` to load that persona's allowlisted tools directly from `starter/mcp/tools.yaml`.
   - Raise `SecurityPolicyError` if `tool_name` isn't in `persona_cfg["tools"]`.
   - Use `get_llm_visible_parameters(tool_cfg)` and `get_oidc_bound_parameters(tool_cfg)` to strip any LLM-supplied `caller_email` (tracking stripped keys in `injected_blocked`).
   - Raise `SecurityPolicyError` if an authenticated tool lacks required `oidc_claims`, inject verified claims server-side, and dispatch to the mock executor helpers when `mock=True`.
3. Test your implementation right away and compare with the reference demo:
   ```bash
   python3 starter/mcp/adk_multi_agent_demo.py --mock
   make demo-mcp-mock
   ```
   Check `C3_PROMPT_INJECTION_OIDC_FAIL_CLOSED` proves that even when a compromised prompt injects `caller_email="security-iam@acme-commerce.example"`, the parameter is stripped and only `team-checkout`'s services (`svc-checkout`, `svc-orders`) are returned—zero `team-security` leakage.

*(Optional Antigravity prompt)*:
> "Help me implement `run_persona_query()` in `starter/mcp/adk_multi_agent_demo.py` so it enforces each persona's allowlisted tools from `starter/mcp/tools.yaml` and strips LLM-supplied OIDC parameters like `caller_email`."

### Step 4: Verify Lab 6 offline (Track A & B)

Run the offline verifier before deploying anything to GCP:

```bash
make TRACK=starter verify-lab LAB=6
```

### Step 5 (Optional — Local MCP Toolbox, Antigravity IDE & Live Bangkok Cloud Run)

1. **Zero-Docker local MCP Toolbox (`v1.13.1`) & Antigravity IDE:** See [`.antigravity/README.md`](../.antigravity/README.md) to wire Antigravity IDE via `npx -y @toolbox-sdk/server` or the compiled `./toolbox` binary (`bash solution/mcp/setup_local_toolbox.sh`). Always pass `--config` (not deprecated `--tools-file`) and call Streamable HTTP JSON-RPC 2.0 on **`/mcp`** (legacy `/api/toolset` returns `HTTP 410 Gone` in `v1.13.1`).
   ```bash
   python3 solution/mcp/render_mcp_config.py \
     --project "${GOOGLE_CLOUD_PROJECT}" \
     --persona impact-analyst \
     --mode npx \
     --dry-run
   ```
2. **Apply your FGAC DDL and deploy Bangkok Cloud Run + Singapore Model Armor/DLP:**
   *(Note: `run_live_sql.py 04` applies cleanly when Lab 3 used `--skip-fgac`; if you provisioned `solution/` in Lab 3, those roles already exist—re-apply grants via [`solution/sql/04b_fgac_grants_rerunnable.sql`](../solution/sql/04b_fgac_grants_rerunnable.sql) if needed.)*
   ```bash
   # Apply your starter FGAC roles, views, and grants to Bangkok Spanner
   python3 scripts/run_live_sql.py 04 --track starter

   # Preview all IAM, Bangkok Cloud Run, Secret Manager, and Singapore Model Armor/DLP steps
   bash solution/mcp/iam_and_model_armor_setup.sh --dry-run

   # Optional live deploy (~3-4 min; skip if short on time)
   bash solution/mcp/iam_and_model_armor_setup.sh

   # Optional Lab 6-only cleanup preview (Lab 8's make cleanup-all-gcp also removes all of these)
   bash solution/mcp/iam_and_model_armor_setup.sh --cleanup --dry-run
   ```
   *(Two Cloud Run reminders: keep IAP disabled since MCP Toolbox doesn't support it, and if you combine Cloud Run IAM with MCP OAuth, send the Cloud Run ID token in `X-Serverless-Authorization`. For Thailand PDPA reviews, document that prompts screened by Model Armor and Cloud DLP transit `asia-southeast1`.)*

---

## Verify This Lab

```bash
make TRACK=starter verify-lab LAB=6
```

*(All 3 files are required in both Track A Express and Track B Full.)*

**Passing output:** `[PASS] Lab 6 (starter/, mode=full)` confirming `04_fgac_roles_views.sql`, `tools.yaml`, and all 6 offline RBAC and prompt-injection checks pass.

**If it fails, check:**
- Unanchored `allowedValues` entries in `starter/mcp/tools.yaml`—every pattern must start with `^` and end with `$` (including `"^$"`).
- Forbidden keys in `starter/mcp/tools.yaml`: `databaseRole`, static `apiKey` under `gemini-embedder`, `spanner-execute-sql`, or `templateParameters`.
- Missing `SQL SECURITY INVOKER` on `PublicApiCatalogView`, missing `GRANT SELECT ... TO ROLE sdlc_readonly_role`, or any remaining `TODO(Lab 6)` markers.

---

**Previous / Next:** [← Lab 5: Hybrid Retrieval & GraphRAG](05_hybrid_retrieval_and_graphrag.md) | [Starter Guide](../starter/README.md) | [Lab 7: Sizing & Cost TCO →](07_sizing_and_cost_tco.md)
