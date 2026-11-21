---
trigger: always_on
description: "Always-on safety, regional residency, GoogleSQL dialect, and MCP security guardrails for the Cloud Spanner Enterprise SDLC Knowledge Graph workshop."
---

# Cloud Spanner Graph Workshop — Always-On Guardrails

Follow these non-negotiable safety, dialect, and security guardrails on every turn. Defer detailed DDL, GQL, search, MCP, and sizing patterns to [GEMINI.md](../../GEMINI.md) and `.agents/skills/*`.

## 1. Live Cloud, Secret & Workspace Safety

- **Require explicit confirmation before live GCP/Spanner mutations**: never run `make provision-gcp`, `make cleanup-gcp`, live pipeline writes (without `--dry-run`), DDL/DML updates, IAM/FGAC changes, or Cloud Run/Model Armor deployments without explicit user approval of target project, instance, database, and region.
- **Verify active GCP context first**: inspect `gcloud config get-value project`, `.env`, and `SPANNER_EMULATOR_HOST` before any live command so workshop actions never touch an unintended project.
- **Zero secrets in repository files**: never commit credentials, OAuth client secrets, or active static `apiKey` values into `tools.yaml`, `mcp_config.json`, scripts, or docs; keep `.env` untracked and use ADC / Cloud Run Workload Identity.
- **Keep lab edits inside `starter/`**: solve hands-on exercises exclusively in `starter/` (`15` `TODO(Lab 1–8)` files); do not alter `data/`, `solution/`, or expected fixtures unless explicitly requested.

## 2. Dialect, Edition, Region & Compute Floor

- **Dialect & Edition (`INV-01`)**: enforce **GoogleSQL** (`GOOGLE_STANDARD_SQL`) and **Cloud Spanner Enterprise Edition** (`ENTERPRISE`).
- **Regional residency**: default primary Spanner, Cloud Run, GCS, and Dataflow resources to Bangkok (`asia-southeast3` / `regional-asia-southeast3`); target Singapore (`asia-southeast1`) for Vertex AI, Model Armor (`modelarmor.asia-southeast1.rep.googleapis.com`), and cross-region backup DR.
- **Vertex AI endpoint rule**: never use `global` for `gemini-embedding-001` (regional `asia-southeast1` required; `global` fallback applies strictly to `gemini-2.5-flash` LLM synthesis).
- **Compute floor**: `100` PU (`--default-backup-schedule-type=NONE`) is workshop sandbox-only; production requires `>= 1,000` PUs (1 node).

## 3. Schema, Property Graph, FTS & Vector Guardrails (`INV-02`–`INV-09`)

- **Graph integrity (`INV-02`–`INV-04`)**: enforce unique element aliases in `CREATE PROPERTY GRAPH`, exact composite PK arity/type matching on `SOURCE KEY`/`DESTINATION KEY REFERENCES`, and graph-wide property type uniqueness.
- **View security & keys (`INV-05`)**: declare explicit `SQL SECURITY INVOKER` (or `DEFINER` for parameterized views) on every SQL view and explicit `KEY (...)` on view-backed graph elements; re-apply `GRANT SELECT ON VIEW` after any `CREATE OR REPLACE VIEW`.
- **Cycle-safe GQL paths**: use `MATCH TRAIL` (not default `WALK`) for variable-length graph traversals over cyclic dependencies such as `svc-orders <-> svc-notifications`.
- **No same-table dual cascade (`INV-06`)**: never combine `INTERLEAVE IN PARENT T ON DELETE CASCADE` with a self-referencing cascading FK to `T` on the same table (use standalone dual-FK, or `INTERLEAVE IN PARENT` + `NOT ENFORCED` FK for Graph Algorithms).
- **Dynamic graph isolation (`INV-07`)**: keep `DYNAMIC LABEL` tables inside `SdlcDynamicGraph`; never mix static node/edge tables with `DYNAMIC LABEL`.
- **Vector & ScaNN discipline (`INV-08`)**: use `ARRAY<FLOAT32>(vector_length=>768)`, require `WHERE embedding IS NOT NULL` on ScaNN vector indexes and `APPROX_COSINE_DISTANCE` queries, isolate ANN inside a single-table/single-node CTE (no general `JOIN` inside ANN `SELECT`), and never run ANN on edges.
- **Read-only FTS & no `ROW_NUMBER()` (`INV-09`)**: run FTS and hybrid queries in **read-only** transactions; compute RRF (`k=60`) with `UNNEST(ARRAY(SELECT ... ORDER BY ... LIMIT k)) AS x WITH OFFSET AS rank` (never `ROW_NUMBER()`).

## 4. MCP Toolbox & Agent Security Guardrails

- **Strict `tools.yaml` contract**: never add `databaseRole` or `database_role` to MCP Toolbox `type: spanner` sources (`project`, `instance`, `database`, `dialect` only), never expose raw `spanner-execute-sql` (`execute_sql`) or `templateParameters`, and set `readOnly: true` on all read tools.
- **Secretless regional embeddings**: bind `embeddingModels.gemini-embedder` to Vertex AI `project` + `location: ${VERTEX_AI_LOCATION}` (`asia-southeast1`) via ADC / Workload Identity — never enable a static `apiKey` in Cloud Run.
- **Dual-layer authorization**: separate Cloud Run MCP Toolbox DB-level IAM (`roles/spanner.databaseReader` / `databaseUser` + sliced persona YAMLs + OIDC `authServices` `@caller_email`) from dedicated `sa-fgac-*` accounts (`fineGrainedAccessUser` + conditional `databaseRoleUser`) for `database_role`-aware clients.

## 5. Defer Deep Guidance to `.agents/skills/*`

- **Platform Ops**: [spanner-enterprise-platform-ops](../skills/spanner-enterprise-platform-ops/SKILL.md)
- **Schema & Relational DDL**: [spanner-schema-and-data-modeling](../skills/spanner-schema-and-data-modeling/SKILL.md)
- **Property Graph & ISO GQL**: [spanner-graph-modeling-and-gql](../skills/spanner-graph-modeling-and-gql/SKILL.md)
- **FTS, Vector, AI & GraphRAG**: [spanner-search-ai-and-graphrag](../skills/spanner-search-ai-and-graphrag/SKILL.md)
- **Migrations & Neo4j Parity**: [spanner-migration-and-neo4j-playbook](../skills/spanner-migration-and-neo4j-playbook/SKILL.md)
- **MCP Toolbox & Security**: [spanner-mcp-toolbox-and-agent-security](../skills/spanner-mcp-toolbox-and-agent-security/SKILL.md)
- **Observability, Perf & TCO**: [spanner-performance-observability-and-sizing](../skills/spanner-performance-observability-and-sizing/SKILL.md)
- **Workshop Navigator (Acme Commerce)**: [sdlc-graphrag-workshop-guide](../skills/sdlc-graphrag-workshop-guide/SKILL.md)
