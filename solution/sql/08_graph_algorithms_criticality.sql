-- =============================================================================
-- 08_graph_algorithms_criticality.sql
-- Acme Commerce Platform — Lab 4 Spanner Graph Algorithms (Data Boost)
--   - PageRank (CALL PER () dynamic subgraph excluding async DATA_FEED edges)
--   - WeaklyConnectedComponents (full service dependency topology)
--   - ShortestPath (weighted distance from every service to core svc-payments)
--
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
-- Compute Engine: Spanner Data Boost ($1.2285 / 1,000 SPU-hr in Bangkok
--                 regional-asia-southeast3; $1.33848 in Singapore asia-southeast1)
-- Required IAM:   roles/spanner.graphIntelligenceUser
--
-- Structural Invariants Enforced (INV-06 & Graph Algorithm Compatibility):
--   1. Spanner Graph Algorithms require every input edge table to be
--      INTERLEAVE IN PARENT <SourceNode> AND declare an enforced or NOT ENFORCED
--      FOREIGN KEY referencing <DestNode>.
--   2. Because Spanner forbids combining INTERLEAVE IN PARENT Services ON DELETE
--      CASCADE with a self-referencing cascading FK to Services on the same table
--      (INV-06), ServiceDependsOnAlgo uses INTERLEAVE IN PARENT Services ON DELETE
--      CASCADE + FOREIGN KEY (to_service_id) REFERENCES Services (service_id)
--      NOT ENFORCED!
--   3. SdlcAlgoGraph is strictly table-backed (zero SQL views).
--   4. Graph Algorithm queries use literal values ONLY (no @param bindings).
--   5. max_idle_time => '5m' minimizes warm SPU idle billing (default is 30m).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Algorithm-Compatible Interleaved Edge Table (ServiceDependsOnAlgo)
--    Leading PK column MUST match parent PK name (`service_id` -> `Services`).
-- -----------------------------------------------------------------------------
CREATE TABLE ServiceDependsOnAlgo (
  service_id STRING(64) NOT NULL,
  to_service_id STRING(64) NOT NULL,
  dependency_type STRING(32) NOT NULL,
  is_sync BOOL NOT NULL,
  criticality STRING(32) NOT NULL,
  edge_weight FLOAT64 NOT NULL,
  CONSTRAINT fk_algo_to_service
    FOREIGN KEY (to_service_id) REFERENCES Services (service_id) NOT ENFORCED,
) PRIMARY KEY (service_id, to_service_id),
  INTERLEAVE IN PARENT Services ON DELETE CASCADE;

-- -----------------------------------------------------------------------------
-- 2. Schema-Free Algorithm Output Persistence Table (ServiceAlgoProperty)
--    Tagged by algo_run_id so iterative experiments persist without ALTER TABLE.
-- -----------------------------------------------------------------------------
CREATE TABLE ServiceAlgoProperty (
  service_id STRING(64) NOT NULL,
  algo_run_id STRING(200) NOT NULL,
  int_val INT64,
  float_val FLOAT64,
) PRIMARY KEY (service_id, algo_run_id),
  INTERLEAVE IN PARENT Services ON DELETE CASCADE;

-- -----------------------------------------------------------------------------
-- 3. Table-Backed Algorithm Property Graph (SdlcAlgoGraph)
--    Zero view-backed elements; unique identifying labels (`Service`, `DEPENDS_ON_ALGO`).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE PROPERTY GRAPH SdlcAlgoGraph
  NODE TABLES (
    Services
      KEY (service_id)
      LABEL Service
      PROPERTIES (service_id, service_name, tier, criticality, owner_id, domain)
  )
  EDGE TABLES (
    ServiceDependsOnAlgo
      KEY (service_id, to_service_id)
      SOURCE KEY (service_id) REFERENCES Services (service_id)
      DESTINATION KEY (to_service_id) REFERENCES Services (service_id)
      LABEL DEPENDS_ON_ALGO
      PROPERTIES (service_id, to_service_id, dependency_type, is_sync, criticality, edge_weight)
  );

-- -----------------------------------------------------------------------------
-- 4. Populate ServiceDependsOnAlgo From Canonical ServiceDependsOnService (28 rows)
--    Assigns positive edge weights: SYNC_RPC = 1.0 (tightest coupling),
--    ASYNC_EVENT = 2.0, DATA_FEED = 5.0 (loosest analytical coupling).
-- -----------------------------------------------------------------------------
INSERT OR UPDATE INTO ServiceDependsOnAlgo (
  service_id,
  to_service_id,
  dependency_type,
  is_sync,
  criticality,
  edge_weight
)
SELECT
  from_service_id AS service_id,
  to_service_id,
  dependency_type,
  is_sync,
  criticality,
  CASE dependency_type
    WHEN 'SYNC_RPC' THEN 1.0
    WHEN 'ASYNC_EVENT' THEN 2.0
    ELSE 5.0
  END AS edge_weight
FROM ServiceDependsOnService;

-- -----------------------------------------------------------------------------
-- 5. Algorithm 1 — PageRank via Dynamic Subgraph (CALL PER ())
--    Excludes async DATA_FEED edges to rank true OLTP blast-radius criticality.
--    Expected #1 hub: svc-auth (in-degree = 10), followed by Tier-0 payment/ledger cores.
--    Note: Each MATCH has exactly ONE element; edge MATCH omits endpoint labels/vars;
--    CALL PER () omits node_labels/edge_labels; RETURN emits bare outputs + literal only.
-- -----------------------------------------------------------------------------
EXPORT DATA OPTIONS (
  format = 'CLOUD_SPANNER',
  table = 'ServiceAlgoProperty',
  write_mode = 'upsert_ignore_all'
) AS
GRAPH SdlcAlgoGraph
MATCH (n:Service)
RETURN n
FULL UNION ALL
MATCH -[e:DEPENDS_ON_ALGO WHERE e.dependency_type <> 'DATA_FEED']->
RETURN e
NEXT
CALL PER () PageRank(
  damping_factor => 0.85,
  max_iterations => 20,
  approx_precision => 1e-4,
  max_idle_time => '5m'
) YIELD node, score
RETURN
  node.service_id AS service_id,
  'pagerank_oltp_v1' AS algo_run_id,
  score AS float_val;

-- -----------------------------------------------------------------------------
-- 6. Algorithm 2 — WeaklyConnectedComponents (Full Dependency Topology)
--    Verifies whether all 12 Acme Commerce microservices form a single connected
--    operational topology or isolate into disconnected blast-radius islands.
-- -----------------------------------------------------------------------------
EXPORT DATA OPTIONS (
  format = 'CLOUD_SPANNER',
  table = 'ServiceAlgoProperty',
  write_mode = 'upsert_ignore_all'
) AS
GRAPH SdlcAlgoGraph
CALL WeaklyConnectedComponents(
  node_labels => ['Service'],
  edge_labels => ['DEPENDS_ON_ALGO'],
  max_idle_time => '5m'
) YIELD node, cluster
RETURN
  node.service_id AS service_id,
  'wcc_full_v1' AS algo_run_id,
  cluster AS int_val;

-- -----------------------------------------------------------------------------
-- 7. Algorithm 3 — Weighted ShortestPath to Tier-0 Core (svc-payments)
--    Computes minimum weighted coupling cost and hop distance (PATH_LENGTH(path))
--    from every service to svc-payments using edge_weight. Single target guarantees
--    at most one row per source_node.service_id in ServiceAlgoProperty.
-- -----------------------------------------------------------------------------
EXPORT DATA OPTIONS (
  format = 'CLOUD_SPANNER',
  table = 'ServiceAlgoProperty',
  write_mode = 'upsert_ignore_all'
) AS
GRAPH SdlcAlgoGraph
CALL ShortestPath(
  node_labels => ['Service'],
  edge_labels => ['DEPENDS_ON_ALGO'],
  edge_weight_property => 'edge_weight',
  source_nodes => ARRAY { MATCH (s:Service) RETURN s },
  target_nodes => ARRAY { MATCH (t:Service {service_id: 'svc-payments'}) RETURN t },
  max_idle_time => '5m'
) YIELD source_node, target_node, path, cost
RETURN
  source_node.service_id AS service_id,
  'shortest_path_to_svc_payments_v1' AS algo_run_id,
  PATH_LENGTH(path) AS int_val,
  cost AS float_val;

-- -----------------------------------------------------------------------------
-- 8. Inspect Persisted Criticality Scores & Monitor Data Boost Execution Status
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
