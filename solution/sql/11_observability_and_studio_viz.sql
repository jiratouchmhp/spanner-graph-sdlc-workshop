-- =============================================================================
-- 11_observability_and_studio_viz.sql
-- Acme Commerce Platform — Lab 8 Spanner Studio Visualization & Observability Pack
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
-- Execution Mode: READ-ONLY transaction (SPANNER_SYS cannot run in RW txns!)
--
-- Covers:
--   1. Spanner Studio Interactive Graph Visualization (RETURN SAFE_TO_JSON(p), < 10 MB)
--   2. Top-CPU Query Insights (SPANNER_SYS.QUERY_STATS_TOP_HOUR)
--   3. Lock Contention & Microsecond-to-Second P99 Wait (SPANNER_SYS.LOCK_STATS_TOP_HOUR)
--   4. Live Active Queries & Cancellation Template (SPANNER_SYS.OLDEST_ACTIVE_QUERIES)
--   5. Pre-Splitting, Locality Group SSD/HDD Tiering & TTL Sweeper Monitoring
-- =============================================================================

-- =============================================================================
-- Query 1A: Spanner Studio Interactive Blast-Radius Path Visualization
--   Spanner Studio activates the interactive Graph tab ONLY when full graph
--   elements or paths are returned via SAFE_TO_JSON(...). Returning a bound
--   path `p` preserves all intermediate nodes and edges cleanly under the
--   10 MB Spanner Studio payload limit.
-- =============================================================================
GRAPH SdlcGraph
MATCH p = TRAIL (upstream:Service)-[:DEPENDS_ON]->{0,3}(direct:Service)-[:CALLS_API]->(target:Api {api_id: 'api-payments-charge-v2'})
RETURN
  SAFE_TO_JSON(p) AS blast_radius_path,
  PATH_LENGTH(p) AS hops
ORDER BY hops ASC
LIMIT 50;

-- =============================================================================
-- Query 1B: Spanner Studio Governance + Legacy Risk Subgraph Visualization
--   Visualizes governing ADRs, requirements, and callers converging on the
--   Hero v2 and deprecated v1 payment charge endpoints.
-- =============================================================================
GRAPH SdlcGraph
MATCH p = (adr:Adr)-[:GOVERNS]->(api:Api)<-[:CALLS_API]-(caller:Service)
WHERE api.api_id IN ('api-payments-charge-v2', 'api-payments-charge-v1')
RETURN
  SAFE_TO_JSON(p) AS governance_impact_path,
  api.api_id AS api_id,
  caller.service_id AS caller_service_id,
  adr.adr_id AS adr_id
ORDER BY api_id ASC, caller_service_id ASC, adr_id ASC
LIMIT 50;

-- =============================================================================
-- Query 2: Query Insights — Top CPU Consumers & Misrouted RW Queries
--   Ranks queries by total CPU (`EXECUTION_COUNT * AVG_CPU_SECONDS`) and flags
--   read-only FTS/Graph queries accidentally executed inside RW transactions
--   (`RUN_IN_RW_TRANSACTION_EXECUTION_COUNT > 0`) as well as HDD tier I/O cost.
-- =============================================================================
SELECT
  INTERVAL_END,
  REQUEST_TAG,
  TEXT_FINGERPRINT,
  QUERY_TYPE,
  EXECUTION_COUNT,
  ROUND(EXECUTION_COUNT * AVG_CPU_SECONDS, 4) AS total_cpu_seconds,
  ROUND(AVG_CPU_SECONDS, 5) AS avg_cpu_seconds,
  ROUND(AVG_LATENCY_SECONDS, 5) AS avg_latency_seconds,
  ROUND(AVG_ROWS_SCANNED, 1) AS avg_rows_scanned,
  ROUND(AVG_REMOTE_SERVER_CALLS, 2) AS avg_remote_server_calls,
  ROUND(AVG_DISK_IO_COST, 4) AS avg_hdd_disk_io_cost,
  RUN_IN_RW_TRANSACTION_EXECUTION_COUNT,
  SUBSTR(TEXT, 1, 200) AS query_preview
FROM SPANNER_SYS.QUERY_STATS_TOP_HOUR
WHERE INTERVAL_END >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR)
ORDER BY total_cpu_seconds DESC
LIMIT 20;

-- =============================================================================
-- Query 3: Lock Insights — Contention Hotspots & P99 Wait (Micros -> Seconds!)
--   CRITICAL GOTCHA: `LOCK_WAIT_SECONDS` is in seconds, whereas
--   `LOCK_WAIT_DISTRIBUTION` buckets are stored in MICROSECONDS — ALWAYS divide
--   `SPANNER_SYS.DISTRIBUTION_PERCENTILE(...)` by `1000000.0` to obtain seconds!
-- =============================================================================
SELECT
  INTERVAL_END,
  CAST(ROW_RANGE_START_KEY AS STRING) AS row_range_start,
  ROUND(LOCK_WAIT_SECONDS, 4) AS total_lock_wait_secs,
  ROUND(
    IF(
      LOCK_WAIT_DISTRIBUTION[SAFE_OFFSET(0)].COUNT > 0,
      SPANNER_SYS.DISTRIBUTION_PERCENTILE(
        LOCK_WAIT_DISTRIBUTION[SAFE_OFFSET(0)], 99.0
      ) / 1000000.0,
      NULL
    ),
    6
  ) AS p99_lock_wait_secs,
  SAMPLE_LOCK_REQUESTS
FROM SPANNER_SYS.LOCK_STATS_TOP_HOUR
WHERE INTERVAL_END >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR)
ORDER BY LOCK_WAIT_SECONDS DESC
LIMIT 20;

-- =============================================================================
-- Query 4: Real-Time Active Queries Inspection & Safe Cancellation Template
--   Note: Intentionally omits CLIENT_IP_ADDRESS (selecting it adds latency).
--   Always confirm the query_id and operator impact before calling cancel_query.
-- =============================================================================
SELECT
  ACTIVE_COUNT,
  OLDEST_START_TIME,
  COUNT_OLDER_THAN_1S,
  COUNT_OLDER_THAN_10S,
  COUNT_OLDER_THAN_100S
FROM SPANNER_SYS.ACTIVE_QUERIES_SUMMARY;

SELECT
  QUERY_ID,
  START_TIME,
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), START_TIME, MILLISECOND) AS elapsed_ms,
  PRIORITY,
  TRANSACTION_TYPE,
  TEXT_FINGERPRINT,
  SESSION_ID,
  SUBSTR(TEXT, 1, 200) AS query_preview
FROM SPANNER_SYS.OLDEST_ACTIVE_QUERIES
ORDER BY START_TIME ASC
LIMIT 20;

-- To cancel a confirmed runaway query (best-effort; requires spanner.sessions.delete):
-- CALL cancel_query('<query_id_from_OLDEST_ACTIVE_QUERIES>');

-- =============================================================================
-- Query 5: Pre-Splitting, Tiered Storage (SSD vs HDD) & TTL Sweeper Health
--   - SPANNER_SYS.USER_SPLIT_POINTS: Verify manual pre-split points for scale loads.
--   - SPANNER_SYS.TABLE_SIZES_STATS_1HOUR: Track SSD vs HDD Locality Group spill.
--   - SPANNER_SYS.ROW_DELETION_POLICIES: Alert if UNDELETABLE_ROWS > 0 or lag > 72h.
-- =============================================================================
SELECT
  TABLE_NAME,
  INDEX_NAME,
  INITIATOR,
  SPLIT_KEY,
  EXPIRE_TIME
FROM SPANNER_SYS.USER_SPLIT_POINTS
ORDER BY TABLE_NAME ASC, INDEX_NAME ASC;

SELECT
  TABLE_NAME,
  USED_BYTES,
  USED_SSD_BYTES,
  USED_HDD_BYTES,
  ROUND(SAFE_DIVIDE(USED_HDD_BYTES, NULLIF(USED_BYTES, 0)) * 100.0, 2) AS hdd_pct
FROM SPANNER_SYS.TABLE_SIZES_STATS_1HOUR
WHERE INTERVAL_END = (
  SELECT MAX(INTERVAL_END) FROM SPANNER_SYS.TABLE_SIZES_STATS_1HOUR
)
ORDER BY USED_BYTES DESC;

SELECT
  TABLE_NAME,
  PROCESSED_WATERMARK,
  UNDELETABLE_ROWS,
  MIN_UNDELETABLE_TIMESTAMP
FROM SPANNER_SYS.ROW_DELETION_POLICIES
ORDER BY UNDELETABLE_ROWS DESC, PROCESSED_WATERMARK ASC;
