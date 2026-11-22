#!/usr/bin/env python3
"""Path B Unstructured GraphRAG Ingestion (STARTER — Lab 3): Markdown -> Cloud Spanner.

Antigravity Skills for Lab 3:
  - Primary: .agents/skills/spanner-schema-and-data-modeling/SKILL.md
  - Support: .agents/skills/spanner-search-ai-and-graphrag/SKILL.md

Lab 3 Exercise:
  Implement `extract_chunks_and_mentions()` below to process all 45 Acme Commerce
  unstructured Markdown artifacts under `data/unstructured/`:
    1. Deterministically split each Markdown file on H2 (`## `) headings
       (preserving H1 title context and sub-chunking oversized sections via
       `_split_oversized_section()`).
    2. Build `DocumentChunks` dicts (`chunk_id`, `doc_type`, `entity_id`,
       `source_path`, `section_heading`, `chunk_index`, `content`,
       `content_hash`, `char_count`, `embedding`, `embedding_model_version`).
    3. Extract deduplicated canonical entity mentions using
       `ENTITY_MENTION_PATTERNS` (`api-*`, `svc-*`, `ADR-*`, `REQ-*`, `team-*`),
       validating against `load_canonical_entity_ids()` so `ChunkEntityMentions`
       bridges unstructured prose cleanly into `SdlcGraph` via `ChunkCites*View`.
    4. Keep `extract_chunks_and_mentions()` 100% Python stdlib-only so
       `--dry-run` works offline without third-party packages!

  Regional Guardrail (INV-09):
    Vertex AI `gemini-embedding-001` (768-dim FLOAT32) MUST target a regional
    endpoint such as Singapore (`asia-southeast1`, ~25 ms RTT from Bangkok
    `asia-southeast3`) — NEVER `global`!

Verify when done:
  python3 starter/pipelines/unstructured_graphrag_ingest.py --dry-run
  make TRACK=starter verify-lab LAB=3
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# Locked regional & model defaults (WORKSHOP_SPEC.json)
DEFAULT_SPANNER_REGION = "asia-southeast3"
DEFAULT_SPANNER_INSTANCE = "sdlc-kg-enterprise"
DEFAULT_SPANNER_DATABASE = "sdlc_knowledge_graph"

# Regional Vertex AI endpoint (Singapore — lowest latency from Bangkok)
DEFAULT_VERTEX_AI_LOCATION = "asia-southeast1"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768
EMBEDDING_MODEL_VERSION = "gemini-embedding-001-768"

# INV-09: Vertex AI predict-API text embedding models do NOT support global/us/eu
FORBIDDEN_EMBEDDING_LOCATIONS = frozenset({"global", "us", "eu"})

# Mutation & API batching limits
MAX_MUTATIONS_PER_COMMIT = 80_000
SAFE_MUTATION_HEADROOM = 60_000
DEFAULT_BATCH_SIZE = 200
DEFAULT_EMBED_BATCH_SIZE = 20
DEFAULT_MAX_CHUNK_CHARS = 1800
DEFAULT_CHUNK_OVERLAP_CHARS = 150

# Matches solution/mcp/tools.yaml `doc_type` allowedValues
DIR_TO_DOC_TYPE: dict[str, str] = {
    "services": "service",
    "apis": "api",
    "adrs": "adr",
    "requirements": "requirement",
    "owners": "owner",
}

DIR_TO_CHUNK_PREFIX: dict[str, str] = {
    "services": "svc",
    "apis": "api",
    "adrs": "adr",
    "requirements": "req",
    "owners": "owner",
}

# Deterministic regex extractors covering all 5 SDLC entity namespaces
ENTITY_MENTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("API", re.compile(r"\b(api-[a-z0-9-]+)\b")),
    ("SERVICE", re.compile(r"\b(svc-[a-z0-9-]+)\b")),
    ("ADR", re.compile(r"\b(ADR-\d{3})\b")),
    ("REQUIREMENT", re.compile(r"\b(REQ-\d{3})\b")),
    ("OWNER", re.compile(r"\b(team-[a-z0-9-]+)\b")),
]

# Core non-generated columns matching solution/sql/01_path_a_schematized.sql
CORE_DOCUMENT_CHUNK_COLUMNS: list[str] = [
    "chunk_id",
    "doc_type",
    "entity_id",
    "source_path",
    "section_heading",
    "chunk_index",
    "content",
    "content_hash",
    "embedding",
    "embedding_model_version",
]

CORE_MENTION_COLUMNS: list[str] = [
    "chunk_id",
    "entity_type",
    "entity_id",
    "mention_source",
]


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


def validate_vertex_embedding_location(location: str) -> str:
    """Enforce INV-09: gemini-embedding-001 requires a regional Vertex AI endpoint."""
    normalized = location.strip().lower()
    if not normalized:
        raise ValueError("vertex_ai_location must be a non-empty regional GCP location.")
    if normalized in FORBIDDEN_EMBEDDING_LOCATIONS:
        raise ValueError(
            f"Invalid VERTEX_AI_LOCATION={location!r} for {DEFAULT_EMBEDDING_MODEL}: "
            "Vertex AI predict-API text embedding models require a regional endpoint "
            f"(default: {DEFAULT_VERTEX_AI_LOCATION}). Reserve 'global' strictly for "
            "gemini-2.5-flash LLM synthesis."
        )
    return normalized


def load_canonical_entity_ids(structured_dir: Path | None) -> set[str] | None:
    """Load the canonical 68 entity IDs from data/structured/nodes/*.csv if available."""
    if structured_dir is None or not structured_dir.is_dir():
        return None
    node_files = [
        ("nodes/owners.csv", "owner_id"),
        ("nodes/services.csv", "service_id"),
        ("nodes/apis.csv", "api_id"),
        ("nodes/adrs.csv", "adr_id"),
        ("nodes/requirements.csv", "req_id"),
    ]
    valid_ids: set[str] = set()
    for relpath, pk_col in node_files:
        path = structured_dir / relpath
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get(pk_col):
                    valid_ids.add(row[pk_col].strip())
    return valid_ids


def _split_oversized_section(
    doc_title: str,
    body: str,
    max_chunk_chars: int,
    overlap_chars: int,
) -> list[str]:
    """Split a Markdown section on paragraph boundaries only if it exceeds max_chunk_chars."""
    enriched = body if body.startswith("# ") else f"# {doc_title}\n\n{body}"
    if len(enriched) <= max_chunk_chars:
        return [enriched]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    prefix = "" if body.startswith("# ") else f"# {doc_title}\n\n"
    parts: list[str] = []
    current = prefix

    for para in paragraphs:
        candidate = f"{current}{para}\n\n" if current.endswith("\n\n") else f"{current}\n\n{para}"
        if len(candidate.strip()) <= max_chunk_chars or current == prefix:
            current = candidate
        else:
            parts.append(current.strip())
            tail = current.strip()[-overlap_chars:] if overlap_chars > 0 else ""
            current = f"{prefix}{tail}\n\n{para}" if tail else f"{prefix}{para}"
    if current.strip() and current.strip() != prefix.strip():
        parts.append(current.strip())
    return parts or [enriched]


def extract_chunks_and_mentions(
    unstructured_dir: Path,
    repo_root: Path | None = None,
    structured_dir: Path | None = None,
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Parse all Path B Markdown files into DocumentChunks and ChunkEntityMentions.

    TODO(Lab 3): Implement Markdown chunking and entity mention extraction!
      Steps to complete:
        1. Discover all 45 Markdown files under `unstructured_dir` (`sorted(unstructured_dir.rglob("*.md"))`)
           and load canonical entity IDs via `load_canonical_entity_ids()`.
        2. For each file, map its parent folder via `DIR_TO_DOC_TYPE` and
           `DIR_TO_CHUNK_PREFIX`, extract the H1 document title, and split the
           body on H2 (`## `) section headings (using `_split_oversized_section()`
           for long sections).
           Note for `owners/team_charters.md` (`doc_type == "owner"`): skip its
           short H1 preamble (`heading == "Overview"` and `len(body) < 350`) and
           extract each section's `entity_id` from the H2 heading via
           `re.match(r"(team-[a-z0-9-]+)", heading)` (all other folders use `md_path.stem`).
        3. Emit deterministic `chunk_id` values (`<= 64` chars), SHA-256
           `content_hash`, `source_path` relative to `repo_root`, `section_heading`,
           `chunk_index`, `content`, `char_count`, `embedding: None`, and
           `embedding_model_version: None` (all keys in `CORE_DOCUMENT_CHUNK_COLUMNS`
           plus `char_count`).
        4. Run every regex in `ENTITY_MENTION_PATTERNS` over each chunk's content,
           filter matches against `canonical_ids`, and emit deduplicated
           `ChunkEntityMentions` dicts (`chunk_id`, `entity_type`, `entity_id`,
           `mention_source="REGEX_EXTRACT"` — matching `CORE_MENTION_COLUMNS`).
        5. Return `(chunks, mentions, stats)` where `stats` includes the exact keys
           accessed by `main()`:
           ```python
           {
               "markdown_files_processed": len(md_files),
               "total_chunks": len(chunks),
               "total_mentions": len(mentions),
               "max_chunk_chars": max((c["char_count"] for c in chunks), default=0),
               "unique_entities_linked": len({m["entity_id"] for m in mentions}),
               "chunks_by_doc_type": ...,
               "mentions_by_entity_type": ...,
           }
           ```
    """
    raise NotImplementedError(
        "TODO(Lab 3): implement markdown chunking and entity mention extraction"
    )


def generate_vertex_embeddings(
    chunks: list[dict[str, Any]],
    project: str,
    vertex_location: str = DEFAULT_VERTEX_AI_LOCATION,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    embed_batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
    max_retries: int = 4,
) -> int:
    """Generate 768-dim FLOAT32 embeddings via Vertex AI regional endpoint (asia-southeast1)."""
    validated_location = validate_vertex_embedding_location(vertex_location)
    if not project:
        raise ValueError(
            "GOOGLE_CLOUD_PROJECT (or --project) is required for Vertex AI embeddings."
        )

    try:
        from google import genai  # type: ignore[import-untyped]
        from google.genai import types  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "google-genai is required for live Vertex AI embeddings. "
            "Install solution/pipelines/requirements.txt or pass --dry-run / --skip-embeddings."
        ) from exc

    client = genai.Client(
        vertexai=True,
        project=project,
        location=validated_location,
    )

    embedded_count = 0
    for start in range(0, len(chunks), embed_batch_size):
        batch = chunks[start : start + embed_batch_size]
        texts = [c["content"] for c in batch]

        response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = client.models.embed_content(
                    model=embedding_model,
                    contents=texts,
                    config=types.EmbedContentConfig(
                        task_type="RETRIEVAL_DOCUMENT",
                        output_dimensionality=EMBEDDING_DIMENSIONS,
                    ),
                )
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == max_retries:
                    raise RuntimeError(
                        f"Vertex AI embedding batch {start}-{start + len(batch)} failed "
                        f"after {max_retries} attempts in {validated_location}: {exc}"
                    ) from exc
                backoff = min(2.0 ** attempt, 16.0)
                print(
                    f"[WARN] Vertex AI embed retry {attempt}/{max_retries} in {backoff:.1f}s: {exc}",
                    file=sys.stderr,
                )
                time.sleep(backoff)

        assert response is not None
        embeddings = response.embeddings or []
        if len(embeddings) != len(batch):
            raise RuntimeError(
                f"Vertex AI returned {len(embeddings)} embeddings for {len(batch)} chunks."
            )

        for chunk, emb in zip(batch, embeddings):
            values = [float(v) for v in emb.values]
            if len(values) != EMBEDDING_DIMENSIONS:
                raise ValueError(
                    f"Chunk {chunk['chunk_id']} embedding length {len(values)} != {EMBEDDING_DIMENSIONS}"
                )
            if not all(math.isfinite(v) for v in values):
                raise ValueError(
                    f"Chunk {chunk['chunk_id']} contains non-finite embedding values (INV-08)."
                )
            chunk["embedding"] = values
            chunk["embedding_model_version"] = f"{embedding_model}-{EMBEDDING_DIMENSIONS}"
            embedded_count += 1

    return embedded_count


def _compute_safe_batch_size(requested: int, num_columns: int) -> int:
    if requested <= 0:
        raise ValueError(f"batch_size must be positive, got {requested}")
    mutations_per_row = max(num_columns, 1) * 2
    return min(requested, max(1, SAFE_MUTATION_HEADROOM // mutations_per_row))


def ingest_unstructured_corpus(
    unstructured_dir: Path,
    project: str,
    instance_id: str = DEFAULT_SPANNER_INSTANCE,
    database_id: str = DEFAULT_SPANNER_DATABASE,
    spanner_region: str = DEFAULT_SPANNER_REGION,
    vertex_location: str = DEFAULT_VERTEX_AI_LOCATION,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
    embed_batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
    dry_run: bool = False,
    skip_embeddings: bool = False,
) -> dict[str, Any]:
    """Run Path B chunking, entity-mention extraction, embedding, and Spanner load."""
    validated_vertex_location = validate_vertex_embedding_location(vertex_location)
    repo_root = Path(__file__).resolve().parents[2]
    chunks, mentions, stats = extract_chunks_and_mentions(
        unstructured_dir=unstructured_dir,
        repo_root=repo_root,
    )

    chunk_batch_size = _compute_safe_batch_size(batch_size, len(CORE_DOCUMENT_CHUNK_COLUMNS))
    mention_batch_size = _compute_safe_batch_size(batch_size, len(CORE_MENTION_COLUMNS))
    chunk_commits_planned = (len(chunks) + chunk_batch_size - 1) // chunk_batch_size
    mention_commits_planned = (len(mentions) + mention_batch_size - 1) // mention_batch_size

    embedded_count = 0
    commits_executed = 0

    if not dry_run:
        if not project:
            raise ValueError(
                "GOOGLE_CLOUD_PROJECT (or --project) is required for live Path B ingestion."
            )
        if not skip_embeddings:
            embedded_count = generate_vertex_embeddings(
                chunks=chunks,
                project=project,
                vertex_location=validated_vertex_location,
                embedding_model=embedding_model,
                embed_batch_size=embed_batch_size,
            )

        try:
            from google.cloud import spanner  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError(
                "google-cloud-spanner is required for live writes. "
                "Install solution/pipelines/requirements.txt or pass --dry-run."
            ) from exc

        client = spanner.Client(project=project)
        database = client.instance(instance_id).database(database_id)

        for start in range(0, len(chunks), chunk_batch_size):
            slice_rows = chunks[start : start + chunk_batch_size]
            values = [tuple(row[col] for col in CORE_DOCUMENT_CHUNK_COLUMNS) for row in slice_rows]
            with database.batch() as batch:
                batch.insert_or_update(
                    table="DocumentChunks",
                    columns=CORE_DOCUMENT_CHUNK_COLUMNS,
                    values=values,
                )
            commits_executed += 1

        for start in range(0, len(mentions), mention_batch_size):
            slice_rows = mentions[start : start + mention_batch_size]
            values = [tuple(row[col] for col in CORE_MENTION_COLUMNS) for row in slice_rows]
            with database.batch() as batch:
                batch.insert_or_update(
                    table="ChunkEntityMentions",
                    columns=CORE_MENTION_COLUMNS,
                    values=values,
                )
            commits_executed += 1

    return {
        "status": "DRY_RUN_OK" if dry_run else "INGESTED_OK",
        "mode": "dry-run" if dry_run else "live",
        "spanner_target": {
            "project": project or "demo-project",
            "region": spanner_region,
            "instance_config": f"regional-{spanner_region}",
            "instance": instance_id,
            "database": database_id,
            "dialect": "GOOGLE_STANDARD_SQL",
        },
        "vertex_ai_embedding": {
            "model": embedding_model,
            "location": validated_vertex_location,
            "dimensions": EMBEDDING_DIMENSIONS,
            "spanner_column_type": f"ARRAY<FLOAT32>(vector_length=>{EMBEDDING_DIMENSIONS})",
            "task_type": "RETRIEVAL_DOCUMENT",
            "embedding_model_version": f"{embedding_model}-{EMBEDDING_DIMENSIONS}",
            "skip_embeddings": skip_embeddings,
            "embeddings_generated": embedded_count,
        },
        "stats": stats,
        "commit_plan": {
            "write_order": ["DocumentChunks", "ChunkEntityMentions"],
            "chunk_batch_size": chunk_batch_size,
            "mention_batch_size": mention_batch_size,
            "planned_commits": chunk_commits_planned + mention_commits_planned,
            "commits_executed": commits_executed,
            "max_mutations_per_commit_limit": MAX_MUTATIONS_PER_COMMIT,
        },
    }


def build_arg_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[2]
    load_dotenv_stdlib(repo_root)

    default_unstructured_dir = repo_root / "data" / "unstructured"
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
            "Ingest Path B Markdown corpus into Cloud Spanner DocumentChunks + "
            "ChunkEntityMentions with 768-dim FLOAT32 Vertex AI embeddings (asia-southeast1)."
        )
    )
    parser.add_argument(
        "--unstructured-dir",
        type=Path,
        default=default_unstructured_dir,
        help="Path to data/unstructured directory.",
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
            f"Regional Vertex AI endpoint for {DEFAULT_EMBEDDING_MODEL} "
            f"(default: {DEFAULT_VERTEX_AI_LOCATION}; NEVER global)."
        ),
    )
    parser.add_argument(
        "--embedding-model",
        type=str,
        default=DEFAULT_EMBEDDING_MODEL,
        help=f"Vertex AI embedding model ID (default: {DEFAULT_EMBEDDING_MODEL}).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Spanner rows per commit batch (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--embed-batch-size",
        type=int,
        default=DEFAULT_EMBED_BATCH_SIZE,
        help=f"Texts per Vertex AI embed_content RPC (default: {DEFAULT_EMBED_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--skip-embeddings",
        action="store_true",
        help="Write chunks with NULL embeddings (for demonstrating 03d PDML ML.PREDICT backfill).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run chunking, mention extraction, and regional guardrails offline (stdlib only).",
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

    summary = ingest_unstructured_corpus(
        unstructured_dir=args.unstructured_dir,
        project=args.project,
        instance_id=args.instance,
        database_id=args.database,
        spanner_region=args.region,
        vertex_location=args.vertex_location,
        embedding_model=args.embedding_model,
        batch_size=args.batch_size,
        embed_batch_size=args.embed_batch_size,
        dry_run=args.dry_run,
        skip_embeddings=args.skip_embeddings,
    )

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        st = summary["stats"]
        emb = summary["vertex_ai_embedding"]
        tgt = summary["spanner_target"]
        print(
            f"[{summary['status']}] Path B GraphRAG ingest ({summary['mode']}) -> "
            f"{tgt['project']}/{tgt['instance']}/{tgt['database']} ({tgt['instance_config']})"
        )
        print(
            f"  Corpus: {st['markdown_files_processed']} Markdown files -> "
            f"{st['total_chunks']} DocumentChunks (max {st['max_chunk_chars']} chars)"
        )
        print(
            f"  Mentions: {st['total_mentions']} ChunkEntityMentions linking "
            f"{st['unique_entities_linked']} unique entities"
        )
        print(
            f"  Embeddings: {emb['model']} ({emb['spanner_column_type']}) via "
            f"Vertex AI regional endpoint {emb['location']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
