# Lab 2: Two-Path SDLC Corpus & Bangkok Regional Architecture

In this lab, you'll explore Acme Commerce's combined structured graph (Path A) and unstructured Markdown corpus (Path B), see how our Bangkok single-region Spanner architecture fits with Singapore AI/security services, and build a 4-tier parity validation SQL suite in [`starter/sql/10_validate_migration_parity.sql`](../starter/sql/10_validate_migration_parity.sql). By the end, you'll have an automated check proving zero data drift between Neo4j exports, Markdown docs, and Cloud Spanner.

## What You'll Work On

| Step | File to open (`starter/`) | Where to edit (`TODO` / line) | Track |
| :-: | :--- | :--- | :-: |
| 1 | Explore [`data/structured/`](../data/structured/README.md) & [`data/unstructured/adrs/ADR-004.md`](../data/unstructured/adrs/ADR-004.md) | Read sample CSVs & Markdown docs; run `stage_apoc_export.py` | Track A & B |
| 2 | [`starter/sql/10_validate_migration_parity.sql`](../starter/sql/10_validate_migration_parity.sql) | Tiers 1–3 (`L52`, `L75`, `L85`) | Track A & B |
| 3 | [`starter/sql/10_validate_migration_parity.sql`](../starter/sql/10_validate_migration_parity.sql) | Tier 4 (`L100`) | Track B (optional on Track A) |
| 4 | Optional live GCP check | `make preflight STRICT_GCP=1` | Optional (Live GCP) |

- **Estimated time:** ~20 min (Track A Express) / ~45 min (Track B Full)
- **Reference copy (read-only if stuck):** [`solution/sql/10_validate_migration_parity.sql`](../solution/sql/10_validate_migration_parity.sql) and [`solution/sql/fixtures/10_row_hash_expected.json`](../solution/sql/fixtures/10_row_hash_expected.json)
- **Antigravity skills:** [`sdlc-graphrag-workshop-guide`](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md), [`spanner-enterprise-platform-ops`](../.agents/skills/spanner-enterprise-platform-ops/SKILL.md) (support: [`spanner-schema-and-data-modeling`](../.agents/skills/spanner-schema-and-data-modeling/SKILL.md))

---

## Quick Background

Splitting graph data, full-text indexes, and vector chunks across separate databases creates CDC lag and stale citations whenever a service or ADR changes. Putting both paths into one Cloud Spanner Enterprise database (`sdlc_knowledge_graph`) keeps topology and prose consistent under ACID transactions:

| Path | Source files (`data/`) | Spanner representation | Exact counts |
| :-: | :--- | :--- | :--- |
| **Path A** (Structured) | `data/structured/nodes/*.csv` & `edges/*.csv` | 5 node tables + 6 explicit M:N edge tables + 4 folded N:1 FKs projected as edges in `SdlcGraph` | `68` nodes, `174` explicit rows, `60` dual-projected (`234` logical edges) |
| **Path B** (Unstructured) | `data/unstructured/{services,apis,adrs,requirements,owners}/*.md` | `DocumentChunks` (FTS `TOKENLIST` + 768-dim `FLOAT32` vectors) + `ChunkEntityMentions` + `ChunkCites*View` | `45` Markdown docs (`152` chunks, `1,160` mentions) |

```mermaid
flowchart BT
    subgraph TIER2["Tier 2 — Downstream Messaging & Analytics"]
        NOTIF["svc-notifications\n(team-integrations | MEDIUM)"]
        HOOKS["svc-webhooks\n(team-integrations | MEDIUM)"]
        ANAL["svc-analytics\n(team-data | LOW)"]
    end

    subgraph TIER1["Tier 1 — Orders, Partners & Inventory"]
        ORD["svc-orders\n(team-checkout | HIGH)"]
        PGW["svc-partner-gw\n(team-integrations | HIGH)"]
        INV["svc-inventory\n(team-catalog | HIGH)"]
    end

    subgraph TIER0["Tier 0 — Core Financial, Risk & Identity (99.99% SLA)"]
        CHK["svc-checkout\n(team-checkout | CRITICAL)"]
        BILL["svc-billing\n(team-billing | CRITICAL)"]
        PAY["svc-payments\n(team-payments | CRITICAL)\nHERO: POST /v2/payments/charge\nLEGACY: POST /v1/payments/charge"]
        FRAUD["svc-fraud\n(team-risk | 45ms fail-closed)"]
        LED["svc-ledger\n(team-billing | SOX)"]
        AUTH["svc-auth\n(team-security | HUB in-degree=10)"]
    end

    CHK -- "CALLS_API (1200 RPS)" --> PAY
    BILL -- "CALLS_API v2 (450 RPS)\n+ AT_RISK v1 (85 RPS!)" --> PAY
    ORD -- "SYNC_RPC" --> CHK
    PGW -- "SYNC_RPC" --> CHK
    PGW -- "SYNC_RPC" --> BILL
    NOTIF <-- "ASYNC_EVENT cycle" --> ORD
    HOOKS -- "ASYNC_EVENT" --> ORD
    HOOKS -- "ASYNC_EVENT" --> PGW
    ANAL -. "DATA_FEED (async BI)" .-> ORD

    PAY -- "SYNC_RPC (<=45ms)" --> FRAUD
    PAY -- "SYNC_RPC (SOX)" --> LED
    CHK --> INV
    ANAL -. "DATA_FEED" .-> LED & INV
    PAY & CHK & BILL & ORD & PGW & NOTIF & HOOKS & FRAUD & LED & INV -. "10x CALLS api-auth-token-v2" .-> AUTH
```

Watch for 5 real-world graph patterns in this dataset — you'll verify each one in Tier 3:
- **Bidirectional cycle:** `svc-orders <-> svc-notifications` (`ASYNC_EVENT`), which requires `MATCH TRAIL`.
- **Async analytics feed:** `svc-analytics` reads from `svc-orders`, `svc-ledger`, and `svc-inventory` via `DATA_FEED` (non-blocking BI, separated from customer-facing outages).
- **High-fanin identity hub:** `svc-auth` and `api-auth-token-v2` both have in-degree `10` (used for PageRank and fanout capping in Lab 4).
- **Legacy deprecated caller:** `svc-billing` still calls deprecated `api-payments-charge-v1` (`migration_status = 'AT_RISK'`, 85 RPS).
- **ADR supersession chain:** `ADR-004 -> ADR-001`, `ADR-008 -> ADR-002`, and `ADR-010 -> ADR-003` separate active `ACCEPTED` rules from older `SUPERSEDED` ones.

**Regional setup:** Cloud Spanner Enterprise (`regional-asia-southeast3`), Cloud Run MCP Toolbox, Secret Manager, GCS, and Dataflow all run in **Bangkok (`asia-southeast3`)** with 3-zone synchronous replication, same-region backups, and `$0.00/mo` cross-region egress. **Singapore (`asia-southeast1`, ~25 ms RTT)** is used strictly for services unavailable in Bangkok: Vertex AI (`gemini-embedding-001` and `gemini-2.5-flash`), Model Armor, and Cloud DLP.

---

## Step-by-Step Hands-On

### Step 1: Explore the structured and unstructured sample data (Track A & B)

1. Open one structured CSV pair (`data/structured/nodes/services.csv` and `data/structured/edges/service_calls_api.csv`) to see the business primary keys (`svc-payments`, `api-payments-charge-v2`) and edge attributes (`migration_status`, `rps_estimate`).
2. Open [`data/unstructured/adrs/ADR-004.md`](../data/unstructured/adrs/ADR-004.md) and [`data/unstructured/apis/api-payments-charge-v2.md`](../data/unstructured/apis/api-payments-charge-v2.md). Notice how the prose references exact entity IDs and concrete thresholds (`tok_pci_v2`, `45ms`, `> $500`, `svc-billing`).
3. Validate the structured export staging manifest locally:
   ```bash
   python3 data/structured/stage_apoc_export.py
   ```
   Confirm it reports `68` nodes, `174` explicit edges, and `60` dual-projected edges (`234` total).

### Step 2: Complete Tiers 1–3 in `starter/sql/10_validate_migration_parity.sql` (Track A & B)

Open [`starter/sql/10_validate_migration_parity.sql`](../starter/sql/10_validate_migration_parity.sql).

1. **Tier 1 — Path B document coverage (`L52`):**
   Inside the `counts` CTE, add `(SELECT COUNT(DISTINCT source_path) FROM DocumentChunks) AS unstructured_markdown_docs`, then append a `'path_b_distinct_markdown_docs'` row (expected `45`) to the scorecard `UNION ALL` right below `'total_logical_graph_edges'`.
2. **Tier 2 — Per-label and per-edge graph counts (`L75`):**
   Replace the `TODO(Lab 2)` block with `GRAPH_TABLE(SdlcGraph ...)` queries asserting exact counts for all 5 node labels (`Owner` 8, `Service` 12, `Api` 24, `Adr` 10, `Requirement` 14), 6 explicit M:N edges (`CALLS_API` 48, `DEPENDS_ON` 28, `GOVERNS` 36, `SUPERSEDES` 3, `APPLIES_TO_API` 33, `APPLIES_TO_SERVICE` 26), and 4 dual-projected edges (`EXPOSES` 24, `OWNS_SERVICE` 12, `OWNS_ADR` 10, `OWNS_REQ` 14).
3. **Tier 3 — Topological invariants (`L85`):**
   Replace the `TODO(Lab 2)` block with a `UNION ALL` scorecard emitting `PASS`/`FAIL` for:
   - `svc-auth` in-degree (`10`) in `ServiceDependsOnService` and `api-auth-token-v2` callers (`10`) in `ServiceCallsApi`
   - `svc-orders <-> svc-notifications` `ASYNC_EVENT` cycle edges (`2`)
   - `svc-analytics` `DATA_FEED` edges to `svc-orders`, `svc-ledger`, and `svc-inventory` (`3`)
   - `svc-billing -> api-payments-charge-v1` legacy call where `migration_status = 'AT_RISK'` and `lifecycle_status = 'DEPRECATED'` (`1`)
   - Hero API `api-payments-charge-v2`: `2` direct callers, `5` governing ADRs, `5` applicable requirements
   - `DocumentChunks` rows with `embedding IS NULL` (`0`)

*(Optional Antigravity prompt)*:
> "Help me complete Tiers 1–3 in `starter/sql/10_validate_migration_parity.sql` using the `sdlc-graphrag-workshop-guide` skill so it checks the 68/174/60/234/45 counts via `SdlcGraph` and all the Tier 3 invariants. Leave `solution/` and `data/` untouched."

Quick check (Track A Express scope):

```bash
make TRACK=starter MODE=express verify-lab LAB=2
```

### Step 3: Complete Tier 4 row-hash parity (`L100`) (Track B — optional on Track A)

Skip this step if you're on Track A Express.

1. Jump to **Tier 4 (`L100`)** in [`starter/sql/10_validate_migration_parity.sql`](../starter/sql/10_validate_migration_parity.sql).
2. Build a `table_lines` CTE across all 11 Path A tables formatting each row as a trimmed pipe-delimited `canonical_line`.
3. Group by `table_name` to compute `TO_HEX(SHA256(CONCAT(STRING_AGG(canonical_line, '\n' ORDER BY canonical_line ASC), '\n')))` alongside order-independent `BIT_XOR(FARM_FINGERPRINT(canonical_line))`. These hashes match [`solution/sql/fixtures/10_row_hash_expected.json`](../solution/sql/fixtures/10_row_hash_expected.json) byte-for-byte.

*(Optional Antigravity prompt)*:
> "Write the Tier 4 canonical row-hash query at line 100 of `starter/sql/10_validate_migration_parity.sql` using `TO_HEX(SHA256(...))` and `BIT_XOR(FARM_FINGERPRINT(...))` matching the column order in `solution/sql/fixtures/10_row_hash_expected.json`."

### Step 4 (Optional — Live Bangkok GCP): Check GCP credentials for the 100-PU sandbox

If you're running against a live Google Cloud sandbox, configure `.env` and verify auth now:

```bash
cp .env.example .env
# Edit .env to set GOOGLE_CLOUD_PROJECT=<your-sandbox-project>
make preflight STRICT_GCP=1
```

We'll provision the Bangkok 100-PU Enterprise instance (`regional-asia-southeast3`, `--default-backup-schedule-type=NONE`) and deploy *your* `starter/` schema in **Lab 3** once you finish `01_path_a_schematized.sql`. *(Shortcut: if you want a live instance early to explore in Cloud Console, run `make provision-gcp TRACK=solution`.)* Once data is loaded in Lab 3, run your parity suite live with:

```bash
python3 scripts/run_live_sql.py 10 --track starter
```

---

## Verify This Lab

```bash
# Track B (Full — verifies dataset counts + Tiers 1–4)
make TRACK=starter verify-lab LAB=2

# Track A (Express — verifies dataset counts + Tiers 1–3)
make TRACK=starter MODE=express verify-lab LAB=2
```

Passing output looks like this:

```text
[PASS] Lab 2 (starter/, mode=full)
    + Dataset verified: 68 nodes, 174 explicit + 60 dual-projected (234 logical) edges, 45 docs
    + Verified 4-tier parity SQL in starter/sql/10_validate_migration_parity.sql
[OK] Verified 1/1 lab(s) on track=starter (mode=full).
```

**If it fails, check:**
- **Remaining `TODO(Lab 2)` markers:** Remove the `-- TODO(Lab 2)` comment lines in Tiers 1–3 (and Tier 4 if running Full mode).
- **Missing literal checks in Tier 3:** The verifier specifically checks for `DocumentChunks`, `'svc-auth'`, `'DATA_FEED'`, `'AT_RISK'`, and `234` in uncommented SQL.
- **Full mode hash check:** Track B checks for `SHA256(` or `FARM_FINGERPRINT(` in Tier 4. If you're on Track A, pass `MODE=express`.

---

**Previous / Next:** [← Lab 1: Neo4j CE Migration](01_neo4j_community_migration.md) | [Starter Checklist](../starter/README.md) | [Lab 3: Database Design & Ingestion →](03_database_design_and_ingestion.md)
