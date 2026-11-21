-- =============================================================================
-- 05_search_strategies_benchmark.sql
-- Acme Commerce Platform — Lab 4 Multi-Model Search Strategy Benchmarks
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
--
-- EXECUTION MODE (INV-09):
--   Execute ALL 7 queries below in READ-ONLY transactions (recommended bounded
--   staleness >= 10s for FTS/hybrid reads) and profile with --query-mode=PROFILE
--   to inspect SeekableKeySize, Index Scans, and Row Amplification.
--
-- Parameters:
--   @hub_service_id STRING          (e.g. 'svc-auth' or 'svc-payments')
--   @search_query   STRING          (e.g. 'pan freeze 45ms')
--   @typo_query     STRING          (e.g. 'paymnts chrge idempoten')
--   @query_vec      ARRAY<FLOAT32>  (768-dim gemini-embedding-001 vector)
-- =============================================================================

-- =============================================================================
-- Benchmark 1: GQL Super-Node Hub Fanout Capping (IS_FIRST Window Filter)
--   Caps per-source outgoing CALLS_API fanout to the top 5 highest-RPS edges
--   inside a graph traversal, preventing super-node explosion on large graphs.
-- =============================================================================
GRAPH SdlcGraph
MATCH (s:Service)
  -[e:CALLS_API WHERE e IN {
    MATCH -[selected_e:CALLS_API]->
    FILTER IS_FIRST(5) OVER (
      PARTITION BY SOURCE_NODE_ID(selected_e)
      ORDER BY selected_e.rps_estimate DESC
    )
    RETURN selected_e
  }]->(a:Api)
RETURN
  s.service_id AS caller_service_id,
  a.api_id AS target_api_id,
  a.http_path AS http_path,
  e.call_mode AS call_mode,
  e.migration_status AS migration_status,
  e.rps_estimate AS rps_estimate
ORDER BY caller_service_id ASC, rps_estimate DESC, target_api_id ASC;

-- =============================================================================
-- Benchmark 2: GQL Converging Multi-Hop Paths (@{factorized_mode=factorize_left})
--   Deduplicates converging upstream dependency paths targeting a high-fanin
--   hub (e.g., @hub_service_id = 'svc-auth' or 'svc-payments') before probing
--   target node properties. Uses MATCH TRAIL for cycle safety on
--   svc-orders <-> svc-notifications and returns per-arm columns (never COUNT(*)).
-- =============================================================================
GRAPH SdlcGraph
MATCH TRAIL (upstream:Service)-[deps:DEPENDS_ON]->{1,3}@{factorized_mode=factorize_left}(hub:Service {service_id: @hub_service_id})
LET hop_count = ARRAY_LENGTH(deps)
LET dep_types = ARRAY_AGG(deps.dependency_type)
RETURN
  upstream.service_id AS upstream_service_id,
  upstream.tier AS tier,
  upstream.criticality AS criticality,
  hub.service_id AS target_hub_id,
  hop_count,
  IF(ARRAY_INCLUDES(dep_types, 'DATA_FEED'), 'DATA_FEED_CONSUMER', 'OLTP_DEPENDENCY') AS dependency_class
ORDER BY hop_count ASC, upstream_service_id ASC;

-- =============================================================================
-- Benchmark 3: Full-Text Search with Custom Dictionary & Query Enhancement
--   Uses SdlcSynonymDict ('pan' -> 'tok_pci_v2', 'freeze' -> '180-day') plus
--   enhance_query => TRUE (enables web-corpus IDF inside SCORE v2 + stemming).
-- =============================================================================
SELECT
  chunk_id,
  doc_type,
  entity_id,
  source_path,
  section_heading,
  SCORE(
    content_tokens,
    @search_query,
    dictionary => 'SdlcSynonymDict',
    enhance_query => TRUE
  ) AS fts_score,
  SNIPPET(
    content,
    @search_query,
    dictionary => 'SdlcSynonymDict',
    enhance_query => TRUE,
    max_snippet_width => 180,
    max_snippets => 2
  ) AS highlight_snippet
FROM DocumentChunks
WHERE SEARCH(
  content_tokens,
  @search_query,
  dictionary => 'SdlcSynonymDict',
  enhance_query => TRUE
)
ORDER BY fts_score DESC, chunk_id ASC
LIMIT 10;

-- =============================================================================
-- Benchmark 4: Fuzzy N-Gram Typo-Tolerant Search (SEARCH_NGRAMS + SCORE_NGRAMS)
--   Caps raw trigram candidates at LIMIT 10000 inside the inner subquery before
--   final top-K Jaccard trigram ranking. Reads `content` directly from
--   IdxDocumentChunksSearch STORING columns with zero base-table back-joins.
-- =============================================================================
SELECT
  chunk_id,
  doc_type,
  entity_id,
  source_path,
  section_heading,
  fuzzy_score
FROM (
  SELECT
    chunk_id,
    doc_type,
    entity_id,
    source_path,
    section_heading,
    SCORE_NGRAMS(content_ngrams, @typo_query) AS fuzzy_score
  FROM DocumentChunks
  WHERE SEARCH_NGRAMS(content_ngrams, @typo_query, min_ngrams => 2)
  LIMIT 10000
)
ORDER BY fuzzy_score DESC, chunk_id ASC
LIMIT 10;

-- =============================================================================
-- Benchmark 5: Combined JSON + Numeric + Literal LIKE Tokenized Search (Apis)
--   Executes a single local zig-zag intersection on IdxApisSearch across
--   TOKENIZE_JSON(metadata), TOKENIZE_NUMBER(latency_budget_ms), and
--   TOKENIZE_NGRAMS(LOWER(http_path)) (constant literal >= 3 chars).
-- =============================================================================
SELECT
  api_id,
  service_id,
  http_method,
  http_path,
  lifecycle_status,
  criticality,
  latency_budget_ms,
  JSON_VALUE(metadata, '$.token_format') AS token_format,
  INT64(metadata.freeze_window_days) AS freeze_window_days
FROM Apis @{FORCE_INDEX=IdxApisSearch}
WHERE JSON_CONTAINS(
    metadata,
    JSON '{"pci_scope": true, "data_residency": "asia-southeast3"}'
  )
  AND latency_budget_ms BETWEEN 1 AND 250
  AND LOWER(http_path) LIKE '%payments%'
ORDER BY latency_budget_ms ASC, api_id ASC
LIMIT 10;

-- =============================================================================
-- Benchmark 6: Exact KNN Vector Search (100% Recall, COSINE_DISTANCE)
--   Ideal for selective slices or corpora up to tens of thousands of vectors.
--   Uses @{scan_method=batch}, WHERE embedding IS NOT NULL, and literal LIMIT 5
--   to unlock Spanner's specialized top-K vector scan plan.
-- =============================================================================
SELECT
  chunk_id,
  doc_type,
  entity_id,
  source_path,
  section_heading,
  COSINE_DISTANCE(embedding, @query_vec) AS cosine_distance
FROM DocumentChunks @{scan_method=batch}
WHERE embedding IS NOT NULL
ORDER BY cosine_distance ASC
LIMIT 5;

-- =============================================================================
-- Benchmark 7: Approximate Nearest Neighbor ScaNN ANN (APPROX_COSINE_DISTANCE)
--   Enforces INV-08:
--     1. WHERE embedding IS NOT NULL matches IdxDocumentChunksEmbedding.
--     2. Single-table SELECT inside ann_candidates CTE (ZERO JOIN inside ANN).
--     3. APPROX_COSINE_DISTANCE is bare, aliased, and the sole ORDER BY key.
--     4. Relational enrichment (Services/Apis metadata) joins OUTSIDE the CTE.
-- =============================================================================
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
  WHERE embedding IS NOT NULL
  ORDER BY approx_distance ASC
  LIMIT 10
)
SELECT
  a.chunk_id,
  a.doc_type,
  a.entity_id,
  a.source_path,
  a.section_heading,
  a.approx_distance,
  d.content_hash
FROM ann_candidates AS a
JOIN DocumentChunks AS d ON a.chunk_id = d.chunk_id
ORDER BY a.approx_distance ASC, a.chunk_id ASC;
