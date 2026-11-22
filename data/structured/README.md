# Path A — Structured SDLC Graph Corpus (Neo4j CE & APOC Export)

This directory contains the authoritative **Acme Commerce Platform** structured SDLC knowledge graph used across Labs 1–8. It includes both the pre-exported canonical CSV/JSONL files (so participants can run offline or directly into Cloud Spanner without running Docker) and a complete local **Neo4j 5 Community Edition + APOC** environment for practicing heap-safe graph exports.

## Dataset Inventory & Parity Counts

| Category | Files | Exact Row Count | Spanner Graph Mapping |
| :--- | :--- | ---: | :--- |
| **Nodes** | `nodes/owners.csv` (8), `services.csv` (12), `apis.csv` (24), `adrs.csv` (10), `requirements.csv` (14) | **68** | 5 static node tables (`Owners`, `Services`, `Apis`, `Adrs`, `Requirements`) |
| **Nodes JSONL** | `nodes/all_nodes.jsonl` | **68** | Combined JSONL snapshot (`label`, `id`, `properties`) |
| **Explicit M:N Edges** | `edges/service_calls_api.csv` (48), `service_depends_on_service.csv` (28), `adr_governs_api.csv` (36), `adr_supersedes_adr.csv` (3), `req_applies_to_api.csv` (33), `req_applies_to_service.csv` (26) | **174** | 6 explicit Spanner edge tables (4 interleaved heterogeneous + 2 standalone dual-FK self-referencing) |
| **Dual-Projected N:1 Edges** | `edges/api_exposes_from_service.csv` (24), `owner_owns_service.csv` (12), `owner_owns_adr.csv` (10), `owner_owns_req.csv` (14) | **60** | Folded as foreign-key columns on Spanner node tables and dual-projected in `CREATE PROPERTY GRAPH` |
| **All Logical Edges JSONL** | `edges/all_edges.jsonl` | **234** | Explicit (`174`) + Dual-Projected (`60`) edges for Neo4j parity & schemaless comparison |

## Locked Topological Invariants

1. **Intentional Cycle (`svc-orders <-> svc-notifications`)**: Two `ASYNC_EVENT` edges in `edges/service_depends_on_service.csv` prove why ISO GQL `MATCH TRAIL` is mandatory over default `WALK` mode.
2. **Isolated Async `DATA_FEED` Consumer (`svc-analytics`)**: Three `DATA_FEED` edges targeting `svc-orders`, `svc-ledger`, and `svc-inventory` separate non-blocking BI streams (`DATA_FEED_CONSUMER`) from synchronous OLTP outages.
3. **High-Fanin Identity Hub (`svc-auth` / `api-auth-token-v2`)**: Exactly 10 services depend on `svc-auth` and call `api-auth-token-v2`, anchoring `PageRank` criticality and `IS_FIRST` fanout capping.
4. **Legacy `AT_RISK` Payment Consumer (`svc-billing -> api-payments-charge-v1`)**: `svc-billing` still sends `85` RPS to `DEPRECATED` `POST /v1/payments/charge` (`migration_status = AT_RISK`).
5. **Hero API Contract (`api-payments-charge-v2`)**: Exactly 2 direct callers (`svc-checkout`, `svc-billing`), 7 deterministic blast-radius services, 5 governing ADRs (`ADR-004`, `ADR-005`, `ADR-006`, `ADR-008`, `ADR-010`), and 5 applicable requirements (`REQ-001`, `REQ-002`, `REQ-003`, `REQ-005`, `REQ-006`).

## Zero-Docker Neo4j CE & APOC Streaming Simulator (Default)

Docker Desktop is **100% optional**. Run the pure-stdlib simulator (`no_docker_neo4j_sim.py`) directly from the repository root to explore all `68` nodes, `234` edges, `WALK` vs `TRAIL` cycle explosion, JVM heap OOM trade-offs, and **15/15 SHA-256 APOC parity**:

```bash
# Full 4-section simulator + Lab 1 parity check (runs in < 2 seconds, zero Docker)
make lab1-no-docker

# Or invoke the simulator directly with named queries or explicit export staging
python3 data/structured/no_docker_neo4j_sim.py --demo
python3 data/structured/no_docker_neo4j_sim.py --list-queries
python3 data/structured/no_docker_neo4j_sim.py --query walk_vs_trail
python3 data/structured/no_docker_neo4j_sim.py --export-dir /tmp/acme-apoc-export --diff
```

## Optional With-Docker: Local Neo4j 5 CE + APOC Streaming Export (Track B)

```bash
cd data/structured

# 1. Start Neo4j 5 Community Edition with APOC enabled and 1G bounded heap
docker compose up -d

# 2. Seed all 68 nodes and 234 relationships idempotently
docker compose exec -T neo4j \
  cypher-shell -u neo4j -p acme-sdlc-workshop-2026 < seed_neo4j.cypher

# 3. Export headerless CSVs (separateHeader: true) + JSONL snapshot into ./export/
docker compose exec -T neo4j \
  cypher-shell -u neo4j -p acme-sdlc-workshop-2026 < export_neo4j_apoc.cypher

# 4. Validate referential integrity & diff the APOC export against canonical CSVs
python3 stage_apoc_export.py --diff-apoc-dir ./export
```

## Validating & Staging for Cloud Spanner Dataflow (`GCS_Text_to_Cloud_Spanner`)

Cloud Spanner's CSV Dataflow import template requires **headerless CSV files** and explicit non-generated column schemas in `manifest_nodes.json` (Stage 1) and `manifest_edges.json` (Stage 2):

```bash
# Offline validation & dry-run summary (no files written)
python3 data/structured/stage_apoc_export.py

# Emit headerless CSVs + Dataflow ImportManifest JSON files into a staging directory
python3 data/structured/stage_apoc_export.py \
  --output-dir /tmp/acme-spanner-staging \
  --gcs-prefix gs://YOUR_BUCKET_ASIA_SOUTHEAST3/neo4j-export
```

> Always execute Stage 1 (`manifest_nodes.json`) to `JOB_STATE_DONE` before launching Stage 2 (`manifest_edges.json`) so interleaved parent rows and foreign-key targets exist in Cloud Spanner before edge writes begin.
