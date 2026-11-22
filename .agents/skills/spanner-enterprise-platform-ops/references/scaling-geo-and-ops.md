# Cloud Spanner Scaling, Replication, Geo-Partitioning, DR & Lifecycle Ops

Self-contained operational runbook for Cloud Spanner instance topologies, Paxos quorum and failover, Read Leases, Managed vs Open-Source Autoscaler deployment, zero-downtime `move-instance`, Geo-Partitioning (Preview), PITR, Backups and Restore, Deletion Protection, Dataplex, IAM operational bundles, CMEK/EKM, and Audit Logging.

## 1. Instance Configurations, Replication, Quorum & Leader Region

| Topology | Replica Layout | Write Quorum | Availability SLA | Custom RO Replicas |
| :--- | :--- | :--- | :---: | :--- |
| **Regional** (e.g., `regional-asia-southeast3`, `regional-asia-southeast1`) | 3 RW replicas across 3 zones in 1 region | 2 of 3 zones | **99.99%** | Optional (Enterprise+) on supported base configs |
| **Dual-Region** (AU, CA, DE, IN, JP only) | 2 regions × (2 RW + 1 Witness) = 6 replicas | >= 2 in **each** region (4 total; RPO = 0) | **99.999%** (Enterprise Plus) | **NOT supported** |
| **Multi-Region** (e.g., `nam3`, `eur3`, `asia1`) | Typically 2 RW regions × 2 RW + 1 Witness region (5 voting) | 3 of 5 voting (incl. leader) | **99.999%** (Enterprise Plus) | Optional (Enterprise Plus) |
| **Multi-Region `asia2`** (Allowlisted: Mumbai, Delhi, Singapore) | 3 RW regions (2 + 2 + 1 = 5 RW, **no witness**) | 3 of 5 voting | **99.999%** (Enterprise Plus) | Optional (Enterprise Plus) |

- **Dual-Region Failover & Failback Mechanics**:
  - **Manual Failover** (requires `spanner.databases.changequorum`): ~**1 minute** RTO; transitions dual-region quorum to single-region quorum in the surviving region. **You MUST manually fail back** once the failed region recovers! Monitor `spanner.googleapis.com/instance/dual_region_quorum_availability`.
  - **Google-Managed Automatic Failover**: Up to **45 minutes** RTO.
- **CRITICAL READ-ONLY REPLICA GOTCHA (NOT Standalone DR!)**:
  - Read-only replicas **do NOT vote** in Paxos quorum and **cannot become leader**.
  - **They DO NOT provide standalone DR**: if the voting quorum goes offline, read-only replicas also go offline shortly afterward and **cannot serve even stale reads**.
  - Best practice: Deploy **>= 2 RO replicas** when spanning continents for resilience against a single-zone failure.
- **Modifying Default Leader Region**: Metadata-only LRO taking a few minutes (eligible only for RW regions in dual/multi-region configs):
  ```sql
  -- GoogleSQL
  ALTER DATABASE `{database_id}` SET OPTIONS (default_leader = 'asia-northeast1');
  -- PostgreSQL
  ALTER DATABASE {database_id} SET spanner.default_leader = 'asia-northeast1';
  ```
  Monitor cross-region replication traffic (`spanner.googleapis.com/instance/cross_region_replicated_bytes_count`) and leader balance (`instance/leader_percentage_by_region`).

## 2. Read Leases (Local Strong Reads in Non-Leader Regions)

Grants specified non-leader RW or RO regions permission to serve **strong reads locally** at intra-region latency without a leader round-trip (zero-downtime enable/disable).

- **Write Latency Trade-Off**: Every write requires the leader to contact all read-lease regions → **write latency increases by +1 RTT** to the farthest read-lease region, holding write locks longer. Best for read-heavy, low-write-contention workloads (e.g., ACL/config catalogs).
- **Exact DDL**:
  ```sql
  -- GoogleSQL (disable with = NULL)
  ALTER DATABASE `{database_id}` SET OPTIONS (read_lease_regions = 'europe-west1, europe-west4');
  -- PostgreSQL (disable with = NULL)
  ALTER DATABASE {database_id} SET "spanner.read_lease_regions" = 'europe-west1, europe-west4';
  -- Placement-level (Preview)
  CREATE PLACEMENT {placement_name} OPTIONS (instance_partition='{partition_id}', read_lease_regions='...');
  ```
- **4 Critical Read Lease Gotchas**:
  1. **RW Transactions Ignored by Default**: Read leases accelerate **read-only transactions** only — unless you combine **Repeatable Read isolation** AND **disable leader-aware routing** (`routeToLeader=false`), and even then read leases turn off after the first write DML in that transaction (for read-your-writes).
  2. **PostgreSQL Connection Prerequisite**: Without explicit PG connection options, PostgreSQL reads **still route to the leader**! Set `options='-c default_transaction_read_only=true'` (RO workloads) OR `options='-c default_isolation_level=REPEATABLE_READ -c routeToLeader=false'`.
  3. **Cleared by `move-instance`**: Moving an instance does **NOT** preserve `read_lease_regions` — re-enable after the move finishes.
  4. **Immutable on Placements**: Cannot alter or disable `read_lease_regions` on an existing Geo-Partitioning placement (must create new placement → PDML move → drop old).
- **Verification**: Use **multiplexed sessions** and verify local serving with `spanner.googleapis.com/api/read_request_latencies_by_serving_location` (filtered by `serving_location`) and `INFORMATION_SCHEMA.DATABASE_OPTIONS`.

## 3. Autoscaling Deployment: Managed vs Open-Source Autoscaler

*(For CPU target tuning [65% regional / 45% multi-region] and node capacity sizing math, route to sibling skill `spanner-performance-observability-and-sizing`.)*

### Managed Autoscaler (Enterprise & Enterprise Plus ONLY)

Scales instances and geo-partitioning instance partitions automatically (fast scale-up, conservative scale-down to prevent flapping). Exempt from `constraints/spanner.managed.restrictCloudSpannerEditions` blocks on existing instances.

- **Hard Deployment Constraints**:
  - **Min capacity >= 1 node (`1,000` PUs)** — cannot autoscale granular `100`–`900` PU instances!
  - **Max capacity <= 10× Min capacity** (`Min >= 10% of Max`).
  - Allowed target ranges: High-priority CPU **`10%–90%`**, Total CPU **`10%–90%`** (MUST be strictly `> High-priority CPU` when both set), Storage **`10%–99%`**. (Setting Total CPU `<= 60%` leaves headroom for faster background index builds.)
  - **CLI Flag Exclusivity**: Cannot combine `--nodes` with `--autoscaling-min-nodes`, and cannot mix node flags with processing-unit flags.
  - Optional downscale lock (CLI only): `--disable-downscaling` / `--no-disable-downscaling`.
- **Asymmetric Read-Only Replica Autoscaling**:
  - Supported for **read-only replicas ONLY** (NOT RW/witness replicas and NOT geo-partitioning instance partitions).
  - Supports `disable_total_cpu_autoscaling=true` or `disable_high_priority_cpu_autoscaling=true` (**cannot disable both**). Keep CPU targets aligned across replicas so failover traffic does not overload a replica.
  - **Southeast Asia Caveat**: `regional-asia-southeast3` currently lists **no** pre-built optional read-only replica regions; adding an optional RO replica to a Bangkok base config requires submitting an allowlist request.
  ```bash
  # Example: Managed Autoscaler + Asymmetric RO Replica (on a custom config with RO replicas)
  gcloud spanner instances update "{instance_id}" \
    --project="{project_id}" \
    --autoscaling-min-nodes=2 --autoscaling-max-nodes=10 \
    --autoscaling-high-priority-cpu-target=65 --autoscaling-storage-target=75 \
    --asymmetric-autoscaling-option="location={ro_region},min_nodes=1,max_nodes=5,high_priority_cpu_target=65,total_cpu_target=75"
  ```
- **Audit Logging**: Inspect scaling actions in Cloud Logging under `cloudaudit.googleapis.com%2Fsystem_event` with `protoPayload.methodName="AutoscaleInstance"` or `"AutoscaleInstancePartition"`.

### Open-Source Spanner Autoscaler (All Editions — Including Standard & `< 1,000` PUs)

- Deployed on **Cloud Run Functions** or **GKE** (`Poller` + `Scaler` architecture; [cloudspannerecosystem/autoscaler](https://github.com/cloudspannerecosystem/autoscaler)). Supports multi-project management, custom metrics, and granular PU instances (`< 1,000` PUs).
- **3 Scaling Methods**: `stepwise`, `linear` (`currentSize * currentUtil / threshold`), and `direct` (for scheduled batch surges).
- **Tuning & Billing**: Default hysteresis ±5%; default cooldowns **5 min scale-up** / **30 min scale-down**. Because Spanner bills at the **peak compute allocation within each clock hour** (1-hour minimum billing), avoid aggressive sub-hour scale-ins.

## 4. Zero-Downtime Instance Move (`move-instance`) Runbook

Moves an instance across instance configurations (e.g., regional → custom RO, regional → dual/multi-region, or region-to-region) with **zero downtime** and preserved strong consistency. You are billed for **BOTH** source and destination compute capacities during the move (+ cross-region network egress if applicable).

### 10 Mandatory Prerequisites & Guardrails

1. **Capacity & Eligibility**: Source MUST have **>= 1 node (`1,000` PUs)**; **no cross-project moves**; **no Free Trial moves**.
2. **Upgrade Edition First**: Moving Standard → Dual/Multi-region requires upgrading to **Enterprise Plus** before moving.
3. **Disable Managed Autoscaler & Over-Provision**: Switch to manual capacity sized to hold **HP CPU < 40%** and **storage < 1 TiB/node** so the move finishes within 24 hours.
4. **Backup DR Safety Pattern (CRITICAL Blocker)**: `move-instance` FAILS if **ANY** backup or backup schedule (including the **default backup schedule**) exists on the instance!
   - Provision **two 100-PU placeholder instances** (`placeholder-source` in source config + `placeholder-dest` in destination config).
   - Copy critical backups to both placeholders, then delete all schedules (disable default schedule) and backups on the moving instance.
   - Run the move, then recreate schedules on the moved instance and clean up placeholders once verified.
5. **Pause IaC / Terraform CI/CD**: **CRITICAL** — Disable automated `terraform apply` before starting! Terraform treats `config` changes on `google_spanner_instance` as `ForceNew` (destroy + recreate → **catastrophic data loss**). Reconcile Terraform state after the move finishes.
6. **Dataplex / Data Catalog Aspects**: Moving an instance **DELETES** Dataplex entries and custom aspects. Export via `gcloud dataplex entries lookup` before moving, wait **~6 hours** post-move for automatic resync, then re-import with `gcloud dataplex entries update-aspects`.
7. **Regional Service Endpoints**: Clients pinned to a regional endpoint (e.g., `spanner.asia-southeast3.rep.googleapis.com`) break during cross-region moves; clients using the global endpoint (`spanner.googleapis.com`) are unaffected.
8. **CMEK Databases (CLI ONLY)**: CMEK-encrypted databases **cannot** be moved in Cloud Console — use `gcloud spanner instances move --target-database-move-configs=...` providing KMS keys for **EVERY destination region** (including witness and RO replicas) with `roles/cloudkms.cryptoKeyEncrypterDecrypter` granted to Spanner's service agent.
9. **Cancellation Time**: `gcloud spanner operations cancel` is **not** instant — rollback takes approximately **as long as the elapsed move duration**.
10. **Geo-Partitioning & Read Leases**: Moves **ONLY the `default` partition** on geo-partitioned instances; clears `read_lease_regions` (re-enable post-move).

```bash
# Confirm all 10 prerequisites above with the user before executing
gcloud spanner instances move "{instance_id}" \
  --project="{project_id}" \
  --target-config="{target_instance_config}" \
  [--target-database-move-configs="database_id={database_id},kms_key_names={kms_key_1},{kms_key_2}"]
```

## 5. Geo-Partitioning (Preview, Enterprise Plus) & Data Residency

- **Hard Limits & Preview Constraints**:
  - Max **20 instance partitions/instance**; each partition requires **>= 1 node (`1,000` PUs)**.
  - **PREVIEW CONSTRAINT**: Both the `default` partition AND all additional instance partitions MUST use **multi-region** configurations (no dual-region configs; cannot reuse the same base config twice in one instance).
  - Max **100 million placement rows/node**; async row relocation (`UPDATE ... SET location = '...'` via PDML) runs at **~10 placement rows/sec per destination node**.
- **CRITICAL Routing Metadata Default Flip (July 2026)**: The `per_placement_routing_metadata` database option changed default from `FALSE` to **`TRUE` in July 2026** and is **IMMUTABLE once any placement exists**!
  - `TRUE`: Shards routing metadata per partition (optimal for partition-local reads).
  - `FALSE`: Centralizes routing metadata in the `default` partition (optimal for cross-region lookups or colocated default multi-region replicas).
- **Transaction, Backup & Drop Restrictions**:
  - `INSERT`/`DELETE` DML on a placement table MUST be the **sole statement** in its transaction; RW transaction `WHERE` clauses can reference **PK columns only** (use RO txns or PDML for non-PK predicates).
  - **Dropping a Placement Table**: Must delete ALL rows AND wait for the full **`version_retention_period`** to elapse before `DROP TABLE` succeeds!
  - Unsupported: incremental backups, backup copy, named schemas, and `ALTER PLACEMENT` (create new → PDML move → drop old). Full backup restore requires identical partition names & configs on the target instance.

### Geo-Partitioning Secondary Index Matrix

| Index Type | Storage Location | Latency & Residency | Uniqueness & Query Behavior |
| :--- | :--- | :--- | :--- |
| **Global** (default) | `default` instance partition | Cross-region write latency; **no** placement residency | Enforces global uniqueness across all placements |
| **Local** | Interleaved in parent hierarchy | Partition-local R/W; residency preserved | Uniqueness per parent only; requires parent key prefix in `WHERE` |
| **Remote** | Interleaved under `auto_managed_root_placement_table_name` (set once via `ALTER DATABASE`, **immutable**) | Partition-local R/W; residency preserved | Placement key MUST be 1st index column; placement table cols only (no interleaved children) |

- **Local-Probe Heuristic**: Pairing a **Global Unique** index with a **Local/Remote** (or partial PK) index lets queries omit `location` in `WHERE` — the optimizer probes the client's local region shard first and falls back to the Global index on a miss.
- **Data Residency Caveat**: Even with placements, split boundary keys, Key Visualizer/lock/query/optimizer stats, **placement table PKs** (**always use opaque UUIDs — NEVER PII — in placement PKs!**), global indexes, and FK backing indexes may reside in the `default` partition. For strict single-country compliance (e.g., Thailand PDPA), `regional-asia-southeast3` keeps 100% of data, replicas, and metadata in-country.

## 6. Data Protection: PITR, Backups, Incremental Chains & Restore

### Point-in-Time Recovery (PITR)

- Configured per database via `version_retention_period`: **`1h` to `7d`** (default **`1h`**), microsecond precision (`earliest_version_time`).
  ```sql
  -- GoogleSQL
  ALTER DATABASE `{database_id}` SET OPTIONS (version_retention_period = '7d');
  -- PostgreSQL
  ALTER DATABASE {database_id} SET spanner.version_retention_period = '7d';
  ```
- **Caveats**: Does **NOT** protect against accidental database deletion (pair with `--enable-drop-protection` + backups). Longer retention increases storage (stabilizes after `new - old` days), compaction CPU, and DDL throttling under heavy schema churn.

### Backups, Schedules & Incremental Chains

- **Retention & Billing**: Up to **1 year** max retention; billed **24-hour minimum** per backup (e.g., Bangkok `regional-asia-southeast3`: $0.0315/GiB-mo; US reference: $0.03/GiB-mo). Zero CPU impact on source instance; creation takes ~1–4 hours.
- **Backup Exclusion Checklist (Must Re-Apply After Restore!)**:
  1. **IAM policies** (database roles & SQL FGAC grants ARE backed up, but IAM principal bindings are NOT!).
  2. Change stream data records.
  3. Row deletion policies (TTL policies).
  4. Pre-split points and Columnar Engine storage blocks.
  5. `--enable-drop-protection` flag.
- **Schedules**: Max **4 schedules/database**. Full backup schedule (all editions): min interval **12 hours**. Incremental backup schedule (**Enterprise & Enterprise Plus ONLY**): min interval **4 hours** (**schedule-only** — cannot take on-demand incremental backups!). New schedules can take up to **24h** to activate; runs start within a **4-hour window** of `versionTime`. New instances default to `--default-backup-schedule-type=AUTOMATIC` (daily full, 7d retention). **You cannot delete an instance while it holds any backups.**
- **Incremental Backup Chains**: 1 full + up to **13 incrementals** (`incrementalBackupChainId`). A new chain (full backup) is forced when: (a) oldest full backup reaches **>= 28 days** old, (b) newest backup in chain is deleted, (c) schedule is modified, or (d) DB CMEK encryption changes on an inheriting schedule. Expiring an older backup retains referenced blocks (`freeableSizeBytes = 0`) until all newer chain backups expire (base full backup billed up to `min(retention + 28d, retention + interval * 14)`). Cross-region copy automatically deduplicates blocks already present at destination!

### Backup Copy & Restore Mechanics

- **Same-Config Restore Rule**: Backups restore ONLY to a **new database** in the **same GCP project** and **same instance configuration**. Target instance needs `< 10 TiB/node` headroom and a compatible edition (restoring an Enterprise backup using FTS/Vector/Graph/Queues onto a Standard instance **fails**).
- **Restore Lifecycle States**: `CREATING` (max **10 concurrent restores/instance**, ~1 hr, source backup locked) → `READY_OPTIMIZING` (DB readable/writable, slightly elevated read latency, incomplete storage metrics, **source backup STILL locked!**) → `READY` (optimization finished, source backup unlocked).
- **Required IAM Roles**: `roles/spanner.backupAdmin`, `roles/spanner.backupWriter`, `roles/spanner.restoreAdmin`.

```bash
# Example: Create an Enterprise incremental backup schedule (every 6h, 14-day retention)
gcloud spanner backup-schedules create "{schedule_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --database="{database_id}" \
  --cron="0 */6 * * *" \
  --retention-duration="14d" \
  --backup-type=INCREMENTAL

# Optional: Copy backup to another instance (same-region isolation, or cross-region where residency permits)
gcloud spanner backups copy \
  --project="{project_id}" \
  --source-instance="{source_instance_id}" --source-backup="{backup_id}" \
  --destination-instance="{target_instance_id}" --destination-backup="{copied_backup_id}" \
  --expiration-date="2026-12-31T00:00:00Z"
```

## 7. Deletion Protection, Dataplex, IAM Ops, CMEK/EKM & Audit Logs

### Database Deletion Protection (CLI / API ONLY)

Disabled by default; **CANNOT be toggled in Cloud Console** (requires `spanner.databases.update` / `roles/spanner.databaseAdmin`):

```bash
gcloud spanner databases update "{database_id}" --project="{project_id}" --instance="{instance_id}" --enable-drop-protection
gcloud spanner databases update "{database_id}" --project="{project_id}" --instance="{instance_id}" --no-enable-drop-protection
gcloud spanner databases describe "projects/{project_id}/instances/{instance_id}/databases/{database_id}"
```

- **4 Critical Gotchas**: (1) Cannot enable while DB is restoring or deleting; (2) Restored DBs do **NOT** inherit protection; (3) Does **NOT** protect against GCP project deletion or Free Trial 90+30-day expiration; (4) Must disable on **every** database before deleting the parent instance.

### Dataplex Universal Catalog Integration

Auto-syncs instances, databases, tables, columns, and views every **~6 hours** into the `@spanner` entry group (FQN: `spanner:{project_id}.{instance_config}.{instance_id}.{database_id}.{table_name}`). **Gotchas**: No FGAC support; `move-instance` deletes entries & custom aspects.

### Operational IAM Custom-Role Permission Bundles

Policy hierarchy: Project → Instance → Database → Backup / Backup Schedule (Database Roles support `testIamPermissions` only).

- **SQL Read**: `spanner.databases.select`, `spanner.sessions.create`, `spanner.sessions.delete` (+ `beginReadOnlyTransaction` for RO txns; + `partitionQuery`; + `useDataBoost`).
- **Read API**: `spanner.databases.read`, `spanner.sessions.create`, `spanner.sessions.delete` (+ `partitionRead`).
- **RW Write**: `spanner.databases.beginOrRollbackReadWriteTransaction`, `spanner.databases.write`, `spanner.sessions.create`, `spanner.sessions.delete` (+ `select`/`read` if reading).
- **PDML**: `spanner.databases.beginPartitionedDmlTransaction`, `spanner.databases.write`, `spanner.sessions.create`, `spanner.sessions.delete`.
- **Backup / Copy / Restore**: Create = `backups.create` + `databases.createBackup`; Copy = `backups.copy` (source) + `backups.create` (dest); Restore = `backups.restoreDatabase` + `databases.create`.
- **Special Ops**: `spanner.databases.changequorum` (dual-region failover), `spanner.databases.adapt` (Adapter API), `spanner.databases.runGraphAlgorithms`, `spanner.databases.addSplitPoints`, `CALL cancel_query` (needs `spanner.sessions.delete`).

### CMEK & External Key Manager (EKM)

- **3-Layer Envelope Encryption**: Chunk → DEK → KEK → CMEK. Zero performance/SLA impact; zero Spanner surcharge (Cloud KMS fees apply). Key rotation re-encrypts KEKs only. Not encrypted by CMEK: split boundary keys, debugging logs/dumps, in-transit/in-memory data, DB metadata.
- **Autokey Limitation**: Terraform or REST API only; cannot create multiple regional KMS keys for one database.
- **Zero-Downtime Key Changes**: Enable CMEK, swap KMS keys, or revert to Google-managed in-place (hours of background re-encryption; brief retryable `ABORTED` txns). **Does NOT re-encrypt existing backups** (keep old keys until backups expire!). Forces a new incremental backup chain on inheriting schedules.
- **Key Revocation & 7-DAY AUTO-DELETION WARNING**:
  - Disable/destroy propagation: up to **3 hours** (Spanner polls KMS every **~5 min**). All keys disabled → immediate lockdown (`FAILED_PRECONDITION`). KMS/EKM unreachable → best-effort up to **1 hour**, then offline.
  - **Partial Regional Key Revocation**: Disabling a subset of regional keys disables the DB over **up to 12 hours** (uncertain behavior). Create a **2-condition AND Cloud Monitoring alert** on `spanner.googleapis.com/instance/replica/cmek/total_keys` (grouped by `database`, 10m rolling mean, 1h retest): `is_key_revoked=TRUE > 0` **AND** `is_key_revoked=FALSE > 0`.
  - **CRITICAL PERMANENT DATA LOSS RULE**: A CMEK/EKM database disabled for **> 7 consecutive days** is **AUTOMATICALLY AND PERMANENTLY DELETED**!
- **Geo-Partitioning + CMEK**: Multi-region KMS keys are unsupported — MUST supply a **regional KMS key in EVERY instance replica region AND EVERY instance partition region**.

### Cloud Audit Logging Caveat

- `DATA_READ` / `DATA_WRITE` logs (`cloudaudit.googleapis.com%2Fdata_access`) include `protoPayload.metadata.processingDurationSeconds` (API frontend first byte in → last byte out).
- **CRITICAL GOTCHA**: `processingDurationSeconds` is **NOT populated** for streaming or partition RPCs: `ExecuteStreamingSql`, `StreamingRead`, `PartitionRead`, and `PartitionQuery`!

## 8. Enterprise POC Validation & Load Testing Rigor

Before certifying a production cutover or regional topology:

- **Quantified Pass/Fail Rubric**: Lock target SLAs upfront for Availability SLA, Security/CMEK/EKM/FGAC, RPO/RTO, p50/p95/p99 latencies at peak QPS, and scale-up/scale-down elasticity.
- **Bulk Data Loading Selection**:
  - Small MySQL / PostgreSQL migrations → **Spanner Migration Tool (SMT)**.
  - Large JDBC / CSV / Avro / Parquet loads → **Dataflow Templates**.
  - Large CSV / Avro / JSON needing SQL transforms → **BigQuery Reverse ETL** (`EXPORT DATA` into Spanner).
- **Load Testing Discipline**: Use **Machmeter** (JMeter-based) or **QuickPerf**. Always run a **>= 30-minute warm-up** (allowing load-based splitting to stabilize), execute steady-state load for **> 1 hour** (to capture background rebalancing and compaction), and verify both **scale-up and scale-down** behavior.

## Official Documentation

- [Instance Configurations & Replication](https://docs.cloud.google.com/spanner/docs/instance-configurations)
- [Read Leases](https://docs.cloud.google.com/spanner/docs/read-lease)
- [Managed & Open-Source Autoscaling](https://docs.cloud.google.com/spanner/docs/autoscaling-overview)
- [Move a Spanner Instance](https://docs.cloud.google.com/spanner/docs/move-instance)
- [Geo-Partitioning & Data Residency](https://docs.cloud.google.com/spanner/docs/geo-partitioning)
- [PITR, Backups & Restore](https://docs.cloud.google.com/spanner/docs/backup)
- [Prevent Database Deletion](https://docs.cloud.google.com/spanner/docs/prevent-database-deletion)
- [CMEK & Cloud Audit Logging](https://docs.cloud.google.com/spanner/docs/cmek)
