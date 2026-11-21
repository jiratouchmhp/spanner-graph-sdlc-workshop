-- =============================================================================
-- 10_validate_migration_parity.sql
-- Acme Commerce Platform — 4-Tier Neo4j-vs-Spanner & Path A/B Parity Validation
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
-- Execution Mode: READ-ONLY transaction
--
-- Validates 100% migration parity against WORKSHOP_SPEC.json &
-- solution/sql/fixtures/10_row_hash_expected.json:
--   Tier 1: Global counts (68 nodes, 174 explicit edges, 60 dual-projected =
--           234 total logical edges, 45 unstructured Markdown source docs)
--   Tier 2: Per-label & per-edge-type breakdown across tables & SdlcGraph
--   Tier 3: Topological & domain invariants (svc-auth hub = 10, cycle = 2,
--           svc-analytics DATA_FEED = 3, legacy AT_RISK = 1, supersessions = 3,
--           Hero API callers = 2, governing ADRs = 5, requirements = 5)
--   Tier 4: Deterministic canonical SHA-256 + BIT_XOR(FARM_FINGERPRINT(...))
--           matching solution/sql/fixtures/10_row_hash_expected.json byte-for-byte.
-- =============================================================================

-- =============================================================================
-- TIER 1: Global Path A + Path B Parity Scorecard (Every Check MUST be 'PASS')
-- =============================================================================
WITH counts AS (
  SELECT
    (SELECT COUNT(*) FROM Owners)
    + (SELECT COUNT(*) FROM Services)
    + (SELECT COUNT(*) FROM Apis)
    + (SELECT COUNT(*) FROM Adrs)
    + (SELECT COUNT(*) FROM Requirements) AS total_nodes,
    (SELECT COUNT(*) FROM ServiceCallsApi)
    + (SELECT COUNT(*) FROM ServiceDependsOnService)
    + (SELECT COUNT(*) FROM AdrGovernsApi)
    + (SELECT COUNT(*) FROM AdrSupersedesAdr)
    + (SELECT COUNT(*) FROM ReqAppliesToApi)
    + (SELECT COUNT(*) FROM ReqAppliesToService) AS explicit_edge_rows,
    (SELECT COUNT(*) FROM Apis WHERE service_id IS NOT NULL)
    + (SELECT COUNT(*) FROM Services WHERE owner_id IS NOT NULL)
    + (SELECT COUNT(*) FROM Adrs WHERE owner_id IS NOT NULL)
    + (SELECT COUNT(*) FROM Requirements WHERE owner_id IS NOT NULL) AS dual_projected_edges,
    (SELECT COUNT(DISTINCT source_path) FROM DocumentChunks) AS unstructured_markdown_docs,
    (SELECT COUNT(*) FROM DocumentChunks) AS total_document_chunks,
    (SELECT COUNT(*) FROM ChunkEntityMentions) AS total_chunk_mentions
)
SELECT
  metric,
  actual_value,
  expected_value,
  IF(actual_value = expected_value, 'PASS', 'FAIL') AS status
FROM (
  SELECT 'total_nodes' AS metric, total_nodes AS actual_value, 68 AS expected_value FROM counts
  UNION ALL
  SELECT 'explicit_edge_rows', explicit_edge_rows, 174 FROM counts
  UNION ALL
  SELECT 'dual_projected_edges', dual_projected_edges, 60 FROM counts
  UNION ALL
  SELECT 'total_logical_graph_edges', explicit_edge_rows + dual_projected_edges, 234 FROM counts
  UNION ALL
  SELECT 'path_b_distinct_markdown_docs', unstructured_markdown_docs, 45 FROM counts
)
ORDER BY metric ASC;

-- =============================================================================
-- TIER 2: Per-Label & Per-Edge-Type Distribution (Relational + SdlcGraph)
-- =============================================================================
SELECT
  category,
  element_name,
  actual_count,
  expected_count,
  IF(actual_count = expected_count, 'PASS', 'FAIL') AS status
FROM (
  -- Path A Node Labels (5 tables -> 68 nodes)
  SELECT 'NODE' AS category, 'Owner' AS element_name, COUNT(*) AS actual_count, 8 AS expected_count
  FROM GRAPH_TABLE(SdlcGraph MATCH (n:Owner) RETURN n.owner_id)
  UNION ALL
  SELECT 'NODE', 'Service', COUNT(*), 12
  FROM GRAPH_TABLE(SdlcGraph MATCH (n:Service) RETURN n.service_id)
  UNION ALL
  SELECT 'NODE', 'Api', COUNT(*), 24
  FROM GRAPH_TABLE(SdlcGraph MATCH (n:Api) RETURN n.api_id)
  UNION ALL
  SELECT 'NODE', 'Adr', COUNT(*), 10
  FROM GRAPH_TABLE(SdlcGraph MATCH (n:Adr) RETURN n.adr_id)
  UNION ALL
  SELECT 'NODE', 'Requirement', COUNT(*), 14
  FROM GRAPH_TABLE(SdlcGraph MATCH (n:Requirement) RETURN n.req_id)

  -- Explicit M:N Edge Types (6 tables -> 174 edges)
  UNION ALL
  SELECT 'EXPLICIT_EDGE', 'CALLS_API', COUNT(*), 48
  FROM GRAPH_TABLE(SdlcGraph MATCH (:Service)-[e:CALLS_API]->(:Api) RETURN e.api_id)
  UNION ALL
  SELECT 'EXPLICIT_EDGE', 'DEPENDS_ON', COUNT(*), 28
  FROM GRAPH_TABLE(SdlcGraph MATCH (:Service)-[e:DEPENDS_ON]->(:Service) RETURN e.to_service_id)
  UNION ALL
  SELECT 'EXPLICIT_EDGE', 'GOVERNS', COUNT(*), 36
  FROM GRAPH_TABLE(SdlcGraph MATCH (:Adr)-[e:GOVERNS]->(:Api) RETURN e.api_id)
  UNION ALL
  SELECT 'EXPLICIT_EDGE', 'SUPERSEDES', COUNT(*), 3
  FROM GRAPH_TABLE(SdlcGraph MATCH (:Adr)-[e:SUPERSEDES]->(:Adr) RETURN e.supersedes_adr_id)
  UNION ALL
  SELECT 'EXPLICIT_EDGE', 'APPLIES_TO_API', COUNT(*), 33
  FROM GRAPH_TABLE(SdlcGraph MATCH (:Requirement)-[e:APPLIES_TO_API]->(:Api) RETURN e.api_id)
  UNION ALL
  SELECT 'EXPLICIT_EDGE', 'APPLIES_TO_SERVICE', COUNT(*), 26
  FROM GRAPH_TABLE(SdlcGraph MATCH (:Requirement)-[e:APPLIES_TO_SERVICE]->(:Service) RETURN e.service_id)

  -- Dual-Projected N:1 Folded FK Edge Types (4 node tables -> 60 edges)
  UNION ALL
  SELECT 'DUAL_PROJECTED_EDGE', 'EXPOSES', COUNT(*), 24
  FROM GRAPH_TABLE(SdlcGraph MATCH (:Service)-[e:EXPOSES]->(:Api) RETURN e.api_id)
  UNION ALL
  SELECT 'DUAL_PROJECTED_EDGE', 'OWNS_SERVICE', COUNT(*), 12
  FROM GRAPH_TABLE(SdlcGraph MATCH (:Owner)-[e:OWNS_SERVICE]->(:Service) RETURN e.service_id)
  UNION ALL
  SELECT 'DUAL_PROJECTED_EDGE', 'OWNS_ADR', COUNT(*), 10
  FROM GRAPH_TABLE(SdlcGraph MATCH (:Owner)-[e:OWNS_ADR]->(:Adr) RETURN e.adr_id)
  UNION ALL
  SELECT 'DUAL_PROJECTED_EDGE', 'OWNS_REQ', COUNT(*), 14
  FROM GRAPH_TABLE(SdlcGraph MATCH (:Owner)-[e:OWNS_REQ]->(:Requirement) RETURN e.req_id)
)
ORDER BY category ASC, element_name ASC;

-- =============================================================================
-- TIER 3: Topological Traps, Hub Degrees & Hero Scenario Invariants
-- =============================================================================
SELECT
  invariant_name,
  actual_value,
  expected_value,
  IF(actual_value = expected_value, 'PASS', 'FAIL') AS status
FROM (
  -- High-fanin hub svc-auth (in-degree = 10)
  SELECT
    'hub_svc_auth_in_degree' AS invariant_name,
    COUNT(*) AS actual_value,
    10 AS expected_value
  FROM ServiceDependsOnService
  WHERE to_service_id = 'svc-auth'

  UNION ALL
  -- High-fanin hub api-auth-token-v2 callers (= 10)
  SELECT
    'hub_api_auth_token_v2_callers',
    COUNT(*),
    10
  FROM ServiceCallsApi
  WHERE api_id = 'api-auth-token-v2'

  UNION ALL
  -- Intentional cycle edges: svc-orders <-> svc-notifications (both ASYNC_EVENT)
  SELECT
    'cycle_svc_orders_notifications_edges',
    COUNT(*),
    2
  FROM ServiceDependsOnService
  WHERE dependency_type = 'ASYNC_EVENT'
    AND (
      (from_service_id = 'svc-orders' AND to_service_id = 'svc-notifications')
      OR (from_service_id = 'svc-notifications' AND to_service_id = 'svc-orders')
    )

  UNION ALL
  -- Async DATA_FEED consumer: svc-analytics -> {svc-orders, svc-ledger, svc-inventory}
  SELECT
    'data_feed_svc_analytics_edges',
    COUNT(*),
    3
  FROM ServiceDependsOnService
  WHERE from_service_id = 'svc-analytics'
    AND dependency_type = 'DATA_FEED'
    AND to_service_id IN ('svc-orders', 'svc-ledger', 'svc-inventory')

  UNION ALL
  -- Legacy AT_RISK consumer: svc-billing -> api-payments-charge-v1 (DEPRECATED)
  SELECT
    'legacy_at_risk_svc_billing_v1_call',
    COUNT(*),
    1
  FROM ServiceCallsApi AS c
  JOIN Apis AS a ON c.api_id = a.api_id
  WHERE c.service_id = 'svc-billing'
    AND c.api_id = 'api-payments-charge-v1'
    AND c.migration_status = 'AT_RISK'
    AND a.lifecycle_status = 'DEPRECATED'

  UNION ALL
  -- Hero API direct callers: strictly svc-billing & svc-checkout (count = 2)
  SELECT
    'hero_api_direct_callers',
    COUNT(*),
    2
  FROM ServiceCallsApi
  WHERE api_id = 'api-payments-charge-v2'

  UNION ALL
  -- Hero API governing ADRs (= 5: ADR-004, ADR-005, ADR-006, ADR-008, ADR-010)
  SELECT
    'hero_api_governing_adrs',
    COUNT(*),
    5
  FROM AdrGovernsApi
  WHERE api_id = 'api-payments-charge-v2'

  UNION ALL
  -- Hero API applicable requirements (= 5: REQ-001, REQ-002, REQ-003, REQ-005, REQ-006)
  SELECT
    'hero_api_applicable_requirements',
    COUNT(*),
    5
  FROM ReqAppliesToApi
  WHERE api_id = 'api-payments-charge-v2'

  UNION ALL
  -- Path B DocumentChunks embedding coverage check (all rows embedded once PDML completes)
  SELECT
    'path_b_embedded_chunks_missing',
    COUNT(*),
    0
  FROM DocumentChunks
  WHERE embedding IS NULL
)
ORDER BY invariant_name ASC;

-- =============================================================================
-- TIER 4: Deterministic Per-Table Row Content Hash Parity
--   Computes the exact sorted-row canonical SHA-256 hash matching
--   data/structured/stage_apoc_export.py & fixtures/10_row_hash_expected.json,
--   alongside order-independent BIT_XOR(FARM_FINGERPRINT(...)) checksums.
-- =============================================================================
WITH table_lines AS (
  SELECT
    'Owners' AS table_name,
    CONCAT(
      TRIM(owner_id), '|', TRIM(team_name), '|', TRIM(org_unit), '|',
      TRIM(slack_channel), '|', TRIM(email), '|', TRIM(oncall_pager), '|',
      TRIM(clearance_tier), '|', TRIM(escalation_lead)
    ) AS canonical_line
  FROM Owners

  UNION ALL
  SELECT
    'Services',
    CONCAT(
      TRIM(service_id), '|', TRIM(service_name), '|', TRIM(tier), '|',
      TRIM(criticality), '|', TRIM(owner_id), '|', CAST(sla_target_pct AS STRING), '|',
      TRIM(domain), '|', TRIM(lifecycle_status), '|', TRIM(description)
    )
  FROM Services

  UNION ALL
  SELECT
    'Apis',
    CONCAT(
      TRIM(api_id), '|', TRIM(service_id), '|', TRIM(http_method), '|',
      TRIM(http_path), '|', TRIM(api_version), '|', TRIM(lifecycle_status), '|',
      TRIM(criticality), '|', IF(is_public, 'true', 'false'), '|',
      CAST(latency_budget_ms AS STRING), '|', TRIM(description)
    )
  FROM Apis

  UNION ALL
  SELECT
    'Adrs',
    CONCAT(
      TRIM(adr_id), '|', TRIM(title), '|', TRIM(lifecycle_status), '|',
      TRIM(owner_id), '|', FORMAT_DATE('%Y-%m-%d', decision_date), '|', TRIM(summary)
    )
  FROM Adrs

  UNION ALL
  SELECT
    'Requirements',
    CONCAT(
      TRIM(req_id), '|', TRIM(framework), '|', TRIM(title), '|',
      TRIM(severity), '|', TRIM(owner_id), '|', TRIM(description)
    )
  FROM Requirements

  UNION ALL
  SELECT
    'ServiceCallsApi',
    CONCAT(
      TRIM(service_id), '|', TRIM(api_id), '|', TRIM(call_mode), '|',
      IF(is_critical, 'true', 'false'), '|', TRIM(migration_status), '|',
      CAST(rps_estimate AS STRING)
    )
  FROM ServiceCallsApi

  UNION ALL
  SELECT
    'ServiceDependsOnService',
    CONCAT(
      TRIM(from_service_id), '|', TRIM(to_service_id), '|', TRIM(dependency_type), '|',
      IF(is_sync, 'true', 'false'), '|', TRIM(criticality)
    )
  FROM ServiceDependsOnService

  UNION ALL
  SELECT
    'AdrGovernsApi',
    CONCAT(
      TRIM(adr_id), '|', TRIM(api_id), '|', TRIM(governance_type), '|',
      FORMAT_DATE('%Y-%m-%d', effective_date)
    )
  FROM AdrGovernsApi

  UNION ALL
  SELECT
    'AdrSupersedesAdr',
    CONCAT(
      TRIM(adr_id), '|', TRIM(supersedes_adr_id), '|',
      FORMAT_DATE('%Y-%m-%d', superseded_date)
    )
  FROM AdrSupersedesAdr

  UNION ALL
  SELECT
    'ReqAppliesToApi',
    CONCAT(TRIM(req_id), '|', TRIM(api_id), '|', TRIM(compliance_status))
  FROM ReqAppliesToApi

  UNION ALL
  SELECT
    'ReqAppliesToService',
    CONCAT(TRIM(req_id), '|', TRIM(service_id), '|', TRIM(compliance_status))
  FROM ReqAppliesToService
)
SELECT
  table_name,
  COUNT(*) AS row_count,
  TO_HEX(SHA256(CONCAT(STRING_AGG(canonical_line, '\n' ORDER BY canonical_line ASC), '\n'))) AS canonical_sha256,
  BIT_XOR(FARM_FINGERPRINT(canonical_line)) AS farm_fingerprint_xor
FROM table_lines
GROUP BY table_name
ORDER BY table_name ASC;
