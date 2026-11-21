# Cloud Spanner Migration Lifecycle, Live CDC, Cutover & Fallback

Production runbook for planning, executing, validating, cutting over, and failing back database migrations into Cloud Spanner Enterprise.

## 1. The 7-Stage Migration Lifecycle & 10 Assessment Dimensions

Execute every non-trivial migration through seven gated stages:

| Stage | Objective | Primary Deliverable / Gate |
| :--- | :--- | :--- |
| **1. Assess** | Evaluate workload fit, cost, compliance, and downtime budget | Signed-off architecture & TCO model |
| **2. Migrate Schema** | Convert DDL via Spanner Migration Tool (SMT), refine PKs/interleaving/indexes | Verified staging DDL & validation scripts |
| **3. Migrate Application** | Rewrite stored procedures/triggers into app code; adopt gRPC client libraries | Passing integration tests against staging |
| **4. Test & Tune** | Load representative data, run load tests, resolve hotspots & CPU bottlenecks | SLA-compliant p99 latency & Key Visualizer |
| **5. Migrate Data** | Execute bulk downtime load OR live snapshot backfill + CDC stream | Zero stranded `severe/` DLQ files |
| **6. Validate Data** | Reconcile schemas, row counts, aggregations, and row hashes via DVT/Beam | 100% parity report |
| **7. Fallback & Cutover** | Arm reverse replication (`Spanner_to_SourceDb`), drain forward CDC, switch traffic | Clean cutover with verified rollback path |

### Mandatory 10-Dimension Assessment Checklist

1. **Business goals:** Scalability, availability SLA (`99.99%` regional vs up to `99.999%` Enterprise Plus multi-region), consistency, latency, and TCO KPIs.
2. **Cost analysis:** Compute + storage + network egress + one-time migration workers + ongoing DR/ops, factoring in 1-yr (`20%` off) and 3-yr (`40%` off) CUDs.
3. **Schema compatibility:** Data types, primary keys, constraints, indexes, and removal of stored procedures/triggers.
4. **Data consistency & transactions:** TrueTime external consistency vs source database isolation defaults.
5. **Data locality & regional compliance:** Primary region selection, residency laws (e.g., Thailand PDPA), and cross-region DR design.
6. **Application code compatibility:** GoogleSQL vs PostgreSQL dialect, client SDKs/ORMs, mutation ceilings, and transaction retries.
7. **Performance & scalability:** Read/write QPS ratios, storage growth, peak bursts, and latency SLAs.
8. **Migration strategy & downtime:** Offline bulk dump/load vs live CDC + cutover window + fallback SLA.
9. **Operational readiness:** Monitoring dashboards, backup schedules, PITR, DR drills, and on-call playbooks.
10. **Security:** IAM, Fine-Grained Access Control (FGAC), TLS encryption in transit, CMEK, VPC-SC, and audit logging.

### Editions, Downgrade Rules & Regional Pricing Example

| Capability / Rate (2026 Verified) | Standard | Enterprise | Enterprise Plus |
| :--- | :--- | :--- | :--- |
| **SLA & Topology** | `99.99%`, regional only | `99.99%`, regional + custom RO replicas | Up to `99.999%`, regional/dual/multi-region |
| **Multi-Model & Search** | Relational, Key-Value | + **Spanner Graph**, **FTS**, **Vector Search** | + **Spanner Graph**, **FTS**, **Vector Search** |
| **Analytics & Protection** | BQ federation, Data Boost, 7d PITR | + **Columnar Engine**, **Incremental Backups**, Managed Autoscaler, Locality Groups | + **Geo-Partitioning** |
| **Bangkok (`regional-asia-southeast3`, 1.05x) Compute/node-hr** | `$0.945` (`$0.756` 1yr / `$0.567` 3yr) | **`$1.2915`** (`$1.0332` 1yr / `$0.7749` 3yr) | `$1.7955` (`$1.4364` 1yr / `$1.0773` 3yr) |
| **Singapore (`regional-asia-southeast1`, 1.144x) Compute/node-hr** | `$1.0296` (`$0.82368` 1yr / `$0.61776` 3yr) | **`$1.40712`** (`$1.125696` 1yr / `$0.844272` 3yr) | `$1.95624` (`$1.564992` 1yr / `$1.173744` 3yr) |
| **Storage (SSD / HDD / Backup) per GiB-mo (Bangkok)** | `$0.315` / `$0.063` / `$0.0315` | `$0.315` / `$0.063` / `$0.0315` | `$0.315` / `$0.063` / `$0.0315` |
| **Data Boost per 1,000 SPU-hr (Bangkok / Singapore)** | `$1.2285` / `$1.33848` | `$1.2285` / `$1.33848` | `$1.2285` / `$1.33848` |

- **Edition upgrades/downgrades:** Take **~10 minutes with zero downtime and zero data migration**. Monitor active edition-gated features via Cloud Monitoring metric `spanner.googleapis.com/instance/edition/feature_usage` (sampled every 60s, visible within 120s).
- **Downgrade blockers:** Dropping from Enterprise requires removing all `PROPERTY GRAPH` schemas, `SEARCH INDEX`es, `VECTOR INDEX`es, KNN/ANN calls, Managed Autoscaler, and custom RO replicas. Dropping from Enterprise Plus also requires moving off dual/multi-region and deleting geo-partitions.
- **Export/Import edition constraint:** Importing a Spanner export requires a target instance on the **same or higher edition** as the source.
- **Regional DR topology note (Bangkok + Singapore example):** Cloud Spanner has no pre-built dual-region pairing `asia-southeast3` and `asia-southeast1` (dual-regions exist in AU/CA/DE/IN/JP; `asia2` covers Mumbai/Delhi/Singapore). Deploy primary on `regional-asia-southeast3` (3 synchronous zones: `asia-southeast3-a`, `asia-southeast3-b`, `asia-southeast3-c`; `$0.00/GiB` intra-region replication) and pair with Singapore (`regional-asia-southeast1`) via cross-region backup copy (`gcloud spanner backups copy`, `$0.08/GiB` Asia transfer + `$0.03432/GiB-mo` backup storage) and/or optional warm DR fed asynchronously via Change Streams + Dataflow. Co-locate GCS staging buckets and Dataflow workers in `asia-southeast3`.

## 2. Migration Tooling Matrix

| Source DB | Assessment & Schema | Sample Data Load | Production Migration | Validation | Fallback / Reverse |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **MySQL** | SMT (`mysqldump` or direct) / DMA | `<100 GB`: SMT POC; `>100 GB`: Cloud SQL -> `sourcedb-to-spanner`; CSV/Avro/Parquet -> BQ Reverse ETL | Bulk (`sourcedb-to-spanner`) + Live (Datastream + `Cloud_Datastream_to_Spanner`) | DVT | SMT `Spanner_to_SourceDb` |
| **PostgreSQL** | SMT (`pg_dump` or direct) / DMA | `<100 GB`: SMT POC; `>100 GB`: Cloud SQL -> minimal-downtime; files -> BQ Reverse ETL | Minimal-downtime migration (SMT) | DVT | No prebuilt reverse pipeline |
| **Cassandra** *(Preview)* | `spanner-cassandra-schema-tool` | `sourcedb-to-spanner` | Bulk (`sourcedb-to-spanner`) + Live (DataStax ZDM Proxy) | `<10M` rows scripts / `>10M` custom | SMT `Spanner_to_SourceDb` |
| **Other / Non-Relational** | Custom DDL design | CSV/Avro/Parquet -> BQ Reverse ETL or GCS -> Dataflow | Dataflow (`GCS_Text`/`GCS_Avro`), BQ Reverse ETL, Spark Spanner | DVT / custom SQL & hash checks | Custom Change Streams |

## 3. Consistency, Schema Conversion & Application Migration

### TrueTime, Isolation & Timestamp Bounds

- **External Consistency (default Serializable):** TrueTime + MVCC guarantees monotonic global timestamps (`T > T'` whenever `T'` committed before `T` started). Strictly stronger than linearizability and serializability. Snapshot reads never block writes; strong reads finish in one network round.
- **MySQL Transaction Alignment:** Set **Repeatable Read isolation** + **pessimistic concurrency control** when migrating MySQL applications so transaction conflict behavior matches MySQL defaults and reduces read-write contention.
- **Read-Only Transaction Timestamp Bounds:**
  - *Strong read (default):* Sees all prior commits.
  - *Exact staleness:* Consistent snapshot up to **1 hour** in the past (or up to **7 days** with PITR configured).
  - *Bounded staleness:* Serves from nearest replica without cross-replica consensus — low latency with full transactional consistency.
  - *Read in the future:* Blocks until the requested timestamp passes.

### MySQL -> Spanner (GoogleSQL) Type Mapping Gotchas

- `TINYINT`/`SMALLINT`/`MEDIUMINT`/`INT`/`BIGINT` -> `INT64` (8 bytes each; add `CHECK` constraints if source range limits matter).
- `TINYINT(1)`/`BOOLEAN` -> `BOOL`; `BIT` -> `BOOL` or `INT64`.
- `CHAR`/`VARCHAR`/`*TEXT` -> `STRING` (UTF-8 only, **no configurable collations**; `2,621,440`-char ceiling; offload values `> 10 MiB` to GCS URI).
- `FLOAT` -> `FLOAT32`; `DOUBLE` -> `FLOAT64`.
- `DECIMAL`/`NUMERIC` (MySQL up to 65 digits) -> `NUMERIC` (**38 precision / 9 scale** in GoogleSQL — audit for overflow/truncation!).
- `BINARY`/`VARBINARY`/`*BLOB` -> `BYTES` (`< 10 MiB`, else GCS URI).
- `DATETIME`/`TIMESTAMP` -> `TIMESTAMP` (timezone-independent; persist source timezone in a separate `STRING` column if needed).
- `ENUM` -> `STRING` (validate in app); `SET` -> `ARRAY<STRING>`; `JSON` -> `JSON` (`< 2,621,440` chars); `GEOMETRY`/`POINT`/`POLYGON` -> unsupported natively (encode as `STRING`/`ARRAY`/`BYTES` and filter in application).
- **Primary Keys & Logic:** Prefer UUIDv4 (`GENERATE_UUID()`) or **bit-reversed positive sequences** to prevent write hotspots. Rewrite all stored procedures and triggers into application-level read-write transactions.

### Client Libraries, Write APIs & Sample Loading

- **Client Priority:** Prefer **gRPC Client Libraries** (Go, Java, Node.js, Python, Ruby, C++, PHP, C#, Rust — manage sessions, routing, retries; note client-side metrics are supported in Go, Java, Node.js, Python, Rust only) or supported ORMs (JDBC, Hibernate, Spring Data, SQLAlchemy, GORM, EF Core, Active Record). Avoid the **REST API** for latency-sensitive apps: REST lacks request cancellation, deadlines/timeouts, and application-level streaming flow control.
- **Write Mechanisms:**
  - **Mutations API:** Atomic blind writes (`INSERT`, `INSERT_OR_UPDATE`, `UPDATE`, `DELETE`) bypassing SQL parsing (cannot read first).
  - **Read-Write Transactions:** Required whenever writes depend on prior reads.
  - **Batch Write:** High-throughput blind writes when cross-batch atomicity is unnecessary.
  - **Partitioned DML:** Mandatory for bulk updates/deletes exceeding **`80,000` mutations/commit** (`40,000` rows on a table with 1 secondary index; max `100 MiB`/commit; up to `20,000` concurrent PDML statements per database).
- **Quick Sample Load via BigQuery Reverse ETL** (requires `roles/bigquery.dataViewer`, `roles/bigquery.user`, `roles/spanner.databaseUser`):

```bash
bq mk --location=asia-southeast3 {bq_dataset}
bq load --source_format=CSV --autodetect --allow_quoted_newlines \
  {bq_dataset}.{bq_table} gs://{bucket}/{sample_file}.csv

bq query --use_legacy_sql=false "
EXPORT DATA OPTIONS(
  uri='https://spanner.googleapis.com/projects/{project_id}/instances/{instance_id}/databases/{database_id}',
  format='CLOUD_SPANNER',
  spanner_options='{\"table\": \"{table_name}\"}'
) AS SELECT * FROM {bq_dataset}.{bq_table};"
```

### High-CPU Triage During Load Testing

1. Separate user CPU from background compaction/index backfill on **CPU utilization - Total**.
2. Inspect `spanner.googleapis.com/instance/cpu/utilization_by_operation_type` grouped by `category`:
   - `read_readonly`, `read_withpartitiontoken` -> Read stats; `read_readwrite` -> Read + Transaction stats.
   - `executesql_select_readonly`, `executesql_select_withpartitiontoken` -> Query stats.
   - `executesql_select_readwrite`, `executesql_dml_readwrite` -> Query + Transaction stats.
   - `executesql_dml_partitioned`, `beginorcommit` -> Transaction stats (use `AVG_TOTAL_LATENCY_SECONDS` / `AVG_COMMIT_LATENCY_SECONDS` as CPU proxies).
3. Rank top CPU queries over the last hour:

```sql
SELECT text,
       execution_count AS count,
       avg_latency_seconds AS latency,
       avg_cpu_seconds AS cpu,
       execution_count * avg_cpu_seconds AS total_cpu
FROM SPANNER_SYS.QUERY_STATS_TOP_HOUR
WHERE interval_end = (SELECT MAX(interval_end) FROM SPANNER_SYS.QUERY_STATS_TOP_HOUR)
ORDER BY total_cpu DESC;
```

## 4. Live Data Migration (Backfill + CDC + DLQ Replay)

### Critical 3-Phase Execution Sequence (MySQL -> Spanner)

1. **Phase 1 — Start CDC Capture to GCS FIRST:** Provision Datastream private VPC connection (dedicated reserved IP range), MySQL source profile, GCS bucket/profile, Datastream stream, and GCS->Pub/Sub notifications (using Terraform sample `v2/datastream-to-spanner/terraform/samples/mysql-end-to-end`). Set **`skip_dataflow = true`** initially so Datastream buffers CDC events safely in GCS without racing the snapshot.
2. **Phase 2 — Run Bulk Backfill:** Execute the `sourcedb-to-spanner` Dataflow template on a dedicated backfill subnetwork to load the consistent snapshot into Spanner. Resolve and replay any backfill DLQ errors.
3. **Phase 3 — Launch CDC Dataflow Writer:** Flip **`skip_dataflow = false`** and re-apply Terraform. `Cloud_Datastream_to_Spanner` drains buffered GCS files via Pub/Sub and applies ongoing CDC changes.

- **Required IAM for Live Migration SA:** `roles/dataflow.admin`, `roles/datastream.admin`, `roles/iam.securityAdmin`, `roles/iam.serviceAccountAdmin`, `roles/pubsub.admin`, `roles/storage.admin`, `roles/compute.networkAdmin`, `roles/viewer`.
- **Offline Downtime Alternative:** Pause app writes or read-lock a paused replica, split dumps into **multiple small files** for parallel workers, upload to GCS, and load via SMT or Dataflow.

### DLQ Anatomy & Replay Rules

- `retry/`: Transient errors (e.g., `DEADLINE_EXCEEDED`, parallel-arrival FK/parent-missing races). Automatically retried by the live streaming job.
- `severe/`: Permanent errors (type conversion errors, unique index violations, transformation JAR exceptions). Removed from the live stream and counted in `Total permanent errors`.
- **CRITICAL GOTCHA:** `Total permanent errors` is **cumulative** and NEVER decrements after replay. Always check `gs://{bucket}/.../severe/` directly as the ground truth.
- **Replay Modes:**
  - `runMode="retryDLQ"`: Replays `severe/` only; **safe to run concurrently** alongside the active streaming pipeline.
  - `runMode="retryAllDLQ"`: Replays both `retry/` and `severe/`; **NEVER run concurrently** with an active streaming job (causes file contention).

```bash
gcloud dataflow flex-template run {retry_job_name} \
  --project={project_id} \
  --region=asia-southeast3 \
  --template-file-gcs-location=gs://dataflow-templates/latest/flex/Cloud_Datastream_to_Spanner \
  --additional-experiments=use_runner_v2 \
  --parameters streamName="ignore",\
datastreamSourceType=mysql,\
instanceId={instance_id},databaseId={database_id},\
sessionFilePath={session_gcs_path},\
deadLetterQueueDirectory={dlq_gcs_dir},\
runMode="retryDLQ"
```

## 5. Data Validation (DVT & Apache Beam)

Run the open-source **Data Validation Tool (DVT)** (`google-pso-data-validator`) prior to cutover across five progressive tiers:
1. Table existence and column schema mapping verification.
2. Exact per-table row count reconciliation.
3. Random row sampling comparisons.
4. Column aggregations (`COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, `GROUP BY`).
5. Row-level CRC / hash comparisons.

For high-volume continuous validation during live CDC, run an **Apache Beam / Dataflow `Join` transform** over keyed source and Spanner snapshots to detect drift continuously.

## 6. Configuring Fallback via Reverse Replication (`Spanner_to_SourceDb`)

Configure reverse replication **before cutover** to enable a low-downtime rollback to MySQL:

- **How it works:** Reads Spanner Change Streams -> filters out forward-migrated writes (`filtrationMode=forward_migration`) -> reverses schema transformations using SMT `session.json` -> tracks per-PK commit timestamps in Spanner **shadow tables** (`shadow_*`) to skip stale writes -> writes to MySQL shards.
- **Firewall & MySQL Prereqs:** MySQL user needs `INSERT`, `UPDATE`, `DELETE`; allowlist Dataflow worker IPs; **TCP port `12345` MUST be open** between Dataflow worker VMs.
- **IAM Roles:** Launcher requires `roles/spanner.databaseUser`, `roles/dataflow.developer`. Compute Engine worker SA requires `roles/spanner.databaseUser`, `roles/secretmanager.secretAccessor`, `roles/secretmanager.viewer`.

```bash
gcloud dataflow flex-template run "{reverse_job_name}" \
  --project "{project_id}" \
  --region "asia-southeast3" \
  --template-file-gcs-location "{template_spec_gcs_path}" \
  --parameters "changeStreamName={change_stream_name}" \
  --parameters "instanceId={instance_id}" \
  --parameters "databaseId={database_id}" \
  --parameters "spannerProjectId={project_id}" \
  --parameters "metadataInstance={metadata_instance_id}" \
  --parameters "metadataDatabase={metadata_database_id}" \
  --parameters "sourceShardsFilePath={source_shards_gcs_path}" \
  --parameters "sessionFilePath={session_gcs_path}" \
  --parameters "filtrationMode=forward_migration"
```

- **Key Optional Defaults:** `shadowTablePrefix=shadow_`, `sourceDbTimezoneOffset=+00:00`, `skipDirectoryName=skip`, `maxShardConnections=10000`, `dlqMaxRetryCount=500`, `dlqRetryMinutes=10`, `runMode=regular` (`regular` | `retryDLQ` | `retryAllDLQ`), `sourceType=mysql`, `filterEventsDirectoryName=filteredEvents`.
- **Pub/Sub DLQ Gotcha:** Setting `dlqGcsPubSubSubscription` causes the job to **ignore** `deadLetterQueueDirectory` and `dlqRetryMinutes` in `regular` mode.
- **Reverse DLQ Counter:** `severe_error_count` is also cumulative; verify `gs://{bucket}/{env}/{service}/reverse/{db_schema}/severe/` directly.

## 7. Cutover & Fallback Runbooks (Exact MQL Gates)

### Forward Cutover Runbook (Short Write-Downtime Window)

Required roles: `roles/spanner.databaseUser`, `roles/dataflow.admin`, `roles/datastream.admin`, `roles/storage.admin`, `roles/monitoring.viewer`, `roles/pubsub.viewer`, `roles/viewer`.

1. **Pre-Cutover Gate:** Confirm forward GCS `severe/` is **100% empty** and reverse replication (`Spanner_to_SourceDb`) is healthy if rollback is required.
2. **Pause Application Writes** *(downtime starts)*: Drain in-flight source MySQL commits.
3. **Track & Pause Datastream:** Wait for `datastream.googleapis.com/stream/freshness == 0` AND confirm `event_count == 0` for **`>= 2` consecutive 1-minute windows**, then pause the stream and wait for state `PAUSED`:
   ```mql
   fetch datastream.googleapis.com/Stream
   | metric 'datastream.googleapis.com/stream/event_count'
   | filter (resource.stream_id == '{stream_id}')
   | align rate(1m)
   | group_by [], [value_event_count_sum: sum(value.event_count)]
   | every 1m
   ```
4. **Drain In-Flight GCS & Pub/Sub Buffers:** Inspect P99 of `datastream.googleapis.com/stream/system_latencies`, wait **at least `(2 * P99 system latency) + 30 seconds`** after `PAUSED`, and confirm `pubsub.googleapis.com/subscription/num_undelivered_messages == 0`.
5. **Drain Dataflow Safely:**
   - Wait for custom counter `Replication lag total latency_MAX == 0`:
     ```mql
     fetch dataflow_job
     | metric 'dataflow.googleapis.com/job/user_counter'
     | filter (metric.job_id == '{job_id}' && metric.metric_name == 'Replication lag total latency_MAX')
     | group_by 1m, [value_p99_latency: percentile(value.user_counter, 99)]
     | every 1m
     ```
   - Confirm `spanner.googleapis.com/transaction_stat/total/bytes_written_count == 0` for **`>= 2` consecutive 1-minute windows**.
   - Verify BOTH `severe/` and `retry/` DLQs are empty, stop Dataflow using **Drain (NEVER Cancel)**, wait for state `Drained`, and **re-verify** `severe/` and `retry/` are still empty.
6. **Switch Application Traffic to Spanner** *(downtime ends)*: Update connection config, start app, validate reads/writes, and watch reverse `severe/` DLQ.

### Fallback (Rollback) Runbook

Requires active `Spanner_to_SourceDb` in `forward_migration` mode and reachable source MySQL:

1. **Pause Spanner Writes** *(downtime starts)* and let in-flight Spanner commits complete.
2. **Wait for Reverse Replication Lag == `0`:**
   ```mql
   fetch dataflow_job
   | metric 'dataflow.googleapis.com/job/user_counter'
   | filter (metric.job_id == '{reverse_job_id}' && metric.metric_name == 'replication_lag_in_seconds_{logical_shard}_MAX')
   | group_by 1m, [value_p99_latency: percentile(value.user_counter, 99)]
   | every 1m
   ```
3. **Verify MySQL Writes Stopped** (check active connections/queries on source shards).
4. **Verify Clean Reverse DLQ:** Run `gcloud storage ls gs://{bucket}/{env}/{service}/reverse/{db_schema}/severe/` (and `retry/`); resolve and replay any stranded files before switching traffic.
5. **Optional — Resume Forward Datastream Stream** (if retrying migration later):
   ```bash
   gcloud datastream streams resume {stream_id} --location=asia-southeast3
   ```
   Wait for state `RUNNING`.
6. **Reroute Application Writes to MySQL** *(downtime ends)* and validate traffic.

## 8. Security, Disaster Recovery & Support

- **Encryption in Transit:** Spanner endpoints behind Google Front Ends (GFEs) enforce TLS by default; layer IAM, FGAC, CMEK, and **database deletion protection**.
- **DR Protection Matrix:** Zonal failure -> 3-zone regional quorum (`regional-asia-southeast3`); Regional failure -> multi-region (Enterprise Plus) or regional primary + cross-region backup copy / Change Streams async DR (`regional-asia-southeast1`); Logical corruption -> **PITR** (up to 7 days), **Full/Incremental Backup Schedules** (up to 1 year; incremental backups require Enterprise+ via backup schedules only), or **GCS Export** (indefinite retention).

| Dimension | Back up and Restore | Import and Export |
| :--- | :--- | :--- |
| **Consistency** | Transactionally & externally consistent | Transactionally & externally consistent |
| **Instance CPU Impact** | **Zero** impact (dedicated jobs) | Export: light/medium (offload via **Data Boost**); Import: **significant CPU load** |
| **Portability & Retention** | Proprietary encrypted; cross-region copyable; max **1 year** | Portable CSV/Avro in GCS; **indefinite** via GCS lifecycle |
| **Restore / Load Speed** | Fast time-to-first-byte (mount restore + background optimize) | Slower (writes every row into target tables) |

- **Official Documentation & Support:** [Migration Overview](https://docs.cloud.google.com/spanner/docs/migration-overview), [MySQL Live Migration](https://docs.cloud.google.com/spanner/docs/mysql-live-data-migration), [Cutover Runbook](https://docs.cloud.google.com/spanner/docs/mysql-perform-cutover), [Fallback Runbook](https://docs.cloud.google.com/spanner/docs/mysql-fallback), [DR Overview](https://docs.cloud.google.com/spanner/docs/backup/disaster-recovery-overview). Community/Support: Stack Overflow `google-cloud-spanner`, GCP Slack `#cloud-spanner`, Public Issue Tracker `componentid:190851`.

### Platform Quotas & Throughput Benchmarks Reference

- **Commit ceilings:** `80,000` mutations/commit (`40,000` rows on a table with 1 secondary index); `100 MiB` max commit size; `20,000` concurrent Partitioned DML statements/database; recommended max `3` unbatched secondary indexes/day (batch DDL statements in one request to avoid throttling).
- **Storage & splits:** `10 TiB` per node (`1,024 GiB` per 100 PU; alert at `75%`); `8 GiB` split size threshold; `STRING`/`JSON` max `2,621,440` chars (offload `> 10 MiB` values to GCS).
- **CPU & QPS benchmarks (regional SSD per node):** Steady-state CPU `<= 65%` regional (`<= 45%` multi-region), `90%` smooth ceiling, `70%–90%` bulk import target; `22,500` reads/sec, `3,500` single-row writes/sec, `22,500` batched writes/sec (regional HDD: `1,500` reads/sec, `3,500` single / `22,500` batched writes/sec).

