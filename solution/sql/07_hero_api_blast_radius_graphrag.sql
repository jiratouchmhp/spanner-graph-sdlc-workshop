-- =============================================================================
-- 07_hero_api_blast_radius_graphrag.sql
-- Acme Commerce Platform — Hero GraphRAG Blast-Radius Queries (A, B & C)
--
-- Hero Question:
--   "What breaks if we change POST /v2/payments/charge?"
--   (@api_id = 'api-payments-charge-v2', exposed by svc-payments / team-payments)
--
-- Deterministic Output Contract (verified against fixtures/07_hero_expected_rows.json):
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
--   Governance attached to every row (or target API context):
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
-- =============================================================================

-- =============================================================================
-- HERO QUERY A: Exact KNN + FTS RRF + GRAPH_TABLE MATCH TRAIL (100% Recall)
-- =============================================================================
WITH
  vec_top AS (
    SELECT
      chunk_id,
      COSINE_DISTANCE(embedding, @query_vec) AS distance
    FROM DocumentChunks @{scan_method=batch}
    WHERE embedding IS NOT NULL
    ORDER BY distance ASC
    LIMIT 20
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
    LIMIT 20
  ),
  rrf_ranked AS (
    SELECT chunk_id, SUM(1.0 / (60.0 + rank + 1.0)) AS rrf_score
    FROM (
      SELECT chunk_id, rank
      FROM UNNEST(ARRAY(SELECT chunk_id FROM vec_top ORDER BY distance ASC)) AS chunk_id WITH OFFSET AS rank
      UNION ALL
      SELECT chunk_id, rank
      FROM UNNEST(ARRAY(SELECT chunk_id FROM fts_top ORDER BY fts_score DESC)) AS chunk_id WITH OFFSET AS rank
    )
    GROUP BY chunk_id
  ),
  raw_blast AS (
    -- Hop 1: Direct callers of the target API
    SELECT
      impacted_service_id,
      1 AS hop_distance,
      'DIRECT_SYNC' AS impact_category
    FROM GRAPH_TABLE(
      SdlcGraph
      MATCH (direct:Service)-[:CALLS_API]->(target:Api {api_id: @api_id})
      RETURN direct.service_id AS impacted_service_id
    )
    UNION ALL
    -- Hops 2..4: Cycle-safe MATCH TRAIL upstream traversal (handles svc-orders <-> svc-notifications)
    -- Separates async DATA_FEED consumers (svc-analytics) from synchronous/event OLTP consumers.
    SELECT
      impacted_service_id,
      hop_distance,
      IF(ARRAY_INCLUDES(dep_types, 'DATA_FEED'), 'DATA_FEED_CONSUMER', 'TRANSITIVE_UPSTREAM') AS impact_category
    FROM GRAPH_TABLE(
      SdlcGraph
      MATCH TRAIL (upstream:Service)-[deps:DEPENDS_ON]->{1,3}(direct:Service)-[:CALLS_API]->(target:Api {api_id: @api_id})
      LET dep_types = ARRAY_AGG(deps.dependency_type)
      RETURN
        upstream.service_id AS impacted_service_id,
        ARRAY_LENGTH(deps) + 1 AS hop_distance,
        dep_types
    )
  ),
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
  ),
  api_governance AS (
    SELECT
      ARRAY(
        SELECT AS STRUCT
          g.adr_id,
          g.governance_type,
          a.title,
          a.lifecycle_status
        FROM AdrGovernsApi AS g
        JOIN Adrs AS a ON g.adr_id = a.adr_id
        WHERE g.api_id = @api_id
        ORDER BY g.adr_id ASC
      ) AS governing_adrs,
      ARRAY(
        SELECT AS STRUCT
          ra.req_id,
          r.framework,
          r.severity,
          r.title,
          ra.compliance_status
        FROM ReqAppliesToApi AS ra
        JOIN Requirements AS r ON ra.req_id = r.req_id
        WHERE ra.api_id = @api_id
        ORDER BY ra.req_id ASC
      ) AS applicable_requirements,
      ARRAY(
        SELECT AS STRUCT
          d.chunk_id,
          d.source_path,
          d.section_heading,
          ROUND(r.rrf_score, 6) AS rrf_score
        FROM rrf_ranked AS r
        JOIN DocumentChunks AS d ON r.chunk_id = d.chunk_id
        WHERE EXISTS(
          SELECT 1
          FROM ChunkEntityMentions AS m
          WHERE m.chunk_id = d.chunk_id
            AND (
              (m.entity_type = 'API' AND m.entity_id = @api_id)
              OR (m.entity_type = 'ADR' AND m.entity_id IN (SELECT adr_id FROM AdrGovernsApi WHERE api_id = @api_id))
              OR (m.entity_type = 'REQUIREMENT' AND m.entity_id IN (SELECT req_id FROM ReqAppliesToApi WHERE api_id = @api_id))
            )
        )
        ORDER BY r.rrf_score DESC, d.chunk_id ASC
        LIMIT 8
      ) AS api_citations
  )
SELECT
  cb.impacted_service_id,
  s.service_name,
  cb.hop_distance,
  CASE cb.impact_category_rank
    WHEN 1 THEN 'DIRECT_SYNC'
    WHEN 2 THEN 'TRANSITIVE_UPSTREAM'
    ELSE 'DATA_FEED_CONSUMER'
  END AS impact_category,
  s.tier,
  s.criticality,
  s.owner_id,
  o.slack_channel,
  EXISTS(
    SELECT 1
    FROM ServiceCallsApi AS leg
    JOIN Apis AS dep_api ON leg.api_id = dep_api.api_id
    WHERE leg.service_id = cb.impacted_service_id
      AND (leg.migration_status = 'AT_RISK' OR dep_api.lifecycle_status = 'DEPRECATED')
  ) AS has_legacy_v1_risk,
  ARRAY(
    SELECT AS STRUCT
      d.chunk_id,
      d.source_path,
      d.section_heading,
      ROUND(COALESCE(r.rrf_score, 0.0), 6) AS rrf_score
    FROM ChunkEntityMentions AS m
    JOIN DocumentChunks AS d ON m.chunk_id = d.chunk_id
    LEFT JOIN rrf_ranked AS r ON r.chunk_id = d.chunk_id
    WHERE m.entity_type = 'SERVICE'
      AND m.entity_id = cb.impacted_service_id
    ORDER BY COALESCE(r.rrf_score, 0.0) DESC, d.chunk_id ASC
    LIMIT 3
  ) AS service_citations,
  gov.governing_adrs,
  gov.applicable_requirements,
  gov.api_citations
FROM collapsed_blast AS cb
JOIN Services AS s ON cb.impacted_service_id = s.service_id
JOIN Owners AS o ON s.owner_id = o.owner_id
CROSS JOIN api_governance AS gov
ORDER BY
  cb.hop_distance ASC,
  cb.impact_category_rank ASC,
  CASE s.criticality
    WHEN 'CRITICAL' THEN 1
    WHEN 'HIGH' THEN 2
    WHEN 'MEDIUM' THEN 3
    ELSE 4
  END ASC,
  cb.impacted_service_id ASC;

-- =============================================================================
-- HERO QUERY B: ScaNN ANN (CTE-Isolated) + FTS RRF + GRAPH_TABLE MATCH TRAIL
--   Production scalable variant (INV-08):
--     - APPROX_COSINE_DISTANCE executes strictly inside single-table `vec_top`
--       with `WHERE embedding IS NOT NULL` and ZERO JOINs inside the ANN block.
--     - Fused via server-side RRF (`UNNEST(ARRAY(...)) WITH OFFSET`) and joined
--       outside to the cycle-safe `GRAPH_TABLE(SdlcGraph MATCH TRAIL ...)` CTE.
-- =============================================================================
WITH
  vec_top AS (
    -- Single-table ScaNN ANN CTE (INV-08 compliant)
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
    LIMIT 20
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
    LIMIT 20
  ),
  rrf_ranked AS (
    SELECT chunk_id, SUM(1.0 / (60.0 + rank + 1.0)) AS rrf_score
    FROM (
      SELECT chunk_id, rank
      FROM UNNEST(ARRAY(SELECT chunk_id FROM vec_top ORDER BY approx_distance ASC)) AS chunk_id WITH OFFSET AS rank
      UNION ALL
      SELECT chunk_id, rank
      FROM UNNEST(ARRAY(SELECT chunk_id FROM fts_top ORDER BY fts_score DESC)) AS chunk_id WITH OFFSET AS rank
    )
    GROUP BY chunk_id
  ),
  raw_blast AS (
    SELECT
      impacted_service_id,
      1 AS hop_distance,
      'DIRECT_SYNC' AS impact_category
    FROM GRAPH_TABLE(
      SdlcGraph
      MATCH (direct:Service)-[:CALLS_API]->(target:Api {api_id: @api_id})
      RETURN direct.service_id AS impacted_service_id
    )
    UNION ALL
    SELECT
      impacted_service_id,
      hop_distance,
      IF(ARRAY_INCLUDES(dep_types, 'DATA_FEED'), 'DATA_FEED_CONSUMER', 'TRANSITIVE_UPSTREAM') AS impact_category
    FROM GRAPH_TABLE(
      SdlcGraph
      MATCH TRAIL (upstream:Service)-[deps:DEPENDS_ON]->{1,3}(direct:Service)-[:CALLS_API]->(target:Api {api_id: @api_id})
      LET dep_types = ARRAY_AGG(deps.dependency_type)
      RETURN
        upstream.service_id AS impacted_service_id,
        ARRAY_LENGTH(deps) + 1 AS hop_distance,
        dep_types
    )
  ),
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
  ),
  api_governance AS (
    SELECT
      ARRAY(
        SELECT AS STRUCT
          g.adr_id,
          g.governance_type,
          a.title,
          a.lifecycle_status
        FROM AdrGovernsApi AS g
        JOIN Adrs AS a ON g.adr_id = a.adr_id
        WHERE g.api_id = @api_id
        ORDER BY g.adr_id ASC
      ) AS governing_adrs,
      ARRAY(
        SELECT AS STRUCT
          ra.req_id,
          r.framework,
          r.severity,
          r.title,
          ra.compliance_status
        FROM ReqAppliesToApi AS ra
        JOIN Requirements AS r ON ra.req_id = r.req_id
        WHERE ra.api_id = @api_id
        ORDER BY ra.req_id ASC
      ) AS applicable_requirements,
      ARRAY(
        SELECT AS STRUCT
          d.chunk_id,
          d.source_path,
          d.section_heading,
          ROUND(r.rrf_score, 6) AS rrf_score
        FROM rrf_ranked AS r
        JOIN DocumentChunks AS d ON r.chunk_id = d.chunk_id
        WHERE EXISTS(
          SELECT 1
          FROM ChunkEntityMentions AS m
          WHERE m.chunk_id = d.chunk_id
            AND (
              (m.entity_type = 'API' AND m.entity_id = @api_id)
              OR (m.entity_type = 'ADR' AND m.entity_id IN (SELECT adr_id FROM AdrGovernsApi WHERE api_id = @api_id))
              OR (m.entity_type = 'REQUIREMENT' AND m.entity_id IN (SELECT req_id FROM ReqAppliesToApi WHERE api_id = @api_id))
            )
        )
        ORDER BY r.rrf_score DESC, d.chunk_id ASC
        LIMIT 8
      ) AS api_citations
  )
SELECT
  cb.impacted_service_id,
  s.service_name,
  cb.hop_distance,
  CASE cb.impact_category_rank
    WHEN 1 THEN 'DIRECT_SYNC'
    WHEN 2 THEN 'TRANSITIVE_UPSTREAM'
    ELSE 'DATA_FEED_CONSUMER'
  END AS impact_category,
  s.tier,
  s.criticality,
  s.owner_id,
  o.slack_channel,
  EXISTS(
    SELECT 1
    FROM ServiceCallsApi AS leg
    JOIN Apis AS dep_api ON leg.api_id = dep_api.api_id
    WHERE leg.service_id = cb.impacted_service_id
      AND (leg.migration_status = 'AT_RISK' OR dep_api.lifecycle_status = 'DEPRECATED')
  ) AS has_legacy_v1_risk,
  ARRAY(
    SELECT AS STRUCT
      d.chunk_id,
      d.source_path,
      d.section_heading,
      ROUND(COALESCE(r.rrf_score, 0.0), 6) AS rrf_score
    FROM ChunkEntityMentions AS m
    JOIN DocumentChunks AS d ON m.chunk_id = d.chunk_id
    LEFT JOIN rrf_ranked AS r ON r.chunk_id = d.chunk_id
    WHERE m.entity_type = 'SERVICE'
      AND m.entity_id = cb.impacted_service_id
    ORDER BY COALESCE(r.rrf_score, 0.0) DESC, d.chunk_id ASC
    LIMIT 3
  ) AS service_citations,
  gov.governing_adrs,
  gov.applicable_requirements,
  gov.api_citations
FROM collapsed_blast AS cb
JOIN Services AS s ON cb.impacted_service_id = s.service_id
JOIN Owners AS o ON s.owner_id = o.owner_id
CROSS JOIN api_governance AS gov
ORDER BY
  cb.hop_distance ASC,
  cb.impact_category_rank ASC,
  CASE s.criticality
    WHEN 'CRITICAL' THEN 1
    WHEN 'HIGH' THEN 2
    WHEN 'MEDIUM' THEN 3
    ELSE 4
  END ASC,
  cb.impacted_service_id ASC;

-- =============================================================================
-- HERO QUERY C: Schemaless Equivalent Over SdlcDynamicGraph (Path B Comparison)
--   Demonstrates identical cycle-safe MATCH TRAIL blast-radius semantics on
--   SdlcDynamicGraph (DYNAMIC LABEL + DYNAMIC PROPERTIES), using explicit
--   STRING(...) casts on dynamic JSON properties and SAFE_TO_JSON(p) for
--   Spanner Studio interactive path visualization.
-- =============================================================================

-- Query C1: Deterministic 7-row blast radius over SdlcDynamicGraph
WITH raw_dynamic_blast AS (
  SELECT
    impacted_service_id,
    service_name,
    tier,
    criticality,
    owner_id,
    1 AS hop_distance,
    'DIRECT_SYNC' AS impact_category
  FROM GRAPH_TABLE(
    SdlcDynamicGraph
    MATCH (direct:Service)-[:CALLS_API]->(target:Api)
    WHERE target.domain_key = @api_id
    RETURN
      direct.domain_key AS impacted_service_id,
      STRING(direct.service_name) AS service_name,
      STRING(direct.tier) AS tier,
      STRING(direct.criticality) AS criticality,
      STRING(direct.owner_id) AS owner_id
  )
  UNION ALL
  SELECT
    impacted_service_id,
    service_name,
    tier,
    criticality,
    owner_id,
    hop_distance,
    IF(ARRAY_INCLUDES(dep_types, 'DATA_FEED'), 'DATA_FEED_CONSUMER', 'TRANSITIVE_UPSTREAM') AS impact_category
  FROM GRAPH_TABLE(
    SdlcDynamicGraph
    MATCH TRAIL (upstream:Service)-[deps:DEPENDS_ON]->{1,3}(direct:Service)-[:CALLS_API]->(target:Api)
    WHERE target.domain_key = @api_id
    LET dep_types = ARRAY_AGG(STRING(deps.dependency_type))
    RETURN
      upstream.domain_key AS impacted_service_id,
      STRING(upstream.service_name) AS service_name,
      STRING(upstream.tier) AS tier,
      STRING(upstream.criticality) AS criticality,
      STRING(upstream.owner_id) AS owner_id,
      ARRAY_LENGTH(deps) + 1 AS hop_distance,
      dep_types
  )
),
collapsed_dynamic AS (
  SELECT
    impacted_service_id,
    ANY_VALUE(service_name) AS service_name,
    ANY_VALUE(tier) AS tier,
    ANY_VALUE(criticality) AS criticality,
    ANY_VALUE(owner_id) AS owner_id,
    MIN(hop_distance) AS hop_distance,
    MIN(
      CASE impact_category
        WHEN 'DIRECT_SYNC' THEN 1
        WHEN 'TRANSITIVE_UPSTREAM' THEN 2
        ELSE 3
      END
    ) AS impact_category_rank
  FROM raw_dynamic_blast
  GROUP BY impacted_service_id
)
SELECT
  impacted_service_id,
  service_name,
  hop_distance,
  CASE impact_category_rank
    WHEN 1 THEN 'DIRECT_SYNC'
    WHEN 2 THEN 'TRANSITIVE_UPSTREAM'
    ELSE 'DATA_FEED_CONSUMER'
  END AS impact_category,
  tier,
  criticality,
  owner_id
FROM collapsed_dynamic
ORDER BY
  hop_distance ASC,
  impact_category_rank ASC,
  CASE criticality
    WHEN 'CRITICAL' THEN 1
    WHEN 'HIGH' THEN 2
    WHEN 'MEDIUM' THEN 3
    ELSE 4
  END ASC,
  impacted_service_id ASC;

-- Query C2: Spanner Studio Interactive Visualization over SdlcDynamicGraph
GRAPH SdlcDynamicGraph
MATCH p = TRAIL (upstream:Service)-[:DEPENDS_ON]->{0,3}(direct:Service)-[:CALLS_API]->(target:Api)
WHERE target.domain_key = @api_id
RETURN
  SAFE_TO_JSON(p) AS blast_radius_path,
  PATH_LENGTH(p) AS hops
ORDER BY hops ASC
LIMIT 50;
