#!/usr/bin/env python3
"""Path A Structured Graph Loader (STARTER — Lab 3): Neo4j CE CSVs -> Cloud Spanner.

Antigravity Skills for Lab 3:
  - Primary: .agents/skills/spanner-schema-and-data-modeling/SKILL.md
  - Support: .agents/skills/spanner-migration-and-neo4j-playbook/SKILL.md

Your Task (`TODO(Lab 3)`):
  Implement `load_graph()` below so Path A loads in strict referential-integrity
  order:
    Stage 1 (Nodes — 68 rows across 5 tables):
      Owners -> Services -> Apis -> Adrs -> Requirements
    Stage 2 (Explicit M:N Edges — 174 rows across 6 tables):
      ServiceCallsApi, ServiceDependsOnService, AdrGovernsApi,
      AdrSupersedesAdr, ReqAppliesToApi, ReqAppliesToService

  Key Cloud Spanner Guardrails:
    - Respect Cloud Spanner's hard 80,000 mutations/commit ceiling using
      `compute_safe_batch_size()` and `chunk_rows()` (500 rows/commit default).
    - Keep `--dry-run` 100% Python stdlib-only (lazy-import `google.cloud.spanner`
      ONLY when `dry_run=False`).
    - Default target: Bangkok (`asia-southeast3`), instance `sdlc-kg-enterprise`,
      database `sdlc_knowledge_graph`.

Verify when done:
  python3 starter/pipelines/neo4j_to_spanner_loader.py --dry-run
  make TRACK=starter verify-lab LAB=3
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Locked Cloud Spanner Enterprise defaults (Bangkok primary — Thailand PDPA)
DEFAULT_SPANNER_REGION = "asia-southeast3"
DEFAULT_SPANNER_INSTANCE = "sdlc-kg-enterprise"
DEFAULT_SPANNER_DATABASE = "sdlc_knowledge_graph"

# Cloud Spanner hard limit is 80,000 mutations/commit (cells + index entries).
MAX_MUTATIONS_PER_COMMIT = 80_000
SAFE_MUTATION_HEADROOM = 60_000
DEFAULT_BATCH_SIZE = 500

# Stage 1: Strict topological node load order (parent FKs before children)
NODE_LOAD_SPECS: list[dict[str, Any]] = [
    {
        "key": "owners",
        "table": "Owners",
        "relpath": "nodes/owners.csv",
        "pk": ("owner_id",),
        "fks": [],
        "columns": [
            ("owner_id", "STRING"),
            ("team_name", "STRING"),
            ("org_unit", "STRING"),
            ("slack_channel", "STRING"),
            ("email", "STRING"),
            ("oncall_pager", "STRING"),
            ("clearance_tier", "STRING"),
            ("escalation_lead", "STRING"),
        ],
    },
    {
        "key": "services",
        "table": "Services",
        "relpath": "nodes/services.csv",
        "pk": ("service_id",),
        "fks": [("owner_id", "owners")],
        "columns": [
            ("service_id", "STRING"),
            ("service_name", "STRING"),
            ("tier", "STRING"),
            ("criticality", "STRING"),
            ("owner_id", "STRING"),
            ("sla_target_pct", "FLOAT64"),
            ("domain", "STRING"),
            ("lifecycle_status", "STRING"),
            ("description", "STRING"),
        ],
    },
    {
        "key": "apis",
        "table": "Apis",
        "relpath": "nodes/apis.csv",
        "pk": ("api_id",),
        "fks": [("service_id", "services")],
        "columns": [
            ("api_id", "STRING"),
            ("service_id", "STRING"),
            ("http_method", "STRING"),
            ("http_path", "STRING"),
            ("api_version", "STRING"),
            ("lifecycle_status", "STRING"),
            ("criticality", "STRING"),
            ("is_public", "BOOL"),
            ("latency_budget_ms", "INT64"),
            ("description", "STRING"),
        ],
    },
    {
        "key": "adrs",
        "table": "Adrs",
        "relpath": "nodes/adrs.csv",
        "pk": ("adr_id",),
        "fks": [("owner_id", "owners")],
        "columns": [
            ("adr_id", "STRING"),
            ("title", "STRING"),
            ("lifecycle_status", "STRING"),
            ("owner_id", "STRING"),
            ("decision_date", "DATE"),
            ("summary", "STRING"),
        ],
    },
    {
        "key": "requirements",
        "table": "Requirements",
        "relpath": "nodes/requirements.csv",
        "pk": ("req_id",),
        "fks": [("owner_id", "owners")],
        "columns": [
            ("req_id", "STRING"),
            ("framework", "STRING"),
            ("title", "STRING"),
            ("severity", "STRING"),
            ("owner_id", "STRING"),
            ("description", "STRING"),
        ],
    },
]

# Stage 2: Explicit M:N edge tables loaded strictly AFTER all Stage 1 nodes
EXPLICIT_EDGE_LOAD_SPECS: list[dict[str, Any]] = [
    {
        "key": "service_calls_api",
        "table": "ServiceCallsApi",
        "relpath": "edges/service_calls_api.csv",
        "pk": ("service_id", "api_id"),
        "fks": [("service_id", "services"), ("api_id", "apis")],
        "columns": [
            ("service_id", "STRING"),
            ("api_id", "STRING"),
            ("call_mode", "STRING"),
            ("is_critical", "BOOL"),
            ("migration_status", "STRING"),
            ("rps_estimate", "INT64"),
        ],
    },
    {
        "key": "service_depends_on_service",
        "table": "ServiceDependsOnService",
        "relpath": "edges/service_depends_on_service.csv",
        "pk": ("from_service_id", "to_service_id"),
        "fks": [("from_service_id", "services"), ("to_service_id", "services")],
        "columns": [
            ("from_service_id", "STRING"),
            ("to_service_id", "STRING"),
            ("dependency_type", "STRING"),
            ("is_sync", "BOOL"),
            ("criticality", "STRING"),
        ],
    },
    {
        "key": "adr_governs_api",
        "table": "AdrGovernsApi",
        "relpath": "edges/adr_governs_api.csv",
        "pk": ("adr_id", "api_id"),
        "fks": [("adr_id", "adrs"), ("api_id", "apis")],
        "columns": [
            ("adr_id", "STRING"),
            ("api_id", "STRING"),
            ("governance_type", "STRING"),
            ("effective_date", "DATE"),
        ],
    },
    {
        "key": "adr_supersedes_adr",
        "table": "AdrSupersedesAdr",
        "relpath": "edges/adr_supersedes_adr.csv",
        "pk": ("adr_id", "supersedes_adr_id"),
        "fks": [("adr_id", "adrs"), ("supersedes_adr_id", "adrs")],
        "columns": [
            ("adr_id", "STRING"),
            ("supersedes_adr_id", "STRING"),
            ("superseded_date", "DATE"),
        ],
    },
    {
        "key": "req_applies_to_api",
        "table": "ReqAppliesToApi",
        "relpath": "edges/req_applies_to_api.csv",
        "pk": ("req_id", "api_id"),
        "fks": [("req_id", "requirements"), ("api_id", "apis")],
        "columns": [
            ("req_id", "STRING"),
            ("api_id", "STRING"),
            ("compliance_status", "STRING"),
        ],
    },
    {
        "key": "req_applies_to_service",
        "table": "ReqAppliesToService",
        "relpath": "edges/req_applies_to_service.csv",
        "pk": ("req_id", "service_id"),
        "fks": [("req_id", "requirements"), ("service_id", "services")],
        "columns": [
            ("req_id", "STRING"),
            ("service_id", "STRING"),
            ("compliance_status", "STRING"),
        ],
    },
]

# Folded N:1 edges tracked for logical parity reporting (no separate Spanner table)
DUAL_PROJECTED_RELPATHS: dict[str, str] = {
    "api_exposes_from_service": "edges/api_exposes_from_service.csv",
    "owner_owns_service": "edges/owner_owns_service.csv",
    "owner_owns_adr": "edges/owner_owns_adr.csv",
    "owner_owns_req": "edges/owner_owns_req.csv",
}


def _resolve_default_project() -> str:
    """Resolve GCP project preferring explicit SPANNER_PROJECT or GOOGLE_CLOUD_PROJECT, ignoring unresolved ${...}."""
    unresolved_re = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")
    for var_name in ("SPANNER_PROJECT", "GOOGLE_CLOUD_PROJECT"):
        val = os.environ.get(var_name, "").strip()
        if val and not unresolved_re.search(val):
            return val
    return ""


def load_dotenv_stdlib(repo_root: Path) -> None:
    """Load key=value pairs from .env without overriding active env vars."""
    env_path = repo_root / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
            if "=" not in line:
                continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        val = re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            val,
        )
        if not key or re.search(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", val):
            continue
        if key not in os.environ:
            os.environ[key] = val


def coerce_spanner_value(raw_value: str, col_type: str) -> Any:
    """Convert CSV string cell into a Cloud Spanner GoogleSQL Python value."""
    val = raw_value.strip()
    if val == "":
        return None
    if col_type == "STRING":
        return val
    if col_type == "BOOL":
        lowered = val.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        raise ValueError(f"Invalid BOOL literal: {raw_value!r}")
    if col_type == "INT64":
        return int(val)
    if col_type == "FLOAT64":
        return float(val)
    if col_type == "DATE":
        return datetime.date.fromisoformat(val)
    raise ValueError(f"Unsupported Spanner column type: {col_type}")


def compute_safe_batch_size(requested_batch_size: int, num_columns: int) -> int:
    """Clamp row batch size so total mutations per commit stay strictly < 80,000."""
    if requested_batch_size <= 0:
        raise ValueError(f"batch_size must be positive, got {requested_batch_size}")
    mutations_per_row = max(num_columns, 1) * 2
    max_safe_rows = max(1, SAFE_MUTATION_HEADROOM // mutations_per_row)
    effective = min(requested_batch_size, max_safe_rows)
    if effective * max(num_columns, 1) >= MAX_MUTATIONS_PER_COMMIT:
        raise ValueError(
            f"Batch size {effective} with {num_columns} columns exceeds Spanner's "
            f"{MAX_MUTATIONS_PER_COMMIT:,} mutations/commit hard limit."
        )
    return effective


def chunk_rows(rows: list[tuple[Any, ...]], batch_size: int) -> list[list[tuple[Any, ...]]]:
    """Split rows into fixed-size commit chunks."""
    return [rows[i : i + batch_size] for i in range(0, len(rows), batch_size)]


def read_and_validate_table(
    data_dir: Path,
    spec: dict[str, Any],
    known_pks: dict[str, set[str]],
) -> tuple[list[str], list[tuple[Any, ...]], set[str]]:
    """Read a structured CSV, coerce types, and verify PK uniqueness & FK integrity."""
    csv_path = data_dir / spec["relpath"]
    if not csv_path.is_file():
        raise FileNotFoundError(f"Missing structured CSV: {csv_path}")

    col_names = [col for col, _ in spec["columns"]]
    col_types = dict(spec["columns"])
    pk_cols = spec["pk"]

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if list(reader.fieldnames or []) != col_names:
            raise ValueError(
                f"Header mismatch in {csv_path}: got {reader.fieldnames}, expected {col_names}"
            )
        raw_rows = list(reader)

    coerced_rows: list[tuple[Any, ...]] = []
    seen_pk_tuples: set[tuple[Any, ...]] = set()
    single_pks: set[str] = set()

    for line_no, raw_row in enumerate(raw_rows, start=2):
        coerced_map: dict[str, Any] = {}
        for col in col_names:
            coerced_map[col] = coerce_spanner_value(raw_row[col], col_types[col])

        pk_tuple = tuple(coerced_map[c] for c in pk_cols)
        if any(v is None for v in pk_tuple):
            raise ValueError(f"{csv_path}:{line_no} contains NULL primary key {pk_cols}")
        if pk_tuple in seen_pk_tuples:
            raise ValueError(f"{csv_path}:{line_no} duplicate primary key {pk_tuple}")
        seen_pk_tuples.add(pk_tuple)
        if len(pk_cols) == 1:
            single_pks.add(str(pk_tuple[0]))

        for fk_col, ref_key in spec["fks"]:
            fk_val = coerced_map[fk_col]
            if fk_val is not None and str(fk_val) not in known_pks.get(ref_key, set()):
                raise ValueError(
                    f"{csv_path}:{line_no} referential integrity error: "
                    f"{spec['table']}.{fk_col}={fk_val!r} not found in {ref_key}"
                )

        coerced_rows.append(tuple(coerced_map[col] for col in col_names))

    return col_names, coerced_rows, single_pks


def _count_dual_projected_edges(data_dir: Path) -> int:
    total = 0
    for relpath in DUAL_PROJECTED_RELPATHS.values():
        path = data_dir / relpath
        if path.is_file():
            with path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                total += sum(1 for _ in reader)
    return total


def load_graph(
    data_dir: Path,
    project: str,
    instance_id: str = DEFAULT_SPANNER_INSTANCE,
    database_id: str = DEFAULT_SPANNER_DATABASE,
    region: str = DEFAULT_SPANNER_REGION,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    load_dynamic_graph: bool = False,
) -> dict[str, Any]:
    """Load Path A nodes-first then edges-second into Cloud Spanner Enterprise.

    TODO(Lab 3): Implement nodes-before-edges batched mutations!
      Steps to complete:
        1. Iterate over `NODE_LOAD_SPECS` using `read_and_validate_table()`
           so `known_pks` captures all 68 node PKs in parent-before-child order
           (`Owners -> Services -> Apis -> Adrs -> Requirements`).
        2. Clamp each table's batch size via `compute_safe_batch_size(batch_size, len(col_names))`
           and split rows with `chunk_rows()` so every commit stays strictly
           `< 80,000` mutations (`MAX_MUTATIONS_PER_COMMIT`).
        3. Iterate over `EXPLICIT_EDGE_LOAD_SPECS` (174 rows across 6 edge tables),
           validating 100% FK referential integrity against `known_pks` and
           building chunked commit plans.
        4. When `dry_run=False`, lazily import `google.cloud.spanner`, commit
           all Stage 1 node batches with `database.batch().insert_or_update(...)`,
           and ONLY THEN commit all Stage 2 edge batches.
        5. Return a summary dict with `status`, `mode`, `spanner_target`,
           `load_order`, `mutation_guardrails`, `counts` (including 60 folded
           dual-projected edges via `_count_dual_projected_edges`),
           `stage_1_nodes`, and `stage_2_edges` (matching `solution/pipelines/neo4j_to_spanner_loader.py`).
    """
    raise NotImplementedError("TODO(Lab 3): implement nodes-before-edges batched mutations")


def build_arg_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv_stdlib(repo_root)

    default_data_dir = repo_root / "data" / "structured"
    default_region = (
        os.environ.get("SPANNER_REGION")
        or os.environ.get("GOOGLE_CLOUD_REGION")
        or DEFAULT_SPANNER_REGION
    )
    default_instance = os.environ.get("SPANNER_INSTANCE", DEFAULT_SPANNER_INSTANCE)
    default_database = os.environ.get("SPANNER_DATABASE", DEFAULT_SPANNER_DATABASE)
    default_project = _resolve_default_project()

    parser = argparse.ArgumentParser(
        description=(
            "Load Path A structured Neo4j CE exports into Cloud Spanner Enterprise "
            "(nodes-first, edges-second, chunked at 500 rows/commit)."
        )
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=default_data_dir,
        help="Path to data/structured directory.",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=default_project,
        help="GCP project ID (defaults to SPANNER_PROJECT / GOOGLE_CLOUD_PROJECT).",
    )
    parser.add_argument(
        "--region",
        type=str,
        default=default_region,
        help=f"Cloud Spanner region (default: {DEFAULT_SPANNER_REGION}).",
    )
    parser.add_argument(
        "--instance",
        type=str,
        default=default_instance,
        help=f"Cloud Spanner instance ID (default: {DEFAULT_SPANNER_INSTANCE}).",
    )
    parser.add_argument(
        "--database",
        type=str,
        default=default_database,
        help=f"Cloud Spanner database ID (default: {DEFAULT_SPANNER_DATABASE}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Rows per commit batch (default: {DEFAULT_BATCH_SIZE}; respects <80,000 mutations).",
    )
    parser.add_argument(
        "--load-dynamic-graph",
        action="store_true",
        help="Also stage/load GraphNode & GraphEdge for SdlcDynamicGraph comparison.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate CSV schemas, types, FK integrity, and batching using stdlib only.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON summary to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    summary = load_graph(
        data_dir=args.data_dir,
        project=args.project,
        instance_id=args.instance,
        database_id=args.database,
        region=args.region,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        load_dynamic_graph=args.load_dynamic_graph,
    )

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        tgt = summary["spanner_target"]
        cnt = summary["counts"]
        guard = summary["mutation_guardrails"]
        print(
            f"[{summary['status']}] Path A loader ({summary['mode']}) -> "
            f"{tgt['project']}/{tgt['instance']}/{tgt['database']} "
            f"({tgt['instance_config']})"
        )
        print(
            f"  Stage 1 Nodes ({cnt['total_nodes']} rows): "
            f"{' -> '.join(summary['load_order']['stage_1_nodes'])}"
        )
        print(
            f"  Stage 2 Explicit Edges ({cnt['explicit_edges']} rows): "
            f"{', '.join(summary['load_order']['stage_2_explicit_edges'])}"
        )
        print(
            f"  Logical Graph Parity: {cnt['explicit_edges']} explicit + "
            f"{cnt['dual_projected_edges_folded_on_nodes']} dual-projected = "
            f"{cnt['total_logical_graph_edges']} total edges"
        )
        print(
            f"  Mutation Guardrail: max {guard['max_mutations_per_commit_observed']:,} "
            f"< {guard['spanner_hard_limit']:,} mutations/commit "
            f"({cnt['total_planned_commits']} commit batch(es), batch_size={guard['requested_batch_size']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
