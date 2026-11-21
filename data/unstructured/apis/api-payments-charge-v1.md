# API Contract: `api-payments-charge-v1` (`POST /v1/payments/charge`)

- **Exposing Service:** `svc-payments` (`TIER_0`, `CRITICAL`)
- **Owning Team:** `team-payments` (`#payments-eng`)
- **HTTP Endpoint:** `POST /v1/payments/charge`
- **Status:** `DEPRECATED` | **Visibility:** Public (Restricted Legacy Ingress) | **Latency Budget:** `250ms` p99
- **Governing ADRs:** `ADR-001` (`DEPRECATED_LEGACY`), `ADR-003` (`DEPRECATED_LEGACY`), `ADR-006` (`CONSTRAINS`)
- **Applicable Requirements:** `REQ-004` (`AT_RISK`), `REQ-001` (`AT_RISK`)

## Deprecation Notice & Residual Consumer Risk

`api-payments-charge-v1` is **DEPRECATED** and scheduled for mandatory decommissioning under `REQ-004`. Built originally under `ADR-001` (superseded by `ADR-004`), this v1 contract accepted legacy direct PAN card fields rather than mandatory `tok_pci_v2_<32hex>` vault tokens and does not enforce the v2 `45ms` fail-closed fraud gate.

- **Active `AT_RISK` Caller:** `svc-billing` (`team-billing`, `#billing-ledger`) continues to send approximately `85` RPS (`migration_status = AT_RISK`) for unmigrated annual enterprise subscription renewals.
- **Compliance Impact:** Keeping `api-payments-charge-v1` live forces `svc-billing` to remain inside PCI-DSS 4.0 CDE audit scope (`REQ-001` `AT_RISK`) and blocks final retirement under `REQ-004`.

## Migration Playbook to `api-payments-charge-v2`

1. Re-vault all legacy recurring card profiles in `svc-billing` to `tok_pci_v2_<32hex>` references.
2. Add mandatory UUIDv4 `Idempotency-Key` headers (`ADR-005`) and handle HTTP `503` fail-closed declines for `> $500` invoices (`ADR-004`, `REQ-003`).
3. Cut over `svc-billing` recurring runners from `api-payments-charge-v1` to `api-payments-charge-v2` and validate zero residual v1 traffic over 14 consecutive billing cycles before returning HTTP `410 Gone`.
