#!/usr/bin/env python3
"""Post-Load Orchestrator for Cloud Spanner Enterprise SDLC Knowledge Graph.

Executes the strict post-load lifecycle AFTER Path A (`neo4j_to_spanner_loader.py`)
and Path B (`unstructured_graphrag_ingest.py`) data loads complete:
  Stage 1: Execute `03a_seed_synonyms_and_metadata.sql` (DML transaction seeding
           `SdlcSynonymDict` custom FTS synonym dictionary and metadata).
  Stage 2: Render & apply `03b_remote_models.sql.tmpl` targeting Vertex AI
           regional endpoint **`asia-southeast1`** (Singapore; enforces INV-09
           so `gemini-embedding-001` NEVER targets `global`). Optionally applies
           `03c_optional_reranker_model.sql.tmpl` (`--include-reranker`) and
           `03d_backfill_embeddings_pdml.sql` (`--run-pdml-backfill`) before
           ScaNN vector index creation.
  Stage 3: Batch `03_indexes_fts_vector_postload.sql` into Cloud Spanner DDL
           Long-Running Operations (LROs) respecting Spanner's hard 10-backfill-
           statement batch cap (covering reverse indexes, FTS `CREATE SEARCH INDEX`,
           and ScaNN `CREATE VECTOR INDEX ... WHERE embedding IS NOT NULL`).
  Stage 4: Run `ANALYZE` as a DDL LRO so Cloud Spanner's query optimizer
           generates fresh statistics packages over the newly loaded graph and
           indexes.

Supports full stdlib-only `--dry-run` mode (zero third-party packages required).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Locked workshop defaults (Bangkok Spanner + Singapore Vertex AI)
DEFAULT_SPANNER_REGION = "asia-southeast3"
DEFAULT_SPANNER_INSTANCE = "sdlc-kg-enterprise"
DEFAULT_SPANNER_DATABASE = "sdlc_knowledge_graph"
DEFAULT_VERTEX_AI_LOCATION = "asia-southeast1"

# Cloud Spanner hard limit: max 10 backfill/validation statements per DDL batch
MAX_DDL_BACKFILL_STATEMENTS_PER_BATCH = 10
DEFAULT_LRO_TIMEOUT_SECONDS = 1800

# INV-09: Vertex AI predict-API text embedding models require a regional endpoint
FORBIDDEN_EMBEDDING_LOCATIONS = frozenset({"global", "us", "eu"})

# Canonical preview statements used ONLY in non-strict --dry-run if solution/sql/
# has not yet been generated on disk.
FALLBACK_DRY_RUN_PREVIEWS: dict[str, str] = {
    "03a_seed_synonyms_and_metadata.sql": """
INSERT OR UPDATE INTO SdlcSynonymDict (Key, Value) VALUES
  ('pci', ['payment card industry', 'cardholder data', 'tokenization']),
  ('mtls', ['mutual tls', 'zero trust', 'oidc']),
  ('fraud', ['risk scoring', 'fail-closed', 'velocity rules']),
  ('ledger', ['double-entry', 'sox', 'reconciliation']),
  ('charge', ['payment authorization', 'capture', 'tok_pci_v2']);
""".strip(),
    "03b_remote_models.sql.tmpl": """
CREATE OR REPLACE MODEL SdlcTextEmbedding
INPUT (
  content STRING(MAX),
  task_type STRING(MAX) OPTIONS (required = false)
)
OUTPUT (
  embeddings STRUCT<
    statistics STRUCT<truncated BOOL, token_count FLOAT64>,
    values ARRAY<FLOAT32>
  >
)
REMOTE OPTIONS (
  endpoint = '//aiplatform.googleapis.com/projects/${GOOGLE_CLOUD_PROJECT}/locations/${VERTEX_AI_LOCATION}/publishers/google/models/gemini-embedding-001',
  default_batch_size = 5
);
""".strip(),
    "03_indexes_fts_vector_postload.sql": """
CREATE INDEX IdxServiceCallsApiByApi
  ON ServiceCallsApi(api_id, service_id)
  STORING (call_mode, is_critical, migration_status, rps_estimate),
  INTERLEAVE IN Apis;

CREATE INDEX IdxServiceDependsOnReverse
  ON ServiceDependsOnService(to_service_id, from_service_id)
  STORING (dependency_type, is_sync, criticality);

CREATE SEARCH INDEX IdxDocumentChunksSearch
  ON DocumentChunks(content_tokens)
  STORING (doc_type, entity_id, source_path, section_heading);

CREATE VECTOR INDEX IdxDocumentChunksEmbedding
  ON DocumentChunks(embedding)
  STORING (doc_type, entity_id, source_path, section_heading)
  WHERE embedding IS NOT NULL
  OPTIONS (distance_type = 'COSINE');
""".strip(),
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


def validate_vertex_location(location: str) -> str:
    """Enforce INV-09: regional Vertex AI endpoint required for gemini-embedding-001."""
    normalized = location.strip().lower()
    if not normalized:
        raise ValueError("VERTEX_AI_LOCATION must not be empty.")
    if normalized in FORBIDDEN_EMBEDDING_LOCATIONS:
        raise ValueError(
            f"Invalid VERTEX_AI_LOCATION={location!r} for 03b remote embedding model: "
            "Vertex AI gemini-embedding-001 requires a regional endpoint such as "
            f"{DEFAULT_VERTEX_AI_LOCATION} (NEVER 'global', 'us', or 'eu')."
        )
    return normalized


def split_sql_statements(sql_text: str) -> list[str]:
    """Split GoogleSQL script into clean statements preserving quotes and stripping comments."""
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

        # Skip line comments outside strings
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

        # Handle triple-quoted strings first
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

        # Handle single/double/backtick quotes and backslash escapes
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

        # Statement terminator outside any quoted string
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


def render_sql_template(template_text: str, variables: dict[str, str]) -> str:
    """Substitute `${VAR}` placeholders and reject unresolved template variables."""
    rendered = template_text
    for key, value in variables.items():
        rendered = rendered.replace(f"${{{key}}}", value)
    unresolved = sorted(set(re.findall(r"\$\{[A-Z0-9_]+\}", rendered)))
    if unresolved:
        raise ValueError(f"Unresolved template variable(s) in SQL template: {unresolved}")
    return rendered


def validate_rendered_remote_model_sql(rendered_sql: str) -> None:
    """Guardrail: ensure gemini-embedding-001 endpoint never targets global/us/eu (INV-09)."""
    if "gemini-embedding-001" in rendered_sql:
        bad = re.search(
            r"/locations/(global|us|eu)/publishers/google/models/gemini-embedding-001",
            rendered_sql,
            re.IGNORECASE,
        )
        if bad:
            raise ValueError(
                f"INV-09 violation in rendered 03b DDL: gemini-embedding-001 cannot use "
                f"locations/{bad.group(1)} (must use regional endpoint e.g. asia-southeast1)."
            )


def validate_postload_index_statements(statements: list[str]) -> None:
    """Guardrail: ensure every CREATE VECTOR INDEX includes WHERE embedding IS NOT NULL (INV-08)."""
    for stmt in statements:
        if re.search(r"\bCREATE\s+VECTOR\s+INDEX\b", stmt, re.IGNORECASE):
            if not re.search(r"\bWHERE\b[^\n;]*\bembedding\s+IS\s+NOT\s+NULL\b", stmt, re.IGNORECASE):
                raise ValueError(
                    "INV-08 violation: CREATE VECTOR INDEX statement is missing "
                    "`WHERE embedding IS NOT NULL`."
                )


def batch_ddl_statements(
    statements: list[str],
    max_per_batch: int = MAX_DDL_BACKFILL_STATEMENTS_PER_BATCH,
) -> list[list[str]]:
    """Group DDL statements into LRO batches of <= 10 statements (Spanner backfill cap)."""
    if max_per_batch <= 0 or max_per_batch > MAX_DDL_BACKFILL_STATEMENTS_PER_BATCH:
        raise ValueError(
            f"max_per_batch must be in 1..{MAX_DDL_BACKFILL_STATEMENTS_PER_BATCH}, "
            f"got {max_per_batch}"
        )
    return [
        statements[i : i + max_per_batch]
        for i in range(0, len(statements), max_per_batch)
    ]


def _load_sql_file(
    sql_dir: Path,
    filename: str,
    dry_run: bool,
    strict: bool,
) -> tuple[str, str]:
    path = sql_dir / filename
    if path.is_file():
        return path.read_text(encoding="utf-8"), str(path)
    if dry_run and not strict and filename in FALLBACK_DRY_RUN_PREVIEWS:
        return FALLBACK_DRY_RUN_PREVIEWS[filename], f"fallback-preview:{filename}"
    raise FileNotFoundError(f"Required post-load SQL file not found: {path}")


def apply_postload(
    sql_dir: Path,
    project: str,
    instance_id: str = DEFAULT_SPANNER_INSTANCE,
    database_id: str = DEFAULT_SPANNER_DATABASE,
    spanner_region: str = DEFAULT_SPANNER_REGION,
    vertex_location: str = DEFAULT_VERTEX_AI_LOCATION,
    reranker_endpoint_id: str = "",
    include_reranker: bool = False,
    run_pdml_backfill: bool = False,
    max_ddl_batch_size: int = MAX_DDL_BACKFILL_STATEMENTS_PER_BATCH,
    lro_timeout_seconds: int = DEFAULT_LRO_TIMEOUT_SECONDS,
    dry_run: bool = False,
    strict: bool = False,
) -> dict[str, Any]:
    """Execute or dry-run the 4-stage post-load sequence (03a -> 03b -> 03 -> ANALYZE)."""
    validated_vertex_location = validate_vertex_location(vertex_location)
    effective_project = project or ("demo-project" if dry_run else "")
    if not effective_project:
        raise ValueError(
            "GOOGLE_CLOUD_PROJECT (or --project) is required for live post-load execution."
        )

    template_vars: dict[str, str] = {
        "GOOGLE_CLOUD_PROJECT": effective_project,
        "SPANNER_PROJECT": effective_project,
        "VERTEX_AI_LOCATION": validated_vertex_location,
        "VERTEX_AI_EMBEDDING_LOCATION": validated_vertex_location,
        "SPANNER_REGION": spanner_region,
        "GOOGLE_CLOUD_REGION": spanner_region,
        "SPANNER_INSTANCE": instance_id,
        "SPANNER_DATABASE": database_id,
    }
    if reranker_endpoint_id:
        template_vars["RERANKER_ENDPOINT_ID"] = reranker_endpoint_id
        template_vars["VERTEX_RERANKER_ENDPOINT_ID"] = reranker_endpoint_id

    # Stage 1: Load 03a DML seed statements
    sql_03a_raw, src_03a = _load_sql_file(
        sql_dir, "03a_seed_synonyms_and_metadata.sql", dry_run=dry_run, strict=strict
    )
    stmts_03a = split_sql_statements(sql_03a_raw)

    # Stage 2: Load and render 03b remote model DDL template (asia-southeast1)
    sql_03b_tmpl, src_03b = _load_sql_file(
        sql_dir, "03b_remote_models.sql.tmpl", dry_run=dry_run, strict=strict
    )
    sql_03b_rendered = render_sql_template(sql_03b_tmpl, template_vars)
    validate_rendered_remote_model_sql(sql_03b_rendered)
    stmts_03b = split_sql_statements(sql_03b_rendered)

    # Optional Stage 2b: Reranker remote model (03c)
    stmts_03c: list[str] = []
    src_03c: str | None = None
    if include_reranker:
        if not reranker_endpoint_id:
            raise ValueError("--reranker-endpoint-id is required with --include-reranker.")
        path_03c = sql_dir / "03c_optional_reranker_model.sql.tmpl"
        if not path_03c.is_file():
            raise FileNotFoundError(f"Optional reranker template not found: {path_03c}")
        src_03c = str(path_03c)
        rendered_03c = render_sql_template(
            path_03c.read_text(encoding="utf-8"), template_vars
        )
        stmts_03c = split_sql_statements(rendered_03c)

    # Optional Stage 2c: Throttled PDML embedding backfill (03d) BEFORE ScaNN index
    stmts_03d: list[str] = []
    src_03d: str | None = None
    if run_pdml_backfill:
        path_03d = sql_dir / "03d_backfill_embeddings_pdml.sql"
        if not path_03d.is_file():
            raise FileNotFoundError(f"PDML backfill script not found: {path_03d}")
        src_03d = str(path_03d)
        stmts_03d = split_sql_statements(path_03d.read_text(encoding="utf-8"))

    # Stage 3: Load & batch 03_indexes_fts_vector_postload.sql DDL LROs
    # Strip any trailing standalone ANALYZE statement so it executes cleanly
    # once in Stage 4 strictly AFTER all index DDL LRO batches finish.
    sql_03_idx_raw, src_03_idx = _load_sql_file(
        sql_dir, "03_indexes_fts_vector_postload.sql", dry_run=dry_run, strict=strict
    )
    all_03_stmts = split_sql_statements(sql_03_idx_raw)
    stmts_03_idx = [s for s in all_03_stmts if s.strip().upper() != "ANALYZE"]
    validate_postload_index_statements(stmts_03_idx)
    idx_ddl_batches = batch_ddl_statements(stmts_03_idx, max_per_batch=max_ddl_batch_size)

    # Stage 4: ANALYZE (Cloud Spanner GoogleSQL DDL LRO)
    analyze_statements = ["ANALYZE"]

    execution_log: list[dict[str, Any]] = []
    if not dry_run:
        if os.environ.get("SPANNER_EMULATOR_HOST"):
            print(
                f"[WARN] SPANNER_EMULATOR_HOST={os.environ['SPANNER_EMULATOR_HOST']} is active "
                "(note: emulator ignores ANALYZE and Vertex AI remote models).",
                file=sys.stderr,
            )
        try:
            from google.cloud import spanner  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "google-cloud-spanner is required for live execution. "
                "Install solution/pipelines/requirements.txt or pass --dry-run."
            ) from exc

        client = spanner.Client(project=effective_project)
        database = client.instance(instance_id).database(database_id)

        # Stage 1: Execute 03a DML inside a single read-write transaction
        if stmts_03a:
            def _run_seed_dml(txn: Any) -> int:
                rows_affected = 0
                for stmt in stmts_03a:
                    rows_affected += int(txn.execute_update(stmt))
                return rows_affected

            total_seeded = database.run_in_transaction(_run_seed_dml)
            execution_log.append(
                {
                    "stage": "stage_1_03a_seed",
                    "type": "DML",
                    "statements": len(stmts_03a),
                    "rows_affected": total_seeded,
                }
            )
            print(
                f"[STAGE 1] 03a seed committed ({len(stmts_03a)} DML stmt(s), "
                f"{total_seeded} row(s) affected)",
                file=sys.stderr,
            )

        # Stage 2: Apply 03b (+ optional 03c) remote model DDL LRO
        model_ddl = stmts_03b + stmts_03c
        if model_ddl:
            op = database.update_ddl(model_ddl)
            op.result(timeout=lro_timeout_seconds)
            execution_log.append(
                {
                    "stage": "stage_2_03b_remote_models",
                    "type": "DDL_LRO",
                    "statements": len(model_ddl),
                    "operation_id": getattr(op, "operation", None)
                    and getattr(op.operation, "name", ""),
                }
            )
            print(
                f"[STAGE 2] 03b remote model DDL LRO complete ({len(model_ddl)} stmt(s), "
                f"vertex_location={validated_vertex_location})",
                file=sys.stderr,
            )

        # Optional Stage 2c: Throttled PDML embedding backfill before ScaNN index
        if stmts_03d:
            pdml_rows = 0
            for stmt in stmts_03d:
                pdml_rows += int(database.execute_partitioned_dml(stmt))
            execution_log.append(
                {
                    "stage": "stage_2c_03d_pdml_backfill",
                    "type": "PARTITIONED_DML",
                    "statements": len(stmts_03d),
                    "rows_affected": pdml_rows,
                }
            )
            print(
                f"[STAGE 2c] 03d PDML embedding backfill complete ({pdml_rows} row(s) updated)",
                file=sys.stderr,
            )

        # Stage 3: Execute batched post-load index DDL LROs sequentially
        for batch_idx, batch_stmts in enumerate(idx_ddl_batches, start=1):
            op = database.update_ddl(batch_stmts)
            op.result(timeout=lro_timeout_seconds)
            execution_log.append(
                {
                    "stage": f"stage_3_postload_indexes_batch_{batch_idx}",
                    "type": "DDL_LRO",
                    "statements": len(batch_stmts),
                    "operation_id": getattr(op, "operation", None)
                    and getattr(op.operation, "name", ""),
                }
            )
            print(
                f"[STAGE 3] Post-load index DDL batch {batch_idx}/{len(idx_ddl_batches)} "
                f"complete ({len(batch_stmts)} stmt(s))",
                file=sys.stderr,
            )

        # Stage 4: Run ANALYZE DDL LRO
        op = database.update_ddl(analyze_statements)
        op.result(timeout=lro_timeout_seconds)
        execution_log.append(
            {
                "stage": "stage_4_analyze",
                "type": "DDL_LRO",
                "statements": 1,
                "operation_id": getattr(op, "operation", None)
                and getattr(op.operation, "name", ""),
            }
        )
        print("[STAGE 4] ANALYZE DDL LRO complete.", file=sys.stderr)

    return {
        "status": "DRY_RUN_OK" if dry_run else "POSTLOAD_APPLIED_OK",
        "mode": "dry-run" if dry_run else "live",
        "spanner_target": {
            "project": effective_project,
            "region": spanner_region,
            "instance_config": f"regional-{spanner_region}",
            "instance": instance_id,
            "database": database_id,
            "dialect": "GOOGLE_STANDARD_SQL",
        },
        "vertex_ai_remote_model": {
            "location": validated_vertex_location,
            "inv09_regional_verified": True,
            "rendered_preview": sql_03b_rendered,
        },
        "stages": {
            "stage_1_03a_seed": {
                "source": src_03a,
                "statement_count": len(stmts_03a),
                "execution_mode": "read_write_dml_transaction",
            },
            "stage_2_03b_remote_models": {
                "source": src_03b,
                "statement_count": len(stmts_03b),
                "optional_03c_source": src_03c,
                "optional_03c_statement_count": len(stmts_03c),
                "optional_03d_pdml_source": src_03d,
                "optional_03d_pdml_statement_count": len(stmts_03d),
                "execution_mode": "ddl_lro",
            },
            "stage_3_03_postload_indexes": {
                "source": src_03_idx,
                "statement_count": len(stmts_03_idx),
                "lro_batch_count": len(idx_ddl_batches),
                "max_statements_per_lro_batch": max_ddl_batch_size,
                "batch_sizes": [len(b) for b in idx_ddl_batches],
                "execution_mode": "batched_ddl_lro",
            },
            "stage_4_analyze": {
                "statements": analyze_statements,
                "execution_mode": "ddl_lro",
            },
        },
        "execution_log": execution_log,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv_stdlib(repo_root)

    default_sql_dir = repo_root / "solution" / "sql"
    default_spanner_region = (
        os.environ.get("SPANNER_REGION")
        or os.environ.get("GOOGLE_CLOUD_REGION")
        or DEFAULT_SPANNER_REGION
    )
    default_vertex_location = (
        os.environ.get("VERTEX_AI_EMBEDDING_LOCATION")
        or os.environ.get("VERTEX_AI_LOCATION")
        or DEFAULT_VERTEX_AI_LOCATION
    )
    default_project = _resolve_default_project()

    parser = argparse.ArgumentParser(
        description=(
            "Apply Cloud Spanner Enterprise post-load stages: 03a synonym seed, "
            "03b regional Vertex AI model (asia-southeast1), batched 03 index DDL LROs, and ANALYZE."
        )
    )
    parser.add_argument(
        "--sql-dir",
        type=Path,
        default=default_sql_dir,
        help="Directory containing 03a/03b/03 post-load SQL files (default: solution/sql).",
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
        default=default_spanner_region,
        help=f"Cloud Spanner region (default: {DEFAULT_SPANNER_REGION}).",
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
        "--vertex-location",
        type=str,
        default=default_vertex_location,
        help=(
            "Regional Vertex AI endpoint for 03b CREATE MODEL "
            f"(default: {DEFAULT_VERTEX_AI_LOCATION}; NEVER global)."
        ),
    )
    parser.add_argument(
        "--include-reranker",
        action="store_true",
        help="Also render & apply 03c_optional_reranker_model.sql.tmpl.",
    )
    parser.add_argument(
        "--reranker-endpoint-id",
        type=str,
        default=os.environ.get("RERANKER_ENDPOINT_ID", ""),
        help="Vertex AI deployed cross-encoder endpoint ID for 03c.",
    )
    parser.add_argument(
        "--run-pdml-backfill",
        action="store_true",
        help="Run 03d_backfill_embeddings_pdml.sql via Partitioned DML before ScaNN index creation.",
    )
    parser.add_argument(
        "--max-ddl-batch-size",
        type=int,
        default=MAX_DDL_BACKFILL_STATEMENTS_PER_BATCH,
        help=(
            "Max index DDL statements per Spanner LRO batch "
            f"(1..{MAX_DDL_BACKFILL_STATEMENTS_PER_BATCH}, default: {MAX_DDL_BACKFILL_STATEMENTS_PER_BATCH})."
        ),
    )
    parser.add_argument(
        "--lro-timeout-seconds",
        type=int,
        default=DEFAULT_LRO_TIMEOUT_SECONDS,
        help=f"Timeout in seconds per Spanner DDL LRO (default: {DEFAULT_LRO_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail even in --dry-run if any 03a/03b/03 SQL file is missing from --sql-dir.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render templates, validate invariants, and print LRO plan offline (stdlib only).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON summary.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    summary = apply_postload(
        sql_dir=args.sql_dir,
        project=args.project,
        instance_id=args.instance,
        database_id=args.database,
        spanner_region=args.region,
        vertex_location=args.vertex_location,
        reranker_endpoint_id=args.reranker_endpoint_id,
        include_reranker=args.include_reranker,
        run_pdml_backfill=args.run_pdml_backfill,
        max_ddl_batch_size=args.max_ddl_batch_size,
        lro_timeout_seconds=args.lro_timeout_seconds,
        dry_run=args.dry_run,
        strict=args.strict,
    )

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        tgt = summary["spanner_target"]
        st = summary["stages"]
        print(
            f"[{summary['status']}] Post-load orchestrator ({summary['mode']}) -> "
            f"{tgt['project']}/{tgt['instance']}/{tgt['database']} ({tgt['instance_config']})"
        )
        print(
            f"  Stage 1 (03a seed):         {st['stage_1_03a_seed']['statement_count']} DML stmt(s) "
            f"[{st['stage_1_03a_seed']['source']}]"
        )
        print(
            f"  Stage 2 (03b remote model): {st['stage_2_03b_remote_models']['statement_count']} DDL stmt(s) "
            f"in Vertex AI {summary['vertex_ai_remote_model']['location']} "
            f"[{st['stage_2_03b_remote_models']['source']}]"
        )
        print(
            f"  Stage 3 (03 indexes):       {st['stage_3_03_postload_indexes']['statement_count']} DDL stmt(s) "
            f"in {st['stage_3_03_postload_indexes']['lro_batch_count']} LRO batch(es) "
            f"(sizes={st['stage_3_03_postload_indexes']['batch_sizes']}) "
            f"[{st['stage_3_03_postload_indexes']['source']}]"
        )
        print("  Stage 4 (ANALYZE):          1 DDL LRO statement (`ANALYZE`)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
