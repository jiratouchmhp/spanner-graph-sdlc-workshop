# Lab 5: Hybrid Retrieval & Hero Blast-Radius GraphRAG

In this lab, you'll combine Full-Text Search (FTS), ScaNN vector search, and cycle-safe ISO GQL traversals inside one read-only Cloud Spanner Enterprise query. By the end, you'll answer Acme Commerce's main change-impact question—*"What breaks if we change `POST /v2/payments/charge` (`api-payments-charge-v2`)?"*—with 7 ranked impacted services, governing ADRs, compliance requirements, and verifiable Markdown citations. All database reads hit Bangkok (`asia-southeast3`), while Vertex AI query embeddings run in Singapore (`asia-southeast1`).

## What You'll Work On

| Step | File to open (`starter/`) | Where to edit (`TODO` / line) | Track |
| :-: | :--- | :--- | :-: |
| 1 | [`starter/sql/06_hybrid_rrf_rsf_filtered.sql`](../starter/sql/06_hybrid_rrf_rsf_filtered.sql) | Query 1 `TODO(Lab 5)` (`L70` & stub `L83`): server-side RRF (`k = 60`) | Track A & B |
| 2 | [`starter/sql/07_hero_api_blast_radius_graphrag.sql`](../starter/sql/07_hero_api_blast_radius_graphrag.sql) | 5 `TODO(Lab 5)` blocks (`L53`, `L93`, `L118`, `L152`, `L163`): ScaNN ANN, RRF, `MATCH TRAIL`, governance, final `SELECT` | Track A & B |
| 3 | Offline verification & Hero replay | Run `make TRACK=starter verify-lab LAB=5` & `--dry-run` 7-row fixture check | Track A & B |
| 4 | *(Optional — Live Bangkok GCP)* | Run your starter SQL live, compare Queries A/B/C, and run the ACID zero-sync-lag demo | Track B (Optional) |

- **Estimated time:** ~45 min (Track A Express) / ~75 min (Track B Full)
- **Reference copy (read-only if stuck):** [`solution/sql/06_hybrid_rrf_rsf_filtered.sql`](../solution/sql/06_hybrid_rrf_rsf_filtered.sql), [`solution/sql/07_hero_api_blast_radius_graphrag.sql`](../solution/sql/07_hero_api_blast_radius_graphrag.sql), [`solution/sql/fixtures/07_hero_expected_rows.json`](../solution/sql/fixtures/07_hero_expected_rows.json), [`labs/run_hero_query.py`](../labs/run_hero_query.py)
- **Antigravity skills:** [`spanner-search-ai-and-graphrag`](../.agents/skills/spanner-search-ai-and-graphrag/SKILL.md), [`spanner-graph-modeling-and-gql`](../.agents/skills/spanner-graph-modeling-and-gql/SKILL.md) (support: [`spanner-performance-observability-and-sizing`](../.agents/skills/spanner-performance-observability-and-sizing/SKILL.md))

---

## Quick Background

Neither search method works well alone on SDLC docs. FTS nails exact tokens like `tok_pci_v2`, `ADR-004`, and `45ms`, while vectors surface conceptual prose when wording differs. Pairing both with `GRAPH_TABLE(SdlcGraph MATCH TRAIL ...)` traces upstream callers safely past the `svc-orders <-> svc-notifications` cycle and grounds the answer in one ACID snapshot.

Keep two Spanner GoogleSQL rules in mind before editing:
- **No `ROW_NUMBER()` window functions (`INV-09`):** Spanner GoogleSQL doesn't support general analytic ranking functions. Instead, collect sorted `chunk_id` values into an `ARRAY(...)` subquery and unnest them with `WITH OFFSET AS rank` (`0`-based, so `1.0 / (60.0 + rank + 1.0)` gives exact 1-based RRF). Also execute all FTS queries in read-only transactions.
- **Keep ScaNN in a single-table CTE (`INV-08`):** Put `APPROX_COSINE_DISTANCE` inside a standalone CTE over `DocumentChunks` with `WHERE embedding IS NOT NULL` and zero `JOIN`s so Spanner uses `IdxDocumentChunksEmbedding`.

For `POST /v2/payments/charge` (`api-payments-charge-v2`), your Hero query should return these exact 7 services in order (`hop_distance ASC`, `impact_category_rank ASC`, criticality rank `CRITICAL=1..LOW=4`, `impacted_service_id ASC`):

| # | `impacted_service_id` | Hop | `impact_category` | Tier / Criticality | Owner (`slack_channel`) | `has_legacy_v1_risk` |
| :-: | :--- | :-: | :--- | :--- | :--- | :-: |
| 1 | `svc-billing` | 1 | `DIRECT_SYNC` | `TIER_0` / `CRITICAL` | `team-billing` (`#billing-ledger`) | `TRUE` *(calls deprecated v1, `AT_RISK`)* |
| 2 | `svc-checkout` | 1 | `DIRECT_SYNC` | `TIER_0` / `CRITICAL` | `team-checkout` (`#checkout-orders`) | `FALSE` |
| 3 | `svc-orders` | 2 | `TRANSITIVE_UPSTREAM` | `TIER_1` / `HIGH` | `team-checkout` (`#checkout-orders`) | `FALSE` |
| 4 | `svc-partner-gw` | 2 | `TRANSITIVE_UPSTREAM` | `TIER_1` / `HIGH` | `team-integrations` (`#partner-messaging`) | `FALSE` |
| 5 | `svc-notifications` | 3 | `TRANSITIVE_UPSTREAM` | `TIER_2` / `MEDIUM` | `team-integrations` (`#partner-messaging`) | `FALSE` |
| 6 | `svc-webhooks` | 3 | `TRANSITIVE_UPSTREAM` | `TIER_2` / `MEDIUM` | `team-integrations` (`#partner-messaging`) | `FALSE` |
| 7 | `svc-analytics` | 3 | `DATA_FEED_CONSUMER` | `TIER_2` / `LOW` | `team-data` (`#data-analytics`) | `FALSE` |

- **5 governing ADRs:** `ADR-004` (`MANDATES`), `ADR-005`, `ADR-006`, `ADR-008`, `ADR-010` (`CONSTRAINS`).
- **5 applicable requirements:** `REQ-001` (PCI-DSS 4.0), `REQ-002` (Tier-0 SLA & `45ms` fraud budget), `REQ-003` (`> $500` fail-closed), `REQ-005` (SOX `Idempotency-Key`), `REQ-006` (Zero-Trust mTLS/OIDC).

---

## Step-by-Step Hands-On

### Step 1: Implement server-side RRF (`k = 60`) in `starter/sql/06_hybrid_rrf_rsf_filtered.sql` (Track A & B)

1. Open [`starter/sql/06_hybrid_rrf_rsf_filtered.sql`](../starter/sql/06_hybrid_rrf_rsf_filtered.sql) and jump to the `TODO(Lab 5)` comment at **line 70** and the stub `SELECT` at **line 83**.
2. Notice `vec_top` and `fts_top` are already written above line 70. Add a `fused_rrf` CTE that ranks each branch with `UNNEST(ARRAY(SELECT chunk_id FROM ... ORDER BY ...)) AS chunk_id WITH OFFSET AS rank` (never `ROW_NUMBER()`), unions both lists with `UNION ALL`, and aggregates `SUM(1.0 / (60.0 + rank + 1.0)) AS rrf_score` plus `COUNT(*) AS matched_retrievers` grouped by `chunk_id`. Keep the top 10 ordered by `rrf_score DESC, chunk_id ASC`.
3. Replace the placeholder `SELECT` on **line 83** with a join from `fused_rrf` back to `DocumentChunks` projecting `chunk_id`, `ROUND(rrf_score, 6) AS rrf_score`, `matched_retrievers`, `doc_type`, `entity_id`, `source_path`, `section_heading`, and `SUBSTR(content, 1, 240) AS excerpt`.
4. Read through **Queries 2–4 below line 90**—they are already complete for reference and live execution:
   - Query 2 (`L91`): Relative Score Fusion (RSF) using `@{optimizer_version=latest}` (required when a `SCORE()` CTE is referenced multiple times) and `NULLIF` divide-by-zero guards.
   - Query 3 (`L168`): Filtered Search (FTS narrows to `LIMIT 1000`, then exact `COSINE_DISTANCE` re-ranks).
   - Query 4 (`L200`): Two-stage ScaNN retrieval with optional `ML.PREDICT(MODEL SdlcReranker, ...)` cross-encoder reranking.

*(Optional Antigravity prompt)*:
> "Help me complete the RRF query in `starter/sql/06_hybrid_rrf_rsf_filtered.sql` using `UNNEST(ARRAY(...)) WITH OFFSET` (`k = 60`) instead of `ROW_NUMBER()`, then join back to `DocumentChunks`."

### Step 2: Complete the Hero GraphRAG query in `starter/sql/07_hero_api_blast_radius_graphrag.sql` (Track A & B)

Open [`starter/sql/07_hero_api_blast_radius_graphrag.sql`](../starter/sql/07_hero_api_blast_radius_graphrag.sql) and work through its five `TODO(Lab 5)` blocks in order:

1. **Single-table ScaNN ANN (`L53`):** Replace the stub `vec_top` CTE with an `APPROX_COSINE_DISTANCE(embedding, @query_vec, options => JSON '{"num_leaves_to_search": 10}')` query over `DocumentChunks`, filtered by `WHERE embedding IS NOT NULL`, with zero `JOIN`s and `ORDER BY approx_distance ASC LIMIT 20`. (`fts_top` right below at **line 70** is already done.)
2. **Server-side RRF (`L93`):** Replace the stub `rrf_ranked` CTE to fuse `vec_top` and `fts_top` using `UNNEST(ARRAY(...)) WITH OFFSET AS rank` and `SUM(1.0 / (60.0 + rank + 1.0)) AS rrf_score`.
3. **Hops 2–4 upstream `MATCH TRAIL` (`L118`):** Inside `raw_blast`, add a `UNION ALL` branch calling `GRAPH_TABLE(SdlcGraph MATCH TRAIL (upstream:Service)-[deps:DEPENDS_ON]->{1,3}(direct:Service)-[:CALLS_API]->(target:Api {api_id: @api_id}) ...)`. Collect `ARRAY_AGG(deps.dependency_type)` as `dep_types`, compute `ARRAY_LENGTH(deps) + 1 AS hop_distance`, and classify any path with `'DATA_FEED'` as `'DATA_FEED_CONSUMER'` (separating `svc-analytics` from `'TRANSITIVE_UPSTREAM'`).
4. **Governance & citations CTE (`L152`):** Add the `api_governance` CTE with three `ARRAY(SELECT AS STRUCT ...)` subqueries: `governing_adrs` (`AdrGovernsApi JOIN Adrs`), `applicable_requirements` (`ReqAppliesToApi JOIN Requirements`), and `api_citations` (top 8 RRF-ranked `DocumentChunks` linked through `ChunkEntityMentions` to `@api_id`, its governing ADRs, or its applicable requirements).
5. **Final enriched `SELECT` (`L163`):** Join `collapsed_blast` with `Services`, `Owners`, and `CROSS JOIN api_governance`. Add `has_legacy_v1_risk` (an `EXISTS` check flagging `ServiceCallsApi.migration_status = 'AT_RISK'` or `Apis.lifecycle_status = 'DEPRECATED'`, which evaluates to `TRUE` for `svc-billing`), `service_citations` (top 3 chunks mentioning `cb.impacted_service_id`), and the governance/citation fields. Order deterministically by `hop_distance ASC`, `impact_category_rank ASC`, criticality rank (`CRITICAL=1`, `HIGH=2`, `MEDIUM=3`, `LOW=4`), and `impacted_service_id ASC`.

*(Optional Antigravity prompt)*:
> "Walk me through the five `TODO(Lab 5)` blocks in `starter/sql/07_hero_api_blast_radius_graphrag.sql`—keeping ScaNN isolated in `vec_top`, fusing with `WITH OFFSET` RRF, and separating `DATA_FEED_CONSUMER` paths in `MATCH TRAIL`."

### Step 3: Verify Lab 5 offline and check the 7-row fixture (Track A & B)

Run the offline lab check against your starter files and replay the Hero 7-row fixture without needing GCP credentials:

```bash
make TRACK=starter verify-lab LAB=5

python3 labs/run_hero_query.py \
  --dry-run \
  --query B \
  --check-fixture solution/sql/fixtures/07_hero_expected_rows.json
```

### Step 4 (Optional — Live Bangkok GCP): Run your SQL live, compare variants, and test zero-sync-lag ACID

1. **Run your completed Lab 5 files live** against your Bangkok (`asia-southeast3`) Spanner instance (`run_live_sql.py` automatically fetches 768-dim query embeddings from Vertex AI in Singapore `asia-southeast1`):
   ```bash
   python3 scripts/run_live_sql.py 06 07 --track starter
   ```
   *(Note: Don't point `labs/run_hero_query.py` at `starter/sql/07_hero_api_blast_radius_graphrag.sql`—your starter file holds only the single production ScaNN Hero query, while `run_hero_query.py` expects all three A, B, and C variants from `solution/`.)*

2. **Compare the reference Hero variants** (Query B ScaNN ANN and Query A Exact KNN) against the 7-row fixture (also a quick shortcut if you're short on time):
   ```bash
   python3 labs/run_hero_query.py \
     --query B \
     --check-fixture solution/sql/fixtures/07_hero_expected_rows.json

   python3 labs/run_hero_query.py \
     --query A \
     --check-fixture solution/sql/fixtures/07_hero_expected_rows.json
   ```
   *(Note: Running `--query C` or `--query all` live also queries the schemaless `SdlcDynamicGraph`, which requires having provisioned `02_path_b_schemaless_comparison.sql` with `--with-dynamic-graph` and loaded it with `--load-dynamic-graph` in Lab 3.)*

3. **Live ACID zero-sync-lag demo:** Because graph edges, Markdown chunks, and embeddings live in one Spanner database, a newly committed dependency edge and doc chunk show up in the Hero query immediately—no CDC lag or index sync delay:
   ```bash
   # 1. Confirm baseline returns 7 impacted services
   python3 labs/run_hero_query.py --query B --api-id api-payments-charge-v2

   # 2. Insert a new CALLS_API edge + doc chunk + mention into Bangkok Spanner
   gcloud spanner databases execute-sql sdlc_knowledge_graph \
     --instance=sdlc-kg-enterprise \
     --sql="INSERT INTO ServiceCallsApi (service_id, api_id, call_mode, is_critical, migration_status, rps_estimate)
            VALUES ('svc-inventory', 'api-payments-charge-v2', 'SYNC', TRUE, 'MIGRATED', 75)"

   gcloud spanner databases execute-sql sdlc_knowledge_graph \
     --instance=sdlc-kg-enterprise \
     --sql="INSERT INTO DocumentChunks (chunk_id, doc_type, entity_id, source_path, section_heading, chunk_index, content, content_hash)
            VALUES ('demo__live_acid_c00', 'api', 'api-payments-charge-v2', 'data/unstructured/apis/live-acid-demo.md',
                    'Live Zero-Sync-Lag Proof', 0,
                    'svc-inventory now invokes POST /v2/payments/charge (api-payments-charge-v2) for instant stock settlement with tok_pci_v2.',
                    'live-acid-demo-hash')"

   gcloud spanner databases execute-sql sdlc_knowledge_graph \
     --instance=sdlc-kg-enterprise \
     --sql="INSERT INTO ChunkEntityMentions (chunk_id, entity_type, entity_id, mention_source)
            VALUES ('demo__live_acid_c00', 'API', 'api-payments-charge-v2', 'LIVE_DEMO'),
                   ('demo__live_acid_c00', 'SERVICE', 'svc-inventory', 'LIVE_DEMO')"

   # 3. Re-run immediately: now shows 8 services (svc-inventory at hop 1) + live-acid-demo.md
   python3 labs/run_hero_query.py --query B --api-id api-payments-charge-v2

   # 4. Delete the demo rows to restore the 7-row baseline (ChunkEntityMentions cascades automatically)
   gcloud spanner databases execute-sql sdlc_knowledge_graph \
     --instance=sdlc-kg-enterprise \
     --sql="DELETE FROM ServiceCallsApi WHERE service_id = 'svc-inventory' AND api_id = 'api-payments-charge-v2'"
   gcloud spanner databases execute-sql sdlc_knowledge_graph \
     --instance=sdlc-kg-enterprise \
     --sql="DELETE FROM DocumentChunks WHERE chunk_id = 'demo__live_acid_c00'"
   ```

---

## Verify This Lab

```bash
make TRACK=starter verify-lab LAB=5
```

*(Both starter files are core to Track A and Track B, so `MODE=express` checks the same two files.)*

**Passing output:** `[PASS] Lab 5 (starter/, mode=full)` confirming `06_hybrid_rrf_rsf_filtered.sql`, `07_hero_api_blast_radius_graphrag.sql`, and the 7-row Hero fixture replay all pass.

**If it fails, check:**
- Leftover `TODO(Lab 5)` markers or the placeholder `SELECT 'TODO(Lab 5)...'` line at `L83` in `06_hybrid_rrf_rsf_filtered.sql`.
- Any `ROW_NUMBER()` call—replace it with `UNNEST(ARRAY(...)) WITH OFFSET`.
- Missing `WHERE embedding IS NOT NULL`, `MATCH TRAIL`, `DATA_FEED_CONSUMER`, `AdrGovernsApi`, `ReqAppliesToApi`, or `ChunkEntityMentions` in `07_hero_api_blast_radius_graphrag.sql`.

---

**Previous / Next:** [← Lab 4: Search Strategy Options](04_search_strategy_options.md) | [Starter Guide](../starter/README.md) | [Lab 6: MCP Toolbox & Agent Security →](06_mcp_toolbox_agent_security.md)
