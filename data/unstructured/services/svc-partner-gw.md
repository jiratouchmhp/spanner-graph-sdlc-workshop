# Service Runbook: `svc-partner-gw` — B2B Partner API Gateway

- **Service ID:** `svc-partner-gw`
- **Owner:** `team-integrations` (`#partner-messaging`, `pd-integrations-ops@acme-commerce.example`)
- **Tier / Criticality / SLA:** `TIER_1` / `HIGH` / `99.95%`
- **Domain:** `ecosystem` | **Region:** `asia-southeast3` (Bangkok)
- **Exposed APIs:** `api-partner-checkout-v1` (`ACTIVE`), `api-partner-settlement-v1` (`ACTIVE`)
- **Applicable Requirements:** `REQ-013`

## Architecture & Dependencies

`svc-partner-gw` is the external B2B ingress gateway for enterprise marketplace partners, enforcing OIDC scope verification, payload validation, and per-tenant token-bucket rate limiting (`REQ-013`).

- **Upstream Direct Callers:** `svc-webhooks` (`ASYNC_EVENT` settlement status lookups via `api-partner-settlement-v1`) and external B2B partner clients.
- **Downstream Synchronous Dependencies:**
  - `svc-checkout` (`api-checkout-submit-v1`, `api-checkout-quote-v1`) — places `svc-partner-gw` at hop 2 (`TRANSITIVE_UPSTREAM`) in the `api-payments-charge-v2` blast radius.
  - `svc-billing` (`api-billing-invoice-v1`, `api-billing-reconcile-v1`) — second hop-2 path to `api-payments-charge-v2`.
  - `svc-auth` (`api-auth-token-v2`, `api-auth-jwks-v2`, `ADR-006`, `REQ-006`).

## Operational Runbook & Quota Isolation

1. **Tenant Traffic Spikes (`REQ-013`):** If a single B2B partner triggers HTTP `429` bursts, confirm per-tenant rate buckets isolate other partners and prevent load amplification into `svc-checkout` and `svc-payments`.
2. **Tier-0 Change Coordination (`ADR-010`):** Because external partner SDKs rely on `api-partner-checkout-v1` error mappings propagated from `api-payments-charge-v2`, `team-integrations` (`#partner-messaging`) MUST sign off on any Tier-0 contract deprecation during the `180-day` freeze window.
