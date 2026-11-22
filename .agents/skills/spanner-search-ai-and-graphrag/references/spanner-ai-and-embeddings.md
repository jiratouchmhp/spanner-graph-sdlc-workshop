# Spanner AI, Vertex AI Embeddings, Semantic SQL & Remote Functions

Comprehensive production reference for invoking **Vertex AI** models directly
from Cloud Spanner SQL (`ML.PREDICT` / `spanner.ML_PREDICT_ROW`), running
**native Gemini semantic SQL functions** (`AI.IF`, `AI.CLASSIFY`, `AI.SCORE`),
calling **Cloud Run Remote Functions** (`LANGUAGE REMOTE`), and integrating
with **LangChain** (`langchain-google-spanner`).

## 1. Architecture, Capabilities & Billing Model

Cloud Spanner does **not** host or train ML models inside database nodes. For
training on Spanner data, export via Dataflow to Cloud Storage -> Vertex AI, or
run BigQuery Federated Queries with BigQuery ML. For online inference, Spanner
provides four SQL/application integration paths:

1. **Spanner ML (Vertex AI `predict` API):** Invoke deployed Vertex AI custom
   endpoints and Model Garden publisher models directly from SQL (`ML.PREDICT`
   in GoogleSQL; `spanner.ML_PREDICT_ROW` in PostgreSQL).
2. **Native Semantic AI SQL Functions (GoogleSQL only):** `AI.CLASSIFY`,
   `AI.IF`, and `AI.SCORE` backed by Vertex AI Gemini LLMs in the same project.
3. **Remote Functions (Preview, GoogleSQL only):** Call custom Cloud Run or
   Cloud Run Functions HTTPS endpoints (including 3rd-party APIs such as OpenAI)
   via `CREATE FUNCTION ... LANGUAGE REMOTE`.
4. **Application & Agent SDKs:** `langchain-google-spanner` (Preview) for Python
   RAG/GraphRAG workflows. For MCP Toolbox for Databases, Antigravity/Gemini CLI
   setup, and FGAC security, use sibling skill
   `spanner-mcp-toolbox-and-agent-security`.

### Pricing & Network Locality
- **Zero Spanner ML feature surcharge:** There is no extra Spanner fee for
  calling Vertex AI. You pay standard:
  1. **Vertex AI online prediction charges** (hourly deployed node rate for
     custom endpoints, or per-call/per-token pricing for publisher models —
     isolate per-call models in a dedicated project with explicit quota caps).
  2. **Spanner compute capacity** for executing queries and joining results.
  3. **Spanner network egress** between the Spanner query region and the Vertex
     AI endpoint region.
- **Regional Topology Example (Southeast Asia):** When your Spanner Enterprise
  instance runs in Bangkok (`regional-asia-southeast3`, 3-zone 99.99% SLA,
  \$1.2915/node-hr) for data residency and invokes Vertex AI regional endpoints
  in Singapore (`asia-southeast1`, ~25 ms RTT), cross-region Spanner-to-Vertex-AI
  transfer applies. Always choose the same region or nearest continent region.
- **Hard I/O Constraint:** Vertex AI model inputs and outputs must be **JSON
  objects**.

## 2. Supported Text Embedding Models & Regional Rules

Spanner does not maintain a static model allowlist; `ML.PREDICT` and
`spanner.ML_PREDICT_ROW` call the Vertex AI **`predict` API** at the configured
endpoint.

| Publisher Model ID (`publishers/google/models/{id}`) | Max Dimensions | Supported Locations | `ML.PREDICT` / `ML_PREDICT_ROW` |
| --- | --- | --- | --- |
| `gemini-embedding-001` | Up to **3,072** (Matryoshka `outputDimensionality`, e.g., `768`) | **Regional ONLY** (`asia-southeast1`, `us-central1`, `europe-west1`, ...) | **Supported — Recommended** |
| `text-embedding-005` | Up to **768** | **Regional ONLY** | Supported |
| `text-multilingual-embedding-002` | Up to **768** | **Regional ONLY** | Supported |
| `gemini-embedding-2` | N/A | `global`, `us`, `eu` only | **Unsupported** (uses `embedContent`, not `predict`) |
| Custom-tuned text embedding models | N/A | N/A | **Unsupported** |

### Four Critical Embedding Gotchas
1. **Regional-Only Endpoints:** Even though Spanner's URL parser accepts
   `global`, `us`, and `eu`, **none** of the Vertex AI `predict`-API text
   embedding models support `global`, `us`, or `eu`. Always target a regional
   endpoint (e.g., `asia-southeast1` for Bangkok deployments).
2. **Deferred Validation Trap:** `CREATE MODEL` does **not** validate publisher
   `predict` endpoints or GenAI schemas at DDL execution time (schema discovery
   is unavailable for GenAI models). Bad model IDs or unsupported regions fail
   at **query time** with `NOT_FOUND`; mismatched `OUTPUT` schemas fail at
   **query time** with `INVALID_ARGUMENT`. Always run a `LIMIT 10` smoke query
   immediately after `CREATE MODEL`.
3. **Version Pinning & Provenance Tracking:** Pin an explicit versioned model
   ID (e.g., `gemini-embedding-001` or `@003`) rather than `@latest`. Running
   `ALTER MODEL` does **not** recompute already-stored vectors — add a
   `{embedding_col}_model_version` (`INT64` or `STRING`) column to track vector
   provenance and drive controlled re-embedding.
4. **Transaction Lock Anti-Pattern:** **Never** invoke `ML.PREDICT` inside a
   latency-sensitive **read-write transaction** (external RPCs hold Spanner
   locks and inflate contention). Compute embeddings in a **read-only
   transaction** and write results in a separate read-write transaction, or use
   throttled **Partitioned DML** for bulk backfills.
5. **Explicit `outputDimensionality` for `gemini-embedding-001`:** Calling
   `ML.PREDICT(MODEL {model}, ...)` against `gemini-embedding-001` **defaults to
   `3072` dimensions** unless you pass **`STRUCT(768 AS outputDimensionality)`**
   (matching your `vector_length=>N`) as the **3rd `params` argument**. Omitting
   the 3rd argument causes dimension-mismatch failures on `768`-dim columns and
   distance functions.

## 3. `CREATE MODEL`, `ALTER MODEL`, `DROP MODEL` (GoogleSQL Only)

PostgreSQL dialect does **not** register models via DDL (it passes endpoint
paths directly to `spanner.ML_PREDICT_ROW`).

```sql
{ CREATE MODEL | CREATE OR REPLACE MODEL | CREATE MODEL IF NOT EXISTS } {model_name}
[ INPUT ( column_name data_type [ OPTIONS ( required = { true | false } ) ] [, ...] )
  OUTPUT ( column_name data_type [ OPTIONS ( required = { true | false } ) ] [, ...] ) ]
REMOTE
[ OPTIONS (
    endpoint = '{endpoint_address}',            -- mutually exclusive with endpoints
    endpoints = [ '{endpoint_address}', ... ],  -- ordered HA failover list
    default_batch_size = 5                      -- integer 1..10; set 1 if no batching
) ];

ALTER MODEL [ IF EXISTS ] {model_name}
SET OPTIONS ( endpoint = '...' | endpoints = [...] | default_batch_size = 5 | option = null );

DROP MODEL [ IF EXISTS ] {model_name};
```

### Endpoint Formats, Data Types & The `OUTPUT` Struct Trap
- **Valid Endpoint Formats:**
  - `//aiplatform.googleapis.com/projects/{project_id}/locations/{location}/endpoints/{endpoint_id}`
  - `//aiplatform.googleapis.com/projects/{project_id}/locations/{location}/publishers/{publisher}/models/{model_id}`
  - `https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/locations/{location}/endpoints/{endpoint_id}`
  - `https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/locations/{location}/publishers/{publisher}/models/{model_id}`
- **Supported `INPUT` / `OUTPUT` Types:** `BOOL`, `BYTES`, `FLOAT32`, `FLOAT64`,
  `INT64` (map 32-bit ints to `INT64`), `STRING`, `STRUCT`, and `ARRAY` of
  supported types.
- **Schema Discovery:** Omitting `INPUT`/`OUTPUT` works **only** when a custom
  Vertex AI endpoint defines instance/prediction schemas. It is **unavailable**
  for GenAI publisher models.
- **`OUTPUT` Struct Requirement:** Vertex AI text embedding responses wrap
  vectors in `{"embeddings": {"statistics": {"truncated": ..., "token_count": ...}, "values": [...]}}`.
  Declaring `OUTPUT (embeddings ARRAY<FLOAT32>)` fails with `INVALID_ARGUMENT`;
  you **must** declare `embeddings` as a `STRUCT`:

```sql
CREATE OR REPLACE MODEL {embedding_model}
INPUT (
  content STRING(MAX),
  task_type STRING(MAX) OPTIONS (required = false)
)
OUTPUT (
  embeddings STRUCT<
    statistics STRUCT<truncated BOOL, token_count FLOAT64>,
    values ARRAY<FLOAT32>
  >
)
REMOTE OPTIONS (
  endpoint = '//aiplatform.googleapis.com/projects/{project_id}/locations/asia-southeast1/publishers/google/models/gemini-embedding-001',
  default_batch_size = 5
);
```

- **`default_batch_size` vs Query Hint:** DDL accepts integers **`1` to `10`**
  (setting `option = null` in `ALTER MODEL` resets to default). Per-query hint
  `@{remote_udf_max_rows_per_rpc=N}` overrides `default_batch_size` (e.g., up
  to `200` where supported by the endpoint; must be `1` for unbatched LLMs).
- **`DROP MODEL`:** Removes only the Spanner metadata entry (never deletes the
  Vertex AI endpoint). Queries referencing the dropped model fail immediately.

## 4. IAM Service Agent, Compliance & Multi-Endpoint HA Failover

### IAM Provisioning
In same-project setups, Spanner automatically creates its service agent
(`service-{project_number}@gcp-sa-spanner.iam.gserviceaccount.com`) on the
first `CREATE MODEL` DDL. For cross-project Vertex AI isolation (recommended
for production/backfill quota separation), grant access explicitly:

```bash
# 1. Ensure Spanner service agent exists in the Spanner project
gcloud beta services identity create \
  --service=spanner.googleapis.com \
  --project="{spanner_project_id}"

# 2. Grant Spanner Service Agent role on the target Vertex AI project
SPANNER_PROJECT_NUMBER="$(gcloud projects describe "{spanner_project_id}" --format="value(projectNumber)")"
gcloud projects add-iam-policy-binding "{vertex_project_id}" \
  --member="serviceAccount:service-${SPANNER_PROJECT_NUMBER}@gcp-sa-spanner.iam.gserviceaccount.com" \
  --role="roles/spanner.serviceAgent"
```

- **Assured Workloads & VPC-SC:** Assured Workloads does **not** support the
  Vertex AI Prediction API (`restrict resource usage` disables Spanner ML).
  Enforce a **VPC Service Controls (VPC-SC)** perimeter so production Spanner
  instances cannot call non-production Vertex AI endpoints.

### High Availability Failover (`endpoints = [...]`) SLA Math
Because Vertex AI online prediction SLA (99.5%–99.9%) is lower than Spanner's
SLA (99.99% regional / 99.999% multi-region), configure `endpoints = [...]`
across distinct regions and separate GCP projects (isolating per-project quota).
Failover executes in list order and accepts lighter fallback models provided
schemas are mergeable (matching column names/types; input required if **any**
endpoint requires it; output required only if **all** endpoints require it):

| Spanner SLA | Vertex AI SLA | 1 row/query | 10 rows/query | 100 rows/query | 1,000 rows/query |
| --- | --- | :---: | :---: | :---: | :---: |
| 99.99% (Regional, e.g., `asia-southeast3`) | 99.9% | 2 endpoints | 2 endpoints | 2 endpoints | 3 endpoints |
| 99.99% | 99.5% | 2 endpoints | 3 endpoints | 3 endpoints | 4 endpoints |
| 99.999% (Multi-region) | 99.9% | 2 endpoints | 2 endpoints | 3 endpoints | 3 endpoints |
| 99.999% | 99.5% | 3 endpoints | 4 endpoints | 4 endpoints | 4 endpoints |

## 5. Invoking Embeddings & Bulk Backfill via Partitioned DML

### GoogleSQL (`ML.PREDICT`) vs PostgreSQL (`spanner.ML_PREDICT_ROW`)

| Feature | GoogleSQL (`ML.PREDICT`) | PostgreSQL (`spanner.ML_PREDICT_ROW`) |
| --- | --- | --- |
| Function Type | Table-valued function (TVF) | Scalar function returning `jsonb` |
| Registration | `CREATE MODEL` required | None; pass endpoint string or JSONB `{"endpoint": "...", "default_batch_size": 1}` |
| Pass-Through | Non-input columns pass through; output col overrides same-named input | Standard scalar projection in `SELECT` |
| Safe Mode | `SAFE.ML.PREDICT(...)` (returns `NULL` on error) | No `SAFE.` prefix |
| Vector Cast | Native `embeddings.values` (`ARRAY<FLOAT32>`) | `spanner.float32_array(...->'predictions'->0->'embeddings'->'values')` |
| Batch Hint | `@{remote_udf_max_rows_per_rpc=200}` | `/*@ remote_udf_max_rows_per_rpc=200 */` |

### Query-Time Embedding with Task Type & `outputDimensionality`
```sql
-- GoogleSQL: RETRIEVAL_QUERY with 768-dim Matryoshka projection
SELECT d.doc_id, d.title,
  COSINE_DISTANCE(
    d.embedding,
    (
      SELECT embeddings.values
      FROM ML.PREDICT(
        MODEL {embedding_model},
        (SELECT @user_query AS content, 'RETRIEVAL_QUERY' AS task_type),
        STRUCT(768 AS outputDimensionality)
      )
    )
  ) AS distance
FROM {table_name} AS d
WHERE d.embedding IS NOT NULL
ORDER BY distance ASC
LIMIT 5;
```

```sql
-- PostgreSQL: inline Vertex AI predict call + float32_array cast
SELECT d.doc_id, d.title,
  spanner.cosine_distance(
    d.embedding,
    spanner.float32_array(
      spanner.ml_predict_row(
        'projects/{project_id}/locations/asia-southeast1/publishers/google/models/gemini-embedding-001',
        jsonb_build_object(
          'instances', jsonb_build_array(
            jsonb_build_object('content', $1, 'task_type', 'RETRIEVAL_QUERY')
          ),
          'parameters', jsonb_build_object('outputDimensionality', 768)
        )
      )->'predictions'->0->'embeddings'->'values'
    )
  ) AS distance
FROM {table_name} AS d
WHERE d.embedding IS NOT NULL
ORDER BY distance ASC
LIMIT 5;
```

### Idempotent Bulk Embedding Backfill (Partitioned DML)
Run during low-traffic windows against a dedicated non-production Vertex AI
project so backfill traffic never starves production prediction quotas:

```sql
-- GoogleSQL Partitioned DML (idempotent: re-runnable on partial quota failures)
@{pdml_max_parallelism=5}
UPDATE {table_name}
SET
  {table_name}.embedding = (
    SELECT embeddings.values
    FROM SAFE.ML.PREDICT(
      MODEL {embedding_model},
      (SELECT {table_name}.content AS content, 'RETRIEVAL_DOCUMENT' AS task_type),
      STRUCT(768 AS outputDimensionality)
    ) @{remote_udf_max_rows_per_rpc=100}
  ),
  {table_name}.embedding_model_version = 1
WHERE {table_name}.embedding IS NULL;
```

```sql
-- PostgreSQL Partitioned DML
UPDATE {table_name}
SET
  embedding = spanner.float32_array(
    spanner.ml_predict_row(
      'projects/{project_id}/locations/asia-southeast1/publishers/google/models/gemini-embedding-001',
      jsonb_build_object(
        'instances', jsonb_build_array(
          jsonb_build_object('content', content, 'task_type', 'RETRIEVAL_DOCUMENT')
        ),
        'parameters', jsonb_build_object('outputDimensionality', 768)
      )
    ) /*@ remote_udf_max_rows_per_rpc=100 */ ->'predictions'->0->'embeddings'->'values'
  ),
  embedding_model_version = 1
WHERE embedding IS NULL;
```

- **PDML Operational Limits:**
  - **Auto-Retry Cap:** Under PDML, Spanner retries Vertex AI quota-throttled
    requests up to **20 times**. Reduce `@{pdml_max_parallelism=N}` if
    throttling persists.
  - **gRPC Client Timeout:** Default Spanner API timeout is **1 hour** —
    configure a higher client deadline for multi-million-row backfills.
  - **Token-Per-Request Limit:** On `INVALID_ARGUMENT` ("Request is too large"),
    lower `default_batch_size`, `@{remote_udf_max_rows_per_rpc}`, or
    `@{remote_udf_max_outstanding_rpcs}` (note: raising
    `remote_udf_max_outstanding_rpcs` increases Spanner CPU/memory usage and
    overrides `default_batch_size`).
  - **Concurrency & Monitoring:** Up to **20,000** concurrent PDML statements
    per database; inspect Vertex AI request rate, latency, and bytes in Spanner
    **System Insights**.

## 6. Native Semantic AI SQL Functions & Generative `ML.PREDICT`

### `AI.CLASSIFY`, `AI.IF`, `AI.SCORE` (GoogleSQL Only)
Evaluated via **Vertex AI Gemini LLMs in the same GCP project** (enable Vertex
AI API; no `CREATE MODEL` DDL required). All three support the `SAFE.` prefix
to return `NULL` on quota or transient failures:

| Function | Signature | Return | Key Constraints & Optimizer Behavior |
| --- | --- | --- | --- |
| `AI.CLASSIFY` | `AI.CLASSIFY(prompt, categories)` | `STRING` | `categories` must be string literals: `ARRAY<STRING>` or `ARRAY<STRUCT<STRING, STRING>>` (`(label, description)`). Include `'Other'` for unmatched rows. |
| `AI.IF` | `AI.IF(prompt)` | `BOOL` | Semantic filter or `JOIN ON` predicate. Returns `NULL` on `NULL` or `''`; `VOLATILE`. **Non-AI SQL predicates evaluate FIRST** before calling `AI.IF`. |
| `AI.SCORE` | `AI.SCORE(prompt)` | `FLOAT64` | Rubric-based score for `ORDER BY ... DESC LIMIT K`. No implicit scale — always state an explicit numeric scale (e.g., `1 to 10`) in `prompt`. |

```sql
SELECT
  AI.SCORE(CONCAT('Rate urgency from 1 to 10. Ticket: ', summary)) AS urgency_score,
  AI.CLASSIFY(summary, [
    ('outage', 'Production service unavailability or elevated error rate'),
    ('billing', 'Invoice, charge, or settlement discrepancy'),
    ('other', 'Other issue')
  ]) AS category,
  ticket_id,
  summary
FROM {table_name}
WHERE status = 'OPEN'  -- Cheap SQL filter runs before AI.IF
  AND AI.IF(CONCAT('This ticket indicates a customer-facing timeout: ', summary))
ORDER BY urgency_score DESC
LIMIT 10;
```

### Generative AI via `ML.PREDICT`
For generative models that do **not** support multi-row batching, set
`default_batch_size = 1` in `CREATE MODEL` or pass
`@{remote_udf_max_rows_per_rpc=1}` in the query, and pass inference parameters
via `STRUCT(256 AS maxOutputTokens, 0.2 AS temperature, 40 AS topK, 0.95 AS topP)`.

## 7. Spanner Remote Functions (`LANGUAGE REMOTE`, Preview)

Use Cloud Run / Cloud Run Functions to invoke 3rd-party APIs (e.g., OpenAI
embeddings) or custom Python/Go inference from **GoogleSQL only**.

### Hard Limitations (Verify Before Designing)
1. **No Fractional Instances (`< 1,000` PUs):** Remote Functions are
   **unsupported** on instances smaller than **1 full node (1,000 PUs)** (e.g.,
   a 100 PU dev instance cannot call Remote Functions; 1 full Enterprise node
   in `asia-southeast3` is \$1.2915/hr on-demand).
2. **Named Schema & Scalar Return Only:** Create inside a named schema
   (`CREATE SCHEMA udfs;`). No TVFs, no generated-column expressions, and no
   Cloud Run custom domains.
3. **At-Least-Once Delivery:** Spanner may resend duplicate batches after transient
   network failures or restarts — design endpoints to be **idempotent**.
4. **Supported Types:** `ARRAY` (including `ARRAY<FLOAT32>`), `BOOLEAN`, `BYTES`,
   `DATE`, `JSON`, `INT64`, `NUMERIC`, `STRING`, `TIMESTAMP`.

```sql
CREATE SCHEMA udfs;

CREATE FUNCTION udfs.GetExternalEmbedding(text STRING)
RETURNS ARRAY<FLOAT32> NOT DETERMINISTIC
LANGUAGE REMOTE OPTIONS (
  endpoint = 'https://asia-southeast3-{project_id}.cloudfunctions.net/{function_name}',
  max_batching_rows = 20
);
```

### BigQuery-Compatible HTTPS JSON Protocol & Security
- **Request (`POST`):** `{"requestId": "<uuid>", "calls": [["text1"], ["text2"]]}`
- **Success (`HTTP 200`):** `{"replies": [[0.1, ...], [0.2, ...]]}` — `replies`
  length **must exactly equal** `calls` length (return `null` for `NULL` inputs;
  `INT64` values outside `±9007199254740992` ($2^{53}$) are JSON-string encoded).
- **Error (non-200):** `{"errorMessage": "<under 1 KB>"}`. Spanner auto-retries
  HTTP `408`, `429`, `500`, `503`, `504`.
- **Security:** Deploy with `--no-allow-unauthenticated`; store 3rd-party API
  keys in **Secret Manager** (`roles/secretmanager.secretAccessor` on the Cloud
  Run service account); grant `roles/spanner.serviceAgent` to Spanner's service
  agent for cross-project calls. Spanner bills for request/response network bytes.

## 8. Spanner ML Troubleshooting & Anti-Patterns

Spanner wraps upstream Vertex AI errors in `google.rpc.Status` details:

| gRPC Code | Likely Root Cause | Remediation |
| --- | --- | --- |
| `CANCELLED` | Client cancelled query | Check client cancellation / timeout logic |
| `DEADLINE_EXCEEDED` | Query deadline too short or Vertex AI overloaded | Raise client deadline; scale Vertex AI replicas |
| `FAILED_PRECONDITION` | Vertex AI endpoint has no deployed model | Deploy model version to target endpoint |
| `INTERNAL` | Transient internal failure | Configure failover `endpoints = [...]`; retry |
| `INVALID_ARGUMENT` | `CREATE MODEL` schema mismatch or batch too large | Verify `embeddings STRUCT<...>` schema; reduce `remote_udf_max_rows_per_rpc` |
| `NOT_FOUND` | Bad model ID/version, deleted endpoint, or `global` on embedding model | Switch to regional endpoint (e.g., `asia-southeast1`) |
| `PERMISSION_DENIED` | Missing `roles/spanner.serviceAgent` or VPC-SC block | Bind service agent on Vertex AI project; check VPC-SC logs |
| `RESOURCE_EXHAUSTED` | Vertex AI HTTP 429 prediction quota hit | Lower `pdml_max_parallelism`; isolate backfill project; request quota |
| `UNAVAILABLE` | Missing service agent, overload, or regional Vertex AI outage | Run `gcloud beta services identity create`; add failover `endpoints` |

- **Anti-Pattern — Calling a model like a scalar UDF:** Writing
  `FROM {model_name}(INPUT(content => '...'))` is invalid SQL. Always write
  `FROM ML.PREDICT(MODEL {model_name}, (SELECT '...' AS content))`.

## 9. LangChain Integration & Official Docs

`langchain-google-spanner` (Preview) provides five official classes:
- `SpannerVectorStore` (vector search over new or existing tables with `VertexAIEmbeddings`)
- `SpannerLoader` & `SpannerDocumentSaver` (load/save/delete LangChain `Document` rows)
- `SpannerChatMessageHistory` (ACID conversational state)
- `SpannerGraphStore` (nodes, edges, and GQL queries)
- `SpannerGraphQAChain` (NL -> ISO GQL -> Spanner Graph -> LLM answer; pairs with `LLMGraphTransformer`)

Official documentation:
- [Spanner ML Overview](https://docs.cloud.google.com/spanner/docs/ml)
- [Generate Embeddings with Spanner ML](https://docs.cloud.google.com/spanner/docs/ml-tutorial-embeddings)
- [Backfill Embeddings with Partitioned DML](https://docs.cloud.google.com/spanner/docs/backfill-embeddings)
- [Spanner ML & AI Functions Reference](https://docs.cloud.google.com/spanner/docs/reference/standard-sql/ml-functions)
- [Cloud Run Remote Functions](https://docs.cloud.google.com/spanner/docs/cloud-run-remote-function)
- [Troubleshoot Spanner ML](https://docs.cloud.google.com/spanner/docs/ml-troubleshoot)
- [LangChain for Cloud Spanner](https://docs.cloud.google.com/spanner/docs/langchain)
