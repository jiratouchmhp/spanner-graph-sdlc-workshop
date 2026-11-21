# Service Runbook: `svc-ledger` — Double-Entry Financial Ledger

- **Service ID:** `svc-ledger`
- **Owner:** `team-billing` (`#billing-ledger`, `pd-billing-sox@acme-commerce.example`, `RESTRICTED_SOX`)
- **Tier / Criticality / SLA:** `TIER_0` / `CRITICAL` / `99.99%`
- **Domain:** `finance` | **Region:** `asia-southeast3` (Bangkok) + DR Backup Copy in `asia-southeast1` (Singapore)
- **Exposed APIs:** `api-ledger-post-v2` (`ACTIVE`), `api-ledger-balance-v2` (`ACTIVE`)
- **Applicable Requirements:** `REQ-005`, `REQ-006`, `REQ-007`, `REQ-014`

## Architecture & Dependencies

`svc-ledger` is the SOX-regulated immutable double-entry system of record backed by `regional-asia-southeast3` Cloud Spanner Enterprise (`ADR-008`, `REQ-005`, `REQ-007`).

- **Upstream Direct Callers:**
  - `svc-payments` (`1,750` RPS on `api-ledger-post-v2`, `480` RPS on `api-ledger-balance-v2`) during `api-payments-charge-v2` and `api-payments-refund-v2`.
  - `svc-billing` (`620` RPS on `api-ledger-post-v2`, `540` RPS on `api-ledger-balance-v2`) during invoicing and reconciliation.
  - `svc-analytics` (`65` RPS async `DATA_FEED` reads on `api-ledger-balance-v2`, `REQ-012`).
- **Downstream Synchronous Dependencies:** `svc-auth` (`api-auth-token-v2`, `api-auth-jwks-v2`, `ADR-006`, `REQ-006`).

## Operational Runbook & SOX / DR Controls

1. **Hotspot & Lock Prevention (`60ms` Budget):** Monitor Cloud Spanner Key Visualizer and `SPANNER_SYS.LOCK_STATS_TOP_HOUR` for hot merchant settlement accounts; apply split points and sharded sub-ledger buckets before major holiday sales.
2. **Append-Only Immutability (`REQ-005`, `ADR-008`):** Direct `UPDATE` or `DELETE` DML against posted journals is strictly forbidden; always emit idempotent reversing journals via `api-ledger-post-v2`.
3. **Cross-Region DR Verification (`REQ-014`):** Verify daily encrypted Cloud Spanner backup copies from `asia-southeast3` to `asia-southeast1` succeed without lag.
