# Service Runbook: `svc-webhooks` — Merchant Webhook Dispatcher

- **Service ID:** `svc-webhooks`
- **Owner:** `team-integrations` (`#partner-messaging`, `pd-integrations-ops@acme-commerce.example`)
- **Tier / Criticality / SLA:** `TIER_2` / `MEDIUM` / `99.9%`
- **Domain:** `ecosystem` | **Region:** `asia-southeast3` (Bangkok)
- **Exposed APIs:** `api-webhooks-emit-v1` (`ACTIVE`), `api-webhooks-verify-v1` (`ACTIVE`)
- **Applicable Requirements:** `REQ-010`

## Architecture & Dependencies

`svc-webhooks` delivers at-least-once HMAC-SHA256 signed webhook notifications (`REQ-010`) to external merchant endpoints whenever order states or partner settlement records change.

- **Downstream Dependencies:**
  - `svc-orders` (`ASYNC_EVENT`, `api-orders-status-v2`) — enriches order webhook payloads, placing `svc-webhooks` at hop 3 (`TRANSITIVE_UPSTREAM`) in the `api-payments-charge-v2` blast radius.
  - `svc-partner-gw` (`ASYNC_EVENT`, `api-partner-settlement-v1`) — enriches B2B settlement webhooks (also hop 3 to `api-payments-charge-v2`).
  - `svc-auth` (`SYNC_RPC`, `api-auth-token-v2`, `ADR-006`).

## Operational Runbook & DLQ Replay

1. **Merchant Endpoint Failures (`REQ-010`, `ADR-007`):** Outbound delivery retries 8 times with exponential backoff over 24 hours. Persistent `5xx` or TLS handshake errors route to the merchant DLQ; use the `team-integrations` replay CLI once the merchant restores availability.
2. **Signature Verification Issues:** Direct merchants to validate `X-Acme-Signature` against `api-webhooks-verify-v1` using raw unparsed request bytes and a 5-minute timestamp window.
