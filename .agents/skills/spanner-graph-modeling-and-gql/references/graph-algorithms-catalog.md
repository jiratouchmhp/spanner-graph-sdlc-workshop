# Spanner Graph Algorithms Catalog & Data Boost Execution Guide

Authoritative reference for Cloud Spanner **Graph Algorithms** (Preview; Enterprise and Enterprise Plus editions, GoogleSQL only) powered by **Spanner Data Boost** serverless compute.

---

## 1. Architecture, Billing, Regional Data Boost Rates & IAM

- **Scale & Execution Model**: Built with Google Research Graph Mining; scales to **tens of billions of edges** with runtimes from minutes to tens of minutes. Executes via **Spanner Data Boost** on independent on-demand serverless compute with **near-zero impact** on provisioned OLTP compute during graph loading and computation.
- **Invocation Channels**: Executed as built-in GQL `CALL` / `CALL PER ()` statements via Google Cloud console, `gcloud`, client libraries, REST, or RPC APIs.
- **Serverless Processing Unit (SPU) Billing & Idle Trap**:
  - Billed strictly per **SPU** consumed while algorithm compute is active — **CRITICAL**: compute remains **billable while idle** during `max_idle_time` (default `'30m'`). Track costs in Cloud Billing Reports by filtering SKUs to regional **Data Boost**.
  - **Bangkok Primary (`regional-asia-southeast3`)**: **`$1.2285 / 1,000 SPU-hr`**
  - **Singapore Secondary (`regional-asia-southeast1`)**: **`$1.33848 / 1,000 SPU-hr`**
  - **US Reference (`regional-us-central1`)**: **`$1.17 / 1,000 SPU-hr`**
- **Least-Privilege IAM (Live-Verified Gotchas)**:
  - **Caller Role (Mandatory Even for Project Owners!)**: The invoking principal **MUST** explicitly hold `roles/spanner.graphIntelligenceUser` (grants `spanner.databases.runGraphAlgorithms` and includes `roles/spanner.databaseReaderWithDataBoost`). **Even `roles/owner` does NOT include `spanner.databases.useDataBoost`** and fails with `PERMISSION_DENIED` until `roles/spanner.graphIntelligenceUser` is bound.
  - **Spanner Write-Back (`format = 'CLOUD_SPANNER'`)**: Grant database-level **`roles/spanner.databaseUser` + `roles/spanner.graphIntelligenceUser`** to the Google-managed Spanner service agent `service-{project_number}@gcp-sa-spanner.iam.gserviceaccount.com` so Data Boost can write results back into the destination Spanner table.
  - **Cloud Storage Export (`format = 'CSV'|'PARQUET'|'AVRO'`)**: Grant `roles/storage.objectAdmin` to `service-{project_number}@gcp-sa-spanner.iam.gserviceaccount.com`.

---

## 2. Query Structure & Common Named Input Parameters

### 2.1 Grammar: Full-Graph `CALL` vs. Dynamic Subgraph `CALL PER ()`

```sql
EXPORT DATA OPTIONS (<export_option_list>) AS
GRAPH {graph_name}
[<match_clause>]
{ CALL | CALL PER () } <AlgorithmName>(<common_params>, <algorithm_params>)
  YIELD <algorithm_outputs>
RETURN <results>;
```

- **Full-Graph Mode (`CALL`)**: Omit `<match_clause>`, optionally scope with `node_labels => [...]` and `edge_labels => [...]`.
- **Dynamic Logical Subgraph Mode (`CALL PER ()`)**: Provide one or more single-element `MATCH` statements (combined via `FULL UNION ALL` and terminated with `NEXT`) to build a working table of unique nodes and edges, then pass all rows into one logical subgraph via `CALL PER ()`.
- **Return Behavior**: On completion, the query returns **0 rows** with `Success` status; inspect results at the `EXPORT DATA` destination (Cloud Storage or a Spanner table).

### 2.2 Common Named Input Parameters (`NAME => VALUE`)

| Parameter | Type | Default | Rules & Cost / Residency Impact |
| :--- | :--- | :--- | :--- |
| `node_labels` | `ARRAY<STRING>` | *(all)* | **Supported ONLY with `CALL` (not `CALL PER ()`)**. Includes nodes matching at least one label. |
| `edge_labels` | `ARRAY<STRING>` | *(all)* | **Supported ONLY with `CALL` (not `CALL PER ()`)**. Includes edges matching at least one label. |
| `edge_weight_property` | `STRING` | *(weight `1`)* | Numeric edge property used as weight; defaults every edge to `1` if omitted. |
| `machine_category` | `STRING` | `'default'` | `'default'` or `'large'`. Set `'large'` when input exceeds **> 1 billion nodes and > 10 billion edges**. |
| `fallback_compute_region` | `STRING` | *(none)* | Exact SQL TVF parameter name is **`fallback_compute_region => '{region}'`** (NOT `fallback_region`). Fallback region under temporary capacity constraints **MUST remain in the same jurisdiction** as the Spanner instance's home region (e.g., `us-central1` for `us-east1`). *Residency note*: A Thailand-resident `asia-southeast3` instance cannot fall back across national borders to another country's jurisdiction. |
| `max_idle_time` | `STRING` | `'30m'` | Warm-compute retention window after completion to skip cold starts on iterative runs (`ns`, `us`/`µs`, `ms`, `s`, `m`, `h`, e.g., `'4m'`, `'1.5h'`, `'1h45m'`). **Billable while idle** — set low (e.g., `'1m'`–`'5m'`) on scheduled batch jobs. |

---

## 3. Output Persistence (`EXPORT DATA OPTIONS`)

### 3.1 Export to Cloud Storage (`CSV`, `PARQUET`, `AVRO`)

| Option | Type | Required | Default | Rules |
| :--- | :--- | :-: | :--- | :--- |
| `uri` | `STRING` | Yes | — | `gs://{bucket_name}/path/prefix-*.ext`. Allows a **single `*` wildcard in the filename ONLY** (not in bucket, folder, or extension); expands to 0-based **12-digit left-padded shard numbers** (`prefix-000000000000.parquet`). Without `*`, writes a single file. |
| `format` | `STRING` | Yes | — | `'CSV'`, `'PARQUET'`, `'AVRO'` (case-insensitive). |
| `header` | `BOOL` | No | `false` | CSV only: writes column header row to each sharded file. |
| `overwrite` | `BOOL` | No | `false` | Fails if target exists unless set to `true`. |
| `field_delimiter` | `STRING` | No | `','` | CSV only. |
| `compression` | `STRING` | No | *(none)* | CSV: `'GZIP'`; PARQUET: `'SNAPPY'`, `'GZIP'`, `'ZSTD'`; AVRO: `'DEFLATE'`, `'SNAPPY'`. |

Output column names come from `RETURN` aliases. **CSV cannot export nested or repeated columns.**

| Spanner Type | CSV | Avro | Parquet |
| :--- | :--- | :--- | :--- |
| `BOOL` | `'true'` / `'false'` | `BOOLEAN` | `BOOLEAN` |
| `INT64` | decimal string | `LONG` | `INT64` |
| `FLOAT32` / `FLOAT64` | string | `FLOAT` / `DOUBLE` | `FLOAT` / `DOUBLE` |
| `NUMERIC` | string | `BYTES` (`DECIMAL(38,9)`) | `DECIMAL(38,9)` |
| `STRING` | string | `STRING` | `STRING` |
| `BYTES` | Base64 string | `BYTES` | `BYTE_ARRAY` |
| `TIMESTAMP` | `YYYY-MM-DD HH:MM:SS.ffffff UTC` | `LONG` (epoch micros) | `TIMESTAMP_MICROS` |
| `NULL` | empty string | `null` | `null` |

### 3.2 Persist Back to Cloud Spanner (`format = 'CLOUD_SPANNER'`)

Enables native GQL/SQL queries, chaining algorithms (e.g., `WeaklyConnectedComponents` -> per-cluster `PageRank`), and Change Streams propagation.

- **Options**: `format = 'CLOUD_SPANNER'`, `table = '{dest_table_in_same_db}'` (must already exist in the **same database**), `write_mode = 'update_ignore_all' | 'upsert_ignore_all'` (skips constraint violations such as missing key on update, unique index, or FK violations; **aborts** on type mismatch or missing `NOT NULL`).
- **Schema Prerequisites**: (1) Destination table must exist; (2) every `RETURN` column must match an existing destination column name and type; (3) `RETURN` must include **all PK columns** of the destination table; (4) after `ALTER TABLE {node_table} ADD COLUMN ...`, re-run `CREATE OR REPLACE PROPERTY GRAPH` so GQL exposes the new column.
- **Write Semantics (CRITICAL)**:
  - **Non-transactional** with **row-level atomicity** and **at-least-once** writes.
  - **Partial visibility**: Concurrent readers may see partially written results mid-run.
  - **No rollback on failure**: Aborts on the first non-retryable error leaving prior rows committed; inspect `ERROR_MESSAGE` in `SPANNER_SYS.GRAPH_OPERATION_EXECUTION_STATUS` for the failing PK.
  - **Leader CPU Warning**: Write-backs execute at `MEDIUM` priority through the **leader replica** — stagger runs if OLTP leader CPU is saturated.

---

## 4. Six Strict Schema & Feature Compatibility Rules

1. **Mandatory Interleaving + Destination FK on Edge Tables**: Every edge table passed into an algorithm **MUST** be `INTERLEAVE IN PARENT` its source node table **AND** declare either an **enforced** or **informational (`NOT ENFORCED`)** `FOREIGN KEY` referencing its destination node table! *(For same-node-type edges where Spanner forbids dual cascade, use `INTERLEAVE IN PARENT NodeTable ON DELETE CASCADE` + `FOREIGN KEY (dst_id) REFERENCES NodeTable(id) NOT ENFORCED`.)*
2. **Unique Identifying Label per Element Table**: Every input node/edge table must have at least one label unique exclusively to that table (e.g., `LABEL Person ... LABEL Entity` is valid; two tables with ONLY `LABEL Entity` is rejected).
3. **No View-Backed Inputs**: Algorithm inputs (including edge endpoints) CANNOT be defined over SQL views.
4. **No Query Parameters (`@param`)**: Inline literal values directly in algorithm queries.
5. **Strict `CALL PER ()` `MATCH` Restrictions**:
   - Each `MATCH` MUST contain **ONLY ONE element pattern** (a single node OR a single edge) with **ONE named variable**.
   - Edge patterns CANNOT specify labels or named variables on endpoints:
     - **Valid**: `MATCH (a:Account WHERE a.id > 400)`, `MATCH -[e:Transfers WHERE e.amount < 500]->`
     - **Invalid**: `MATCH (a:Account)-[]->`, `MATCH (a:Account)-[e:Transfers]->`
   - Inline `WHERE` cannot reference outer variables; label expression must map to **exactly one** element table.
6. **Strict `RETURN` Restrictions**:
   - **Allowed ONLY**: Yielded node properties (`node.id`), `ELEMENT_DEFINITION_NAME(node)`, `PATH_LENGTH(path)`, scalar algorithm outputs (`score`, `cluster`, `clique`, `similarity`, `cost`), and literals (`'run_01' AS algo_run_id`).
   - **Forbidden**: Raw graph elements (`RETURN node, score`) or expressions/functions over outputs (`node.id + 1`, `score * 100`, `JSON_OBJECT(...)`).

---

## 5. Execution Monitoring & Cancellation (`SPANNER_SYS`)

`SPANNER_SYS.GRAPH_OPERATION_EXECUTION_STATUS` retains 30 days of execution history with columns: `QUERY_ID` (`STRING`), `QUERY_TEXT` (`STRING`), `START_TIMESTAMP` (`TIMESTAMP`), `LAST_UPDATE_TIMESTAMP` (`TIMESTAMP`), `PROGRESS` (`FLOAT64` in `[0, 1]`), `STATUS` (`PENDING`, `IN_PROGRESS`, `OK`, `CANCELLED`, `DEADLINE_EXCEEDED`, `INVALID_ARGUMENT`, `FAILED_PRECONDITION`, `RESOURCE_EXHAUSTED`, `UNAVAILABLE`, `UNKNOWN`), and `ERROR_MESSAGE` (`STRING`).

```sql
-- Inspect active or failed algorithm jobs
SELECT query_id, status, progress, start_timestamp, last_update_timestamp, error_message
FROM SPANNER_SYS.GRAPH_OPERATION_EXECUTION_STATUS
WHERE status != 'OK'
ORDER BY start_timestamp DESC;

-- Cancel an in-flight job (confirm with user first)
CALL cancel_query('{query_id}');
```

---

## 6. Complete 10-Algorithm Catalog

All 10 algorithm families accept the 6 common parameters from Section 2.2. Notation: `n = |V|`, `m = |E|`, `Delta` = max degree, `d` = graph degeneracy.

### 6.1 Quick Comparison Matrix

| # | Algorithm | Category | Direction | Edge Weights | Deterministic | `YIELD` Columns |
| :-: | :--- | :--- | :--- | :--- | :-: | :--- |
| 1 | `PageRank` | Centrality | Directed | Non-negative (`>= 0`) | Yes | `node, score` |
| 2 | `BetweennessCentrality` | Centrality | Directed | **Strictly positive (`> 0`)** | Exact: Yes / Approx: No | `node, score` |
| 3 | `ClosenessCentrality` | Centrality | **`EXACT`: Directed**<br>**`HYBRID`: Undirected** | Non-negative (`>= 0`) | `EXACT`: Yes | `node, score` |
| 4 | `WeaklyConnectedComponents` | Clustering | Undirected | **Ignored** | Yes (partition) | `node, cluster` |
| 5 | `ModularityClustering` | Clustering | Undirected | Non-negative (`>= 0`) | **No** (async Louvain) | `node, cluster` |
| 6 | `CorrelationClustering` | Clustering | Undirected | **Negative, 0, or Positive!** | **No** (async Louvain) | `node, cluster` |
| 7 | `LabelPropagation` | Clustering | Undirected | Non-negative (`>= 0`) | **Yes** (LLF coloring) | `node, cluster` |
| 8 | `CliqueFinding` | Clustering | Undirected | **Ignored** (+ ignores self-loops) | Yes | `node, clique` *(multi-row/node!)* |
| 9 | `JaccardSimilarity` / `CosineSimilarity` / `CommonNeighborsSimilarity` / `TotalNeighborsSimilarity` | Pairwise Similarity | Directed (out-neighbors) | **ONLY `CosineSimilarity` uses weights** (`<, =, > 0`) | Yes | `source_node, target_node, similarity` |
| 10 | `ShortestPath` | Path Finding | Directed | Non-negative (`>= 0`, ignores self-loops) | **Yes** (min hops, rev lex IDs) | `source_node, target_node, path, cost` |

### 6.2 Algorithm-Specific Parameters, Semantics & Complexity

1. **`PageRank`** (`YIELD node, score`):
   - Params: `source_nodes` (`ARRAY<GRAPH_NODE>`, optional -> Personalized PageRank via `ARRAY { MATCH (n:Account {id: 7}) RETURN n }`), `damping_factor` (`FLOAT64`, default `0.85`, `[0, 1)`), `max_iterations` (`INT64`, default `10`, `> 0`), `approx_precision` (`FLOAT64`, default `1e-2`, `>= 0`; early stop on L1 delta `< approx_precision * n`). Sink nodes teleport uniformly to `source_nodes` (or all nodes).
   - Complexity: Work `O(iter * (m + n))`, Depth `O(iter * log n)`, Memory `O(n)`.
2. **`BetweennessCentrality`** (`YIELD node, score`):
   - Parallel Brandes; requires **strictly positive (`> 0`)** weights. Unnormalized range: `[0, (n-1)(n-2)]` (exact) or `[0, |S|(n-2)]` (approx).
   - Params: `num_source_nodes` (`INT64`, optional `> 0`; omitted or `> n` -> exact; else out-degree proportional reservoir sampling; 0-out-degree nodes never sampled).
   - Complexity: Work `O(s * m * log n)`, Depth `O(m * log n)`, Memory `O(threads * n)`.
3. **`ClosenessCentrality`** (`YIELD node, score`):
   - **Mode Caveat**: `'EXACT'` = **directed**; `'HYBRID'` = **undirected**. Empty reachable set `R(x)` -> `0.0`; non-empty with all 0 distances -> `+Infinity`.
   - Params: `mode` (`'EXACT'` default | `'HYBRID'`), `use_wasserman_faust` (`BOOL`, default `FALSE` -> multiplies by `|R(x)| / (n - 1)` for disconnected graphs), `epsilon` (`FLOAT64`, default `0.1`, `(0, 1)`, `HYBRID` only), `sample_size` (`INT64`, default `0` -> `min(100 * ln(n), n)`, `HYBRID` only).
   - Complexity: `EXACT` `O((m + n) * n * log n)`; `HYBRID` `O((m + n) * s * log n)`; Memory `O(threads * n)`.
4. **`WeaklyConnectedComponents`** (`YIELD node, cluster` in `[0, n)`):
   - ConnectIt asynchronous union-find during loading; no extra params. Work `O(n + m * log n)`, Depth `O(n)`, Memory `O(n)`.
5. **`ModularityClustering`** (`YIELD node, cluster`):
   - Parallel async Louvain (thin wrapper over `CorrelationClustering` with `k_x = weighted_degree(x) / M`).
   - Params: `resolution` (`FLOAT64`, default `1.0`, `>= 0`, typical `[0.5, 5]`; smaller -> larger clusters; `0` -> connected components ignoring 0-weight edges), `max_iterations` (`10`), `max_inner_iterations` (`10`).
   - Complexity: Work `O(iter * inner * m)`, Depth `O(iter * inner * log n)`, Memory `O(m + n)`.
6. **`CorrelationClustering`** (`YIELD node, cluster`):
   - Maximizes LambdaCC `sum(w_xy - resolution * 1 * 1)` — **only clustering algorithm supporting negative, zero, and positive weights**.
   - Params: `resolution` (`FLOAT64`, **REQUIRED — no default!**, finite `>= 0`), `max_iterations` (`10`), `max_inner_iterations` (`10`).
   - Complexity: Work `O(iter * inner * m)`, Memory `O(m + n)`.
7. **`LabelPropagation`** (`YIELD node, cluster`):
   - Majority-vote scheduled via **Largest-Log-First (LLF) coloring** (lock-free, deterministic; weight ties pick higher label ID).
   - Params: `seed_label_property` (`STRING`, optional — **gotcha**: initial seeds CAN change during iterations!), `max_iterations` (`10`, `> 0`).
   - Complexity: Work `O(iter * (m + n))`, Depth `O(iter * Delta * log n)`, Memory `O(m + n)`.
8. **`CliqueFinding`** (`YIELD node, clique`):
   - **Overlapping clusters**: one node can emit **multiple rows** (one per clique). Every clique of size >= 2 is covered; density >= `min_density`; maximal via Bron-Kerbosch pruning; isolated nodes are NOT guaranteed to appear.
   - Params: `min_density` (`FLOAT64`, default `0.9`, `[0, 1]`). **Warning**: `min_density = 1.0` reduces to maximal clique enumeration (exponential worst-case!). Time `n * d^O(log d)` for `< 1`; Memory `O(n * d)`.
9. **Pairwise Node Similarity (`JaccardSimilarity`, `CosineSimilarity`, `CommonNeighborsSimilarity`, `TotalNeighborsSimilarity`)** (`YIELD source_node, target_node, similarity`):
   - Params: `source_nodes` (`ARRAY<GRAPH_NODE>`, **REQUIRED**), `target_nodes` (`ARRAY<GRAPH_NODE>`, **REQUIRED**).
   - Directed out-neighbors `N(x)` only. **ONLY `CosineSimilarity` uses `edge_weight_property`**; the other three ignore edge weights!
   - `NaN` cases: `JaccardSimilarity` -> `NaN` when `|N(x) U N(y)| = 0`; `CosineSimilarity` -> `NaN` when either node has no out-neighbors or 0-norm weights.
   - Complexity: Work `O(|S| * |T| * Delta)`, Depth `O(Delta)`, Memory `O(|S| * |T| + threads * Delta)`.
10. **`ShortestPath`** (`YIELD source_node, target_node, path, cost`):
    - Parallel `rho`-stepping SSSP per source node with deterministic tie-breaking (min hops, then lexicographically min node IDs backwards from target). Self-distance = `0.0`; unreachable = `-1.0` internally.
    - Params: `source_nodes` (`ARRAY<GRAPH_NODE>`, **REQUIRED**), `target_nodes` (`ARRAY<GRAPH_NODE>`, **REQUIRED**).
    - **CRITICAL `RETURN` Mandate**: `ShortestPath` requires **EVERY yielded node variable** (`source_node` **AND** `target_node`) to appear in the `RETURN` clause (e.g., `RETURN source_node.id AS id, 'sp_v1' AS algo_run_id, target_node.id AS target_id, PATH_LENGTH(path) AS int_val, cost AS float_val`). Omitting `target_node` even when `target_nodes` has a single target fails compilation — include a nullable `target_id` column on your output table.
    - Complexity: Work `O(|S| * D * m * log(n^2 / (m * rho)))`.

---

## 7. Production Best Practices & Schema-Free Experimentation Pattern

1. **Expose Element Keys & Minimize Returned Columns**: Always expose key properties in `PROPERTIES (...)`, and return ONLY keys + algorithm output columns (plus `ELEMENT_DEFINITION_NAME(node)` for multi-table runs) to minimize SPU shuffle/export cost.
2. **Community Modeling**: Store `cluster_id` as a node property for point lookups ("which cluster is X in?"), or as a `Community` node + membership edge table for neighborhood scans ("list all members of X's cluster").
3. **Iterative Experimentation Without Repeated `ALTER TABLE`**: Persist tagged experiment runs into generic interleaved child tables and expose them via a `SQL SECURITY INVOKER` view + separate augmented graph (keeping the base table-backed graph for running new algorithms!):

```sql
-- Use CREATE TABLE IF NOT EXISTS so iterative setup scripts are safely re-runnable
CREATE TABLE IF NOT EXISTS {node_table}AlgoProperty (
  id          INT64 NOT NULL,
  algo_run_id STRING(200) NOT NULL,
  target_id   INT64,                -- Populated by ShortestPath (must project target_node!)
  int_val     INT64,
  float_val   FLOAT64,
) PRIMARY KEY (id, algo_run_id),
  INTERLEAVE IN PARENT {node_table};

CREATE TABLE IF NOT EXISTS {node_table}AlgoEdge (
  id          INT64 NOT NULL,
  algo_run_id STRING(200) NOT NULL,
  to_id       INT64 NOT NULL,
  int_val     INT64,
  float_val   FLOAT64,
  CONSTRAINT FK_AlgoDest FOREIGN KEY (to_id) REFERENCES {node_table} (id) NOT ENFORCED,
) PRIMARY KEY (id, algo_run_id, to_id),
  INTERLEAVE IN PARENT {node_table};

-- Write-back tagged run (no schema alteration per experiment)
EXPORT DATA OPTIONS (
  format = 'CLOUD_SPANNER',
  table = '{node_table}AlgoProperty',
  write_mode = 'upsert_ignore_all'
) AS
GRAPH {base_graph_name}
CALL PageRank(node_labels => ['EntityNode'], edge_labels => ['LinksTo'], max_idle_time => '5m')
  YIELD node, score
RETURN node.id, 'pagerank_exp_01' AS algo_run_id, score AS float_val;

-- Aggregate runs into a JSON view for interactive GQL exploration on {base_graph_name}Augmented
CREATE OR REPLACE VIEW {node_table}WithAlgoProps SQL SECURITY INVOKER AS
SELECT
  n.id,
  ANY_VALUE(n.name) AS name,
  JSON_OBJECT(
    IF(COUNT(c.algo_run_id) = 0, [], ARRAY_AGG(c.algo_run_id)),
    IF(COUNT(c.algo_run_id) = 0, [], ARRAY_AGG(
      COALESCE(
        IF(c.int_val IS NULL, NULL, TO_JSON(c.int_val)),
        IF(c.float_val IS NULL, NULL, TO_JSON(c.float_val))
      )
    ))
  ) AS algo_props
FROM {node_table} AS n
LEFT JOIN {node_table}AlgoProperty AS c ON n.id = c.id
GROUP BY n.id;
```

*(Keep `{base_graph_name}` table-backed for running algorithms, and create a separate `{base_graph_name}Augmented` over `{node_table}WithAlgoProps` for GQL querying `n.algo_props.pagerank_exp_01`.)*

---

## 8. Official Documentation

- Graph Algorithms Overview: https://docs.cloud.google.com/spanner/docs/graph/graph-algorithms-overview
- Run Graph Algorithms: https://docs.cloud.google.com/spanner/docs/graph/run-algorithms
- Schema Requirements & Compatibility: https://docs.cloud.google.com/spanner/docs/graph/algorithm-schema-requirements-and-feature-compatibility
- Algorithms Reference: https://docs.cloud.google.com/spanner/docs/graph/algorithms
- Algorithm Best Practices: https://docs.cloud.google.com/spanner/docs/graph/algorithm-best-practices
