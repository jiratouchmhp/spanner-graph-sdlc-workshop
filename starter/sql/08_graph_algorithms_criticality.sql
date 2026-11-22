-- =============================================================================
-- 08_graph_algorithms_criticality.sql (STARTER — Lab 4)
-- Acme Commerce Platform — Lab 4 Spanner Graph Algorithms (Data Boost)
--   - Algorithm-compatible interleaved edge projection (avoiding INV-06!)
--   - Table-backed algorithm Property Graph (zero views)
--   - Dynamic subgraph criticality ranking via Data Boost (`max_idle_time => '5m'`)
--
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
-- Compute Engine: Spanner Data Boost ($1.2285 / 1,000 SPU-hr in Bangkok
--                 regional-asia-southeast3; $1.33848 in Singapore asia-southeast1)
-- Required IAM:   roles/spanner.graphIntelligenceUser
--
-- Antigravity Skills for Lab 4:
--   - Primary: .agents/skills/spanner-graph-modeling-and-gql/SKILL.md
--   - Support: .agents/skills/spanner-schema-and-data-modeling/SKILL.md
--
-- Structural Rules for Spanner Graph Algorithms:
--   1. Every input edge table MUST be interleaved in its source node table AND
--      declare a destination FOREIGN KEY (enforced or non-enforced).
--   2. Because Spanner forbids combining parent interleaving cascade with a
--      self-referencing cascading FK to the same table (INV-06), use parent
--      interleaving cascade + a non-enforced destination FK for self-referential
--      service edges!
--   3. The algorithm Property Graph MUST be strictly table-backed (zero views).
--   4. Graph Algorithm calls require literal arguments ONLY (zero `@param` bindings).
--   5. Always set `max_idle_time => '5m'` to minimize warm SPU idle billing.
--
-- Verify when done:
--   make TRACK=starter verify-lab LAB=4
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Algorithm-Compatible Interleaved Edge Table
-- -----------------------------------------------------------------------------
-- TODO(Lab 4): Create the algorithm edge table `ServiceDependsOnAlgo` interleaved
--   in parent `Services` (`ON DELETE CASCADE`) with a non-enforced destination FK
--   referencing `Services (service_id)` so it obeys INV-06 while satisfying
--   Spanner Graph Algorithms' interleaving + FK prerequisite!
--   Required columns:
--     - service_id      STRING(64) NOT NULL   (leading PK MUST match `Services.service_id`)
--     - to_service_id   STRING(64) NOT NULL
--     - dependency_type STRING(32) NOT NULL
--     - is_sync         BOOL NOT NULL
--     - criticality     STRING(32) NOT NULL
--     - edge_weight     FLOAT64 NOT NULL
--   Primary key: `(service_id, to_service_id)`.

-- -----------------------------------------------------------------------------
-- 2. Schema-Free Algorithm Output Persistence Table — PROVIDED COMPLETE
--    Tagged by algo_run_id so iterative experiments persist without ALTER TABLE.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ServiceAlgoProperty (
  service_id STRING(64) NOT NULL,
  algo_run_id STRING(200) NOT NULL,
  target_service_id STRING(64),
  int_val INT64,
  float_val FLOAT64,
) PRIMARY KEY (service_id, algo_run_id),
  INTERLEAVE IN PARENT Services ON DELETE CASCADE;

-- -----------------------------------------------------------------------------
-- 3. Table-Backed Algorithm Property Graph
-- -----------------------------------------------------------------------------
-- TODO(Lab 4): Define the strictly table-backed Property Graph `SdlcAlgoGraph`
--   (zero views!) containing:
--     - NODE TABLES: `Services` (`KEY (service_id)`, `LABEL Service`)
--     - EDGE TABLES: the algorithm edge table from Step 1
--       (`KEY (service_id, to_service_id)`,
--        `SOURCE KEY (service_id) REFERENCES Services (service_id)`,
--        `DESTINATION KEY (to_service_id) REFERENCES Services (service_id)`,
--        `LABEL DEPENDS_ON_ALGO`).

-- -----------------------------------------------------------------------------
-- 4. Populate Algorithm Edge Table From Canonical ServiceDependsOnService (28 rows)
-- -----------------------------------------------------------------------------
-- TODO(Lab 4): Copy all 28 rows from `ServiceDependsOnService` into your
--   algorithm edge table using `INSERT OR UPDATE`, mapping:
--     - `from_service_id AS service_id`
--     - `edge_weight`: `SYNC_RPC -> 1.0`, `ASYNC_EVENT -> 2.0`, `DATA_FEED -> 5.0`.

-- -----------------------------------------------------------------------------
-- 5. Algorithm 1 — Dynamic Subgraph Centrality via Data Boost (`CALL PER ()`)
-- -----------------------------------------------------------------------------
-- TODO(Lab 4): Write a Data Boost export query writing to `ServiceAlgoProperty`
--   (`format = 'CLOUD_SPANNER'`, `write_mode = 'upsert_ignore_all'`) that:
--     1. Queries the table-backed algorithm graph from Step 3.
--     2. Builds a dynamic subgraph with `MATCH (n:Service) RETURN n FULL UNION ALL`
--        `MATCH -[e:DEPENDS_ON_ALGO WHERE e.dependency_type <> 'DATA_FEED']-> RETURN e`
--        (excluding async analytics feeds to rank true OLTP blast-radius hubs!).
--     3. Pipes via `NEXT` into `CALL PER ()` invoking Spanner's PageRank algorithm
--        with literal options: `damping_factor => 0.85`, `max_iterations => 20`,
--        `approx_precision => 1e-4`, `max_idle_time => '5m'` (`YIELD node, score`).
--     4. Returns `node.service_id AS service_id`, `'pagerank_oltp_v1' AS algo_run_id`,
--        and `score AS float_val`.
--     (Expected #1 ranked hub: `svc-auth` with in-degree = 10!)
--
-- Track B Stretch Goal: Also add `WeaklyConnectedComponents` (`'wcc_full_v1'`)
--   and weighted `ShortestPath` to `'svc-payments'` (`'shortest_path_to_svc_payments_v1'`).
--   See `solution/sql/08_graph_algorithms_criticality.sql` for reference!

-- -----------------------------------------------------------------------------
-- 6. Inspect Persisted Criticality Scores & Monitor Data Boost Execution Status
--    PROVIDED COMPLETE — run after your Data Boost export job finishes.
-- -----------------------------------------------------------------------------
SELECT
  s.service_id,
  s.service_name,
  s.tier,
  s.criticality,
  ROUND(pr.float_val, 6) AS pagerank_score,
  wcc.int_val AS wcc_cluster_id,
  sp.int_val AS hops_to_svc_payments,
  ROUND(sp.float_val, 2) AS weighted_cost_to_svc_payments
FROM Services AS s
LEFT JOIN ServiceAlgoProperty AS pr
  ON s.service_id = pr.service_id AND pr.algo_run_id = 'pagerank_oltp_v1'
LEFT JOIN ServiceAlgoProperty AS wcc
  ON s.service_id = wcc.service_id AND wcc.algo_run_id = 'wcc_full_v1'
LEFT JOIN ServiceAlgoProperty AS sp
  ON s.service_id = sp.service_id AND sp.algo_run_id = 'shortest_path_to_svc_payments_v1'
ORDER BY pagerank_score DESC, s.service_id ASC;

-- Check Data Boost algorithm execution status & errors (30-day retention)
SELECT
  query_id,
  status,
  progress,
  start_timestamp,
  last_update_timestamp,
  error_message
FROM SPANNER_SYS.GRAPH_OPERATION_EXECUTION_STATUS
ORDER BY start_timestamp DESC
LIMIT 10;
