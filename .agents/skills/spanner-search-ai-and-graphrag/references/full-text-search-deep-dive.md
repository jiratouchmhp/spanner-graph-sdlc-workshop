# Cloud Spanner Full-Text Search (FTS) Deep Dive

Comprehensive production reference for **Cloud Spanner Full-Text Search**
(Enterprise and Enterprise Plus editions): physical architecture, all nine
tokenizers across GoogleSQL and PostgreSQL, `CREATE SEARCH INDEX` partitioning
and interleaving, `rquery`/`words` dialects, `enhance_query`, Custom
Dictionaries, `SCORE` v2, fuzzy n-grams, pattern-matching acceleration, facets,
and pagination.

## 1. Architecture, Consistency, Storage & Sharding

- **Edition & Billing:** Requires **Enterprise** or **Enterprise Plus** edition
  (zero additional FTS feature surcharge; pay standard compute and storage).
- **Transactional Consistency:** Search indexes update synchronously in the
  same ACID transaction as base table commits and backfill automatically when
  created.
- **Compliance Limit:** Full-Text Search does **not** support **Assured
  Workloads**.

### Two-Level Document-Sharded Storage
Internally, every search index stores two colocated structures per split:
1. **Secondary mapping index:** `{partition_keys, docid} -> {base_table_PK, STORING/INCLUDE columns}`.
2. **Per-`TOKENLIST` inverted token index:** `{partition_keys, _token, docid}`
   (postings lists sorted by token then `docid`, storing token positions for
   phrase/proximity matching and HTML prominence attributes for `SCORE`).
- `docid` is one or two 64-bit integers: `{uid}` when `ORDER BY` is omitted, or
  `{order_by_col, uid}` when presorted (`uid` is an internal tiebreaker never
  exposed in SQL).
- **Document-Sharded Advantage:** All tokens for a row reside in the **same
  split**. Single-row writes touch a constant number of index splits regardless
  of token count or number of indexed `TOKENLIST` columns, and multi-column
  boolean intersections (`AND`/`OR`/`NOT` across text, numeric, exact, and JSON
  tokenlists) run as a **local zig-zag join** on each split followed by a
  distributed merge union — zero distributed joins.

### Uniform Sharding vs Sort-Order Sharding
- **`sort_order_sharding = false` (Default — Uniform):** Randomly distributes
  rows across splits inside a partition. Eliminates write hotspots on monotonic
  timestamps/IDs, but queries fan out in parallel across **all splits** in the
  partition.
- **`sort_order_sharding = true` (Sort-Order):** Assigns rows to splits ordered
  by the `ORDER BY` presort column. Unlocks **early termination** for low-`LIMIT`
  top-K queries matching the index `ORDER BY` direction, and is **mandatory for
  interleaved search indexes**, but carries write-hotspot risk if writes cluster
  at the latest timestamp.

## 2. Complete Tokenizer Matrix (GoogleSQL & PostgreSQL)

All tokenizers return `TOKENLIST` (GoogleSQL) / `spanner.tokenlist` (PostgreSQL)
and should be declared as `HIDDEN` generated columns (`AS (...) HIDDEN` or
`STORED HIDDEN` in GoogleSQL; `GENERATED ALWAYS AS (...) VIRTUAL HIDDEN` in PG).
Use **`STORED HIDDEN`** in GoogleSQL whenever a `TOKENLIST` column must be
exposed as a Spanner Graph node or edge property.

| Tokenizer (GSQL / PG) | Accepted Inputs (GSQL vs PG) | Key Options & Defaults | Behavior & Critical Gotchas |
| --- | --- | --- | --- |
| `TOKENIZE_FULLTEXT` / `spanner.tokenize_fulltext` | GSQL: `STRING`, `ARRAY<STRING>`; PG: `text`, `text[]` | `language_tag` (BCP 47, default `NULL` auto-detect), `content_type` (`"text/plain"` or `"text/html"`), `token_category` (`"small"`, `"medium"`, `"large"`, `"title"`), `remove_diacritics` (`BOOL`) | Lowercases tokens; **no write-time stemming or stopword removal**; preserves positions; auto CJK segmentation (`language_tag` disambiguates phrases); HTML mode strips tags, unescapes entities, and sets prominence used **only** by `SCORE`. |
| `TOKENIZE_SUBSTRING` / `spanner.tokenize_substring` | GSQL: `STRING`, `ARRAY<STRING>`; PG: `text`, `text[]` | `ngram_size_min` (`1`), `ngram_size_max` (`4`), `relative_search_types` (`'phrase'`, `'value_prefix'`, `'value_suffix'`, `'word_prefix'`, `'word_suffix'`, `'all'`), `short_tokens_only_for_anchors` (`FALSE`), `content_type`, `remove_diacritics`, `language_tag` | Word-splits and lowercases first, then builds n-grams (plus whole words `< ngram_size_min`). **No CJK segmentation**; supports emojis. **10–30x storage amplification** vs full-text — set `short_tokens_only_for_anchors=>TRUE` when only prefix/suffix matching is needed. |
| `TOKENIZE_NGRAMS` / `spanner.tokenize_ngrams` | GSQL: `STRING`, `ARRAY<STRING>`; PG: `text` | `ngram_size_min` (`1`), `ngram_size_max` (`4`; **`3`** recommended for fuzzy typo search), `remove_diacritics` | Does **not** split into words; **preserves case and whitespace**. Powers cross-word `SEARCH_NGRAMS` and is **mandatory** on `LOWER(col)` for pattern-matching acceleration. |
| `TOKEN` / `spanner.token` | GSQL: `STRING`, `BYTES`, `ARRAY<STRING>`, `ARRAY<BYTES>`; **PG: scalar `text`/`bytea` ONLY** | None | Verbatim exact-match acceleration (`=`, `IN`; plus `ARRAY_INCLUDES`, `ARRAY_INCLUDES_ANY`, `ARRAY_INCLUDES_ALL` in GoogleSQL). |
| `TOKENIZE_BOOL` / `spanner.tokenize_bool` | GSQL: `BOOL`, `ARRAY<BOOL>`; PG: `bool` | None | Emits `'y'` (`TRUE`), `'n'` (`FALSE`), `NULL` (`NULL`). |
| `TOKENIZE_NUMBER` / `spanner.tokenize_number` | GSQL: `INT64`, `FLOAT32`, `FLOAT64`, and `ARRAY` of each; **PG: scalar `bigint` ONLY (no floats/arrays)** | `comparison_type` (`"all"` default, `"equality"` **required for arrays**), `algorithm` (`"logtree"` default INT64, `"prefixtree"` for unbounded `>`/`>=`, `"floatingpoint"` default floats), `min`, `max`, `granularity` (`1`), `tree_base` (`2`), `precision` (`ieee_precision` in PG, `15`) | `equality` emits 1 token (`==42`) for `=`/`IN` only (not `!=`). `all` accelerates `<, <=, >, >=, BETWEEN` and (for **`INT64` ONLY**) `!=`, `<>`, `NOT`. **Gotcha:** `!=`/`<>`/`NOT` are **never** accelerated on `FLOAT32`/`FLOAT64` (`NaN` produces no tokens). `NUMERIC` unsupported directly (project to `INT64` + post-filter). Changing `min`/`max` requires index rebuild. |
| `TOKENIZE_JSON` / `spanner.tokenize_jsonb` | GSQL: `JSON`; PG: `jsonb` | None | **MUST be virtual `HIDDEN` (NEVER `STORED HIDDEN` — Spanner rejects `JSON derived TOKENLIST column ... may not be STORED`).** Accelerates `=`, `!=`, `<>`, `IN`, `NOT IN` on paths/subpaths (`.`/`[]` in GSQL; `->` in PG), containment (`JSON_CONTAINS`; PG `@>`, `<@`), and key existence (`IS NOT NULL`; PG `?`, `?|`, `?&`). Does **not** accelerate `<, <=, >, >=` or PG `->>`. **Hard limit:** `< 10 MB` token output per JSON document. |
| `TOKENLIST_CONCAT` / `spanner.tokenlist_concat` | `[t1, t2]` / `ARRAY[t1, t2]` | None | Combines **only** `TOKENIZE_FULLTEXT` or **only** `TOKENIZE_SUBSTRING` (never mix types). Prevents false cross-field phrase matches vs string concatenation; treats `NULL` elements as empty. **PG gotcha:** PG forbids generated columns referencing other generated columns — nest `spanner.tokenize_fulltext(...)` directly inside `spanner.tokenlist_concat(...)`. |
| `DEBUG_TOKENLIST` | `TOKENLIST` | None | Debugging only; human-readable string format may change. |

## 3. `CREATE SEARCH INDEX`, Partitioning, Sort Order & Interleaving

### GoogleSQL & PostgreSQL DDL Templates
```sql
-- GoogleSQL
CREATE TABLE {table_name} (
  tenant_id STRING(64) NOT NULL,
  doc_id STRING(64) NOT NULL,
  title STRING(MAX),
  body STRING(MAX),
  tags ARRAY<STRING(64)>,
  metadata JSON,
  priority INT64,
  updated_epoch_sec INT64 NOT NULL,
  title_tokens TOKENLIST AS (TOKENIZE_FULLTEXT(title, token_category => 'title')) STORED HIDDEN,
  body_tokens TOKENLIST AS (TOKENIZE_FULLTEXT(body)) STORED HIDDEN,
  all_text_tokens TOKENLIST AS (TOKENLIST_CONCAT([title_tokens, body_tokens])) HIDDEN,
  title_ngrams TOKENLIST AS (TOKENIZE_NGRAMS(title, ngram_size_min => 3, ngram_size_max => 3)) HIDDEN,
  title_lower_ngrams TOKENLIST AS (TOKENIZE_NGRAMS(LOWER(title), ngram_size_min => 3, ngram_size_max => 4)) HIDDEN,
  tag_tokens TOKENLIST AS (TOKEN(tags)) HIDDEN,
  metadata_tokens TOKENLIST AS (TOKENIZE_JSON(metadata)) HIDDEN,
  priority_tokens TOKENLIST AS (TOKENIZE_NUMBER(priority, min => 0, max => 100)) HIDDEN
) PRIMARY KEY (tenant_id, doc_id);

CREATE SEARCH INDEX {index_name}
ON {table_name}(all_text_tokens, title_ngrams, title_lower_ngrams, tag_tokens, metadata_tokens, priority_tokens)
STORING (title, tags, priority)
PARTITION BY tenant_id
ORDER BY updated_epoch_sec DESC
WHERE title IS NOT NULL
, INTERLEAVE IN {parent_tenant_table}
OPTIONS (sort_order_sharding = true);

ALTER SEARCH INDEX {index_name} ADD STORED COLUMN body;
DROP SEARCH INDEX IF EXISTS {index_name};
```

```sql
-- PostgreSQL (index name CANNOT be schema-qualified even on named-schema tables)
CREATE SEARCH INDEX {index_name}
ON {schema_name}.{table_name}(all_text_tokens, priority_tokens)
INCLUDE (title, priority)
PARTITION BY tenant_id
ORDER BY updated_epoch_sec DESC
INTERLEAVE IN {parent_tenant_table}
WHERE title IS NOT NULL
WITH (sort_order_sharding = true);

ALTER SEARCH INDEX {index_name} ADD INCLUDE COLUMN body;
```

### Mandatory Index Schema Rules & Gotchas
1. **Column order irrelevant:** The order of `TOKENLIST` columns in `ON {table_name}(...)` has no performance impact.
2. **`ORDER BY` restrictions:**
   - Accepts **at most one `INT64`** (`bigint` in PG) column. **`TIMESTAMP` is forbidden** (nanosecond resolution exceeds 64-bit integer; store Unix seconds/microseconds as `INT64`).
   - Must be `NOT NULL`, or filtered with `WHERE sort_col IS NOT NULL` in the index (and mirrored in every query).
   - **Write-amplification trap:** Because the presort column is embedded in every `docid` across every posting list, updating it rewrites the mapping row **and every token entry** for that document. For frequently updated timestamps, omit from `ORDER BY` or bucket via a generated column (`UNIX_SECONDS(TIMESTAMP_TRUNC(updated_at, HOUR))`).
3. **Three mandatory interleaving rules:**
   - Must set `sort_order_sharding = true`.
   - Parent must be a **top-level** table (never a child table).
   - Parent table's primary key must be a **prefix** of the search index's `PARTITION BY` columns.
   - *Benefit:* Eliminates distributed 2PC writes and back-join hops for small tenant partitions.
4. **Partial (`WHERE col IS NOT NULL`) indexes:** Allowed on partition keys, sort columns, or `STORING`/`INCLUDE` columns (never on stored `ARRAY` columns). Use a `HIDDEN` generated column returning `TRUE`/`NULL` for arbitrary partial search indexes.
5. **Named schemas:** In GoogleSQL force via `@{FORCE_INDEX="{schema_name}.{index_name}"}`. In PostgreSQL index names cannot be schema-qualified (`/*@ FORCE_INDEX = {index_name} */`).

## 4. Query Functions, Dialects, Eligibility & Transaction Traps

### Retrieval & Snippet Functions
- `SEARCH(tokens, query [, dialect => 'rquery'|'words'|'words_phrase', language_tag => ..., enhance_query => TRUE|FALSE, dictionary => '{dict_table}'])` -> `BOOL`
- `SEARCH_SUBSTRING(tokens, query [, language_tag => ..., relative_search_type => 'phrase'|'value_prefix'|'value_suffix'|'word_prefix'|'word_suffix'])` -> `BOOL`
- `SEARCH_NGRAMS(tokens, query [, language_tag => ..., min_ngrams => 2, min_ngrams_percent => ...])` -> `BOOL`
- `SNIPPET(source_text, raw_query [, language_tag, enhance_query, dictionary, max_snippet_width, max_snippets, content_type])` -> `JSON` (`JSONB` in PG):
  ```json
  {"snippets": [{"highlights": [{"begin": 1, "end": 5}], "snippet": "Sample text.", "source_begin": 1, "source_end": 13}]}
  ```
  (`max_snippet_width` counts estimated proportional-width characters.)

### Query Dialects
- **`rquery` (default):**
  - Space-separated terms use implicit `AND`.
  - **`OR` (UPPERCASE required)** and `|` bind **only the two immediately adjacent terms**: `'big time OR fast car'` parses as `big AND (time OR fast) AND car`.
  - `"..."` matches exact adjacent phrases.
  - **`AROUND` (UPPERCASE required)** performs ordered proximity search (default distance = **5 positions**; supports `fast AROUND(10) car` and `fast AROUND 10 car`).
  - `-term` / `-"phrase"` negates terms (`NULL` column rows return `NULL` and are excluded by both `SEARCH(t, '-car')` and `NOT SEARCH(t, 'car')`).
  - Prefer `rquery` (`SEARCH(t, @q)` with `'car OR truck'`) over SQL `OR` (`SEARCH(t, @p1) OR SEARCH(t, @p2)`) to maximize query-plan cache hit rates.
- **`words`:** Disables all operators (`OR`, `""`, `-` treated as plain text); implicit `AND` on all terms. Ideal for raw end-user search boxes.
- **`words_phrase`:** Disables operators; all terms must appear adjacently in exact order.

### Six Non-Negotiable Eligibility & Transaction Rules
1. **Index-only `WHERE` predicates:** `SEARCH`, `SEARCH_SUBSTRING`, and `SEARCH_NGRAMS` are valid **only** in `WHERE` and never scan base tables or secondary indexes. Always pass the **`TOKENLIST` column**, never the index name!
2. **Single search index per query:** Every `TOKENLIST` column referenced in a query **must** reside in the **same** search index. Splitting `title_tokens` and `body_tokens` into separate search indexes causes `WHERE SEARCH(title_tokens, @p1) AND SEARCH(body_tokens, @p2)` to fail.
3. **Mandatory partition binding:** If an index declares `PARTITION BY tenant_id, region_id`, the `WHERE` clause must bind **all** partition columns with `=` (`tenant_id = @t AND region_id = @r`), or the optimizer skips the index and `FORCE_INDEX` errors.
4. **Mirror partial-index `IS NOT NULL`:** Any `WHERE col IS NOT NULL` on the index must be repeated verbatim in the query `WHERE` clause.
5. **Disallowed execution modes:** Search indexes are **unsupported** in DML, Partitioned DML, and Partitioned Queries.
6. **Read-write transaction lock trap:** Search queries lock an **entire index partition**, so Spanner rejects them inside read-write transactions by default (`The search index ... cannot be used in transactional queries by default`). Overriding via `@{ALLOW_SEARCH_INDEXES_IN_TRANSACTION=TRUE}` risks severe lock contention. Always execute search queries in **read-only transactions**, preferably with bounded staleness **`>= 10s`** to reduce cross-split latency.

## 5. Query Enhancement & Custom Dictionaries

### `enhance_query => TRUE`
Performs query-time expansion without re-indexing: stemming, spelling correction,
contextual synonyms, stopword normalization, number-word equivalence (`five` <->
`5`), and pluralization.
- **Timeout & fail-open:** Default timeout is **500 ms**; on timeout, the query
  falls back to unenhanced search. Tune with `@{enhance_query_timeout_ms=200}`
  or enforce fail-closed via `@{require_enhance_query=true}`.
- **Drift note:** Expansion algorithms evolve over time; results can subtly shift.

### Custom Dictionary Tables (Domain Synonyms)
```sql
-- GoogleSQL (default schema ONLY; exact 2 columns required)
CREATE TABLE {dictionary_table} (
  Key STRING(MAX) NOT NULL,
  Value ARRAY<STRING(MAX)> NOT NULL
) PRIMARY KEY (Key),
OPTIONS (
  fulltext_dictionary_table = true,
  fulltext_dictionary_staleness = '15s'
);

-- Unidirectional: insert reverse mapping if bidirectional expansion is desired
INSERT OR UPDATE INTO {dictionary_table} (Key, Value) VALUES
  ('pci', ['payment card industry', 'cardholder data', 'tokenization']),
  ('mtls', ['mutual tls', 'zero trust']);
```

- **PostgreSQL DDL:** `WITH (type = 'fulltext_dictionary', fulltext_dictionary_staleness = '15s')` — **no `ALTER TABLE`** support to change staleness later (GoogleSQL supports `ALTER TABLE ... SET OPTIONS`).
- **Hard Rules:** Default staleness is **15s** (override per query via `@{fulltext_dictionary_staleness="0s"}` / `/*@ fulltext_dictionary_staleness='0s' */`); keys must be **single lowercase words**; multi-word values expand as **exact phrases**; must live in the **default schema**; works with **`rquery` dialect only**; limited Import/Export support.
- When paired with `enhance_query => TRUE`, both run **independently** on original query terms (outputs do not chain).

## 6. Ranked Search (`SCORE`), Fuzzy Search & Pattern Acceleration

### `SCORE` & Scorer v2
- Requires `SEARCH(...)` in the same query; returns `FLOAT64` (`0` on `NULL`).
- **Critical IDF rule:** The IDF term is computed **only when `enhance_query => TRUE`** and uses Google Search's **global web corpus** (not local table document frequency). Without `enhance_query => TRUE`, `IDF = 1` (pure TF scoring).
- **Options (`options => JSON '{...}'` / `'...'::jsonb`):** `bigram_weight` (default `2.0`), `idf_weight` (default `1.0`), `token_category_weights` (`small`, `medium`, `large`, `title`), `version` (`1` or `2`).
- **Scorer v2 (default after April 2026):** Demotes lower-confidence `enhance_query` synonyms, improves per-term score capping, and fixes single-hit/`OR` weighting. Pin DB-wide via `ALTER DATABASE {database_id} SET OPTIONS (score_version = 2)` (PG: `ALTER DATABASE {database_id} SET "spanner.score_version" = 2`).
- **Retrieval depth cap:** Bound low-selectivity scans using the presort index before scoring:
  ```sql
  SELECT doc_id, title, score
  FROM (
    SELECT doc_id, title, SCORE(all_text_tokens, @q, enhance_query => TRUE) AS score
    FROM {table_name}
    WHERE tenant_id = @tenant_id
      AND title IS NOT NULL
      AND SEARCH(all_text_tokens, @q, enhance_query => TRUE, dictionary => '{dictionary_table}')
    ORDER BY updated_epoch_sec DESC
    LIMIT 5000
  )
  ORDER BY score DESC
  LIMIT 20;
  ```

### Fuzzy Search: `SEARCH_NGRAMS` + `SCORE_NGRAMS` vs `enhance_query` vs `SOUNDEX`
- `SEARCH_NGRAMS` matches rows sharing `>= min_ngrams` (default `2`) or `min_ngrams_percent` distinct n-grams (returns `FALSE` when query length `< ngram_size_min`). Always cap candidates with an inner `LIMIT 10000` to prevent common trigram explosions.
- `SCORE_NGRAMS` computes Jaccard trigram similarity (`algorithm => 'trigrams'` only): $\frac{\text{shared}}{\text{total}_{\text{index}} + \text{total}_{\text{query}} - \text{shared}}$.
  - **Storage gotcha:** `SCORE_NGRAMS` reads the **original source column** — place that column in `STORING`/`INCLUDE` to avoid base-table back-joins! Cannot directly accept `TOKENLIST_CONCAT` (materialize an intermediate generated column first). Supports `array_aggregator => 'flatten' | 'max_element'`.
- **Choice guide:** Use **N-grams** for typos in *both* index and query, partial words, and proper nouns/SKUs (short fields, higher storage). Use **`enhance_query`** for query-side typos, dictionary words, and long documents. Use `TOKEN(LOWER(SOUNDEX(name)))` for Latin-script phonetic matching.

```sql
-- Bounded fuzzy n-gram search (READ-ONLY; title is in STORING to avoid back-joins)
SELECT doc_id, title, score
FROM (
  SELECT doc_id, title, SCORE_NGRAMS(title_ngrams, @raw_query) AS score
  FROM {table_name}
  WHERE tenant_id = @tenant_id
    AND title IS NOT NULL
    AND SEARCH_NGRAMS(title_ngrams, @raw_query, min_ngrams => 2)
  LIMIT 10000
)
ORDER BY score DESC
LIMIT 10;
```

### Accelerating `LIKE`, `STARTS_WITH`, `ENDS_WITH`, `REGEXP_CONTAINS`
GoogleSQL accelerates all four; PostgreSQL accelerates `LIKE` and `STARTS_WITH` only. **All five prerequisites are mandatory:**
1. Tokenize with **`TOKENIZE_NGRAMS`** (e.g., `ngram_size_min => 3, ngram_size_max => 4`).
2. Tokenize **`LOWER(column_name)`** (not raw `column_name`).
3. Put `column_name` in **`STORING`** / **`INCLUDE`** to avoid back-joins during post-filtering.
4. Pattern/prefix/suffix/regex **must be a constant literal** (query parameters `@p` are **not** accelerated!).
5. Literal must include a contiguous non-wildcard sequence of length `>= ngram_size_min` (`"%ab%"` fails when `ngram_size_min = 3`). Use `FORCE_INDEX` if selectivity estimates skip the search index.

```sql
-- Combined JSON, numeric, exact-array, and literal LIKE acceleration in one local zig-zag scan
SELECT doc_id, title, priority
FROM {table_name} @{FORCE_INDEX={index_name}}
WHERE tenant_id = @tenant_id
  AND title IS NOT NULL
  AND LOWER(title) LIKE '%settlement%'          -- Constant literal >= ngram_size_min (3)
  AND JSON_CONTAINS(metadata, JSON '{"region": "asia-southeast3"}')
  AND priority BETWEEN 1 AND 25
  AND ARRAY_INCLUDES(tags, 'tier-0')
ORDER BY updated_epoch_sec DESC
LIMIT 20;
```

## 7. Facets, Pagination, Top-K & Index Selection

### Single-Query Multi-Facet Pattern
Bound candidates once in a CTE (`LIMIT 10000`) and return both the first result page and grouped lower-bound facet counts (`UNNEST` for arrays) from `STORING`/`INCLUDE` columns without back-joins:

```sql
WITH candidates AS (
  SELECT doc_id, title, tags, priority, updated_epoch_sec
  FROM {table_name}
  WHERE tenant_id = @tenant_id
    AND title IS NOT NULL
    AND SEARCH(all_text_tokens, @q, enhance_query => TRUE)
  ORDER BY updated_epoch_sec DESC, doc_id ASC
  LIMIT 10000
)
SELECT
  ARRAY(SELECT AS STRUCT doc_id, title FROM candidates LIMIT 20) AS page_items,
  ARRAY(
    SELECT AS STRUCT tag, COUNT(*) AS doc_count
    FROM candidates, UNNEST(tags) AS tag
    GROUP BY tag ORDER BY doc_count DESC LIMIT 10
  ) AS tag_facets;
```

### Pagination & Top-K Rules
- **Presorted pagination (`ORDER BY updated_epoch_sec DESC, doc_id ASC`):** Use **key-based seekable pagination**: `WHERE (updated_epoch_sec < @last_ts OR (updated_epoch_sec = @last_ts AND doc_id > @last_id)) AND SEARCH(...)`. Avoid pinning snapshot reads across user think time (fails past `version_retention_period`, default 1 hour).
- **Score-ranked pagination (`ORDER BY score DESC, doc_id ASC`):** Use `LIMIT ... OFFSET ...` (`SCORE` is not seekable and cannot be compared across scorer versions; optionally pin `options => JSON_OBJECT('version', @score_version)` inside the page token).
- **Exact `ORDER BY` direction match:** Reversing direction (`ASC` on a `DESC` search index) or sorting by a non-presort `STORING` column forces scanning **all** matching rows before applying `LIMIT`.

### Search Index vs Secondary Index
- **Use Secondary Index:** Point/range structured lookups, single-split efficiency, arbitrary sort keys, Read API, or read-write transactions.
- **Use Search Index:** Full-text/fuzzy/substring search, `ARRAY` or `JSON` key indexing, or multi-column arbitrary boolean intersections (local zig-zag join) in read-only SQL queries.

Official documentation:
- [Full-Text Search Overview](https://docs.cloud.google.com/spanner/docs/full-text-search)
- [Search Indexes](https://docs.cloud.google.com/spanner/docs/full-text-search/search-indexes)
- [Tokenization Reference](https://docs.cloud.google.com/spanner/docs/full-text-search/tokenization)
- [Search Functions Reference](https://docs.cloud.google.com/spanner/docs/reference/standard-sql/search_functions)
- [Query Enhancement & Custom Dictionaries](https://docs.cloud.google.com/spanner/docs/full-text-search/search-query-enhancement)
- [Ranked, Fuzzy & Pattern-Matching Search](https://docs.cloud.google.com/spanner/docs/full-text-search/ranked-search)
