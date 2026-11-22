# Module 3: Production Schema Design, Post-Load Indexes & Ingestion

**Customer Requirement 3:** Design a split-safe, multi-model **Cloud Spanner Enterprise** schema enforcing all structural graph invariants (`INV-01`–`INV-09`), compare schematized vs. schemaless (`DYNAMIC LABEL`) graph layouts, load Path A and Path B data without hitting Spanner's mutation or index-backfill traps, and apply post-load covering, FTS, and ScaNN vector indexes.

- **Duration:** Track A (4h Express): `45 min` | Track B (8h Deep-Dive): `90 min`
- **Primary Antigravity Skills:** [`spanner-schema-and-data-modeling`](../.agents/skills/spanner-schema-and-data-modeling/SKILL.md), [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md)
- **Support Skills:** [`spanner-enterprise-platform-ops`](../.agents/skills/spanner-enterprise-platform-ops/SKILL.md), [`spanner-search-ai-and-graphrag`](../.agents/skills/spanner-search-ai-and-graphrag/SKILL.md), [`spanner-migration-and-neo4j-playbook`](../.agents/skills/spanner-migration-and-neo4j-playbook/SKILL.md)
- **Hands-On Starter Files (`4` files with `TODO(Lab 3)`):**
  1. [`starter/sql/01_path_a_schematized.sql`](../starter/sql/01_path_a_schematized.sql)
  2. [`starter/sql/03_indexes_fts_vector_postload.sql`](../starter/sql/03_indexes_fts_vector_postload.sql)
  3. [`starter/pipelines/neo4j_to_spanner_loader.py`](../starter/pipelines/neo4j_to_spanner_loader.py)
  4. [`starter/pipelines/unstructured_graphrag_ingest.py`](../starter/pipelines/unstructured_graphrag_ingest.py)
- **Reference Solution:** `solution/sql/{01,02,03,03a,03b,03c,03d}*`, `solution/pipelines/{neo4j_to_spanner_loader.py,dataflow_migration.sh,unstructured_graphrag_ingest.py,apply_postload.py}`

---

## 1. Core Schema Engineering Decisions (`01_path_a_schematized.sql`)

### A. Interleaving & The Dual-Cascade Trap (`INV-06`)
In Cloud Spanner Graph, interleaving outgoing edges under their source node table physically co-locates adjacency lists on the same split — turning forward hops into local disk reads:
- **Heterogeneous Edges (Source != Destination):** `ServiceCallsApi` (`Services -> Apis`), `AdrGovernsApi` (`Adrs -> Apis`), `ReqAppliesToApi`, `ReqAppliesToService`, and `ChunkEntityMentions` legally combine `INTERLEAVE IN PARENT <Source> ON DELETE CASCADE` with a destination `FOREIGN KEY ... ON DELETE CASCADE`.
- **Self-Referential Edges (Source == Destination — `INV-06`):** Cloud Spanner **forbids** combining `INTERLEAVE IN PARENT T ON DELETE CASCADE` with a self-referencing cascading `FOREIGN KEY REFERENCES T ON DELETE CASCADE` on the same table! Therefore:
  - `ServiceDependsOnService` (`Services -> Services`) and `AdrSupersedesAdr` (`Adrs -> Adrs`) are **standalone tables with two enforced cascading FKs**.
  - For Lab 4 Data Boost Graph Algorithms (`08_graph_algorithms_criticality.sql`), `ServiceDependsOnAlgo` satisfies Spanner's algorithm interleaving requirement via `INTERLEAVE IN PARENT Services ON DELETE CASCADE` + a **`NOT ENFORCED`** destination FK!

### B. Dual-Projected N:1 Edges (`INV-02`–`INV-04`)
Instead of creating 4 extra junction tables for `EXPOSES`, `OWNS_SERVICE`, `OWNS_ADR`, and `OWNS_REQ` (`60` edges), we project the existing FK columns on `Apis`, `Services`, `Adrs`, and `Requirements` directly in `CREATE OR REPLACE PROPERTY GRAPH SdlcGraph` using **unique `AS` aliases** (`Apis AS ExposesApi`, `Services AS TeamOwnsService`, `Adrs AS TeamOwnsAdr`, `Requirements AS TeamOwnsReq`). Note that folded N:1 FKs omit `ON DELETE CASCADE` so removing a team record never cascades away production microservices.

### C. Path B Bridge Views (`INV-05`), Locality Groups & Custom Dictionary
- **Pattern-1 Citation Views:** `ChunkCitesApiView`, `ChunkCitesServiceView`, `ChunkCitesAdrView`, and `ChunkCitesReqView` declare explicit `SQL SECURITY INVOKER` and project the single-table primary key `(chunk_id, entity_type, entity_id)` with explicit `KEY (...)` clauses in `SdlcGraph`.
- **Vector Embeddings (`INV-08`):** `DocumentChunks.embedding` uses `ARRAY<FLOAT32>(vector_length=>768)` (`3.0 KiB/vector` — half the footprint of `FLOAT64` and required for optimal ScaNN memory efficiency).
- **Tiered Storage & TTL:** `CREATE LOCALITY GROUP sdlc_cold_archive OPTIONS (storage = 'ssd', ssd_to_hdd_spill_timespan = '30d')` applies **only** to the cold `AgentQueryAudit.raw_payload` JSON column (keeping row-existence metadata and PK checks on SSD!), paired with `ROW DELETION POLICY (OLDER_THAN(created_at, INTERVAL 90 DAY))` — delivering **5x storage savings** (`$0.315` → `$0.063/GiB-mo` in Bangkok).
- **Custom FTS Dictionary & Virtual `TOKENIZE_JSON`:** `SdlcSynonymDict` uses the exact required 2-column schema (`Key STRING(MAX)`, `Value ARRAY<STRING(MAX)>`) with `fulltext_dictionary_table = true, fulltext_dictionary_staleness = '15s'`. **Live DDL Gotcha:** `Apis.metadata_tokens` (`TOKENIZE_JSON(metadata)`) **must** be declared virtual `HIDDEN` (never `STORED HIDDEN` — Cloud Spanner rejects `JSON derived TOKENLIST column ... may not be STORED`), and multi-line JSON DML literals in `03a_seed_synonyms_and_metadata.sql` must use triple quotes (`JSON '''{ ... }'''`).

---

## 2. Schematized (`SdlcGraph`) vs. Schemaless Comparison (`SdlcDynamicGraph`)

Inspect [`solution/sql/02_path_b_schemaless_comparison.sql`](../solution/sql/02_path_b_schemaless_comparison.sql) to compare Cloud Spanner's flexible `DYNAMIC LABEL` / `DYNAMIC PROPERTIES` model (`INV-07`):

| Aspect | Schematized (`SdlcGraph`) | Schemaless (`SdlcDynamicGraph`) |
| :--- | :--- | :--- |
| **Tables** | 6 typed node tables + 6 explicit edge tables + 4 views | 2 generic tables: `GraphNode` + interleaved `GraphEdge` (`Id, dest_id, edge_id`) |
| **Graph Isolation (`INV-07`)** | Static labels/columns only | Must live in its **own separate** `CREATE PROPERTY GRAPH` (cannot mix static & `DYNAMIC LABEL`) |
| **Case Sensitivity Guard** | Standard SQL identifiers | Stored dynamic label strings & depth-1 JSON keys **must be lowercase** (enforced via `CHECK` constraints) |
| **JSON Cleanliness** | Typed columns | `PROPERTIES ALL COLUMNS EXCEPT (properties)` prevents double-nested JSON in `SAFE_TO_JSON(n)` |
| **When to Use** | Core enterprise SDLC entities with strong SLAs, FKs, FTS, & ScaNN | Rapidly evolving, user-defined or heterogeneous external metadata graphs |

---

## 3. Strict Ingestion & 4-Stage Post-Load Lifecycle

> **Golden Rule of Bulk Loading:** **Never** create secondary indexes, `SEARCH INDEX`, or ScaNN `VECTOR INDEX` on an empty table before bulk loading! Creating `IdxDocumentChunksEmbedding` *after* loading lets ScaNN train k-means centroids over real embeddings and keeps DDL LRO backfills fast.

```text
Step 1: Base DDL (01_path_a_schematized.sql) — tables, FKs, views, SdlcGraph (ZERO secondary/FTS/ScaNN indexes)
Step 2: Path A Load — Nodes FIRST (Owners -> Services -> Apis -> Adrs -> Requirements), Edges SECOND (< 80k mutations/commit)
Step 3: Path B Load — Chunk 45 Markdown docs (152 chunks), extract 1,160 mentions, embed via Vertex AI asia-southeast1 (768-dim FLOAT32)
Step 4: Post-Load Orchestrator (`apply_postload.py`):
        Stage 1: 03a_seed_synonyms_and_metadata.sql (8 DML statements: SdlcSynonymDict + Apis.metadata JSON)
        Stage 2: 03b_remote_models.sql.tmpl (CREATE MODEL SdlcTextEmbedding in asia-southeast1; NEVER global!)
                 [Optional: 03c reranker + 03d throttled PDML SAFE.ML.PREDICT backfill for any NULL embeddings]
        Stage 3: 03_indexes_fts_vector_postload.sql (20 DDL statements batched into 2x10 LRO batches — respects Spanner's 10-backfill-per-batch cap!)
        Stage 4: ANALYZE DDL LRO (refreshes Query Optimizer statistics)
```

### Key Post-Load Index Patterns (`03_indexes_fts_vector_postload.sql`) & Live Bangkok Timings
- **Remote Reverse Covering Indexes (Verified Live):** `CREATE INDEX IdxApiCalledByService ON ServiceCallsApi (api_id, service_id) STORING (call_mode, is_critical, migration_status, rps_estimate), INTERLEAVE IN Apis;` — non-parent `INTERLEAVE IN Apis` (without `PARENT`) co-locates incoming `CALLS_API` edges directly on the destination `Api` split! Never repeat PK columns inside `STORING`.
- **Multi-Modal Search Indexes:** `CREATE SEARCH INDEX IdxApisSearch ON Apis (search_tokens, path_ngrams, path_lower_ngrams, metadata_tokens, latency_tokens) STORING (...)` — all `TOKENLIST` columns queried together must reside in the same search index.
- **ScaNN Vector Index (`INV-08`) & `ML.PREDICT` Matryoshka Sizing:**
  ```sql
  CREATE VECTOR INDEX IdxDocumentChunksEmbedding
    ON DocumentChunks (embedding)
    STORING (doc_type, entity_id, source_path, section_heading)
    WHERE embedding IS NOT NULL
    OPTIONS (distance_type = 'COSINE');
  ```
  Omit manual `tree_depth`/`num_leaves` so Cloud Spanner Enterprise auto-tunes ScaNN partitions, and keep `STORING` compact (never store huge `content` blobs inside the vector index). In `03d_backfill_embeddings_pdml.sql`, always pass `STRUCT(768 AS outputDimensionality)` as the 3rd argument to `SAFE.ML.PREDICT` (otherwise `gemini-embedding-001` defaults to `3072` dimensions).
- **Live-Verified Bangkok (`100` PU) Timings:** Base DDL create **~2 min**, Path A + `SdlcDynamicGraph` load **<5s** (`domain_key = node_id`, no label prefix, so Hero Query C matches `@api_id`), Path B + 152 Singapore Vertex AI embeddings **~22s**, and 4-stage `apply-postload` (20 indexes + 5 FTS + ScaNN + `ANALYZE`) **~10 min** (*tip: kick off `apply-postload` right before the morning break, or temporarily use `300` PU during live demos*).

### Running Pipelines Offline (`--dry-run`) and Live (Bangkok + Singapore)

```bash
# Offline zero-dependency validation of all Path A/B loaders + 4-stage post-load
make dry-run-pipelines

# Optional Track B Dataflow bulk-load dry-run (GCS_Text_to_Cloud_Spanner in asia-southeast3)
bash solution/pipelines/dataflow_migration.sh --dry-run

# Live execution against your Bangkok sandbox (after `make provision-gcp`)
python3 solution/pipelines/neo4j_to_spanner_loader.py
python3 solution/pipelines/unstructured_graphrag_ingest.py
bash scripts/provision_workshop_env.sh apply-postload

# Optional: demonstrate throttled PDML in-DB embedding backfill (if loaded with --skip-embeddings)
gcloud spanner databases execute-sql sdlc_knowledge_graph \
  --instance=sdlc-kg-enterprise \
  --enable-partitioned-dml \
  --sql="$(cat solution/sql/03d_backfill_embeddings_pdml.sql)"
```

---

## 4. Hands-On Exercises — Lab 3 (`starter/`)

Complete the `TODO(Lab 3)` blocks across all **4 starter files**:

1. **`starter/sql/01_path_a_schematized.sql`**: Add `ServiceCallsApi` (`INTERLEAVE IN PARENT Services ON DELETE CASCADE` + destination FK), standalone dual-FK `ServiceDependsOnService` (respecting `INV-06`), `ChunkCitesApiView` (`SQL SECURITY INVOKER`), and `CREATE OR REPLACE PROPERTY GRAPH SdlcGraph`.
2. **`starter/sql/03_indexes_fts_vector_postload.sql`**: Create `IdxApiCalledByService` (`STORING (...)`, `INTERLEAVE IN Apis`), `CREATE SEARCH INDEX IdxDocumentChunksSearch`, and `CREATE VECTOR INDEX IdxDocumentChunksEmbedding ... WHERE embedding IS NOT NULL`.
3. **`starter/pipelines/neo4j_to_spanner_loader.py`**: Implement `load_graph()` with nodes-before-edges ordering and `500`-row batching strictly under Spanner's `80,000` mutations/commit ceiling (with zero-dependency `--dry-run` support).
4. **`starter/pipelines/unstructured_graphrag_ingest.py`**: Implement `extract_chunks_and_mentions()` to chunk the 45 Markdown docs, extract `api-*`, `svc-*`, `ADR-*`, `REQ-*`, and `team-*` mentions, and enforce regional `asia-southeast1` Vertex AI validation (`INV-09`).

### Antigravity IDE Prompt (Copy-Paste)

> *"Using the `spanner-schema-and-data-modeling` and `spanner-graph-modeling-and-gql` skills, help me complete all `TODO(Lab 3)` tasks in `starter/sql/01_path_a_schematized.sql`, `starter/sql/03_indexes_fts_vector_postload.sql`, `starter/pipelines/neo4j_to_spanner_loader.py`, and `starter/pipelines/unstructured_graphrag_ingest.py`. Enforce `INV-06` (no self-ref dual cascade), `INV-08` (`ARRAY<FLOAT32>(vector_length=>768)` + `WHERE embedding IS NOT NULL`), and `< 80,000` mutations/commit. Do not edit `solution/`."*

### Verify Lab 3 Offline

```bash
# Track B (Full: SQL DDL/Indexes + both Python loaders)
make TRACK=starter verify-lab LAB=3

# Track A (4-Hour Express: SQL DDL/Indexes 01 & 03 only)
make TRACK=starter MODE=express verify-lab LAB=3
```

**Checkpoint Expected Output:** `[PASS] Lab 3 (starter/)` — all 4 SQL & Python loader checks green!

---

**Navigation:** [← Module 2: Two-Path SDLC Convergence](02_two_path_sdlc_workshop.md) | [README](../README.md) | [Module 4: Search Strategy Options →](04_search_strategy_options.md)
