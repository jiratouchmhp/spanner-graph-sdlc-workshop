// =============================================================================
// Acme Commerce SDLC Knowledge Graph — Heap-Safe APOC Streaming Export
//
// Exports headerless CSV files (separateHeader: true) compatible with Cloud
// Spanner Dataflow's GCS_Text_to_Cloud_Spanner template, plus a full JSONL
// snapshot for schemaless ingestion and offline audit parity.
//
// Notes:
//   - Never exports Neo4j internal id(n); always projects stable domain keys.
//   - Uses batchSize: 5000 and stream: false to keep JVM heap bounded.
//   - Writes to /var/lib/neo4j/import/export (mounted to ./export on host).
// =============================================================================

// -----------------------------------------------------------------------------
// 1. Export 5 Node Tables (68 total rows)
// -----------------------------------------------------------------------------
CALL apoc.export.csv.query(
  "MATCH (o:Owner)
   RETURN o.owner_id AS owner_id,
          o.team_name AS team_name,
          o.org_unit AS org_unit,
          o.slack_channel AS slack_channel,
          o.email AS email,
          o.oncall_pager AS oncall_pager,
          o.clearance_tier AS clearance_tier,
          o.escalation_lead AS escalation_lead
   ORDER BY o.owner_id",
  "export/nodes_owners.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

CALL apoc.export.csv.query(
  "MATCH (s:Service)
   RETURN s.service_id AS service_id,
          s.service_name AS service_name,
          s.tier AS tier,
          s.criticality AS criticality,
          s.owner_id AS owner_id,
          s.sla_target_pct AS sla_target_pct,
          s.domain AS domain,
          s.lifecycle_status AS lifecycle_status,
          s.description AS description
   ORDER BY s.service_id",
  "export/nodes_services.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

CALL apoc.export.csv.query(
  "MATCH (a:Api)
   RETURN a.api_id AS api_id,
          a.service_id AS service_id,
          a.http_method AS http_method,
          a.http_path AS http_path,
          a.api_version AS api_version,
          a.lifecycle_status AS lifecycle_status,
          a.criticality AS criticality,
          a.is_public AS is_public,
          a.latency_budget_ms AS latency_budget_ms,
          a.description AS description
   ORDER BY a.api_id",
  "export/nodes_apis.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

CALL apoc.export.csv.query(
  "MATCH (d:Adr)
   RETURN d.adr_id AS adr_id,
          d.title AS title,
          d.lifecycle_status AS lifecycle_status,
          d.owner_id AS owner_id,
          d.decision_date AS decision_date,
          d.summary AS summary
   ORDER BY d.adr_id",
  "export/nodes_adrs.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

CALL apoc.export.csv.query(
  "MATCH (r:Requirement)
   RETURN r.req_id AS req_id,
          r.framework AS framework,
          r.title AS title,
          r.severity AS severity,
          r.owner_id AS owner_id,
          r.description AS description
   ORDER BY r.req_id",
  "export/nodes_requirements.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

// -----------------------------------------------------------------------------
// 2. Export 6 Explicit M:N Edge Tables (174 total rows -> Spanner Edge Tables)
// -----------------------------------------------------------------------------
CALL apoc.export.csv.query(
  "MATCH (s:Service)-[r:CALLS_API]->(a:Api)
   RETURN s.service_id AS service_id,
          a.api_id AS api_id,
          r.call_mode AS call_mode,
          r.is_critical AS is_critical,
          r.migration_status AS migration_status,
          r.rps_estimate AS rps_estimate
   ORDER BY s.service_id, a.api_id",
  "export/edges_service_calls_api.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

CALL apoc.export.csv.query(
  "MATCH (src:Service)-[r:DEPENDS_ON]->(dst:Service)
   RETURN src.service_id AS from_service_id,
          dst.service_id AS to_service_id,
          r.dependency_type AS dependency_type,
          r.is_sync AS is_sync,
          r.criticality AS criticality
   ORDER BY src.service_id, dst.service_id",
  "export/edges_service_depends_on_service.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

CALL apoc.export.csv.query(
  "MATCH (d:Adr)-[r:GOVERNS]->(a:Api)
   RETURN d.adr_id AS adr_id,
          a.api_id AS api_id,
          r.governance_type AS governance_type,
          r.effective_date AS effective_date
   ORDER BY d.adr_id, a.api_id",
  "export/edges_adr_governs_api.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

CALL apoc.export.csv.query(
  "MATCH (newer:Adr)-[r:SUPERSEDES]->(older:Adr)
   RETURN newer.adr_id AS adr_id,
          older.adr_id AS supersedes_adr_id,
          r.superseded_date AS superseded_date
   ORDER BY newer.adr_id, older.adr_id",
  "export/edges_adr_supersedes_adr.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

CALL apoc.export.csv.query(
  "MATCH (req:Requirement)-[r:APPLIES_TO_API]->(a:Api)
   RETURN req.req_id AS req_id,
          a.api_id AS api_id,
          r.compliance_status AS compliance_status
   ORDER BY req.req_id, a.api_id",
  "export/edges_req_applies_to_api.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

CALL apoc.export.csv.query(
  "MATCH (req:Requirement)-[r:APPLIES_TO_SERVICE]->(s:Service)
   RETURN req.req_id AS req_id,
          s.service_id AS service_id,
          r.compliance_status AS compliance_status
   ORDER BY req.req_id, s.service_id",
  "export/edges_req_applies_to_service.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

// -----------------------------------------------------------------------------
// 3. Export 4 Dual-Projected N:1 Edge Tables (60 rows — Parity Audit Only;
//    already stored as FK columns on Services/Apis/Adrs/Requirements in Spanner)
// -----------------------------------------------------------------------------
CALL apoc.export.csv.query(
  "MATCH (s:Service)-[:EXPOSES]->(a:Api)
   RETURN s.service_id AS service_id, a.api_id AS api_id
   ORDER BY s.service_id, a.api_id",
  "export/edges_api_exposes_from_service.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

CALL apoc.export.csv.query(
  "MATCH (o:Owner)-[:OWNS_SERVICE]->(s:Service)
   RETURN o.owner_id AS owner_id, s.service_id AS service_id
   ORDER BY o.owner_id, s.service_id",
  "export/edges_owner_owns_service.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

CALL apoc.export.csv.query(
  "MATCH (o:Owner)-[:OWNS_ADR]->(d:Adr)
   RETURN o.owner_id AS owner_id, d.adr_id AS adr_id
   ORDER BY o.owner_id, d.adr_id",
  "export/edges_owner_owns_adr.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

CALL apoc.export.csv.query(
  "MATCH (o:Owner)-[:OWNS_REQ]->(req:Requirement)
   RETURN o.owner_id AS owner_id, req.req_id AS req_id
   ORDER BY o.owner_id, req.req_id",
  "export/edges_owner_owns_req.csv",
  { batchSize: 5000, separateHeader: true, quotes: "ifNeeded", stream: false }
);

// -----------------------------------------------------------------------------
// 4. Export Full Graph JSONL Snapshot (Schemaless / Audit Parity)
// -----------------------------------------------------------------------------
CALL apoc.export.json.all(
  "export/all_graph_export.jsonl",
  { batchSize: 5000, useTypes: true, stream: false }
);
