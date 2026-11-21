# API Contract: `api-auth-token-v2` (`POST /v2/auth/token`)

- **Exposing Service:** `svc-auth` (`TIER_0`, `CRITICAL`, `99.99%` SLA)
- **Owning Team:** `team-security` (`#security-iam`, `RESTRICTED_SEC`)
- **HTTP Endpoint:** `POST /v2/auth/token`
- **Status:** `ACTIVE` | **Visibility:** Internal Mesh Only (`is_public = false`) | **Latency Budget:** `25ms` p99
- **Governing ADRs:** `ADR-006` (`MANDATES`)
- **Applicable Requirements:** `REQ-006`

## Overview & High-Fanin Hub Topology

`api-auth-token-v2` is the highest-fanin API in the Acme Commerce SDLC Knowledge Graph, serving **10 direct microservice callers** across the platform:

- **Tier-0 Callers:** `svc-payments` (`2,400` RPS), `svc-checkout` (`2,800` RPS), `svc-billing` (`900` RPS), `svc-fraud` (`1,600` RPS), `svc-ledger` (`1,400` RPS)
- **Tier-1 Callers:** `svc-orders` (`1,900` RPS), `svc-partner-gw` (`1,500` RPS), `svc-inventory` (`1,100` RPS)
- **Tier-2 Callers:** `svc-notifications` (`650` RPS), `svc-webhooks` (`720` RPS)

Only `svc-analytics` (async pseudonymized `DATA_FEED`) and `svc-auth` itself do not call `api-auth-token-v2`. Because of this 10-caller fan-in, graph criticality algorithms (`PageRank`) consistently rank `svc-auth` as the #1 structural dependency, and graph traversal queries apply fan-out capping (`IS_FIRST`) when pivoting through `svc-auth`.

## Zero-Trust Token Exchange Contract

1. Caller establishes TLS 1.3 mTLS presenting its short-lived SPIFFE workload X.509 certificate (`ADR-006`, `REQ-006`).
2. `api-auth-token-v2` verifies certificate chain, workload allowlist, and requested target audience + OIDC scope.
3. Returns a signed 5-minute OIDC JWT verifiable locally by downstream services via public keys published on `api-auth-jwks-v2`.
4. Strict `25ms` p99 latency budget; multi-zone active-active deployment across `asia-southeast3-a/b/c`.
