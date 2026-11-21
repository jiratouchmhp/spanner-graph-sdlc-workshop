# Service Runbook: `svc-fraud` — Real-Time Fraud Scoring Service

- **Service ID:** `svc-fraud`
- **Owner:** `team-risk` (`#risk-fraud`, `pd-risk-realtime@acme-commerce.example`)
- **Tier / Criticality / SLA:** `TIER_0` / `CRITICAL` / `99.99%`
- **Domain:** `risk` | **Region:** `asia-southeast3` (Bangkok)
- **Exposed APIs:** `api-fraud-score-v1` (`ACTIVE`), `api-fraud-rules-v2` (`ACTIVE`)
- **Applicable Requirements:** `REQ-002`, `REQ-003`

## Architecture & Dependencies

`svc-fraud` executes low-latency machine-learning risk scoring (`api-fraud-score-v1`, `45ms` p99 budget) and deterministic velocity/BIN rule evaluation (`api-fraud-rules-v2`, `35ms` p99 budget) on every payment authorization and checkout submission.

- **Upstream Direct Callers:**
  - `svc-payments` (`1,650` RPS on `api-fraud-score-v1` and `1,650` RPS on `api-fraud-rules-v2`) during `api-payments-charge-v2`.
  - `svc-checkout` (`1,250` RPS on `api-fraud-score-v1` and `420` RPS on `api-fraud-rules-v2`) during `api-checkout-submit-v1`.
- **Downstream Synchronous Dependencies:** `svc-auth` (`api-auth-token-v2`, `api-auth-jwks-v2`, `ADR-006`).

## Operational Runbook & 45ms Fail-Closed Posture

1. **Latency Breach > `45ms` (`REQ-002`, `ADR-004`):** Because `svc-payments` enforces a hard `45ms` RPC timeout and **fail-closed** declines for transactions `> $500` (`REQ-003`), any tail-latency spike in `api-fraud-score-v1` directly lowers high-value GMV conversion. Immediately roll back recent model deployments or shed heavy feature lookups.
2. **Degraded Fallback (`<= $500` Only):** Transactions `<= $500` fall back to `api-fraud-rules-v2` deterministic checks while logging async review tasks for `team-risk` (`#risk-fraud`). Never authorize `> $500` blind fallbacks without security and risk executive sign-off.
