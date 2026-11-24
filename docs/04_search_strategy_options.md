# Lab 4: Evaluating Search Strategies & Graph Algorithms

In this lab, you'll complete and compare Cloud Spanner Enterprise's native search modes inside your Bangkok (`asia-southeast3`) database. You'll finish Full-Text Search with custom synonyms, typo-tolerant Fuzzy N-grams, and ScaNN ANN vector search alongside the GQL traversal, JSON/numeric, and Exact KNN benchmarks already in [`starter/sql/05_search_strategies_benchmark.sql`](../starter/sql/05_search_strategies_benchmark.sql). On Track B, you'll also configure serverless Data Boost PageRank in [`starter/sql/08_graph_algorithms_criticality.sql`](../starter/sql/08_graph_algorithms_criticality.sql) to rank service criticality without touching OLTP CPU.

## What You'll Work On

| Step | File to open (`starter/`) | Where to edit (`TODO` / line) | Track |
| :-: | :--- | :--- | :-: |
| 1 | [`starter/sql/05_search_strategies_benchmark.sql`](../starter/sql/05_search_strategies_benchmark.sql) | Benchmarks 3 (`L68`), 4 (`L82`), and 7 (`L134`) — Benchmarks 1, 2, 5, 6 are provided | Track A & B |
| 2 | [`starter/sql/08_graph_algorithms_criticality.sql`](../starter/sql/08_graph_algorithms_criticality.sql) | `ServiceDependsOnAlgo` (`L35`), `SdlcAlgoGraph` (`L64`), edge copy (`L76`), `PageRank` (`L84`) | Track B (optional on Track A) |
| 3 | Live Bangkok run & plan check | `python3 scripts/run_live_sql.py 05 08 --track starter` | Optional (Live GCP) |

- **Estimated time:** ~25 min (Track A Express) / ~60 min (Track B Full)
- **Reference copies (read-only if stuck):** [`solution/sql/05_search_strategies_benchmark.sql`](../solution/sql/05_search_strategies_benchmark.sql), [`solution/sql/08_graph_algorithms_criticality.sql`](../solution/sql/08_graph_algorithms_criticality.sql)
- **Antigravity skills:** [`spanner-search-ai-and-graphrag`](../.agents/skills/spanner-search-ai-and-graphrag/SKILL.md), [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md) (support: [`spanner-performance-observability-and-sizing`](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md), [`sdlc-graphrag-workshop-guide`](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md))

---

## Quick Background

All 6 search and graph modes run inside your single Bangkok Cloud Spanner Enterprise database (`regional-asia-southeast3`), so you don't need a separate OpenSearch or vector cluster. Pick the mode that matches the query:

| Search Mode | Key Spanner Syntax | When to Use It | Key Spanner Caveat |
| :--- | :--- | :--- | :--- |
| **Exact GQL Traversal** | `MATCH TRAIL`, `IS_FIRST`, `@{factorized_mode=factorize_left}` | Deterministic dependency impact walks and hub fanout capping | Always use `TRAIL` on cyclic graphs; avoid `COUNT(*)` over factorized arms |
| **Graph Algorithms (Data Boost)** | `EXPORT DATA ... CALL PER () PageRank` / `WCC` / `ShortestPath` | Batch criticality scoring (`$1.2285/1k SPU-hr` Bangkok) with zero OLTP CPU load | Table-backed interleaved graph only (no views), literal args only (no `@param`), set `max_idle_time => '5m'` |
| **Full-Text Search (FTS)** | `SEARCH`, `SCORE`, `SNIPPET` + `SdlcSynonymDict` | Exact IDs (`ADR-004`, `tok_pci_v2`, `45ms`) and domain synonyms (`pan` -> `tok_pci_v2`) | Must run in a **read-only transaction** (`INV-09`); bounded staleness `>= 10s` recommended |
| **Fuzzy N-Grams** | `SEARCH_NGRAMS`, `SCORE_NGRAMS` | Typo-tolerant developer search bars (`paymnts chrge idempoten`) | Read-only transaction; wrap inner scan with `LIMIT 10000` before sorting |
| **Exact KNN Vector** | `COSINE_DISTANCE` + `@{scan_method=batch}` | 100% recall on selective slices or corpora up to tens of thousands of chunks | Drop `@{scan_method=batch}` if joining the CTE downstream |
| **ScaNN ANN Vector** | `APPROX_COSINE_DISTANCE` | Millisecond semantic search over `100k`–`100M+` vectors | Single-table CTE with **no joins inside the ANN block** + `WHERE embedding IS NOT NULL` (`INV-08`) |

*(Benchmark 5 in `05_search_strategies_benchmark.sql` also combines `TOKENIZE_JSON`, `TOKENIZE_NUMBER`, and `LIKE` in one zig-zag scan on `IdxApisSearch`.)*

---

## Step-by-Step Hands-On

### Step 1: Complete Benchmarks 3, 4, and 7 in `starter/sql/05_search_strategies_benchmark.sql` (Track A & B)

Open [`starter/sql/05_search_strategies_benchmark.sql`](../starter/sql/05_search_strategies_benchmark.sql). Benchmarks 1 (`IS_FIRST`), 2 (`factorized_mode=factorize_left`), 5 (`JSON_CONTAINS` + numeric + `LIKE`), and 6 (Exact KNN `COSINE_DISTANCE`) are already written — skim them first to see the patterns.

1. **Benchmark 3 — Full-Text Search with custom synonyms (`L68`):**
   Replace the `TODO(Lab 4)` block with a read-only `SELECT` over `DocumentChunks` querying `content_tokens` against `@search_query`. Pass `dictionary => 'SdlcSynonymDict'` and `enhance_query => TRUE` to all three FTS functions: `SEARCH(...)` in `WHERE`, `SCORE(...) AS fts_score`, and `SNIPPET(content, ...)` as `highlight_snippet`. Order by `fts_score DESC, chunk_id ASC` and `LIMIT 10`.
2. **Benchmark 4 — Typo-tolerant Fuzzy N-grams (`L82`):**
   Replace the `TODO(Lab 4)` block with a nested query over `DocumentChunks`. Inside the inner subquery, filter with `SEARCH_NGRAMS(content_ngrams, @typo_query, min_ngrams => 2)`, score with `SCORE_NGRAMS(content_ngrams, @typo_query) AS fuzzy_score`, and cap candidates at `LIMIT 10000`. In the outer query, sort by `fuzzy_score DESC, chunk_id ASC LIMIT 10`.
3. **Benchmark 7 — CTE-isolated ScaNN ANN (`L134`):**
   Replace the `TODO(Lab 4)` block with a single-table `ann_candidates` CTE over `DocumentChunks` (no joins inside the CTE!). Filter with `WHERE embedding IS NOT NULL`, compute `APPROX_COSINE_DISTANCE(embedding, @query_vec, options => JSON '{"num_leaves_to_search": 10}') AS approx_distance`, and sort solely by `ORDER BY approx_distance ASC LIMIT 10`. Join back to `DocumentChunks` *outside* the CTE to fetch `content_hash` and break ties with `ORDER BY a.approx_distance ASC, a.chunk_id ASC`.

*(Optional Antigravity prompt)*:
> "Help me complete Benchmarks 3, 4, and 7 in `starter/sql/05_search_strategies_benchmark.sql` using `spanner-search-ai-and-graphrag`. Use `SdlcSynonymDict` + `enhance_query => TRUE` for FTS, an inner `LIMIT 10000` cap for fuzzy n-grams, and a single-table CTE with `WHERE embedding IS NOT NULL` for ScaNN ANN."

Quick check (Track A Express scope):

```bash
make TRACK=starter MODE=express verify-lab LAB=4
```

### Step 2: Build Data Boost PageRank in `starter/sql/08_graph_algorithms_criticality.sql` (Track B — optional on Track A)

Skip this step if you're on Track A Express.

Open [`starter/sql/08_graph_algorithms_criticality.sql`](../starter/sql/08_graph_algorithms_criticality.sql). The output table `ServiceAlgoProperty` and the result/monitoring queries at the bottom are already complete.

1. **`ServiceDependsOnAlgo` table (`L35`):**
   Create `ServiceDependsOnAlgo` with `CREATE TABLE IF NOT EXISTS`, `PRIMARY KEY (service_id, to_service_id)`, `INTERLEAVE IN PARENT Services ON DELETE CASCADE`, and `FOREIGN KEY (to_service_id) REFERENCES Services (service_id) NOT ENFORCED`. Here's why: Spanner Graph Algorithms require interleaved edge tables with a destination FK, and marking the self-referencing FK `NOT ENFORCED` avoids the dual-cascade restriction (`INV-06`).
2. **Table-backed `SdlcAlgoGraph` (`L64`):**
   Define `CREATE OR REPLACE PROPERTY GRAPH SdlcAlgoGraph` using `Services` (`LABEL Service`) and `ServiceDependsOnAlgo` (`LABEL DEPENDS_ON_ALGO`). Keep views out of this graph — Data Boost graph algorithms only run on table-backed graphs.
3. **Copy edges (`L76`):**
   Write `INSERT OR UPDATE INTO ServiceDependsOnAlgo` selecting all 28 rows from `ServiceDependsOnService`, mapping `from_service_id AS service_id` and setting `edge_weight` to `1.0` (`SYNC_RPC`), `2.0` (`ASYNC_EVENT`), or `5.0` (`DATA_FEED`).
4. **Data Boost `PageRank` export (`L84`):**
   Write `EXPORT DATA OPTIONS (format = 'CLOUD_SPANNER', table = 'ServiceAlgoProperty', write_mode = 'upsert_ignore_all') AS GRAPH SdlcAlgoGraph ...` building a dynamic subgraph that drops `DATA_FEED` edges (`MATCH (n:Service) RETURN n FULL UNION ALL MATCH -[e:DEPENDS_ON_ALGO WHERE e.dependency_type <> 'DATA_FEED']-> RETURN e`), then piping with `NEXT` into `CALL PER () PageRank(damping_factor => 0.85, max_iterations => 20, approx_precision => 1e-4, max_idle_time => '5m') YIELD node, score`. Return `node.service_id AS service_id`, `'pagerank_oltp_v1' AS algo_run_id`, and `score AS float_val`. Use literals only (no `@param` bindings). Expect `svc-auth` (in-degree `10`) to rank #1.

*(Stretch goal: [`solution/sql/08_graph_algorithms_criticality.sql`](../solution/sql/08_graph_algorithms_criticality.sql) also includes `WeaklyConnectedComponents` and weighted `ShortestPath` to `svc-payments`.)*

*(Optional Antigravity prompt)*:
> "Help me complete the four `TODO(Lab 4)` sections in `starter/sql/08_graph_algorithms_criticality.sql` using `spanner-graph-modeling-and-gql` (`ServiceDependsOnAlgo` with a `NOT ENFORCED` FK, table-backed `SdlcAlgoGraph`, the 28-row `INSERT OR UPDATE`, and the `EXPORT DATA ... CALL PER () PageRank` query with `max_idle_time => '5m'`)."

### Step 3 (Optional — Live Bangkok GCP): Run your queries & check execution plans

Preview statement routing offline first, then run live against your Bangkok (`asia-southeast3`) Spanner database:

```bash
# Offline preview of statement routing and auto-bound parameters
python3 scripts/run_live_sql.py 05 08 --track starter --dry-run

# Run live against Bangkok Spanner (Track A: pass `05` only; Track B: pass `05 08`)
python3 scripts/run_live_sql.py 05 08 --track starter
```

*(Short on time? Swap `--track starter` for `--track solution`. Note that `provision_workshop_env.sh create` already grants `roles/spanner.graphIntelligenceUser` on your database, which even `roles/owner` doesn't include, along with write-back permissions for the Spanner service agent.)*

When profiling Benchmark 3, 5, and 7 in Spanner Studio's **Explanation** tab (or with `--query-mode=PROFILE`), verify that `IdxDocumentChunksSearch` and `IdxApisSearch` show **Search Index Scan** and Benchmark 7 shows **Vector Index Scan** on `IdxDocumentChunksEmbedding` (typical Bangkok 100-PU latency is ~33–520 ms).

---

## Verify This Lab

```bash
# Track B (Full — verifies 05 search benchmarks + 08 Data Boost graph algorithms)
make TRACK=starter verify-lab LAB=4

# Track A (Express — verifies 05 search benchmarks only)
make TRACK=starter MODE=express verify-lab LAB=4
```

Passing output looks like this:

```text
[PASS] Lab 4 (starter/, mode=full)
    + Verified 7 search strategy benchmarks in starter/sql/05_search_strategies_benchmark.sql
    + Verified Data Boost Graph Algorithms in starter/sql/08_graph_algorithms_criticality.sql
[OK] Verified 1/1 lab(s) on track=starter (mode=full).
```

**If it fails, check:**
- **FTS or fuzzy check fails in `05`:** Confirm `SdlcSynonymDict`, `enhance_query => TRUE`, `SEARCH_NGRAMS(`, and `SCORE_NGRAMS(` are present in uncommented SQL and every `TODO(Lab 4)` marker is removed.
- **ScaNN check fails in `05`:** Confirm `APPROX_COSINE_DISTANCE(` and `WHERE embedding IS NOT NULL` appear together inside the single-table CTE.
- **Data Boost check fails in `08` (Track B):** Verify `ServiceDependsOnAlgo` declares `FOREIGN KEY (to_service_id) REFERENCES Services (service_id) NOT ENFORCED` and your `EXPORT DATA` query invokes `PageRank(`.

---

**Previous / Next:** [← Lab 3: Database Design & Ingestion](03_database_design_and_ingestion.md) | [Starter Checklist](../starter/README.md) | [Lab 5: Hybrid Retrieval & Hero GraphRAG →](05_hybrid_retrieval_and_graphrag.md)
