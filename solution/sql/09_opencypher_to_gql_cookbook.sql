-- =============================================================================
-- 09_opencypher_to_gql_cookbook.sql
-- Acme Commerce Platform — Lab 1 Neo4j openCypher v9 -> Spanner ISO GQL Cookbook
-- 10 Side-by-Side Translations Covering Every High-Frequency Migration Trap
--
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
-- Standards: ISO/IEC 39075:2024 (GQL) + ISO/IEC 9075-16:2023 (SQL/PGQ)
-- =============================================================================

-- =============================================================================
-- Translation 1: Cycle Safety — Default WALK vs MATCH TRAIL
--   Trap: Neo4j openCypher enforces edge uniqueness by default. Cloud Spanner
--   GQL defaults to WALK (repeats cycle edges on svc-orders <-> svc-notifications).
--   Always specify MATCH TRAIL for Neo4j parity!
--
--   Neo4j openCypher:
--     MATCH (upstream:Service)-[r:DEPENDS_ON*1..3]->(target:Service {service_id: 'svc-checkout'})
--     RETURN DISTINCT upstream.service_id AS upstream_service_id, length(r) AS hops
--     ORDER BY hops ASC, upstream_service_id ASC;
-- =============================================================================
GRAPH SdlcGraph
MATCH TRAIL (upstream:Service)-[deps:DEPENDS_ON]->{1,3}(target:Service {service_id: 'svc-checkout'})
RETURN DISTINCT
  upstream.service_id AS upstream_service_id,
  ARRAY_LENGTH(deps) AS hops
ORDER BY hops ASC, upstream_service_id ASC;

-- =============================================================================
-- Translation 2: UNWIND -> FOR (Unnesting Arrays in GQL)
--   Trap: openCypher UNWIND does not exist in ISO GQL; use FOR elem IN array_expr.
--   Note: Scalar variables from FOR/LET can only be referenced in the outermost
--   WHERE clause of MATCH (never inside inline `{service_id: sid}` syntax!).
--
--   Neo4j openCypher:
--     UNWIND ['svc-payments', 'svc-auth', 'svc-ledger'] AS sid
--     MATCH (s:Service {service_id: sid})-[:EXPOSES]->(a:Api)
--     RETURN s.service_id AS service_id, a.api_id AS api_id, a.http_path AS http_path
--     ORDER BY service_id ASC, api_id ASC;
-- =============================================================================
GRAPH SdlcGraph
LET target_services = ['svc-payments', 'svc-auth', 'svc-ledger']
FOR sid IN target_services
MATCH (s:Service)-[:EXPOSES]->(a:Api)
WHERE s.service_id = sid
RETURN
  s.service_id AS service_id,
  a.api_id AS api_id,
  a.http_path AS http_path
ORDER BY service_id ASC, api_id ASC;

-- =============================================================================
-- Translation 3: Returning Whole Nodes/Edges -> SAFE_TO_JSON(n)
--   Trap: Top-level `RETURN s` fails in Spanner GQL ("Returning expressions of
--   type GRAPH_ELEMENT is not allowed"). Wrap in SAFE_TO_JSON(...) (preferred
--   over TO_JSON(...) because SAFE_TO_JSON safely maps TOKENLIST columns to JSON null).
--
--   Neo4j openCypher:
--     MATCH (s:Service {tier: 'TIER_0'})
--     RETURN s
--     ORDER BY s.service_id ASC
--     LIMIT 5;
-- =============================================================================
GRAPH SdlcGraph
MATCH (s:Service {tier: 'TIER_0'})
RETURN
  s.service_id AS service_id,
  SAFE_TO_JSON(s) AS service_node_json
ORDER BY service_id ASC
LIMIT 5;

-- =============================================================================
-- Translation 4: Standalone ORDER BY Trap & OFFSET-Before-LIMIT Placement
--   Trap 1: Standalone `ORDER BY ... RETURN ... LIMIT ...` is silently ignored!
--           Always attach ORDER BY inside the RETURN statement.
--   Trap 2: Inside RETURN, OFFSET MUST precede LIMIT (`LIMIT 5 OFFSET 2` errors!).
--   Trap 3: Every non-trivial RETURN expression requires an explicit AS alias.
--
--   Neo4j openCypher:
--     MATCH (a:Api)
--     RETURN a.api_id, a.latency_budget_ms, toUpper(a.http_method)
--     ORDER BY a.latency_budget_ms ASC, a.api_id ASC
--     SKIP 2 LIMIT 5;
-- =============================================================================
GRAPH SdlcGraph
MATCH (a:Api)
RETURN
  a.api_id AS api_id,
  a.latency_budget_ms AS latency_budget_ms,
  UPPER(a.http_method) AS method_upper
ORDER BY latency_budget_ms ASC, api_id ASC
OFFSET 2
LIMIT 5;

-- =============================================================================
-- Translation 5: OPTIONAL MATCH ... WHERE vs Post-Join FILTER Trap
--   Trap: Writing `OPTIONAL MATCH (s)-[c:CALLS_API]->(a) FILTER c.migration_status = 'AT_RISK'`
--   evaluates FILTER *after* the LEFT OUTER JOIN, silently dropping all services
--   with zero AT_RISK calls (converting LEFT JOIN into INNER JOIN). Keep optional
--   predicates INSIDE the OPTIONAL MATCH ... WHERE clause!
--
--   Neo4j openCypher:
--     MATCH (s:Service)
--     OPTIONAL MATCH (s)-[c:CALLS_API]->(a:Api)
--     WHERE c.migration_status = 'AT_RISK'
--     RETURN s.service_id AS service_id, a.api_id AS at_risk_api_id, c.rps_estimate AS legacy_rps
--     ORDER BY service_id ASC;
-- =============================================================================
GRAPH SdlcGraph
MATCH (s:Service)
OPTIONAL MATCH (s)-[c:CALLS_API]->(a:Api)
WHERE c.migration_status = 'AT_RISK'
RETURN
  s.service_id AS service_id,
  a.api_id AS at_risk_api_id,
  c.rps_estimate AS legacy_rps
ORDER BY service_id ASC;

-- =============================================================================
-- Translation 6: shortestPath(...) -> MATCH ANY SHORTEST + ARRAY_LENGTH(e)
--   Trap: openCypher `shortestPath((src)-[:DEPENDS_ON*1..5]->(dst))` translates
--   to `MATCH ANY SHORTEST (src)-[deps:DEPENDS_ON]->{1,5}(dst)` with finite
--   upper bound `{1,5}` (unbounded `*` is unsupported).
--
--   Neo4j openCypher:
--     MATCH (src:Service {service_id: 'svc-webhooks'}), (dst:Service {service_id: 'svc-payments'}),
--           p = shortestPath((src)-[:DEPENDS_ON*1..5]->(dst))
--     RETURN src.service_id AS src_id, dst.service_id AS dst_id, length(p) AS hop_count;
-- =============================================================================
GRAPH SdlcGraph
MATCH ANY SHORTEST (src:Service {service_id: 'svc-webhooks'})-[deps:DEPENDS_ON]->{1,5}(dst:Service {service_id: 'svc-payments'})
RETURN
  src.service_id AS src_id,
  dst.service_id AS dst_id,
  ARRAY_LENGTH(deps) AS hop_count,
  ARRAY_TRANSFORM(deps, d -> d.to_service_id) AS path_hops;

-- =============================================================================
-- Translation 7: collect() & List Comprehension -> ARRAY_AGG + ARRAY_FILTER + ARRAY_TRANSFORM
--   Trap 1: openCypher `collect(x)` -> `ARRAY_AGG(x)`.
--   Trap 2: openCypher list comprehension `[x IN list WHERE pred | expr]` ->
--           `ARRAY_TRANSFORM(ARRAY_FILTER(list, x -> pred), x -> expr)`.
--   Trap 3: `ARRAY_FIRST([])` throws a runtime error — use `[SAFE_OFFSET(0)]`!
--
--   Neo4j openCypher:
--     MATCH (s:Service)-[:EXPOSES]->(a:Api)
--     WITH s, collect(a) AS apis
--     RETURN s.service_id AS service_id,
--            [x IN apis WHERE x.is_public = true | x.http_path] AS public_paths,
--            head([x IN apis WHERE x.is_public = true | x.api_id]) AS first_public_api
--     ORDER BY service_id ASC;
-- =============================================================================
GRAPH SdlcGraph
MATCH (s:Service)-[:EXPOSES]->(a:Api)
WITH
  s.service_id AS service_id,
  ARRAY_AGG(STRUCT(a.api_id AS api_id, a.http_path AS http_path, a.is_public AS is_public) ORDER BY a.api_id ASC) AS apis
LET public_apis = ARRAY_FILTER(apis, x -> x.is_public = TRUE)
RETURN
  service_id,
  ARRAY_TRANSFORM(public_apis, x -> x.http_path) AS public_paths,
  ARRAY_TRANSFORM(public_apis, x -> x.api_id)[SAFE_OFFSET(0)] AS first_public_api
ORDER BY service_id ASC;

-- =============================================================================
-- Translation 8: size(pattern) & EXISTS -> VALUE { MATCH ... } Subquery
--   Trap: openCypher `size((s)<-[:DEPENDS_ON]-())` is unsupported. Rewrite as
--   `VALUE { MATCH (inner_s:Service)<-[:DEPENDS_ON]-(:Service) WHERE inner_s = s RETURN COUNT(*) AS cnt }`.
--   CRITICAL: Never redeclare outer variable `s` inside a GQL subquery — bind a
--   fresh inner variable (`inner_s`) and correlate with `WHERE inner_s = s`!
--
--   Neo4j openCypher:
--     MATCH (s:Service)
--     RETURN s.service_id AS service_id,
--            size((s)<-[:DEPENDS_ON]-()) AS in_degree,
--            EXISTS { MATCH (s)-[:CALLS_API]->(:Api {api_id: 'api-auth-token-v2'}) } AS uses_auth_hub
--     ORDER BY in_degree DESC, service_id ASC;
-- =============================================================================
GRAPH SdlcGraph
MATCH (s:Service)
LET in_degree = VALUE {
  MATCH (inner_s:Service)<-[:DEPENDS_ON]-(:Service)
  WHERE inner_s = s
  RETURN COUNT(*) AS cnt
}
LET uses_auth_hub = EXISTS {
  MATCH (inner_s:Service)-[:CALLS_API]->(:Api {api_id: 'api-auth-token-v2'})
  WHERE inner_s = s
}
RETURN
  s.service_id AS service_id,
  in_degree,
  uses_auth_hub
ORDER BY in_degree DESC, service_id ASC;

-- =============================================================================
-- Translation 9: Custom Criticality CASE Sort + DATA_FEED Classification
--   Trap: Sorting criticality alphabetically orders CRITICAL, HIGH, LOW, MEDIUM
--   (putting LOW before MEDIUM!). Use explicit CASE rank mapping and classify
--   any path traversing a DATA_FEED edge as DATA_FEED_CONSUMER.
--
--   Neo4j openCypher:
--     MATCH (u:Service)-[deps:DEPENDS_ON*1..3]->(d:Service)-[:CALLS_API]->(:Api {api_id: 'api-payments-charge-v2'})
--     WITH u, min(length(deps) + 1) AS hops,
--          CASE WHEN any(x IN deps WHERE x.dependency_type = 'DATA_FEED')
--               THEN 'DATA_FEED_CONSUMER' ELSE 'TRANSITIVE_UPSTREAM' END AS category
--     RETURN u.service_id, hops, category, u.criticality
--     ORDER BY hops ASC,
--              CASE u.criticality WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END ASC,
--              u.service_id ASC;
-- =============================================================================
GRAPH SdlcGraph
MATCH TRAIL (u:Service)-[deps:DEPENDS_ON]->{1,3}(d:Service)-[:CALLS_API]->(:Api {api_id: 'api-payments-charge-v2'})
LET dep_types = ARRAY_AGG(deps.dependency_type)
LET hops = ARRAY_LENGTH(deps) + 1
LET impact_category = IF(ARRAY_INCLUDES(dep_types, 'DATA_FEED'), 'DATA_FEED_CONSUMER', 'TRANSITIVE_UPSTREAM')
LET criticality_rank = CASE u.criticality
  WHEN 'CRITICAL' THEN 1
  WHEN 'HIGH' THEN 2
  WHEN 'MEDIUM' THEN 3
  ELSE 4
END
RETURN
  u.service_id AS impacted_service_id,
  MIN(hops) AS hop_distance,
  ANY_VALUE(impact_category) AS impact_category,
  u.criticality AS criticality,
  criticality_rank
GROUP BY impacted_service_id, criticality, criticality_rank
ORDER BY hop_distance ASC, criticality_rank ASC, impacted_service_id ASC;

-- =============================================================================
-- Translation 10: Graph Mutations — Cypher MERGE / MATCH ... SET ... RETURN
--   Trap: ISO GQL in Spanner is read-only (no CREATE/MERGE/SET/DETACH DELETE).
--   Mutate backing relational tables with ACID GoogleSQL DML using:
--     - `INSERT OR UPDATE` (replaces Cypher `MERGE`)
--     - `UPDATE ... WHERE pk IN { GRAPH ... RETURN pk } THEN RETURN ...`
--     - `ON DELETE CASCADE` on interleaved/FK edges (replaces `DETACH DELETE`)
--
--   Neo4j openCypher:
--     MERGE (a:Adr {adr_id: 'ADR-011'})
--     SET a.title = 'Proposed GraphRAG Audit Policy', a.lifecycle_status = 'PROPOSED',
--         a.owner_id = 'team-security', a.decision_date = '2026-11-21',
--         a.summary = 'Standardize automated GraphRAG change reviews.'
--     RETURN a.adr_id, a.lifecycle_status;
-- =============================================================================

-- 10A. Cypher MERGE ... RETURN -> GoogleSQL INSERT OR UPDATE ... THEN RETURN
--      (Commented out to keep cookbook script safe for read-only batch runs.)
-- INSERT OR UPDATE INTO Adrs (
--   adr_id, title, lifecycle_status, owner_id, decision_date, summary
-- ) VALUES (
--   'ADR-011',
--   'Proposed GraphRAG Audit Policy',
--   'PROPOSED',
--   'team-security',
--   DATE '2026-11-21',
--   'Standardize automated GraphRAG change reviews.'
-- )
-- THEN RETURN adr_id, lifecycle_status;

-- 10B. Cypher MATCH ... SET ... RETURN -> GoogleSQL UPDATE ... IN { GRAPH ... } THEN RETURN
-- UPDATE Services
-- SET lifecycle_status = 'ACTIVE'
-- WHERE service_id IN {
--   GRAPH SdlcGraph
--   MATCH TRAIL (u:Service)-[:DEPENDS_ON]->{1,2}(:Service {service_id: 'svc-payments'})
--   RETURN u.service_id
-- }
-- THEN RETURN service_id, lifecycle_status;
