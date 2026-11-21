# openCypher v9 to Cloud Spanner Graph (ISO GQL) Parity Reference

Complete translation reference for migrating Neo4j Community Edition openCypher v9 queries, expressions, functions, and mutations to Cloud Spanner Graph (**ISO/IEC 39075 GQL** + **ISO SQL/PGQ** + GoogleSQL).

> **Edition & Dialect Gate:** Spanner Graph requires **Enterprise or Enterprise Plus edition** and the **GoogleSQL dialect** (PostgreSQL interface is unsupported).

## 1. Core Paradigm & Syntax Differences

| Aspect | openCypher v9 (Neo4j CE) | Cloud Spanner Graph (ISO GQL + GoogleSQL) |
| :--- | :--- | :--- |
| **Terminology** | nodes, relationships, node labels, relationship types, clauses | nodes, **edges**, node labels, **edge labels**, **statements** (execution unit, e.g., `MATCH`) vs **clauses** (modifiers, e.g., `WHERE`) |
| **Edge Labels** | Exactly one relationship type per relationship | Nodes AND edges can hold **one or more labels** |
| **Schema Model** | Schemaless (dynamic labels & properties) | Explicit `CREATE PROPERTY GRAPH` supporting **schematized** typed tables AND **schemaless/hybrid** (`DYNAMIC LABEL (str_col)` + `DYNAMIC PROPERTIES (json_col)`). Strict rule: at most 1 `DYNAMIC LABEL` node table and 1 `DYNAMIC LABEL` edge table, never mixed with static tables in the same `NODE TABLES`/`EDGE TABLES` list; store dynamic labels and top-level JSON keys in **lowercase**. |
| **Graph Scope** | Single implicit default graph | Multiple named graphs; every query MUST start with **`GRAPH {graph_name}`** |
| **Parameters** | `$param` | **`@param`** |
| **Edge Uniqueness** | Default **relationship uniqueness** (no repeated edges per `MATCH`) | Default **`WALK`** (allows repeated edges — loops on cycles!); MUST write **`MATCH TRAIL`** for openCypher uniqueness |
| **Returning Elements** | `RETURN n` returns node/edge directly | **Not supported in top-level `RETURN`** — MUST wrap in **`SAFE_TO_JSON(n) AS n`** (or `TO_JSON(n) AS n`). Intermediate `RETURN ... NEXT`, `WITH`, subqueries, and `GRAPH_TABLE` can pass raw `GRAPH_ELEMENT`s. (`SAFE_TO_JSON` avoids errors on unsupported property types such as `TOKENLIST`.) |

## 2. Graph Pattern Matching & Path Semantics (Worked Rewrites)

### 2.1 Cycle Safety: `MATCH TRAIL` vs Default `WALK`

```sql
-- openCypher (returns 0 rows because edge t1 cannot be traversed twice):
MATCH (src:Account)-[t1:Transfers]->(dst:Account)-[t2:Transfers]->(src)-[t1]->(dst)
WHERE src.id = 16
RETURN src.id AS src_id, dst.id AS dst_id;

-- Spanner Graph (MUST specify TRAIL for identical semantics; omitting TRAIL defaults to WALK!):
GRAPH {graph_name}
MATCH TRAIL (src:Account)-[t1:Transfers]->(dst:Account)-[t2:Transfers]->(src)-[t1]->(dst)
WHERE src.id = 16
RETURN src.id AS src_id, dst.id AS dst_id;
```

### 2.2 Variable-Length Quantified Paths & Path Variables

```sql
-- openCypher:
MATCH p = (src:Account)-[:Transfers*1..3]->(dst:Account)
WHERE src.id = 16
RETURN edges(p);

-- Spanner Graph (quantifier OUTSIDE arrow with {min,max}; bind edge array directly for lower scan cost):
GRAPH {graph_name}
MATCH TRAIL (src:Account)-[e:Transfers]->{1,3}(dst:Account)
WHERE src.id = 16
RETURN SAFE_TO_JSON(e) AS edges_json;

-- Path-variable equivalent (natively supports PATH_LENGTH, NODES, EDGES):
GRAPH {graph_name}
MATCH p = TRAIL (src:Account)-[:Transfers]->{1,3}(dst:Account)
WHERE src.id = 16
RETURN PATH_LENGTH(p) AS hops, SAFE_TO_JSON(EDGES(p)) AS edges_json;
```

*Performance Tip:* Binding edge group variable `e` directly avoids materializing intermediate nodes inside `RecursiveScan` when only edge fields are needed; use `MATCH p = ...` with `NODES(p)`/`EDGES(p)`/`PATH_LENGTH(p)` when inspecting full paths or multi-segment quantified patterns.

### 2.3 Shortest & Weighted Cheapest Paths

- `shortestPath(...)` -> **`MATCH ANY SHORTEST`** + `ARRAY_LENGTH(e)` or `PATH_LENGTH(p)`.
- Weighted shortest paths -> **`MATCH ANY CHEAPEST`** with positive finite `COST <expr>` on edge patterns.
- **`allShortestPath(...)` is NOT supported** in Spanner Graph.

```sql
-- openCypher:
MATCH (src:Account {id: 7}), (dst:Account {id: 20}),
      p = shortestPath((src)-[*1..10]->(dst))
RETURN length(p) AS path_length;

-- Spanner Graph (hop count):
GRAPH {graph_name}
MATCH ANY SHORTEST (src:Account {id: 7})-[e:Transfers]->{1,10}(dst:Account {id: 20})
RETURN ARRAY_LENGTH(e) AS path_length;

-- Spanner Graph (weighted cheapest path with positive finite COST):
GRAPH {graph_name}
MATCH p = ANY CHEAPEST (src:Account {id: 7})-[e:Transfers COST e.fee]->{1,10}(dst:Account {id: 20})
RETURN PATH_LENGTH(p) AS hops, SAFE_TO_JSON(p) AS cheapest_path;
```

### 2.4 `OPTIONAL MATCH ... WHERE` Placement Trap

Putting a filter on an optionally matched variable in a standalone `FILTER` statement after `OPTIONAL MATCH` turns the LEFT join into an INNER join and drops `NULL` rows. Keep optional predicates **inside** the `OPTIONAL MATCH` clause:

```sql
-- CORRECT: preserves Accounts with zero large transfers (dst_id = NULL)
GRAPH {graph_name}
MATCH (a:Account {id: @account_id})
OPTIONAL MATCH (a)-[t:Transfers]->(b:Account)
WHERE t.amount > 1000
RETURN a.id AS src_id, b.id AS dst_id, t.amount AS amount;
```

## 3. Statements & Clauses Parity Matrix

| openCypher Clause | Spanner Graph Status & Exact Rewrite |
| :--- | :--- |
| `MATCH`, `OPTIONAL MATCH`, `WHERE`, `UNION`, `UNION ALL` | **Supported** (prefix query with `GRAPH {graph_name}`) |
| `RETURN`, `WITH` | **Supported**, BUT **explicit aliasing (`AS alias`) is REQUIRED** for all non-trivial expressions (`RETURN EXTRACT(YEAR FROM p.birthday)` fails -> add `AS birth_year`) |
| `ORDER BY`, `SKIP`, `LIMIT` | Attach to `RETURN` (`RETURN ... ORDER BY ... OFFSET @skip LIMIT @lim`) — standalone `ORDER BY` after `RETURN` is invalid. `SKIP`/`OFFSET` and `LIMIT` require a **constant or `@param`** (`LIMIT VALUE { ... }` subquery fails!) |
| `UNWIND` | **Replaced by `FOR`:** `GRAPH {graph_name} LET arr = [1, 2, 3] FOR num IN arr RETURN num;` |
| `MANDATORY MATCH` | **Not supported** |
| `CREATE`, `DELETE`, `SET`, `REMOVE`, `MERGE` | **Not supported in GQL** — use GoogleSQL DML or Spanner Mutation API (Section 6) |
| `CALL [YIELD ...]` | **APOC / GDS / custom DB stored procedures: Not supported** (rewrite in application or SQL/GQL). **ISO GQL `CALL` / `CALL PER ()` IS supported** — see Section 3.1 below. |

### 3.1 ISO GQL `CALL`, `CALL PER ()` & Built-In Graph Algorithms

Spanner Graph supports four `CALL` forms replacing many Neo4j subquery and GDS workflows:
1. **Per-row named TVF:** `[OPTIONAL] CALL tvf(args) [YIELD col [AS alias], ...]`
2. **Whole-working-table TVF:** `CALL PER () tvf(args) [YIELD ...]` (named TVFs only; no `OPTIONAL`; un-yielded input columns are dropped).
3. **Inline subquery:** `[OPTIONAL] CALL ([var, ...]) { subquery }` (no `YIELD`; outer node/edge variables listed in `(...)` can be multiply-declared inside).
4. **Spanner Graph Algorithms** *(Preview, Enterprise/Enterprise Plus, executed via Data Boost)* replacing common Neo4j GDS procedures via `EXPORT DATA OPTIONS (format = 'CLOUD_SPANNER', table = '{output_table}', write_mode = 'upsert_ignore_all') AS GRAPH {graph_name} [MATCH ...] { CALL | CALL PER () } <Algorithm>(...) YIELD ... RETURN ...`:
   - *Centrality:* `PageRank`, `BetweennessCentrality`, `ClosenessCentrality`
   - *Community / Components:* `WeaklyConnectedComponents`, `ModularityClustering`, `CorrelationClustering`, `LabelPropagation`, `CliqueFinding`
   - *Similarity & Pathfinding:* `JaccardSimilarity`, `CosineSimilarity`, `CommonNeighborsSimilarity`, `TotalNeighborsSimilarity`, `ShortestPath`

## 4. Data Types, Functions & Operators Parity

### 4.1 Type Mapping & Heterogeneous Composite Rules

- **Structural:** `Node`, `Edge`, `Path` supported internally (serialize with `TO_JSON`/`SAFE_TO_JSON` for final output).
- **Primitives:** `INT` -> `INT64`, `FLOAT` -> `FLOAT64`, `STRING` -> `STRING`, `BOOLEAN` -> `BOOL`. `INT` -> `FLOAT64` coerces automatically per GoogleSQL rules.
- **Composite Types:**
  - `LIST` -> **`ARRAY<T>`** (strictly homogeneous) OR **`JSON`** (mandatory for heterogeneous lists mixing types).
  - `MAP` -> **`STRUCT<...>`** (fixed schema) OR **`JSON`** (mandatory for dynamic keys or heterogeneous value types).

### 4.2 Structural Functions & Pattern Expressions

| Scope | openCypher v9 | Spanner Graph (ISO GQL / GoogleSQL) |
| :--- | :--- | :--- |
| Node / Edge | `exists(n.prop)` | `PROPERTY_EXISTS(n, prop)` |
| Node / Edge | `id(n)` | **Not supported** — reference explicit PK property (e.g., `n.id`) |
| Node / Edge | `properties(n)` | `TO_JSON(n)` or `SAFE_TO_JSON(n)` |
| Node / Edge | `keys(n)` | `PROPERTY_NAMES(n)` |
| Node / Edge | `labels(n)` / `type(e)` | `LABELS(n)` / `LABELS(e)` |
| Node / Edge | `n.prop` / `n[n.key]` | Static `.` supported; **dynamic `n[expr]` NOT supported** |
| Edge | `startNode(e)`, `endNode(e)` | **Not supported** — bind endpoints in `MATCH` or read source/dest FK columns |
| Path | `length(p)` / `nodes(p)` / `relationships(p)` | **`PATH_LENGTH(p)`**, **`NODES(p)`**, **`EDGES(p)`** (plus `PATH_FIRST(p)`, `PATH_LAST(p)`, `IS_ACYCLIC(p)`, `IS_TRAIL(p)`, `IS_SIMPLE(p)`, `PATH(...)`, `p \|\| q`). Wrap `NODES(p)`/`EDGES(p)` in `SAFE_TO_JSON(...)` for top-level output or project via `ARRAY_TRANSFORM(...)`. |
| Pattern Expr | `size(pattern)` | **Not supported** — rewrite as scalar subquery: `VALUE { MATCH pattern RETURN COUNT(*) AS cnt }` |

### 4.3 Scalar, Aggregate, List, Math, String & Logical Parity

| Category | openCypher v9 | Spanner Graph (GoogleSQL) |
| :--- | :--- | :--- |
| **Scalar** | `coalesce` / `head` / `last` | `COALESCE` / `ARRAY_FIRST` / `ARRAY_LAST` |
| | `size(list)` / `size(string)` | `ARRAY_LENGTH` / `LENGTH` |
| | `timestamp()` | `UNIX_MILLIS(CURRENT_TIMESTAMP())` |
| | `toBoolean` / `toFloat` / `toInteger` / `toString` | `CAST(expr AS BOOL \| FLOAT64 \| INT64 \| STRING)` |
| **Aggregate** | `avg`, `count`, `max`, `min`, `sum`, `DISTINCT` | `AVG`, `COUNT`, `MAX`, `MIN`, `SUM`, `DISTINCT` |
| | `collect` | **`ARRAY_AGG`** |
| | `percentileCont`, `percentileDisc`, `stDev` | `PERCENTILE_CONT`, `PERCENTILE_DISC`, `STDDEV` |
| | `stDevP` | **Not supported** |
| **List** | `range` / `reverse` | `GENERATE_ARRAY` / `ARRAY_REVERSE` |
| | `tail(list)` | **Not supported** -> `ARRAY_SLICE(arr, 1, -1)` (or `ARRAY_SLICE(arr, 1, ARRAY_LENGTH(arr) - 1)`) |
| | list `+` / `IN` / `[]` | `ARRAY_CONCAT` / **`ARRAY_INCLUDES`** / `[]` |
| **Math** | `abs`, `ceil`, `floor`, `rand`, `round`, `sign`, `exp`, `log`, `log10`, `sqrt`, trig funcs | Identical uppercase GoogleSQL functions |
| | `e` / `pi` | `EXP(1)` / `ACOS(-1)` |
| | `degrees(r)` / `radians(d)` | `r * 90 / ASIN(1)` / `d * ASIN(1) / 90` |
| | `%` (modulo) / `^` (power) | **`MOD(x, y)`** / **`POW(x, y)`** |
| **String** | `left`, `right`, `ltrim`, `rtrim`, `trim`, `replace`, `reverse`, `split` | Identical uppercase GoogleSQL functions |
| | `substring` / `toLower` / `toUpper` | `SUBSTR` / `LOWER` / `UPPER` |
| | `STARTS WITH` / `ENDS WITH` | `STARTS_WITH` / `ENDS_WITH` |
| | `CONTAINS` / string `+` | **`REGEXP_CONTAINS`** / **`CONCAT`** |
| **Comparison** | `=`, `<>`, `<`, `>`, `<=`, `>=`, `IS [NOT] NULL` | Identical |
| | Chained `a < b < c` | **Not supported** -> `a < b AND b < c` |
| **Boolean** | `AND`, `OR`, `NOT` | Identical |
| | `XOR` | **Not supported** -> **`bool_1 <> bool_2`** |
| **Expressions** | `CASE`, `EXISTS` subquery | Supported |
| | Map projection / List comprehension | **Not supported** -> `STRUCT(...)` / **`GENERATE_ARRAY` + `ARRAY_TRANSFORM` + `ARRAY_FILTER`** |

```sql
-- openCypher list comprehension: [x IN range(1, 5) WHERE x % 2 = 0 | x * 10]
-- Spanner Graph rewrite:
GRAPH {graph_name}
LET nums = GENERATE_ARRAY(1, 5)
RETURN ARRAY_TRANSFORM(
  ARRAY_FILTER(nums, x -> MOD(x, 2) = 0),
  x -> x * 10
) AS even_tens;
```

## 5. GoogleSQL Array Function Edge-Cases for Cypher Rewrites

- **`ARRAY(subquery)`:** Returns empty `[]` (never `NULL`) on 0 rows; preserves order ONLY if subquery includes `ORDER BY`; fails on `> 1` column or nested `ARRAY` (wrap multi-column subqueries in `SELECT AS STRUCT`).
- **`ARRAY_FIRST(arr)` / `ARRAY_LAST(arr)`:** **Throws a runtime error on an empty array** (`[]`); guard with `IF(ARRAY_LENGTH(arr) > 0, ARRAY_FIRST(arr), NULL)` or `arr[SAFE_OFFSET(0)]`. Returns `NULL` if `arr IS NULL`.
- **`ARRAY_LENGTH(arr)`:** Returns `0` for `[]`, `NULL` for `NULL`.
- **`ARRAY_SLICE(arr, start_offset, end_offset)`:** Both offsets are **inclusive**; supports 0-based and negative indices (`-1` = last element); clamps out-of-bounds indices; returns `[]` if `start > end` or empty input; returns `NULL` if any arg is `NULL`.
- **`ARRAY_FILTER(arr, e -> pred)` / `ARRAY_TRANSFORM(arr, e -> expr)`:** Optional 2nd lambda parameter `(e, i)` provides 0-based index; `ARRAY_FILTER` drops elements where predicate evaluates to `FALSE` or `NULL`.
- **`ARRAY_INCLUDES` / `ARRAY_INCLUDES_ALL` / `ARRAY_INCLUDES_ANY` / `ARRAY_IS_DISTINCT`:** `ARRAY_IS_DISTINCT` treats two `NULL` elements as duplicates (`FALSE`).
- **`ARRAY_CONCAT(a1, a2, ...)` (or `||`):** Returns `NULL` if *any* input array is `NULL`.
- **`ARRAY_MIN` / `ARRAY_MAX`:** Ignores `NULL` elements; returns `NaN` if any element is `NaN`.
- **`ARRAY_TO_STRING(arr, delim[, null_text])`:** Accepts `ARRAY<STRING>` or `ARRAY<BYTES>`; omits `NULL`s when `null_text` is omitted.
- **`GENERATE_ARRAY(start, end[, step])`:** Inclusive; accepts `INT64`/`NUMERIC`/`FLOAT64`; errors on `step = 0`, `NaN`, or oversized output.
- **`GENERATE_DATE_ARRAY(start, end[, INTERVAL n unit])`:** Units `DAY`/`WEEK`/`MONTH`/`QUARTER`/`YEAR` (default `1 DAY`); default cap is **`16,000` elements**.

## 6. Graph Mutation Parity (GoogleSQL DML & Mutation API)

Mutate underlying relational node and edge tables inside Spanner read-write transactions (respecting Nodes-before-Edges RI and the `80,000` mutations/commit ceiling):

```sql
-- 1. Cypher CREATE nodes & edges (nodes MUST exist before edges if RI enforced)
INSERT INTO Person (id, name) VALUES (100, 'Alex');
INSERT INTO PersonOwnAccount (id, account_id, create_time)
VALUES (100, 1000, CURRENT_TIMESTAMP());

-- 2. Cypher MATCH ... CREATE -> INSERT ... SELECT FROM GRAPH_TABLE
-- CRITICAL: GRAPH_TABLE MUST end with RETURN and unique column aliases to avoid Ambiguous name errors!
INSERT INTO AccountTransferAccount (id, to_id, create_time, amount)
SELECT src_id, dst_id, CURRENT_TIMESTAMP(), 100
FROM GRAPH_TABLE(
  {graph_name}
  MATCH (a:Account {id: 1000}), (oa:Account)
  WHERE oa <> a
  RETURN a.id AS src_id, oa.id AS dst_id
);

-- 3. Cypher MATCH ... SET -> UPDATE ... WHERE pk IN { GRAPH ... RETURN ... }
UPDATE Account
SET is_blocked = FALSE
WHERE id IN {
  GRAPH {graph_name}
  MATCH TRAIL (a:Account WHERE a.id = @src_id)-[:Transfers]->{1,2}(b:Account)
  RETURN b.id
};

-- 4. Cypher MERGE -> INSERT OR UPDATE
INSERT OR UPDATE INTO Person (id, country) VALUES (100, 'Thailand');

-- 5. Cypher SET/DELETE ... RETURN -> GoogleSQL THEN RETURN
UPDATE Person AS p
SET p.country = 'Thailand'
WHERE p.id = 100
THEN RETURN id, name;

-- 6. Cypher DETACH DELETE -> ON DELETE CASCADE on interleaved/FK edge tables
DELETE FROM Account WHERE id = 1000 THEN RETURN id;
```

- **Without `ON DELETE CASCADE`:** Delete incident edges before deleting the node row, or Spanner throws a referential integrity violation.
- **Bulk / Automated Graph Mutations:** Use **Partitioned DML** for updates/deletes exceeding `80,000` mutations/commit, and **Row Deletion Policies (TTL)** for automated node/edge expiration.

## 7. High-Frequency Translation Anti-Patterns

```sql
-- WRONG: unaliased expression, raw element return, standalone ORDER BY, size(pattern)
-- MATCH (p:Person) RETURN p, EXTRACT(YEAR FROM p.birthday), size((p)-[:OWNS]->()) ORDER BY p.id LIMIT 10;

-- RIGHT: GRAPH header, SAFE_TO_JSON, explicit AS aliases, VALUE subquery, ORDER BY/LIMIT on RETURN
GRAPH {graph_name}
MATCH (p:Person)
RETURN
  SAFE_TO_JSON(p) AS person_json,
  EXTRACT(YEAR FROM p.birthday) AS birth_year,
  VALUE { MATCH (p)-[:OWNS]->() RETURN COUNT(*) } AS owned_accounts
ORDER BY p.id
OFFSET @offset_rows
LIMIT @limit_rows;
```

- **Never assume `ARRAY_FIRST(arr)` returns `NULL` on `[]`:** It raises an error; guard with `arr[SAFE_OFFSET(0)]`.
- **Never omit `RETURN` or unique column aliases inside `GRAPH_TABLE(...)`:** Duplicate column names (e.g., `a.id` and `b.id`) fail with `Ambiguous name: id`.
- **Never mix `DYNAMIC LABEL` with static tables in the same `NODE TABLES`/`EDGE TABLES` list:** Isolate schemaless tables in a dedicated `PROPERTY GRAPH`.

- **Official Documentation:** [openCypher to Spanner Graph Reference](https://docs.cloud.google.com/spanner/docs/graph/opencypher-reference), [Work with Paths](https://docs.cloud.google.com/spanner/docs/graph/work-with-paths), [Graph Query Statements](https://docs.cloud.google.com/spanner/docs/reference/standard-sql/graph-query-statements), [Insert, Update, or Delete Graph Data](https://docs.cloud.google.com/spanner/docs/graph/insert-update-delete-data), [GoogleSQL Array Functions](https://docs.cloud.google.com/spanner/docs/reference/standard-sql/array_functions).
