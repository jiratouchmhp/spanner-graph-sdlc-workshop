# API Contract: `api-ledger-post-v2` (`POST /v2/ledger/entries`)

- **Exposing Service:** `svc-ledger` (`TIER_0`, `CRITICAL`, `99.99%` SLA)
- **Owning Team:** `team-billing` (`#billing-ledger`)
- **HTTP Endpoint:** `POST /v2/ledger/entries`
- **Status:** `ACTIVE` | **Visibility:** Internal Only (`is_public = false`) | **Latency Budget:** `60ms` p99
- **Governing ADRs:** `ADR-005` (`MANDATES`), `ADR-006` (`CONSTRAINS`), `ADR-008` (`MANDATES`)
- **Applicable Requirements:** `REQ-005`, `REQ-006`

## Overview & Direct Callers

`api-ledger-post-v2` commits immutable double-entry financial journals directly into Bangkok (`asia-southeast3`) Cloud Spanner (`ADR-008`, `REQ-007`), replacing the legacy nightly batch settlement process (`ADR-002`).

- **Direct Callers:**
  - `svc-payments` (`1,750` RPS, `SYNC`, `is_critical = true`) — posts charge and refund journals inside `api-payments-charge-v2` and `api-payments-refund-v2`.
  - `svc-billing` (`620` RPS, `SYNC`, `is_critical = true`) — posts subscription invoice recognition and adjustment entries.

## SOX Double-Entry & Idempotency Contract

- **Balanced Journal Invariant (`REQ-005`):** Every request MUST contain at least two journal legs where `SUM(debit_minor) == SUM(credit_minor)` within the same currency. Unbalanced payloads fail with HTTP `400 Bad Request`.
- **Mandatory `Idempotency-Key` (`ADR-005`):** Stored atomically in the ledger transaction so retried commits from `svc-payments` or `svc-billing` guarantee 1:1 financial parity with zero duplicate postings.
- **Zero-Trust Auth (`REQ-006`):** Enforces mTLS and `api-auth-token-v2` OIDC scope `ledger:entries:post`.
- **Disaster Recovery (`REQ-014`):** Underlying Cloud Spanner data is continuously protected by automated cross-region backup copies to Singapore (`asia-southeast1`).
