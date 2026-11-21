# API Contract: `api-payments-charge-v2` (`POST /v2/payments/charge`)

- **Exposing Service:** `svc-payments` (`TIER_0`, `CRITICAL`, `99.99%` SLA)
- **Owning Team:** `team-payments` (`#payments-eng`)
- **HTTP Endpoint:** `POST /v2/payments/charge`
- **Status:** `ACTIVE` | **Visibility:** Public (Partner & Gateway Accessible) | **Latency Budget:** `180ms` p99
- **Governing ADRs:** `ADR-004` (`MANDATES`), `ADR-005` (`CONSTRAINS`), `ADR-006` (`CONSTRAINS`), `ADR-008` (`CONSTRAINS`), `ADR-010` (`CONSTRAINS`)
- **Applicable Requirements:** `REQ-001`, `REQ-002`, `REQ-003`, `REQ-005`, `REQ-006`

## Overview & Direct Consumers

`api-payments-charge-v2` (`POST /v2/payments/charge`) is the primary Tier-0 card authorization and capture endpoint of the Acme Commerce Platform. Exactly two direct synchronous consumers invoke this contract:

1. `svc-checkout` (`team-checkout`, `#checkout-orders`) — `1,200` RPS synchronous web/mobile/partner checkout charges.
2. `svc-billing` (`team-billing`, `#billing-ledger`) — `450` RPS synchronous subscription and invoice charges. Note that `svc-billing` also still calls the `DEPRECATED` `api-payments-charge-v1` endpoint (`85` RPS, `AT_RISK`) and is migrating remaining recurring plans to `api-payments-charge-v2`.

Transitive upstream blast radius spans `svc-orders` and `svc-partner-gw` (hop 2), plus `svc-notifications`, `svc-webhooks`, and non-blocking `DATA_FEED` consumer `svc-analytics` (hop 3). Any breaking change is subject to the `180-day` Tier-0 breaking-change freeze under `ADR-010`.

## Authentication, Headers & Request Schema

Callers MUST authenticate over TLS 1.3 mTLS (`ADR-006`, `REQ-006`) with a 5-minute scoped OIDC bearer token issued by `svc-auth` (`api-auth-token-v2`, scope `payments:charge:write`).

- **Required Headers:**
  - `Authorization: Bearer <oidc_jwt>`
  - `Idempotency-Key: <uuid-v4>` (mandatory per `ADR-005` and `REQ-005`; 72-hour deduplication window)
  - `traceparent: <w3c-trace-context>` (`REQ-011`)
- **Request Body (`application/json`):**
  ```json
  {
    "payment_token": "tok_pci_v2_9f8e7d6c5b4a3210fedcba9876543210",
    "amount_minor": 65000,
    "currency": "USD",
    "merchant_account_id": "acct_merchant_1042",
    "order_reference": "ord_2026_0008412",
    "capture_mode": "AUTOMATIC"
  }
  ```
- **Validation Rules:** `payment_token` MUST match `tok_pci_v2_<32hex>` (`ADR-004`, `REQ-001`). Raw PAN payloads are rejected with HTTP `400 Bad Request`.

## Downstream Execution & Fail-Closed Risk Contract

On every request, `svc-payments` executes the following synchronous pipeline within a `180ms` p99 budget (`REQ-002`):

1. **Fraud Risk Gate (`<= 45ms`):** Calls `svc-fraud` via `api-fraud-score-v1` and `api-fraud-rules-v2`. If `amount > $500` and `svc-fraud` exceeds the `45ms` timeout or errors, `POST /v2/payments/charge` MUST **fail-closed** with HTTP `503` (`FRAUD_GATE_TIMEOUT`) per `ADR-004` and `REQ-003`.
2. **Acquirer Authorization:** Submits vaulted token authorization to the acquiring network.
3. **Write-Ahead Double-Entry Ledger (`<= 60ms`):** Posts balanced debit/credit journals to `svc-ledger` via `api-ledger-post-v2` using the same `Idempotency-Key` (`ADR-008`, `REQ-005`).

## Response Codes & Change Freeze

- `200 OK`: Charge captured and ledger posted (`transaction_id`, `ledger_journal_id`, `fraud_decision`).
- `400 Bad Request`: Invalid `tok_pci_v2_<32hex>` token or missing `Idempotency-Key`.
- `409 Conflict`: `Idempotency-Key` reused with a different request body hash.
- `422 Unprocessable Entity`: Declined by fraud rules or issuer.
- `503 Service Unavailable`: `> $500` fail-closed fraud timeout (`> 45ms`) or ledger unavailable.
