#!/usr/bin/env python3
"""Path B Unstructured GraphRAG Ingestion: Markdown Docs -> Cloud Spanner Enterprise.

Processes all 45 Acme Commerce Platform unstructured Markdown artifacts under
`data/unstructured/` (`services/`, `apis/`, `adrs/`, `requirements/`, `owners/`):
  1. Splits each Markdown document deterministically on H2 headings (preserving
     H1 title context and sub-chunking oversized sections on paragraph bounds)
     into `DocumentChunks` rows (`chunk_id`, `doc_type`, `entity_id`,
     `source_path`, `section_heading`, `content`, `embedding`,
     `embedding_model_version`).
  2. Extracts deterministic canonical entity mentions (`api-*`, `svc-*`,
     `ADR-*`, `REQ-*`, `team-*`) into `ChunkEntityMentions` rows linking
     unstructured prose directly into `SdlcGraph` via `ChunkCites*View`.
  3. Generates 768-dimensional `FLOAT32` embeddings using Vertex AI
     `gemini-embedding-001` in regional **Singapore (`asia-southeast1`)** —
     lowest latency (~25 ms RTT) from the primary Bangkok (`asia-southeast3`)
     Cloud Spanner Enterprise instance (NEVER `global`, per INV-09).
  4. Commits `DocumentChunks` first, then `ChunkEntityMentions` second
     (parent-before-child for interleaving), chunked safely well below
     Spanner's 80,000 mutations/commit limit.

Zero-Dependency Dry-Run:
  Passing `--dry-run` runs 100% on the Python standard library (zero external
  packages imported), validates all 45 Markdown files, chunk IDs, entity links,
  regional Vertex AI guardrails, and commit batching plan.
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
from collections import Counter
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

    Returns ``(chunks, mentions, stats)`` using Python stdlib only.
    """
    if not unstructured_dir.is_dir():
        raise FileNotFoundError(f"Unstructured corpus directory not found: {unstructured_dir}")

    effective_root = repo_root or unstructured_dir.parent.parent
    canonical_ids = load_canonical_entity_ids(
        structured_dir if structured_dir is not None else (effective_root / "data" / "structured")
    )

    md_files = sorted(unstructured_dir.rglob("*.md"))
    if not md_files:
        raise ValueError(f"No Markdown files found under {unstructured_dir}")

    chunks: list[dict[str, Any]] = []
    mentions: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    unknown_mentions: set[str] = set()
    doc_type_counts: Counter[str] = Counter()
    entity_type_counts: Counter[str] = Counter()

    for md_path in md_files:
        folder = md_path.parent.name
        doc_type = DIR_TO_DOC_TYPE.get(folder, folder.rstrip("s"))
        chunk_prefix = DIR_TO_CHUNK_PREFIX.get(folder, doc_type)
        try:
            source_path = md_path.relative_to(effective_root).as_posix()
        except ValueError:
            source_path = md_path.as_posix()

        raw_text = md_path.read_text(encoding="utf-8").strip()
        if not raw_text:
            continue
        lines = raw_text.splitlines()
        doc_title = (
            lines[0].lstrip("#").strip() if lines and lines[0].startswith("#") else md_path.stem
        )

        # Segment by H2 (`## `) sections while keeping the H1 preamble intact
        sections: list[tuple[str, str]] = []
        current_heading = "Overview"
        current_lines: list[str] = []
        for line in lines:
            if line.startswith("## "):
                if current_lines:
                    sections.append((current_heading, "\n".join(current_lines).strip()))
                current_heading = line[3:].strip()
                current_lines = [line]
            else:
                current_lines.append(line)
        if current_lines:
            sections.append((current_heading, "\n".join(current_lines).strip()))

        chunk_ordinal = 0
        for sec_idx, (heading, body) in enumerate(sections):
            # team_charters.md has a 2-line H1 preamble followed by 8 team-* H2 sections
            if doc_type == "owner" and sec_idx == 0 and heading == "Overview" and len(body) < 350:
                continue

            if doc_type == "owner":
                team_match = re.match(r"(team-[a-z0-9-]+)", heading)
                primary_entity = team_match.group(1) if team_match else "team-platform"
                id_stem = primary_entity
            else:
                primary_entity = md_path.stem
                id_stem = md_path.stem

            sub_parts = _split_oversized_section(
                doc_title=doc_title,
                body=body,
                max_chunk_chars=max_chunk_chars,
                overlap_chars=overlap_chars,
            )

            for part_idx, content in enumerate(sub_parts):
                suffix = f"c{chunk_ordinal:02d}" if len(sub_parts) == 1 else f"c{chunk_ordinal:02d}_p{part_idx:02d}"
                raw_chunk_id = f"{chunk_prefix}__{id_stem}__{suffix}"
                if len(raw_chunk_id) > 64:
                    digest = hashlib.sha256(raw_chunk_id.encode("utf-8")).hexdigest()[:12]
                    chunk_id = f"{chunk_prefix}__{id_stem[:32]}__{digest}"[:64]
                else:
                    chunk_id = raw_chunk_id

                if chunk_id in seen_chunk_ids:
                    raise ValueError(f"Duplicate chunk_id generated: {chunk_id}")
                seen_chunk_ids.add(chunk_id)

                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "doc_type": doc_type,
                        "entity_id": primary_entity,
                        "source_path": source_path,
                        "section_heading": heading,
                        "chunk_index": chunk_ordinal,
                        "doc_title": doc_title,
                        "content": content,
                        "content_hash": content_hash,
                        "char_count": len(content),
                        "embedding": None,
                        "embedding_model_version": None,
                    }
                )
                doc_type_counts[doc_type] += 1

                # Extract deduplicated entity mentions per chunk (with occurrence counts)
                for entity_type, pattern in ENTITY_MENTION_PATTERNS:
                    matches = pattern.findall(content)
                    if not matches:
                        continue
                    counts = Counter(matches)
                    for entity_id in sorted(counts):
                        if canonical_ids is not None and entity_id not in canonical_ids:
                            unknown_mentions.add(entity_id)
                            continue
                        mentions.append(
                            {
                                "chunk_id": chunk_id,
                                "entity_type": entity_type,
                                "entity_id": entity_id,
                                "mention_source": "REGEX_EXTRACT",
                                "mention_count": counts[entity_id],
                            }
                        )
                        entity_type_counts[entity_type] += 1

                chunk_ordinal += 1

    stats = {
        "markdown_files_processed": len(md_files),
        "total_chunks": len(chunks),
        "total_mentions": len(mentions),
        "max_chunk_id_length": max((len(c["chunk_id"]) for c in chunks), default=0),
        "max_chunk_chars": max((c["char_count"] for c in chunks), default=0),
        "chunks_by_doc_type": dict(sorted(doc_type_counts.items())),
        "mentions_by_entity_type": dict(sorted(entity_type_counts.items())),
        "unique_entities_linked": len({m["entity_id"] for m in mentions}),
        "unknown_mentions_filtered": sorted(unknown_mentions),
    }
    return chunks, mentions, stats


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

        print(
            f"[EMBED] {validated_location}/{embedding_model}: "
            f"{embedded_count}/{len(chunks)} chunks embedded (768-dim FLOAT32)",
            file=sys.stderr,
        )

    return embedded_count


def _compute_safe_batch_size(requested: int, num_columns: int) -> int:
    if requested <= 0:
        raise ValueError(f"batch_size must be positive, got {requested}")
    mutations_per_row = max(num_columns, 1) * 2
    return min(requested, max(1, SAFE_MUTATION_HEADROOM // mutations_per_row))


def _introspect_live_schema(database: Any) -> tuple[list[str], list[str], dict[str, str]]:
    """Read live non-generated columns and ChunkCites*View entity_type literals."""
    chunk_cols = list(CORE_DOCUMENT_CHUNK_COLUMNS)
    mention_cols = list(CORE_MENTION_COLUMNS)
    entity_type_map = {
        "API": "API",
        "SERVICE": "SERVICE",
        "ADR": "ADR",
        "REQUIREMENT": "REQUIREMENT",
        "OWNER": "OWNER",
    }
    try:
        with database.snapshot(multi_use=True) as snapshot:
            col_rows = list(
                snapshot.execute_sql(
                    """
                    SELECT TABLE_NAME, COLUMN_NAME
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_NAME IN ('DocumentChunks', 'ChunkEntityMentions')
                      AND IS_GENERATED = 'NEVER'
                    ORDER BY ORDINAL_POSITION
                    """
                )
            )
            live_by_table: dict[str, list[str]] = {}
            for tbl, col in col_rows:
                live_by_table.setdefault(tbl, []).append(col)

            if "DocumentChunks" in live_by_table:
                candidate_chunk_keys = {
                    "chunk_id",
                    "doc_type",
                    "entity_id",
                    "source_path",
                    "section_heading",
                    "chunk_index",
                    "doc_title",
                    "content",
                    "content_hash",
                    "char_count",
                    "embedding",
                    "embedding_model_version",
                }
                matched = [c for c in live_by_table["DocumentChunks"] if c in candidate_chunk_keys]
                if matched:
                    chunk_cols = matched

            if "ChunkEntityMentions" in live_by_table:
                candidate_mention_keys = {
                    "chunk_id",
                    "entity_type",
                    "entity_id",
                    "mention_source",
                    "mention_count",
                }
                matched = [
                    c for c in live_by_table["ChunkEntityMentions"] if c in candidate_mention_keys
                ]
                if matched:
                    mention_cols = matched

            # Align entity_type values with deployed ChunkCites*View definitions if present
            view_rows = list(
                snapshot.execute_sql(
                    """
                    SELECT TABLE_NAME, VIEW_DEFINITION
                    FROM INFORMATION_SCHEMA.VIEWS
                    WHERE TABLE_NAME IN (
                      'ChunkCitesApiView', 'ChunkCitesServiceView',
                      'ChunkCitesAdrView', 'ChunkCitesReqView'
                    )
                    """
                )
            )
            view_to_key = {
                "ChunkCitesApiView": "API",
                "ChunkCitesServiceView": "SERVICE",
                "ChunkCitesAdrView": "ADR",
                "ChunkCitesReqView": "REQUIREMENT",
            }
            for view_name, view_def in view_rows:
                key = view_to_key.get(view_name)
                if not key or not view_def:
                    continue
                m = re.search(r"entity_type\s*=\s*'([^']+)'", view_def, re.IGNORECASE)
                if m:
                    entity_type_map[key] = m.group(1)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[WARN] Live schema introspection skipped (using core column defaults): {exc}",
            file=sys.stderr,
        )

    return chunk_cols, mention_cols, entity_type_map


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

    # Verify Hero scenario verifiable_unstructured_citations are covered if WORKSHOP_SPEC.json exists
    spec_path = repo_root / "WORKSHOP_SPEC.json"
    hero_citation_checks: dict[str, Any] = {"checked": False, "missing_sources": [], "missing_facts": []}
    if spec_path.is_file():
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        by_source: dict[str, str] = {}
        for chunk in chunks:
            by_source[chunk["source_path"]] = (
                by_source.get(chunk["source_path"], "") + "\n" + chunk["content"]
            )
        missing_sources: list[str] = []
        missing_facts: list[str] = []
        for cit in spec.get("hero_scenario", {}).get("verifiable_unstructured_citations", []):
            src = cit["source_path"]
            if src not in by_source:
                missing_sources.append(src)
                continue
            for fact in cit.get("key_facts", []):
                if fact not in by_source[src]:
                    missing_facts.append(f"{src}: {fact}")
        hero_citation_checks = {
            "checked": True,
            "verified_sources": len(
                spec.get("hero_scenario", {}).get("verifiable_unstructured_citations", [])
            )
            - len(missing_sources),
            "missing_sources": missing_sources,
            "missing_facts": missing_facts,
        }
        if missing_sources or missing_facts:
            raise RuntimeError(
                f"Hero citation verification failed: missing_sources={missing_sources}, "
                f"missing_facts={missing_facts}"
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

        if os.environ.get("SPANNER_EMULATOR_HOST"):
            print(
                f"[WARN] SPANNER_EMULATOR_HOST={os.environ['SPANNER_EMULATOR_HOST']} is active.",
                file=sys.stderr,
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
        chunk_cols, mention_cols, entity_type_map = _introspect_live_schema(database)

        # Stage 1: Commit DocumentChunks FIRST (parent table for ChunkEntityMentions)
        eff_chunk_batch = _compute_safe_batch_size(batch_size, len(chunk_cols))
        for start in range(0, len(chunks), eff_chunk_batch):
            slice_rows = chunks[start : start + eff_chunk_batch]
            values = [tuple(row[col] for col in chunk_cols) for row in slice_rows]
            with database.batch() as batch:
                batch.insert_or_update(
                    table="DocumentChunks",
                    columns=chunk_cols,
                    values=values,
                )
            commits_executed += 1
            print(
                f"[SPANNER] DocumentChunks: committed {start + len(slice_rows)}/{len(chunks)} rows",
                file=sys.stderr,
            )

        # Stage 2: Commit ChunkEntityMentions SECOND (child rows linked to DocumentChunks)
        eff_mention_batch = _compute_safe_batch_size(batch_size, len(mention_cols))
        for start in range(0, len(mentions), eff_mention_batch):
            slice_rows = mentions[start : start + eff_mention_batch]
            values = []
            for row in slice_rows:
                mapped = dict(row)
                mapped["entity_type"] = entity_type_map.get(
                    row["entity_type"], row["entity_type"]
                )
                values.append(tuple(mapped[col] for col in mention_cols))
            with database.batch() as batch:
                batch.insert_or_update(
                    table="ChunkEntityMentions",
                    columns=mention_cols,
                    values=values,
                )
            commits_executed += 1
            print(
                f"[SPANNER] ChunkEntityMentions: committed "
                f"{start + len(slice_rows)}/{len(mentions)} rows",
                file=sys.stderr,
            )

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
        "hero_citation_checks": hero_citation_checks,
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
            f"{st['total_chunks']} DocumentChunks (max {st['max_chunk_chars']} chars, "
            f"max chunk_id {st['max_chunk_id_length']} chars)"
        )
        print(
            f"  Mentions: {st['total_mentions']} ChunkEntityMentions linking "
            f"{st['unique_entities_linked']} unique entities ({st['mentions_by_entity_type']})"
        )
        print(
            f"  Embeddings: {emb['model']} ({emb['spanner_column_type']}) via "
            f"Vertex AI regional endpoint {emb['location']}"
        )
        if summary["hero_citation_checks"]["checked"]:
            print(
                f"  Hero Citations: verified all "
                f"{summary['hero_citation_checks']['verified_sources']} required Markdown sources & facts."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
