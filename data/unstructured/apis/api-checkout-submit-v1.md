# API Contract: `api-checkout-submit-v1` (`POST /v1/checkout/submit`)

- **Exposing Service:** `svc-checkout` (`TIER_0`, `CRITICAL`, `99.99%` SLA)
- **Owning Team:** `team-checkout` (`#checkout-orders`)
- **HTTP Endpoint:** `POST /v1/checkout/submit`
- **Status:** `ACTIVE` | **Visibility:** Public (`is_public = true`) | **Latency Budget:** `350ms` p99
- **Governing ADRs:** `ADR-004` (`CONSTRAINS`), `ADR-005` (`MANDATES`), `ADR-006` (`CONSTRAINS`), `ADR-010` (`CONSTRAINS`)
- **Applicable Requirements:** `REQ-001`, `REQ-003`, `REQ-007`, `REQ-008`

## Overview & Direct Callers

`api-checkout-submit-v1` is the Tier-0 checkout submission API coordinating inventory locks, fraud pre-checks, and tokenized payment capture.

- **Direct Callers:**
  - `svc-orders` (`980` RPS, `SYNC`, `is_critical = true`) — direct web and mobile consumer checkout submissions.
  - `svc-partner-gw` (`340` RPS, `SYNC`, `is_critical = true`) — B2B partner-initiated checkouts routed from `api-partner-checkout-v1`.

## Orchestration Saga & Downstream Dependencies

Within its `350ms` p99 latency budget, `api-checkout-submit-v1` executes a deterministic saga (`REQ-009`):

1. Validates mTLS + OIDC token via `svc-auth` (`api-auth-token-v2`, `REQ-006`).
2. Confirms 15-minute SKU soft-locks on `svc-inventory` (`api-inventory-reserve-v1`, `ADR-009`).
3. Runs pre-charge risk evaluation on `svc-fraud` (`api-fraud-score-v1`, `api-fraud-rules-v2`, `REQ-003`).
4. Submits tokenized card payment (`tok_pci_v2_<32hex>`, `REQ-001`, `ADR-004`) with forwarded `Idempotency-Key` (`ADR-005`) to `svc-payments` (`api-payments-charge-v2`).
5. On decline or timeout (such as a `> $500` fail-closed `45ms` fraud gate rejection), releases the `svc-inventory` soft-lock and returns a structured error without charging the customer.

Because `api-checkout-submit-v1` is a `Tier-0` API, any breaking contract change is governed by the `180-day` freeze policy (`ADR-010`, `REQ-008`).
