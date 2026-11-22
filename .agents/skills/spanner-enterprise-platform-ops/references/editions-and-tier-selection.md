# Cloud Spanner Editions, Pricing, Governance & Tier Selection

Self-contained reference for selecting between Cloud Spanner **Standard**, **Enterprise**, and **Enterprise Plus** editions, evaluating regional pricing and Southeast Asia topology constraints, managing upgrades/downgrades, enforcing Organization Policies, sizing Committed Use Discounts (CUDs), operating Free Trial instances, and respecting compute/storage/feature limits.

## 1. Editions Feature Matrix

Cloud Spanner editions govern feature access and maximum availability SLA at the **instance** level. Standard retains features GA prior to **September 24, 2024**, plus Reverse ETL and Scheduled Backups.

| Capability | Standard | Enterprise | Enterprise Plus |
| :--- | :--- | :--- | :--- |
| **Max Availability SLA** | 99.99% (Regional only) | 99.99% (Regional + custom RO replicas) | Up to **99.999%** (Dual-region & Multi-region) |
| **Instance Configurations** | Regional only | Regional + optional custom RO replicas | Regional, Custom RO, **Dual-region**, **Multi-region** |
| **Core Models & Engines** | Relational (GoogleSQL & PostgreSQL), Key-Value | All Standard + **Spanner Graph** (GoogleSQL only) | All Enterprise |
| **Search & AI** | Vertex AI remote models (core SQL), BigQuery Federation | All Standard + **Full-Text Search**, **Vector Search (KNN & ANN)** | All Enterprise |
| **Messaging & CDC** | Change Streams | All Standard + **Spanner Queues** | All Enterprise |
| **Analytics & Storage** | Data Boost, BigQuery Federation, Reverse ETL (BQ → Spanner) | All Standard + **Columnar Engine**, **Locality Groups & Tiered Storage (SSD/HDD)** | All Enterprise |
| **Autoscaling** | Open-source Autoscaler only | All Standard + **Managed Autoscaler**, **Asymmetric RO Autoscaling** | All Enterprise |
| **Backups & Recovery** | Standard (full) backups, 7-day PITR, Scheduled backups | All Standard + **Incremental backups** | All Enterprise |
| **Global Data Placement** | — | — | **Geo-partitioning** (Preview) |
| **Committed Use Discounts** | Eligible (20% 1-yr / 40% 3-yr on compute) | Eligible (20% 1-yr / 40% 3-yr on compute) | Eligible (20% 1-yr / 40% 3-yr on compute) |

### Tier Selection Rules of Thumb

- **Standard**: Pure regional OLTP (relational or KV), Change Streams, Data Boost, or BigQuery Federation without Graph, FTS, Vector Search, Queues, Tiered Storage, Columnar Engine, Incremental Backups, or Managed Autoscaler.
- **Enterprise**: Default choice for regional AI/Search/Graph/Queue platforms, tiered SSD/HDD storage, incremental backups, or Managed Autoscaler (99.99% SLA, single-region data residency).
- **Enterprise Plus**: Required strictly for Dual-Region or Multi-Region configurations (99.999% SLA) or Geo-Partitioning (Preview). Do not pay the Enterprise Plus surcharge if regulatory residency confines your instance to a single regional configuration.

## 2. Verified Regional Pricing & Southeast Asia Architecture Reference

Pricing verified against [Cloud Spanner Pricing](https://cloud.google.com/spanner/pricing) (USD, 2026; monthly compute calculated at 730 hours/month):

| Pricing Dimension | Bangkok Primary (`regional-asia-southeast3`, 1.05× US) | Singapore Secondary (`regional-asia-southeast1`, 1.144× US) | Reference US (`regional-us-central1`, 1.00×) |
| :--- | :--- | :--- | :--- |
| **SLA & Topology** | 99.99% (3 zones: `asia-southeast3-{a,b,c}`) | 99.99% (3 zones: `asia-southeast1-{a,b,c}`) | 99.99% (3 zones) |
| **Standard Compute** (node-hr / mo) | $0.945 / hr ($689.85 / mo) | $1.0296 / hr ($751.608 / mo) | $0.90 / hr ($657.00 / mo) |
| **Enterprise Compute** (node-hr / mo) | $1.2915 / hr ($942.795 / mo) | $1.40712 / hr ($1,027.1976 / mo) | $1.23 / hr ($897.90 / mo) |
| **Enterprise Plus Compute** (node-hr / mo) | $1.7955 / hr ($1,310.715 / mo) | $1.95624 / hr ($1,428.0552 / mo) | $1.71 / hr ($1,248.30 / mo) |
| **1-Year CUD (20% off)** — Std / Ent / Ent+ | $0.756 / $1.0332 / $1.4364 per node-hr | $0.82368 / $1.125696 / $1.564992 per node-hr | $0.72 / $0.984 / $1.368 |
| **3-Year CUD (40% off)** — Std / Ent / Ent+ | $0.567 / $0.7749 / $1.0773 per node-hr | $0.61776 / $0.844272 / $1.173744 per node-hr | $0.54 / $0.738 / $1.026 |
| **Additional RO Replica** — Ent / Ent+ | $0.4305 / $0.5985 per node-hr | $0.46904 / $0.65208 per node-hr | — |
| **Storage (SSD / HDD / Backup)** | $0.315 / $0.063 / $0.0315 per GiB-mo | $0.3432 / $0.06864 / $0.03432 per GiB-mo | $0.30 / $0.06 / $0.03 |
| **Data Boost** | $1.2285 / 1,000 SPU-hr | $1.33848 / 1,000 SPU-hr | $1.17 / 1,000 SPU-hr |
| **Intra-Region Replication** | $0.00 / GiB | $0.00 / GiB | $0.00 / GiB |
| **Cross-Region Backup Egress (Asia)** | $0.08 / GiB (`asia-southeast3` → `asia-southeast1`) | $0.08 / GiB | — |
| **Dev/PoC Estimate (100 PU × 8 hr, Ent)** | **$1.0332** (0.1 node × 8 hr × $1.2915) | $1.1257 | $0.9840 |

### Southeast Asia Architectural Reality Check

- **No Pre-Built Bangkok + Singapore Dual-Region or Multi-Region Config**: Pre-built dual-region configurations exist only in Australia (`dual-region-australia1`), Canada (`dual-region-canada1`), Germany (`dual-region-germany1`), India (`dual-region-india1`), and Japan (`dual-region-japan1`). Pre-built Asia multi-region configs are `asia1` (Tokyo/Osaka + Seoul witness) and `asia2` (Mumbai/Delhi/Singapore — allowlisted, 3 RW regions, no witness). Neither `regional-asia-southeast3` nor `regional-asia-southeast1` lists pre-built optional read-only replica regions.
- **Recommended Thailand Enterprise Reference Architecture**:
  1. **Primary OLTP & AI Data Platform**: Deploy `regional-asia-southeast3` (Bangkok) on **Enterprise** edition for 3-zone synchronous quorum, 99.99% SLA, Thailand PDPA data residency, and full access to Graph, FTS, Vector Search, Queues, Managed Autoscaler, Tiered Storage, Columnar Engine, and Incremental Backups without paying Enterprise Plus rates.
  2. **Vertex AI Integration**: Route regional Vertex AI text-embedding and generative model endpoints (e.g., `gemini-embedding-001`, `gemini-2.5-flash`) to `asia-southeast1` (Singapore, ~25 ms RTT from Bangkok; use `global` fallback only for supported LLM endpoints, never for regional embedding `predict` models).
  3. **Regional Disaster Recovery**: Copy backups from `regional-asia-southeast3` to `regional-asia-southeast1` via `gcloud spanner backups copy` ($0.08/GiB egress + $0.03432/GiB-mo Singapore backup storage), or run warm/passive async DR to a `regional-asia-southeast1` Enterprise instance using **Spanner Change Streams + Dataflow**.
  4. **Quota Fallback**: Fall back to `regional-asia-southeast1` for non-production environments if a project lacks Spanner quota in `asia-southeast3`.

## 3. Upgrading, Downgrading & Tracking Edition Usage

Edition changes are **in-place, zero-downtime** operations taking ~10 minutes with no data migration.

```bash
# Confirm live instance change with user before running
gcloud spanner instances update "{instance_id}" \
  --project="{project_id}" \
  --edition="STANDARD"|"ENTERPRISE"|"ENTERPRISE_PLUS" \
  [--async]
```

### Downgrade Prerequisite Checklist (Hard Blockers)

Downgrades fail until **every** higher-edition feature is removed from the instance and all its databases:

- **Enterprise Plus → Enterprise (or Standard)**:
  - Move any dual-region or multi-region instance to a regional configuration (`gcloud spanner instances move`).
  - Delete all Geo-Partitioning placements and additional instance partitions.
- **Enterprise → Standard**:
  - Remove optional custom read-only replicas (move to a base regional configuration or delete custom instance config).
  - Drop all Property Graphs (`DROP PROPERTY GRAPH`).
  - Drop all Full-Text Search indexes (`DROP SEARCH INDEX`).
  - Stop KNN/ANN vector queries and drop all Vector Indexes (`DROP VECTOR INDEX` in GoogleSQL / `DROP INDEX` in PostgreSQL).
  - Drop all Spanner Queues (`DROP QUEUE`).
  - Disable Managed Autoscaler (switch to manual `--nodes` or `--processing-units`).
  - Revert custom Locality Groups / Tiered Storage back to SSD default.
  - Disable Columnar Engine (`columnar_policy`).
  - Delete all incremental backup schedules.

### Tracking Edition Feature Consumption

Query Cloud Monitoring metric `spanner.googleapis.com/instance/edition/feature_usage` (sampled every 60 seconds, up to 120 seconds visibility lag). Tracked features: Asymmetric autoscaling, Columnar engine, Full-text search, Geo-partitioning, Incremental backups, KNN vector search, Managed autoscaler, Scheduled backups, Spanner Graph, Tiered storage, and Vector search.

## 4. Organization Policy Governance & Committed Use Discounts (CUDs)

### Organization Policy Constraints

- **Google-Managed Edition Constraint**: `constraints/spanner.managed.restrictCloudSpannerEditions` restricts allowed editions (`STANDARD`, `ENTERPRISE`, `ENTERPRISE_PLUS`) on both **instance creation AND instance updates**.
  - **CRITICAL AUTOSCALER GOTCHA**: Because this constraint also evaluates on `instances.patch` / `update`, customer-managed or **open-source autoscalers WILL FAIL** to scale existing non-compliant instances! Spanner's built-in **Managed Autoscaler** is exempt and continues scaling existing instances.
- **Custom Organization Policy Constraints**: Supported resource types are `spanner.googleapis.com/Backup`, `spanner.googleapis.com/Database`, `spanner.googleapis.com/Instance`, and `spanner.googleapis.com/InstanceConfig`. Policy changes take ~2 minutes to propagate; always validate with dry-run mode or Policy Simulator before enforcing.

```yaml
# Example: Org Policy allowing only ENTERPRISE and ENTERPRISE_PLUS editions
name: projects/{project_id}/policies/spanner.managed.restrictCloudSpannerEditions
spec:
  rules:
    - values:
        allowedValues:
          - ENTERPRISE
          - ENTERPRISE_PLUS
```

### Committed Use Discounts (CUDs)

Spanner CUDs are **spend-based** — they are not pinned to a specific instance, edition, or region, and automatically apply across all projects under the Cloud Billing account for Spanner compute capacity (nodes/processing units).

| Commitment Term | Discount | Consumption Model ID | Covered Costs | Excluded Costs |
| :--- | :---: | :--- | :--- | :--- |
| **1-Year CUD** | **20%** | `558C-892D-2291` | Compute capacity (nodes / PUs across Standard, Enterprise, Enterprise Plus, all regions) | Database storage (SSD/HDD), backup storage, Data Boost SPU usage, network egress / cross-region data transfer |
| **3-Year CUD** | **40%** | `38C3-A961-A68B` | Compute capacity (nodes / PUs across Standard, Enterprise, Enterprise Plus, all regions) | Same exclusions as 1-Year CUD |

## 5. Free Trial Instance Lifecycle & Gotchas

```bash
gcloud spanner instances create "{instance_id}" \
  --project="{project_id}" \
  --instance-type=free-instance \
  --config="regional-asia-southeast3" \
  --description="Spanner 90-Day Free Trial"
```

- **Hard Quotas**: **90-day duration**, **10 GB** storage cap, **~500 read QPS / ~100 write QPS** (1 KB rows), max **5 databases per instance**, **regional configurations only**.
- **Permanent Project Quota Gotcha**: Strictly **1 free trial instance per GCP project EVER** — deleting the free trial instance does **NOT** reset project eligibility! Billing accounts are capped at **5** active/historical free trial instances.
- **Supported vs Unsupported Features**:
  - *Included*: Standard features plus selected Enterprise features (**Spanner Graph, Full-Text Search, KNN, ANN**).
  - *Unsupported*: No availability SLA, no backups/restores, no CMEK, no Data Boost (and no Graph Algorithms requiring Data Boost), no `move-instance`.
- **Expiration & Deletion Protection GOTCHA**: After 90 days, the instance stops serving traffic and enters a **30-day grace period** (still upgradeable). After 30 days, Spanner **permanently deletes** the instance and all databases — **EVEN IF `--enable-drop-protection` is enabled!**
- **Upgrade Edition Gotcha**: Upgrading via `gcloud spanner instances update "{instance_id}" --instance-type=provisioned` defaults to **Enterprise** edition and starts billing immediately (to downgrade to Standard later, remove all Enterprise features first).

## 6. Compute Capacity Granularity, Throughput & Hard Limits

### Granularity & Provisioning Mechanics

- **Granular Instances (`100`–`900` PUs, multiples of `100`)**: Run on a **single server task per zone**. Performance does not scale strictly linearly and can experience intermittent latency spikes under load. Suitable for dev, CI/CD, and PoCs only — **not recommended for latency-sensitive production**.
- **Node-Scale Instances (`>= 1,000` PUs, multiples of `1,000` where `1 node = 1,000 PUs`)**: Allocate 1 dedicated server task per `1,000` PUs in every zone. Required floor for Managed Autoscaler and `move-instance`.
- **Full Per-Zone Provisioning**: Compute capacity is provisioned **in full in every zone** of the instance configuration (not divided across zones).
- **Always-On Billing**: Spanner instances cannot be stopped, paused, or restarted; billing accrues continuously with a **1-hour minimum** billed at the peak hourly compute allocation.

### Storage Bounds & Scale-Down Blockers

- **Max Storage**: **10 TiB per node (`1,000` PUs)**; for granular instances (`< 1,000` PUs), **1,024 GiB (1 TiB) per `100` PUs**. Alert at **75%** storage utilization.
- **Scale-Down Blockers**: Spanner rejects compute reduction if remaining capacity would exceed **10 TiB/node** OR if the instance has **too many splits** (splits take **1–2 weeks** of reduced load to merge back together).
- **Baseline CPU Utilization Targets**: Regional configs: **65%** high-priority CPU / **90%** 24-hr rolling smooth max; Dual-region & Multi-region configs: **45%** high-priority CPU / **90%** 24-hr rolling smooth max. *(Use sibling skill `spanner-performance-observability-and-sizing` for full capacity formulas and TCO modeling.)*

### Expected Peak QPS per Node (`1,000` PUs, ~1 KB Rows)

| Storage & Topology | Peak Read QPS / Node | Peak Write QPS / Node (Single) | Peak Write QPS / Node (Batched) |
| :--- | :---: | :---: | :---: |
| **SSD — Regional** | **22,500** | **3,500** | **22,500** |
| **SSD — Dual/Multi-Region** | **15,000** per RW region (**7,500** optional RO) | **2,700** (**1,500** 3-continent) | **15,000** |
| **HDD — Regional** | **1,500** | **3,500** | **22,500** |
| **HDD — Dual/Multi-Region** | **1,000** per RW region (**500** optional RO) | **2,700** | **15,000** |

### Core Operational Limits

| Limit Dimension | Hard Limit / Recommended Ceiling |
| :--- | :--- |
| **Max Split Size** | **8 GiB** |
| **Max Commit Size** | **100 MiB** |
| **Max Mutations per Commit** | **80,000** (**40,000** recommended safety margin accounting for secondary indexes) |
| **Protocol Buffer Bundle Size** | **5 MiB** max (**500 KiB** recommended) |
| **Max Database Roles (FGAC)** | **100** per database |
| **New Secondary Indexes / DB / Day** | **3** recommended (unless batched into a single DDL request) |

## 7. Enterprise Feature Selection Boundaries & Operational Gotchas

For deep DDL/query authoring on these capabilities, route to `spanner-schema-and-data-modeling` or `spanner-search-ai-and-graphrag`. Below are the platform-level operational constraints that determine edition and sizing choices:

- **Tiered Storage & Locality Groups (Enterprise / Enterprise Plus)**:
  - Delivers **5× storage cost savings** on cold data (e.g., Bangkok SSD $0.315 vs HDD $0.063/GiB-mo) via `storage = 'ssd'` + `ssd_to_hdd_spill_timespan = '1h'..'365d'` (or `storage = 'hdd'`).
  ```sql
  -- GoogleSQL (note mandatory backticks around built-in `default` locality group)
  CREATE LOCALITY GROUP cold_archive OPTIONS (storage = 'ssd', ssd_to_hdd_spill_timespan = '30d');
  ALTER TABLE {table_name} ALTER COLUMN payload SET OPTIONS (locality_group = 'cold_archive');
  ALTER LOCALITY GROUP `default` SET OPTIONS (storage = 'ssd', ssd_to_hdd_spill_timespan = '90d');

  -- PostgreSQL (note double quotes around built-in "default" locality group)
  CREATE LOCALITY GROUP cold_archive STORAGE 'ssd' SSD_TO_HDD_SPILL_TIMESPAN '30d';
  ALTER TABLE {table_name} ALTER COLUMN payload SET LOCALITY GROUP cold_archive;
  ALTER LOCALITY GROUP "default" STORAGE 'ssd' SSD_TO_HDD_SPILL_TIMESPAN '90d';
  ```
  - **Row-Existence Gotcha**: Row existence lives in the **table's** locality group. Keep the table itself on SSD and move only large cold non-key columns to an HDD/age-based locality group; otherwise PK uniqueness checks (`INSERT`, `INSERT OR IGNORE`, `ON CONFLICT DO NOTHING`, partial `ON CONFLICT DO UPDATE`) trigger expensive HDD reads (`INSERT OR UPDATE` / `InsertOrUpdate` skip existence checks).
  - **Compaction, Inheritance & Quoting Gotchas**: SSD→HDD migration runs during background compaction (**~7-day lag**; can trigger manual major compaction); interleaved child tables do **not** inherit parent locality groups; reads do not reset cell age (updating a cold cell writes the new version back to SSD); Search/Vector indexes inherit the `default` locality group.
  - **HDD Bottleneck**: Monitor `spanner.googleapis.com/instance/disk_load` (hitting 100% causes severe latency spikes), `instance/storage/combined/utilization`, and `SPANNER_SYS.TABLE_SIZES_STATS_PER_LOCALITY_GROUP_1HOUR` (`AVG_DISK_IO_COST`).
- **Data Boost (All Editions)**:
  - Serverless isolated compute for batch, analytical, federated, and export workloads with near-zero OLTP CPU impact; uses isolated HDD disk load quota.
  - **Eligibility**: Query execution plan MUST start with a **Distributed Union** root operator (`PartitionQuery`, `PartitionRead`, BigQuery Federation, Dataflow, Spark).
  - **IAM & Billing**: Requires `spanner.databases.useDataBoost` (`roles/spanner.databaseReaderWithDataBoost`). Billed strictly on consumed SPU-hours (Bangkok: $1.2285 / 1,000 SPU-hr; Singapore: $1.33848 / 1,000 SPU-hr).
- **Columnar Engine (Enterprise / Enterprise Plus)**:
  - Maintains columnar blocks alongside row-oriented storage for **up to 200× faster** analytical scans with strong consistency (merges live row mutations at query time).
  - **Configuration & Hints**: Enable via ``ALTER DATABASE `{database_id}` SET OPTIONS (columnar_policy = ...)`` (and table/index options); verify via **"Columnar read share"** in execution plans or force with `@{SCAN_METHOD=COLUMNAR}` / `@{SCAN_METHOD=NO_COLUMNAR}`.
  - **Billing & Gotchas**: Billed on **additional storage only** (no compute surcharge; avoids 8-byte-per-cell row-store overhead); **NOT included in backups** (rebuilt asynchronously after restore); high write churn degrades efficiency; always project needed columns (avoid `SELECT *`).
- **Full-Text Search & Vector Search — KNN & ANN (Enterprise / Enterprise Plus)**:
  - **Compliance Gotcha**: Full-Text Search does **NOT** support **Assured Workloads**.
  - **ANN (ScaNN) Operational Guardrails**: Vector columns use fixed length (`ARRAY<FLOAT32>(vector_length=>N)` in GoogleSQL; `float4[] VECTOR LENGTH N` in PG). Nullable embedding columns require `WHERE embedding IS NOT NULL` in both index DDL and ANN queries; distance function must be the sole `ORDER BY` key paired with `LIMIT`; `num_leaves_to_search` (INT64) and `pct_leaves_to_search` (FLOAT64 `0.0`–`100.0` with decimal point) are mutually exclusive (vector indexes created before **November 2025** may not support `pct_leaves_to_search`).

## 8. Common Anti-Patterns to Reject

- **Paying for Enterprise Plus on a single-region deployment**: If data residency restricts you to `regional-asia-southeast3`, `ENTERPRISE` unlocks every non-geo feature at ~28% lower compute cost ($1.2915 vs $1.7955/node-hr).
- **Running production workloads on `100`–`900` PU instances**: Single server task per zone breaks Managed Autoscaler, `move-instance`, and tail-latency SLAs.
- **Enforcing `restrictCloudSpannerEditions` while running an Open-Source Autoscaler on legacy Standard instances**: The autoscaler's `instances.patch` calls will immediately fail.
- **Relying on `--enable-drop-protection` to preserve a Free Trial instance past Day 120**: Free Trial expiration permanently deletes the instance regardless of drop protection.

## Official Documentation

- [Cloud Spanner Editions Overview](https://docs.cloud.google.com/spanner/docs/editions-overview)
- [Cloud Spanner Pricing](https://cloud.google.com/spanner/pricing)
- [Committed Use Discounts](https://docs.cloud.google.com/spanner/docs/cuds)
- [Free Trial Instances](https://docs.cloud.google.com/spanner/docs/free-trial-instance)
- [Compute Capacity & Quotas](https://docs.cloud.google.com/spanner/docs/compute-capacity)
- [Tiered Storage & Locality Groups](https://docs.cloud.google.com/spanner/docs/tiered-storage)
- [Data Boost Overview](https://docs.cloud.google.com/spanner/docs/databoost/databoost-overview)
- [Columnar Engine](https://docs.cloud.google.com/spanner/docs/columnar-engine)
