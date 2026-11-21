---
name: spanner-performance-observability-and-sizing
metadata:
  version: "1.0.0"
  category: "Cloud Spanner Enterprise"
description: >-
  Guides Cloud Spanner query optimization, execution plans, Insights, Key Visualizer, pre-splitting, and Enterprise sizing. Use when managing Query Optimizer versions/ANALYZE/stats pinning, profiling execution plans, applying hints or Index Advisor, querying SPANNER_SYS (Query/Lock/Txn/Read Insights, OLDEST_ACTIVE_QUERIES, cancel_query, tags), triaging CPU/locks, diagnosing Key Visualizer heatmaps, pre-splitting (splits add), tuning CPU targets (65%/45%) and max_commit_delay, or modeling node capacity and Bangkok/Singapore Enterprise 3-store TCO. Don't use for table/index/TTL DDL (spanner-schema-and-data-modeling), autoscaler deployment/edition ops (spanner-enterprise-platform-ops), Graph GQL/Algorithms (spanner-graph-modeling-and-gql), FTS/Vector/GraphRAG (spanner-search-ai-and-graphrag), migrations (spanner-migration-and-neo4j-playbook), or MCP/FGAC security (spanner-mcp-toolbox-and-agent-security).
---

# Cloud Spanner Performance, Observability & Enterprise Sizing

Opinionated operational cheatsheet and workflow router for Cloud Spanner query optimization, distributed execution plan profiling, `SPANNER_SYS` introspection, Key Visualizer hotspot diagnostics, split-point management, and Enterprise capacity/TCO modeling.

---

## Non-Negotiable Safety & Operational Invariants

1. **Confirm Before Mutating Live Databases**: Never execute state-changing statements or CLI commands against a live remote Spanner instance/database—including `ALTER DATABASE`, `ALTER STATISTICS`, `ANALYZE`, `CALL cancel_query(...)`, `CALL compact_all()`, `gcloud spanner databases splits add`, `gcloud spanner instances update`, or write DML—without explicit user confirmation of `{project_id}`, `{instance_id}`, `{database_id}`, and operational impact.
2. **No `ROW_NUMBER()` or General Window Functions in GoogleSQL**: Cloud Spanner GoogleSQL does **not** support `ROW_NUMBER() OVER (...)`, `RANK()`, `DENSE_RANK()`, `LAG()`, or `LEAD()`. Use `UNNEST(ARRAY(SELECT ... ORDER BY ... LIMIT k)) AS x WITH OFFSET AS rank` (bounded top-K / RRF), `ANY_VALUE(col HAVING MAX(order_col))` (top-1 per group), correlated `JOIN UNNEST(ARRAY(SELECT AS STRUCT ...)) WITH OFFSET` (top-K per group), or BigQuery federated queries (`EXTERNAL_QUERY` + Data Boost) for general analytics.
3. **Do Not Double-Count Distributed Operator Latency**: In query execution plans, `Latency` on `Distributed union` and `Distributed apply` is **cumulative** (includes child execution across splits); non-distributed operators report **additive** latency. Via API (`ResultSetStats.queryStats`), reject partial stats if `statistics_since_last_restart: true` is present and re-run.
4. **Convert `LOCK_WAIT_DISTRIBUTION` Microseconds to Seconds**: `SPANNER_SYS.LOCK_STATS_TOP_*.LOCK_WAIT_DISTRIBUTION` (and `*_JSON_STRING`) stores **microseconds** (divide by `1000000.0`), whereas `LOCK_WAIT_SECONDS` and `QUERY_STATS` latency distributions store **seconds**.
5. **Respect Production Failover Headroom & Minimum Scale**: Keep high-priority CPU `<= 65%` in regional configs (3-zone synchronous quorum) and `<= 45%` in dual-/multi-region configs, storage `< 75%` (`7.5 TiB/node` of the `10 TiB/node` hard limit), and provision `>= 1 node (1,000 PU)` for production workloads (required for Managed Autoscaler and linear scaling).
6. **Reclaim Pre-Split Quota Before Drops**: Expire all user split points (`expire_time = now`) **before** dropping a table, index, or database, or they continue consuming the 50 active splits/node instance quota. Never pre-split Search or Vector indexes directly (pre-split the base table).
7. **Benchmark From Same-Region Compute**: Never benchmark production latency using Spanner Studio or `QueryMode.PROFILE` (artificial console/profiling overhead). Benchmark from a same-region GCE VM (e.g., `asia-southeast3`) with randomized bind parameters.

---

## Workflow Router & Decision Matrix

| Symptom, Goal, or Task | Immediate First Step | Deep Reference |
| :--- | :--- | :--- |
| Plan regression, stale histograms, or optimizer upgrade (`v8` → `v9`) | Check `SPANNER_SYS.SUPPORTED_OPTIMIZER_VERSIONS` & `INFORMATION_SCHEMA.SPANNER_STATISTICS`; pin or run `ANALYZE` | [query-optimizer-and-execution-plans.md](references/query-optimizer-and-execution-plans.md) |
| Slow SQL, full table scan warning, back joins, or join/sort spills | Profile plan (`--query-mode=PROFILE`), check seek vs. residual predicates, add covering `STORING`/`INCLUDE` or targeted hints | [query-optimizer-and-execution-plans.md](references/query-optimizer-and-execution-plans.md) |
| Query requires `ROW_NUMBER()` / ranking / top-K per group | Rewrite with `UNNEST(ARRAY(...)) WITH OFFSET`, `ANY_VALUE(... HAVING MAX(...))`, or BigQuery `EXTERNAL_QUERY` + Data Boost | [query-optimizer-and-execution-plans.md](references/query-optimizer-and-execution-plans.md) |
| CPU spike, runaway query, or missing attribution | Group `cpu/utilization_by_operation_type` by `category`; query `QUERY_STATS_TOP_HOUR` & `OLDEST_ACTIVE_QUERIES`; add `REQUEST_TAG` | [query-insights-and-introspection.md](references/query-insights-and-introspection.md) |
| Lock contention, commit aborts, or `ReaderShared` / `Exclusive` waits | Check CPU/hotspots first, inspect `LOCK_STATS_TOP_HOUR` (divide distribution by `1e6`), move reads out of RW txns | [query-insights-and-introspection.md](references/query-insights-and-introspection.md) |
| Hot keys, monotonic PK diagonals, or bursty load on new tables | Inspect Key Visualizer absolute legend (watch 1,000-range cap); pre-split at 10 splits/node (7d–12h ahead) via `splits add` | [key-visualizer-and-presplitting.md](references/key-visualizer-and-presplitting.md) |
| Capacity sizing, `max_commit_delay`, Tiered Storage, or 3-store TCO | Size `max(cpu_nodes@65%/45%, storage_nodes@75%, 1)`; apply batching, HDD locality groups, CUDs, and regional pricing | [enterprise-sizing-and-tco.md](references/enterprise-sizing-and-tco.md) |

---

## Sibling Skill Boundaries

Route out-of-scope requests immediately rather than improvising:

| Out-of-Scope Request | Route To |
| :--- | :--- |
| Edition selection, CUDs/Free Trial lifecycle, Managed Autoscaler deployment/asymmetric RO topology, CLI/Emulator, PITR/backups, `move-instance`, geo-partitioning ops | `spanner-enterprise-platform-ops` |
| Authoring table/PK/interleaving/index/FK/CHECK/view/TTL/Locality Group/Change Stream DDL | `spanner-schema-and-data-modeling` |
| `CREATE PROPERTY GRAPH`, ISO GQL traversals, GQL hints (`IS_FIRST`), Data Boost graph algorithms (`PageRank`, `WCC`), Studio graph viz | `spanner-graph-modeling-and-gql` |
| Vertex AI `CREATE MODEL` / `ML.PREDICT`, Full-Text Search, Exact KNN / ScaNN Vector Search, Hybrid RRF/RSF/GraphRAG | `spanner-search-ai-and-graphrag` |
| 7-stage migrations, Neo4j CE APOC export, openCypher-to-GQL translation, Elasticsearch-to-Spanner FTS migration | `spanner-migration-and-neo4j-playbook` |
| MCP Toolbox `tools.yaml`, Antigravity/ADK setup, FGAC `GRANT`/`REVOKE`, conditional `databaseRoleUser` IAM, Model Armor, Audit Logs | `spanner-mcp-toolbox-and-agent-security` |

---

## Runnable Quickstart Patterns

### 1. Inspect Optimizer Version, Statistics & Profile Execution Plan

```sql
-- Check supported optimizer versions and current default (GoogleSQL)
SELECT VERSION, RELEASE_DATE, IS_DEFAULT
FROM SPANNER_SYS.SUPPORTED_OPTIMIZER_VERSIONS
ORDER BY VERSION DESC;

-- Check active database-level overrides (GoogleSQL uses SCHEMA_NAME = ""; PostgreSQL uses schema_name = 'public')
SELECT OPTION_NAME, OPTION_VALUE
FROM INFORMATION_SCHEMA.DATABASE_OPTIONS
WHERE SCHEMA_NAME = ""
  AND OPTION_NAME IN ('optimizer_version', 'optimizer_statistics_package');

-- List available statistics packages (auto-generated every 3 days; retained 30 days)
SELECT PACKAGE_NAME, ALLOW_GC, CREATION_TIME
FROM INFORMATION_SCHEMA.SPANNER_STATISTICS
ORDER BY CREATION_TIME DESC;
```

```bash
# Profile a query plan from CLI (read-only diagnostic; do not use for latency benchmarking)
gcloud spanner databases execute-sql "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --query-mode=PROFILE \
  --sql='SELECT {col_a}, {col_b} FROM {table_name} WHERE {key_col} = "sample-id"'
```

*Override precedence (lowest → highest)*: Spanner Default ← Database Option ← Client App `QueryOptions` ← Env Var (`SPANNER_OPTIMIZER_VERSION`) ← Client Query `QueryOptions` ← Statement Hint (`@{OPTIMIZER_VERSION=9}` / `/*@ OPTIMIZER_VERSION=9 */`).

### 2. High-CPU, Lock Contention & Active-Query Triage (`SPANNER_SYS`)

```sql
-- Top 10 CPU-consuming queries in the last hour (GoogleSQL)
SELECT
  TEXT_FINGERPRINT,
  REQUEST_TAG,
  QUERY_TYPE,
  EXECUTION_COUNT,
  ROUND(EXECUTION_COUNT * AVG_CPU_SECONDS, 3) AS total_cpu_secs,
  ROUND(AVG_LATENCY_SECONDS * 1000, 2) AS avg_latency_ms,
  ROUND(AVG_ROWS_SCANNED, 1) AS avg_rows_scanned,
  RUN_IN_RW_TRANSACTION_EXECUTION_COUNT AS rw_txn_execs,
  SUBSTR(TEXT, 1, 140) AS query_preview
FROM SPANNER_SYS.QUERY_STATS_TOP_HOUR
WHERE INTERVAL_END >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 HOUR)
ORDER BY total_cpu_secs DESC
LIMIT 10;

-- Top lock wait ranges with P99 wait converted from MICROSECONDS to SECONDS (GoogleSQL)
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
WHERE INTERVAL_END >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 HOUR)
ORDER BY LOCK_WAIT_SECONDS DESC
LIMIT 10;
```

```sql
-- Real-time runaway query check (run OUTSIDE read-write transactions; omit CLIENT_IP_ADDRESS by default)
SELECT ACTIVE_COUNT, OLDEST_START_TIME, COUNT_OLDER_THAN_1S, COUNT_OLDER_THAN_10S, COUNT_OLDER_THAN_100S
FROM SPANNER_SYS.ACTIVE_QUERIES_SUMMARY;

SELECT QUERY_ID, START_TIME, PRIORITY, TRANSACTION_TYPE, SERVER_REGION, SUBSTR(TEXT, 1, 120) AS query_preview
FROM SPANNER_SYS.OLDEST_ACTIVE_QUERIES
ORDER BY START_TIME ASC
LIMIT 20;

-- Terminate a confirmed runaway query (REQUIRES explicit user confirmation + spanner.sessions.delete)
-- GoogleSQL:  CALL cancel_query('{query_id}');
-- PostgreSQL: CALL spanner.cancel_query('{query_id}');
```

### 3. Pre-Splitting Hot Tables & Indexes Before Traffic Bursts

Prepare `splits.txt` (max **100 lines/file**; single-quote `STRING`, `INT64`, `NUMERIC`, `TIMESTAMP`; leave `BOOL`, `FLOAT64`, `NULL` unquoted; escape inner commas as `\,`):

```text
TABLE {table_name} ('40000000-0000-0000-0000-000000000000')
TABLE {table_name} ('80000000-0000-0000-0000-000000000000')
TABLE {table_name} ('c0000000-0000-0000-0000-000000000000')
INDEX {index_name} ('tenant_a', '2026-12-01T00:00:00Z') TableKey ('1001', 'region\,ap')
```

```bash
# Apply split points 7 days to 12 hours before launch (REQUIRES explicit user confirmation)
gcloud spanner databases splits add "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --splits-file="splits.txt" \
  --split-expiration-date="2026-12-10T00:00:00Z" \
  --initiator="launch-presplit"

# Audit active user split points (CLI or SQL: SELECT * FROM SPANNER_SYS.USER_SPLIT_POINTS)
gcloud spanner databases splits list "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}"
```

### 4. Regional Node Sizing Formula & Bangkok / Singapore Quick Reference

$$\text{Nodes}_{\text{prod}} = \max\!\left(\left\lceil \frac{\text{Read QPS}}{C_{\text{read}} \cdot U_{\text{hp}}} + \frac{\text{Write QPS}}{C_{\text{write}} \cdot U_{\text{hp}}} \right\rceil,\; \left\lceil \frac{\text{Total Storage (TiB)}}{7.5} \right\rceil,\; 1\right)$$

- **Regional SSD capacities (`C_read` / `C_write` per node at 100% CPU, 1 KB ops)**: `22,500` reads/s, `3,500` standard writes/s, or `22,500` writes/s with `max_commit_delay = 100 ms` (`U_hp = 0.65` → `14,625` R / `2,275` standard W / `14,625` batched W per node).
- **Dual-/Multi-Region SSD capacities**: `15,000` reads/s per region, `2,700` standard writes/s (`1,500` 3-continent), or `15,000` batched writes/s (`U_hp = 0.45`).
- **Regional Enterprise Economics (verified 2026 USD, 730 hrs/mo)**:
  - **Bangkok (`regional-asia-southeast3`, 1.05× US)**: `$1.2915/node-hr` (`$942.80/mo` OD; `$754.24/mo` 1-yr CUD; `$565.68/mo` 3-yr CUD), SSD `$0.315/GiB-mo`, HDD `$0.063/GiB-mo` (**5× cheaper**), Backup `$0.0315/GiB-mo`, Data Boost `$1.2285/1k SPU-hr`.
  - **Singapore (`regional-asia-southeast1`, 1.144× US)**: `$1.40712/node-hr` (`$1,027.20/mo` OD), SSD `$0.3432/GiB-mo`, Backup `$0.03432/GiB-mo`, Cross-region backup egress `$0.08/GiB`. (No pre-built Bangkok+Singapore dual-region exists; pair `regional-asia-southeast3` primary with Singapore DR backup copy or Change Streams async DR.)

---

## Reference Library

- [query-optimizer-and-execution-plans.md](references/query-optimizer-and-execution-plans.md) — Optimizer v1–v9 evolution, `ANALYZE` & statistics GC/PII pinning, 6-tier override precedence, distributed execution operators, full GoogleSQL/PostgreSQL hint matrix, SQL tuning & no-`ROW_NUMBER()` workarounds, Visualizer JSON replay, and Index Advisor.
- [query-insights-and-introspection.md](references/query-insights-and-introspection.md) — IAM vs. FGAC observability access, clock-aligned retention windows, `QUERY_STATS` / `LOCK_STATS` / `TXN_STATS` / `READ_STATS` schemas & cross-dialect P99 SQL, `ACTIVE_QUERIES` & `cancel_query`, request/transaction tags & call-stack autotagging, and high-CPU triage runbook.
- [key-visualizer-and-presplitting.md](references/key-visualizer-and-presplitting.md) — Key Visualizer metrics, 1,000-range cap, relative brightness caveats, visual hotspot diagnostic catalog, dynamic vs. pre-splitting lifecycle, exact `splits.txt` quoting/CLI/REST syntax, quotas, deletion traps, and split-manager automation.
- [enterprise-sizing-and-tco.md](references/enterprise-sizing-and-tco.md) — Verified per-node throughput benchmarks, `max_commit_delay` tuning, DML/mutation/service quotas, granular vs. multi-node limits, Tiered Storage existence-check trap, Columnar Engine, failover CPU & autoscaler targets, verified Bangkok/Singapore pricing, worked sizing optimization, and 3-store consolidation TCO.
