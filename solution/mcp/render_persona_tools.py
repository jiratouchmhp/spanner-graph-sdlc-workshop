#!/usr/bin/env python3
"""Render isolated per-persona MCP Toolbox YAML configs from tools.yaml.

Why persona slicing matters for Cloud Run MCP Toolbox:
  Mounting one monolithic tools.yaml onto a shared Cloud Run endpoint expands
  the blast radius if an agent prompt or service is compromised. This script
  slices solution/mcp/tools.yaml into three minimal persona files under
  solution/mcp/personas/:
    - tools.impact-analyst.yaml    (strictly read-only; 4 tools)
    - tools.adr-curator.yaml       (read + scoped PROPOSED ADR write; 5 tools)
    - tools.security-auditor.yaml  (strictly read-only compliance audit; 4 tools)

Each rendered file contains ONLY that persona's tools, toolset, and referenced
sources/embeddingModels/authServices, and enforces all workshop security
invariants before writing.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import yaml


VALID_SPANNER_SOURCE_KEYS = frozenset({"type", "project", "instance", "database", "dialect"})
FORBIDDEN_SOURCE_KEYS = frozenset({"databaseRole", "database_role"})
FORBIDDEN_TOOL_TYPES = frozenset({"spanner-execute-sql"})

PERSONA_SPECS: dict[str, dict[str, Any]] = {
    "impact-analyst": {
        "toolset": "impact-analyst-toolset",
        "readonly_only": True,
        "service_account": "sa-impact-analyst",
        "cloud_run_iam_role": "roles/spanner.databaseReader",
        "fgac_role": "sdlc_readonly_role",
    },
    "adr-curator": {
        "toolset": "adr-curator-toolset",
        "readonly_only": False,
        "service_account": "sa-adr-curator",
        "cloud_run_iam_role": "roles/spanner.databaseUser",
        "fgac_role": "sdlc_adr_writer_role",
    },
    "security-auditor": {
        "toolset": "security-auditor-toolset",
        "readonly_only": True,
        "service_account": "sa-security-auditor",
        "cloud_run_iam_role": "roles/spanner.databaseReader",
        "fgac_role": "sdlc_security_auditor_role",
    },
}


class _LiteralBlockDumper(yaml.SafeDumper):
    """Preserve readable literal block scalars (`|`) for multi-line SQL."""


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_LiteralBlockDumper.add_representer(str, _str_representer)


def validate_master_config(config: dict[str, Any]) -> None:
    """Validate all MCP Toolbox security and schema invariants."""
    required_top = ("sources", "embeddingModels", "authServices", "tools", "toolsets")
    for key in required_top:
        if key not in config or not isinstance(config[key], dict):
            raise ValueError(f"Master tools.yaml missing required mapping section: {key}")

    # 1. Validate Spanner source keys strictly (reject unsupported databaseRole).
    for src_name, src_cfg in config["sources"].items():
        if not isinstance(src_cfg, dict):
            raise ValueError(f"Source {src_name!r} must be a mapping")
        if src_cfg.get("type") == "spanner":
            keys = set(src_cfg.keys())
            forbidden = keys & FORBIDDEN_SOURCE_KEYS
            if forbidden:
                raise ValueError(
                    f"Source {src_name!r} contains forbidden key(s): {sorted(forbidden)}"
                )
            unexpected = keys - VALID_SPANNER_SOURCE_KEYS
            if unexpected:
                raise ValueError(
                    f"Source {src_name!r} contains unexpected key(s): {sorted(unexpected)}"
                )
            for req in ("project", "instance", "database", "dialect"):
                if req not in src_cfg:
                    raise ValueError(f"Source {src_name!r} missing required key {req!r}")

    # 2. Validate regional secretless Vertex AI embedding model (no active apiKey).
    embedder = config["embeddingModels"].get("gemini-embedder")
    if not isinstance(embedder, dict):
        raise ValueError("embeddingModels.gemini-embedder is required")
    if "apiKey" in embedder:
        raise ValueError(
            "embeddingModels.gemini-embedder must NOT contain an active static apiKey; "
            "use Vertex AI project + regional location via ADC / Workload Identity"
        )
    for req in ("type", "model", "project", "location", "dimension"):
        if req not in embedder:
            raise ValueError(f"embeddingModels.gemini-embedder missing required key {req!r}")
    if str(embedder["location"]).strip().lower() == "global":
        raise ValueError(
            "embeddingModels.gemini-embedder location must be regional "
            "(e.g. ${VERTEX_AI_LOCATION} -> asia-southeast1), never 'global'"
        )
    if embedder["dimension"] != 768:
        raise ValueError(f"Expected embedding dimension 768, got {embedder['dimension']!r}")

    # 3. Validate curated tools (zero raw SQL execution, zero templateParameters).
    for tool_name, tool_cfg in config["tools"].items():
        if not isinstance(tool_cfg, dict):
            raise ValueError(f"Tool {tool_name!r} must be a mapping")
        if tool_cfg.get("type") in FORBIDDEN_TOOL_TYPES:
            raise ValueError(
                f"Tool {tool_name!r} uses forbidden tool type {tool_cfg.get('type')!r}"
            )
        if tool_cfg.get("type") != "spanner-sql":
            raise ValueError(
                f"Tool {tool_name!r} must use curated 'spanner-sql', got {tool_cfg.get('type')!r}"
            )
        if "templateParameters" in tool_cfg:
            raise ValueError(f"Tool {tool_name!r} must not declare 'templateParameters'")
        if "readOnly" not in tool_cfg or not isinstance(tool_cfg["readOnly"], bool):
            raise ValueError(f"Tool {tool_name!r} must explicitly declare boolean readOnly")

    # 4. Validate every persona toolset exists and satisfies read-only isolation.
    for persona_id, spec in PERSONA_SPECS.items():
        ts_name = spec["toolset"]
        if ts_name not in config["toolsets"]:
            raise ValueError(f"Missing toolset {ts_name!r} for persona {persona_id!r}")
        tool_names = config["toolsets"][ts_name]
        if not isinstance(tool_names, list) or not tool_names:
            raise ValueError(f"Toolset {ts_name!r} must be a non-empty list")
        for tname in tool_names:
            if tname not in config["tools"]:
                raise ValueError(f"Toolset {ts_name!r} references unknown tool {tname!r}")
            if spec["readonly_only"] and not config["tools"][tname].get("readOnly", False):
                raise ValueError(
                    f"Read-only persona {persona_id!r} cannot include write tool {tname!r}"
                )


def slice_persona_config(master: dict[str, Any], persona_id: str) -> dict[str, Any]:
    """Build a minimal isolated MCP Toolbox config for a single persona."""
    spec = PERSONA_SPECS[persona_id]
    ts_name = spec["toolset"]
    allowed_tools: list[str] = list(master["toolsets"][ts_name])

    sliced_tools: dict[str, Any] = {}
    used_sources: set[str] = set()
    used_embedders: set[str] = set()
    used_auth_services: set[str] = set()

    for tname in allowed_tools:
        tcfg = master["tools"][tname]
        sliced_tools[tname] = tcfg
        if "source" in tcfg:
            used_sources.add(tcfg["source"])
        for auth_name in tcfg.get("authRequired", []) or []:
            used_auth_services.add(auth_name)
        for param in tcfg.get("parameters", []) or []:
            if "embeddedBy" in param:
                used_embedders.add(param["embeddedBy"])
            for auth_ref in param.get("authServices", []) or []:
                if isinstance(auth_ref, dict) and "name" in auth_ref:
                    used_auth_services.add(auth_ref["name"])

    rendered: dict[str, Any] = {
        "sources": {
            k: v for k, v in master["sources"].items() if k in used_sources
        },
    }
    if used_embedders:
        rendered["embeddingModels"] = {
            k: v for k, v in master["embeddingModels"].items() if k in used_embedders
        }
    if used_auth_services:
        rendered["authServices"] = {
            k: v for k, v in master["authServices"].items() if k in used_auth_services
        }
    rendered["tools"] = sliced_tools
    rendered["toolsets"] = {ts_name: allowed_tools}
    return rendered


def format_persona_yaml(persona_id: str, sliced: dict[str, Any]) -> str:
    """Serialize a persona config with a clear security header."""
    spec = PERSONA_SPECS[persona_id]
    header = (
        f"# AUTO-GENERATED by solution/mcp/render_persona_tools.py — DO NOT EDIT DIRECTLY\n"
        f"# Persona:            {persona_id}\n"
        f"# Toolset:            {spec['toolset']}\n"
        f"# Cloud Run SA:       {spec['service_account']} ({spec['cloud_run_iam_role']})\n"
        f"# Layer-2 FGAC Role:  {spec['fgac_role']} (for FGAC-aware clients only)\n"
        f"# Allowed Tools:      {', '.join(sliced['tools'].keys())}\n"
        f"#\n"
        f"# Blast-Radius Isolation: This file contains ONLY the tools permitted for\n"
        f"# {persona_id}. Compromise of this Cloud Run endpoint cannot invoke tools\n"
        f"# belonging to any other agent persona.\n\n"
    )
    body = yaml.dump(
        sliced,
        Dumper=_LiteralBlockDumper,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    return header + body


def render_all(source_path: Path, output_dir: Path, check_only: bool = False) -> list[Path]:
    """Validate master tools.yaml and write (or verify) all persona YAMLs."""
    with source_path.open("r", encoding="utf-8") as fh:
        master = yaml.safe_load(fh)
    if not isinstance(master, dict):
        raise ValueError(f"{source_path} did not parse into a YAML mapping")

    validate_master_config(master)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    mismatches: list[str] = []

    for persona_id in PERSONA_SPECS:
        sliced = slice_persona_config(master, persona_id)
        content = format_persona_yaml(persona_id, sliced)
        target = output_dir / f"tools.{persona_id}.yaml"
        if check_only:
            if not target.exists():
                mismatches.append(f"Missing persona file: {target}")
            elif target.read_text(encoding="utf-8") != content:
                mismatches.append(f"Out-of-date persona file: {target}")
        else:
            target.write_text(content, encoding="utf-8")
        written.append(target)

    if check_only and mismatches:
        raise RuntimeError(
            "Persona YAML drift detected:\n  - " + "\n  - ".join(mismatches)
        )
    return written


def main(argv: list[str] | None = None) -> int:
    mcp_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Render isolated per-persona MCP Toolbox YAML files from tools.yaml."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=mcp_dir / "tools.yaml",
        help="Path to master tools.yaml (default: solution/mcp/tools.yaml)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=mcp_dir / "personas",
        help="Output directory for sliced persona YAML files (default: solution/mcp/personas)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify rendered persona files are up-to-date without modifying disk",
    )
    args = parser.parse_args(argv)

    paths = render_all(args.source, args.output_dir, check_only=args.check)
    verb = "Verified" if args.check else "Rendered"
    for p in paths:
        print(f"[OK] {verb} {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
