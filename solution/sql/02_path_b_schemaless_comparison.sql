-- =============================================================================
-- 02_path_b_schemaless_comparison.sql
-- Acme Commerce Platform — Schemaless / Hybrid Graph Comparison Schema
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
--
-- Structural Invariant INV-07 Enforced:
--   - DYNAMIC LABEL node/edge tables MUST live in a dedicated separate property
--     graph (SdlcDynamicGraph) and MUST NEVER mix with static node/edge tables.
--   - Stored dynamic label strings and depth-1 JSON property keys MUST be
--     lowercase (mixed/uppercase values are silently skipped at query time),
--     enforced here via CHECK constraints.
--   - PROPERTIES ALL COLUMNS EXCEPT (properties) prevents duplicate nested
--     "properties": {"properties": {...}} objects in SAFE_TO_JSON(n).
--   - Per-label uniqueness is enforced via a HIDDEN virtual generated column
--     (STRING(MAX)) backed by a UNIQUE NULL_FILTERED INDEX.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Generic Schemaless Node Table (GraphNode)
-- -----------------------------------------------------------------------------
CREATE TABLE GraphNode (
  id STRING(128) NOT NULL,
  label STRING(64) NOT NULL,
  domain_key STRING(128) NOT NULL,
  properties JSON,
  -- Virtual HIDDEN generated column (GoogleSQL requires STRING(MAX) for virtual
  -- generated STRING columns) enabling per-label uniqueness without bloating
  -- non-service rows.
  service_code_key STRING(MAX) AS (
    IF(label = 'service', STRING(properties.service_id), NULL)
  ) HIDDEN,
  CONSTRAINT chk_graph_node_label_lowercase
    CHECK (LOWER(label) = label),
  CONSTRAINT chk_graph_node_prop_keys_lowercase
    CHECK (
      NOT ARRAY_INCLUDES(
        COALESCE(JSON_KEYS(properties, 1), []),
        key -> key <> LOWER(key)
      )
    ),
  CONSTRAINT chk_graph_node_service_name_str
    CHECK (
      IF(label = 'service', JSON_TYPE(properties.service_name) = 'string', TRUE)
    ),
) PRIMARY KEY (id);

-- -----------------------------------------------------------------------------
-- 2. Interleaved Schemaless Edge Table (GraphEdge)
--    No self-referencing cascading destination FK on GraphNode (respects INV-06);
--    forward adjacency co-locates with source GraphNode via INTERLEAVE IN PARENT.
-- -----------------------------------------------------------------------------
CREATE TABLE GraphEdge (
  id STRING(128) NOT NULL,
  dest_id STRING(128) NOT NULL,
  edge_id STRING(128) NOT NULL,
  label STRING(64) NOT NULL,
  properties JSON,
  CONSTRAINT chk_graph_edge_label_lowercase
    CHECK (LOWER(label) = label),
  CONSTRAINT chk_graph_edge_prop_keys_lowercase
    CHECK (
      NOT ARRAY_INCLUDES(
        COALESCE(JSON_KEYS(properties, 1), []),
        key -> key <> LOWER(key)
      )
    ),
) PRIMARY KEY (id, dest_id, edge_id),
  INTERLEAVE IN PARENT GraphNode ON DELETE CASCADE;

-- -----------------------------------------------------------------------------
-- 3. Per-Label Uniqueness & Reverse Covering Indexes
-- -----------------------------------------------------------------------------

-- Enforces unique service_id strictly across GraphNode rows where label = 'service'
CREATE UNIQUE NULL_FILTERED INDEX IdxGraphNodeUniqueServiceCode
  ON GraphNode (service_code_key);

-- Fast label-scoped lookup (e.g., all 'api' or 'service' nodes)
CREATE INDEX IdxGraphNodeByLabel
  ON GraphNode (label)
  STORING (domain_key);

-- Reverse covering index co-located with destination GraphNode splits
-- (uses INTERLEAVE IN without PARENT for positional key matching: dest_id -> id)
CREATE INDEX IdxGraphEdgeReverse
  ON GraphEdge (dest_id, id, edge_id)
  STORING (label, properties),
  INTERLEAVE IN GraphNode;

-- -----------------------------------------------------------------------------
-- 4. Dedicated Schemaless Property Graph (SdlcDynamicGraph)
--    Strictly isolated from SdlcGraph static tables (INV-07).
--    GQL queries against SdlcDynamicGraph are case-insensitive (e.g., :Service,
--    :DEPENDS_ON, :CALLS_API match stored lowercase 'service', 'depends_on',
--    'calls_api').
-- -----------------------------------------------------------------------------
CREATE OR REPLACE PROPERTY GRAPH SdlcDynamicGraph
  NODE TABLES (
    GraphNode
      KEY (id)
      LABEL SdlcEntity
      PROPERTIES ALL COLUMNS EXCEPT (properties)
      DYNAMIC LABEL (label)
      DYNAMIC PROPERTIES (properties)
  )
  EDGE TABLES (
    GraphEdge
      KEY (id, dest_id, edge_id)
      SOURCE KEY (id) REFERENCES GraphNode (id)
      DESTINATION KEY (dest_id) REFERENCES GraphNode (id)
      PROPERTIES ALL COLUMNS EXCEPT (properties)
      DYNAMIC LABEL (label)
      DYNAMIC PROPERTIES (properties)
  );
