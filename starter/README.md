# Customer Starter Project — Cloud Spanner Enterprise SDLC Knowledge Graph & GraphRAG Workshop

Welcome to your hands-on participant workspace (`starter/`) for **Use Case 4**: consolidating **Neo4j Community Edition + a separate Vector Database + a separate Keyword Search cluster** into a single ACID **Cloud Spanner Enterprise** platform (**Spanner Graph + Full-Text Search + ScaNN Vector Search + MCP Toolbox for Databases**) for the fictional **Acme Commerce Platform**.

Work **exclusively inside `starter/`** using **Antigravity IDE** and the **8 bundled `.agents/skills/`** (7 production-reusable Cloud Spanner engineering skills + 1 Acme Commerce workshop navigator). Leave `data/` and `solution/` intact so automated lab verification and deterministic Hero blast-radius replay stay reproducible.

- **Workspace Orientation & Locked Invariants**: [../GEMINI.md](../GEMINI.md)
- **Workshop Blueprint & Delivery Tracks**: [../WORKSHOP_CONTENT_PLAN.md](../WORKSHOP_CONTENT_PLAN.md)
- **Single Source of Truth**: [../WORKSHOP_SPEC.json](../WORKSHOP_SPEC.json)
- **Always-On Antigravity Guardrails**: [../.agents/rules/spanner-graph-workshop.md](../.agents/rules/spanner-graph-workshop.md)

> **Zero-Docker vs. With-Docker:** No Docker Desktop needed! Start with `make preflight` and `make lab1-no-docker` — every offline simulator, verifier, pipeline `--dry-run`, and `--mock` security suite runs on pure Python 3.10+ standard library. Optional Docker Compose (`data/structured/docker-compose.yml`) is available only if you want a live local Neo4j 5 CE container in Track B.

---

## 1. Quickstart (5 Minutes — Zero-Docker Ready)

### Step 0: Run Preflight & Zero-Docker Lab 1 Simulator

```bash
# 1. Fast 5-second attendee readiness check (Python, regional defaults, Zero-Docker confirmation)
make preflight

# 2. Run Lab 1 Zero-Docker Neo4j CE & APOC simulator (WALK vs TRAIL + 15/15 SHA-256 APOC parity)
make lab1-no-docker
```

### Step 1: Confirm Your Starter Stubs Are Armed (Offline, Zero GCP Required)

Every `starter/` exercise intentionally fails its lab verification check until you complete its `TODO(Lab N)` markers:

```bash
# Confirm all 15 starter exercises across Labs 1-8 are properly armed
# (all starter checks fail until solved; reference solution/ checks pass 100%)
make verify-starter-stubs
```

### Step 2: Check Your Target Delivery Track (`MODE=express` vs. `MODE=full`)

| Track | Duration | `verify-lab` Mode | Recommended Scope Inside `starter/` |
| :--- | :---: | :---: | :--- |
| **Track A — 4-Hour Express** | 240 min | `MODE=express` | Labs 1–8 core exercises: `09` (Exercises 1–3), `10` (Tiers 1–3), `01` & `03` (skip 2 Python loaders), `05` (skip `08` Data Boost), plus full Labs 5–8 (`06`, `07`, `04`, `tools.yaml`, `adk_multi_agent_demo.py`, `customer_scale_worksheet.json`, `11`). |
| **Track B — 1-Day Deep-Dive** | 480 min | `MODE=full` *(default)* | Complete **all 15 `starter/` files** (including both Python loaders in Lab 3, Data Boost `PageRank` in Lab 4, full Tier 4 SHA-256 parity in Lab 2, and live Bangkok GCP execution). |

### Step 3: Solve Lab `N` → Verify Offline → Compare with `solution/`

```bash
# Track B (Full 15-file contract, default MODE=full): verify any single lab (replace N with 1..8)
make TRACK=starter verify-lab LAB=1

# Track A (4-Hour Express): verify Track A subset cleanly without hitting Track B-only checks
make TRACK=starter MODE=express verify-lab LAB=1
make TRACK=starter MODE=express verify-lab LAB=all

# Verify all 8 labs once full Track B is completed
make TRACK=starter verify-lab LAB=all

# Compare against the verified reference implementation at any time
make TRACK=solution verify-lab LAB=all
```

### Regional Architecture Defaults (Locked in `WORKSHOP_SPEC.json`)

| Component | Default Region / Setting | Why It Matters |
| :--- | :--- | :--- |
| **Primary Cloud Spanner Enterprise** | Bangkok (`asia-southeast3` / `regional-asia-southeast3`) | 3-zone sync quorum (`a/b/c`), 99.99% SLA, Thailand PDPA data residency; instance `sdlc-kg-enterprise`, DB `sdlc_knowledge_graph` (`GOOGLE_STANDARD_SQL`). |
| **Vertex AI Embeddings & LLM** | Singapore (`asia-southeast1`, ~25 ms RTT from Bangkok) | `gemini-embedding-001` (768-dim `ARRAY<FLOAT32>`) **requires a regional endpoint** (NEVER `global`); `gemini-2.5-flash` for LLM synthesis. |
| **Model Armor & DR Backup Copy** | Singapore (`asia-southeast1`) | Regional Model Armor (`modelarmor.asia-southeast1.rep.googleapis.com`) + cross-region Spanner backup copy (`$0.08/GiB` egress). |
| **Compute Floor** | Workshop: `100` PU (`~$1.15` / 8 hr) | **Production requires `>= 1,000` PUs (`1` node)** to unlock dedicated zone tasks, Managed Autoscaler, and Remote Functions. |

---

## 2. Lab-by-Lab `TODO` Checklist (All 8 Labs / 15 Starter Files)

Check off each file as you complete its `TODO(Lab N)` exercises and pass `make TRACK=starter verify-lab LAB=N`:

| Done | Lab | Starter File (`TODO(Lab N)`) | Key Exercise Objectives | Reference (`solution/`) | Verify Command |
| :-: | :-: | :--- | :--- | :--- | :--- |
| [ ] | **1** | [sql/09_opencypher_to_gql_cookbook.sql](sql/09_opencypher_to_gql_cookbook.sql) | Translate 5 Neo4j openCypher queries to ISO GQL (`MATCH TRAIL` cycle safety on `svc-orders <-> svc-notifications`, `FOR`, `SAFE_TO_JSON`, `RETURN ORDER BY ... OFFSET ... LIMIT`, `OPTIONAL MATCH ... WHERE`). | [09_opencypher_to_gql_cookbook.sql](../solution/sql/09_opencypher_to_gql_cookbook.sql) | `make TRACK=starter verify-lab LAB=1` |
| [ ] | **2** | [sql/10_validate_migration_parity.sql](sql/10_validate_migration_parity.sql) | Complete 4-tier Path A + Path B parity assertions (`68` nodes, `174` explicit rows, `60` dual-projected edges = `234` logical, `45` Markdown docs, `svc-auth` hub = `10`, `DATA_FEED`, legacy `AT_RISK`, SHA-256 row hashes). | [10_validate_migration_parity.sql](../solution/sql/10_validate_migration_parity.sql) | `make TRACK=starter verify-lab LAB=2` |
| [ ] | **3** | [sql/01_path_a_schematized.sql](sql/01_path_a_schematized.sql) | Complete `ServiceCallsApi` (`INTERLEAVE IN PARENT Services ON DELETE CASCADE`), standalone dual-FK `ServiceDependsOnService` (`INV-06`), `ChunkCitesApiView` (`SQL SECURITY INVOKER`), and `CREATE OR REPLACE PROPERTY GRAPH SdlcGraph`. | [01_path_a_schematized.sql](../solution/sql/01_path_a_schematized.sql) | `make TRACK=starter verify-lab LAB=3` |
| [ ] | **3** | [sql/03_indexes_fts_vector_postload.sql](sql/03_indexes_fts_vector_postload.sql) | Add post-load `IdxApiCalledByService` (`STORING`, `INTERLEAVE IN Apis`), `IdxDocumentChunksSearch` (`CREATE SEARCH INDEX`), and ScaNN `IdxDocumentChunksEmbedding` (`WHERE embedding IS NOT NULL`). | [03_indexes_fts_vector_postload.sql](../solution/sql/03_indexes_fts_vector_postload.sql) | `make TRACK=starter verify-lab LAB=3` |
| [ ] | **3** | [pipelines/neo4j_to_spanner_loader.py](pipelines/neo4j_to_spanner_loader.py) | Implement `load_graph()` nodes-before-edges batched mutations (`< 80,000` mutations/commit; `500`-row chunks; stdlib `--dry-run`). | [neo4j_to_spanner_loader.py](../solution/pipelines/neo4j_to_spanner_loader.py) | `make TRACK=starter verify-lab LAB=3` |
| [ ] | **3** | [pipelines/unstructured_graphrag_ingest.py](pipelines/unstructured_graphrag_ingest.py) | Implement `extract_chunks_and_mentions()` Markdown H2 chunking + regex entity extraction (`api-*`, `svc-*`, `ADR-*`, `REQ-*`, `team-*`) + stdlib `--dry-run`. | [unstructured_graphrag_ingest.py](../solution/pipelines/unstructured_graphrag_ingest.py) | `make TRACK=starter verify-lab LAB=3` |
| [ ] | **4** | [sql/05_search_strategies_benchmark.sql](sql/05_search_strategies_benchmark.sql) | Complete FTS (`SdlcSynonymDict` + `enhance_query => TRUE`), Fuzzy N-grams (`SEARCH_NGRAMS`/`SCORE_NGRAMS`, `LIMIT 10000`), and CTE-isolated ScaNN `APPROX_COSINE_DISTANCE` (`WHERE embedding IS NOT NULL`). | [05_search_strategies_benchmark.sql](../solution/sql/05_search_strategies_benchmark.sql) | `make TRACK=starter verify-lab LAB=4` |
| [ ] | **4** | [sql/08_graph_algorithms_criticality.sql](sql/08_graph_algorithms_criticality.sql) | Define `ServiceDependsOnAlgo` (`INTERLEAVE IN PARENT Services` + `NOT ENFORCED` FK), table-backed `SdlcAlgoGraph`, and Data Boost `EXPORT DATA ... CALL PER () PageRank`. | [08_graph_algorithms_criticality.sql](../solution/sql/08_graph_algorithms_criticality.sql) | `make TRACK=starter verify-lab LAB=4` |
| [ ] | **5** | [sql/06_hybrid_rrf_rsf_filtered.sql](sql/06_hybrid_rrf_rsf_filtered.sql) | Implement server-side RRF (`k=60`) with `UNNEST(ARRAY(...)) WITH OFFSET` (NEVER `ROW_NUMBER()`) fusing ScaNN ANN and FTS ranks. | [06_hybrid_rrf_rsf_filtered.sql](../solution/sql/06_hybrid_rrf_rsf_filtered.sql) | `make TRACK=starter verify-lab LAB=5` |
| [ ] | **5** | [sql/07_hero_api_blast_radius_graphrag.sql](sql/07_hero_api_blast_radius_graphrag.sql) | Complete the Hero API (`api-payments-charge-v2`) GraphRAG query: CTE-isolated ScaNN ANN + RRF + `MATCH TRAIL` + `DATA_FEED_CONSUMER` separation + 5 ADRs / 5 REQs / citations + legacy v1 `AT_RISK` flag. | [07_hero_api_blast_radius_graphrag.sql](../solution/sql/07_hero_api_blast_radius_graphrag.sql) | `make TRACK=starter verify-lab LAB=5` |
| [ ] | **6** | [sql/04_fgac_roles_views.sql](sql/04_fgac_roles_views.sql) | Create `PublicApiCatalogView` (`SQL SECURITY INVOKER` + explicit `CAST`) and least-privilege `GRANT SELECT` statements for `sdlc_readonly_role` (column-restricted `Owners`). | [04_fgac_roles_views.sql](../solution/sql/04_fgac_roles_views.sql) | `make TRACK=starter verify-lab LAB=6` |
| [ ] | **6** | [mcp/tools.yaml](mcp/tools.yaml) | Configure secretless Vertex AI `embeddingModels` (`gemini-embedding-001` in `asia-southeast1`), OIDC `authServices` (`@caller_email`), security controls (`readOnly`, `valueFromParam`/`embeddedBy`, anchored `allowedValues`) on the 5 pre-scaffolded curated `spanner-sql` tools, and 3 isolated persona `toolsets`. | [tools.yaml](../solution/mcp/tools.yaml) | `make TRACK=starter verify-lab LAB=6` |
| [ ] | **6** | [mcp/adk_multi_agent_demo.py](mcp/adk_multi_agent_demo.py) | Implement `run_persona_query()` using `load_persona_config()` to slice your own `starter/mcp/tools.yaml` allowlists and enforce OIDC fail-closed binding against prompt injection (`--mock`). | [adk_multi_agent_demo.py](../solution/mcp/adk_multi_agent_demo.py) | `make TRACK=starter verify-lab LAB=6` |
| [ ] | **7** | [tools/customer_scale_worksheet.json](tools/customer_scale_worksheet.json) | Plug in your real enterprise scale (`nodes`, `edges`, `chunks`, `qps`, `cold_hdd_pct`, Bangkok primary + Singapore DR), run `sizing_calculator.py --worksheet`, then set `todo_status` to `"COMPLETED"`. | [sizing_calculator.py](../solution/tools/sizing_calculator.py) | `make TRACK=starter verify-lab LAB=7` |
| [ ] | **8** | [sql/11_observability_and_studio_viz.sql](sql/11_observability_and_studio_viz.sql) | Author Spanner Studio `RETURN SAFE_TO_JSON(p)` path visualization + `SPANNER_SYS` Query, Lock (microseconds `/ 1000000.0`), and Split-Point introspection queries. | [11_observability_and_studio_viz.sql](../solution/sql/11_observability_and_studio_viz.sql) | `make TRACK=starter verify-lab LAB=8` |

---

## 3. Antigravity IDE Prompt Suggestions & Skill Routing per Lab

Open this repository in **Antigravity IDE** — the workspace automatically loads `.agents/rules/spanner-graph-workshop.md` and the **8 skills** under `.agents/skills/`. Use the prompts below to learn each Cloud Spanner Enterprise pattern hands-on while staying aligned with the locked invariants.

### Lab 1 — Neo4j CE Export & openCypher → ISO GQL Parity
- **Primary Skill**: [spanner-migration-and-neo4j-playbook](../.agents/skills/spanner-migration-and-neo4j-playbook/SKILL.md)
- **Support Skills**: [spanner-graph-modeling-and-gql](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md), [sdlc-graphrag-workshop-guide](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md)
- **Starter File**: [sql/09_opencypher_to_gql_cookbook.sql](sql/09_opencypher_to_gql_cookbook.sql)
- **Suggested Antigravity Prompt**:
  > *"Using the `spanner-migration-and-neo4j-playbook` skill, help me complete all 5 `TODO(Lab 1)` openCypher-to-ISO-GQL translations in `starter/sql/09_opencypher_to_gql_cookbook.sql` against `SdlcGraph`. Explain why default `WALK` loops on `svc-orders <-> svc-notifications`, why `FOR` variables must be filtered in the outermost `WHERE`, and why `SAFE_TO_JSON` is safer than `TO_JSON` when `TOKENLIST` columns exist."*
- **Verify**:
  ```bash
  make TRACK=starter verify-lab LAB=1
  ```

### Lab 2 — Two-Path SDLC Corpus & Bangkok/Singapore Regional Architecture
- **Primary Skills**: [sdlc-graphrag-workshop-guide](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md), [spanner-enterprise-platform-ops](../.agents/skills/spanner-enterprise-platform-ops/SKILL.md)
- **Support Skill**: [spanner-schema-and-data-modeling](../.agents/skills/spanner-schema-and-data-modeling/SKILL.md)
- **Starter File**: [sql/10_validate_migration_parity.sql](sql/10_validate_migration_parity.sql)
- **Suggested Antigravity Prompt**:
  > *"Using the `sdlc-graphrag-workshop-guide` and `spanner-enterprise-platform-ops` skills, complete the `TODO(Lab 2)` 4-tier parity checks in `starter/sql/10_validate_migration_parity.sql` (verifying 68 nodes, 174 explicit rows, 60 dual-projected edges, 45 Markdown docs, `svc-auth` hub in-degree 10, `DATA_FEED`, and legacy `AT_RISK`). Also summarize why Bangkok (`regional-asia-southeast3`) + Singapore (`asia-southeast1`) is our PDPA-compliant topology."*
- **Verify**:
  ```bash
  make TRACK=starter verify-lab LAB=2
  ```

### Lab 3 — Schema Design, Post-Load Indexes & Path A/B Ingestion
- **Primary Skills**: [spanner-schema-and-data-modeling](../.agents/skills/spanner-schema-and-data-modeling/SKILL.md), [spanner-graph-modeling-and-gql](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md)
- **Support Skills**: [spanner-search-ai-and-graphrag](../.agents/skills/spanner-search-ai-and-graphrag/SKILL.md), [spanner-migration-and-neo4j-playbook](../.agents/skills/spanner-migration-and-neo4j-playbook/SKILL.md)
- **Starter Files**: [sql/01_path_a_schematized.sql](sql/01_path_a_schematized.sql), [sql/03_indexes_fts_vector_postload.sql](sql/03_indexes_fts_vector_postload.sql), [pipelines/neo4j_to_spanner_loader.py](pipelines/neo4j_to_spanner_loader.py), [pipelines/unstructured_graphrag_ingest.py](pipelines/unstructured_graphrag_ingest.py)
- **Suggested Antigravity Prompts**:
  > *"Using `spanner-schema-and-data-modeling` and `spanner-graph-modeling-and-gql`, complete the 4 `TODO(Lab 3)` sections in `starter/sql/01_path_a_schematized.sql` (`ServiceCallsApi` interleaving, standalone dual-FK `ServiceDependsOnService` obeying `INV-06`, `ChunkCitesApiView` with `SQL SECURITY INVOKER`, and `CREATE OR REPLACE PROPERTY GRAPH SdlcGraph`) and the 3 post-load indexes in `starter/sql/03_indexes_fts_vector_postload.sql`."*
  >
  > *"Now complete `load_graph()` in `starter/pipelines/neo4j_to_spanner_loader.py` (nodes-before-edges, `< 80,000` mutations/commit) and `extract_chunks_and_mentions()` in `starter/pipelines/unstructured_graphrag_ingest.py` (Markdown H2 chunking + regex mentions), keeping `--dry-run` 100% Python stdlib-only."*
- **Verify**:
  ```bash
  python3 starter/pipelines/neo4j_to_spanner_loader.py --dry-run
  python3 starter/pipelines/unstructured_graphrag_ingest.py --dry-run
  make TRACK=starter verify-lab LAB=3
  ```

### Lab 4 — Multi-Model Search Benchmarks & Data Boost Graph Algorithms
- **Primary Skills**: [spanner-search-ai-and-graphrag](../.agents/skills/spanner-search-ai-and-graphrag/SKILL.md), [spanner-graph-modeling-and-gql](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md)
- **Support Skills**: [spanner-performance-observability-and-sizing](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md), [sdlc-graphrag-workshop-guide](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md)
- **Starter Files**: [sql/05_search_strategies_benchmark.sql](sql/05_search_strategies_benchmark.sql), [sql/08_graph_algorithms_criticality.sql](sql/08_graph_algorithms_criticality.sql)
- **Suggested Antigravity Prompt**:
  > *"Using `spanner-search-ai-and-graphrag` and `spanner-graph-modeling-and-gql`, complete the `TODO(Lab 4)` queries in `starter/sql/05_search_strategies_benchmark.sql` (FTS with `SdlcSynonymDict` + `enhance_query => TRUE`, fuzzy `SEARCH_NGRAMS`/`SCORE_NGRAMS` with `LIMIT 10000`, and single-table CTE ScaNN `APPROX_COSINE_DISTANCE` with `WHERE embedding IS NOT NULL`) and `starter/sql/08_graph_algorithms_criticality.sql` (`ServiceDependsOnAlgo` with `NOT ENFORCED` FK, table-backed `SdlcAlgoGraph`, and Data Boost `EXPORT DATA ... CALL PER () PageRank`)."*
- **Verify**:
  ```bash
  make TRACK=starter verify-lab LAB=4
  ```

### Lab 5 — Hybrid Retrieval (RRF/RSF/Filtered) & Hero Blast-Radius GraphRAG
- **Primary Skills**: [spanner-search-ai-and-graphrag](../.agents/skills/spanner-search-ai-and-graphrag/SKILL.md), [spanner-graph-modeling-and-gql](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md)
- **Support Skills**: [sdlc-graphrag-workshop-guide](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md), [spanner-performance-observability-and-sizing](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md)
- **Starter Files**: [sql/06_hybrid_rrf_rsf_filtered.sql](sql/06_hybrid_rrf_rsf_filtered.sql), [sql/07_hero_api_blast_radius_graphrag.sql](sql/07_hero_api_blast_radius_graphrag.sql)
- **Suggested Antigravity Prompt**:
  > *"Using `spanner-search-ai-and-graphrag` and `spanner-graph-modeling-and-gql`, implement `TODO(Lab 5)` server-side RRF (`k=60`) via `UNNEST(ARRAY(...)) WITH OFFSET` (zero `ROW_NUMBER()`) in `starter/sql/06_hybrid_rrf_rsf_filtered.sql`, then finish the Hero API (`api-payments-charge-v2`) GraphRAG query in `starter/sql/07_hero_api_blast_radius_graphrag.sql` with CTE-isolated ScaNN ANN, `MATCH TRAIL`, `DATA_FEED_CONSUMER` separation, governing ADRs/REQs, Markdown citations, and `has_legacy_v1_risk`."*
- **Verify**:
  ```bash
  make TRACK=starter verify-lab LAB=5
  python3 solution/pipelines/hero_blast_radius_offline.py \
    --api-id api-payments-charge-v2 \
    --check-fixture solution/sql/fixtures/07_hero_expected_rows.json
  ```

### Lab 6 — MCP Toolbox for Databases, Per-Agent RBAC, FGAC & Model Armor
- **Primary Skill**: [spanner-mcp-toolbox-and-agent-security](../.agents/skills/spanner-mcp-toolbox-and-agent-security/SKILL.md)
- **Support Skills**: [spanner-schema-and-data-modeling](../.agents/skills/spanner-schema-and-data-modeling/SKILL.md), [sdlc-graphrag-workshop-guide](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md)
- **Starter Files**: [sql/04_fgac_roles_views.sql](sql/04_fgac_roles_views.sql), [mcp/tools.yaml](mcp/tools.yaml), [mcp/adk_multi_agent_demo.py](mcp/adk_multi_agent_demo.py)
- **Suggested Antigravity Prompt**:
  > *"Using `spanner-mcp-toolbox-and-agent-security`, complete all three `TODO(Lab 6)` files: (1) `PublicApiCatalogView` (`SQL SECURITY INVOKER`) and `sdlc_readonly_role` grants in `starter/sql/04_fgac_roles_views.sql`, (2) secretless regional Vertex AI `embeddingModels` (`asia-southeast1`), OIDC `authServices`, curated `spanner-sql` tools, and 3 isolated persona `toolsets` in `starter/mcp/tools.yaml` (never adding `databaseRole` or `spanner-execute-sql`), and (3) `run_persona_query()` in `starter/mcp/adk_multi_agent_demo.py` so `--mock` proves prompt injection fails closed."*
- **Verify**:
  ```bash
  python3 starter/mcp/adk_multi_agent_demo.py --mock
  make TRACK=starter verify-lab LAB=6
  ```

### Lab 7 — Enterprise Sizing, Bangkok/Singapore Pricing & 3-Store Consolidation TCO
- **Primary Skills**: [spanner-performance-observability-and-sizing](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md), [spanner-enterprise-platform-ops](../.agents/skills/spanner-enterprise-platform-ops/SKILL.md)
- **Support Skill**: [sdlc-graphrag-workshop-guide](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md)
- **Starter File**: [tools/customer_scale_worksheet.json](tools/customer_scale_worksheet.json)
- **Suggested Antigravity Prompt**:
  > *"Using `spanner-performance-observability-and-sizing`, help me tailor `starter/tools/customer_scale_worksheet.json` for our real SDLC Knowledge Graph scale in Bangkok (`asia-southeast3`) with Singapore DR backup copies (`asia-southeast1`), run `solution/tools/sizing_calculator.py --worksheet starter/tools/customer_scale_worksheet.json`, explain the 1-Yr/3-Yr CUD and SSD→HDD Locality Group savings vs a 3-store stack, and mark `todo_status` as `COMPLETED`."*
- **Verify**:
  ```bash
  make sizing WORKSHEET=starter/tools/customer_scale_worksheet.json REGION=asia-southeast3
  make TRACK=starter verify-lab LAB=7
  ```

### Lab 8 — Observability, Spanner Studio Visualizations, Pre-Splitting & Teardown
- **Primary Skills**: [spanner-performance-observability-and-sizing](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md), [spanner-graph-modeling-and-gql](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md), [spanner-enterprise-platform-ops](../.agents/skills/spanner-enterprise-platform-ops/SKILL.md)
- **Support Skill**: [sdlc-graphrag-workshop-guide](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md)
- **Starter File**: [sql/11_observability_and_studio_viz.sql](sql/11_observability_and_studio_viz.sql)
- **Suggested Antigravity Prompt**:
  > *"Using `spanner-performance-observability-and-sizing` and `spanner-graph-modeling-and-gql`, complete the `TODO(Lab 8)` queries in `starter/sql/11_observability_and_studio_viz.sql`: Spanner Studio `RETURN SAFE_TO_JSON(p)` blast-radius visualization (`LIMIT 50`), `SPANNER_SYS.QUERY_STATS_TOP_HOUR`, `SPANNER_SYS.LOCK_STATS_TOP_HOUR` converting microsecond distribution percentiles to seconds (`/ 1000000.0`), and `SPANNER_SYS.USER_SPLIT_POINTS`."*
- **Verify**:
  ```bash
  make TRACK=starter verify-lab LAB=8
  ```

---

## 4. Non-Negotiable Spanner Guardrails Cheat Sheet

Keep these 7 rules top of mind while editing `starter/` (full list in [../GEMINI.md](../GEMINI.md)):

1. **Always use `MATCH TRAIL`** for variable-length graph traversals — default `WALK` loops on `svc-orders <-> svc-notifications`.
2. **Never combine `INTERLEAVE IN PARENT T ON DELETE CASCADE` with a self-referencing cascading FK to `T`** (`INV-06`) — keep `ServiceDependsOnService` standalone dual-FK (Lab 3) or use a `NOT ENFORCED` destination FK for `ServiceDependsOnAlgo` (Lab 4).
3. **Never use `ROW_NUMBER()`** (`INV-09`) — compute server-side RRF (`k=60`) via `UNNEST(ARRAY(SELECT ... ORDER BY ... LIMIT k)) AS chunk_id WITH OFFSET AS rank`.
4. **Isolate ScaNN ANN inside a single-table CTE with `WHERE embedding IS NOT NULL`** (`INV-08`) — zero `JOIN`s inside the ANN `SELECT` block, and always `ARRAY<FLOAT32>(vector_length=>768)`.
5. **Run all FTS, Hybrid, and `SPANNER_SYS` queries in read-only transactions** (`readOnly: true` in `tools.yaml`).
6. **Keep `tools.yaml` strict**: zero `databaseRole` on `type: spanner`, zero `spanner-execute-sql`, zero `templateParameters`, secretless regional Vertex AI `asia-southeast1` embeddings via ADC (never `global` and never an active static `apiKey`).
7. **Watch units in `SPANNER_SYS.LOCK_STATS_TOP_HOUR`**: `LOCK_WAIT_DISTRIBUTION` stores **microseconds**, so divide `SPANNER_SYS.DISTRIBUTION_PERCENTILE(...)` by `1000000.0` for seconds!

---

## 5. Useful Workshop Commands Reference (All 16 `Makefile` Targets)

```bash
make help                                             # List all 16 Makefile targets & variables
make preflight                                        # Fast 5-second attendee readiness check (STRICT_GCP=1 for live)
make lab1-no-docker                                   # Zero-Docker Lab 1 Neo4j CE & APOC simulator + parity check
make install                                          # Create PEP-668-safe .venv & install core pipeline SDKs
make install-mcp                                      # Optional Lab 6 live ADK/MCP & Model Armor packages
make verify-starter-stubs                             # Confirm starter/ stubs fail until solved
make TRACK=starter verify-lab LAB=<1..8|all>          # Verify your starter/ work offline (default MODE=full)
make TRACK=starter MODE=express verify-lab LAB=1      # Verify Track A (4-Hour Express) core subset
make TRACK=solution verify-lab LAB=all                # Verify the reference solution/
make test                                             # Run all 8 offline stdlib unittest suites
make validate-skills                                  # Audit all 8 .agents/skills/ packages
make dry-run-pipelines                                # Offline dry-run of reference Path A & B loaders
make demo-mcp-mock                                    # Offline prompt-injection fail-closed demo
make sizing PRESET=all REGION=asia-southeast3         # Bangkok Enterprise 2026 sizing & 3-store TCO
make provision-gcp                                    # [LIVE GCP] Provision Bangkok 100-PU Enterprise sandbox
make verify-live                                      # [LIVE GCP] Run 5-check live Cloud Spanner verification
make cleanup-gcp                                      # [LIVE GCP] Delete Spanner instances & backups only
make cleanup-all-gcp                                  # [LIVE GCP] Full teardown (MCP/IAM/DLP/GCS/Spanner) + zero-residual audit
```

> **Resetting Your Starter Workspace:** If you want to practice a lab again from scratch, restore any file cleanly with `git checkout -- starter/` (if initialized as a Git repo) or compare side-by-side against `solution/`.
