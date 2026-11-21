# Service Runbook: `svc-orders` — Order Lifecycle Service

- **Service ID:** `svc-orders`
- **Owner:** `team-checkout` (`#checkout-orders`, `pd-checkout-tier0@acme-commerce.example`)
- **Tier / Criticality / SLA:** `TIER_1` / `HIGH` / `99.95%`
- **Domain:** `orders` | **Region:** `asia-southeast3` (Bangkok)
- **Exposed APIs:** `api-orders-create-v2` (`ACTIVE`), `api-orders-status-v2` (`ACTIVE`)
- **Applicable Requirements:** `REQ-007`, `REQ-009`

## Architecture & Dependencies

`svc-orders` owns the durable customer order state machine from creation through fulfillment and delivery confirmation in Bangkok (`asia-southeast3`) Cloud Spanner (`REQ-007`).

- **Upstream Direct Callers:** `svc-notifications` (async callback + `api-orders-status-v2`), `svc-webhooks` (`api-orders-status-v2`), and `svc-analytics` (async `DATA_FEED` on `api-orders-status-v2`).
- **Downstream Dependencies:**
  - `svc-checkout` (`SYNC_RPC`, `api-checkout-submit-v1`, `api-checkout-quote-v1`) — links `svc-orders` at hop 2 in the `api-payments-charge-v2` blast radius!
  - `svc-inventory` (`SYNC_RPC`, `api-inventory-reserve-v1`, `api-inventory-catalog-v1`) — coordinates soft-lock confirmation and saga rollback (`ADR-009`, `REQ-009`).
  - `svc-notifications` (`ASYNC_EVENT`, `api-notify-dispatch-v1`, `api-notify-receipt-v1`) — forms an intentional bidirectional cycle (`svc-orders <-> svc-notifications`, `ADR-007`).
  - `svc-auth` (`SYNC_RPC`, `api-auth-token-v2`, `api-auth-jwks-v2`, `ADR-006`).

## Operational Runbook & Cycle-Safety Guardrails

1. **Preventing Notification Event Loops:** Because `svc-orders` emits `order.confirmed` to `svc-notifications` and receives delivery receipts back from `svc-notifications`, workers MUST ignore `notification.delivered` events for outbound re-dispatch and deduplicate on `event_id` (`ADR-007`).
2. **Checkout Outage Degradation:** If `svc-checkout` or `api-payments-charge-v2` degrades, `api-orders-create-v2` fails fast and triggers `REQ-009` inventory release while `api-orders-status-v2` continues serving read-only order tracking.
