-- =============================================================================
-- 03d_backfill_embeddings_pdml.sql
-- Acme Commerce Platform — Idempotent Throttled PDML Embedding Backfill
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
--
-- Execution Command (MUST pass --enable-partitioned-dml):
--   gcloud spanner databases execute-sql sdlc_knowledge_graph \
--     --instance=sdlc-kg-enterprise \
--     --enable-partitioned-dml \
--     --sql="$(cat solution/sql/03d_backfill_embeddings_pdml.sql)"
--
-- Best Practices Enforced:
--   - @{pdml_max_parallelism=5} prevents overwhelming regional Vertex AI quota
--     in asia-southeast1 during bulk document ingestion.
--   - SAFE.ML.PREDICT returns NULL on transient per-row prediction errors so
--     re-running the identical statement backfills only remaining NULL rows.
--   - Never call ML.PREDICT inside latency-sensitive OLTP read-write transactions.
--   - Explicit Matryoshka projection: STRUCT(768 AS outputDimensionality) matches
--     DocumentChunks.embedding ARRAY<FLOAT32>(vector_length=>768) (INV-08).
-- =============================================================================

@{pdml_max_parallelism=5}
UPDATE DocumentChunks
SET
  DocumentChunks.embedding = (
    SELECT embeddings.values
    FROM SAFE.ML.PREDICT(
      MODEL SdlcTextEmbedding,
      (
        SELECT
          DocumentChunks.content AS content,
          'RETRIEVAL_DOCUMENT' AS task_type
      ),
      STRUCT(768 AS outputDimensionality)
    ) @{remote_udf_max_rows_per_rpc=50}
  ),
  DocumentChunks.embedding_model_version = 'gemini-embedding-001-768'
WHERE DocumentChunks.embedding IS NULL;
