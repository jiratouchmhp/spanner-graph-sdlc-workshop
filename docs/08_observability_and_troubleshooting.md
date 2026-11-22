# Module 8: Observability, Spanner Studio Visualizations, Pre-Splitting, DR & Teardown

**Customer Requirement 7 (Operational Excellence Capstone):** Master day-2 **Cloud Spanner Enterprise** operations — interactive **Spanner Studio** graph visualizations, `SPANNER_SYS` Query/Lock/Active-Query introspection, Execution Plan profiling, **Key Visualizer** hotspot diagnosis, **Pre-Splitting**, Emulator vs. Enterprise boundaries, Singapore cross-region DR backup copy, and clean workshop teardown.

- **Duration:** Track A (4h Express): `15 min` | Track B (8h Deep-Dive): `30 min`
- **Primary Antigravity Skills:** [`spanner-performance-observability-and-sizing`](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md), [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md), [`spanner-enterprise-platform-ops`](../.agents/skills/spanner-enterprise-platform-ops/SKILL.md)
- **Support Skill:** [`sdlc-graphrag-workshop-guide`](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md)
- **Hands-On Starter File:** [`starter/sql/11_observability_and_studio_viz.sql`](../starter/sql/11_observability_and_studio_viz.sql) (`TODO(Lab 8)`)
- **Reference Solution:** [`solution/sql/11_observability_and_studio_viz.sql`](../solution/sql/11_observability_and_studio_viz.sql), [`scripts/provision_workshop_env.sh`](../scripts/provision_workshop_env.sh), [`scripts/verify_live_gcp.sh`](../scripts/verify_live_gcp.sh)

---

## 1. Spanner Studio Interactive Graph Visualization (`RETURN SAFE_TO_JSON(p)`)

Spanner Studio activates the interactive **Graph** tab **only** when a GQL query returns full graph elements or paths serialized via `SAFE_TO_JSON(...)` (staying under Spanner Studio's `10 MB` result payload cap). Returning scalar columns alone renders only a tabular grid.

Run Query 1A from [`solution/sql/11_observability_and_studio_viz.sql`](../solution/sql/11_observability_and_studio_viz.sql) in **Cloud Console → Spanner → `sdlc-kg-enterprise` → `sdlc_knowledge_graph` → Spanner Studio**:

```sql
GRAPH SdlcGraph
MATCH p = TRAIL (upstream:Service)-[:DEPENDS_ON]->{0,3}(direct:Service)-[:CALLS_API]->(target:Api {api_id: 'api-payments-charge-v2'})
RETURN
  SAFE_TO_JSON(p) AS blast_radius_path,
  PATH_LENGTH(p) AS hops
ORDER BY hops ASC
LIMIT 50;
```

- Switch to the **Graph** tab to inspect `api-payments-charge-v2`, its direct callers (`svc-checkout`, `svc-billing`), the `svc-orders <-> svc-notifications` cycle, and `svc-analytics` (`DATA_FEED`).
- Toggle **Schema view** to inspect all 6 node labels and 14 edge labels on `SdlcGraph` visually.

---

## 2. Production `SPANNER_SYS` Introspection & Critical Gotchas

> **Transaction Mode Rule:** Every `SPANNER_SYS.*` introspection query **must run in a read-only transaction** (cannot execute inside a read-write transaction).

| Diagnostic Area | System Table (`11_observability_and_studio_viz.sql`) | Key Signals & Production Gotchas |
| :--- | :--- | :--- |
| **Top-CPU & Misrouted RW Queries** | `SPANNER_SYS.QUERY_STATS_TOP_HOUR` | Rank by `EXECUTION_COUNT * AVG_CPU_SECONDS`. Alert when `RUN_IN_RW_TRANSACTION_EXECUTION_COUNT > 0` on read-only FTS/Graph queries or when `AVG_DISK_IO_COST` spikes from accidental HDD reads. Always attach structured `REQUEST_TAG` (e.g., `persona=impact-analyst,tool=blast_radius`). |
| **Lock Contention Hotspots** | `SPANNER_SYS.LOCK_STATS_TOP_HOUR` | **CRITICAL UNIT TRAP:** `LOCK_WAIT_SECONDS` is in **seconds**, whereas `LOCK_WAIT_DISTRIBUTION` buckets are stored in **microseconds**! Always divide `SPANNER_SYS.DISTRIBUTION_PERCENTILE(LOCK_WAIT_DISTRIBUTION[SAFE_OFFSET(0)], 99.0)` by **`1000000.0`** to obtain seconds! |
| **Runaway Active Queries** | `SPANNER_SYS.ACTIVE_QUERIES_SUMMARY` & `OLDEST_ACTIVE_QUERIES` | Intentionally **omit `CLIENT_IP_ADDRESS`** (selecting it adds lookup overhead). Cancel confirmed runaway queries with `CALL cancel_query('<query_id>')` (best-effort; requires `spanner.sessions.delete`). |
| **Tiered Storage & TTL Health** | `SPANNER_SYS.TABLE_SIZES_STATS_1HOUR` & `ROW_DELETION_POLICIES` | Monitor `USED_SSD_BYTES` vs. `USED_HDD_BYTES` spill ratio on `sdlc_cold_archive` (`AgentQueryAudit.raw_payload`) and alert if `UNDELETABLE_ROWS > 0` or `PROCESSED_WATERMARK` lags `> 72h`. |

---

## 3. Execution Plans, Key Visualizer & Pre-Splitting

### Execution Plans (`--query-mode=PLAN|PROFILE`)
- Use `--query-mode=PLAN` (zero data read) or `--query-mode=PROFILE` (returns actual latency, rows scanned, CPU, and `SeekableKeySize`) or open Spanner Studio's **Explanation** tab.
- **ScaNN Check:** Confirm `IdxDocumentChunksEmbedding` appears in the plan for `APPROX_COSINE_DISTANCE ... WHERE embedding IS NOT NULL`. If you see a full `TableScan` on `DocumentChunks`, check `INV-08` (missing `WHERE embedding IS NOT NULL` or a `JOIN` inside the ANN block).

### Key Visualizer & Pre-Splitting Before Scale Loads
- **Key Visualizer Heatmaps:** A bright diagonal line rising left-to-right signals a **monotonic sequential primary key hotspot** (e.g., raw timestamp or auto-incrementing integer). Always use split-safe keys (`GENERATE_UUID()`, domain-prefixed hashes, or bit-reversed sequences — as in `AgentQueryAudit.audit_id DEFAULT (GENERATE_UUID())`).
- **Pre-Splitting (`splits add`):** Before a large multi-million-row Neo4j migration into production (`>= 1,000` PU), pre-split hot edge and chunk keyspaces so Dataflow distributes writes across splits immediately instead of waiting for organic load-based splitting, then verify via `SPANNER_SYS.USER_SPLIT_POINTS`:
  ```bash
  # Example: pre-split DocumentChunks across doc_type prefixes before a 20M-chunk load
  gcloud spanner databases splits add \
    --instance=sdlc-kg-enterprise \
    --database=sdlc_knowledge_graph \
    --splits-file=splits.txt
  ```

---

## 4. Cloud Spanner Emulator vs. Cloud Spanner Enterprise Feature Matrix

Knowing exactly what the free local Emulator skips prevents wasted debugging hours:

| Feature / Capability | Local Spanner Emulator | Cloud Spanner Enterprise (`regional-asia-southeast3`) |
| :--- | :---: | :---: |
| Basic relational DDL, DML, Transactions & GQL syntax | Supported (subset) | **Full Production Support** |
| **ScaNN Vector Index (`CREATE VECTOR INDEX`) & ANN Plan** | Unsupported / ignored | **Full ScaNN ANN Index + Auto-Tuned Leaves** |
| **Full-Text Search (`SdlcSynonymDict`, `enhance_query`, `SCORE`)** | Limited / non-parity | **Full FTS + Custom Dictionaries + IDF Scoring** |
| **Vertex AI `CREATE MODEL` / `ML.PREDICT` (`asia-southeast1`)** | Unsupported | **Native In-DB Inference + Throttled PDML** |
| **Data Boost Graph Algorithms (`PageRank`, `WCC`, `ShortestPath`)** | Unsupported | **Serverless Isolated Data Boost Execution** |
| **FGAC Roles, IAM Enforcement & `SECURE_CONTEXT()`** | **Skipped (all queries allowed!)** | **Strict Enforced SQL RBAC + Conditional IAM** |
| **`PLAN` / `PROFILE` Stats, `ANALYZE`, `SPANNER_SYS`, Key Visualizer** | Empty / no-op | **Full Optimizer Stats, Insights & Heatmaps** |
| **Locality Groups (SSD→HDD), TTL Sweeper, PITR & Backups** | No-op | **Full Tiering, 7-Day PITR & Cross-Region DR** |

---

## 5. Live End-to-End Verification, Singapore DR Backup Copy & Teardown

### Step 1: Run the 5-Check Live GCP Verification Suite
```bash
# Preview checks safely first (or run live against your Bangkok sandbox)
make verify-live DRY_RUN=1
make verify-live
```
This verifies: (1) DDL describe, (2) ScaNN `PLAN`, (3) live Hero Query B 7-row fixture parity, (4) `sdlc_readonly_role` ALLOW on `PublicApiCatalogView` and DENY on `Owners.clearance_tier` / `INSERT INTO Adrs`, and (5) cross-region backup DR status.

### Step 2: Optional Cross-Region Backup DR Copy (Bangkok → Singapore, `~35–40 min` on 100 PU)
```bash
# Preview or execute cross-region backup copy ($0.08/GiB Asia egress + $0.03432/GiB-mo SG storage)
bash scripts/provision_workshop_env.sh backup-to-singapore --dry-run
bash scripts/provision_workshop_env.sh backup-to-singapore
```
> **DR Restore Reminder:** Cloud Spanner backups preserve SQL FGAC roles and `GRANT` statements inside the schema, but do **not** include GCP IAM bindings. Always re-apply Step 3 of `solution/mcp/iam_and_model_armor_setup.sh` after restoring to Singapore.

### Step 3: Guaranteed Zero-Residual Workshop Teardown (Confirm Active Project First!)
```bash
# Confirm you are deleting ONLY your workshop sandbox project!
gcloud config get-value project

# Full teardown (Cloud Run MCP, Secrets, all 7 SAs, DLP/Model Armor, GCS, Spanner) + guaranteed verify_zero_residual.sh audit
make cleanup-all-gcp
# Or Spanner-only teardown: make cleanup-gcp
# Preview or re-run the 6-category zero-residual audit anytime:
bash scripts/verify_zero_residual.sh --dry-run

# Confirm zero Spanner instances remain
gcloud spanner instances list
```
> **Clean CLI Tip:** Set `gcloud auth application-default set-quota-project "${GOOGLE_CLOUD_PROJECT}"` and `export SPANNER_DISABLE_BUILTIN_METRICS=true` in live shells to silence ADC quota warnings and OpenTelemetry client metrics noise. `make cleanup-all-gcp` finishes by running `scripts/verify_zero_residual.sh` across all 6 GCP resource categories (handling `gcloud run services delete` 404 `could not be found` idempotently) so zero billable resources remain.

---

## 6. Hands-On Exercise — Lab 8 (`starter/`)

Open [`starter/sql/11_observability_and_studio_viz.sql`](../starter/sql/11_observability_and_studio_viz.sql) and complete the `TODO(Lab 8)` queries:
1. **Spanner Studio Interactive Path Query:** `GRAPH SdlcGraph MATCH p = TRAIL ... RETURN SAFE_TO_JSON(p) AS blast_radius_path, PATH_LENGTH(p) AS hops LIMIT 50`.
2. **Query Insights:** Top-CPU query against `SPANNER_SYS.QUERY_STATS_TOP_HOUR` exposing `RUN_IN_RW_TRANSACTION_EXECUTION_COUNT` and `AVG_DISK_IO_COST`.
3. **Lock Insights:** P99 lock wait query against `SPANNER_SYS.LOCK_STATS_TOP_HOUR` converting microseconds to seconds (`/ 1000000.0` or `/ 1e6`).
4. **Split-Point & Lifecycle Health:** Query `SPANNER_SYS.USER_SPLIT_POINTS`, `TABLE_SIZES_STATS_1HOUR`, and `ROW_DELETION_POLICIES`.

### Antigravity IDE Prompt (Copy-Paste)

> *"Using the `spanner-performance-observability-and-sizing`, `spanner-graph-modeling-and-gql`, and `spanner-enterprise-platform-ops` skills, help me complete `TODO(Lab 8)` in `starter/sql/11_observability_and_studio_viz.sql`, including `RETURN SAFE_TO_JSON(p)`, `SPANNER_SYS.QUERY_STATS_TOP_HOUR`, `SPANNER_SYS.LOCK_STATS_TOP_HOUR` (converting microseconds via `/ 1000000.0`), and `SPANNER_SYS.USER_SPLIT_POINTS`. Do not edit `solution/`."*

### Verify Lab 8 & Full Workshop Completion Offline

```bash
make TRACK=starter verify-lab LAB=8
make TRACK=starter verify-lab LAB=all
make validate-skills
```

**Checkpoint Expected Output:** `[PASS] Lab 8 (starter/)` — Congratulations on completing all 8 modules of the Cloud Spanner Enterprise SDLC Knowledge Graph & GraphRAG Workshop!

---

**Navigation:** [← Module 7: Enterprise Sizing & Cost TCO](07_sizing_and_cost_tco.md) | [README](../README.md) | [Facilitator Guide](00_facilitator_guide.md)
