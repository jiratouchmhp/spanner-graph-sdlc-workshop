# Lab 3: Database Schema Design, Post-Load Indexes & Ingestion

In this lab, you'll complete the Cloud Spanner Enterprise schema and property graph DDL, define post-load covering, full-text, and ScaNN vector indexes, and (on Track B) implement both the Path A and Path B ingestion pipelines. Finish your `starter/` edits first (Steps 1–4), then optionally deploy and load your own schema into Bangkok (`asia-southeast3`) in Step 5.

## What You'll Work On

| Step | File to open (`starter/`) | Where to edit (`TODO` / line) | Track |
| :-: | :--- | :--- | :-: |
| 1 | [`starter/sql/01_path_a_schematized.sql`](../starter/sql/01_path_a_schematized.sql) | `ServiceCallsApi` (`L213`), `ServiceDependsOnService` (`L278`), `ChunkCitesApiView` (`L308`), `SdlcGraph` (`L348`) | Track A & B |
| 2 | [`starter/sql/03_indexes_fts_vector_postload.sql`](../starter/sql/03_indexes_fts_vector_postload.sql) | `IdxApiCalledByService` (`L25`), `IdxDocumentChunksSearch` (`L129`), `IdxDocumentChunksEmbedding` (`L140`) | Track A & B |
| 3 | [`starter/pipelines/neo4j_to_spanner_loader.py`](../starter/pipelines/neo4j_to_spanner_loader.py) | `load_graph()` (`L377`, `TODO` at `L389`, stub `L439`) | Track B |
| 4 | [`starter/pipelines/unstructured_graphrag_ingest.py`](../starter/pipelines/unstructured_graphrag_ingest.py) | `extract_chunks_and_mentions()` (`L224`, `TODO` at `L233`, stub `L268`) | Track B |
| 5 | Live Bangkok GCP deploy & load | `provision_workshop_env.sh`, loaders, `apply-postload` | Optional (Live GCP) |

- **Estimated time:** ~45 min (Track A Express) / ~90 min (Track B Full)
- **Reference copies (read-only if stuck):** [`01_path_a_schematized.sql`](../solution/sql/01_path_a_schematized.sql), [`03_indexes_fts_vector_postload.sql`](../solution/sql/03_indexes_fts_vector_postload.sql), [`neo4j_to_spanner_loader.py`](../solution/pipelines/neo4j_to_spanner_loader.py), [`unstructured_graphrag_ingest.py`](../solution/pipelines/unstructured_graphrag_ingest.py)
- **Antigravity skills:** [`spanner-schema-and-data-modeling`](../.agents/skills/spanner-schema-and-data-modeling/SKILL.md), [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md) (support: [`spanner-enterprise-platform-ops`](../.agents/skills/spanner-enterprise-platform-ops/SKILL.md), [`spanner-search-ai-and-graphrag`](../.agents/skills/spanner-search-ai-and-graphrag/SKILL.md), [`spanner-migration-and-neo4j-playbook`](../.agents/skills/spanner-migration-and-neo4j-playbook/SKILL.md))

---

## Quick Background

**Typed vs. schemaless graphs:** Our primary schema (`SdlcGraph`) uses typed tables so we get enforced foreign keys, covering reverse indexes, full-text search, and ScaNN vector search. If you want to compare Spanner's flexible `DYNAMIC LABEL` / `DYNAMIC PROPERTIES` layout, inspect [`solution/sql/02_path_b_schemaless_comparison.sql`](../solution/sql/02_path_b_schemaless_comparison.sql) — Spanner requires dynamic-label tables to live in their own separate property graph (`SdlcDynamicGraph`, `INV-07`).

**Why load data *before* creating indexes:** Never build secondary, full-text, or ScaNN vector indexes on empty tables. Bulk-load all nodes, edges, chunks, and embeddings first, then apply `03_indexes_fts_vector_postload.sql` so ScaNN trains its k-means centroids on real vectors and index backfills complete in one pass.

**What `apply-postload` automates for you:** When you run post-load in Step 5, [`solution/pipelines/apply_postload.py`](../solution/pipelines/apply_postload.py) executes [`03a_seed_synonyms_and_metadata.sql`](../solution/sql/03a_seed_synonyms_and_metadata.sql) (populates `SdlcSynonymDict` and `Apis.metadata`), [`03b_remote_models.sql.tmpl`](../solution/sql/03b_remote_models.sql.tmpl) (registers `gemini-embedding-001` in Singapore `asia-southeast1` — never `global`, per `INV-09`), batches your 20 index DDL statements into 10-statement LROs, and runs `ANALYZE`. Optional [`03d_backfill_embeddings_pdml.sql`](../solution/sql/03d_backfill_embeddings_pdml.sql) shows throttled in-DB Partitioned DML embedding backfill if you ever load chunks with `--skip-embeddings`.

---

## Step-by-Step Hands-On

### Step 1: Complete the schema and `SdlcGraph` in `starter/sql/01_path_a_schematized.sql` (Track A & B)

Open [`starter/sql/01_path_a_schematized.sql`](../starter/sql/01_path_a_schematized.sql). The 5 node tables, `DocumentChunks` (`ARRAY<FLOAT32>(vector_length=>768)`, `INV-08`), `AgentQueryAudit` (with `sdlc_cold_archive` SSD-to-HDD locality group and 90-day TTL), and `SdlcSynonymDict` are already provided.

1. **`ServiceCallsApi` (`L213`):**
   Add the `Services -> Apis` edge table with `PRIMARY KEY (service_id, api_id)`, `INTERLEAVE IN PARENT Services ON DELETE CASCADE`, and a cascading FK `fk_service_calls_api_dest` referencing `Apis (api_id)`. Interleaving under `Services` co-locates outgoing calls on the parent service's split and works cleanly because source (`Services`) != destination (`Apis`).
2. **`ServiceDependsOnService` (`L278`):**
   Add the `Services -> Services` edge table as a **standalone** table with `PRIMARY KEY (from_service_id, to_service_id)` and two cascading FKs (`fk_svc_dep_from` and `fk_svc_dep_to`). Here's why: Spanner won't let you put `ON DELETE CASCADE` on both `INTERLEAVE IN PARENT` and a self-referencing foreign key on the same table (`INV-06`).
3. **`ChunkCitesApiView` (`L308`):**
   Create the bridge view over `ChunkEntityMentions` filtered to `entity_type = 'API'`, declaring explicit `SQL SECURITY INVOKER` (`INV-05`).
4. **`CREATE OR REPLACE PROPERTY GRAPH SdlcGraph` (`L348`):**
   Unite all 6 node tables and 14 edge definitions:
   - 6 explicit M:N edge tables (`ServiceCallsApi`, `ServiceDependsOnService`, `AdrGovernsApi`, `AdrSupersedesAdr`, `ReqAppliesToApi`, `ReqAppliesToService`)
   - 4 dual-projected folded FK edges using unique `AS` aliases (`Apis AS ExposesApi`, `Services AS TeamOwnsService`, `Adrs AS TeamOwnsAdr`, `Requirements AS TeamOwnsReq`) matching exact PK arity and property types (`INV-02`, `INV-03`, `INV-04`)
   - 4 view-backed Path B citation edges (`ChunkCitesApiView`, `ChunkCitesServiceView`, `ChunkCitesAdrView`, `ChunkCitesReqView`) with explicit `KEY (chunk_id, entity_type, entity_id)` (`INV-05`)

*(Optional Antigravity prompt)*:
> "Help me complete the four `TODO(Lab 3)` blocks in `starter/sql/01_path_a_schematized.sql` using `spanner-schema-and-data-modeling` and `spanner-graph-modeling-and-gql`. Keep `ServiceDependsOnService` standalone to avoid the dual-cascade error (`INV-06`), add `SQL SECURITY INVOKER` on `ChunkCitesApiView`, and wire all 6 node tables and 14 edges into `SdlcGraph`."

### Step 2: Add post-load indexes in `starter/sql/03_indexes_fts_vector_postload.sql` (Track A & B)

Open [`starter/sql/03_indexes_fts_vector_postload.sql`](../starter/sql/03_indexes_fts_vector_postload.sql).

1. **`IdxApiCalledByService` (`L25`):**
   Create a reverse covering index on `ServiceCallsApi (api_id, service_id)` with `STORING (call_mode, is_critical, migration_status, rps_estimate)` and `INTERLEAVE IN Apis` (without `PARENT`). That places inbound callers on the target API's split so blast-radius queries avoid full table scans. Don't repeat PK columns inside `STORING`.
2. **`IdxDocumentChunksSearch` (`L129`):**
   Create a `SEARCH INDEX` on `DocumentChunks (content_tokens, content_ngrams)` with `STORING (doc_type, entity_id, source_path, section_heading, content)` so full-text `SNIPPET(...)` and fuzzy trigram queries share one index with zero base-table lookups.
3. **`IdxDocumentChunksEmbedding` (`L140`):**
   Create a ScaNN `VECTOR INDEX` on `DocumentChunks (embedding)` storing compact metadata only (`STORING (doc_type, entity_id, source_path, section_heading)` — omit the large `content` text), add the mandatory partial-index filter `WHERE embedding IS NOT NULL` (`INV-08`), and set `OPTIONS (distance_type = 'COSINE')`. Omit manual `tree_depth` and `num_leaves` so Cloud Spanner Enterprise auto-tunes ScaNN partitions.

*(Optional Antigravity prompt)*:
> "Complete the three `TODO(Lab 3)` index statements in `starter/sql/03_indexes_fts_vector_postload.sql` (`IdxApiCalledByService`, `IdxDocumentChunksSearch`, and `IdxDocumentChunksEmbedding` with `WHERE embedding IS NOT NULL`)."

Quick check (Track A Express — checks both SQL files):

```bash
make TRACK=starter MODE=express verify-lab LAB=3
```

### Step 3: Implement the Path A loader in `starter/pipelines/neo4j_to_spanner_loader.py` (Track B — skip on Track A)

Skip Steps 3 and 4 if you're on Track A Express.

1. Open [`starter/pipelines/neo4j_to_spanner_loader.py`](../starter/pipelines/neo4j_to_spanner_loader.py) and jump to `load_graph()` (`L377`, `TODO` at `L389`, stub `L439`).
2. Iterate through `NODE_LOAD_SPECS` first (`Owners -> Services -> Apis -> Adrs -> Requirements`, 68 rows) using `read_and_validate_table()` so `known_pks` collects every parent primary key before any edge loads.
3. Iterate through `EXPLICIT_EDGE_LOAD_SPECS` second (174 rows across 6 edge tables), validating foreign keys against `known_pks`.
4. Use `compute_safe_batch_size(batch_size, len(col_names))` and `chunk_rows()` (500 rows/commit default) so every commit stays well under Spanner's 80,000 mutations/commit limit. Keep `google.cloud.spanner` lazily imported inside `if not dry_run:` so `--dry-run` works with Python's stdlib alone.
5. Test immediately:
   ```bash
   python3 starter/pipelines/neo4j_to_spanner_loader.py --dry-run
   ```

### Step 4: Implement Path B chunking & extraction in `starter/pipelines/unstructured_graphrag_ingest.py` (Track B — skip on Track A)

1. Open [`starter/pipelines/unstructured_graphrag_ingest.py`](../starter/pipelines/unstructured_graphrag_ingest.py) and jump to `extract_chunks_and_mentions()` (`L224`, `TODO` at `L233`, stub `L268`).
2. Discover all 45 `.md` files under `unstructured_dir`, load valid entity IDs with `load_canonical_entity_ids()`, split each file by H2 (`## `) sections (handling `owners/team_charters.md` and long sections via `_split_oversized_section()`), and build the `DocumentChunks` rows.
3. Run every regex in `ENTITY_MENTION_PATTERNS` (`api-*`, `svc-*`, `ADR-*`, `REQ-*`, `team-*`) over each chunk, filter against canonical IDs, and emit deduplicated `ChunkEntityMentions` dicts. The provided `generate_vertex_embeddings()` helper handles 768-dim `FLOAT32` embeddings against Singapore (`asia-southeast1`) Vertex AI while blocking `global`.
4. Test immediately:
   ```bash
   python3 starter/pipelines/unstructured_graphrag_ingest.py --dry-run
   ```
   You can also run `make dry-run-pipelines` to dry-run the full end-to-end reference pipeline chain offline (and `bash solution/pipelines/dataflow_migration.sh --dry-run` to preview the Bangkok `asia-southeast3` Dataflow bulk-load job for larger datasets).

*(Optional Antigravity prompt for Steps 3–4)*:
> "Help me implement `load_graph()` in `starter/pipelines/neo4j_to_spanner_loader.py` and `extract_chunks_and_mentions()` in `starter/pipelines/unstructured_graphrag_ingest.py` so both `--dry-run` commands pass with Python stdlib only."

### Step 5 (Optional — Live Bangkok GCP): Provision, load, and build post-load indexes

Finish Steps 1–4 and pass `verify-lab LAB=3` first, then deploy **your** `starter/` files to Bangkok (`regional-asia-southeast3`). Vertex AI embeddings call Singapore (`asia-southeast1`) automatically because `gemini-embedding-001` isn't hosted in Bangkok.

```bash
# 1. Create the 100-PU Bangkok sandbox and apply your starter/sql/01_path_a_schematized.sql
#    (--skip-fgac defers Lab 6's 04_fgac_roles_views.sql until Lab 6)
bash scripts/provision_workshop_env.sh create --track starter --skip-fgac

# 2. Load Path A (nodes -> edges) and Path B (45 Markdown docs + Singapore Vertex AI embeddings)
#    (Track A shortcut: use solution/pipelines/*.py instead of starter/pipelines/*.py)
python3 starter/pipelines/neo4j_to_spanner_loader.py
python3 starter/pipelines/unstructured_graphrag_ingest.py

# 3. Run 4-stage post-load (03a synonyms -> 03b asia-southeast1 model -> your starter/sql/03 indexes -> ANALYZE)
#    Takes ~10 min on 100 PU — start this right before the morning break!
bash scripts/provision_workshop_env.sh apply-postload --track starter
```

*(Short on time? Run `make provision-gcp TRACK=solution`, then the two `solution/pipelines/*.py` loaders and `bash scripts/provision_workshop_env.sh apply-postload --track solution`. Once Lab 6's `04_fgac_roles_views.sql` is solved, `make provision-gcp TRACK=starter` also works without `--skip-fgac`.)*

---

## Verify This Lab

```bash
# Track B (Full — checks SQL 01 & 03 + both Python loaders via --dry-run)
make TRACK=starter verify-lab LAB=3

# Track A (Express — checks SQL 01 & 03 only)
make TRACK=starter MODE=express verify-lab LAB=3
```

Passing output looks like this:

```text
[PASS] Lab 3 (starter/, mode=full)
    + Verified core DDL & SdlcGraph in starter/sql/01_path_a_schematized.sql
    + Verified post-load covering/FTS/ScaNN indexes in starter/sql/03_indexes_fts_vector_postload.sql
    + starter/pipelines/neo4j_to_spanner_loader.py --dry-run succeeded
    + starter/pipelines/unstructured_graphrag_ingest.py --dry-run succeeded
[OK] Verified 1/1 lab(s) on track=starter (mode=full).
```

**If it fails, check:**
- **Dual-cascade error (`INV-06`):** `ServiceDependsOnService` must *not* include `INTERLEAVE IN PARENT Services`; define it as a standalone table with two cascading FKs.
- **Missing ScaNN predicate (`INV-08`):** `IdxDocumentChunksEmbedding` must include `WHERE embedding IS NOT NULL`.
- **Loader `--dry-run` `ImportError`:** Keep `from google.cloud import spanner` inside `if not dry_run:` so offline verification runs without external packages.

---

**Previous / Next:** [← Lab 2: Two-Path SDLC Corpus & Regional Setup](02_two_path_sdlc_workshop.md) | [Starter Checklist](../starter/README.md) | [Lab 4: Search Strategies & Graph Algorithms →](04_search_strategy_options.md)
