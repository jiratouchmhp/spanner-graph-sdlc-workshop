# Starter Pack File Map & Cheat Sheet

You only edit files inside this `starter/` folder. Each lab guide in [`../docs/`](../docs/01_neo4j_community_migration.md) walks you through the exercises step by step, and [`../solution/`](../solution/sql/09_opencypher_to_gql_cookbook.sql) holds the working reference answers if you get stuck.

---

## 1. The 15 Files You'll Edit (Labs 1–8)

Every file below has `TODO(Lab N)` markers showing exactly where to write code. Track A (4h Express) skips the three Track B-only files marked in the table and grades cleanly with `MODE=express`.

| Done | Lab | File to Edit (`starter/`) | Exact TODO Lines & Track Scope | Lab Guide | Reference (`solution/`) |
| :-: | :-: | :--- | :--- | :--- | :--- |
| [ ] | **1** | [sql/09_opencypher_to_gql_cookbook.sql](sql/09_opencypher_to_gql_cookbook.sql) | 5 TODOs (`L34`, `L51`, `L68`, `L88`, `L107`; Track A = `L34–L68`) | [Lab 1](../docs/01_neo4j_community_migration.md) | [09_opencypher_to_gql_cookbook.sql](../solution/sql/09_opencypher_to_gql_cookbook.sql) |
| [ ] | **2** | [sql/10_validate_migration_parity.sql](sql/10_validate_migration_parity.sql) | 4 TODOs (`L52`, `L75`, `L85`, `L100`; Track A = `L52–L85`) | [Lab 2](../docs/02_two_path_sdlc_workshop.md) | [10_validate_migration_parity.sql](../solution/sql/10_validate_migration_parity.sql) |
| [ ] | **3** | [sql/01_path_a_schematized.sql](sql/01_path_a_schematized.sql) | 4 TODOs (`L213`, `L278`, `L308`, `L348`, Track A & B) | [Lab 3](../docs/03_database_design_and_ingestion.md) | [01_path_a_schematized.sql](../solution/sql/01_path_a_schematized.sql) |
| [ ] | **3** | [sql/03_indexes_fts_vector_postload.sql](sql/03_indexes_fts_vector_postload.sql) | 3 TODOs (`L25`, `L129`, `L140`, Track A & B) | [Lab 3](../docs/03_database_design_and_ingestion.md) | [03_indexes_fts_vector_postload.sql](../solution/sql/03_indexes_fts_vector_postload.sql) |
| [ ] | **3** | [pipelines/neo4j_to_spanner_loader.py](pipelines/neo4j_to_spanner_loader.py) | `load_graph()` (`L377`, `TODO` at `L389`, Track B) | [Lab 3](../docs/03_database_design_and_ingestion.md) | [neo4j_to_spanner_loader.py](../solution/pipelines/neo4j_to_spanner_loader.py) |
| [ ] | **3** | [pipelines/unstructured_graphrag_ingest.py](pipelines/unstructured_graphrag_ingest.py) | `extract_chunks_and_mentions()` (`L224`, `TODO` at `L233`, Track B) | [Lab 3](../docs/03_database_design_and_ingestion.md) | [unstructured_graphrag_ingest.py](../solution/pipelines/unstructured_graphrag_ingest.py) |
| [ ] | **4** | [sql/05_search_strategies_benchmark.sql](sql/05_search_strategies_benchmark.sql) | 3 TODOs (`L68`, `L82`, `L134`, Track A & B) | [Lab 4](../docs/04_search_strategy_options.md) | [05_search_strategies_benchmark.sql](../solution/sql/05_search_strategies_benchmark.sql) |
| [ ] | **4** | [sql/08_graph_algorithms_criticality.sql](sql/08_graph_algorithms_criticality.sql) | 4 TODOs (`L35`, `L64`, `L76`, `L84`, Track B) | [Lab 4](../docs/04_search_strategy_options.md) | [08_graph_algorithms_criticality.sql](../solution/sql/08_graph_algorithms_criticality.sql) |
| [ ] | **5** | [sql/06_hybrid_rrf_rsf_filtered.sql](sql/06_hybrid_rrf_rsf_filtered.sql) | Query 1 RRF (`L70`, `L83`, Track A & B) | [Lab 5](../docs/05_hybrid_retrieval_and_graphrag.md) | [06_hybrid_rrf_rsf_filtered.sql](../solution/sql/06_hybrid_rrf_rsf_filtered.sql) |
| [ ] | **5** | [sql/07_hero_api_blast_radius_graphrag.sql](sql/07_hero_api_blast_radius_graphrag.sql) | 5 TODOs (`L53`, `L93`, `L118`, `L152`, `L163`, Track A & B) | [Lab 5](../docs/05_hybrid_retrieval_and_graphrag.md) | [07_hero_api_blast_radius_graphrag.sql](../solution/sql/07_hero_api_blast_radius_graphrag.sql) |
| [ ] | **6** | [sql/04_fgac_roles_views.sql](sql/04_fgac_roles_views.sql) | 3 TODOs (`L41`, `L152`, `L198`, Track A & B) | [Lab 6](../docs/06_mcp_toolbox_agent_security.md) | [04_fgac_roles_views.sql](../solution/sql/04_fgac_roles_views.sql) |
| [ ] | **6** | [mcp/tools.yaml](mcp/tools.yaml) | 17 TODOs across 4 passes: models/OIDC (`L34`, `L44`), tool flags/embeddings/regexes & OIDC params (`L139–L604`), toolsets (`L612`) (Track A & B) | [Lab 6](../docs/06_mcp_toolbox_agent_security.md) | [tools.yaml](../solution/mcp/tools.yaml) |
| [ ] | **6** | [mcp/adk_multi_agent_demo.py](mcp/adk_multi_agent_demo.py) | `run_persona_query()` (`L679`, `TODO` at `L690`, Track A & B) | [Lab 6](../docs/06_mcp_toolbox_agent_security.md) | [adk_multi_agent_demo.py](../solution/mcp/adk_multi_agent_demo.py) |
| [ ] | **7** | [tools/customer_scale_worksheet.json](tools/customer_scale_worksheet.json) | `todo_status` (`L2`) & scale numbers (`L35–L46`, Track A & B) | [Lab 7](../docs/07_sizing_and_cost_tco.md) | [sizing_calculator.py](../solution/tools/sizing_calculator.py) |
| [ ] | **8** | [sql/11_observability_and_studio_viz.sql](sql/11_observability_and_studio_viz.sql) | 4 TODOs (`L19`, `L32`, `L44`, `L87`, Track A & B) | [Lab 8](../docs/08_observability_and_troubleshooting.md) | [11_observability_and_studio_viz.sql](../solution/sql/11_observability_and_studio_viz.sql) |

---

## 2. How to Verify as You Go

Run every command from the repository root (one directory up from `starter/`). No Docker or GCP credentials are required for offline checks.

### Offline Checks (Instant, Python 3.10+ Only)

```bash
# Before you start: confirm your machine and see the armed starter stubs fail
make preflight
make lab1-no-docker
make verify-starter-stubs

# After editing Lab N (replace 1 with 1..8)
make TRACK=starter verify-lab LAB=1                  # Track B (full 15-file contract)
make TRACK=starter MODE=express verify-lab LAB=1     # Track A (4h Express subset)

# Run your own starter scripts offline as you finish Labs 3, 5, 6, and 7
python3 starter/pipelines/neo4j_to_spanner_loader.py --dry-run
python3 starter/pipelines/unstructured_graphrag_ingest.py --dry-run
python3 starter/mcp/adk_multi_agent_demo.py --mock
make sizing WORKSHEET=starter/tools/customer_scale_worksheet.json REGION=asia-southeast3

# Compare against the reference solution anytime
make TRACK=solution verify-lab LAB=all
```

### Live Bangkok GCP Checks (Optional)

These commands target your Bangkok (`asia-southeast3`) Spanner instance and call Singapore (`asia-southeast1`) Vertex AI for embeddings.

```bash
# Provision in Lab 3 (pass --skip-fgac until Lab 6's 04_fgac_roles_views.sql is solved; drop it after Lab 6)
bash scripts/provision_workshop_env.sh create --track starter --skip-fgac
# Load Path A & Path B (Track A Express skips starter/pipelines/*.py — run solution/pipelines/*.py instead)
python3 starter/pipelines/neo4j_to_spanner_loader.py
python3 starter/pipelines/unstructured_graphrag_ingest.py
bash scripts/provision_workshop_env.sh apply-postload --track starter

# Execute your edited starter SQL files directly against live Spanner
python3 scripts/run_live_sql.py 09 10 --track starter
python3 scripts/run_live_sql.py 05 08 --track starter
python3 scripts/run_live_sql.py 06 07 --track starter
python3 scripts/run_live_sql.py 11 --track starter

# Compare all 3 Hero query variants (A/B/C) and check the 7-row fixture (uses solution/ 3-query file)
python3 labs/run_hero_query.py --query B --check-fixture solution/sql/fixtures/07_hero_expected_rows.json
```

---

## 3. Natural Antigravity Prompts per Lab

When you open this repo in Antigravity IDE, it automatically picks up [../.agents/rules/spanner-graph-workshop.md](../.agents/rules/spanner-graph-workshop.md) and the 8 skills under `.agents/skills/`. Try prompts like these if you want a pair programmer as you work through `starter/`:

- **Lab 1** ([`spanner-migration-and-neo4j-playbook`](../.agents/skills/spanner-migration-and-neo4j-playbook/SKILL.md)):  
  *"Walk me through the 5 TODOs in `starter/sql/09_opencypher_to_gql_cookbook.sql`. Why does default `WALK` loop on `svc-orders <-> svc-notifications`, and why should I use `SAFE_TO_JSON` here?"*
- **Lab 2** ([`sdlc-graphrag-workshop-guide`](../.agents/skills/sdlc-graphrag-workshop-guide/SKILL.md), [`spanner-enterprise-platform-ops`](../.agents/skills/spanner-enterprise-platform-ops/SKILL.md)):  
  *"Help me fill in the 4 parity check tiers in `starter/sql/10_validate_migration_parity.sql` so they verify the 68 nodes, 174 explicit edges, 60 dual-projected edges, and 45 Markdown docs."*
- **Lab 3** ([`spanner-schema-and-data-modeling`](../.agents/skills/spanner-schema-and-data-modeling/SKILL.md), [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md)):  
  *"Let's finish `starter/sql/01_path_a_schematized.sql` and `starter/sql/03_indexes_fts_vector_postload.sql`. Explain why `ServiceDependsOnService` can't be interleaved with cascading FKs, then help me implement `load_graph()` and `extract_chunks_and_mentions()` in `starter/pipelines/`."*
- **Lab 4** ([`spanner-search-ai-and-graphrag`](../.agents/skills/spanner-search-ai-and-graphrag/SKILL.md), [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md)):  
  *"Complete the FTS synonym, fuzzy n-gram, and ScaNN ANN queries in `starter/sql/05_search_strategies_benchmark.sql`, then help me set up `ServiceDependsOnAlgo` and the Data Boost `PageRank` export in `starter/sql/08_graph_algorithms_criticality.sql`."*
- **Lab 5** ([`spanner-search-ai-and-graphrag`](../.agents/skills/spanner-search-ai-and-graphrag/SKILL.md), [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md)):  
  *"Write the `UNNEST(ARRAY(...)) WITH OFFSET` RRF query in `starter/sql/06_hybrid_rrf_rsf_filtered.sql` without using `ROW_NUMBER()`, then wire up the 5 TODOs in `starter/sql/07_hero_api_blast_radius_graphrag.sql` for `api-payments-charge-v2`."*
- **Lab 6** ([`spanner-mcp-toolbox-and-agent-security`](../.agents/skills/spanner-mcp-toolbox-and-agent-security/SKILL.md)):  
  *"Finish `PublicApiCatalogView` and `sdlc_readonly_role` grants in `starter/sql/04_fgac_roles_views.sql`, wire up regional Vertex AI embeddings, OIDC `authServices`, and the 3 toolsets in `starter/mcp/tools.yaml`, and implement `run_persona_query()` in `starter/mcp/adk_multi_agent_demo.py`."*
- **Lab 7** ([`spanner-performance-observability-and-sizing`](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md)):  
  *"Update `starter/tools/customer_scale_worksheet.json` for a 500k-node Bangkok Enterprise deployment with 40% cold HDD tiering, run the sizing calculator, and explain the 1-year and 3-year CUD trade-offs before setting `todo_status` to `COMPLETED`."*
- **Lab 8** ([`spanner-performance-observability-and-sizing`](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md), [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md)):  
  *"Help me finish the Spanner Studio `SAFE_TO_JSON(p)` path query and the `SPANNER_SYS` query/lock/split queries in `starter/sql/11_observability_and_studio_viz.sql`."*

---

## 4. Common Spanner Gotchas (Save Yourself Debugging Time)

Full reference in [../GEMINI.md](../GEMINI.md) and [../WORKSHOP_CONTENT_PLAN.md](../WORKSHOP_CONTENT_PLAN.md). These seven catch most people first:

1. **Cycle loops in GQL:** Always write `MATCH TRAIL` for variable-length paths. Default `WALK` mode loops endlessly around `svc-orders <-> svc-notifications`.
2. **Self-referencing cascade ban:** Spanner won't let you put `ON DELETE CASCADE` on both `INTERLEAVE IN PARENT T` and a self-referencing foreign key back to `T` on the same table (`INV-06`). Keep `ServiceDependsOnService` as a standalone dual-FK table in Lab 3, and use a `NOT ENFORCED` destination FK on `ServiceDependsOnAlgo` in Lab 4.
3. **No `ROW_NUMBER()` in Spanner GoogleSQL:** Compute reciprocal rank fusion (`k=60`) with `UNNEST(ARRAY(SELECT ... ORDER BY ... LIMIT k)) AS chunk_id WITH OFFSET AS rank` instead (`INV-09`).
4. **ScaNN ANN needs its own CTE:** Keep `APPROX_COSINE_DISTANCE` inside a single-table CTE with `WHERE embedding IS NOT NULL` and zero `JOIN`s inside that `SELECT` block (`INV-08`). Vector columns are `ARRAY<FLOAT32>(vector_length=>768)`.
5. **Read-only transactions for search and stats:** Run all FTS, hybrid, and `SPANNER_SYS` queries in read-only transactions (`readOnly: true` in `tools.yaml`).
6. **`tools.yaml` restrictions:** Never add `databaseRole` to a `type: spanner` source, never expose raw `spanner-execute-sql`, and never use `templateParameters`. Point `gemini-embedding-001` at regional `asia-southeast1` via ADC—never `global` and never a static `apiKey`.
7. **Lock wait units:** `SPANNER_SYS.LOCK_STATS_TOP_HOUR.LOCK_WAIT_DISTRIBUTION` stores **microseconds**. Divide `SPANNER_SYS.DISTRIBUTION_PERCENTILE(...)` by `1000000.0` before displaying seconds.

---

## 5. Quick Command Reference

```bash
make help                                             # Show all 16 Makefile targets
make test                                             # Run all 8 offline unittest modules
make validate-skills                                  # Audit all 8 .agents/skills/ folders
make dry-run-pipelines                                # Offline dry-run of reference loaders & Hero fixture
make demo-mcp-mock                                    # Offline Lab 6 prompt-injection demo
make provision-gcp TRACK=starter                      # [LIVE] Provision Bangkok 100-PU sandbox with starter DDL
make verify-live                                      # [LIVE] Verify live Bangkok instance
make cleanup-all-gcp                                  # [LIVE] Tear down all Bangkok + Singapore workshop resources
```

Want to reset a file and retry a lab from scratch? Run `git checkout -- starter/<path>` (when inside a Git clone) or diff side by side against `solution/`.
