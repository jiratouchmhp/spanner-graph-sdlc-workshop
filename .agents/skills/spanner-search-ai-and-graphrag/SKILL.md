---
name: spanner-search-ai-and-graphrag
metadata:
  version: "1.0.0"
  category: "Cloud Spanner Enterprise"
description: >-
  Guides Cloud Spanner AI, Full-Text Search, Vector Search (KNN & ScaNN ANN),
  and Hybrid GraphRAG. Use when building Vertex AI CREATE MODEL + ML.PREDICT
  embeddings (regional endpoints e.g. asia-southeast1), PDML backfills,
  AI.IF/CLASSIFY/SCORE, FTS tokenizers/SEARCH/SCORE/enhance_query/Custom
  Dictionaries/fuzzy/facets, Exact KNN or ScaNN CREATE VECTOR INDEX
  (ARRAY<FLOAT32>, WHERE embedding IS NOT NULL, single-table/single-node ANN
  block), RRF (UNNEST(ARRAY(...)) WITH OFFSET, no ROW_NUMBER())/RSF/filtered/reranked
  hybrid search, Graph+FTS/Vector NEXT, or LangChain GraphRAG. Don't use for
  relational DDL (spanner-schema-and-data-modeling), pure GQL/Algorithms without
  search/vectors (spanner-graph-modeling-and-gql), ES-to-Spanner migration
  (spanner-migration-and-neo4j-playbook), MCP/agent security
  (spanner-mcp-toolbox-and-agent-security), or CPU/lock introspection/sizing
  (spanner-performance-observability-and-sizing).
---

# Cloud Spanner AI, Full-Text Search, Vector Search & Hybrid GraphRAG

Production cheatsheet and workflow router for consolidating **in-database ML/LLM
inference**, **full-text search (FTS)**, **vector similarity search (exact KNN
and ScaNN ANN)**, and **multi-model GraphRAG** inside a single strongly
consistent **Cloud Spanner Enterprise** or **Enterprise Plus** database.

## 1. Edition, Dialect & Regional Prerequisites

| Capability | Edition Gate | GoogleSQL | PostgreSQL | Usage Metric Label |
| --- | --- | :---: | :---: | --- |
| Full-Text Search (`CREATE SEARCH INDEX`, `SEARCH`, `SCORE`) | Enterprise+ | Yes | Yes (subset) | `Full-text search` |
| Vector Search — Exact KNN (`COSINE_DISTANCE`, etc.) | Enterprise+ | Yes | Yes | `KNN vector search` |
| Vector Search — ScaNN ANN (`CREATE VECTOR INDEX`, `APPROX_*`) | Enterprise+ | Yes | Yes (`USING scann`) | `Vector search` |
| Spanner ML (`CREATE MODEL`/`ML.PREDICT` vs `ML_PREDICT_ROW`) | All editions | Yes (TVF) | Yes (scalar) | Vertex AI billing |
| Native Semantic SQL (`AI.IF`, `AI.CLASSIFY`, `AI.SCORE`) | All editions | Yes | No | Vertex AI Gemini |
| Remote Functions (`LANGUAGE REMOTE`, Cloud Run) | All (`>= 1,000` PU) | Yes (Preview) | No | Cloud Run + egress |
| Spanner Graph + FTS / Vector (`GRAPH ... NEXT`) | Enterprise+ | Yes | No | `Spanner Graph` |

- **Monitor edition features:** Query Cloud Monitoring metric
  `spanner.googleapis.com/instance/edition/feature_usage` (60s sampling, up to
  120s delay). Edition upgrades take ~10 minutes with zero downtime; backups
  using Enterprise+ features only restore to instances of the **same or higher**
  edition.
- **Downgrade preconditions:** Before downgrading Enterprise to Standard, drop
  all property graphs, search indexes, and vector indexes, and stop all KNN/ANN
  distance calls.
- **Regional Topology Reference (e.g., Southeast Asia):** Keep operational data
  resident in a regional Spanner Enterprise instance such as Bangkok
  (`regional-asia-southeast3`, 3-zone 99.99% SLA, \$1.2915/node-hr on-demand)
  and invoke Vertex AI regional endpoints in Singapore (`asia-southeast1`,
  ~25 ms RTT, GA for `gemini-embedding-001` and `gemini-2.5-flash`). Spanner
  currently has no pre-built dual-region pairing `asia-southeast3` +
  `asia-southeast1`; pair primary Bangkok with scheduled cross-region backup
  copies (`$0.08/GiB` egress) or Change Streams async DR to `asia-southeast1`.
- **Compliance note:** Neither Vertex AI Prediction (Spanner ML) nor Full-Text
  Search supports **Assured Workloads**. Use **VPC Service Controls (VPC-SC)**
  to keep production Spanner instances isolated from non-production endpoints.

## 2. Safety Gates Before Live Remote Execution

Never mutate a live remote Cloud Spanner database, IAM policy, or Vertex AI
quota-consuming pipeline without explicit user confirmation:

1. **DDL mutations:** Confirm before running `CREATE/ALTER/DROP MODEL`,
   `CREATE/ALTER/DROP SEARCH INDEX`, `CREATE/ALTER/DROP VECTOR INDEX`,
   `ALTER VECTOR INDEX ... REBUILD`, `SET OPTIONS (disable_search=true)`,
   `CREATE FUNCTION ... LANGUAGE REMOTE`, or `CREATE OR REPLACE PROPERTY GRAPH`.
2. **Deferred model validation:** `CREATE MODEL` does **not** validate GenAI
   publisher endpoints or schemas at DDL time. Immediately smoke-test with a
   read-only `LIMIT 10` query before running bulk backfills.
3. **Bulk embedding backfills:** Confirm target row count, Vertex AI project
   quota isolation, and `@{pdml_max_parallelism=N}` throttling before running
   Partitioned DML (`UPDATE ... WHERE embedding IS NULL`), as PDML incurs
   Vertex AI per-call/token fees and cross-region network transfer.
4. **Read-only search execution:** Always run FTS and hybrid search queries in
   **read-only transactions** (use bounded staleness `>= 10s` when acceptable).
   Never bypass the read-write transaction block with
   `@{ALLOW_SEARCH_INDEXES_IN_TRANSACTION=TRUE}` in latency-sensitive OLTP paths.

## 3. Non-Negotiable Technical Invariants

Check every generated schema and query against these hard Cloud Spanner rules:

- **Embeddings & Spanner ML**
  1. Target a **regional** Vertex AI endpoint (e.g., `asia-southeast1`,
     `us-central1`) for `gemini-embedding-001`, `text-embedding-005`, and
     `text-multilingual-embedding-002`. **Never** use `global`, `us`, or `eu`
     for `predict`-API text embedding models, and never use `gemini-embedding-2`
     (uses `embedContent`, not `predict`).
  2. Declare `OUTPUT` for text embeddings as
     `embeddings STRUCT<statistics STRUCT<truncated BOOL, token_count FLOAT64>, values ARRAY<FLOAT32>>`
     and select `embeddings.values` via `FROM ML.PREDICT(MODEL {model}, ...)`.
  3. **Never** call `ML.PREDICT` inside a latency-sensitive read-write
     transaction (remote RPCs hold Spanner locks). Generate embeddings in a
     read-only transaction or backfill via throttled Partitioned DML.
  4. **Never** deploy `LANGUAGE REMOTE` functions on fractional instances
     (`< 1,000` PUs / `< 1` node) — they are unsupported below 1 full node.
- **Full-Text Search**
  5. Define `TOKENLIST` generated columns (`STORED HIDDEN` when exposed to
     Spanner Graph) and pass the **`TOKENLIST` column** — never the index name
     or raw `STRING` column — to `SEARCH(col_tokens, @q)`.
  6. Keep all `TOKENLIST` columns queried together inside a **single** search
     index, bind every `PARTITION BY` column with `=` in `WHERE`, and mirror any
     `WHERE col IS NOT NULL` partial index filter.
  7. Search index `ORDER BY` accepts **at most one `INT64`** (`NOT NULL`)
     column — never `TIMESTAMP`. For `SCORE`, global-corpus IDF activates
     **only** when `enhance_query => TRUE`.
  8. Custom dictionary tables must reside in the **default schema**, contain
     **exact 2 columns** (`Key STRING(MAX) NOT NULL`, `Value ARRAY<STRING(MAX)> NOT NULL`),
     use single lowercase keys, and work only with `dialect => 'rquery'`.
- **Vector Search (KNN & ScaNN ANN)**
  9. Store embeddings as `ARRAY<FLOAT32>(vector_length=>768)` (or model dim;
     `float4[] VECTOR LENGTH 768` in PG). Fixed `vector_length` is **mandatory**
     for `CREATE VECTOR INDEX`.
  10. Include `WHERE {embedding_col} IS NOT NULL` in both `CREATE VECTOR INDEX`
      and **every** ANN query (unless the column was declared `NOT NULL` in
      `CREATE TABLE`; `ALTER TABLE` cannot add `NOT NULL` to an `ARRAY` column).
  11. Keep every ScaNN ANN `SELECT` block **single-table** (no general `JOIN`
      inside the ANN block — only an optional comma cross-join with a single-row
      constant query-vector CTE). Wrap ANN in a CTE and `JOIN` outside.
  12. In Spanner Graph, ScaNN ANN is supported on **graph nodes ONLY** (never
      edges) using a single-node `MATCH ... ORDER BY APPROX_* LIMIT K` followed
      by `NEXT` for edge traversal. Use exact KNN on graph edges.
  13. Create ScaNN vector indexes **after** bulk loading initial embeddings
      (centroids are static until rebuilt).
- **Hybrid Retrieval & GraphRAG**
  14. Cloud Spanner GoogleSQL has **no general `ROW_NUMBER()` / `RANK()`**
      analytic window functions. Always compute server-side RRF ranks with
      `UNNEST(ARRAY(SELECT ... ORDER BY ... LIMIT k)) AS x WITH OFFSET AS rank`.
  15. Guard Relative Score Fusion (RSF) normalization denominators with
      `NULLIF(max_score - min_score, 0)` (or `SAFE_DIVIDE`).
  16. Re-run `CREATE OR REPLACE PROPERTY GRAPH {graph_name}` after adding new
      `TOKENLIST` or embedding columns to node/edge tables so GQL exposes them.

## 4. Retrieval Strategy Decision Matrix

| Requirement / Workload | Recommended Pattern | Primary Reference |
| --- | --- | --- |
| Generate embeddings / LLM inference in SQL | `CREATE MODEL` + `ML.PREDICT` (regional `asia-southeast1`) / PDML backfill | `spanner-ai-and-embeddings.md` |
| Ad-hoc semantic filter, rubric rating, labeling | `AI.IF`, `AI.SCORE`, `AI.CLASSIFY` (GoogleSQL, same-project Gemini) | `spanner-ai-and-embeddings.md` |
| Call 3rd-party API / custom Cloud Run model | `CREATE FUNCTION ... LANGUAGE REMOTE` (`>= 1,000` PU only) | `spanner-ai-and-embeddings.md` |
| Exact keyword, phrase, boolean, proximity, snippets | `TOKENIZE_FULLTEXT` + `CREATE SEARCH INDEX` + `SEARCH`/`SCORE`/`SNIPPET` | `full-text-search-deep-dive.md` |
| Stemming, spelling, domain synonyms without reindex | `enhance_query => TRUE` + 2-col Custom Dictionary table (`rquery`) | `full-text-search-deep-dive.md` |
| Typos in both index & query, short names/codes | `TOKENIZE_NGRAMS` + `SEARCH_NGRAMS` + `SCORE_NGRAMS` (inner `LIMIT 10000`) | `full-text-search-deep-dive.md` |
| Accelerate `LIKE`, `STARTS_WITH`, `REGEXP_CONTAINS` | `TOKENIZE_NGRAMS(LOWER(col))` + `STORING(col)` + constant literal pattern | `full-text-search-deep-dive.md` |
| JSON/numeric/array multi-predicate filtering + facets | `TOKENIZE_JSON`, `TOKENIZE_NUMBER`, `TOKEN` + bounded CTE facets | `full-text-search-deep-dive.md` |
| `< ~50K` post-filter rows or per-tenant PK slice | Exact KNN (`COSINE_DISTANCE`/`DOT_PRODUCT` + covering index, 100% recall) | `vector-search-knn-and-scann.md` |
| Millions to billions of vectors, low-latency top-K | ScaNN `CREATE VECTOR INDEX` + `APPROX_COSINE_DISTANCE` (single-table CTE) | `vector-search-knn-and-scann.md` |
| Combine SKU/keyword precision + semantic recall | Server-side RRF (`UNNEST(ARRAY(...)) WITH OFFSET`, `k=60`) or RSF | `hybrid-retrieval-and-graphrag.md` |
| Hard keyword filter + semantic rank on subset | FTS pre-filter (`LIMIT 1000`) -> Exact KNN re-rank | `hybrid-retrieval-and-graphrag.md` |
| Maximum top-10 precision (RAG / search QA) | Two-stage ANN/RRF (`LIMIT 50-100`) -> `ML.PREDICT` cross-encoder reranker | `hybrid-retrieval-and-graphrag.md` |
| Multi-hop entity context + unstructured citations | Single-node ScaNN ANN -> `NEXT` GQL traversal (or `GRAPH_TABLE` + RRF CTE) | `hybrid-retrieval-and-graphrag.md` |

## 5. Runnable Quickstart Patterns (GoogleSQL Enterprise)

### A. Register Regional Vertex AI Embedding Model & Smoke-Test
```sql
CREATE OR REPLACE MODEL {model_name}
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
  endpoint = '//aiplatform.googleapis.com/projects/{project_id}/locations/asia-southeast1/publishers/google/models/gemini-embedding-001',
  default_batch_size = 5
);

-- Always smoke-test immediately (validation is deferred until query time)
SELECT embeddings.values
FROM ML.PREDICT(
  MODEL {model_name},
  (SELECT 'smoke test query' AS content, 'RETRIEVAL_QUERY' AS task_type),
  STRUCT(768 AS outputDimensionality)
);
```

### B. Post-Load FTS + ScaNN Indexes & Server-Side RRF (Zero `ROW_NUMBER()`)
```sql
CREATE SEARCH INDEX {search_index_name}
ON {table_name}(content_tokens)
STORING (title)
ORDER BY updated_epoch_sec DESC;

CREATE VECTOR INDEX {vector_index_name}
ON {table_name}(embedding)
STORING (title)
WHERE embedding IS NOT NULL
OPTIONS (distance_type = 'COSINE');

-- Execute in a READ-ONLY transaction (k = 60, 0-based OFFSET)
SELECT key_id, SUM(1.0 / (60 + rank)) AS rrf_score
FROM (
  (
    SELECT rank, x AS key_id
    FROM UNNEST(ARRAY(
      SELECT doc_id
      FROM {table_name}
      WHERE embedding IS NOT NULL
      ORDER BY APPROX_COSINE_DISTANCE(
        embedding, @query_vec,
        options => JSON '{"num_leaves_to_search": 10}'
      ) ASC
      LIMIT 50
    )) AS x WITH OFFSET AS rank
  )
  UNION ALL
  (
    SELECT rank, x AS key_id
    FROM UNNEST(ARRAY(
      SELECT doc_id
      FROM {table_name}
      WHERE SEARCH(content_tokens, @search_query, enhance_query => TRUE)
      ORDER BY SCORE(content_tokens, @search_query, enhance_query => TRUE) DESC
      LIMIT 50
    )) AS x WITH OFFSET AS rank
  )
)
GROUP BY key_id
ORDER BY rrf_score DESC
LIMIT 10;
```

### C. GraphRAG Entry-Point ScaNN ANN -> `NEXT` Multi-Hop Traversal
```sql
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
MATCH TRAIL (upstream:Entity)-[:DEPENDS_ON]->{1,3}(entity:Entity)<-[:MENTIONS]-(c)
RETURN entity.entity_id, upstream.entity_id, c.source_uri, distance
ORDER BY distance ASC;
```

## 6. Reference Deep-Dive Guides

Read the targeted reference file when authoring production DDL, tuning recall
or latency, debugging errors, or architecting end-to-end pipelines:

- [Spanner AI, Vertex AI Embeddings, Semantic SQL & Remote Functions](references/spanner-ai-and-embeddings.md) —
  `CREATE MODEL` DDL, regional endpoint rules, `ML.PREDICT` vs
  `spanner.ML_PREDICT_ROW`, HA failover SLA math, PDML backfills,
  `AI.IF`/`CLASSIFY`/`SCORE`, Cloud Run `LANGUAGE REMOTE` protocol, gRPC error
  troubleshooting, and `langchain-google-spanner`.
- [Full-Text Search Deep Dive](references/full-text-search-deep-dive.md) —
  document-sharded architecture, all 9 tokenizers (GoogleSQL & PostgreSQL),
  `CREATE SEARCH INDEX` partitioning/interleaving/sort rules, `rquery`/`words`
  dialects, `enhance_query` & Custom Dictionaries, `SCORE` v2, fuzzy n-grams,
  pattern-matching acceleration, facets, and pagination.
- [Vector Search: Exact KNN & ScaNN ANN](references/vector-search-knn-and-scann.md) —
  `ARRAY<FLOAT32>(vector_length=>N)` constraints, Cosine/Euclidean/Dot Product
  matrix, KNN access patterns, ScaNN auto-tuning & `INFORMATION_SCHEMA`
  introspection, inline filtering hierarchy, single-table CTE isolation, and
  zero-downtime `REBUILD` / blue-green index cutover.
- [Hybrid Retrieval & Production GraphRAG](references/hybrid-retrieval-and-graphrag.md) —
  server-side RRF (`UNNEST(ARRAY(...)) WITH OFFSET`), RSF with zero-division
  guards, FTS-filtered KNN, two-stage `ML.PREDICT` cross-encoder reranking,
  Graph + FTS/Vector `NEXT` & `GRAPH_TABLE` patterns, Bangkok/Singapore
  GraphRAG reference architecture, dialect parity matrix, and anti-patterns.

## 7. Sibling Skill Routing

| Out-of-Scope Task | Route To |
| --- | --- |
| Editions, CUDs, CLI/SDKs, Emulator, Spanner Queues, DR/backups, autoscaler deploy | `spanner-enterprise-platform-ops` |
| Relational tables, split-safe PKs, interleaving, secondary indexes, FKs, views, TTL, Locality Groups | `spanner-schema-and-data-modeling` |
| `CREATE PROPERTY GRAPH` invariants, pure ISO GQL, Data Boost Graph Algorithms, Studio viz | `spanner-graph-modeling-and-gql` |
| Elasticsearch-to-Spanner FTS migration, Neo4j APOC/openCypher migration, SMT/CDC cutover | `spanner-migration-and-neo4j-playbook` |
| MCP Toolbox `tools.yaml`, Antigravity/ADK/Remote MCP setup, FGAC roles/IAM, OIDC, Model Armor | `spanner-mcp-toolbox-and-agent-security` |
| Execution plans, `ANALYZE`, `SPANNER_SYS` insights, Key Visualizer, pre-splitting, node sizing & TCO | `spanner-performance-observability-and-sizing` |

## 8. Official Public Documentation

- [Spanner AI Overview](https://docs.cloud.google.com/spanner/docs/spanner-ai-overview)
- [Full-Text Search Overview](https://docs.cloud.google.com/spanner/docs/full-text-search)
- [Vector Search Overview](https://docs.cloud.google.com/spanner/docs/find-k-nearest-neighbors)
- [Hybrid Full-Text and Vector Search](https://docs.cloud.google.com/spanner/docs/hybrid-full-text-vector-search)
- [GraphRAG Architecture on Cloud Spanner](https://docs.cloud.google.com/architecture/gen-ai-graphrag-spanner)
- [Cloud Spanner Building-Block Samples](https://github.com/GoogleCloudPlatform/spanner-bb-samples)
