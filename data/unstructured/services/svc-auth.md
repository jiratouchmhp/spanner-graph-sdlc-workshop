# Service Runbook: `svc-auth` — Zero-Trust Identity & Token Service

- **Service ID:** `svc-auth`
- **Owner:** `team-security` (`#security-iam`, `pd-secops-24x7@acme-commerce.example`, `RESTRICTED_SEC`)
- **Tier / Criticality / SLA:** `TIER_0` / `CRITICAL` / `99.99%`
- **Domain:** `security` | **Region:** `asia-southeast3` (Bangkok)
- **Exposed APIs:** `api-auth-token-v2` (`ACTIVE`, internal hub), `api-auth-jwks-v2` (`ACTIVE`, public JWKS)
- **Applicable Requirements:** `REQ-006`

## Architecture & High-Fanin Hub Criticality

`svc-auth` is the foundational zero-trust identity provider for Acme Commerce (`ADR-006`, `REQ-006`), enforcing TLS 1.3 mTLS workload identity attestation and minting 5-minute scoped OIDC bearer tokens.

- **Upstream Direct Callers (In-Degree = 10):** `svc-payments`, `svc-checkout`, `svc-billing`, `svc-orders`, `svc-partner-gw`, `svc-notifications`, `svc-webhooks`, `svc-fraud`, `svc-ledger`, and `svc-inventory` all depend synchronously on `svc-auth` and call `api-auth-token-v2`. Seven core services also poll `api-auth-jwks-v2` for public signing key rotation.
- **Downstream Dependencies:** Zero commerce-service dependencies (self-contained Tier-0 root to avoid circular bootstrap deadlocks).

## Operational Runbook & Restricted Access Controls

1. **Zero-Downtime Key Rotation:** Publish new signing keys to `api-auth-jwks-v2` at least 24 hours before signing tokens on `api-auth-token-v2`, and retain retiring public keys for 24 hours after token expiration (`5m` TTL).
2. **`25ms` Latency Protection:** If `api-auth-token-v2` latency rises above `25ms`, scale regional Bangkok replicas immediately; a full `svc-auth` outage blocks new token minting across all 10 dependent services once 5-minute caches expire.
3. **`RESTRICTED_SEC` Confidentiality:** `svc-auth` signing HSM configurations, workload trust policies, and `team-security` private assets are strictly restricted from non-security personas and AI agent queries.
