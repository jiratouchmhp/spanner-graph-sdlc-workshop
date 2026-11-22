#!/usr/bin/env python3
"""Multi-Statement Live SQL & ISO GQL Runner for Cloud Spanner Enterprise.

Parses, classifies, and executes any workshop SQL script (`01`..`11`, helper
scripts `03a`..`03d` / `04b`, `--file <path>`, or `--all-labs`) against Cloud
Spanner Enterprise in Bangkok (`asia-southeast3`) with Vertex AI regional
embeddings in Singapore (`asia-southeast1` — NEVER `global`, per INV-09).

Runtime Dependencies:
  - `--dry-run` / `--list`: 100% Python 3 standard library (zero pip packages,
    zero GCP credentials required).
  - Live execution: `google-cloud-spanner` (and `google-genai` whenever any
    executed query references `@query_vec` or `@query_embedding`).

Statement Classification & Automatic Routing:
  - DDL (`CREATE`, `ALTER`, `DROP`, `GRANT`, `REVOKE`, `ANALYZE`, `RENAME`):
      Batches consecutive DDL statements <= 10 per `database.update_ddl(...)`
      LRO (flushing before `ANALYZE`) and waits for LRO completion.
  - Partitioned DML (`@{pdml_...} UPDATE/DELETE` or `--partitioned-dml`):
      Routes to `database.execute_partitioned_dml(stmt)`.
  - DML (`INSERT`, `UPDATE`, `DELETE`, `MERGE`):
      Routes to `database.run_in_transaction(...)` using `txn.execute_update`
      (or `txn.execute_sql` when `THEN RETURN` is present).
  - Data Boost Graph Algorithms (`EXPORT DATA ...`):
      Executes outside any read-write transaction via a single-use read-only
      snapshot (`snapshot.execute_sql(...)`, which Spanner routes to Data Boost
      Graph Intelligence compute automatically without requiring a partition
      token) and verifies persisted rows in the destination table (e.g.,
      `ServiceAlgoProperty`).
  - Read-Only Queries (`SELECT`, `WITH`, `GRAPH`, `SHOW`, `TABLE`, `VALUES`):
      Executes consecutive read queries inside `database.snapshot(multi_use=True)`
      (INV-09: mandatory for Full-Text Search and `SPANNER_SYS.*` views) with
      auto-bound typed parameters (`STRING`, `INT64`, `FLOAT64`, `BOOL`,
      768-dim `ARRAY<FLOAT32>`).
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

# Locked workshop defaults aligned with WORKSHOP_SPEC.json
DEFAULT_SPANNER_REGION = "asia-southeast3"
DEFAULT_SPANNER_INSTANCE = "sdlc-kg-enterprise"
DEFAULT_SPANNER_DATABASE = "sdlc_knowledge_graph"
DEFAULT_VERTEX_AI_LOCATION = "asia-southeast1"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768
MAX_DDL_STATEMENTS_PER_LRO = 10
DEFAULT_LRO_TIMEOUT_SECONDS = 1800
DEFAULT_ALGO_TIMEOUT_SECONDS = 600
FORBIDDEN_EMBEDDING_LOCATIONS = frozenset({"global", "us", "eu"})

# Default query values
DEFAULT_API_ID = "api-payments-charge-v2"
DEFAULT_SERVICE_ID = "svc-payments"
DEFAULT_HUB_SERVICE_ID = "svc-auth"
DEFAULT_SEARCH_QUERY = "POST /v2/payments/charge tok_pci_v2 45ms 180-day freeze"
DEFAULT_BENCHMARK_05_SEARCH_QUERY = "pan freeze 45ms"
DEFAULT_TYPO_QUERY = "paymnts chrge idempoten"
DEFAULT_CALLER_EMAIL = "payments-eng@acme-commerce.example"

# Workshop SQL Catalog: 11 primary numbered scripts + 5 solution-only helper scripts
SQL_CATALOG: dict[str, dict[str, Any]] = {
    "01": {
        "filename": "01_path_a_schematized.sql",
        "title": "Path A Schematized Relational + SdlcGraph DDL",
        "has_starter": True,
        "is_helper": False,
        "lab": 3,
    },
    "02": {
        "filename": "02_path_b_schemaless_comparison.sql",
        "title": "Path B Schemaless SdlcDynamicGraph Comparison DDL",
        "has_starter": False,
        "is_helper": True,
        "lab": 3,
    },
    "03a": {
        "filename": "03a_seed_synonyms_and_metadata.sql",
        "title": "Seed SdlcSynonymDict & Apis.metadata JSON (DML)",
        "has_starter": False,
        "is_helper": True,
        "lab": 3,
    },
    "03b": {
        "filename": "03b_remote_models.sql.tmpl",
        "title": "Register Vertex AI SdlcTextEmbedding Remote Model (Template DDL)",
        "has_starter": False,
        "is_helper": True,
        "is_template": True,
        "lab": 3,
    },
    "03c": {
        "filename": "03c_optional_reranker_model.sql.tmpl",
        "title": "Register Optional SdlcReranker Remote Model (Template DDL)",
        "has_starter": False,
        "is_helper": True,
        "is_template": True,
        "optional": True,
        "lab": 5,
    },
    "03d": {
        "filename": "03d_backfill_embeddings_pdml.sql",
        "title": "Throttled Partitioned DML Embedding Backfill",
        "has_starter": False,
        "is_helper": True,
        "lab": 3,
    },
    "03": {
        "filename": "03_indexes_fts_vector_postload.sql",
        "title": "Post-Load Reverse, FTS & ScaNN Vector Indexes + ANALYZE",
        "has_starter": True,
        "is_helper": False,
        "lab": 3,
    },
    "04": {
        "filename": "04_fgac_roles_views.sql",
        "title": "FGAC Database Roles, Security Views & Least-Privilege Grants",
        "has_starter": True,
        "is_helper": False,
        "lab": 6,
    },
    "04b": {
        "filename": "04b_fgac_grants_rerunnable.sql",
        "title": "Re-Runnable Idempotent FGAC Grants",
        "has_starter": False,
        "is_helper": True,
        "lab": 6,
    },
    "05": {
        "filename": "05_search_strategies_benchmark.sql",
        "title": "Lab 4 Multi-Model Search Strategy Benchmarks (7 Queries)",
        "has_starter": True,
        "is_helper": False,
        "lab": 4,
    },
    "06": {
        "filename": "06_hybrid_rrf_rsf_filtered.sql",
        "title": "Lab 5 Hybrid RRF, RSF, Filtered Search & Reranker Queries",
        "has_starter": True,
        "is_helper": False,
        "lab": 5,
    },
    "07": {
        "filename": "07_hero_api_blast_radius_graphrag.sql",
        "title": "Lab 5 Hero API Blast-Radius GraphRAG Queries (A, B, C1, C2)",
        "has_starter": True,
        "is_helper": False,
        "lab": 5,
    },
    "08": {
        "filename": "08_graph_algorithms_criticality.sql",
        "title": "Lab 4 Data Boost Graph Algorithms (DDL + DML + EXPORT DATA + Queries)",
        "has_starter": True,
        "is_helper": False,
        "lab": 4,
    },
    "09": {
        "filename": "09_opencypher_to_gql_cookbook.sql",
        "title": "Lab 1 openCypher v9 -> Cloud Spanner ISO GQL Cookbook",
        "has_starter": True,
        "is_helper": False,
        "lab": 1,
    },
    "10": {
        "filename": "10_validate_migration_parity.sql",
        "title": "Lab 2/8 4-Tier Migration & Row-Hash Parity Validation",
        "has_starter": True,
        "is_helper": False,
        "lab": 2,
    },
    "11": {
        "filename": "11_observability_and_studio_viz.sql",
        "title": "Lab 8 Spanner Studio Visualization & SPANNER_SYS Observability",
        "has_starter": True,
        "is_helper": False,
        "lab": 8,
    },
}

ALL_LAB_KEYS: list[str] = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11"]
ALL_LAB_KEYS_WITH_HELPERS: list[str] = [
    "01",
    "02",
    "03a",
    "03b",
    "03d",
    "03",
    "04",
    "04b",
    "05",
    "06",
    "07",
    "08",
    "09",
    "10",
    "11",
]

DDL_LEADING_KEYWORDS = frozenset({"CREATE", "ALTER", "DROP", "GRANT", "REVOKE", "ANALYZE", "RENAME"})
DML_LEADING_KEYWORDS = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE"})
READ_LEADING_KEYWORDS = frozenset({"SELECT", "WITH", "GRAPH", "SHOW", "TABLE", "VALUES", "FROM"})
VECTOR_PARAM_NAMES = frozenset({"query_vec", "query_embedding"})

SAFE_SQL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# -----------------------------------------------------------------------------
# Environment & Validation Helpers (aligned with workshop .env conventions)
# -----------------------------------------------------------------------------

def _resolve_default_project() -> str:
    """Resolve GCP project preferring explicit SPANNER_PROJECT or GOOGLE_CLOUD_PROJECT."""
    unresolved_re = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")
    for var_name in ("SPANNER_PROJECT", "GOOGLE_CLOUD_PROJECT"):
        val = os.environ.get(var_name, "").strip()
        if val and not unresolved_re.search(val):
            return val
    return ""


def load_dotenv_stdlib(repo_root: Path) -> None:
    """Load key=value pairs from .env without overriding active environment variables."""
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


# -----------------------------------------------------------------------------
# GoogleSQL / ISO GQL Parser, Classifier & Parameter Extractor
# -----------------------------------------------------------------------------

def split_sql_statements(sql_text: str) -> list[str]:
    """Split a GoogleSQL / ISO GQL script into clean statements preserving quotes."""
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql_text)
    in_single = False
    in_double = False
    in_backtick = False
    in_triple_single = False
    in_triple_double = False

    while i < n:
        ch = sql_text[i]
        nxt = sql_text[i + 1] if i + 1 < n else ""
        tri = sql_text[i : i + 3]

        if not (in_single or in_double or in_backtick or in_triple_single or in_triple_double):
            if ch == "-" and nxt == "-":
                nl = sql_text.find("\n", i)
                if nl == -1:
                    break
                buf.append("\n")
                i = nl + 1
                continue
            if ch == "#":
                nl = sql_text.find("\n", i)
                if nl == -1:
                    break
                buf.append("\n")
                i = nl + 1
                continue
            if ch == "/" and nxt == "*":
                end_blk = sql_text.find("*/", i + 2)
                if end_blk == -1:
                    break
                buf.append(" ")
                i = end_blk + 2
                continue

        if not (in_single or in_double or in_backtick):
            if tri == "'''" and not in_triple_double:
                in_triple_single = not in_triple_single
                buf.append(tri)
                i += 3
                continue
            if tri == '"""' and not in_triple_single:
                in_triple_double = not in_triple_double
                buf.append(tri)
                i += 3
                continue

        if not (in_triple_single or in_triple_double):
            if ch == "\\" and (in_single or in_double) and i + 1 < n:
                buf.append(ch)
                buf.append(nxt)
                i += 2
                continue
            if ch == "'" and not (in_double or in_backtick):
                in_single = not in_single
                buf.append(ch)
                i += 1
                continue
            if ch == '"' and not (in_single or in_backtick):
                in_double = not in_double
                buf.append(ch)
                i += 1
                continue
            if ch == "`" and not (in_single or in_double):
                in_backtick = not in_backtick
                buf.append(ch)
                i += 1
                continue

        if ch == ";" and not (
            in_single or in_double or in_backtick or in_triple_single or in_triple_double
        ):
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    trailing = "".join(buf).strip()
    if trailing:
        statements.append(trailing)
    return statements


def _strip_quoted_literals(sql: str) -> str:
    """Replace single/double/triple/backtick quoted literals with empty quotes for safe scanning."""
    out: list[str] = []
    i = 0
    n = len(sql)
    in_single = in_double = in_backtick = in_tri_s = in_tri_d = False
    while i < n:
        ch = sql[i]
        tri = sql[i : i + 3]
        if not (in_single or in_double or in_backtick):
            if tri == "'''" and not in_tri_d:
                in_tri_s = not in_tri_s
                out.append("''")
                i += 3
                continue
            if tri == '"""' and not in_tri_s:
                in_tri_d = not in_tri_d
                out.append('""')
                i += 3
                continue
        if in_tri_s or in_tri_d:
            if ch == "\\" and i + 1 < n:
                i += 2
            else:
                i += 1
            continue
        if ch == "\\" and (in_single or in_double) and i + 1 < n:
            i += 2
            continue
        if ch == "'" and not (in_double or in_backtick):
            in_single = not in_single
            out.append("'")
            i += 1
            continue
        if ch == '"' and not (in_single or in_backtick):
            in_double = not in_double
            out.append('"')
            i += 1
            continue
        if ch == "`" and not (in_single or in_double):
            in_backtick = not in_backtick
            out.append("`")
            i += 1
            continue
        if in_single or in_double or in_backtick:
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_query_hints(sql_no_strings: str) -> str:
    """Remove Spanner @{key=val, ...} query/table hints so they are not mistaken for @params."""
    out: list[str] = []
    i = 0
    n = len(sql_no_strings)
    while i < n:
        if sql_no_strings[i] == "@" and i + 1 < n and sql_no_strings[i + 1] == "{":
            depth = 0
            j = i + 1
            while j < n:
                if sql_no_strings[j] == "{":
                    depth += 1
                elif sql_no_strings[j] == "}":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            out.append(" ")
            i = j
            continue
        out.append(sql_no_strings[i])
        i += 1
    return "".join(out)


def extract_sql_parameters(stmt: str) -> list[str]:
    """Extract sorted unique @param names from a comment-stripped SQL statement."""
    no_strings = _strip_quoted_literals(stmt)
    no_hints = _strip_query_hints(no_strings)
    return sorted(set(re.findall(r"(?<![A-Za-z0-9_@])@([A-Za-z_][A-Za-z0-9_]*)", no_hints)))


def _strip_leading_hints_and_parens(stmt: str) -> tuple[str, str]:
    """Return (leading_hints_text, remaining_sql_after_leading_hints_and_open_parens)."""
    s = stmt.lstrip()
    hints: list[str] = []
    while s.startswith("@{"):
        depth = 0
        end = -1
        for idx in range(1, len(s)):
            if s[idx] == "{":
                depth += 1
            elif s[idx] == "}":
                depth -= 1
                if depth == 0:
                    end = idx + 1
                    break
        if end == -1:
            break
        hints.append(s[:end])
        s = s[end:].lstrip()
    s_unwrapped = s.lstrip("(").lstrip()
    return " ".join(hints), s_unwrapped


@dataclass
class ClassifiedStatement:
    """Parsed and classified GoogleSQL / ISO GQL statement."""

    index: int
    kind: str  # DDL, PARTITIONED_DML, DML, EXPORT_DATA, READ_QUERY, CALL_PROCEDURE
    leading_keyword: str
    sql: str
    params: list[str] = field(default_factory=list)
    preview: str = ""
    has_then_return: bool = False
    is_analyze: bool = False
    export_target_table: str | None = None
    export_algo_run_id: str | None = None
    references_optional_reranker: bool = False


def classify_statement(
    stmt: str,
    index: int,
    *,
    force_partitioned_dml: bool = False,
) -> ClassifiedStatement:
    """Classify a single GoogleSQL / ISO GQL statement for exact Cloud Spanner API routing."""
    leading_hints, body = _strip_leading_hints_and_parens(stmt)
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", body)
    first_kw = m.group(1).upper() if m else ""
    params = extract_sql_parameters(stmt)
    preview = re.sub(r"\s+", " ", stmt).strip()
    if len(preview) > 110:
        preview = preview[:107] + "..."

    no_strings = _strip_quoted_literals(stmt)
    has_then_return = bool(re.search(r"\bTHEN\s+RETURN\b", no_strings, re.IGNORECASE))
    refs_reranker = bool(
        re.search(r"\bMODEL\s+SdlcReranker\b", no_strings, re.IGNORECASE)
    )

    if re.match(r"^EXPORT\s+DATA\b", body, re.IGNORECASE):
        tbl_m = re.search(r"\btable\s*=\s*'([^']+)'", stmt, re.IGNORECASE)
        run_m = re.search(r"'([^']+)'\s+AS\s+algo_run_id\b", stmt, re.IGNORECASE)
        return ClassifiedStatement(
            index=index,
            kind="EXPORT_DATA",
            leading_keyword="EXPORT DATA",
            sql=stmt,
            params=params,
            preview=preview,
            export_target_table=tbl_m.group(1) if tbl_m else None,
            export_algo_run_id=run_m.group(1) if run_m else None,
        )

    if first_kw in DDL_LEADING_KEYWORDS:
        return ClassifiedStatement(
            index=index,
            kind="DDL",
            leading_keyword=first_kw,
            sql=stmt,
            params=params,
            preview=preview,
            is_analyze=(first_kw == "ANALYZE"),
        )

    if first_kw in DML_LEADING_KEYWORDS:
        is_pdml = force_partitioned_dml or ("pdml_" in leading_hints.lower())
        return ClassifiedStatement(
            index=index,
            kind="PARTITIONED_DML" if is_pdml else "DML",
            leading_keyword=first_kw,
            sql=stmt,
            params=params,
            preview=preview,
            has_then_return=has_then_return,
        )

    if first_kw in READ_LEADING_KEYWORDS:
        return ClassifiedStatement(
            index=index,
            kind="READ_QUERY",
            leading_keyword=first_kw,
            sql=stmt,
            params=params,
            preview=preview,
            references_optional_reranker=refs_reranker,
        )

    if first_kw == "CALL":
        return ClassifiedStatement(
            index=index,
            kind="CALL_PROCEDURE",
            leading_keyword="CALL",
            sql=stmt,
            params=params,
            preview=preview,
        )

    raise ValueError(
        f"Unrecognized SQL statement #{index} leading keyword {first_kw!r}: {preview}"
    )


def render_sql_template(template_text: str, variables: dict[str, str]) -> str:
    """Substitute ${VAR} placeholders in .sql.tmpl files and reject unresolved variables."""
    rendered = template_text
    for key, value in variables.items():
        rendered = rendered.replace(f"${{{key}}}", value)
    unresolved = sorted(set(re.findall(r"\$\{[A-Z0-9_]+\}", rendered)))
    if unresolved:
        raise ValueError(f"Unresolved template variable(s): {unresolved}")
    return rendered


# -----------------------------------------------------------------------------
# File Resolution (--track starter|solution, keys 01..11, helpers, --file)
# -----------------------------------------------------------------------------

@dataclass
class ResolvedSqlTarget:
    """Resolved workshop or custom SQL file target."""

    key: str
    path: Path
    track_used: str
    fallback_to_solution: bool
    title: str
    is_template: bool = False


def normalize_lab_key(raw_key: str) -> str:
    """Normalize shorthand keys ('1', '01', '08_graph_algorithms_criticality.sql') to catalog keys."""
    cleaned = raw_key.strip()
    if cleaned in SQL_CATALOG:
        return cleaned
    low = cleaned.lower()
    if low in SQL_CATALOG:
        return low
    if cleaned.isdigit():
        padded = f"{int(cleaned):02d}"
        if padded in SQL_CATALOG:
            return padded
    # Match by filename or prefix (e.g., 08_graph_algorithms_criticality[.sql])
    for key, meta in SQL_CATALOG.items():
        fname = meta["filename"]
        if low in (fname.lower(), Path(fname).stem.lower()):
            return key
    raise ValueError(
        f"Unknown workshop SQL key {raw_key!r}. Valid keys: {', '.join(SQL_CATALOG)} "
        "(or pass --file <path>)."
    )


def resolve_sql_target(
    key: str,
    track: str,
    repo_root: Path,
) -> ResolvedSqlTarget:
    """Resolve a catalog key respecting --track starter|solution fallback rules."""
    canonical = normalize_lab_key(key)
    meta = SQL_CATALOG[canonical]
    filename = meta["filename"]
    solution_path = repo_root / "solution" / "sql" / filename
    starter_path = repo_root / "starter" / "sql" / filename

    if track == "solution":
        if not solution_path.is_file():
            raise FileNotFoundError(f"Solution SQL file not found: {solution_path}")
        return ResolvedSqlTarget(
            key=canonical,
            path=solution_path,
            track_used="solution",
            fallback_to_solution=False,
            title=meta["title"],
            is_template=bool(meta.get("is_template", False)),
        )

    # track == "starter":
    # Files with starter exercises MUST come strictly from starter/sql/.
    # Non-starter helper files (02, 03a..03d, 04b) cleanly fall back to solution/sql/.
    if meta["has_starter"]:
        if not starter_path.is_file():
            raise FileNotFoundError(
                f"Expected starter SQL file missing: {starter_path} "
                "(starter-owned labs do not silently fall back to solution/)"
            )
        return ResolvedSqlTarget(
            key=canonical,
            path=starter_path,
            track_used="starter",
            fallback_to_solution=False,
            title=meta["title"],
            is_template=bool(meta.get("is_template", False)),
        )

    if not solution_path.is_file():
        raise FileNotFoundError(f"Helper SQL file not found in solution/sql/: {solution_path}")
    return ResolvedSqlTarget(
        key=canonical,
        path=solution_path,
        track_used="solution",
        fallback_to_solution=True,
        title=meta["title"],
        is_template=bool(meta.get("is_template", False)),
    )


def resolve_custom_file_target(file_path: Path) -> ResolvedSqlTarget:
    """Resolve an arbitrary --file <path> SQL file."""
    resolved = file_path if file_path.is_absolute() else (Path.cwd() / file_path)
    if not resolved.is_file():
        # Also allow paths relative to REPO_ROOT if invoked from a subdirectory
        alt = REPO_ROOT / file_path
        if alt.is_file():
            resolved = alt
        else:
            raise FileNotFoundError(f"SQL file not found: {file_path}")
    return ResolvedSqlTarget(
        key=resolved.stem,
        path=resolved,
        track_used="custom",
        fallback_to_solution=False,
        title=f"Custom SQL file ({resolved.name})",
        is_template=resolved.name.endswith(".tmpl"),
    )


# -----------------------------------------------------------------------------
# Auto-Bound Typed Parameter Registry & Vertex AI Singapore Embedding Client
# -----------------------------------------------------------------------------

def build_parameter_registry(
    args: argparse.Namespace,
    *,
    file_key: str,
    explicit_search_query: bool,
) -> dict[str, tuple[str, Any]]:
    """Build typed parameter bindings (spanner_type_name, value) for a SQL file."""
    # Benchmark 05 uses 'pan freeze 45ms' to showcase SdlcSynonymDict unless overridden
    effective_search_query = args.search_query
    if not explicit_search_query and file_key == "05":
        effective_search_query = DEFAULT_BENCHMARK_05_SEARCH_QUERY

    registry: dict[str, tuple[str, Any]] = {
        "api_id": ("STRING", args.api_id),
        "service_id": ("STRING", args.service_id),
        "hub_service_id": ("STRING", args.hub_service_id),
        "owner_id": ("STRING", "team-payments"),
        "adr_id": ("STRING", "ADR-004"),
        "req_id": ("STRING", "REQ-001"),
        "search_query": ("STRING", effective_search_query),
        "query_text": ("STRING", effective_search_query),
        "typo_query": ("STRING", args.typo_query),
        "query_vec": (
            "ARRAY<FLOAT32>",
            f"<768-dim FLOAT32 embedding via {args.vertex_location}/{args.embedding_model}>",
        ),
        "query_embedding": (
            "ARRAY<FLOAT32>",
            f"<768-dim FLOAT32 embedding via {args.vertex_location}/{args.embedding_model}>",
        ),
        "caller_email": ("STRING", args.caller_email),
        "doc_type": ("STRING", ""),
        "domain": ("STRING", ""),
        "framework": ("STRING", ""),
        "limit": ("INT64", args.limit),
        "max_rows": ("INT64", 20),
        "top_k": ("INT64", args.limit),
        "k": ("INT64", args.k),
        "title": ("STRING", "Proposed GraphRAG Audit Policy"),
        "summary": ("STRING", "Standardize automated GraphRAG change reviews."),
    }

    # Apply explicit --param key=value (or key:TYPE=value) overrides
    for raw in args.param or []:
        if "=" not in raw:
            raise ValueError(f"Invalid --param {raw!r}: expected key=value or key:TYPE=value")
        lhs, rhs = raw.split("=", 1)
        lhs = lhs.strip().lstrip("@")
        if ":" in lhs:
            pname, ptype = lhs.split(":", 1)
            pname = pname.strip()
            ptype = ptype.strip().upper()
        else:
            pname = lhs
            ptype = registry[pname][0] if pname in registry else "STRING"
        if ptype == "INT64":
            val: Any = int(rhs)
        elif ptype == "FLOAT64":
            val = float(rhs)
        elif ptype == "BOOL":
            val = rhs.strip().lower() in ("true", "1", "yes")
        else:
            val = rhs
        registry[pname] = (ptype, val)

    return registry


def embed_query_vertex_ai(
    query_text: str,
    *,
    project: str,
    vertex_location: str,
    embedding_model: str,
    cache: dict[tuple[str, str, str], list[float]],
) -> list[float]:
    """Generate & cache a 768-dim FLOAT32 RETRIEVAL_QUERY vector via regional Vertex AI."""
    validated_loc = validate_vertex_location(vertex_location)
    cache_key = (query_text, validated_loc, embedding_model)
    if cache_key in cache:
        return cache[cache_key]

    try:
        from google import genai  # type: ignore[import-untyped]
        from google.genai import types  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "google-genai is required for live Vertex AI query embeddings. "
            "Run `make install` or pass `--dry-run`."
        ) from exc

    client = genai.Client(vertexai=True, project=project, location=validated_loc)
    response = client.models.embed_content(
        model=embedding_model,
        contents=[query_text],
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
    cache[cache_key] = values
    return values


def _spanner_param_type(spanner_mod: Any, type_name: str) -> Any:
    pt = spanner_mod.param_types
    if type_name == "STRING":
        return pt.STRING
    if type_name == "INT64":
        return pt.INT64
    if type_name == "FLOAT64":
        return pt.FLOAT64
    if type_name == "BOOL":
        return pt.BOOL
    if type_name == "ARRAY<FLOAT32>":
        return pt.Array(pt.FLOAT32)
    if type_name == "ARRAY<STRING>":
        return pt.Array(pt.STRING)
    return pt.STRING


# -----------------------------------------------------------------------------
# Execution Planner (Groups Consecutive DDL <= 10 & Consecutive READ_QUERY)
# -----------------------------------------------------------------------------

@dataclass
class ExecutionPhase:
    """Ordered execution phase preserving strict statement order inside each file."""

    phase_type: str  # DDL_LRO_BATCH, PARTITIONED_DML, DML_TXN, EXPORT_DATA_ALGO, READ_SNAPSHOT, CALL_PROCEDURE
    statements: list[ClassifiedStatement]


def plan_execution_phases(
    statements: list[ClassifiedStatement],
    *,
    max_ddl_batch_size: int = MAX_DDL_STATEMENTS_PER_LRO,
) -> list[ExecutionPhase]:
    """Group consecutive DDL (<= 10 per LRO, flushing before ANALYZE) and consecutive READs."""
    if max_ddl_batch_size <= 0 or max_ddl_batch_size > MAX_DDL_STATEMENTS_PER_LRO:
        raise ValueError(
            f"max_ddl_batch_size must be in 1..{MAX_DDL_STATEMENTS_PER_LRO}, "
            f"got {max_ddl_batch_size}"
        )

    phases: list[ExecutionPhase] = []
    idx = 0
    total = len(statements)

    while idx < total:
        st = statements[idx]
        if st.kind == "DDL":
            if st.is_analyze:
                phases.append(ExecutionPhase(phase_type="DDL_LRO_BATCH", statements=[st]))
                idx += 1
                continue
            ddl_group: list[ClassifiedStatement] = []
            while idx < total and statements[idx].kind == "DDL" and not statements[idx].is_analyze:
                ddl_group.append(statements[idx])
                idx += 1
            for start in range(0, len(ddl_group), max_ddl_batch_size):
                phases.append(
                    ExecutionPhase(
                        phase_type="DDL_LRO_BATCH",
                        statements=ddl_group[start : start + max_ddl_batch_size],
                    )
                )
        elif st.kind == "READ_QUERY":
            read_group: list[ClassifiedStatement] = []
            while idx < total and statements[idx].kind == "READ_QUERY":
                read_group.append(statements[idx])
                idx += 1
            phases.append(ExecutionPhase(phase_type="READ_SNAPSHOT", statements=read_group))
        elif st.kind == "PARTITIONED_DML":
            phases.append(ExecutionPhase(phase_type="PARTITIONED_DML", statements=[st]))
            idx += 1
        elif st.kind == "DML":
            dml_group: list[ClassifiedStatement] = []
            while idx < total and statements[idx].kind == "DML":
                dml_group.append(statements[idx])
                idx += 1
            phases.append(ExecutionPhase(phase_type="DML_TXN", statements=dml_group))
        elif st.kind == "EXPORT_DATA":
            phases.append(ExecutionPhase(phase_type="EXPORT_DATA_ALGO", statements=[st]))
            idx += 1
        else:
            phases.append(ExecutionPhase(phase_type="CALL_PROCEDURE", statements=[st]))
            idx += 1

    return phases


# -----------------------------------------------------------------------------
# Core Dry-Run & Live Execution Engine
# -----------------------------------------------------------------------------

def _bind_statement_params(
    stmt: ClassifiedStatement,
    registry: dict[str, tuple[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Return (bound_descriptor_map, missing_param_names) for a statement."""
    bound: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for pname in stmt.params:
        if pname not in registry:
            missing.append(pname)
        else:
            ptype, pval = registry[pname]
            bound[pname] = {"type": ptype, "value": pval}
    return bound, missing


def _execute_sql_with_optional_timeout(
    snapshot: Any,
    sql: str,
    *,
    params: dict[str, Any] | None = None,
    param_types: dict[str, Any] | None = None,
    timeout: int | None = None,
    data_boost_enabled: bool = False,
) -> Any:
    """Invoke snapshot.execute_sql passing optional kwargs only when accepted by the SDK."""
    kwargs: dict[str, Any] = {}
    if params:
        kwargs["params"] = params
    if param_types:
        kwargs["param_types"] = param_types
    try:
        sig = inspect.signature(snapshot.execute_sql)
        accepts_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        if timeout is not None and (accepts_var_kw or "timeout" in sig.parameters):
            kwargs["timeout"] = timeout
        if data_boost_enabled and (accepts_var_kw or "data_boost_enabled" in sig.parameters):
            kwargs["data_boost_enabled"] = True
    except (TypeError, ValueError):
        pass
    return snapshot.execute_sql(sql, **kwargs)


def _verify_export_target_rows(
    database: Any,
    spanner_mod: Any,
    target_table: str | None,
    algo_run_id: str | None,
) -> dict[str, Any] | None:
    """After EXPORT DATA finishes, verify persisted rows in the destination table."""
    if not target_table or not SAFE_SQL_IDENT_RE.match(target_table):
        return None
    verification: dict[str, Any] = {"table": target_table}
    with database.snapshot(multi_use=True) as snap:
        total_rows = list(snap.execute_sql(f"SELECT COUNT(*) FROM {target_table}"))
        verification["total_table_rows"] = int(total_rows[0][0]) if total_rows else 0
        if algo_run_id and target_table == "ServiceAlgoProperty":
            run_rows = list(
                snap.execute_sql(
                    "SELECT COUNT(*) FROM ServiceAlgoProperty WHERE algo_run_id = @algo_run_id",
                    params={"algo_run_id": algo_run_id},
                    param_types={"algo_run_id": spanner_mod.param_types.STRING},
                )
            )
            verification["algo_run_id"] = algo_run_id
            verification["rows_for_algo_run_id"] = int(run_rows[0][0]) if run_rows else 0
    return verification


def process_target_file(
    target: ResolvedSqlTarget,
    *,
    args: argparse.Namespace,
    explicit_search_query: bool,
    effective_project: str,
    validated_vertex_location: str,
    database: Any | None,
    spanner_mod: Any | None,
    embedding_cache: dict[tuple[str, str, str], list[float]],
) -> dict[str, Any]:
    """Parse, classify, validate bindings, and dry-run or execute a single SQL file."""
    raw_text = target.path.read_text(encoding="utf-8")
    if target.is_template:
        tmpl_vars = {
            "GOOGLE_CLOUD_PROJECT": effective_project,
            "SPANNER_PROJECT": effective_project,
            "VERTEX_AI_LOCATION": validated_vertex_location,
            "VERTEX_AI_EMBEDDING_LOCATION": validated_vertex_location,
            "SPANNER_REGION": args.region,
            "GOOGLE_CLOUD_REGION": args.region,
            "SPANNER_INSTANCE": args.instance,
            "SPANNER_DATABASE": args.database,
            "RERANKER_ENDPOINT_ID": os.environ.get("RERANKER_ENDPOINT_ID", "0000000000000000000"),
        }
        raw_text = render_sql_template(raw_text, tmpl_vars)

    raw_statements = split_sql_statements(raw_text)
    classified = [
        classify_statement(s, idx, force_partitioned_dml=args.partitioned_dml)
        for idx, s in enumerate(raw_statements, start=1)
    ]
    phases = plan_execution_phases(classified, max_ddl_batch_size=args.max_ddl_batch_size)
    registry = build_parameter_registry(
        args,
        file_key=target.key,
        explicit_search_query=explicit_search_query,
    )

    statement_reports: list[dict[str, Any]] = []
    unbound_errors: list[str] = []
    for st in classified:
        bound, missing = _bind_statement_params(st, registry)
        if missing:
            unbound_errors.append(
                f"{target.path.name} stmt #{st.index} has unbound parameter(s): {missing}"
            )
        if st.kind == "EXPORT_DATA" and st.params:
            unbound_errors.append(
                f"{target.path.name} stmt #{st.index} EXPORT DATA Graph Algorithm forbids @params: {st.params}"
            )
        statement_reports.append(
            {
                "index": st.index,
                "kind": st.kind,
                "leading_keyword": st.leading_keyword,
                "params": bound,
                "missing_params": missing,
                "export_target_table": st.export_target_table,
                "export_algo_run_id": st.export_algo_run_id,
                "references_optional_reranker": st.references_optional_reranker,
                "preview": st.preview,
            }
        )

    phase_plan_summary = [
        {
            "phase_index": p_idx,
            "phase_type": ph.phase_type,
            "statement_indices": [s.index for s in ph.statements],
            "statement_count": len(ph.statements),
        }
        for p_idx, ph in enumerate(phases, start=1)
    ]

    if unbound_errors:
        raise ValueError("; ".join(unbound_errors))

    live_results: list[dict[str, Any]] = []
    if not args.dry_run:
        assert database is not None and spanner_mod is not None

        for p_idx, ph in enumerate(phases, start=1):
            t0 = time.monotonic()

            if ph.phase_type == "DDL_LRO_BATCH":
                ddl_stmts = [s.sql for s in ph.statements]
                op = database.update_ddl(ddl_stmts)
                op.result(timeout=args.lro_timeout_seconds)
                live_results.append(
                    {
                        "phase_index": p_idx,
                        "phase_type": ph.phase_type,
                        "statement_indices": [s.index for s in ph.statements],
                        "status": "OK",
                        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
                    }
                )

            elif ph.phase_type == "PARTITIONED_DML":
                st = ph.statements[0]
                rows_affected = int(database.execute_partitioned_dml(st.sql))
                live_results.append(
                    {
                        "phase_index": p_idx,
                        "phase_type": ph.phase_type,
                        "statement_indices": [st.index],
                        "status": "OK",
                        "rows_affected": rows_affected,
                        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
                    }
                )

            elif ph.phase_type == "DML_TXN":
                def _run_dml_group(txn: Any) -> list[dict[str, Any]]:
                    out_items: list[dict[str, Any]] = []
                    for st in ph.statements:
                        p_vals: dict[str, Any] = {}
                        p_types: dict[str, Any] = {}
                        for pname in st.params:
                            ptype, pval = registry[pname]
                            p_vals[pname] = pval
                            p_types[pname] = _spanner_param_type(spanner_mod, ptype)
                        if st.has_then_return:
                            rs = txn.execute_sql(
                                st.sql,
                                params=p_vals or None,
                                param_types=p_types or None,
                            )
                            raw_rows = list(rs)
                            cols = [f.name for f in rs.fields]
                            out_items.append(
                                {
                                    "statement_index": st.index,
                                    "rows_returned": len(raw_rows),
                                    "sample_rows": [
                                        dict(zip(cols, r))
                                        for r in raw_rows[: args.max_rows_display]
                                    ],
                                }
                            )
                        else:
                            affected = int(
                                txn.execute_update(
                                    st.sql,
                                    params=p_vals or None,
                                    param_types=p_types or None,
                                )
                            )
                            out_items.append(
                                {
                                    "statement_index": st.index,
                                    "rows_affected": affected,
                                }
                            )
                    return out_items

                dml_items = database.run_in_transaction(_run_dml_group)
                live_results.append(
                    {
                        "phase_index": p_idx,
                        "phase_type": ph.phase_type,
                        "statement_indices": [s.index for s in ph.statements],
                        "status": "OK",
                        "details": dml_items,
                        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
                    }
                )

            elif ph.phase_type == "EXPORT_DATA_ALGO":
                st = ph.statements[0]
                # Execute EXPORT DATA outside any RW transaction via single-use read-only
                # snapshot; Spanner automatically dispatches Graph Algorithms to Data Boost.
                with database.snapshot() as snap:
                    rs = _execute_sql_with_optional_timeout(
                        snap,
                        st.sql,
                        timeout=args.algo_timeout_seconds,
                        data_boost_enabled=args.data_boost_enabled,
                    )
                    _ = list(rs)
                verification = _verify_export_target_rows(
                    database,
                    spanner_mod,
                    st.export_target_table,
                    st.export_algo_run_id,
                )
                live_results.append(
                    {
                        "phase_index": p_idx,
                        "phase_type": ph.phase_type,
                        "statement_indices": [st.index],
                        "status": "OK",
                        "target_verification": verification,
                        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
                    }
                )

            elif ph.phase_type == "READ_SNAPSHOT":
                # Pre-compute any required Vertex AI embeddings before opening snapshot
                resolved_vectors: dict[int, list[float]] = {}
                for st in ph.statements:
                    if any(p in VECTOR_PARAM_NAMES for p in st.params):
                        q_text = str(registry["search_query"][1])
                        resolved_vectors[st.index] = embed_query_vertex_ai(
                            q_text,
                            project=effective_project,
                            vertex_location=validated_vertex_location,
                            embedding_model=args.embedding_model,
                            cache=embedding_cache,
                        )

                query_outputs: list[dict[str, Any]] = []
                # INV-09: multi_use=True READ-ONLY snapshot for FTS, GQL, Vector & SPANNER_SYS
                with database.snapshot(multi_use=True) as snap:
                    for st in ph.statements:
                        qt0 = time.monotonic()
                        p_vals: dict[str, Any] = {}
                        p_types: dict[str, Any] = {}
                        for pname in st.params:
                            ptype, pval = registry[pname]
                            if pname in VECTOR_PARAM_NAMES:
                                p_vals[pname] = resolved_vectors[st.index]
                            else:
                                p_vals[pname] = pval
                            p_types[pname] = _spanner_param_type(spanner_mod, ptype)

                        try:
                            rs = _execute_sql_with_optional_timeout(
                                snap,
                                st.sql,
                                params=p_vals or None,
                                param_types=p_types or None,
                            )
                            raw_rows = list(rs)
                            cols = [f.name for f in rs.fields]
                            dict_rows = [
                                dict(zip(cols, r))
                                for r in raw_rows[: args.max_rows_display]
                            ]
                            query_outputs.append(
                                {
                                    "statement_index": st.index,
                                    "status": "OK",
                                    "row_count": len(raw_rows),
                                    "columns": cols,
                                    "rows": dict_rows,
                                    "elapsed_ms": round((time.monotonic() - qt0) * 1000, 1),
                                }
                            )
                        except Exception as exc:
                            err_msg = str(exc)
                            if (
                                st.references_optional_reranker
                                and not args.strict_optional
                                and (
                                    "SdlcReranker" in err_msg
                                    or "Model not found" in err_msg
                                    or "NOT_FOUND" in err_msg.upper()
                                )
                            ):
                                query_outputs.append(
                                    {
                                        "statement_index": st.index,
                                        "status": "SKIPPED_OPTIONAL_MODEL",
                                        "note": (
                                            "Optional model SdlcReranker is not deployed; "
                                            "proceeding to endpoint-free Query 4B fallback."
                                        ),
                                        "elapsed_ms": round((time.monotonic() - qt0) * 1000, 1),
                                    }
                                )
                            else:
                                raise

                live_results.append(
                    {
                        "phase_index": p_idx,
                        "phase_type": ph.phase_type,
                        "statement_indices": [s.index for s in ph.statements],
                        "status": "OK",
                        "queries": query_outputs,
                        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
                    }
                )

            else:  # CALL_PROCEDURE
                st = ph.statements[0]
                with database.snapshot() as snap:
                    rs = snap.execute_sql(st.sql)
                    raw_rows = list(rs)
                live_results.append(
                    {
                        "phase_index": p_idx,
                        "phase_type": ph.phase_type,
                        "statement_indices": [st.index],
                        "status": "OK",
                        "row_count": len(raw_rows),
                        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
                    }
                )

    counts_by_kind: dict[str, int] = {}
    for st in classified:
        counts_by_kind[st.kind] = counts_by_kind.get(st.kind, 0) + 1

    try:
        display_path = target.path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        display_path = str(target.path)

    return {
        "key": target.key,
        "title": target.title,
        "path": display_path,
        "track_used": target.track_used,
        "fallback_to_solution": target.fallback_to_solution,
        "statement_count": len(classified),
        "counts_by_kind": counts_by_kind,
        "phase_count": len(phases),
        "phases": phase_plan_summary,
        "statements": statement_reports,
        "live_execution": live_results if not args.dry_run else None,
    }


# -----------------------------------------------------------------------------
# CLI Parser & Terminal Output Formatting
# -----------------------------------------------------------------------------

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
            "Parse, classify, and execute Cloud Spanner Enterprise workshop SQL/GQL files "
            "(Bangkok asia-southeast3 Spanner + Singapore asia-southeast1 Vertex AI embeddings)."
        )
    )
    parser.add_argument(
        "labs",
        nargs="*",
        help=(
            "One or more workshop SQL keys to run (e.g., 01, 05, 08, 03a, 03b, 04b). "
            "Alternatively pass --file <path> or --all-labs."
        ),
    )
    parser.add_argument(
        "--file",
        dest="files",
        action="append",
        type=Path,
        default=[],
        help="Path to a specific SQL file to parse and execute (repeatable).",
    )
    parser.add_argument(
        "--all-labs",
        action="store_true",
        help="Select all 11 primary workshop SQL files (01..11).",
    )
    parser.add_argument(
        "--include-helpers",
        action="store_true",
        help="With --all-labs, also include non-starter helper SQL files (03a, 03b, 03d, 04b).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all cataloged workshop SQL files and exit.",
    )
    parser.add_argument(
        "--track",
        choices=("starter", "solution"),
        default="solution",
        help=(
            "Resolve lab SQL files from solution/sql/ (default) or starter/sql/ "
            "(falling back to solution/sql/ only for non-starter helpers 02, 03a..03d, 04b)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse files, classify every statement, verify parameter bindings, and exit 0 offline (stdlib only).",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=default_project,
        help="GCP project ID (defaults to SPANNER_PROJECT / GOOGLE_CLOUD_PROJECT).",
    )
    parser.add_argument(
        "--instance",
        type=str,
        default=os.environ.get("SPANNER_INSTANCE", DEFAULT_SPANNER_INSTANCE),
        help=f"Cloud Spanner instance ID (default: {DEFAULT_SPANNER_INSTANCE}).",
    )
    parser.add_argument(
        "--database",
        type=str,
        default=os.environ.get("SPANNER_DATABASE", DEFAULT_SPANNER_DATABASE),
        help=f"Cloud Spanner database ID (default: {DEFAULT_SPANNER_DATABASE}).",
    )
    parser.add_argument(
        "--region",
        type=str,
        default=default_region,
        help=f"Cloud Spanner region (default: {DEFAULT_SPANNER_REGION}).",
    )
    parser.add_argument(
        "--vertex-location",
        type=str,
        default=default_vertex_loc,
        help=f"Regional Vertex AI embedding location (default: {DEFAULT_VERTEX_AI_LOCATION}; NEVER global).",
    )
    parser.add_argument(
        "--embedding-model",
        type=str,
        default=os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        help=f"Vertex AI embedding model ID (default: {DEFAULT_EMBEDDING_MODEL}).",
    )
    parser.add_argument(
        "--database-role",
        type=str,
        default=None,
        help="Optional Cloud Spanner FGAC database role (e.g., sdlc_readonly_role).",
    )
    parser.add_argument(
        "--max-ddl-batch-size",
        type=int,
        default=MAX_DDL_STATEMENTS_PER_LRO,
        help=f"Max consecutive DDL statements per LRO (1..{MAX_DDL_STATEMENTS_PER_LRO}, default: {MAX_DDL_STATEMENTS_PER_LRO}).",
    )
    parser.add_argument(
        "--lro-timeout-seconds",
        type=int,
        default=DEFAULT_LRO_TIMEOUT_SECONDS,
        help=f"Timeout in seconds per DDL LRO (default: {DEFAULT_LRO_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--algo-timeout-seconds",
        type=int,
        default=DEFAULT_ALGO_TIMEOUT_SECONDS,
        help=f"Timeout in seconds per EXPORT DATA Graph Algorithm job (default: {DEFAULT_ALGO_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--partitioned-dml",
        action="store_true",
        help="Force DML statements to execute via database.execute_partitioned_dml().",
    )
    parser.add_argument(
        "--data-boost-enabled",
        action="store_true",
        help="Explicitly forward data_boost_enabled=True on EXPORT DATA execute_sql calls.",
    )
    parser.add_argument(
        "--strict-optional",
        action="store_true",
        help="Fail instead of skipping if optional MODEL SdlcReranker is not deployed (Lab 5 Query 4A).",
    )
    parser.add_argument(
        "--max-rows-display",
        type=int,
        default=20,
        help="Max rows per query displayed in terminal / JSON sample (default: 20).",
    )
    # Parameter overrides
    parser.add_argument("--api-id", default=DEFAULT_API_ID, help=f"Default @api_id (default: {DEFAULT_API_ID}).")
    parser.add_argument("--service-id", default=DEFAULT_SERVICE_ID, help=f"Default @service_id (default: {DEFAULT_SERVICE_ID}).")
    parser.add_argument("--hub-service-id", default=DEFAULT_HUB_SERVICE_ID, help=f"Default @hub_service_id (default: {DEFAULT_HUB_SERVICE_ID}).")
    parser.add_argument("--search-query", default=None, help="Override @search_query and @query_text.")
    parser.add_argument("--typo-query", default=DEFAULT_TYPO_QUERY, help=f"Default @typo_query (default: {DEFAULT_TYPO_QUERY!r}).")
    parser.add_argument("--caller-email", default=DEFAULT_CALLER_EMAIL, help=f"Default @caller_email (default: {DEFAULT_CALLER_EMAIL}).")
    parser.add_argument("--limit", type=int, default=10, help="Default @limit / @top_k (default: 10).")
    parser.add_argument("--k", type=int, default=5, help="Default @k (default: 5).")
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="Override/add any SQL parameter as key=value or key:TYPE=value (repeatable).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON execution plan or live results.",
    )
    return parser


def _print_human_report(payload: dict[str, Any]) -> None:
    tgt = payload["spanner_target"]
    vai = payload["vertex_ai_embedding"]
    totals = payload["totals"]
    print("=" * 80)
    print(
        f"[{payload['status']}] Cloud Spanner Live SQL Runner ({payload['mode']}, track={payload['track']})"
    )
    print(
        f"  Spanner Target : {tgt['project']}/{tgt['instance']}/{tgt['database']} "
        f"({tgt['instance_config']})"
        + (f" [role={tgt['database_role']}]" if tgt.get("database_role") else "")
    )
    print(
        f"  Vertex AI Embed: {vai['location']}/{vai['model']} "
        f"({vai['dimensions']}-dim FLOAT32, INV-09 regional verified)"
    )
    print(
        f"  Totals         : {totals['files']} file(s), {totals['statements']} statement(s) "
        f"({', '.join(f'{k}={v}' for k, v in sorted(totals['by_kind'].items()))})"
    )
    print("=" * 80)

    for f_rep in payload["files"]:
        fb = " [fallback->solution]" if f_rep["fallback_to_solution"] else ""
        print(
            f"\n[{f_rep['key']}] {f_rep['path']} ({f_rep['track_used']}{fb}) — "
            f"{f_rep['statement_count']} stmt(s) in {f_rep['phase_count']} execution phase(s)"
        )
        if f_rep["statement_count"] == 0:
            print("    (0 executable statements — starter TODO stubs pending completion)")
            continue

        for ph in f_rep["phases"]:
            s_range = (
                f"#{ph['statement_indices'][0]}"
                if len(ph["statement_indices"]) == 1
                else f"#{ph['statement_indices'][0]}..#{ph['statement_indices'][-1]}"
            )
            print(f"  Phase {ph['phase_index']}: {ph['phase_type']:17s} ({ph['statement_count']} stmt(s): {s_range})")

        for st in f_rep["statements"]:
            param_desc = ""
            if st["params"]:
                parts = []
                for pname, pmeta in st["params"].items():
                    val_str = (
                        "<768-dim FLOAT32>"
                        if pmeta["type"] == "ARRAY<FLOAT32>"
                        else repr(pmeta["value"])
                    )
                    parts.append(f"@{pname}:{pmeta['type']}={val_str}")
                param_desc = " | params: " + ", ".join(parts)
            algo_desc = ""
            if st["export_target_table"]:
                algo_desc = f" | sink={st['export_target_table']}"
                if st["export_algo_run_id"]:
                    algo_desc += f" ({st['export_algo_run_id']})"
            opt_desc = " [OPTIONAL: SdlcReranker]" if st["references_optional_reranker"] else ""
            print(
                f"    #{st['index']:02d} [{st['kind']:15s}] {st['preview']}"
                f"{param_desc}{algo_desc}{opt_desc}"
            )

        if f_rep["live_execution"]:
            print("  Live Execution Results:")
            for res in f_rep["live_execution"]:
                p_type = res["phase_type"]
                ms = res.get("elapsed_ms", 0.0)
                if p_type == "DDL_LRO_BATCH":
                    print(
                        f"    [OK] Phase {res['phase_index']} DDL LRO "
                        f"({len(res['statement_indices'])} stmt(s)) completed in {ms} ms"
                    )
                elif p_type == "PARTITIONED_DML":
                    print(
                        f"    [OK] Phase {res['phase_index']} Partitioned DML: "
                        f"{res['rows_affected']} row(s) updated in {ms} ms"
                    )
                elif p_type == "DML_TXN":
                    print(
                        f"    [OK] Phase {res['phase_index']} DML txn "
                        f"({len(res['details'])} stmt(s)) committed in {ms} ms"
                    )
                elif p_type == "EXPORT_DATA_ALGO":
                    ver = res.get("target_verification") or {}
                    ver_str = ""
                    if ver:
                        ver_str = f" | verified {ver.get('table')} total_rows={ver.get('total_table_rows')}"
                        if "rows_for_algo_run_id" in ver:
                            ver_str += f" ({ver['algo_run_id']}={ver['rows_for_algo_run_id']} rows)"
                    print(
                        f"    [OK] Phase {res['phase_index']} EXPORT DATA Graph Algo "
                        f"completed in {ms} ms{ver_str}"
                    )
                elif p_type == "READ_SNAPSHOT":
                    for q in res.get("queries", []):
                        if q["status"] == "SKIPPED_OPTIONAL_MODEL":
                            print(f"    [SKIP] Query #{q['statement_index']:02d}: {q['note']}")
                        else:
                            print(
                                f"    [OK] Query #{q['statement_index']:02d}: "
                                f"{q['row_count']} row(s) in {q['elapsed_ms']} ms"
                            )
                            for row in q["rows"][:5]:
                                print(f"         {json.dumps(row, default=str)}")


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    explicit_search_query = args.search_query is not None
    if args.search_query is None:
        args.search_query = DEFAULT_SEARCH_QUERY

    if args.list:
        if args.json:
            print(
                json.dumps(
                    {
                        "all_lab_keys": ALL_LAB_KEYS,
                        "all_lab_keys_with_helpers": ALL_LAB_KEYS_WITH_HELPERS,
                        "catalog": SQL_CATALOG,
                    },
                    indent=2,
                )
            )
        else:
            print("Cataloged Workshop SQL Files (`scripts/run_live_sql.py <key>`):")
            print(f"  {'Key':5s} | {'Track':15s} | {'Filename':38s} | Title")
            print(f"  {'-' * 5}-+-{'-' * 15}-+-{'-' * 38}-+-{'-' * 40}")
            for key, meta in SQL_CATALOG.items():
                track_lbl = "starter+sol" if meta["has_starter"] else "solution-helper"
                print(f"  {key:5s} | {track_lbl:15s} | {meta['filename']:38s} | {meta['title']}")
        return 0

    validated_vertex_location = validate_vertex_location(args.vertex_location)

    # Resolve selected targets
    targets: list[ResolvedSqlTarget] = []
    if args.all_labs:
        keys = ALL_LAB_KEYS_WITH_HELPERS if args.include_helpers else ALL_LAB_KEYS
        for k in keys:
            targets.append(resolve_sql_target(k, args.track, REPO_ROOT))
    for k in args.labs:
        targets.append(resolve_sql_target(k, args.track, REPO_ROOT))
    for fpath in args.files:
        targets.append(resolve_custom_file_target(fpath))

    if not targets:
        if args.dry_run:
            for k in ALL_LAB_KEYS:
                targets.append(resolve_sql_target(k, args.track, REPO_ROOT))
        else:
            parser.error(
                "Specify one or more SQL keys (e.g. 05 08), --file <path>, or --all-labs "
                "(or pass --list / --dry-run)."
            )

    effective_project = args.project or ("demo-project" if args.dry_run else "")
    if not effective_project:
        raise ValueError(
            "GOOGLE_CLOUD_PROJECT (or --project) is required for live Cloud Spanner execution."
        )

    database = None
    spanner_mod = None
    if not args.dry_run:
        try:
            from google.cloud import spanner as spanner_mod  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "google-cloud-spanner is required for live execution. "
                "Run `make install` or pass `--dry-run`."
            ) from exc

        client = spanner_mod.Client(project=effective_project)
        instance = client.instance(args.instance)
        db_kwargs: dict[str, Any] = {}
        if args.database_role:
            db_kwargs["database_role"] = args.database_role
        database = instance.database(args.database, **db_kwargs)

    embedding_cache: dict[tuple[str, str, str], list[float]] = {}
    file_reports: list[dict[str, Any]] = []
    totals_by_kind: dict[str, int] = {}
    total_statements = 0

    for target in targets:
        rep = process_target_file(
            target,
            args=args,
            explicit_search_query=explicit_search_query,
            effective_project=effective_project,
            validated_vertex_location=validated_vertex_location,
            database=database,
            spanner_mod=spanner_mod,
            embedding_cache=embedding_cache,
        )
        file_reports.append(rep)
        total_statements += rep["statement_count"]
        for k, v in rep["counts_by_kind"].items():
            totals_by_kind[k] = totals_by_kind.get(k, 0) + v

    payload = {
        "status": "DRY_RUN_OK" if args.dry_run else "LIVE_OK",
        "mode": "dry-run" if args.dry_run else "live",
        "track": args.track,
        "spanner_target": {
            "project": effective_project,
            "region": args.region,
            "instance_config": f"regional-{args.region}",
            "instance": args.instance,
            "database": args.database,
            "dialect": "GOOGLE_STANDARD_SQL",
            "database_role": args.database_role,
        },
        "vertex_ai_embedding": {
            "location": validated_vertex_location,
            "model": args.embedding_model,
            "dimensions": EMBEDDING_DIMENSIONS,
            "inv09_regional_verified": True,
        },
        "totals": {
            "files": len(file_reports),
            "statements": total_statements,
            "by_kind": totals_by_kind,
        },
        "files": file_reports,
    }

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_human_report(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
