---
name: spanner-schema-and-data-modeling
metadata:
  version: "1.0.0"
  category: "Cloud Spanner Enterprise"
description: >-
  Guides Cloud Spanner relational schema, PK, indexing, constraint, view DDL, and lifecycle design. Use when designing split-safe PKs (UUIDv4, bit-reversed IDENTITY/SEQUENCE, FARM_FINGERPRINT), INTERLEAVE IN PARENT vs INTERLEAVE IN, multi-tenancy, secondary/covering/NULL_FILTERED/expression indexes, enforced vs NOT ENFORCED FKs (same-table dual-cascade ban), CHECKs, generated columns, named schemas, CREATE VIEW & SECURE_CONTEXT() DDL syntax, synonyms, INFORMATION_SCHEMA, DDL LROs, Locality Groups, TTL, or Change Streams DDL. Don't use for editions/CLI/Emulator/Queues (spanner-enterprise-platform-ops), Property Graphs/GQL (spanner-graph-modeling-and-gql), Search/Vector/ML models (spanner-search-ai-and-graphrag), migrations (spanner-migration-and-neo4j-playbook), FGAC GRANT/IAM/MCP security (spanner-mcp-toolbox-and-agent-security), or query tuning/sizing (spanner-performance-observability-and-sizing).
---

# Cloud Spanner Schema & Data Modeling

Authoritative production blueprint for Cloud Spanner relational data modeling across both **GoogleSQL** and **PostgreSQL** dialects, covering split-safe primary keys, table interleaving, secondary indexes, foreign keys, constraints, views, named schemas, synonyms, `INFORMATION_SCHEMA`, online DDL LROs, Enterprise Locality Groups & Tiered Storage, TTL, and Change Streams DDL.

> **Safety & Confirmation Gate:** NEVER execute live remote DDL mutations (`gcloud spanner databases ddl update`, `DROP TABLE`, `DROP INDEX`, `DROP CHANGE STREAM`, `ALTER TABLE ... DROP ROW DELETION POLICY`, or `CALL compact_all()`) against a remote Cloud Spanner database without explicit user confirmation of `{project_id}`, `{instance_id}`, `{database_id}`, and the exact DDL statements. Before applying validating DDL (such as `NOT NULL`, `CHECK`, `FOREIGN KEY`, `allow_commit_timestamp`, or length reductions), always pre-audit existing rows with read-only `SELECT COUNT(*)` queries to prevent transient write-rejection windows.

## Non-Negotiable Schema Invariants

1. **Never lead a high-write PK or global index with a monotonic column** (`TIMESTAMP`, commit timestamp, sequential integer). Changing `ASC` to `DESC` does **not** eliminate write hotspots; use UUID v4, bit-reversed `IDENTITY`/`SEQUENCE`, key swapping, or `FARM_FINGERPRINT` hash sharding.
2. **Choose `INTERLEAVE IN PARENT` OR an enforced `FOREIGN KEY`—never both** for the same relationship. Never combine `INTERLEAVE IN PARENT T ON DELETE CASCADE` with a self-referencing `FOREIGN KEY (...) REFERENCES T (...) ON DELETE CASCADE` on the same table (use standalone dual-FK cascade or interleaved + `NOT ENFORCED`/`NO ACTION` FK).
3. **`UNIQUE NULL_FILTERED` does NOT enforce uniqueness when any key column is `NULL`:** Rows containing any `NULL` key part are omitted from the index, allowing duplicate tuples like `(1, NULL, 1)`. Mark key columns `NOT NULL` or omit `NULL_FILTERED` when strict uniqueness is required.
4. **Place `PENDING_COMMIT_TIMESTAMP()` writes last in a transaction:** After writing a pending commit timestamp, that table and all its secondary indexes become **unreadable** for the remainder of the same transaction.
5. **Every `CREATE [OR REPLACE] VIEW` requires explicit `SQL SECURITY INVOKER` or `DEFINER`, and `CREATE OR REPLACE VIEW` wipes existing grants:** Always re-apply `GRANT SELECT` immediately after replacing a view, and explicitly `CAST` projected columns to avoid schema-evolution invalidation.
6. **Always use `ALTER DATABASE {database_id} SET OPTIONS (...)`** for database-level options (such as `default_sequence_kind = 'bit_reversed_positive'` or `use_unenforced_foreign_key_for_query_optimization = false`).
7. **Batch `CREATE TABLE` + its `CREATE INDEX` statements contiguously** with no intervening backfill/validation DDL to trigger **single-version optimization** (1 schema version, zero backfill). Cap backfill/validation statements at **10 per batch** and target **<= 3 backfilled indexes/database/day**.
8. **Edition Gate for Storage Tiering & Compaction:** **Locality Groups**, **Tiered Storage (SSD/HDD)**, and **Manual Major Compaction (`compact_all()`)** require **Cloud Spanner Enterprise or Enterprise Plus** (unavailable on Standard).
9. **Backup restores automatically drop all TTL policies:** Re-apply `ROW DELETION POLICY` (GoogleSQL) / `TTL INTERVAL` (PostgreSQL) after every restore, and ensure all interleaved children and referencing FKs on a TTL-enabled table use `ON DELETE CASCADE`.
10. **Keep insert-heavy tables and `UNIQUE`/high-write indexes on SSD-only locality groups:** Every `INSERT` checks row-existence metadata in the table's locality group; offload only large cold payload columns (blobs, archives) to HDD or age-based spill.

## Reference Modules (Load On Demand)

| Need / Task | Reference Document |
| :--- | :--- |
| Physical splits (`8 GiB`), `INTERLEAVE IN PARENT` vs `INTERLEAVE IN`, PK strategies (UUIDv4, `IDENTITY`/`SEQUENCE`, `FARM_FINGERPRINT`), PK migrations, GoogleSQL/PG data types, arbitrary precision numerics, commit timestamps, bulk loading, and 4 multi-tenancy patterns | [data-model-and-pk-design.md](references/data-model-and-pk-design.md) |
| Secondary indexes (global vs interleaved, `STORING`/`INCLUDE`, `NULL_FILTERED` & per-column `WHERE`, `UNIQUE` traps, expression & proto indexes, `DESC` scans, `FORCE_INDEX`), enforced vs `NOT ENFORCED` FKs, dual-cascade ban, `CHECK` constraints, and `STORED`/virtual generated columns | [indexes-fks-and-constraints.md](references/indexes-fks-and-constraints.md) |
| Named schemas, `CREATE VIEW` (`INVOKER` vs `DEFINER`), parameterized views (`SECURE_CONTEXT()`), table renaming & zero-downtime synonyms, atomic table swaps, and `INFORMATION_SCHEMA` GoogleSQL vs PostgreSQL introspection | [views-schemas-and-synonyms.md](references/views-schemas-and-synonyms.md) |
| Zero-downtime schema updates, early write-rejection LROs, DDL batching & throttling, Enterprise Locality Groups & Tiered Storage economics, `compact_all()`, TTL policies & monitoring, Change Streams DDL & cross-region DR, LRO `gcloud` workflows, and CPU priority escalation | [lifecycle-ttl-locality-and-lros.md](references/lifecycle-ttl-locality-and-lros.md) |

## Architecture & Modeling Decision Matrix

| Design Choice | Recommended Default | When to Choose Alternative | Key Limit / Gotcha |
| :--- | :--- | :--- | :--- |
| **Primary Key Strategy** | **UUID v4** (`NEW_UUID()` / `gen_random_uuid()`) for greenfield & geo-partitioned tables | **Bit-reversed `IDENTITY`/`SEQUENCE`** when 64-bit integer PKs are required; **`(EntityId, EventTs DESC)`** or **`(ShardId, ...)`** for time-series | Cannot alter PK columns or convert `STRING(36)` <-> `UUID` later; never use UUID v1 |
| **Parent-Child Modeling** | **`INTERLEAVE IN PARENT ... ON DELETE CASCADE`** for tightly coupled 1:N trees accessed/deleted together | **Enforced FK** for M:N, non-PK refs, or removable relationships; **`INTERLEAVE IN`** for parallel bulk load then `ALTER TABLE ... SET INTERLEAVE IN PARENT` | Max **7 layers** deep; interleaving is permanent; FK cascade hits **80,000** mutations/commit limit |
| **Same-Table Self-Reference Edge** (`T -> T`) | **Standalone table + 2 enforced `ON DELETE CASCADE` FKs** (Option A) | **`INTERLEAVE IN PARENT T ON DELETE CASCADE` + `NOT ENFORCED`/`NO ACTION` dest FK** (Option B) for max forward locality | Combining `INTERLEAVE IN PARENT T ON DELETE CASCADE` + self-ref cascading FK to `T` is **forbidden** |
| **Secondary Index Placement** | **Interleaved index** (`INTERLEAVE IN Parent`) for entity-scoped queries & monotonic columns | **Global index** for cross-database lookups (`WHERE Email = @email`) | Max **16 key columns**, **`8 KiB`** key size; `ORDER BY pk DESC LIMIT 1` needs a `DESC` index |
| **Derived / Filtered Column** | **Expression index** `((JSON_VALUE(...)))` or **Virtual generated col + `NULL_FILTERED` index** | **`STORED` generated column** for generated PKs (`ShardId`), proto extraction, or multi-tier TTL | Virtual `STRING`/`BYTES` must be `MAX` & nullable; generated cols cannot reference commit timestamps |
| **Multi-Tenancy Tiering** | **Row pattern** (`TenantId` leading PK) for massive SaaS scale | **Database pattern** (per-tenant CMEK/backup, max `100` DBs/instance, `10` DBs/`100` PU) or **Instance pattern** (strict compliance/isolation) | Table-per-tenant (`5,000` tables/DB) risks DDL schema-version throttling during fast onboarding |
| **Cold Storage Tiering** *(Enterprise+)* | **SSD-only table LG** + column-level `hdd_only` or `ssd_to_hdd_spill_timespan` (min `1h`) | **Column-grouping LG** (no storage option) to isolate hot scans from wide columns on SSD | **5x cheaper HDD**: Bangkok `asia-southeast3` `$0.315` vs `$0.063`/GiB-mo; Singapore `asia-southeast1` `$0.3432` vs `$0.06864`/GiB-mo |
| **Data Expiration** | **TTL** (`ROW DELETION POLICY` / `TTL INTERVAL`) for automated daily cleanup | **Partitioned DML** first to clear huge pre-existing backlogs before enabling TTL | <= `72h` deletion SLA, ~`7d` compaction reclaim; intra-region replication `$0.00/GiB` in `regional-asia-southeast3` & `regional-asia-southeast1` |

## Runnable Quickstart Templates

### 1. Enterprise GoogleSQL Schema Pattern (Interleaving + Covering Index + Locality Group + TTL)

```sql
-- Enterprise / Enterprise Plus: offload large payloads to HDD after 30 days
-- (Bangkok regional-asia-southeast3: $0.063/GiB-mo HDD vs $0.315/GiB-mo SSD)
CREATE LOCALITY GROUP cold_payload_archive OPTIONS (
  storage = 'ssd',
  ssd_to_hdd_spill_timespan = '30d'
);

CREATE TABLE Customers (
  CustomerId  UUID NOT NULL DEFAULT (NEW_UUID()),
  Email       STRING(320) NOT NULL,
  RegionCode  STRING(32) NOT NULL,
  UpdatedAt   TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
) PRIMARY KEY (CustomerId);

CREATE UNIQUE INDEX IdxCustomersByEmail ON Customers(Email);

CREATE TABLE Orders (
  CustomerId  UUID NOT NULL,
  OrderId     INT64 NOT NULL GENERATED BY DEFAULT AS IDENTITY (BIT_REVERSED_POSITIVE),
  OrderTime   TIMESTAMP NOT NULL,
  Status      STRING(32) NOT NULL,
  TotalAmount NUMERIC NOT NULL,
  RawPayload  JSON OPTIONS (locality_group = 'cold_payload_archive'),
  CONSTRAINT ck_order_total_non_neg CHECK (TotalAmount >= 0),
) PRIMARY KEY (CustomerId, OrderId),
  INTERLEAVE IN PARENT Customers ON DELETE CASCADE,
  ROW DELETION POLICY (OLDER_THAN(OrderTime, INTERVAL 365 DAY));

-- Interleaved reverse-chronological covering index (batch right after CREATE TABLE!)
CREATE INDEX IdxOrdersByCustomerRecent
  ON Orders(CustomerId, OrderTime DESC)
  STORING (Status, TotalAmount),
  INTERLEAVE IN Customers;

-- Read-only view with explicit SQL SECURITY & CASTs
CREATE OR REPLACE VIEW ActiveCustomerOrdersView
SQL SECURITY INVOKER AS
SELECT
  CAST(c.CustomerId AS UUID) AS CustomerId,
  CAST(c.Email AS STRING) AS Email,
  CAST(o.OrderId AS INT64) AS OrderId,
  CAST(o.OrderTime AS TIMESTAMP) AS OrderTime,
  CAST(o.TotalAmount AS NUMERIC) AS TotalAmount
FROM Customers AS c
JOIN Orders AS o ON c.CustomerId = o.CustomerId
WHERE o.Status = 'ACTIVE';
```

### 2. PostgreSQL Dialect Equivalent Highlights

```sql
CREATE LOCALITY GROUP cold_payload_archive
  STORAGE 'ssd' SSD_TO_HDD_SPILL_TIMESPAN '30d';

CREATE TABLE customers (
  customer_id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  email       varchar(320) NOT NULL,
  updated_at  spanner.commit_timestamp
                DEFAULT spanner.pending_commit_timestamp()
                ON UPDATE spanner.pending_commit_timestamp()
);

CREATE TABLE orders (
  customer_id  uuid NOT NULL,
  order_id     bigint GENERATED BY DEFAULT AS IDENTITY (BIT_REVERSED_POSITIVE),
  order_time   timestamptz NOT NULL,
  status       varchar(32) NOT NULL,
  total_amount numeric NOT NULL,
  raw_payload  jsonb LOCALITY GROUP cold_payload_archive,
  CONSTRAINT ck_order_total_non_neg CHECK (total_amount >= 0),
  PRIMARY KEY (customer_id, order_id)
) INTERLEAVE IN PARENT customers ON DELETE CASCADE
  TTL INTERVAL '365 days' ON order_time;

CREATE INDEX idx_orders_by_customer_recent
  ON orders(customer_id, order_time DESC)
  INCLUDE (status, total_amount)
  INTERLEAVE IN customers;
```

### 3. Safe DDL Submission & LRO Verification Workflow

```bash
# 1. Pre-audit existing rows before any validating DDL (read-only)
gcloud spanner databases execute-sql {database_id} \
  --project={project_id} \
  --instance={instance_id} \
  --sql="SELECT COUNT(*) AS invalid_rows FROM {table_name} WHERE {column_name} IS NULL"

# 2. Submit DDL batch ONLY after explicit user confirmation
gcloud spanner databases ddl update {database_id} \
  --project={project_id} \
  --instance={instance_id} \
  --ddl-file={schema_file}.sql \
  --async

# 3. Monitor per-statement progress (progress[] aligns 1:1 with statements[])
gcloud spanner operations list \
  --project={project_id} \
  --instance={instance_id} \
  --database={database_id} \
  --type=DATABASE_UPDATE_DDL

gcloud spanner operations describe {operation_id} \
  --project={project_id} \
  --instance={instance_id} \
  --database={database_id}
```

## Production Schema Pre-Flight Checklist

Before applying DDL to staging or production, verify all five gates:
1. **PK & Split Safety:** Confirm no high-write table or global index leads with a monotonic column (`TIMESTAMP`, commit timestamp, sequential integer) and no table relies on an implicit `rowid` PK if user-managed keys are expected later.
2. **Relationship & Cascade Integrity:** Confirm no parent-child relationship duplicates `INTERLEAVE IN PARENT` with an enforced `FOREIGN KEY`, and no same-table edge combines `INTERLEAVE IN PARENT T ON DELETE CASCADE` with a self-referencing cascading `FOREIGN KEY` to `T`.
3. **Sparse & Unique Index Audit:** Confirm every `UNIQUE NULL_FILTERED` index has `NOT NULL` keys (or omits `NULL_FILTERED`), and every query targeting a `NULL_FILTERED`/`WHERE ... IS NOT NULL` index repeats the `IS NOT NULL` predicate.
4. **View & Batch Hygiene:** Confirm every view declares `SQL SECURITY` + explicit `CAST`s, schedule an idempotent grant script after any `CREATE OR REPLACE VIEW`, keep `CREATE TABLE` + `CREATE INDEX` contiguous, and cap backfill/validation statements at `<= 10` per batch.
5. **Lifecycle & Edition Readiness:** Confirm Enterprise/Enterprise Plus edition before creating Locality Groups or running `compact_all()`, pre-clean large TTL backlogs with Partitioned DML, and add TTL re-creation to backup restore runbooks.

## Sibling Skill Routing

| Out-of-Scope Request | Route To |
| :--- | :--- |
| Standard/Enterprise/Enterprise Plus editions, CUDs, regional/DR topologies, `gcloud`/SDKs/PGAdapter/Emulator, KV APIs, Spanner Queues, autoscaler deployment, PITR, backups | `spanner-enterprise-platform-ops` |
| `CREATE PROPERTY GRAPH`, graph invariants, edge folding/dual-projection, view-backed/schemaless `DYNAMIC LABEL` graphs, ISO GQL, Data Boost Graph Algorithms | `spanner-graph-modeling-and-gql` |
| Vertex AI `CREATE MODEL`, `ML.PREDICT`, Full-Text Search `TOKENLIST`/`CREATE SEARCH INDEX`, Exact KNN, ScaNN `CREATE VECTOR INDEX`, Hybrid RRF/RSF, GraphRAG | `spanner-search-ai-and-graphrag` |
| 7-stage migrations, SMT, Datastream+Dataflow CDC, Neo4j APOC export & openCypher-to-GQL translation, Elasticsearch-to-Spanner FTS migration | `spanner-migration-and-neo4j-playbook` |
| Fine-Grained Access Control `CREATE ROLE`/`GRANT`/`REVOKE`, conditional `databaseRoleUser` IAM, IAM Deny CEL, MCP Toolbox `tools.yaml`, agent `secure_context` injection, Model Armor | `spanner-mcp-toolbox-and-agent-security` |
| Query Optimizer versions, `ANALYZE`, execution plan profiling, `SPANNER_SYS` Insights, Key Visualizer, `splits add`, CPU target tuning (`65%`/`45%`), node sizing & TCO modeling | `spanner-performance-observability-and-sizing` |

## Official Documentation

- [Schema and Data Model](https://docs.cloud.google.com/spanner/docs/schema-and-data-model)
- [Schema Design Best Practices](https://docs.cloud.google.com/spanner/docs/schema-design)
- [Secondary Indexes](https://docs.cloud.google.com/spanner/docs/secondary-indexes) & [Foreign Keys Overview](https://docs.cloud.google.com/spanner/docs/foreign-keys/overview)
- [Views & Parameterized Views](https://docs.cloud.google.com/spanner/docs/views) | [INFORMATION_SCHEMA](https://docs.cloud.google.com/spanner/docs/information-schema)
- [Locality Groups & Tiered Storage](https://docs.cloud.google.com/spanner/docs/create-manage-locality-groups) | [TTL](https://docs.cloud.google.com/spanner/docs/ttl) | [Change Streams](https://docs.cloud.google.com/spanner/docs/change-streams/manage)
- [Cloud Spanner Best-Practice Samples](https://github.com/GoogleCloudPlatform/spanner-bb-samples)
