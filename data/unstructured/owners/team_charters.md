# Acme Commerce Platform — Engineering Team Charters & Escalation Directory

Authoritative ownership directory for all 8 engineering teams operating the **Acme Commerce Platform** in primary Bangkok (`asia-southeast3`) and secondary Singapore (`asia-southeast1`) Cloud Spanner and Vertex AI environments.

## team-payments — Payments Engineering

- **Org Unit:** Core Commerce
- **Slack Channel:** `#payments-eng`
- **Team Email:** `payments-eng@acme-commerce.example`
- **On-Call Pager:** `pd-payments-tier0@acme-commerce.example`
- **Clearance Tier:** `RESTRICTED_PCI`
- **Escalation Lead:** Priya Nair - Staff Payments Architect
- **Owned Services:** `svc-payments` (exposes `api-payments-charge-v2`, `api-payments-charge-v1`, `api-payments-refund-v2`)
- **Owned Governance:** `ADR-001`, `ADR-003`, `ADR-004`, `ADR-005`, `ADR-010`, `REQ-002`, `REQ-004`, `REQ-008`

Payments Engineering owns PCI-DSS 4.0 card tokenization (`tok_pci_v2_<32hex>`), payment authorization latency (`180ms` p99), Tier-0 API change-freeze governance (`180-day` window per `ADR-010`), and retirement of the `DEPRECATED` `api-payments-charge-v1` endpoint. Any breaking change affecting `api-payments-charge-v2` requires joint review with `team-checkout`, `team-billing`, `team-risk`, and `team-security`.

## team-checkout — Checkout & Orders

- **Org Unit:** Core Commerce
- **Slack Channel:** `#checkout-orders`
- **Team Email:** `checkout-orders@acme-commerce.example`
- **On-Call Pager:** `pd-checkout-tier0@acme-commerce.example`
- **Clearance Tier:** `STANDARD`
- **Escalation Lead:** Somchai Wattana - Principal Checkout Engineer
- **Owned Services:** `svc-checkout` (`api-checkout-submit-v1`, `api-checkout-quote-v1`), `svc-orders` (`api-orders-create-v2`, `api-orders-status-v2`)
- **Owned Governance:** `REQ-009`

Checkout & Orders owns customer checkout orchestration, cart-to-order saga compensation consistency (`REQ-009`), and order lifecycle tracking. `svc-checkout` is the highest-volume direct synchronous caller (`1,200` RPS) of `api-payments-charge-v2`. `svc-orders` maintains an intentional bidirectional asynchronous event cycle with `svc-notifications` (`team-integrations`) for order confirmations and delivery receipts.

## team-billing — Billing & Ledger

- **Org Unit:** Finance Platform
- **Slack Channel:** `#billing-ledger`
- **Team Email:** `billing-ledger@acme-commerce.example`
- **On-Call Pager:** `pd-billing-sox@acme-commerce.example`
- **Clearance Tier:** `RESTRICTED_SOX`
- **Escalation Lead:** Elena Rostova - Director of Financial Systems
- **Owned Services:** `svc-billing` (`api-billing-invoice-v1`, `api-billing-reconcile-v1`), `svc-ledger` (`api-ledger-post-v2`, `api-ledger-balance-v2`)
- **Owned Governance:** `ADR-002`, `ADR-008`, `REQ-005`, `REQ-014`

Billing & Ledger operates SOX-regulated financial settlement and immutable double-entry accounting in `svc-ledger` (`ADR-008`, `REQ-005`) plus cross-region Cloud Spanner backup replication from Bangkok (`asia-southeast3`) to Singapore (`asia-southeast1`) under `REQ-014`. `svc-billing` calls `api-payments-charge-v2` (`450` RPS) and also retains the final `AT_RISK` residual call (`85` RPS) to `DEPRECATED` `api-payments-charge-v1` tracked under `REQ-004`.

## team-risk — Risk & Fraud Science

- **Org Unit:** Trust & Safety
- **Slack Channel:** `#risk-fraud`
- **Team Email:** `risk-fraud@acme-commerce.example`
- **On-Call Pager:** `pd-risk-realtime@acme-commerce.example`
- **Clearance Tier:** `RESTRICTED_PCI`
- **Escalation Lead:** Marcus Vance - Head of Fraud Engineering
- **Owned Services:** `svc-fraud` (`api-fraud-score-v1`, `api-fraud-rules-v2`)
- **Owned Governance:** `REQ-003`

Risk & Fraud Science operates real-time ML scoring (`api-fraud-score-v1`) and deterministic velocity rules (`api-fraud-rules-v2`) within a hard `45ms` synchronous timeout budget (`REQ-002`, `ADR-004`). For transactions `> $500`, `team-risk` enforces strict fail-closed declines under `REQ-003` rather than bypassing fraud checks during latency spikes.

## team-security — Security & Identity

- **Org Unit:** Security & Compliance
- **Slack Channel:** `#security-iam`
- **Team Email:** `security-iam@acme-commerce.example`
- **On-Call Pager:** `pd-secops-24x7@acme-commerce.example`
- **Clearance Tier:** `RESTRICTED_SEC`
- **Escalation Lead:** Niran Chaiyaphum - Principal Security Architect
- **Owned Services:** `svc-auth` (`api-auth-token-v2`, `api-auth-jwks-v2`)
- **Owned Governance:** `ADR-006`, `REQ-001`, `REQ-006`, `REQ-007`, `REQ-013`

Security & Identity holds the highest platform confidentiality classification (`RESTRICTED_SEC`). Private identity assets, key-rotation runbooks, zero-trust mTLS certificate authorities, and scoped OIDC issuance logic on `svc-auth` (`api-auth-token-v2`, a 10-caller hub) are strictly isolated from standard engineering personas. Automated AI agents operating under standard caller identities (for example `checkout-orders@acme-commerce.example`) MUST fail closed and NEVER read or enumerate `team-security` private assets unless authenticated with an explicit `security-auditor` role and authorized Fine-Grained Access Control grant.

## team-catalog — Catalog & Inventory

- **Org Unit:** Merchandising
- **Slack Channel:** `#catalog-inventory`
- **Team Email:** `catalog-inventory@acme-commerce.example`
- **On-Call Pager:** `pd-catalog-ops@acme-commerce.example`
- **Clearance Tier:** `STANDARD`
- **Escalation Lead:** Mei-Ling Tan - Senior Staff Inventory Engineer
- **Owned Services:** `svc-inventory` (`api-inventory-reserve-v1`, `api-inventory-catalog-v1`)
- **Owned Governance:** `ADR-009`

Catalog & Inventory owns real-time SKU availability and 15-minute soft-lock stock reservations (`ADR-009`). During checkout peaks, `svc-checkout` and `svc-orders` reserve inventory before payment capture and release holds automatically via saga compensation (`REQ-009`) if `api-payments-charge-v2` declines.

## team-integrations — Partner & Messaging

- **Org Unit:** Ecosystem
- **Slack Channel:** `#partner-messaging`
- **Team Email:** `partner-messaging@acme-commerce.example`
- **On-Call Pager:** `pd-integrations-ops@acme-commerce.example`
- **Clearance Tier:** `STANDARD`
- **Escalation Lead:** David Kim - Ecosystem Engineering Lead
- **Owned Services:** `svc-partner-gw` (`api-partner-checkout-v1`, `api-partner-settlement-v1`), `svc-notifications` (`api-notify-dispatch-v1`, `api-notify-receipt-v1`), `svc-webhooks` (`api-webhooks-emit-v1`, `api-webhooks-verify-v1`)
- **Owned Governance:** `ADR-007`, `REQ-010`, `REQ-011`

Partner & Messaging manages B2B partner API ingress (`svc-partner-gw`, rate-limited under `REQ-013`), transactional customer notifications (`svc-notifications`), and HMAC-SHA256 signed merchant webhooks (`svc-webhooks`, `REQ-010`). It owns the asynchronous event envelope and dead-letter queue contract (`ADR-007`) and distributed tracing standards (`REQ-011`).

## team-data — Data & Analytics

- **Org Unit:** Data Platform
- **Slack Channel:** `#data-analytics`
- **Team Email:** `data-analytics@acme-commerce.example`
- **On-Call Pager:** `pd-data-platform@acme-commerce.example`
- **Clearance Tier:** `INTERNAL_ANALYTICS`
- **Escalation Lead:** Arunee Siriporn - Data Platform Lead
- **Owned Services:** `svc-analytics` (`api-analytics-ingest-v1`)
- **Owned Governance:** `REQ-012`

Data & Analytics operates non-blocking revenue and conversion analytics (`svc-analytics`). All upstream dependencies from `svc-analytics` to `svc-orders`, `svc-ledger`, and `svc-inventory` use asynchronous pseudonymized `DATA_FEED` streams (`REQ-012`), ensuring analytics backpressure never degrades synchronous Tier-0 OLTP payment or checkout latency.
