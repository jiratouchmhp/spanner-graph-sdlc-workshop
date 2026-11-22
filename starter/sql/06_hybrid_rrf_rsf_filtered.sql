-- =============================================================================
-- 06_hybrid_rrf_rsf_filtered.sql (STARTER — Lab 5)
-- Acme Commerce Platform — Lab 5 Hybrid Retrieval Patterns
--   1. Server-Side Reciprocal Rank Fusion (RRF, k = 60) [Lab 5 Exercise]
--   2. Relative Score Fusion (RSF) with NULLIF(max_s - min_s, 0.0) guard [PROVIDED]
--   3. Filtered Search (FTS lexical pre-filter -> Exact KNN semantic re-rank) [PROVIDED]
--   4. Two-Stage Retrieval + Optional ML.PREDICT Cross-Encoder Reranker [PROVIDED]
--
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
--
-- Antigravity Skills for Lab 5:
--   - Primary: .agents/skills/spanner-search-ai-and-graphrag/SKILL.md
--   - Support: .agents/skills/spanner-performance-observability-and-sizing/SKILL.md
--
-- Invariants to Enforce (INV-08, INV-09):
--   - Execute ALL queries in READ-ONLY transactions.
--   - Cloud Spanner GoogleSQL lacks general analytic ranking window functions —
--     convert each sorted top-K candidate list into an ordered array and unnest
--     it with positional offsets to assign deterministic 0-based ranks!
--   - ScaNN ANN branches operate on a single table (`DocumentChunks`) with
--     `WHERE embedding IS NOT NULL` and join metadata strictly OUTSIDE the ANN block.
--
-- Parameters:
--   @search_query STRING         (e.g. 'POST /v2/payments/charge tok_pci_v2 45ms 180-day freeze')
--   @query_vec    ARRAY<FLOAT32> (768-dim gemini-embedding-001 vector)
--
-- Verify when done:
--   make TRACK=starter verify-lab LAB=5
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
  )
-- TODO(Lab 5): Complete `fused_rrf` and the final hydration `SELECT` for Query 1!
--   Requirements (INV-09):
--     1. NEVER use analytic rank window functions (unsupported in Cloud Spanner).
--     2. Rank `vec_top` (ordered by `approx_distance ASC`) and `fts_top` (ordered
--        by `fts_score DESC`) via Spanner's array-unnest positional-offset idiom
--        (`WITH OFFSET AS rank`), combine both branches with `UNION ALL`, and
--        compute `SUM(1.0 / (60.0 + rank + 1.0)) AS rrf_score` +
--        `COUNT(*) AS matched_retrievers` grouped by `chunk_id`.
--     3. Keep top 10 (`ORDER BY rrf_score DESC, chunk_id ASC LIMIT 10`).
--     4. Join `fused_rrf` to `DocumentChunks` OUTSIDE the ANN/RRF CTEs and return:
--        `chunk_id`, `ROUND(rrf_score, 6) AS rrf_score`, `matched_retrievers`,
--        `doc_type`, `entity_id`, `source_path`, `section_heading`, and
--        `SUBSTR(content, 1, 240) AS excerpt`.
SELECT 'TODO(Lab 5): replace this stub with fused_rrf + DocumentChunks join' AS status;

-- =============================================================================
-- Query 2: Relative Score Fusion (RSF) with NULLIF Divide-by-Zero Guard
--   PROVIDED COMPLETE — weighted min-max normalization (0.60 semantic + 0.40 FTS).
--   Note: @{optimizer_version=latest} ensures Spanner uses optimizer >= 6 for
--   SCORE() inside a CTE referenced by both FULL OUTER JOIN and bounds CTEs.
-- =============================================================================
@{optimizer_version=latest}
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
--   PROVIDED COMPLETE — hard lexical predicate followed by exact COSINE_DISTANCE.
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
-- Query 4: Two-Stage Retrieval + Optional In-DB Cross-Encoder Reranker
--   PROVIDED COMPLETE — requires MODEL SdlcReranker (03c_optional_reranker_model.sql.tmpl).
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
