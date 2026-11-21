# API Contract: `api-orders-create-v2` (`POST /v2/orders`)

- **Exposing Service:** `svc-orders` (`TIER_1`, `HIGH`, `99.95%` SLA)
- **Owning Team:** `team-checkout` (`#checkout-orders`)
- **HTTP Endpoint:** `POST /v2/orders`
- **Status:** `ACTIVE` | **Visibility:** Public (`is_public = true`) | **Latency Budget:** `400ms` p99
- **Governing ADRs:** `ADR-005` (`MANDATES`)
- **Applicable Requirements:** `REQ-007`, `REQ-009`

## Overview & Order Commit Flow

`api-orders-create-v2` creates durable customer orders backed by Bangkok (`asia-southeast3`) Cloud Spanner (`REQ-007`), coordinating checkout orchestration (`svc-checkout`), SKU inventory soft-lock confirmation (`svc-inventory`), and asynchronous customer notifications (`svc-notifications`).

## Synchronous & Asynchronous Interactions

1. **Idempotency (`ADR-005`):** Requires UUIDv4 `Idempotency-Key` header so mobile/web retries never spawn duplicate orders.
2. **Checkout & Payment Delegation (`REQ-009`):** Invokes `svc-checkout` (`api-checkout-submit-v1` and `api-checkout-quote-v1`) which in turn charges `api-payments-charge-v2`. If payment fails or times out, `svc-orders` executes saga compensation releasing `api-inventory-reserve-v1` holds.
3. **Async Event Dispatch (`ADR-007`):** Upon commit, emits `order.confirmed` CloudEvents to `svc-notifications` (`api-notify-dispatch-v1`, `api-notify-receipt-v1`). Note that `svc-notifications` subsequently queries `api-orders-status-v2` and publishes delivery receipts back to `svc-orders`, forming an intentional cycle (`svc-orders <-> svc-notifications`).
4. **Analytics Feed (`REQ-012`):** `svc-analytics` reads pseudonymized order streams via non-blocking `DATA_FEED` channels.
