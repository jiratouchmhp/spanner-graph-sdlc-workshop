# Query Optimizer, Execution Plans, Hints & Tuning

Authoritative reference for Cloud Spanner's SQL query optimizer (v1–v9), statistics package lifecycle, distributed execution plan operators, GoogleSQL and PostgreSQL query hints, SQL tuning patterns (including no-window-function rewrites), Query Plan Visualizer replay, and Index Advisor.

---

## 1. Optimizer Architecture & Statistics Packages

Cloud Spanner's query optimizer translates declarative GoogleSQL and PostgreSQL queries into imperative **query execution plans** (trees of relational operators) using three layers:

1. **Rule-Based / Heuristic Transformations**: Pushes data-reducing logic toward storage splits (predicate pushdown, remote filtering, limit pushdown, redundant join and expression elimination).
2. **Cost-Based Optimization (CBO)**: Evaluates candidate plans using query structure, schema metadata, and data-distribution histograms stored in **statistics packages**.
3. **Adaptive Execution & Storage Selection**: Dynamically selects row-oriented vs. batch-oriented scanning/execution and chooses PAX row-formatted storage vs. columnar storage (when the Enterprise/Enterprise Plus Columnar Engine is enabled).

### Statistics Package Lifecycle, Pinning & PII Governance

- **Automatic Collection**: Generated automatically **every 3 days** (`auto_{PACKAGE_TIMESTAMP}UTC`).
- **Manual Refresh (`ANALYZE`)**: Run `ANALYZE` (GoogleSQL and PostgreSQL DDL; produces `analyze_{PACKAGE_TIMESTAMP}UTC`) after bulk loads, large deletes/updates, or creating new indexes/columns.
  - Executes as a **schema update / long-running operation (LRO)** and **cancels** any in-progress automatic collection.
  - Takes **up to 10 minutes** after LRO completion for the optimizer to adopt the new package.
  - Histogram contents are **not directly queryable** (`INFORMATION_SCHEMA.SPANNER_STATISTICS` exposes package names, creation timestamps, and `ALLOW_GC` metadata only).
- **Retention & Garbage Collection (GC) Rules**:
  - Packages are retained for **30 days** by default before becoming eligible for GC.
  - Setting `optimizer_statistics_package` at the **database level** automatically exempts that package from GC (regardless of `ALLOW_GC`).
  - Specifying a package at the **query, client, or environment variable level** requires the package to be either database-pinned OR explicitly marked `allow_gc = false`; otherwise queries fail once GC removes it.
- **PII & Storage Cost Caveat**: Histograms sample column values and can retain deleted PII for **up to 30 additional days** (or indefinitely if pinned / `allow_gc = false`). Statistics are encrypted identically to user data, included in database backups, typically consume **< 100 MB**, and **count toward billed database storage**.

```sql
-- Inspect available statistics packages (GoogleSQL & PostgreSQL)
SELECT PACKAGE_NAME, ALLOW_GC, CREATION_TIME
FROM INFORMATION_SCHEMA.SPANNER_STATISTICS
ORDER BY CREATION_TIME DESC;

-- Disable GC on a package (GoogleSQL — confirm before mutating live DB)
ALTER STATISTICS `{package_name}` SET OPTIONS (allow_gc = false);

-- Disable GC on a package (PostgreSQL — confirm before mutating live DB)
ALTER STATISTICS spanner."{package_name}" SET OPTIONS (allow_gc = false);
```

---

## 2. Query Optimizer Versions (v1–v9) & Override Precedence

- **Default Version**: **Version 8** (released October 28, 2024).
- **Latest Version**: **Version 9** (released July 21, 2026).
- **Promotion SLA**: New optimizer versions become the automatic default **no sooner than 30 days** after release, providing a bake/regression testing window.
- **Upper-Bound Semantics**: Database-level `optimizer_version` sets an **upper bound**; Spanner may internally use an earlier version for specific query shapes to preserve stability. Specifying an unsupported integer (e.g., `10` when `9` is latest) fails with `Query optimizer version: 10 is not supported`.

### Version Evolution Summary

| Version | Release Date | Key Plan & Cost-Model Changes |
| :--- | :--- | :--- |
| **v9 (Latest)** | Jul 21, 2026 | Projection pruning on Distributed Apply (ships only required columns to map side); automatic index selection on **computed columns** (e.g., `WHERE LOWER(col) > 'k'` matching indexed `(LOWER(col))`); interleaved index lookup key batching & sorting for sequential seeks; prefers **Distributed Apply** over `Apply` + `Distributed Union` on map side when no `LIMIT` sits above Apply; more aggressive index union selection; expanded scan-based `GROUP BY` for predicates comparing keys to constant/parameter functions; cost-based optimization for tables in **named schemas**; cardinality refinements. |
| **v8 (Default)** | Oct 28, 2024 | Cost-based planning across `WITH` (CTE) clauses; faster Distributed Cross Apply & indexed lookups; improved `JOIN` reordering; faster large `IN (...)` predicates; improved `GROUP BY`, `LIMIT`, foreign key, and index selection. |
| **v7** | May 22, 2024 | Cost-based **index union** plans; statistics-driven seek vs. scan choice when predicates cover partial keys; cost-based **hash join** selection. |
| **v6** | Sep 11, 2023 | Limit/predicate pushdown through `FULL OUTER JOIN`; improved cost model; **cost-based optimization for DML**. |
| **v5** | Jul 15, 2022 | Cost-based join method choice (**Hash Join vs. Apply Join**; Merge Join still requires hint); join commutativity; refined index/distribution/sort/`GROUP BY` cost model. |
| **v4** | Mar 1, 2022 | **Single-pass Hash Join** (`hash_join_execution=one_pass` spills build+probe to disk to scan probe once vs. default `multi_pass`); improved secondary index selection (interleaved, covering, stale stats, leading-column preference); better seek-key-count selection. |
| **v3** | Aug 1, 2021 | **Merge Join** (`join_method=merge_join`), **Push Broadcast Hash Join** (`join_method=push_broadcast_hash_join`), **Distributed Merge Union** (default on); skips unused non-grouped PK cols in `GROUP BY` without `MIN`/`MAX`; pushes `ORDER BY ... LIMIT` to cross-apply input; pushes subqueries/structs through `JOIN`. |
| **v2** | Mar 1, 2020 | Index selection improvements; faster `REGEXP_CONTAINS`/`LIKE` predicates; `GROUP BY` scan improvements. |
| **v1** | Jun 18, 2019 | Rule-based pushdowns (predicates, limits, redundant joins/expressions) + statistics-based index selection. |

### 6-Tier Option Override Precedence & Configuration

Lowest precedence (left) to highest precedence (right):

> **Spanner Default** ← **Database Option** ← **Client App (`QueryOptions`)** ← **Environment Variable** ← **Client Query (`QueryOptions`)** ← **Statement Hint**

```bash
# Client environment variables (read once at client initialization)
export SPANNER_OPTIMIZER_VERSION="9"
export SPANNER_OPTIMIZER_STATISTICS_PACKAGE="{package_name}"

# Database-level DDL update via gcloud (confirm before mutating live DB)
gcloud spanner databases ddl update "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --ddl='ALTER DATABASE `{database_id}` SET OPTIONS ( optimizer_version = 9 )'

# Verify plan & optimizer version with PROFILE mode
gcloud spanner databases execute-sql "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --query-mode=PROFILE \
  --sql='SELECT * FROM {table_name} LIMIT 10'
```

```sql
-- Inspect supported versions and IS_DEFAULT flag
SELECT * FROM SPANNER_SYS.SUPPORTED_OPTIMIZER_VERSIONS;

-- GoogleSQL: pin version/stats (set NULL to reset to default)
ALTER DATABASE `{database_id}` SET OPTIONS (
  optimizer_version = 9,
  optimizer_statistics_package = '{package_name}'
);
SELECT OPTION_NAME, OPTION_VALUE
FROM INFORMATION_SCHEMA.DATABASE_OPTIONS
WHERE SCHEMA_NAME = ""
  AND OPTION_NAME IN ('optimizer_version', 'optimizer_statistics_package');

-- PostgreSQL: pin version/stats (set DEFAULT to reset to default)
ALTER DATABASE "{database_id}" SET spanner.optimizer_version = 9;
ALTER DATABASE "{database_id}" SET spanner.optimizer_statistics_package = '{package_name}';
SELECT option_name, option_value
FROM information_schema.database_options
WHERE schema_name = 'public'
  AND option_name IN ('optimizer_version', 'optimizer_statistics_package');
```

- **JDBC & Monitoring**: JDBC supports connection properties and session-level `SET OPTIMIZER_VERSION`. Track adoption in Cloud Monitoring via resource `Cloud Spanner Instance` → metric `Count of queries` grouped by `database`, `optimizer_version`, and `status` (invalid versions appear under `Bad usage` with `0` count).

---

## 3. Distributed Execution Model & Operator Taxonomy

- **Top-Down Control, Bottom-Up Data**: Spanner selects a **root server** (minimizing network hops to data) that executes the top operators, dispatches subplans to remote split-hosting servers (acting recursively as root servers), and merges result streams.
- **Logical vs. Physical Boundaries**: Operators prefixed with `Distributed` mark logical distribution boundaries. **Machine groups** (color-coded in Query Plan Visualizer) represent contiguous operators executed sequentially on a server for a split set; transitions across machine groups invoke RPCs (`Remote calls`). A hidden `Local distributed union` sits directly above every scan to ensure restart stability if splits move.
- **CRITICAL — Cumulative vs. Additive Latency**:
  - Most operators report **additive** latency (self-time only).
  - **`Distributed union` and `Distributed apply` (all distributed operators)** report **cumulative** latency (includes parallel child execution across splits). Never sum distributed operator latency with child latency.
- **CRITICAL — Query Restart Statistics Truncation**:
  - If a split moves, a server restarts, or a client resumes streaming mid-query, reported stats (`Rows returned`, `Rows scanned`, `CPU time`, `Total elapsed time`) reflect **only work since the last restart** (`Rows scanned` may appear smaller than total rows returned to the client).
  - Via API (`ResultSetStats.queryStats`), inspect `statistics_since_last_restart: true` (omitted when clean) and re-run to gather complete metrics.

### Execution Operator Reference

| Category | Operator | Execution Mechanics & Key Diagnostic Fields |
| :--- | :--- | :--- |
| **Distributed** | **Distributed union** | Splits table/index scan ranges, applies **split pruning** via split predicates, executes subplans remotely in parallel, and unions streams. Metrics: `Local parallel executions`, `Remote calls`. |
| **Distributed** | **Distributed apply (DA)** (`Cross`, `Outer`, `Semi`, `Anti-Semi`) | Batches input rows (`Create batch`) and sends batches to remote servers running local Apply Joins on map splits. Metrics: `Local parallel executions`, `Remote calls`, `Number of batches`. |
| **Distributed** | **Distributed merge union** | Executes sorted subplans on split servers and merge-sorts streams at root (**distributed merge sort**; default since v3). |
| **Distributed** | **Push broadcast hash join** | Broadcasts serialized build-side rows to **all** map-side split servers, which build local hash tables, probe local splits, and allow pushing post-join aggregates down. **Requires hint** (`JOIN_METHOD=PUSH_BROADCAST_HASH_JOIN`). |
| **Binary** | **Apply join** (`Cross`, `Outer`, `Semi`, `Anti-Semi`) | Row-oriented lookup join (`input` left, `map` right). Join keys act as **seek keys** on `map`. Optimal for low input cardinality, PK-prefix lookups, or **interleaved co-located** tables. |
| **Binary** | **Hash join** | Set-based join (`build` left in-memory hash table, `probe` right). Pushes a **bloom filter** as a **residual filter** (not seek filter) to prune probe rows. Supports `MULTI_PASS` (default; partitions build, re-scans probe per partition) vs. `ONE_PASS` (spills both sides to disk, probes once). |
| **Binary** | **Merge join** | Streams two inputs sorted on join keys. Zero memory if pre-sorted; else inserts `Sort` (distributed merge sort per split). **Requires hint** (`JOIN_METHOD=MERGE_JOIN`). |
| **Binary** | **Recursive union** | Evaluates Spanner Graph **quantified path traversals** (e.g., `MATCH (a)-[e]->{1,3}(b)`). Evaluates `base` once, then iteratively joins `recursive` against the prior iteration's `recursive spool scan` until upper bound or empty delta. |
| **Unary** | **Aggregate** | `Stream aggregate` (low memory, non-blocking, requires pre-sorted input) vs. `Hash aggregate` (in-memory hash table). Splits into **local-global pairs** across distributed splits. |
| **Unary** | **Sort** / **Limit** | Splits into **local-global pairs** (local top-K per split → global merge/limit at root) when `ORDER BY` pairs with `LIMIT`/`OFFSET`. |
| **Unary** | **Filter** | Post-scan scalar filtering; slower than `Filter scan` inside storage. |
| **Unary** | **Apply mutations** | Top-level execution operator for DML (`INSERT`, `UPDATE`, `DELETE`). |
| **Unary** | **Local split union** | Appears on **geo-partitioned placement tables** (`PLACEMENT KEY`), batching co-located splits on the same server into one RPC. |
| **Unary** | **DataBlockToRowAdapter** / **RowToDataBlockAdapter** | Inserted automatically at boundaries between vectorized **Batch** and **Row** operators. |
| **Unary** | **Create batch**, **Compute**, **Compute struct**, **Serialize result**, **Random ID assign**, **TVF**, **Union input** | `Random ID assign` drives `TABLESAMPLE BERNOULLI` (with `Filter`) and `RESERVOIR`/`SPANNER.RESERVOIR` (with `Sort`+`LIMIT`). `TVF` executes table-valued functions (`ML.PREDICT`). |
| **Leaf** | **Scan** (`Table`, `Index`, `Batch`) & **Filter scan** | `Seek condition` navigates PK/index prefixes directly; missing seek condition triggers `full scan: true` (warning icon). `Filter scan` applies `Residual condition` inside storage. Stats: `Scanned rows`, `Seek count`, `Deleted rows scanned`, `Method` (`Automatic`/`Batch`/`Row`). |
| **Leaf** | **Array unnest**, **Generate / Unit / Empty relation** | `UNNEST(...) WITH OFFSET`, scalar expressions (`SELECT 1 + 2`), and `LIMIT 0` plans. |
| **N-ary** | **Union all** | Concatenates >2 identically typed `Union input` streams without deduplication. |

---

## 4. Complete Query Hints Matrix (GoogleSQL & PostgreSQL)

- **GoogleSQL syntax**: `@{KEY=VALUE, ...}`
- **PostgreSQL syntax**: `/*@ KEY=VALUE, ... */`

| Scope | Hint Key | Valid Values | GSQL | PG | Operational Notes & Gotchas |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **Statement** | `OPTIMIZER_VERSION` | `1..9`, `latest_version` (GSQL) / `"latest"` (PG), `default_version` (GSQL) | Yes | Yes | Highest precedence override. Unsupported version raises an error. |
| **Statement** | `OPTIMIZER_STATISTICS_PACKAGE` | `{package_name}`, `latest` | Yes | Yes | Package MUST be DB-pinned or have `allow_gc = false`. |
| **Statement** | `USE_ADDITIONAL_PARALLELISM` | `TRUE` \| `FALSE` (default) | Yes | Yes | Accelerates large scans / `LIMIT` queries; can saturate split CPU—pair with `PRIORITY_MEDIUM` or `PRIORITY_LOW`. |
| **Statement** | `ALLOW_DISTRIBUTED_MERGE` | `TRUE` (default) \| `FALSE` | Yes | — | Controls distributed merge sort for `ORDER BY`. |
| **Statement** | `LOCK_SCANNED_RANGES` | `exclusive` \| `shared` (default) | Yes | Yes | Acquires exclusive locks upfront to prevent read→write lock-upgrade deadlocks. **Errors** if combined with `SELECT ... FOR UPDATE`. Advisory—**not a distributed mutex**. |
| **Stmt / Table** | `SCAN_METHOD` | `AUTO` (default), `BATCH`, `ROW`, `COLUMNAR`, `NO_COLUMNAR` | Yes | Yes | Controls storage scan interaction. **Cannot** pass `AUTO` explicitly (omit hint for default). Prefer table-level granularity. |
| **Statement** | `EXECUTION_METHOD` | `DEFAULT`, `BATCH`, `ROW` | Yes | Yes | Advisory hint for vectorized operator execution (independent of `SCAN_METHOD`). **Cannot** pass `DEFAULT` explicitly. |
| **Statement** | `ALLOW_TIMESTAMP_PREDICATE_PUSHDOWN` | `TRUE` \| `FALSE` (default) | Yes | Yes | Pushes timestamp filters for age-based tiered storage pruning. |
| **Statement** | `AUTO_PARAMETER` | `"ON"`, `"OFF"`, `"AUTO"` (default) | Yes | Yes | Controls literal-to-parameter conversion. `"OFF"` recompiles per literal set, hurting Query Insights grouping and CPU. |
| **Statement** | `USE_UNENFORCED_FOREIGN_KEY` | `TRUE` (default) \| `FALSE` | Yes | — | Lets optimizer exploit `NOT ENFORCED` FKs (redundant scan elimination, limit pushdown); wrong results if FK data is violated. |
| **Statement** | `RANDOMIZE_QUERY_ROOT` | `TRUE` \| `FALSE` (default) | Yes | — | Bypasses `PrepareQuery` location hint (first split's server) to mitigate **root coordinator hotspotting** on high-fanout queries. |
| **Table** | `FORCE_INDEX` | `{index_name}` \| `_BASE_TABLE` | Yes | Yes | **Directive (not advisory)**: errors if index does not exist. Only `_BASE_TABLE` is legal as a statement-level hint. |
| **Table** | `GROUPBY_SCAN_OPTIMIZATION` | `TRUE` \| `FALSE` | Yes | Yes | Fast skip-scan for `GROUP BY` / `SELECT DISTINCT` when grouping keys form a key prefix and only first row per group is needed. |
| **Table** | `INDEX_STRATEGY` | `FORCE_INDEX_UNION` | Yes | — | Forces index union for `OR` across separate indexes; errors if impossible. |
| **Table** | `SEEKABLE_KEY_SIZE` | `0` to `16` | Yes | — | Sets leading key columns used as seek vs. residual filter. **Requires `FORCE_INDEX`**. |
| **Join** | `FORCE_JOIN_ORDER` | `TRUE` \| `FALSE` (default) | Yes | Yes | Preserves exact textual join order; place most selective filters first. |
| **Join** | `JOIN_METHOD` | `APPLY_JOIN`, `HASH_JOIN`, `MERGE_JOIN`, `PUSH_BROADCAST_HASH_JOIN` | Yes | Yes | Never combine `HASH JOIN` keyword syntax with `JOIN@{JOIN_METHOD=HASH_JOIN}`. |
| **Join** | `HASH_JOIN_BUILD_SIDE` | `BUILD_LEFT` \| `BUILD_RIGHT` | Yes | Yes | Requires `JOIN_METHOD=HASH_JOIN`. |
| **Join** | `HASH_JOIN_EXECUTION` | `MULTI_PASS` (default) \| `ONE_PASS` | Yes | Yes | Requires `JOIN_METHOD=HASH_JOIN`. Use `ONE_PASS` when right (probe) executions exceed hash-join executions with high probe latency. |
| **Join** | `BATCH_MODE` | `TRUE` (default) \| `FALSE` | Yes | Yes | `FALSE` switches from batched to row-at-a-time apply. Requires `JOIN_METHOD=APPLY_JOIN`. |
| **Join** | `FACTORIZED_MODE` | `FACTORIZE_LEFT`, `FACTORIZE_RIGHT`, `FACTORIZE_BOTH` | Yes | — | Deduplicates join keys before equality `INNER JOIN`s; accelerates many-to-many graph traversals. |
| **Group** | `GROUP_METHOD` | `HASH_GROUP` \| `STREAM_GROUP` | Yes | — | Syntax: `GROUP @{GROUP_METHOD=STREAM_GROUP} BY ...`. |
| **Function** | `DISABLE_INLINE` | `TRUE` \| `FALSE` (default) | — | Yes | Evaluates expensive scalar function once (`SHA512(z) /*@ DISABLE_INLINE=TRUE */`) instead of per reference; invalid on casts/non-scalar functions. |

---

## 5. SQL Tuning Playbook & No-Window-Functions Patterns

### Core Query & Storage Optimization Rules

1. **Seek vs. Residual Predicates**: Order composite PK and index keys with **equality (`=`) columns first, range/inequality columns second**. Selective filters appearing in `Residual condition` over large scans indicate misaligned key order.
2. **Eliminate Base-Table Back Joins**: An `Index scan` feeding `Distributed cross apply` → `Table scan` indicates a back join. Cover projected/filtered columns with `STORING (...)` (GoogleSQL) or `INCLUDE (...)` (PostgreSQL).
   - For **`AND`** across two indexed columns: prefer adding the second column to `STORING`/`INCLUDE`; if schema edits are blocked, intersect forced index PKs via `INTERSECT DISTINCT` and join back to the base table.
   - For **`OR`** across two indexed columns: rely on v7+ cost-based index union or `@{INDEX_STRATEGY=FORCE_INDEX_UNION}` (`Union all` + `Hash aggregate`).
3. **Contiguous Scans & Join Selection**: Range scans (`BETWEEN 1 AND 5`) outperform scattered seeks (`IN (1, 3, 9, 10, 15)`). Use **Apply Join** for selective OLTP lookups (thousands of rows with indexed keys); switch large-fraction joins (millions of rows) to **Hash Join** (`ONE_PASS` if spilling) or **Merge Join** to avoid millions of scattered seeks.
4. **Optimize `LIMIT`, `NULL_FILTERED` & Sharded Prefix `ORDER BY`**:
   - For `NULL_FILTERED` indexes, include explicit `IS NOT NULL` predicates so the optimizer can legally select the index.
   - Match index order `(eq_col, order_col DESC)` to `WHERE eq_col = @v ORDER BY order_col DESC LIMIT N` to eliminate full sorting.
   - Rewrite range predicates over hash/shard prefixes (`WHERE ShardId BETWEEN 0 AND 31 ORDER BY UpdatedAt DESC LIMIT 10`) using explicit enumeration so Spanner pulls top-N per shard:
     ```sql
     -- GoogleSQL: top-10 across 32 hash shards without full scan + sort
     SELECT u.*
     FROM UNNEST(GENERATE_ARRAY(0, 31)) AS shard_id
     JOIN {table_name} AS u ON u.ShardId = shard_id
     ORDER BY u.UpdatedAt DESC
     LIMIT 10;

     -- PostgreSQL equivalent
     SELECT u.*
     FROM generate_series(0, 31) AS shard_id
     JOIN {table_name} AS u ON u.shard_id = shard_id
     ORDER BY u.updated_at DESC
     LIMIT 10;
     ```
5. **Fast Existence Checks**: Use `SELECT 1 FROM {table_name} WHERE {pred} LIMIT 1;` instead of `COUNT(*) > 0`.
6. **Storage-Layer Bottlenecks**:
   - **Deleted rows scanned**: MVCC/PITR retains deleted versions (1h default, up to 7d) + up to 7d until background compaction. If `Deleted rows scanned` is high in the `Scan` card, trigger manual major compaction (Enterprise / Enterprise Plus only; confirm before running): `CALL compact_all();` (GoogleSQL) or `CALL spanner.compact_all();` (PostgreSQL).
   - **Large rows**: Row storage reads entire rows regardless of column projection. Move large blobs/JSON to a separate **locality group**, child table, narrow covering PK index (`CREATE INDEX IdxMeta ON {table_name}(Id) STORING (Summary)`), or enable **Columnar Engine** for analytical scans.
   - **Sparse interleaved scans**: Scanning a sparse parent or sibling interleaved with a massive child table skips over child rows; add a **non-interleaved secondary index** on the sparse table.
   - **Batch-oriented scans**: Start in `Row` mode and switch dynamically to `Batch`. Effective for large low-churn scans and fixed-width filters; ineffective for point lookups, small scans, `LIMIT` queries, high-churn data (> ~10% active writes), or rows with single columns > **32,000 bytes** pre-compression.

### Non-Negotiable Dialect Constraint: No `ROW_NUMBER()` or General Window Functions

Cloud Spanner GoogleSQL has **no** general `ROW_NUMBER() OVER (...)`, `RANK()`, `DENSE_RANK()`, `LAG()`, or `LEAD()` analytic window functions. Queries using them fail compilation. Use these verified replacements:

```sql
-- Pattern A: Bounded Top-K Ranking / RRF (0-based rank; use rank + 1 for 1-based)
-- Always bound LIMIT k and project minimal columns because ARRAY(...) materializes in memory.
SELECT item.EntityId, item.Score, rank
FROM UNNEST(ARRAY(
  SELECT AS STRUCT EntityId, Score
  FROM {table_name}
  WHERE Status = 'ACTIVE'
  ORDER BY Score DESC
  LIMIT 50
)) AS item WITH OFFSET AS rank;

-- Pattern B: Top-1 per Group (replaces ROW_NUMBER() OVER (PARTITION BY g ORDER BY ts DESC) = 1)
SELECT GroupId, ANY_VALUE(Payload HAVING MAX(EventTimestamp)) AS latest_payload
FROM {table_name}
GROUP BY GroupId;

-- Pattern C: Top-K per Group (backed by interleaved child table or (ParentId, Score DESC) index)
SELECT p.ParentId, c.ChildId, c.Score, rank
FROM {parent_table} AS p
JOIN UNNEST(ARRAY(
  SELECT AS STRUCT ChildId, Score
  FROM {child_table}
  WHERE {child_table}.ParentId = p.ParentId
  ORDER BY Score DESC
  LIMIT 10
)) AS c WITH OFFSET AS rank;
```

- **Narrow Spanner Graph GQL Exception**: ISO GQL supports `IS_FIRST(k) OVER ([PARTITION BY ...] [ORDER BY ...])` strictly as a boolean predicate inside GQL `FILTER` clauses to cap hub fanout—not as a general SQL `ROW_NUMBER()` expression.
- **General Window Functions Workaround**: Offload workloads requiring `ROW_NUMBER()`, `RANK()`, `LAG()`, `LEAD()`, or window frames to **BigQuery federated queries** (`EXTERNAL_QUERY('{project_id}.{location}.{connection_id}', '''SELECT ...''')`) with **Spanner Data Boost** (and optionally `@{scan_method=columnar}`) to isolate OLTP CPU.

---

## 6. Query Plan Visualizer, Benchmarking & Index Advisor

- **Visualizer Access Modes**:
  1. **On-Demand**: Spanner Studio → Run (or *Explanation only*) → **Explanation** tab.
  2. **Historical Sampled Plans**: Query Insights → TopN query fingerprint → **Query plans samples** dot plot (**30-day retention**, max 7-day zoom window; sampled for higher-CPU queries; **unsupported** for `PartitionQuery` with `partitionToken` and **Partitioned DML**).
- **Visual Cues & Views**:
  - **Tree View (DAG)**: Edge thickness scales with rows; **red bars** show % of total latency/CPU; **warning triangles** mark full table scans; **machine group colors** mark RPC boundaries.
  - **Sequential View**: Tabular breakdown (`Name`, `Machine group`, `Latency`, `Cumulative latency`, `CPU Time`, `Rows returned`).
  - **Offline JSON Export & Replay**: Click **DOWNLOAD JSON** (strips query result rows). Replay in any Spanner Studio tab by pasting:
    ```text
    PROTO:
    <PASTE_EXPORTED_JSON_HERE>
    ```
    then clicking **Run** → **Explanation**.
- **Benchmarking Rule**: Never measure production query latency in Spanner Studio or with `QueryMode.PROFILE`. Execute benchmarks from a **same-region GCE VM** (e.g., `asia-southeast3`) using randomized bind parameters.
- **Index Advisor** (GoogleSQL & PostgreSQL; Spanner Studio Explanation tab & Query Insights TopN):
  - Suggests `CREATE INDEX` and `ALTER INDEX` DDL only when measurable improvement is expected.
  - **Limitations**: Does **not** recommend `DROP INDEX`; Query Insights TopN recommendations refresh **every 12 hours** (covering the prior 12h window); **FGAC users** cannot click *Copy to Spanner Studio* / execute directly and must copy DDL manually.

---

## Official Public Documentation

- Query Optimizer Overview & Versions: https://docs.cloud.google.com/spanner/docs/query-optimizer/overview
- Managing Optimizer Statistics: https://docs.cloud.google.com/spanner/docs/query-optimizer/manage-query-optimizer
- Query Execution Plans & Operators: https://docs.cloud.google.com/spanner/docs/query-execution-plans
- SQL Best Practices & Query Hints: https://docs.cloud.google.com/spanner/docs/sql-best-practices
- Query Plan Visualizer & Index Advisor: https://docs.cloud.google.com/spanner/docs/query-plan-visualizer-overview
