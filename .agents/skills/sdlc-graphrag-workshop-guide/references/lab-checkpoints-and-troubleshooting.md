# Lab Checkpoints, `Makefile` Targets & Troubleshooting Runbook

Operational handbook for running Labs 1–8 across **Track A (4-Hour Express)** and **Track B (1-Day Deep-Dive)**, verifying all **15 `starter/` `TODO(Lab 1-8)`** exercises against `solution/`, executing the live Bangkok/Singapore lifecycle safely, and resolving workshop-specific errors.

## 1. Delivery Tracks, Timeboxes & Instructor Proof Points

| Lab | Req # | Participant Guide (`docs/`) | Track A (4h) | Track B (8h) | Primary Skill(s) | Key Live "Aha!" Demo Cue |
| :-: | :-: | :--- | :-: | :-: | :--- | :--- |
| **1** | 1 | `docs/01_neo4j_community_migration.md` | 30m | 60m | `spanner-migration-and-neo4j-playbook` | Run upstream query on `svc-orders` without `TRAIL` (loops on `svc-notifications`), then add `MATCH TRAIL` for openCypher parity |
| **2** | 2 | `docs/02_two_path_sdlc_workshop.md` | 20m | 45m | `sdlc-graphrag-workshop-guide`, `spanner-enterprise-platform-ops` | Inspect 68 nodes + 174 explicit + 60 dual-projected edges (234 logical) + 45 Markdown docs + Bangkok/Singapore PDPA split |
| **3** | 3 | `docs/03_database_design_and_ingestion.md` | 45m | 90m | `spanner-schema-and-data-modeling`, `spanner-graph-modeling-and-gql` | Contrast `ServiceCallsApi` (interleaved + dest FK cascade) vs `ServiceDependsOnService` (standalone dual-FK avoiding `INV-06`) |
| **4** | 4 | `docs/04_search_strategy_options.md` | 25m | 60m | `spanner-search-ai-and-graphrag`, `spanner-graph-modeling-and-gql` | Compare `SdlcSynonymDict` + `enhance_query`, fuzzy n-grams, ScaNN ANN, and Data Boost `PageRank` (`svc-auth` hub) |
| **5** | 5 | `docs/05_hybrid_retrieval_and_graphrag.md` | 45m | 75m | `spanner-search-ai-and-graphrag`, `spanner-graph-modeling-and-gql` | **Zero-Sync-Lag ACID Proof**: insert a new caller edge + Markdown chunk in one txn → re-run Hero Query B → **8 rows** appear immediately |
| **6** | 6 | `docs/06_mcp_toolbox_agent_security.md` | 40m | 75m | `spanner-mcp-toolbox-and-agent-security` | **Prompt-Injection Fail-Closed**: run `make demo-mcp-mock` probing `team-security` as `team-checkout` → returns only `team-checkout` |
| **7** | 7 | `docs/07_sizing_and_cost_tco.md` | 20m | 45m | `spanner-performance-observability-and-sizing`, `spanner-enterprise-platform-ops` | Toggle `--cold-hdd-pct 40` + `--dr-backup-region asia-southeast1` + 1-yr/3-yr CUDs to reveal 3-store TCO consolidation savings |
| **8** | 7 | `docs/08_observability_and_troubleshooting.md` | 15m | 30m | `spanner-performance-observability-and-sizing`, `spanner-graph-modeling-and-gql`, `spanner-enterprise-platform-ops` | Render `RETURN SAFE_TO_JSON(p)` in Spanner Studio, inspect `SPANNER_SYS` lock wait in seconds, copy backup to Singapore, clean teardown |
| **Total** | **1–7** | `docs/00_facilitator_guide.md` | **240m** | **480m** | All 8 skills under `.agents/skills/` | *Add 30–60 min breaks for full-day Track B delivery* |

## 2. Complete 15-File `starter/` → `solution/` Checkpoint Matrix

Every `starter/` file intentionally fails `make TRACK=starter verify-lab LAB=N` until its `TODO(Lab N)` markers are completed. Verify any single lab offline without GCP credentials:

```bash
make TRACK=starter verify-lab LAB=<1..8>
make TRACK=solution verify-lab LAB=all
```

### Lab 1 — Neo4j CE Export & openCypher → ISO GQL (1 File)
- **Starter**: `starter/sql/09_opencypher_to_gql_cookbook.sql` (`TODO(Lab 1)`)
- **Reference**: `solution/sql/09_opencypher_to_gql_cookbook.sql`, `data/structured/{docker-compose.yml,seed_neo4j.cypher,export_neo4j_apoc.cypher,stage_apoc_export.py}`
- **`verify_lab.py` Assertions**: Zero `TODO(Lab` markers; contains `GRAPH SdlcGraph`, `MATCH TRAIL`, `SAFE_TO_JSON`, `FOR `, and `RETURN` with `ORDER BY ... LIMIT`.

### Lab 2 — Two-Path SDLC Corpus & Parity Validation (1 File)
- **Starter**: `starter/sql/10_validate_migration_parity.sql` (`TODO(Lab 2)`)
- **Reference**: `solution/sql/10_validate_migration_parity.sql`, `solution/sql/fixtures/10_row_hash_expected.json`
- **`verify_lab.py` Assertions**: `data/structured/` holds exact 68 nodes, 174 explicit rows, 60 dual-projected edges (234 logical) and `data/unstructured/` holds 45 Markdown docs; `10_validate_migration_parity.sql` has zero `TODO(Lab` markers and validates `DocumentChunks`, `svc-auth`, `DATA_FEED`, and `AT_RISK`.

### Lab 3 — Schema Design, Post-Load Indexes & Ingestion (4 Files)
- **Starter**:
  1. `starter/sql/01_path_a_schematized.sql` (`TODO(Lab 3)`): `ServiceCallsApi` interleaving, standalone dual-FK `ServiceDependsOnService`, `ChunkCitesApiView` (`SQL SECURITY INVOKER`), `CREATE PROPERTY GRAPH SdlcGraph`.
  2. `starter/sql/03_indexes_fts_vector_postload.sql` (`TODO(Lab 3)`): reverse covering `STORING` index (`INTERLEAVE IN Apis`), `CREATE SEARCH INDEX`, ScaNN `CREATE VECTOR INDEX ... WHERE embedding IS NOT NULL`.
  3. `starter/pipelines/neo4j_to_spanner_loader.py` (`TODO(Lab 3)`): replace `NotImplementedError` in `load_graph()` with nodes-before-edges 500-row batched mutations (`< 80,000` mutations/commit) + `--dry-run`.
  4. `starter/pipelines/unstructured_graphrag_ingest.py` (`TODO(Lab 3)`): replace `NotImplementedError` in `extract_chunks_and_mentions()` with Markdown chunking + regex entity extraction (`api-*`, `svc-*`, `team-*`, `ADR-*`, `REQ-*`) + `--dry-run`.
- **Reference**: `solution/sql/{01,02,03,03a,03b,03c,03d}*`, `solution/pipelines/{neo4j_to_spanner_loader.py,unstructured_graphrag_ingest.py,dataflow_migration.sh,apply_postload.py}`
- **`verify_lab.py` Assertions**: Zero `TODO(Lab` in SQL files; required DDL clauses present; both Python loaders exit `0` under `--dry-run`.

### Lab 4 — Multi-Model Search Strategies & Graph Algorithms (2 Files)
- **Starter**:
  1. `starter/sql/05_search_strategies_benchmark.sql` (`TODO(Lab 4)`): FTS with `SdlcSynonymDict` + `enhance_query => TRUE`, fuzzy `SEARCH_NGRAMS`/`SCORE_NGRAMS` (`LIMIT 10000` cap), single-table ScaNN `APPROX_COSINE_DISTANCE` with `WHERE embedding IS NOT NULL`.
  2. `starter/sql/08_graph_algorithms_criticality.sql` (`TODO(Lab 4)`): `ServiceDependsOnAlgo` (`INTERLEAVE IN PARENT Services ON DELETE CASCADE` + `NOT ENFORCED` destination FK), table-backed `SdlcAlgoGraph`, `EXPORT DATA ... CALL PageRank` via Data Boost.
- **Reference**: `solution/sql/05_search_strategies_benchmark.sql`, `solution/sql/08_graph_algorithms_criticality.sql`
- **`verify_lab.py` Assertions**: Zero `TODO(Lab`; checks `SdlcSynonymDict`, `enhance_query`, `SEARCH_NGRAMS`, `SCORE_NGRAMS`, `APPROX_COSINE_DISTANCE` + `WHERE embedding IS NOT NULL`, `ServiceDependsOnAlgo`, `NOT ENFORCED`, `SdlcAlgoGraph`, `PageRank`, `EXPORT DATA`.

### Lab 5 — Hybrid Retrieval (RRF/RSF/Filtered) & Hero GraphRAG (2 Files)
- **Starter**:
  1. `starter/sql/06_hybrid_rrf_rsf_filtered.sql` (`TODO(Lab 5)`): server-side RRF (`k=60`) using `UNNEST(ARRAY(...)) WITH OFFSET` (zero `ROW_NUMBER()`).
  2. `starter/sql/07_hero_api_blast_radius_graphrag.sql` (`TODO(Lab 5)`): `MATCH TRAIL` upstream traversal, `DATA_FEED_CONSUMER` separation, CTE-isolated `APPROX_COSINE_DISTANCE`, governing ADRs/REQs/citations.
- **Reference**: `solution/sql/{06,07}*`, `solution/sql/fixtures/07_hero_expected_rows.json`, `solution/pipelines/hero_blast_radius_offline.py`, `labs/run_hero_query.py`
- **`verify_lab.py` Assertions**: Zero `TODO(Lab`, `UNNEST(ARRAY(`, zero `ROW_NUMBER()`, `MATCH TRAIL`, `DATA_FEED_CONSUMER`, CTE-isolated `APPROX_COSINE_DISTANCE`, AND `hero_blast_radius_offline.py --check-fixture` exits `0`.

### Lab 6 — MCP Toolbox, Per-Agent RBAC, FGAC & Model Armor (3 Files)
- **Starter**:
  1. `starter/sql/04_fgac_roles_views.sql` (`TODO(Lab 6)`): `PublicApiCatalogView` (`SQL SECURITY INVOKER`) and `GRANT SELECT` for `sdlc_readonly_role`.
  2. `starter/mcp/tools.yaml` (`TODO(Lab 6)`): Vertex AI regional `embeddingModels` (`project` + `location`), OIDC `authServices`, curated `spanner-sql` tools, isolated `toolsets` (no `databaseRole`, no `spanner-execute-sql`, no `templateParameters`).
  3. `starter/mcp/adk_multi_agent_demo.py` (`TODO(Lab 6)`): replace `NotImplementedError` in `run_persona_query()` to bind persona toolset and verify OIDC fail-closed behavior under `--mock`.
- **Reference**: `solution/sql/{04_fgac_roles_views.sql,04b_fgac_grants_rerunnable.sql}`, `solution/mcp/{tools.yaml,render_persona_tools.py,personas/*,iam_and_model_armor_setup.sh,adk_multi_agent_demo.py}`, `.antigravity/mcp_config.json`
- **`verify_lab.py` Assertions**: Zero `TODO(Lab`; SQL view/grant clauses; valid YAML keys & Vertex AI config; `<track>/mcp/adk_multi_agent_demo.py --mock` exits `0`.

### Lab 7 — Enterprise Sizing & Bangkok/Singapore TCO Worksheet (1 File)
- **Starter**: `starter/tools/customer_scale_worksheet.json` (`TODO(Lab 7)` via `"todo_status"` field — valid JSON from the start so `--worksheet` parses cleanly).
- **Reference**: `solution/tools/sizing_calculator.py`
- **`verify_lab.py` Assertions**: Runs `sizing_calculator.py --preset all --json` and `--worksheet starter/tools/customer_scale_worksheet.json --json`; in `--track starter`, asserts `"todo_status"` no longer contains `TODO(Lab 7)` (update to `"COMPLETED"`).

### Lab 8 — Observability, Spanner Studio Viz & Teardown (1 File)
- **Starter**: `starter/sql/11_observability_and_studio_viz.sql` (`TODO(Lab 8)`): Spanner Studio `RETURN SAFE_TO_JSON(p)` path query + `SPANNER_SYS` Query, Lock (converting microseconds via `/ 1000000.0`), and Split-Point queries.
- **Reference**: `solution/sql/11_observability_and_studio_viz.sql`, `scripts/{provision_workshop_env.sh,verify_live_gcp.sh}`
- **`verify_lab.py` Assertions**: Zero `TODO(Lab`; checks `SAFE_TO_JSON`, `SPANNER_SYS.QUERY_STATS_TOP_HOUR`, `SPANNER_SYS.LOCK_STATS_TOP_HOUR`, `1000000`, `SPANNER_SYS.USER_SPLIT_POINTS`, Bangkok defaults in `provision_workshop_env.sh`, and all 8 skills under `.agents/skills/`.

## 3. `Makefile` Targets & 7-Module Offline Test Suite

### All 11 `Makefile` Targets

| Target | Command / Default Arguments | Live GCP? | Purpose |
| :--- | :--- | :---: | :--- |
| `help` | `make help` | No | Prints target list and overrides (`TRACK`, `LAB`, `PRESET`, `REGION`, `WORKSHEET`) |
| `install` | `make install` | No | Installs `solution/pipelines/requirements.txt` (`google-cloud-spanner`, `google-genai`, `pyyaml`) |
| `test` | `make test` | No | Runs all 7 stdlib `unittest` modules under `tests/test_*.py` |
| `validate-skills` | `make validate-skills` | No | Runs `tests/test_antigravity_skills.py` (YAML, line bounds, links, leaks, reusability) |
| `verify-starter-stubs` | `make verify-starter-stubs` | No | Confirms all 15 `starter/` files have `TODO(Lab` and fail `verify_lab.py` while `solution/` passes |
| `verify-lab` | `make TRACK=solution LAB=all verify-lab` | No | Runs `scripts/verify_lab.py --track $(TRACK) --lab $(LAB)` (`1..8` or `all`) |
| `sizing` | `make PRESET=all REGION=asia-southeast3 sizing` | No | Runs `solution/tools/sizing_calculator.py` (optional `WORKSHEET=starter/tools/customer_scale_worksheet.json`) |
| `dry-run-pipelines` | `make dry-run-pipelines` | No | Executes Path A + Path B loaders and `apply_postload.py` with `--dry-run` (stdlib-only) |
| `demo-mcp-mock` | `make demo-mcp-mock` | No | Runs `solution/mcp/adk_multi_agent_demo.py --mock` (persona isolation + prompt-injection check) |
| `provision-gcp` | `make provision-gcp` | **YES** | Calls `scripts/provision_workshop_env.sh create` (Bangkok 100-PU Enterprise sandbox; confirm first!) |
| `cleanup-gcp` | `make cleanup-gcp` | **YES** | Calls `scripts/provision_workshop_env.sh cleanup` (deletes sandbox instance/DB; confirm first!) |

### 7-Module Offline Test Suite (`tests/`)

1. `tests/test_sql_and_gql_invariants.py`: Enforces `INV-01`–`INV-09` across `solution/sql/01..11` and `.tmpl` files.
2. `tests/test_dataset_and_hero_replay.py`: Verifies 68/174/60/234 counts, 100% FK RI, 45 Markdown docs & verbatim facts, `stage_apoc_export.py`, `--dry-run` loaders, and 7-row Hero fixture equality.
3. `tests/test_mcp_toolbox_rbac.py`: Validates `tools.yaml` & 3 rendered persona YAMLs (no `databaseRole`, Vertex AI regional `project`+`location`, no `spanner-execute-sql`, OIDC `authServices`, dual-layer IAM script separation, `--mock` prompt-injection fail-closed).
4. `tests/test_sizing_calculator.py`: Checks verified Bangkok/Singapore/Iowa 2026 SKU rates, CUDs, 4 presets, `>= 1,000` PU prod floor vs 100 PU workshop flag, Singapore backup DR egress, worksheet mode, and 3-store TCO savings `> 0`.
5. `tests/test_antigravity_skills.py`: Audits all 8 skills under `.agents/skills/` (frontmatter, `< 1024`-char descriptions, line limits, 1-level links, zero orphan/nested/broken links, zero leaks, Skills #1–#7 Acme-free).
6. `tests/test_starter_vs_solution_parity.py`: Verifies all 15 `starter/` files fail `--track starter --lab 1..8` while `--track solution --lab all` passes.
7. `tests/test_docs_consistency.py`: Checks `README.md`, `WORKSHOP_CONTENT_PLAN.md`, `GEMINI.md`, `.agents/rules/spanner-graph-workshop.md`, `starter/README.md`, and `docs/00..08.md` for link integrity and spec alignment.

## 4. Live Bangkok GCP Workshop Lifecycle (Confirm Every Mutating Step)

```bash
# 1. Copy .env.example -> .env and confirm active GCP project (never commit .env!)
cp .env.example .env
# Expected defaults:
#   GOOGLE_CLOUD_REGION=asia-southeast3   VERTEX_AI_LOCATION=asia-southeast1
#   SPANNER_INSTANCE=sdlc-kg-enterprise   SPANNER_DATABASE=sdlc_knowledge_graph
#   SPANNER_PROCESSING_UNITS=100

# 2. Provision Bangkok 100-PU Enterprise instance (--default-backup-schedule-type=NONE) + base DDL
bash scripts/provision_workshop_env.sh create

# 3. Load Path A structured graph (nodes before edges) + Path B 45 Markdown docs (embeddings via asia-southeast1)
python3 solution/pipelines/neo4j_to_spanner_loader.py
python3 solution/pipelines/unstructured_graphrag_ingest.py
# Or bulk Dataflow staging (100 PU sandbox uses --max-workers=1; production uses 4x-6x nodes):
# bash solution/pipelines/dataflow_migration.sh

# 4. Apply post-load synonyms, regional CREATE MODEL (asia-southeast1), covering/FTS/ScaNN indexes & ANALYZE
python3 solution/pipelines/apply_postload.py
# Optional PDML backfill for any NULL embeddings (uses @{pdml_max_parallelism=5} + @{remote_udf_max_rows_per_rpc=50})

# 5. Execute live Hero Queries (A = Exact KNN, B = CTE-isolated ScaNN ANN, C = SdlcDynamicGraph)
python3 labs/run_hero_query.py --query B --api-id api-payments-charge-v2

# 6. Render persona YAMLs, configure dual-layer IAM & Model Armor (asia-southeast1), check Antigravity MCP
python3 solution/mcp/render_persona_tools.py
bash solution/mcp/iam_and_model_armor_setup.sh
bash scripts/setup_antigravity_skills.sh
bash scripts/verify_live_gcp.sh

# 7. Optional Lab 8 DR backup copy to Singapore, then teardown
bash scripts/provision_workshop_env.sh backup-to-singapore
bash scripts/provision_workshop_env.sh cleanup
```

### Verified Templates for High-Friction Lab Checkpoints (Labs 3, 6 & 8)

#### Lab 3: Regional Vertex AI `CREATE MODEL` & Throttled PDML Backfill (`03b` / `03d`)

```sql
-- 03b_remote_models.sql.tmpl (VERTEX_AI_LOCATION=asia-southeast1; NEVER global!)
CREATE OR REPLACE MODEL SdlcTextEmbedding
INPUT (
  content STRING(MAX),
  task_type STRING(MAX) OPTIONS (required = false)
)
OUTPUT (
  embeddings STRUCT<
    statistics STRUCT<truncated BOOL, token_count FLOAT64>,
    values ARRAY<FLOAT32>
  >
)
REMOTE OPTIONS (
  endpoint = '//aiplatform.googleapis.com/projects/${GOOGLE_CLOUD_PROJECT}/locations/${VERTEX_AI_LOCATION}/publishers/google/models/gemini-embedding-001',
  default_batch_size = 5
);

-- 03d_backfill_embeddings_pdml.sql (throttled PDML to protect 100-PU sandbox CPU)
@{pdml_max_parallelism=5}
UPDATE DocumentChunks
SET embedding = (
      SELECT embeddings.values
      FROM SAFE.ML.PREDICT(
        MODEL SdlcTextEmbedding,
        (SELECT DocumentChunks.content AS content, 'RETRIEVAL_DOCUMENT' AS task_type),
        STRUCT(768 AS outputDimensionality)
      ) @{remote_udf_max_rows_per_rpc=50}
    ),
    embedding_model_version = 'gemini-embedding-001-768'
WHERE embedding IS NULL;
```

#### Lab 6: Curated `tools.yaml` Source, Regional Vertex AI Embedder & OIDC Parameter

```yaml
sources:
  sdlc-spanner:
    type: spanner
    project: ${SPANNER_PROJECT}
    instance: ${SPANNER_INSTANCE}
    database: ${SPANNER_DATABASE}
    dialect: googlesql
    # NEVER add databaseRole here (unsupported on type: spanner)
embeddingModels:
  gemini-embedder:
    type: gemini
    model: gemini-embedding-001
    project: ${GOOGLE_CLOUD_PROJECT}
    location: ${VERTEX_AI_LOCATION}  # asia-southeast1 via ADC / Workload Identity (no apiKey!)
    dimension: 768
authServices:
  google-corp-oidc:
    type: google
    clientId: ${GOOGLE_OAUTH_CLIENT_ID}
```

#### Lab 8: `SPANNER_SYS` Lock Wait (Microseconds → Seconds) & Studio Graph Viz (`11`)

```sql
-- Convert LOCK_WAIT_DISTRIBUTION from microseconds to seconds (/ 1000000.0)
SELECT
  CAST(row_range_start_key AS STRING) AS row_range_start_key,
  lock_wait_seconds,
  IF(
    lock_wait_distribution[SAFE_OFFSET(0)].COUNT > 0,
    SPANNER_SYS.DISTRIBUTION_PERCENTILE(lock_wait_distribution[SAFE_OFFSET(0)], 95.0) / 1000000.0,
    NULL
  ) AS p95_wait_sec
FROM SPANNER_SYS.LOCK_STATS_TOP_HOUR
ORDER BY lock_wait_seconds DESC
LIMIT 10;

-- Spanner Studio interactive graph visualization (< 10 MB payload)
GRAPH SdlcGraph
MATCH p = TRAIL (upstream:Service)-[deps:DEPENDS_ON]->{1,3}(direct:Service)-[:CALLS_API]->(target:Api {api_id: 'api-payments-charge-v2'})
RETURN SAFE_TO_JSON(p) AS blast_path
LIMIT 50;
```

## 5. Workshop Troubleshooting Matrix

| # | Symptom / Error | Root Cause | Exact Verified Fix |
| :-: | :--- | :--- | :--- |
| 1 | `make TRACK=starter verify-lab LAB=N` fails immediately | Remaining `TODO(Lab N)` comment, `NotImplementedError`, or unedited `customer_scale_worksheet.json` `todo_status` | Complete all `TODO(Lab N)` blocks for that lab (for Lab 7, update `"todo_status"` to `"COMPLETED"`), then re-run |
| 2 | `make verify-starter-stubs` fails after you practiced in `starter/` | `verify-starter-stubs` asserts that starter files are still unsolved stubs | Expected once solved! Run `make TRACK=starter verify-lab LAB=all` instead, or `git checkout -- starter/` to reset stubs |
| 3 | DDL error on `ServiceDependsOnService` or `ServiceDependsOnAlgo` about cascade cycles | `INV-06`: Spanner bans `INTERLEAVE IN PARENT T ON DELETE CASCADE` + cascading self-ref FK to `T` on the same table | Keep `ServiceDependsOnService` / `AdrSupersedesAdr` standalone dual-FK; use `NOT ENFORCED` destination FK on `ServiceDependsOnAlgo` |
| 4 | Upstream GQL query on `svc-orders` loops or duplicates paths | Default GQL path mode is `WALK`, which repeats `svc-orders <-> svc-notifications` cycle edges | Change `MATCH (...)` to `MATCH TRAIL (...)` for openCypher relationship-uniqueness parity |
| 5 | Hero blast-radius query returns != 7 rows or mislabels `svc-analytics` | Extra edges added to `data/structured/`, missing `MIN(hop_distance)` grouping, or missing `DATA_FEED` check | Restore locked 28 edges in `service_depends_on_service.csv`; aggregate `MIN(hop_distance)` per `impacted_service_id`; branch on `ARRAY_INCLUDES(dep_types, 'DATA_FEED')` |
| 6 | `ROW_NUMBER()` fails in Lab 5 RRF query | `INV-09`: Cloud Spanner GoogleSQL does not support general analytic `ROW_NUMBER()` | Replace with `UNNEST(ARRAY(SELECT chunk_id ... ORDER BY ... LIMIT 50)) AS chunk_id WITH OFFSET AS rank` |
| 7 | ScaNN `APPROX_COSINE_DISTANCE` fails or skips `IdxDocumentChunksEmbedding` | `INV-08`: missing `WHERE embedding IS NOT NULL`, `JOIN` inside ANN block, or `FLOAT64` column | Use `ARRAY<FLOAT32>(vector_length=>768)`, add `WHERE embedding IS NOT NULL`, and isolate ANN inside a single-table CTE |
| 8 | FTS `SEARCH(...)` fails with transaction-mode error | `INV-09`: Full-Text Search requires a read-only transaction | Execute FTS and Hybrid queries in read-only mode (and set `readOnly: true` in `tools.yaml`) |
| 9 | `CREATE MODEL SdlcTextEmbedding` fails on `locations/global` | `INV-09`: Vertex AI predict-API `gemini-embedding-001` requires a regional endpoint | Set `VERTEX_AI_LOCATION=asia-southeast1`; reserve `global` fallback strictly for `gemini-2.5-flash` LLM calls |
| 10 | Cloud Run MCP Toolbox returns `PERMISSION_DENIED` or fails startup on `databaseRole` | `type: spanner` rejects `databaseRole` and creates ADC sessions without a Spanner role | Remove `databaseRole` from `tools.yaml`; grant Layer 1 `sa-impact-analyst`/`sa-security-auditor` `roles/spanner.databaseReader` and `sa-adr-curator` `roles/spanner.databaseUser`; reserve `sa-fgac-*` for `database_role`-aware clients |
| 11 | `sdlc_readonly_role` loses `SELECT` access after updating `PublicApiCatalogView` | `CREATE OR REPLACE VIEW` resets prior `GRANT SELECT ON VIEW` bindings | Re-run idempotent `solution/sql/04b_fgac_grants_rerunnable.sql` immediately after any view replacement |
| 12 | Lab 4 `PageRank` / `EXPORT DATA` fails with parameter or schema error | Data Boost Graph Algorithms disallow `@param` (literals only) and require table-backed interleaved graph + FK | Pass literal values only, run over `SdlcAlgoGraph` (`ServiceDependsOnAlgo` interleaved in `Services` with `NOT ENFORCED` FK) |
| 13 | `make cleanup-gcp` fails to delete `sdlc-kg-enterprise` | Existing Singapore backup copies, automatic backup schedule, or `--enable-drop-protection` | Delete all backups on the instance first, disable drop protection if enabled, then re-run `scripts/provision_workshop_env.sh cleanup` |
| 14 | Quota exceeded creating instance in `asia-southeast3` (Bangkok) | Customer GCP project lacks `asia-southeast3` Spanner quota | Switch `.env` to `GOOGLE_CLOUD_REGION=asia-southeast1` (Singapore fallback; `100` PU × 8 hr = `$1.1257` compute) |
| 15 | Lab 8 `LOCK_STATS_TOP_HOUR` wait times look 1,000,000x too high | `SPANNER_SYS.LOCK_WAIT_DISTRIBUTION` stores durations in **microseconds**, not seconds | Divide `SPANNER_SYS.DISTRIBUTION_PERCENTILE(...)` output by `1000000.0` to display seconds |

## 6. Common Workshop Anti-Patterns (Never Do These)

- **Never put `databaseRole` in `tools.yaml` `sources.sdlc-spanner`** or enable `spanner-execute-sql` / `templateParameters` in production toolsets.
- **Never commit an active static `apiKey`** in `solution/mcp/tools.yaml` — use Vertex AI regional ADC (`project` + `location: ${VERTEX_AI_LOCATION}`).
- **Never mix static node/edge tables with `DYNAMIC LABEL`** inside `SdlcGraph` — keep dynamic entities in `SdlcDynamicGraph`.
- **Never create `IdxDocumentChunksEmbedding` or FTS search indexes before bulk loading** — load Path A and Path B data first, run `apply_postload.py` second, then `ANALYZE`.
- **Never size production on `100` PUs** — `100` PU (`$0.12915/hr` Bangkok Enterprise) is strictly for workshop sandboxes; production starts at `>= 1,000` PUs (`1` node).
- **Never rely on the local Spanner Emulator to validate Lab 4/5/6/8** — the emulator skips IAM/FGAC, returns empty `PLAN`/`PROFILE`, and ignores `ANALYZE`.
