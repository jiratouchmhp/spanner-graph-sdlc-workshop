# Data Model, Table Interleaving, Primary Keys & Data Types

Production reference for Cloud Spanner physical storage mechanics, table interleaving, split-safe primary key design, PK migrations, GoogleSQL vs. PostgreSQL data types, commit timestamps, bulk loading, and multi-tenancy patterns.

## 1. Physical Storage Architecture, Splits & Locality

- **Lexicographic Key Ordering:** Every table's rows are physically sorted lexicographically by the concatenation of `PRIMARY KEY` columns in declaration order. Adjacent keys live in the same disk blocks and share block cache locality.
- **Replication & Splits:** Each zone in a regional instance (for example, `regional-asia-southeast3` across `asia-southeast3-a`, `asia-southeast3-b`, and `asia-southeast3-c`, or `regional-asia-southeast1` across `asia-southeast1-a`, `asia-southeast1-b`, and `asia-southeast1-c`) stores a complete replica partitioned across compute nodes in that zone.
  - Physical hierarchy: **Database Splits** (contiguous key ranges bounded by *split boundaries*; **split size limit = `8 GiB`**) -> **Blocks**.
  - Splits dynamically split, merge, and move across servers based on **size** and **load** (*load-based splitting*).
  - **Cross-Split 2PC Penalty:** Transactions touching multiple splits automatically execute **two-phase commit (2PC)**, adding CPU and latency—even if both splits are currently served by the same node.
  - **Load-Based Splitting Ramp:** When a table develops hot rows (for example, 10 hot keys), Spanner inserts split boundaries between them within minutes to isolate each onto its own server. Throughput on well-distributed keys roughly doubles every few minutes until reaching CPU saturation or an indivisible split (a single row without interleaved children).
- **Storage Capacity per Compute Unit:** **`10 TiB` per node** (`1,024 GiB` per `100` processing units).

## 2. Table Hierarchies & Interleaving (`INTERLEAVE IN PARENT` vs `INTERLEAVE IN`)

- **Row Trees & Split Boundaries:** Root (top-level) tables define *root rows*. A parent row plus all interleaved descendants form a **row tree**. Spanner prefers placing split boundaries at root rows to keep an entire row tree colocated in one split; if a row tree exceeds `8 GiB` or a child row becomes hot, Spanner splits *inside* the child table to isolate that hot child row and its descendants.
- **Hierarchy Depth & Permanence:** Supports up to **7 layers deep** (keep practical depth minimal to limit overhead). Interleaving is **permanent**—you cannot un-interleave an existing table without creating a new table and migrating data.
- **Strict Prefix Rules:** Every table in an interleaved hierarchy must define a PK. A child's composite PK must begin with **all parent PK columns as a leading prefix**, matching exact **order**, **data types**, **sort order**, **nullability** (if the parent omits `NOT NULL`, the child cannot add `NOT NULL`), and **`allow_commit_timestamp`** settings.

| Mode | DDL Clause | Referential Integrity | Parent Must Exist Before Child Insert? | `ON DELETE` Actions | Primary Use Cases |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Enforced Parent-Child** | `INTERLEAVE IN PARENT {parent_table} [ON DELETE CASCADE \| ON DELETE NO ACTION]` | **Enforced** (`NO ACTION` is default if omitted) | **Yes** (must exist or be inserted earlier in the same txn) | `ON DELETE CASCADE` (atomic child deletion) or `ON DELETE NO ACTION` (fails if children exist) | Tightly coupled 1:N trees accessed/deleted together (e.g., `Customers` -> `Orders` -> `OrderItems`) |
| **Physical Colocation Only** | `INTERLEAVE IN {parent_table}` | **Not enforced** (orphan children allowed; parent delete ignores children) | **No** | N/A | (1) Independent cleanup lifecycles (e.g., `Projects` deleted while `Resources` drain); (2) **Parallel bulk loads** followed by `ALTER TABLE ... SET INTERLEAVE IN PARENT` |

### Exact Interleaving DDL (GoogleSQL vs. PostgreSQL)

```sql
-- GoogleSQL: Enforced 2-level hierarchy + unenforced colocation
CREATE TABLE Customers (
  CustomerId INT64 NOT NULL,
  FullName   STRING(1024),
  ProfileBin BYTES(MAX),
) PRIMARY KEY (CustomerId);

CREATE TABLE Orders (
  CustomerId INT64 NOT NULL,
  OrderId    INT64 NOT NULL,
  OrderTitle STRING(MAX),
) PRIMARY KEY (CustomerId, OrderId),
  INTERLEAVE IN PARENT Customers ON DELETE CASCADE;

CREATE TABLE Resources (
  ProjectId    INT64 NOT NULL,
  ResourceId   INT64 NOT NULL,
  ResourceName STRING(1024),
) PRIMARY KEY (ProjectId, ResourceId),
  INTERLEAVE IN Projects;
```

```sql
-- PostgreSQL: PRIMARY KEY inside parens; INTERLEAVE after closing paren (NO comma!)
CREATE TABLE customers (
  customer_id BIGINT PRIMARY KEY,
  full_name   VARCHAR(1024),
  profile_bin BYTEA
);

CREATE TABLE orders (
  customer_id BIGINT,
  order_id    BIGINT,
  order_title VARCHAR,
  PRIMARY KEY (customer_id, order_id)
) INTERLEAVE IN PARENT customers ON DELETE CASCADE;

CREATE TABLE resources (
  project_id    BIGINT,
  resource_id   BIGINT,
  resource_name VARCHAR(1024),
  PRIMARY KEY (project_id, resource_id)
) INTERLEAVE IN projects;
```

### Bulk-Load Promotion Pattern (`INTERLEAVE IN` -> `INTERLEAVE IN PARENT`)

```sql
-- 1. Audit for orphaned child rows first (promotion fails if any exist)
SELECT ProjectId FROM Resources
EXCEPT DISTINCT
SELECT ProjectId FROM Projects;

-- 2. Promote unenforced colocation to enforced parent-child
ALTER TABLE Resources SET INTERLEAVE IN PARENT Projects;
```

> **Locality Anti-Pattern:** Never interleave large, infrequently accessed binary blobs into hot parent rows. Large blobs bloat row trees and degrade block cache efficiency—store them in a non-interleaved table or assign only the cold column to an HDD locality group (Enterprise/Enterprise Plus).

## 3. Primary Key Rules, Keyless Tables & Hotspot Prevention

- **PK Immutability:** You cannot add or remove PK columns on an existing table.
- **NULLs in Primary Keys:** **GoogleSQL** allows `NULL` in PK columns if `NOT NULL` is omitted (at most 1 `NULL` row for a single-column PK). **PostgreSQL** forbids `NULL` in PKs (`PRIMARY KEY` implies `NOT NULL`).
- **Tables Without Explicit Primary Keys (Two Distinct Behaviors):**
  1. **Explicit empty `PRIMARY KEY ()` (GoogleSQL only):** Table holds **at most 1 row**; normal update/delete operations by key are unavailable.
  2. **Omitting `PRIMARY KEY` completely (GoogleSQL & PostgreSQL):** Spanner creates an automatic **`HIDDEN`** `rowid` PK column backed by a bit-reversed positive identity sequence:
     - GoogleSQL equivalent: `rowid INT64 NOT NULL GENERATED BY DEFAULT AS IDENTITY (BIT_REVERSED_POSITIVE) HIDDEN` + `PRIMARY KEY (rowid)`
     - PostgreSQL equivalent: `rowid bigint GENERATED BY DEFAULT AS IDENTITY (BIT_REVERSED_POSITIVE) NOT NULL HIDDEN, PRIMARY KEY (rowid)`
     - **Gotchas:** (a) You **cannot** later add a user-defined PK; (b) you cannot create another column named `rowid`; (c) `rowid` is excluded from `SELECT *`, must be explicitly selected (`SELECT rowid FROM {table_name}`), and appears in `INFORMATION_SCHEMA.KEY_COLUMN_USAGE` (`constraint_name LIKE 'PK_%'`).
- **Best Practice #1 (Avoid Monotonic Leading Keys):** Never choose a monotonically increasing or decreasing column (`TIMESTAMP`, commit timestamp, sequential integer) as the **first PK column** of a high-write-rate table. All inserts funnel to a single split at the end (or top with `DESC`—**switching `ASC` to `DESC` does NOT prevent hotspots**).
- **Best Practice #2 (`DESC` for Trailing Timestamps):** Once a non-monotonic key leads the PK (e.g., `PRIMARY KEY (UserId, LastAccess DESC)`), declare the trailing timestamp `DESC` when reading the parent plus newest interleaved history (keeps recent rows physically adjacent to the parent) or running reverse-chronological `LIMIT` queries with early cancellation.

### Five Split-Safe Primary Key Strategies

#### Strategy 1: UUID Version 4 (Recommended for Greenfield & Geo-Partitioning)

- Use **UUID v4** (122 random bits; 50% collision takes 2.71 × 10^18 values). **Never use UUID v1** (high-order timestamp bits cause hotspots).
- Client-generated and Spanner-generated UUIDs interoperate cleanly if clients serialize UUIDs in **lowercase**.
- **Functions & Schema Lock-In:**
  - Native 128-bit `UUID`: GoogleSQL `NEW_UUID()` (`UUID`), PostgreSQL `gen_random_uuid()` (`uuid`).
  - Lowercase 36-char string: GoogleSQL `GENERATE_UUID()` (`STRING(36)`), PostgreSQL `spanner.generate_uuid()` (`text`).
  - **Cannot alter** an existing `STRING(36)` column to `UUID` or vice versa.

```sql
-- GoogleSQL
CREATE TABLE Accounts (
  AccountId UUID NOT NULL DEFAULT (NEW_UUID()),
  Name      STRING(MAX),
) PRIMARY KEY (AccountId);

INSERT INTO Accounts (Name) VALUES ('Operations') THEN RETURN AccountId;
```

```sql
-- PostgreSQL
CREATE TABLE accounts (
  account_id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  name       text
);

INSERT INTO accounts (name) VALUES ('Operations') RETURNING account_id;
```

#### Strategy 2: `IDENTITY`, `AUTO_INCREMENT` (GoogleSQL), & `SERIAL` (PostgreSQL)

- Generates bit-reversed positive 64-bit integers (`INT64` / `bigint`) with automatic lifecycle management (dropping the column drops its backing sequence).
- `AUTO_INCREMENT` (GoogleSQL) and `smallserial`/`serial`/`bigserial`/`serial2`/`serial4`/`serial8` (PostgreSQL) are **DDL aliases** for `IDENTITY` (DDL export renders `GENERATED BY DEFAULT AS IDENTITY`).
- **Mandatory Prerequisite for Aliases:** Set `default_sequence_kind = 'bit_reversed_positive'` at the database level first.

```sql
-- GoogleSQL: explicit IDENTITY + AUTO_INCREMENT alias + ALTER IDENTITY
CREATE TABLE Invoices (
  InvoiceId INT64 GENERATED BY DEFAULT AS IDENTITY (
    BIT_REVERSED_POSITIVE START COUNTER WITH 1000 SKIP RANGE 1, 4294967296
  ),
  Amount NUMERIC,
) PRIMARY KEY (InvoiceId);

ALTER DATABASE {database_id} SET OPTIONS (default_sequence_kind = 'bit_reversed_positive');
CREATE TABLE Tickets (
  TicketId INT64 AUTO_INCREMENT PRIMARY KEY,
  Title    STRING(MAX),
);

ALTER TABLE Invoices ALTER COLUMN InvoiceId ALTER IDENTITY SET SKIP RANGE 1, 4294967296;
ALTER TABLE Invoices ALTER COLUMN InvoiceId ALTER IDENTITY RESTART COUNTER WITH 11000;
```

```sql
-- PostgreSQL: SPACE-separated SKIP RANGE (no comma!) + SERIAL alias
CREATE TABLE invoices (
  invoice_id bigint GENERATED BY DEFAULT AS IDENTITY (
    BIT_REVERSED_POSITIVE START COUNTER WITH 1000 SKIP RANGE 1 4294967296
  ),
  amount numeric,
  PRIMARY KEY (invoice_id)
);

ALTER DATABASE {database_id} SET spanner.default_sequence_kind = 'bit_reversed_positive';
CREATE TABLE tickets (
  ticket_id serial PRIMARY KEY,
  title     text
);

ALTER TABLE invoices ALTER COLUMN invoice_id SET SKIP RANGE 1 4294967296;
ALTER TABLE invoices ALTER COLUMN invoice_id RESTART COUNTER WITH 11000;
```

#### Strategy 3: Bit-Reversed Positive `SEQUENCE` Objects

- Only supported `sequence_kind` is **`bit_reversed_positive`** (positive 64-bit ints in `[1, 2^63]`; no monotonic sequences exist in Spanner).
- **Read-Write Only:** `GET_NEXT_SEQUENCE_VALUE(SEQUENCE {seq})` (GoogleSQL) and `nextval('{seq}')` (PG) execute **only inside read-write transactions**. Inspect the pre-reversal counter for migrations via `GET_INTERNAL_SEQUENCE_STATE(SEQUENCE {seq})` / `spanner.get_internal_sequence_state('{seq}')` (returns `NULL` before first call).

```sql
-- GoogleSQL
CREATE SEQUENCE IF NOT EXISTS {seq_name} OPTIONS (
  sequence_kind = 'bit_reversed_positive',
  start_with_counter = 1000,
  skip_range_min = 100,
  skip_range_max = 200
);
ALTER SEQUENCE {seq_name} SET OPTIONS (skip_range_min = 1, skip_range_max = 21000);
CREATE TABLE Contracts (
  ContractId INT64 DEFAULT (GET_NEXT_SEQUENCE_VALUE(SEQUENCE {seq_name})),
  Title      STRING(MAX),
) PRIMARY KEY (ContractId);
```

```sql
-- PostgreSQL
CREATE SEQUENCE {seq_name} BIT_REVERSED_POSITIVE START COUNTER 1000 SKIP RANGE 100 200;
ALTER SEQUENCE {seq_name} SKIP RANGE 1 21000;
ALTER SEQUENCE {seq_name} RESTART COUNTER 11000;
CREATE TABLE contracts (
  contract_id bigint DEFAULT nextval('{seq_name}') PRIMARY KEY,
  title       text
);
```

- **Sequence FGAC Nuance:** `SELECT` on a sequence allows `GET_INTERNAL_SEQUENCE_STATE()`, `INFORMATION_SCHEMA.SEQUENCES`, and `GET_NEXT_SEQUENCE_VALUE()`; `UPDATE` on a sequence allows `GET_NEXT_SEQUENCE_VALUE()`/`nextval()` and `INFORMATION_SCHEMA.SEQUENCES`—**but `UPDATE` on a sequence does NOT imply `INSERT`/`UPDATE` privilege on the table column referencing it as `DEFAULT`**.
- **Geo-Partitioning Latency Warning:** Sequence counters live in system tables in the **default placement**. Using sequences on geo-partitioned tables adds **hundreds of milliseconds of cross-region tail latency**—use **UUID v4** instead.

#### Strategy 4: Swap Key Order

Move a high-cardinality non-monotonic key ahead of the timestamp: `PRIMARY KEY (UserId, LastAccess DESC)` instead of `(LastAccess, UserId)`.

#### Strategy 5: Hash Sharding (`ShardId`) via `STORED` Generated Column

```sql
-- GoogleSQL
CREATE TABLE AccessLogs (
  ShardId    INT64 NOT NULL AS (MOD(FARM_FINGERPRINT(CAST(EventTime AS STRING)), 64)) STORED,
  EventTime  TIMESTAMP NOT NULL,
  UserId     INT64 NOT NULL,
) PRIMARY KEY (ShardId, EventTime DESC, UserId);
```

```sql
-- PostgreSQL
CREATE TABLE access_logs (
  shard_id   bigint GENERATED ALWAYS AS (MOD(spanner.farm_fingerprint(event_time::text), '64'::bigint)) STORED NOT NULL,
  event_time timestamptz NOT NULL,
  user_id    bigint NOT NULL,
  PRIMARY KEY (shard_id, event_time DESC, user_id)
);
```

- **Sizing `N`:** Correlate `N` with expected **node count** (e.g., `N = 10` for up to 10 nodes). `N` is **static** (changing requires schema migration + backfill), and overly large `N` degrades cross-shard range reads without improving write scaling beyond physical nodes.
- **Hash Function Selection:** Use `FARM_FINGERPRINT` (`INT64`, deterministic across engines, non-cryptographic; collision risk rises above ~4B distinct inputs) for sharding. Use `SHA256` (32B `BYTES`) for cryptographic digests; avoid `SHA512` (64B, 2x storage/bandwidth) unless mandated, and never use deprecated `SHA1`.

## 4. Primary Key Migration Patterns

1. **Sequential Keys (`AUTO_INCREMENT` / `SERIAL`):** Load existing rows with legacy IDs -> query `SELECT MAX(Id) FROM {table_name}` -> set `SKIP RANGE` `[1, max + buffer]` on the Spanner sequence/`IDENTITY` -> resume writes.
2. **Zero-Downtime Dual-Write Cutover (32-bit PG `SERIAL` -> 64-bit Spanner):** Widen source PG `SERIAL` to `bigint` + add `CHECK (id <= 2147483647)` so legacy writes stay in 32-bit range; configure Spanner sequence `SKIP RANGE 1 2147483647` (or `4294967296` for unsigned 32-bit) so Spanner generates disjoint 64-bit keys.
3. **UUIDs:** Export source UUIDs as lowercase strings into `STRING(36) DEFAULT (GENERATE_UUID())` / `TEXT DEFAULT spanner.generate_uuid()`, or native `UUID` with `NEW_UUID()` / `gen_random_uuid()`.
4. **External Sequential Order Preservation:** Composite PK `(KeyHash, SequentialId)` where `KeyHash` is `INT64 AS (FARM_FINGERPRINT(CAST(SequentialId AS STRING))) STORED`.
5. **Pre-Existing Bit-Reversed Keys:** Configure `start_with_counter` / `START COUNTER` above source maximum counter + safety buffer.

## 5. Data Types, Key Eligibility & Arbitrary Precision Numerics

| Category | GoogleSQL | PostgreSQL | PK / FK / Index? | Critical Nuances & Hard Limits |
| :--- | :--- | :--- | :--- | :--- |
| 64-bit Int | `INT64` | `int8` / `bigint` / `int` | Yes (both) | 18 decimal digits, scale 0. In Spanner PG, **`int` aliases 8-byte `int8`** (not OSS PG 4-byte `int4`)! |
| 32-bit Float | `FLOAT32` | `float4` / `real` | **NO (both)** | IEEE-754 binary32 (4B). PG: `NaN` > all non-nulls, `NaN = NaN`. |
| 64-bit Float | `FLOAT64` | `float8` / `double precision` | Yes (**PG keys forbid `NaN`**) | 15–17 significant digits (binary float: `0.1 + 0.2 != 0.3`). |
| Exact Decimal | `NUMERIC` | `numeric` / `decimal` | **GoogleSQL: YES / PG: NO** | **GoogleSQL:** Fixed **precision 38, scale 9**. **PG:** Arbitrary up to **147,455** precision / **16,383** scale; `numeric(p,s)` modifiers allowed in **DQL/DML only, NOT DDL**; supports `NaN`. |
| String | `STRING(N\|MAX)` | `varchar(N)` / `text` | Yes (both) | GoogleSQL length mandatory; PG length optional, max **2,621,440 chars**. PG **`CHAR` unsupported**. |
| Binary | `BYTES(N\|MAX)` | `bytea` | Yes (both) | GoogleSQL length mandatory. |
| Boolean / Date | `BOOL` / `DATE` | `bool` / `date` | Yes (both) | `DATE`: `0001-01-01`..`9999-12-31`. PG forbids `today`/`yesterday`/`infinity`. |
| Timestamp | `TIMESTAMP` | `timestamptz` | Yes (both) | PG **`TIMESTAMP WITHOUT TIME ZONE` unsupported**; forbid `now`/`epoch`/`infinity` literals; avoid TZ abbrevs (`PST`/`IST`)—use IANA names (e.g., `Asia/Bangkok`, `Asia/Singapore`). Default PG TZ for `date_trunc` is `America/Los_Angeles`. |
| Commit TS | `TIMESTAMP OPTIONS (allow_commit_timestamp=true)` | `spanner.commit_timestamp` | PK/Index Yes; **FK NO** | Supports PG `DEFAULT` & `ON UPDATE spanner.pending_commit_timestamp()`. |
| UUID | `UUID` | `uuid` | Yes (both) | 128-bit value. Cannot `ALTER COLUMN` between `STRING(36)` and `UUID`. |
| JSON | `JSON` | `jsonb` | **NO (both)** | Index JSON fields via expression indexes or generated columns. |
| Array / Vector | `ARRAY<T>` | `T[]` / `T ARRAY` | **NO (both)** | PG: 1D only, lower bound 1, declared length (`int[3]`) unenforced. Vector length annotations (`N >= 0`): GoogleSQL `ARRAY<FLOAT32>(vector_length=>N)` (**recommended**) or `FLOAT64`; PG `float4[] VECTOR LENGTH N` (`real[]`, **recommended** for ScaNN/KNN) or `float8[]`. Empty array `!= NULL`. |
| Interval | `INTERVAL` | `interval` | **N/A (PG query-only)** | PG cannot store in table columns (expressions/views only); ISO 8601 output; bounds ±120,000 mo / ±3,660,000 d. |
| Proto / Enum / Struct | `PROTO` / `ENUM` / `STRUCT` | GoogleSQL only | `ENUM`: Yes; others **NO** | `PROTO BUNDLE` max **`5 MiB`** (recommended **`500 KiB`**). |

### Arbitrary Precision Numerics (Beyond GoogleSQL 38,9 or PG Bounds)

1. **Recommended — Decimal `STRING`/`VARCHAR`:** Store canonical plain strings (e.g., Java `BigDecimal.toPlainString()`). Exact, human-readable, portable; run exact `SUM` in application code, approximate SQL sorting/filtering via `CAST(val AS FLOAT64)` / `val::FLOAT8`.
2. **Scaled `INT64`/`INT8`:** Multiply by fixed scale (e.g., `10^5`) for direct SQL math (max 18 digits; watch overflow; rescaling requires full rewrite).
3. **Separate `(unscaled BYTES, scale INT64)`:** Store big-endian two's complement `BigInteger.toByteArray()` + scale (portable, app-side math only).
4. **Avoid App-Internal Binary Serialization:** Ties data permanently to a single language/runtime.

## 6. Commit Timestamps

- **TrueTime Semantics:** Microsecond granularity stored as nanoseconds; **not guaranteed unique** across disjoint transactions (unique across overlapping mutations).
- **DDL:** GoogleSQL `allow_commit_timestamp` is **case-sensitive**. Enabling on an existing column (`ALTER TABLE {t} ALTER COLUMN {c} SET OPTIONS (allow_commit_timestamp=true)`) triggers **background validation** verifying all existing values are in the past (cannot alter type or `NOT NULL` in the same statement). Disable with `allow_commit_timestamp=null`. Must match across interleaved PK columns.
- **Writing:** DML uses `PENDING_COMMIT_TIMESTAMP()` (GoogleSQL) / `spanner.pending_commit_timestamp()` (PG); Mutation API uses `'spanner.commit_timestamp()'` or client SDK constants. Custom timestamps must be **strictly in the past** (`FailedPrecondition` otherwise); **`CURRENT_TIMESTAMP()` is NOT TrueTime-based** and cannot safely be compared against commit timestamps.
- **CRITICAL TRANSACTION GOTCHA:** Once a statement writes `PENDING_COMMIT_TIMESTAMP()`, **the table and all derived secondary indexes become unreadable to subsequent SQL statements in the same transaction** (and Change Streams cannot extract old values on second writes in the same txn). **Always execute commit-timestamp writes as the final statement of the transaction.**
- **Hotspots & Recent-Data Pruning:** Leading PK or leading non-interleaved index column on a commit timestamp hotspots even at low write rates (unindexed non-key columns add zero write overhead). Predicates `WHERE CommitTs > const_expr` or `>= const_expr` (literal, `@param`, or constant-arg expression like `TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)`) get storage-level I/O pruning when combined with `AND` (**`OR` disqualifies pruning**).

## 7. Bulk Loading Best Practices

1. **Key-Range Partitioning:** Sort input by PK, split into **`10 x (node count)`** contiguous key-range partitions, assign 1 worker per partition, and keep every commit inside one partition to minimize cross-split 2PC. Achieves **`10–20 MB/s per node`** as load-based splitting ramps throughput.
2. **Commit Sizing & Hard Limits:** Target **`1 MB – 5 MB`** per commit for large rows, or **a few hundred rows** for small/indexed rows. Hard limits: **`80,000` mutations/commit** and **`100 MiB` commit size**.
3. **Pushback Avoidance:** Overloading triggers aborted-transaction pushback (1+ minute write-only retry latency). Throttle below regional **65% high-priority CPU** (or scale nodes temporarily).
4. **Interleaved & Indexed Load Sequence:** Define `INTERLEAVE IN` -> parallel load parents & children -> `ALTER TABLE {child} SET INTERLEAVE IN PARENT {parent}`. Create business-critical indexes in the initial `CREATE TABLE` batch; defer non-critical indexes until post-load (subject to 10/batch & 3/day backfill rules).

## 8. Multi-Tenancy Architecture Patterns

| Dimension | Instance Pattern (1/instance) | Database Pattern (1/DB) | Table Pattern (Prefix / Named Schema) | Row Pattern (`TenantId` Leading PK) |
| :--- | :--- | :--- | :--- | :--- |
| **Isolation** | Complete (physical + project/IAM/VPC) | High (per-DB IAM & physical separation) | Moderate (logical; shared storage files; FGAC per schema/table) | Low (shared tables; app predicate or Parameterized View + FGAC) |
| **Scale Limits** | Unlimited instances & nodes | **100 DBs/instance** (**10 DBs/100 PU** on granular instances) | **5,000 tables/DB** (`500,000` tenants/instance at 1 table/tenant) | Unlimited tenants (highest SaaS density) |
| **Min Compute / Tenant** | **100 PU** granular / 1 node standard (`$94.28/mo` at 100 PU Enterprise in `asia-southeast3`) | **1 node per 100 DBs** (10 DB / 100 PU cap) | 1 node per 500,000 tables | **No minimum** |
| **Per-Tenant Backup / CMEK** | Native | Native | **Unsupported** (shared CMEK; custom export) | **Unsupported** (custom export) |
| **Onboarding Agility** | Low (IaC) | Moderate (IaC DB creation) | Moderate (**DDL throttling risk** on frequent onboarding!) | Highest (zero DDL; row delete to offboard) |
| **Data Residency (e.g., PDPA)** | Regional instance (`regional-asia-southeast3`) | Regional instance or Geo-partitioning | Regional instance or Geo-partitioning | Regional instance or Geo-partitioning |

- **Hybrid Tiers & Attribution:** Separate different multi-tenancy patterns into **separate databases**. Tag queries with SQL comments (`-- {"TENANT": 356}`) or Spanner request/transaction tags for `SPANNER_SYS` cost attribution. Always build operational hooks for *Stop Tenant*, *Start Tenant*, *Export before Delete*, and dual-admin *Emergency Shutdown*.

## Official Documentation

- [Schema and Data Model](https://docs.cloud.google.com/spanner/docs/schema-and-data-model) | [Schema Design Best Practices](https://docs.cloud.google.com/spanner/docs/schema-design)
- [Primary Key Default Values](https://docs.cloud.google.com/spanner/docs/primary-key-default-value) | [Migrating Primary Keys](https://docs.cloud.google.com/spanner/docs/migrating-primary-keys)
- [Commit Timestamps](https://docs.cloud.google.com/spanner/docs/commit-timestamp) | [Storing Arbitrary Precision Numerics](https://docs.cloud.google.com/spanner/docs/storing-numeric-data)
- [Bulk Loading Best Practices](https://docs.cloud.google.com/spanner/docs/bulk-loading) | [Implementing Multi-Tenancy](https://docs.cloud.google.com/spanner/docs/implement-multi-tenancy)
