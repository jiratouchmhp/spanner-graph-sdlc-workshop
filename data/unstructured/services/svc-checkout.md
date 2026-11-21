# Service Runbook: `svc-checkout` — Checkout Orchestrator

- **Service ID:** `svc-checkout`
- **Owner:** `team-checkout` (`#checkout-orders`, `pd-checkout-tier0@acme-commerce.example`)
- **Tier / Criticality / SLA:** `TIER_0` / `CRITICAL` / `99.99%`
- **Domain:** `checkout` | **Region:** `asia-southeast3` (Bangkok)
- **Exposed APIs:** `api-checkout-submit-v1` (`ACTIVE`), `api-checkout-quote-v1` (`ACTIVE`)
- **Applicable Requirements:** `REQ-001`, `REQ-008`, `REQ-009`

## Architecture & Dependencies

`svc-checkout` orchestrates cart pricing, SKU reservation verification, fraud pre-scoring, and tokenized payment execution across web, mobile, and B2B partner channels.

- **Upstream Direct Callers:** `svc-orders` (`api-checkout-submit-v1`, `api-checkout-quote-v1`) and `svc-partner-gw` (`api-checkout-submit-v1`, `api-checkout-quote-v1`).
- **Downstream Synchronous Dependencies:**
  - `svc-payments` (`api-payments-charge-v2` at `1,200` RPS — primary direct synchronous caller; `api-payments-refund-v2` at `110` RPS).
  - `svc-inventory` (`api-inventory-reserve-v1`, `api-inventory-catalog-v1`) for 15-minute soft-lock holds (`ADR-009`).
  - `svc-fraud` (`api-fraud-score-v1`, `api-fraud-rules-v2`) for pre-auth risk screening (`ADR-004`, `REQ-003`).
  - `svc-auth` (`api-auth-token-v2`, `api-auth-jwks-v2`) for zero-trust mTLS & OIDC (`ADR-006`, `REQ-006`).

## Operational Runbook & Saga Recovery

1. **Payment Charge Failures:** If `api-payments-charge-v2` returns HTTP `422` or `503` (including `> $500` fail-closed `45ms` fraud declines), `svc-checkout` executes saga compensation (`REQ-009`) to immediately release `svc-inventory` reservations.
2. **Idempotency Replay (`ADR-005`):** Always forward the caller's `Idempotency-Key` unchanged to `api-payments-charge-v2` so checkout retries never double-charge tokenized cards (`tok_pci_v2_<32hex>`, `REQ-001`).
3. **Change Freeze Governance:** Because `svc-checkout` is `TIER_0`, breaking changes to `api-checkout-submit-v1` require `180-day` notice under `ADR-010` and `REQ-008`.
