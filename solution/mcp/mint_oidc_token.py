#!/usr/bin/env python3
"""Mint or inspect Google OIDC ID tokens for MCP Toolbox & Cloud Run Auth.

Supports two distinct authentication tokens used in Module 6:
  1. Cloud Run IAM invoker token (`roles/run.invoker`):
     Minted with `target_audience=https://<cloud-run-service>.run.app`.
     Passed as `Authorization: Bearer <token>` (or `X-Serverless-Authorization`
     when MCP Toolbox OAuth 2.1 `mcpEnabled: true` also claims `Authorization`).
  2. Toolbox Native OIDC AuthService token (`google-corp-oidc_token`):
     Carries verified OIDC `email` and `sub` claims decoded server-side by MCP
     Toolbox to bind `@caller_email` outside LLM prompt control.

Includes a zero-dependency `--mock` mode that synthesizes an unsigned
workshop demonstration JWT (clearly marked `MOCK-ONLY`) so participants can
inspect claim binding offline without GCP credentials.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from typing import Any


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_mock_oidc_jwt(
    *,
    email: str,
    audience: str,
    subject: str | None = None,
    ttl_seconds: int = 3600,
) -> tuple[str, dict[str, Any]]:
    """Build a deterministic unsigned mock OIDC JWT for offline workshop inspection."""
    now = int(time.time())
    header = {"alg": "none", "typ": "JWT", "kid": "workshop-mock-only"}
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": audience,
        "sub": subject or f"mock-sub-{email.split('@')[0]}",
        "email": email,
        "email_verified": True,
        "iat": now,
        "exp": now + ttl_seconds,
        "note": "UNSIGNED_WORKSHOP_MOCK_TOKEN_NEVER_USE_IN_PRODUCTION",
    }
    token = (
        f"{_b64url(json.dumps(header, separators=(',', ':')).encode('utf-8'))}."
        f"{_b64url(json.dumps(claims, separators=(',', ':')).encode('utf-8'))}."
        f"MOCK_SIGNATURE"
    )
    return token, claims


def fetch_live_cloud_run_id_token(audience: str) -> str:
    """Mint a real Google OIDC ID token via google-auth ADC or gcloud CLI."""
    try:
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2 import id_token  # type: ignore

        return str(id_token.fetch_id_token(Request(), audience))
    except Exception as adc_err:
        # Fallback to gcloud identity token for interactive developer workstations.
        proc = subprocess.run(
            ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
        raise RuntimeError(
            "Unable to mint live Google OIDC ID token via google-auth ADC or gcloud CLI.\n"
            f"  ADC error: {adc_err}\n"
            f"  gcloud stderr: {proc.stderr.strip()}\n"
            "Run `gcloud auth login` / `gcloud auth application-default login` or pass --mock."
        ) from adc_err


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mint Google OIDC ID tokens for Cloud Run MCP Toolbox & authServices."
    )
    parser.add_argument(
        "--audience",
        default=os.environ.get(
            "TOOLBOX_CLOUD_RUN_URL",
            "https://sdlc-mcp-impact-analyst-PROJECT_HASH-as.a.run.app",
        ),
        help="Target OIDC audience (Cloud Run service URL or OAuth Client ID)",
    )
    parser.add_argument(
        "--email",
        default="checkout-orders@acme-commerce.example",
        help="Email claim for --mock mode (default: checkout-orders@acme-commerce.example)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Generate an unsigned offline demo JWT without calling GCP",
    )
    parser.add_argument(
        "--print-headers",
        action="store_true",
        help="Print JSON showing Cloud Run & Toolbox Native Auth header mapping",
    )
    args = parser.parse_args(argv)

    if args.mock:
        token, claims = build_mock_oidc_jwt(email=args.email, audience=args.audience)
        if args.print_headers:
            payload = {
                "mode": "mock",
                "decoded_claims": claims,
                "headers": {
                    "Authorization": f"Bearer {token}",
                    "google-corp-oidc_token": token,
                    "X-Serverless-Authorization_note": (
                        "Use X-Serverless-Authorization for Cloud Run IAM when "
                        "Authorization carries a separate MCP OAuth 2.1 access token."
                    ),
                },
            }
            print(json.dumps(payload, indent=2))
        else:
            print(token)
        return 0

    token = fetch_live_cloud_run_id_token(args.audience)
    if args.print_headers:
        payload = {
            "mode": "live",
            "audience": args.audience,
            "headers": {
                "Authorization": f"Bearer {token}",
                "google-corp-oidc_token": token,
            },
        }
        print(json.dumps(payload, indent=2))
    else:
        print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
