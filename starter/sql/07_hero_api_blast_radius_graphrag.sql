-- =============================================================================
-- 07_hero_api_blast_radius_graphrag.sql (STARTER — Lab 5)
-- Acme Commerce Platform — Hero GraphRAG Blast-Radius Query
--
-- Antigravity Skills for Lab 5:
--   - Primary: .agents/skills/spanner-search-ai-and-graphrag/SKILL.md
--   - Primary: .agents/skills/spanner-graph-modeling-and-gql/SKILL.md
--   - Support: .agents/skills/sdlc-graphrag-workshop-guide/SKILL.md
--
-- Hero Question:
--   "What breaks if we change POST /v2/payments/charge?"
--   (@api_id = 'api-payments-charge-v2', exposed by svc-payments / team-payments)
--
-- Deterministic Output Contract (verified against solution/sql/fixtures/07_hero_expected_rows.json):
--   Exact 7 impacted service rows ordered by:
--     1. hop_distance ASC
--     2. impact_category_rank ASC (DIRECT_SYNC=1, TRANSITIVE_UPSTREAM=2, DATA_FEED_CONSUMER=3)
--     3. criticality_rank ASC     (CRITICAL=1, HIGH=2, MEDIUM=3, LOW=4)
--     4. impacted_service_id ASC
--
--   Row 1: svc-billing       | hop 1 | DIRECT_SYNC         | TIER_0 | CRITICAL | team-billing      | has_legacy_v1_risk = TRUE
--   Row 2: svc-checkout      | hop 1 | DIRECT_SYNC         | TIER_0 | CRITICAL | team-checkout     | has_legacy_v1_risk = FALSE
--   Row 3: svc-orders        | hop 2 | TRANSITIVE_UPSTREAM | TIER_1 | HIGH     | team-checkout     | has_legacy_v1_risk = FALSE
--   Row 4: svc-partner-gw    | hop 2 | TRANSITIVE_UPSTREAM | TIER_1 | HIGH     | team-integrations | has_legacy_v1_risk = FALSE
--   Row 5: svc-notifications | hop 3 | TRANSITIVE_UPSTREAM | TIER_2 | MEDIUM   | team-integrations | has_legacy_v1_risk = FALSE
--   Row 6: svc-webhooks      | hop 3 | TRANSITIVE_UPSTREAM | TIER_2 | MEDIUM   | team-integrations | has_legacy_v1_risk = FALSE
--   Row 7: svc-analytics     | hop 3 | DATA_FEED_CONSUMER  | TIER_2 | LOW      | team-data         | has_legacy_v1_risk = FALSE
--
--   Governance & Citations attached:
--     - 5 Governing ADRs: ADR-004 (MANDATES), ADR-005, ADR-006, ADR-008, ADR-010 (CONSTRAINS)
--     - 5 Applicable Requirements: REQ-001, REQ-002, REQ-003, REQ-005, REQ-006
--     - Top RRF-ranked Markdown citations grounded via ChunkEntityMentions
--
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
-- Execution Mode: READ-ONLY transaction (INV-08, INV-09)
--
-- Parameters:
--   @api_id       STRING         (default: 'api-payments-charge-v2')
--   @search_query STRING         (default: 'POST /v2/payments/charge tok_pci_v2 45ms 180-day freeze')
--   @query_vec    ARRAY<FLOAT32> (768-dim gemini-embedding-001 vector)
--
-- Verify when done:
--   make TRACK=starter verify-lab LAB=5
-- =============================================================================

-- =============================================================================
-- HERO QUERY (Production ScaNN ANN + FTS RRF + Cycle-Safe GraphRAG)
-- =============================================================================
WITH
  -- ---------------------------------------------------------------------------
  -- Step 1: Single-Table ScaNN ANN Vector Retrieval (`vec_top`)
  -- ---------------------------------------------------------------------------
  -- TODO(Lab 5): Replace this stub `vec_top` CTE with a single-table ScaNN ANN
  --   scan over `DocumentChunks` (INV-08):
  --     - Select `chunk_id` and bare cosine ANN distance (`approx_distance`)
  --       against `@query_vec` with `options => JSON '{"num_leaves_to_search": 10}'`
  --     - Filter `WHERE embedding IS NOT NULL` (matches IdxDocumentChunksEmbedding)
  --     - ZERO JOINs inside this CTE; `ORDER BY approx_distance ASC LIMIT 20`.
  vec_top AS (
    SELECT
      CAST(NULL AS STRING) AS chunk_id,
      CAST(NULL AS FLOAT64) AS approx_distance
    FROM DocumentChunks
    WHERE FALSE
  ),

  -- ---------------------------------------------------------------------------
  -- Step 2: Full-Text Search Retrieval (`fts_top`) — PROVIDED COMPLETE
  -- ---------------------------------------------------------------------------
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
    LIMIT 20
  ),

  -- ---------------------------------------------------------------------------
  -- Step 3: Server-Side Reciprocal Rank Fusion (`rrf_ranked`, k = 60)
  -- ---------------------------------------------------------------------------
  -- TODO(Lab 5): Fuse `vec_top` and `fts_top` using `UNNEST(ARRAY(...)) WITH OFFSET`
  --   (INV-09: never use analytic row-numbering window functions!) and compute
  --   `SUM(1.0 / (60.0 + rank + 1.0)) AS rrf_score` grouped by `chunk_id`.
  rrf_ranked AS (
    SELECT
      chunk_id,
      CAST(0.0 AS FLOAT64) AS rrf_score
    FROM fts_top
  ),

  -- ---------------------------------------------------------------------------
  -- Step 4: Multi-Hop Cycle-Safe Upstream Blast Radius (`raw_blast`)
  -- ---------------------------------------------------------------------------
  raw_blast AS (
    -- Hop 1 (PROVIDED): Direct callers of the target API
    SELECT
      impacted_service_id,
      1 AS hop_distance,
      'DIRECT_SYNC' AS impact_category
    FROM GRAPH_TABLE(
      SdlcGraph
      MATCH (direct:Service)-[:CALLS_API]->(target:Api {api_id: @api_id})
      RETURN direct.service_id AS impacted_service_id
    )

    -- TODO(Lab 5): UNION ALL Hops 2..4 upstream traversal over `SdlcGraph`!
    --   Requirements:
    --     1. Traverse `(upstream:Service)-[deps:DEPENDS_ON]->{1,3}(direct:Service)-[:CALLS_API]->(target:Api {api_id: @api_id})`
    --        using Spanner GQL's edge-unique path mode so the intentional
    --        `svc-orders <-> svc-notifications` cycle terminates deterministically.
    --     2. Collect edge `dependency_type` values (`ARRAY_AGG(deps.dependency_type)`)
    --        and compute `hop_distance = ARRAY_LENGTH(deps) + 1`.
    --     3. Separate non-blocking async analytical consumers (`svc-analytics`,
    --        whose path traverses a `DATA_FEED` edge) from synchronous/event
    --        OLTP upstream consumers (`TRANSITIVE_UPSTREAM`) using
    --        `IF(ARRAY_INCLUDES(dep_types, 'DATA_FEED'), ..., 'TRANSITIVE_UPSTREAM')`.
  ),

  -- ---------------------------------------------------------------------------
  -- Step 5: Collapse Shortest Hop & Highest-Priority Impact Category per Service
  -- ---------------------------------------------------------------------------
  collapsed_blast AS (
    SELECT
      impacted_service_id,
      MIN(hop_distance) AS hop_distance,
      MIN(
        CASE impact_category
          WHEN 'DIRECT_SYNC' THEN 1
          WHEN 'TRANSITIVE_UPSTREAM' THEN 2
          ELSE 3
        END
      ) AS impact_category_rank
    FROM raw_blast
    GROUP BY impacted_service_id
  )

  -- ---------------------------------------------------------------------------
  -- Step 6: Governance (ADRs + Requirements) & Verifiable Markdown Citations
  -- ---------------------------------------------------------------------------
  -- TODO(Lab 5): Add an `api_governance` CTE that aggregates:
  --   1. `governing_adrs`: ARRAY of STRUCTs from `AdrGovernsApi JOIN Adrs`
  --      for `@api_id` ordered by `adr_id ASC` (5 ADRs for api-payments-charge-v2).
  --   2. `applicable_requirements`: ARRAY of STRUCTs from `ReqAppliesToApi JOIN Requirements`
  --      for `@api_id` ordered by `req_id ASC` (5 REQs for api-payments-charge-v2).
  --   3. `api_citations`: Top 8 RRF-ranked `DocumentChunks` linked via
  --      `ChunkEntityMentions` to `@api_id`, its governing ADRs, or its
  --      applicable requirements (`ORDER BY rrf_score DESC, chunk_id ASC LIMIT 8`).

-- -----------------------------------------------------------------------------
-- Step 7: Final Deterministic Blast-Radius Projection
--   TODO(Lab 5): Enrich each row in `collapsed_blast` with:
--     - `Services` metadata (`service_name`, `tier`, `criticality`, `owner_id`)
--     - `Owners.slack_channel`
--     - `has_legacy_v1_risk` (EXISTS check flagging `ServiceCallsApi.migration_status = 'AT_RISK'`
--       or `Apis.lifecycle_status = 'DEPRECATED'` — must be TRUE for `svc-billing`!)
--     - `service_citations` (top 3 `DocumentChunks` mentioning `cb.impacted_service_id`)
--     - `gov.governing_adrs`, `gov.applicable_requirements`, `gov.api_citations`
--     - Deterministic 4-key sort:
--         hop_distance ASC, impact_category_rank ASC,
--         CASE criticality (CRITICAL=1, HIGH=2, MEDIUM=3, ELSE 4) ASC,
--         impacted_service_id ASC.
-- -----------------------------------------------------------------------------
SELECT
  cb.impacted_service_id,
  s.service_name,
  cb.hop_distance,
  cb.impact_category_rank,
  s.tier,
  s.criticality,
  s.owner_id,
  o.slack_channel
FROM collapsed_blast AS cb
JOIN Services AS s ON cb.impacted_service_id = s.service_id
JOIN Owners AS o ON s.owner_id = o.owner_id
ORDER BY cb.hop_distance ASC, cb.impacted_service_id ASC;
