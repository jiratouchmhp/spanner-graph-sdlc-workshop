-- =============================================================================
-- 11_observability_and_studio_viz.sql (STARTER — Lab 8)
-- Acme Commerce Platform — Lab 8 Spanner Studio Visualization & Observability Pack
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
-- Execution Mode: READ-ONLY transaction (SPANNER_SYS cannot run in RW txns!)
--
-- Antigravity Skills for Lab 8:
--   - Primary: .agents/skills/spanner-performance-observability-and-sizing/SKILL.md
--   - Primary: .agents/skills/spanner-graph-modeling-and-gql/SKILL.md
--   - Primary: .agents/skills/spanner-enterprise-platform-ops/SKILL.md
--
-- Verify when done:
--   make TRACK=starter verify-lab LAB=8
-- =============================================================================

-- =============================================================================
-- Query 1: Spanner Studio Interactive Blast-Radius Path Visualization
-- =============================================================================
-- TODO(Lab 8): Write a cycle-safe Spanner Graph query over `SdlcGraph` that
--   activates Spanner Studio's interactive Graph tab:
--     1. Bind full path `p` using edge-unique `MATCH p = TRAIL ...` from
--        `(upstream:Service)-[:DEPENDS_ON]->{0,3}(direct:Service)-[:CALLS_API]->(target:Api {api_id: 'api-payments-charge-v2'})`.
--     2. Return the safely JSON-serialized path `p` as `blast_radius_path`
--        (required for Studio Graph rendering and safe around hidden TOKENLIST columns!)
--        and `PATH_LENGTH(p) AS hops`.
--     3. Order by `hops ASC` and cap at `LIMIT 50` to stay well under Studio's
--        10 MB visualization payload ceiling.

-- =============================================================================
-- Query 2: Query Insights — Top CPU Consumers & Misrouted RW Queries
-- =============================================================================
-- TODO(Lab 8): Query the hourly top-query statistics system view over the last
--   6 hours (`INTERVAL_END >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR)`):
--     - Compute `ROUND(EXECUTION_COUNT * AVG_CPU_SECONDS, 4) AS total_cpu_seconds`
--     - Include `REQUEST_TAG`, `TEXT_FINGERPRINT`, `QUERY_TYPE`, `EXECUTION_COUNT`,
--       `AVG_LATENCY_SECONDS`, `AVG_ROWS_SCANNED`, `AVG_REMOTE_SERVER_CALLS`,
--       `AVG_DISK_IO_COST`, and `RUN_IN_RW_TRANSACTION_EXECUTION_COUNT` (to catch
--       FTS/Graph reads mistakenly sent inside read-write transactions!)
--     - Order by `total_cpu_seconds DESC LIMIT 20`.

-- =============================================================================
-- Query 3: Lock Insights — Contention Hotspots & P99 Wait (Micros -> Seconds!)
-- =============================================================================
-- TODO(Lab 8): Query the hourly top-lock statistics system view over the last
--   6 hours to pinpoint hot key ranges:
--     - CRITICAL UNIT GOTCHA: `LOCK_WAIT_SECONDS` is in seconds, whereas
--       `LOCK_WAIT_DISTRIBUTION` buckets are stored in MICROSECONDS!
--     - Guard against empty buckets (`LOCK_WAIT_DISTRIBUTION[SAFE_OFFSET(0)].COUNT > 0`)
--       and divide `SPANNER_SYS.DISTRIBUTION_PERCENTILE(LOCK_WAIT_DISTRIBUTION[SAFE_OFFSET(0)], 99.0)`
--       by one million (`1e6`) to report `p99_lock_wait_secs` accurately!
--     - Include `CAST(ROW_RANGE_START_KEY AS STRING)`, `LOCK_WAIT_SECONDS`,
--       and `SAMPLE_LOCK_REQUESTS`; order by `LOCK_WAIT_SECONDS DESC LIMIT 20`.

-- =============================================================================
-- Query 4: Real-Time Active Queries Inspection & Safe Cancellation Template
--   PROVIDED COMPLETE — omits CLIENT_IP_ADDRESS to avoid lookup overhead.
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
-- =============================================================================

-- Query 5A: User-Defined Pre-Split Points
-- TODO(Lab 8): Query the user split points system table to audit manual
--   pre-splits (`TABLE_NAME`, `INDEX_NAME`, `INITIATOR`, `SPLIT_KEY`, `EXPIRE_TIME`)
--   ordered by `TABLE_NAME ASC, INDEX_NAME ASC`.

-- Query 5B: Tiered Storage Locality Group Monitoring (SSD vs HDD) — PROVIDED
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

-- Query 5C: Row Deletion Policy (TTL) Sweeper Health — PROVIDED
SELECT
  TABLE_NAME,
  PROCESSED_WATERMARK,
  UNDELETABLE_ROWS,
  MIN_UNDELETABLE_TIMESTAMP
FROM SPANNER_SYS.ROW_DELETION_POLICIES
ORDER BY UNDELETABLE_ROWS DESC, PROCESSED_WATERMARK ASC;
