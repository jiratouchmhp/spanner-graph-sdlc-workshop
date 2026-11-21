# Key Visualizer Hotspot Diagnostics & Pre-Splitting Lifecycle

Authoritative reference for diagnosing Cloud Spanner key-range hotspots with Key Visualizer, understanding the 1,000-row-range cap and relative brightness scaling, designing pre-split strategies before burst events, authoring valid `splits.txt` / CLI / REST payloads, respecting split-point quotas, and automating split lifecycles.

---

## 1. Key Visualizer Architecture, Controls & Limitations

- **Default State & Coverage**: **Enabled by default** across all Spanner editions (Standard, Enterprise, Enterprise Plus) and regional/multi-region topologies at no additional cost.
- **Required Access**: IAM permission `spanner.databases.read`, or FGAC system role `spanner_sys_reader` (or a member role).
- **Disable / Re-Enable for Strict PII or Wipeout Compliance**: Because Key Visualizer records key-range boundaries in internal metadata, disable it if primary keys contain sensitive data subject to immediate wipeout policies *(always confirm before running against a live database)*:

```sql
-- GoogleSQL: disable Key Visualizer on a database
ALTER DATABASE `{database_id}` SET OPTIONS (enable_key_visualizer = false);
```

```bash
# Disable via gcloud CLI (confirm before mutating live DB)
gcloud spanner databases ddl update "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --ddl='ALTER DATABASE `{database_id}` SET OPTIONS ( enable_key_visualizer = false )'

# Verify current database options
gcloud spanner databases ddl describe "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}"
```

*(When re-enabled, the heatmap remains blank for several minutes while fresh interval metrics accumulate.)*

### Row Range Model, Normalization & Key Gotchas

- **Coordinate Axes**: X-axis = wall-clock time; Y-axis = contiguous **row ranges** sorted **alphabetically by table or index name first**, then by **primary or index key value**.
- **Table & Interleaving Isolation**: Each row range belongs to a single table or index. For interleaved hierarchies, a parent table's row ranges **exclude** keys belonging to interleaved child tables.
- **1,000 Row Range Hard Cap**: Key Visualizer selects **at most 1,000 row ranges** across an entire database:
  - **`< 1,000` combined tables + indexes**: Every table receives at least 1 row range (large tables receive multiple ranges; small tables may collapse into 1 range, concealing intra-table hotspots).
  - **`> 1,000` combined tables + indexes**: Multiple tables roll up onto a single row or are downsampled/omitted, potentially masking localized hotspots.
- **Row-Count Normalization**: Every metric is **normalized by the row count** in its row range (`aggregated_value / row_count_in_range`).
- **CRITICAL — Relative Brightness Gotcha**: Heatmap color intensity (dark = cold, bright = hot, white = peak) is scaled **relative to your own database's traffic**, not absolute Spanner node ceilings. A low-traffic database with 20 QPS on one range and 1 QPS elsewhere still renders bright white. Always read the **absolute legend values** and adjust brightness (`+ / -`) before escalating a hotspot.
- **Warmup & Volatility Caveats**: Newly created databases have no immediate heatmap history; writes to newly inserted keys may initially attribute to adjacent existing ranges; rapidly shifting key distributions introduce split/merge approximation lag.

### Key Visualizer Metrics & Retention Schedule

| Metric | Definition & Nuance |
| :--- | :--- |
| **CPU seconds** | CPU seconds consumed per wall-clock second for reads and writes in the row range |
| **Logical stored bytes** | Logical bytes stored in the row range |
| **Number of rows read** | Rows read per second in the row range |
| **Number of bytes read** | Bytes read per second **from storage** (not bytes returned to the client!) |
| **Number of rows written** | Rows inserted or updated per second in the row range |
| **Number of bytes written** | Bytes inserted or updated per second in the row range |

| Historical Window | Bucket Granularity | Automatic Retention / Wipeout |
| :--- | :--- | :--- |
| Past **6 hours** | **1 minute** | Deleted after 6 hours |
| Past **4 days** | **10 minutes** | Deleted after 4 days |
| Past **28 days** | **1 hour** | Deleted after 28 days |

---

## 2. Heatmap Visual Pattern Diagnostic Catalog

| Visual Pattern | Root Cause | Concrete Action / Remediation |
| :--- | :--- | :--- |
| **Evenly distributed mix of dark and bright** | Uniform key distribution (UUIDv4, bit-reversed sequences, hash-prefixed keys) | Healthy baseline — no remediation needed. |
| **Single bright diagonal line (falling left → right) or flat-top triangle** | **Monotonically increasing keys** (sequential IDs, leading `TIMESTAMP` PK or index) | **Severe anti-pattern.** Primary keys are immutable after creation—catch in PoC and replace with UUIDv4, bit-reversed sequence, or hash-prefixed composite PK. |
| **Persistently hot narrow horizontal bands** | Single hot row(s) or oversized row accessed repeatedly | Refactor schema/app to spread writes across multiple rows (e.g., sharded counters/buckets or hot-key salting). |
| **Single hot horizontal band spanning an entire table** | Heavy load concentrated on one table or recurring **full table scan** | Benign if latency/CPU are healthy; otherwise replace full scans with selective indexed queries or add compute nodes. |
| **Multiple rising or falling diagonal bright bands (roving)** | Sequential range reads (e.g., bulk export) or sequential writes | Reads: usually benign (scale nodes temporarily if latency degrades). Writes: randomize write order before ingestion. |
| **Bright horizontal bands beginning abruptly partway across** | Traffic surge, batch job start, or newly deployed unindexed query | Correlate onset timestamp with Query Insights, `SPANNER_SYS.OLDEST_ACTIVE_QUERIES`, and recent deployments. |

---

## 3. Dynamic Splitting vs. Pre-Splitting Lifecycle

### When to Pre-Split

- **Dynamic Splitting Mechanics**: Spanner automatically splits key ranges on **size (~8 GiB threshold)** or **CPU load**, and merges cold splits automatically.
- **Why Pre-Split**: Dynamic splitting and split movement across servers are **not instantaneous**. Sudden traffic spikes on new or empty tables (initial bulk loads, product launches, flash sales, seasonal events) can overwhelm a single split's server, causing scheduler queuing, latency spikes, timeouts, and transaction aborts.
- **Sizing Rule of Thumb**: Provision **10 split points per node**, allocated across tables and secondary indexes in proportion to expected traffic. Small instances (< multi-node scale) typically adapt rapidly enough without manual pre-splitting.
- **Lead-Time Window**: Create split points **no earlier than 7 days and no later than 12 hours** before the expected traffic surge.
- **Managed Autoscaler Launch Recipe**:
  1. Set Managed Autoscaler **maximum limit** = target peak node count `N`.
  2. Set Managed Autoscaler **minimum limit** = `N / 5` so sufficient baseline nodes host the pre-created splits before traffic arrives (always respecting autoscaler floors: `min >= max / 10` and `min >= 1` node).

### Expiration & Cleanup Lifecycle

- **Default Expiration**: **10 days** from creation or update.
- **Maximum Expiration**: **30 days** from creation or update.
- **Post-Expiration**: Spanner resumes automated split management (cold splits become eligible for merging) and removes expired entries from `SPANNER_SYS.USER_SPLIT_POINTS`.
- **Extend or Delete Immediately**: Spanner exposes no separate `DeleteSplitPoints` RPC. Extend a split point by re-submitting it with a later expiration timestamp, or **delete/expire it immediately** by re-submitting the exact split point with an expiration timestamp equal to the current UTC time.

---

## 4. Split Point Calculation & Exact CLI / File / REST / SQL Syntax

### Determining Split Boundary Keys

- **Uniform Key Spaces (UUIDv4 / Bit-Reversed `INT64`)**: Divide the key range into `N` equal intervals:
  $$\text{offset} = \frac{\text{max\_key}}{N - 1}, \qquad \text{split}_k = \text{first\_key} + \text{offset} \cdot (k - 1)$$
- **Known Hot Prefixes**: Prefix split values do not need to match full production key lengths (e.g., `(1000, 'SG')`, `(1000, 'TH')`, `(1000, 'US')` effectively split keys formatted as `{COUNTRY}_{STATE}_{CITY}_{ID}`).
- **Interleaved Tables**: Splitting a parent table automatically splits interleaved children at parent key boundaries. Add explicit child-table split points when a single hot parent key owns disproportionate child-row traffic.
- **Secondary Indexes**: Split on index key columns alone, or subdivide a hot index key value further by appending `TableKey (...)` containing the base table's primary key columns.

### Splits File Syntax & Strict Quoting Rules

- **Required IAM**: `roles/spanner.databaseAdmin` (`spanner.databases.addSplitPoints`).
- **Line Grammar**:
  ```text
  TABLE <TableName> (<SplitValue1>, <SplitValue2>, ...)
  INDEX <IndexName> (<IndexSplitValue1>, ...) [TableKey (<TableSplitValue1>, ...)]
  ```
- **CRITICAL Quoting & Formatting Rules**:
  1. **MUST single-quote** `STRING`, `INT64`, `NUMERIC`, and `TIMESTAMP` values (e.g., `'123'`, `'99.99'`, `'2026-12-01T00:00:00Z'`).
  2. **Do NOT quote** `BOOL` (`true`/`false`), `FLOAT64` (`1.287`), or `NULL`.
  3. **Escape commas inside string values** with `\,` (e.g., `'ab\,c'`).
  4. Values **must match exact primary/index key column order**.
  5. `TABLE` lines accept at most **1 key tuple**; `INDEX` lines accept at most **2 key tuples** (index key + optional complete `TableKey`).
  6. Maximum **100 split points per file / API request**.

```text
# Example splits.txt (valid quoting & escaping across types)
TABLE {table_name} ('40000000-0000-0000-0000-000000000000')
TABLE {composite_table} ('0', '7ef9db22-d0e5-6041-8937-4bc6a7ef9db2')
INDEX {index_name_a} ('8762203435012030000', NULL, NULL)
INDEX {index_name_b} ('0', '2026-12-01T00:00:00Z') TableKey ('123', 'ab\,c')
```

### Verified `gcloud` CLI, SQL & REST v1 Commands

```bash
# Add or update split points (REQUIRES explicit user confirmation against live DB)
gcloud spanner databases splits add "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --splits-file="splits.txt" \
  --split-expiration-date="2026-12-10T00:00:00Z" \
  --initiator="launch-prep"

# Immediately expire/remove split points before dropping a table/index/database
gcloud spanner databases splits add "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --splits-file="splits.txt" \
  --split-expiration-date="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --initiator="cleanup-before-drop"

# List active user-defined split points
gcloud spanner databases splits list "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}"
```

```sql
-- Inspect active user split points via SQL
SELECT TABLE_NAME, INDEX_NAME, INITIATOR, SPLIT_KEY, EXPIRE_TIME
FROM SPANNER_SYS.USER_SPLIT_POINTS
ORDER BY TABLE_NAME, INDEX_NAME;
```

```json
// REST v1: POST https://spanner.googleapis.com/v1/projects/{project_id}/instances/{instance_id}/databases/{database_id}:addSplitPoints
{
  "split_points": [
    {
      "table": "{table_name}",
      "index": "{index_name}",
      "expire_time": "2026-12-10T00:00:00Z",
      "keys": [
        { "key_parts": { "values": ["3"] } },
        { "key_parts": { "values": ["10"] } }
      ]
    }
  ]
}
```

---

## 5. Quotas, Operational Traps & Enterprise Automation

### Hard Split-Point Quotas

| Quota / Limit | Exact Ceiling |
| :--- | :--- |
| Split points per API request / splits file | **100** |
| API request payload size | **1 MiB** |
| Active user split points per node (summed across **all** DBs in instance) | **50** |
| Split points added or updated per minute per node | **10** |
| Split points added or updated per day per node | **200** |

### Seven Critical Operational Caveats

1. **Search & Vector Indexes Cannot Be Pre-Split**: Only pre-split the underlying base table (search indexes rely on search index sharding).
2. **Table / Index / Database Deletion Quota Trap**: Always expire all associated user split points (`expire_time = now`) **before** dropping a table, index, or database; otherwise orphaned entries continue consuming the 50/node instance quota.
3. **Backups Exclude Split Points**: Restoring a backup creates a database with **zero** user split points—re-apply split points post-restore before cutting traffic over.
4. **Asymmetric Read-Only Autoscaling Quota Floor**: When asymmetric RO autoscaling is active, split-point quotas scale with the **minimum node count across all regions**, not the primary region's peak.
5. **Temporary 2× Storage Inflation on Populated Ranges**: Splitting already-populated key ranges temporarily increases total database storage (up to **2×** for moving data) until compaction completes; splitting empty ranges avoids this overhead.
6. **Excess-Split Fanout Penalty**: Over-splitting increases multi-split transaction participants and query RPC fanout, inflating latency and CPU per query. Always let split points expire once load stabilizes.
7. **API Rejection Triggers**: Duplicate split points in one request, undefined table/index names, mismatched table name on an index split, >1 key tuple on a table split, or >2 key tuples on an index split fail the entire request.

### Reference Automation Pattern (`spanner-split-mgr`)

For large fleets managing hundreds of split points across release events, adopt the reference pattern demonstrated in `spanner-split-mgr` (public `GoogleCloudPlatform/spanner-bb-samples` repository):

- **3-State Local Staging Machine**: Track split points locally (e.g., SQLite/GitOps state) as `SYNCED` (live in Spanner), `PENDING_ADD` (staged additions), and `PENDING_DELETE` (staged immediate expirations) so operators review diffs before mutating production.
- **Automatic 100-Item Batching & Rate Limiting**: Transparently chunk sync operations into <= 100 split points per `:addSplitPoints` RPC while pacing writes to stay under the **10 splits/min/node** rate quota.
- **Safe Delete Emulation**: Translate every `PENDING_DELETE` entry into an `:addSplitPoints` call carrying the exact original key tuple(s) and `expire_time = now()`.
- **Clean Control Plane API**: Expose staged CRUD (`GET/POST /api/splits`, `DELETE /api/splits/{id}`), explicit dry-run/sync (`POST /api/sync`), and instance targeting (`GET/POST /api/settings`).

---

## Official Public Documentation & Reference Code

- Key Visualizer Overview: https://docs.cloud.google.com/spanner/docs/key-visualizer
- Key Visualizer Heatmap Patterns: https://docs.cloud.google.com/spanner/docs/key-visualizer/patterns
- Pre-Splitting Overview: https://docs.cloud.google.com/spanner/docs/pre-splitting-overview
- Create & Manage Split Points: https://docs.cloud.google.com/spanner/docs/create-manage-split-points
- Reference Split Manager Implementation (`spanner-split-mgr`): https://github.com/GoogleCloudPlatform/spanner-bb-samples
