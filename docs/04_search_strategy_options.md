# Module 4: Multi-Model Search & Graph Algorithm Strategy Evaluation

**Customer Requirement 4:** Evaluate every native retrieval and analytical capability inside our single-region Bangkok **Cloud Spanner Enterprise** database (`regional-asia-southeast3`) side-by-side — ISO GQL traversals, Serverless Data Boost Graph Algorithms, Full-Text Search (FTS), Fuzzy N-grams, JSON/Numeric tokenized filtering, Exact KNN, and ScaNN ANN — so architects choose the optimal strategy for each SDLC query class.

- **Duration:** Track A (4h Express): `25 min` | Track B (8h Deep-Dive): `60 min`
- **Primary Antigravity Skills:** [`spanner-search-ai-and-graphrag`](../.agents/skills/spanner-search-ai-and-graphrag/SKILL.md), [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md)
- **Support Skills:** [`spanner-performance-observability-and-sizing`](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md), [`sdlc-graphrag-workshop-guide`](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md)
- **Hands-On Starter Files (`2` files with `TODO(Lab 4)`):**
  1. [`starter/sql/05_search_strategies_benchmark.sql`](../starter/sql/05_search_strategies_benchmark.sql)
  2. [`starter/sql/08_graph_algorithms_criticality.sql`](../starter/sql/08_graph_algorithms_criticality.sql)
- **Reference Solution:** [`solution/sql/05_search_strategies_benchmark.sql`](../solution/sql/05_search_strategies_benchmark.sql), [`solution/sql/08_graph_algorithms_criticality.sql`](../solution/sql/08_graph_algorithms_criticality.sql)

---

## 1. Native Cloud Spanner Enterprise Search & Graph Evaluation Matrix

All 7 benchmarks in `05_search_strategies_benchmark.sql` plus the 3 Data Boost algorithms in `08_graph_algorithms_criticality.sql` execute inside **one** Cloud Spanner Enterprise database — no separate OpenSearch or Vector DB cluster required:

| # | Strategy | Primary Function / Syntax | Recall vs. Speed Trade-Off | Txn Mode & Key Guardrail | Best SDLC Use Case |
| :-: | :--- | :--- | :--- | :--- | :--- |
| **1** | **GQL Super-Node Capping** | `FILTER IS_FIRST(5) OVER (PARTITION BY SOURCE_NODE_ID(e) ORDER BY ...)` | Deterministic top-K fanout per source node | R/O or R/W; prevents super-node explosion | Capping noisy callers on high-fanin hubs (`svc-auth`, `api-auth-token-v2`) |
| **2** | **GQL Converging Paths** | `MATCH TRAIL ... @{factorized_mode=factorize_left}` | Deduplicates converging upstream subpaths early | Avoid `COUNT(*)` over factorized arms; return per-arm cols | Multi-hop upstream blast-radius analysis over cyclic graphs |
| **3** | **Full-Text Search (FTS)** | `SEARCH(content_tokens, @q, dictionary => 'SdlcSynonymDict', enhance_query => TRUE)` + `SCORE` + `SNIPPET` | Exact lexical + domain synonym expansion (`pan` → `tok_pci_v2`, `freeze` → `180-day`) | **Read-Only txn mandatory (`INV-09`)**; bounded staleness `>= 10s` recommended | Finding exact identifiers (`ADR-004`, `tok_pci_v2`, `45ms`, `Idempotency-Key`) |
| **4** | **Fuzzy N-Gram Search** | `SEARCH_NGRAMS(content_ngrams, @typo, min_ngrams => 2)` + `SCORE_NGRAMS` | Typo-tolerant trigram matching (`paymnts chrge idempoten`) | **Read-Only txn**; always wrap inner scan with `LIMIT 10000` cap | Interactive developer search bars tolerating typos |
| **5** | **JSON + Numeric + `LIKE`** | `TOKENIZE_JSON` + `TOKENIZE_NUMBER` + `TOKENIZE_NGRAMS(LOWER(http_path))` | Single local zig-zag intersection on `IdxApisSearch` | **Read-Only txn**; literal `LIKE` pattern `>= 3` chars | Governance queries: *"PCI-scoped APIs in `asia-southeast3` with latency `<= 250ms` matching `%payments%`"* |
| **6** | **Exact KNN Vector** | `COSINE_DISTANCE(embedding, @query_vec)` with `@{scan_method=batch}` (standalone only!) | **100% exact recall**; O(N) over filtered slice | `WHERE embedding IS NOT NULL` + literal `LIMIT k`; **omit `@{scan_method=batch}` on CTEs joined downstream** (unsupported on right side of apply/merge/hash joins) | Selective slices or corpora up to tens of thousands of vectors |
| **7** | **ScaNN ANN Vector** | `APPROX_COSINE_DISTANCE(embedding, @query_vec, options => JSON '{"num_leaves_to_search": 10}')` | Sub-linear millisecond ANN over millions of chunks | **`INV-08`:** `WHERE embedding IS NOT NULL`, single-table CTE (zero `JOIN` inside ANN block), never on edges | Production-scale semantic GraphRAG retrieval (`>= 100k`–`100M+` vectors) |
| **8** | **Data Boost Graph Algorithms** | `EXPORT DATA ... CALL PER () PageRank` / `WeaklyConnectedComponents` / `ShortestPath` | Serverless isolated compute (`$1.2285/1k SPU-hr` Bangkok) — **zero OLTP CPU impact** | **Literals only (no `@param`)**; table-backed interleaved graph + `NOT ENFORCED` FK; `max_idle_time => '5m'` | Nightly architectural criticality scoring (`svc-auth` #1 hub) & topology health |

---

## 2. Deep Dive: ScaNN Vector Index Isolation (`INV-08`)

One of the most common production mistakes is joining metadata tables directly inside the `SELECT` block that calls `APPROX_COSINE_DISTANCE`. That prevents Spanner's optimizer from pushing the top-K scan into `IdxDocumentChunksEmbedding`.

Always structure ScaNN queries using a **single-table CTE** (`ann_candidates`), then join metadata outside:

```sql
WITH ann_candidates AS (
  SELECT
    chunk_id,
    doc_type,
    entity_id,
    source_path,
    section_heading,
    APPROX_COSINE_DISTANCE(
      embedding,
      @query_vec,
      options => JSON '{"num_leaves_to_search": 10}'
    ) AS approx_distance
  FROM DocumentChunks
  WHERE embedding IS NOT NULL          -- Mandatory: must match CREATE VECTOR INDEX predicate!
  ORDER BY approx_distance ASC         -- Bare distance alias as sole ORDER BY key
  LIMIT 10
)
SELECT a.*, d.content_hash
FROM ann_candidates AS a
JOIN DocumentChunks AS d ON a.chunk_id = d.chunk_id
ORDER BY a.approx_distance ASC, a.chunk_id ASC;
```

### Live Profiling Check (`--query-mode=PROFILE`)
When connected to your Bangkok instance, inspect execution plans with `--query-mode=PROFILE` (or Spanner Studio's **Explanation** tab) and verify:
- **`SeekableKeySize`** matches index leading key columns on `IdxApiCalledByService` and `IdxServiceDependedOnBy`.
- **Search Index Scan** appears on `IdxDocumentChunksSearch` and `IdxApisSearch` (zero full-table scan).
- **Vector Index Scan** appears on `IdxDocumentChunksEmbedding` for Benchmark 7.
- **Live-Verified Optimizer & Latency Notes (Bangkok `100` PU):** All Lab 4/5 SQL, FTS, Vector, and Hero GraphRAG queries run in **33–520 ms**. Whenever an FTS `SCORE()` CTE is referenced multiple times (such as Lab 5 Query 2 RSF `fts_raw` + `fts_bounds` or MCP Toolbox hybrid tools), prefix the query with **`@{optimizer_version=latest}`** (optimizer `>= 6`).

---

## 3. Deep Dive: Data Boost Graph Algorithms (`08_graph_algorithms_criticality.sql`)

Spanner Graph Algorithms (`PageRank`, `WeaklyConnectedComponents`, `ShortestPath`) execute on **serverless Data Boost** (`roles/spanner.graphIntelligenceUser`) so heavy analytical iterations never starve your Bangkok OLTP serving nodes.

Notice how `08_graph_algorithms_criticality.sql` solves two critical constraints cleanly:
1. **Satisfying Algorithm Interleaving Without Violating `INV-06`:** Graph Algorithms require edge tables to be `INTERLEAVE IN PARENT <SourceNode>` **and** declare a foreign key to `<DestNode>`. Because `Services -> Services` is self-referential, `ServiceDependsOnAlgo` uses `INTERLEAVE IN PARENT Services ON DELETE CASCADE` plus `FOREIGN KEY (to_service_id) REFERENCES Services (service_id) NOT ENFORCED`!
2. **Dynamic Subgraph Filtering (`CALL PER () PageRank`):** We exclude non-blocking `DATA_FEED` edges (`WHERE e.dependency_type <> 'DATA_FEED'`) inside a `FULL UNION ALL ... NEXT CALL PER () PageRank(...)` block and write results directly back into `ServiceAlgoProperty` via `EXPORT DATA OPTIONS (format = 'CLOUD_SPANNER', table = 'ServiceAlgoProperty', write_mode = 'upsert_ignore_all')`. Expected #1 hub: **`svc-auth`** (`in-degree = 10`).

> **Live-Verified IAM, Syntax & Cost Hygiene (`~3 min` for all 3 algorithms):**
> - **Caller IAM:** Bind **`roles/spanner.graphIntelligenceUser`** on the caller — **even `roles/owner` does NOT include `spanner.databases.useDataBoost`**!
> - **Spanner Write-Back IAM (`format = 'CLOUD_SPANNER'`):** Grant database-level **`roles/spanner.databaseUser` + `roles/spanner.graphIntelligenceUser`** to `service-<PROJECT_NUMBER>@gcp-sa-spanner.iam.gserviceaccount.com` so Data Boost can write results into `ServiceAlgoProperty`.
> - **`ShortestPath`, Idempotency & Cost:** Declare setup tables with `CREATE TABLE IF NOT EXISTS`, return **every** yielded node in `ShortestPath` (`source_node.service_id` AND `target_node.service_id`), use `fallback_compute_region => '<region>'` (if setting a same-jurisdiction fallback), keep `max_idle_time => '5m'` (default `30m` bills while idle), and monitor via `SPANNER_SYS.GRAPH_OPERATION_EXECUTION_STATUS`.

---

## 4. Hands-On Exercises — Lab 4 (`starter/`)

Complete the `TODO(Lab 4)` blocks in both starter SQL files:

1. **`starter/sql/05_search_strategies_benchmark.sql`**:
   - Complete **Benchmark 3** (FTS `SEARCH` + `SCORE` + `SNIPPET` using `dictionary => 'SdlcSynonymDict'` and `enhance_query => TRUE`).
   - Complete **Benchmark 4** (Fuzzy `SEARCH_NGRAMS` + `SCORE_NGRAMS` wrapped with an inner `LIMIT 10000` cap).
   - Complete **Benchmark 7** (CTE-isolated ScaNN `APPROX_COSINE_DISTANCE` on `DocumentChunks` with `WHERE embedding IS NOT NULL`).
2. **`starter/sql/08_graph_algorithms_criticality.sql`**:
   - Define `ServiceDependsOnAlgo` (`INTERLEAVE IN PARENT Services ON DELETE CASCADE` + `NOT ENFORCED` destination FK).
   - Define the table-backed `CREATE OR REPLACE PROPERTY GRAPH SdlcAlgoGraph`.
   - Complete the `EXPORT DATA ... CALL PER () PageRank(..., max_idle_time => '5m')` block (literals only, zero `@param`).

### Antigravity IDE Prompt (Copy-Paste)

> *"Using the `spanner-search-ai-and-graphrag` and `spanner-graph-modeling-and-gql` skills, help me complete the `TODO(Lab 4)` blocks in `starter/sql/05_search_strategies_benchmark.sql` and `starter/sql/08_graph_algorithms_criticality.sql`. Ensure `INV-08` CTE isolation on ScaNN ANN, `LIMIT 10000` on fuzzy n-grams, and `NOT ENFORCED` FK on `ServiceDependsOnAlgo` (avoiding `INV-06`). Do not edit `solution/`."*

### Verify Lab 4 Offline

```bash
# Track B (Full: 05 Search Benchmarks + 08 Data Boost Graph Algorithms)
make TRACK=starter verify-lab LAB=4

# Track A (4-Hour Express: 05 Search Benchmarks only)
make TRACK=starter MODE=express verify-lab LAB=4
```

**Checkpoint Expected Output:** `[PASS] Lab 4 (starter/)` — all 7 search benchmarks and Data Boost Graph Algorithms verified!

---

**Navigation:** [← Module 3: Database Design & Ingestion](03_database_design_and_ingestion.md) | [README](../README.md) | [Module 5: Hybrid Retrieval & Hero GraphRAG →](05_hybrid_retrieval_and_graphrag.md)
