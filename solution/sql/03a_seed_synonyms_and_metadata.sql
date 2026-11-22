-- =============================================================================
-- 03a_seed_synonyms_and_metadata.sql
-- Acme Commerce Platform — Custom FTS Dictionary Seed & API JSON Metadata
-- Target: Cloud Spanner Enterprise Edition | Dialect: GOOGLE_STANDARD_SQL
--
-- Notes:
--   - SdlcSynonymDict keys MUST be single lowercase words (unidirectional).
--   - Multi-word values expand as exact phrase queries under rquery dialect.
--   - Apis.metadata populates TOKENIZE_JSON(metadata) for Lab 4 JSON search
--     benchmarks (JSON_CONTAINS, path equality, key existence).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Seed Domain Synonym Dictionary (SdlcSynonymDict)
-- -----------------------------------------------------------------------------
INSERT OR UPDATE INTO SdlcSynonymDict (Key, Value) VALUES
  ('pan',    ['primary account number', 'cardholder data', 'tok_pci_v2', 'tokenized card vault']),
  ('pci',    ['pci-dss', 'cardholder data', 'tokenization', 'tok_pci_v2']),
  ('mtls',   ['mutual tls', 'zero trust', 'oidc', 'service-to-service authentication']),
  ('freeze', ['180-day', 'breaking-change freeze', 'breaking change', 'tier-0 deprecation']),
  ('charge', ['payment charge', 'card capture', 'idempotency-key', 'payments']),
  ('sox',    ['sarbanes-oxley', 'double-entry ledger', 'financial auditability', 'reconciliation']),
  ('cycle',  ['circular dependency', 'mutual dependency', 'async event loop', 'trail']),
  ('pdpa',   ['thailand pdpa', 'data residency', 'asia-southeast3', 'bangkok']),
  ('fraud',  ['fail-closed', 'risk scoring', '45ms', 'high-value']),
  ('sunset', ['deprecated', 'at_risk', 'legacy v1', 'migration']);

-- -----------------------------------------------------------------------------
-- 2. Enrich Key Apis.metadata JSON Documents (Indexed by TOKENIZE_JSON)
-- -----------------------------------------------------------------------------
UPDATE Apis
SET metadata = JSON '''{
  "pci_scope": true,
  "sox_controlled": true,
  "data_residency": "asia-southeast3",
  "token_format": "tok_pci_v2",
  "requires_idempotency_key": true,
  "fraud_gate_budget_ms": 45,
  "fraud_fail_closed_threshold_usd": 500,
  "freeze_window_days": 180,
  "auth_protocol": "mtls_oidc",
  "compliance_frameworks": ["PCI-DSS 4.0", "SOX", "ZERO_TRUST"]
}'''
WHERE api_id = 'api-payments-charge-v2';

UPDATE Apis
SET metadata = JSON '''{
  "pci_scope": true,
  "sox_controlled": true,
  "data_residency": "asia-southeast3",
  "token_format": "legacy_raw_pan",
  "requires_idempotency_key": false,
  "deprecation_risk": "AT_RISK",
  "replacement_api_id": "api-payments-charge-v2",
  "auth_protocol": "mtls_oidc",
  "compliance_frameworks": ["PCI-DSS 4.0", "COMPLIANCE_LEGACY"]
}'''
WHERE api_id = 'api-payments-charge-v1';

UPDATE Apis
SET metadata = JSON '''{
  "pci_scope": false,
  "sox_controlled": false,
  "data_residency": "asia-southeast3",
  "auth_protocol": "mtls_oidc",
  "token_ttl_seconds": 300,
  "is_high_fanin_hub": true,
  "expected_caller_count": 10,
  "compliance_frameworks": ["ZERO_TRUST", "THAILAND_PDPA"]
}'''
WHERE api_id = 'api-auth-token-v2';

UPDATE Apis
SET metadata = JSON '''{
  "pci_scope": true,
  "sox_controlled": false,
  "data_residency": "asia-southeast3",
  "fail_mode": "FAIL_CLOSED",
  "fraud_gate_budget_ms": 45,
  "fraud_fail_closed_threshold_usd": 500,
  "auth_protocol": "mtls_oidc",
  "compliance_frameworks": ["RISK_POLICY", "PCI-DSS 4.0", "SLA"]
}'''
WHERE api_id = 'api-fraud-score-v1';

UPDATE Apis
SET metadata = JSON '''{
  "pci_scope": false,
  "sox_controlled": true,
  "data_residency": "asia-southeast3",
  "ledger_model": "DOUBLE_ENTRY_WRITE_AHEAD",
  "requires_idempotency_key": true,
  "dr_backup_region": "asia-southeast1",
  "auth_protocol": "mtls_oidc",
  "compliance_frameworks": ["SOX", "DR_RPO_RTO", "THAILAND_PDPA"]
}'''
WHERE api_id = 'api-ledger-post-v2';

UPDATE Apis
SET metadata = JSON '''{
  "pci_scope": true,
  "sox_controlled": false,
  "data_residency": "asia-southeast3",
  "saga_pattern": "COMPENSATING_CHECKOUT",
  "requires_idempotency_key": true,
  "auth_protocol": "mtls_oidc",
  "compliance_frameworks": ["RESILIENCE", "ZERO_TRUST"]
}'''
WHERE api_id = 'api-checkout-submit-v1';

UPDATE Apis
SET metadata = JSON '''{
  "pci_scope": false,
  "sox_controlled": false,
  "data_residency": "asia-southeast3",
  "async_event_bus": true,
  "has_bidirectional_notification_cycle": true,
  "auth_protocol": "mtls_oidc",
  "compliance_frameworks": ["RESILIENCE", "OBSERVABILITY"]
}'''
WHERE api_id = 'api-orders-create-v2';
