---
name: spanner-enterprise-platform-ops
metadata:
  version: "1.0.0"
  category: "Cloud Spanner Enterprise"
description: >-
  Guides Cloud Spanner platform ops, editions, dialects, CLI/SDKs, and regional/multi-region topology. Use when selecting Standard/Enterprise/Enterprise Plus editions, CUDs, or Free Trial; configuring regional/multi-region topologies (e.g., Bangkok regional-asia-southeast3 single-region primary, dual-/multi-region configs), GoogleSQL vs PostgreSQL dialects, KV APIs, Spanner Queues, gcloud/SDK/PGAdapter/Emulator, autoscaler deployment & eligibility, geo-partitioning, read leases, move-instance, PITR, backups, or deletion protection. Don't use for table/index/FK/view/TTL/locality DDL (spanner-schema-and-data-modeling), Property Graphs/GQL (spanner-graph-modeling-and-gql), FTS/Vector/GraphRAG (spanner-search-ai-and-graphrag), migrations (spanner-migration-and-neo4j-playbook), MCP/FGAC security (spanner-mcp-toolbox-and-agent-security), or CPU tuning/query plans/TCO sizing (spanner-performance-observability-and-sizing).
---

# Cloud Spanner Enterprise Platform Operations

Production runbook and decision router for Cloud Spanner editions, dialects, regional/multi-region topologies, CLI/SDK/PGAdapter/Emulator workflows, Spanner Queues, autoscaling deployment, zero-downtime instance moves, geo-partitioning, backups, PITR, CMEK, and lifecycle operations.

## Mandatory Safety & Confirmation Guardrails

Before executing any state-mutating command against a live GCP project or remote Spanner instance:

1. **Require explicit user confirmation** before running `gcloud spanner instances create|update|delete|move`, `databases create|delete|restore|ddl update`, `backups copy|delete`, `changequorum`, Org Policy changes, or `PARTITIONED UPDATE|DELETE`.
2. **Verify active target context first**: run `gcloud config get-value project`, `gcloud config get-value spanner/instance`, and check `env | grep SPANNER_EMULATOR_HOST` plus `gcloud config get-value api_endpoint_overrides/spanner` so local emulator commands never hit production (and vice versa).
3. **Protect production databases immediately**: enable `--enable-drop-protection` via CLI/API right after creation or restore (not available in Cloud Console and not inherited on restore).
4. **Pause IaC before `move-instance`**: disable automated `terraform apply` pipelines before running `gcloud spanner instances move`; Terraform treats instance `config` changes as `ForceNew` (destroy + recreate = catastrophic data loss).
5. **Prevent irreversible CMEK loss**: disabling or destroying a CMEK/EKM key for **> 7 consecutive days** triggers automatic, permanent database deletion.

## Non-Negotiable Platform Invariants

1. **Edition Floor**: Use `ENTERPRISE` for Spanner Graph, Full-Text Search, Vector Search (KNN/ANN), Spanner Queues, Managed Autoscaler, Tiered Storage/Locality Groups, Columnar Engine, and Incremental Backups. Use `ENTERPRISE_PLUS` only when Dual-Region/Multi-Region (`99.999%` SLA) or Geo-Partitioning is required.
2. **Dialect Immutability**: Choose `GOOGLE_STANDARD_SQL` or `POSTGRESQL` per database at creation time — it cannot be changed later. **Spanner Graph (GQL)**, **`CREATE MODEL`** remote Vertex AI DDL, and **`gcloud spanner cli`** are **GoogleSQL-only**.
3. **No General `ROW_NUMBER()` or Analytic Window Functions**: Cloud Spanner (both GoogleSQL and PostgreSQL) does **not** support general SQL analytic window functions (`ROW_NUMBER()`, `RANK()`, `DENSE_RANK()`, `LAG()`, `LEAD()`). Use `UNNEST(ARRAY(SELECT ... ORDER BY ... LIMIT k)) AS x WITH OFFSET AS rank` (0-based) for top-K ranking/RRF, or offload general analytic windows to BigQuery `EXTERNAL_QUERY`.
4. **Production Compute Floor (`>= 1,000` PUs)**: Provision at least **1 node (`1,000` PUs)** for production. Granular instances (`100`–`900` PUs) run on a single server task per zone (non-linear scaling, intermittent latency spikes) and cannot use Managed Autoscaler or `move-instance`.
5. **Southeast Asia Regional Topology (Bangkok Single-Region Example)**: No pre-built Bangkok + Singapore dual-region or multi-region config exists. For Thailand PDPA residency, run a **single regional** `regional-asia-southeast3` (Bangkok, 3-zone sync quorum `asia-southeast3-a/b/c`, `99.99%` SLA) instance on `ENTERPRISE` with co-located Cloud Run, user-managed Secret Manager, GCS staging, Dataflow, and same-region PITR/incremental backups; use Singapore (`asia-southeast1`, ~25 ms RTT) **only** for companion GCP services unavailable in Bangkok (Vertex AI embeddings/LLMs, Model Armor, Cloud DLP).
6. **Read-Only Replicas Are Not Standalone DR**: RO replicas do not vote in Paxos quorum and cannot become leader; if the primary voting quorum fails, RO replicas also go offline and cannot serve even stale reads.
7. **Deletion Protection Blind Spots**: `--enable-drop-protection` / `--no-enable-drop-protection` is a CLI/API-only **`databases update`** flag (NOT an `instances update` flag), is NOT inherited by restored databases, must be disabled on every database before deleting an instance, and does NOT block GCP project deletion or Free Trial 90+30-day permanent expiration.
8. **Queue At-Most-Once `OUT_OF_RANGE` Trap**: `DELETE ... ASSERT_ROWS_MODIFIED 1` fails at the **statement level** with `OUT_OF_RANGE` if a message was already acked, and does **not** auto-rollback the transaction. Never catch `OUT_OF_RANGE` and commit — always let the error abort the transaction.
9. **Emulator Divergences**: Never validate production readiness on the emulator alone. It skips IAM/FGAC, returns empty `PLAN`/`PROFILE`, ignores `ANALYZE` and `enable_drop_protection`, locks the entire database on RW/DDL, and fails to reject non-partitionable PDML or `PartitionQuery`.
10. **Zero-Downtime `move-instance` Backup Blocker**: Instance moves fail if *any* backup or backup schedule (including the automatic default schedule) exists on the instance. Copy backups to temporary 100-PU placeholder instances in source and destination configs first.
11. **Instance Display Name (`--description`) Length Limit**: `gcloud spanner instances create --description="..."` maps to `instance.display_name`, which the Spanner Admin API strictly limits to **4–30 characters** (longer values fail with `INVALID_ARGUMENT`). Keep instance descriptions concise (especially when interpolating region names).

## Platform Decision Matrix

| Need / Decision | Recommended Choice | Key Constraint / Trade-Off | Reference |
| :--- | :--- | :--- | :--- |
| Greenfield GCP app with Graph, FTS, Vector, or `CREATE MODEL` | **GoogleSQL** + **Enterprise** | Immutable per DB; Graph/GQL & `gcloud spanner cli` require GoogleSQL | [editions-and-tier-selection.md](references/editions-and-tier-selection.md), [dialects-kv-and-queues.md](references/dialects-kv-and-queues.md) |
| Existing PostgreSQL drivers/ORMs (`pgx`, `psycopg`, Prisma, Hibernate) | **PostgreSQL** + **PGAdapter** (`spgc`/sidecar) | `<= 0.2 ms` proxy overhead; avoid 6 OSS PG behavioral divergences | [dialects-kv-and-queues.md](references/dialects-kv-and-queues.md), [cli-sdks-and-emulator.md](references/cli-sdks-and-emulator.md) |
| Single-country PDPA residency (e.g., Thailand) + AI/Search | `regional-asia-southeast3` (**Enterprise**, single region) + `asia-southeast1` companion AI/security | 99.99% SLA (3-zone sync quorum); same-region PITR/backups; SG only for Vertex AI, Model Armor & DLP | [editions-and-tier-selection.md](references/editions-and-tier-selection.md), [scaling-geo-and-ops.md](references/scaling-geo-and-ops.md) |
| Active-active multi-region HA (`99.999%` SLA) or Geo-Partitioning | **Enterprise Plus** | Geo-Partitioning Preview requires multi-region partitions only | [editions-and-tier-selection.md](references/editions-and-tier-selection.md), [scaling-geo-and-ops.md](references/scaling-geo-and-ops.md) |
| Transactional outbox / pull worker queue inside DB | **Spanner Queues** (**Enterprise+**) | Atomic commit with state; 10s lease rotation; keep payloads `< 4 KB` | [dialects-kv-and-queues.md](references/dialects-kv-and-queues.md) |
| CDC to BigQuery / Kafka / async cross-region DR | **Change Streams** (All Editions) | Discard sentinel read timestamp `2^63 - 2`; `1d`–`30d` retention | [dialects-kv-and-queues.md](references/dialects-kv-and-queues.md) |
| Autoscaling `>= 1` node on Enterprise/Enterprise Plus | **Managed Autoscaler** | `min >= 1` node, `max <= 10x min`; exempt from edition Org Policy block | [scaling-geo-and-ops.md](references/scaling-geo-and-ops.md) |
| Autoscaling Standard edition, `< 1,000` PUs, or custom metrics | **Open-Source Autoscaler** (Cloud Run Functions / GKE) | Blocked on non-compliant instances if edition Org Policy is enforced | [scaling-geo-and-ops.md](references/scaling-geo-and-ops.md) |
| Low-latency local strong reads in non-leader regions | **Read Leases** (`read_lease_regions`) | Adds +1 RTT to writes; PG requires `default_transaction_read_only` or `REPEATABLE_READ` | [scaling-geo-and-ops.md](references/scaling-geo-and-ops.md) |
| Local CI/CD unit tests without GCP billing | **Spanner Emulator** (`9010` gRPC / `9020` REST) | C# requires `EmulatorDetection.EmulatorOnly`; validate plans/IAM in GCP | [cli-sdks-and-emulator.md](references/cli-sdks-and-emulator.md) |

## Runnable Quickstart Patterns

### 1. Provision Regional Enterprise Instance, Protected DB & PITR (Bangkok Example)

```bash
# Confirm project & API enablement (enable in BOTH caller and target projects for cross-project SAs)
gcloud services enable spanner.googleapis.com --project="{project_id}"

# Production instance (>= 1,000 PUs; use --processing-units=100 only for non-prod/dev)
# NOTE: --description maps to instance.display_name and MUST be 4-30 characters long.
gcloud spanner instances create "{instance_id}" \
  --project="{project_id}" \
  --config="regional-asia-southeast3" \
  --edition="ENTERPRISE" \
  --nodes=1 \
  --default-backup-schedule-type=AUTOMATIC \
  --description="Production Enterprise BKK"

# Create GoogleSQL database, enable CLI-only drop protection, and set 7-day PITR
gcloud spanner databases create "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --database-dialect=GOOGLE_STANDARD_SQL

gcloud spanner databases update "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --enable-drop-protection

gcloud spanner databases ddl update "{database_id}" \
  --project="{project_id}" \
  --instance="{instance_id}" \
  --ddl="ALTER DATABASE \`{database_id}\` SET OPTIONS (version_retention_period = '7d')"
```

### 2. Backup Copy & Restore Workflow (Generic Platform Operations)

```bash
# 1. Copy backup to a target instance (same-region isolation, or cross-region where residency permits; incremental blocks deduplicate)
gcloud spanner backups copy \
  --project="{project_id}" \
  --source-instance="{source_instance_id}" \
  --source-backup="{backup_id}" \
  --destination-instance="{target_instance_id}" \
  --destination-backup="{copied_backup_id}" \
  --expiration-date="2026-12-31T00:00:00Z"

# 2. Restore into a NEW database on the target instance (same project + same destination config)
gcloud spanner databases restore \
  --project="{project_id}" \
  --destination-instance="{target_instance_id}" \
  --destination-database="{restored_database_id}" \
  --source-instance="{target_instance_id}" \
  --source-backup="{copied_backup_id}"

# 3. Re-enable drop protection and re-apply IAM policies (NOT copied by backup restore!)
gcloud spanner databases update "{restored_database_id}" \
  --project="{project_id}" \
  --instance="{target_instance_id}" \
  --enable-drop-protection
```

### 3. Zero-Downtime In-Place Edition Upgrade

```bash
# Takes ~10 minutes with zero downtime and no data migration
gcloud spanner instances update "{instance_id}" \
  --project="{project_id}" \
  --edition="ENTERPRISE"
```

### 4. Isolated Local Emulator `gcloud` Profile

```bash
gcloud emulators spanner start  # gRPC: localhost:9010, REST: localhost:9020
gcloud config configurations create spanner-emulator
gcloud config set auth/disable_credentials true
gcloud config set project "local-test-project"
gcloud config set api_endpoint_overrides/spanner "http://localhost:9020/"
export SPANNER_EMULATOR_HOST="localhost:9010"

gcloud spanner instances create "test-instance" \
  --config="emulator-config" --description="Local Emulator" --nodes=1
```

## Production Go-Live Readiness Checklist

1. **Edition & Capacity**: Confirm `ENTERPRISE` (or `ENTERPRISE_PLUS` for dual/multi-region/geo-partitioning) and `>= 1,000` PUs (`>= 1` node) for production SLAs.
2. **Deletion Protection**: Enable `--enable-drop-protection` via CLI/API on every production database (and re-enable after any restore).
3. **PITR & Backups**: Set `version_retention_period` (`1h`–`7d`), verify automated same-region full/incremental schedules, and test backup restore (including IAM policy and `--enable-drop-protection` re-application).
4. **Autoscaler Compatibility**: Confirm Managed Autoscaler (`min >= 1` node, `max <= 10x min`) or verify Open-Source Autoscaler is not blocked by `constraints/spanner.managed.restrictCloudSpannerEditions`.
5. **Client Configuration**: Use multiplexed sessions, verify cross-project `spanner.googleapis.com` enablement, and configure `>= 15s` exact staleness or Read Leases where cross-region read latency matters.
6. **CMEK & Monitoring**: If using CMEK/EKM with multiple regional keys, configure the 2-condition AND partial-key-revocation alert and never leave a key disabled `> 7` days.
7. **Live GCP Validation**: Never rely on the emulator alone — validate IAM/FGAC, `PROFILE` execution plans, `ANALYZE`, and PDML/`PartitionQuery` partitionability in a live GCP project.

## Reference Deep-Dives

Read the specific reference file matching your task (each file is self-contained):

- **[Editions & Tier Selection](references/editions-and-tier-selection.md)**: Standard vs Enterprise vs Enterprise Plus matrix, verified Bangkok (`asia-southeast3`) & reference regional 2026 pricing, downgrade checklist, `restrictCloudSpannerEditions` Org Policy gotcha, spend-based CUDs, Free Trial limits, compute/storage/QPS bounds, and Enterprise feature boundaries (Tiered Storage, Data Boost, Columnar Engine, FTS/Vector).
- **[Dialects, KV & Queues](references/dialects-kv-and-queues.md)**: GoogleSQL vs PostgreSQL selection, 6 PG behavioral divergences, complete Syntax Rosetta Stone, no-`ROW_NUMBER()` replacement & BigQuery `EXTERNAL_QUERY` workaround, hints, DML/PDML/Batch Write, Serializable vs Repeatable Read isolation, staleness reads, KV APIs, Change Streams (`2^63 - 2` sentinel gotcha), and Spanner Queues (DDL, lease rotation, `ASSERT_ROWS_MODIFIED 1` trap, limits, FGAC gotchas, patterns).
- **[CLI, SDKs & Emulator](references/cli-sdks-and-emulator.md)**: Cross-project API enablement, `gcloud spanner` CLI & PG gotchas (`rows insert`, `--ddl-file`, `^:^` escaping), `gcloud spanner cli` (GoogleSQL-only, `TRUNCATE TABLE` PDML trap), 9 official SDKs, REST API caveats, Drivers/ORMs matrix, PGAdapter 6 deployment modes (`spgc`, sidecar, `pgadapter-emulator`), `psql` `COPY` modes, Spanner Studio/IntelliJ, and 7 Emulator vs Production divergences.
- **[Scaling, Geo & Ops](references/scaling-geo-and-ops.md)**: Regional/Dual/Multi-region quorum & failover (`changequorum`), RO replica non-DR gotcha, `default_leader`, Read Leases (4 gotchas), Managed vs Open-Source Autoscaler deployment & asymmetric RO caveats, 10-step `move-instance` runbook, Geo-Partitioning Preview & July 2026 routing metadata flip, PITR & incremental backup chains, Deletion Protection, Dataplex, IAM permission bundles, CMEK/EKM 7-day deletion rule, and Audit Logging caveats.

## Sibling Skill Routing (Out-of-Scope Tasks)

| If the task focuses on... | Route to sibling skill |
| :--- | :--- |
| Table/PK/interleaving, indexes, FKs, views (`SECURE_CONTEXT`), TTL, Locality Group or Change Streams DDL | `spanner-schema-and-data-modeling` |
| `CREATE PROPERTY GRAPH`, GQL traversals, view/schemaless graphs, Data Boost Graph Algorithms | `spanner-graph-modeling-and-gql` |
| Deep FTS tokenizers/dictionaries, ScaNN vector indexes, Vertex AI `ML.PREDICT`, RRF/GraphRAG | `spanner-search-ai-and-graphrag` |
| 7-stage migrations, SMT, Datastream/Dataflow CDC, Neo4j APOC/openCypher, Elasticsearch→FTS | `spanner-migration-and-neo4j-playbook` |
| MCP Toolbox `tools.yaml`, Antigravity/ADK MCP, FGAC `GRANT`/conditional IAM, Model Armor | `spanner-mcp-toolbox-and-agent-security` |
| Query Optimizer tuning, execution plans, `SPANNER_SYS` Insights, Key Visualizer, pre-splitting, CPU targets (65%/45%), TCO sizing | `spanner-performance-observability-and-sizing` |

## Official Documentation

- [Cloud Spanner Documentation](https://docs.cloud.google.com/spanner/docs)
- [Cloud Spanner Editions Overview](https://docs.cloud.google.com/spanner/docs/editions-overview)
- [Cloud Spanner Pricing](https://cloud.google.com/spanner/pricing)
- [Cloud Spanner Instance Configurations](https://docs.cloud.google.com/spanner/docs/instance-configurations)
- [Cloud Spanner Best-Practice Samples (GitHub)](https://github.com/GoogleCloudPlatform/spanner-bb-samples)
