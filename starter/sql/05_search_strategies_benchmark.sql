-- =============================================================================
-- 05_search_strategies_benchmark.sql (STARTER — Lab 4)
-- Acme Commerce Platform — Lab 4 Multi-Model Search Strategy Benchmarks
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
--
-- Antigravity Skills for Lab 4:
--   - Primary: .agents/skills/spanner-search-ai-and-graphrag/SKILL.md
--   - Primary: .agents/skills/spanner-graph-modeling-and-gql/SKILL.md
--
-- EXECUTION MODE (INV-09):
--   Execute ALL queries below in READ-ONLY transactions (recommended bounded
--   staleness >= 10s for FTS/hybrid reads) and profile with --query-mode=PROFILE
--   to inspect SeekableKeySize, Index Scans, and Row Amplification.
--
-- Parameters:
--   @hub_service_id STRING          (e.g. 'svc-auth' or 'svc-payments')
--   @search_query   STRING          (e.g. 'pan freeze 45ms')
--   @typo_query     STRING          (e.g. 'paymnts chrge idempoten')
--   @query_vec      ARRAY<FLOAT32>  (768-dim gemini-embedding-001 vector)
--
-- Verify when done:
--   make TRACK=starter verify-lab LAB=4
-- =============================================================================

-- =============================================================================
-- Benchmark 1: GQL Super-Node Hub Fanout Capping (IS_FIRST Window Filter)
--   PROVIDED COMPLETE — caps per-source outgoing CALLS_API fanout to top 5 RPS.
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
--   PROVIDED COMPLETE — deduplicates converging upstream paths to a hub.
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
-- Benchmark 3: Full-Text Search with Custom Synonym Dictionary & Query Enhancement
-- =============================================================================
-- TODO(Lab 4): Write a read-only Full-Text Search query over `DocumentChunks`
--   using `@search_query` (e.g., 'pan freeze 45ms').
--   Requirements:
--     - Query `content_tokens` using `SEARCH(...)`, `SCORE(...)`, and `SNIPPET(...)`
--     - Enable the custom synonym dictionary table defined in Lab 3 (maps 'pan'
--       to 'tok_pci_v2' and 'freeze' to '180-day') AND turn on query enhancement
--       (web-corpus IDF + stemming) on all three FTS functions
--     - Return `chunk_id`, `doc_type`, `entity_id`, `source_path`,
--       `section_heading`, `fts_score`, and `highlight_snippet`
--     - Order by `fts_score DESC, chunk_id ASC` and `LIMIT 10`.

-- =============================================================================
-- Benchmark 4: Fuzzy N-Gram Typo-Tolerant Search
-- =============================================================================
-- TODO(Lab 4): Write a typo-tolerant trigram search query over `DocumentChunks`
--   using `@typo_query` (e.g., 'paymnts chrge idempoten').
--   Requirements:
--     - Filter `content_ngrams` inside an inner subquery with Spanner's fuzzy
--       n-gram predicate (`min_ngrams => 2`) and score with the n-gram Jaccard
--       scorer (`fuzzy_score`)
--     - Cap raw inner trigram candidates at `LIMIT 10000` before sorting in the
--       outer query (`ORDER BY fuzzy_score DESC, chunk_id ASC LIMIT 10`).

-- =============================================================================
-- Benchmark 5: Combined JSON + Numeric + Literal LIKE Tokenized Search (Apis)
--   PROVIDED COMPLETE — single local zig-zag intersection on IdxApisSearch.
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
--   PROVIDED COMPLETE — baseline for comparing Exact KNN vs ScaNN ANN recall.
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
-- Benchmark 7: Approximate Nearest Neighbor ScaNN ANN
-- =============================================================================
-- TODO(Lab 4): Write a ScaNN Approximate Nearest Neighbor query over
--   `DocumentChunks` using `@query_vec` that strictly enforces INV-08:
--     1. Isolate the ANN scan inside a single-table CTE (`ann_candidates`) with
--        ZERO `JOIN`s inside the ANN `SELECT` block.
--     2. Include the non-null embedding predicate matching
--        `IdxDocumentChunksEmbedding` (`embedding IS NOT NULL`).
--     3. Compute cosine ANN distance with `options => JSON '{"num_leaves_to_search": 10}'`,
--        keep the distance expression bare as `approx_distance`, and make
--        `approx_distance ASC` the sole `ORDER BY` key with `LIMIT 10`.
--     4. Join back to `DocumentChunks` OUTSIDE the CTE to hydrate `content_hash`
--        and sort deterministically by `approx_distance ASC, chunk_id ASC`.
