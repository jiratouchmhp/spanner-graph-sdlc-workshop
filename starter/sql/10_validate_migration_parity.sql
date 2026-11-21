-- =============================================================================
-- 10_validate_migration_parity.sql (STARTER — Lab 2)
-- Acme Commerce Platform — 4-Tier Neo4j-vs-Spanner & Path A/B Parity Validation
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
-- Execution Mode: READ-ONLY transaction
--
-- Antigravity Skills for Lab 2:
--   - Primary: .agents/skills/sdlc-graphrag-workshop-guide/SKILL.md
--   - Primary: .agents/skills/spanner-enterprise-platform-ops/SKILL.md
--
-- Locked Acme Commerce Parity Targets (WORKSHOP_SPEC.json):
--   - Tier 1 Global Counts:
--       * 68 structured Path A nodes (8 Owners, 12 Services, 24 Apis, 10 Adrs, 14 Requirements)
--       * 174 explicit M:N relationship table rows
--       * 60 dual-projected N:1 folded FK edges (234 total logical graph edges)
--       * 45 distinct unstructured Path B Markdown source files
--   - Tier 2 Per-Label & Per-Edge-Type Distribution over `SdlcGraph`
--   - Tier 3 Topological Traps & Hero Invariants:
--       * High-fanin identity hub (`in_degree = 10`, auth token callers = 10)
--       * Intentional `svc-orders <-> svc-notifications` async cycle (`= 2` edges)
--       * Async analytics consumer (`svc-analytics` targeting `svc-orders`, `svc-ledger`, `svc-inventory` = 3)
--       * Legacy deprecated `svc-billing -> api-payments-charge-v1` risky call (`= 1`)
--       * Hero API `api-payments-charge-v2`: 2 direct callers, 5 governing ADRs, 5 applicable REQs
--       * Unstructured chunk embedding completeness (`embedding IS NULL` count = 0)
--   - Tier 4 Deterministic Canonical Row-Content Hash (`SHA256` + `BIT_XOR(FARM_FINGERPRINT(...))`)
--       matching `solution/sql/fixtures/10_row_hash_expected.json`.
--
-- Verify when done:
--   make TRACK=starter verify-lab LAB=2
-- =============================================================================

-- =============================================================================
-- TIER 1: Global Path A + Path B Parity Scorecard (PARTIAL — COMPLETE PATH B)
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
    + (SELECT COUNT(*) FROM Requirements WHERE owner_id IS NOT NULL) AS dual_projected_edges
    -- TODO(Lab 2): Add Path B unstructured coverage count selecting
    --   `COUNT(DISTINCT source_path)` from the Path B chunks table (`expected = 45`)
    --   and include `'path_b_distinct_markdown_docs'` in the scorecard below!
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
)
ORDER BY metric ASC;

-- =============================================================================
-- TIER 2: Per-Label & Per-Edge-Type Distribution (SdlcGraph)
-- =============================================================================
-- TODO(Lab 2): Write `GRAPH_TABLE(SdlcGraph ...)` count assertions comparing
--   actual vs expected counts for:
--     - 5 Node labels: Owner (8), Service (12), Api (24), Adr (10), Requirement (14)
--     - 6 Explicit M:N edges: CALLS_API (48), DEPENDS_ON (28), GOVERNS (36),
--       SUPERSEDES (3), APPLIES_TO_API (33), APPLIES_TO_SERVICE (26)
--     - 4 Dual-projected edges: EXPOSES (24), OWNS_SERVICE (12), OWNS_ADR (10), OWNS_REQ (14)

-- =============================================================================
-- TIER 3: Topological Traps, Hub Degrees & Hero Scenario Invariants
-- =============================================================================
-- TODO(Lab 2): Author the Tier 3 invariant query asserting PASS/FAIL for:
--   1. High-fanin identity hub in-degree in `ServiceDependsOnService` (`= 10`)
--      and callers of `api-auth-token-v2` in `ServiceCallsApi` (`= 10`)
--   2. Intentional async cycle edges `svc-orders <-> svc-notifications` (`= 2`)
--   3. Async analytics feed edges from `svc-analytics` to `svc-orders`,
--      `svc-ledger`, and `svc-inventory` (`= 3`)
--   4. Legacy deprecated consumer call `svc-billing -> api-payments-charge-v1`
--      filtering on deprecated API + at-risk migration status (`= 1`)
--   5. Hero API `api-payments-charge-v2`: 2 direct callers, 5 governing ADRs,
--      5 applicable requirements
--   6. Zero chunks missing embeddings (`WHERE embedding IS NULL` count = 0).

-- =============================================================================
-- TIER 4: Deterministic Per-Table Row Content Hash Parity
-- =============================================================================
-- TODO(Lab 2): Compute sorted canonical pipe-delimited `TO_HEX(SHA256(...))`
--   and order-independent `BIT_XOR(FARM_FINGERPRINT(...))` checksums per table
--   to verify byte-for-byte parity with `solution/sql/fixtures/10_row_hash_expected.json`.
