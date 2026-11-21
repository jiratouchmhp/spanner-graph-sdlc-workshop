# API Contract: `api-fraud-score-v1` (`POST /v1/fraud/score`)

- **Exposing Service:** `svc-fraud` (`TIER_0`, `CRITICAL`, `99.99%` SLA)
- **Owning Team:** `team-risk` (`#risk-fraud`)
- **HTTP Endpoint:** `POST /v1/fraud/score`
- **Status:** `ACTIVE` | **Visibility:** Internal Only (`is_public = false`) | **Latency Budget:** `45ms` p99
- **Governing ADRs:** `ADR-004` (`MANDATES`), `ADR-006` (`CONSTRAINS`)
- **Applicable Requirements:** `REQ-002`, `REQ-003`, `REQ-006`

## Overview & Callers

`api-fraud-score-v1` executes real-time gradient-boosted fraud risk inference for payment authorizations and checkout pre-screening.

- **Direct Callers:**
  - `svc-payments` (`1,650` RPS, `SYNC`, `is_critical = true`) — invoked synchronously inside `api-payments-charge-v2`.
  - `svc-checkout` (`1,250` RPS, `SYNC`, `is_critical = true`) — invoked during pre-authorization cart screening in `api-checkout-submit-v1`.

## Request / Response Contract & 45ms Timeout Budget

Authenticated via mTLS and `svc-auth` OIDC bearer token (`api-auth-token-v2`, `REQ-006`). Callers pass tokenized card fingerprint hashes (`tok_pci_v2_<32hex>`), device telemetry, merchant category, and USD-equivalent amount.

- **Hard SLA (`REQ-002`):** `svc-fraud` MUST respond within `45ms` p99. `svc-payments` cancels the RPC at `45ms` to preserve the `180ms` end-to-end payment budget.
- **Fail-Closed Gate (`REQ-003`, `ADR-004`):** Returns `risk_score` (`0.00–1.00`), `decision` (`APPROVE`, `CHALLENGE`, `DECLINE`), and `model_version`. For any charge `> $500`, a timeout or `5xx` error triggers a mandatory fail-closed decline in `svc-payments`.
