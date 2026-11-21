-- =============================================================================
-- 03_indexes_fts_vector_postload.sql (STARTER — Lab 3)
-- Acme Commerce Platform — Post-Load Covering Reverse, FTS & ScaNN Vector Indexes
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
--
-- Antigravity Skills for Lab 3:
--   - Primary: .agents/skills/spanner-schema-and-data-modeling/SKILL.md
--   - Support: .agents/skills/spanner-search-ai-and-graphrag/SKILL.md
--
-- Execution Order (CRITICAL):
--   1. Apply 01_path_a_schematized.sql
--   2. Bulk-load Path A nodes/edges + Path B DocumentChunks & embeddings
--   3. Run 03a_seed_synonyms_and_metadata.sql (populates Apis.metadata + dictionary)
--   4. Apply THIS file so ScaNN centroids train over real embeddings (not empty
--      tables) and secondary/search index backfills run cleanly in one pass.
--
-- Verify when done:
--   make TRACK=starter verify-lab LAB=3
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Covering Reverse Traversal Indexes on Explicit M:N Edge Tables
-- -----------------------------------------------------------------------------

-- TODO(Lab 3): Create the reverse covering index `IdxApiCalledByService` on
--   `ServiceCallsApi` for Hero API blast-radius entry (`Api <- CALLS_API - Service`).
--   Requirements:
--     - Indexed key columns: `(api_id, service_id)`
--     - Covering STORING columns: `(call_mode, is_critical, migration_status, rps_estimate)`
--       (NEVER repeat primary key columns inside STORING!)
--     - Remote interleaved layout: co-locate incoming adjacency lists with the
--       destination node split using `INTERLEAVE IN Apis` (without `PARENT`).

-- Reverse upstream dependency traversal: Service <- DEPENDS_ON - Service
-- Global index because ServiceDependsOnService is a standalone dual-FK table (INV-06).
CREATE INDEX IdxServiceDependedOnBy
  ON ServiceDependsOnService (to_service_id, from_service_id)
  STORING (dependency_type, is_sync, criticality);

-- Reverse ADR governance lookup: Api <- GOVERNS - Adr
CREATE INDEX IdxApiGovernedByAdr
  ON AdrGovernsApi (api_id, adr_id)
  STORING (governance_type, effective_date),
  INTERLEAVE IN Apis;

-- Reverse ADR supersession lineage: OlderAdr <- SUPERSEDES - NewerAdr
CREATE INDEX IdxAdrSupersededBy
  ON AdrSupersedesAdr (supersedes_adr_id, adr_id)
  STORING (superseded_date);

-- Reverse requirement compliance lookup for APIs: Api <- APPLIES_TO_API - Requirement
CREATE INDEX IdxApiRequirements
  ON ReqAppliesToApi (api_id, req_id)
  STORING (compliance_status),
  INTERLEAVE IN Apis;

-- Reverse requirement compliance lookup for Services: Service <- APPLIES_TO_SERVICE - Requirement
CREATE INDEX IdxServiceRequirements
  ON ReqAppliesToService (service_id, req_id)
  STORING (compliance_status),
  INTERLEAVE IN Services;

-- Reverse Path B entity-to-chunk citation lookup: (entity_type, entity_id) -> chunk_id
CREATE INDEX IdxChunkEntityLookup
  ON ChunkEntityMentions (entity_type, entity_id, chunk_id)
  STORING (mention_source);

-- -----------------------------------------------------------------------------
-- 2. Reverse Covering Indexes for Dual-Projected Folded N:1 Edges & Lookups
-- -----------------------------------------------------------------------------

CREATE INDEX IdxApisByService
  ON Apis (service_id, api_id)
  STORING (http_method, http_path, lifecycle_status, criticality, is_public),
  INTERLEAVE IN Services;

CREATE INDEX IdxServicesByOwner
  ON Services (owner_id, service_id)
  STORING (service_name, tier, criticality, domain),
  INTERLEAVE IN Owners;

CREATE INDEX IdxAdrsByOwner
  ON Adrs (owner_id, adr_id)
  STORING (title, lifecycle_status, decision_date),
  INTERLEAVE IN Owners;

CREATE INDEX IdxRequirementsByOwner
  ON Requirements (owner_id, req_id)
  STORING (framework, title, severity),
  INTERLEAVE IN Owners;

-- OIDC @caller_email lookup for Lab 6 team-scoped queries (owner_id is PK, auto-stored)
CREATE UNIQUE INDEX IdxOwnersByEmail
  ON Owners (email)
  STORING (team_name, slack_channel, clearance_tier);

-- Point lookup by HTTP method + path (e.g., POST /v2/payments/charge)
CREATE INDEX IdxApisByMethodPath
  ON Apis (http_method, http_path)
  STORING (service_id, lifecycle_status, criticality, is_public);

-- Deterministic ordered retrieval of chunks within a single Markdown source file
CREATE INDEX IdxDocumentChunksBySource
  ON DocumentChunks (source_path, chunk_index)
  STORING (doc_type, entity_id, section_heading);

-- -----------------------------------------------------------------------------
-- 3. Full-Text, Fuzzy N-Gram, JSON & Numeric Search Indexes (Enterprise+)
--    Remember: All TOKENLIST columns queried together MUST reside in the same
--    search index; FTS queries MUST run in read-only transactions (INV-09).
-- -----------------------------------------------------------------------------

CREATE SEARCH INDEX IdxServicesSearch
  ON Services (search_tokens, name_ngrams)
  STORING (service_name, tier, criticality, domain, owner_id);

CREATE SEARCH INDEX IdxApisSearch
  ON Apis (search_tokens, path_ngrams, path_lower_ngrams, metadata_tokens, latency_tokens)
  STORING (service_id, http_method, http_path, lifecycle_status, criticality, is_public, latency_budget_ms);

CREATE SEARCH INDEX IdxAdrsSearch
  ON Adrs (search_tokens)
  STORING (title, lifecycle_status, owner_id, decision_date);

CREATE SEARCH INDEX IdxRequirementsSearch
  ON Requirements (search_tokens)
  STORING (framework, title, severity, owner_id);

-- TODO(Lab 3): Create the Full-Text & Fuzzy N-Gram search index
--   `IdxDocumentChunksSearch` on `DocumentChunks`.
--   Requirements:
--     - Index BOTH `content_tokens` AND `content_ngrams` in the same search index
--     - Store `(doc_type, entity_id, source_path, section_heading, content)` so
--       FTS `SNIPPET(...)` and fuzzy search avoid base-table back-joins.

-- -----------------------------------------------------------------------------
-- 4. ScaNN Approximate Nearest Neighbor (ANN) Vector Index (INV-08)
-- -----------------------------------------------------------------------------

-- TODO(Lab 3): Create the ScaNN ANN vector index `IdxDocumentChunksEmbedding`
--   on `DocumentChunks (embedding)`.
--   Requirements (INV-08):
--     - Store ONLY compact metadata `(doc_type, entity_id, source_path, section_heading)`
--       (NEVER include large `content` text blobs inside a vector index!)
--     - Filter out unembedded rows with a partial-index predicate checking that
--       `embedding` is non-null (required so ScaNN ANN queries match the index!)
--     - Configure `OPTIONS (distance_type = 'COSINE')` and omit manual
--       `tree_depth`/`num_leaves` so Cloud Spanner Enterprise auto-tunes partitions.

-- -----------------------------------------------------------------------------
-- 5. Refresh Query Optimizer Statistics Post-Load
-- -----------------------------------------------------------------------------
ANALYZE;
