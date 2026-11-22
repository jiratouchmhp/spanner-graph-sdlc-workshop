---
name: spanner-graph-modeling-and-gql
metadata:
  version: "1.0.0"
  category: "Cloud Spanner Enterprise"
description: >-
  Guides Spanner Graph modeling, ISO GQL queries, Data Boost graph algorithms,
  and visualizations. Use when authoring CREATE PROPERTY GRAPH (GoogleSQL
  Enterprise+), enforcing the 8 structural invariants, folding/dual-projecting
  edges, remote interleaved reverse indexes, view-backed or schemaless DYNAMIC
  LABEL/DYNAMIC PROPERTIES graphs, Remote Functions, ISO GQL & GRAPH_TABLE,
  quantified paths (WALK/TRAIL/ACYCLIC, ANY SHORTEST/CHEAPEST), GQL hints
  (IS_FIRST, factorized_mode), Data Boost Graph Algorithms (PageRank, WCC,
  ShortestPath), or SAFE_TO_JSON Studio visualizations. Don't use for relational
  DDL/TTL/locality (spanner-schema-and-data-modeling), FTS/Vector/ML.PREDICT/GraphRAG
  (spanner-search-ai-and-graphrag), Neo4j CE export or openCypher migration
  (spanner-migration-and-neo4j-playbook), MCP/FGAC security
  (spanner-mcp-toolbox-and-agent-security), or SQL Insights/TCO sizing
  (spanner-performance-observability-and-sizing).
---

# Cloud Spanner Graph Modeling, ISO GQL & Graph Algorithms

Opinionated production playbook for designing, querying, tuning, analyzing, and visualizing **Spanner Graph** on Cloud Spanner **Enterprise** and **Enterprise Plus** editions using **GoogleSQL** and **ISO GQL**.

---

## 1. Platform Gates & Prerequisites

| Gate | Requirement | Notes & Regional Reference |
| :--- | :--- | :--- |
| **Spanner Edition** | **Enterprise** or **Enterprise Plus** ONLY | Not available in Standard Edition. Covers Property Graphs, ISO GQL, views-based/schemaless graphs, visualizations, Graph Algorithms, and `TOKENIZE_JSON` search indexes. |
| **Database Dialect** | **GoogleSQL (`GOOGLE_STANDARD_SQL`)** ONLY | PostgreSQL dialect does NOT support `CREATE PROPERTY GRAPH`, ISO GQL, `GRAPH_TABLE`, the `JSON` type, or Remote Functions (`LANGUAGE REMOTE`). |
| **Standards** | **ISO/IEC 9075-16:2023 (SQL/PGQ)** + **ISO/IEC 39075:2024 (GQL)** | Spanner Graph is **ISO GQL**, NOT openCypher. Default path mode is `WALK` (repeatable elements allowed). |
| **Preview Gates** | Visual Schema Editor, Graph Algorithms, Remote Functions, LangChain | Remote Functions require **>= 1,000 PUs (>= 1 node)** — NOT supported on fractional dev instances (< 1,000 PUs). |
| **Regional Example (PDPA / APAC)** | **Primary (ONLY DB/App):** `regional-asia-southeast3` (Bangkok; zones `a`, `b`, `c`; 99.99% SLA)<br>**Companion (AI/Security only):** `asia-southeast1` (Singapore, ~25 ms RTT) | Bangkok single-region Enterprise compute: `$1.2915/node-hr` (`$942.795/node-mo`); Data Boost: `$1.2285 / 1,000 SPU-hr`; same-region PITR/backups. Use Singapore **only** for companion GCP services unavailable in Bangkok (Vertex AI embeddings/LLMs, Model Armor, Cloud DLP). |
| **Least-Privilege IAM** | DDL: `roles/spanner.databaseAdmin`<br>GQL Read/Profile: `roles/spanner.databaseReader`<br>Graph Algorithms: `roles/spanner.graphIntelligenceUser` | Caller **MUST** hold `roles/spanner.graphIntelligenceUser` (even `roles/owner` omits `spanner.databases.useDataBoost`!). For `format = 'CLOUD_SPANNER'` write-back, grant `service-{project_number}@gcp-sa-spanner.iam.gserviceaccount.com` database-level `roles/spanner.databaseUser` + `roles/spanner.graphIntelligenceUser`; for GCS exports, grant `roles/storage.objectAdmin`. For Remote Functions, ensure `roles/spanner.serviceAgent`. |

---

## 2. Safety & Confirmation Guardrails

Before executing any command against a live remote Cloud Spanner database:

1. **Require Explicit User Confirmation** before running:
   - `gcloud spanner databases ddl update` (schema creation, `CREATE OR REPLACE PROPERTY GRAPH`, `DROP PROPERTY GRAPH`, index creation, or adding `ON DELETE CASCADE`).
   - Any DML (`INSERT`, `UPDATE`, `DELETE`, Partitioned DML) or `EXPORT DATA` writing back to Spanner (`format = 'CLOUD_SPANNER'`).
   - Adding **`NOT ENFORCED` informational foreign keys** (eliminates hub write contention, but dangling edges can cause silent incorrect query results because the optimizer relies on informational FKs).
   - **Folding 1:1 intermediate pass-through nodes** into edges (removes that node label from `MATCH`).
   - Launching billable **Data Boost Graph Algorithms** (remind the user that `max_idle_time` defaults to `'30m'` and is **billable while idle**).
   - Executing `CALL cancel_query('{query_id}')` on a live production database.
2. **Validate Read-Only First**: Inspect live schema via `gcloud spanner databases ddl describe {database_id} --instance={instance_id} --project={project_id}` and dry-run candidate DDL on a temporary empty database or local Spanner Emulator (`--configuration=emulator`) before mutating production.

---

## 3. Architecture & Feature Decision Matrix

| Decision | Recommended Default | Alternative & When to Switch |
| :--- | :--- | :--- |
| **Graph Storage Model** | **Schematized** (typed relational tables + `CREATE PROPERTY GRAPH`) | **Schemaless / Hybrid** (`DYNAMIC LABEL` + `DYNAMIC PROPERTIES`) when > 50 node types / > 150 edge types, schemas evolve rapidly, or multi-label quantified paths (`-[]->{1,3}`) expand > 10,000 intermediate paths. |
| **View-Backed Graphs** | Direct base tables for highest index/interleaving fidelity & Graph Algorithm eligibility | **SQL Views** (`SQL SECURITY DEFINER` or `INVOKER`) for row-level FGAC, unnesting `ARRAY` keys, extracting JSON keys, bridging `INT64`/`STRING` keys, Remote Functions, or typing a schemaless table without migration. *Never usable as Graph Algorithm inputs.* |
| **Edge Physical Layout** | `PRIMARY KEY (src_id, dst_id [, uniquifier])` + `INTERLEAVE IN PARENT SourceNode ON DELETE CASCADE` + enforced dest FK + remote `INTERLEAVE IN DestNode` covering index | **Same-node-type edge** (`Account -> Account`): Spanner forbids dual cascade on both `INTERLEAVE` and FK to the same parent. Choose **Standalone + 2 enforced cascading FKs** (strict integrity) OR **`INTERLEAVE IN PARENT` + `NOT ENFORCED` dest FK** (max locality & Graph Algorithms compatibility). |
| **Cardinality Folding** | Pure `M:N` -> dedicated edge table; Event/Order with children -> dual projection (`NODE TABLES` + `EDGE TABLES` with distinct `AS` aliases) | Strict `1:N` child -> interleave child node + project as node & edge. `many-to-{0,1}` -> fold `target_id` onto source node (NEVER add `ON DELETE CASCADE` on folded FK!). |
| **Traversal Path Mode** | `MATCH TRAIL` (no repeated edges) or `MATCH ACYCLIC` (no repeated nodes) whenever cycles exist | Default `WALK` only for DAGs or intentional cycle counting. Use `ANY SHORTEST` / `ANY CHEAPEST` (strictly positive finite `COST`) when returning one optimal path per endpoint pair. |
| **Analytical Compute** | Parameterized online ISO GQL for OLTP local subgraphs (ms latency) | **Data Boost Graph Algorithms** (`PageRank`, `WCC`, `ModularityClustering`, `ShortestPath`, etc.) for global multi-billion-edge analytics with near-zero OLTP impact. |

---

## 4. Non-Negotiable Core Invariants

Enforce these before writing any DDL or GQL:

1. **All 8 Property Graph DDL Structural Invariants** (details and exact compiler errors in [graph-schema-and-invariants.md](references/graph-schema-and-invariants.md)):
   - (1) Unique element aliases across `NODE TABLES` and `EDGE TABLES`; (2) `REFERENCES` targets the node's `AS` alias (never base name if aliased, never a label); (3) Shared `LABEL` declarations have identical property name + type signatures; (4) `REFERENCES` includes the **full composite key** and exact types (partial composite key silently duplicates edges!); (5) Every view-backed element defines explicit `KEY (...)` (or `validate_element_key_uniqueness = false`); (6) **Graph-wide** property type uniqueness for every property name across all labels/tables; (7) Explicit `SQL SECURITY INVOKER | DEFINER` on every view; (8) Table `KEY` matches PK superset or `UNIQUE` index.
2. **No `ALTER PROPERTY GRAPH`**: Every graph schema update (including exposing newly added columns) MUST re-run `CREATE OR REPLACE PROPERTY GRAPH ...`.
3. **Never Lead Edge PKs with Synthetic `edge_id`**: Always lead with `(src_id, dst_id [, edge_id])` so outgoing traversals seek adjacency lists instead of full-scanning.
4. **Schemaless Lowercase & Single-Table Rules**: Store `DYNAMIC LABEL` values and top-level `DYNAMIC PROPERTIES` JSON keys strictly in **lowercase** (mixed-case values are silently dropped!). At most **one** node table (and zero other node tables) may use `DYNAMIC LABEL` (same for edges); use `PROPERTIES ALL COLUMNS EXCEPT (properties)` to prevent duplicate nested JSON in `TO_JSON(n)`.
5. **GQL Syntax & Scoping Guardrails**:
   - Final top-level `RETURN` cannot emit raw `GRAPH_ELEMENT` or `GRAPH_PATH` — return scalar properties or `SAFE_TO_JSON(p)`.
   - Standalone `ORDER BY` before `RETURN` is **silently ignored** unless immediately followed by `LIMIT`/`OFFSET`; inside `RETURN`, **`OFFSET` must precede `LIMIT`**.
   - Put filters on optional variables inside `OPTIONAL MATCH ... WHERE ...` (a trailing `FILTER` converts it into an inner join).
   - Never force `@{JOIN_METHOD=HASH_JOIN}` on anchored variable-length paths (`->{1,n}`).
   - Always give computed outputs an explicit `AS` alias even inside scalar `VALUE { ... }` subqueries (e.g., `VALUE { MATCH ... RETURN COUNT(*) AS cnt }`).
   - In Data Boost Graph Algorithms: declare setup/output tables idempotently with `CREATE TABLE IF NOT EXISTS`, use `fallback_compute_region => '{region}'` (not `fallback_region`), and in `ShortestPath` include **every** yielded node variable (`source_node` AND `target_node`) in `RETURN`.

---

## 5. Runnable Quickstart Patterns

### 5.1 Schematized Graph DDL with Interleaving & Reverse Covering Index

```sql
CREATE TABLE {source_table} (
  src_id INT64 NOT NULL,
  name   STRING(256) NOT NULL,
) PRIMARY KEY (src_id);

CREATE TABLE {dest_table} (
  dst_id     INT64 NOT NULL,
  is_active  BOOL NOT NULL,
) PRIMARY KEY (dst_id);

-- Forward adjacency co-located with source node via INTERLEAVE IN PARENT
CREATE TABLE {edge_table} (
  src_id     INT64 NOT NULL,
  dst_id     INT64 NOT NULL,
  created_at TIMESTAMP NOT NULL,
  weight     FLOAT64 NOT NULL,
  CONSTRAINT FK_EdgeDest FOREIGN KEY (dst_id)
    REFERENCES {dest_table} (dst_id) ON DELETE CASCADE,
) PRIMARY KEY (src_id, dst_id, created_at),
  INTERLEAVE IN PARENT {source_table} ON DELETE CASCADE;

-- Reverse & any-direction traversal co-located with destination node (NO "PARENT" keyword!)
CREATE INDEX Idx_{edge_table}_Reverse
ON {edge_table} (dst_id, src_id, created_at)
STORING (weight),
INTERLEAVE IN {dest_table};

CREATE OR REPLACE PROPERTY GRAPH {graph_name}
  NODE TABLES (
    {source_table} KEY (src_id) LABEL SourceNode PROPERTIES (src_id, name),
    {dest_table}   KEY (dst_id) LABEL DestNode   PROPERTIES (dst_id, is_active)
  )
  EDGE TABLES (
    {edge_table}
      KEY (src_id, dst_id, created_at)
      SOURCE KEY (src_id) REFERENCES {source_table} (src_id)
      DESTINATION KEY (dst_id) REFERENCES {dest_table} (dst_id)
      LABEL Connects PROPERTIES (created_at, weight)
  );
```

### 5.2 Cycle-Safe Parameterized ISO GQL, `GRAPH_TABLE` Join & Studio Visualization

```sql
-- 1. Cycle-safe bounded path traversal joined back to relational SQL via GRAPH_TABLE
SELECT gt.target_id, gt.hops, m.owner_team
FROM GRAPH_TABLE (
  {graph_name}
  MATCH TRAIL (s:SourceNode {src_id: @start_id})-[e:Connects]->{1,3}(d:DestNode)
  WHERE d.is_active = TRUE
  RETURN d.dst_id AS target_id, ARRAY_LENGTH(e) AS hops
) AS gt
JOIN {metadata_table} AS m ON gt.target_id = m.dst_id
ORDER BY gt.hops ASC, gt.target_id ASC
LIMIT 25;

-- 2. Spanner Studio interactive graph visualization (must return SAFE_TO_JSON and stay < 10 MB)
GRAPH {graph_name}
MATCH p = TRAIL (s:SourceNode {src_id: @start_id})-[e:Connects]->{1,3}(d:DestNode)
RETURN SAFE_TO_JSON(p) AS path_json
LIMIT 50;
```

### 5.3 Data Boost Graph Algorithm (`PageRank`) + Status Check

```sql
-- Requires roles/spanner.graphIntelligenceUser; edge table must be interleaved + dest FK (enforced or NOT ENFORCED)
-- Set max_idle_time low on scheduled batch runs (default '30m' is billable while idle!)
EXPORT DATA OPTIONS (
  format = 'CLOUD_SPANNER',
  table = '{algo_results_table}',
  write_mode = 'upsert_ignore_all'
) AS
GRAPH {graph_name}
CALL PageRank(
  node_labels => ['EntityNode'],
  edge_labels => ['LinksTo'],
  damping_factor => 0.85,
  max_iterations => 20,
  max_idle_time => '5m'
) YIELD node, score
RETURN node.node_id AS node_id, 'pagerank_v1' AS algo_run_id, score AS rank_score;

-- Monitor progress (30-day retention)
SELECT query_id, status, progress, error_message, start_timestamp
FROM SPANNER_SYS.GRAPH_OPERATION_EXECUTION_STATUS
ORDER BY start_timestamp DESC
LIMIT 10;
```

### 5.4 Closed-Loop GQL Execution Plan Profiling

```bash
# Inspect live schema (read-only)
gcloud spanner databases ddl describe {database_id} \
  --instance={instance_id} --project={project_id}

# Profile execution plan (use --query-mode=PLAN if the query times out)
gcloud spanner databases execute-sql {database_id} \
  --instance={instance_id} --project={project_id} \
  --query-mode=PROFILE \
  --sql="GRAPH {graph_name} MATCH (s:SourceNode {src_id: 1001})-[e:Connects]->(d:DestNode) RETURN d.dst_id"
```

---

## 6. Reference Guide Router

Load only the specific reference document needed for your task:

| Task | Reference Document | Key Contents |
| :--- | :--- | :--- |
| **Schema DDL, Invariants, Topology & Indexing** | [graph-schema-and-invariants.md](references/graph-schema-and-invariants.md) | Complete `CREATE PROPERTY GRAPH` grammar, all 8 structural invariants, PK/index/interleaving rules, edge folding & dual projection, same-node-type cascade exception, TTL, Visual Modeler limits, DML subqueries. |
| **View-Backed Graphs, Remote Functions & Schemaless Data** | [views-and-schemaless-graphs.md](references/views-and-schemaless-graphs.md) | FGAC & bridge views, `validate_element_key_uniqueness`, `allow_view_with_volatile_column`, `LANGUAGE REMOTE` limits, `DYNAMIC LABEL`/`DYNAMIC PROPERTIES` schema & constraints, JSON null/coercion rules, LangChain GraphRAG. |
| **ISO GQL Statements, Paths, Functions & Query Tuning** | [gql-paths-and-query-tuning.md](references/gql-paths-and-query-tuning.md) | Every GQL statement & scoping trap, `GRAPH_TABLE` & subqueries, quantified paths, horizontal aggregates, `WALK`/`TRAIL`/`ACYCLIC`, `ANY SHORTEST`/`CHEAPEST`, built-in functions, 8 GQL rewrite rules, exact hint placement. |
| **Data Boost Graph Algorithms Catalog** | [graph-algorithms-catalog.md](references/graph-algorithms-catalog.md) | Data Boost SPU billing & regional rates, GCS & Spanner `EXPORT DATA` rules, 6 schema compatibility mandates, `SPANNER_SYS` monitoring, full 10-algorithm catalog (params/math/complexity), iterative experimentation pattern. |
| **Spanner Studio Visualizations & Troubleshooting** | [visualizations-and-troubleshooting.md](references/visualizations-and-troubleshooting.md) | `SAFE_TO_JSON(p)` & 10 MB cap, layouts/context menu/schema view caveats, external visualization tools (`spanner-graph-notebook`, `G.V()`, Graphistry, GraphXR, Linkurious, Cambridge Intelligence), 19-case error & trap catalog, verified doc errata. |

---

## 7. Sibling Skill Routing (Out-of-Scope Boundaries)

Delegate immediately when the user's task belongs to a sibling skill:

- **Pure relational DDL, PKs, Locality Groups, TTL, Change Streams DDL, or standalone `CREATE VIEW`/`SECURE_CONTEXT()`** -> `spanner-schema-and-data-modeling`
- **Full-Text Search, Exact KNN / ScaNN Vector Indexes, `CREATE MODEL` / `ML.PREDICT`, RRF/RSF Hybrid Search, or GraphRAG pipelines** -> `spanner-search-ai-and-graphrag`
- **Neo4j Community Edition APOC export, Dataflow graph loading, or openCypher-to-GQL migration** -> `spanner-migration-and-neo4j-playbook`
- **MCP Toolbox `tools.yaml`, Antigravity/ADK agent setup, FGAC `CREATE ROLE`/`GRANT`, conditional IAM, or Model Armor** -> `spanner-mcp-toolbox-and-agent-security`
- **Query Optimizer stats, `SPANNER_SYS` SQL/Lock Insights, Key Visualizer heatmaps, pre-splitting, or node/TCO sizing** -> `spanner-performance-observability-and-sizing`
- **Instance editions, CUDs, regional/DR topologies, gcloud/SDKs, Emulator setup, or Spanner Queues** -> `spanner-enterprise-platform-ops`

---

## 8. Official Public Documentation

- Spanner Graph Overview: https://docs.cloud.google.com/spanner/docs/graph/overview
- Spanner Graph Building-Block Samples: https://github.com/GoogleCloudPlatform/spanner-bb-samples
