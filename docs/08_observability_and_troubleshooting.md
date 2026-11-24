# Lab 8: Observability, Spanner Studio & Clean Teardown

In this final lab, you'll complete a day-2 observability pack for your Bangkok (`regional-asia-southeast3`) Cloud Spanner Enterprise database—an interactive Spanner Studio graph query plus `SPANNER_SYS` queries for top CPU consumers, lock hotspots, and split points. After verifying all 8 labs (and optionally inspecting your live instance in Spanner Studio and running `make verify-live`), you'll run teardown as the **very last step** so every workshop resource in Bangkok (`asia-southeast3`) and Singapore (`asia-southeast1`) is deleted and billing stops cleanly.

## What You'll Work On

| Step | File to open (`starter/`) | Where to edit (`TODO` / line) | Track |
| :-: | :--- | :--- | :-: |
| 1 | [`starter/sql/11_observability_and_studio_viz.sql`](../starter/sql/11_observability_and_studio_viz.sql) | 4 `TODO(Lab 8)` queries (`L19`, `L32`, `L44`, `L87`): Studio graph viz, Query Insights, Lock Insights, Split Points | Track A & B |
| 2 | Offline verification | Run `make TRACK=starter verify-lab LAB=8` and `make TRACK=starter verify-lab LAB=all` | Track A & B |
| 3 | *(Optional — Live Bangkok GCP)* | Run `11` live, check Spanner Studio Graph / Explanation / Key Visualizer, and run `make verify-live` | Track B (Optional) |
| 4 | *(Final Step — Clean Up GCP)* | Run the full GCP teardown **last** (in Step 4, after Step 3!) so you don't delete your database early | Live GCP Only |

- **Estimated time:** ~15 min (Track A Express) / ~30 min (Track B Full)
- **Reference copy (read-only if stuck):** [`solution/sql/11_observability_and_studio_viz.sql`](../solution/sql/11_observability_and_studio_viz.sql), [`scripts/verify_live_gcp.sh`](../scripts/verify_live_gcp.sh), [`scripts/verify_zero_residual.sh`](../scripts/verify_zero_residual.sh)
- **Antigravity skills:** [`spanner-performance-observability-and-sizing`](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md), [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md), [`spanner-enterprise-platform-ops`](../.agents/skills/spanner-enterprise-platform-ops/SKILL.md)

---

## Quick Background

Four practical habits save hours when running Cloud Spanner Enterprise in production:
- **Read-only transactions for `SPANNER_SYS`:** Every `SPANNER_SYS.*` query must run in a read-only transaction (Spanner rejects them inside read-write transactions).
- **Spanner Studio Graph tab:** Studio activates the interactive **Graph** tab only when your GQL query returns full graph elements or paths via `SAFE_TO_JSON(...)` under its `10 MB` payload cap. Returning plain scalar columns renders only a table.
- **Execution Plans & Key Visualizer:** Check the **Explanation** tab (or `--query-mode=PROFILE`) to verify `IdxDocumentChunksEmbedding` shows `VectorIndexScan` instead of `TableScan`. In **Key Visualizer**, a bright diagonal line rising left-to-right signals a monotonic key hotspot—which is why `AgentQueryAudit` uses `GENERATE_UUID()`.
- **Why we test live on 100-PU Bangkok Enterprise instead of the local Emulator:**

| Capability | Local Spanner Emulator | Cloud Spanner Enterprise (`regional-asia-southeast3`) |
| :--- | :--- | :--- |
| Basic relational DDL, DML & GQL syntax | Partial subset | Full support |
| ScaNN Vector Index (`CREATE VECTOR INDEX`) & ANN plans | Ignored / unsupported | Full ScaNN index & auto-tuned leaves |
| Full-Text Search (`SdlcSynonymDict`, `enhance_query`, `SCORE`) | Limited / non-parity | Full FTS, custom dictionaries & IDF scoring |
| Vertex AI `ML.PREDICT` (`asia-southeast1`) & Data Boost Graph Algos | Unsupported | Native in-DB inference + serverless Data Boost |
| FGAC Roles, IAM Enforcement & `SECURE_CONTEXT()` | Skipped (allows everything!) | Enforced SQL RBAC + conditional IAM |
| `PLAN`/`PROFILE`, `SPANNER_SYS`, Key Visualizer, Locality Groups, PITR | Empty / no-op | Full insights, SSD→HDD tiering, 7-day PITR & backups |

---

## Step-by-Step Hands-On

### Step 1: Complete the 4 observability queries in `starter/sql/11_observability_and_studio_viz.sql` (Track A & B)

Open [`starter/sql/11_observability_and_studio_viz.sql`](../starter/sql/11_observability_and_studio_viz.sql). Real-time active queries (`L58–L77`, intentionally omitting `CLIENT_IP_ADDRESS` to avoid lookup overhead), SSD/HDD Locality Group sizes (`L91–L102`), and TTL row-deletion monitoring (`L104–L111`) are already complete. Fill in the four `TODO(Lab 8)` sections:

1. **Query 1 — Spanner Studio graph visualization (`L19`):** Write a `GRAPH SdlcGraph` query using `MATCH p = TRAIL (upstream:Service)-[:DEPENDS_ON]->{0,3}(direct:Service)-[:CALLS_API]->(target:Api {api_id: 'api-payments-charge-v2'})`. Return `SAFE_TO_JSON(p) AS blast_radius_path` and `PATH_LENGTH(p) AS hops`, ordered by `hops ASC` with `LIMIT 50`.
2. **Query 2 — Hourly top CPU queries (last 6 hours) (`L32`):** Query `SPANNER_SYS.QUERY_STATS_TOP_HOUR` filtered to `INTERVAL_END >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 6 HOUR)`. Compute `ROUND(EXECUTION_COUNT * AVG_CPU_SECONDS, 4) AS total_cpu_seconds`, include `REQUEST_TAG`, `TEXT_FINGERPRINT`, `QUERY_TYPE`, `EXECUTION_COUNT`, `AVG_LATENCY_SECONDS`, `AVG_ROWS_SCANNED`, `AVG_REMOTE_SERVER_CALLS`, `AVG_DISK_IO_COST`, and `RUN_IN_RW_TRANSACTION_EXECUTION_COUNT` (flags read-only FTS/Graph queries accidentally run in read-write transactions), and order by `total_cpu_seconds DESC LIMIT 20`.
3. **Query 3 — Hourly lock hotspots (last 6 hours) (`L44`):** Query `SPANNER_SYS.LOCK_STATS_TOP_HOUR` over the last 6 hours. Watch out for the unit mismatch: `LOCK_WAIT_SECONDS` is in **seconds**, while `LOCK_WAIT_DISTRIBUTION` buckets are in **microseconds**. Guard against empty buckets (`LOCK_WAIT_DISTRIBUTION[SAFE_OFFSET(0)].COUNT > 0`) and divide `SPANNER_SYS.DISTRIBUTION_PERCENTILE(LOCK_WAIT_DISTRIBUTION[SAFE_OFFSET(0)], 99.0)` by `1000000.0` (or `1e6`) to report `p99_lock_wait_secs` in seconds. Include `CAST(ROW_RANGE_START_KEY AS STRING)` and `SAMPLE_LOCK_REQUESTS`, ordered by `LOCK_WAIT_SECONDS DESC LIMIT 20`.
4. **Query 5A — Split-point audit (`L87`):** Select `TABLE_NAME`, `INDEX_NAME`, `INITIATOR`, `SPLIT_KEY`, and `EXPIRE_TIME` from `SPANNER_SYS.USER_SPLIT_POINTS`, ordered by `TABLE_NAME ASC, INDEX_NAME ASC`.

*(Optional Antigravity prompt)*:
> "Help me complete the four observability queries in `starter/sql/11_observability_and_studio_viz.sql`, including `SAFE_TO_JSON(p)` for the Spanner Studio graph view and the microsecond-to-second conversion for lock waits."

### Step 2: Verify Lab 8 and full workshop completion offline (Track A & B)

Run the offline verifier for Lab 8, then check your full workshop progress across all 8 labs (and validate the 8 Antigravity skills):

```bash
# Verify Lab 8
make TRACK=starter verify-lab LAB=8

# Verify all 8 labs (Track B Full)
make TRACK=starter verify-lab LAB=all

# Or if you followed Track A (4-Hour Express):
make TRACK=starter MODE=express verify-lab LAB=all

# Confirm all 8 Antigravity skills & .antigravity/mcp_config.json pass
make validate-skills
```

### Step 3 (Optional — Live Bangkok GCP): Inspect queries in Spanner Studio & run `make verify-live`

Do this step **before** Step 4 while your Bangkok (`asia-southeast3`) sandbox is still running.

1. **Execute your Lab 8 observability queries live** (if short on time, swap `--track starter` for `--track solution`):
   ```bash
   python3 scripts/run_live_sql.py 11 --track starter
   ```
2. **Explore in Cloud Console Spanner Studio** (**Spanner → `sdlc-kg-enterprise` → `sdlc_knowledge_graph` → Spanner Studio**):
   - Paste Query 1 (`RETURN SAFE_TO_JSON(p) ... LIMIT 50`) and open the **Graph** tab to inspect `api-payments-charge-v2`, direct callers, and the `svc-orders <-> svc-notifications` cycle. Toggle **Schema view** to browse all 6 node labels and 14 edge labels.
   - Run your Lab 5 Hero query and open the **Explanation** tab to confirm `VectorIndexScan` on `IdxDocumentChunksEmbedding`.
   - Open **Key Visualizer** to inspect key-range heatmaps. Before a large multi-million-row production load (`>= 1,000` PU), you can pre-split hot prefixes and audit them with your Query 5A:
     ```bash
     gcloud spanner databases splits add \
       --instance=sdlc-kg-enterprise \
       --database=sdlc_knowledge_graph \
       --splits-file=splits.txt
     ```
   - *(Production backup reminder: production instances in `regional-asia-southeast3` enable `--default-backup-schedule-type=AUTOMATIC`, `--enable-drop-protection`, and `ALTER DATABASE sdlc_knowledge_graph SET OPTIONS (version_retention_period = '7d')`—keeping 100% of backups in Bangkok with `$0.00/mo` cross-region egress. Backups include SQL FGAC roles/grants, but GCP IAM bindings must be re-applied after a restore.)*
3. **Run the end-to-end live verification suite:**
   ```bash
   make verify-live DRY_RUN=1
   make verify-live
   ```
   `scripts/verify_live_gcp.sh` confirms DDL objects, ScaNN `VectorIndexScan` plans, live Hero Query B 7-row fixture parity, `sdlc_readonly_role` allow/deny checks, Bangkok instance residency, and Singapore (`asia-southeast1`) Vertex AI model bindings.

### Step 4 (Final Step — Clean Up GCP Resources): Delete everything so billing stops

Run teardown **only after you're done with Step 3**. Always check your active GCP project first so you only delete your workshop sandbox:

```bash
# 1. Confirm your active project is your workshop sandbox!
gcloud config get-value project

# 2. Full teardown (~2 min): deletes Bangkok (asia-southeast3) Spanner instance/backups,
#    Cloud Run MCP services, user-managed Secrets, GCS staging bucket, all 7 SAs,
#    and Singapore (asia-southeast1) DLP & Model Armor templates — then runs
#    scripts/verify_zero_residual.sh to confirm 0 leftover resources
make cleanup-all-gcp

# Or, if you only provisioned Cloud Spanner (skipped Lab 6 Cloud Run & Model Armor):
make cleanup-gcp

# 3. Confirm zero Spanner instances remain
gcloud spanner instances list
```

You can preview or re-run the 6-category zero-residual check anytime with `bash scripts/verify_zero_residual.sh --dry-run`.

---

## Verify This Lab

```bash
make TRACK=starter verify-lab LAB=8
make TRACK=starter verify-lab LAB=all
```

*(For Track A Express, run `make TRACK=starter MODE=express verify-lab LAB=all`.)*

**Passing output:** `[PASS] Lab 8 (starter/, mode=full)` (and `[OK] Verified 8/8 lab(s)` across all labs).

**If it fails, check:**
- Any remaining `TODO(Lab 8)` comments in `starter/sql/11_observability_and_studio_viz.sql`.
- Missing `SAFE_TO_JSON(...)`, `SPANNER_SYS.QUERY_STATS_TOP_HOUR`, `SPANNER_SYS.LOCK_STATS_TOP_HOUR`, or `SPANNER_SYS.USER_SPLIT_POINTS`.
- Forgot to divide the microsecond lock wait percentile by `1000000.0` (or `1e6`) in Query 3.

---

**Previous / Next:** [← Lab 7: Sizing & Cost TCO](07_sizing_and_cost_tco.md) | [Starter Guide](../starter/README.md) | [Workshop README](../README.md) | [Facilitator Guide](00_facilitator_guide.md)
