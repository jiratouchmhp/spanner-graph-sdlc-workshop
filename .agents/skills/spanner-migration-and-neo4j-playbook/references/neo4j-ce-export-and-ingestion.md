# Neo4j Community Edition Export & Cloud Spanner Graph Ingestion

End-to-end engineering playbook for migrating Neo4j Community Edition (CE) graphs into Cloud Spanner Graph using APOC streaming exports, Dataflow (CSV or Avro), and BigQuery Reverse ETL.

## 1. Prerequisites & Graph Schema Strategy

- **Mandatory Edition & Dialect:**
  - **Spanner Enterprise** (`$1.2915/node-hr` in `regional-asia-southeast3`) or **Enterprise Plus** (`$1.7955/node-hr`). Standard edition does NOT support Spanner Graph.
  - **GoogleSQL dialect ONLY** (`Spanner Graph` is unsupported on the PostgreSQL interface).
- **7-Stage Graph Migration Sequence:**
  1. Gather query patterns, latency/throughput SLAs, and growth projections.
  2. Design Spanner Graph schema (**schematized** vs **schemaless/hybrid**).
  3. Translate openCypher queries to ISO GQL and Cypher writes to GoogleSQL DML / Mutation API.
  4. Benchmark schema and queries on representative data.
  5. Export Neo4j CE to CSV/Avro/JSONL and ingest in strict **Nodes -> Edges** order.
  6. Validate graph parity (total counts, per-label counts, per-property summary stats & hashes).
  7. Execute cutover and fallback plan.

### Schematized vs Schemaless / Hybrid Modeling Decision

| Criteria | Schematized (`CREATE PROPERTY GRAPH`) | Schemaless / Hybrid (`DYNAMIC LABEL` / `DYNAMIC PROPERTIES`) |
| :--- | :--- | :--- |
| **Best Fit** | `< 50` node types / `< 150` edge types, stable labels, strict SQL typing, fastest OLTP writes | `> 50` node types / `> 150` edge types, rapidly evolving properties/labels, multi-label quantified paths |
| **Relationship Folding** | Fold **N:1** relationships into FK columns on node tables (dual-project as edge aliases); store **M:N** in explicit edge tables | Store all edges in a unified `GraphEdge` table interleaved in `GraphNode` |
| **Schema Constraints** | Unique element aliases; graph-wide property type uniqueness; label changes require DDL | **Strict Single-Table Rule:** at most **1** `DYNAMIC LABEL` node table (cannot mix with ANY other node table in `NODE TABLES`) and **1** `DYNAMIC LABEL` edge table (cannot mix in `EDGE TABLES`). `DYNAMIC PROPERTIES` CAN be used on any number of static/hybrid tables. |
| **Case & JSON Rules** | Standard SQL identifiers and typed columns | Dynamic label values and top-level JSON keys MUST be stored in **lowercase** (enforce with `CHECK` constraints). Use `PROPERTIES ALL COLUMNS EXCEPT (properties)` in hybrid schemas to prevent duplicate nested `"properties"` in `TO_JSON`/`SAFE_TO_JSON`. |

```sql
-- Schemaless / Hybrid Pattern (strictly isolated in its own PROPERTY GRAPH)
CREATE TABLE GraphNode (
  id         STRING(64) NOT NULL,
  label      STRING(64) NOT NULL,
  properties JSON,
  CONSTRAINT chk_node_label_lower CHECK (label = LOWER(label))
) PRIMARY KEY (id);

CREATE TABLE GraphEdge (
  id         STRING(64) NOT NULL,
  dest_id    STRING(64) NOT NULL,
  edge_id    STRING(64) NOT NULL,
  label      STRING(64) NOT NULL,
  properties JSON,
  CONSTRAINT chk_edge_label_lower CHECK (label = LOWER(label))
) PRIMARY KEY (id, dest_id, edge_id),
INTERLEAVE IN PARENT GraphNode ON DELETE CASCADE;

-- Defer this reverse covering index until AFTER bulk load!
CREATE INDEX IdxGraphEdgeReverse
  ON GraphEdge (dest_id, id, edge_id)
  STORING (label, properties),
  INTERLEAVE IN GraphNode;

CREATE OR REPLACE PROPERTY GRAPH DynamicGraph
  NODE TABLES (
    GraphNode
      KEY (id)
      PROPERTIES ALL COLUMNS EXCEPT (properties)
      DYNAMIC LABEL (label)
      DYNAMIC PROPERTIES (properties)
  )
  EDGE TABLES (
    GraphEdge
      KEY (id, dest_id, edge_id)
      SOURCE KEY (id) REFERENCES GraphNode (id)
      DESTINATION KEY (dest_id) REFERENCES GraphNode (id)
      PROPERTIES ALL COLUMNS EXCEPT (properties)
      DYNAMIC LABEL (label)
      DYNAMIC PROPERTIES (properties)
  );
```

## 2. Heap-Safe Neo4j Community Edition Exports (APOC)

Neo4j CE has no native parallel bulk exporter to Spanner; use the **APOC plugin** with bounded heap and batched file output.

- **Never export Neo4j internal `id(n)` as a Spanner primary key:** Internal Neo4j IDs are recycled and non-portable; always export stable domain keys or UUIDv4 identifiers.
- **Container / Server Configuration (`neo4j.conf` / Docker env):**
  - `NEO4J_PLUGINS=["apoc"]`
  - `NEO4J_apoc_export_file_enabled=true`
  - `NEO4J_dbms_security_procedures_unrestricted=apoc.*`
  - Bound JVM heap (e.g., `NEO4J_server_memory_heap_max__size=4G`) to avoid OOM during large traversals.
- **Streaming Headerless CSV Export (`separateHeader: true`):**
  Cloud Spanner's `GCS_Text_to_Cloud_Spanner` Dataflow template **rejects CSV files containing header rows**. Setting `separateHeader: true` emits a separate `.header.csv` file while keeping the data shards strictly headerless.

```cypher
// 1. Export Node table per label (headerless data file + separate header file)
CALL apoc.export.csv.query(
  "MATCH (p:Person) RETURN p.id AS id, p.name AS name, p.country AS country",
  "nodes_person.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

// 2. Export explicit M:N Edge table per relationship type
CALL apoc.export.csv.query(
  "MATCH (p:Person)-[r:OWNS]->(a:Account)
   RETURN p.id AS id, a.id AS account_id, r.create_time AS create_time",
  "edges_person_own_account.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

// 3. Optional JSONL export for schemaless ingestion or offline audit parity
CALL apoc.export.json.all(
  "all_graph_export.jsonl",
  { batchSize: 5000, useTypes: true }
);
```

- **Split Large Tables into Multiple Files:** For multi-GB exports, partition the APOC Cypher query by hash/range (e.g., `WHERE p.id >= $lo AND p.id < $hi`) or split exported CSVs (`split -l 500000`) before uploading to GCS so Dataflow workers read in parallel.

## 3. Referential Integrity & Load-Order Engineering

Spanner Graph best practices model edges using:
- **`INTERLEAVE IN PARENT {SourceNode} ON DELETE CASCADE`** — enforces parent node existence at write time.
- **Enforced `FOREIGN KEY ({dest_col}) REFERENCES {DestNode}({pk})`** — enforces destination node existence at write time.

**Mandatory Load Sequence:** **Stage 1: Load ALL Node tables -> Stage 2: Load ALL Edge tables.**

Violating load order fails ingestion with errors such as:
- `Foreign key constraint FK_Account is violated on table PersonOwnAccount. Can't find referenced values in Account(id)`
- `Parent row for row [15,1] in table PersonOwnAccount is missing`

A failure mid-load can leave partial data requiring cleanup and re-migration. Choose one of three production strategies:

1. **Staged Sequential Jobs (Recommended Default):** Run Stage 1 (all node tables) to `JOB_STATE_DONE`, then launch Stage 2 (all edge tables).
2. **Defer Secondary Indexes, Enforced FKs & Change Streams (Initial Bulk Load / Downtime):** Create only primary keys and parent interleaving prior to bulk load; add secondary indexes (batched in one DDL request to avoid the recommended 3 unbatched indexes/day limit), enforced FKs, search/vector indexes, and change streams **after** data load completes, then run `ANALYZE;`. Enforced FKs add read validation round-trips and secondary indexes multiply mutations per row (`40,000` rows max per commit with 1 secondary index vs `80,000` without).
3. **Relaxed Referential Integrity (For Unordered / Continuous Pipelines Only):**

```sql
CREATE TABLE PersonOwnAccount (
  id          INT64 NOT NULL,
  account_id  INT64 NOT NULL,
  create_time TIMESTAMP,
  CONSTRAINT FK_Account FOREIGN KEY (account_id) REFERENCES Account (id) NOT ENFORCED
) PRIMARY KEY (id, account_id),
INTERLEAVE IN Person;  -- co-locates physically WITHOUT write-time parent existence check
```

*Trade-off:* `INTERLEAVE IN` (omitting `PARENT`) + `NOT ENFORCED` FKs allow unordered loads and continuous BigQuery queries, but your pipeline MUST guarantee zero dangling edges or graph query performance and correctness degrade.

## 4. Ingestion Path A: GCS CSV -> Dataflow (`GCS_Text_to_Cloud_Spanner`)

### Hard Constraints & Gotchas

- **NO CSV header line:** Strip headers (or use APOC `separateHeader: true`).
- **Target tables MUST already exist** with matching GoogleSQL types.
- **Supported GoogleSQL CSV types:** `BOOL`, `INT64`, `FLOAT64`, `NUMERIC`, `STRING`, `DATE`, `TIMESTAMP`, `BYTES`, `JSON`.
- **CRITICAL GAP — No `ARRAY<T>` support in CSV import:** If Neo4j nodes/edges contain list properties (tags, multi-valued attributes, float embeddings), use **Avro (Path B)**, **BigQuery Reverse ETL (Path C)**, **Spark Spanner**, or serialize to `JSON`.
- **Generated Columns Rule:** If a table contains generated columns (such as hidden `TOKENLIST` columns for hybrid Graph + FTS), the manifest **MUST** list only the non-generated base columns explicitly; Spanner computes generated columns automatically on write.

### Exact `ImportManifest` JSON & Staged `gcloud` Commands

`manifest_nodes.json` (glob wildcards ARE supported in `file_patterns`; `dialect` defaults to `GOOGLE_STANDARD_SQL`):

```json
{
  "dialect": "GOOGLE_STANDARD_SQL",
  "tables": [
    {
      "table_name": "Person",
      "file_patterns": [
        "gs://{bucket}/neo4j-export/nodes_person_*.csv"
      ],
      "columns": [
        {"column_name": "id", "type_name": "INT64"},
        {"column_name": "name", "type_name": "STRING"}
      ]
    },
    {
      "table_name": "Account",
      "file_patterns": [
        "gs://{bucket}/neo4j-export/nodes_account_*.csv"
      ],
      "columns": [
        {"column_name": "id", "type_name": "INT64"},
        {"column_name": "create_time", "type_name": "TIMESTAMP"},
        {"column_name": "is_blocked", "type_name": "BOOL"}
      ]
    }
  ]
}
```

```bash
# Co-locate GCS bucket, Dataflow workers, and Spanner instance in asia-southeast3
# Naming prefix cloud-spanner-import-{instance}-{db} surfaces the job in Spanner Console
gcloud dataflow jobs run cloud-spanner-import-{instance_id}-{database_id}-nodes \
  --project={project_id} \
  --region=asia-southeast3 \
  --gcs-location=gs://dataflow-templates/latest/GCS_Text_to_Cloud_Spanner \
  --worker-machine-type=n1-standard-2 \
  --max-workers=4 \
  --parameters \
instanceId={instance_id},\
databaseId={database_id},\
importManifest=gs://{bucket}/neo4j-export/manifest_nodes.json

# After Stage 1 reaches JOB_STATE_DONE, run Stage 2 for manifest_edges.json
```

## 5. Ingestion Path B: GCS Non-Spanner Avro -> Dataflow (`GCS_Avro_to_Cloud_Spanner`)

Prefer Avro over CSV when migrating Neo4j `LIST` properties or vector embeddings (`ARRAY<FLOAT32/FLOAT64>`).

### Avro -> Spanner (GoogleSQL) Type Mapping

| Avro Type | Spanner GoogleSQL Type | Notes / Precision Rules |
| :--- | :--- | :--- |
| `ARRAY` | `ARRAY<T>` | Native support for Neo4j lists and embeddings |
| `BOOL` | `BOOL` | Direct mapping |
| `INT` / `LONG` | `INT64` (or `TIMESTAMP`) | `LONG` maps to `TIMESTAMP` as microseconds since `1970-01-01T00:00:00Z` |
| `FLOAT` / `DOUBLE` | `FLOAT64` | Direct mapping |
| `STRING` | `STRING` (or `TIMESTAMP`) | Canonical SQL timestamp format (e.g., `'2026-05-28T07:08:21.123456789Z'`) |
| `BYTES` | `BYTES` (or `NUMERIC`) | Maps to `NUMERIC` **ONLY** when `logicalType=decimal`, `precision=38`, `scale=9` (PG requires `147455`/`16383`); otherwise imported as raw `BYTES` |

### Avro Critical Gotchas & Exact `spanner-export.json`

- **Exact filename:** Manifest MUST be named **`spanner-export.json`** in the import GCS directory.
- **NO wildcards in `dataFiles`:** Every relative file path MUST be enumerated explicitly!
- **No automatic FK/interleave ordering:** Stage separate directories (`nodes/spanner-export.json`, then `edges/spanner-export.json`) or defer FKs/interleaving until post-load.
- **No Locality Groups:** Avro import/export does not carry locality group definitions.
- **Nullability:** Any Avro field containing `NULL` must target a nullable Spanner column.
- **Enterprise Networking:** Console Import UI lacks VPC/subnetwork parameters for Avro — launch via `gcloud dataflow jobs run` with `--subnetwork` and `--disable-public-ips`.

```json
{
  "tables": [
    {
      "name": "Person",
      "dataFiles": [
        "nodes/person_part_000.avro",
        "nodes/person_part_001.avro"
      ]
    }
  ],
  "dialect": "GOOGLE_STANDARD_SQL"
}
```

```bash
gcloud dataflow jobs run cloud-spanner-import-{instance_id}-{database_id}-avro-nodes \
  --project={project_id} \
  --region=asia-southeast3 \
  --gcs-location=gs://dataflow-templates/latest/GCS_Avro_to_Cloud_Spanner \
  --worker-machine-type=n1-standard-2 \
  --max-workers=4 \
  --parameters \
instanceId={instance_id},\
databaseId={database_id},\
inputDir=gs://{bucket}/neo4j-avro/nodes
```

## 6. Ingestion Path C: BigQuery Reverse ETL (`EXPORT DATA`)

Use BigQuery Reverse ETL when Neo4j exports (CSV/Avro/Parquet) need deduplication, cleansing, joins, or feature engineering before entering Spanner Graph.

- **Prerequisites:** BigQuery **Enterprise or Enterprise Plus** slot reservation (configure **0 baseline + autoscaling** to avoid idle spend); Spanner **Enterprise/Enterprise Plus**; IAM `roles/bigquery.dataViewer`, `roles/bigquery.user`, `roles/spanner.viewer`, `roles/spanner.databaseUser`.
- **Upsert Semantics & 6-Hour Limit:** `EXPORT DATA` performs batched upserts (`INSERT OR UPDATE` keyed by PK, automatically honoring the `80,000` mutations/commit cap) in arbitrary batch order. Split massive tables to stay well inside BigQuery's **6-hour query timeout**.
- **RI-Safe Sequential Script:** Execute inside a BigQuery `BEGIN ... END;` block with `"priority": "LOW"` and `"tag"` for Spanner Insights attribution, filtering out dangling edges via `INNER JOIN` (warning: **do NOT add unenforced PK/FK constraints in BigQuery**, which can allow BigQuery's optimizer to eliminate those validation joins!):

```sql
BEGIN
  EXPORT DATA OPTIONS (
    uri="https://spanner.googleapis.com/projects/{project_id}/instances/{instance_id}/databases/{database_id}",
    format='CLOUD_SPANNER',
    spanner_options="""{"table": "Person", "priority": "LOW", "tag": "graph_load_person"}"""
  ) AS SELECT id, name FROM `{project_id}.{dataset_name}.Person`;

  EXPORT DATA OPTIONS (
    uri="https://spanner.googleapis.com/projects/{project_id}/instances/{instance_id}/databases/{database_id}",
    format='CLOUD_SPANNER',
    spanner_options="""{"table": "Account", "priority": "LOW", "tag": "graph_load_account"}"""
  ) AS SELECT id, create_time, is_blocked FROM `{project_id}.{dataset_name}.Account`;

  -- Stage 2: RI-safe edge export filtering out any orphaned source/dest references
  EXPORT DATA OPTIONS (
    uri="https://spanner.googleapis.com/projects/{project_id}/instances/{instance_id}/databases/{database_id}",
    format='CLOUD_SPANNER',
    spanner_options="""{"table": "PersonOwnAccount", "priority": "LOW", "tag": "graph_load_person_own_account"}"""
  ) AS
  SELECT poa.id, poa.account_id, poa.create_time
  FROM `{project_id}.{dataset_name}.PersonOwnAccount` poa
  JOIN `{project_id}.{dataset_name}.Person` p ON poa.id = p.id
  JOIN `{project_id}.{dataset_name}.Account` a ON poa.account_id = a.id;
END;
```

### 4 Strategies for Synchronizing Deletions (Reverse ETL Upserts Only)

1. **Logical / Soft Delete + Generated TTL Column:**
   ```sql
   ALTER TABLE PersonOwnAccount ADD COLUMN is_deleted BOOL DEFAULT (FALSE);
   ALTER TABLE PersonOwnAccount ADD COLUMN expired_ts_generated TIMESTAMP AS (
     IF(is_deleted, TIMESTAMP("1970-01-01 00:00:00+00"), TIMESTAMP("9999-01-01 00:00:00+00"))
   ) STORED HIDDEN;
   ALTER TABLE PersonOwnAccount
     ADD ROW DELETION POLICY (OLDER_THAN(expired_ts_generated, INTERVAL 0 DAY));
   ```
   Filter `NOT is_deleted` in queries until Spanner TTL sweeps rows (or purge explicitly with Partitioned DML).
2. **BigQuery Change History `CHANGES()` *(Preview)* + Spanner TTL:** Enable `ALTER TABLE ... SET OPTIONS (enable_change_history=TRUE)` in BQ; add `expired_ts TIMESTAMP` + `ROW DELETION POLICY (OLDER_THAN(expired_ts, INTERVAL 1 DAY))` in Spanner (TTL retention MUST exceed pipeline interval). `LEFT JOIN` `CHANGES(TABLE ..., start_ts, end_ts)` against the live table so delete-then-reinsert rows are preserved. **10-Minute Lag Rule:** `end_ts` MUST be **`>= 10 minutes` before `CURRENT_TIMESTAMP()`** (e.g., run daily UTC jobs at `>= 00:10 UTC`).
3. **Last-Seen Timestamp TTL (`last_seen_ts`):** Stamp every upserted row; filter/TTL stale rows not refreshed within your sync window.
4. **Full Refresh:** Truncate/delete target tables prior to reload (simplest, highest downtime/re-load cost).

### Orchestration & Continuous Queries

- **Batch Orchestration:** Prefer **BigQuery Pipelines (Dataform DAG)** or **Cloud Workflows + Cloud Scheduler** over standalone Scheduled Queries (which lack dynamic dependency DAGs and rich retry handling).
- **Near-Real-Time Continuous Queries:** Require a BigQuery reservation with `job_type=CONTINUOUS`, `bq --continuous=true`, `APPENDS(TABLE ..., CURRENT_TIMESTAMP() - INTERVAL 10 MINUTE)` (within BQ time-travel window), and `"change_timestamp_column": "{ts_col}"` in `spanner_options`.
- **3 Hard Continuous Query Limitations:** (a) **No cross-table load ordering** -> ONLY valid on relaxed-RI schemas (`INTERLEAVE IN` + `NOT ENFORCED` FKs); (b) **`APPENDS` captures INSERTS ONLY** (ignores updates & deletes); (c) **Any Spanner error** (duplicate PK, RI violation) **cancels the continuous query**, requiring manual remediation and restart.

## 7. Bulk-Load Worker Sizing, Pre-Splitting & Parity Validation

### Dataflow Worker Sizing Formula

Target **`70%–90%` Spanner CPU** during bulk import. Uncapped Dataflow autoscaling saturates Spanner and triggers aborts/timeouts. Cap total Dataflow worker vCPUs to **`4x–6x` the Spanner node count**:

| Spanner Capacity | Target Worker vCPUs (`4x–6x`) | Example `--max-workers` (`n1-standard-2`, 2 vCPU) |
| :--- | :--- | :--- |
| **100 PU (0.1 node, dev/test)** | `1–2` vCPUs | `--max-workers=1` (prevents CPU saturation) |
| **1 Node (1,000 PU)** | `4–6` vCPUs | `--max-workers=2` to `3` |
| **10 Nodes (10,000 PU)** | `40–60` vCPUs | `--max-workers=25` (50 vCPUs = `5x` nodes) |

- **Additional Accelerators:** Co-locate GCS, Dataflow, and Spanner in `asia-southeast3`; **pre-split** target tables (`gcloud spanner databases splits add`) before large initial loads; temporarily enable **Managed Autoscaler** (Enterprise+) during bulk load windows.

### Post-Migration Graph Parity Validation

Run matching checks on BOTH Neo4j CE and Spanner Graph and assert 100% equality:
1. **Total counts:** Total nodes (`MATCH (n) RETURN count(n)`) and total edges (`MATCH ()-[r]->() RETURN count(r)`).
2. **Per-label breakdown:** Node count per label and edge count per edge label (accounting for N:1 dual-projected edges!).
3. **Per-property statistics & deterministic hashes:** `COUNT`, `SUM`, `AVG`, `MIN`, `MAX` per property, plus order-independent table hash verification:

```sql
-- Deterministic per-table row-set parity fingerprint in Spanner GoogleSQL
SELECT
  COUNT(*) AS row_count,
  BIT_XOR(FARM_FINGERPRINT(CONCAT(CAST(id AS STRING), '|', IFNULL(name, '')))) AS xor_fingerprint
FROM Person;
```

- **Official Documentation:** [Migrate to Spanner Graph](https://docs.cloud.google.com/spanner/docs/graph/migrate), [Import CSV to Spanner](https://docs.cloud.google.com/spanner/docs/import-export-csv), [Import Non-Spanner Avro](https://docs.cloud.google.com/spanner/docs/import-non-spanner), [BigQuery Reverse ETL to Spanner Graph](https://docs.cloud.google.com/spanner/docs/graph/reverse-etl), [Manage Schemaless Graph Data](https://docs.cloud.google.com/spanner/docs/graph/manage-schemaless-data).
