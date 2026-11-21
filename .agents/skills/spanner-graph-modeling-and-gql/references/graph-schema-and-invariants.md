# Spanner Graph Schema Design, Structural Invariants & Physical Indexing

Authoritative reference for `CREATE PROPERTY GRAPH` DDL, the 8 compiler-verified structural invariants, physical storage layout, indexing, topology folding, cascading deletes, and DML mutations in Cloud Spanner Enterprise / Enterprise Plus (GoogleSQL).

---

## 1. Property Graph Data Model & Complete DDL Grammar

Spanner Graph projects rows of existing relational tables (or SQL views) declaratively as graph **nodes** and **directed edges** with **zero ETL and zero data duplication**. Mutations run directly against the underlying tables via standard GoogleSQL DML, Partitioned DML, or Mutation APIs.

### 1.1 `CREATE PROPERTY GRAPH` and `DROP PROPERTY GRAPH`

```sql
CREATE [ OR REPLACE ] PROPERTY GRAPH [ IF NOT EXISTS ] {property_graph_name}
  NODE TABLES ( element [, ...] )
  [ EDGE TABLES ( element [, ...] ) ]
  [ OPTIONS (
      [ validate_element_key_uniqueness = { true | false } ]
      [, allow_view_with_volatile_column = { true | false } ]
  ) ];

DROP PROPERTY GRAPH [ IF EXISTS ] {property_graph_name};
```

### 1.2 Element, Label, Property & Dynamic Clause Grammar

```sql
element:
  {table_or_view_name} [ AS {element_alias} ]
  [ KEY ( col1 [, ...] ) ]
  -- Required for EDGE TABLES only:
  [ SOURCE KEY ( edge_col [, ...] ) REFERENCES {node_alias} [ ( node_col [, ...] ) ]
    DESTINATION KEY ( edge_col [, ...] ) REFERENCES {node_alias} [ ( node_col [, ...] ) ] ]
  [ { label_and_properties_list | element_properties } ]
  [ DYNAMIC LABEL ( string_col_name ) ]
  [ DYNAMIC PROPERTIES ( json_col_name ) ]

label_and_properties_list:
  { LABEL {label_name} | DEFAULT LABEL [ OPTIONS ( [ description = '...' ] [, synonyms = ['...'] ] ) ] }
  [ NO PROPERTIES
    | PROPERTIES [ ARE ] ALL COLUMNS [ EXCEPT ( col [, ...] ) ]
    | PROPERTIES ( value_expr [ AS prop_name ]
        [ OPTIONS ( [ description = '...' ] [, synonyms = ['...'] ] ) ] [, ...] )
  ] [ ... ]
```

### 1.3 Critical DDL Lifecycle & Mapping Rules

- **No `ALTER PROPERTY GRAPH`**: All graph schema changes — adding/removing elements, changing labels, or exposing newly added table columns after `ALTER TABLE ADD COLUMN` — MUST re-run `CREATE OR REPLACE PROPERTY GRAPH ...`.
- **Mutual Exclusion**: `OR REPLACE` and `IF NOT EXISTS` cannot be combined in the same statement.
- **Named Schemas & Naming**: `{property_graph_name}` may be schema-qualified (e.g., `CREATE OR REPLACE PROPERTY GRAPH analytics.FinanceGraph ...`) via `CREATE SCHEMA`, and must not collide with any existing table, view, or property graph name in that schema.
- **Dependency Enforcement**: Spanner blocks `DROP TABLE` and `ALTER TABLE ... DROP COLUMN` for any table or column referenced by a property graph until you first remove that element/property via `CREATE OR REPLACE PROPERTY GRAPH`. `DROP PROPERTY GRAPH` drops only graph metadata; underlying tables and rows stay intact.
- **Metadata Introspection**: Query `INFORMATION_SCHEMA.PROPERTY_GRAPHS` to inspect live graph definitions.
- **Implicit Defaults**: Omitting `label_and_properties_list` applies `DEFAULT LABEL PROPERTIES ARE ALL COLUMNS`, where `DEFAULT LABEL` takes the element's `AS` alias (or base table name if unaliased).
- **NL Metadata `OPTIONS` vs. Label Aliases**: `OPTIONS (description = '...', synonyms = ['...'])` is valid on `DEFAULT LABEL` and individual derived properties to guide natural-language interfaces. Writing `LABEL LinkA SYNONYMS (LinkB)` without `OPTIONS` is a **syntax error**; to expose two queryable GQL labels simultaneously during a rename, declare both explicitly: `LABEL LinkA PROPERTIES (...) LABEL LinkB PROPERTIES (...)`.
- **Derived Properties**: Scalar GoogleSQL expressions referencing only columns of that input table (e.g., `CONCAT(city, ', ', country) AS address`, `salary + bonus AS total_comp`, `CAST(port_id AS STRING) AS port_str`). **Subqueries are disallowed** (`Property value expression of <name> cannot contain a subquery`). An explicit `AS prop_name` alias is required whenever the expression is not a bare column reference.
- **Row-to-Element Mapping & `NULL` Keys**:
  - Rows where any `KEY` column is `NULL` are **silently skipped** during `MATCH`.
  - Edge rows whose `SOURCE KEY` or `DESTINATION KEY` is `NULL` (e.g., root CEO in `Employees(emp_id, manager_id)`) or matches zero nodes emit zero edges. If a reference matches multiple nodes (e.g., partial composite key), a single edge row silently maps to **multiple** edges.
- **Undirected / Symmetric Relationships**: Spanner Graph stores strictly directed edges. Model symmetric relations using one of two patterns:
  1. **Single directed row + reverse index + any-direction GQL `-[:Friend]-`**: Minimizes write amplification; watch for double-counting on unanchored scans.
  2. **Doubled directed rows `(A, B)` and `(B, A)` written atomically in one transaction under `PRIMARY KEY (src_id, dst_id)` + directed GQL `-[:Friend]->`**: Faster adjacency scans from high-degree start nodes; **NEVER** query doubled rows with undirected `-[:Friend]-` or every neighbor is returned twice.

---

## 2. The 8 Compiler-Verified Structural Invariants

Verify all 8 invariants before running `gcloud spanner databases ddl update`:

| # | Invariant | Exact Spanner Compiler Error / Silent Bug | Remediation |
| :-: | :--- | :--- | :--- |
| **1** | **Unique Element Aliases Across Nodes & Edges** | `The node table '<alias>' is defined more than once; use a unique name` / `<Table> is defined more than once; use a unique name.` | Assign unique `AS <alias>` whenever a table appears in both `NODE TABLES` and `EDGE TABLES` (e.g., `Account` + `Account AS Owns`) or multiple times in `EDGE TABLES`. |
| **2** | **`REFERENCES` Targets the Node Alias** | `The referenced node table '<name>' is not defined in the property graph` | `SOURCE KEY ... REFERENCES <node_alias>` MUST match the node's `AS` alias (never the base table name when aliased, and never a `LABEL` name). Define all referenced nodes in `NODE TABLES`. |
| **3** | **Shared Labels Have Identical Property Signatures** | `The label '<L>' is defined with different properties. One definition of the label has 2 property declarations: '[id, code]'. Another definition has 1 property declarations: '[id]'.` | Every table sharing `LABEL <L>` MUST expose the exact same property names and SQL types (use aliases or `CAST(NULL AS <type>) AS missing_col` to align, or split labels). Within one element table, a property name cannot map to two different expressions across labels (`Property location has more than one definition in the element table ...`). |
| **4** | **Reference Full Composite Node Key & Exact Types** | Arity mismatch (omitted col list): `The number of referencing columns in the edge table does not match that of referenced columns in the node table...`<br>Type mismatch: `Data types and/or annotations of the referencing columns in the edge table do not match those of the referenced columns in the node table`<br>**SILENT BUG**: Supplying a partial explicit column list (`REFERENCES ChildNode(child_id)` when PK is `(parent_id, child_id)`) compiles but silently duplicates edges across every matching `child_id`! | List **every** column of a composite node key (including parent PK columns for interleaved child nodes) in `KEY`, `SOURCE KEY`, `DESTINATION KEY`, and `REFERENCES (...)`. Bridge mismatched types (`INT64` vs `STRING`) with a SQL bridge view using `SAFE_CAST`. |
| **5** | **Explicit Unique `KEY` on Views (or Disable Validation)** | Missing key: `The node table '<view>' does not have primary key defined; graph element table keys must be explicitly defined`<br>Unverifiable uniqueness: `failed to create the graph element '<view>' based on view '<view>', because we cannot verify the specified element key (...) is unique` | Always specify `KEY (...)` on view-backed elements. Ensure the view matches single-table PK or `GROUP BY` / `SELECT DISTINCT` over key columns, OR set `OPTIONS (validate_element_key_uniqueness = false)` when uniqueness is guaranteed by application logic. |
| **6** | **Graph-Wide Property Type Uniqueness** | `The property declaration of 'id' has type conflicts. There is an existing declaration of type STRING. There is a conflicting one of type INT64` | Across the **entire property graph** — even on unrelated labels or across node and edge tables — a property name MUST have one uniform SQL data type. Rename via `AS`, exclude via `EXCEPT (...)`, or `CAST(...)` to a shared type. |
| **7** | **Explicit `SQL SECURITY` on Every View** | `View '<view>' is missing the SQL SECURITY clause.` | Include `SQL SECURITY INVOKER` (caller privileges) or `SQL SECURITY DEFINER` (definer privileges for FGAC) on every `CREATE VIEW`. |
| **8** | **Table `KEY` Matches PK Superset or `UNIQUE` Index** | `Neither the primary keys nor any unique index defined on the property graph element source table '<T>' provides the uniqueness guarantee for graph element '<E>' belonging to the graph '<G>'...` | Use the table's primary key (or superset) or add a `UNIQUE` (or `UNIQUE NULL_FILTERED`) index on the `KEY` columns. Mark `KEY` columns `NOT NULL` to prevent silent `MATCH` omissions. |

---

## 3. Physical Schema Design, Interleaving & Indexing

### 3.1 Primary Keys & Anti-Patterns

- **Anti-Pattern — Leading Synthetic `edge_id`**: NEVER define an edge table with `PRIMARY KEY (edge_id)`. Outgoing traversals from a source node cannot seek `src_id`, forcing full edge-table scans and preventing parent interleaving.
- **Canonical Edge PK**: `PRIMARY KEY (src_id, dst_id [, uniquifier_col])` where optional `uniquifier_col` (e.g., `created_at` or `edge_id`) supports parallel multi-edges between the same node pair.
- **Avoid Monotonic Leading Keys**: Do not lead node or standalone edge PKs with sequential timestamps or auto-incrementing integers (creates write hotspots). Prefer **bit-reversed sequence `INT64`** (8 bytes), random/hashed `INT64`, or **native `UUID`** (16 bytes, lowercase `[8]-[4]-[4]-[4]-[12]`) over `STRING(36)` (36 bytes inflates secondary indexes and multi-hop joins).
- **`NOT NULL` Key Hygiene**: Declare PK, `SOURCE KEY`, and `DESTINATION KEY` columns `NOT NULL` whenever non-optional so the optimizer prunes null-check branches across multi-hop plans.
- **Keep Junction PKs Narrow**: Never pack 4–6 audit/status columns into a junction PK; Spanner automatically copies all PK columns into every secondary index.

### 3.2 Canonical Interleaved Forward, Reverse, Filter & Top-K Index Template

```sql
CREATE TABLE {parent_node} (
  id   INT64 NOT NULL,
  name STRING(MAX),
) PRIMARY KEY (id);

CREATE TABLE {dest_node} (
  id         INT64 NOT NULL,
  create_ts  TIMESTAMP,
  close_ts   TIMESTAMP,
  is_blocked BOOL,
  nick_name  STRING(MAX),
) PRIMARY KEY (id),
  ROW DELETION POLICY (OLDER_THAN(close_ts, INTERVAL 90 DAY));

-- 1. Forward traversal: co-locate outgoing edges with parent node
CREATE TABLE {edge_table} (
  id         INT64 NOT NULL,
  target_id  INT64 NOT NULL,
  create_ts  TIMESTAMP NOT NULL,
  amount     FLOAT64,
  CONSTRAINT FK_DestNode FOREIGN KEY (target_id)
    REFERENCES {dest_node} (id) ON DELETE CASCADE,
) PRIMARY KEY (id, target_id, create_ts),
  INTERLEAVE IN PARENT {parent_node} ON DELETE CASCADE;

-- 2. Reverse & any-direction traversal: remote interleaved covering index
-- NOTE: INTERLEAVE IN (no "PARENT" keyword!) matches columns positionally by type (target_id -> {dest_node}.id)
CREATE INDEX Idx_{edge_table}_ByDest
ON {edge_table} (target_id, id, create_ts)
STORING (amount),
INTERLEAVE IN {dest_node};

-- 3. Property-filtering, sparse NULL_FILTERED, and per-node Top-K indexes
CREATE INDEX Idx_{dest_node}_ByNickName ON {dest_node} (nick_name) STORING (is_blocked);
CREATE NULL_FILTERED INDEX Idx_{dest_node}_ByCloseTs ON {dest_node} (close_ts);

-- Forward edge range filter:
CREATE INDEX Idx_{edge_table}_FwdTime
ON {edge_table} (id, create_ts),
INTERLEAVE IN {parent_node};

-- Reverse edge range filter:
CREATE INDEX Idx_{edge_table}_RevTime
ON {edge_table} (target_id, create_ts),
INTERLEAVE IN {dest_node};

-- Per-node Top-K (eliminates full adjacency-list Sort Limit on high-degree hubs):
CREATE INDEX Idx_{edge_table}_TopKRecent
ON {edge_table} (id, create_ts DESC)
STORING (amount),
INTERLEAVE IN {parent_node};
```

### 3.3 Indexing, FK Join Elimination & Anti-Pattern Guardrails

1. **`INTERLEAVE IN PARENT` vs. Remote `INTERLEAVE IN`**:
   - Table `INTERLEAVE IN PARENT SourceNode` requires exact matching column names, types, and order, and enforces parent row existence.
   - Index `INTERLEAVE IN DestNode` (no `PARENT` keyword) uses **positional type matching** — `target_id` co-locates in `{dest_node}(id)` splits even with a different column name.
   - If the destination column is nullable, Spanner rejects a plain interleaved index; use `CREATE NULL_FILTERED INDEX ... INTERLEAVE IN DestNode`.
   - If `DestNode` is itself interleaved under another parent whose PK is absent from the edge table, omit `INTERLEAVE IN` and create a global covering index `ON {edge_table} (target_id, id) STORING (...)`.
2. **Never List PK Columns in `STORING`**: Spanner automatically stores all table PK columns in every secondary index; listing a PK column in `STORING` fails DDL validation (`Index ... specifies stored column <col> which is a key of table ...`).
3. **Destination FK Join Elimination vs. Hub-Node Write Contention**:
   - A `FOREIGN KEY` (enforced or `NOT ENFORCED` informational) on `DESTINATION KEY` lets the query optimizer **eliminate the destination node table join** when a query reads only the destination key (e.g., `RETURN dst.id`).
   - **Enforced FK (default)**: Prevents dangling edges at write time, but high-degree hub nodes (thousands/millions of concurrent incoming edge writes) can incur read-write lock contention on hub rows.
   - **Informational FK (`NOT ENFORCED`, table-level only)**: Removes hub lock contention while keeping join elimination — **WARNING**: Does NOT prevent dangling edges, and because the optimizer trusts informational FKs by default, dangling edges can cause **incorrect query results**. Confirm explicitly with the user before choosing `NOT ENFORCED`.
4. **Swapped-Key Reverse Edge Aliasing Anti-Pattern**: Projecting one edge table twice in `EDGE TABLES` with swapped `SOURCE KEY` / `DESTINATION KEY` (`Debits` vs. `Credits`) is fine for domain readability, **NOT for performance**. Querying `-[:Transfers|ReverseTransfers]->{1,3}` unions two edge types at every hop, which is slower than `-[:Transfers]-{1,3}` backed by a reverse index.

---

## 4. Relational-to-Graph Topology Restructuring Patterns

| Pattern | Cardinality / Signal | Physical & Graph Schema Strategy | Critical Guardrail |
| :--- | :--- | :--- | :--- |
| **Pure `M:N` Junction** | `M:N`, PK `(a_id, b_id [, seq])`, no downstream children | Map in `EDGE TABLES` only; `PRIMARY KEY (a_id, b_id [, seq])`, `INTERLEAVE IN PARENT EntityA`. | Never fold `M:N` into a node table (duplicates node rows and skews `COUNT(n)`). |
| **Attributed Junction / Dual Projection** | Event/Order table that also has downstream edges (`Order -> LineItem`) | Map in **both** `NODE TABLES` (`Orders AS OrderNode`) and `EDGE TABLES` (`Orders AS PlacedOrder`). | Must use distinct `AS` aliases and reference the full composite key `(customer_id, order_id)` if interleaved. |
| **Strict `1:N` Containment Child** | Child belongs to exactly 1 parent (`Device -> Card`, `Customer -> Order`) | Interleave child `INTERLEAVE IN PARENT ParentNode` with PK `(parent_id, child_id)` and project child as both Node and Edge (no separate junction table). | **Strict `1:N` only** — never for `M:N` or independently movable entities. Avoid 3-level interleaving `(a_id, b_id, c_id)` where edges widen PKs and re-parenting requires delete+insert. |
| **Folded `many-to-{0,1}` / `many-to-1` Edge** | Source node has <= 1 outgoing edge of this type (`Employee(emp_id, manager_id)`) | Store `target_id` column directly on the source node table (nullable for `0..1`) with enforced FK and project the source table in both `NODE TABLES` and `EDGE TABLES` (`Table AS EdgeAlias`). | **NEVER put `ON DELETE CASCADE` on a folded-edge FK** (deleting the target would delete source node rows!). Use `CREATE NULL_FILTERED INDEX ... INTERLEAVE IN TargetNode` for reverse traversal. |
| **1:1 Subtype Consolidation** | Fragmented 1:1 tables (`BorderRouter`, `EdgeRouter`) sharing `device_id` | Consolidate into one `Device` table with a `device_role` discriminator column. | Eliminates redundant 1:1 joins and polymorphic union scans. |
| **1:1 Pass-Through Folding** | Binary link modeled as node (`NodeA <- LinkNode -> NodeB`) | Fold `LinkNode` attributes into a single `LinkEdge(a_id, b_id, link_id, ...)` table, halving hop count. | **Confirm with user first**: `LinkNode` ceases to be a queryable node (`MATCH (l:LinkNode)` breaks; links can no longer own separate edges). |

---

## 5. Dangling Edges, Same-Node-Type Cascade Exception & TTL

- **Heterogeneous Node Types (`Person -> Account`)**: Combine `INTERLEAVE IN PARENT Person ON DELETE CASCADE` (cleans outgoing edges when `Person` is deleted) with `CONSTRAINT FK_Account FOREIGN KEY (account_id) REFERENCES Account(id) ON DELETE CASCADE` (cleans incoming edges when `Account` is deleted).
- **CRITICAL Same-Node-Type Exception (`Account -> Account`)**: When an edge connects two rows of the **same** node table and is interleaved into that table, Spanner **rejects** combining `INTERLEAVE IN PARENT Account ON DELETE CASCADE` with a second `FOREIGN KEY ... REFERENCES Account(id) ON DELETE CASCADE`. Choose one:
  - **Option A — Strict Bidirectional Cascade**: Keep the edge table **standalone** (do NOT interleave) with **two** enforced `FOREIGN KEY ... REFERENCES Account(id) ON DELETE CASCADE` constraints (on both `src_id` and `dst_id`), plus a global covering reverse index on `(dst_id, src_id)`.
  - **Option B — Maximum Forward Locality (and Graph Algorithm Compatibility)**: Keep `INTERLEAVE IN PARENT Account ON DELETE CASCADE` + an informational `FOREIGN KEY (dst_id) REFERENCES Account(id) NOT ENFORCED`, cleaning up incoming edges in application logic or via TTL.
- **Row Deletion Policy (TTL) Interaction**: A node table declaring `ROW DELETION POLICY (OLDER_THAN(close_ts, INTERVAL 90 DAY))` requires attached edge tables to either cascade deletes (`ON DELETE CASCADE`) or use informational FKs (`NOT ENFORCED`); Spanner rejects TTL if an enforced non-cascading child edge blocks expiration. Edge tables may also declare their own independent TTL alongside parent `ON DELETE CASCADE`.

---

## 6. Visual Schema Modeler (Preview), Validation & DML Mutations

### 6.1 Visual Schema Modeler Capabilities & Limitations

Available in **Spanner Studio -> Create graph** or **Object explorer -> View/modify visually**. Supports building **From scratch** (auto-generates tables, forward FKs, reverse indexes, and `CREATE OR REPLACE PROPERTY GRAPH`), **From existing tables**, or **Hybrid**.

Six hard limitations to plan around:
1. **Additive only**: Never generates `DROP TABLE` or `DROP COLUMN` (removing elements updates only the property graph DDL).
2. **No mapped edge indexes/constraints**: Edges mapped from existing tables do not get auto-generated FKs or reverse indexes — add them manually.
3. **No draft persistence**: Closing the browser tab discards unsaved schema edits.
4. **No schemaless support**: Cannot author `DYNAMIC LABEL` or `DYNAMIC PROPERTIES`.
5. **No views or named schemas**: Cannot pick SQL views or tables inside custom named schemas as existing sources.
6. **Subset of schema features**: Edit generated DDL manually before applying advanced options (TTL, locality groups, custom constraints).

### 6.2 Safe DDL Validation Workflow

```bash
# 1. Inspect existing DDL (read-only)
gcloud spanner databases ddl describe {database_id} \
  --instance={instance_id} --project={project_id}

# 2. Validate candidate DDL on a temporary empty database on a live instance (recommended)
gcloud spanner databases create {temp_validation_db} \
  --instance={instance_id} --project={project_id} \
  --ddl-file={schema_file}.sql
gcloud spanner databases delete {temp_validation_db} \
  --instance={instance_id} --project={project_id} --quiet

# 3. Local best-effort validation on Spanner Emulator (never activate globally over prod CLI config)
gcloud config configurations create emulator --no-activate
gcloud spanner databases create {temp_validation_db} \
  --instance={emulator_instance} --ddl-file={schema_file}.sql \
  --configuration=emulator
```

### 6.3 Mutating Graph Data via DML & Graph Subqueries

Insert source and destination nodes before edges; delete edges before nodes (unless `ON DELETE CASCADE` handles cleanup). Standard DML supports `IN { GRAPH ... }` subqueries:

```sql
UPDATE {dest_table}
SET is_blocked = FALSE
WHERE id IN {
  GRAPH {graph_name}
  MATCH (a:Account WHERE a.id = @seed_id)-[:Transfers]->{1,2}(b:Account)
  RETURN b.id
};
```

---

## 7. Official Documentation

- Schema Overview: https://docs.cloud.google.com/spanner/docs/graph/schema-overview
- Create, Update, and Drop Property Graph Schemas: https://docs.cloud.google.com/spanner/docs/graph/create-update-drop-schema
- Graph Schema Statements Reference: https://docs.cloud.google.com/spanner/docs/reference/standard-sql/graph-schema-statements
- Schema Design Best Practices: https://docs.cloud.google.com/spanner/docs/graph/best-practices-designing-schema
- Visual Schema Modeler: https://docs.cloud.google.com/spanner/docs/graph/create-update-drop-schema-visually
- Insert, Update, and Delete Graph Data: https://docs.cloud.google.com/spanner/docs/graph/insert-update-delete-data
