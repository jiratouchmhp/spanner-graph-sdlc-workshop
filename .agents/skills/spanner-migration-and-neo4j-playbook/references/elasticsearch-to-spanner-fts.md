# Elasticsearch to Cloud Spanner Full-Text Search (FTS) Migration Playbook

Production guide for migrating Elasticsearch search contracts (stemming, custom synonym dictionaries, fuzzy matching, faceting/aggregations) and bulk document data into Cloud Spanner Enterprise Full-Text Search and Columnar Engine.

## 1. Paradigm, Edition Prerequisites & Field Classification

- **Mandatory Edition:** Cloud Spanner Full-Text Search (FTS) and Columnar Engine require **Enterprise** (`$1.2915/node-hr` in `regional-asia-southeast3`) or **Enterprise Plus** (`$1.7955/node-hr`). Standard edition lacks both.
- **Preserve the User-Facing Search Contract, Not ES JSON Wire Shapes:** Do NOT mechanically mirror every Elasticsearch mapping field. Classify every source field into one of seven target roles:

| Source ES Field Role | Cloud Spanner Target Pattern |
| :--- | :--- |
| **Search text** (long/medium prose) | Typed `STRING(MAX)` base column + `HIDDEN` generated `TOKENLIST` via `TOKENIZE_FULLTEXT(...)` |
| **Short fuzzy text** (titles, names, cities) | `STRING` base column + `HIDDEN` generated `TOKENLIST` via `TOKENIZE_SUBSTRING(...)` |
| **Exact filter / ES `keyword`** | Typed scalar (`STRING`, `BOOL`, `INT64`) or `ARRAY<STRING>` copied into `SEARCH INDEX ... STORING (...)` |
| **Sort fields** | Typed column included in `STORING (...)` or index order |
| **Facet / aggregation fields** | Typed scalar or `ARRAY<STRING>` in `STORING (...)` aggregated via SQL CTEs |
| **Response payload / metadata** | Base table columns (place hot summary fields in `STORING (...)` to avoid base-table back-joins) |
| **Internal ES / dead fields** | **Drop** during migration |

- **Multi-Tenant Isolation Pattern:** Key tables by `(TenantId, EntityId)` and declare `CREATE SEARCH INDEX ... STORING (...) PARTITION BY TenantId` so tenant searches scan only a single partition without cross-tenant interference.
- **Query Optimizer Note:** Default Spanner Query Optimizer is version `8` (latest `9`); avoid hardcoding legacy optimizer version hints in application SQL unless validating an explicit plan regression.

## 2. Stemming & Language Analyzers vs `enhance_query => true`

Elasticsearch relies on configurable index/search analyzers (e.g., `english_possessive_stemmer`, `_english_` stop filter, `english` stemmer) plus `multi_match` field boosts (`title^2`). Spanner has **no per-index configurable English stemmer** — instead, tag fields with `token_category`, concatenate into a combined `TOKENLIST`, and evaluate query-time **`enhance_query => true`**:

```sql
CREATE TABLE SearchProducts (
  TenantId          STRING(36) NOT NULL,
  ProductId         STRING(64) NOT NULL,
  Title             STRING(MAX) NOT NULL,
  Description       STRING(MAX),
  Category          STRING(64),
  Status            STRING(32),
  TitleTokens       TOKENLIST AS (TOKENIZE_FULLTEXT(Title, token_category => "title")) HIDDEN,
  DescriptionTokens TOKENLIST AS (TOKENIZE_FULLTEXT(Description)) HIDDEN,
  SearchTokens      TOKENLIST AS (TOKENLIST_CONCAT([TitleTokens, DescriptionTokens])) HIDDEN
) PRIMARY KEY (TenantId, ProductId);

CREATE SEARCH INDEX SearchProductsByText
ON SearchProducts (SearchTokens)
STORING (Title, Description, Category, Status)
PARTITION BY TenantId;

-- Enhanced query (broadens morphological forms AND semantically related terms at query time):
SELECT ProductId, Title, Description
FROM SearchProducts
WHERE TenantId = @tenant_id
  AND Status = 'active'
  AND SEARCH(SearchTokens, @query_text, enhance_query => TRUE)
LIMIT 25;

-- Strict query (omit enhance_query => TRUE when expansion is too broad):
SELECT ProductId, Title, Description
FROM SearchProducts
WHERE TenantId = @tenant_id
  AND Status = 'active'
  AND SEARCH(SearchTokens, @query_text)
LIMIT 25;
```

### Verified Semantic Parity Gap

| Test Query | ES English Stemmer | Spanner `enhance_query => TRUE` | Spanner Strict (`SEARCH` default) |
| :--- | :---: | :---: | :---: |
| `running` | 2 hits | 2 hits | 1 hit |
| `hiking` | 1 hit | **2 hits** *(broader semantic expansion)* | 1 hit |
| `walking` | 1 hit | **2 hits** *(broader semantic expansion)* | 1 hit |

**Takeaway:** `enhance_query => true` is NOT a deterministic Lucene stemmer clone — it recovers morphological variants AND expands to semantically related words. Always benchmark high-value queries side-by-side (`enhanced` vs `strict`) with product owners before cutover.

## 3. Custom Dictionaries & Domain Synonyms (`fulltext_dictionary_table`)

Elasticsearch `synonym_graph` filters expand domain aliases (e.g., `raincoat -> rain jacket, waterproof jacket`; `sneaker <-> trainer -> running shoe`). Spanner natively supports **Custom Dictionary Tables** — do NOT assume `enhance_query => true` alone covers curated domain aliases (without a dictionary table, queries like `raincoat`, `trainer`, or `daypack` fail to match documents indexed only with `waterproof jacket`, `running shoe`, or `hydration pack`).

### Exact Custom Dictionary DDL & Query Invocation

```sql
-- GoogleSQL: MUST have EXACTLY 2 columns (Key, Value) and reside in the default schema!
CREATE TABLE ProductDictionary (
  Key   STRING(MAX) NOT NULL,
  Value ARRAY<STRING(MAX)> NOT NULL
) PRIMARY KEY (Key),
OPTIONS (
  fulltext_dictionary_table = true,
  fulltext_dictionary_staleness = '15s'
);

-- Single lowercase word keys; multi-word values expand as exact adjacent phrases;
-- insert explicit reverse rows for bidirectional single-word synonyms:
INSERT OR UPDATE INTO ProductDictionary (Key, Value) VALUES
  ('raincoat', ['rain jacket', 'waterproof jacket']),
  ('sneaker',  ['trainer', 'running shoe']),
  ('trainer',  ['sneaker', 'running shoe']),
  ('daypack',  ['hydration pack', 'running vest']);

-- Supported in SEARCH(), SCORE(), and SNIPPET()
-- Optionally combine with enhance_query => TRUE (both expand original terms independently)
SELECT ProductId, Title, Description
FROM SearchProducts
WHERE TenantId = @tenant_id
  AND Status = 'active'
  AND SEARCH(SearchTokens, @query_text, dictionary => 'ProductDictionary')
ORDER BY ProductId
LIMIT 25;
```

### 6 Hard Constraints & Migration Boundaries

1. **Exact 2-Column Schema & Default Schema Only:** Must declare only `Key` + `Value` (`PRIMARY KEY (Key)`) in the **default schema** (named schemas are unsupported; Spanner Import/Export support is limited). PostgreSQL uses `WITH (type = 'fulltext_dictionary', fulltext_dictionary_staleness = '15s')` and does NOT support `ALTER TABLE` to update staleness (GoogleSQL supports `ALTER TABLE ... SET OPTIONS`).
2. **Unidirectional Expansion:** Matching a `Key` expands to `Key` + every entry in `Value`. Insert reverse rows explicitly for bidirectional single-word synonyms.
3. **Single Lowercase Word Keys Only:** `Key` MUST be a single lowercase word (store `Value` entries in lowercase too). Multi-word `Value` entries expand as **exact adjacent phrases** (ideal for acronyms like `'edm' -> ['electronic dance music']`).
4. **Multi-Word Trigger Gap:** Because `Key` cannot contain spaces, multi-word ES synonym triggers (e.g., querying `"waterproof jacket"` to match `"raincoat"`) still require application-level query rewriting or indexed alias fields.
5. **`rquery` Dialect Only:** Custom dictionaries work only with the default `rquery` query dialect (not `words` or `words_phrase`).
6. **Staleness Control:** Default dictionary staleness is **`15s`**. Override at table level or per query via statement hint `@{fulltext_dictionary_staleness="0s"}` (GoogleSQL) / `/*@ fulltext_dictionary_staleness='0s' */` (PostgreSQL; query hint overrides table option).

**Validation Discipline:** Compare source ES queries against Spanner (`dictionary` strict vs `dictionary` + `enhance_query => true`), classifying missing high-value matches as **recall gaps** and extra expanded matches as **precision changes** requiring product-owner sign-off.

## 4. Fuzzy Matching vs Substring N-Grams (`SEARCH_NGRAMS` / `SCORE_NGRAMS`)

Spanner has **no Levenshtein `fuzziness` switch on `SEARCH(...)`**. Replace ES `"fuzziness": "AUTO"` on short fields (product titles, person names, city names, compact labels) with **`TOKENIZE_SUBSTRING`** + two-stage **`SEARCH_NGRAMS`** (candidate generation) + **`SCORE_NGRAMS`** (similarity cutoff):

```sql
CREATE TABLE FuzzyProducts (
  TenantId    STRING(36) NOT NULL,
  ProductId   STRING(64) NOT NULL,
  Title       STRING(MAX) NOT NULL,
  Brand       STRING(64),
  Status      STRING(32),
  TitleNgrams TOKENLIST AS (
    TOKENIZE_SUBSTRING(
      Title,
      ngram_size_min => 2,
      ngram_size_max => 3,
      relative_search_types => ["word_prefix", "word_suffix"]
    )
  ) HIDDEN
) PRIMARY KEY (TenantId, ProductId);

CREATE SEARCH INDEX FuzzyProductsByTitleNgrams
ON FuzzyProducts (TitleNgrams)
STORING (Title, Brand, Status)
PARTITION BY TenantId;

-- Two-stage pattern: cap candidates with LIMIT 10000, then filter/rank by SCORE_NGRAMS
SELECT ProductId, Title, Brand, Score
FROM (
  SELECT ProductId, Title, Brand,
         SCORE_NGRAMS(TitleNgrams, @typo_query) AS Score
  FROM FuzzyProducts
  WHERE TenantId = @tenant_id
    AND Status = 'active'
    AND SEARCH_NGRAMS(TitleNgrams, @typo_query)
  LIMIT 10000
)
WHERE Score >= 0.5
ORDER BY Score DESC
LIMIT 10;
```

- **Tuning Guidance:** N-gram similarity is not identical to ES edit distance. Lowering the `SCORE_NGRAMS` cutoff increases recall and noise; raising it tightens precision but drops heavier typos. Calibrate `ngram_size_min`, `ngram_size_max`, candidate `LIMIT`, and cutoff against real query logs.

## 5. Faceting, `post_filter`, Range Buckets & Columnar Rollups

| Elasticsearch Construct | Spanner GoogleSQL Equivalent |
| :--- | :--- |
| `keyword` facet field | Scalar or `ARRAY<STRING>` in `SEARCH INDEX ... STORING (...)` |
| `terms` agg (scalar) | `GROUP BY {col}` inside `ARRAY(SELECT AS STRUCT ...)` CTE |
| `terms` agg (multi-valued array) | `FROM SearchResults, UNNEST({array_col}) AS val GROUP BY val` |
| `range` agg | `CASE WHEN PriceCents < 5000 THEN 'under_50' ... END` |
| `avg` / `sum` metric aggs | SQL `AVG(...)` / `SUM(...)` |
| `post_filter` | Compute facet CTEs over unfiltered `SearchResults`; apply selected facet filter only to hits & metrics CTEs |

```sql
WITH SearchResults AS (
  SELECT ProductId, Title, Category, Brand, Colors, PriceCents, InventoryCount
  FROM FacetedProducts
  WHERE TenantId = @tenant_id
    AND Status = 'active'
    AND SEARCH(SearchTokens, @query_text)
),
CategoryFacets AS (
  SELECT ARRAY(
    SELECT AS STRUCT Category AS Value, ProductCount
    FROM (SELECT Category, COUNT(*) AS ProductCount FROM SearchResults GROUP BY Category)
    ORDER BY ProductCount DESC, Category ASC
  ) AS Buckets
),
ColorFacets AS (
  SELECT ARRAY(
    SELECT AS STRUCT Color AS Value, ProductCount
    FROM (SELECT Color, COUNT(*) AS ProductCount FROM SearchResults, UNNEST(Colors) AS Color GROUP BY Color)
    ORDER BY ProductCount DESC, Color ASC
  ) AS Buckets
),
PriceRangeFacets AS (
  SELECT ARRAY(
    SELECT AS STRUCT PriceBucket AS Value, ProductCount
    FROM (
      SELECT
        CASE
          WHEN PriceCents < 5000 THEN 'under_50'
          WHEN PriceCents < 15000 THEN '50_to_150'
          ELSE '150_plus'
        END AS PriceBucket,
        COUNT(*) AS ProductCount
      FROM SearchResults
      GROUP BY PriceBucket
    )
    ORDER BY PriceBucket ASC
  ) AS Buckets
),
-- ES post_filter equivalent: filter hits by user-selected facet while keeping facet counts broad
FilteredHits AS (
  SELECT ARRAY(
    SELECT AS STRUCT ProductId, Title, Category, Brand, PriceCents
    FROM SearchResults
    WHERE (@selected_category IS NULL OR Category = @selected_category)
    ORDER BY PriceCents ASC, ProductId ASC
    LIMIT 25
  ) AS Hits
),
Metrics AS (
  SELECT COUNT(*) AS ResultCount, AVG(PriceCents) AS AvgPriceCents, SUM(InventoryCount) AS TotalInventory
  FROM SearchResults
  WHERE (@selected_category IS NULL OR Category = @selected_category)
)
SELECT
  CategoryFacets.Buckets AS CategoryFacets,
  ColorFacets.Buckets AS ColorFacets,
  PriceRangeFacets.Buckets AS PriceRangeFacets,
  FilteredHits.Hits AS Hits,
  Metrics.*
FROM CategoryFacets, ColorFacets, PriceRangeFacets, FilteredHits, Metrics;
```

- **Interactive Facets vs Catalog Analytics:** Serve interactive UI facets over `SEARCH(...)` using the `SEARCH INDEX`. Route broad catalog rollups (many rows, few columns, no `SEARCH` predicate, e.g., `GROUP BY Category, Brand`) to the **Spanner Columnar Engine** (Enterprise+) over the exact same tables and SQL — zero ETL required.

## 6. Bulk Data Movement via Managed Service for Apache Spark

When Elasticsearch itself is the source dataset, use **Dataproc Serverless Batch (Managed Service for Apache Spark)** for bulk migration *(bulk-only pattern — NOT continuous CDC)*.

### Verified Runtime, Connectors & Exact `gcloud` Submit Command

- **Runtime:** **`2.3`** (Spark 3.5 + Scala 2.13).
- **Source Connector:** `org.elasticsearch:elasticsearch-spark-30_2.13:8.15.3` (`spark.read.format("es")`).
- **Target Connector:** `com.google.cloud.spark.spanner:spark-3.5-spanner:1.4.0` (`df.write.format("cloud-spanner")`).
- **Comma-Safe Delimiter:** Use `--properties=^#^spark.jars.packages=...` so `gcloud` does not split the comma-separated Maven coordinates.

```bash
gcloud dataproc batches submit pyspark \
  gs://{bucket}/spark/es_to_spanner.py \
  --project={project_id} \
  --region=asia-southeast3 \
  --version=2.3 \
  --deps-bucket={deps_bucket} \
  --subnet={managed_spark_subnet} \
  --service-account={spark_runtime_sa} \
  --properties=^#^spark.jars.packages=com.google.cloud.spark.spanner:spark-3.5-spanner:1.4.0,org.elasticsearch:elasticsearch-spark-30_2.13:8.15.3 \
  -- \
  --es-nodes={es_host} --es-port=9200 --es-resource={es_index} \
  --es-input-max-docs-per-partition=50000 --es-scroll-size=2000 \
  --es-partition-field=updated_at \
  --es-partition-ranges=2026-05-01T00:00:00Z..2026-05-04T00:00:00Z,2026-05-04T00:00:00Z..2026-05-07T00:00:00Z \
  --spanner-project-id={project_id} --spanner-instance-id={instance_id} \
  --spanner-database-id={database_id} --spanner-table={table_name}
```

### 2-Level ES Read Parallelism & Saturation Guardrails

1. **Connector Shard & Sliced-Scroll Partitioning (Tune First):** `es.input.max.docs.per.partition` (e.g., `50000`) splits large shards via scroll slicing; `es.scroll.size` (e.g., `2000`) bounds documents per scroll request.
2. **Disjoint Field-Partitioned Queries (If Shard Slicing Underutilizes Spark):** Union disjoint ES range/term queries over a `keyword`, numeric, or `date` field (**NEVER analyzed `text`**). Field must be non-null, snapshot-stable, low-skew, and exhaustive. Inspect candidate distributions beforehand with `/_mapping`, `/_field_caps`, and `size=0` `terms`/`date_histogram` aggs. **Mandatory gate:** Sum of all partition `_count`s MUST equal the unpartitioned query `_count`.
3. **Health Monitoring:** Watch ES search thread pools, rejected requests, scroll contexts, JVM heap/CPU, Spark task skew, and Spanner write latency/CPU.

### PySpark Transform & Mandatory Spanner Writer Options

Explicitly cast schema types (`F.col("price_cents").cast("long")`, `F.to_timestamp("updated_at")`), deduplicate on primary keys (`df.dropDuplicates(["TenantId", "ProductId"])`), and write **base columns ONLY** so Spanner computes hidden `TOKENLIST` columns and maintains `SEARCH INDEX`es:

| Spark Spanner Writer Option | Verified Default | Operational Significance & Gotchas |
| :--- | :--- | :--- |
| **`enablePartialRowUpdates`** | **`true`** | **CRITICAL:** Without `true`, the connector tries to write EVERY column — including hidden generated `TOKENLIST` columns — and **fails before writing a single row!** |
| `mutationType` | `insert_or_update` | Makes reruns idempotent on PKs; use `insert` to fail fast on duplicate keys |
| `mutationsPerTransaction` | `1000` | Batch size per commit; tune for row width + `80,000` mutations/commit cap |
| `numWriteThreads` | `8` | Parallel writer threads per executor; size against Spanner node count |
| `maxPendingTransactions` | `20` | Caps queued write pressure; higher values raise memory & abort risk |
| `assumeIdempotentRows` | `false` | Faster at-least-once write path when duplicate writes are safe |

### Operational IAM, Networking & Post-Load Verification

- **IAM Requirements:** Submitter needs `dataproc.batches.create` (`roles/dataproc.admin`). Runtime Service Account **FAILS at startup without `roles/dataproc.worker`**, and also needs `roles/spanner.databaseUser` plus GCS access to `--deps-bucket`.
- **Networking & Secrets:** Dataproc Serverless cannot reach local/loopback Elasticsearch; target Elastic Cloud or an Internal Load Balancer reachable from `{managed_spark_subnet}` co-located in `asia-southeast3`. Fetch ES credentials from Secret Manager — NEVER pass passwords in plaintext CLI args.
- **Post-Load Verification Checklist:**
  1. Reconcile source ES `_count` == Spark driver rows written == Spanner `SELECT Status, COUNT(*) FROM {table_name} GROUP BY Status`.
  2. Run representative `SEARCH(SearchTokens, ...)` and `SEARCH_NGRAMS(...)` queries to confirm generated `TOKENLIST` columns and `SEARCH INDEX`es are populated and serving expected hits.

- **Official Documentation & Samples:** [Spanner Full-Text Search Query Enhancement](https://docs.cloud.google.com/spanner/docs/full-text-search/search-query-enhancement), [Spanner Search Functions Reference](https://docs.cloud.google.com/spanner/docs/reference/standard-sql/search_functions), [Spanner Enterprise Samples (`spanner-bb-samples`)](https://github.com/GoogleCloudPlatform/spanner-bb-samples).
