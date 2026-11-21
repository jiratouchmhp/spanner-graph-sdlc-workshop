# Service Runbook: `svc-inventory` — Stock & Catalog Reservation Service

- **Service ID:** `svc-inventory`
- **Owner:** `team-catalog` (`#catalog-inventory`, `pd-catalog-ops@acme-commerce.example`)
- **Tier / Criticality / SLA:** `TIER_1` / `HIGH` / `99.95%`
- **Domain:** `merchandising` | **Region:** `asia-southeast3` (Bangkok)
- **Exposed APIs:** `api-inventory-reserve-v1` (`ACTIVE`), `api-inventory-catalog-v1` (`ACTIVE`)
- **Applicable Requirements:** `REQ-009`

## Architecture & Dependencies

`svc-inventory` manages authoritative SKU catalog data and atomic 15-minute soft-lock stock reservations (`ADR-009`) to prevent overselling during high-concurrency checkout bursts.

- **Upstream Direct Callers:**
  - `svc-checkout` (`1,320` RPS on `api-inventory-reserve-v1`, `2,200` RPS on `api-inventory-catalog-v1`).
  - `svc-orders` (`890` RPS on `api-inventory-reserve-v1`, `1,150` RPS on `api-inventory-catalog-v1`).
  - `svc-analytics` (`140` RPS async `DATA_FEED` reads on `api-inventory-catalog-v1`, `REQ-012`).
- **Downstream Synchronous Dependencies:** `svc-auth` (`api-auth-token-v2`, `ADR-006`).

## Operational Runbook & Soft-Lock TTL Recovery

1. **Saga Release Backlogs (`REQ-009`, `ADR-009`):** If payment declines surge on `api-payments-charge-v2`, verify `svc-checkout` compensation releases return stock immediately to `api-inventory-reserve-v1`. Abandoned holds automatically expire via the 15-minute Cloud Spanner TTL policy.
2. **Flash-Sale SKU Contention:** Use read-only stale reads (15s bounded staleness) on `api-inventory-catalog-v1` (`50ms` budget) to absorb catalog browsing surges without competing against strong read-write reservation locks on `api-inventory-reserve-v1`.
