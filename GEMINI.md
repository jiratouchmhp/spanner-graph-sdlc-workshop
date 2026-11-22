# Cloud Spanner Enterprise SDLC Knowledge Graph & GraphRAG Workshop

Workspace orientation for **Use Case 4**: consolidating **Neo4j Community Edition + a separate Vector Database + a separate Keyword Search cluster** into one ACID **Cloud Spanner Enterprise** platform (Spanner Graph + Full-Text Search + ScaNN Vector Search + MCP Toolbox for Databases) for the fictional **Acme Commerce Platform**.

- **Single Source of Truth**: [WORKSHOP_SPEC.json](WORKSHOP_SPEC.json) and [WORKSHOP_CONTENT_PLAN.md](WORKSHOP_CONTENT_PLAN.md)
- **Always-On Guardrails**: [.agents/rules/spanner-graph-workshop.md](.agents/rules/spanner-graph-workshop.md)
- **Strict Regional Architecture (NO 2-Region Spanner DR)**:
  - **Primary & Only Database/App Region**: **Bangkok (`asia-southeast3` / `regional-asia-southeast3`)** — single Cloud Spanner Enterprise instance (3-zone sync quorum `a/b/c`, 99.99% SLA, Thailand PDPA data residency, same-region Bangkok backups/PITR), Cloud Run MCP Toolbox, user-managed Secret Manager, GCS staging & Dataflow.
  - **Companion Region (Fallback ONLY for Services Unavailable in Bangkok)**: **Singapore (`asia-southeast1`, `~25 ms` RTT)** — **ONLY** Vertex AI (`gemini-embedding-001` & `gemini-2.5-flash`), Model Armor (`modelarmor.asia-southeast1.rep.googleapis.com`), and Cloud DLP / Sensitive Data Protection. **No second Singapore Spanner DR instance and no cross-region Spanner backup copy step.**
- **Dataset Contract**: `68` structured nodes (`Owners` 8, `Services` 12, `Apis` 24, `Adrs` 10, `Requirements` 14), `174` explicit M:N edge rows, `60` dual-projected N:1 edges (`234` total logical edges), and `45` unstructured Markdown documents.
- **Hero Question**: *"What breaks if we change `POST /v2/payments/charge`?"* (`api-payments-charge-v2`, exposed by `svc-payments` / `team-payments`)
  - **7 Deterministic Impact Rows**: `svc-billing`, `svc-checkout` (`DIRECT_SYNC`, hop 1); `svc-orders`, `svc-partner-gw` (`TRANSITIVE_UPSTREAM`, hop 2); `svc-notifications`, `svc-webhooks` (`TRANSITIVE_UPSTREAM`, hop 3); `svc-analytics` (`DATA_FEED_CONSUMER`, hop 3).
  - **Governance & Citations**: `5` governing ADRs (`ADR-004`, `ADR-005`, `ADR-006`, `ADR-008`, `ADR-010`), `5` applicable requirements (`REQ-001`, `REQ-002`, `REQ-003`, `REQ-005`, `REQ-006`), and verifiable Markdown citations.
  - **Built-In Topological Traps**: `svc-orders <-> svc-notifications` cycle (`MATCH TRAIL`), `svc-analytics` async `DATA_FEED`, `svc-auth` hub (in-degree `10`), and `svc-billing -> api-payments-charge-v1` legacy `AT_RISK` call.

## Locked Invariants (Non-Negotiable)

### Platform, Regional Topology & Compute Floor

| Dimension | Locked Workshop Default & Rule |
| :--- | :--- |
| **Dialect & Edition (`INV-01`)** | **GoogleSQL** (`GOOGLE_STANDARD_SQL`) and **Cloud Spanner Enterprise Edition** (`ENTERPRISE`) only (unlocks Spanner Graph, FTS, ScaNN Vector Search, Locality Groups, Managed Autoscaler). |
| **Primary & Only DB/App Region (Bangkok)** | `asia-southeast3` (`regional-asia-southeast3`, 3-zone sync quorum `a/b/c`, 99.99% SLA, Thailand PDPA residency). Single Spanner instance `sdlc-kg-enterprise`, DB `sdlc_knowledge_graph`, same-region Bangkok backups/PITR, Cloud Run MCP, user-managed Secret Manager, GCS & Dataflow. **NO 2-region Spanner DR.** |
| **Companion AI/Security Region (Singapore)** | `asia-southeast1` (`~25 ms` RTT) — fallback **ONLY** for services unavailable in Bangkok: Vertex AI (`VERTEX_AI_LOCATION=asia-southeast1`), Model Armor (`modelarmor.asia-southeast1.rep.googleapis.com`), and Cloud DLP / Sensitive Data Protection. |
| **Vertex AI Endpoint Scope** | Use regional `asia-southeast1` for `gemini-embedding-001` and `gemini-2.5-flash`. **Never** target `global` for `gemini-embedding-001` (`global` fallback applies strictly to `gemini-2.5-flash` LLM synthesis). |
| **Compute & Backup Floor** | Workshop sandbox uses `100` PU (`--default-backup-schedule-type=NONE`); **production requires `>= 1,000` PUs** (1 node) + same-region Bangkok `AUTOMATIC` backups + 7-day PITR. |

### Schema, Property Graph, FTS & Vector Invariants (`INV-02`–`INV-09`)

- **Unique Aliases & Full PK Parity (`INV-02`–`INV-04`)**: Every element in `CREATE PROPERTY GRAPH SdlcGraph` has a unique alias (explicit `AS` aliases for dual-projected edges); `SOURCE KEY`/`DESTINATION KEY REFERENCES` match full composite PK arity and SQL types; no property name has conflicting SQL types graph-wide.
- **Explicit View Security & Keys (`INV-05`)**: Every SQL view declares explicit `SQL SECURITY INVOKER` (or `DEFINER` for parameterized views), and every view-backed graph element declares explicit `KEY (...)`.
- **No Same-Table Dual Cascade (`INV-06`)**: Never combine `INTERLEAVE IN PARENT T ON DELETE CASCADE` with a self-referencing cascading FK to `T` on the same table (use standalone dual-FK for `ServiceDependsOnService`/`AdrSupersedesAdr`, or `INTERLEAVE IN PARENT` + `NOT ENFORCED` FK for `ServiceDependsOnAlgo`).
- **Isolated Schemaless Graph (`INV-07`)**: Keep `DYNAMIC LABEL` / `DYNAMIC PROPERTIES` tables in a separate graph (`SdlcDynamicGraph`) without mixing static node/edge tables; enforce lowercase labels/JSON keys via `CHECK`.
- **Vector & ScaNN Discipline (`INV-08`)**: Declare embeddings as `ARRAY<FLOAT32>(vector_length=>768)`. Require `WHERE embedding IS NOT NULL` on `CREATE VECTOR INDEX` and `APPROX_COSINE_DISTANCE` queries, isolate ANN inside a single-table/single-node CTE (no general `JOIN` inside the ANN `SELECT` block), and never run ANN on edges.
- **Read-Only FTS & No `ROW_NUMBER()` (`INV-09`)**: Run all FTS and hybrid queries in **read-only** transactions. Cloud Spanner lacks general `ROW_NUMBER()` — compute RRF (`k=60`) via `UNNEST(ARRAY(SELECT ... ORDER BY ... LIMIT k)) AS x WITH OFFSET AS rank`.

### MCP Toolbox & Dual-Layer Security Contract

- **Curated Parameterized SQL Only**: Use parameterized `spanner-sql` tools with `readOnly: true` on read paths — **no raw `spanner-execute-sql` (`execute_sql`)** and **no `templateParameters`**.
- **No `databaseRole` in MCP `tools.yaml`**: `type: spanner` sources accept **only** `project`, `instance`, `database`, and `dialect` (never `databaseRole` or `database_role`).
- **Secretless Vertex AI Embeddings**: Configure `embeddingModels.gemini-embedder` with `project` + regional `location: ${VERTEX_AI_LOCATION}` via ADC / Cloud Run Workload Identity (never an active static `apiKey` in Cloud Run).
- **Separated Authorization Layers**: Cloud Run MCP Toolbox personas use least-privilege DB IAM (`roles/spanner.databaseReader` / `databaseUser` + sliced `personas/tools.<persona>.yaml` + OIDC `authServices` `@caller_email`), kept strictly separate from dedicated `sa-fgac-*` accounts (`roles/spanner.fineGrainedAccessUser` + conditional `roles/spanner.databaseRoleUser`) for `database_role`-aware clients (`spanner.googleapis.com/mcp`, SDKs, `gcloud --database-role`).

### Property Graphs & Agent Personas Quick Reference

- **Three Property Graphs**: `SdlcGraph` (schematized Path A + `ChunkCites*View` Path B bridge views), `SdlcDynamicGraph` (isolated `DYNAMIC LABEL` / `DYNAMIC PROPERTIES` comparison in `02_path_b_schemaless_comparison.sql`), and `SdlcAlgoGraph` (table-backed graph over `ServiceDependsOnAlgo` for Data Boost `PageRank` / `WCC` / `ShortestPath`).
- **Three Isolated Personas**: `impact-analyst` (`tools.impact-analyst.yaml`, `sa-impact-analyst`, `roles/spanner.databaseReader`, FGAC `sdlc_readonly_role`), `adr-curator` (`tools.adr-curator.yaml`, `sa-adr-curator`, `roles/spanner.databaseUser`, FGAC `sdlc_adr_writer_role`), and `security-auditor` (`tools.security-auditor.yaml`, `sa-security-auditor`, `roles/spanner.databaseReader`, FGAC `sdlc_security_auditor_role`).

## Repository Map (`starter/` vs `solution/`)

Solve hands-on lab exercises **exclusively inside `starter/`**; leave `data/`, `solution/`, and test fixtures intact unless explicitly updating the reference build.

| Path | Ownership & Contents |
| :--- | :--- |
| `starter/` | **Participant Workspace**: `starter/README.md` + `15` `TODO(Lab 1–8)` stubs across `starter/sql/` (`10` files), `starter/pipelines/` (`2` loaders), `starter/mcp/` (`tools.yaml`, `adk_multi_agent_demo.py`), and `starter/tools/customer_scale_worksheet.json`. |
| `solution/` | **Verified Reference Implementation**: `solution/sql/` (`01`–`11`, `03a`–`03d`, `04b`, `fixtures/`), `solution/pipelines/` (`5` ingestion & offline Hero replay scripts), `solution/mcp/` (`tools.yaml`, `3` rendered `personas/*.yaml`, IAM/Model Armor script, ADK demo), `solution/tools/sizing_calculator.py`. |
| `data/` | **Locked Shared Corpus**: `data/structured/` (Zero-Docker simulator `no_docker_neo4j_sim.py`, optional Neo4j CE Docker/APOC scripts, `5` node CSVs, `10` edge CSVs, JSONLs) and `data/unstructured/` (`45` Markdown SDLC docs). |
| `docs/` | Facilitator guide (`docs/00_facilitator_guide.md`) and Module 1–8 lab guides (`docs/01_*.md`–`docs/08_*.md`). |
| `labs/` | Parameterized live/dry-run Hero Query runner (`labs/run_hero_query.py`). |
| `scripts/` | GCP & local verification helpers (`preflight_check.sh`, `provision_workshop_env.sh`, `run_live_sql.py`, `setup_antigravity_skills.sh`, `verify_lab.py`, `verify_live_gcp.sh`, `verify_zero_residual.sh`). |
| `tests/` | `8` stdlib `unittest` modules (`tests/test_*.py`) covering invariants, dataset parity, MCP RBAC, sizing, skills, starter stubs, docs consistency, and Zero-Docker/ops scripts. |
| `.antigravity/` | Antigravity MCP configuration (`.antigravity/mcp_config.json`, `.antigravity/README.md`). |
| `.agents/` | Always-on workspace rule (`.agents/rules/spanner-graph-workshop.md`) and `8` modular skills (`.agents/skills/*`). |

### Hands-On Lab Progression (`starter/` `TODO(Lab 1–8)`)

| Lab | Focus | Starter Stub(s) |
| :-: | :--- | :--- |
| **1** | Neo4j CE Export & openCypher → ISO GQL Parity | `starter/sql/09_opencypher_to_gql_cookbook.sql` |
| **2** | Two-Path Corpus & Bangkok Single-Region Parity | `starter/sql/10_validate_migration_parity.sql` |
| **3** | Schema, Post-Load Indexes & Path A/B Ingestion | `starter/sql/{01,03}*.sql`, `starter/pipelines/{neo4j_to_spanner_loader,unstructured_graphrag_ingest}.py` |
| **4** | Multi-Model Search & Data Boost Graph Algorithms | `starter/sql/05_search_strategies_benchmark.sql`, `starter/sql/08_graph_algorithms_criticality.sql` |
| **5** | Hybrid RRF/RSF & Hero Blast-Radius GraphRAG | `starter/sql/06_hybrid_rrf_rsf_filtered.sql`, `starter/sql/07_hero_api_blast_radius_graphrag.sql` |
| **6** | MCP Toolbox, Per-Agent RBAC, FGAC & Model Armor | `starter/sql/04_fgac_roles_views.sql`, `starter/mcp/{tools.yaml,adk_multi_agent_demo.py}` |
| **7** | Bangkok Single-Region Enterprise Sizing & 3-Store TCO | `starter/tools/customer_scale_worksheet.json` |
| **8** | Observability, Studio Viz, Pre-Splitting & Teardown | `starter/sql/11_observability_and_studio_viz.sql` |

## Standard Verification & Lifecycle Commands (All 16 `Makefile` Targets)

Always confirm active GCP project/region and obtain explicit user approval before running `make provision-gcp`, `make verify-live`, `make cleanup-gcp`, `make cleanup-all-gcp`, or any live Cloud Spanner write.

```bash
make help                                             # List all 16 Makefile targets & variables
make preflight                                        # Fast 5-second attendee readiness check (STRICT_GCP=1 for live)
make lab1-no-docker                                   # Zero-Docker Lab 1 Neo4j CE & APOC simulator + parity check
make install                                          # Create PEP-668-safe .venv & install core pipeline SDKs
make install-mcp                                      # Optional Lab 6 live ADK/MCP & Model Armor packages
make test                                             # Run all 8 offline stdlib unittest modules
make validate-skills                                  # Audit all 8 Antigravity skills, frontmatter & links
make verify-starter-stubs                             # Confirm all 15 starter stubs fail until completed
make TRACK=starter verify-lab LAB=1                   # Check starter/ Lab 1..8 progress (MODE=express for 4h Track A)
make sizing PRESET=all REGION=asia-southeast3         # Run verified 2026 Bangkok single-region sizing & 3-store TCO
make dry-run-pipelines                                # Stdlib dry-run for Path A & Path B ingestion loaders
make demo-mcp-mock                                    # Offline persona RBAC & prompt-injection fail-closed demo
make provision-gcp                                    # [CONFIRM FIRST] Provision Bangkok 100-PU Enterprise sandbox
make verify-live                                      # [CONFIRM FIRST] Run live Bangkok Cloud Spanner verification
make cleanup-gcp                                      # [CONFIRM FIRST] Delete Bangkok sandbox instance & same-region backups
make cleanup-all-gcp                                  # [CONFIRM FIRST] Full teardown (Bangkok MCP/Secrets/SAs/GCS/Spanner + SG DLP/Model Armor) + zero-residual audit
```

## Antigravity Skill Routing Table (`.agents/skills/`)

Skills **#1–#7** are **100% production-reusable** Cloud Spanner skills (zero workshop hardcoding); Skill **#8** is the workshop navigator. Read the matching `SKILL.md` before authoring DDL, GQL, search, pipelines, MCP configs, or sizing models.

| # | Skill | Scope | Primary Labs | Load When Working On... |
| :-: | :--- | :---: | :---: | :--- |
| **1** | [spanner-enterprise-platform-ops](.agents/skills/spanner-enterprise-platform-ops/SKILL.md) | Production | 2, 7, 8 | Editions, CUDs, Bangkok single-region 3-zone topology (`99.99%` SLA), dialects, KV, Queues, CLI/SDKs/Emulator, autoscaler deployment, same-region PITR, backups, drop protection. |
| **2** | [spanner-schema-and-data-modeling](.agents/skills/spanner-schema-and-data-modeling/SKILL.md) | Production | 3 | Split-safe PKs, `INTERLEAVE IN PARENT` vs `INTERLEAVE IN`, secondary/covering/expression indexes, FKs (dual-cascade ban), `CREATE VIEW`/`SECURE_CONTEXT()`, Locality Groups, TTL, Change Streams DDL. |
| **3** | [spanner-graph-modeling-and-gql](.agents/skills/spanner-graph-modeling-and-gql/SKILL.md) | Production | 3, 4, 5, 8 | `CREATE PROPERTY GRAPH`, 8 graph invariants, dual-projected edges, view-backed & `DYNAMIC LABEL` graphs, ISO GQL (`TRAIL`, `IS_FIRST`, `factorized_mode`), Data Boost Graph Algorithms (`PageRank`, `WCC`, `ShortestPath`), `SAFE_TO_JSON` Studio viz. |
| **4** | [spanner-search-ai-and-graphrag](.agents/skills/spanner-search-ai-and-graphrag/SKILL.md) | Production | 4, 5 | Vertex AI `CREATE MODEL`/`ML.PREDICT` embeddings (`asia-southeast1`), PDML backfills, FTS (`SEARCH`, Custom Dictionaries, fuzzy n-grams), Exact KNN, ScaNN `CREATE VECTOR INDEX`, RRF (`UNNEST(ARRAY(...)) WITH OFFSET`)/RSF/filtered hybrid search, GraphRAG. |
| **5** | [spanner-migration-and-neo4j-playbook](.agents/skills/spanner-migration-and-neo4j-playbook/SKILL.md) | Production | 1 | 7-stage migrations (SMT, Datastream+Dataflow CDC, DVT, cutover), Neo4j CE APOC export & nodes-before-edges loading, openCypher v9 → ISO GQL translation, Elasticsearch → Spanner FTS. |
| **6** | [spanner-mcp-toolbox-and-agent-security](.agents/skills/spanner-mcp-toolbox-and-agent-security/SKILL.md) | Production | 6 | Antigravity MCP Store/`mcp_config.json`, ADK `ToolboxToolset`/`SpannerToolset`, Spanner Remote MCP, `tools.yaml` (no `databaseRole`, `readOnly: true`, Vertex AI `embeddingModels`, OIDC `authServices`, persona isolation), FGAC `GRANT`/conditional `databaseRoleUser` IAM, Model Armor, CMEK, VPC-SC. |
| **7** | [spanner-performance-observability-and-sizing](.agents/skills/spanner-performance-observability-and-sizing/SKILL.md) | Production | 7, 8 | Query Optimizer/`ANALYZE`, execution plans, `SPANNER_SYS` Insights (Query/Lock/Txn/Read), Key Visualizer, pre-splitting, CPU targets (65%/45%), node capacity & Bangkok/Singapore 3-store TCO. |
| **8** | [sdlc-graphrag-workshop-guide](.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md) | Workshop | 2 (all support) | Acme Commerce entity/edge counts, Hero `api-payments-charge-v2` 7-row blast-radius contract & citations, `starter/` `TODO(Lab 1–8)` checkpoints, `Makefile` commands, and lab troubleshooting. |
