# Hybrid Retrieval (RRF / RSF / Reranking) & Production GraphRAG

Comprehensive production reference for **server-side hybrid full-text + vector
search** (RRF, RSF, filtered KNN, two-stage `ML.PREDICT` cross-encoder
reranking), **Spanner Graph + FTS/Vector (`NEXT` and `GRAPH_TABLE`)**,
end-to-end **GraphRAG reference architecture** (regional primary + nearest
Vertex AI/DR region), full **GoogleSQL vs PostgreSQL dialect parity**, and
verified **anti-patterns**.

## 1. Hybrid Full-Text + Vector Search Patterns

Fusing Full-Text Search with Vector Search inside a single Cloud Spanner SQL
query combines exact identifier/SKU precision (where FTS excels) with semantic
paraphrase recall (where embeddings excel). Spanner executes both retriever
branches in parallel server-side, avoiding network round trips for large
unranked candidate pools. Execute all hybrid retrieval queries in **read-only
transactions**.

### Non-Negotiable Spanner SQL Constraint: Why RRF Cannot Use `ROW_NUMBER()`
- **No general SQL analytic window functions:** Cloud Spanner GoogleSQL (and
  Spanner PostgreSQL) has **no** general `ROW_NUMBER() OVER (...)`,
  `RANK() OVER (...)`, `DENSE_RANK()`, `LAG()`, or `LEAD()`. Writing
  `ROW_NUMBER() OVER (ORDER BY ...)` in an RRF CTE fails at query compilation.
- **Required Top-K Ranking Idiom:** Materialize each retriever's bounded top-K
  result set into an ordered `ARRAY(...)` and attach 0-based positions via
  **`UNNEST(ARRAY(SELECT ... ORDER BY ... LIMIT k)) AS x WITH OFFSET AS rank`**.
  - Bare `UNNEST` does **not** preserve array order — `WITH OFFSET` is mandatory.
  - `WITH OFFSET` is **0-based** (`0, 1, ..., k-1`); use `1.0 / (60 + rank)`
    directly (or `1.0 / (60 + rank + 1)` if matching 1-based rank math).
  - Always bound the inner `SELECT` with `LIMIT k` (e.g., `LIMIT 50` or `100`)
    and project minimal columns (`doc_id` or a compact `STRUCT`) to keep
    in-memory array materialization small.
- **Narrow GQL Exception vs Analytical Offload:** Spanner Graph GQL supports
  one specialized windowed `BOOL` predicate — `IS_FIRST(k) OVER (...)` inside
  GQL `FILTER` clauses to cap super-node edge fanout — **not** for emitting
  numeric ranks in SQL RRF. When general analytic window functions are truly
  required, offload to **BigQuery Federated Queries** (`EXTERNAL_QUERY(...)` or
  a Spanner external dataset backed by Data Boost).

### Pattern 1A: Server-Side Reciprocal Rank Fusion (RRF, Default `k = 60`)
Recommended default because FTS `SCORE` / `SCORE_NGRAMS` and vector distances
(`APPROX_COSINE_DISTANCE` / `APPROX_DOT_PRODUCT`) live on incomparable scales:
$$\text{RRF}(d) = \sum_{r \in R} \frac{1}{k + \text{rank}_r(d)}$$

```sql
-- Server-side RRF fusing ScaNN ANN + FTS (READ-ONLY transaction; UNNEST WITH OFFSET)
WITH fused AS (
  SELECT doc_id, SUM(1.0 / (60 + rank)) AS rrf_score
  FROM (
    (
      SELECT rank, x AS doc_id
      FROM UNNEST(ARRAY(
        SELECT doc_id
        FROM {table_name}
        WHERE embedding IS NOT NULL
        ORDER BY APPROX_COSINE_DISTANCE(
          embedding, @query_vec,
          options => JSON '{"num_leaves_to_search": 50}'
        ) ASC
        LIMIT 100
      )) AS x WITH OFFSET AS rank
    )
    UNION ALL
    (
      SELECT rank, x AS doc_id
      FROM UNNEST(ARRAY(
        SELECT doc_id
        FROM {table_name}
        WHERE SEARCH(content_tokens, @search_query, enhance_query => TRUE)
        ORDER BY SCORE(content_tokens, @search_query, enhance_query => TRUE) DESC
        LIMIT 100
      )) AS x WITH OFFSET AS rank
    )
  )
  GROUP BY doc_id
  ORDER BY rrf_score DESC
  LIMIT 10
)
SELECT f.doc_id, f.rrf_score, t.title, t.source_uri
FROM fused AS f
JOIN {table_name} AS t ON f.doc_id = t.doc_id
ORDER BY f.rrf_score DESC;
```

*(Tip: To generate `@query_vec` inline in SQL, add a leading single-row CTE
`query_vec AS (SELECT embeddings.values AS val FROM ML.PREDICT(MODEL {embedding_model}, (SELECT @search_query AS content, 'RETRIEVAL_QUERY' AS task_type), STRUCT(768 AS outputDimensionality)))`
and comma-join `FROM {table_name}, query_vec` inside the ANN branch, or swap
`SEARCH`/`SCORE` for `SEARCH_NGRAMS`/`SCORE_NGRAMS` for typo-tolerant hybrid
matching.)*

### Pattern 1B: Relative Score Fusion (RSF) with Divide-by-Zero Guard
Use RSF when preserving relative score magnitudes and retriever weights ($w_r$)
matters more than pure rank order. Convert distance metrics (e.g., cosine
distance) to similarity (`1.0 - distance`) and guard min-max denominators with
`NULLIF(max_s - min_s, 0)` (or `SAFE_DIVIDE`) to prevent divide-by-zero when
all candidates tie or only one candidate matches.
**CRITICAL Optimizer Hint (`@{optimizer_version=latest}`):** Whenever an FTS
CTE calling `SCORE()` is referenced multiple times in the same query (for
example, `fts_raw` scanned by both `FULL OUTER JOIN` and `fts_bounds`, or
multi-referenced hybrid CTEs inside MCP Toolbox tools), prefix the statement
with **`@{optimizer_version=latest}`** (requires optimizer version `>= 6`) so
Spanner compiles the shared FTS `SCORE()` CTE cleanly:

```sql
@{optimizer_version=latest}
WITH ann_raw AS (
  -- Keep APPROX_COSINE_DISTANCE bare, aliased, and the sole ORDER BY key
  SELECT doc_id,
    APPROX_COSINE_DISTANCE(
      embedding, @query_vec,
      options => JSON '{"num_leaves_to_search": 50}'
    ) AS dist
  FROM {table_name}
  WHERE embedding IS NOT NULL
  ORDER BY dist ASC
  LIMIT 100
),
ann_sim AS (
  SELECT doc_id, 1.0 - dist AS sim FROM ann_raw
),
fts_raw AS (
  SELECT doc_id,
    SCORE(content_tokens, @search_query, enhance_query => TRUE) AS score
  FROM {table_name}
  WHERE SEARCH(content_tokens, @search_query, enhance_query => TRUE)
  ORDER BY score DESC
  LIMIT 100
),
ann_bounds AS (SELECT MIN(sim) AS min_s, MAX(sim) AS max_s FROM ann_sim),
fts_bounds AS (SELECT MIN(score) AS min_s, MAX(score) AS max_s FROM fts_raw)
SELECT
  COALESCE(a.doc_id, f.doc_id) AS doc_id,
  0.6 * COALESCE(SAFE_DIVIDE(a.sim - ab.min_s, NULLIF(ab.max_s - ab.min_s, 0.0)), IF(a.doc_id IS NOT NULL, 1.0, 0.0))
  + 0.4 * COALESCE(SAFE_DIVIDE(f.score - fb.min_s, NULLIF(fb.max_s - fb.min_s, 0.0)), IF(f.doc_id IS NOT NULL, 1.0, 0.0))
  AS rsf_score
FROM ann_sim AS a
FULL OUTER JOIN fts_raw AS f ON a.doc_id = f.doc_id
CROSS JOIN ann_bounds AS ab
CROSS JOIN fts_bounds AS fb
ORDER BY rsf_score DESC
LIMIT 10;
```

### Pattern 2: FTS-Filtered Exact Vector Search
Use when keyword/boolean matching is a **hard requirement** (high selectivity)
and exact semantic similarity ranks the surviving slice:

```sql
SELECT doc_id, title, COSINE_DISTANCE(embedding, @query_vec) AS distance
FROM (
  SELECT doc_id, title, embedding
  FROM {table_name}
  WHERE SEARCH(content_tokens, @search_query)
    AND embedding IS NOT NULL
  ORDER BY updated_epoch_sec DESC
  LIMIT 1000
)
ORDER BY distance ASC
LIMIT 10;
```

### Pattern 3: Two-Stage Retrieval + In-DB Cross-Encoder Reranking
Deploy a cross-encoder (e.g., Hugging Face `bge-reranker-v2-m3`) on a regional
Vertex AI endpoint (e.g., `asia-southeast1`), retrieve top 50–100 candidates
via ANN or RRF, and rerank directly inside Spanner SQL:

```sql
CREATE OR REPLACE MODEL {reranker_model}
INPUT (text STRING(MAX), text_pair STRING(MAX))
OUTPUT (score FLOAT32)
REMOTE OPTIONS (
  endpoint = '//aiplatform.googleapis.com/projects/{project_id}/locations/asia-southeast1/endpoints/{endpoint_id}'
);

SELECT doc_id, score
FROM ML.PREDICT(
  MODEL {reranker_model},
  (
    SELECT doc_id, content AS text, @user_query AS text_pair
    FROM {table_name}
    WHERE embedding IS NOT NULL
    ORDER BY APPROX_COSINE_DISTANCE(
      embedding, @query_vec,
      options => JSON '{"num_leaves_to_search": 50}'
    ) ASC
    LIMIT 50
  )
)
ORDER BY score DESC
LIMIT 10;
```

## 2. Spanner Graph + Full-Text Search & Vector Search

- **Edition & Dialect:** Enterprise / Enterprise Plus, **GoogleSQL ONLY**
  (Spanner Graph is unsupported in PostgreSQL).
- **Mandatory Schema Evolution Rule:** Whenever you add new `TOKENLIST`
  (`STORED HIDDEN`) or embedding (`ARRAY<FLOAT32>(vector_length=>N)`) columns
  to backing node or edge tables, you **must** re-run
  `CREATE OR REPLACE PROPERTY GRAPH {graph_name} ...` so ISO GQL exposes those
  columns as graph element properties.
- **Graph Quotas:** Up to **250** property graphs per database, **100** joins
  per graph query, and **100** quantified path depth. Property graphs can also
  be backed by SQL views (with explicit `KEY (...)`).

### Graph + Full-Text Search (Nodes & Edges)
```sql
-- Node FTS + traversal + SCORE ranking
GRAPH {graph_name}
MATCH (n:Account)<-[e:Transfers]-(:Account)
WHERE SEARCH(n.alias_tokens, '"treasury reserve" | settlement')
RETURN n.alias, e.amount
ORDER BY SCORE(n.alias_tokens, '"treasury reserve" | settlement') DESC
LIMIT 20;

-- Inline edge FTS filter
GRAPH {graph_name}
MATCH (src:Account)-[e:Transfers WHERE SEARCH(e.notes_tokens, 'refund')]->(dst:Account)
RETURN src.account_id, dst.account_id, e.notes;
```

### Graph + Vector Search (KNN on Nodes & Edges; ScaNN ANN on Nodes ONLY)
- **Hard Rule 1 (No Edge ANN):** ScaNN ANN (`APPROX_*` + `CREATE VECTOR INDEX`)
  is supported on **graph nodes ONLY** — **never** on graph edges. Use exact
  KNN (`COSINE_DISTANCE`, `EUCLIDEAN_DISTANCE`, `DOT_PRODUCT`) for edge vectors.
- **Hard Rule 2 (Single-Node `MATCH` -> `NEXT`):** Never traverse edges or join
  additional nodes inside the ScaNN ANN `MATCH` block. Match a single indexed
  node with `WHERE n.embedding IS NOT NULL`, `ORDER BY distance LIMIT K`, then
  chain **`NEXT`** to traverse multi-hop relationships:

```sql
-- Exact KNN on graph edges (ANN is unsupported on edges)
GRAPH {graph_name}
MATCH (owner:Owner)-[:OWNS]->(:Account)-[t:Transfers]->(:Account)
WHERE t.notes_embedding IS NOT NULL
RETURN owner.owner_name, t.notes,
  COSINE_DISTANCE(t.notes_embedding, @query_vec) AS distance
ORDER BY distance ASC
LIMIT 5;

-- ScaNN ANN entry point on single graph node -> NEXT multi-hop traversal
GRAPH {graph_name}
MATCH (@{FORCE_INDEX={vector_index_name}} c:DocumentChunk)
WHERE c.embedding IS NOT NULL
RETURN c, APPROX_COSINE_DISTANCE(
  c.embedding, @query_vec,
  options => JSON '{"num_leaves_to_search": 10}'
) AS distance
ORDER BY distance ASC
LIMIT 10
NEXT
MATCH TRAIL (upstream:Entity)-[:DEPENDS_ON]->{1,3}(target:Entity)<-[:MENTIONS]-(c)
RETURN target.entity_id, upstream.entity_id, c.source_uri, distance
ORDER BY distance ASC;
```

### Combining Server-Side Hybrid RRF + `GRAPH_TABLE` (ISO SQL/PGQ)
When a single SQL query needs **both** hybrid FTS+ScaNN citation retrieval and
cycle-safe multi-hop graph traversal, keep the ScaNN ANN `SELECT` isolated in
its own CTE, fuse via RRF (`UNNEST(ARRAY(...)) WITH OFFSET`), and join outside
against `FROM GRAPH_TABLE({graph_name} MATCH TRAIL ... RETURN ...)`:

```sql
WITH rrf_chunks AS (
  SELECT chunk_id, SUM(1.0 / (60 + rank)) AS rrf_score
  FROM (
    (
      SELECT rank, x AS chunk_id
      FROM UNNEST(ARRAY(
        SELECT chunk_id
        FROM {chunks_table}
        WHERE embedding IS NOT NULL
        ORDER BY APPROX_COSINE_DISTANCE(
          embedding, @query_vec,
          options => JSON '{"num_leaves_to_search": 10}'
        ) ASC
        LIMIT 30
      )) AS x WITH OFFSET AS rank
    )
    UNION ALL
    (
      SELECT rank, x AS chunk_id
      FROM UNNEST(ARRAY(
        SELECT chunk_id
        FROM {chunks_table}
        WHERE SEARCH(content_tokens, @search_query, enhance_query => TRUE)
        ORDER BY SCORE(content_tokens, @search_query, enhance_query => TRUE) DESC
        LIMIT 30
      )) AS x WITH OFFSET AS rank
    )
  )
  GROUP BY chunk_id
),
impacted_entities AS (
  SELECT entity_id, upstream_id, hop_count
  FROM GRAPH_TABLE({graph_name}
    MATCH TRAIL (u:Entity)-[e:DEPENDS_ON]->{1,3}(t:Entity {entity_id: @target_id})
    RETURN t.entity_id AS entity_id, u.entity_id AS upstream_id, ARRAY_LENGTH(e) AS hop_count
  )
)
SELECT i.upstream_id, MIN(i.hop_count) AS min_hops,
  ARRAY_AGG(
    IF(c.chunk_id IS NOT NULL, STRUCT(c.source_uri, r.rrf_score), NULL)
    IGNORE NULLS ORDER BY r.rrf_score DESC LIMIT 3
  ) AS citations
FROM impacted_entities AS i
LEFT JOIN {chunk_mentions_table} AS m ON m.entity_id = i.upstream_id
LEFT JOIN rrf_chunks AS r ON r.chunk_id = m.chunk_id
LEFT JOIN {chunks_table} AS c ON c.chunk_id = r.chunk_id
GROUP BY i.upstream_id
ORDER BY min_hops ASC, i.upstream_id ASC;
```

## 3. Production GraphRAG Reference Architecture

GraphRAG closes flat RAG's multi-hop blind spot by unifying the **knowledge
graph, full-text tokens, and vector embeddings** inside a single ACID Cloud
Spanner Enterprise database.

### Two-Subsystem Regional Topology (Example: Bangkok Primary + Singapore AI/DR)
1. **Event-Driven Data Ingestion Subsystem (Data-Resident Primary, e.g., `asia-southeast3`):**
   - **Cloud Storage** regional bucket (`asia-southeast3`, Standard class,
     Object Lifecycle Management, parallel composite uploads, CMEK/CSEK, Cloud
     DLP / Sensitive Data Protection PII de-identification).
   - Object finalized -> **Pub/Sub** topic (message storage policy pinned to
     `asia-southeast3`, flow control, retries, CMEK) -> **Cloud Run Functions**
     (`asia-southeast3`, tuned memory/CPU, Binary Authorization, CMEK).
   - **KG Extraction & Chunking:** Invoke **Gemini** (`asia-southeast1`) +
     LangChain `LLMGraphTransformer` with explicit schema allowlists
     (`allowed_nodes`, `allowed_relationships`, `node_properties`,
     `relationship_properties`; for niche regulated domains like clinical
     healthcare or local law, pair with deterministic extractors or existing
     enterprise KGs). Segment documents via `RecursiveCharacterTextSplitter` or
     **Document AI Layout Parser**, embed with `gemini-embedding-001` in
     `asia-southeast1`, and commit nodes, edges, chunks, `TOKENLIST`s, and
     `ARRAY<FLOAT32>` vectors atomically into **Spanner Enterprise
     (`regional-asia-southeast3`)**.
2. **Low-Latency Agentic Serving Subsystem:**
   - Agent runtime on **Vertex AI Agent Engine**, **Cloud Run**, or **GKE** in
     `asia-southeast3` embeds user query (`asia-southeast1`, ~25 ms RTT), runs
     Spanner Graph ScaNN ANN + `NEXT` / Hybrid RRF in `regional-asia-southeast3`,
     optionally reranks via **Vertex AI Search Ranking API** or in-DB
     `ML.PREDICT`, and synthesizes grounded responses via **Gemini
     (`gemini-2.5-flash` in `asia-southeast1`; `global` fallback only for LLM
     synthesis when data residency allows cross-region routing — never for
     `predict`-API embeddings)**.
   - Emit Admin Activity + Data Access audit logs to regionalized **Cloud
     Logging** & **Cloud Monitoring**.
3. **Reliability, Cost & DR Controls:**
   - **Vertex AI:** Handle HTTP 429 (`RESOURCE_EXHAUSTED`) via exponential
     backoff or **Provisioned Throughput**; enable **Zero Data Retention (ZDR)**
     and safety filters; cut token spend via **Context Caching** (repeated KG
     schemas/system prompts) and **Batch Inference** for offline KG extraction.
   - **Spanner Enterprise:** Enable **Managed Autoscaler**, **1-yr / 3-yr CUDs**
     (e.g., \$1.0332 / \$0.7749 per node-hr in `asia-southeast3` vs \$1.2915
     on-demand), **database deletion protection**, **7-day PITR**, and scheduled
     cross-region backup copies to `regional-asia-southeast1` (\$0.08/GiB Asia
     egress + \$0.03432/GiB-mo backup storage; Spanner currently has no pre-built
     Bangkok+Singapore dual-region config).

## 4. GoogleSQL vs PostgreSQL Dialect Parity Matrix

| Capability | GoogleSQL | PostgreSQL |
| --- | --- | --- |
| Embedding Column Type | `ARRAY<FLOAT32>(vector_length=>N)` | `float4[] VECTOR LENGTH N` (`real[]`) |
| Vector Index DDL & Rebuild | `CREATE VECTOR INDEX ...` / `ALTER VECTOR INDEX idx REBUILD;` | `CREATE INDEX ... USING scann(...)` / `REINDEX INDEX CONCURRENTLY idx;` |
| Blue-Green Disable Search | `ALTER VECTOR INDEX idx SET OPTIONS (disable_search=true);` | Not documented in PG `ALTER INDEX` |
| ANN / KNN Functions | `APPROX_*` (`options => JSON '...'`) / `COSINE_DISTANCE` etc. | `spanner.approx_*` (`options => '...'::jsonb`) / `spanner.cosine_distance` |
| Spanner ML Inference | `CREATE MODEL` + `ML.PREDICT` / `SAFE.ML.PREDICT` (TVF) | Scalar `spanner.ML_PREDICT_ROW` (`jsonb`) + `spanner.float32_array()` |
| `AI.IF` / `CLASSIFY` / `SCORE` | Supported | Not supported |
| Remote Functions (`LANGUAGE REMOTE`) | Supported (Preview, `>= 1,000` PUs only) | Not supported |
| Spanner Graph (GQL + FTS/Vector) | Supported | Not supported |
| FTS `TOKENIZE_NUMBER` / `TOKEN` | Scalar + `ARRAY` (`INT64`, `FLOAT32`, `FLOAT64`, `STRING`, `BYTES`) | **Scalar `bigint` / `text` / `bytea` ONLY** (no floats, no arrays) |
| FTS `TOKENLIST_CONCAT` in Gen Col | Can reference other `TOKENLIST` generated cols | Cannot reference generated cols; nest `spanner.tokenize_*` inline |
| FTS Custom Dict Staleness `ALTER` | `ALTER TABLE ... SET OPTIONS` supported | No `ALTER TABLE` support |
| FTS Pattern Acceleration | `LIKE`, `STARTS_WITH`, `ENDS_WITH`, `REGEXP_CONTAINS` | `LIKE`, `STARTS_WITH` only |
| Query Hints Syntax | `@{FORCE_INDEX=idx}`, `@{pdml_max_parallelism=5}` | `/*@ FORCE_INDEX = idx */`, `/*@ remote_udf_max_rows_per_rpc=200 */` |
| SQL `ROW_NUMBER()` / `OVER (...)` | Unsupported (use `UNNEST(ARRAY(...)) WITH OFFSET` for RRF) | Unsupported (offload general windows to BigQuery `EXTERNAL_QUERY`) |

## 5. Cross-Cutting Anti-Patterns to Reject

1. **Passing a raw `STRING` column or index name to `SEARCH(...)`:** Always
   index a `TOKENLIST` generated column and pass that column name to `SEARCH`.
2. **Writing `ROW_NUMBER() OVER (...)` in RRF SQL:** Fails compilation; always
   use `UNNEST(ARRAY(SELECT ... ORDER BY ... LIMIT k)) AS x WITH OFFSET AS rank`.
3. **Joining tables or traversing graph edges inside a ScaNN ANN block:** Keep
   ScaNN ANN strictly single-table (join outside CTE) or single-node (`NEXT`).
4. **Attempting ScaNN ANN on graph edges:** Supported on graph nodes only; use
   exact KNN on edges.
5. **Fabricating `ALTER TABLE ... ADD COLUMNAR INDEX (...)`:** Columnar Engine
   has no index DDL — enable via `ALTER DATABASE/TABLE ... SET OPTIONS (columnar_policy = 'enabled')`,
   and note that Search and Vector indexes are never stored in columnar format.
6. **Calling Spanner Graph's language "openCypher":** Spanner Graph implements
   **ISO GQL (`ISO/IEC 39075:2024`)** and **ISO SQL/PGQ (`ISO/IEC 9075-16:2023`)**
   (use `GRAPH_TABLE` inside SQL `SELECT` and `GRAPH ... NEXT` for standalone GQL).
7. **Placing `@{scan_method=batch}` on a joined Exact KNN CTE:** Batch scan is
   unsupported on the right side of apply/merge/hash joins — keep it on
   standalone Exact KNN queries and omit it on GraphRAG CTEs joined downstream.
8. **Omitting `@{optimizer_version=latest}` on multi-referenced `SCORE()` CTEs:**
   RSF (`fts_raw` + `fts_bounds`) and multi-referenced hybrid FTS CTEs require
   optimizer version `>= 6` (`@{optimizer_version=latest}`).

Official documentation & samples:
- [Hybrid Full-Text and Vector Search](https://docs.cloud.google.com/spanner/docs/hybrid-full-text-vector-search)
- [Full-Text Search with Spanner Graph](https://docs.cloud.google.com/spanner/docs/graph/full-text-search-and-graph)
- [Vector Similarity Search with Spanner Graph](https://docs.cloud.google.com/spanner/docs/graph/perform-vector-similarity-search)
- [GraphRAG Reference Architecture on Cloud Spanner](https://docs.cloud.google.com/architecture/gen-ai-graphrag-spanner)
- [Spanner Building-Block Hybrid Search Samples](https://github.com/GoogleCloudPlatform/spanner-bb-samples)
- [Spanner Graph + ADK GraphRAG Notebook](https://github.com/GoogleCloudPlatform/generative-ai/blob/main/gemini/use-cases/graphrag/graph_rag_spanner_sdk_adk.ipynb)
