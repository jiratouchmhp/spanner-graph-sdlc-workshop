# Online Schema Updates, Locality Groups, TTL, Change Streams & LROs

Production reference for Cloud Spanner zero-downtime DDL updates and batching rules, Enterprise Locality Groups & Tiered Storage, manual compaction, Time to Live (TTL) row deletion policies, Change Streams DDL and Dataflow CDC, Long-Running Operation (LRO) CLI management, and CPU task priorities.

## 1. Zero-Downtime Schema Updates & DDL Batching Rules

### Supported vs. Unsupported Online Updates

- **Supported Online:** Create/drop named schemas; create tables (`NOT NULL` allowed); drop tables (**only if no interleaved children and no secondary indexes exist**); add/drop FKs, secondary indexes, `CHECK` constraints, `STORED` generated columns, views, sequences, DB roles/grants, change streams, ML models, DB options, `ANALYZE`; add **nullable** non-key column (**new non-key columns CANNOT be `NOT NULL`**); drop non-key column (unless referenced by index, FK, `STORED` generated col, `CHECK`, or TTL); add `NOT NULL` to existing non-key col (**excluding `ARRAY`**); remove `NOT NULL`; `STRING` <-> `BYTES`; `PROTO` <-> `BYTES`; change `PROTO` message type; `ALTER PROTO BUNDLE` (add/rename enum values, non-key changes compatible with data); resize `STRING`/`BYTES` (including `MAX`, **unless PK column inherited by child tables**); resize `ARRAY<STRING|BYTES|PROTO>`; toggle `allow_commit_timestamp`; set/change/drop `DEFAULT`.
- **Unsupported Online:** Adding/removing PK columns; un-interleaving a table; converting `STRING(36)` <-> `UUID`; removing `ENUM` values from a **`PROTO` enum field referenced by a table or index key** (removing values from standalone `ENUM<>` columns *is* supported, even in keys); editing `CHECK` expressions, `STORED`/indexed generated column expressions, or FK action/enforcement in place; renaming indexes; cascading `DROP SCHEMA`.

### All 10 Background Data Validation Updates & the Early Write-Rejection Gotcha

1. Adding `NOT NULL` to an existing non-key column
2. Decreasing a `STRING`/`BYTES` length limit
3. Altering `BYTES` -> `STRING` (also decodes historical values safely for Change Streams)
4. Altering `INT64`/`INT32` -> `ENUM`
5. Removing values from an `ENUM` definition
6. Setting `allow_commit_timestamp = true` on an existing `TIMESTAMP` column
7. Adding a `CHECK` constraint to an existing table
8. Adding a `STORED` generated column to an existing table
9. Creating a new table with a foreign key
10. Adding a foreign key to an existing table

> **CRITICAL GOTCHA — Early Write Rejection:** Spanner enforces new constraints (for example, rejecting `NULL` writes for a newly added `NOT NULL` constraint) **almost immediately** while the background validation LRO scans existing data (minutes to hours). If validation fails on pre-existing invalid rows, the DDL rolls back—leaving a window where previously valid writes were rejected! **Always pre-audit existing rows with a read-only `SELECT COUNT(*)` query before running validating DDL.** Every DDL update is also validated against all existing views and rejected if any view would become invalid.

### Schema Versioning, Throttling & Batching Semantics

- **Version Retention Throttling:** Spanner retains historical schema versions across the database's `version_retention_period` (for stale reads and PITR) and caps total schema-version metadata. Exceeding the cap sets `UpdateDatabaseDdlMetadata.throttled = true` (example: a **2,000-column database** supports at most **~1,500 schema updates** within one retention period). Never run repetitive deploy-time up/down migration loops.
- **Batch Ordering & Partial-Success Semantics:**
  - Statements in a **single DDL batch** execute **in order** and stop on the first failure. The failed statement rolls back, **but earlier statements in the same batch remain committed!**
  - Statements in **separate concurrent batches** may be combined and reordered **non-deterministically**—poll `GetOperation` between dependent batches.
- **Single-Version Optimization & Hard Limits:**
  - Grouping `CREATE TABLE` immediately followed by its `CREATE INDEX` statements (with **no intervening backfill/validation statement**) consumes **1 schema version**, skips index backfill entirely, and allows **several thousand statements** per batch.
  - Inserting an unrelated `CREATE INDEX` on an *existing* table mid-batch breaks single-version optimization for all subsequent `CREATE INDEX` statements in that batch—move existing-table indexes to the end or a separate batch.
  - **Hard Limit:** Maximum **10 statements requiring backfill or data validation per batch**.
  - **Recommended Daily Limit:** Maximum **`3` new backfilled indexes per database per day**.
  - **Parallel Backfill Tip:** Because statements inside one batch execute sequentially, submit independent index backfills on large existing tables in **separate DDL batches** to backfill in parallel.
- **Expected Durations:** `CREATE`/`DROP TABLE`, `DROP INDEX`, `ADD`/`DROP COLUMN`, `CREATE INDEX` (same batch as `CREATE TABLE`), non-validating `ALTER COLUMN` -> **Minutes**; `CREATE INDEX` (existing table), validating `ALTER COLUMN`, `ANALYZE` -> **Minutes to hours**.

## 2. Locality Groups, Tiered Storage & Manual Compaction (Enterprise+)

> **Edition Boundary:** Locality Groups, Tiered Storage (SSD/HDD & age-based spill), and Manual Major Compaction (`compact_all()`) require **Cloud Spanner Enterprise or Enterprise Plus edition** (unavailable on Standard edition).

### Tiered Storage Economics & Inheritance

- **Default:** The built-in `default` locality group uses **SSD-only**.
- **Verified Regional Storage Savings (5x cheaper on HDD):**
  - **Bangkok (`regional-asia-southeast3`):** SSD **`$0.315`/GiB-mo** vs. HDD **`$0.063`/GiB-mo** (saves **`$0.252`/GiB-mo** = **`$258.05`/TiB-mo**).
  - **Reference US (`regional-us-central1`):** SSD **`$0.30`/GiB-mo** vs. HDD **`$0.06`/GiB-mo** (**5x savings** across regions).
- **Age-Based Spill (`ssd_to_hdd_spill_timespan`):** Retains data on SSD for the specified duration relative to its **commit timestamp**, then migrates aged data to HDD during normal background compaction (**~7 days**). **Minimum SSD duration = `1h` (1 hour)**.
- **Column-Grouping (Without Tiered Storage):** `CREATE LOCALITY GROUP {lg_name};` (no storage options) stores assigned columns in separate physical storage files to accelerate narrow scans over hot columns while inheriting storage tiering from the table or `default` group.
- **Inheritance Hierarchy:** Database (`default`) -> Table -> Column / Secondary Index (child inherits parent unless overridden).

### Exact Locality Group DDL Matrix (GoogleSQL vs. PostgreSQL)

| Task | GoogleSQL | PostgreSQL |
| :--- | :--- | :--- |
| Column-grouping LG | `CREATE LOCALITY GROUP separate_storage;` | `CREATE LOCALITY GROUP separate_storage;` |
| HDD-only LG | `CREATE LOCALITY GROUP hdd_only OPTIONS (storage='hdd');` | `CREATE LOCALITY GROUP hdd_only STORAGE 'hdd';` |
| Age-based spill LG (min `1h`) | `CREATE LOCALITY GROUP cold_30d OPTIONS (storage='ssd', ssd_to_hdd_spill_timespan='30d');` | `CREATE LOCALITY GROUP cold_30d STORAGE 'ssd' SSD_TO_HDD_SPILL_TIMESPAN '30d';` |
| Alter DB-level `default` LG | ``ALTER LOCALITY GROUP `default` SET OPTIONS (storage='ssd', ssd_to_hdd_spill_timespan='10d');`` *(backticks mandatory ONLY for `default`!)* | `ALTER LOCALITY GROUP "default" STORAGE 'ssd' SSD_TO_HDD_SPILL_TIMESPAN '10d';` *(double quotes!)* |
| Alter LG options | `ALTER LOCALITY GROUP cold_30d SET OPTIONS (ssd_to_hdd_spill_timespan='60d');` | `ALTER LOCALITY GROUP cold_30d SSD_TO_HDD_SPILL_TIMESPAN '60d';` |
| Table + column override | `CREATE TABLE Events (..., RawBlob BYTES(MAX) OPTIONS (locality_group='hdd_only')) PRIMARY KEY (...), OPTIONS (locality_group='ssd_only');` | `CREATE TABLE events (..., raw_blob BYTEA LOCALITY GROUP hdd_only, PRIMARY KEY (...)) LOCALITY GROUP ssd_only;` |
| Secondary index override | `CREATE INDEX IdxOld ON Events(Code) OPTIONS (locality_group='cold_30d');` | `CREATE INDEX idx_old ON events(code) LOCALITY GROUP cold_30d;` |
| Alter table / col / index LG | `ALTER TABLE {t} SET OPTIONS (locality_group='{lg}');` / `ALTER TABLE {t} ALTER COLUMN {c} SET OPTIONS (locality_group='{lg}');` / `ALTER INDEX {i} SET OPTIONS (locality_group='{lg}');` | `ALTER TABLE {t} SET LOCALITY GROUP {lg};` / `ALTER TABLE {t} ALTER COLUMN {c} SET LOCALITY GROUP {lg};` / `ALTER INDEX {i} SET LOCALITY GROUP {lg};` |
| Drop LG *(must have zero refs)* | `DROP LOCALITY GROUP {lg};` | `DROP LOCALITY GROUP {lg};` |

### Critical Tiered Storage & Compaction Gotchas

- **Insert-Heavy Tables Stay on SSD:** Every `INSERT` checks row-existence metadata stored in the **table's** locality group to enforce PK uniqueness. Always keep insert-heavy tables in an **`ssd_only`** locality group and override **only large cold non-key columns** (blobs, audit JSON payloads) to `hdd_only` or age-based spill!
- **Secondary Index Whole-Index Scope:** A secondary index's locality group governs the **entire index** (keys, row-existence metadata, and `STORING`/`INCLUDE` columns—no per-column overrides). Keep **`UNIQUE`** and write-heavy secondary indexes on **SSD-only** to avoid HDD reads during uniqueness checks.
- **7-Day Automatic Migration vs. Manual Major Compaction:**
  - `CREATE TABLE`/`INDEX` or `ADD COLUMN` applies immediately; `ALTER` applies immediately to **new writes** while existing data migrates over up to **7 days** during normal compaction.
  - **Force Immediate Compaction (Enterprise+):** GoogleSQL `CALL compact_all();` / PostgreSQL `CALL spanner.compact_all();` — returns a monitorable/cancellable **database LRO ID**. Temporarily spikes instance CPU (scale compute up first if running warm; e.g., Bangkok Enterprise compute is `$1.2915`/node-hr).
- **Storage Observability:** Per-table SSD vs. HDD bytes via `SPANNER_SYS.TABLE_SIZES_STATS_1HOUR`; Cloud Monitoring metric `spanner.googleapis.com/instance/storage/used_bytes`; schema config via `INFORMATION_SCHEMA.LOCALITY_GROUP_OPTIONS`, `COLUMN_OPTIONS` (`IS_INHERITED`), `TABLE_OPTIONS`, and `INDEX_OPTIONS` (latter two omit `default`).

```sql
-- Inspect latest hourly SSD vs. HDD storage footprint per table (GoogleSQL)
SELECT TABLE_NAME, USED_BYTES, USED_SSD_BYTES, USED_HDD_BYTES
FROM SPANNER_SYS.TABLE_SIZES_STATS_1HOUR
WHERE INTERVAL_END = (SELECT MAX(INTERVAL_END) FROM SPANNER_SYS.TABLE_SIZES_STATS_1HOUR)
ORDER BY USED_BYTES DESC;
```

## 3. Time to Live (TTL) / Row Deletion Policies

| Operation | GoogleSQL (`ROW DELETION POLICY`) | PostgreSQL (`TTL INTERVAL`) |
| :--- | :--- | :--- |
| Create with table | `CREATE TABLE AuditLogs (Id UUID, CreatedAt TIMESTAMP) PRIMARY KEY (Id), ROW DELETION POLICY (OLDER_THAN(CreatedAt, INTERVAL 90 DAY));` | `CREATE TABLE audit_logs (id uuid NOT NULL, created_at timestamptz, PRIMARY KEY (id)) TTL INTERVAL '90 days' ON created_at;` |
| Add to existing table | `ALTER TABLE AuditLogs ADD ROW DELETION POLICY (OLDER_THAN(CreatedAt, INTERVAL 90 DAY));` | `ALTER TABLE audit_logs ADD TTL INTERVAL '90 days' ON created_at;` |
| Modify policy | `ALTER TABLE AuditLogs REPLACE ROW DELETION POLICY (OLDER_THAN(UpdatedAt, INTERVAL 30 DAY));` | `ALTER TABLE audit_logs ALTER TTL INTERVAL '30 days' ON updated_at;` |
| Drop policy | `ALTER TABLE AuditLogs DROP ROW DELETION POLICY;` | `ALTER TABLE audit_logs DROP TTL;` |

### Operational Rules, Limits & Gotchas

1. **Constraints:** Max **1 TTL policy per table**; requires `TIMESTAMP` (GoogleSQL) / `TIMESTAMPTZ` (PG); interval must evaluate to a **non-negative whole number of days** (`DAY` is the only GoogleSQL unit; PG rejects fractional days like `'4 DAYS 3 MINUTES'`).
2. **`NULL` & Expired Inserts:** Rows with `NULL` timestamp are **never deleted**. Inserting already-expired rows is permitted and rows remain queryable until the sweeper removes them.
3. **Timing & Replication Cost:** Daily low-priority sweeper executes atomic local batches (row + indexes + interleaved children per batch; separate txns across batches). Deletion SLA: **`<= 72 hours`** past expiration; storage reclamation via compaction: **~7 days**. Intra-region delete replication costs **`$0.00/GiB`** in regional configs (e.g., `regional-asia-southeast3`), unlike multi-region inter-region replication fees.
4. **Foreign Key Rule:** Cannot attach TTL to a table (or ancestor of a table) referenced by a FK **unless that FK specifies `ON DELETE CASCADE`**.
5. **Interleaved Tables & `80,000` Mutation Cap:** All interleaved children under a TTL-enabled parent **must specify `ON DELETE CASCADE`**. If 1 parent row + interleaved children + indexes exceed `80,000` mutations/commit, TTL retries down to 1 parent row and marks it **undeletable**—mitigate by attaching an **earlier-expiring TTL** directly on child tables with global indexes and `> 100` children/parent.
6. **Multi-Tier TTL via Generated Columns:** Use a `STORED` generated column (e.g., `ExpireAt TIMESTAMP AS (IF(Status='CANCELLED', TIMESTAMP_ADD(Ts, INTERVAL 30 DAY), TIMESTAMP_ADD(Ts, INTERVAL 180 DAY))) STORED`) with `INTERVAL 0 DAY`. **Restriction:** Generated TTL columns **cannot reference a commit timestamp column** (direct commit timestamp columns *are* allowed).
7. **Backup Restore Gotcha:** Restoring a database from backup **automatically drops all TTL policies** (prevents immediate deletion of historical restored data)—you **must manually re-add TTL policies** after restore!
8. **Backlog Pre-Cleanup & Drops:** Clear huge pre-existing backlogs with Partitioned DML (`--enable-partitioned-dml`) before enabling TTL. Cannot drop a TTL column until the policy is dropped; dropping a policy immediately aborts active sweeps.
9. **Auditing & Monitoring:** TTL deletes emit Change Stream records tagged `transaction_tag = 'RowDeletionPolicy'` and `is_system_transaction = true`. Monitor `SPANNER_SYS.ROW_DELETION_POLICIES` (`TABLE_NAME`, `PROCESSED_WATERMARK`, `UNDELETABLE_ROWS`, `MIN_UNDELETABLE_TIMESTAMP`) and alert on Cloud Monitoring `row_deletion_policy/undeletable_rows > 0` or `row_deletion_policy/processed_watermark_age > 72 hours` (CPU starvation or hotspots).

```bash
# Pre-clean huge existing backlogs before enabling TTL (ONLY after explicit user confirmation)
gcloud spanner databases execute-sql {database_id} \
  --project={project_id} --instance={instance_id} \
  --enable-partitioned-dml \
  --sql="DELETE FROM {table_name} WHERE TIMESTAMP_ADD(CreatedAt, INTERVAL 90 DAY) < CURRENT_TIMESTAMP()"
```

```sql
-- Monitor TTL sweeper lag and undeletable rows (GoogleSQL; read-only)
SELECT TABLE_NAME, PROCESSED_WATERMARK, UNDELETABLE_ROWS, MIN_UNDELETABLE_TIMESTAMP
FROM SPANNER_SYS.ROW_DELETION_POLICIES
ORDER BY UNDELETABLE_ROWS DESC, PROCESSED_WATERMARK ASC;
```

## 4. Change Streams DDL, Internals & Dataflow CDC

```sql
-- GoogleSQL (OPTIONS clause)
CREATE CHANGE STREAM {stream_name} FOR ALL
OPTIONS (
  retention_period = '7d',                   -- default 7d, max 30d (h or d)
  value_capture_type = 'OLD_AND_NEW_VALUES', -- OLD_AND_NEW_VALUES | NEW_VALUES | NEW_ROW | NEW_ROW_AND_OLD_VALUES
  exclude_ttl_deletes = true,
  exclude_insert = false,
  exclude_update = false,
  exclude_delete = false,
  allow_txn_exclusion = true                 -- allows per-txn exclude_txn_from_change_streams=true
);
-- Track specific tables / non-key columns (NEVER list PK columns; always tracked automatically!)
CREATE CHANGE STREAM {partial_stream} FOR Customers(Email), Orders;
-- Suspend (keeps existing records for retention_period) vs. Drop (immediately deletes all stored records)
ALTER CHANGE STREAM {stream_name} DROP FOR ALL;
DROP CHANGE STREAM {stream_name};
```

```sql
-- PostgreSQL (WITH on CREATE; SET on ALTER)
CREATE CHANGE STREAM {stream_name} FOR ALL
WITH (
  retention_period = '7d',
  value_capture_type = 'NEW_ROW',
  exclude_ttl_deletes = true,
  allow_txn_exclusion = true
);
ALTER CHANGE STREAM {stream_name} SET (retention_period = '36h', exclude_delete = true);
```

- **Schema Evolution Nuances:** `FOR ALL` automatically tracks future tables/columns (excludes views & `INFORMATION_SCHEMA`); `FOR {table}` tracks added/dropped columns. Changing `value_capture_type` or a column type (`BYTES` -> `STRING`) affects **subsequent records only** (no historical backfill). Set a filter option to `false` or `null` to clear it.
- **Synchronous Split Colocation & Record Types:** Change records write synchronously inside the **same transaction and data split** (zero extra 2PC); partitions split/merge dynamically with data splits. Three record types: `data_change_record` (`mods` holds non-key `old_values`/`new_values`), `heartbeat_record` (watermark guarantee), and `child_partitions_record` (**on merges, wait for ALL parent partitions to finish before reading the merged child partition**).
- **Direct Query TVF (via `ExecuteStreamingSql` with single-use STRONG read-only txn ONLY):**
  - GoogleSQL: `SELECT ChangeRecord FROM READ_{StreamName}(@start_ts, @end_ts, @token, @heartbeat_ms, NULL)` (`ARRAY<STRUCT<...>>`)
  - PostgreSQL: `SELECT * FROM "spanner"."read_json_{StreamName}"($1, $2, $3, $4, NULL)` (`JSON`)
  - `heartbeat_ms` bounds: **`100` to `300,000` ms**; `read_options` must be `NULL`; **discard** sentinel `return_read_timestamp = kint64max - 1`.
- **Dataflow `SpannerIO.readChangeStream()` Operational Rules (e.g., co-located in `asia-southeast3`):**
  - **Separate Metadata Database (Best Practice; MANDATORY for FGAC):** Prevents connector writes from looping into `FOR ALL` streams and allows read-only permissions (`SELECT` on stream + `EXECUTE` on TVF) on the production DB.
  - **Beam & Runner Rules:** Apache Beam **`>= 2.39.0`**; Portable Runner mandatory (`--experiments=enable_portable_runner` on Beam `>= 2.74`, `--experiments=use_runner_v2` on `<= 2.73`); size **1 worker per `1,000` writes/sec**; **no Snapshots and no Draining** (Cancel or Update via `withMetadataTable`); enforce per-key commit-timestamp ordering with **Beam States & Timers** (**NOT Windows**); prefer `value_capture_type = 'NEW_ROW'` over stale snapshot reads; note Pub/Sub Flex Template **`10 MB`** message cap.
  - **Co-Location & Residency Pattern:** Co-locate Change Stream Dataflow workers and the separate metadata database inside the primary Spanner region (e.g., Bangkok `asia-southeast3`, 3-zone sync quorum, Thailand PDPA data residency) to avoid cross-region egress and keep CDC streams inside the regional residency boundary. (When cross-region async replication is explicitly approved for multi-region architectures, set `allow_txn_exclusion = true` to prevent bidirectional loops.)

## 5. Long-Running Operations (LROs) & CPU Task Priority

- **Supported LRO Verbs:** Database LROs (`databases.create`, `restore`, `updateDdl`, `compact_all()`) support `get`, `list`, `cancel` (**`delete` is UNSUPPORTED**). Instance LROs add `delete`. Backup LROs support `get`, `list`, `cancel`.

```bash
# List DDL / Backup / Restore LROs (--type is case-insensitive: DATABASE_UPDATE_DDL, BACKUP, DATABASE_RESTORE)
gcloud spanner operations list \
  --project={project_id} --instance={instance_id} --database={database_id} \
  --type=DATABASE_UPDATE_DDL

# Filter by metadata type and inspect per-statement progress (progress[] aligns 1:1 with statements[]; NO ETA)
gcloud spanner operations list \
  --project={project_id} --instance={instance_id} --database={database_id} \
  --filter="@TYPE:UpdateDatabaseDdlMetadata"

gcloud spanner operations describe {operation_id} \
  --project={project_id} --instance={instance_id} --database={database_id}

# Cancel an in-flight LRO ONLY after explicit user confirmation
gcloud spanner operations cancel {operation_id} \
  --project={project_id} --instance={instance_id} --database={database_id}
```

### CPU Task Priority & Escalation Guardrail

| Priority | User Tasks (`RequestOptions.Priority`) | System Tasks (Non-Configurable) |
| :--- | :--- | :--- |
| **High** | Default (`PRIORITY_HIGH`) reads, SQL, transactions | **Data splitting** |
| **Medium** | `PRIORITY_MEDIUM` requests; **Dataflow** import/export | **Database compaction**, **Schema change validation**, **Restore optimization phase** |
| **Low** | `PRIORITY_LOW` requests | **Index backfill**, **Generated column backfill**, **TTL sweeper** |

- **Backups:** Execute off-instance on dedicated backup workers (zero instance CPU impact).
- **Recommended Max CPU Targets:** Regional instances (e.g., `regional-asia-southeast3`) <= **`65%`** high-priority CPU; Dual-/Multi-Region instances <= **`45%`** per region; **24-hour smoothed aggregate <= `90%`** (never rely on unbilled `> 100%` burst headroom for steady state).
- **CRITICAL PRIORITY-ESCALATION GOTCHA:** Low/medium system tasks (compaction, schema validation) yield quickly to user requests, **but cannot be starved forever**. After **several hours to a day** of insufficient compute, Spanner **escalates their priority**, directly degrading production latency. Temporarily scale compute up before invoking `compact_all()` or large DDL backfills on warm instances.

## Official Documentation

- [Schema Updates](https://docs.cloud.google.com/spanner/docs/schema-updates) | [Schema Update Best Practices](https://docs.cloud.google.com/spanner/docs/schema-updates-best-practices)
- [Locality Groups & Tiered Storage](https://docs.cloud.google.com/spanner/docs/create-manage-locality-groups) | [Manual Data Compaction](https://docs.cloud.google.com/spanner/docs/manual-data-compaction)
- [Time to Live (TTL)](https://docs.cloud.google.com/spanner/docs/ttl) | [TTL Monitoring & Metrics](https://docs.cloud.google.com/spanner/docs/ttl/monitoring-and-metrics)
- [Manage Change Streams](https://docs.cloud.google.com/spanner/docs/change-streams/manage) | [Change Streams with Dataflow](https://docs.cloud.google.com/spanner/docs/change-streams/use-dataflow)
- [Manage & Observe LROs](https://docs.cloud.google.com/spanner/docs/manage-and-observe-long-running-operations) | [CPU Utilization & Task Priority](https://docs.cloud.google.com/spanner/docs/cpu-utilization)
