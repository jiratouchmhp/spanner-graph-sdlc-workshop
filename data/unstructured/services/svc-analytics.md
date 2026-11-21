# Service Runbook: `svc-analytics` — Revenue & Funnel Analytics

- **Service ID:** `svc-analytics`
- **Owner:** `team-data` (`#data-analytics`, `pd-data-platform@acme-commerce.example`)
- **Tier / Criticality / SLA:** `TIER_2` / `LOW` / `99.5%`
- **Domain:** `analytics` | **Region:** `asia-southeast3` (Bangkok)
- **Exposed APIs:** `api-analytics-ingest-v1` (`ACTIVE`)
- **Applicable Requirements:** `REQ-012`

## Architecture & `DATA_FEED` Isolation

`svc-analytics` aggregates pseudonymized conversion funnels, GMV cohorts, and SKU sell-through dashboards for business stakeholders while maintaining strict failure-domain separation from OLTP commerce services (`REQ-012`).

- **Downstream Asynchronous `DATA_FEED` Sources (`is_sync = false`, `dependency_type = DATA_FEED`):**
  1. `svc-orders` (`api-orders-status-v2` stream) — connects `svc-analytics` at hop 3 in the `api-payments-charge-v2` blast radius strictly as a `DATA_FEED_CONSUMER` (non-blocking reporting lag, NOT an OLTP outage).
  2. `svc-ledger` (`api-ledger-balance-v2` aggregated financial snapshots).
  3. `svc-inventory` (`api-inventory-catalog-v1` SKU catalog availability snapshots).

Note that `svc-analytics` does not depend synchronously on `svc-auth` (`api-auth-token-v2`) during internal batch stream consumption.

## Operational Runbook & Privacy Guardrails

1. **PII Pseudonymization Verification (`REQ-012`, `REQ-007`):** Reject any upstream schema addition containing raw emails, names, or `tok_pci_v2` payment tokens before ingestion into `api-analytics-ingest-v1`.
2. **Change-Impact Triage:** During Tier-0 payment or checkout migrations (such as `api-payments-charge-v2`), `team-data` (`#data-analytics`) validates downstream event schema compatibility without blocking OLTP incident recovery.
