# API Contract: `api-fraud-rules-v2` (`POST /v2/fraud/rules/evaluate`)

- **Exposing Service:** `svc-fraud` (`TIER_0`, `CRITICAL`, `99.99%` SLA)
- **Owning Team:** `team-risk` (`#risk-fraud`)
- **HTTP Endpoint:** `POST /v2/fraud/rules/evaluate`
- **Status:** `ACTIVE` | **Visibility:** Internal Only (`is_public = false`) | **Latency Budget:** `35ms` p99
- **Governing ADRs:** `ADR-004` (`MANDATES`)
- **Applicable Requirements:** `REQ-002`, `REQ-003`

## Overview & Rule Engine Semantics

`api-fraud-rules-v2` evaluates deterministic compliance and velocity controls alongside ML risk scores from `api-fraud-score-v1`. Rules include per-token card velocity caps, high-risk BIN blocklists, geo-IP mismatch flags, and sanctions list screening.

- **Direct Callers:**
  - `svc-payments` (`1,650` RPS, `SYNC`, `is_critical = true`) during `api-payments-charge-v2`.
  - `svc-checkout` (`420` RPS, `SYNC`, `is_critical = false`) for high-value basket verification.

## Degradation & Fallback Behavior

Under `ADR-004` and `REQ-003`, if `api-fraud-score-v1` times out past `45ms` on transactions `<= $500`, `svc-payments` may rely on `api-fraud-rules-v2` deterministic rules (budget `35ms`) as a controlled degraded gate while emitting an async risk audit alert to `#risk-fraud`. For charges `> $500`, deterministic fallback is prohibited and transactions fail closed.
