---
name: spanner-migration-and-neo4j-playbook
metadata:
  version: "1.0.0"
  category: "Cloud Spanner Enterprise"
description: >-
  Guides Cloud Spanner migrations, Neo4j Community Edition to Spanner Graph, and
  Elasticsearch to Spanner FTS. Use when planning 7-stage migrations (SMT,
  Datastream+Dataflow CDC, DLQ replay, DVT, Spanner_to_SourceDb reverse
  replication, cutover/fallback), exporting Neo4j CE via APOC, loading
  nodes-before-edges via Dataflow CSV/Avro or BigQuery Reverse ETL, translating
  openCypher v9 to ISO GQL (TRAIL, FOR, SAFE_TO_JSON, THEN RETURN), or migrating
  ES stemming/synonyms/fuzzy/facets and Managed Spark bulk loads to Spanner FTS.
  Don't use for greenfield relational DDL (spanner-schema-and-data-modeling),
  greenfield GQL/Algorithms (spanner-graph-modeling-and-gql), greenfield
  Vector/GraphRAG (spanner-search-ai-and-graphrag), platform ops
  (spanner-enterprise-platform-ops), MCP security
  (spanner-mcp-toolbox-and-agent-security), or profiling/sizing
  (spanner-performance-observability-and-sizing).
---

# Cloud Spanner Migration, Neo4j CE & Elasticsearch FTS Playbook

End-to-end engineering playbook for migrating relational databases (MySQL, PostgreSQL, Cassandra), **Neo4j Community Edition** graphs, and **Elasticsearch** search indexes into **Cloud Spanner Enterprise**.

## Safety & Live-Database Guardrails

Before executing any command or DDL/DML against a live remote environment:

1. **Require explicit user confirmation** before mutating any live source or Spanner database, dropping indexes/foreign keys, pausing live Datastream streams, launching/draining Dataflow or Managed Spark jobs, or executing cutover/fallback traffic switches.
2. **Validate in staging / dry-run first:** Test schema conversion, APOC exports, Dataflow manifests, openCypher-to-GQL rewrites, and relevance benchmarks against a non-production database before production runs.
3. **Never pass credentials in plaintext CLI flags:** Load MySQL, Neo4j, or Elasticsearch credentials via Secret Manager or Workload Identity.
4. **Never cut over or fail back with non-empty DLQs:** Always inspect GCS `severe/` and `retry/` directories directly before switching writes.

## Non-Negotiable Migration Invariants

1. **Edition & Dialect Boundaries:**
   - **Spanner Graph**, **Full-Text Search (FTS)**, **Vector Search**, **Columnar Engine**, **Incremental Backups**, and **Managed Autoscaler** require **Enterprise** (`$1.2915/node-hr` in `regional-asia-southeast3`) or **Enterprise Plus** (`$1.7955/node-hr`). Standard edition lacks Graph and FTS.
   - **Spanner Graph is strictly GoogleSQL only** (PostgreSQL interface is unsupported).
   - **Export/Import edition rule:** Importing a Spanner export requires a target instance on the **same or higher edition** than the source.
2. **Graph Load-Order Invariant:** Always load **ALL Node tables before ANY Edge table** when using `INTERLEAVE IN PARENT` or enforced `FOREIGN KEY` constraints. Defer secondary indexes, enforced FKs, and change streams until after initial bulk loads.
3. **Platform Quotas & Bulk-Load Sizing:**
   - Max **`80,000` mutations/commit** (`40,000` rows on a table with 1 secondary index); max **`100 MiB`** commit size; `10 TiB/node` storage (alert at `75%`).
   - Target **`70%–90%` Spanner CPU** during bulk loads by capping Dataflow worker vCPUs to **`4×–6×` Spanner nodes** (e.g., 10 nodes -> 25 `n1-standard-2` workers = 50 vCPUs; cap to 1 small worker on 100 PU dev/test instances). Steady-state regional CPU target is `<= 65%`.
4. **DLQ Ground-Truth Rule:** Both forward (`Total permanent errors`) and reverse (`severe_error_count`) Dataflow counters are **cumulative and never decrement** after replay. Treat GCS `severe/` as the sole source of truth. Run `runMode="retryDLQ"` concurrently if needed, but **NEVER run `runMode="retryAllDLQ"` concurrently** with an active streaming pipeline.
5. **openCypher -> ISO GQL Semantic Traps:**
   - Every GQL query starts with `GRAPH {graph_name}` and uses `@param` (not `$param`).
   - Spanner defaults to **`WALK`** (allows repeated edges); add **`MATCH TRAIL`** for openCypher edge uniqueness.
   - Top-level `RETURN` cannot emit raw graph elements; wrap in **`SAFE_TO_JSON(e)`** (or `TO_JSON(e)`).
   - Replace `UNWIND` with **`FOR`**, alias all non-trivial `RETURN`/`WITH` expressions, and perform mutations via GoogleSQL DML (`INSERT OR UPDATE`, `THEN RETURN`) or the Mutation API.
6. **Elasticsearch -> Spanner FTS Traps:**
   - Always pass **`enablePartialRowUpdates=true`** on the Spark Spanner connector; omitting it attempts to write hidden generated `TOKENLIST` columns and fails immediately.
   - Use **`fulltext_dictionary_table = true`** (exact 2 columns: `Key`, `Value`, default schema, single lowercase word keys, `rquery` dialect) for domain synonyms; `enhance_query => true` alone does NOT replace curated domain aliases and may broaden recall semantically.
7. **Single-Region Co-Location & Companion AI/Security Topology:**
   - Co-locate Cloud Spanner (`regional-asia-southeast3`, 3-zone sync quorum, 99.99% SLA, `1.05x` vs `us-central1`, Thailand PDPA residency), GCS staging buckets, Dataflow workers, Managed Spark subnets, Cloud Run, user-managed Secret Manager, and same-region PITR/backups inside the **single primary region** (e.g., Bangkok `asia-southeast3`).
   - No pre-built dual-region pairs Bangkok + Singapore; use Singapore (`asia-southeast1`, ~25 ms RTT) **only** for companion GCP services unavailable in Bangkok (Vertex AI embeddings/LLMs, Model Armor, Cloud DLP)—not as a default second Spanner DR instance.

## Migration Decision Matrix

| Source & Goal | Recommended Path | Key Constraint / Gotcha | Deep-Dive Reference |
| :--- | :--- | :--- | :--- |
| **MySQL / PG** live zero/minimal-downtime migration | SMT schema -> Datastream CDC to GCS (`skip_dataflow=true`) -> `sourcedb-to-spanner` backfill -> `Cloud_Datastream_to_Spanner` CDC -> DVT -> `Spanner_to_SourceDb` fallback | Pre-built reverse replication supports MySQL & Cassandra (not PG); open **TCP `12345`** between reverse Dataflow workers | [migration-lifecycle-and-cutover.md](references/migration-lifecycle-and-cutover.md) |
| **MySQL / PG** offline bulk migration | Stop writes / paused replica -> split dump files -> SMT POC (`<100 GB`) or `sourcedb-to-spanner` Dataflow (`>100 GB`) | Use Repeatable Read + pessimistic locking to align MySQL txn semantics; watch `NUMERIC(38,9)` truncation | [migration-lifecycle-and-cutover.md](references/migration-lifecycle-and-cutover.md) |
| **Neo4j CE** flat properties (no `ARRAY` cols) | APOC streaming CSV (`separateHeader: true`) -> GCS -> staged `GCS_Text_to_Cloud_Spanner` (Nodes then Edges) | **NO CSV headers**; CSV import omits `ARRAY<T>`; list non-generated columns explicitly in manifest | [neo4j-ce-export-and-ingestion.md](references/neo4j-ce-export-and-ingestion.md) |
| **Neo4j CE** with `LIST` / vector `ARRAY` props | Export Avro -> GCS `spanner-export.json` -> staged `GCS_Avro_to_Cloud_Spanner` | **NO wildcards** in `dataFiles`; Avro job does not resolve FK/interleave ordering automatically | [neo4j-ce-export-and-ingestion.md](references/neo4j-ce-export-and-ingestion.md) |
| **Neo4j CE** needing cleansing / feature engineering | Stage CSV/Avro/Parquet in BigQuery -> sequential `BEGIN ... END` `EXPORT DATA` Reverse ETL | Upserts only (configure TTL/soft-delete or `CHANGES()` for deletes); `INNER JOIN` edges to filter orphans | [neo4j-ce-export-and-ingestion.md](references/neo4j-ce-export-and-ingestion.md) |
| **openCypher v9** queries & mutations | Translate to ISO GQL (`GRAPH`, `TRAIL`, `FOR`, `SAFE_TO_JSON`) + GoogleSQL DML (`THEN RETURN`) | `allShortestPath`, `id(n)`, `n[dyn]`, `stDevP`, `XOR`, chained `<` unsupported; see exact rewrites | [opencypher-to-gql-parity.md](references/opencypher-to-gql-parity.md) |
| **Elasticsearch** search & bulk index load | `TOKENIZE_FULLTEXT`/`SUBSTRING` + `SEARCH INDEX` + Custom Dictionary + Managed Spark Runtime `2.3` | Set `enablePartialRowUpdates=true`; runtime SA MUST have `roles/dataproc.worker` | [elasticsearch-to-spanner-fts.md](references/elasticsearch-to-spanner-fts.md) |

## Runnable Quickstart Cheatsheet

### 1. Staged Nodes-Before-Edges Dataflow CSV Import (Bangkok Primary)

```bash
# Stage 1: Load ALL node tables first (CSV files MUST omit header rows)
gcloud dataflow jobs run cloud-spanner-import-{instance_id}-{database_id}-nodes \
  --project={project_id} \
  --region=asia-southeast3 \
  --gcs-location=gs://dataflow-templates/latest/GCS_Text_to_Cloud_Spanner \
  --max-workers=4 \
  --parameters instanceId={instance_id},databaseId={database_id},importManifest=gs://{bucket}/export/manifest_nodes.json

# Wait for JOB_STATE_DONE, then run Stage 2 for ALL edge tables:
gcloud dataflow jobs run cloud-spanner-import-{instance_id}-{database_id}-edges \
  --project={project_id} \
  --region=asia-southeast3 \
  --gcs-location=gs://dataflow-templates/latest/GCS_Text_to_Cloud_Spanner \
  --max-workers=4 \
  --parameters instanceId={instance_id},databaseId={database_id},importManifest=gs://{bucket}/export/manifest_edges.json
```

### 2. High-Frequency openCypher -> ISO GQL Translations

| openCypher v9 | Cloud Spanner Graph (ISO GQL / GoogleSQL) |
| :--- | :--- |
| `MATCH (a)-[e:T*1..3]->(b) WHERE a.id = $id RETURN a, edges(p)` | `GRAPH {graph_name} MATCH TRAIL (a:Node)-[e:T]->{1,3}(b:Node) WHERE a.id = @id RETURN SAFE_TO_JSON(a) AS a, SAFE_TO_JSON(e) AS e;` |
| `UNWIND [1, 2, 3] AS x RETURN x` | `GRAPH {graph_name} LET arr = [1, 2, 3] FOR x IN arr RETURN x;` |
| `MATCH p = shortestPath((a)-[*1..6]->(b)) RETURN length(p)` | `GRAPH {graph_name} MATCH ANY SHORTEST (a)-[e]->{1,6}(b) RETURN ARRAY_LENGTH(e) AS hop_count;` |
| `RETURN collect(n.name), size((n)-->())` | `RETURN ARRAY_AGG(n.name) AS names, VALUE { MATCH (n)-->() RETURN COUNT(*) } AS out_deg;` |
| `MERGE (n:Node {id: 1}) SET n.flag = true RETURN n.id` | `INSERT OR UPDATE INTO Node (id, flag) VALUES (1, TRUE) THEN RETURN id;` |

### 3. Pre-Cutover DLQ Audit & Concurrent Replay Gate

```bash
# Sole source of truth for stranded errors (counters never decrement!)
gcloud storage ls gs://{bucket}/{env}/{service}/cdc/{db_schema}/severe/

# Safe concurrent replay of permanent errors only (NEVER use retryAllDLQ while streaming!)
gcloud dataflow flex-template run {retry_job_name} \
  --project={project_id} \
  --region=asia-southeast3 \
  --template-file-gcs-location=gs://dataflow-templates/latest/flex/Cloud_Datastream_to_Spanner \
  --additional-experiments=use_runner_v2 \
  --parameters streamName="ignore",datastreamSourceType=mysql,instanceId={instance_id},databaseId={database_id},sessionFilePath={session_gcs_path},deadLetterQueueDirectory={dlq_gcs_dir},runMode="retryDLQ"
```

### 4. Managed Spark Bulk Load (Elasticsearch -> Spanner FTS)

```bash
gcloud dataproc batches submit pyspark {script_gcs_path}/es_to_spanner.py \
  --project={project_id} \
  --region=asia-southeast3 \
  --version=2.3 \
  --deps-bucket={deps_bucket} \
  --subnet={managed_spark_subnet} \
  --service-account={spark_runtime_sa} \
  --properties=^#^spark.jars.packages=com.google.cloud.spark.spanner:spark-3.5-spanner:1.4.0,org.elasticsearch:elasticsearch-spark-30_2.13:8.15.3 \
  -- --es-nodes={es_host} --es-port=9200 --es-resource={es_index} \
     --es-input-max-docs-per-partition=50000 --es-scroll-size=2000 \
     --spanner-project-id={project_id} --spanner-instance-id={instance_id} \
     --spanner-database-id={database_id} --spanner-table={table_name}
# Inside PySpark writer: .option("enablePartialRowUpdates", "true") is MANDATORY for generated TOKENLIST columns!
```

## Common Migration Anti-Patterns (Never Do These)

| Anti-Pattern | Failure Mode | Required Fix |
| :--- | :--- | :--- |
| Running `runMode="retryAllDLQ"` while CDC streaming is active | File contention and duplicate/corrupted processing on `retry/` | Use `runMode="retryDLQ"` concurrently (`severe/` only), or stop streaming first |
| Trusting `Total permanent errors` / `severe_error_count` after replay | Counters are cumulative and never decrement | Audit `gs://{bucket}/.../severe/` directly before cutover or fallback |
| Leaving CSV header rows in `GCS_Text_to_Cloud_Spanner` files | Dataflow import fails parsing headers as data rows | Strip headers or export from Neo4j with APOC `separateHeader: true` |
| Loading Edge tables before Node tables (or in parallel with enforced FKs) | Parent-missing / FK violation aborts leaving partial data | Run Stage 1 (all Nodes) to `JOB_STATE_DONE` before Stage 2 (all Edges) |
| Omitting `TRAIL` in translated openCypher `MATCH` queries | Default `WALK` mode repeats edges and loops on cyclic graphs | Write `MATCH TRAIL (...)` whenever openCypher edge uniqueness is expected |
| Omitting `enablePartialRowUpdates=true` in Spark ES -> Spanner loads | Connector tries writing hidden generated `TOKENLIST` columns and fails | Set `.option("enablePartialRowUpdates", "true")` and write base columns only |
| Assuming `enhance_query => true` replaces curated domain synonyms | Domain aliases miss canonical phrases; broadens unrelated queries | Create a 2-column `fulltext_dictionary_table = true` table and pass `dictionary => '...'` |

## Reference Playbooks

Read the matching reference file for full DDL, CLI flags, MQL monitoring queries, and edge cases:

- [migration-lifecycle-and-cutover.md](references/migration-lifecycle-and-cutover.md) — 7-stage migration lifecycle, 10 assessment dimensions, Bangkok single-region pricing & topology, tooling matrix, TrueTime & MySQL transaction parity, Datastream + Dataflow CDC, DLQ replay, DVT, `Spanner_to_SourceDb` reverse replication, and MQL-gated cutover/fallback runbooks.
- [neo4j-ce-export-and-ingestion.md](references/neo4j-ce-export-and-ingestion.md) — Schematized vs schemaless (`DYNAMIC LABEL`/`DYNAMIC PROPERTIES`) decisions, heap-safe APOC CSV/JSON exports, nodes-before-edges RI engineering, Dataflow CSV & Avro manifest formats, worker vCPU sizing, BigQuery Reverse ETL (`EXPORT DATA`, 4 deletion strategies, Continuous Queries), and graph parity validation.
- [opencypher-to-gql-parity.md](references/opencypher-to-gql-parity.md) — Complete openCypher v9 -> ISO GQL + GoogleSQL parity matrices: pattern matching (`TRAIL`, `ANY SHORTEST`/`CHEAPEST`, path variables), clauses (`FOR`, `CALL`/`CALL PER ()`, Graph Algorithms), types, all scalar/aggregate/list/math/string/array functions, and DML/Mutation API write equivalents (`GRAPH_TABLE`, `THEN RETURN`, `DETACH DELETE`).
- [elasticsearch-to-spanner-fts.md](references/elasticsearch-to-spanner-fts.md) — Field classification, stemming vs `enhance_query => true`, Custom Dictionary tables (`fulltext_dictionary_table = true`), fuzzy `TOKENIZE_SUBSTRING` + `SEARCH_NGRAMS`/`SCORE_NGRAMS`, SQL CTE faceting + `post_filter` + Columnar rollups, and Managed Spark ES-to-Spanner bulk data movement.

## When to Route to Sibling Skills

| User Goal Outside Migration Scope | Route To |
| :--- | :--- |
| Greenfield relational PKs, interleaving, indexes, views, TTL, locality groups | `spanner-schema-and-data-modeling` |
| Greenfield `CREATE PROPERTY GRAPH`, GQL tuning hints, Data Boost Graph Algorithms | `spanner-graph-modeling-and-gql` |
| Greenfield Vertex AI `CREATE MODEL`, FTS, ScaNN Vector Search, RRF/GraphRAG | `spanner-search-ai-and-graphrag` |
| Edition selection, CUDs, CLI/Emulator, autoscaler deployment, backups, PITR | `spanner-enterprise-platform-ops` |
| MCP Toolbox `tools.yaml`, FGAC `GRANT`/IAM, OIDC auth, Model Armor | `spanner-mcp-toolbox-and-agent-security` |
| Query Optimizer plans, `SPANNER_SYS` Insights, Key Visualizer, node sizing & TCO | `spanner-performance-observability-and-sizing` |

## Official Documentation

- [Cloud Spanner Migration Overview](https://docs.cloud.google.com/spanner/docs/migration-overview)
- [Migrate to Spanner Graph](https://docs.cloud.google.com/spanner/docs/graph/migrate)
- [openCypher to Spanner Graph Reference](https://docs.cloud.google.com/spanner/docs/graph/opencypher-reference)
- [Spanner Enterprise Samples (`spanner-bb-samples`)](https://github.com/GoogleCloudPlatform/spanner-bb-samples)
