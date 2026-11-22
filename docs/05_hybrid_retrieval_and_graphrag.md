# Module 5: Hybrid Retrieval (RRF/RSF/Filtered) & Hero Blast-Radius GraphRAG

**Customer Requirement 5:** Combine **Full-Text Search**, **ScaNN Vector Search**, and **cycle-safe ISO GQL `MATCH TRAIL`** inside a single read-only Cloud Spanner Enterprise query to answer Acme Commerce's **Hero Blast-Radius Question** with deterministic impacted services, governing ADRs, compliance requirements, verifiable Markdown citations, and **zero cross-store synchronization lag**.

- **Duration:** Track A (4h Express): `45 min` | Track B (8h Deep-Dive): `75 min`
- **Primary Antigravity Skills:** [`spanner-search-ai-and-graphrag`](../.agents/skills/spanner-search-ai-and-graphrag/SKILL.md), [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md)
- **Support Skills:** [`spanner-performance-observability-and-sizing`](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md), [`sdlc-graphrag-workshop-guide`](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md)
- **Hands-On Starter Files (`2` files with `TODO(Lab 5)`):**
  1. [`starter/sql/06_hybrid_rrf_rsf_filtered.sql`](../starter/sql/06_hybrid_rrf_rsf_filtered.sql)
  2. [`starter/sql/07_hero_api_blast_radius_graphrag.sql`](../starter/sql/07_hero_api_blast_radius_graphrag.sql)
- **Reference Solution:** [`solution/sql/06_hybrid_rrf_rsf_filtered.sql`](../solution/sql/06_hybrid_rrf_rsf_filtered.sql), [`solution/sql/07_hero_api_blast_radius_graphrag.sql`](../solution/sql/07_hero_api_blast_radius_graphrag.sql), [`solution/sql/fixtures/07_hero_expected_rows.json`](../solution/sql/fixtures/07_hero_expected_rows.json), [`labs/run_hero_query.py`](../labs/run_hero_query.py)

---

## 1. Four Production Hybrid Retrieval Patterns (`06_hybrid_rrf_rsf_filtered.sql`)

Single-modality retrieval misses critical SDLC context: FTS nails exact tokens (`tok_pci_v2`, `ADR-004`, `45ms`) while vectors surface conceptual prose (*"cardholder vault fail-closed risk policy"*). Cloud Spanner Enterprise fuses both **server-side inside one read-only SQL query**:

| Pattern | Query in `06_hybrid_rrf_rsf_filtered.sql` | Key Cloud Spanner GoogleSQL Pattern | When to Choose |
| :--- | :--- | :--- | :--- |
| **1. Server-Side RRF (`k = 60`)** | Query 1 | `UNNEST(ARRAY(SELECT chunk_id FROM vec_top ORDER BY approx_distance ASC)) AS chunk_id WITH OFFSET AS rank` (enforces `INV-09`: **zero `ROW_NUMBER()`**!) | Best general-purpose default — immune to incomparable BM25 vs. cosine score scales. |
| **2. Relative Score Fusion (RSF)** | Query 2 | Weighted min-max normalization (`0.60` semantic + `0.40` FTS) with `SAFE_DIVIDE(..., NULLIF(max_s - min_s, 0.0))` | When continuous score confidence margins matter and weights are tuned on evals. |
| **3. Filtered Search (FTS → KNN)** | Query 3 | Lexical `SEARCH(...)` narrows to `LIMIT 1000`, then exact `COSINE_DISTANCE` re-ranks survivors | When domain/compliance terms are hard inclusion filters before semantic ranking. |
| **4. Two-Stage Reranking** | Queries 4A & 4B | **4A:** Stage 1 ScaNN top-30 → `ML.PREDICT(MODEL SdlcReranker, ...)` (`03c`). **4B (Endpoint-Free Fallback):** Stage 1 RRF top-25 → exact `COSINE_DISTANCE` rescoring | High-precision top-5 citations for executive/compliance agent responses. |

### Why `UNNEST(ARRAY(...)) WITH OFFSET` Replaces `ROW_NUMBER()` (`INV-09`)
Cloud Spanner GoogleSQL does **not** support general analytic window functions such as `ROW_NUMBER() OVER (...)`. Instead, materialize the ordered top-K CTE into an `ARRAY` subquery and unnest it with `WITH OFFSET AS rank` (`0`-based, so `1.0 / (60.0 + rank + 1.0)` gives exact 1-based RRF):

```sql
SELECT chunk_id, SUM(1.0 / (60.0 + rank + 1.0)) AS rrf_score
FROM (
  SELECT chunk_id, rank
  FROM UNNEST(ARRAY(SELECT chunk_id FROM vec_top ORDER BY approx_distance ASC)) AS chunk_id WITH OFFSET AS rank
  UNION ALL
  SELECT chunk_id, rank
  FROM UNNEST(ARRAY(SELECT chunk_id FROM fts_top ORDER BY fts_score DESC)) AS chunk_id WITH OFFSET AS rank
)
GROUP BY chunk_id
```

---

## 2. Hero Blast-Radius GraphRAG Contract (`07_hero_api_blast_radius_graphrag.sql`)

**Hero Question:** *"What breaks if we change `POST /v2/payments/charge` (`api-payments-charge-v2`)?"*

[`solution/sql/07_hero_api_blast_radius_graphrag.sql`](../solution/sql/07_hero_api_blast_radius_graphrag.sql) ships three interoperable variants backed by [`labs/run_hero_query.py`](../labs/run_hero_query.py):
- **Query A (`--query A`):** Exact KNN (`COSINE_DISTANCE` `@{scan_method=batch}`) + FTS RRF + `GRAPH_TABLE(SdlcGraph MATCH TRAIL ...)`
- **Query B (`--query B`, Production Default):** CTE-isolated ScaNN ANN (`APPROX_COSINE_DISTANCE` with `WHERE embedding IS NOT NULL`, `INV-08`) + FTS RRF + `GRAPH_TABLE(SdlcGraph MATCH TRAIL ...)`
- **Query C (`--query C`):** Schemaless equivalent over `SdlcDynamicGraph` (`INV-07`) + `SAFE_TO_JSON(p)` path visualization

### Expected Deterministic 7-Row Impact Output (`07_hero_expected_rows.json`)

Sorted strictly by `hop_distance ASC`, `impact_category_rank ASC` (`DIRECT_SYNC=1`, `TRANSITIVE_UPSTREAM=2`, `DATA_FEED_CONSUMER=3`), `criticality_rank ASC` (`CRITICAL=1`, `HIGH=2`, `MEDIUM=3`, `LOW=4`), `impacted_service_id ASC`:

| # | `impacted_service_id` | Hop | `impact_category` | Tier / Criticality | Owner (`slack_channel`) | `has_legacy_v1_risk` |
| :-: | :--- | :-: | :--- | :--- | :--- | :-: |
| 1 | `svc-billing` | 1 | `DIRECT_SYNC` | `TIER_0` / `CRITICAL` | `team-billing` (`#billing-ledger`) | **`TRUE`** *(calls `DEPRECATED` `api-payments-charge-v1`, `AT_RISK`)* |
| 2 | `svc-checkout` | 1 | `DIRECT_SYNC` | `TIER_0` / `CRITICAL` | `team-checkout` (`#checkout-orders`) | `FALSE` |
| 3 | `svc-orders` | 2 | `TRANSITIVE_UPSTREAM` | `TIER_1` / `HIGH` | `team-checkout` (`#checkout-orders`) | `FALSE` |
| 4 | `svc-partner-gw` | 2 | `TRANSITIVE_UPSTREAM` | `TIER_1` / `HIGH` | `team-integrations` (`#partner-messaging`) | `FALSE` |
| 5 | `svc-notifications` | 3 | `TRANSITIVE_UPSTREAM` | `TIER_2` / `MEDIUM` | `team-integrations` (`#partner-messaging`) | `FALSE` |
| 6 | `svc-webhooks` | 3 | `TRANSITIVE_UPSTREAM` | `TIER_2` / `MEDIUM` | `team-integrations` (`#partner-messaging`) | `FALSE` |
| 7 | `svc-analytics` | 3 | `DATA_FEED_CONSUMER` | `TIER_2` / `LOW` | `team-data` (`#data-analytics`) | `FALSE` |

- **5 Governing ADRs:** `ADR-004` (`MANDATES`), `ADR-005` (`CONSTRAINS`), `ADR-006` (`CONSTRAINS`), `ADR-008` (`CONSTRAINS`), `ADR-010` (`CONSTRAINS`).
- **5 Applicable Requirements:** `REQ-001` (PCI-DSS 4.0), `REQ-002` (Tier-0 SLA & `45ms` fraud budget), `REQ-003` (`> $500` fail-closed), `REQ-005` (SOX `Idempotency-Key`), `REQ-006` (Zero-Trust mTLS/OIDC).
- **9 Verifiable Markdown Citations:** `ADR-004.md`, `ADR-010.md`, `api-payments-charge-v2.md`, `api-payments-charge-v1.md`, `REQ-001.md`, `REQ-002.md`, `REQ-003.md`, `REQ-005.md`, `REQ-006.md`.

---

## 3. Running the Hero Query Offline & Live (`labs/run_hero_query.py`)

```bash
# 1. Inspect available query variants (A, B, C, all)
python3 labs/run_hero_query.py --list

# 2. Offline zero-dependency dry-run & exact 7-row fixture parity check
python3 labs/run_hero_query.py \
  --dry-run \
  --query B \
  --check-fixture solution/sql/fixtures/07_hero_expected_rows.json

# 3. Emit executable SQL for pasting into Spanner Studio
python3 labs/run_hero_query.py --query B --emit-sql

# 4. Live execution against Bangkok Cloud Spanner (asia-southeast3, sole DB region) + Singapore companion Vertex AI (asia-southeast1)
python3 labs/run_hero_query.py \
  --query B \
  --api-id api-payments-charge-v2 \
  --check-fixture solution/sql/fixtures/07_hero_expected_rows.json
```

---

## 4. Live "Aha!" Exercise: Zero-Sync-Lag ACID Proof (Track B)

In a legacy 3-store stack (Neo4j + Vector DB + OpenSearch), adding a new service caller and design doc requires waiting for CDC connectors, embedding workers, and index refreshes — risking stale change-impact approvals. Prove Cloud Spanner's **single-commit TrueTime consistency** live:

```bash
# Step 1: Confirm baseline Hero Query B returns exactly 7 impacted services
python3 labs/run_hero_query.py --query B --api-id api-payments-charge-v2

# Step 2: Insert a new CALLS_API edge + a new Markdown chunk & mention atomically
gcloud spanner databases execute-sql sdlc_knowledge_graph \
  --instance=sdlc-kg-enterprise \
  --sql="INSERT INTO ServiceCallsApi (service_id, api_id, call_mode, is_critical, migration_status, rps_estimate)
         VALUES ('svc-inventory', 'api-payments-charge-v2', 'SYNC', TRUE, 'MIGRATED', 75)"

gcloud spanner databases execute-sql sdlc_knowledge_graph \
  --instance=sdlc-kg-enterprise \
  --sql="INSERT INTO DocumentChunks (chunk_id, doc_type, entity_id, source_path, section_heading, chunk_index, content, content_hash)
         VALUES ('demo__live_acid_c00', 'api', 'api-payments-charge-v2', 'data/unstructured/apis/live-acid-demo.md',
                 'Live Zero-Sync-Lag Proof', 0,
                 'svc-inventory now invokes POST /v2/payments/charge (api-payments-charge-v2) for instant stock settlement with tok_pci_v2.',
                 'live-acid-demo-hash')"

gcloud spanner databases execute-sql sdlc_knowledge_graph \
  --instance=sdlc-kg-enterprise \
  --sql="INSERT INTO ChunkEntityMentions (chunk_id, entity_type, entity_id, mention_source)
         VALUES ('demo__live_acid_c00', 'API', 'api-payments-charge-v2', 'LIVE_DEMO'),
                ('demo__live_acid_c00', 'SERVICE', 'svc-inventory', 'LIVE_DEMO')"

# Step 3: Re-run Hero Query B IMMEDIATELY (next second!)
# Observe: 8 impacted services (svc-inventory at hop=1 DIRECT_SYNC) + live-acid-demo.md citation!
python3 labs/run_hero_query.py --query B --api-id api-payments-charge-v2

# Step 4: Clean up the live demo rows to restore exact 7-row workshop parity
gcloud spanner databases execute-sql sdlc_knowledge_graph \
  --instance=sdlc-kg-enterprise \
  --sql="DELETE FROM ServiceCallsApi WHERE service_id = 'svc-inventory' AND api_id = 'api-payments-charge-v2'"
gcloud spanner databases execute-sql sdlc_knowledge_graph \
  --instance=sdlc-kg-enterprise \
  --sql="DELETE FROM DocumentChunks WHERE chunk_id = 'demo__live_acid_c00'"
```

*(Notice `DELETE FROM DocumentChunks` automatically cascades `ChunkEntityMentions` via `INTERLEAVE IN PARENT DocumentChunks ON DELETE CASCADE`!)*

---

## 5. Hands-On Exercises — Lab 5 (`starter/`)

Complete all `TODO(Lab 5)` blocks in both starter files:

1. **`starter/sql/06_hybrid_rrf_rsf_filtered.sql`**: Implement server-side RRF (`k = 60`) using `UNNEST(ARRAY(...)) WITH OFFSET` (zero `ROW_NUMBER()`), RSF with `NULLIF`, and CTE-isolated `APPROX_COSINE_DISTANCE ... WHERE embedding IS NOT NULL`.
2. **`starter/sql/07_hero_api_blast_radius_graphrag.sql`**: Complete the Hero Query combining single-table ScaNN ANN, FTS RRF, cycle-safe `GRAPH_TABLE(SdlcGraph MATCH TRAIL ...)`, `DATA_FEED_CONSUMER` separation, `AdrGovernsApi`, `ReqAppliesToApi`, and `ChunkEntityMentions`.

### Antigravity IDE Prompt (Copy-Paste)

> *"Using the `spanner-search-ai-and-graphrag` and `spanner-graph-modeling-and-gql` skills, help me complete `TODO(Lab 5)` in `starter/sql/06_hybrid_rrf_rsf_filtered.sql` and `starter/sql/07_hero_api_blast_radius_graphrag.sql`. Enforce `INV-08` (single-table ScaNN CTE with `WHERE embedding IS NOT NULL`), `INV-09` (RRF via `UNNEST(ARRAY(...)) WITH OFFSET`, zero `ROW_NUMBER()`), and `MATCH TRAIL` with `DATA_FEED_CONSUMER` separation. Do not edit `solution/`."*

### Verify Lab 5 Offline

```bash
make TRACK=starter verify-lab LAB=5
```

**Checkpoint Expected Output:** `[PASS] Lab 5 (starter/)` — hybrid RRF/RSF SQL, Hero GraphRAG SQL, and exact 7-row fixture replay all pass!

---

**Navigation:** [← Module 4: Search Strategy Options](04_search_strategy_options.md) | [README](../README.md) | [Module 6: MCP Toolbox & Agent Security →](06_mcp_toolbox_agent_security.md)
