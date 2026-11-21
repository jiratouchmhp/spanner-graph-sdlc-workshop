# Named Schemas, Views, Parameterized Views, Synonyms & `INFORMATION_SCHEMA`

Production reference for Cloud Spanner named schemas, `SQL SECURITY INVOKER` vs. `DEFINER` views, parameterized views (`SECURE_CONTEXT()`), zero-downtime table renaming & synonyms, and GoogleSQL vs. PostgreSQL `INFORMATION_SCHEMA` introspection.

> **Scope Boundary:** This document covers DDL syntax, semantics, and schema-level caveats for named schemas, views, and `SECURE_CONTEXT()`. For full Fine-Grained Access Control (FGAC) role architecture, conditional `roles/spanner.databaseRoleUser` IAM bindings, MCP Toolbox `authServices`, agent `secure_context` runtime injection, and Model Armor guardrails, route to **`spanner-mcp-toolbox-and-agent-security`**.

## 1. Named Schemas

- **Supported Schema Objects:** `TABLE` (including `INTERLEAVE IN [PARENT]`, `FOREIGN KEY`, `SYNONYM`), `VIEW`, `INDEX`, `SEARCH INDEX`, `FOREIGN KEY`, and `SEQUENCE`.
- **Mandatory Fully Qualified Names (FQN):** Objects inside non-default schemas **must always** be referenced as `{schema_name}.{object_name}`. Configured schema search paths for unqualified lookups are **not supported**.
- **Index Schema Rule (Critical Dialect Difference):**
  - Every index must live in the **same schema** as its base table.
  - **GoogleSQL:** Must schema-qualify **both** the index name and the table name.
  - **PostgreSQL:** Automatically places the index in the base table's schema—**do NOT qualify the index name**.

```sql
-- GoogleSQL
CREATE SCHEMA IF NOT EXISTS billing;
CREATE TABLE billing.Invoices (
  InvoiceId UUID NOT NULL DEFAULT (NEW_UUID()),
  TenantId  STRING(64) NOT NULL,
  Amount    NUMERIC NOT NULL,
) PRIMARY KEY (InvoiceId);
CREATE INDEX billing.IdxInvoicesByTenant ON billing.Invoices(TenantId);

-- Query hint MUST quote qualified index name in GoogleSQL
SELECT InvoiceId FROM billing.Invoices@{FORCE_INDEX="billing.IdxInvoicesByTenant"}
WHERE TenantId = @tenant_id;
```

```sql
-- PostgreSQL
CREATE SCHEMA IF NOT EXISTS billing;
CREATE TABLE billing.invoices (
  invoice_id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  tenant_id  varchar(64) NOT NULL,
  amount     numeric NOT NULL
);
-- Note: index name is UNQUALIFIED in PostgreSQL DDL and hints
CREATE INDEX idx_invoices_by_tenant ON billing.invoices(tenant_id);

SELECT invoice_id FROM billing.invoices /*@ FORCE_INDEX = idx_invoices_by_tenant */
WHERE tenant_id = $1;
```

- **No Cascading Drop:** `DROP SCHEMA [IF EXISTS] {schema_name};` **fails** if any object exists inside the schema—drop every contained view, index, sequence, and table first.
- **Schema-Level Access Syntax Nuances:**
  - Accessing an object in a named schema requires **both** `USAGE ON SCHEMA` and object-level privileges.
  - Bulk grants (`GRANT SELECT ON ALL TABLES IN SCHEMA {schema_name} ...`) apply **only to tables that exist at execution time**; tables created later require explicit grants.
  - **Default Schema Syntax Difference:** GoogleSQL uses `SCHEMA default` / `SCHEMA DEFAULT` with `ROLE` (`GRANT USAGE ON SCHEMA default TO ROLE {role};` / `REVOKE USAGE ON SCHEMA DEFAULT FROM ROLE public;`), whereas PostgreSQL uses `SCHEMA public` for `USAGE` without the `ROLE` keyword (`REVOKE USAGE ON SCHEMA public FROM public;`, `GRANT USAGE ON SCHEMA public TO {role};`) while still using `SCHEMA default` for `ALL TABLES IN SCHEMA default`.
  - **Operational Caution:** Revoking `USAGE` on the default schema from `public` immediately blocks every role lacking an explicit `USAGE` grant from accessing default-schema objects!
- **Schema Discovery:**
  - GoogleSQL: `SELECT CATALOG_NAME, SCHEMA_NAME, PROTO_BUNDLE FROM INFORMATION_SCHEMA.SCHEMATA;` (default schema is `''`).
  - PostgreSQL: `SELECT schema_name, schema_owner, effective_timestamp FROM information_schema.schemata ORDER BY schema_owner;` (returns `public`, user schemas, `pg_catalog`, `information_schema`, `spanner_sys`).

## 2. Views (`SQL SECURITY INVOKER` vs. `SQL SECURITY DEFINER`)

- **Virtual Semantics:** Views are dynamic logical queries (not materialized; negligible storage; query cost matches inlining the view definition).
- **Creation Permission:** Requires database-level `spanner.databases.updateDdl`.
- **Mandatory Security Mode:** Every `CREATE [OR REPLACE] VIEW` **must** specify one of:
  - **`SQL SECURITY INVOKER`:** Caller must hold privileges on the view **AND** on every underlying table/object. Safest default for shared read-only projections.
  - **`SQL SECURITY DEFINER`:** Caller needs `SELECT` privilege **ONLY on the view** (meaningful only with FGAC roles; database-level IAM readers already bypass FGAC and see all tables).
- **Strict Name Qualification & Explicit `CAST` Best Practice:**
  - Every output column must have a name, and every referenced table/column must be unambiguously qualified.
  - Cloud Spanner **validates all future DDL against existing views** and rejects any schema update that would break a view.
  - **Best Practice:** Explicitly `CAST` every projected base-table column in the view definition so underlying column type evolutions (e.g., resizing or compatible type changes) do not invalidate the view.

```sql
-- GoogleSQL: Explicit SQL SECURITY + explicit CASTs on all columns
CREATE OR REPLACE VIEW ActiveInvoicesView
SQL SECURITY INVOKER AS
SELECT
  CAST(i.InvoiceId AS UUID) AS InvoiceId,
  CAST(i.TenantId AS STRING) AS TenantId,
  CAST(i.Amount AS NUMERIC) AS Amount
FROM billing.Invoices AS i
WHERE i.Amount > 0;
```

```sql
-- PostgreSQL: Explicit SQL SECURITY + explicit CASTs
CREATE OR REPLACE VIEW active_invoices_view
SQL SECURITY INVOKER AS
SELECT
  CAST(i.invoice_id AS uuid) AS invoice_id,
  CAST(i.tenant_id AS varchar) AS tenant_id,
  CAST(i.amount AS numeric) AS amount
FROM billing.invoices AS i
WHERE i.amount > 0;
```

### Critical View Gotchas & Grant Dialect Trap

- **`CREATE OR REPLACE VIEW` Wipes Grants:** Replacing a view executes like drop + recreate—**all existing FGAC privileges on the view are dropped**! Always maintain an idempotent, re-runnable grants script and execute it immediately after any view replacement.
- **GoogleSQL vs. PostgreSQL View Grant Syntax:**
  - **GoogleSQL:** `GRANT SELECT ON VIEW {view_name} TO ROLE {role_name};` / `REVOKE SELECT ON VIEW {view_name} FROM ROLE {role_name};`
  - **PostgreSQL:** `GRANT SELECT ON TABLE {view_name} TO {role_name};` / `REVOKE SELECT ON TABLE {view_name} FROM {role_name};` (**must use `ON TABLE`, NOT `ON VIEW`**).

### All 8 View Limitations

1. **Read-Only:** No `INSERT`, `UPDATE`, or `DELETE` DML against views.
2. **No Query Parameters:** Defining SQL cannot reference `@param` or `$1`.
3. **Not Indexable:** You cannot create secondary indexes on a view (index underlying base tables instead).
4. **No External Table Hints:** Callers cannot attach table hints (e.g., `@{FORCE_INDEX=...}`) to a view reference; hints must be placed *inside* the view definition on base tables.
5. **SQL-Only:** Unsupported by the Cloud Spanner Read API.
6. **No Data Boost on Definer's Rights Views:** Querying a `SQL SECURITY DEFINER` view with **Data Boost** fails with an error.
7. **`NORMAL` Query Mode Required (Definer's Rights):** Callers without privileges on underlying tables can only run definer's rights views in `NORMAL` query mode (`PLAN` and `PROFILE` modes fail).
8. **Side-Channel Information Leakage:** A caller with `SELECT` on a definer's rights view can craft runtime-error predicates (e.g., `WHERE s.Level = 7 AND 1 / (s.Score - 20) = 1`) that trigger division-by-zero errors revealing the existence of rows filtered out by the view.

## 3. Parameterized Views (`SECURE_CONTEXT()`)

- **Purpose:** Dynamically enforce row-level security from application-injected out-of-band session context—tailored for **multi-tenant row isolation**, **AI agents / LLM-generated SQL** (preventing prompt injection from reading other tenants' rows), and **ephemeral/application-level identities** not modeled in IAM.
- **DDL Pattern (`SQL SECURITY DEFINER` Required):**

```sql
-- GoogleSQL: Filter on leading PK/index column for storage-layer pushdown
CREATE OR REPLACE VIEW TenantOrdersView
SQL SECURITY DEFINER AS
SELECT
  CAST(Orders.TenantId AS STRING) AS TenantId,
  CAST(Orders.OrderId AS INT64) AS OrderId,
  CAST(Orders.Status AS STRING) AS Status,
  CAST(Orders.TotalAmount AS NUMERIC) AS TotalAmount
FROM Orders
WHERE Orders.TenantId = CAST(SECURE_CONTEXT('tenant_id') AS STRING);
```

- **Casting Rule:** `SECURE_CONTEXT('<string_literal>')` always returns `STRING`; cast explicitly using standard `CAST(SECURE_CONTEXT('user_id') AS INT64)` (never comma syntax).
- **Out-of-Band Context Binding:** Applications attach key-value pairs via the request's `secure_context` map (`map<string, google.protobuf.Value>`) outside the SQL text so neither end users nor LLMs can read or spoof it. Direct ad-hoc queries calling `SECURE_CONTEXT()` outside a definer's rights view fail unless the caller holds the system role **`spanner_secure_context_reader`**.
- **Multi-Field Context Best Practice:** Serialize complex attributes (e.g., multiple caller roles or clearance tiers) as a **JSON string** in one `SECURE_CONTEXT('caller_claims')` parameter and parse with `JSON_VALUE()` / `JSON_VALUE_ARRAY()`. If encapsulating parsing in a SQL UDF, **pass the `SECURE_CONTEXT()` result into the UDF as an argument**—never call `SECURE_CONTEXT()` inside the UDF body:

```sql
-- GoogleSQL: Pass JSON-encoded SECURE_CONTEXT() into filter predicates (never inside UDF body)
CREATE OR REPLACE VIEW ScopedTeamResourcesView
SQL SECURITY DEFINER AS
SELECT
  CAST(r.TenantId AS STRING) AS TenantId,
  CAST(r.ResourceId AS UUID) AS ResourceId,
  CAST(r.ClearanceTier AS STRING) AS ClearanceTier
FROM Resources AS r
WHERE r.TenantId = JSON_VALUE(PARSE_JSON(SECURE_CONTEXT('caller_claims')), '$.tenant_id')
  AND r.ClearanceTier IN UNNEST(
    JSON_VALUE_ARRAY(PARSE_JSON(SECURE_CONTEXT('caller_claims')), '$.allowed_tiers')
  );
```
- **All 4 Parameterized View Limitations:**
  1. The argument to `SECURE_CONTEXT()` **must be a string literal** (no `@param`, column refs, or expressions like `CONCAT(...)`).
  2. Always returns `STRING` (requires `CAST` for non-string PKs).
  3. **Disallowed** inside generated columns, column `DEFAULT` expressions, and **inside UDF bodies**.
  4. **No security barrier:** Internal evaluation order may still inspect filtered rows prior to applying the predicate (same runtime-error side-channel caveat as definer's rights views).

## 4. Table Renaming & Zero-Downtime Synonyms

- **Permission & Limits:** Requires `spanner.databases.updateDdl`. Each table supports **at most 1 synonym** (stored as a `synonym` schema object). Synonyms incur **zero runtime overhead**, but reserve the synonym name across the schema until dropped.

### Exact Rename & Synonym DDL Matrix (GoogleSQL vs. PostgreSQL)

| Operation | GoogleSQL | PostgreSQL |
| :--- | :--- | :--- |
| **Zero-downtime rename + keep old name as synonym** | `ALTER TABLE {old_tbl} RENAME TO {new_tbl}, ADD SYNONYM {old_tbl};` | `ALTER TABLE {old_tbl} RENAME WITH SYNONYM TO {new_tbl};` |
| **Atomic 2-table swap** (blue/green cutover) | `RENAME TABLE {tbl_a} TO {tmp}, {tbl_b} TO {tbl_a}, {tmp} TO {tbl_b};` | `ALTER TABLE {tbl_a} RENAME TO {tmp}, ALTER TABLE {tbl_b} RENAME TO {tbl_a}, ALTER TABLE {tmp} RENAME TO {tbl_b};` |
| **Simple rename** *(breaks callers using old name!)* | `ALTER TABLE {old_tbl} RENAME TO {new_tbl};` or `RENAME TABLE {old_tbl} TO {new_tbl};` | `ALTER TABLE [IF EXISTS] [ONLY] {old_tbl} RENAME TO {new_tbl};` |
| **Create table with initial synonym** | `CREATE TABLE {tbl} (Id INT64 NOT NULL, SYNONYM ({syn})) PRIMARY KEY (Id);` | `CREATE TABLE {tbl} (id bigint PRIMARY KEY, SYNONYM ({syn}));` |
| **Add synonym without renaming** | `ALTER TABLE {tbl} ADD SYNONYM {syn};` | `ALTER TABLE [IF EXISTS] [ONLY] {tbl} ADD SYNONYM {syn};` |
| **Drop synonym** (after all callers migrate) | `ALTER TABLE {tbl} DROP SYNONYM {syn};` | `ALTER TABLE [IF EXISTS] [ONLY] {tbl} DROP SYNONYM {syn};` |

### What Auto-Updates on Table Rename vs. What Breaks

- **Automatically Updated:** Interleaved child tables, secondary indexes, foreign keys, change streams, and FGAC table/column privileges.
- **NOT Automatically Updated:** **Views!** You must drop and recreate affected views (and re-apply their FGAC grants) when renaming an underlying table.
- **Additional Restrictions:** An interleaved table cannot be renamed to match one of its own column names; secondary indexes cannot be renamed (must drop & recreate).

## 5. `INFORMATION_SCHEMA` Reference & Dialect Parity

- **Access Rules:** Accessible via **SQL interfaces only** (`executeQuery`, `gcloud spanner databases execute-sql`, Spanner Studio) inside **read-only transactions** (strong, bounded, or exact staleness). **Unsupported** by the single-read API and **read-write transactions**.
- **FGAC Row Filtering:** Database-level IAM principals and the **`spanner_info_reader`** system role see all rows; other FGAC roles see only metadata for objects they hold privileges on (for example, `VIEWS` requires `SELECT` on the view; `INDEXES` requires table privilege or column privilege on *all* columns in the index).

### Critical GoogleSQL vs. PostgreSQL `INFORMATION_SCHEMA` Differences

| Aspect | GoogleSQL (`INFORMATION_SCHEMA`) | PostgreSQL (`information_schema`) |
| :--- | :--- | :--- |
| **Default Schema Name** | `''` (empty string) | `'public'` |
| **`*_CATALOG` Columns** | Always `''` | Actual **database name** |
| **Boolean-Like Flags** | Mostly `BOOL` (`TRUE`/`FALSE`): `IS_UNIQUE`, `IS_NULL_FILTERED`, `SPANNER_IS_MANAGED`, `IS_INHERITED`, `ALLOW_GC`, `ALL`, `ALL_COLUMNS`. Exceptions (`STRING`): `IS_NULLABLE`, `ENFORCED`, `IS_IDENTITY`, `IS_STORED` (`'YES'`/`'NO'`), `IS_HIDDEN` (`'TRUE'`/`'FALSE'`). | **Always `varchar` `'YES'`/`'NO'`** (`is_unique`, `is_null_filtered`, `spanner_is_managed`, `is_inherited`, `allow_gc`, `all`, `all_columns`, `enforced`)! |
| **`COLUMNS.DATA_TYPE`** | Always `NULL` (read `SPANNER_TYPE`) | Populated (`bigint`, `character varying`, `ARRAY`) + `spanner_type` |
| **Identity & Hidden Metadata** | `IS_IDENTITY`, `IDENTITY_GENERATION`, `IDENTITY_KIND`, `IDENTITY_START_WITH_COUNTER`, `IDENTITY_SKIP_RANGE_MIN/MAX`, `IS_HIDDEN` populated | Standard PG `is_identity`, `identity_*` columns exist but are **always `NULL`**! |
| **`COLUMNS.SPANNER_STATE`** | `WRITE_ONLY`, `COMMITTED` | `NO_WRITE`, `WRITE_ONLY`, `COMMITTED`, `NULL` (system schemas) |
| **`INDEXES.INDEX_TYPE`** | `INDEX`, `PRIMARY_KEY` | `LOCAL`, `GLOBAL`, `PRIMARY_KEY` |
| **`INDEX_OPTIONS.INDEX_TYPE`** | `SEARCH`, `VECTOR`, `INDEX` | `SEARCH`, **`ScaNN`**, `INDEX` |
| **`REFERENTIAL_CONSTRAINTS.MATCH_OPTION`** | `'SIMPLE'` | `'NONE'` |
| **Sequences Metadata** | Two tables: `SEQUENCES` + `SEQUENCE_OPTIONS` | Single view `sequences` (Spanner cols populated; standard PG `start_value`/`increment` always `NULL`) |
| **`TABLE_SYNONYMS` Columns** | `TABLE_CATALOG`, `TABLE_SCHEMA`, `TABLE_NAME`, `SYNONYM_*` | `CATALOG`, `SCHEMA`, `TABLE_NAME`, `SYNONYM_*` |
| **PG-Only Extras & `SELECT *` Warning** | `SELECT *` stable within GoogleSQL | Extra view `column_column_usage` (base -> generated col). **Never use `SELECT *`** in PG: Spanner columns are appended after standard PG columns and shift positions when new PG columns are added! |

### Essential Schema Introspection Queries

```sql
-- 1. User tables, interleaving hierarchy, state & TTL policy (GoogleSQL)
SELECT TABLE_SCHEMA, TABLE_NAME, PARENT_TABLE_NAME, INTERLEAVE_TYPE,
       ON_DELETE_ACTION, SPANNER_STATE, ROW_DELETION_POLICY_EXPRESSION
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA NOT IN ('INFORMATION_SCHEMA', 'SPANNER_SYS')
  AND TABLE_TYPE = 'BASE TABLE'
ORDER BY TABLE_SCHEMA, TABLE_NAME;

-- 2. Foreign keys, enforcement, actions & hidden managed backing indexes (GoogleSQL)
SELECT tc.CONSTRAINT_NAME, tc.TABLE_NAME, tc.ENFORCED,
       rc.DELETE_RULE, rc.SPANNER_STATE
FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS AS tc
JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS AS rc USING (CONSTRAINT_NAME)
WHERE tc.CONSTRAINT_TYPE = 'FOREIGN KEY';

SELECT TABLE_NAME, INDEX_NAME, INDEX_STATE, IS_UNIQUE, IS_NULL_FILTERED
FROM INFORMATION_SCHEMA.INDEXES
WHERE SPANNER_IS_MANAGED = TRUE; -- PostgreSQL: WHERE spanner_is_managed = 'YES'

-- 3. Views, security mode & table synonyms (GoogleSQL)
SELECT TABLE_SCHEMA, TABLE_NAME, SECURITY_TYPE
FROM INFORMATION_SCHEMA.VIEWS
WHERE TABLE_SCHEMA NOT IN ('INFORMATION_SCHEMA', 'SPANNER_SYS');

SELECT TABLE_SCHEMA, TABLE_NAME, SYNONYM_NAME
FROM INFORMATION_SCHEMA.TABLE_SYNONYMS;

-- 4. Locality groups & column inheritance (Enterprise / Enterprise Plus, GoogleSQL)
SELECT LOCALITY_GROUP_NAME, OPTION_NAME, OPTION_VALUE, IS_INHERITED
FROM INFORMATION_SCHEMA.LOCALITY_GROUP_OPTIONS;

SELECT TABLE_NAME, COLUMN_NAME, OPTION_VALUE AS locality_group, IS_INHERITED
FROM INFORMATION_SCHEMA.COLUMN_OPTIONS
WHERE OPTION_NAME = 'locality_group';
```

## Official Documentation

- [Named Schemas](https://docs.cloud.google.com/spanner/docs/named-schemas) | [Views Overview](https://docs.cloud.google.com/spanner/docs/views)
- [Create & Manage Views](https://docs.cloud.google.com/spanner/docs/create-manage-views) | [Parameterized Views](https://docs.cloud.google.com/spanner/docs/parameterized-views)
- [Table Renaming & Synonyms](https://docs.cloud.google.com/spanner/docs/table-name-synonym)
- [INFORMATION_SCHEMA (GoogleSQL)](https://docs.cloud.google.com/spanner/docs/information-schema) | [information_schema (PostgreSQL)](https://docs.cloud.google.com/spanner/docs/information-schema-pg)
