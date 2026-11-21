# Service Runbook: `svc-notifications` — Customer Notifications Engine

- **Service ID:** `svc-notifications`
- **Owner:** `team-integrations` (`#partner-messaging`, `pd-integrations-ops@acme-commerce.example`)
- **Tier / Criticality / SLA:** `TIER_2` / `MEDIUM` / `99.9%`
- **Domain:** `messaging` | **Region:** `asia-southeast3` (Bangkok)
- **Exposed APIs:** `api-notify-dispatch-v1` (`ACTIVE`), `api-notify-receipt-v1` (`ACTIVE`)
- **Applicable Requirements:** `REQ-011`

## Architecture & Dependencies

`svc-notifications` delivers transactional order confirmations, payment receipts, refund notices, and shipping updates over email, SMS, and mobile push channels using the `ADR-007` CloudEvents contract.

- **Upstream Direct Callers:** `svc-orders` (`ASYNC_EVENT`, `api-notify-dispatch-v1`, `api-notify-receipt-v1`).
- **Downstream Dependencies:**
  - `svc-orders` (`ASYNC_EVENT` feedback loop + `api-orders-status-v2` enrichment lookup) — intentional cycle `svc-orders <-> svc-notifications`, placing `svc-notifications` at hop 3 (`TRANSITIVE_UPSTREAM`) in the `api-payments-charge-v2` blast radius.
  - `svc-auth` (`SYNC_RPC`, `api-auth-token-v2`, `ADR-006`).

## Operational Runbook & Trace Attribution

1. **Async Queue Lag:** Because `svc-notifications` is `TIER_2` asynchronous, SMS/email provider slowdowns buffer safely in durable event topics and dead-letter queues (`ADR-007`) without degrading `svc-orders` or `svc-payments`.
2. **Distributed Trace Propagation (`REQ-011`):** Every outbound notification log and delivery receipt callback MUST retain the originating `traceparent` header from `svc-orders` so support engineers can trace end-to-end receipt delivery.
3. **Cycle Termination Verification:** Never emit `order.confirmed` back to `svc-orders` from `api-notify-receipt-v1`; only delivery status updates are permitted across the cycle edge.
