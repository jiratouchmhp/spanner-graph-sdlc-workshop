#!/usr/bin/env python3
"""Parameterized Read-Only Runner for the Hero API Blast-Radius GraphRAG Queries.

Executes `solution/sql/07_hero_api_blast_radius_graphrag.sql` against Cloud
Spanner Enterprise in Bangkok (`asia-southeast3`) using Vertex AI regional
embeddings (`gemini-embedding-001`, 768-dim `FLOAT32`) in Singapore
(`asia-southeast1` — NEVER `global`, per INV-09).

Supports:
  - `--list`                 List available Hero query variants (A, B, C, all)
  - `--query A|B|C|all`      Select Exact KNN (A), CTE-isolated ScaNN ANN (B),
                             Schemaless SdlcDynamicGraph (C), or all three
  - `--emit-sql`             Emit clean executable SQL for Spanner Studio / gcloud
  - `--dry-run`              Offline zero-dependency stdlib validation & Hero replay
  - `--check-fixture <json>` Assert exact 7-row deterministic impact contract parity
  - `--json`                 Emit machine-readable JSON output

All live queries run strictly inside a Cloud Spanner READ-ONLY snapshot
transaction (INV-09: required for Full-Text Search `SEARCH()` / `SCORE()`).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

# Locked defaults aligned with WORKSHOP_SPEC.json
DEFAULT_SPANNER_REGION = "asia-southeast3"
DEFAULT_SPANNER_INSTANCE = "sdlc-kg-enterprise"
DEFAULT_SPANNER_DATABASE = "sdlc_knowledge_graph"
DEFAULT_VERTEX_AI_LOCATION = "asia-southeast1"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768
DEFAULT_API_ID = "api-payments-charge-v2"
DEFAULT_SEARCH_QUERY = "POST /v2/payments/charge tok_pci_v2 45ms 180-day freeze"
FORBIDDEN_EMBEDDING_LOCATIONS = frozenset({"global", "us", "eu"})

QUERY_CATALOG: dict[str, dict[str, Any]] = {
    "A": {
        "key": "A",
        "title": "Hero Query A — Exact KNN (COSINE_DISTANCE) + FTS RRF + GRAPH_TABLE MATCH TRAIL",
        "graph": "SdlcGraph",
        "vector_mode": "EXACT_KNN",
        "needs_embedding": True,
        "params": ["api_id", "search_query", "query_vec"],
        "description": (
            "100% recall exact KNN vector search (@{scan_method=batch}) fused via server-side "
            "RRF (k=60, UNNEST(ARRAY(...)) WITH OFFSET) with FTS (SdlcSynonymDict + enhance_query) "
            "and cycle-safe MATCH TRAIL blast-radius traversal."
        ),
    },
    "B": {
        "key": "B",
        "title": "Hero Query B — ScaNN ANN (CTE-Isolated) + FTS RRF + GRAPH_TABLE MATCH TRAIL",
        "graph": "SdlcGraph",
        "vector_mode": "SCANN_ANN",
        "needs_embedding": True,
        "params": ["api_id", "search_query", "query_vec"],
        "description": (
            "Production-scalable ScaNN ANN variant enforcing INV-08: APPROX_COSINE_DISTANCE "
            "runs strictly inside single-table CTE `vec_top` with WHERE embedding IS NOT NULL "
            "and zero JOINs inside the ANN block, joined outside to MATCH TRAIL blast radius."
        ),
    },
    "C": {
        "key": "C",
        "title": "Hero Query C — Schemaless Equivalent Over SdlcDynamicGraph (Path B Comparison)",
        "graph": "SdlcDynamicGraph",
        "vector_mode": "NONE",
        "needs_embedding": False,
        "params": ["api_id"],
        "description": (
            "Executes identical cycle-safe MATCH TRAIL blast-radius semantics against "
            "SdlcDynamicGraph (DYNAMIC LABEL + DYNAMIC PROPERTIES, INV-07) using explicit "
            "STRING(...) casts over dynamic JSON properties."
        ),
    },
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
    """Load key=value pairs from .env without overriding active environment vars."""
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


def validate_vertex_location(location: str) -> str:
    """Enforce INV-09: regional Vertex AI endpoint required for gemini-embedding-001."""
    normalized = location.strip().lower()
    if not normalized:
        raise ValueError("VERTEX_AI_LOCATION must not be empty.")
    if normalized in FORBIDDEN_EMBEDDING_LOCATIONS:
        raise ValueError(
            f"INV-09 violation: VERTEX_AI_LOCATION={location!r} is unsupported for "
            f"{DEFAULT_EMBEDDING_MODEL}. Use a regional endpoint such as "
            f"{DEFAULT_VERTEX_AI_LOCATION} (NEVER 'global', 'us', or 'eu')."
        )
    return normalized


def _split_top_level_sql(sql_text: str) -> list[str]:
    """Split SQL text into semicolon-terminated statements preserving string literals."""
    stmts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql_text)
    in_s = in_d = in_b = False
    while i < n:
        ch = sql_text[i]
        nxt = sql_text[i + 1] if i + 1 < n else ""
        if not (in_s or in_d or in_b):
            if ch == "-" and nxt == "-":
                nl = sql_text.find("\n", i)
                if nl == -1:
                    break
                buf.append(sql_text[i : nl + 1])
                i = nl + 1
                continue
            if ch == "/" and nxt == "*":
                end = sql_text.find("*/", i + 2)
                if end == -1:
                    break
                buf.append(sql_text[i : end + 2])
                i = end + 2
                continue
        if ch == "\\" and (in_s or in_d) and i + 1 < n:
            buf.append(ch)
            buf.append(nxt)
            i += 2
            continue
        if ch == "'" and not (in_d or in_b):
            in_s = not in_s
        elif ch == '"' and not (in_s or in_b):
            in_d = not in_d
        elif ch == "`" and not (in_s or in_d):
            in_b = not in_b
        if ch == ";" and not (in_s or in_d or in_b):
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    trailing = "".join(buf).strip()
    if trailing:
        stmts.append(trailing)
    return stmts


def extract_hero_queries(sql_path: Path) -> dict[str, str]:
    """Extract Hero Queries A, B, and C1 from 07_hero_api_blast_radius_graphrag.sql."""
    if not sql_path.is_file():
        raise FileNotFoundError(f"Hero SQL file not found: {sql_path}")
    text = sql_path.read_text(encoding="utf-8")
    stmts = _split_top_level_sql(text)

    queries: dict[str, str] = {}
    for stmt in stmts:
        if "HERO QUERY A:" in stmt or ("COSINE_DISTANCE(embedding" in stmt and "APPROX_COSINE_DISTANCE" not in stmt):
            queries["A"] = stmt
        elif "HERO QUERY B:" in stmt or "APPROX_COSINE_DISTANCE(" in stmt:
            queries["B"] = stmt
        elif "Query C1:" in stmt or ("SdlcDynamicGraph" in stmt and "raw_dynamic_blast" in stmt):
            queries["C"] = stmt

    missing = [k for k in ("A", "B", "C") if k not in queries]
    if missing:
        raise ValueError(
            f"Could not extract Hero query section(s) {missing} from {sql_path}"
        )
    return queries


def resolve_selected_keys(query_arg: str) -> list[str]:
    norm = query_arg.strip().upper()
    if norm == "ALL":
        return ["A", "B", "C"]
    if norm in ("A", "B", "C"):
        return [norm]
    raise ValueError(f"Unsupported --query {query_arg!r}; choose A, B, C, or all.")


def embed_search_query_vertex(
    search_query: str,
    project: str,
    vertex_location: str,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> list[float]:
    """Generate a 768-dim FLOAT32 RETRIEVAL_QUERY vector via regional Vertex AI."""
    validated_loc = validate_vertex_location(vertex_location)
    try:
        from google import genai  # type: ignore[import-untyped]
        from google.genai import types  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "google-genai is required for live Vertex AI query embeddings. "
            "Run `make install` or pass `--dry-run` / `--emit-sql`."
        ) from exc

    client = genai.Client(vertexai=True, project=project, location=validated_loc)
    response = client.models.embed_content(
        model=embedding_model,
        contents=[search_query],
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=EMBEDDING_DIMENSIONS,
        ),
    )
    embeddings = response.embeddings or []
    if not embeddings:
        raise RuntimeError("Vertex AI returned zero embeddings for query text.")
    values = [float(v) for v in embeddings[0].values]
    if len(values) != EMBEDDING_DIMENSIONS or not all(math.isfinite(v) for v in values):
        raise ValueError(
            f"Invalid embedding returned from {validated_loc}/{embedding_model} "
            f"(len={len(values)}, expected {EMBEDDING_DIMENSIONS} finite floats)."
        )
    return values


def verify_rows_against_fixture(
    rows: list[dict[str, Any]],
    fixture_path: Path,
    query_key: str,
) -> tuple[bool, list[str]]:
    """Check impact rows against 07_hero_expected_rows.json (C omits slack/legacy cols)."""
    if not fixture_path.is_file():
        return False, [f"Fixture file not found: {fixture_path}"]
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    expected = fixture.get("expected_impact_rows", [])
    errors: list[str] = []
    if len(rows) != len(expected):
        return False, [f"Query {query_key}: got {len(rows)} rows, expected {len(expected)}"]

    compare_fields = (
        ["impacted_service_id", "service_name", "hop_distance", "impact_category", "tier", "criticality", "owner_id"]
        if query_key == "C"
        else [
            "impacted_service_id",
            "service_name",
            "hop_distance",
            "impact_category",
            "tier",
            "criticality",
            "owner_id",
            "slack_channel",
            "has_legacy_v1_risk",
        ]
    )
    for idx, (act, exp) in enumerate(zip(rows, expected), start=1):
        for field in compare_fields:
            if act.get(field) != exp.get(field):
                errors.append(
                    f"Query {query_key} row {idx} field {field!r}: "
                    f"got {act.get(field)!r}, expected {exp.get(field)!r}"
                )
    return not errors, errors


def run_live_queries(
    selected_keys: list[str],
    queries_sql: dict[str, str],
    *,
    project: str,
    instance_id: str,
    database_id: str,
    spanner_region: str,
    vertex_location: str,
    embedding_model: str,
    api_id: str,
    search_query: str,
    check_fixture: Path | None,
) -> dict[str, Any]:
    """Execute selected Hero queries live inside a Cloud Spanner READ-ONLY snapshot."""
    validated_loc = validate_vertex_location(vertex_location)
    if not project:
        raise ValueError("GOOGLE_CLOUD_PROJECT (or --project) is required for live execution.")

    try:
        from google.cloud import spanner  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-spanner is required for live execution. "
            "Run `make install` or pass `--dry-run`."
        ) from exc

    needs_vec = any(QUERY_CATALOG[k]["needs_embedding"] for k in selected_keys)
    query_vec: list[float] | None = None
    if needs_vec:
        query_vec = embed_search_query_vertex(
            search_query=search_query,
            project=project,
            vertex_location=validated_loc,
            embedding_model=embedding_model,
        )

    client = spanner.Client(project=project)
    database = client.instance(instance_id).database(database_id)

    results_by_query: dict[str, Any] = {}
    all_fixtures_ok = True

    # INV-09: ALWAYS execute FTS + Hybrid GraphRAG queries in a READ-ONLY snapshot!
    # Pass multi_use=True so `--query all` can execute Queries A, B, and C in one snapshot.
    with database.snapshot(multi_use=True) as snapshot:
        for key in selected_keys:
            sql = queries_sql[key]
            params: dict[str, Any] = {"api_id": api_id}
            param_types: dict[str, Any] = {"api_id": spanner.param_types.STRING}
            if QUERY_CATALOG[key]["needs_embedding"]:
                params["search_query"] = search_query
                params["query_vec"] = query_vec
                param_types["search_query"] = spanner.param_types.STRING
                param_types["query_vec"] = spanner.param_types.Array(
                    spanner.param_types.FLOAT32
                )

            rs = snapshot.execute_sql(sql=sql, params=params, param_types=param_types)
            raw_rows = list(rs)
            col_names = [f.name for f in rs.fields]
            dict_rows = [dict(zip(col_names, row)) for row in raw_rows]

            fixture_result = None
            if check_fixture is not None:
                ok, errs = verify_rows_against_fixture(dict_rows, check_fixture, key)
                if not ok:
                    all_fixtures_ok = False
                fixture_result = {"passed": ok, "errors": errs}

            results_by_query[key] = {
                "title": QUERY_CATALOG[key]["title"],
                "graph": QUERY_CATALOG[key]["graph"],
                "transaction_mode": "READ_ONLY_SNAPSHOT",
                "row_count": len(dict_rows),
                "rows": dict_rows,
                "fixture_check": fixture_result,
            }

    return {
        "status": "LIVE_OK" if all_fixtures_ok else "LIVE_FIXTURE_MISMATCH",
        "mode": "live",
        "all_passed": all_fixtures_ok,
        "spanner_target": {
            "project": project,
            "region": spanner_region,
            "instance_config": f"regional-{spanner_region}",
            "instance": instance_id,
            "database": database_id,
            "transaction_mode": "READ_ONLY_SNAPSHOT",
        },
        "vertex_ai_embedding": {
            "location": validated_loc,
            "model": embedding_model,
            "dimensions": EMBEDDING_DIMENSIONS,
            "generated": query_vec is not None,
        },
        "parameters": {
            "api_id": api_id,
            "search_query": search_query,
        },
        "queries": results_by_query,
    }


def run_dry_run(
    selected_keys: list[str],
    queries_sql: dict[str, str],
    *,
    project: str,
    instance_id: str,
    database_id: str,
    spanner_region: str,
    vertex_location: str,
    embedding_model: str,
    api_id: str,
    search_query: str,
    check_fixture: Path | None,
) -> dict[str, Any]:
    """Validate SQL invariants, Vertex AI regional guardrails & offline Hero replay (stdlib only)."""
    validated_loc = validate_vertex_location(vertex_location)

    sys.path.insert(0, str(REPO_ROOT / "solution" / "pipelines"))
    from hero_blast_radius_offline import (  # type: ignore
        replay_blast_radius,
        verify_against_fixture,
    )

    replay = replay_blast_radius(
        api_id=api_id,
        structured_dir=REPO_ROOT / "data" / "structured",
        unstructured_dir=REPO_ROOT / "data" / "unstructured",
        search_query=search_query,
    )

    fixture_ok = True
    fixture_errors: list[str] = []
    if check_fixture is not None:
        fixture_ok, fixture_errors = verify_against_fixture(replay, check_fixture)

    query_plans: dict[str, Any] = {}
    for key in selected_keys:
        sql = queries_sql[key]
        inv_checks = {
            "has_match_trail": bool(re.search(r"\bMATCH\s+TRAIL\b", sql, re.IGNORECASE)),
            "has_data_feed_separation": "DATA_FEED_CONSUMER" in sql,
            "no_row_number": not bool(re.search(r"\bROW_NUMBER\s*\(", sql, re.IGNORECASE)),
            "scann_where_not_null": (
                bool(re.search(r"WHERE\s+embedding\s+IS\s+NOT\s+NULL", sql, re.IGNORECASE))
                if key == "B"
                else None
            ),
        }
        query_plans[key] = {
            "title": QUERY_CATALOG[key]["title"],
            "graph": QUERY_CATALOG[key]["graph"],
            "vector_mode": QUERY_CATALOG[key]["vector_mode"],
            "transaction_mode": "READ_ONLY_SNAPSHOT",
            "params_bound": QUERY_CATALOG[key]["params"],
            "sql_line_count": len(sql.splitlines()),
            "invariant_checks": inv_checks,
        }

    return {
        "status": "DRY_RUN_OK" if fixture_ok else "DRY_RUN_FIXTURE_MISMATCH",
        "mode": "dry-run",
        "all_passed": fixture_ok,
        "spanner_target": {
            "project": project or "demo-project",
            "region": spanner_region,
            "instance_config": f"regional-{spanner_region}",
            "instance": instance_id,
            "database": database_id,
            "transaction_mode": "READ_ONLY_SNAPSHOT",
        },
        "vertex_ai_embedding": {
            "location": validated_loc,
            "model": embedding_model,
            "dimensions": EMBEDDING_DIMENSIONS,
            "inv09_regional_verified": True,
        },
        "parameters": {
            "api_id": api_id,
            "search_query": search_query,
        },
        "selected_queries": query_plans,
        "offline_replay_summary": {
            "api_id": replay["api_id"],
            "impact_row_count": replay["impact_row_count"],
            "impact_rows": replay["impact_rows"],
            "governing_adrs": [a["adr_id"] for a in replay["governing_adrs"]],
            "applicable_requirements": replay["applicable_requirement_ids"],
            "fixture_check": (
                {"fixture_path": str(check_fixture), "passed": fixture_ok, "errors": fixture_errors}
                if check_fixture is not None
                else None
            ),
        },
    }


def build_arg_parser() -> argparse.ArgumentParser:
    load_dotenv_stdlib(REPO_ROOT)

    default_region = (
        os.environ.get("SPANNER_REGION")
        or os.environ.get("GOOGLE_CLOUD_REGION")
        or DEFAULT_SPANNER_REGION
    )
    default_vertex_loc = (
        os.environ.get("VERTEX_AI_EMBEDDING_LOCATION")
        or os.environ.get("VERTEX_AI_LOCATION")
        or DEFAULT_VERTEX_AI_LOCATION
    )
    default_project = _resolve_default_project()

    parser = argparse.ArgumentParser(
        description=(
            "Parameterized read-only runner for Acme Commerce Hero API Blast-Radius "
            "GraphRAG queries (Spanner asia-southeast3 + Vertex AI asia-southeast1)."
        )
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available Hero query variants (A, B, C, all) and exit.",
    )
    parser.add_argument(
        "--query",
        default="B",
        help="Hero query variant to run or emit: A, B, C, or all (default: B).",
    )
    parser.add_argument(
        "--emit-sql",
        action="store_true",
        help="Print the selected SQL statement(s) to stdout and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate SQL invariants, regional settings, and replay Hero fixture offline (stdlib only).",
    )
    parser.add_argument(
        "--api-id",
        default=DEFAULT_API_ID,
        help=f"Target API identifier (default: {DEFAULT_API_ID}).",
    )
    parser.add_argument(
        "--search-query",
        default=DEFAULT_SEARCH_QUERY,
        help="Natural-language search query for FTS + Vertex AI vector retrieval.",
    )
    parser.add_argument(
        "--sql-file",
        type=Path,
        default=REPO_ROOT / "solution" / "sql" / "07_hero_api_blast_radius_graphrag.sql",
        help="Path to 07_hero_api_blast_radius_graphrag.sql.",
    )
    parser.add_argument(
        "--project",
        default=default_project,
        help="GCP project ID (defaults to SPANNER_PROJECT / GOOGLE_CLOUD_PROJECT).",
    )
    parser.add_argument(
        "--region",
        default=default_region,
        help=f"Cloud Spanner region (default: {DEFAULT_SPANNER_REGION}).",
    )
    parser.add_argument(
        "--instance",
        default=os.environ.get("SPANNER_INSTANCE", DEFAULT_SPANNER_INSTANCE),
        help=f"Cloud Spanner instance ID (default: {DEFAULT_SPANNER_INSTANCE}).",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("SPANNER_DATABASE", DEFAULT_SPANNER_DATABASE),
        help=f"Cloud Spanner database ID (default: {DEFAULT_SPANNER_DATABASE}).",
    )
    parser.add_argument(
        "--vertex-location",
        default=default_vertex_loc,
        help=f"Regional Vertex AI embedding location (default: {DEFAULT_VERTEX_AI_LOCATION}; NEVER global).",
    )
    parser.add_argument(
        "--embedding-model",
        default=os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        help=f"Vertex AI embedding model ID (default: {DEFAULT_EMBEDDING_MODEL}).",
    )
    parser.add_argument(
        "--check-fixture",
        type=Path,
        default=None,
        help="Optional expected-rows JSON fixture (e.g., solution/sql/fixtures/07_hero_expected_rows.json).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.list:
        if args.json:
            print(json.dumps({"queries": QUERY_CATALOG, "all_option": "all"}, indent=2))
        else:
            print("Available Hero API Blast-Radius GraphRAG Queries (--query A|B|C|all):")
            print("")
            for key in ("A", "B", "C"):
                meta = QUERY_CATALOG[key]
                print(f"  [{key}] {meta['title']}")
                print(f"      Graph: {meta['graph']} | Vector: {meta['vector_mode']} | Params: {', '.join(meta['params'])}")
                print(f"      {meta['description']}")
            print("")
            print("  [all] Execute/emit Queries A, B, and C sequentially.")
        return 0

    selected_keys = resolve_selected_keys(args.query)
    queries_sql = extract_hero_queries(args.sql_file)

    if args.emit_sql:
        for idx, key in enumerate(selected_keys):
            if idx > 0:
                print("\n")
            print(f"-- === {QUERY_CATALOG[key]['title']} ===")
            print(f"-- Parameters: @api_id='{args.api_id}', @search_query='{args.search_query}'")
            print(queries_sql[key] + ";")
        return 0

    if args.dry_run:
        payload = run_dry_run(
            selected_keys,
            queries_sql,
            project=args.project,
            instance_id=args.instance,
            database_id=args.database,
            spanner_region=args.region,
            vertex_location=args.vertex_location,
            embedding_model=args.embedding_model,
            api_id=args.api_id,
            search_query=args.search_query,
            check_fixture=args.check_fixture,
        )
    else:
        payload = run_live_queries(
            selected_keys,
            queries_sql,
            project=args.project,
            instance_id=args.instance,
            database_id=args.database,
            spanner_region=args.region,
            vertex_location=args.vertex_location,
            embedding_model=args.embedding_model,
            api_id=args.api_id,
            search_query=args.search_query,
            check_fixture=args.check_fixture,
        )

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        tgt = payload["spanner_target"]
        vai = payload["vertex_ai_embedding"]
        print(
            f"[{payload['status']}] Hero GraphRAG Runner ({payload['mode']}) -> "
            f"{tgt['project']}/{tgt['instance']}/{tgt['database']} ({tgt['instance_config']})"
        )
        print(
            f"  Read Mode  : {tgt['transaction_mode']} (INV-09 compliant) | "
            f"Vertex AI: {vai['location']}/{vai['model']} ({vai['dimensions']}-dim FLOAT32)"
        )
        print(f"  Target API : {args.api_id} | Query variant(s): {', '.join(selected_keys)}")
        if args.dry_run:
            rep = payload["offline_replay_summary"]
            print(
                f"  Offline Parity: {rep['impact_row_count']} impacted services, "
                f"{len(rep['governing_adrs'])} ADRs, {len(rep['applicable_requirements'])} REQs"
            )
            for idx, row in enumerate(rep["impact_rows"], start=1):
                flag = " [LEGACY_V1_AT_RISK]" if row["has_legacy_v1_risk"] else ""
                print(
                    f"    {idx}. {row['impacted_service_id']:18s} | hop={row['hop_distance']} | "
                    f"{row['impact_category']:19s} | {row['tier']:6s}/{row['criticality']:8s} | "
                    f"{row['owner_id']} ({row['slack_channel']}){flag}"
                )
            if rep["fixture_check"] is not None:
                fc = rep["fixture_check"]
                if fc["passed"]:
                    print(f"  [PASS] Fixture verified: {fc['fixture_path']}")
                else:
                    print(f"  [FAIL] Fixture mismatch: {fc['errors']}", file=sys.stderr)
        else:
            for key in selected_keys:
                qres = payload["queries"][key]
                print(f"\n  --- Query {key}: {qres['row_count']} row(s) ({qres['graph']}) ---")
                for idx, row in enumerate(qres["rows"], start=1):
                    flag = " [LEGACY_V1_AT_RISK]" if row.get("has_legacy_v1_risk") else ""
                    print(
                        f"    {idx}. {row.get('impacted_service_id', ''):18s} | "
                        f"hop={row.get('hop_distance')} | {row.get('impact_category', ''):19s} | "
                        f"{row.get('tier', ''):6s}/{row.get('criticality', ''):8s} | "
                        f"{row.get('owner_id', '')}{flag}"
                    )
                if qres["fixture_check"] is not None:
                    if qres["fixture_check"]["passed"]:
                        print(f"  [PASS] Query {key} matched expected 7-row fixture.")
                    else:
                        print(
                            f"  [FAIL] Query {key} fixture errors: {qres['fixture_check']['errors']}",
                            file=sys.stderr,
                        )

    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
