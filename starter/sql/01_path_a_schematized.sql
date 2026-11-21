-- =============================================================================
-- 01_path_a_schematized.sql (STARTER — Lab 3)
-- Acme Commerce Platform — Schematized SDLC Knowledge Graph & GraphRAG Schema
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
-- Primary Region: Bangkok (regional-asia-southeast3, Thailand PDPA resident)
--
-- Antigravity Skills for Lab 3:
--   - Primary: .agents/skills/spanner-schema-and-data-modeling/SKILL.md
--   - Primary: .agents/skills/spanner-graph-modeling-and-gql/SKILL.md
--
-- Structural Invariants to Enforce:
--   INV-01: GoogleSQL + Cloud Spanner Enterprise (Graph, FTS, ScaNN, Locality Groups)
--   INV-02: Unique element aliases across NODE TABLES and EDGE TABLES
--   INV-03: Exact PK arity & SQL type matching in SOURCE KEY / DESTINATION KEY REFERENCES
--   INV-04: Graph-wide property type uniqueness across all 6 node tables & 14 edges
--   INV-05: Explicit SQL SECURITY INVOKER on all views + explicit KEY (...) on view edges
--   INV-06: NEVER combine INTERLEAVE IN PARENT T ON DELETE CASCADE with a
--           self-referencing cascading FK to T on the same table!
--   INV-08: ARRAY<FLOAT32>(vector_length=>768) embeddings on DocumentChunks
--
-- Verify when done:
--   make TRACK=starter verify-lab LAB=3
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Tiered Storage Locality Group (Enterprise+: SSD -> HDD after 30 days)
--    Bangkok SSD ($0.315/GiB-mo) -> HDD ($0.063/GiB-mo) = 5x storage savings.
-- -----------------------------------------------------------------------------
CREATE LOCALITY GROUP sdlc_cold_archive
OPTIONS (
  storage = 'ssd',
  ssd_to_hdd_spill_timespan = '30d'
);

-- -----------------------------------------------------------------------------
-- 2. Core Path A Node Tables (5 Structured Entity Tables = 68 Seed Nodes)
--    PROVIDED COMPLETE — inspect PKs, folded N:1 FKs, and TOKENLIST columns.
-- -----------------------------------------------------------------------------

-- 2.1 Owners (8 engineering teams)
CREATE TABLE Owners (
  owner_id STRING(64) NOT NULL,
  team_name STRING(256) NOT NULL,
  org_unit STRING(128) NOT NULL,
  slack_channel STRING(128) NOT NULL,
  email STRING(256) NOT NULL,
  oncall_pager STRING(256) NOT NULL,
  clearance_tier STRING(64) NOT NULL,
  escalation_lead STRING(256) NOT NULL,
) PRIMARY KEY (owner_id);

-- 2.2 Services (12 microservices; owner_id folds the N:1 OWNS_SERVICE edge)
--     Note: Folded N:1 FKs MUST NOT declare ON DELETE CASCADE so deleting a
--     team never wipes production microservices.
CREATE TABLE Services (
  service_id STRING(64) NOT NULL,
  service_name STRING(256) NOT NULL,
  tier STRING(32) NOT NULL,
  criticality STRING(32) NOT NULL,
  owner_id STRING(64) NOT NULL,
  sla_target_pct FLOAT64 NOT NULL,
  domain STRING(64) NOT NULL,
  lifecycle_status STRING(32) NOT NULL,
  description STRING(MAX) NOT NULL,
  search_tokens TOKENLIST AS (
    TOKENLIST_CONCAT([
      TOKENIZE_FULLTEXT(service_name, token_category => 'title'),
      TOKENIZE_FULLTEXT(description)
    ])
  ) STORED HIDDEN,
  name_ngrams TOKENLIST AS (
    TOKENIZE_NGRAMS(service_name, ngram_size_min => 3, ngram_size_max => 3)
  ) STORED HIDDEN,
  CONSTRAINT fk_services_owner
    FOREIGN KEY (owner_id) REFERENCES Owners (owner_id),
) PRIMARY KEY (service_id);

-- 2.3 Apis (24 HTTP APIs; service_id folds the N:1 EXPOSES edge)
--     Includes JSON metadata and multi-modal TOKENLIST columns for FTS,
--     trigram fuzzy search, LIKE acceleration, JSON containment, and numeric ranges.
CREATE TABLE Apis (
  api_id STRING(64) NOT NULL,
  service_id STRING(64) NOT NULL,
  http_method STRING(16) NOT NULL,
  http_path STRING(512) NOT NULL,
  api_version STRING(32) NOT NULL,
  lifecycle_status STRING(32) NOT NULL,
  criticality STRING(32) NOT NULL,
  is_public BOOL NOT NULL,
  latency_budget_ms INT64 NOT NULL,
  description STRING(MAX) NOT NULL,
  metadata JSON,
  search_tokens TOKENLIST AS (
    TOKENLIST_CONCAT([
      TOKENIZE_FULLTEXT(http_path, token_category => 'title'),
      TOKENIZE_FULLTEXT(description)
    ])
  ) STORED HIDDEN,
  path_ngrams TOKENLIST AS (
    TOKENIZE_NGRAMS(http_path, ngram_size_min => 3, ngram_size_max => 3)
  ) STORED HIDDEN,
  path_lower_ngrams TOKENLIST AS (
    TOKENIZE_NGRAMS(LOWER(http_path), ngram_size_min => 3, ngram_size_max => 4)
  ) STORED HIDDEN,
  metadata_tokens TOKENLIST AS (
    TOKENIZE_JSON(metadata)
  ) STORED HIDDEN,
  latency_tokens TOKENLIST AS (
    TOKENIZE_NUMBER(latency_budget_ms, comparison_type => 'all', min => 0, max => 60000)
  ) STORED HIDDEN,
  CONSTRAINT fk_apis_service
    FOREIGN KEY (service_id) REFERENCES Services (service_id),
) PRIMARY KEY (api_id);

-- 2.4 Adrs (10 Architecture Decision Records; owner_id folds N:1 OWNS_ADR)
CREATE TABLE Adrs (
  adr_id STRING(64) NOT NULL,
  title STRING(512) NOT NULL,
  lifecycle_status STRING(32) NOT NULL,
  owner_id STRING(64) NOT NULL,
  decision_date DATE NOT NULL,
  summary STRING(MAX) NOT NULL,
  search_tokens TOKENLIST AS (
    TOKENLIST_CONCAT([
      TOKENIZE_FULLTEXT(title, token_category => 'title'),
      TOKENIZE_FULLTEXT(summary)
    ])
  ) STORED HIDDEN,
  CONSTRAINT fk_adrs_owner
    FOREIGN KEY (owner_id) REFERENCES Owners (owner_id),
) PRIMARY KEY (adr_id);

-- 2.5 Requirements (14 Compliance & SLA Requirements; owner_id folds N:1 OWNS_REQ)
CREATE TABLE Requirements (
  req_id STRING(64) NOT NULL,
  framework STRING(64) NOT NULL,
  title STRING(512) NOT NULL,
  severity STRING(32) NOT NULL,
  owner_id STRING(64) NOT NULL,
  description STRING(MAX) NOT NULL,
  search_tokens TOKENLIST AS (
    TOKENLIST_CONCAT([
      TOKENIZE_FULLTEXT(title, token_category => 'title'),
      TOKENIZE_FULLTEXT(description)
    ])
  ) STORED HIDDEN,
  CONSTRAINT fk_requirements_owner
    FOREIGN KEY (owner_id) REFERENCES Owners (owner_id),
) PRIMARY KEY (req_id);

-- -----------------------------------------------------------------------------
-- 3. Path B Unstructured Knowledge, Operational Audit & Custom FTS Dictionary
-- -----------------------------------------------------------------------------

-- 3.1 DocumentChunks (Chunked Markdown corpus: 45 SDLC files -> GraphRAG nodes)
--     Enforces INV-08: 768-dim FLOAT32 embedding vector_length for ScaNN ANN.
CREATE TABLE DocumentChunks (
  chunk_id STRING(128) NOT NULL,
  doc_type STRING(32) NOT NULL,
  entity_id STRING(64) NOT NULL,
  source_path STRING(512) NOT NULL,
  section_heading STRING(512) NOT NULL,
  chunk_index INT64 NOT NULL,
  content STRING(MAX) NOT NULL,
  content_hash STRING(64) NOT NULL,
  embedding ARRAY<FLOAT32>(vector_length=>768),
  embedding_model_version STRING(64),
  updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP()),
  content_tokens TOKENLIST AS (
    TOKENLIST_CONCAT([
      TOKENIZE_FULLTEXT(section_heading, token_category => 'title'),
      TOKENIZE_FULLTEXT(content)
    ])
  ) STORED HIDDEN,
  content_ngrams TOKENLIST AS (
    TOKENIZE_NGRAMS(content, ngram_size_min => 3, ngram_size_max => 3)
  ) STORED HIDDEN,
) PRIMARY KEY (chunk_id);

-- 3.2 AgentQueryAudit (Operational audit log: hot table stays on default SSD
--     for fast PK uniqueness checks; cold JSON payload spills to HDD after 30d;
--     Row Deletion Policy purges records older than 90 days automatically.)
CREATE TABLE AgentQueryAudit (
  audit_id STRING(64) NOT NULL DEFAULT (GENERATE_UUID()),
  created_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
  persona_id STRING(64) NOT NULL,
  caller_email STRING(256),
  tool_name STRING(128) NOT NULL,
  status STRING(32) NOT NULL,
  latency_ms INT64,
  raw_payload JSON OPTIONS (locality_group = 'sdlc_cold_archive'),
) PRIMARY KEY (audit_id),
  ROW DELETION POLICY (OLDER_THAN(created_at, INTERVAL 90 DAY));

-- 3.3 SdlcSynonymDict (Custom FTS Dictionary — exact 2-column schema required;
--     default schema only; single lowercase keys; 15s staleness SLA.)
CREATE TABLE SdlcSynonymDict (
  Key STRING(MAX) NOT NULL,
  Value ARRAY<STRING(MAX)> NOT NULL,
) PRIMARY KEY (Key),
  OPTIONS (
    fulltext_dictionary_table = true,
    fulltext_dictionary_staleness = '15s'
  );

-- -----------------------------------------------------------------------------
-- 4. Interleaved Heterogeneous Edge Tables (Source != Destination Node Table)
--    Co-locates outgoing edges physically with parent source nodes while
--    enforcing destination referential integrity with ON DELETE CASCADE.
-- -----------------------------------------------------------------------------

-- 4.1 ServiceCallsApi (48 seed rows: Services -> Apis)
-- TODO(Lab 3): Define the interleaved heterogeneous edge table `ServiceCallsApi`.
--   Required columns (matching data/structured/edges/service_calls_api.csv):
--     - service_id       STRING(64) NOT NULL
--     - api_id           STRING(64) NOT NULL
--     - call_mode        STRING(32) NOT NULL
--     - is_critical      BOOL NOT NULL
--     - migration_status STRING(32) NOT NULL
--     - rps_estimate     INT64 NOT NULL
--   Required constraints & storage layout:
--     - Destination FOREIGN KEY `fk_service_calls_api_dest` on `(api_id)`
--       referencing `Apis (api_id)` with `ON DELETE CASCADE`
--     - PRIMARY KEY `(service_id, api_id)`
--     - Physically interleaved in parent `Services` with cascading delete
--       (safe because source `Services` != destination `Apis`!).

-- 4.2 AdrGovernsApi (36 seed rows: Adrs -> Apis)
CREATE TABLE AdrGovernsApi (
  adr_id STRING(64) NOT NULL,
  api_id STRING(64) NOT NULL,
  governance_type STRING(32) NOT NULL,
  effective_date DATE NOT NULL,
  CONSTRAINT fk_adr_governs_api_dest
    FOREIGN KEY (api_id) REFERENCES Apis (api_id) ON DELETE CASCADE,
) PRIMARY KEY (adr_id, api_id),
  INTERLEAVE IN PARENT Adrs ON DELETE CASCADE;

-- 4.3 ReqAppliesToApi (33 seed rows: Requirements -> Apis)
CREATE TABLE ReqAppliesToApi (
  req_id STRING(64) NOT NULL,
  api_id STRING(64) NOT NULL,
  compliance_status STRING(32) NOT NULL,
  CONSTRAINT fk_req_applies_api_dest
    FOREIGN KEY (api_id) REFERENCES Apis (api_id) ON DELETE CASCADE,
) PRIMARY KEY (req_id, api_id),
  INTERLEAVE IN PARENT Requirements ON DELETE CASCADE;

-- 4.4 ReqAppliesToService (26 seed rows: Requirements -> Services)
CREATE TABLE ReqAppliesToService (
  req_id STRING(64) NOT NULL,
  service_id STRING(64) NOT NULL,
  compliance_status STRING(32) NOT NULL,
  CONSTRAINT fk_req_applies_service_dest
    FOREIGN KEY (service_id) REFERENCES Services (service_id) ON DELETE CASCADE,
) PRIMARY KEY (req_id, service_id),
  INTERLEAVE IN PARENT Requirements ON DELETE CASCADE;

-- 4.5 ChunkEntityMentions (Polymorphic Path B citation links interleaved under DocumentChunks)
CREATE TABLE ChunkEntityMentions (
  chunk_id STRING(128) NOT NULL,
  entity_type STRING(32) NOT NULL,
  entity_id STRING(64) NOT NULL,
  mention_source STRING(64) NOT NULL DEFAULT ('REGEX_EXTRACT'),
  CONSTRAINT chk_chunk_mention_entity_type
    CHECK (entity_type IN ('API', 'SERVICE', 'ADR', 'REQUIREMENT', 'OWNER')),
) PRIMARY KEY (chunk_id, entity_type, entity_id),
  INTERLEAVE IN PARENT DocumentChunks ON DELETE CASCADE;

-- -----------------------------------------------------------------------------
-- 5. Standalone Self-Referential Dual-FK Edge Tables (INV-06 Compliance)
--    Cloud Spanner forbids combining parent interleaving cascade with a
--    self-referencing cascading FK to the same table. Use standalone tables
--    with two enforced cascading FKs for bidirectional cascade safety.
-- -----------------------------------------------------------------------------

-- 5.1 ServiceDependsOnService (28 seed rows: Services -> Services)
-- TODO(Lab 3): Define the standalone self-referential dual-FK table
--   `ServiceDependsOnService` (DO NOT interleave in `Services` — see INV-06!).
--   Required columns (matching data/structured/edges/service_depends_on_service.csv):
--     - from_service_id STRING(64) NOT NULL
--     - to_service_id   STRING(64) NOT NULL
--     - dependency_type STRING(32) NOT NULL
--     - is_sync         BOOL NOT NULL
--     - criticality     STRING(32) NOT NULL
--   Required constraints & key:
--     - CONSTRAINT `fk_svc_dep_from`: FOREIGN KEY (from_service_id) -> Services (service_id) ON DELETE CASCADE
--     - CONSTRAINT `fk_svc_dep_to`:   FOREIGN KEY (to_service_id)   -> Services (service_id) ON DELETE CASCADE
--     - PRIMARY KEY `(from_service_id, to_service_id)`

-- 5.2 AdrSupersedesAdr (3 seed rows: Adrs -> Adrs)
CREATE TABLE AdrSupersedesAdr (
  adr_id STRING(64) NOT NULL,
  supersedes_adr_id STRING(64) NOT NULL,
  superseded_date DATE NOT NULL,
  CONSTRAINT fk_adr_sup_newer
    FOREIGN KEY (adr_id) REFERENCES Adrs (adr_id) ON DELETE CASCADE,
  CONSTRAINT fk_adr_sup_older
    FOREIGN KEY (supersedes_adr_id) REFERENCES Adrs (adr_id) ON DELETE CASCADE,
) PRIMARY KEY (adr_id, supersedes_adr_id);

-- -----------------------------------------------------------------------------
-- 6. Pattern-1 SQL SECURITY INVOKER Bridge Views (Path B -> Path A Citations)
--    Each view projects the exact single-table primary key of ChunkEntityMentions
--    so Spanner Graph validates key uniqueness while preserving index pushdown.
-- -----------------------------------------------------------------------------

-- TODO(Lab 3): Create the `ChunkCitesApiView` bridge view (INV-05).
--   Requirements:
--     - Declare explicit `SQL SECURITY INVOKER`
--     - Project `chunk_id`, `entity_type`, `entity_id`, `mention_source`
--       from `ChunkEntityMentions` filtered to `entity_type = 'API'`.

CREATE OR REPLACE VIEW ChunkCitesServiceView
SQL SECURITY INVOKER AS
SELECT
  m.chunk_id,
  m.entity_type,
  m.entity_id,
  m.mention_source
FROM ChunkEntityMentions AS m
WHERE m.entity_type = 'SERVICE';

CREATE OR REPLACE VIEW ChunkCitesAdrView
SQL SECURITY INVOKER AS
SELECT
  m.chunk_id,
  m.entity_type,
  m.entity_id,
  m.mention_source
FROM ChunkEntityMentions AS m
WHERE m.entity_type = 'ADR';

CREATE OR REPLACE VIEW ChunkCitesReqView
SQL SECURITY INVOKER AS
SELECT
  m.chunk_id,
  m.entity_type,
  m.entity_id,
  m.mention_source
FROM ChunkEntityMentions AS m
WHERE m.entity_type = 'REQUIREMENT';

-- -----------------------------------------------------------------------------
-- 7. Unified Schematized Property Graph (`SdlcGraph`)
-- -----------------------------------------------------------------------------

-- TODO(Lab 3): Author the `SdlcGraph` property graph DDL uniting Path A & Path B.
--   Enforce INV-02 (unique element aliases), INV-03 (exact PK arity & types),
--   INV-04 (no conflicting property types), and INV-05 (explicit KEY on views):
--
--   NODE TABLES (6):
--     1. Owners         -> KEY (owner_id)   LABEL Owner         PROPERTIES ALL COLUMNS
--     2. Services       -> KEY (service_id) LABEL Service       PROPERTIES ALL COLUMNS
--     3. Apis           -> KEY (api_id)     LABEL Api           PROPERTIES ALL COLUMNS
--     4. Adrs           -> KEY (adr_id)     LABEL Adr           PROPERTIES ALL COLUMNS
--     5. Requirements   -> KEY (req_id)     LABEL Requirement   PROPERTIES ALL COLUMNS
--     6. DocumentChunks -> KEY (chunk_id)   LABEL DocumentChunk PROPERTIES ALL COLUMNS
--
--   EDGE TABLES (14 total):
--     Explicit M:N tables (6):
--       - ServiceCallsApi         (Services -> Apis,         LABEL CALLS_API)
--       - ServiceDependsOnService (Services -> Services,     LABEL DEPENDS_ON)
--       - AdrGovernsApi           (Adrs -> Apis,             LABEL GOVERNS)
--       - AdrSupersedesAdr        (Adrs -> Adrs,             LABEL SUPERSEDES)
--       - ReqAppliesToApi         (Requirements -> Apis,     LABEL APPLIES_TO_API)
--       - ReqAppliesToService     (Requirements -> Services, LABEL APPLIES_TO_SERVICE)
--     Dual-projected N:1 folded FK edges (4 — MUST use unique `AS` aliases per INV-02!):
--       - Apis AS ExposesApi            (Services -> Apis,         LABEL EXPOSES)
--       - Services AS TeamOwnsService   (Owners -> Services,       LABEL OWNS_SERVICE)
--       - Adrs AS TeamOwnsAdr           (Owners -> Adrs,           LABEL OWNS_ADR)
--       - Requirements AS TeamOwnsReq   (Owners -> Requirements,   LABEL OWNS_REQ)
--     View-backed Path B citation edges (4 — MUST declare explicit KEY (chunk_id, entity_type, entity_id)!):
--       - ChunkCitesApiView             (DocumentChunks -> Apis,         LABEL CITES_API)
--       - ChunkCitesServiceView         (DocumentChunks -> Services,     LABEL CITES_SERVICE)
--       - ChunkCitesAdrView             (DocumentChunks -> Adrs,         LABEL CITES_ADR)
--       - ChunkCitesReqView             (DocumentChunks -> Requirements, LABEL CITES_REQ)
