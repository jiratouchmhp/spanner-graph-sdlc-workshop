# Service Runbook: `svc-payments` — Payments Core Service

- **Service ID:** `svc-payments`
- **Owner:** `team-payments` (`#payments-eng`, `pd-payments-tier0@acme-commerce.example`)
- **Tier / Criticality / SLA:** `TIER_0` / `CRITICAL` / `99.99%`
- **Domain:** `payments` | **Region:** `asia-southeast3` (Bangkok)
- **Exposed APIs:** `api-payments-charge-v2` (`ACTIVE`), `api-payments-charge-v1` (`DEPRECATED`), `api-payments-refund-v2` (`ACTIVE`)
- **Applicable Requirements:** `REQ-001`, `REQ-002`, `REQ-003`, `REQ-004`, `REQ-006`, `REQ-007`, `REQ-008`

## Architecture & Dependencies

`svc-payments` is the Tier-0 PCI-DSS 4.0 payment authorization, capture, and refund engine of the Acme Commerce Platform.

- **Upstream Direct Callers:** `svc-checkout` (`1,200` RPS on `api-payments-charge-v2`, `110` RPS on `api-payments-refund-v2`) and `svc-billing` (`450` RPS on `api-payments-charge-v2`, `95` RPS on `api-payments-refund-v2`, plus `85` RPS `AT_RISK` legacy calls to `api-payments-charge-v1`).
- **Downstream Synchronous Dependencies:**
  - `svc-auth` (`api-auth-token-v2`, `api-auth-jwks-v2`) for zero-trust mTLS & OIDC validation (`ADR-006`, `REQ-006`).
  - `svc-fraud` (`api-fraud-score-v1`, `api-fraud-rules-v2`) within a strict `45ms` timeout budget (`ADR-004`, `REQ-002`, `REQ-003`).
  - `svc-ledger` (`api-ledger-post-v2`, `api-ledger-balance-v2`) for synchronous double-entry posting (`ADR-008`, `REQ-005`).

## Operational Runbook & Incident Triage

1. **High `503 FRAUD_GATE_TIMEOUT` Rate:** Check `svc-fraud` p99 latency on `api-fraud-score-v1`. Per `ADR-004` and `REQ-003`, charges `> $500` intentionally **fail-closed** whenever fraud latency breaches `45ms`. Do NOT disable the fail-closed gate without dual approval from `team-risk` (`#risk-fraud`) and `team-security` (`#security-iam`).
2. **End-to-End p99 > `180ms` (`REQ-002`):** Inspect Cloud Spanner `SPANNER_SYS.QUERY_STATS_TOP_HOUR` and `SPANNER_SYS.LOCK_STATS_TOP_HOUR` filtered by `svc-payments` request tags (`REQ-011`) and verify `svc-ledger` (`api-ledger-post-v2`) stays below `60ms`.
3. **Legacy v1 Traffic Alerts (`REQ-004`):** Coordinate with `team-billing` (`#billing-ledger`) to track remaining `svc-billing` calls to `api-payments-charge-v1`. Never alter `api-payments-charge-v2` contracts outside the `180-day` `Tier-0` freeze policy (`ADR-010`, `REQ-008`).
