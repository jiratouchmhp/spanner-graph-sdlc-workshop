#!/usr/bin/env python3
"""Path A Structured Graph Loader: Neo4j CE Export CSVs -> Cloud Spanner Enterprise.

Loads the Acme Commerce Platform structured SDLC knowledge graph in strict
referential-integrity order:
  Stage 1 (Nodes — 68 rows across 5 tables):
    Owners -> Services -> Apis -> Adrs -> Requirements
  Stage 2 (Explicit M:N Edges — 174 rows across 6 tables):
    ServiceCallsApi, ServiceDependsOnService, AdrGovernsApi,
    AdrSupersedesAdr, ReqAppliesToApi, ReqAppliesToService

Dual-projected N:1 relationships (60 edges: EXPOSES, OWNS_SERVICE, OWNS_ADR,
OWNS_REQ) are already folded as foreign-key columns on the node tables in
Cloud Spanner Graph (totaling 234 logical graph edges).

Safety & Spanner Limits:
  - Respects Cloud Spanner's hard 80,000 mutations/commit ceiling by chunking
    at 500 rows/commit (and dynamically clamping if a custom --batch-size would
    exceed the mutation budget).
  - Defaults to Bangkok primary region (`SPANNER_REGION=asia-southeast3`),
    instance `sdlc-kg-enterprise`, database `sdlc_knowledge_graph`.
  - Full `--dry-run` mode executes using Python stdlib ONLY (zero third-party
    packages required) and validates schemas, types, counts, and 100% FK
    referential integrity.
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
    # Account for base-table cells + conservative 2x safety factor for any
    # pre-existing secondary indexes or FK index validations.
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


def _prepare_dynamic_graph_records(
    data_dir: Path,
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Build lowercase-validated GraphNode and GraphEdge rows from JSONLs (INV-07)."""
    nodes_path = data_dir / "nodes/all_nodes.jsonl"
    edges_path = data_dir / "edges/all_edges.jsonl"
    if not nodes_path.is_file() or not edges_path.is_file():
        raise FileNotFoundError(
            f"Dynamic graph JSONLs missing under {data_dir} "
            "(expected nodes/all_nodes.jsonl and edges/all_edges.jsonl)"
        )

    def _lower_keys(obj: dict[str, Any]) -> dict[str, Any]:
        return {str(k).lower(): v for k, v in obj.items()}

    node_rows: list[tuple[Any, ...]] = []
    with nodes_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            node_id = str(rec["id"])
            node_label = str(rec["label"]).lower()
            domain_key = f"{node_label}:{node_id}"
            node_rows.append(
                (
                    node_id,
                    node_label,
                    domain_key,
                    json.dumps(_lower_keys(rec.get("properties", {})), sort_keys=True),
                )
            )

    edge_rows: list[tuple[Any, ...]] = []
    with edges_path.open("r", encoding="utf-8") as fh:
        for idx, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            rec = json.loads(line)
            edge_label = str(rec["edge_type"]).lower()
            src_id = str(rec["source_id"])
            dst_id = str(rec["target_id"])
            edge_id = f"{edge_label}__{idx:04d}"
            props = _lower_keys(rec.get("properties", {}))
            props["is_dual_projected"] = bool(rec.get("is_dual_projected", False))
            edge_rows.append(
                (src_id, dst_id, edge_id, edge_label, json.dumps(props, sort_keys=True))
            )

    return node_rows, edge_rows


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

    When ``dry_run=True``, executes strictly with the Python standard library
    (zero external packages imported) and returns the complete validation and
    mutation-chunking plan.
    """
    known_pks: dict[str, set[str]] = {}
    stage1_plan: list[dict[str, Any]] = []
    stage2_plan: list[dict[str, Any]] = []
    prepared_stage1: list[tuple[str, list[str], list[list[tuple[Any, ...]]]]] = []
    prepared_stage2: list[tuple[str, list[str], list[list[tuple[Any, ...]]]]] = []

    # 1. Stage 1 — Prepare & validate ALL node tables in strict parent-before-child order
    for spec in NODE_LOAD_SPECS:
        col_names, rows, pks = read_and_validate_table(data_dir, spec, known_pks)
        known_pks[spec["key"]] = pks
        eff_batch = compute_safe_batch_size(batch_size, len(col_names))
        chunks = chunk_rows(rows, eff_batch)
        est_mutations = len(rows) * len(col_names)
        max_chunk_mutations = max((len(c) * len(col_names) for c in chunks), default=0)
        assert max_chunk_mutations < MAX_MUTATIONS_PER_COMMIT
        stage1_plan.append(
            {
                "table": spec["table"],
                "rows": len(rows),
                "columns": len(col_names),
                "batch_size": eff_batch,
                "commits": len(chunks),
                "estimated_mutations": est_mutations,
                "max_mutations_per_commit": max_chunk_mutations,
            }
        )
        prepared_stage1.append((spec["table"], col_names, chunks))

    # 2. Stage 2 — Prepare & validate ALL explicit edge tables against Stage 1 node PKs
    for spec in EXPLICIT_EDGE_LOAD_SPECS:
        col_names, rows, _ = read_and_validate_table(data_dir, spec, known_pks)
        eff_batch = compute_safe_batch_size(batch_size, len(col_names))
        chunks = chunk_rows(rows, eff_batch)
        est_mutations = len(rows) * len(col_names)
        max_chunk_mutations = max((len(c) * len(col_names) for c in chunks), default=0)
        assert max_chunk_mutations < MAX_MUTATIONS_PER_COMMIT
        stage2_plan.append(
            {
                "table": spec["table"],
                "rows": len(rows),
                "columns": len(col_names),
                "batch_size": eff_batch,
                "commits": len(chunks),
                "estimated_mutations": est_mutations,
                "max_mutations_per_commit": max_chunk_mutations,
            }
        )
        prepared_stage2.append((spec["table"], col_names, chunks))

    # Optional schemaless comparison tables (02_path_b_schemaless_comparison.sql)
    dynamic_plan: list[dict[str, Any]] = []
    prepared_dynamic: list[tuple[str, list[str], list[list[tuple[Any, ...]]]]] = []
    if load_dynamic_graph:
        dyn_nodes, dyn_edges = _prepare_dynamic_graph_records(data_dir)
        for tbl, cols, rows in [
            ("GraphNode", ["id", "label", "domain_key", "properties"], dyn_nodes),
            ("GraphEdge", ["id", "dest_id", "edge_id", "label", "properties"], dyn_edges),
        ]:
            eff_batch = compute_safe_batch_size(batch_size, len(cols))
            chunks = chunk_rows(rows, eff_batch)
            dynamic_plan.append(
                {
                    "table": tbl,
                    "rows": len(rows),
                    "columns": len(cols),
                    "batch_size": eff_batch,
                    "commits": len(chunks),
                    "estimated_mutations": len(rows) * len(cols),
                    "max_mutations_per_commit": max(
                        (len(c) * len(cols) for c in chunks), default=0
                    ),
                }
            )
            prepared_dynamic.append((tbl, cols, chunks))

    # 3. Execute live Spanner writes ONLY when dry_run is False (lazy SDK import!)
    commits_executed = 0
    if not dry_run:
        if not project:
            raise ValueError(
                "GOOGLE_CLOUD_PROJECT (or --project) is required for live Spanner writes."
            )
        if os.environ.get("SPANNER_EMULATOR_HOST"):
            print(
                f"[WARN] SPANNER_EMULATOR_HOST={os.environ['SPANNER_EMULATOR_HOST']} is active.",
                file=sys.stderr,
            )
        try:
            from google.cloud import spanner  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "google-cloud-spanner is required for live execution. "
                "Install solution/pipelines/requirements.txt or pass --dry-run."
            ) from exc

        client = spanner.Client(project=project)
        instance = client.instance(instance_id)
        database = instance.database(database_id)

        def _write_stage(
            stage_label: str,
            items: list[tuple[str, list[str], list[list[tuple[Any, ...]]]]],
        ) -> int:
            local_commits = 0
            for table_name, columns, chunks in items:
                for chunk_idx, chunk in enumerate(chunks, start=1):
                    with database.batch() as batch:
                        batch.insert_or_update(
                            table=table_name,
                            columns=columns,
                            values=chunk,
                        )
                    local_commits += 1
                    print(
                        f"[{stage_label}] {table_name}: committed batch "
                        f"{chunk_idx}/{len(chunks)} ({len(chunk)} rows)",
                        file=sys.stderr,
                    )
            return local_commits

        commits_executed += _write_stage("STAGE-1-NODES", prepared_stage1)
        commits_executed += _write_stage("STAGE-2-EDGES", prepared_stage2)
        if prepared_dynamic:
            commits_executed += _write_stage("STAGE-3-DYNAMIC", prepared_dynamic)

    total_nodes = sum(item["rows"] for item in stage1_plan)
    total_explicit_edges = sum(item["rows"] for item in stage2_plan)
    dual_projected_edges = _count_dual_projected_edges(data_dir)
    all_plans = stage1_plan + stage2_plan + dynamic_plan

    return {
        "status": "DRY_RUN_OK" if dry_run else "LOADED_OK",
        "mode": "dry-run" if dry_run else "live",
        "spanner_target": {
            "project": project or "demo-project",
            "region": region,
            "instance_config": f"regional-{region}",
            "instance": instance_id,
            "database": database_id,
            "dialect": "GOOGLE_STANDARD_SQL",
        },
        "load_order": {
            "stage_1_nodes": [item["table"] for item in stage1_plan],
            "stage_2_explicit_edges": [item["table"] for item in stage2_plan],
        },
        "mutation_guardrails": {
            "spanner_hard_limit": MAX_MUTATIONS_PER_COMMIT,
            "requested_batch_size": batch_size,
            "max_mutations_per_commit_observed": max(
                (item["max_mutations_per_commit"] for item in all_plans), default=0
            ),
            "within_limit": all(
                item["max_mutations_per_commit"] < MAX_MUTATIONS_PER_COMMIT
                for item in all_plans
            ),
        },
        "counts": {
            "total_nodes": total_nodes,
            "explicit_edges": total_explicit_edges,
            "dual_projected_edges_folded_on_nodes": dual_projected_edges,
            "total_logical_graph_edges": total_explicit_edges + dual_projected_edges,
            "total_planned_commits": sum(item["commits"] for item in all_plans),
            "commits_executed": commits_executed,
        },
        "stage_1_nodes": stage1_plan,
        "stage_2_edges": stage2_plan,
        "stage_3_dynamic_graph": dynamic_plan,
    }


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
