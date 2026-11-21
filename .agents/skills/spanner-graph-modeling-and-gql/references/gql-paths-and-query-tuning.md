# ISO GQL Queries, Quantified Paths, Built-in Functions & Query Tuning

Authoritative reference for ISO/IEC 39075:2024 GQL & ISO/IEC 9075-16:2023 SQL/PGQ query semantics, `GRAPH_TABLE` interoperability, quantified paths, path modes, search prefixes, built-in GQL functions, execution plan profiling, the 8 GQL rewrite rules, and exact optimizer hint syntax on Cloud Spanner Enterprise / Enterprise Plus (GoogleSQL).

---

## 1. ISO GQL Query Structure & Complete Statement Traps

Every standalone GQL query begins with `GRAPH {graph_name}` followed by one or more linear query statements chained by `NEXT`. Each statement transforms an incoming **working table** (starting with 1 empty row) into an outgoing working table.

| Statement | Syntax | Critical Semantics & Traps |
| :--- | :--- | :--- |
| **`MATCH` / `OPTIONAL MATCH`** | `[ OPTIONAL ] MATCH [ hint ] graph_pattern` | Joins incoming working table with pattern matches: **`INNER JOIN`** when sharing variable names, **`CROSS JOIN`** when disjoint! `OPTIONAL MATCH` performs a `LEFT OUTER JOIN` (`NULL` for unmatched variables). **Trap**: Filter optional variables inside `OPTIONAL MATCH ... WHERE ...`; a trailing `FILTER` runs post-join and silently turns it into an inner join. |
| **`FILTER`** | `FILTER [ WHERE ] bool_expr` | Post-statement row filter (acts like SQL `HAVING` after `GROUP BY` + `NEXT`). Drops `FALSE` and `NULL`. Supports horizontal aggregates. |
| **`LET`** | `LET v1 = expr1 [, v2 = expr2 ...]` | Adds computed columns without changing row count. **Traps**: (1) Cannot reference a variable defined in the same `LET` (`LET a = x, b = a` -> ERROR; split into two `LET` statements); (2) Cannot redefine an existing variable in the same linear scope; (3) Scoped to the current linear statement unless projected in `RETURN` before `NEXT`. Supports horizontal aggregates. |
| **`FOR`** | `FOR elem IN array_expr [ WITH OFFSET [ AS off ] ]` | Unnests `ARRAY` (like SQL `UNNEST`); does NOT preserve order; empty or `NULL` array yields **0 rows**. **Syntax Trap**: Placing a `WITH` statement right after `FOR` is parsed as `WITH OFFSET` (`Expected keyword OFFSET` error) — add `WITH OFFSET` or use `RETURN`. |
| **`CALL`** | `[ OPTIONAL ] CALL [ PER () ] tvf(args) [ YIELD c [ AS a ], ... ]`<br>`[ OPTIONAL ] CALL ( [ var, ... ] ) { subquery }` | `CALL` runs per row; `CALL PER ()` runs **once** over the whole working table (named TVFs only; no `OPTIONAL`; replaces input columns with yielded columns). In inline `CALL (p) { ... }`, ONLY listed outer **node/edge** variables may be multiply-declared inside inner `MATCH` (scalars cannot be redeclared). Empty `CALL () { ... }` treats inner `p` as a brand-new variable (Cartesian product). Combined outer + inner column names must be unique. |
| **`WITH`** | `WITH [ ALL \| DISTINCT ] item [, ...] [ GROUP BY ... ]` | Projects/aggregates mid-pipeline; drops unmentioned columns. Implicit `GROUP BY` on all groupable items when any aggregate is present. No window functions; no column ordinals in `GROUP BY`; unique output names required. |
| **`ORDER BY` / `LIMIT` / `OFFSET` / `SKIP`** | `ORDER BY expr [ COLLATE ... ] [ ASC\|DESC ]`<br>`LIMIT n` / `OFFSET n` / `SKIP n` | `SKIP` is a synonym for `OFFSET`. **CRITICAL Trap**: Standalone `ORDER BY` before `RETURN` is **silently ignored** unless IMMEDIATELY followed by `LIMIT` or `OFFSET` (`ORDER BY ... RETURN ... LIMIT` -> UNORDERED!). Always put `ORDER BY` inside `RETURN ... ORDER BY ... [OFFSET m] [LIMIT n]` or right before `LIMIT`/`OFFSET`. No ordinals. |
| **`RETURN`** | `RETURN [ ALL \| DISTINCT ] { expr [ AS alias ] \| * } [, ...] [ GROUP BY ... ] [ ORDER BY ... ] [ OFFSET m ] [ LIMIT n ]` | Ends a linear statement. **Traps**: (1) **`OFFSET` MUST precede `LIMIT`** inside `RETURN` (`LIMIT 5 OFFSET 10` -> ERROR); (2) Unique output names mandatory (`RETURN a.id, b.id` -> `Ambiguous name: id`); (3) **Final top-level `RETURN` CANNOT emit raw `GRAPH_ELEMENT` or `GRAPH_PATH`** (`Returning expressions of type GRAPH_ELEMENT is not allowed`) — intermediate `RETURN n NEXT ...`, `WITH n`, subqueries, and `GRAPH_TABLE` CAN return raw elements! Wrap final elements/paths in `SAFE_TO_JSON(x)`. |
| **`NEXT`** | `linear_stmt_1 NEXT linear_stmt_2` | Pipes working table into the next linear statement. **Scoping Trap**: Non-element scalar columns carried across `NEXT` (or from `LET`) can ONLY be referenced in the **outermost `WHERE` of `MATCH`**, NEVER inside `{id: carried_id}`. |
| **Set Operations** | `[ FULL [OUTER] \| OUTER \| LEFT [OUTER] \| INNER ] { UNION \| INTERSECT \| EXCEPT } { ALL \| DISTINCT }` | Matches columns **by name** (order-independent) using common supertypes; `FULL`/`LEFT`/`INNER` control column propagation (missing columns padded with `NULL`). **Restrictions**: no hints, no `CORRESPONDING`, **cannot mix different operator types** in one chain (e.g., `UNION ALL` + `EXCEPT DISTINCT` -> ERROR), no parentheses, left-associative. |

---

## 2. SQL Interoperability: `GRAPH_TABLE` & GQL Subqueries

### 2.1 Embedding GQL Inside SQL with `GRAPH_TABLE`

```sql
SELECT gt.node_el.id AS entity_id, r.risk_score
FROM GRAPH_TABLE (
  {graph_name}
  MATCH (node_el:Account)-[:Transfers]->(:Account {is_blocked: TRUE})
  RETURN DISTINCT node_el
) AS gt
JOIN {risk_table} AS r ON gt.node_el.id = r.account_id;
```

`GRAPH_TABLE` may `RETURN` raw `GRAPH_ELEMENT` values to outer SQL so outer clauses can access `gt.node_el.id` or `SAFE_TO_JSON(gt.node_el)` (only the final outermost `SELECT` cannot project raw `GRAPH_ELEMENT`).

### 2.2 GQL Subqueries (`ARRAY`, `EXISTS`, `IN`, `VALUE`)

Enclosed in `{ ... }`; optionally start with `GRAPH {other_graph}` (otherwise inherit the enclosing graph and start with `MATCH`):
1. `ARRAY { gql_query }` -> `ARRAY<T>` (0 rows -> empty `[]`, never `NULL`).
2. `EXISTS { gql_query | match_stmt | graph_pattern }` -> `BOOL` (never `NULL`).
3. `val [ NOT ] IN { gql_query }` -> `BOOL` (single comparable column).
4. `VALUE { gql_query }` -> scalar `T` (single column; 0 rows -> `NULL`; **> 1 row -> runtime error**).

**Subquery Variable Shadowing Prohibition**: You **CANNOT** redeclare an outer graph variable inside `VALUE`/`ARRAY`/`EXISTS`/`IN` subqueries (`redefining graph element variables in a subquery is not allowed`). Bind a fresh variable and correlate via `WHERE inner_n = outer_n` (or use `CALL (outer_n) { ... }`).

---

## 3. Patterns, Quantified Paths, Path Modes & Built-in Functions

### 3.1 Element Patterns, Labels & Property Filters

- **Edge directions**: Right `-[...]->` / `->`; Left `<-[...]-` / `<-`; Any-direction `-[...]-` / `-`. (**`<-[e:Label]->` is INVALID syntax in Spanner GQL!**)
- **Label expressions**: `:Label` or `IS Label`; wildcard `:%`; logical `!X`, `X & Y`, `X | Y`, `(...)`. Backtick-escape reserved keywords (e.g., `` (o:`Order`) ``, `` (g:`Group`) ``, `` -[:`Contains`]-> ``; GoogleSQL reserves `ALL`, `ANY`, `CONTAINS`, `CREATE`, `DEFAULT`, `FOR`, `FROM`, `FULL`, `GRAPH_TABLE`, `GROUP`, `IN`, `INNER`, `IS`, `JOIN`, `LEFT`, `LIMIT`, `MERGE`, `NO`, `NOT`, `OF`, `ON`, `OR`, `ORDER`, `OUTER`, `OVER`, `PARTITION`, `RANGE`, `RECURSIVE`, `RIGHT`, `SELECT`, `SET`, `STRUCT`, `TO`, `UNION`, `UNNEST`, `USING`, `WHERE`, `WINDOW`, `WITH`, `WITHIN`).
- **Property filter `{k: v}` traps**:
  - Cannot mix `{...}` and `WHERE` in the same element filler (`(n:Account {id: 1} WHERE n.is_blocked)` -> ERROR).
  - Empty `{}` is an error.
  - `{id: NULL}` evaluates `= NULL` (always unknown / never matches), NOT `IS NULL` — write `WHERE n.id IS NULL`.
  - Cannot reference other elements' variables or aggregates inside `{...}`.
- **Any-direction double matching**: `(n:Account)-[t:Transfers]-(m:Account)` matches every directed edge **twice** (`(x, y)` and `(y, x)`). Disambiguate via `WHERE n IS SOURCE OF t` or `IS DESTINATION OF t`.

### 3.2 Quantified Paths, Group Variables & Horizontal Aggregates

- **Syntax**: Fixed `{k}` (`k >= 1`) or bounded `{m, n}` (`0 <= m <= n`, `n >= 1`; `{, 4}` defaults `m = 0`). **Upper bound `n` MUST be a finite positive integer** (unbounded `{1,}`, `*`, `+` are unsupported).
- **Rules**: Quantifications cannot be nested (sequential quantifications in one pattern are valid). Minimum path length of a quantified pattern and overall path node count must be `> 0`. Variables declared outside a parenthesized subpath `(...)` cannot be referenced inside its `WHERE`; variables declared inside CAN be referenced outside.
- **Group variables**: Any variable declared inside a quantified pattern is singleton inside it and becomes an ordered **`ARRAY<GRAPH_ELEMENT>`** outside it. Group variables cannot be multiply-declared outside their quantified pattern.
- **Horizontal Aggregation**: Summarizes **exactly one** array/group variable (e.g., `SUM(e.amount)`, `COUNT(DISTINCT e.amount)`, `ARRAY_AGG(e.id)` preserving array order without `ORDER BY`):
  1. Allowed **ONLY in `LET`, `FILTER`, or `WHERE` — NEVER in `RETURN`**!
  2. Must reference **exactly one** array variable (`AVG(arr1 + arr2)` -> ERROR).
  3. **No nested** horizontal aggregates.
  4. Every array reference inside the aggregate is treated as a scalar element (`SUM(arr / ARRAY_LENGTH(arr))` -> ERROR; fix by hoisting `LET len = ARRAY_LENGTH(arr)` first, then `LET val = SUM(arr / len)`).

### 3.3 Path Modes, Search Prefixes & `COST` Expressions

| Construct | Options | Exact Semantics & Constraints |
| :--- | :--- | :--- |
| **Path Modes** | `WALK` (default), `ACYCLIC`, `TRAIL` (optional `PATH`/`PATHS`) | `WALK`: repeats allowed. `ACYCLIC`: no repeated nodes. `TRAIL`: no repeated edges. Scoped to a single path/subpath (not across comma-separated patterns). **Mutual Exclusion Workaround**: Cannot write `MATCH ANY SHORTEST TRAIL ->{1,4}` directly, **BUT** subpath placement works: `MATCH ANY SHORTEST (TRAIL ->{1,4})`! |
| **Search Prefixes** | `ALL` (default), `ANY`, `ANY SHORTEST`, `ANY CHEAPEST` | Partitions matches by `(first_node, last_node)`, picking 1 path per pair (`ANY`: arbitrary; `ANY SHORTEST`: min hops; `ANY CHEAPEST`: min total `COST`). Must precede an entire top-level path pattern. Non-`ALL` prefixes forbid reusing interior non-endpoint variables in another comma-separated pattern of the same `MATCH`. |
| **`COST <expr>`** | `-[e:Transfers COST e.amount]->{1,3}` | Required for `ANY CHEAPEST`. **ALL quantified edge patterns MUST define `COST`**. Edge-only; local variables only. Must evaluate to a **STRICTLY POSITIVE FINITE number** (`NULL`, `0`, negative, `Inf`, `NaN` -> runtime error!). |

### 3.4 Built-in GQL Functions, Operators & JSON Serialization

- **Types & Casting**: `GRAPH_ELEMENT<T>` (`GRAPH_NODE` / `GRAPH_EDGE`) and `GRAPH_PATH<NODE_TYPE, EDGE_TYPE>`. Castable ONLY to `JSON` (`TO_JSON` / `SAFE_TO_JSON`). Supertyping requires matching element kind and compatible property superset.
- **Element & Path Functions**:
  - `ELEMENT_ID(el)`, `SOURCE_NODE_ID(e)`, `DESTINATION_NODE_ID(e)` -> opaque `STRING` IDs **valid ONLY within that single query execution** (never persist or join across queries!).
  - `ELEMENT_DEFINITION_NAME(el)` -> `STRING`; `LABELS(el)` / `PROPERTY_NAMES(el)` -> `ARRAY<STRING>` (original case; defined overrides dynamic).
  - `PATH(n1, e1, n2, ...)` -> `GRAPH_PATH` (errors on `NULL`, mis-interleaved args, or disconnected edge).
  - `NODES(p)`, `EDGES(p)`, `PATH_FIRST(p)`, `PATH_LAST(p)`, `PATH_LENGTH(p)` (edge count).
  - `IS_ACYCLIC(p)` -> `TRUE` iff **NO** repeated nodes; `IS_TRAIL(p)` -> `TRUE` iff **NO** repeated edges; `IS_SIMPLE(p)` -> `TRUE` iff no repeated nodes or only head == tail.
  - `IS_FIRST(k) OVER ([PARTITION BY ...] [ORDER BY ...])` -> `BOOL` (`TRUE` for first `k` rows; `k <= 0` errors).
- **Operators & Predicates**:
  - `p || q`: path concatenation (deduplicates joining node; errors if `PATH_LAST(p) != PATH_FIRST(q)`; `NULL` if operand is `NULL`).
  - `ALL_DIFFERENT(e1, e2, ...)` / `SAME(e1, e2, ...)`: **errors if any argument is `NULL`**.
  - `node IS [NOT] SOURCE [OF] edge`, `node IS [NOT] DESTINATION [OF] edge`, `el IS [NOT] LABELED label_expr`.
  - `PROPERTY_EXISTS(el, prop_name)`: **errors** if `prop_name` is undeclared anywhere in the graph schema!
- **Serialization**: Prefer **`SAFE_TO_JSON(x)`** (emits JSON `null` for unsupported column types like `TOKENLIST` instead of failing) over `TO_JSON(x)`. Pass `stringify_wide_numbers => TRUE` to preserve precision for `INT64`/`NUMERIC` values outside `FLOAT64` range.

---

## 4. Closed-Loop Query Profiling & Live Cancellation

1. **Inspect Schema**: `gcloud spanner databases ddl describe {database_id} --instance={instance_id} --project={project_id}`
2. **Profile Plan** (fall back to `--query-mode=PLAN` if execution times out):
   ```bash
   gcloud spanner databases execute-sql {database_id} \
     --instance={instance_id} --project={project_id} \
     --query-mode=PROFILE \
     --sql="GRAPH {graph_name} MATCH ..."
   ```
3. **Read Plan Signals**:
   - **`SeekableKeySize: [0]`**: Full table/index scan — check missing index, non-leading key column, or non-sargable predicate (`LOWER(n.code) = @code`, `CAST(n.id AS STRING) = @id`). `[1]+` confirms a direct B-tree prefix seek.
   - **`Residual Condition`**: Predicates applied *after* storage scan.
   - **Join Selection**: `Local Distributed Cross Apply` / `Distributed Cross Apply` (`APPLY_JOIN`) excels for selective anchors (1 to a few thousand rows) and interleaved children; `Hash Join` (`HASH_JOIN`) excels for bulk unselective scans.
   - **Row Amplification**: Compare `Rows Returned` vs. `Storage Read` to spot Cartesian blowups.
4. **Live Active Queries & Cancellation**: Inspect `SPANNER_SYS.ACTIVE_QUERIES_SUMMARY` (`>1s`, `>10s`, `>100s` buckets) and `SPANNER_SYS.OLDEST_ACTIVE_QUERIES` (`QUERY_ID`, `START_TIME`, `TEXT`, `SESSION_ID`, `PRIORITY`; cannot query inside RW txns). Cancel a runaway query (best-effort, confirm with user first): `CALL cancel_query('{query_id}')`.
5. **Parameterization Hygiene**: Always bind literals via `@param` in online GQL to hit the query-plan cache and save compilation CPU (parameters cannot replace identifiers and are unsupported in Data Boost Graph Algorithms).

---

## 5. The 8 Verified GQL Query Rewrite Rules (Apply BEFORE Hints)

1. **Start from the Lowest-Cardinality Anchor**: Place the most selective node filter (`{id: @id}` or selective indexed property) first in one connected `MATCH` chain.
2. **Eliminate Multi-Arm Star Query Cartesian Explosions (`M x N x K`)**:
   - *Aggregations/Collections (Preferred)*: Split arms across `NEXT` stages with early `GROUP BY` (carry arrays across `GROUP BY` with `ANY_VALUE(...)` because Spanner forbids `GROUP BY` on `ARRAY`), OR use nested `ARRAY { MATCH (n2)-[:HasTag]->(t:Tag) WHERE n2 = n RETURN t.name }` subqueries.
   - *Flat Cross-Product Required*: Use `@{factorized_mode=factorize_both}` on conditionally independent arms:
     ```sql
     GRAPH {graph_name}
     MATCH (a:Account {id: @id})-[e1:Transfers]->(a1:Account),
           @{factorized_mode=factorize_both} (a)-[e2:Owns]->(p:Person)
     RETURN a.id, a1.id AS peer_id, p.id AS owner_id;
     ```
     **Gotcha**: Spanner **rejects** `factorized_mode` with `RETURN COUNT(*)` (`A join in the query cannot be executed in factorized execution mode`) — always return per-arm columns and verify gain with `PROFILE`.
   - *4+ Wide Branches*: Fan out parallel 1-hop queries client-side using `WHERE n.id IN UNNEST(@anchor_ids)`.
3. **Specify ALL Node & Edge Labels Across Every `MATCH` and `NEXT` Boundary**: Unlabeled patterns scan every table via `Distributed Union`. Always re-state `:NodeLabel` on bound node variables after `NEXT` (`NEXT MATCH (r:Router)-[:LocatedIn]->(z:Zone)`).
4. **Eliminate Redundant `NEXT` Joins on Folded Edges**: When an `N:1` edge is folded into the source node table (`r.site_id`), project `r.site_id` with `WHERE r.site_id IS NOT NULL` instead of traversing another hop.
5. **Bound Hub / Super-Node Fan-Out & Fix `OPTIONAL MATCH + LIMIT`**:
   ```sql
   -- Per-hop edge cap inside a quantified path (pairs best with a DESC interleaved index!)
   GRAPH {graph_name}
   MATCH (a1:Account {is_blocked: TRUE})
     -[e:Transfers WHERE e IN {
       MATCH -[selected_e:Transfers]->
       FILTER IS_FIRST(@max_edges) OVER (
         PARTITION BY SOURCE_NODE_ID(selected_e)
         ORDER BY selected_e.create_ts DESC
       )
       RETURN selected_e
     }]->{1,5}(a2:Account)
   RETURN a1.id AS src_id, a2.id AS dst_id;
   ```
   Also use midpoint sampling (`FILTER IS_FIRST(1) OVER (PARTITION BY mid)` across `NEXT`) or a precomputed `is_hub BOOL` filter `((a:Account WHERE a.is_hub = FALSE)-[e:Transfers]->(b:Account)){1,3}`. For `OPTIONAL MATCH` + `ORDER BY` + `LIMIT` (which Spanner cannot push through `DistributedOuterApply`), bound the optional branch inside `IS_FIRST` or an `ARRAY { MATCH ... ORDER BY ... LIMIT k RETURN ... }` subquery.
6. **Keep Predicates Sargable**: Compare raw indexed columns directly (`n.code = @code`) instead of wrapping them in expressions unless backed by a generated-column index.
7. **Optimize Deep Quantified Paths (`{m, n}`)**:
   - *Lightweight recursion*: Unreferenced intermediate node variables are pruned automatically, **BUT referencing intermediate node group variables** (e.g., `ARRAY_TRANSFORM(mid, n -> n.id)` or `MATCH p = ... NODES(p)`) forces `RecursiveScan` to scan the node table at every hop and buffer `ARRAY<GRAPH_NODE>` structs! Read intermediate IDs from the **edge group variable** instead (`ARRAY_TRANSFORM(e, el -> el.dst_id)`).
   - *Late materialization across `NEXT`*: Carry only lightweight keys (`dst.id`, served via FK join elimination with zero node scans) through `GROUP BY` / `ORDER BY` / `LIMIT`, then re-match `NEXT MATCH (dst:NodeLabel) WHERE dst.id = target_id` to fetch wide columns only for surviving winners.
   - *Converging paths*: `-[e:Transfers]->{1,3}@{factorized_mode=factorize_left}(a2:Account)` deduplicates converging destination keys before probing `Account` (disallowed with `RETURN COUNT(*)`; effective at high convergence ratios).
8. **Avoid `ARRAY_AGG + IN UNNEST` Across `NEXT` & Decorrelate `NOT EXISTS`**:
   - Pass bound node variables directly (`RETURN DISTINCT f NEXT MATCH (f:Person)...`) rather than collecting IDs into an array and unnesting.
   - Rewrite correlated `NOT EXISTS { MATCH (a2)-[:Transfers]->(b) WHERE a2 = a }` (unpushed `Anti-Semi Apply` re-scanning per outer row) to **`OPTIONAL MATCH (a)-[:Transfers]->(b) FILTER b IS NULL`** (compiles to a set-based hash anti-semi join).

---

## 6. Exact GQL Hint Syntax & Guardrails

| Hint Scope | Exact Verified Syntax | Placement & Restrictions |
| :--- | :--- | :--- |
| **Statement** | `@{JOIN_METHOD=HASH_JOIN} GRAPH {graph_name} ...` | Immediately before `GRAPH`. |
| **Element (Table/Index)** | `(@{FORCE_INDEX=IdxName} n:Account {id: @id})`<br>`-[@{FORCE_INDEX=RevIdx} e:Transfers]-`<br>`-[@{INDEX_STRATEGY=FORCE_INDEX_UNION} :Transfers]-`<br>`(@{FORCE_INDEX=_BASE_TABLE} n:Account)` | **MUST appear FIRST** inside node/edge filler (`-[e:Label @{...}]->` fails with `Expected "]" but got "@"`!). |
| **Traversal (Join)** | `MATCH @{JOIN_METHOD=APPLY_JOIN} ...`<br>`, @{JOIN_METHOD=HASH_JOIN, HASH_JOIN_BUILD_SIDE=BUILD_RIGHT} (...)`<br>`-[e]-> @{JOIN_METHOD=APPLY_JOIN} (m)`<br>`(p) @{JOIN_METHOD=APPLY_JOIN} -[e]->`<br>`-[e]-> @{JOIN_METHOD=APPLY_JOIN} ((a)-[s]->(b))` | Allowed on `MATCH`, between comma-separated patterns, edge-to-node, node-to-edge, and subpath-to-edge. **NOT allowed** between two subpaths or between a subpath and a node! Join methods: `APPLY_JOIN`, `HASH_JOIN`, `MERGE_JOIN`. |
| **Group** | `GROUP @{GROUP_METHOD=HASH_GROUP} BY p.name` | Between `GROUP` and `BY`. |

**Three Hint Guardrails**:
1. **NEVER force `@{JOIN_METHOD=HASH_JOIN}` on anchored variable-length paths (`->{1,4}`)**: Forces full-table build/probe at every recursion depth instead of expanding only the active frontier.
2. **Preserve FK Destination Join Elimination**: Placing an element hint on the destination node (`(@{...} b:Account)`) or reading non-key properties of `b` can disable FK join elimination and trigger a full node scan.
3. **Always Re-Profile**: Verify every hint with `--query-mode=PROFILE` and revert immediately if latency or storage reads regress.

---

## 7. Official Documentation

- Queries Overview: https://docs.cloud.google.com/spanner/docs/graph/queries-overview
- Work with Paths: https://docs.cloud.google.com/spanner/docs/graph/work-with-paths
- Query Tuning Best Practices: https://docs.cloud.google.com/spanner/docs/graph/best-practices-tuning-queries
- GQL Query Statements & Hints: https://docs.cloud.google.com/spanner/docs/reference/standard-sql/graph-query-statements
- Graph Patterns: https://docs.cloud.google.com/spanner/docs/reference/standard-sql/graph-patterns
- GQL Functions: https://docs.cloud.google.com/spanner/docs/reference/standard-sql/graph-gql-functions
- Graph Operators & Subqueries: https://docs.cloud.google.com/spanner/docs/reference/standard-sql/graph-operators
- `GRAPH_TABLE` & SQL Interoperability: https://docs.cloud.google.com/spanner/docs/reference/standard-sql/graph-sql-queries
- Active Queries & `cancel_query`: https://docs.cloud.google.com/spanner/docs/introspection/oldest-active-queries
