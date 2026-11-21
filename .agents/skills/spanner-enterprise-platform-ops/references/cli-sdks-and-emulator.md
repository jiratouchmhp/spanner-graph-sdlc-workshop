# Cloud Spanner CLI, Official SDKs, PGAdapter & Emulator Reference

Self-contained operational reference for project/API setup, `gcloud spanner`, `gcloud spanner cli`, the 9 official client libraries, REST API caveats, Drivers/ORMs, PGAdapter (`spgc`, Docker, sidecar, `psql`), Spanner Studio, IntelliJ, and the Cloud Spanner Emulator.

## 1. Project Initialization & Authentication

```bash
# Enable Spanner API
gcloud services enable spanner.googleapis.com --project="{project_id}"

# Local interactive developer authentication (ADC)
gcloud auth login
gcloud auth application-default login
gcloud config set project "{project_id}"
gcloud config set spanner/instance "{instance_id}"
```

- **CRITICAL Cross-Project Service Account Gotcha**: When a service account residing in **Project A** accesses a Spanner database in **Project B**, `spanner.googleapis.com` MUST be enabled in **BOTH** Project A and Project B (otherwise requests fail with `SERVICE_DISABLED`).
- **Production Auth Standard**: Always use **Workload Identity Federation** or attached GCE/GKE/Cloud Run service accounts rather than exported static JSON service account keys.

## 2. Essential `gcloud spanner` CLI Commands & Gotchas

### Instance & Database Lifecycle

```bash
# Provision Enterprise regional instance (Bangkok example; >= 1 node for production)
gcloud spanner instances create "{instance_id}" \
  --project="{project_id}" \
  --config="regional-asia-southeast3" \
  --edition="ENTERPRISE" \
  --nodes=1 \
  --description="Production Enterprise Instance"

# GoogleSQL database: supports --ddl / --ddl-file directly at creation time
gcloud spanner databases create "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --database-dialect=GOOGLE_STANDARD_SQL \
  --ddl-file="schema.sql"

# PostgreSQL database: GOTCHA — --ddl and --ddl-file are UNSUPPORTED at create time!
# Create an empty PostgreSQL DB first, then apply DDL with ddl update:
gcloud spanner databases create "{pg_database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --database-dialect=POSTGRESQL

gcloud spanner databases ddl update "{pg_database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --ddl-file="pg_schema.sql"
```

### Operations Tracking, SQL Execution & Deletion

```bash
# Monitor async DDL Long-Running Operations (LROs)
gcloud spanner operations list \
  --project="{project_id}" --instance="{instance_id}" --database="{database_id}" \
  --type=DATABASE_UPDATE_DDL

gcloud spanner operations describe "{operation_id}" \
  --project="{project_id}" --instance="{instance_id}" --database="{database_id}"

# Execute SQL with query profiling or explicit FGAC database role
gcloud spanner databases execute-sql "{database_id}" \
  --project="{project_id}" --instance="{instance_id}" \
  --query-mode=PROFILE \
  [--database-role="{role_name}"] \
  --sql="SELECT * FROM {table_name} WHERE id = @id" \
  --params="id=123"

# Database deletion: DDL has NO `DROP DATABASE` statement (blocked if drop protection is enabled)
gcloud spanner databases delete "{database_id}" \
  --project="{project_id}" --instance="{instance_id}"
```

### Critical `gcloud spanner rows` Gotchas

1. **PostgreSQL `rows insert` Failure**: `gcloud spanner rows insert` (mutation-based) fails on PostgreSQL-dialect databases due to a known table-name resolution issue. **Always use `gcloud spanner databases execute-sql` with SQL DML (`INSERT INTO ...`) on PostgreSQL databases.**
2. **Commas Inside `--data=` Values**: When a column value contains a comma, override the key-value delimiter using `^:^`:
   ```bash
   gcloud spanner rows insert --instance="{instance_id}" --database="{database_id}" \
     --table="{table_name}" --data='^:^Id=1:FullName=Smith, Jane'
   ```

## 3. `gcloud spanner cli` Interactive Shell (GoogleSQL ONLY)

- **Dialect Restriction**: `gcloud spanner cli` supports **GoogleSQL databases ONLY**! For PostgreSQL databases, use `spgc psql` or `psql` + PGAdapter.
- **Installation**: Auto-installs on first run, or install explicitly via `gcloud components install spanner-cli` / `apt-get install google-cloud-cli-spanner-cli`.

```bash
gcloud spanner cli "{database_id}" \
  --project="{project_id}" --instance="{instance_id}" \
  [--execute="SELECT 1;"] \
  [--source="script.sql"] \
  [--tee="session.log"] \
  [--host="localhost" --port=9010]
```

### Meta-Commands & Interactive SQL Gotchas

- **Meta-Commands**: `\?` / `\h` (help), `\d <delim>` (change delimiter), `\q` (quit), `\g` (execute), `\T <file>` (tee log on), `\t` (notee), `\R <prompt>` (custom prompt), `\. <file>` (source SQL file), `\! <cmd>` (shell escape), `\u <db>` (switch database).
- **Introspection & Analysis**: `SHOW DATABASES`, `SHOW TABLES`, `SHOW CREATE TABLE {table_name}`, `SHOW COLUMNS FROM {table_name}`, `SHOW INDEX FROM {table_name}`, `USE {database_id} [ROLE {role_name}]`, `EXPLAIN <sql>`, `EXPLAIN ANALYZE <sql>`, `DESCRIBE <sql>`, `ANALYZE`.
- **Batched DDL**: `START BATCH DDL` → multiple DDL statements → `RUN BATCH` (or `ABORT BATCH`).
- **Partitioned DML**: `PARTITIONED UPDATE ...`, `PARTITIONED DELETE ...`.
- **CRITICAL `TRUNCATE TABLE` GOTCHA**: `TRUNCATE TABLE {table_name}` in `gcloud spanner cli` runs as **non-atomic Partitioned DML** (`DELETE FROM {table_name} WHERE true`) — it is **NOT** a single atomic transaction and **cannot** be rolled back!
- **Transactions & Tags**:
  ```sql
  BEGIN RW ISOLATION LEVEL SERIALIZABLE PRIORITY HIGH TAG checkout_txn;
  BEGIN RO 15 PRIORITY MEDIUM TAG analytics_read;   -- 15s exact staleness
  ```
  - **RO `TAG` Nuance**: Because Spanner read-only transactions do not natively support transaction tags, `gcloud spanner cli` transparently applies `TAG` on `BEGIN RO` as a **request tag** on each query in the transaction.

## 4. Official Client Libraries, REST API & Drivers/ORMs Matrix

### 9 Official Client Libraries (gRPC / HTTP2 over TLS)

| Language | Official Package | Key Entrypoints / Notes |
| :--- | :--- | :--- |
| **Python** | `google-cloud-spanner` | `spanner.Client()`, `instance.database()`, `database.snapshot()`, `run_in_transaction()`, `batch()` |
| **Node.js** | `@google-cloud/spanner` | `new Spanner()`, `instance.database()`, `database.run()`, `runTransaction()` |
| **Go** | `cloud.google.com/go/spanner` | `spanner.NewClient()`, `ReadWriteTransaction()`, `Single()` |
| **Java** | `com.google.cloud:google-cloud-spanner` | `SpannerOptions`, `DatabaseClient`, `TransactionRunner` |
| **C# / .NET** | `Google.Cloud.Spanner.Data` | ADO.NET provider + `SpannerConnectionStringBuilder` (special emulator flag!) |
| **C++** | `google-cloud-cpp` | `spanner::Client` |
| **PHP** | `google/cloud-spanner` | **Requires the PHP `grpc` extension** |
| **Ruby** | `google-cloud-spanner` | `Google::Cloud::Spanner.new` |
| **Rust** | `google-cloud-spanner` | Async runtime using `tokio` |

- **AI & Framework Integrations**: **LangChain** (`SpannerVectorStore`, `SpannerLoader`, `SpannerChatMessageHistory`) and **Spring Data Cloud Spanner**.
- **Legacy App Engine Standard**: Legacy Go, PHP, and Python runtimes without gRPC support must call the REST API (Java 8+ supports gRPC).
- **Multiplexed Sessions & Legacy Session Pool IAM Gotcha**: Prefer modern **multiplexed sessions** (default in current SDKs). Legacy session-pooled clients pre-allocate a fraction of sessions for RW transactions; principals holding ONLY `roles/spanner.databaseReader` can experience intermittent permission errors on read-only requests unless `write_sessions_fraction` is set to `0` or multiplexed sessions are enabled.

```python
# Minimal Python SDK pattern (google-cloud-spanner): RO exact staleness + RW transaction
from datetime import timedelta
from google.cloud import spanner

client = spanner.Client(project="{project_id}")
db = client.instance("{instance_id}").database("{database_id}")

# Non-leader local read (>= 15s exact staleness avoids leader round-trip in multi-region/RO)
with db.snapshot(exact_staleness=timedelta(seconds=15)) as snapshot:
    rows = list(snapshot.execute_sql(
        "SELECT id, status FROM {table_name} WHERE id = @id",
        params={"id": "item-1"},
        param_types={"id": spanner.param_types.STRING},
    ))

# Atomic RW transaction (auto-retried on ABORTED; NEVER call external HTTP APIs inside!)
def update_status(transaction):
    transaction.execute_update(
        "UPDATE {table_name} SET status = @status WHERE id = @id",
        params={"id": "item-1", "status": "ACTIVE"},
        param_types={"id": spanner.param_types.STRING, "status": spanner.param_types.STRING},
    )

db.run_in_transaction(update_status)
```

### REST API Operational Caveats

- Requires explicit session lifecycle management (`projects.instances.databases.sessions.create`); idle sessions are **deleted by Spanner after 1 hour** (subsequent calls fail with `NOT_FOUND`).
- Core RPCs: `sessions.commit`, `sessions.executeSql`, `sessions.read`, `sessions.beginTransaction`.
- **Read API Secondary-Index Join Gotcha**: The `Read` API **cannot join** a secondary index back to the base table — any non-key column read via a secondary index MUST be stored in the index (`STORING (...)` in GoogleSQL / `INCLUDE (...)` in PostgreSQL).
- **Hyphenated Database Names in DDL**: In `CREATE DATABASE` DDL via REST/RPC, wrap hyphenated database IDs in backticks (e.g., `` CREATE DATABASE `example-db` ``).

### Drivers & ORMs Ecosystem Matrix

- **Direct Dialect Support (NO PGAdapter required)**:
  - *GoogleSQL (11)*: Go `database/sql`, GORM, Spanner JDBC, Hibernate, Spring Data, R2DBC, ADO.NET, Entity Framework Core, Ruby Active Record, Python SQLAlchemy, Django.
  - *PostgreSQL **without** PGAdapter (**ONLY 3**)*: Go **`database/sql`**, **Spanner JDBC** (`jdbc:cloudspanner:/projects/...` auto-detects dialect), and **.NET ADO.NET**.
- **PostgreSQL Ecosystem via PGAdapter**:
  - *9 Drivers*: PostgreSQL JDBC (`org.postgresql`), Go `pgx`, Python `psycopg2` & `psycopg3`, Node.js `node-postgres` (`pg`), .NET `Npgsql`, `R2DBC`, Apache Arrow `ADBC`, and Spanner JDBC.
  - *ORMs & Tooling*: Hibernate, Spring Data JPA, GORM, SQLAlchemy 2, `Knex.js`, `Sequelize.js`, `Prisma`, `Drizzle`, PHP `PDO_PGSQL`, `postgres_fdw`, `connectorx`, `Liquibase`.

## 5. PGAdapter, `spgc`, `psql`, Spanner Studio & IntelliJ

### PGAdapter 6 Deployment Modes (`<= 0.2 ms` Proxy Overhead)

PGAdapter translates PostgreSQL wire protocol ↔ Cloud Spanner gRPC. Use **Unix domain sockets** on localhost for minimum latency.

1. **`spanner-pg-connector` (`spgc`)**: Self-contained CLI bundling PGAdapter + minimal JRE (no Java or Docker required):
   ```bash
   spgc psql -d "projects/{project_id}/instances/{instance_id}/databases/{pg_database_id}"
   # GOTCHA: Do NOT pass -h or -p to `spgc psql` — spgc manages the local socket/port automatically!
   ```
2. **Standalone JAR**:
   ```bash
   java -jar pgadapter.jar -p "{project_id}" -i "{instance_id}" -d "{pg_database_id}" [-c /path/to/creds.json]
   ```
3. **Docker / Distroless**: `gcr.io/cloud-spanner-pg-adapter/pgadapter` or non-root `gcr.io/cloud-spanner-pg-adapter/pgadapter-distroless`.
4. **Sidecar**: Cloud Run sidecar container or GKE pod sidecar communicating over `localhost` or a shared Unix socket volume.
5. **In-Process Java**: Embed `com.google.cloud.spanner.pgadapter.ProxyServer` directly inside a JVM service.
6. **Embedded Emulator Container**: `gcr.io/cloud-spanner-pg-adapter/pgadapter-emulator` exposing ports **`5432`** (PostgreSQL wire), **`9010`** (Spanner gRPC), and **`9020`** (Spanner REST) — auto-creates instance and database on connect.

- **FGAC IAM for PGAdapter**: Principals need `spanner.databases.useRoleBasedAccess` (plus `spanner.databaseRoles.use` for non-`public` roles; DDL requires `spanner.databases.updateDdl`).

### `psql` Supported Commands & Bulk `COPY` Modes

- **Supported Meta-Commands**: `\d`, `\d <table>`, `\dt`, `\dt <table>`, `\dn` (connection-management and Large Objects meta-commands are unsupported).
- **Multi-Statement `-c` Gotcha**: `psql -c "stmt1; stmt2;"` runs inside an **implicit transaction block** — because Spanner forbids transactional DDL, combining DDL with explicit `BEGIN ... COMMIT` fails.
- **Bulk Load (`COPY FROM STDIN`)**:
  - Default mode is **atomic**, capped at **80,000 mutations** and **100 MiB**!
  - For large bulk loads, switch to parallel partitioned non-atomic ingestion (**GOTCHA: no rollback on error!**):
    ```sql
    SET SPANNER.AUTOCOMMIT_DML_MODE = 'PARTITIONED_NON_ATOMIC';
    COPY {table_name} FROM STDIN WITH (FORMAT csv);
    ```

### Spanner Studio (Cloud Console) & JetBrains IntelliJ

- **Spanner Studio**: Executes mixed DDL/DML/SQL scripts (auto-batches consecutive DDL), exports results to CSV/JSON/Google Sheets, surfaces schema recommendations and Index Advisor cards, and includes **4** GoogleSQL sample datasets (Finance graph, Online banking, Online gaming, Retail) vs **3** PostgreSQL sample datasets (no Finance graph).
- **Console Data Import**: Imports CSV (requires companion JSON schema declaring `name`, `type`, `notNull`, and `primaryKeyOrder` where `0` = non-PK and `1..N` = composite PK order), MySQL dumps, or PostgreSQL dumps via GCS + Spanner Migration Tool (SMT) / `gcloud alpha spanner databases import`, plus one-time Cloud SQL for MySQL bulk imports.
- **JetBrains IntelliJ**: Built-in Google Cloud Spanner datasource supports both GoogleSQL and PostgreSQL plus local Emulator (`autoConfigEmulator=true`, `No Auth` auto-provisions project/instance/DB).

## 6. Cloud Spanner Emulator

In-memory local emulator supporting **both GoogleSQL and PostgreSQL** ([GoogleCloudPlatform/cloud-spanner-emulator](https://github.com/GoogleCloudPlatform/cloud-spanner-emulator)); all state is wiped on restart.

```bash
# Start via gcloud component (cloud-spanner-emulator) or Docker:
gcloud emulators spanner start
# Docker alternative:
# docker run -p 9010:9010 -p 9020:9020 gcr.io/cloud-spanner-emulator/emulator

# Endpoints: localhost:9010 (gRPC) and localhost:9020 (REST)
gcloud config configurations create spanner-emulator
gcloud config set auth/disable_credentials true
gcloud config set project "test-project"
gcloud config set api_endpoint_overrides/spanner "http://localhost:9020/"
eval "$(gcloud emulators spanner env-init)"   # exports SPANNER_EMULATOR_HOST=localhost:9010

gcloud spanner instances create "test-instance" \
  --config="emulator-config" --description="Local Emulator" --nodes=1
```

- **CRITICAL C# / .NET GOTCHA**: `Google.Cloud.Spanner.Data` **IGNORES** `SPANNER_EMULATOR_HOST` by default! You MUST explicitly set `EmulatorDetection = EmulatorDetection.EmulatorOnly` (or `"EmulatorOnly"` in `SpannerConnectionStringBuilder`).

### 7 Critical Emulator vs Production Divergences

Never treat passing emulator tests as sufficient proof of production safety:

| # | Area | Emulator Behavior | Production Impact if Missed |
| :-: | :--- | :--- | :--- |
| **1** | **Security / IAM / FGAC / TLS** | Completely bypassed (zero permission checks) | `PERMISSION_DENIED` or missing FGAC grants discovered only in live GCP |
| **2** | **`PLAN` & `PROFILE` Query Plans** | Returns empty plans | Cannot verify index usage, join strategy, or columnar read share |
| **3** | **`ANALYZE` & Optimizer Stats** | Accepted as no-op | Optimizer stats packages and plan regressions are untested |
| **4** | **Audit Logs & Cloud Monitoring** | None emitted | Alert policies and audit log pipelines cannot be validated locally |
| **5** | **`enable_drop_protection`** | Accepted but **IGNORED** (allows DB deletion!) | Gives false confidence in deletion-protection automation tests |
| **6** | **Lock Granularity** | Exclusive **database-wide lock** on RW txns & DDL | Completely hides real cell-level lock contention and wound-wait aborts |
| **7** | **PDML & `PartitionQuery` Validation** | **Does NOT validate partitionability!** | Non-partitionable PDML/queries pass locally and **fail with `BadUsage`** in prod |

## Official Documentation

- [Set Up Cloud Spanner & Authentication](https://docs.cloud.google.com/spanner/docs/getting-started/set-up)
- [Use `gcloud spanner cli`](https://docs.cloud.google.com/spanner/docs/spanner-cli)
- [Cloud Spanner Client Libraries](https://docs.cloud.google.com/spanner/docs/reference/libraries)
- [Drivers & ORMs Overview](https://docs.cloud.google.com/spanner/docs/drivers-overview)
- [PGAdapter & `spgc` Overview](https://docs.cloud.google.com/spanner/docs/pgadapter)
- [Cloud Spanner Emulator](https://docs.cloud.google.com/spanner/docs/emulator)
- [Cloud Spanner Best-Practice Samples (GitHub)](https://github.com/GoogleCloudPlatform/spanner-bb-samples)
