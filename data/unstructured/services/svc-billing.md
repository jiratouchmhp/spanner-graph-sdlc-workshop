# Service Runbook: `svc-billing` — Subscription & Invoice Billing

- **Service ID:** `svc-billing`
- **Owner:** `team-billing` (`#billing-ledger`, `pd-billing-sox@acme-commerce.example`)
- **Tier / Criticality / SLA:** `TIER_0` / `CRITICAL` / `99.95%`
- **Domain:** `finance` | **Region:** `asia-southeast3` (Bangkok)
- **Exposed APIs:** `api-billing-invoice-v1` (`ACTIVE`), `api-billing-reconcile-v1` (`ACTIVE`)
- **Applicable Requirements:** `REQ-004` (`AT_RISK`), `REQ-005` (`COMPLIANT`)

## Architecture & Dependencies

`svc-billing` manages recurring B2B subscriptions, automated enterprise invoicing, and hourly settlement reconciliation against `svc-ledger`.

- **Upstream Direct Callers:** `svc-partner-gw` (`api-billing-invoice-v1`, `api-billing-reconcile-v1`).
- **Downstream Synchronous Dependencies:**
  - `svc-payments` (`api-payments-charge-v2` at `450` RPS `CURRENT`; `api-payments-refund-v2` at `95` RPS; AND legacy `api-payments-charge-v1` at `85` RPS `AT_RISK`).
  - `svc-ledger` (`api-ledger-post-v2`, `api-ledger-balance-v2`) for SOX double-entry invoice posting and balance checks (`ADR-008`, `REQ-005`).
  - `svc-auth` (`api-auth-token-v2`, `api-auth-jwks-v2`) for mTLS + OIDC (`ADR-006`).

## Operational Runbook & Legacy v1 Risk Mitigation

1. **Legacy `api-payments-charge-v1` Exposure (`AT_RISK`):** `svc-billing` is the sole remaining consumer of `DEPRECATED` `api-payments-charge-v1` (`ADR-001`, `REQ-004`). Any change to `svc-payments` MUST verify both v2 (`450` RPS) and legacy v1 (`85` RPS) billing jobs until the re-tokenization migration to `tok_pci_v2_<32hex>` finishes.
2. **SOX Reconciliation Variance (`REQ-005`):** If `api-billing-reconcile-v1` detects a non-zero delta against `api-ledger-balance-v2`, halt automated payout release and page `team-billing` (`#billing-ledger`) immediately. Never edit posted rows in `svc-ledger`; issue compensating entries per `ADR-008`.
3. **High-Value Invoice Retries:** Enterprise invoices `> $500` encountering a `503` fail-closed fraud timeout on `api-payments-charge-v2` (`REQ-003`) retry with exponential backoff reusing the original `Idempotency-Key` (`ADR-005`).
