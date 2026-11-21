-- =============================================================================
-- 04_fgac_roles_views.sql (STARTER — Lab 6)
-- Acme Commerce Platform — Fine-Grained Access Control (FGAC) Roles & Views
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
--
-- Antigravity Skills for Lab 6:
--   - Primary: .agents/skills/spanner-mcp-toolbox-and-agent-security/SKILL.md
--   - Support: .agents/skills/spanner-schema-and-data-modeling/SKILL.md
--
-- Two-Layer Authorization Architecture:
--   Layer 1 (Cloud Run MCP Toolbox): Uses dedicated SAs with least-privilege
--            DB IAM (roles/spanner.databaseReader / databaseUser) + sliced
--            per-persona tools.<persona>.yaml + OIDC authServices (@caller_email).
--   Layer 2 (FGAC-Capable Clients — Spanner Remote MCP, SDKs, ADK SpannerToolset,
--            gcloud --database-role): Uses dedicated sa-fgac-* SAs holding ONLY
--            roles/spanner.fineGrainedAccessUser + conditional
--            roles/spanner.databaseRoleUser bound to the 3 SQL roles below.
--
-- Security Invariants:
--   - INV-05: Every SQL view declares explicit SQL SECURITY INVOKER or DEFINER.
--   - Explicit CAST(...) on every non-graph view output column for DDL stability.
--   - ZERO privileges granted to `public` or `spanner_secure_context_reader`.
--   - IMPORTANT: Running CREATE OR REPLACE VIEW wipes prior GRANT SELECT ON
--     VIEW bindings; re-apply grants via solution/sql/04b_fgac_grants_rerunnable.sql.
--
-- Verify when done:
--   make TRACK=starter verify-lab LAB=6
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Create the 3 Persona Database Roles (Least-Privilege RBAC)
-- -----------------------------------------------------------------------------
CREATE ROLE sdlc_readonly_role;
CREATE ROLE sdlc_adr_writer_role;
CREATE ROLE sdlc_security_auditor_role;

-- -----------------------------------------------------------------------------
-- 2. PublicApiCatalogView (SQL SECURITY INVOKER)
-- -----------------------------------------------------------------------------

-- TODO(Lab 6): Author `PublicApiCatalogView` with explicit `SQL SECURITY INVOKER`
--   and explicit `CAST(...)` on every projected column (INV-05).
--   Requirements:
--     - Join `Apis AS a`, `Services AS s` (`a.service_id = s.service_id`), and
--       `Owners AS o` (`s.owner_id = o.owner_id`).
--     - Expose ONLY public APIs (`WHERE a.is_public = TRUE`).
--     - Project non-sensitive catalog columns:
--         api_id, http_method, http_path, api_version, lifecycle_status,
--         api_criticality (from a.criticality), latency_budget_ms (INT64),
--         api_description (from a.description), service_id, service_name,
--         service_tier (from s.tier), sla_target_pct (FLOAT64),
--         owner_id, team_name, slack_channel.
--     - Hide private internal APIs as well as sensitive `Owners` columns
--       (`oncall_pager`, `clearance_tier`, `escalation_lead`).

-- -----------------------------------------------------------------------------
-- 3. SecurityComplianceImpactView (SQL SECURITY INVOKER) — PROVIDED COMPLETE
--    Dedicated audit projection for sdlc_security_auditor_role surfacing
--    PCI-DSS, SOX, Zero-Trust, Risk Policy, Thailand PDPA, and Legacy AT_RISK
--    API exposure alongside owner clearance tiers and governance counts.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW SecurityComplianceImpactView
SQL SECURITY INVOKER AS
SELECT
  CAST(a.api_id AS STRING) AS api_id,
  CAST(a.http_method AS STRING) AS http_method,
  CAST(a.http_path AS STRING) AS http_path,
  CAST(a.lifecycle_status AS STRING) AS lifecycle_status,
  CAST(a.criticality AS STRING) AS api_criticality,
  CAST(a.is_public AS BOOL) AS is_public,
  CAST(s.service_id AS STRING) AS service_id,
  CAST(s.service_name AS STRING) AS service_name,
  CAST(s.tier AS STRING) AS service_tier,
  CAST(o.owner_id AS STRING) AS owner_id,
  CAST(o.team_name AS STRING) AS team_name,
  CAST(o.slack_channel AS STRING) AS slack_channel,
  CAST(o.clearance_tier AS STRING) AS clearance_tier,
  CAST(
    (
      SELECT COUNT(*)
      FROM AdrGovernsApi AS g
      WHERE g.api_id = a.api_id
    ) AS INT64
  ) AS governing_adr_count,
  CAST(
    (
      SELECT COUNT(*)
      FROM ReqAppliesToApi AS ra
      WHERE ra.api_id = a.api_id
    ) AS INT64
  ) AS applicable_req_count,
  CAST(
    (
      SELECT COUNT(*)
      FROM ServiceCallsApi AS c
      WHERE c.api_id = a.api_id
        AND c.migration_status = 'AT_RISK'
    ) AS INT64
  ) AS at_risk_caller_count,
  CAST(
    EXISTS(
      SELECT 1
      FROM ReqAppliesToApi AS ra
      WHERE ra.api_id = a.api_id
        AND ra.compliance_status = 'AT_RISK'
    ) AS BOOL
  ) AS has_at_risk_requirement
FROM Apis AS a
JOIN Services AS s ON a.service_id = s.service_id
JOIN Owners AS o ON s.owner_id = o.owner_id
WHERE o.clearance_tier IN ('RESTRICTED_PCI', 'RESTRICTED_SOX', 'RESTRICTED_SEC')
   OR a.lifecycle_status = 'DEPRECATED'
   OR EXISTS(
     SELECT 1
     FROM ReqAppliesToApi AS ra
     JOIN Requirements AS r ON ra.req_id = r.req_id
     WHERE ra.api_id = a.api_id
       AND r.framework IN (
         'PCI-DSS 4.0', 'SOX', 'ZERO_TRUST',
         'RISK_POLICY', 'COMPLIANCE_LEGACY', 'THAILAND_PDPA', 'SECURITY'
       )
   );

-- -----------------------------------------------------------------------------
-- 4. TeamScopedAssetsView (SQL SECURITY DEFINER + SECURE_CONTEXT) — PROVIDED
--    Parameterized row-level security view for FGAC-capable SDK / agent apps
--    that attach out-of-band secure_context['caller_email'] on the Spanner RPC.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW TeamScopedAssetsView
SQL SECURITY DEFINER AS
SELECT
  CAST(o.owner_id AS STRING) AS owner_id,
  CAST(o.team_name AS STRING) AS team_name,
  CAST(o.slack_channel AS STRING) AS slack_channel,
  CAST(o.email AS STRING) AS owner_email,
  CAST(s.service_id AS STRING) AS service_id,
  CAST(s.service_name AS STRING) AS service_name,
  CAST(s.tier AS STRING) AS tier,
  CAST(s.criticality AS STRING) AS criticality,
  CAST(s.sla_target_pct AS FLOAT64) AS sla_target_pct,
  CAST(s.domain AS STRING) AS domain,
  CAST(s.lifecycle_status AS STRING) AS lifecycle_status
FROM Owners AS o
JOIN Services AS s ON s.owner_id = o.owner_id
WHERE o.email = CAST(SECURE_CONTEXT('caller_email') AS STRING);

-- -----------------------------------------------------------------------------
-- 5. Least-Privilege FGAC Grants
-- -----------------------------------------------------------------------------

-- 5.1 sdlc_readonly_role
-- TODO(Lab 6): Write least-privilege read-only GRANT statements for
--   `sdlc_readonly_role`:
--     1. Column-level SELECT on `Owners`: allow ONLY
--        `(owner_id, team_name, org_unit, slack_channel, email)` so
--        `oncall_pager`, `clearance_tier`, and `escalation_lead` stay hidden!
--     2. Table-level SELECT on Path A & Path B read tables:
--        `Services`, `Apis`, `Adrs`, `Requirements`, `DocumentChunks`,
--        `ServiceCallsApi`, `ServiceDependsOnService`, `AdrGovernsApi`,
--        `AdrSupersedesAdr`, `ReqAppliesToApi`, `ReqAppliesToService`,
--        `ChunkEntityMentions`, `SdlcSynonymDict`
--        (DO NOT grant `AgentQueryAudit` or any INSERT/UPDATE/DELETE privileges!)
--     3. View-level SELECT on:
--        `ChunkCitesApiView`, `ChunkCitesServiceView`, `ChunkCitesAdrView`,
--        `ChunkCitesReqView`, `PublicApiCatalogView`, `TeamScopedAssetsView`
--        (DO NOT grant `SecurityComplianceImpactView` to the readonly role!).

-- 5.2 sdlc_adr_writer_role: Inherits read-only base + scoped ADR authoring
--     and audit log append ONLY (no DELETE, no writes to Services/Apis/Owners).
GRANT ROLE sdlc_readonly_role TO ROLE sdlc_adr_writer_role;
GRANT INSERT, UPDATE ON TABLE Adrs TO ROLE sdlc_adr_writer_role;
GRANT INSERT, UPDATE ON TABLE AdrGovernsApi TO ROLE sdlc_adr_writer_role;
GRANT INSERT ON TABLE AgentQueryAudit TO ROLE sdlc_adr_writer_role;

-- 5.3 sdlc_security_auditor_role: Read-only compliance & audit visibility
--     (full Owners columns including clearance_tier + AgentQueryAudit +
--     SecurityComplianceImpactView; ZERO write privileges).
GRANT SELECT ON TABLE Owners TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE Services TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE Apis TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE Adrs TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE Requirements TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE DocumentChunks TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE ServiceCallsApi TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE ServiceDependsOnService TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE AdrGovernsApi TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE AdrSupersedesAdr TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE ReqAppliesToApi TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE ReqAppliesToService TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE ChunkEntityMentions TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE SdlcSynonymDict TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON TABLE AgentQueryAudit TO ROLE sdlc_security_auditor_role;

GRANT SELECT ON VIEW ChunkCitesApiView TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON VIEW ChunkCitesServiceView TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON VIEW ChunkCitesAdrView TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON VIEW ChunkCitesReqView TO ROLE sdlc_security_auditor_role;
-- TODO(Lab 6): Once you create `PublicApiCatalogView` in Section 2, also grant
--   SELECT on it to `sdlc_security_auditor_role` (in addition to `sdlc_readonly_role`).
GRANT SELECT ON VIEW SecurityComplianceImpactView TO ROLE sdlc_security_auditor_role;
