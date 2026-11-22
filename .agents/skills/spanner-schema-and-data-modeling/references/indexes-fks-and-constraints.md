# Secondary Indexes, Foreign Keys, CHECK Constraints & Generated Columns

Production reference for Cloud Spanner secondary indexes (global, interleaved, covering, sparse, unique, expression, proto), enforced vs. `NOT ENFORCED` foreign keys, same-table dual-cascade guardrails, `CHECK` constraints, and `STORED` vs. virtual generated columns across GoogleSQL and PostgreSQL.

## 1. Secondary Indexes: Mechanics & Hard Limits

- **Reserved Pseudo-Index:** Every table's PK is automatically indexed under the reserved name **`PRIMARY_KEY`** (cannot be reused as a secondary index name).
- **Physical Storage Layout:** Every secondary index stores: (1) all base table PK columns, (2) all indexed key columns, and (3) all `STORING` (GoogleSQL) / `INCLUDE` (PostgreSQL) columns.
- **Hard Limits:** Maximum **16 key columns** and **`8 KiB` aggregate key size**. `ARRAY` is disallowed in index keys; PostgreSQL additionally disallows `NUMERIC`, `FLOAT4`, `JSONB`, and `NaN` inside `FLOAT8` index keys.
- **No In-Place Rename:** Indexes cannot be renamed—you must `DROP INDEX` and recreate them.

### Global vs. Interleaved Secondary Indexes

| Dimension | Interleaved Index (`INTERLEAVE IN {Parent}`) | Global Index (Default) |
| :--- | :--- | :--- |
| **Physical Layout** | Colocated inside the parent row's split (row tree) | Stored as an independent root table split separately from the base table |
| **Key Prefix Requirement** | Parent PK must be a leading prefix of index keys matching **type, sort order, and nullability** (**column names do NOT need to match**) | None |
| **Allowed Parent** | Ancestor of base table (**preferred**) **OR a non-ancestor table** (verified live on Enterprise: e.g., covering reverse index on `OrderItems(SupplierId, ...)` or an edge table `INTERLEAVE IN Suppliers` / `INTERLEAVE IN TargetTable`) | N/A |
| **Write Overhead** | **Low** when interleaved in an ancestor (single-split local write, avoids 2PC); **higher** when interleaved in a non-ancestor (2PC) | **Higher** (separate split from base table -> distributed 2PC) |
| **Primary Use Case** | Single-entity scoped queries (`WHERE CustomerId = @id AND ...`); **Schema Design Best Practice #3** to avoid hotspots on monotonic indexed columns | Cross-database lookups (`WHERE Email = @email`) |

```sql
-- GoogleSQL: Interleaved covering index (DESC applies ONLY to OrderTime)
CREATE INDEX IdxOrdersByCustomerRecent
  ON Orders(CustomerId, OrderTime DESC)
  STORING (TotalAmount, Status),
  INTERLEAVE IN Customers;
```

```sql
-- PostgreSQL: Interleaved covering index (NO comma before INTERLEAVE IN!)
CREATE INDEX idx_orders_by_customer_recent
  ON orders(customer_id, order_time DESC)
  INCLUDE (total_amount, status)
  INTERLEAVE IN customers;
```

### Covering Indexes (`STORING` / `INCLUDE`) & Online `ALTER INDEX`

- Covering columns eliminate base-table back-joins for SQL and are **mandatory for the Read API** (which cannot join an index back to its base table) whenever returning non-PK columns.
- You cannot add/remove index *key* columns, but you can add/drop stored columns online:
  - **GoogleSQL:** `ALTER INDEX {index_name} ADD STORED COLUMN {col};` / `DROP STORED COLUMN {col};`
  - **PostgreSQL:** `ALTER INDEX {index_name} ADD INCLUDE COLUMN {col};` / `DROP INCLUDE COLUMN {col};`
  - Adding a stored column triggers a low-priority **background backfill**; the column remains unreadable via the index until complete.

### `NULL` Ordering, Sparse Indexes (`NULL_FILTERED` / `WHERE`), & the `UNIQUE NULL_FILTERED` Trap

- **Default `NULL` Behavior:** `NULL`s are indexed and sort as the **smallest value** (`ASC`: `NULL` first; `DESC`: `NULL` last).
- **Sparse (NULL-Filtered) Indexes:**
  - **GoogleSQL (two forms):**
    1. All-keys filter: `CREATE NULL_FILTERED INDEX IdxContacts ON Contacts(FirstName, LastName);` — excludes any row where **any** key column is `NULL`.
    2. Per-column `WHERE` filter: `CREATE INDEX IdxContactsLastName ON Contacts(FirstName, LastName) WHERE LastName IS NOT NULL;` — excludes rows where listed columns are `NULL`. Every column referenced in `WHERE ... IS NOT NULL` must be in the index (key or `STORING` column).
  - **PostgreSQL:** `CREATE INDEX idx_contacts ON contacts(first_name, last_name) WHERE first_name IS NOT NULL AND last_name IS NOT NULL;`
  - **Query Requirement:** Queries **cannot** use a sparse index unless the `WHERE` clause proves filtered columns are non-null (e.g., `WHERE FirstName = @fn AND LastName IS NOT NULL`). Using `FORCE_INDEX` without the matching `IS NOT NULL` predicate throws an **error**.
- **CRITICAL TRAP — `UNIQUE NULL_FILTERED`:** A `UNIQUE NULL_FILTERED` index **does NOT enforce uniqueness when ANY key column is `NULL`**! Because rows with any `NULL` key part are omitted from the index, duplicate rows such as `(1, NULL, 1)` and `(1, NULL, 1)` commit without error. To enforce strict tuple uniqueness, declare key columns `NOT NULL` or omit `NULL_FILTERED`.

### Expression Indexes, Proto Field Indexing, Reverse Scans & Hints

- **Expression Indexes (Double Parentheses Required):** Index scalar expressions directly without declaring a separate generated column:

```sql
-- GoogleSQL
CREATE INDEX IdxVenuesByLocation ON Venues(
  VenueName,
  (JSON_VALUE(VenueData.address.city)),
  (JSON_VALUE(VenueData.address.country))
);
```

```sql
-- PostgreSQL
CREATE INDEX idx_venues_by_location ON venues(
  venue_name,
  (venue_data -> 'address' ->> 'city'),
  (venue_data -> 'address' ->> 'country')
);
```

- **Proto Field Indexing (GoogleSQL):** Add a `STORED` generated column extracting a primitive or `ENUM` field (`Region STRING(64) AS (PayloadProto.region) STORED`) and index it. **Restrictions:** Cannot index `repeated` proto fields; once indexed, that field **cannot be modified or removed** from the proto schema; use `FORCE_INDEX` to guarantee index usage.
- **Reverse-Order Scan Gotcha:** Even on a primary key (`OrderId ASC`), `SELECT OrderId FROM Orders ORDER BY OrderId DESC LIMIT 1` performs a **full table scan** unless a `DESC` index exists (`CREATE INDEX IdxOrderIdDesc ON Orders(OrderId DESC)`).
- **Index Directives (`FORCE_INDEX`) & Read API:**
  - GoogleSQL: `FROM Orders@{FORCE_INDEX=IdxOrdersByCustomerRecent}`, base table `@{FORCE_INDEX=_BASE_TABLE}`, named schema `FROM sch1.Orders@{FORCE_INDEX="sch1.IdxOrders"}`.
  - PostgreSQL: `FROM orders /*@ FORCE_INDEX = idx_orders_by_customer_recent */`, base table `/*@ FORCE_INDEX = _BASE_TABLE */`, named schema `FROM sch1.orders /*@ FORCE_INDEX = idx_orders */` (**do NOT schema-qualify the index name in PG hints**).
  - Run `ANALYZE` after major schema/data shifts, and use `FORCE_INDEX` on latency-critical OLTP queries for plan stability.
  - **Read API:** Never auto-selects indexes—specify the index explicitly; on `DESC` indexes, key ranges require `start_key > end_key`.
- **Index State Lifecycle (`INFORMATION_SCHEMA.INDEXES.INDEX_STATE`):** `PREPARE` -> `WRITE_ONLY` -> `WRITE_ONLY_CLEANUP` -> `WRITE_ONLY_VALIDATE_UNIQUE` -> `READ_WRITE` (`NULL` for `PRIMARY_KEY` in PG).
- **Batch Cancel Nuance:** Canceling a multi-index DDL LRO halts pending indexes only—**already-completed indexes are NOT automatically dropped**.
- **Execution Plan Operator Cheat Sheet:** Table/Index -> `Table Scan` / `Index Scan`; `WHERE` -> `Filter Scan` / `Filter`; `JOIN` -> `Apply join`, `Hash join`, `Merge join`, `Push broadcast hash join`; Distributed -> `Distributed union`, `Distributed apply`, `Distributed merge union`, `Local split union`; `ORDER BY`/`LIMIT` -> `Sort`, `Limit`, `Sort Limit`; `GROUP BY` -> `Aggregate`; scalar expressions -> `Compute`; DML -> `Apply mutations`.

## 2. Foreign Keys (Enforced vs. `NOT ENFORCED`)

| Feature | Enforced Foreign Key (`ENFORCED`, Default) | Informational Foreign Key (`NOT ENFORCED`) |
| :--- | :--- | :--- |
| **Dialect Support** | GoogleSQL & PostgreSQL | **GoogleSQL ONLY** |
| **Referential Validation** | Enforced on writes + backfilled/validated on existing data | **Not validated** (application guarantees consistency) |
| **Managed Backing Indexes** | Up to **2** (referenced `UNIQUE NULL_FILTERED` if not PK + referencing `NULL_FILTERED`) | Up to **1** (referenced `UNIQUE NULL_FILTERED` if not PK; **0** when referencing PK; never on referencing side) |
| **`ON DELETE` Actions** | `NO ACTION` (default), `CASCADE`, `SET NULL` | **Unsupported** |
| **Query Optimizer Use** | Yes | Yes (**ENABLED by default!**) — stale/inconsistent data can produce **wrong query results**! |
| **Best Fit** | Default choice for strict referential integrity | Eventual-consistency pipelines or high-fan-out referenced rows where validation/cascade hurts throughput |

### Disabling Optimizer Reliance on `NOT ENFORCED` Foreign Keys

```sql
-- Database-wide (re-enable with true or null)
ALTER DATABASE {database_id}
  SET OPTIONS (use_unenforced_foreign_key_for_query_optimization = false);

-- Per-query directive
@{use_unenforced_foreign_key=false}
SELECT o.OrderId, c.Email
FROM Orders AS o
JOIN Customers AS c ON o.CustomerId = c.CustomerId;
```

### Rules, Backing Indexes & Zero-Rebackfill Modification

- **Column Rules:** Referencing and referenced column lists must match in count, order, and indexable types. Disallowed types: `allow_commit_timestamp=true`, `ARRAY`, `JSON`/`JSONB`, and PG `NUMERIC`/`FLOAT4`. Referenced columns must be unique (reuses PK or creates a `UNIQUE NULL_FILTERED` backing index, failing if duplicates exist).
- **Complex Graphs:** Multi-column FKs, **self-referencing** FKs (e.g., `Employees.ManagerId -> Employees.EmployeeId`), and **circular references** (`CREATE TABLE A` -> `CREATE TABLE B` with FK to `A` -> `ALTER TABLE A ADD CONSTRAINT ...` to `B`) are supported.
- **Managed Backing Indexes (`SPANNER_IS_MANAGED`):**
  - FKs **never reuse user-created secondary indexes**; they create managed backing indexes (which SQL queries and `FORCE_INDEX` *can* use).
  - Shared across multiple FKs with identical keys, automatically dropped when the last dependent FK is dropped, and **cannot be manually altered or dropped**.
  - **Invisible in Cloud Console UI**—inspect via `INFORMATION_SCHEMA.INDEXES WHERE SPANNER_IS_MANAGED = TRUE` (GoogleSQL) or `WHERE spanner_is_managed = 'YES'` (PG).
- **Modifying FK Action or Enforcement Without Expensive Re-Backfill:** Direct `ALTER CONSTRAINT` is unsupported. Always:
  1. `ALTER TABLE {table_name} ADD CONSTRAINT {new_fk} ...` **FIRST** (reuses existing identical backing indexes!).
  2. `ALTER TABLE {table_name} DROP CONSTRAINT {old_fk}` **SECOND** (dropping first deletes backing indexes and forces full re-backfill!).
  - Track progress via `INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS.SPANNER_STATE` (`BACKFILLING_INDEXES` -> `VALIDATING_DATA` -> `WAITING_FOR_COMMIT` -> `COMMITTED`). New tables created with an FK are **unwritable** until `TABLES.SPANNER_STATE = 'COMMITTED'`.

### `ON DELETE CASCADE` vs. `ON DELETE SET NULL` Cost Matrix

`UPDATE_RULE` is always `NO ACTION`. `ON DELETE SET NULL` requires referencing columns to be **nullable**, **non-PK**, and **non-generated**.

| Cost Dimension | `ON DELETE CASCADE` | `ON DELETE SET NULL` |
| :--- | :--- | :--- |
| **Base Table Mutations** | **1 mutation/row**, or **1 mutation/key-range** if referencing columns form a prefix of the referencing table's PK | **Multiple mutations/row** (row key + each column nulled) |
| **Secondary Index Mutations** | 1 delete on FK backing index + deletes on **every** secondary index of referencing table | 1 delete on FK backing index + updates **only** indexes containing referencing columns |
| **Reads** | Can **skip backing index read** and range-delete directly when referencing cols are a PK prefix | **Always reads** FK backing index to find referencing rows |
| **Downstream Cascade** | Cascades to interleaved children (`ON DELETE CASCADE`) and downstream FKs | **Never** cascades to interleaved children (PK cols cannot be `NULL`); only triggers FKs on the nulled column |
| **`80,000` Mutation Limit** | Exceeding `80,000` mutations/commit fails the txn—batch-delete children first or use **`INTERLEAVE IN PARENT ... ON DELETE CASCADE`** for million-row deletes | Batch-update referencing rows to `NULL` or delete them before parent deletion |

### Enforced Foreign Keys vs. Table Interleaving (And the Dual-Cascade Ban)

| Property | `INTERLEAVE IN PARENT` | Enforced `FOREIGN KEY` |
| :--- | :--- | :--- |
| Non-PK columns | No (PK prefix only) | Yes |
| Max parents per table | 0..1 | 0..N |
| Physical row colocation | **Yes** | **No** |
| Null matching | `NOT DISTINCT` (`NULL` matches `NULL`) | `SIMPLE` (PG `NONE`): passes if **any** referencing col is `NULL` |
| Enforcement timing | Mutation API: per-op; DML: per-statement | Mutation API: **deferred to commit** (child before parent OK); DML: per-statement |
| Million-row cascade deletes | Supported (`ON DELETE CASCADE`) | Fails `80,000` mutation cap |
| Removable later? | **No** (permanent) | **Yes** (`DROP CONSTRAINT`) |

> **NON-NEGOTIABLE GUARDRAIL — Never Combine Both, & Same-Table Dual-Cascade Ban:**
> 1. Never model the same parent-child edge as **both** `INTERLEAVE IN PARENT` and an enforced `FOREIGN KEY` (wastes storage, CPU, and backing index writes).
> 2. While a table may combine `INTERLEAVE IN PARENT TableA ON DELETE CASCADE` with `FOREIGN KEY (...) REFERENCES TableB (...) ON DELETE CASCADE` for **distinct** tables `TableA` and `TableB`, Cloud Spanner **forbids** combining `INTERLEAVE IN PARENT T ON DELETE CASCADE` with a second (self-referencing) `FOREIGN KEY (...) REFERENCES T (...) ON DELETE CASCADE` targeting the **same** table `T`.

```sql
-- OPTION A (Strict Bidirectional Cascade on Same-Table Edge T -> T):
-- Keep edge table STANDALONE (no INTERLEAVE IN PARENT) with 2 enforced cascading FKs.
CREATE TABLE AccountTransfers (
  FromAccountId UUID NOT NULL,
  ToAccountId   UUID NOT NULL,
  TransferId    UUID NOT NULL DEFAULT (NEW_UUID()),
  Amount        NUMERIC NOT NULL,
  CONSTRAINT fk_transfer_from FOREIGN KEY (FromAccountId)
    REFERENCES Accounts(AccountId) ON DELETE CASCADE,
  CONSTRAINT fk_transfer_to FOREIGN KEY (ToAccountId)
    REFERENCES Accounts(AccountId) ON DELETE CASCADE,
) PRIMARY KEY (FromAccountId, ToAccountId, TransferId);

-- OPTION B (Max Forward Locality on Same-Table Edge T -> T):
-- Interleave under source T with ON DELETE CASCADE + NOT ENFORCED (or NO ACTION) dest FK.
CREATE TABLE AccountFollows (
  AccountId       UUID NOT NULL,
  TargetAccountId UUID NOT NULL,
  CreatedAt       TIMESTAMP NOT NULL,
  CONSTRAINT fk_follow_target FOREIGN KEY (TargetAccountId)
    REFERENCES Accounts(AccountId) NOT ENFORCED,
) PRIMARY KEY (AccountId, TargetAccountId),
  INTERLEAVE IN PARENT Accounts ON DELETE CASCADE;
```

## 3. `CHECK` Constraints

```sql
-- GoogleSQL & PostgreSQL inline or post-creation
CREATE TABLE Reservations (
  ReservationId UUID NOT NULL,
  StartTime     TIMESTAMP NOT NULL,
  EndTime       TIMESTAMP NOT NULL,
  SeatCount     INT64 NOT NULL,
  CONSTRAINT ck_time_order CHECK (StartTime < EndTime),
) PRIMARY KEY (ReservationId);

ALTER TABLE Reservations ADD CONSTRAINT ck_seats_positive CHECK (SeatCount > 0);
ALTER TABLE Reservations DROP CONSTRAINT ck_seats_positive;
```

- **Semantics & Naming:** Auto-generated names use prefix **`CK_`** (schema-scoped, unique across tables/indexes/constraints). Evaluated on `INSERT` and on `UPDATE` of referenced columns; **`TRUE` or `NULL` succeeds**, **`FALSE` fails**.
- **All 5 Restrictions:** (1) Reference columns in the **same table only**; (2) reference **at least one non-generated column** (directly or indirectly); (3) **forbid** `allow_commit_timestamp=true` columns; (4) **no subqueries**; (5) **no non-deterministic functions** (`CURRENT_DATE()`, `CURRENT_TIMESTAMP()`).
- **Online Validation & Immutability:** `ALTER TABLE ADD CONSTRAINT` enforces immediately on new writes and launches a validation LRO (`INFORMATION_SCHEMA.CHECK_CONSTRAINTS.SPANNER_STATE` = `VALIDATING` / `VALIDATING_DATA` -> `COMMITTED`), rolling back on any existing violation. Expressions cannot be altered in place—drop and re-add.

## 4. Generated Columns (`STORED` vs. Virtual)

```sql
-- GoogleSQL
CREATE TABLE Users (
  UserId     STRING(36) NOT NULL,
  FirstName  STRING(50),
  LastName   STRING(50),
  Age        INT64 NOT NULL,
  FullName   STRING(101) AS (FirstName || ' ' || LastName) STORED, -- computed on write
  AdultAge   INT64 AS (IF(Age >= 18, Age, NULL))                   -- virtual (computed on read)
) PRIMARY KEY (UserId);

-- Partial index pattern: virtual generated column + NULL_FILTERED index (zero duplicate storage!)
CREATE NULL_FILTERED INDEX IdxAdultUsers ON Users(AdultAge);
```

```sql
-- PostgreSQL
CREATE TABLE users (
  user_id    VARCHAR(36) NOT NULL,
  first_name VARCHAR(50),
  last_name  VARCHAR(50),
  age        BIGINT NOT NULL,
  full_name  VARCHAR(101) GENERATED ALWAYS AS (first_name || ' ' || last_name) STORED,
  adult_age  BIGINT GENERATED ALWAYS AS (NULLIF(age, LEAST(17, age))) VIRTUAL,
  PRIMARY KEY (user_id)
);

CREATE INDEX idx_adult_users ON users(adult_age) WHERE adult_age IS NOT NULL;
```

- **Partial Index Advantage:** Pairing a **virtual** generated column with a `NULL_FILTERED` / `WHERE col IS NOT NULL` index avoids storing values twice AND avoids runtime evaluation during index scans—and enables the **Read API** to read the virtual column through that index (otherwise virtual columns are SQL-only).
- **Generated Primary Keys & Optimizer Inference:** Generated PKs must be **`STORED`** (ideal for `ShardId` or JSON PK extraction: `DocId INT64 NOT NULL AS (INT64(Payload.id)) STORED` / PG `((payload ->> 'id')::BIGINT) STORED NOT NULL`). When a generated PK depends on **a single column** constrained by `=`, `IN (...)`, or an equi-join, Spanner automatically infers the generated PK predicate (e.g., `WHERE UserId = @u` rewrites to `WHERE ShardId = MOD(@u, 64) AND UserId = @u`).
- **Exhaustive Restrictions:**
  1. Scalar, **immutable functions only**, same table only, no subqueries, no direct writes.
  2. **Virtual (non-stored) columns:** `STRING`/`BYTES` (GoogleSQL) and `VARCHAR` (PG) **must be length `MAX`** and **cannot be `NOT NULL`**. **CRITICAL `TOKENIZE_JSON` Rule:** Generated `TOKENLIST` columns derived from `TOKENIZE_JSON(json_col)` **MUST be virtual `HIDDEN`** (`TOKENLIST AS (TOKENIZE_JSON(metadata)) HIDDEN`) — **NEVER `STORED HIDDEN`** (Cloud Spanner rejects DDL with `JSON derived TOKENLIST column ... may not be STORED`).
  3. **Immutability:** Cannot alter the expression of a `STORED` or indexed generated column (only unindexed virtual columns can be modified); cannot alter the data type of a `STORED`/indexed generated column or any column it references; cannot drop referenced columns.
  4. **`allow_commit_timestamp` forbidden** on generated columns AND on any column they reference.
  5. **Generated PK Extra Rules:** Cannot reference other generated columns; can reference **at most 1 non-key column**; cannot reference a non-key column with a `DEFAULT` clause.
- **API Behavior for Generated Key Columns:**
  - **Read API:** **Must** specify all key columns including generated keys.
  - **Mutation API:** `INSERT` / `INSERT_OR_UPDATE` / `REPLACE` **forbid** specifying generated keys; `UPDATE` is optional; `DELETE` **must** specify all keys including generated keys.
  - **DML:** Never write generated keys explicitly in `INSERT`/`UPDATE`.
  - **Backfill State:** Adding a `STORED` generated column to an existing table runs an LRO (`INFORMATION_SCHEMA.COLUMNS.SPANNER_STATE`: `NO_WRITE` [PG] -> `WRITE_ONLY` -> `COMMITTED`) and adds write latency whenever referenced columns change.

## Official Documentation

- [Secondary Indexes](https://docs.cloud.google.com/spanner/docs/secondary-indexes) | [Optimizing Schema Design](https://docs.cloud.google.com/spanner/docs/whitepapers/optimizing-schema-design)
- [Foreign Keys Overview](https://docs.cloud.google.com/spanner/docs/foreign-keys/overview) | [Creating & Managing Foreign Keys](https://docs.cloud.google.com/spanner/docs/foreign-keys/how-to)
- [Check Constraints](https://docs.cloud.google.com/spanner/docs/check-constraint/how-to) | [Generated Columns](https://docs.cloud.google.com/spanner/docs/generated-column/how-to)
- [Query Execution Operators](https://docs.cloud.google.com/spanner/docs/query-execution-operators)
