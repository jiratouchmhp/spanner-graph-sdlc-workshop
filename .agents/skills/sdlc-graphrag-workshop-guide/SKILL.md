---
name: sdlc-graphrag-workshop-guide
metadata:
  version: "1.0.0"
  category: "Cloud Spanner Enterprise"
description: >-
  Navigates the Acme Commerce SDLC Knowledge Graph workshop repository, hero blast-radius contract, and lab checkpoints. Use when checking Acme Commerce entity/edge counts (68 nodes, 174 explicit rows, 60 dual-projected), Hero API api-payments-charge-v2 7-row blast radius/5 ADRs/5 REQs/citations, starter/ TODO(Lab 1-8) exercises, or Makefile verification targets. Don't use for general reusable Cloud Spanner production guidance outside this workshop — route to spanner-enterprise-platform-ops, spanner-schema-and-data-modeling, spanner-graph-modeling-and-gql, spanner-search-ai-and-graphrag, spanner-migration-and-neo4j-playbook, spanner-mcp-toolbox-and-agent-security, or spanner-performance-observability-and-sizing.
---

# SDLC Knowledge Graph & GraphRAG Workshop Guide (Acme Commerce)

Workshop-specific navigator and verification cheatsheet for the **Acme Commerce Platform** Cloud Spanner Enterprise SDLC Knowledge Graph & GraphRAG workshop (**Use Case 4**: consolidating Neo4j Community Edition + a separate Vector Database + a separate Keyword Search cluster into one ACID **Cloud Spanner Enterprise** engine).

This skill is the **only** workshop-coupled skill in `.agents/skills/`. It holds Acme Commerce entity IDs, the locked Hero blast-radius contract, `starter/` → `solution/` lab checkpoints, and `Makefile` verification commands. Route all general, reusable Cloud Spanner engineering topics to the 7 production sibling skills listed at the bottom of this file.

## Mandatory Workshop Safety & Confirmation Guardrails

1. **Require explicit user confirmation before any live GCP or remote Spanner mutation**: never run `make provision-gcp`, `make cleanup-gcp`, `scripts/provision_workshop_env.sh`, live `solution/pipelines/*.py` (without `--dry-run`), `solution/pipelines/dataflow_migration.sh`, `solution/mcp/iam_and_model_armor_setup.sh`, or `gcloud spanner ... ddl update|execute-sql` writes until the user explicitly confirms the target project and instance.
2. **Verify active GCP context before live commands**: inspect `gcloud config get-value project`, `.env` (`SPANNER_REGION`, `SPANNER_INSTANCE`, `SPANNER_DATABASE`, `VERTEX_AI_LOCATION`), and `env | grep SPANNER_EMULATOR_HOST` so workshop sandbox actions never touch an unrelated production project.
3. **Keep `solution/` and `data/` intact during participant labs**: solve hands-on exercises exclusively inside `starter/` (`15` `TODO(Lab 1-8)` files). Modifying `data/structured/` edges or `solution/` fixtures breaks deterministic Hero blast-radius parity (`7` rows) and automated tests.
4. **Never commit secrets or active static API keys**: keep `.env` untracked, and configure MCP Toolbox `embeddingModels.gemini-embedder` using regional Vertex AI (`project` + `location: ${VERTEX_AI_LOCATION}`) via Application Default Credentials / Cloud Run Workload Identity — never an active static `apiKey` in Cloud Run.
5. **Confirm before teardown or backup deletion**: `make cleanup-gcp` permanently deletes the workshop instance `sdlc-kg-enterprise`, database `sdlc_knowledge_graph`, and any same-region Bangkok backups.

## Locked Workshop Defaults & Non-Negotiable Invariants

### Locked Environment & Regional Architecture (`WORKSHOP_SPEC.json`)

| Setting | Locked Workshop Default | Production / Architecture Rule |
| :--- | :--- | :--- |
| **Fictional Enterprise** | Acme Commerce Platform | 68 nodes, 174 explicit edge rows, 60 dual-projected edges (234 logical), 45 Markdown docs |
| **Primary & ONLY DB/App Region** | Bangkok (`asia-southeast3` / `regional-asia-southeast3`) | Single Cloud Spanner **ENTERPRISE** instance (3-zone sync quorum `asia-southeast3-a/b/c`, 99.99% SLA, Thailand PDPA residency), Cloud Run MCP Toolbox, user-managed Secret Manager, GCS staging & Dataflow, same-region PITR/backups |
| **Companion Region (Services Unavailable in Bangkok)** | Singapore (`asia-southeast1`, ~25 ms RTT) | Vertex AI (`gemini-embedding-001` & `gemini-2.5-flash`), Model Armor (`modelarmor.asia-southeast1.rep.googleapis.com`), Cloud DLP / Sensitive Data Protection — companion services unavailable in Bangkok ONLY (no Singapore Spanner instance) |
| **Vertex AI Endpoint Rule** | `VERTEX_AI_LOCATION=asia-southeast1` | `global` fallback applies **strictly** to `gemini-2.5-flash` LLM synthesis — **NEVER** `global` for `gemini-embedding-001` |
| **Instance / DB / Dialect** | `sdlc-kg-enterprise` / `sdlc_knowledge_graph` / `GOOGLE_STANDARD_SQL` | GoogleSQL is mandatory for Spanner Graph (GQL), `CREATE MODEL`, and `gcloud spanner cli` |
| **Compute Sizing Floor** | Workshop sandbox: `100` PU (`$1.0332` / 8 hr compute, `~$1.15` total) | Production floor: `>= 1,000` PUs (`>= 1` node) for dedicated zone tasks, Managed Autoscaler, and Remote Functions |
| **Backup Schedule Default** | Workshop: `--default-backup-schedule-type=NONE` | Production: same-region Bangkok `AUTOMATIC` incremental backups + 7-day PITR + `--enable-drop-protection` |
| **Hero Target API** | `api-payments-charge-v2` (`POST /v2/payments/charge`) | Exposed by `svc-payments` (`team-payments`); deterministic **7** impacted services, **5** ADRs, **5** REQs |
| **Live-Verified Timings** | Bangkok (`asia-southeast3`, 100 PU) + Singapore (`asia-southeast1`) | Create ~2m · Path A <5s · Path B + 152 SG embeddings ~22s · `apply-postload` ~10m (start before break!) · Hero/SQL/GQL 33–520 ms · Data Boost 3 algos ~3m · Lab 6 deploy ~3–4m · `cleanup-all-gcp` ~2m |

### The 9 Workshop Spanner Invariants (`INV-01`–`INV-09`)

1. **`INV-01` (Edition & Dialect)**: `GOOGLE_STANDARD_SQL` + Cloud Spanner `ENTERPRISE` only (unlocks Spanner Graph, FTS, ScaNN Vector Search, Locality Groups, Managed Autoscaler).
2. **`INV-02` (Unique Graph Aliases)**: Every node and edge table in `CREATE PROPERTY GRAPH SdlcGraph` has a unique alias; dual-projected node tables declare explicit `AS` edge aliases (`ExposesApi`, `TeamOwnsService`, `TeamOwnsAdr`, `TeamOwnsReq`).
3. **`INV-03` (Full PK Reference Parity)**: Every `SOURCE KEY` / `DESTINATION KEY REFERENCES` targets the exact node alias and matches full composite primary key arity and SQL types.
4. **`INV-04` (Graph-Wide Property Type Consistency)**: No exposed property name has conflicting SQL types across any node or edge element inside the same property graph.
5. **`INV-05` (Explicit View Security & Graph Keys)**: Every SQL view (`ChunkCites*View`, `PublicApiCatalogView`, `SecurityComplianceImpactView`) declares explicit `SQL SECURITY INVOKER` (or `DEFINER` for `TeamScopedAssetsView`), and every view-backed graph element declares explicit `KEY (...)`.
6. **`INV-06` (No Same-Table Dual Cascade)**: Never combine `INTERLEAVE IN PARENT T ON DELETE CASCADE` with a self-referencing cascading `FOREIGN KEY REFERENCES T ON DELETE CASCADE` on the same table. Use standalone dual-FK for `ServiceDependsOnService` / `AdrSupersedesAdr`, or `INTERLEAVE IN PARENT Services ON DELETE CASCADE` + `NOT ENFORCED` destination FK for `ServiceDependsOnAlgo` (Lab 4 Data Boost algorithms).
7. **`INV-07` (Isolated Dynamic Graph)**: Schemaless `GraphNode`/`GraphEdge` tables live in a separate graph (`SdlcDynamicGraph`) — never mix static tables with `DYNAMIC LABEL`; enforce lowercase labels/keys via `CHECK` and expose `PROPERTIES ALL COLUMNS EXCEPT (properties)`.
8. **`INV-08` (Vector & ScaNN Discipline)**: Embeddings use `ARRAY<FLOAT32>(vector_length=>768)`. Both `CREATE VECTOR INDEX` and `APPROX_COSINE_DISTANCE` queries MUST filter `WHERE embedding IS NOT NULL`, isolate ANN inside a single-table/single-node CTE with no general `JOIN` inside the ANN block, and never run ANN on edges.
9. **`INV-09` (No `ROW_NUMBER()`, Read-Only FTS, Regional Embeddings)**: Cloud Spanner lacks general `ROW_NUMBER()` — compute RRF (`k=60`) via `UNNEST(ARRAY(SELECT ... ORDER BY ... LIMIT k)) AS x WITH OFFSET AS rank`. Run FTS queries in read-only transactions, and bind `CREATE MODEL` for `gemini-embedding-001` to regional `asia-southeast1`.

## Workshop Decision Matrix

| What You Need to Do | Exact File / Command | Deep Reference / Sibling Skill |
| :--- | :--- | :--- |
| Check Acme entity/edge counts, schema, or topological traps | `WORKSHOP_SPEC.json`, `data/structured/` | [acme-sdlc-domain-and-hero-contract.md](references/acme-sdlc-domain-and-hero-contract.md) |
| Verify the 7-row Hero API blast radius, 5 ADRs, 5 REQs, citations | `solution/pipelines/hero_blast_radius_offline.py --check-fixture solution/sql/fixtures/07_hero_expected_rows.json` | [acme-sdlc-domain-and-hero-contract.md](references/acme-sdlc-domain-and-hero-contract.md) |
| Check workstation readiness or run Zero-Docker Lab 1 simulator | `make preflight` and `make lab1-no-docker` (`data/structured/no_docker_neo4j_sim.py`) | [lab-checkpoints-and-troubleshooting.md](references/lab-checkpoints-and-troubleshooting.md) |
| Solve or verify a `starter/` `TODO(Lab 1-8)` exercise | `make TRACK=starter verify-lab LAB=<1..8>` *(or `MODE=express` for 4h Track A)* | [lab-checkpoints-and-troubleshooting.md](references/lab-checkpoints-and-troubleshooting.md) |
| Confirm starter stubs fail while reference solution passes | `make verify-starter-stubs` and `make TRACK=solution verify-lab LAB=all` | [lab-checkpoints-and-troubleshooting.md](references/lab-checkpoints-and-troubleshooting.md) |
| Run full 8-module offline test suite and skill validation | `make test` and `make validate-skills` | [lab-checkpoints-and-troubleshooting.md](references/lab-checkpoints-and-troubleshooting.md) |
| Test Lab 6 MCP persona RBAC & prompt-injection fail-closed | `make demo-mcp-mock` (`solution/mcp/adk_multi_agent_demo.py --mock`) | [acme-sdlc-domain-and-hero-contract.md](references/acme-sdlc-domain-and-hero-contract.md) |
| Model Bangkok Enterprise sizing, CUDs, HDD tiering & 3-store TCO | `make sizing PRESET=all REGION=asia-southeast3` or `WORKSHEET=starter/tools/customer_scale_worksheet.json` | [acme-sdlc-domain-and-hero-contract.md](references/acme-sdlc-domain-and-hero-contract.md) |
| Debug workshop SQL, GQL, ScaNN, MCP IAM, or teardown errors | Check symptom → root cause → exact fix | [lab-checkpoints-and-troubleshooting.md](references/lab-checkpoints-and-troubleshooting.md) |
| Answer general Cloud Spanner design, GQL, Search, or Ops questions | Outside Acme Commerce scope | Route to the matching `spanner-*` sibling skill below |

## Runnable Quickstart Workflows

### 1. Offline Lab Verification Loop (`starter/` → `solution/`)

```bash
# Inspect all 16 Makefile targets and run fast 5-second Zero-Docker preflight
make help
make preflight
make lab1-no-docker

# 1. Confirm all 15 starter stubs across Labs 1-8 are properly armed
make verify-starter-stubs

# 2. After completing TODO(Lab N) inside starter/, verify that specific lab
make TRACK=starter verify-lab LAB=1                  # Full Track B (default MODE=full)
make TRACK=starter MODE=express verify-lab LAB=1     # Express Track A (core subset)

# 3. Verify the complete reference implementation and all 8 offline test suites
make TRACK=solution verify-lab LAB=all
make test
make validate-skills
```

### 2. Offline Dry-Runs: Pipelines, Hero Replay, MCP RBAC & Bangkok TCO

```bash
# Dry-run Path A (68 nodes + 174 explicit edge rows) and Path B (45 Markdown docs) loaders
make dry-run-pipelines

# Replay Hero Blast-Radius GraphRAG offline and assert exact match against the 7-row fixture
python3 solution/pipelines/hero_blast_radius_offline.py \
  --api-id api-payments-charge-v2 \
  --check-fixture solution/sql/fixtures/07_hero_expected_rows.json

# Verify Lab 6 isolated persona YAMLs and OIDC prompt-injection fail-closed behavior offline
make demo-mcp-mock

# Run verified 2026 Bangkok Enterprise sizing presets + Lab 7 customer worksheet
make sizing PRESET=all REGION=asia-southeast3
make sizing WORKSHEET=starter/tools/customer_scale_worksheet.json REGION=asia-southeast3
```

### 3. Live Bangkok GCP Sandbox Lifecycle (Confirm with User First!)

```bash
# 0. Confirm project/region and clean ADC quota-project before live commands
gcloud config get-value project
gcloud auth application-default set-quota-project "$GOOGLE_CLOUD_PROJECT"
export SPANNER_DISABLE_BUILTIN_METRICS=true   # Optional: silences noisy OpenTelemetry metrics logs

# 1. Provision Bangkok 100-PU Enterprise sandbox (--default-backup-schedule-type=NONE)
make provision-gcp

# 2. Run live Hero Queries (A = Exact KNN, B = CTE-isolated ScaNN ANN, C = SdlcDynamicGraph)
python3 labs/run_hero_query.py --query B --api-id api-payments-charge-v2

# 3. Verify live Spanner smoke checks and FGAC role enforcement
make verify-live

# 4. Clean teardown (single Bangkok Spanner instance + all companion resources)
make cleanup-gcp          # Bangkok Spanner instance & same-region backups only
make cleanup-all-gcp      # Full teardown (BKK Cloud Run MCP/Secrets/GCS/Spanner + IAM + SG DLP/Model Armor) + zero-residual audit
```

## Workshop Repository Quick Map

- **Source of Truth & Plans**: `WORKSHOP_SPEC.json`, `WORKSHOP_CONTENT_PLAN.md`, `README.md`, `GEMINI.md`, `Makefile`, `.env.example`.
- **Shared Corpus (`data/`)**: `data/structured/` (Zero-Docker simulator `no_docker_neo4j_sim.py`, optional Neo4j CE Docker + APOC Cypher + `stage_apoc_export.py` + 5 node CSVs + 10 edge CSVs + JSONLs) and `data/unstructured/` (45 Markdown SDLC docs).
- **Hands-On Workspace (`starter/`)**: `starter/README.md` + 15 exercise files with `TODO(Lab 1-8)` markers across `starter/sql/` (10), `starter/pipelines/` (2), `starter/mcp/` (2), and `starter/tools/customer_scale_worksheet.json` (1).
- **Reference Implementation (`solution/`)**: `solution/sql/` (`01`–`11`, `03a`–`03d`, `04b`, `fixtures/`), `solution/pipelines/` (5 scripts), `solution/mcp/` (`tools.yaml`, 3 rendered `personas/*.yaml`, IAM/Model Armor script, ADK demo), `solution/tools/sizing_calculator.py`.
- **Guides, Scripts & Tests**: `docs/00_facilitator_guide.md` through `docs/08_observability_and_troubleshooting.md`, `labs/run_hero_query.py`, `scripts/{preflight_check.sh,provision_workshop_env.sh,run_live_sql.py,setup_antigravity_skills.sh,verify_lab.py,verify_live_gcp.sh,verify_zero_residual.sh}`, `tests/` (8 stdlib unittest modules), `.antigravity/mcp_config.json`.

## Reference Deep-Dives

Read the self-contained reference matching your immediate workshop task:

- **[Acme SDLC Domain & Hero Contract](references/acme-sdlc-domain-and-hero-contract.md)**: Complete 68-node / 174-explicit / 60-dual-projected dataset catalog, locked 28-edge dependency graph & 5 topological test traps, exact 7-row Hero `api-payments-charge-v2` blast-radius contract (5 ADRs, 5 REQs, 9 verifiable Markdown citations), three property graph designs (`SdlcGraph`, `SdlcDynamicGraph`, `SdlcAlgoGraph`), dual-layer MCP Toolbox vs FGAC persona RBAC matrix, and verified 2026 Bangkok Enterprise pricing & TCO presets.
- **[Lab Checkpoints & Troubleshooting](references/lab-checkpoints-and-troubleshooting.md)**: Track A (4-hour) vs Track B (8-hour) delivery matrix, all 15 `starter/` → `solution/` `TODO(Lab 1-8)` checkpoints with exact `scripts/verify_lab.py` assertions, `Makefile` & 8-module `tests/` catalog, live Bangkok + Singapore companion execution timings, and a 25-item symptom → root-cause → fix troubleshooting runbook covering all live-verified findings.

## Routing to the 7 Production Cloud Spanner Skills

Whenever a question goes beyond Acme Commerce workshop specifics into general Cloud Spanner architecture, syntax, or operations, hand off to the corresponding reusable sibling skill:

| If the task focuses on... | Route to Production Sibling Skill | Primary Workshop Lab(s) |
| :--- | :--- | :---: |
| Editions, CUDs, regional/multi-region topologies, dialects, KV, Queues, CLI/SDKs, Emulator, autoscaler deployment, PITR/backups | `spanner-enterprise-platform-ops` | Labs 2, 7, 8 |
| Split-safe PKs, `INTERLEAVE IN PARENT` vs `INTERLEAVE IN`, secondary/covering/expression indexes, FKs (dual-cascade ban), `CREATE VIEW`/`SECURE_CONTEXT()`, Locality Groups, TTL, Change Streams DDL | `spanner-schema-and-data-modeling` | Lab 3 |
| `CREATE PROPERTY GRAPH`, 8 graph invariants, dual-projected edges, view-backed & `DYNAMIC LABEL` graphs, ISO GQL (`TRAIL`, `IS_FIRST`, `factorized_mode`), Data Boost Graph Algorithms, `SAFE_TO_JSON` Studio viz | `spanner-graph-modeling-and-gql` | Labs 3, 4, 5, 8 |
| Vertex AI `CREATE MODEL` + `ML.PREDICT` embeddings, PDML backfills, FTS (`SEARCH`, Custom Dictionaries, fuzzy n-grams), Exact KNN, ScaNN `CREATE VECTOR INDEX`, RRF/RSF/Filtered Hybrid Search, GraphRAG | `spanner-search-ai-and-graphrag` | Labs 4, 5 |
| 7-stage migrations, SMT, Datastream/Dataflow CDC, Neo4j CE APOC export & nodes-before-edges loading, openCypher v9 → ISO GQL translation, Elasticsearch → Spanner FTS | `spanner-migration-and-neo4j-playbook` | Lab 1 |
| Antigravity MCP Store/`mcp_config.json`, MCP Toolbox `tools.yaml` (`type: spanner`, Vertex AI `embeddingModels`, OIDC `authServices`, persona isolation), FGAC `GRANT`/conditional `databaseRoleUser` IAM, Model Armor | `spanner-mcp-toolbox-and-agent-security` | Lab 6 |
| Query Optimizer versions/`ANALYZE`, execution plans, `SPANNER_SYS` Insights (Query/Lock/Txn/Read), Key Visualizer, pre-splitting, CPU targets (65%/45%), node sizing & 3-store TCO economics | `spanner-performance-observability-and-sizing` | Labs 7, 8 |

## Official Public Documentation

- [Cloud Spanner Documentation](https://docs.cloud.google.com/spanner/docs)
- [Spanner Graph Overview](https://docs.cloud.google.com/spanner/docs/graph/overview)
- [Full-Text Search in Cloud Spanner](https://docs.cloud.google.com/spanner/docs/full-text-search)
- [Vector Search (KNN & ScaNN ANN)](https://docs.cloud.google.com/spanner/docs/find-approximate-nearest-neighbors)
- [Fine-Grained Access Control (FGAC)](https://docs.cloud.google.com/spanner/docs/fgac-about)
- [Cloud Spanner Pricing](https://cloud.google.com/spanner/pricing)
- [MCP Toolbox for Databases](https://mcp-toolbox.dev/)
- [Cloud Spanner Best-Practice Samples (GitHub)](https://github.com/GoogleCloudPlatform/spanner-bb-samples)
