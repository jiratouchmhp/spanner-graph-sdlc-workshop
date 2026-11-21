# Cloud Spanner Vector Search: Exact KNN & ScaNN ANN

Comprehensive production reference for **Cloud Spanner Vector Search**
(Enterprise and Enterprise Plus editions): vector column schema and hard array
constraints, exact KNN vs approximate ScaNN ANN distance metrics, ScaNN index
DDL and auto-tuning, inline filtering hierarchy, single-table CTE isolation,
and zero-downtime index rebuilds.

## 1. Edition Gate, Scale Guidance & Embedding Column Schema

- **Edition Requirement:** **Enterprise** or **Enterprise Plus** edition is
  required for **both** exact KNN (`COSINE_DISTANCE`, `EUCLIDEAN_DISTANCE`,
  `DOT_PRODUCT`) and approximate ScaNN ANN (`CREATE VECTOR INDEX`, `APPROX_*`).
  Cloud Monitoring tracks them separately under
  `spanner.googleapis.com/instance/edition/feature_usage` as
  `KNN vector search` and `Vector search`.
- **Scale Guidance:**
  - **Unpartitioned ScaNN ANN:** Scales to **billions** of vectors with
    millisecond-level latency.
  - **PK-Partitioned Exact KNN:** Scales to **trillions** of total vectors when
    each query scopes to a small tenant/user/entity key prefix.

### Recommended Embedding Column DDL
Prefer 32-bit floats (`FLOAT32` / `float4[]`) over 64-bit floats for **2x
storage, memory, and scan bandwidth efficiency** (e.g., 768 dimensions * 4 bytes
= 3,072 bytes/row):

```sql
-- GoogleSQL
CREATE TABLE {table_name} (
  doc_id STRING(64) NOT NULL,
  category STRING(128),
  author_id STRING(64),
  word_count INT64,
  embedding ARRAY<FLOAT32>(vector_length=>768),
  embedding_model_version INT64
) PRIMARY KEY (doc_id);
```

```sql
-- PostgreSQL
CREATE TABLE {table_name} (
  doc_id varchar(64) NOT NULL,
  category varchar(128),
  author_id varchar(64),
  word_count bigint,
  embedding float4[] VECTOR LENGTH 768,
  embedding_model_version bigint,
  PRIMARY KEY (doc_id)
);
```

### Hard Array & `vector_length` Constraints
1. **Valid types:** `vector_length` requires an integer `>= 0` and is supported
   only on `ARRAY<FLOAT32>` / `ARRAY<FLOAT64>` (`float4[]` / `float8[]` in PG).
2. **Mandatory for ScaNN:** `vector_length` is optional for storage, recommended
   for KNN scan optimizations, and **mandatory for `CREATE VECTOR INDEX`**.
3. **No virtual generated columns:** `vector_length` cannot be applied to
   non-stored (virtual) generated columns.
4. **No element-level `NULL`s:** Every stored non-`NULL` vector must have exact
   length `= vector_length` and **zero `NULL` elements** (element-level `NULL`
   raises a runtime error; whole-vector `NULL` returns `NULL`).
5. **`ALTER TABLE` `NOT NULL` Trap:** You can declare an `ARRAY` column
   `NOT NULL` **only in `CREATE TABLE`**. After table creation, `ALTER TABLE`
   **cannot** add an `ARRAY` column with `NOT NULL` or attach `NOT NULL` to an
   existing `ARRAY` column.
6. **Size & Key Quotas:** Max single cell size is **10 MiB**; max non-key
   columns per row is **1,600 MiB**; `ARRAY` columns cannot appear in primary
   keys.

## 2. Vector Distance Functions Matrix (Exact KNN & ScaNN ANN)

| Metric | Exact KNN (GSQL / PG) | Approx ANN (GSQL / PG) | `distance_type` | Element Types | Zero Vector (`[0,0]`) | Closest Sort |
| --- | --- | --- | --- | --- | --- | :---: |
| **Cosine Distance** ($1 - \frac{a \cdot b}{\|a\|\|b\|}$, `[0, 2]`) | `COSINE_DISTANCE` / `spanner.cosine_distance` | `APPROX_COSINE_DISTANCE` / `spanner.approx_cosine_distance` | `'COSINE'` | `FLOAT32`, `FLOAT64` | **ERROR** | **`ASC`** (`0` = identical) |
| **Euclidean (L2)** ($\sqrt{\sum(a_i - b_i)^2}$) | `EUCLIDEAN_DISTANCE` / `spanner.euclidean_distance` | `APPROX_EUCLIDEAN_DISTANCE` / `spanner.approx_euclidean_distance` | `'EUCLIDEAN'` | `FLOAT32`, `FLOAT64` | Allowed | **`ASC`** (`0` = identical) |
| **Dot Product** ($\sum a_i b_i$) | `DOT_PRODUCT` / `spanner.dot_product` | `APPROX_DOT_PRODUCT` / `spanner.approx_dot_product` | `'DOT_PRODUCT'` | **`INT64`**, `FLOAT32`, `FLOAT64` (`int8[]`, `float4[]`, `float8[]`) | Allowed | **`DESC`** (higher = closer) |

- **Common Semantics:** All six functions return `FLOAT64` (`float8`), require
  identical array lengths and matching element types (otherwise error), error on
  any `NULL` element inside an array, and return `NULL` if either vector is
  `NULL`.
- **Metric Selection Guide:**
  1. Match the metric your embedding model was trained with.
  2. **Unit-normalized vectors ($\|v\|_2 = 1$):** All three produce identical
     ranking; `DOT_PRODUCT` avoids magnitude division (slight CPU edge).
  3. **Non-normalized vectors:** **Never** use `DOT_PRODUCT` (not a valid
     distance metric); choose between `COSINE_DISTANCE` (angle) and
     `EUCLIDEAN_DISTANCE` (magnitude + angle).
  4. **Unsure about normalization:** Default to **`COSINE_DISTANCE`** (normalizes
     automatically).

## 3. Exact KNN Search Patterns & Optimizations

Choose **Exact KNN** instead of ScaNN ANN whenever the post-filter candidate
slice is small enough to meet your latency SLA (typically **fewer than a few
tens of thousands of rows** for a ~100 ms budget). Exact KNN provides **100%
recall**, zero vector index storage overhead, and zero index rebuild operations.

### Three Exact KNN Access Patterns
```sql
-- Pattern 1: Unpartitioned Exact KNN (small tables / analytical runs)
SELECT doc_id, title, COSINE_DISTANCE(embedding, @query_vec) AS distance
FROM {table_name} @{scan_method=batch}
WHERE embedding IS NOT NULL
ORDER BY distance ASC
LIMIT 10;

-- Pattern 2: PK-Partitioned Exact KNN (scales to trillions of total vectors)
SELECT doc_id, EUCLIDEAN_DISTANCE(embedding, @query_vec) AS distance
FROM {tenant_table}
WHERE tenant_id = @tenant_id AND embedding IS NOT NULL
ORDER BY distance ASC
LIMIT 5;

-- Pattern 3: Secondary-Index Covering Filtered KNN (index-only scan, no back-join)
CREATE INDEX {secondary_index_name}
ON {table_name}(author_id) STORING (embedding, title); -- PG: INCLUDE (embedding, title)

SELECT author_id, doc_id, title, COSINE_DISTANCE(embedding, @query_vec) AS distance
FROM {table_name}
WHERE author_id = @author_id AND embedding IS NOT NULL
ORDER BY distance ASC
LIMIT 5;
```

### Mandatory KNN Performance Practices
- **Always Top-K (`ORDER BY ... LIMIT K`), never distance threshold:** Writing
  `WHERE COSINE_DISTANCE(embedding, @q) < 0.3` disables specialized top-K vector
  scan optimizations and produces brittle recall across query domains.
- **Use a SQL literal for fixed `LIMIT`:** Write `LIMIT 10` instead of
  `LIMIT @limit` so the optimizer selects the optimal top-K execution plan.
- **Batch scan hint:** Apply `@{scan_method=batch}` for scan-heavy KNN queries
  (already enabled by default for ANN).
- **Parallelism & Benchmarking:** Pre-split large KNN tables (`splits add`) or
  allow load-based splitting under sustained load for maximum scan parallelism.
  Repeated identical benchmark queries hit caches — run warm-up reads with
  varied query vectors.

## 4. ScaNN Vector Indexes & Approximate Nearest Neighbor (ANN)

Spanner ANN implements Google Research's **ScaNN** tree-quantization algorithm
using either a **2-level tree** (`tree_depth = 2`: root centroids ->
`num_leaves`) or a **3-level tree** (`tree_depth = 3`: root -> `num_branches` ->
`num_leaves`).

### DDL Templates: Auto-Tuned + Extra Keys + `STORING` + Filtered Partial Index
```sql
-- GoogleSQL: Filtered (partial) vector index with extra key + STORING columns
CREATE TABLE {table_name} (
  doc_id STRING(64) NOT NULL,
  category STRING(128),
  author_id STRING(64),
  word_count INT64,
  is_published_key BOOL AS (IF(category = 'Published', TRUE, NULL)) HIDDEN,
  embedding ARRAY<FLOAT32>(vector_length=>768)
) PRIMARY KEY (doc_id);

-- Create AFTER bulk loading initial embeddings!
CREATE VECTOR INDEX {vector_index_name}
ON {table_name}(embedding, author_id)      -- Embedding column MUST be first
STORING (word_count, is_published_key)
WHERE embedding IS NOT NULL AND is_published_key IS NOT NULL
OPTIONS (distance_type = 'COSINE');        -- Omit tree_depth/num_leaves to auto-tune
```

```sql
-- PostgreSQL
CREATE TABLE {table_name} (
  doc_id varchar(64) NOT NULL,
  category varchar(128),
  author_id varchar(64),
  word_count bigint,
  is_published_key boolean GENERATED ALWAYS AS (
    CASE WHEN category = 'Published' THEN true END
  ) VIRTUAL HIDDEN,
  embedding float4[] VECTOR LENGTH 768,
  PRIMARY KEY (doc_id)
);

CREATE INDEX {vector_index_name}
ON {table_name}
USING scann(embedding, author_id)
INCLUDE (word_count, is_published_key)
WITH (distance_type = 'COSINE')
WHERE embedding IS NOT NULL AND is_published_key IS NOT NULL;
```

### Auto-Optimization, Manual Tuning & Query-Time Recall Control
- **Auto-Tuning (Recommended Default):** Leave `tree_depth`, `num_leaves`, and
  `num_branches` unset so Spanner chooses tree geometry from data volume.
  Inspect active values via `INFORMATION_SCHEMA`:
  ```sql
  SELECT option_name, option_type, option_value
  FROM INFORMATION_SCHEMA.INDEX_OPTIONS
  WHERE index_name = @vector_index_name;
  -- Returns: system_optimized_tree_depth, system_optimized_num_leaves, system_optimized_num_branches
  ```
- **Manual Tuning Heuristics:**
  - `tree_depth`: Use `2` for `< 10M` rows (`3` for large corpora up to ~**10
    billion** rows).
  - `num_leaves`: Target **200–1,000 rows per leaf** (roughly `rows / 1000`).
    Excessive leaves lengthen index build time and fragment leaf scans.
  - `num_branches` (`tree_depth = 3` only): Target **50–500 leaves per branch**
    (roughly `1000` to `sqrt(rows)`); must satisfy `num_branches < num_leaves`.
- **Mutually Exclusive Query Options:**
  - `options => JSON '{"num_leaves_to_search": 10}'` (positive integer; start at
    **~1% of `num_leaves`**; raise when adding `WHERE` filters).
  - `options => JSON '{"pct_leaves_to_search": 10.0}'` (float in `(0.0, 100.0]`
    — **must include a decimal point**, e.g., `10.0`, not `10`; older indexes
    created prior to Nov 2025 may not support `pct_leaves_to_search`).
- **QPS Tuning Loop:** When recall meets targets but QPS lags, multiply both
  `num_leaves` and `num_leaves_to_search` by a factor $k$ (e.g., `2x` leaves),
  then step down `num_leaves_to_search` until target recall/QPS balance is met.

### Inline Filtering Hierarchy (Fastest to Most Flexible)
1. **Partial (Filtered) Vector Index:** Generate a `HIDDEN` column returning
   `TRUE`/`NULL` and filter with `WHERE embedding IS NOT NULL AND is_published_key IS NOT NULL`.
   **Critical Trap:** Queries **must** write `WHERE embedding IS NOT NULL AND is_published_key IS NOT NULL` —
   writing `WHERE category = 'Published'` will **not** match the partial index!
2. **Extra Key Columns (`ON tbl(embedding, author_id)`):** Must follow the
   embedding column, must be valid key types, and cannot be added/dropped via
   `ALTER`. Accelerates exact equality (`author_id = @a`) best; **`IN` clauses
   do not receive equivalent acceleration**.
3. **`STORING` / `INCLUDE` Columns:** Evaluates filters at leaf level without
   base-table lookups. **Never store or key large text/JSON blobs** inside a
   vector index (dilutes embedding blocks and degrades scan throughput) — store
   a compact hash column if needed and post-filter the full column after top-K.
4. **Selective filter recall drop:** Inline filters run after leaf pruning, so
   always raise `num_leaves_to_search` when adding selective `WHERE` predicates.

## 5. Non-Negotiable ScaNN ANN Query Restrictions & CTE Isolation

Every ScaNN ANN query must obey all five structural constraints:

1. **Mandatory `WHERE embedding IS NOT NULL`:** Unless the embedding column was
   declared `NOT NULL` at `CREATE TABLE`, both `CREATE VECTOR INDEX` and every
   ANN query must filter `WHERE embedding IS NOT NULL`.
2. **Single-Table ANN Block (No General `JOIN` Inside ANN `SELECT`):** The
   `SELECT` block invoking `APPROX_*` must scan a **single indexed base
   table**. General `JOIN`s (`INNER JOIN ... ON`, `LEFT`/`RIGHT`/`FULL` outer
   joins, or multi-table joins) are **forbidden** inside the ANN `SELECT` block.
   The **only** permitted join inside an ANN block is an unconditional comma
   cross-join with a **single-row constant query-vector CTE/subquery**. Always
   isolate the ANN `SELECT ... ORDER BY APPROX_* LIMIT K` inside a CTE and
   perform relational `JOIN`s **outside** the CTE:
   ```sql
   -- Legal relational enrichment: single-table ScaNN ANN CTE -> outer JOIN
   WITH query_vec AS (
     SELECT embedding AS val
     FROM {seed_table}
     WHERE doc_id = @seed_doc_id
     LIMIT 1
   ),
   top_k AS (
     SELECT d.doc_id, d.author_id,
       APPROX_COSINE_DISTANCE(
         d.embedding, query_vec.val,
         options => JSON '{"num_leaves_to_search": 10}'
       ) AS distance
     FROM {table_name} AS d, query_vec
     WHERE d.embedding IS NOT NULL
     ORDER BY distance ASC
     LIMIT 20
   )
   SELECT t.doc_id, t.distance, a.display_name
   FROM top_k AS t
   JOIN {authors_table} AS a ON t.author_id = a.author_id
   ORDER BY t.distance ASC;
   ```
3. **Clause Placement:** One argument must be the indexed embedding column; the
   other must be a constant expression (parameter, literal, or single-row
   cross-joined CTE). `APPROX_*` must be the **sole sort key** in `ORDER BY`,
   immediately followed by `LIMIT`. It **cannot** appear in `WHERE`, `ON`,
   `GROUP BY`, compound expressions, or `SELECT` except when aliased and used as
   the sole `ORDER BY` key.
4. **Spanner Graph Nodes-Only:** In Spanner Graph, ScaNN ANN works on **graph
   nodes ONLY** (single-node `MATCH` -> `NEXT` traversal), **never on graph
   edges** (use exact KNN for edge embeddings).
5. **Non-Determinism & Pre-Splitting:** ScaNN ANN results are non-deterministic
   across invocations, and vector indexes **cannot be pre-split**.

## 6. Zero-Downtime ScaNN Index Lifecycle & Rebuilding

ScaNN cluster centroids are computed once at index creation and remain
**static**. Always create vector indexes **after** initial bulk loads, and
rebuild periodically when vector distributions drift significantly:

### Option 1 — In-Place Zero-Downtime Rebuild (Keep Existing Options)
```sql
-- GoogleSQL
ALTER VECTOR INDEX {vector_index_name} REBUILD;

-- PostgreSQL (CONCURRENTLY is mandatory; REINDEX supports vector indexes only)
REINDEX INDEX CONCURRENTLY {vector_index_name};
```

### Option 2 — Blue-Green Zero-Downtime Rebuild (Change Options / Keys)
```sql
-- 1. Build replacement index alongside active index
CREATE VECTOR INDEX {new_vector_index}
ON {table_name}(embedding)
WHERE embedding IS NOT NULL
OPTIONS (distance_type = 'COSINE', tree_depth = 3, num_leaves = 50000, num_branches = 500);

-- 2. Validate recall & latency using @{FORCE_INDEX={new_vector_index}} (PG: /*@ FORCE_INDEX = ... */)
-- 3. Atomically cut over traffic (GoogleSQL only; WARNING: queries hardcoding FORCE_INDEX on old index WILL FAIL!)
ALTER VECTOR INDEX {old_vector_index} SET OPTIONS (disable_search = true);

-- 4. Drop retired index once validated
DROP VECTOR INDEX {old_vector_index};
```

- **Column evolution without rebuild:**
  - GoogleSQL: `ALTER VECTOR INDEX {vector_index_name} ADD STORED COLUMN {col};`
    (or `DROP STORED COLUMN`).
  - PostgreSQL: `ALTER INDEX {vector_index_name} ADD INCLUDE COLUMN {col};` (or
    `DROP INCLUDE COLUMN`).

## 7. Native Spanner ScaNN vs External Export & Anti-Patterns

- **Native ScaNN vs Standalone Vertex AI Vector Search:** Exporting embeddings
  via Cloud Workflows/Scheduler (`cloudspannerecosystem/spanner-ai`) into
  standalone Vertex AI Vector Search adds ETL sync lag, dual operational
  surfaces, and cross-service hydration joins. For transactional ACID
  consistency and strict regional data residency (e.g., Bangkok
  `regional-asia-southeast3`), **native Spanner ScaNN ANN** keeps vectors,
  metadata, FTS tokens, and graph edges consistent in one database.
- **Top Anti-Patterns to Reject:**
  - Pairing `CREATE VECTOR INDEX` with exact `COSINE_DISTANCE(...)` (vector
    indexes accelerate **only** `APPROX_*` functions).
  - Creating `CREATE VECTOR INDEX` before loading initial rows (empty/skewed
    centroids).
  - Storing `ARRAY<FLOAT64>` without need, or omitting `vector_length=>N`.
  - Writing `JOIN` directly inside an `APPROX_*` `SELECT` block.

Official documentation:
- [Exact KNN Vector Search](https://docs.cloud.google.com/spanner/docs/find-k-nearest-neighbors)
- [Choose a Vector Distance Function](https://docs.cloud.google.com/spanner/docs/choose-vector-distance-function)
- [ScaNN Vector Indexes](https://docs.cloud.google.com/spanner/docs/vector-indexes)
- [Approximate Nearest Neighbor (ANN) Search](https://docs.cloud.google.com/spanner/docs/find-approximate-nearest-neighbors)
- [Vector Index Best Practices](https://docs.cloud.google.com/spanner/docs/vector-index-best-practices)
