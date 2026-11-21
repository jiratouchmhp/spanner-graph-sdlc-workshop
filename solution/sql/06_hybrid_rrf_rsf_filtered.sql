-- =============================================================================
-- 06_hybrid_rrf_rsf_filtered.sql
-- Acme Commerce Platform — Lab 5 Hybrid Retrieval Patterns
--   1. Server-Side Reciprocal Rank Fusion (RRF, k = 60) via UNNEST(ARRAY(...)) WITH OFFSET
--   2. Relative Score Fusion (RSF) with NULLIF(max_s - min_s, 0.0) guard
--   3. Filtered Search (FTS lexical pre-filter -> Exact KNN semantic re-rank)
--   4. Two-Stage Retrieval + Optional ML.PREDICT Cross-Encoder Reranker + Fallback
--
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
--
-- Invariants Enforced (INV-08, INV-09):
--   - Execute ALL queries in READ-ONLY transactions.
--   - Cloud Spanner GoogleSQL does NOT support analytic row-numbering window
--     functions; rank top-K candidates deterministically with
--     `UNNEST(ARRAY(SELECT ... ORDER BY ... LIMIT k)) AS chunk_id WITH OFFSET AS rank`.
--   - ScaNN ANN branches operate on a single table (`DocumentChunks`) with
--     `WHERE embedding IS NOT NULL` and join metadata strictly OUTSIDE the ANN block.
--
-- Parameters:
--   @search_query STRING         (e.g. 'POST /v2/payments/charge tok_pci_v2 45ms 180-day freeze')
--   @query_vec    ARRAY<FLOAT32> (768-dim gemini-embedding-001 vector)
-- =============================================================================

-- =============================================================================
-- Query 1: Server-Side Reciprocal Rank Fusion (RRF, k = 60)
--   Fuses ScaNN ANN semantic rank + FTS (SdlcSynonymDict + enhance_query) rank
--   inside a single Cloud Spanner SQL query with zero client-side merge lag.
-- =============================================================================
WITH
  vec_top AS (
    -- Single-table ScaNN ANN CTE (INV-08: no JOIN, WHERE embedding IS NOT NULL)
    SELECT
      chunk_id,
      APPROX_COSINE_DISTANCE(
        embedding,
        @query_vec,
        options => JSON '{"num_leaves_to_search": 10}'
      ) AS approx_distance
    FROM DocumentChunks
    WHERE embedding IS NOT NULL
    ORDER BY approx_distance ASC
    LIMIT 50
  ),
  fts_top AS (
    SELECT
      chunk_id,
      SCORE(
        content_tokens,
        @search_query,
        dictionary => 'SdlcSynonymDict',
        enhance_query => TRUE
      ) AS fts_score
    FROM DocumentChunks
    WHERE SEARCH(
      content_tokens,
      @search_query,
      dictionary => 'SdlcSynonymDict',
      enhance_query => TRUE
    )
    ORDER BY fts_score DESC
    LIMIT 50
  ),
  fused_rrf AS (
    SELECT
      chunk_id,
      SUM(1.0 / (60.0 + rank + 1.0)) AS rrf_score,
      COUNT(*) AS matched_retrievers
    FROM (
      SELECT chunk_id, rank
      FROM UNNEST(ARRAY(
        SELECT chunk_id FROM vec_top ORDER BY approx_distance ASC
      )) AS chunk_id WITH OFFSET AS rank
      UNION ALL
      SELECT chunk_id, rank
      FROM UNNEST(ARRAY(
        SELECT chunk_id FROM fts_top ORDER BY fts_score DESC
      )) AS chunk_id WITH OFFSET AS rank
    )
    GROUP BY chunk_id
    ORDER BY rrf_score DESC, chunk_id ASC
    LIMIT 10
  )
SELECT
  f.chunk_id,
  ROUND(f.rrf_score, 6) AS rrf_score,
  f.matched_retrievers,
  d.doc_type,
  d.entity_id,
  d.source_path,
  d.section_heading,
  SUBSTR(d.content, 1, 240) AS excerpt
FROM fused_rrf AS f
JOIN DocumentChunks AS d ON f.chunk_id = d.chunk_id
ORDER BY f.rrf_score DESC, f.chunk_id ASC;

-- =============================================================================
-- Query 2: Relative Score Fusion (RSF) with NULLIF Divide-by-Zero Guard
--   Preserves continuous score margins using weighted min-max normalization
--   (0.60 semantic similarity + 0.40 lexical FTS score). Guards against
--   zero-variance denominators when a retriever returns 1 match or tied scores.
-- =============================================================================
WITH
  ann_raw AS (
    SELECT
      chunk_id,
      APPROX_COSINE_DISTANCE(
        embedding,
        @query_vec,
        options => JSON '{"num_leaves_to_search": 10}'
      ) AS dist
    FROM DocumentChunks
    WHERE embedding IS NOT NULL
    ORDER BY dist ASC
    LIMIT 50
  ),
  ann_sim AS (
    SELECT chunk_id, 1.0 - dist AS sim
    FROM ann_raw
  ),
  fts_raw AS (
    SELECT
      chunk_id,
      SCORE(
        content_tokens,
        @search_query,
        dictionary => 'SdlcSynonymDict',
        enhance_query => TRUE
      ) AS fts_score
    FROM DocumentChunks
    WHERE SEARCH(
      content_tokens,
      @search_query,
      dictionary => 'SdlcSynonymDict',
      enhance_query => TRUE
    )
    ORDER BY fts_score DESC
    LIMIT 50
  ),
  ann_bounds AS (
    SELECT MIN(sim) AS min_s, MAX(sim) AS max_s FROM ann_sim
  ),
  fts_bounds AS (
    SELECT MIN(fts_score) AS min_s, MAX(fts_score) AS max_s FROM fts_raw
  ),
  rsf_scored AS (
    SELECT
      COALESCE(a.chunk_id, f.chunk_id) AS chunk_id,
      0.60 * COALESCE(
        SAFE_DIVIDE(a.sim - ab.min_s, NULLIF(ab.max_s - ab.min_s, 0.0)),
        IF(a.chunk_id IS NOT NULL, 1.0, 0.0)
      )
      + 0.40 * COALESCE(
        SAFE_DIVIDE(f.fts_score - fb.min_s, NULLIF(fb.max_s - fb.min_s, 0.0)),
        IF(f.chunk_id IS NOT NULL, 1.0, 0.0)
      ) AS rsf_score
    FROM ann_sim AS a
    FULL OUTER JOIN fts_raw AS f ON a.chunk_id = f.chunk_id
    CROSS JOIN ann_bounds AS ab
    CROSS JOIN fts_bounds AS fb
    ORDER BY rsf_score DESC, chunk_id ASC
    LIMIT 10
  )
SELECT
  r.chunk_id,
  ROUND(r.rsf_score, 6) AS rsf_score,
  d.doc_type,
  d.entity_id,
  d.source_path,
  d.section_heading
FROM rsf_scored AS r
JOIN DocumentChunks AS d ON r.chunk_id = d.chunk_id
ORDER BY r.rsf_score DESC, r.chunk_id ASC;

-- =============================================================================
-- Query 3: Filtered Search (FTS Lexical Pre-Filter -> Exact KNN Semantic Re-Rank)
--   Use when domain terminology (e.g., exact PCI/SOX/ADR keywords) is a hard
--   inclusion predicate and exact COSINE_DISTANCE ranks the surviving slice.
-- =============================================================================
SELECT
  chunk_id,
  doc_type,
  entity_id,
  source_path,
  section_heading,
  COSINE_DISTANCE(embedding, @query_vec) AS exact_cosine_distance
FROM (
  SELECT
    chunk_id,
    doc_type,
    entity_id,
    source_path,
    section_heading,
    embedding
  FROM DocumentChunks
  WHERE SEARCH(
      content_tokens,
      @search_query,
      dictionary => 'SdlcSynonymDict',
      enhance_query => TRUE
    )
    AND embedding IS NOT NULL
  LIMIT 1000
)
ORDER BY exact_cosine_distance ASC, chunk_id ASC
LIMIT 10;

-- =============================================================================
-- Query 4A: Two-Stage Retrieval + Optional In-DB Cross-Encoder Reranker
--   Requires deploying a Vertex AI custom reranker endpoint in asia-southeast1
--   and registering MODEL SdlcReranker via 03c_optional_reranker_model.sql.tmpl.
--   Stage 1 isolates ScaNN ANN (single table, WHERE embedding IS NOT NULL);
--   Stage 2 hydrates full content and reranks top 30 via ML.PREDICT.
-- =============================================================================
WITH stage1_ann AS (
  SELECT
    chunk_id,
    APPROX_COSINE_DISTANCE(
      embedding,
      @query_vec,
      options => JSON '{"num_leaves_to_search": 10}'
    ) AS approx_distance
  FROM DocumentChunks
  WHERE embedding IS NOT NULL
  ORDER BY approx_distance ASC
  LIMIT 30
)
SELECT
  r.chunk_id,
  r.source_path,
  r.section_heading,
  r.score AS reranker_score
FROM ML.PREDICT(
  MODEL SdlcReranker,
  (
    SELECT
      d.chunk_id,
      d.source_path,
      d.section_heading,
      d.content AS text,
      @search_query AS text_pair
    FROM stage1_ann AS s1
    JOIN DocumentChunks AS d ON s1.chunk_id = d.chunk_id
  )
) AS r
ORDER BY reranker_score DESC, r.chunk_id ASC
LIMIT 5;

-- =============================================================================
-- Query 4B: Endpoint-Free Two-Stage Reranking Fallback (Zero Custom Endpoint)
--   When no custom cross-encoder endpoint is deployed, use Stage 1 hybrid RRF
--   (k = 60) followed by Stage 2 exact COSINE_DISTANCE re-scoring over the
--   narrowed top-25 candidate set inside Cloud Spanner Enterprise.
-- =============================================================================
WITH
  stage1_vec AS (
    SELECT
      chunk_id,
      APPROX_COSINE_DISTANCE(
        embedding,
        @query_vec,
        options => JSON '{"num_leaves_to_search": 10}'
      ) AS approx_distance
    FROM DocumentChunks
    WHERE embedding IS NOT NULL
    ORDER BY approx_distance ASC
    LIMIT 25
  ),
  stage1_fts AS (
    SELECT
      chunk_id,
      SCORE(
        content_tokens,
        @search_query,
        dictionary => 'SdlcSynonymDict',
        enhance_query => TRUE
      ) AS fts_score
    FROM DocumentChunks
    WHERE SEARCH(
      content_tokens,
      @search_query,
      dictionary => 'SdlcSynonymDict',
      enhance_query => TRUE
    )
    ORDER BY fts_score DESC
    LIMIT 25
  ),
  stage1_rrf AS (
    SELECT chunk_id, SUM(1.0 / (60.0 + rank + 1.0)) AS rrf_score
    FROM (
      SELECT chunk_id, rank
      FROM UNNEST(ARRAY(SELECT chunk_id FROM stage1_vec ORDER BY approx_distance ASC)) AS chunk_id WITH OFFSET AS rank
      UNION ALL
      SELECT chunk_id, rank
      FROM UNNEST(ARRAY(SELECT chunk_id FROM stage1_fts ORDER BY fts_score DESC)) AS chunk_id WITH OFFSET AS rank
    )
    GROUP BY chunk_id
    ORDER BY rrf_score DESC, chunk_id ASC
    LIMIT 25
  )
SELECT
  s1.chunk_id,
  d.doc_type,
  d.entity_id,
  d.source_path,
  d.section_heading,
  ROUND(s1.rrf_score, 6) AS rrf_score,
  ROUND(1.0 - COSINE_DISTANCE(d.embedding, @query_vec), 6) AS exact_cosine_similarity
FROM stage1_rrf AS s1
JOIN DocumentChunks AS d ON s1.chunk_id = d.chunk_id
WHERE d.embedding IS NOT NULL
ORDER BY s1.rrf_score DESC, exact_cosine_similarity DESC, s1.chunk_id ASC
LIMIT 5;
