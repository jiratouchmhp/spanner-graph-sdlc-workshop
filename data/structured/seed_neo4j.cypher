// =============================================================================
// Acme Commerce SDLC Knowledge Graph — Idempotent Neo4j CE 5.x Seed Script
// Seeds exactly 68 nodes (5 labels) and 234 relationships (174 explicit M:N +
// 60 dual-projected N:1) from ./nodes/*.csv and ./edges/*.csv.
// =============================================================================

// -----------------------------------------------------------------------------
// 1. Domain Primary-Key Uniqueness Constraints (never rely on internal id(n))
// -----------------------------------------------------------------------------
CREATE CONSTRAINT owner_id_unique IF NOT EXISTS
FOR (o:Owner) REQUIRE o.owner_id IS UNIQUE;

CREATE CONSTRAINT service_id_unique IF NOT EXISTS
FOR (s:Service) REQUIRE s.service_id IS UNIQUE;

CREATE CONSTRAINT api_id_unique IF NOT EXISTS
FOR (a:Api) REQUIRE a.api_id IS UNIQUE;

CREATE CONSTRAINT adr_id_unique IF NOT EXISTS
FOR (d:Adr) REQUIRE d.adr_id IS UNIQUE;

CREATE CONSTRAINT req_id_unique IF NOT EXISTS
FOR (r:Requirement) REQUIRE r.req_id IS UNIQUE;

// -----------------------------------------------------------------------------
// 2. Stage 1 — Load All 68 Nodes (5 Labels)
// -----------------------------------------------------------------------------
LOAD CSV WITH HEADERS FROM 'file:///nodes/owners.csv' AS row
MERGE (o:Owner {owner_id: row.owner_id})
SET o.team_name       = row.team_name,
    o.org_unit        = row.org_unit,
    o.slack_channel   = row.slack_channel,
    o.email           = row.email,
    o.oncall_pager    = row.oncall_pager,
    o.clearance_tier  = row.clearance_tier,
    o.escalation_lead = row.escalation_lead;

LOAD CSV WITH HEADERS FROM 'file:///nodes/services.csv' AS row
MERGE (s:Service {service_id: row.service_id})
SET s.service_name     = row.service_name,
    s.tier             = row.tier,
    s.criticality      = row.criticality,
    s.owner_id         = row.owner_id,
    s.sla_target_pct   = toFloat(row.sla_target_pct),
    s.domain           = row.domain,
    s.lifecycle_status = row.lifecycle_status,
    s.description      = row.description;

LOAD CSV WITH HEADERS FROM 'file:///nodes/apis.csv' AS row
MERGE (a:Api {api_id: row.api_id})
SET a.service_id        = row.service_id,
    a.http_method       = row.http_method,
    a.http_path         = row.http_path,
    a.api_version       = row.api_version,
    a.lifecycle_status  = row.lifecycle_status,
    a.criticality       = row.criticality,
    a.is_public         = (toLower(row.is_public) = 'true'),
    a.latency_budget_ms = toInteger(row.latency_budget_ms),
    a.description       = row.description;

LOAD CSV WITH HEADERS FROM 'file:///nodes/adrs.csv' AS row
MERGE (d:Adr {adr_id: row.adr_id})
SET d.title            = row.title,
    d.lifecycle_status = row.lifecycle_status,
    d.owner_id         = row.owner_id,
    d.decision_date    = row.decision_date,
    d.summary          = row.summary;

LOAD CSV WITH HEADERS FROM 'file:///nodes/requirements.csv' AS row
MERGE (r:Requirement {req_id: row.req_id})
SET r.framework   = row.framework,
    r.title       = row.title,
    r.severity    = row.severity,
    r.owner_id    = row.owner_id,
    r.description = row.description;

// -----------------------------------------------------------------------------
// 3. Stage 2a — Load 174 Explicit M:N Relationships
// -----------------------------------------------------------------------------
LOAD CSV WITH HEADERS FROM 'file:///edges/service_calls_api.csv' AS row
MATCH (s:Service {service_id: row.service_id})
MATCH (a:Api {api_id: row.api_id})
MERGE (s)-[r:CALLS_API]->(a)
SET r.call_mode        = row.call_mode,
    r.is_critical      = (toLower(row.is_critical) = 'true'),
    r.migration_status = row.migration_status,
    r.rps_estimate     = toInteger(row.rps_estimate);

LOAD CSV WITH HEADERS FROM 'file:///edges/service_depends_on_service.csv' AS row
MATCH (src:Service {service_id: row.from_service_id})
MATCH (dst:Service {service_id: row.to_service_id})
MERGE (src)-[r:DEPENDS_ON]->(dst)
SET r.dependency_type = row.dependency_type,
    r.is_sync         = (toLower(row.is_sync) = 'true'),
    r.criticality     = row.criticality;

LOAD CSV WITH HEADERS FROM 'file:///edges/adr_governs_api.csv' AS row
MATCH (d:Adr {adr_id: row.adr_id})
MATCH (a:Api {api_id: row.api_id})
MERGE (d)-[r:GOVERNS]->(a)
SET r.governance_type = row.governance_type,
    r.effective_date  = row.effective_date;

LOAD CSV WITH HEADERS FROM 'file:///edges/adr_supersedes_adr.csv' AS row
MATCH (newer:Adr {adr_id: row.adr_id})
MATCH (older:Adr {adr_id: row.supersedes_adr_id})
MERGE (newer)-[r:SUPERSEDES]->(older)
SET r.superseded_date = row.superseded_date;

LOAD CSV WITH HEADERS FROM 'file:///edges/req_applies_to_api.csv' AS row
MATCH (req:Requirement {req_id: row.req_id})
MATCH (a:Api {api_id: row.api_id})
MERGE (req)-[r:APPLIES_TO_API]->(a)
SET r.compliance_status = row.compliance_status;

LOAD CSV WITH HEADERS FROM 'file:///edges/req_applies_to_service.csv' AS row
MATCH (req:Requirement {req_id: row.req_id})
MATCH (s:Service {service_id: row.service_id})
MERGE (req)-[r:APPLIES_TO_SERVICE]->(s)
SET r.compliance_status = row.compliance_status;

// -----------------------------------------------------------------------------
// 4. Stage 2b — Load 60 Dual-Projected N:1 Relationships (for Neo4j parity;
//    folded into FK columns on node tables in Cloud Spanner Graph)
// -----------------------------------------------------------------------------
LOAD CSV WITH HEADERS FROM 'file:///edges/api_exposes_from_service.csv' AS row
MATCH (s:Service {service_id: row.service_id})
MATCH (a:Api {api_id: row.api_id})
MERGE (s)-[:EXPOSES]->(a);

LOAD CSV WITH HEADERS FROM 'file:///edges/owner_owns_service.csv' AS row
MATCH (o:Owner {owner_id: row.owner_id})
MATCH (s:Service {service_id: row.service_id})
MERGE (o)-[:OWNS_SERVICE]->(s);

LOAD CSV WITH HEADERS FROM 'file:///edges/owner_owns_adr.csv' AS row
MATCH (o:Owner {owner_id: row.owner_id})
MATCH (d:Adr {adr_id: row.adr_id})
MERGE (o)-[:OWNS_ADR]->(d);

LOAD CSV WITH HEADERS FROM 'file:///edges/owner_owns_req.csv' AS row
MATCH (o:Owner {owner_id: row.owner_id})
MATCH (req:Requirement {req_id: row.req_id})
MERGE (o)-[:OWNS_REQ]->(req);

// -----------------------------------------------------------------------------
// 5. Post-Seed Parity Summary (Expected: 68 nodes, 234 relationships)
// -----------------------------------------------------------------------------
MATCH (n)
WITH count(n) AS total_nodes
MATCH ()-[r]->()
RETURN total_nodes, count(r) AS total_relationships;
