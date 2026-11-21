-- =============================================================================
-- 04b_fgac_grants_rerunnable.sql
-- Acme Commerce Platform — Idempotent Re-Runnable FGAC Grants Script
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
--
-- WHY THIS FILE EXISTS (Critical Cloud Spanner Operational Rule):
--   Executing `CREATE OR REPLACE VIEW` drops and recreates the view metadata,
--   SILENTLY WIPING all prior `GRANT SELECT ON VIEW ... TO ROLE ...` grants.
--   Re-run this standalone script after every view DDL change or restore.
--
-- Guardrails:
--   - Exact-case table/view names are mandatory in Spanner GRANT DDL.
--   - NEVER grant base tables to `public` or grant `spanner_secure_context_reader`
--     to agent roles.
--   - Uncomment the `GRANT EXECUTE ON MODEL SdlcTextEmbedding` statements below
--     once 03b_remote_models.sql.tmpl has been applied to the database.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. sdlc_readonly_role (Impact Analyst — Read-Only, Column-Scoped Owners)
-- -----------------------------------------------------------------------------
GRANT SELECT (owner_id, team_name, org_unit, slack_channel, email)
  ON TABLE Owners TO ROLE sdlc_readonly_role;
GRANT SELECT ON TABLE Services TO ROLE sdlc_readonly_role;
GRANT SELECT ON TABLE Apis TO ROLE sdlc_readonly_role;
GRANT SELECT ON TABLE Adrs TO ROLE sdlc_readonly_role;
GRANT SELECT ON TABLE Requirements TO ROLE sdlc_readonly_role;
GRANT SELECT ON TABLE DocumentChunks TO ROLE sdlc_readonly_role;
GRANT SELECT ON TABLE ServiceCallsApi TO ROLE sdlc_readonly_role;
GRANT SELECT ON TABLE ServiceDependsOnService TO ROLE sdlc_readonly_role;
GRANT SELECT ON TABLE AdrGovernsApi TO ROLE sdlc_readonly_role;
GRANT SELECT ON TABLE AdrSupersedesAdr TO ROLE sdlc_readonly_role;
GRANT SELECT ON TABLE ReqAppliesToApi TO ROLE sdlc_readonly_role;
GRANT SELECT ON TABLE ReqAppliesToService TO ROLE sdlc_readonly_role;
GRANT SELECT ON TABLE ChunkEntityMentions TO ROLE sdlc_readonly_role;
GRANT SELECT ON TABLE SdlcSynonymDict TO ROLE sdlc_readonly_role;

GRANT SELECT ON VIEW ChunkCitesApiView TO ROLE sdlc_readonly_role;
GRANT SELECT ON VIEW ChunkCitesServiceView TO ROLE sdlc_readonly_role;
GRANT SELECT ON VIEW ChunkCitesAdrView TO ROLE sdlc_readonly_role;
GRANT SELECT ON VIEW ChunkCitesReqView TO ROLE sdlc_readonly_role;
GRANT SELECT ON VIEW PublicApiCatalogView TO ROLE sdlc_readonly_role;
GRANT SELECT ON VIEW TeamScopedAssetsView TO ROLE sdlc_readonly_role;

-- -----------------------------------------------------------------------------
-- 2. sdlc_adr_writer_role (ADR Curator — Scoped ADR Insert/Update + Audit Append)
-- -----------------------------------------------------------------------------
GRANT ROLE sdlc_readonly_role TO ROLE sdlc_adr_writer_role;
GRANT INSERT, UPDATE ON TABLE Adrs TO ROLE sdlc_adr_writer_role;
GRANT INSERT, UPDATE ON TABLE AdrGovernsApi TO ROLE sdlc_adr_writer_role;
GRANT INSERT ON TABLE AgentQueryAudit TO ROLE sdlc_adr_writer_role;

-- -----------------------------------------------------------------------------
-- 3. sdlc_security_auditor_role (Security Auditor — Read-Only Compliance & Audit)
-- -----------------------------------------------------------------------------
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
GRANT SELECT ON VIEW PublicApiCatalogView TO ROLE sdlc_security_auditor_role;
GRANT SELECT ON VIEW SecurityComplianceImpactView TO ROLE sdlc_security_auditor_role;

-- -----------------------------------------------------------------------------
-- 4. Optional ML Model Execution Grants (apply after 03b_remote_models.sql.tmpl)
-- -----------------------------------------------------------------------------
-- GRANT EXECUTE ON MODEL SdlcTextEmbedding TO ROLE sdlc_readonly_role;
-- GRANT EXECUTE ON MODEL SdlcTextEmbedding TO ROLE sdlc_security_auditor_role;
