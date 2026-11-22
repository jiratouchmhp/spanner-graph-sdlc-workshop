# Module 7: Enterprise Capacity Sizing, Verified Bangkok Single-Region Pricing & 3-Store TCO

**Customer Requirement 7:** Size a production single-region **Bangkok (`asia-southeast3` / `regional-asia-southeast3`) Cloud Spanner Enterprise** multi-model SDLC Knowledge Graph across graph, FTS, and ScaNN vector footprints using **verified 2026 SKU rates** (benchmarked against Singapore `asia-southeast1` and US `us-central1`), apply cost-optimization levers (1-Yr/3-Yr CUDs, 5x SSD→HDD Locality Groups, `$0` cross-region DR egress), and quantify 3-store consolidation TCO savings.

- **Duration:** Track A (4h Express): `20 min` | Track B (8h Deep-Dive): `45 min`
- **Primary Antigravity Skills:** [`spanner-performance-observability-and-sizing`](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md), [`spanner-enterprise-platform-ops`](../.agents/skills/spanner-enterprise-platform-ops/SKILL.md)
- **Support Skill:** [`sdlc-graphrag-workshop-guide`](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md)
- **Hands-On Starter File:** [`starter/tools/customer_scale_worksheet.json`](../starter/tools/customer_scale_worksheet.json) (`TODO(Lab 7)`)
- **Reference Tool:** [`solution/tools/sizing_calculator.py`](../solution/tools/sizing_calculator.py) (100% Python stdlib)

---

## 1. Verified 2026 Cloud Spanner Regional Pricing Rate Card (USD, 730 hrs/mo)

All calculations in [`solution/tools/sizing_calculator.py`](../solution/tools/sizing_calculator.py) match verified 2026 live Cloud Spanner list prices (`cloud.google.com/spanner/pricing`). **Bangkok (`regional-asia-southeast3`, `1.05x` US)** is the **primary and only Cloud Spanner region** in this architecture — saving **~8.2%** compared to the **Singapore (`asia-southeast1`, `1.144x` US)** list-price benchmark while keeping 100% of operational data Thailand PDPA resident (Singapore is used strictly for companion Vertex AI, Model Armor, and Cloud DLP services unavailable in Bangkok — **no** 2nd Singapore Spanner instance or cross-region DR):

| Pricing Dimension | Bangkok Primary & Only Spanner Region (`regional-asia-southeast3`, **1.05x**) | Reference Benchmark: Singapore (`asia-southeast1`, **1.144x**) | Reference Benchmark: US (`regional-us-central1`, 1.00x) |
| :--- | ---: | ---: | ---: |
| **SLA & Quorum** | 99.99% (3 zones `a/b/c`, PDPA resident) | 99.99% (3 zones — benchmark only) | 99.99% (3 zones) |
| **Standard Compute** (node-hr / node-mo) | `$0.945` / `$689.85` | `$1.0296` / `$751.608` | `$0.90` / `$657.00` |
| **Enterprise Compute** (node-hr / node-mo) | **`$1.2915` / `$942.795`** | **`$1.40712` / `$1,027.1976`** | `$1.23` / `$897.90` |
| **Enterprise Plus Compute** (node-hr / node-mo) | `$1.7955` / `$1,310.715` | `$1.95624` / `$1,428.0552` | `$1.71` / `$1,248.30` |
| **Enterprise 1-Yr CUD (-20%)** (node-hr / mo) | **`$1.0332`** (`$754.236/mo`) | `$1.125696` (`$821.758/mo`) | `$0.984` (`$718.32/mo`) |
| **Enterprise 3-Yr CUD (-40%)** (node-hr / mo) | **`$0.7749`** (`$565.677/mo`) | `$0.844272` (`$616.319/mo`) | `$0.738` (`$538.74/mo`) |
| **Additional RO Replica — Enterprise** (node-hr) | `$0.4305` (`$314.265/mo`) | `$0.46904` (`$342.399/mo`) | `$0.41` (`$299.30/mo`) |
| **SSD / HDD / Backup Storage** (GiB-mo) | **`$0.315` / `$0.063` / `$0.0315`** | `$0.3432` / `$0.06864` / `$0.03432` | `$0.30` / `$0.06` / `$0.03` |
| **Data Boost** (per 1,000 SPU-hr) | **`$1.2285`** | `$1.33848` | `$1.17` |
| **Intra-Region Replication / Cross-Region DR Egress** | **`$0.00/GiB`** (same-region Bangkok only) | — *(No Spanner DR in Singapore)* | — |
| **Workshop Sandbox (`100` PU × 8 hr, Enterprise)** | **`$1.0332` compute (`~$1.15` total)** | `$1.1257` compute *(benchmark)* | `$0.984` compute |

---

## 2. Multi-Model Capacity Sizing Rules (Avoid Common Under-Sizing Traps)

1. **Multi-Model Storage Multipliers:** Do not size on raw JSON/CSV size alone! `sizing_calculator.py` models every index layer explicitly:
   - Base rows: `1 KiB/node` + `256 B/edge` + `2 KiB/chunk` text.
   - Reverse covering `STORING` indexes: **`~2.0x`** base edge bytes.
   - FTS `TOKENLIST` + `CREATE SEARCH INDEX`: **`~0.5x`** indexed text bytes.
   - `ARRAY<FLOAT32>(vector_length=>768)` embeddings: **`3.0 KiB/chunk`** (`768 × 4 bytes`) — half the size of `FLOAT64`.
   - ScaNN `CREATE VECTOR INDEX`: **`~1.3x`** raw vector bytes.
2. **Regional CPU & Storage Failover Headroom:** Keep High-Priority (HP) CPU strictly **`< 65%`** on regional 3-zone configs (so a single zone failure leaves `<= 97.5%` load on the 2 surviving zones!) and storage **`<= 7.5 TiB/node`** (`75%` alert threshold of the `10 TiB/node` hard limit).
3. **Production Minimum Floor (`>= 1,000` PUs):** `100` PUs is strictly a workshop/dev sandbox. Production requires **`>= 1,000` PUs (1 node)** to enable dedicated zone server tasks, **Managed Autoscaler** (`min >= 1 node`, `max <= 10x min`, HP CPU `65%` / Total CPU `70%` / Storage `75%`), and Remote Functions.
4. **Tiered Storage Locality Group Guardrail (5x HDD Savings):** Spill only aged non-key payloads (e.g., `AgentQueryAudit.raw_payload` after 30 days) from SSD (`$0.315/GiB-mo`) to HDD (`$0.063/GiB-mo`). Keep base row-existence metadata and active indexes on SSD to avoid the write existence-check HDD read penalty!
5. **Same-Region Bangkok Backups & 7-Day PITR (`$0` Cross-Region DR Egress):** All production database storage, `AUTOMATIC` backups (`$0.0315/GiB-mo`), and 7-day PITR remain inside `regional-asia-southeast3` (`$0.00/GiB` 3-zone intra-region replication and **`$0` cross-region DR backup egress** by default).

---

## 3. Verified 4 T-Shirt Presets & 3-Store Consolidation TCO (Pure Bangkok Single-Region)

Run `make sizing PRESET=all REGION=asia-southeast3` to reproduce these exact verified pure Bangkok single-region numbers live (`$0` cross-region DR egress):

| Preset | Scale (Nodes / Edges / Chunks / QPS) | Provisioned PUs (HP CPU%) | Total Storage (HDD%) | Spanner OD / 1-Yr / 3-Yr CUD (`/mo`) | Legacy 3-Store Infra (`/mo`) | Legacy Fully Loaded (`/mo`) | 3-Yr CUD Savings (Infra / Fully Loaded) |
| :--- | :--- | :---: | :---: | :---: | ---: | ---: | :---: |
| **`workshop` \*** | `68` / `234` / `150` / `5` QPS | `100` PU (`11.7%`) | `0.002 GiB` (`0%`) | `$94.34` / `$75.49` / `$56.63` *(8-hr lab: `$1.03`)* | `$367.50` *(dev)* | `$617.50` *(dev)* | `84.6%` / `90.8%` |
| **`small`** | `100k` / `500k` / `250k` / `50` QPS | `1,000` PU (`12.0%`) | `2.86 GiB` (`30%`) | **`$968.63` / `$780.07` / `$591.51`** | `$3,672.11` | `$6,231.29` | **`83.9%`** / **`90.5%`** |
| **`medium`** | `1M` / `5M` / `2.5M` / `300` QPS | `2,000` PU (`36.0%`) | `28.61 GiB` (`40%`) | **`$1,977.75` / `$1,600.64` / `$1,223.52`** | `$5,214.78` | `$8,396.48` | **`76.5%`** / **`85.4%`** |
| **`large`** | `10M` / `50M` / `20M` / `2,000` QPS | `8,000` PU (`60.1%`) | `238.90 GiB` (`50%`) | **`$8,211.37` / `$6,702.90` / `$5,194.43`** | `$16,701.01` | `$20,541.83` | **`68.9%`** / **`74.7%`** |

*\* `workshop` (`100` PU) is dev/lab-only (`is_production_eligible: false`). At `large` scale (`238.90 GiB`) in pure Bangkok single-region Enterprise (`$0.00` cross-region DR egress), same-region Bangkok `AUTOMATIC` backup storage costs only `$7.53/mo` (`$0.0315/GiB-mo`), 50% SSD→HDD Locality Group tiering saves `$30.10/mo` (-40% storage spend), and consolidating Neo4j Enterprise HA + Managed Vector DB HA + OpenSearch/Elastic HA + CDC pipelines saves **`$138,079/yr` infra-only (`$184,169/yr` fully loaded)** on 3-Yr CUD!*

### Useful Sensitivity CLI Commands

```bash
# 1. Compare all 4 presets in pure Bangkok single-region Enterprise ($0 cross-region DR egress)
make sizing PRESET=all REGION=asia-southeast3

# 2. Benchmark Bangkok vs US reference compute economics side-by-side
make sizing PRESET=medium REGION=us-central1

# 3. Custom what-if sensitivity in Bangkok (toggle cold HDD tiering & read-only replicas)
python3 solution/tools/sizing_calculator.py \
  --nodes 2000000 --edges 10000000 --chunks 5000000 --qps 600 --write-pct 15 \
  --cold-hdd-pct 45 --region asia-southeast3
```

---

## 4. Hands-On Exercise — Lab 7 Interactive Customer Worksheet (`starter/`)

Open [`starter/tools/customer_scale_worksheet.json`](../starter/tools/customer_scale_worksheet.json) and tailor it to your organization's target SDLC Knowledge Graph scale:

1. Update `nodes`, `edges`, `chunks`, `qps`, `write_pct`, and `cold_hdd_pct` for your real or projected engineering platform.
2. Confirm `region` is `asia-southeast3` (pure Bangkok single-region Enterprise with `$0` cross-region DR egress) and `enable_primary_backup` is `true` for same-region Bangkok production backups.
3. Run the calculator against your worksheet and inspect the projected HP CPU (`< 65%`), Managed Autoscaler `min_nodes`/`max_nodes`, and 3-store TCO savings:
   ```bash
   make sizing WORKSHEET=starter/tools/customer_scale_worksheet.json REGION=asia-southeast3
   ```
4. Once satisfied with your capacity model, update `"todo_status"` from `"TODO(Lab 7)"` to **`"COMPLETED"`**.

### Antigravity IDE Prompt (Copy-Paste)

> *"Using the `spanner-performance-observability-and-sizing` and `spanner-enterprise-platform-ops` skills, help me customize `starter/tools/customer_scale_worksheet.json` for a multi-team pure Bangkok single-region (`asia-southeast3`) SDLC Knowledge Graph (`$0` cross-region DR egress), run `solution/tools/sizing_calculator.py --worksheet`, explain our 3-Yr CUD + Locality Group savings, and set `todo_status` to `COMPLETED`. Do not edit `solution/`."*

### Verify Lab 7 Offline

```bash
make TRACK=starter verify-lab LAB=7
```

**Checkpoint Expected Output:** `[PASS] Lab 7 (starter/)` — verified Bangkok rate card, all 4 presets, and your completed customer worksheet!

---

**Navigation:** [← Module 6: MCP Toolbox & Agent Security](06_mcp_toolbox_agent_security.md) | [README](../README.md) | [Module 8: Observability, Troubleshooting & Teardown →](08_observability_and_troubleshooting.md)
