# Module 1: Migrating Neo4j Community Edition to Cloud Spanner Graph Enterprise

**Customer Requirement 1:** Migrate an existing **Neo4j Community Edition** SDLC knowledge graph to **Cloud Spanner Enterprise** without JVM heap OOMs during export, referential-integrity failures during bulk load, or silent query regressions when translating **openCypher v9** to **ISO GQL (`ISO/IEC 39075`)**.

- **Duration:** Track A (4h Express): `30 min` | Track B (8h Deep-Dive): `60 min`
- **Primary Antigravity Skill:** [`spanner-migration-and-neo4j-playbook`](../.agents/skills/spanner-migration-and-neo4j-playbook/SKILL.md)
- **Support Skills:** [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md), [`sdlc-graphrag-workshop-guide`](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md)
- **Hands-On Starter File:** [`starter/sql/09_opencypher_to_gql_cookbook.sql`](../starter/sql/09_opencypher_to_gql_cookbook.sql) (`TODO(Lab 1)`)
- **Reference Solution:** [`solution/sql/09_opencypher_to_gql_cookbook.sql`](../solution/sql/09_opencypher_to_gql_cookbook.sql), [`data/structured/`](../data/structured/README.md)

---

## 1. Why Enterprises Outgrow Neo4j Community Edition

Neo4j Community Edition (CE) is popular for early internal SDLC catalogs, but production platform teams quickly hit architectural ceilings:

| Capability | Neo4j Community Edition | Cloud Spanner Enterprise (Bangkok `regional-asia-southeast3`) |
| :--- | :--- | :--- |
| **High Availability & SLA** | Single-node only (no clustering, no automated failover) | 3-zone synchronous quorum (`asia-southeast3-a/b/c`), **99.99% SLA** |
| **Scale-Out & Memory** | Single JVM heap & vertical scaling ceiling | Horizontal split-based scale-out (up to 10 TiB/node, Managed Autoscaler) |
| **Security & RBAC** | No role-based access control (RBAC) in CE | IAM + SQL Fine-Grained Access Control (FGAC) + CMEK + VPC-SC + Audit Logs |
| **Multi-Model Consolidation** | Graph only — requires separate Vector DB & OpenSearch/ES | Unified Graph (ISO GQL) + Relational + Full-Text Search + ScaNN Vector Search in **one ACID commit** |
| **Backup & PITR** | Offline dumps only | Online backups, **7-day PITR**, cross-region backup copy DR to Singapore (`asia-southeast1`) |

---

## 2. The 4-Phase Neo4j CE → Spanner Graph Migration Playbook

1. **Phase 1 — Heap-Safe APOC Streaming Export (Never Export `id(n)`):**  
   Neo4j's internal integer `id(n)` is ephemeral and recycled after deletions. Always project stable business primary keys (`owner_id`, `service_id`, `api_id`, `adr_id`, `req_id`) via `apoc.export.csv.query` with `{ batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }`. `separateHeader: true` produces headerless data CSVs compatible directly with Cloud Spanner's Dataflow `GCS_Text_to_Cloud_Spanner` template.
2. **Phase 2 — Relational & Dual-Projected Target Design:**  
   Separate **5 static node tables** (`Owners`, `Services`, `Apis`, `Adrs`, `Requirements` = `68` rows), **6 explicit M:N edge tables** (`174` rows), and **4 folded N:1 FK columns** (`60` dual-projected edges = `234` total logical edges).
3. **Phase 3 — Strict Nodes-Before-Edges Bulk Load (Zero Indexes Pre-Load):**  
   Load **all 5 node tables** to completion first (`Owners -> Services -> Apis -> Adrs -> Requirements`), then load **all 6 explicit edge tables** second so interleaved parents and foreign keys exist. Create secondary, FTS, and ScaNN indexes **after** bulk loading (Module 3).
4. **Phase 4 — 4-Tier Parity Validation & openCypher → ISO GQL Translation:**  
   Validate global counts, per-label/edge counts, topological traps, and deterministic per-table `SHA256` / `BIT_XOR(FARM_FINGERPRINT(...))` row hashes (`solution/sql/10_validate_migration_parity.sql`).

---

## 3. Optional Local Neo4j 5 CE + APOC Export Walkthrough (Track B)

The repository already ships pre-exported canonical CSVs and JSONLs under `data/structured/` so **Docker is completely optional**. On Track B (8-hour deep-dive), run the live Neo4j CE export loop to verify byte-for-byte parity:

```bash
cd data/structured

# 1. Start Neo4j 5 Community Edition with APOC enabled and bounded 1G heap
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

Even without Docker, run the offline staging & parity validator from the repository root:

```bash
python3 data/structured/stage_apoc_export.py
```

---

## 4. High-Frequency openCypher v9 → Cloud Spanner ISO GQL Traps

Translating Neo4j queries naively causes subtle runtime errors or silent semantic bugs. Master these **7 critical differences** (all 10 full translations live in `solution/sql/09_opencypher_to_gql_cookbook.sql`):

| # | Neo4j openCypher v9 Pattern | Cloud Spanner ISO GQL Equivalent | Critical Trap Avoided |
| :-: | :--- | :--- | :--- |
| **1** | `MATCH (u)-[r:DEPENDS_ON*1..3]->(t)` | `MATCH TRAIL (u)-[deps:DEPENDS_ON]->{1,3}(t)` | **Cycle explosion!** Spanner GQL defaults to `WALK` (repeating edges on `svc-orders <-> svc-notifications`). `MATCH TRAIL` matches Neo4j edge-uniqueness. |
| **2** | `UNWIND ['svc-payments', ...] AS sid` + `{service_id: sid}` | `LET arr = [...] FOR sid IN arr MATCH (s:Service) WHERE s.service_id = sid` | `UNWIND` doesn't exist in ISO GQL; scalar `FOR`/`LET` vars must filter inside outer `WHERE`, **never** inside inline `{service_id: sid}`! |
| **3** | `RETURN s` (whole node/edge/path) | `RETURN SAFE_TO_JSON(s) AS service_node_json` | Returning raw `GRAPH_ELEMENT` fails; `SAFE_TO_JSON` handles `TOKENLIST` columns safely as JSON `null` (whereas `TO_JSON` errors on `TOKENLIST`). |
| **4** | `RETURN ... ORDER BY ... SKIP 2 LIMIT 5` | `RETURN ... ORDER BY ... OFFSET 2 LIMIT 5` | Standalone `ORDER BY` before `RETURN` is silently ignored; **`OFFSET` must precede `LIMIT`**; every expression requires an explicit `AS` alias. |
| **5** | `OPTIONAL MATCH (s)-[c]->(a) WHERE c.migration_status = 'AT_RISK'` | Keep `WHERE` attached to `OPTIONAL MATCH` (never post-join `FILTER`) | Writing `FILTER` after `OPTIONAL MATCH` turns the `LEFT OUTER JOIN` into an `INNER JOIN`, silently dropping unmatched services! |
| **6** | `size((s)<-[:DEPENDS_ON]-())` | `VALUE { MATCH (inner_s:Service)<-[:DEPENDS_ON]-(:Service) WHERE inner_s = s RETURN COUNT(*) }` | Never redeclare outer variable `s` inside a GQL subquery; bind fresh `inner_s` and correlate with `WHERE inner_s = s`. |
| **7** | `MERGE (a:Adr {adr_id: 'ADR-011'}) SET ... RETURN ...` | `INSERT OR UPDATE INTO Adrs (...) VALUES (...) THEN RETURN ...` | ISO GQL in Spanner is read-only; mutate backing tables via ACID GoogleSQL DML (`INSERT OR UPDATE`, `UPDATE ... WHERE pk IN { GRAPH ... } THEN RETURN`). |

---

## 5. Hands-On Exercise — Lab 1 (`starter/`)

Open [`starter/sql/09_opencypher_to_gql_cookbook.sql`](../starter/sql/09_opencypher_to_gql_cookbook.sql). You will find `TODO(Lab 1)` blocks for the 5 highest-impact Neo4j → Spanner ISO GQL translations:

1. **Cycle-safe upstream traversal (`MATCH TRAIL`)** targeting `svc-checkout` over `svc-orders <-> svc-notifications` (using `ARRAY_LENGTH(deps)`).
2. **Array unnesting (`FOR sid IN target_services`)** replacing `UNWIND` with an outer `WHERE s.service_id = sid` predicate.
3. **Whole-node serialization (`SAFE_TO_JSON(s)`)** returning `TIER_0` services safely with `TOKENLIST` columns.
4. **Pagination & aliasing (`RETURN ... ORDER BY ... OFFSET 2 LIMIT 5`)** with `UPPER(a.http_method) AS method_upper`.
5. **Preserving `LEFT OUTER JOIN` semantics (`OPTIONAL MATCH ... WHERE c.migration_status = 'AT_RISK'`)** so services with zero legacy calls stay in the result set.

### Antigravity IDE Prompt (Copy-Paste)

> *"Using the `spanner-migration-and-neo4j-playbook` skill, help me complete all `TODO(Lab 1)` openCypher-to-ISO-GQL translations in `starter/sql/09_opencypher_to_gql_cookbook.sql` against `SdlcGraph`. Explain how `MATCH TRAIL` avoids the `svc-orders <-> svc-notifications` cycle and why `OPTIONAL MATCH ... WHERE` differs from post-join `FILTER`. Do not edit `solution/`."*

### Verify Lab 1 Offline

```bash
# Confirm your starter/ edits pass every Lab 1 ISO GQL & staging check
make TRACK=starter verify-lab LAB=1

# Compare against the verified reference cookbook if stuck
diff -u starter/sql/09_opencypher_to_gql_cookbook.sql solution/sql/09_opencypher_to_gql_cookbook.sql
```

**Checkpoint Expected Output:** `[PASS] Lab 1 (starter/)` and `[OK] Verified 1/1 lab(s) on track=starter.`

---

**Navigation:** [← Module 0: Facilitator Guide](00_facilitator_guide.md) | [README](../README.md) | [Module 2: Two-Path SDLC Convergence →](02_two_path_sdlc_workshop.md)
