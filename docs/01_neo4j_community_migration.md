# Lab 1: Migrating Neo4j Community Edition to Spanner Graph

In this lab, you'll run a local Neo4j Community Edition + APOC export simulation (no Docker needed) and translate 5 real openCypher queries into Cloud Spanner ISO GQL (`ISO/IEC 39075`). By the end, you'll have a verified [`starter/sql/09_opencypher_to_gql_cookbook.sql`](../starter/sql/09_opencypher_to_gql_cookbook.sql) ready to run against your Bangkok (`asia-southeast3`) Cloud Spanner Enterprise database.

## What You'll Work On

| Step | File to open (`starter/`) | Where to edit (`TODO` / line) | Track |
| :-: | :--- | :--- | :-: |
| 1 | No file edits — runs [`data/structured/no_docker_neo4j_sim.py`](../data/structured/no_docker_neo4j_sim.py) | Terminal command (`make lab1-no-docker`) | Track A & B |
| 2 | [`starter/sql/09_opencypher_to_gql_cookbook.sql`](../starter/sql/09_opencypher_to_gql_cookbook.sql) | Exercises 1–3 (`L34`, `L51`, `L68`) | Track A & B |
| 3 | [`starter/sql/09_opencypher_to_gql_cookbook.sql`](../starter/sql/09_opencypher_to_gql_cookbook.sql) | Exercises 4–5 (`L88`, `L107`) | Track B (optional on Track A) |

- **Estimated time:** ~30 min (Track A Express) / ~60 min (Track B Full)
- **Reference copy (read-only if stuck):** [`solution/sql/09_opencypher_to_gql_cookbook.sql`](../solution/sql/09_opencypher_to_gql_cookbook.sql)
- **Antigravity skill:** [`spanner-migration-and-neo4j-playbook`](../.agents/skills/spanner-migration-and-neo4j-playbook/SKILL.md) (support: [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md), [`sdlc-graphrag-workshop-guide`](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md))

---

## Quick Background

Neo4j Community Edition works well for a small catalog, but production teams run into hard limits: single-node availability, one JVM heap, no built-in RBAC, and separate clusters for keyword and vector search. Moving to Cloud Spanner Enterprise in Bangkok (`regional-asia-southeast3`) puts graph, relational, full-text, and vector workloads in a single database with 3-zone synchronous HA (99.99% SLA), same-region backups, and ACID transactions.

A typical migration follows 7 stages: assess the source graph -> stream-export with APOC (`batchSize: 5000`, `separateHeader: true`, projecting business IDs instead of Neo4j's ephemeral `id(n)`) -> deploy base tables with zero secondary indexes -> bulk-load nodes before edges -> build indexes -> run count/hash parity checks -> shadow-read and cut over.

The biggest query gotchas when translating openCypher v9 to Spanner ISO GQL:

| Neo4j openCypher v9 | Cloud Spanner ISO GQL | Why it matters |
| :--- | :--- | :--- |
| `MATCH (u)-[r:DEPENDS_ON*1..3]->(t)` | `MATCH TRAIL (u)-[deps:DEPENDS_ON]->{1,3}(t)` | Spanner defaults to `WALK` (repeating edges on cycles like `svc-orders <-> svc-notifications`); `TRAIL` matches Neo4j edge-uniqueness. |
| `UNWIND [...] AS sid MATCH (s {service_id: sid})` | `LET arr = [...] FOR sid IN arr MATCH (s:Service) WHERE s.service_id = sid` | ISO GQL replaces `UNWIND` with `FOR`, and scalar `LET`/`FOR` variables must go in outer `WHERE`, not inline `{...}`. |
| `RETURN s` | `RETURN SAFE_TO_JSON(s) AS service_node_json` | Raw `GRAPH_ELEMENT` returns fail, and `TO_JSON` errors on `TOKENLIST` columns; `SAFE_TO_JSON` maps them to JSON `null`. |
| `RETURN ... ORDER BY ... SKIP 2 LIMIT 5` | `RETURN ... ORDER BY ... OFFSET 2 LIMIT 5` | Attach `ORDER BY` to `RETURN`, put `OFFSET` *before* `LIMIT` (opposite of SQL), and alias every expression with `AS`. |
| `OPTIONAL MATCH ... WHERE ...` | Keep `WHERE` on `OPTIONAL MATCH` (never post-join `FILTER`) | A trailing `FILTER` runs after the join and turns a left join into an inner join, dropping unmatched rows. |

*(Note: Spanner ISO GQL is read-only. For writes like Cypher `MERGE` or `SET`, run standard GoogleSQL DML such as `INSERT OR UPDATE ... THEN RETURN` against the backing tables.)*

---

## Step-by-Step Hands-On

### Step 1: Run the Zero-Docker Neo4j & APOC export simulator (Track A & B)

You don't need Docker Desktop for this lab. From the repo root, run the preflight check and the pure-Python simulator:

```bash
make preflight && make lab1-no-docker
```

Here's what the simulator runs in under 2 seconds:
1. Loads the Acme Commerce source graph (`68` nodes, `174` explicit M:N edges, and `60` dual-projected N:1 edges = `234` logical edges).
2. Compares default `WALK` against `MATCH TRAIL` upstream from `svc-checkout` so you can see the `svc-orders <-> svc-notifications` cycle blow up under `WALK`.
3. Replays the `api-payments-charge-v2` blast radius and contrasts `OPTIONAL MATCH ... WHERE` against post-join `FILTER`.
4. Streams headerless APOC CSVs (`separateHeader: true`) and checks SHA-256 parity against the canonical exports (`15/15` match).

You can also inspect individual queries directly:

```bash
python3 data/structured/no_docker_neo4j_sim.py --list-queries
python3 data/structured/no_docker_neo4j_sim.py --query walk_vs_trail
```

<details>
<summary>Optional (Track B with Docker): Run a real local Neo4j 5 CE container</summary>

If you have Docker Desktop installed and want to seed and export from an actual Neo4j 5 CE container using [`data/structured/docker-compose.yml`](../data/structured/docker-compose.yml):

```bash
cd data/structured
docker compose up -d
docker compose exec -T neo4j cypher-shell -u neo4j -p acme-sdlc-workshop-2026 < seed_neo4j.cypher
docker compose exec -T neo4j cypher-shell -u neo4j -p acme-sdlc-workshop-2026 < export_neo4j_apoc.cypher
python3 stage_apoc_export.py --diff-apoc-dir ./export
```

</details>

### Step 2: Complete Exercises 1–3 in `starter/sql/09_opencypher_to_gql_cookbook.sql` (Track A & B)

Open [`starter/sql/09_opencypher_to_gql_cookbook.sql`](../starter/sql/09_opencypher_to_gql_cookbook.sql). Every query starts with `GRAPH SdlcGraph`.

1. **Exercise 1 — Cycle-safe upstream impact (`L34`):**
   Replace the `TODO(Lab 1)` block with a `MATCH TRAIL` query traversing `DEPENDS_ON` 1 to 3 hops upstream into `target:Service {service_id: 'svc-checkout'}`. Return `DISTINCT upstream.service_id AS upstream_service_id` and `ARRAY_LENGTH(deps) AS hops`, ordered by `hops ASC, upstream_service_id ASC`.
2. **Exercise 2 — Unnesting arrays (`L51`):**
   Replace the `TODO(Lab 1)` block using `LET target_services = ['svc-payments', 'svc-auth', 'svc-ledger']` and `FOR sid IN target_services`. Match `(s:Service)-[:EXPOSES]->(a:Api)` and filter with an outer `WHERE s.service_id = sid` clause (not inline inside `{...}`).
3. **Exercise 3 — Returning whole nodes safely (`L68`):**
   Replace the `TODO(Lab 1)` block to match `TIER_0` services and return `s.service_id AS service_id` plus `SAFE_TO_JSON(s) AS service_node_json`, ordered by `service_id ASC` with `LIMIT 5`.

*(Optional Antigravity prompt)*:
> "Can you walk me through Exercises 1–3 in `starter/sql/09_opencypher_to_gql_cookbook.sql` using the `spanner-migration-and-neo4j-playbook` skill? Show me the `MATCH TRAIL`, `FOR`, and `SAFE_TO_JSON` queries against `SdlcGraph` without touching `solution/`."

Quick check (Track A Express scope):

```bash
make TRACK=starter MODE=express verify-lab LAB=1
```

### Step 3: Complete Exercises 4–5 in `starter/sql/09_opencypher_to_gql_cookbook.sql` (Track B — optional on Track A)

Skip this step if you're on Track A Express.

1. **Exercise 4 — Pagination and aliasing (`L88`):**
   Replace the `TODO(Lab 1)` block to match `(a:Api)` and return `a.api_id AS api_id`, `a.latency_budget_ms AS latency_budget_ms`, and `UPPER(a.http_method) AS method_upper`. Attach `ORDER BY latency_budget_ms ASC, api_id ASC`, then place `OFFSET 2` **before** `LIMIT 5`.
2. **Exercise 5 — Preserving left-outer join semantics (`L107`):**
   Replace the `TODO(Lab 1)` block to match all `(s:Service)` nodes, followed by `OPTIONAL MATCH (s)-[c:CALLS_API]->(a:Api) WHERE c.migration_status = 'AT_RISK'`. Return `service_id`, `a.api_id AS at_risk_api_id`, and `c.rps_estimate AS legacy_rps` ordered by `service_id ASC`. All 12 services stay in the result set, with only `svc-billing` showing its legacy `AT_RISK` call.

*(Optional Antigravity prompt)*:
> "Help me finish Exercises 4 and 5 in `starter/sql/09_opencypher_to_gql_cookbook.sql` — specifically `OFFSET` before `LIMIT` and keeping `WHERE` inside `OPTIONAL MATCH` so all 12 services stay in the output. Don't edit `solution/`."

### Step 4 (Optional — Live Bangkok GCP): Execute your cookbook live

Once you've provisioned and loaded your Bangkok (`asia-southeast3`) Spanner database in Lab 3, you can run your completed cookbook directly against Spanner:

```bash
# Run your starter/ cookbook live against Bangkok Spanner
python3 scripts/run_live_sql.py 09 --track starter

# Quick reference shortcut using solution/
python3 scripts/run_live_sql.py 09 --track solution
```

---

## Verify This Lab

Run the offline lab verifier from the repository root:

```bash
# Track B (Full — verifies all 5 queries + APOC export parity)
make TRACK=starter verify-lab LAB=1

# Track A (Express — verifies Exercises 1–3 + APOC export parity)
make TRACK=starter MODE=express verify-lab LAB=1
```

Passing output looks like this:

```text
[PASS] Lab 1 (starter/, mode=full)
    + Verified all 5 ISO GQL translations in starter/sql/09_opencypher_to_gql_cookbook.sql
    + Path A Neo4j APOC export staging & parity validated
[OK] Verified 1/1 lab(s) on track=starter (mode=full).
```

**If it fails, check:**
- **Leftover `TODO(Lab 1)` comments:** Delete or overwrite the `-- TODO(Lab 1)` marker lines in your active track's exercises; the verifier rejects any remaining markers.
- **Commented-out SQL:** The verifier strips `--` comments before checking for `GRAPH SdlcGraph`, `MATCH TRAIL`, `FOR ... IN`, `SAFE_TO_JSON(`, `ORDER BY ... LIMIT`, and `OPTIONAL MATCH ... WHERE`.
- **Stuck on syntax?** Compare your file side-by-side with the reference: `diff -u starter/sql/09_opencypher_to_gql_cookbook.sql solution/sql/09_opencypher_to_gql_cookbook.sql`.

---

**Previous / Next:** [← Facilitator Guide](00_facilitator_guide.md) | [Starter Checklist](../starter/README.md) | [Lab 2: Two-Path SDLC Corpus & Regional Setup →](02_two_path_sdlc_workshop.md)
