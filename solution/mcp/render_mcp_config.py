#!/usr/bin/env python3
"""Render a personalized Antigravity IDE mcp_config.json without static secrets.

Generates an Antigravity-compatible `mcp_config.json` (root key `"mcpServers"`)
pointing to either:
  - `npx -y @toolbox-sdk/server` (zero manual binary download required),
  - local `./solution/mcp/bin/toolbox --config ... --stdio`,
  - remote Bangkok (`asia-southeast3`) Cloud Run MCP Toolbox HTTP endpoints,
  - or all modes side-by-side.

Security guarantees:
  - Never writes API keys, OAuth client secrets, or raw bearer tokens into JSON.
  - Defaults to the least-privilege read-only `impact-analyst` persona YAML.
  - Targets Vertex AI in `asia-southeast1` (never `global` for embeddings).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


VALID_PERSONAS = ("impact-analyst", "adr-curator", "security-auditor", "all")
VALID_MODES = ("npx", "binary", "remote", "all")


def resolve_tools_file(repo_root: Path, persona: str) -> Path:
    mcp_dir = repo_root / "solution" / "mcp"
    if persona == "all":
        return mcp_dir / "tools.yaml"
    return mcp_dir / "personas" / f"tools.{persona}.yaml"


def build_mcp_config(
    repo_root: Path,
    *,
    project_id: str,
    instance_id: str,
    database_id: str,
    vertex_location: str,
    oauth_client_id: str,
    persona: str,
    mode: str,
    cloud_run_url: str | None,
    include_prebuilt_dev: bool,
) -> dict[str, Any]:
    tools_file = resolve_tools_file(repo_root, persona)
    toolbox_bin = repo_root / "solution" / "mcp" / "bin" / "toolbox"
    base_env = {
        "SPANNER_PROJECT": project_id,
        "SPANNER_INSTANCE": instance_id,
        "SPANNER_DATABASE": database_id,
        "SPANNER_DIALECT": "googlesql",
        "GOOGLE_CLOUD_PROJECT": project_id,
        "VERTEX_AI_LOCATION": vertex_location,
        "GOOGLE_OAUTH_CLIENT_ID": oauth_client_id,
    }

    servers: dict[str, Any] = {}

    if mode in ("npx", "all"):
        servers[f"sdlc-spanner-{persona}-npx"] = {
            "command": "npx",
            "args": [
                "-y",
                "@toolbox-sdk/server",
                "--config",
                str(tools_file),
                "--stdio",
                "--allowed-hosts=localhost,127.0.0.1",
                "--allowed-origins=http://localhost:5000,http://127.0.0.1:5000",
                "--disable-version-check",
            ],
            "env": dict(base_env),
        }

    if mode in ("binary", "all"):
        servers[f"sdlc-spanner-{persona}-local-bin"] = {
            "command": str(toolbox_bin),
            "args": [
                "--config",
                str(tools_file),
                "--stdio",
                "--allowed-hosts=localhost,127.0.0.1",
                "--allowed-origins=http://localhost:5000,http://127.0.0.1:5000",
                "--disable-version-check",
            ],
            "env": dict(base_env),
        }

    if include_prebuilt_dev:
        servers["spanner-prebuilt-dev-hitl"] = {
            "command": "npx",
            "args": [
                "-y",
                "@toolbox-sdk/server",
                "--prebuilt",
                "spanner",
                "--stdio",
            ],
            "env": {
                "SPANNER_PROJECT": project_id,
                "SPANNER_INSTANCE": instance_id,
                "SPANNER_DATABASE": database_id,
                "SPANNER_DIALECT": "googlesql",
            },
        }

    if mode in ("remote", "all"):
        endpoint = (
            cloud_run_url.rstrip("/")
            if cloud_run_url
            else f"https://sdlc-mcp-{persona}-PROJECT_HASH-as.a.run.app"
        )
        if not endpoint.endswith("/mcp"):
            endpoint = f"{endpoint}/mcp"
        servers[f"sdlc-spanner-cloudrun-{persona}"] = {
            "type": "http",
            "url": endpoint,
            "headers": {
                "Authorization": "Bearer ${MCP_BEARER_TOKEN}",
            },
        }

    return {"mcpServers": servers}


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Render Antigravity IDE mcp_config.json with workspace paths and env vars."
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("GOOGLE_CLOUD_PROJECT", "YOUR_GCP_PROJECT_ID"),
        help="Google Cloud Project ID (default: $GOOGLE_CLOUD_PROJECT)",
    )
    parser.add_argument(
        "--instance",
        default=os.environ.get("SPANNER_INSTANCE", "sdlc-kg-enterprise"),
        help="Cloud Spanner instance ID (default: sdlc-kg-enterprise)",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("SPANNER_DATABASE", "sdlc_knowledge_graph"),
        help="Cloud Spanner database ID (default: sdlc_knowledge_graph)",
    )
    parser.add_argument(
        "--vertex-location",
        default=os.environ.get("VERTEX_AI_LOCATION", "asia-southeast1"),
        help="Regional Vertex AI endpoint for embeddings (default: asia-southeast1; never global)",
    )
    parser.add_argument(
        "--oauth-client-id",
        default=os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "${GOOGLE_OAUTH_CLIENT_ID}"),
        help="OAuth 2.0 Client ID for OIDC authServices (never embed client secrets)",
    )
    parser.add_argument(
        "--persona",
        choices=VALID_PERSONAS,
        default="impact-analyst",
        help="Persona YAML to bind (default: impact-analyst read-only)",
    )
    parser.add_argument(
        "--mode",
        choices=VALID_MODES,
        default="all",
        help="Server transport modes to include (default: all)",
    )
    parser.add_argument(
        "--cloud-run-url",
        default=os.environ.get("TOOLBOX_CLOUD_RUN_URL"),
        help="Optional Bangkok (asia-southeast3) Cloud Run service base URL",
    )
    parser.add_argument(
        "--include-prebuilt-dev",
        action="store_true",
        help="Also include build-time HITL --prebuilt spanner entry (dev IDE only)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / ".antigravity" / "mcp_config.json",
        help="Target JSON file path (default: .antigravity/mcp_config.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print rendered JSON to stdout without writing to disk",
    )
    args = parser.parse_args(argv)

    if args.vertex_location.strip().lower() == "global":
        parser.error(
            "--vertex-location must be a regional endpoint (e.g. asia-southeast1); "
            "gemini-embedding-001 does not support 'global'"
        )

    config = build_mcp_config(
        repo_root,
        project_id=args.project,
        instance_id=args.instance,
        database_id=args.database,
        vertex_location=args.vertex_location,
        oauth_client_id=args.oauth_client_id,
        persona=args.persona,
        mode=args.mode,
        cloud_run_url=args.cloud_run_url,
        include_prebuilt_dev=args.include_prebuilt_dev,
    )
    rendered = json.dumps(config, indent=2) + "\n"

    if args.dry_run:
        sys.stdout.write(rendered)
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"[OK] Wrote Antigravity MCP config to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
