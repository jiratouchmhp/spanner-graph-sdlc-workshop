# Lab 7: Capacity Sizing, Bangkok Pricing & 3-Store TCO

In this lab, you'll size a production Cloud Spanner Enterprise deployment in Bangkok (`regional-asia-southeast3`) that stores graph edges, full-text indexes, and ScaNN vector indexes inside a single database. You'll run the 4 built-in presets, plug your own team's numbers into `starter/tools/customer_scale_worksheet.json`, and compare On-Demand, 1-year/3-year CUDs, and SSD-to-HDD Locality Groups against a separate Neo4j + Vector DB + OpenSearch stack. Backups stay in Bangkok (`$0.00/mo` cross-region egress), while Singapore (`asia-southeast1`) appears only as a pricing benchmark and companion AI/Security region.

## What You'll Work On

| Step | File to open (`starter/`) | Where to edit (`TODO` / line) | Track |
| :-: | :--- | :--- | :-: |
| 1 | Built-in sizing presets | Run `make sizing PRESET=all REGION=asia-southeast3` (`workshop` vs `small`/`medium`/`large`) | Track A & B |
| 2 | [`starter/tools/customer_scale_worksheet.json`](../starter/tools/customer_scale_worksheet.json) | Workload scale knobs (`L35–L46`: `nodes`, `edges`, `chunks`, `qps`, `write_pct`, `cold_hdd_pct`) | Track A & B |
| 3 | Sizing & 3-store TCO calculation | Run `make sizing WORKSHEET=starter/tools/customer_scale_worksheet.json REGION=asia-southeast3` | Track A & B |
| 4 | [`starter/tools/customer_scale_worksheet.json`](../starter/tools/customer_scale_worksheet.json) | Mark `"todo_status": "COMPLETED"` (`L2`) and run `make TRACK=starter verify-lab LAB=7` | Track A & B |

- **Estimated time:** ~20 min (Track A Express) / ~45 min (Track B Full)
- **Reference calculator (read-only):** [`solution/tools/sizing_calculator.py`](../solution/tools/sizing_calculator.py) (pure Python stdlib, runs offline)
- **Antigravity skills:** [`spanner-performance-observability-and-sizing`](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md), [`spanner-enterprise-platform-ops`](../.agents/skills/spanner-enterprise-platform-ops/SKILL.md)

---

## Quick Background

Don't size a multi-model Spanner database from raw CSV or JSON file sizes alone. `sizing_calculator.py` accounts for every index layer: base rows (`1 KiB/node`, `256 B/edge`, `2 KiB/chunk`), reverse covering `STORING` indexes (`~2.0x` edge bytes), FTS `TOKENLIST` + search index (`~0.5x` text bytes), `768`-dim `FLOAT32` embeddings (`3.0 KiB/chunk`—half the size of `FLOAT64`), and ScaNN vector index overhead (`~1.3x` raw vectors).

Keep three sizing rules in mind:
- **Regional failover headroom:** Keep High-Priority (HP) CPU under **`65%`** on 3-zone regional instances (so losing one zone leaves `<= 97.5%` load on the two surviving zones) and storage under **`7.5 TiB/node`**.
- **Production minimum is `1,000` PUs (1 node):** `100` PUs is dev/workshop-only (`is_production_eligible: false`). Production needs `>= 1,000` PUs to enable Managed Autoscaler (`min >= 1 node`, `max <= 10x min`) and dedicated zone server tasks.
- **Same-region Bangkok backups (`$0.00/mo` egress):** Production backups (`$0.0315/GiB-mo`) and 7-day PITR stay inside `regional-asia-southeast3`.

**Verified 2026 Regional Rate Card (USD, 730 hrs/mo):**

| Pricing Dimension | Bangkok Primary (`asia-southeast3`, 1.05x) | Singapore Benchmark (`asia-southeast1`, 1.144x) | Iowa Benchmark (`us-central1`, 1.00x) |
| :--- | ---: | ---: | ---: |
| Enterprise Compute (node-hr / mo) | **`$1.2915`** (`$942.80`) | `$1.40712` (`$1,027.20`) | `$1.23` (`$897.90`) |
| Enterprise 1-Yr / 3-Yr CUD (node-hr) | `$1.0332` / `$0.7749` | `$1.125696` / `$0.844272` | `$0.984` / `$0.738` |
| Additional RO Replica — Enterprise (node-hr) | `$0.4305` | `$0.46904` | `$0.41` |
| SSD / HDD / Backup Storage (GiB-mo) | **`$0.315`** / **`$0.063`** / `$0.0315` | `$0.3432` / `$0.06864` / `$0.03432` | `$0.30` / `$0.06` / `$0.03` |
| Data Boost (per 1,000 SPU-hr) | **`$1.2285`** | `$1.33848` | `$1.17` |
| Cross-Region DR Egress / 8-hr Workshop (`100` PU) | `$0.00/mo` / `$1.03` compute | *(benchmark & AI/Security only)* | — |

Bangkok (`regional-asia-southeast3`) runs ~8.2% cheaper than Singapore list prices while keeping all database data resident in Thailand.

---

## Step-by-Step Hands-On

### Step 1: Run the 4 built-in Bangkok sizing presets (Track A & B)

1. Run the sizing calculator across all 4 built-in presets in Bangkok:
   ```bash
   make sizing PRESET=all REGION=asia-southeast3
   ```
2. Inspect the jump from sandbox to production tiers:
   - **`workshop` (`100` PU, ~`$1.03` for an 8-hour lab):** dev/sandbox only (`is_production_eligible: false`).
   - **`small` (`1,000` PU, 12.0% HP CPU, `2.86 GiB`):** `$968.63/mo` On-Demand → `$591.51/mo` on 3-Yr CUD (vs `$3,672.11/mo` legacy 3-store infra).
   - **`medium` (`2,000` PU, 36.0% HP CPU, `28.61 GiB`):** `$1,977.75/mo` On-Demand → `$1,223.52/mo` on 3-Yr CUD (vs `$5,214.78/mo` legacy 3-store infra).
   - **`large` (`8,000` PU, 60.1% HP CPU, `238.90 GiB`):** `$8,211.37/mo` On-Demand → `$5,194.43/mo` on 3-Yr CUD (vs `$16,701.01/mo` legacy 3-store infra; same-region Bangkok backups cost `$7.53/mo` with `$0.00/mo` cross-region egress).
3. *(Optional benchmark)* Run `make sizing PRESET=medium REGION=us-central1` to compare Bangkok (`1.05x`) against Iowa (`1.00x`).

### Step 2: Enter your workload numbers in `starter/tools/customer_scale_worksheet.json` (Track A & B)

1. Open [`starter/tools/customer_scale_worksheet.json`](../starter/tools/customer_scale_worksheet.json) and jump to **lines 35–46**.
2. Adjust the fields to match your own platform or target growth scenario:
   - `nodes`, `edges`, `chunks`: expected counts of graph entities, edges, and document chunks.
   - `qps` and `write_pct`: peak combined read + write requests per second and the write share (`10`–`20%` is typical for an SDLC knowledge graph). The calculator sizes PUs to hold HP CPU under `65%`.
   - `cold_hdd_pct`: percentage of aged, non-key payload bytes (such as old `AgentQueryAudit.raw_payload` records) tiered from SSD (`$0.315/GiB-mo`) to HDD (`$0.063/GiB-mo`—5x cheaper) via Locality Groups. Keep hot primary keys and indexes on SSD so writes avoid HDD existence-check reads.
   - Keep `"region": "asia-southeast3"`, `"dr_backup_region": "none"`, `"edition": "enterprise"`, and `"enable_primary_backup": true` so backups stay inside Bangkok with `$0.00/mo` cross-region egress.

*(Optional Antigravity prompt)*:
> "Help me size a 1.5M-node, 8M-edge, 3M-chunk Bangkok workload at 400 QPS in `starter/tools/customer_scale_worksheet.json` and explain the 1-year vs. 3-year CUD and HDD tiering savings."

### Step 3: Run the sizing & 3-store TCO comparison on your worksheet (Track A & B)

1. Run the calculator against your customized worksheet:
   ```bash
   make sizing WORKSHEET=starter/tools/customer_scale_worksheet.json REGION=asia-southeast3
   ```
2. Review the output for:
   - Provisioned PUs (`>= 1,000` PUs), projected HP CPU (`< 65%`), and recommended Managed Autoscaler `min_nodes`/`max_nodes`.
   - Monthly spend across On-Demand, 1-Yr CUD (`-20%`), and 3-Yr CUD (`-40%`), plus HDD Locality Group storage savings.
   - Monthly and annual savings compared to running Neo4j HA + Managed Vector DB HA + OpenSearch/Elastic HA + CDC sync pipelines.
3. *(Optional what-if run)* Test a quick sensitivity change from the CLI without editing the worksheet again:
   ```bash
   python3 solution/tools/sizing_calculator.py \
     --nodes 2000000 --edges 10000000 --chunks 5000000 --qps 600 --write-pct 15 \
     --cold-hdd-pct 45 --region asia-southeast3
   ```

### Step 4: Mark the worksheet done (`L2`) and verify (Track A & B)

1. In [`starter/tools/customer_scale_worksheet.json`](../starter/tools/customer_scale_worksheet.json), jump to **line 2** and replace `"todo_status"` with `"COMPLETED"`.
2. Save the file and run the offline verifier below. *(Lab 7 is 100% offline—no live GCP calls needed.)*

---

## Verify This Lab

```bash
make TRACK=starter verify-lab LAB=7
```

**Passing output:** `[PASS] Lab 7 (starter/, mode=full)` confirming the verified 2026 Bangkok rate card, all 4 presets, and your completed worksheet.

**If it fails, check:**
- Line 2 (`"todo_status"`) is set strictly to `"COMPLETED"`, and no `TODO(Lab 7)` string remains anywhere in the file.
- JSON syntax is valid (no trailing commas or missing quotes around string values).
- `"region"` is `"asia-southeast3"` and `"dr_backup_region"` is `"none"` for single-region Bangkok pricing.

---

**Previous / Next:** [← Lab 6: MCP Toolbox & Agent Security](06_mcp_toolbox_agent_security.md) | [Starter Guide](../starter/README.md) | [Lab 8: Observability & Troubleshooting →](08_observability_and_troubleshooting.md)
