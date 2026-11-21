# Query Insights, Lock & Transaction Introspection & CPU Triage

Authoritative reference for Cloud Spanner observability: IAM vs. FGAC permissions, clock-aligned retention windows, `SPANNER_SYS` schemas (`QUERY_STATS`, `LOCK_STATS`, `TXN_STATS`, `READ_STATS`, `ACTIVE_QUERIES`), cross-dialect P99 SQL, lock conflict mitigation, request/transaction tagging, call-stack autotagging, and high-CPU incident triage.

---

## 1. Access Model, Pricing & Retention Windows

- **Included Across All Editions**: Query Insights, Lock Insights, Transaction Insights, Active Queries, and all `SPANNER_SYS` introspection tables carry **no additional cost** across Standard, Enterprise, and Enterprise Plus in both regional and multi-region topologies.
- **SQL-Only Access**: `SPANNER_SYS` tables are readable **only via SQL** (Spanner Studio, `gcloud spanner databases execute-sql`, `executeSql`, `executeStreamingSql`). Single-read API methods (strong/stale table read, secondary index read) **do not support** `SPANNER_SYS`.
- **IAM vs. FGAC Permission Gotcha**:
  - **IAM Principals**: Require `roles/spanner.viewer` + `roles/spanner.databaseReader` (`spanner.databases.beginReadOnlyTransaction`, `spanner.databases.select`, `spanner.sessions.create`). Canceling active queries also requires `spanner.sessions.delete`.
  - **FGAC Principals**: Require `roles/spanner.viewer` + FGAC system role `spanner_sys_reader` (or a member role) chosen on the database overview page.
  - **CRITICAL GOTCHA**: If an FGAC principal holds **any** IAM database-level permission (such as `spanner.databases.select`), Cloud Console classifies them as an IAM user and **prevents selecting `spanner_sys_reader`**.

### Standard Clock-Aligned Aggregation Windows

Applies uniformly to `QUERY_STATS_*`, `LOCK_STATS_*`, `TXN_STATS_*`, and `READ_STATS_*`:

| Table Suffix | Clock-Aligned Window | Example Latest Bucket at 11:59:30 | Minimum Retention |
| :--- | :--- | :--- | :--- |
| `_MINUTE` | Non-overlapping 1-min (ends on minute) | 11:58:00 – 11:58:59 | **6 hours** |
| `_10MINUTE` | Non-overlapping 10-min (ends :00, :10, :20…) | 11:40:00 – 11:49:59 | **4 days** |
| `_HOUR` | Non-overlapping 1-hour (ends on hour) | 10:00:00 – 10:59:59 | **30 days** |

- **Collection Cannot Be Disabled**: `SPANNER_SYS` collection is always on; rows expire only on retention age or database deletion. Export periodically to BigQuery or Cloud Storage if retention beyond 30 days is required.

---

## 2. Query Insights Dashboard, Graph Divergence & Audit Logs

- **Dashboard Caps & Behavior**:
  - Displays Database selector, time-range filter, **Total CPU Utilization (All queries)** (CPU sec/min), **Total CPU Utilization (per Query or Request tag)**, and **TopN queries and tags** (capped at **Top 100** sorted by CPU; SQL text truncated at **~64 KB**).
  - Batch DML statements are flattened with `;` delimiters and consecutive identical statements are deduplicated before fingerprinting/truncation.
  - **Parameterization Mandatory**: Unparameterized queries containing distinct string literals explode fingerprint cardinality, preventing the TopN table from rendering cleanly and crowding out genuine top consumers.
- **Why the Two CPU Graphs Diverge**:
  1. **Distinct Data Sources**: *All queries* reads **Cloud Monitoring** (1-min cadence, **45-day retention**). *Per Query/Tag* reads `SPANNER_SYS.QUERY_STATS_TOP_*` (**30-day retention**), falling back to 10-min buckets (> 6h) and 1-hour buckets (> 4d), which smooths short spikes.
  2. **Window Alignment**: Beyond 6 hours, an event at `10:01` lands in the `10:10` `_10MINUTE` bucket timestamp.
- **Caller Attribution via Audit Logs**: Join `TEXT_FINGERPRINT` from `QUERY_STATS_TOP_*` (or Query Insights) to `query_fingerprint` in Cloud Spanner Data Access Audit Logs to identify the exact calling principal.

---

## 3. Real-Time Active Queries & Safe Termination

- **Console View**: Query Insights → **Active queries** lists the top **50 longest-running** queries (auto-refreshes every **60 seconds**; default columns: `Query ID`, `Query`, `Fingerprint`, `Start time`, `Query duration`, `Action`).
- **System Tables**:
  1. **`SPANNER_SYS.ACTIVE_QUERIES_SUMMARY`** (immune to per-row memory truncation):
     - Columns: `ACTIVE_COUNT` (`INT64`), `OLDEST_START_TIME` (`TIMESTAMP`, upper bound), `COUNT_OLDER_THAN_1S`, `COUNT_OLDER_THAN_10S`, `COUNT_OLDER_THAN_100S` (`INT64`).
     - **Bucket Semantics**: Threshold counters are **inclusive/overlapping** (a 12-second query increments both `COUNT_OLDER_THAN_1S` and `COUNT_OLDER_THAN_10S`).
  2. **`SPANNER_SYS.OLDEST_ACTIVE_QUERIES`** (sorted ascending by `START_TIME`; may truncate under high concurrency due to memory caps):
     - Columns: `START_TIME`, `TEXT_FINGERPRINT`, `TEXT`, `TEXT_TRUNCATED`, `SESSION_ID`, `QUERY_ID`, `CLIENT_IP_ADDRESS`, `API_CLIENT_HEADER`, `USER_AGENT_HEADER`, `SERVER_REGION` (root server region), `PRIORITY` (`PRIORITY_HIGH`/`MEDIUM`/`LOW`), `TRANSACTION_TYPE` (`READ_ONLY`, `READ_WRITE`, `NONE`).
- **Critical Gotchas & Exclusions**:
  - **Excluded Operations**: Change stream queries, DML already in the **Apply mutations** phase, queries mid-restart from transient errors, and queries executing on overloaded/unresponsive servers.
  - **Transaction Rules**: **Cannot** be queried inside a read-write transaction. In a read-only transaction, it **ignores the read timestamp** and reads live state; if a rare `ABORTED` error returns partial rows, discard and retry.
  - **`CLIENT_IP_ADDRESS` Overhead**: Selecting `CLIENT_IP_ADDRESS` adds **extra query latency** and can return redacted values (per Audit Log rules) or transient `<error>` strings (retry if needed). Omit it from automated health checks.
- **Canceling Runaway Queries** *(always confirm `{query_id}` and impact with the user first; best-effort under heavy load—re-query `OLDEST_ACTIVE_QUERIES` and retry if still active)*:
  ```sql
  -- GoogleSQL
  CALL cancel_query('{query_id}');

  -- PostgreSQL
  CALL spanner.cancel_query('{query_id}');
  ```
  Alternatively, delete the owning `SESSION_ID` or click **Terminate** in Cloud Console (requires `spanner.sessions.delete`).

---

## 4. `SPANNER_SYS` Introspection SQL Cookbook

### A. Query Statistics (`QUERY_STATS_TOP_*` & `QUERY_STATS_TOTAL_*`)

- **Key Columns**:
  - Identity: `INTERVAL_END`, `REQUEST_TAG`, `QUERY_TYPE` (`QUERY` vs. `PARTITIONED_QUERY` [PartitionQuery token or PDML]), `TEXT` (~64 KB cap), `TEXT_TRUNCATED`, `TEXT_FINGERPRINT` (`INT64`: hash of `REQUEST_TAG` if set, else `TEXT`; **always hash of `TEXT` for PDML**).
  - Throughput & Latency: `EXECUTION_COUNT`, `STATEMENT_COUNT` (equals `EXECUTION_COUNT` except Batch DML where it counts individual statements), `AVG_LATENCY_SECONDS` (server-side DB execution only; excludes result serialization and network transit), `AVG_CPU_SECONDS`, `AVG_ROWS`, `AVG_BYTES`, `AVG_ROWS_SCANNED` (excludes deleted versions), `AVG_ROWS_WRITTEN`, `AVG_BYTES_WRITTEN`, `RUN_IN_RW_TRANSACTION_EXECUTION_COUNT` (flags read-only queries mistakenly run inside RW transactions).
  - Failures: `ALL_FAILED_EXECUTION_COUNT`, `ALL_FAILED_AVG_LATENCY_SECONDS`, `CANCELLED_OR_DISCONNECTED_EXECUTION_COUNT`, `TIMED_OUT_EXECUTION_COUNT`. **PDML Caveat**: Failure counts are **not tracked** for PDML; successful PDML reports strictly `EXECUTION_COUNT = 1`, while failed/canceled/in-flight PDML reports `0`.
  - Deep Diagnostics (`TOP` only):
    - `AVG_MEMORY_PEAK_USAGE_BYTES`, `AVG_MEMORY_USAGE_PERCENTAGE` (required memory only; excludes optional buffers—detects OOM-prone plans early).
    - `AVG_QUERY_PLAN_CREATION_TIME_SECS` (high → parameterize queries).
    - `AVG_FILESYSTEM_DELAY_SECS` (high → add selective index or `STORING`/`INCLUDE`).
    - `AVG_REMOTE_SERVER_CALLS` (high fanout → add covering index or co-locate via interleaving).
    - `AVG_ROWS_SPOOLED` (temp disk spill → reorder joins or add sort-aligned index).
    - `AVG_DISK_IO_COST` (relative **HDD disk load** under Tiered Storage; SSD reads = `0`).
    - `AVG_COLUMNAR_READ_SHARE` (`0.0–1.0` share of bytes read from Columnar Engine).
    - `QUERY_OPTIMIZER_VERSIONS` (`ARRAY<INT64>`), `STATISTICS_PACKAGE_NAMES` (`ARRAY<STRING>`).
  - Distributions (`TOP` and `TOTAL`):
    - **GoogleSQL**: `LATENCY_DISTRIBUTION`, `CPU_SECONDS_DISTRIBUTION`, `ROW_SCANNED_DISTRIBUTION` — 1-element `ARRAY<STRUCT<COUNT, MEAN, SUM_OF_SQUARED_DEVIATION, NUM_FINITE_BUCKETS, GROWTH_FACTOR, SCALE, BUCKET_COUNTS>>`.
    - **PostgreSQL** (also available in GoogleSQL): `LATENCY_DISTRIBUTION_JSON_STRING`, `CPU_SECONDS_DISTRIBUTION_JSON_STRING`, `ROW_SCANNED_DISTRIBUTION_JSON_STRING` (`STRING`). PostgreSQL **does not support** the `ARRAY<STRUCT>` columns.

```sql
-- GoogleSQL: Cross-bucket P99 query latency over the past hour
SELECT
  IF(latency_dist IS NOT NULL,
    SPANNER_SYS.DISTRIBUTION_PERCENTILE(latency_dist, 99.0),
    NULL) AS p99_latency_secs
FROM (
  SELECT
    SPANNER_SYS.DISTRIBUTION_AGG(
      IF(T.LATENCY_DISTRIBUTION[SAFE_OFFSET(0)].COUNT > 0,
         T.LATENCY_DISTRIBUTION[SAFE_OFFSET(0)],
         NULL)
    ) AS latency_dist
  FROM SPANNER_SYS.QUERY_STATS_TOP_MINUTE AS T
  WHERE T.INTERVAL_END > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
    AND T.INTERVAL_END <= CURRENT_TIMESTAMP()
    AND T.TEXT_FINGERPRINT = @query_fingerprint
);

-- GoogleSQL: Derive successful-only average latency
SELECT
  INTERVAL_END,
  AVG_LATENCY_SECONDS AS combined_avg_secs,
  ALL_FAILED_AVG_LATENCY_SECONDS AS failed_avg_secs,
  (AVG_LATENCY_SECONDS * EXECUTION_COUNT
   - ALL_FAILED_AVG_LATENCY_SECONDS * ALL_FAILED_EXECUTION_COUNT)
    / NULLIF(EXECUTION_COUNT - ALL_FAILED_EXECUTION_COUNT, 0) AS success_avg_secs
FROM SPANNER_SYS.QUERY_STATS_TOP_HOUR
WHERE TEXT_FINGERPRINT = @query_fingerprint
ORDER BY INTERVAL_END DESC
LIMIT 24;
```

```sql
-- PostgreSQL: Cross-bucket P99 query latency using JSON_STRING distribution
SELECT
  CASE WHEN latency_dist IS NOT NULL
    THEN spanner_sys.distribution_percentile(latency_dist, 99.0)
    ELSE NULL
  END AS p99_latency_secs
FROM (
  SELECT
    spanner_sys.distribution_agg(
      CASE WHEN T.latency_distribution_json_string::jsonb ->> 'COUNT' != '0'
        THEN T.latency_distribution_json_string
        ELSE NULL
      END
    ) AS latency_dist
  FROM spanner_sys.query_stats_top_minute AS T
  WHERE T.interval_end > spanner.timestamptz_subtract(now(), '1 HOUR')
    AND T.interval_end <= now()
    AND T.text_fingerprint = $1
) AS agg;
```

### B. Lock Statistics (`LOCK_STATS_TOP_*` & `LOCK_STATS_TOTAL_*`)

- **Transaction & Concurrency Semantics**: Read-only transactions acquire **zero locks**. Transactions using **Optimistic Concurrency Control (OCC)** acquire locks only at **commit time**, so lock wait stats capture commit-time conflicts for OCC.
- **Lock Conflict Matrix**:

| Lock Mode | `ReaderShared` | `WriterShared` | `Exclusive` | `WriterSharedTimestamp` |
| :--- | :---: | :---: | :---: | :---: |
| **`ReaderShared`** (RW read) | No | **Yes** | **Yes** | **Yes** (full scans lock non-existent keys) |
| **`WriterShared`** (commit write) | **Yes** | No | **Yes** | N/A |
| **`Exclusive`** (RW read→write upgrade) | **Yes** | **Yes** | **Yes** | N/A |
| **`WriterSharedTimestamp`** (commit TS in PK) | **Yes** | N/A | N/A | **Yes** |

- **Schema & CRITICAL MICROSECOND GOTCHA**:
  - `ROW_RANGE_START_KEY` (`BYTES(MAX)`): Convert via `CAST(ROW_RANGE_START_KEY AS STRING)`. A trailing `+` (e.g., `orders(2,1+)`) denotes a **key range**; `table._exists` denotes row-existence checks.
  - `LOCK_WAIT_SECONDS` (`FLOAT64`): Total wait time in **seconds**.
  - `LOCK_WAIT_DISTRIBUTION` / `LOCK_WAIT_DISTRIBUTION_JSON_STRING`: Bucketed wait distribution stored in **MICROSECONDS** — always divide percentile output by `1000000.0` to obtain seconds!
  - `SAMPLE_LOCK_REQUESTS` (`ARRAY<STRUCT<column, lock_mode, transaction_tag>>`; PG `SAMPLE_LOCK_REQUESTS_JSON_STRING`): Up to **20 uniform random samples**—may capture only the lock holder or only the waiter.
  - **System Table Caveat**: High `TOTAL_LOCK_WAIT_SECONDS` in `LOCK_STATS_TOTAL_*` with empty `TOP` rows can originate from internal Spanner system tables (e.g., session management). Lock Insights UI caps TopN ranges at **100**.

```sql
-- GoogleSQL: Hourly lock contention with P99 wait converted from microseconds to seconds
SELECT
  INTERVAL_END,
  CAST(ROW_RANGE_START_KEY AS STRING) AS row_range_start,
  ROUND(LOCK_WAIT_SECONDS, 3) AS total_lock_wait_secs,
  ROUND(
    IF(
      LOCK_WAIT_DISTRIBUTION[SAFE_OFFSET(0)].COUNT > 0,
      SPANNER_SYS.DISTRIBUTION_PERCENTILE(LOCK_WAIT_DISTRIBUTION[SAFE_OFFSET(0)], 99.0) / 1000000.0,
      NULL
    ),
    4
  ) AS p99_lock_wait_secs,
  SAMPLE_LOCK_REQUESTS
FROM SPANNER_SYS.LOCK_STATS_TOP_HOUR
WHERE INTERVAL_END >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR)
ORDER BY LOCK_WAIT_SECONDS DESC
LIMIT 20;
```

- **7-Step Lock Contention Mitigation Checklist**:
  1. Check CPU utilization and Key Visualizer **first**—CPU exhaustion or hotspots prolong lock hold times, mimicking lock contention.
  2. Move read-only work out of read-write transactions (audit `RUN_IN_RW_TRANSACTION_EXECUTION_COUNT`).
  3. Eliminate full-table-scan DML in RW transactions; filter on PK (or resolve PKs in a separate RO transaction first).
  4. Project minimal columns (locks are **per cell**—never `SELECT *` inside RW transactions).
  5. Keep RW transactions short; remove external RPCs from RW scopes; split multi-thousand-row mutations or use Partitioned DML.
  6. For high read-then-write hot-row contention, evaluate `@{LOCK_SCANNED_RANGES=exclusive}` or `SELECT ... FOR UPDATE` to serialize waits instead of aborting on lock-upgrade deadlocks.
  7. Consider **Repeatable Read** (default OCC) if write-skew is acceptable, or **Serializable + OCC** for low-contention workloads.

### C. Transaction (`TXN_STATS_*`) & Read (`READ_STATS_*`) Statistics

- **`TXN_STATS_TOP_*`**: Key columns include `FPRINT` (hash of `TRANSACTION_TAG` or operation shape), `TRANSACTION_TAG`, `READ_COLUMNS` (includes index/change-stream maintenance reads), `WRITE_CONSTRUCTIVE_COLUMNS` (includes `<change_stream>.data` and `._exists`), `WRITE_DELETE_TABLES`, `ATTEMPT_COUNT`, `COMMIT_ATTEMPT_COUNT`, `COMMIT_ABORT_COUNT`, `COMMIT_RETRY_COUNT`, `COMMIT_FAILED_PRECONDITION_COUNT`, `AVG_PARTICIPANTS`, `AVG_TOTAL_LATENCY_SECONDS`, `AVG_COMMIT_LATENCY_SECONDS`, and isolation/locking counters (`SERIALIZABLE_PESSIMISTIC_TXN_COUNT`, `SERIALIZABLE_OPTIMISTIC_TXN_COUNT`, `REPEATABLE_READ_PESSIMISTIC_TXN_COUNT`, `REPEATABLE_READ_OPTIMISTIC_TXN_COUNT`).
  - **Gotcha**: `TXN_STATS` does **not** report per-transaction CPU; use `AVG_TOTAL_LATENCY_SECONDS` and `AVG_COMMIT_LATENCY_SECONDS` as proxies when investigating `beginorcommit` or `executesql_dml_partitioned` CPU spikes.
- **`READ_STATS_TOP_*`**: Tracks Read API shapes (`READ` vs. `PARTITIONED_READ`), `READ_COLUMNS` (sorted alphabetically), `AVG_CPU_SECONDS` (server-side excluding prefetch), `AVG_LOCKING_DELAY_SECONDS`, `AVG_CLIENT_WAIT_SECONDS` (detects slow client stream consumers), `AVG_LEADER_REFRESH_DELAY_SECONDS` (Paxos leader confirmation wait), `RUN_IN_RW_TRANSACTION_EXECUTION_COUNT`, and `AVG_DISK_IO_COST`.

---

## 5. Request Tags, Transaction Tags & Automated Call-Stack Autotagging

### Tag Scope, Normalization & Limits

- **Request Tags (`REQUEST_TAG`)**: Populate `QUERY_STATS_TOP_*` and `READ_STATS_TOP_*`. Valid on `Read`, `StreamingRead`, `ExecuteSql`, `ExecuteStreamingSql`, and `ExecuteBatchDml` (RO and RW). Apache Beam SpannerIO Dataflow jobs automatically set `REQUEST_TAG` to the Dataflow job name on change stream queries.
- **Transaction Tags (`TRANSACTION_TAG`)**: Populate `TXN_STATS_TOP_*`, `LOCK_STATS_TOP_*`, and **Change Streams data records**. Valid on **RW transactions only** (`BeginTransaction`, `Commit`, and reads/SQL inside RW; **invalid** on read-only transactions).
- **Hard Constraints & Side Effects**:
  - Max length: **50 characters** (silently truncated beyond 50).
  - Charset: **ASCII 32–126** only (non-ASCII replaced with `_`); **leading `_` stripped** automatically.
  - **Case-sensitive** (`APP=orders` ≠ `app=orders`); prefix `sys_cloud_console_` is reserved.
  - **Grouping Side Effect**: Distinct SQL queries sharing one `REQUEST_TAG` merge into **one row** in `QUERY_STATS_TOP_*` (retaining one sample `TEXT`); distinct transactions sharing one `TRANSACTION_TAG` merge `READ_COLUMNS` and `WRITE_CONSTRUCTIVE_COLUMNS`.
  - **Security**: Never embed PII or customer IDs (tags are stored in plaintext in `SPANNER_SYS`).
  - **Recommended Convention**: Comma-separated `key=value` pairs (e.g., `app=checkout,env=prod,svc=orders,op=create`).

### Automated Call-Stack Autotagging (Java & Go)

- **SDK Minimums**: Java client `>= 6.118.0` (emits `OrderDao.placeOrder`), Go client `>= 1.92.0` (emits `db__OrderDao__PlaceOrder`).
- **Precedence**: Manual `request_tag`/`transaction_tag` always wins; otherwise:
  1. `SPANNER_DISABLE_AUTO_TAGGING=true` (global force-disable)
  2. `SPANNER_ENABLE_AUTO_TAGGING=true` (global force-enable)
  3. Programmatic config (`SpannerOptions.newBuilder().enableAutoTagging().setAutoTaggingPackages(...).setAutoTaggingTracerLimit(100)` in Java; `spanner.ClientConfig{EnableAutoTagging: true, AutoTaggingPackages: [...], AutoTaggingTracerLimit: 50}` in Go; default stack depth = **50**)
  4. Default: **disabled**.
- **Production Trade-offs**: Stack walking adds client CPU/latency under high QPS; incompatible with `STARTS_WITH(request_tag, 'app=')` prefix queries; overloaded methods inflate tag cardinality; exposes internal class/method names in plaintext inside `SPANNER_SYS`.

---

## 6. High-CPU Investigation Runbook & Task Priority Model

### Step 1 — Separate User vs. System Priority Tasks (`CPU utilization - Total`)

| Priority | User Workloads | Spanner System Background Tasks |
| :--- | :--- | :--- |
| **High** | Default user requests (`PRIORITY_HIGH`) | **Data splitting** |
| **Medium** | `PRIORITY_MEDIUM`, Dataflow Import/Export | **Database compaction**, **Schema validation**, **Restore optimization** |
| **Low** | `PRIORITY_LOW` | **Index backfill**, **Generated column backfill**, **TTL sweeper** |

- **Backups Use Zero Instance CPU**: Managed backups execute on separate backup infrastructure.
- **Deferred Task Escalation Gotcha**: High-priority traffic can reach 100% CPU and temporarily starve medium/low system tasks—**but after several hours to a day, Spanner elevates critical deferred system tasks' priority**, directly impacting user tail latency.
- **>100% Burst Caveat**: Opportunistic unbilled bursts above 100% CPU are **not guaranteed**; sustained operation near 100% degrades latency.

### Step 2 — Drill Down by Operation Category

Query Cloud Monitoring metric `spanner.googleapis.com/instance/cpu/utilization_by_operation_type` grouped by `category`, then route to the exact `SPANNER_SYS` family:

| Operation `category` | Underlying API Methods | Query Stats | Read Stats | Txn Stats |
| :--- | :--- | :---: | :---: | :---: |
| `read_readonly` | `Read`, `StreamingRead` | No | **Yes** | No |
| `read_readwrite` | `Read`, `StreamingRead` | No | **Yes** | **Yes** |
| `read_withpartitiontoken` | `Read`, `StreamingRead` | No | **Yes** | No |
| `executesql_select_readonly` | `ExecuteSql`, `ExecuteStreamingSql` (incl. change streams) | **Yes** | No | No |
| `executesql_select_withpartitiontoken` | `ExecuteSql`, `ExecuteStreamingSql` | **Yes** | No | No |
| `executesql_select_readwrite` | `ExecuteSql`, `ExecuteStreamingSql` | **Yes** | No | **Yes** |
| `executesql_dml_readwrite` | `ExecuteSql`, `ExecuteStreamingSql`, `ExecuteBatchDml` | **Yes** | No | **Yes** |
| `executesql_dml_partitioned` | `ExecuteSql`, `ExecuteStreamingSql`, `ExecuteBatchDml` | No* | No | **Yes** |
| `beginorcommit` | `BeginTransaction`, `Commit`, `Rollback` | No | No | **Yes** |
| `misc` | `PartitionQuery`, `PartitionRead`, sessions, admin/internal | — | — | — |

*\*Route `executesql_dml_partitioned` CPU spikes to `TXN_STATS` first (completed PDML also logs `EXECUTION_COUNT = 1` in `QUERY_STATS_TOP_*` with `QUERY_TYPE = 'PARTITIONED_QUERY'`).*

### Step 3 — Incident Mitigation Sequence

1. **Scale First**: During an active production SLO incident, add compute capacity first to restore headroom (`<= 65%` regional / `<= 45%` multi-region high-priority CPU).
2. **Isolate Culprits**: Rank `EXECUTION_COUNT * AVG_CPU_SECONDS` in `QUERY_STATS_TOP_HOUR`, inspect `ACTIVE_QUERIES_SUMMARY` / `OLDEST_ACTIVE_QUERIES`, and check Key Visualizer for single-split hotspots.
3. **Remediate Root Cause**: Fix missing indexes, remove reads from RW transactions, terminate confirmed runaway queries (`CALL cancel_query(...)`), or isolate analytical scans to Data Boost / read-only replicas.

---

## Official Public Documentation

- Query Insights & Active Queries: https://docs.cloud.google.com/spanner/docs/using-query-insights
- Query, Lock, Transaction & Read Statistics: https://docs.cloud.google.com/spanner/docs/introspection/query-statistics
- Lock Insights & Contention Troubleshooting: https://docs.cloud.google.com/spanner/docs/use-lock-and-transaction-insights
- Request & Transaction Tagging: https://docs.cloud.google.com/spanner/docs/introspection/troubleshooting-with-tags
- Investigating High CPU Utilization: https://docs.cloud.google.com/spanner/docs/cpu-utilization
