# Enterprise Sizing, Benchmarks, Tiered Storage & Regional TCO

Authoritative reference for Cloud Spanner peak per-node throughput, `max_commit_delay` write batching, DML/mutation/service quotas, compute & storage capacity limits, Tiered Storage (SSD/HDD), Columnar Engine, failover CPU & autoscaler target tuning, verified Bangkok (`asia-southeast3`) single-region 2026 pricing (with reference regional rates), worked node-sizing math, and 3-store consolidation TCO modeling.

---

## 1. Peak Throughput Benchmarks & `max_commit_delay`

### Verified Peak Throughput per Node (1,000 PU, 100% CPU, 1 KB Single-Row Ops)

| Configuration Type | Peak Reads (QPS / region / node) | Peak Standard Writes (QPS total / node) | Peak Writes w/ Batching (`100 ms` delay, QPS total / node) |
| :--- | :--- | :--- | :--- |
| **Regional** (e.g., `regional-asia-southeast3` Bangkok, `regional-us-central1` Iowa) | **SSD: 22,500**<br>HDD: 1,500 | **SSD: 3,500**<br>HDD: 3,500 | **SSD: 22,500**<br>HDD: 22,500 |
| **Regional Optional Read-Only Replica** | **SSD: +7,500**<br>HDD: +500 | — | — |
| **Dual-Region & Multi-Region** (e.g., `dual-region-japan1`, `asia1`, `asia2`, `nam3`, `eur6`) | **SSD: 15,000** (7,500 per optional RO replica or 1-replica regions)<br>HDD: 1,000 (500 RO) | **SSD: 2,700** *(3-continent e.g. `nam-eur-asia1`: 1,500)*<br>HDD: 2,700 | **SSD: 15,000**<br>HDD: 15,000 |

- **Linear Scaling**: Both read and write throughput scale linearly at `>= 1 node (1,000 PU)` (e.g., 4 regional nodes yield 90,000 peak SSD reads/s at 100% CPU).
- **Benchmarking Caveat**: Peak numbers reflect pure single-row 1 KB read-only or write-only traffic at 100% CPU. Always validate production sizing with workload-specific benchmarks (e.g., PerfKit Benchmarker / YCSB via `https://github.com/cloudspannerecosystem/spanner-benchmarks-tutorial`) reflecting actual schema, row size, key distribution, read/write mix, and query complexity.

### Throughput-Optimized Writes (`max_commit_delay`)

- **Mechanism**: Adds a bounded commit delay on `CommitRequest` (RPC/REST/SDK) to batch concurrent commits hitting the same voting Paxos participants, amortizing replication CPU (**up to ~6.4× regional write QPS**: `3,500 → 22,500` QPS/node).
- **Valid Range**: **`0 ms` to `500 ms`** (`> 500 ms` returns an error).
- **Tuning Guidance**:
  - **Unset (default)**: Spanner may apply a small dynamic delay on bursty workloads.
  - **Latency-critical OLTP**: Set explicitly to `0 ms` to disable batching delay.
  - **Throughput-oriented / bulk ingest**: Start at **`100 ms`** for peak batched write throughput; most production OLTP services tune between **`20 ms` and `100 ms`**.
  - **Mixed Delay Rule**: When concurrent commits in a batch carry different `max_commit_delay` values, Spanner honors the **shortest delay** in the group—standardize values per workload tier for predictable batching.

---

## 2. DML, Mutation & Core Service Quotas

| Constraint | Exact Limit & Operational Nuance |
| :--- | :--- |
| **Mutations per DML statement** | **80,000** (resets per statement! Multiple DML statements in one RW txn may exceed 80,000 total until hitting the 100 MiB commit cap). Exceeding returns `BadUsage`. |
| **Mutations per Commit (Mutation API)** | **80,000** cumulative across all mutations in the commit (including if mixed with DML), and 80,000 per mutation group in `BatchWrite`. |
| **Transaction / Commit Size** | **100 MiB** hard cap (includes secondary index entries and change stream writes); applies to all transactions including multi-DML transactions. |
| **Mutation Multiplicity Formula** | `INSERT`/`UPDATE` count every written column + all PK columns + generated/default columns + every affected secondary/FK backing index entry. `DELETE` / `DELETE RANGE` / `ON DELETE CASCADE` count as **1 mutation**, *except* every deleted secondary index row counts individually. Change streams add **0** mutations toward this limit. |
| **RW Execution & Visibility** | SQL statements inside a RW transaction run **sequentially** (only multiple `SELECT`s may run concurrently). DML changes are **immediately visible** to later statements with per-statement constraint checks, whereas Mutation API writes buffer until commit. |
| **Returning & Batch DML** | GoogleSQL: `THEN RETURN`; PostgreSQL: `RETURNING` (executed via query API, not `execute_update`). Use `ExecuteBatchDml` to amortize serial network RTTs for multiple DML statements in one RW transaction. |
| **Partitioned DML (PDML)** | Bulk backfill/cleanup (`--enable-partitioned-dml`). Cap: **20,000 concurrent PDML statements/DB**. **Gotcha**: `--query-mode=PLAN` and `--query-mode=PROFILE` are **unsupported** for PDML. |
| **`PENDING_COMMIT_TIMESTAMP()` Trap** | Calling `PENDING_COMMIT_TIMESTAMP()` (GSQL) or `SPANNER.PENDING_COMMIT_TIMESTAMP()` (PG) makes that table and its indexes **unreadable for all subsequent statements in the same transaction**—always execute it last! |
| **Query, Schema & Data Boost Limits** | Query text: 1M chars; Params: 950; Joins: 20 (**100** for graph queries); `IN` list: 10,000; `GROUP BY` cols: 1,000; Unions: 200; Tables/DB: 5,000; Indexes/DB: 10,000 (128/table); PK/Index cols: 16 (max 8 KiB key); Interleaving depth: 7; Cell max: 10 MiB; Row non-key max: 1,600 MiB; Concurrent reads/session: 100; Non-commit request size: 10 MiB; Admin API rate: 5/sec/project/user (100s avg); **Data Boost concurrent requests/project/region**: **400** in `asia-southeast3` / `asia-southeast1` (**1,000** in `us-central1`). |

---

## 3. Compute Capacity, Storage Limits, Tiered Storage & Columnar Engine

### Compute Capacity & Granular (`< 1,000 PU`) Caveats

- **Provisioning Increments**: `< 1,000 PU` in steps of **100 PU** (`100..900`); `>= 1,000 PU` in steps of **1,000 PU** or integer **nodes** (`1 node = 1,000 PU`).
- **Full Per-Zone Replication**: Provisioned PUs are allocated **in full in every zone** of the instance configuration (a 1-node regional instance runs 1,000 PU in each of 3 zones—not divided by 3).
- **Sub-Node (`100–900 PU`) Production Risks**:
  - Instances `<= 1,000 PU` allocate a **single server task per zone** (`>= 2,000 PU` allocate 1 task per 1,000 PU/zone, unlocking multi-task split distribution).
  - Sub-node instances exhibit **non-linear scaling** and latency jitter under bursty loads and **cannot use Managed Autoscaler**. Always enforce **`>= 1 node (1,000 PU)`** for production.
  - **Object Scaling**: **100 DBs** and **1,000,000 schema objects** at `>= 1 node`; scaled linearly to **10 DBs** and **100,000 schema objects per 100 PU** below 1 node (`spanner.googleapis.com/instance/schema_objects`).
  - **No Pause/Suspend**: Instances have no stop/suspend mode; dedicated compute bills continuously until deleted.

### Storage Capacity, Multi-Version Overhead & Scale-Down Blockers

- **Hard Storage Allotment** (binary units: `1 GiB = 2^30`, `1 TiB = 1,024 GiB`):
  - **`>= 1 node`**: **10 TiB per node** (combined SSD + HDD under Tiered Storage).
  - **`< 1 node`**: **1,024 GiB (1 TiB) per 100 PU**.
  - **Recommended Alert Threshold**: **75%** (`7.5 TiB/node` = `8,246,337,208,320 bytes/node`).
  - **Billing & Backups**: Billed strictly on **actual utilized storage**, not allotted capacity. Backups are billed separately, have no size cap (max retention **1 year**), and **do not count** toward instance storage limits.
- **Approaching/Exceeding Limit**: Spanner blocks backup restores, schema changes (including index creation), and compute scale-downs; exceeding risks write rejections.
- **Multi-Version & Split/Merge Inflation**: Deleted/updated versions remain billed during `version_retention_period` (**1h default, up to 7d** PITR) plus background compaction (~12h for large deletes, up to 7d otherwise). Active splitting/merging can hold **up to 2× temporary storage** for moving ranges for ~2 weeks (reclaim immediately on Enterprise/Enterprise Plus via `CALL compact_all();`).
- **Scale-Down Blockers**: Scale-down fails if remaining capacity violates `10 TiB/node` OR active **split count** exceeds the target node count's ceiling (step down gradually or wait 1–2 weeks for cold splits to merge).

### Tiered Storage (SSD + HDD) — Enterprise & Enterprise Plus Only

- **Policy Mechanics**: Configured via **locality groups** (max **16 per DB**: `default` + 15 custom; `ssd_to_hdd_spill_timespan` range: **1 hour to 365 days**). Aging is **cell-level** by commit timestamp; reads do **not** reset age, while updates write fresh versions directly to SSD.
- **Critical Gotchas**:
  1. **No Interleaved Inheritance**: Interleaved child tables do **not** inherit parent locality groups—configure explicitly per table.
  2. **Search & Vector Inheritance**: Search and Vector indexes inherit the **database-level** default locality group.
  3. **HDD Disk Load Ceiling (`spanner.googleapis.com/instance/disk_load`)**: SSD reads add `0` HDD disk load; HDD reads consume per-node disk load (`1,500 QPS/node` regional vs. `22,500` SSD). Saturating 100% `disk_load` triggers severe latency spikes—add nodes or return hot columns/indexes to SSD. (Data Boost uses separate HDD disk load capacity.)
  4. **CRITICAL WRITE EXISTENCE-CHECK TRAP**: Row-existence metadata resides in the **table's** locality group. Applying an HDD/age-based policy at the table or DB level moves existence metadata to HDD—forcing `INSERT`, `INSERT OR IGNORE`, `ON CONFLICT DO NOTHING`, and partial `ON CONFLICT DO UPDATE` to **read from HDD** during uniqueness checks (severely amplified on interleaved child tables, which lack existence filters!).
     - **Fix**: Keep the table on an **SSD-only** locality group and assign only large cold non-key columns to the HDD/age-based locality group—OR use full-row `INSERT OR UPDATE` / `InsertOrUpdate` mutations / full-row PK `ON CONFLICT DO UPDATE` (without `WHERE`, updating all columns to `EXCLUDED`), which bypass existence reads.
  5. **Edition Restore Lock**: Restoring a backup containing locality groups requires an **Enterprise** or **Enterprise Plus** target instance.
  6. **Observability**: Query `SPANNER_SYS.TABLE_SIZES_STATS_1HOUR`, `SPANNER_SYS.TABLE_SIZES_STATS_PER_LOCALITY_GROUP_1HOUR`, `INFORMATION_SCHEMA.LOCALITY_GROUP_OPTIONS`, and `AVG_DISK_IO_COST`. Remove any legacy `storage_class:ssd` filter on `storage/used_bytes` alerts.

### Columnar Engine — Enterprise & Enterprise Plus Only

- **Capabilities**: Accelerates analytical scans up to **200×** over live operational data without ETL while preserving strong consistency (builds columnar runs asynchronously during compactions, merges recent PAX **Ressi** writes at query time).
- **Cost & Operational Rules**: Billed on incremental columnar storage (exempt from 8-byte/cell overhead); **excluded from backups**; project only required columns (avoid `SELECT *`); verify coverage via `AVG_COLUMNAR_READ_SHARE`; isolate heavy analytical scans with **Directed Reads** (to RO replicas) or **Data Boost**.

---

## 4. Failover Headroom, Autoscaler Targets & Editions Capacity

### Failure-Domain CPU Headroom Thresholds

| Metric | Regional (3-Zone Quorum) | Dual-Region / Multi-Region (Per Region) |
| :--- | :---: | :---: |
| **High-Priority CPU Target Max** | **65%** | **45%** |
| **24-Hour Smoothed Aggregate CPU Max** | **90%** | **90%** |

*Rationale*: Surviving 1 lost zone in a 3-zone regional quorum leaves `66.7%` compute (`<= 65%` target); surviving 1 lost read-write region leaves `50%` regional capacity (`<= 45%` target).

### Managed Autoscaler Target Tuning (Enterprise & Enterprise Plus)

*(For full autoscaler deployment lifecycle, edition upgrades, and asymmetric RO topology management, route to `spanner-enterprise-platform-ops`.)*

- **Dimensions & Hard Constraints**: Computes the **maximum** node count across High-priority CPU (`10–90%`), Total CPU (`10–90%`), and Storage utilization (`10–99%`). Scales down slower than up to preserve tail latency.
  - Hard floor: **`min >= 1 node (1,000 PU)`** (no sub-node autoscaling) and **`max <= 10 × min`** (e.g., `max = 40` requires `min >= 4`).
  - When both CPU targets are set: **`high_priority_cpu_target < total_cpu_target`** strictly.
  - Disable Managed Autoscaler before running `move-instance`.
  - **Asymmetric RO Failover Rule**: Configure **identical CPU target types** across all asymmetric read-only replicas so failover onto a replica lacking `total_cpu_target` does not starve background/analytical tasks.

| Workload Profile | Regional Targets | Multi-Region Targets | Trade-off |
| :--- | :--- | :--- | :--- |
| **Reliable Failover Default** | Total: **70%**, High-Pri: **65%** | Total: **50%**, High-Pri: **45%** | Balanced throughput + full zonal/regional failover headroom |
| **Throughput-Sensitive, Write-Heavy** | Total: **70%** | Total: **50%** | Higher sustained write throughput at slight latency trade-off |
| **Latency-Sensitive, Read-Heavy** | Total: **80%**, High-Pri: **65%** | Total: **80%**, High-Pri: **45%** | Tight predictable tail latency at higher compute cost |
| **Cost-Efficiency Priority** | Total: **85%**, High-Pri: **65%** | Total: **85%**, High-Pri: **45%** | Lowest cost; may delay index backfill (lower Total to `<= 60%` during builds) |

- **CLI Flag Precision Warning**: Use **`-target`** flags (not `-percent`!). Never combine `--nodes`/`--processing-units` with `--autoscaling-*`, and never mix node and PU autoscaling flags. Track autoscale events in Audit Logs via `protoPayload.methodName="AutoscaleInstance"`.

```bash
# Confirm before executing against live GCP project (Bangkok Enterprise regional example)
gcloud spanner instances create "{instance_id}" \
  --project="{project_id}" \
  --edition=ENTERPRISE \
  --config=regional-asia-southeast3 \
  --description="Enterprise Production OLTP" \
  --default-backup-schedule-type=AUTOMATIC \
  --autoscaling-min-nodes=2 \
  --autoscaling-max-nodes=10 \
  --autoscaling-high-priority-cpu-target=65 \
  --autoscaling-total-cpu-target=70 \
  --autoscaling-storage-target=75
```

### Editions Capacity & Cost-Lever Summary

| Capability | Standard | Enterprise | Enterprise Plus |
| :--- | :--- | :--- | :--- |
| **Availability SLA** | 99.99% | 99.99% | **Up to 99.999%** |
| **Topologies** | Regional | Regional + Optional custom RO replicas | Regional, **Dual-region, Multi-region** + Optional RO |
| **Performance & Storage Levers** | Open-source Autoscaler, Data Boost, BigQuery federation | + **Managed Autoscaler**, **Asymmetric RO**, **Tiered Storage (SSD/HDD)**, **Columnar Engine**, **`CALL compact_all()`**, **Incremental Backups**, **Spanner Graph**, **FTS & Vector Search** | + **Geo-partitioning** (20 partitions/instance, 50 placements/DB, 100M placement rows/node) |
| **Observability & Pre-Splitting** | Full Insights, `SPANNER_SYS`, Key Visualizer, Index Advisor, Pre-splitting | Full | Full |
| **CUD Discounts** | 20% (1-yr) / 40% (3-yr) | 20% (1-yr) / 40% (3-yr) | 20% (1-yr) / 40% (3-yr) |

*(Edition upgrades/downgrades complete in ~10 minutes with zero downtime and zero migration; audit feature usage before downgrading via `spanner.googleapis.com/instance/edition/feature_usage`.)*

---

## 5. Bangkok (`asia-southeast3`) Single-Region Pricing & 3-Store TCO

### Regional Topology & Companion Service Roles

- **Primary & ONLY Database/App Region — Bangkok, Thailand (`asia-southeast3` / `regional-asia-southeast3`)**: Single regional Cloud Spanner **Enterprise** instance with 3-zone synchronous Paxos quorum (`asia-southeast3-a`, `asia-southeast3-b`, `asia-southeast3-c`), **99.99% SLA**, Thailand PDPA data residency, **`$0.00/GiB`** intra-region replication, co-located Cloud Run, user-managed Secret Manager, GCS staging, Dataflow, and same-region 7-day PITR + incremental backups.
- **Companion Region ONLY for Unavailable Services — Singapore (`asia-southeast1`, ~25 ms RTT)**:
  - **No Pre-Built Dual-Region**: Cloud Spanner does not offer a pre-built dual-region or multi-region pairing `asia-southeast3` + `asia-southeast1` (dual-regions cover AU/CA/DE/IN/JP; `asia2` pairs Mumbai/Delhi/Singapore).
  - **Strictly Companion AI & Security Services**: Route to `asia-southeast1` **only** for GCP services unavailable in Bangkok: (1) Vertex AI embeddings & GenAI (`gemini-embedding-001`, `gemini-2.5-flash`); (2) Model Armor (`modelarmor.asia-southeast1.rep.googleapis.com`); and (3) Cloud DLP / Sensitive Data Protection companion inspection templates—not a second Spanner DR instance.

### Verified 2026 Pricing Schedule (USD, 730 Hours/Month)

| Pricing Dimension | `us-central1` (1.00×) | `asia-southeast3` Bangkok Primary (**1.05×**) | `asia-southeast1` Reference APAC (**1.144×**) |
| :--- | ---: | ---: | ---: |
| **Standard Compute** (node-hr / node-mo) | `$0.9000` / `$657.00` | **`$0.9450` / `$689.85`** | `$1.0296` / `$751.61` |
| **Enterprise Compute** (node-hr / node-mo) | `$1.2300` / `$897.90` | **`$1.2915` / `$942.80`** | `$1.40712` / `$1,027.20` |
| **Enterprise Plus Compute** (node-hr / node-mo) | `$1.7100` / `$1,248.30` | **`$1.7955` / `$1,310.72`** | `$1.95624` / `$1,428.06` |
| **Enterprise 1-Yr CUD (-20%)** (node-hr / node-mo) | `$0.9840` / `$718.32` | **`$1.0332` / `$754.24`** | `$1.12570` / `$821.76` |
| **Enterprise 3-Yr CUD (-40%)** (node-hr / node-mo) | `$0.7380` / `$538.74` | **`$0.7749` / `$565.68`** | `$0.84427` / `$616.32` |
| **Standard 1-Yr / 3-Yr CUD** (node-hr) | `$0.7200` / `$0.5400` | **`$0.7560` / `$0.5670`** | `$0.82368` / `$0.61776` |
| **Enterprise Plus 1-Yr / 3-Yr CUD** (node-hr) | `$1.3680` / `$1.0260` | **`$1.4364` / `$1.0773`** | `$1.56499` / `$1.17374` |
| **Optional RO Replica — Enterprise** (node-hr / node-mo) | `$0.4100` / `$299.30` | **`$0.4305` / `$314.27`** | `$0.46904` / `$342.40` |
| **Optional RO Replica — Ent. Plus** (node-hr) | `$0.5700` | **`$0.5985`** | `$0.65208` |
| **SSD Storage** (per GiB-month) | `$0.3000` | **`$0.3150`** | `$0.34320` |
| **HDD Storage** (per GiB-month, **5× cheaper**) | `$0.0600` | **`$0.0630`** | `$0.06864` |
| **Backup Storage** (per GiB-month) | `$0.0300` | **`$0.0315`** | `$0.03432` |
| **Data Boost** (per 1,000 SPU-hour) | `$1.1700` | **`$1.2285`** | `$1.33848` |
| **Intra-Region Replication & Same-Region Backups** | `$0.0000 / GiB` | **`$0.0000 / GiB`** | `$0.0000 / GiB` |
| **Dev/Sandbox (100 PU × 8 hrs, Enterprise)** | `$0.9840` | **`$1.0332`** | `$1.1257` |

### Multi-Model Storage Estimator (Graph + FTS + Vector + Relational)

Estimate total logical storage before applying the `7.5 TiB/node` (75%) node floor:
1. **Base Relational / Graph Tables**: `(node_rows × avg_node_bytes) + (edge_rows × avg_edge_bytes) + (chunk_rows × avg_chunk_text_bytes)`.
2. **Reverse Covering `STORING` Indexes**: `~2.0×` base edge bytes (covers reverse traversals without base-table back joins).
3. **Full-Text Search `TOKENLIST` + Search Index**: `~0.5×` indexed text bytes.
4. **Vector Embeddings (`ARRAY<FLOAT32>`)**: `chunk_rows × vector_dim × 4` bytes (e.g., `768 × 4 = 3,072` bytes/chunk) + **ScaNN Vector Index**: `~1.3×` raw vector bytes.
5. **Tiered Split**: Partition `total_gib` across SSD (`1 - cold_hdd_pct`) and HDD locality group (`cold_hdd_pct`).

### Worked Enterprise Sizing & Optimization Model (`regional-asia-southeast3`)

**Workload**: Regional OLTP + ledger in Bangkok (`Enterprise`, 99.99% SLA): **50,000 SSD reads/s**, **4,000 SSD writes/s** (1 KB rows), **12 TiB (`12,288 GiB`)** total storage (`4 TiB` hot active + `8 TiB` cold > 30d), targets **65% HP CPU** / **75% storage (`7.5 TiB/node`)**:

1. **CPU Sizing (Standard Writes)**: Effective regional SSD capacity at 65% CPU = `14,625` reads/node and `2,275` standard writes/node → `50,000 / 14,625 + 4,000 / 2,275 = 3.42 + 1.76 = 5.18` → **6 nodes**.
2. **Storage Sizing**: `12 TiB / 7.5 TiB = 1.6` → **2 nodes**. Provision `max(6, 2, 1) = 6 nodes`.
3. **Three Optimization Levers**:
   - **`max_commit_delay = 100 ms`** on write-heavy paths lifts effective batched writes to `14,625` QPS/node → `(50,000 + 4,000) / 14,625 = 3.69` → **4 nodes** (-2 nodes).
   - **Tiered Storage (`4 TiB` SSD + `8 TiB` HDD)**: Cuts storage from `$3,870.72` to `$1,806.34/mo` (**-53.3%**).
   - **3-Year CUD (-40% compute)**: Cuts Enterprise compute from `$942.80` to `$565.68/node-mo`.

| Cost Component (`regional-asia-southeast3`, Enterprise) | Baseline (6 Nodes OD, 12 TiB All-SSD) | Optimized (4 Nodes 3-Yr CUD, 4 TiB SSD + 8 TiB HDD) | Monthly Delta |
| :--- | ---: | ---: | ---: |
| **Compute** | `$5,656.77` | `$2,262.71` | `-$3,394.06` (-60.0%) |
| **Database Storage (12 TiB)** | `$3,870.72` | `$1,806.34` | `-$2,064.38` (-53.3%) |
| **Total Core Monthly TCO** | **`$9,527.49 / mo`** | **`$4,069.04 / mo`** | **`-$5,458.45 / mo` (-57.3%)** |
| *Same-Region Bangkok Backups (12 TiB)* | *`+$387.07/mo` (`$0.0315/GiB-mo`, `$0` egress)* | *`+$387.07/mo` (lower w/ Enterprise incremental backups)* | *Same-region recovery* |
| *Optional: 1 Additional RO Replica (4 nodes)* | — | *`+$1,257.06/mo` (`+30,000` SSD read QPS)* | *Read isolation* |

### 3-Store Consolidation TCO Comparison Framework

When building executive TCO cases for consolidating fragmented Graph + Vector + Keyword Search platforms onto Cloud Spanner Enterprise, account for all six total-cost pillars—not raw database compute alone:

| Cost & Operational Pillar | Fragmented 3-Store Stack (HA Graph DB + Managed Vector DB + Search Cluster) | Consolidated Cloud Spanner Enterprise (`regional-asia-southeast3`) |
| :--- | :--- | :--- |
| **Base Compute & Licensing** | 3 separate HA clusters (minimum 3-node quorums × 3 engines) + commercial Graph/Search enterprise licenses | Single unified 3-zone synchronous compute pool (starts at 1 node / `1,000 PU`; 20%/40% CUDs) |
| **Data Duplication & Storage** | 3× duplicated payloads + separate backups + cross-store replication buffers | Single copy of source rows + compact co-located covering/FTS/ScaNN indexes + 5× cheaper HDD tiering |
| **CDC & Sync Pipelines** | Dedicated Kafka/PubSub/Dataflow/Debezium glue jobs, DLQ replay, and schema drift reconciliation | **Zero ETL/CDC** between transactional, graph, FTS, and vector indexes—single ACID commit |
| **Consistency & Stale Reads** | Eventual consistency across stores (dangling edges, unindexed vectors, ghost search hits) | **External consistency (TrueTime)** across relational, ISO GQL, FTS, and vector reads |
| **Security & Governance** | 3 independent RBAC/IAM models, 3 audit streams, 3 CMEK/VPC-SC perimeters | Unified IAM + FGAC roles, single Cloud Audit Log stream, single PDPA residency boundary |
| **SRE & Incident Overhead** | 3 upgrade cycles, 3 monitoring stacks, cross-store incident triage | Unified `SPANNER_SYS` Insights, Key Visualizer, Managed Autoscaler, zero-downtime upgrades |

---

## Official Public Documentation

- Cloud Spanner Pricing: https://cloud.google.com/spanner/pricing
- Performance Overview & Throughput Benchmarks: https://docs.cloud.google.com/spanner/docs/performance
- Throughput-Optimized Writes (`max_commit_delay`): https://docs.cloud.google.com/spanner/docs/throughput-optimized-writes
- Quotas & Limits: https://docs.cloud.google.com/spanner/quotas
- Tiered Storage & Locality Groups: https://docs.cloud.google.com/spanner/docs/tiered-storage
- Columnar Engine: https://docs.cloud.google.com/spanner/docs/columnar-engine
- Managed Autoscaler Targets & CPU Utilization: https://docs.cloud.google.com/spanner/docs/autoscaling-overview
- Spanner Benchmarks Tutorial (PerfKit / YCSB): https://github.com/cloudspannerecosystem/spanner-benchmarks-tutorial
