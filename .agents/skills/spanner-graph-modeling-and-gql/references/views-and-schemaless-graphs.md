# View-Backed Graphs, Remote Functions & Schemaless Data Management

Authoritative reference for building Spanner Property Graphs over SQL views, integrating Cloud Run / Cloud Functions Remote Functions, modeling schemaless and hybrid graphs with `DYNAMIC LABEL` / `DYNAMIC PROPERTIES`, navigating `JSON` coercion traps, and wiring LangChain GraphRAG (Enterprise / Enterprise Plus, GoogleSQL only).

---

## 1. Property Graphs Created from SQL Views

Spanner SQL views are **virtual (non-materialized)** and **read-only**: Spanner evaluates the view definition at query execution time, and all mutations must target the underlying base tables.

- **Directionality Rule**: You **CAN** define property graph node and edge elements over SQL views, but you **CANNOT** create a SQL view whose query body queries a property graph.
- **Graph Algorithms Exclusion**: Data Boost Graph Algorithms (`PageRank`, `WeaklyConnectedComponents`, `ShortestPath`, etc.) are **ONLY supported** when none of the node or edge elements (including edge endpoint nodes) passed to the algorithm are backed by a view.

### 1.1 Four Production Use Cases for View-Backed Graphs

1. **Row-Level Fine-Grained Access Control (FGAC)**:
   ```sql
   CREATE OR REPLACE VIEW {scoped_node_view} SQL SECURITY DEFINER AS
   SELECT e.emp_id, e.full_name, e.cost_center
   FROM {employee_table} AS e
   WHERE LOWER(e.cost_center) = 'engineering';

   GRANT SELECT ON VIEW {scoped_node_view} TO ROLE {scoped_reader_role};
   ```
   Callers using `{scoped_reader_role}` query the property graph built over `{scoped_node_view}` and see only permitted rows without needing direct `SELECT` privileges on `{employee_table}`. Use `SQL SECURITY INVOKER` when you want caller permissions enforced directly.
2. **SQL Bridge Views for Structural & Type Mismatches**:
   - **Unnesting `ARRAY` Foreign Keys**: `ARRAY` columns cannot appear in `SOURCE KEY` or `DESTINATION KEY`. Unnest via a bridge view:
     ```sql
     CREATE OR REPLACE VIEW {item_tag_edge_view} SQL SECURITY INVOKER AS
     SELECT DISTINCT item_id, tag_id
     FROM {item_table}, UNNEST(tag_ids) AS tag_id
     WHERE tag_id IS NOT NULL;
     ```
     *(Alternative: `UNNEST(tag_ids) AS tag_id WITH OFFSET AS pos` + `KEY (item_id, pos)` + graph `OPTIONS (validate_element_key_uniqueness = false)`.)*
   - **Extracting JSON Foreign Keys**: Project scalar keys via `INT64(JSON_QUERY(metadata, '$.parent_id'))` or `JSON_VALUE(metadata, '$.parent_id')`, filtering `IS NOT NULL`.
   - **Bridging Mismatched Key Types (`INT64` vs. `STRING`)**: Wrap in `SAFE_CAST(target_id AS INT64) AS target_id_int` with `WHERE SAFE_CAST(target_id AS INT64) IS NOT NULL`.
   - **Typed Subgraphs Over Polymorphic Tables**: Split one consolidated `Entities(entity_type, entity_id)` table into typed node views (`WHERE entity_type = 'Asset'`).
3. **Computed Properties via Remote Functions (`LANGUAGE REMOTE`)**: Enrich nodes or edges dynamically by invoking Cloud Run / Cloud Functions endpoints inside a view (see Section 2).
4. **Zero-Migration Schemaless-to-Formalized Transition**: Keep canonical `GraphNode` / `GraphEdge` tables for flexible or multi-label quantified traversals while layering strongly typed SQL views simultaneously:
   ```sql
   CREATE OR REPLACE VIEW {typed_person_view} SQL SECURITY INVOKER AS
   SELECT
     n.id,
     STRING(n.properties.name) AS name,
     INT64(n.properties.age) AS age
   FROM {graph_node_table} AS n
   WHERE n.label = 'person';
   ```
   Enforce `CHECK` constraints on the base `JSON` column so typed casts in the view never fail at query runtime.

### 1.2 Mandatory View Requirements & Property Graph `OPTIONS`

| Requirement / Option | Default | Behavior & Caveats |
| :--- | :--- | :--- |
| **`SQL SECURITY` Clause** | *(Required — no default)* | Every view must declare `SQL SECURITY INVOKER` or `SQL SECURITY DEFINER`. Omitting it fails DDL (`View '<view>' is missing the SQL SECURITY clause.`). |
| **Explicit `KEY (...)` Clause** | *(Required on every view element)* | Views lack physical primary keys; omitting `KEY (...)` fails DDL. |
| **`validate_element_key_uniqueness`** | `true` | **`true` (Strict)**: View MUST match either **Pattern 1** (single base table where `KEY (...)` matches that table's PK — preserves base-table interleaving, FK join elimination, and indexes!) or **Pattern 2** (`GROUP BY` or `SELECT DISTINCT` where `KEY (...)` matches ALL grouped/distinct columns — base-table layout optimizations may be less effective). Optional `WHERE`, `HAVING`, `ORDER BY`, `LIMIT`, `TABLESAMPLE` are permitted.<br>**`false` (Disabled)**: Allows arbitrary SQL (e.g., PK-preserving `JOIN` without `GROUP BY`, `UNNEST ... WITH OFFSET`). **Caution**: You assume sole responsibility for key uniqueness; duplicates cause duplicate graph elements/paths and corrupted `MATCH` results. |
| **`allow_view_with_volatile_column`** | `false` | **`false`**: Rejects graphs over views containing volatile columns.<br>**`true`**: Required when a view invokes volatile functions (`LANGUAGE REMOTE`, `GENERATE_UUID`, `RAND`). **Volatility propagation**: Any volatile expression in a view's `WHERE` or `JOIN` clause marks **every** projected column of that view volatile. **Caution**: Queries may return non-deterministic results within or across executions. |

---

## 2. Function Volatility & Cloud Run / Cloud Functions Remote Functions (Preview)

### 2.1 Function Volatility Categories

- **Immutable**: Identical inputs always produce identical output regardless of DB state or session (e.g., `ABS`, `LENGTH`, `CONCAT`, `LOWER`). **Required** for generated columns; pre-evaluated for constants. Session time-zone dependent functions are NOT immutable.
- **Stable**: Consistent output within a single statement execution; may vary across statements (e.g., `CURRENT_TIMESTAMP`, `CURRENT_DATE`). Optimizer often caches per statement.
- **Volatile**: Output may change even within a single statement for identical inputs (e.g., `GENERATE_UUID`, `RAND`, `LANGUAGE REMOTE` marked `NOT DETERMINISTIC`). **Forbidden** in generated columns and indexes; requires `OPTIONS (allow_view_with_volatile_column = true)` in view-backed property graphs.

### 2.2 Remote Functions Grammar, Wire Protocol & Hard Limits

```sql
CREATE OR REPLACE FUNCTION {remote_fn_name}(x INT64)
  RETURNS INT64 NOT DETERMINISTIC LANGUAGE REMOTE
  OPTIONS (
    endpoint = 'https://asia-southeast1-{project_id}.cloudfunctions.net/{function_name}',
    max_batching_rows = 100  -- Optional; Spanner auto-batches when omitted
  );
```

- **Supported Parameter & Return Types**: `BOOL`, `BYTES`, `DATE`, `INT64`, `JSON`, `NUMERIC`, `STRING`, `TIMESTAMP`, and `ARRAY` of any of those types.
- **HTTPS JSON Wire Protocol**:
  - Request (`POST`): `{"requestId": "...", "calls": [[arg1, arg2], ...]}`
  - Success (`HTTP 200`): `{"replies": [res1, ...]}` — array length MUST match `calls`.
  - Error (non-200): `{"errorMessage": "..."}` (< 1 KB).
  - Auto-retried HTTP codes: **`408`, `429`, `500`, `503`, `504`**.
  - Wide integer rule: `INT64` values outside `[-2^53, 2^53]` (`±9007199254740992`) are JSON-encoded as strings by `TO_JSON_STRING`.
- **7 Hard Limitations**:
  1. **NO fractional Spanner instances (< 1,000 PUs / < 1 node)** — Remote Functions require at least 1 full node (`>= 1,000 PUs`).
  2. **NO PostgreSQL dialect** (GoogleSQL only).
  3. **NO table-valued remote functions (TVFs)** (scalar only).
  4. **NO generated column expressions**.
  5. **NO custom domains** on Cloud Run endpoints.
  6. **At-least-once invocation**: Retries and internal restarts can invoke the endpoint multiple times for the same row — endpoints MUST be idempotent.
  7. **Dual billing**: Standard Spanner compute/storage + Spanner bytes sent/received with Cloud Run / Cloud Functions + Cloud Run / Cloud Functions execution charges.

---

## 3. Schemaless & Hybrid Graphs (`DYNAMIC LABEL` & `DYNAMIC PROPERTIES`)

### 3.1 Schematized vs. Schemaless / Hybrid Decision Matrix

| Criterion | Schematized (Default) | Schemaless / Hybrid (`DYNAMIC LABEL` + `DYNAMIC PROPERTIES`) |
| :--- | :--- | :--- |
| **Best Fit** | Stable schemas, strict SQL types (`TIMESTAMP`, `NUMERIC`), high write throughput on mutated columns, single-label paths. | > 50 node types / > 150 edge types, rapidly evolving attributes, polymorphic edges connecting many node types, latency-sensitive **multi-label quantified paths** (`-[:A\|B]->{1,3}`, `-[]->{1,3}`) expanding > 10,000 intermediate paths. |
| **Multi-Label Path Speed** | Every hop unions and joins across every candidate node & edge table. | Single node table + single edge table -> each hop scans one table pair regardless of label count. |
| **Write Cost** | Direct typed column updates (lowest cost). | `JSON_SET` / `JSON_REMOVE` perform **read-modify-write** on the `JSON` column (higher CPU & lock cost). |

### 3.2 Canonical Hybrid Schemaless Schema Template

Promote hot filter/sort fields to static typed columns alongside `JSON` dynamic properties, and enforce lowercase invariants with `CHECK` constraints:

```sql
CREATE TABLE {graph_node_table} (
  id         INT64 NOT NULL,
  label      STRING(MAX) NOT NULL,
  first_seen TIMESTAMP,          -- Hot static typed column (hybrid pattern)
  properties JSON,
  CONSTRAINT node_label_lower_case CHECK (LOWER(label) = label),
  CONSTRAINT node_property_keys_lower_case CHECK (
    NOT ARRAY_INCLUDES(COALESCE(JSON_KEYS(properties, 1), []), key -> key <> LOWER(key))
  ),
) PRIMARY KEY (id);

CREATE TABLE {graph_edge_table} (
  id         INT64 NOT NULL,
  dest_id    INT64 NOT NULL,
  edge_id    INT64 NOT NULL,
  label      STRING(MAX) NOT NULL,
  created_at TIMESTAMP,
  properties JSON,
  CONSTRAINT edge_label_lower_case CHECK (LOWER(label) = label),
  CONSTRAINT edge_property_keys_lower_case CHECK (
    NOT ARRAY_INCLUDES(COALESCE(JSON_KEYS(properties, 1), []), key -> key <> LOWER(key))
  ),
) PRIMARY KEY (id, dest_id, edge_id),
  INTERLEAVE IN PARENT {graph_node_table} ON DELETE CASCADE;

-- CRITICAL: Exclude the raw JSON column via PROPERTIES ALL COLUMNS EXCEPT (properties)
-- to avoid duplicate nested "properties": {"properties": {...}} in TO_JSON(n)!
CREATE OR REPLACE PROPERTY GRAPH {dynamic_graph_name}
  NODE TABLES (
    {graph_node_table}
      LABEL Entity               -- Optional static super-label alongside dynamic label
      PROPERTIES ALL COLUMNS EXCEPT (properties)
      DYNAMIC LABEL (label)
      DYNAMIC PROPERTIES (properties)
  )
  EDGE TABLES (
    {graph_edge_table}
      SOURCE KEY (id) REFERENCES {graph_node_table} (id)
      DESTINATION KEY (dest_id) REFERENCES {graph_node_table} (id)
      PROPERTIES ALL COLUMNS EXCEPT (properties)
      DYNAMIC LABEL (label)
      DYNAMIC PROPERTIES (properties)
  );

-- Reverse covering index + dynamic label lookup index
CREATE INDEX Idx_{graph_edge_table}_Reverse
ON {graph_edge_table} (dest_id, id, edge_id)
STORING (label, properties),
INTERLEAVE IN {graph_node_table};

CREATE INDEX Idx_{graph_node_table}_Label ON {graph_node_table} (label);
```

### 3.3 Seven Hard Limitations & Operational Patterns for Schemaless Graphs

1. **Single-Table Mandate for `DYNAMIC LABEL`**: At most **ONE** node table may declare `DYNAMIC LABEL`, AND you **cannot** define ANY other node table (even static) in that same property graph. Identical rule applies to `EDGE TABLES`.
   - *Contrast*: **`DYNAMIC PROPERTIES (json_col)`** CAN be used on **any number** of node and edge tables in a schematized or hybrid graph!
2. **Mandatory Lowercase Storage (Silent Drop Trap)**:
   - Stored `DYNAMIC LABEL` strings (`'account'`) and top-level `DYNAMIC PROPERTIES` JSON keys (`{"name": "Ada", "age": 30}`) **MUST be lowercase**.
   - Any row with uppercase/mixed-case `'Account'` **silently receives NO dynamic label** and is skipped by `MATCH (n:Account)`.
   - Queries themselves are **case-insensitive** (`MATCH (n:Account {Age: 30})` matches stored `'account'` and `'age'`).
3. **Defined-Over-Dynamic Precedence**: A statically declared `LABEL` or `PROPERTY` always overrides a conflicting dynamic label or dynamic property of the same name.
4. **Top-Level JSON Keys Only**: Only depth-1 JSON object keys become graph properties. Access nested keys via GoogleSQL JSON dot (`.`) or subscript (`['city']`, `[0]`) syntax.
5. **Duplicate `properties` in `TO_JSON(n)`**: Omitting `PROPERTIES ALL COLUMNS EXCEPT (properties)` exposes the JSON column twice (flattened dynamically AND nested under `"properties"`).
6. **Indexing, Uniqueness, Type Checks & JSON Search Indexes**:
   ```sql
   -- Generated column + NULL_FILTERED index for a per-label dynamic property
   ALTER TABLE {graph_node_table} ADD COLUMN person_age INT64 AS
     (IF(label = 'person', LAX_INT64(properties.age), NULL));
   CREATE NULL_FILTERED INDEX Idx_PersonAge
     ON {graph_node_table} (id, label, person_age), INTERLEAVE IN {graph_node_table};
   -- Re-run CREATE OR REPLACE PROPERTY GRAPH and optionally ANALYZE after adding generated columns!

   -- Per-label uniqueness via HIDDEN generated column + UNIQUE NULL_FILTERED INDEX
   ALTER TABLE {graph_node_table} ADD COLUMN person_email STRING(MAX) AS
     (IF(label = 'person', STRING(properties.email), NULL)) HIDDEN;
   CREATE UNIQUE NULL_FILTERED INDEX Idx_UniquePersonEmail ON {graph_node_table} (person_email);

   -- Per-label existence & JSON_TYPE CHECK constraints
   -- JSON_TYPE returns lowercase: 'object', 'array', 'string', 'number', 'boolean', 'null'
   ALTER TABLE {graph_node_table} ADD CONSTRAINT chk_person_name_exists
     CHECK (IF(label = 'person', properties.name IS NOT NULL, TRUE));
   ALTER TABLE {graph_node_table} ADD CONSTRAINT chk_person_name_str
     CHECK (IF(label = 'person', JSON_TYPE(properties.name) = 'string', TRUE));

   -- Enterprise / Enterprise Plus JSON Search Index (read-only txns)
   ALTER TABLE {graph_node_table} ADD COLUMN props_tokens TOKENLIST AS
     (TOKENIZE_JSON(properties)) HIDDEN;
   CREATE SEARCH INDEX Idx_{graph_node_table}_JsonSearch ON {graph_node_table} (props_tokens);
   ```
7. **Batch JSON Read-Modify-Write DML**: Batch ~100 rows sorted by PK into non-overlapping ranges using a single parameterized `UPDATE`:
   ```sql
   UPDATE {graph_node_table}
   SET properties = JSON_SET(
     properties,
     '$.is_blocked',
     CASE id
       WHEN @ids[OFFSET(0)] THEN @vals[OFFSET(0)]
       WHEN @ids[OFFSET(1)] THEN @vals[OFFSET(1)]
     END,
     create_if_missing => TRUE
   )
   WHERE id IN UNNEST(@ids);
   ```

---

## 4. `JSON` Semantics, Comparison & Coercion Traps

- **Hard `JSON` Specs**: Max nesting depth **80 levels**; max normalized size **10 MB**; UTF-8 only; no comments; object keys sorted lexicographically; array order preserved; duplicate object keys keep **only the first key**. Preserves signed `INT64`, unsigned `UINT64`, and round-trippable `FLOAT64` (use `PARSE_JSON(s, wide_number_mode => 'round')` for non-round-trippable numbers). Cannot use `JSON` columns in PKs or secondary index keys.
- **JSON `null` vs. SQL `NULL` Trap**: JSON `null` is **SQL non-NULL**!
  - `(JSON '{"a":null}').a IS NULL` evaluates to **`FALSE`**; missing key `.b IS NULL` evaluates to **`TRUE`**.
  - `JSON_QUERY(JSON '{"a":null}', '$.a')` returns JSON `null`; `JSON_QUERY(..., '$.b')` returns SQL `NULL`.
- **Cross-Type JSON Sort Order**: `JSON null` < `STRING` < `NUMBER` < `BOOLEAN` (`false < true`) < `ARRAY` (element-wise, longer > prefix) < `OBJECT` (lexicographic sorted pairs, more pairs > prefix).
- **Implicit Coercion to `JSON`**:
  - Equality (`=`, `!=`, list `IN (v1, v2)` where first operand is `JSON`): supports `BOOL`, numeric, `DATE`, `TIMESTAMP`, `UUID`, `STRING`, `ENUM`, `BYTES`, and `ARRAY` of those.
  - Ordering (`<`, `<=`, `>`, `>=`, `BETWEEN`): supports the above **EXCEPT `BYTES`, `RANGE`, and `ENUM`**.
- **3 Dynamic Property Query Traps**:
  1. **Type-strict equality**: Comparing JSON string `"10042"` against integer `10042` returns **zero matches** — extract explicitly with `LAX_INT64(...)` or `INT64(...)`.
  2. **Boolean predicate error**: `WHERE a.is_blocked` fails on a `JSON` boolean — write `WHERE a.is_blocked = TRUE` or `WHERE BOOL(a.is_blocked)`.
  3. **Unsupported operators on raw `JSON`**: Arithmetic (`+`, `-`), string functions (`LOWER`, `LIKE`), aggregates (`SUM`, `AVG`), and `IN UNNEST` require explicit conversion (`INT64()`, `FLOAT64()`, `STRING()`, `BOOL()`, `LAX_INT64()`).

---

## 5. LangChain GraphRAG Integration (Preview) & Regional Wiring

The `langchain-google-spanner` Python package provides five components: `SpannerVectorStore`, `SpannerLoader` / `SpannerDocumentSaver`, `SpannerChatMessageHistory`, **`SpannerGraphStore`** (persists LLM-extracted nodes/edges via `LLMGraphTransformer` and executes ISO GQL), and **`SpannerGraphQAChain`** (NL -> ISO GQL -> Spanner execution -> grounded answer).

**Regional Data-Residency Example (Bangkok + Singapore)**: Host the Cloud Spanner Enterprise database (`SpannerGraphStore` + `SpannerVectorStore`) in `regional-asia-southeast3` (Bangkok) for Thailand PDPA data residency, and configure regional Vertex AI endpoints (`gemini-embedding-001` and `gemini-2.5-flash`) in `asia-southeast1` (Singapore, ~25 ms RTT; `global` fallback applies only to LLM synthesis, never regional text-embedding endpoints).

---

## 6. Official Documentation

- Property Graphs from Views Overview & How-To: https://docs.cloud.google.com/spanner/docs/graph/graph-with-views-overview
- Create Graphs from Views: https://docs.cloud.google.com/spanner/docs/graph/graph-with-views-how-to
- Manage Schemaless Data: https://docs.cloud.google.com/spanner/docs/graph/manage-schemaless-data
- Cloud Run / Cloud Functions Remote Functions: https://docs.cloud.google.com/spanner/docs/cloud-run-remote-function
- Function Volatility: https://docs.cloud.google.com/spanner/docs/concepts/function-volatility
- Working with JSON Data: https://docs.cloud.google.com/spanner/docs/working-with-json
- LangChain Integration: https://docs.cloud.google.com/spanner/docs/langchain
