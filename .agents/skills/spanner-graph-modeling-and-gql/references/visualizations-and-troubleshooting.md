# Spanner Studio Graph Visualizations, Integrations & Troubleshooting Catalog

Authoritative reference for rendering interactive graph visualizations in Spanner Studio, integrating notebook and third-party visualization tools, diagnosing the 19 most common DDL/GQL/mutation failures, and avoiding known documentation errata (Enterprise / Enterprise Plus, GoogleSQL).

---

## 1. Spanner Studio Graph Visualizations

### 1.1 Mandatory Query Rules, JSON Shape & 10 MB Payload Cap

- **`SAFE_TO_JSON(...)` Mandate**: Spanner Studio activates the interactive graph visualization pane **ONLY** when the GQL query returns full graph elements or paths serialized via **`SAFE_TO_JSON(...)`** (recommended — converts unsupported column types like `TOKENLIST` to JSON `null` instead of failing the query) or `TO_JSON(...)`. Returning only scalar IDs or scalar properties (`n.id`, `n.name`) renders **table format only**.
- **Return Full Paths (Best Practice)**: Returning a bound path variable (`result_path`) keeps `RETURN` concise and prevents silently omitting intermediate nodes and edges on multi-hop traversals:
  ```sql
  -- Multi-hop cycle-safe path visualization (returns graph canvas + hop metadata)
  GRAPH {graph_name}
  MATCH result_path = TRAIL (src:Account {is_blocked: TRUE})-[:Transfers]->{1,3}(dst:Account)
  RETURN
    SAFE_TO_JSON(result_path) AS result_path,
    PATH_LENGTH(result_path) AS hops
  ORDER BY hops ASC
  LIMIT 100;
  ```
- **JSON Output Structure (for Custom UI / Notebook Parsers)**:
  - `SAFE_TO_JSON(node)` emits `{"identifier": "...", "kind": "node", "labels": ["..."], "properties": {...}}`.
  - `SAFE_TO_JSON(path)` emits a JSON array of alternating node and edge objects.
  - Pass `stringify_wide_numbers => TRUE` (`SAFE_TO_JSON(result_path, stringify_wide_numbers => TRUE)`) when downstream JavaScript/JSON parsers read `INT64` or `NUMERIC` values outside the IEEE-754 `FLOAT64` exact range (`±2^53`).
  - **Schemaless Duplication Guard**: When visualizing `DYNAMIC PROPERTIES (properties)` graphs, define the property graph with `PROPERTIES ALL COLUMNS EXCEPT (properties)` so `SAFE_TO_JSON(n)` does not emit both flattened dynamic keys AND a redundant nested `"properties": {"properties": {...}}` object.
- **Hard 10 MB Visualization Limit**: Spanner Studio visualizes up to **10 MB** of returned result data. Queries exceeding 10 MB render a **partial visualization** — add selective anchor filters or a `LIMIT` clause to stay comfortably under 10 MB.

### 1.2 Layouts, Interactive Exploration & Schema View Caveats

- **Layout Modes**:
  - **Force layout (default)**: Repulsive node physics pulling connected clusters together.
  - **Hierarchical**: Layered hierarchy derived from graph connectivity.
  - **Sequential**: Ordered sequence layout derived from connectivity.
  - **Show labels** toggle: Renders all node and edge labels continuously across every zoom level.
- **Right-Click Node Context Menu**:
  - **Expand**: Fetch and draw adjacent nodes via all incoming edges, all outgoing edges, or a specific edge type.
  - **Collapse**: Prune connected nodes via incoming, outgoing, or specific edge type.
  - **Hide node**: Remove the selected node from the current canvas.
  - **Show only neighbors**: Hide everything except the selected node and its immediate 1-hop neighbors.
  - **Highlight node**: Visually emphasize the selected node.
- **Schema Visualization (Works for BOTH Schematized and Schemaless Graphs)**:
  - **Entry Point 1 (From Query Results)**: Click **Switch to schema view** on the **Results** tab.
  - **Entry Point 2 (From Object Explorer)**: In **Spanner Studio -> Object explorer**, open **View actions (`⋮`) -> View schema** on the property graph.
- **Node Display Property & Color Customization**:
  - By default, each node renders its **first declared property** as the visible text label.
  - To customize the displayed property or node color (palette or hex color via **Node display options**), click **Switch to schema view** on the **Results** tab -> click a node type -> edit in the detail panel.
  - **Two Critical Caveats**:
    1. **Session-Only Persistence**: Custom node display properties and colors reset immediately if you re-run the same query.
    2. **Object Explorer Restriction**: You **CANNOT** change displayed node properties when entering schema view via **Object explorer -> View schema** (only via the **Results** tab).

---

## 2. Visualization Integrations & External Ecosystem

| Tool / SDK | Category | Key Capabilities & Public Link |
| :--- | :--- | :--- |
| **`spanner-graph-notebook`** | Open-Source Notebook Widget | Interactive Spanner Studio-style graph visualization inside **Google Colab** and **Jupyter Notebooks**: https://github.com/cloudspannerecosystem/spanner-graph-notebook |
| **`G.V()`** | Desktop Database Client | Interactive GQL visualization plus a no-code **Graph Data Explorer** to search and edit Spanner Graph data: https://gdotv.com/google-cloud-spanner-graph-visualization-tool/ |
| **`Graphistry` + `Louie.ai`** | GPU Visual Analytics + AI | GPU-accelerated visualization (`pygraphistry`) paired with AI-assisted investigation over high-scale Spanner Graph datasets: https://docs.cloud.google.com/spanner/docs/graph/visualization-integrations |
| **`Kineviz GraphXR`** | Large-Scale Visual Exploration | Interactive large-scale graph exploration and AI-assisted insights for Spanner Graph: https://www.kineviz.com/spannergraph |
| **`Linkurious Enterprise`** | Decision Intelligence Platform | No-code enterprise graph investigation over millions of entities, real-time alerting, and collaborative case workspaces: https://linkurious.com/ |
| **`Cambridge Intelligence`** | Embeddable JS / React SDKs | Custom interactive graph UI SDKs (**KeyLines**, **ReGraph**, **MapWeave**, **KronoGraph**) for embedding Spanner Graph views in web applications: https://docs.cloud.google.com/spanner/docs/graph/visualization-integrations |

---

## 3. Comprehensive 19-Case Troubleshooting Catalog

### 3.1 Schema DDL Errors (#1–#6)

| # | Failure Mode | Exact Verified Spanner Error Message | Cause & Exact Fix |
| :-: | :--- | :--- | :--- |
| **1** | **Non-unique element `KEY`** | `Neither the primary keys nor any unique index defined on the property graph element source table 'Person' provides the uniqueness guarantee for graph element 'Person' belonging to the graph 'FinGraph'. You want to redefine the element key columns ('name') based on the source table's primary keys, or create a unique index on the element's key columns.` | `KEY (name)` references a non-unique column. Fix: use the PK (`KEY (id)`) OR add `CREATE UNIQUE INDEX Idx_PersonName ON Person(name);` (for views, use `OPTIONS (validate_element_key_uniqueness = false)` only if guaranteed unique). |
| **2** | **Duplicate element definition name** | `Account is defined more than once; use a unique name.` | Same table appears in both `NODE TABLES` and `EDGE TABLES` (or twice in `EDGE TABLES`) without an alias. Fix: assign a distinct alias (`Account AS Owns`). |
| **3** | **Inconsistent properties on a shared label** | `The label Entity is defined with different property declarations. There is one instance of this label defined with properties of [id]. Another instance is defined with properties of [name].` | Two element tables declare `LABEL Entity` with different property sets. Fix: expose the exact same property names and types on every `LABEL Entity` block (use aliases or `CAST(NULL AS <type>) AS missing_col`), or split into separate labels. |
| **4** | **Graph-wide property type conflict** | `The property declaration of name has type conflicts. There is an existing declaration of type INT64. There is a conflicting one of type STRING.` | Same property name exposed with conflicting SQL types across two elements (even under unrelated labels!). Fix: `CAST(col AS STRING) AS name`, rename via `AS`, or exclude via `EXCEPT (...)`. |
| **5** | **Subquery inside derived property** | `Property value expression of count cannot contain a subquery.` | Used `(SELECT COUNT(*) FROM ...)` inside `PROPERTIES (...)`. Fix: compute aggregates inside GQL/SQL queries or a view instead. |
| **6** | **Conflicting property definitions in one element** | `Property location has more than one definition in the element table Person` | Two labels on `Person` map `location` to different expressions (`country AS location` vs. `city AS location`). Fix: align both expressions or rename one (`city AS city`). |

### 3.2 GQL Compilation & Runtime Query Errors (#7–#10)

| # | Failure Mode | Exact Verified Spanner Error Message | Cause & Exact Fix |
| :-: | :--- | :--- | :--- |
| **7** | **Returning raw `GRAPH_ELEMENT` / `GRAPH_PATH`** | `Returning expressions of type GRAPH_ELEMENT is not allowed` | Final top-level `RETURN n` emitted a raw element (allowed only before `NEXT`, in `WITH`, in subqueries, or as output of `GRAPH_TABLE`). Fix: return scalar properties (`n.id`) or `SAFE_TO_JSON(n)`. |
| **8** | **Combining `{...}` and `WHERE` in one filler** | `WHERE clause cannot be used together with property specification` | Wrote `(n:Account {id: 1} WHERE n.is_blocked)`. Fix: move `WHERE` outside parentheses (`(n:Account {id: 1}) WHERE n.is_blocked`), unify inside `WHERE` (`(n:Account WHERE n.id = 1 AND n.is_blocked)`), or merge into `{id: 1, is_blocked: TRUE}`. |
| **9** | **Carried / `LET` variable inside `{...}`** | `Name 'account_id', defined in the previous statement, can only be referenced in the outermost WHERE clause of MATCH` | Wrote `LET account_id = 1 MATCH (n:Account {id: account_id})`. Fix: move the filter to the outer `WHERE`: `MATCH (n:Account) WHERE n.id = account_id`. |
| **10** | **Redefining outer graph variable in subquery** | `The name account is already defined; redefining graph element variables in a subquery is not allowed. To refer to the same graph element, use a different name and add an explicit filter that checks for equality.` | Reused outer `account` inside `VALUE`/`ARRAY`/`EXISTS`/`IN` `{ MATCH (account:Account)... }`. Fix: declare a fresh inner variable `a` and correlate with `WHERE a = account` (or use `CALL (account) { ... }`). |

### 3.3 Silent GQL Semantic & Visualization Traps (#11–#15)

| # | Trap | Wrong Pattern -> Correct Pattern | Verified Behavior Difference |
| :-: | :--- | :--- | :--- |
| **11** | **`OPTIONAL MATCH` `WHERE` vs. `FILTER`** | Wrong: `OPTIONAL MATCH (m:Account) FILTER m.is_blocked`<br>Right: `OPTIONAL MATCH (m:Account) WHERE m.is_blocked` | `WHERE` belongs to `OPTIONAL MATCH` and preserves unmatched left rows as `NULL`. Standalone `FILTER` runs post-join and drops `NULL` rows, silently turning `OPTIONAL MATCH` into an inner join. |
| **12** | **Bound variable carried across `NEXT`** | Unintended intersection: `MATCH (n:Account {id: 7}) RETURN n NEXT MATCH (n:Account {id: 16})`<br>Fresh match: `... RETURN n.id AS prev_id NEXT MATCH (n:Account {id: 16})` | Returning graph element `n` keeps `n` bound across `NEXT`, intersecting `id = 7` AND `id = 16` -> **empty result**. Projecting only scalar `n.id` drops `n`, allowing the next `MATCH` to bind a fresh `n`. |
| **13** | **Standalone `ORDER BY` silently dropped** | Wrong: `MATCH (n:Account) ORDER BY n.id DESC RETURN n.id LIMIT 3`<br>Right: `MATCH (n:Account) RETURN n.id ORDER BY n.id DESC LIMIT 3` | Standalone `ORDER BY` is **silently ignored** unless it is the final statement or immediately followed by `LIMIT`/`OFFSET`. Always place `ORDER BY` inside `RETURN ... ORDER BY ... [OFFSET m] [LIMIT n]`. |
| **14** | **Any-direction `-[:E]-` double counting** | `MATCH (n:Account)-[:Transfers]-(m:Account) RETURN COUNT(*)` (counts `2 * \|E\|`) vs. `-[:Transfers]->` (counts `\|E\|`) | Every directed edge `(x)->(y)` matches twice under `-[]-`: once as `(n=x, m=y)` and once as `(n=y, m=x)`. Filter with `WHERE n IS SOURCE OF e` when deduplicating. |
| **15** | **Studio visualization silent fallbacks** | Scalars -> table only; payload > 10 MB -> truncated graph; individual elements -> missing intermediates | Always return `SAFE_TO_JSON(p)` for a bound path `p` and add selective filters + `LIMIT` to stay under 10 MB. |

### 3.4 Underlying Relational Mutation Errors (#16–#19)

| # | Failure Mode | Exact Verified Spanner Error Message | Cause & Exact Fix |
| :-: | :--- | :--- | :--- |
| **16** | **Missing source node on interleaved edge insert** | `Parent row for row [...] in table AccountTransferAccount is missing. Row cannot be written.` | Edge table is `INTERLEAVE IN PARENT Account`. Insert the source `Account` row first (or earlier in the same transaction). |
| **17** | **Missing destination node on FK edge insert** | `Foreign key constraint FK_TransferTo is violated on table AccountTransferAccount. Cannot find referenced values in Account(id)` | Destination `Account(to_id)` does not exist. Insert destination nodes before inserting edge rows. |
| **18** | **Deleting node with interleaved outgoing edges** | `Integrity constraint violation during DELETE/REPLACE. Found child row [...] in table AccountTransferAccount` | Deleting parent `Account` while child edge rows exist without `ON DELETE CASCADE`. Delete outgoing edges first or define `INTERLEAVE IN PARENT Account ON DELETE CASCADE`. |
| **19** | **Deleting node with FK incoming edges** | `Foreign key constraint violation when deleting or updating referenced row(s): referencing row(s) found in table AccountTransferAccount` | Deleting referenced `Account` while incoming FK rows exist without cascade. Delete incoming edges first or configure cascading FKs *(remember: for same-node-type edges, Spanner forbids combining `INTERLEAVE ... ON DELETE CASCADE` and a self-referencing `FOREIGN KEY ... ON DELETE CASCADE` on the same table!)*. |

---

## 4. Runnable Fixes for the Top Silent GQL Traps

```sql
-- Trap #10 Fix: Correlate outer variable cleanly via fresh variable + WHERE equality
GRAPH {graph_name}
MATCH (acct:Account)
LET out_count = VALUE {
  MATCH (inner_acct:Account)-[:Transfers]->(:Account)
  WHERE inner_acct = acct
  RETURN COUNT(*)
}
RETURN acct.id AS account_id, out_count;

-- Trap #11 & #13 Fix: Keep filter inside OPTIONAL MATCH + ORDER BY inside RETURN (OFFSET before LIMIT)
GRAPH {graph_name}
MATCH (src:Account)
OPTIONAL MATCH (src)-[e:Transfers]->(dst:Account)
WHERE e.amount > 1000
RETURN src.id AS src_id, dst.id AS high_value_dst_id
ORDER BY src_id ASC
OFFSET 0
LIMIT 25;

-- Trap #14 Fix: Deduplicate undirected traversal using IS SOURCE OF
GRAPH {graph_name}
MATCH (a:Account)-[e:Transfers]-(b:Account)
WHERE a IS SOURCE OF e
RETURN COUNT(*) AS unique_edge_count;
```

---

## 5. Quick Symptom-to-Root-Cause Triage Checklist

| Symptom | Most Likely Cause | Immediate Check / Fix |
| :--- | :--- | :--- |
| **Spanner Studio shows Table tab only (no Graph tab)** | `RETURN` projected scalars instead of `SAFE_TO_JSON(el)` / `SAFE_TO_JSON(path)`. | Bind `MATCH p = ...` and return `SAFE_TO_JSON(p) AS path_json`. |
| **Graph canvas is missing nodes/edges or truncated** | Result payload exceeded the hard **10 MB** visualization cap, OR individual elements were returned instead of a path `p`. | Return `SAFE_TO_JSON(p)` and tighten anchor filters + `LIMIT`. |
| **Schemaless `MATCH (n:Account)` returns 0 rows** | `label` column stored `'Account'` in mixed/uppercase (silently ignored by `DYNAMIC LABEL`). | Enforce `CHECK (LOWER(label) = label)` and normalize stored label strings and top-level JSON keys to lowercase. |
| **Some base-table rows silently vanish from `MATCH`** | One or more `KEY` / `SOURCE KEY` / `DESTINATION KEY` columns contain `NULL`. | Query base tables for `WHERE key_col IS NULL`; enforce `NOT NULL` on key columns. |
| **Edge counts mysteriously double or multiply** | Either (a) any-direction `-[]-` matched each edge twice, or (b) `REFERENCES` omitted part of a composite node PK. | Use directed `->` or `IS SOURCE OF`, and verify every composite PK column appears in `REFERENCES (...)`. |

---

## 6. Verified Documentation Errata & Common Misconceptions

1. **`IS_ACYCLIC(path)` and `IS_TRAIL(path)` Boolean Polarity**:
   - Some narrative walkthrough text inadvertently describes these functions with inverted polarity.
   - **Verified ground truth** (confirmed by result tables and the formal GQL Functions reference):
     - `IS_ACYCLIC(path)` returns **`TRUE`** when **NO node repeats** (`FALSE` when a node repeats).
     - `IS_TRAIL(path)` returns **`TRUE`** when **NO edge repeats** (`FALSE` when an edge repeats).
2. **ISO GQL vs. openCypher & Synthetic Edge Primary Keys**:
   - Casual third-party examples sometimes refer to Spanner Graph queries as "openCypher" or show edge tables with a synthetic single-column `PRIMARY KEY (edge_id)`.
   - **Verified ground truth**: Spanner Graph implements **ISO/IEC 39075:2024 (GQL)** and **ISO/IEC 9075-16:2023 (SQL/PGQ)** — NOT openCypher — and leading an edge table PK with a synthetic `edge_id` is an **anti-pattern** that prevents parent interleaving and degrades adjacency traversal to full scans. Always use `PRIMARY KEY (src_id, dst_id [, uniquifier])` with `INTERLEAVE IN PARENT`.

---

## 7. Official Documentation

- Work with Spanner Graph Visualizations: https://docs.cloud.google.com/spanner/docs/graph/work-with-visualizations
- Visualization Integrations: https://docs.cloud.google.com/spanner/docs/graph/visualization-integrations
- Troubleshoot Spanner Graph: https://docs.cloud.google.com/spanner/docs/graph/troubleshoot
- ISO Standards Conformance: https://docs.cloud.google.com/spanner/docs/graph/iso-standards
- GQL Functions Reference: https://docs.cloud.google.com/spanner/docs/reference/standard-sql/graph-gql-functions
- Spanner Graph Building-Block Samples: https://github.com/GoogleCloudPlatform/spanner-bb-samples
