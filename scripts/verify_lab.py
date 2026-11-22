#!/usr/bin/env python3
"""Offline Lab Verification Runner for Cloud Spanner Enterprise SDLC Workshop.

Backs `make TRACK=starter|solution verify-lab LAB=1..8|all` and
`make verify-starter-stubs` using ONLY the Python 3 standard library
(zero external packages and zero GCP credentials required).

Gives participants instant green/red feedback as they solve the 15 `TODO(Lab 1-8)`
exercises in `starter/` and compares deterministically against `solution/`.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ALL_LABS = (1, 2, 3, 4, 5, 6, 7, 8)
TODO_MARKER_RE = re.compile(r"TODO\(\s*Lab\s*[1-8]\s*\)", re.IGNORECASE)


def strip_sql_comments(sql_text: str) -> str:
    """Remove -- line comments and /* ... */ block comments while keeping strings intact."""
    out: list[str] = []
    i = 0
    n = len(sql_text)
    in_single = False
    in_double = False
    in_backtick = False
    while i < n:
        ch = sql_text[i]
        nxt = sql_text[i + 1] if i + 1 < n else ""
        if not (in_single or in_double or in_backtick):
            if ch == "-" and nxt == "-":
                nl = sql_text.find("\n", i)
                if nl == -1:
                    break
                out.append("\n")
                i = nl + 1
                continue
            if ch == "/" and nxt == "*":
                end = sql_text.find("*/", i + 2)
                if end == -1:
                    break
                out.append(" ")
                i = end + 2
                continue
        if ch == "\\" and (in_single or in_double) and i + 1 < n:
            out.append(ch)
            out.append(nxt)
            i += 2
            continue
        if ch == "'" and not (in_double or in_backtick):
            in_single = not in_single
        elif ch == '"' and not (in_single or in_backtick):
            in_double = not in_double
        elif ch == "`" and not (in_single or in_double):
            in_backtick = not in_backtick
        out.append(ch)
        i += 1
    return "".join(out)


def _read_text(path: Path, failures: list[str]) -> str | None:
    if not path.is_file():
        failures.append(f"Missing file: {path.relative_to(REPO_ROOT)}")
        return None
    return path.read_text(encoding="utf-8")


def _assert_no_todo(text: str, relpath: str, failures: list[str]) -> None:
    matches = TODO_MARKER_RE.findall(text)
    if matches:
        failures.append(f"{relpath} still contains {len(matches)} unfinished TODO marker(s) ({matches[0]})")


def _note_if_clean(before_count: int, failures: list[str], notes: list[str], msg: str) -> None:
    if len(failures) == before_count:
        notes.append(msg)


def _assert_patterns(
    text: str,
    relpath: str,
    patterns: list[tuple[str, str]],
    failures: list[str],
) -> None:
    for label, pattern in patterns:
        if not re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            failures.append(f"{relpath} missing required construct: {label}")


def _run_py(cmd: list[str], label: str, failures: list[str]) -> subprocess.CompletedProcess[str] | None:
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        err_snippet = (proc.stderr or proc.stdout).strip().splitlines()
        tail = " | ".join(err_snippet[-3:]) if err_snippet else f"exit code {proc.returncode}"
        failures.append(f"{label} failed (rc={proc.returncode}): {tail}")
        return None
    return proc


def _parse_yaml_scalar(raw_val: str) -> Any:
    """Parse a simple YAML scalar or inline empty dict/list using stdlib only."""
    vs = raw_val.strip()
    if vs == "{}":
        return {}
    if vs == "[]":
        return []
    if vs.startswith("[") and vs.endswith("]"):
        inner = vs[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_scalar(part) for part in inner.split(",")]
    if (vs.startswith('"') and vs.endswith('"') and len(vs) >= 2):
        # Handle basic double-quoted YAML escapes (e.g. \\. -> \.)
        return vs[1:-1].replace("\\\\", "\\").replace('\\"', '"')
    if (vs.startswith("'") and vs.endswith("'") and len(vs) >= 2):
        return vs[1:-1].replace("''", "'")
    low = vs.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", vs):
        return int(vs)
    return vs


def _parse_yaml_stdlib_fallback(text: str) -> dict[str, Any]:
    """Self-contained stdlib YAML parser fallback for MCP Toolbox tools.yaml.

    Works with zero third-party packages and zero dependency on `solution/`,
    handling both 0-offset and 2-space indented sequences, inline `[]`/`{}`,
    literal/folded block scalars (`|`/`>-`), `allowedValues`, `authRequired`,
    `authServices`, `templateParameters`, `valueFromParam`, and `embeddedBy`.
    """
    result: dict[str, Any] = {
        "sources": {},
        "embeddingModels": {},
        "authServices": {},
        "tools": {},
        "toolsets": {},
    }
    section: str | None = None
    cur_item: str | None = None
    cur_sub: str | None = None
    cur_param: dict[str, Any] | None = None
    block_field: str | None = None
    block_indent: int = 0
    block_lines: list[str] = []
    param_item_indent: int | None = None
    param_prop_indent: int | None = None
    param_list_key: str | None = None
    auth_item_indent: int | None = None

    def _flush_block() -> None:
        nonlocal block_field, block_lines
        if block_field and section == "tools" and cur_item and cur_item in result["tools"]:
            joiner = "\n" if block_field == "statement" else " "
            result["tools"][cur_item][block_field] = joiner.join(block_lines).strip()
        block_field = None
        block_lines = []

    for raw_line in text.splitlines():
        if block_field is not None:
            if not raw_line.strip():
                if block_field == "statement":
                    block_lines.append("")
                continue
            cur_indent = len(raw_line) - len(raw_line.lstrip(" "))
            if cur_indent >= block_indent:
                block_lines.append(raw_line[block_indent:] if block_field == "statement" else raw_line.strip())
                continue
            _flush_block()

        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        # Strip trailing inline comments outside quotes
        if "#" in stripped and not stripped.startswith("- "):
            in_q: str | None = None
            cut_idx: int | None = None
            for idx, ch in enumerate(stripped):
                if ch in ("'", '"'):
                    if in_q is None:
                        in_q = ch
                    elif in_q == ch:
                        in_q = None
                elif ch == "#" and in_q is None and (idx == 0 or stripped[idx - 1].isspace()):
                    cut_idx = idx
                    break
            if cut_idx is not None:
                stripped = stripped[:cut_idx].rstrip()
                if not stripped:
                    continue

        if indent == 0 and ":" in stripped:
            key, rest = stripped.split(":", 1)
            section = key.strip()
            cur_item = None
            cur_sub = None
            cur_param = None
            param_item_indent = None
            param_prop_indent = None
            param_list_key = None
            auth_item_indent = None
            rest_s = rest.strip()
            if rest_s:
                result[section] = _parse_yaml_scalar(rest_s)
            else:
                result.setdefault(section, {})
            continue

        if section in ("sources", "embeddingModels", "authServices"):
            if not isinstance(result.get(section), dict):
                result[section] = {}
            if indent == 2 and stripped.endswith(":"):
                cur_item = stripped[:-1].strip()
                result[section][cur_item] = {}
            elif indent == 4 and cur_item and ":" in stripped:
                k, v = stripped.split(":", 1)
                result[section][cur_item][k.strip()] = _parse_yaml_scalar(v)
        elif section == "toolsets":
            if not isinstance(result.get("toolsets"), dict):
                result["toolsets"] = {}
            if indent == 2 and not stripped.startswith("- ") and ":" in stripped:
                k, v = stripped.split(":", 1)
                cur_item = k.strip()
                result["toolsets"][cur_item] = _parse_yaml_scalar(v) if v.strip() else []
            elif cur_item and stripped.startswith("- ") and indent in (2, 4):
                result["toolsets"][cur_item].append(_parse_yaml_scalar(stripped[2:]))
        elif section == "tools":
            if not isinstance(result.get("tools"), dict):
                result["tools"] = {}
            if indent == 2 and not stripped.startswith("- ") and stripped.endswith(":"):
                cur_item = stripped[:-1].strip()
                result["tools"][cur_item] = {"parameters": [], "authRequired": []}
                cur_sub = None
                cur_param = None
                param_item_indent = None
                param_prop_indent = None
                param_list_key = None
                auth_item_indent = None
            elif cur_item and cur_sub == "authRequired" and stripped.startswith("- ") and indent in (4, 6):
                result["tools"][cur_item]["authRequired"].append(_parse_yaml_scalar(stripped[2:]))
            elif (
                cur_item
                and cur_sub in ("parameters", "templateParameters")
                and stripped.startswith("- name:")
                and (
                    (param_item_indent is None and indent in (4, 6))
                    or (param_item_indent is not None and indent == param_item_indent)
                )
            ):
                cur_param = {"name": _parse_yaml_scalar(stripped.split(":", 1)[1])}
                result["tools"][cur_item].setdefault(cur_sub, []).append(cur_param)
                param_item_indent = indent
                param_prop_indent = indent + 2
                param_list_key = None
                auth_item_indent = None
            elif indent == 4 and cur_item and not stripped.startswith("- "):
                cur_param = None
                param_item_indent = None
                param_prop_indent = None
                param_list_key = None
                auth_item_indent = None
                if ":" in stripped:
                    k, v = stripped.split(":", 1)
                    k = k.strip()
                    vs = v.strip()
                    if vs in ("|", "|-", ">", ">-"):
                        block_field = k
                        block_indent = 6
                        block_lines = []
                        cur_sub = k
                    elif not vs:
                        cur_sub = k
                        result["tools"][cur_item].setdefault(k, [])
                    else:
                        result["tools"][cur_item][k] = _parse_yaml_scalar(vs)
                        cur_sub = k
            elif cur_param is not None and param_prop_indent is not None:
                if (
                    param_list_key == "authServices"
                    and stripped.startswith("- name:")
                    and indent in (param_prop_indent, param_prop_indent + 2)
                ):
                    cur_param["authServices"].append({"name": _parse_yaml_scalar(stripped.split(":", 1)[1])})
                    auth_item_indent = indent + 2
                elif (
                    param_list_key == "authServices"
                    and auth_item_indent is not None
                    and indent == auth_item_indent
                    and stripped.startswith("field:")
                ):
                    cur_param["authServices"][-1]["field"] = _parse_yaml_scalar(stripped.split(":", 1)[1])
                elif (
                    param_list_key == "allowedValues"
                    and stripped.startswith("- ")
                    and indent in (param_prop_indent, param_prop_indent + 2)
                ):
                    cur_param["allowedValues"].append(_parse_yaml_scalar(stripped[2:]))
                elif indent == param_prop_indent and not stripped.startswith("- ") and ":" in stripped:
                    k, v = stripped.split(":", 1)
                    k = k.strip()
                    vs = v.strip()
                    if not vs and k in ("authServices", "allowedValues"):
                        cur_param[k] = []
                        param_list_key = k
                        auth_item_indent = None
                    else:
                        param_list_key = None
                        auth_item_indent = None
                        cur_param[k] = _parse_yaml_scalar(vs)
    _flush_block()
    return result


def _load_yaml_safe(path: Path) -> dict[str, Any]:
    """Load tools.yaml using PyYAML if available, else self-contained stdlib fallback."""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("YAML root is not a mapping")
        return data
    except ImportError:
        return _parse_yaml_stdlib_fallback(text)


# -----------------------------------------------------------------------------
# Individual Lab Verifiers (Labs 1..8)
# -----------------------------------------------------------------------------


def verify_lab_1(track: str, mode: str = "full") -> tuple[bool, list[str], list[str]]:
    """Lab 1: Neo4j CE Export & openCypher -> ISO GQL Parity."""
    failures: list[str] = []
    notes: list[str] = []
    rel = f"{track}/sql/09_opencypher_to_gql_cookbook.sql"
    raw = _read_text(REPO_ROOT / rel, failures)
    if raw is not None:
        before = len(failures)
        check_raw = raw
        if mode == "express":
            # Track A (4h Express) scope: Exercises 1-3 (MATCH TRAIL, FOR, SAFE_TO_JSON)
            ex4_match = re.search(r"^--\s*Exercise\s+4\b", raw, re.IGNORECASE | re.MULTILINE)
            if ex4_match:
                check_raw = raw[: ex4_match.start()]
        _assert_no_todo(check_raw, rel, failures)
        sql = strip_sql_comments(check_raw)
        patterns = [
            ("GRAPH SdlcGraph header", r"\bGRAPH\s+SdlcGraph\b"),
            ("MATCH TRAIL cycle-safe traversal", r"\bMATCH\s+TRAIL\b"),
            ("FOR array unnest (replacing UNWIND)", r"\bFOR\s+[a-zA-Z_]\w*\s+IN\b"),
            ("SAFE_TO_JSON node serialization", r"\bSAFE_TO_JSON\s*\("),
        ]
        if mode != "express":
            patterns.extend(
                [
                    ("RETURN ... ORDER BY ... LIMIT", r"\bRETURN\b.*?\bORDER\s+BY\b.*?\bLIMIT\b"),
                    ("OPTIONAL MATCH ... WHERE predicate", r"\bOPTIONAL\s+MATCH\b.*?\bWHERE\b"),
                ]
            )
        _assert_patterns(sql, rel, patterns, failures)
        scope_label = "Track A Express (Exercises 1-3)" if mode == "express" else "all 5 ISO GQL translations"
        _note_if_clean(before, failures, notes, f"Verified {scope_label} in {rel}")

    if _run_py(
        [sys.executable, "data/structured/stage_apoc_export.py"],
        "data/structured/stage_apoc_export.py",
        failures,
    ):
        notes.append("Path A Neo4j APOC export staging & parity validated")
    return not failures, failures, notes


def verify_lab_2(track: str, mode: str = "full") -> tuple[bool, list[str], list[str]]:
    """Lab 2: Two-Path SDLC Corpus & 4-Tier Parity Validation."""
    failures: list[str] = []
    notes: list[str] = []

    # 1. Verify locked dataset counts (68 nodes, 174 explicit, 60 dual-projected, 45 Markdown docs)
    structured = REPO_ROOT / "data" / "structured"
    node_files = ["owners.csv", "services.csv", "apis.csv", "adrs.csv", "requirements.csv"]
    explicit_edge_files = [
        "service_calls_api.csv",
        "service_depends_on_service.csv",
        "adr_governs_api.csv",
        "adr_supersedes_adr.csv",
        "req_applies_to_api.csv",
        "req_applies_to_service.csv",
    ]
    dual_edge_files = [
        "api_exposes_from_service.csv",
        "owner_owns_service.csv",
        "owner_owns_adr.csv",
        "owner_owns_req.csv",
    ]

    def _count_csv_rows(subdir: str, names: list[str]) -> int:
        total = 0
        for name in names:
            p = structured / subdir / name
            if not p.is_file():
                failures.append(f"Missing dataset CSV: data/structured/{subdir}/{name}")
                continue
            with p.open("r", encoding="utf-8", newline="") as fh:
                total += sum(1 for _ in csv.DictReader(fh))
        return total

    node_count = _count_csv_rows("nodes", node_files)
    explicit_count = _count_csv_rows("edges", explicit_edge_files)
    dual_count = _count_csv_rows("edges", dual_edge_files)
    md_files = list((REPO_ROOT / "data" / "unstructured").rglob("*.md"))

    if node_count != 68:
        failures.append(f"Expected 68 structured nodes, found {node_count}")
    if explicit_count != 174:
        failures.append(f"Expected 174 explicit edge rows, found {explicit_count}")
    if dual_count != 60:
        failures.append(f"Expected 60 dual-projected edges, found {dual_count}")
    if len(md_files) != 45:
        failures.append(f"Expected 45 unstructured Markdown docs, found {len(md_files)}")
    notes.append(
        f"Dataset verified: {node_count} nodes, {explicit_count} explicit + "
        f"{dual_count} dual-projected ({explicit_count + dual_count} logical) edges, {len(md_files)} docs"
    )

    # 2. Verify parity validation SQL script
    rel = f"{track}/sql/10_validate_migration_parity.sql"
    raw = _read_text(REPO_ROOT / rel, failures)
    if raw is not None:
        before = len(failures)
        check_raw = raw
        if mode == "express":
            # Track A (4h Express) scope: Tiers 1-3 (Tier 4 row-hash checksum is Track B)
            t4_match = re.search(r"^--\s*TIER\s+4\b", raw, re.IGNORECASE | re.MULTILINE)
            if t4_match:
                check_raw = raw[: t4_match.start()]
        _assert_no_todo(check_raw, rel, failures)
        sql = strip_sql_comments(check_raw)
        patterns = [
            ("DocumentChunks Path B check", r"\bDocumentChunks\b"),
            ("High-fanin hub svc-auth check", r"'svc-auth'"),
            ("Async DATA_FEED check", r"'DATA_FEED'"),
            ("Legacy AT_RISK consumer check", r"'AT_RISK'"),
            ("Logical edge parity (234)", r"\b234\b"),
        ]
        if mode != "express":
            patterns.append(
                ("Tier 4 deterministic hash (SHA256 / FARM_FINGERPRINT)", r"\b(SHA256|FARM_FINGERPRINT)\s*\(")
            )
        _assert_patterns(sql, rel, patterns, failures)
        scope_label = "Track A Express (Tiers 1-3)" if mode == "express" else "4-tier parity SQL"
        _note_if_clean(before, failures, notes, f"Verified {scope_label} in {rel}")

    return not failures, failures, notes


def verify_lab_3(track: str, mode: str = "full") -> tuple[bool, list[str], list[str]]:
    """Lab 3: Schema Design, Post-Load Indexes & Path A/B Ingestion (2 SQL Express / 4 files Full)."""
    failures: list[str] = []
    notes: list[str] = []

    # File 1: 01_path_a_schematized.sql
    rel_01 = f"{track}/sql/01_path_a_schematized.sql"
    raw_01 = _read_text(REPO_ROOT / rel_01, failures)
    if raw_01 is not None:
        before = len(failures)
        _assert_no_todo(raw_01, rel_01, failures)
        sql_01 = strip_sql_comments(raw_01)
        _assert_patterns(
            sql_01,
            rel_01,
            [
                ("ServiceCallsApi interleaved in parent Services ON DELETE CASCADE", r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?ServiceCallsApi\b.*?\bINTERLEAVE\s+IN\s+PARENT\s+Services\s+ON\s+DELETE\s+CASCADE"),
                ("ServiceDependsOnService table definition", r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?ServiceDependsOnService\b.*?\bPRIMARY\s+KEY\s*\(\s*from_service_id\s*,\s*to_service_id\s*\)"),
                ("ChunkCitesApiView with SQL SECURITY INVOKER", r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+ChunkCitesApiView\s+SQL\s+SECURITY\s+INVOKER\b"),
                ("CREATE PROPERTY GRAPH SdlcGraph", r"CREATE\s+(?:OR\s+REPLACE\s+)?PROPERTY\s+GRAPH\s+SdlcGraph\b"),
                ("768-dim FLOAT32 embedding column (INV-08)", r"ARRAY\s*<\s*FLOAT32\s*>\s*\(\s*vector_length\s*=>\s*768\s*\)"),
            ],
            failures,
        )
        # INV-06 check: ServiceDependsOnService must NOT combine INTERLEAVE IN PARENT Services ON DELETE CASCADE with self-ref cascading FK
        dep_stmt = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?ServiceDependsOnService\b.*?;", sql_01, re.IGNORECASE | re.DOTALL)
        if dep_stmt and re.search(r"\bINTERLEAVE\s+IN\s+PARENT\s+Services\b", dep_stmt.group(0), re.IGNORECASE):
            failures.append(f"{rel_01} violates INV-06: ServiceDependsOnService must be a standalone dual-FK table (no INTERLEAVE IN PARENT Services)")
        _note_if_clean(before, failures, notes, f"Verified core DDL & SdlcGraph in {rel_01}")

    # File 2: 03_indexes_fts_vector_postload.sql
    rel_03 = f"{track}/sql/03_indexes_fts_vector_postload.sql"
    raw_03 = _read_text(REPO_ROOT / rel_03, failures)
    if raw_03 is not None:
        before = len(failures)
        _assert_no_todo(raw_03, rel_03, failures)
        sql_03 = strip_sql_comments(raw_03)
        _assert_patterns(
            sql_03,
            rel_03,
            [
                ("Reverse covering STORING index interleaved in Apis", r"CREATE\s+INDEX\b.*?\bON\s+ServiceCallsApi\b.*?\bSTORING\s*\(.*?\bINTERLEAVE\s+IN\s+Apis\b"),
                ("Full-Text CREATE SEARCH INDEX on DocumentChunks", r"CREATE\s+SEARCH\s+INDEX\b.*?\bON\s+DocumentChunks\b"),
                ("ScaNN CREATE VECTOR INDEX with WHERE embedding IS NOT NULL (INV-08)", r"CREATE\s+VECTOR\s+INDEX\b.*?\bON\s+DocumentChunks\s*\(\s*embedding\s*\).*?\bWHERE\s+embedding\s+IS\s+NOT\s+NULL\b"),
            ],
            failures,
        )
        _note_if_clean(before, failures, notes, f"Verified post-load covering/FTS/ScaNN indexes in {rel_03}")

    if mode == "express":
        notes.append("Express mode (Track A): skipped Track B Python loaders (neo4j_to_spanner_loader.py & unstructured_graphrag_ingest.py)")
        return not failures, failures, notes

    # File 3: neo4j_to_spanner_loader.py
    rel_p1 = f"{track}/pipelines/neo4j_to_spanner_loader.py"
    raw_p1 = _read_text(REPO_ROOT / rel_p1, failures)
    if raw_p1 is not None:
        _assert_no_todo(raw_p1, rel_p1, failures)
        proc1 = _run_py([sys.executable, rel_p1, "--dry-run"], f"{rel_p1} --dry-run", failures)
        if proc1 is not None:
            notes.append(f"{rel_p1} --dry-run succeeded")

    # File 4: unstructured_graphrag_ingest.py
    rel_p2 = f"{track}/pipelines/unstructured_graphrag_ingest.py"
    raw_p2 = _read_text(REPO_ROOT / rel_p2, failures)
    if raw_p2 is not None:
        _assert_no_todo(raw_p2, rel_p2, failures)
        proc2 = _run_py([sys.executable, rel_p2, "--dry-run"], f"{rel_p2} --dry-run", failures)
        if proc2 is not None:
            notes.append(f"{rel_p2} --dry-run succeeded")

    return not failures, failures, notes


def verify_lab_4(track: str, mode: str = "full") -> tuple[bool, list[str], list[str]]:
    """Lab 4: Multi-Model Search Strategies & Data Boost Graph Algorithms (1 file Express / 2 files Full)."""
    failures: list[str] = []
    notes: list[str] = []

    rel_05 = f"{track}/sql/05_search_strategies_benchmark.sql"
    raw_05 = _read_text(REPO_ROOT / rel_05, failures)
    if raw_05 is not None:
        before = len(failures)
        _assert_no_todo(raw_05, rel_05, failures)
        sql_05 = strip_sql_comments(raw_05)
        _assert_patterns(
            sql_05,
            rel_05,
            [
                ("GQL IS_FIRST fanout capping", r"\bIS_FIRST\s*\("),
                ("GQL factorize_left hint", r"factorized_mode\s*=\s*factorize_left"),
                ("FTS custom dictionary SdlcSynonymDict", r"SdlcSynonymDict"),
                ("FTS enhance_query => TRUE", r"enhance_query\s*=>\s*TRUE"),
                ("Fuzzy SEARCH_NGRAMS", r"\bSEARCH_NGRAMS\s*\("),
                ("Fuzzy SCORE_NGRAMS", r"\bSCORE_NGRAMS\s*\("),
                ("ScaNN APPROX_COSINE_DISTANCE with WHERE embedding IS NOT NULL", r"APPROX_COSINE_DISTANCE\s*\(.*?WHERE\s+embedding\s+IS\s+NOT\s+NULL"),
            ],
            failures,
        )
        _note_if_clean(before, failures, notes, f"Verified 7 search strategy benchmarks in {rel_05}")

    if mode == "express":
        notes.append("Express mode (Track A): skipped Track B Data Boost Graph Algorithms (08_graph_algorithms_criticality.sql)")
        return not failures, failures, notes

    rel_08 = f"{track}/sql/08_graph_algorithms_criticality.sql"
    raw_08 = _read_text(REPO_ROOT / rel_08, failures)
    if raw_08 is not None:
        before = len(failures)
        _assert_no_todo(raw_08, rel_08, failures)
        sql_08 = strip_sql_comments(raw_08)
        _assert_patterns(
            sql_08,
            rel_08,
            [
                ("ServiceDependsOnAlgo table", r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?ServiceDependsOnAlgo\b"),
                ("INTERLEAVE IN PARENT Services ON DELETE CASCADE", r"INTERLEAVE\s+IN\s+PARENT\s+Services\s+ON\s+DELETE\s+CASCADE"),
                ("NOT ENFORCED destination FK (INV-06)", r"FOREIGN\s+KEY\s*\(\s*to_service_id\s*\)\s+REFERENCES\s+Services\s*\(\s*service_id\s*\)\s+NOT\s+ENFORCED"),
                ("Table-backed SdlcAlgoGraph", r"CREATE\s+(?:OR\s+REPLACE\s+)?PROPERTY\s+GRAPH\s+SdlcAlgoGraph\b"),
                ("Data Boost EXPORT DATA + PageRank", r"EXPORT\s+DATA\b.*?\bPageRank\s*\("),
            ],
            failures,
        )
        _note_if_clean(before, failures, notes, f"Verified Data Boost Graph Algorithms in {rel_08}")

    return not failures, failures, notes


def verify_lab_5(track: str, mode: str = "full") -> tuple[bool, list[str], list[str]]:
    """Lab 5: Hybrid Retrieval (RRF/RSF/Filtered) & Hero Blast-Radius GraphRAG."""
    del mode  # Both Lab 5 files are core to Track A Express and Track B Full
    failures: list[str] = []
    notes: list[str] = []

    rel_06 = f"{track}/sql/06_hybrid_rrf_rsf_filtered.sql"
    raw_06 = _read_text(REPO_ROOT / rel_06, failures)
    if raw_06 is not None:
        before = len(failures)
        _assert_no_todo(raw_06, rel_06, failures)
        sql_06 = strip_sql_comments(raw_06)
        if re.search(r"\bROW_NUMBER\s*\(", sql_06, re.IGNORECASE):
            failures.append(f"{rel_06} violates INV-09: ROW_NUMBER() is unsupported in Cloud Spanner GoogleSQL")
        _assert_patterns(
            sql_06,
            rel_06,
            [
                ("RRF ranking via UNNEST(ARRAY(...)) WITH OFFSET (INV-09)", r"\bUNNEST\s*\(\s*ARRAY\s*\(.*?\bWITH\s+OFFSET\b"),
                ("RRF k=60 formula", r"60(?:\.0)?\s*\+"),
                ("RSF divide-by-zero NULLIF guard", r"\bNULLIF\s*\("),
                ("Single-table ScaNN APPROX_COSINE_DISTANCE + WHERE embedding IS NOT NULL", r"APPROX_COSINE_DISTANCE\s*\(.*?WHERE\s+embedding\s+IS\s+NOT\s+NULL"),
            ],
            failures,
        )
        _note_if_clean(before, failures, notes, f"Verified RRF/RSF/Filtered hybrid SQL in {rel_06}")

    rel_07 = f"{track}/sql/07_hero_api_blast_radius_graphrag.sql"
    raw_07 = _read_text(REPO_ROOT / rel_07, failures)
    if raw_07 is not None:
        before = len(failures)
        _assert_no_todo(raw_07, rel_07, failures)
        sql_07 = strip_sql_comments(raw_07)
        if re.search(r"\bROW_NUMBER\s*\(", sql_07, re.IGNORECASE):
            failures.append(f"{rel_07} violates INV-09: ROW_NUMBER() is unsupported in Cloud Spanner GoogleSQL")
        _assert_patterns(
            sql_07,
            rel_07,
            [
                ("Cycle-safe MATCH TRAIL traversal", r"\bMATCH\s+TRAIL\b"),
                ("Async DATA_FEED_CONSUMER separation", r"DATA_FEED_CONSUMER"),
                ("CTE-isolated ScaNN APPROX_COSINE_DISTANCE + WHERE embedding IS NOT NULL", r"APPROX_COSINE_DISTANCE\s*\(.*?WHERE\s+embedding\s+IS\s+NOT\s+NULL"),
                ("Governing ADRs join (AdrGovernsApi)", r"\bAdrGovernsApi\b"),
                ("Applicable Requirements join (ReqAppliesToApi)", r"\bReqAppliesToApi\b"),
                ("Unstructured Markdown citations (ChunkEntityMentions)", r"\bChunkEntityMentions\b"),
            ],
            failures,
        )
        _note_if_clean(before, failures, notes, f"Verified Hero Blast-Radius GraphRAG SQL in {rel_07}")

    hero_script = REPO_ROOT / "solution" / "pipelines" / "hero_blast_radius_offline.py"
    hero_fixture = REPO_ROOT / "solution" / "sql" / "fixtures" / "07_hero_expected_rows.json"
    if hero_script.is_file() and hero_fixture.is_file():
        proc = _run_py(
            [
                sys.executable,
                "solution/pipelines/hero_blast_radius_offline.py",
                "--api-id",
                "api-payments-charge-v2",
                "--check-fixture",
                "solution/sql/fixtures/07_hero_expected_rows.json",
            ],
            "Offline Hero 7-row fixture replay",
            failures,
        )
        if proc is not None:
            notes.append("Hero 7-row blast radius, 5 ADRs, 5 REQs & 9 Markdown citations matched fixture 100%")
    elif track == "solution":
        failures.append("Missing solution/pipelines/hero_blast_radius_offline.py or 07_hero_expected_rows.json")
    else:
        notes.append("Starter-only workspace: skipped reference solution/ Hero offline fixture replay")

    return not failures, failures, notes


def verify_lab_6(track: str, mode: str = "full") -> tuple[bool, list[str], list[str]]:
    """Lab 6: MCP Toolbox, Per-Agent RBAC, FGAC Views & Prompt-Injection Demo (3 files)."""
    del mode  # All 3 Lab 6 files are core to Track A Express and Track B Full
    failures: list[str] = []
    notes: list[str] = []

    # File 1: 04_fgac_roles_views.sql
    rel_04 = f"{track}/sql/04_fgac_roles_views.sql"
    raw_04 = _read_text(REPO_ROOT / rel_04, failures)
    if raw_04 is not None:
        before = len(failures)
        _assert_no_todo(raw_04, rel_04, failures)
        sql_04 = strip_sql_comments(raw_04)
        _assert_patterns(
            sql_04,
            rel_04,
            [
                ("CREATE ROLE sdlc_readonly_role", r"CREATE\s+ROLE\s+sdlc_readonly_role\b"),
                ("PublicApiCatalogView with SQL SECURITY INVOKER", r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+PublicApiCatalogView\s+SQL\s+SECURITY\s+INVOKER\b"),
                ("GRANT SELECT ... TO ROLE sdlc_readonly_role", r"GRANT\s+SELECT\b.*?\bTO\s+ROLE\s+sdlc_readonly_role\b"),
            ],
            failures,
        )
        _note_if_clean(before, failures, notes, f"Verified FGAC roles & views in {rel_04}")

    # File 2: mcp/tools.yaml
    rel_yaml = f"{track}/mcp/tools.yaml"
    raw_yaml = _read_text(REPO_ROOT / rel_yaml, failures)
    if raw_yaml is not None:
        before = len(failures)
        _assert_no_todo(raw_yaml, rel_yaml, failures)
        try:
            cfg = _load_yaml_safe(REPO_ROOT / rel_yaml)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{rel_yaml} failed YAML parsing: {exc}")
            cfg = {}

        if cfg:
            src = (cfg.get("sources") or {}).get("sdlc-spanner") or {}
            if not src or src.get("type") != "spanner":
                failures.append(f"{rel_yaml} missing sources.sdlc-spanner (type: spanner)")
            for bad_key in ("databaseRole", "database_role"):
                if bad_key in src:
                    failures.append(f"{rel_yaml} sources.sdlc-spanner must NOT contain forbidden key '{bad_key}'")
            for req_key in ("project", "instance", "database", "dialect"):
                if req_key not in src:
                    failures.append(f"{rel_yaml} sources.sdlc-spanner missing required key '{req_key}'")

            emb = (cfg.get("embeddingModels") or {}).get("gemini-embedder") or {}
            if not emb:
                failures.append(f"{rel_yaml} missing embeddingModels.gemini-embedder")
            else:
                if "apiKey" in emb:
                    failures.append(f"{rel_yaml} embeddingModels.gemini-embedder must NOT declare active static apiKey")
                for req_key in ("project", "location", "model", "dimension"):
                    if req_key not in emb:
                        failures.append(f"{rel_yaml} embeddingModels.gemini-embedder missing '{req_key}'")
                if str(emb.get("location", "")).strip().lower() == "global":
                    failures.append(f"{rel_yaml} embeddingModels.gemini-embedder must not target 'global'")

            if not (cfg.get("authServices") or {}):
                failures.append(f"{rel_yaml} missing OIDC authServices configuration")

            tools = cfg.get("tools") or {}
            if not tools:
                failures.append(f"{rel_yaml} declares zero tools")
            for tname, tcfg in tools.items():
                if not isinstance(tcfg, dict):
                    continue
                if tcfg.get("type") == "spanner-execute-sql":
                    failures.append(f"{rel_yaml} tool '{tname}' uses forbidden type 'spanner-execute-sql'")
                if "templateParameters" in tcfg:
                    failures.append(f"{rel_yaml} tool '{tname}' uses forbidden 'templateParameters'")
                for param in tcfg.get("parameters") or []:
                    if not isinstance(param, dict):
                        continue
                    for pat in param.get("allowedValues") or []:
                        if not isinstance(pat, str) or not (pat.startswith("^") and pat.endswith("$")):
                            failures.append(
                                f"{rel_yaml} tool '{tname}' param '{param.get('name')}' has unanchored "
                                f"allowedValues {pat!r}; anchor with '^...$' (bare '' matches all strings in Toolbox v1.13.1)"
                            )

            toolsets = cfg.get("toolsets") or {}
            for ts_name in ("impact-analyst-toolset", "adr-curator-toolset", "security-auditor-toolset"):
                if ts_name not in toolsets or not toolsets[ts_name]:
                    failures.append(f"{rel_yaml} missing required persona toolset '{ts_name}'")
                else:
                    for tname in toolsets[ts_name]:
                        if tname not in tools:
                            failures.append(f"{rel_yaml} toolset '{ts_name}' references undefined tool '{tname}'")
                        elif ts_name in ("impact-analyst-toolset", "security-auditor-toolset") and not tools[tname].get("readOnly", False):
                            failures.append(
                                f"{rel_yaml} read-only toolset '{ts_name}' must not expose non-readOnly tool '{tname}'"
                            )

            _note_if_clean(before, failures, notes, f"Verified MCP Toolbox YAML security contract in {rel_yaml}")

    # File 3: mcp/adk_multi_agent_demo.py
    rel_adk = f"{track}/mcp/adk_multi_agent_demo.py"
    raw_adk = _read_text(REPO_ROOT / rel_adk, failures)
    if raw_adk is not None:
        _assert_no_todo(raw_adk, rel_adk, failures)
        proc = _run_py([sys.executable, rel_adk, "--mock"], f"{rel_adk} --mock", failures)
        if proc is not None:
            notes.append(f"{rel_adk} --mock passed persona isolation & prompt-injection checks")

    return not failures, failures, notes


def verify_lab_7(track: str, mode: str = "full") -> tuple[bool, list[str], list[str]]:
    """Lab 7: Enterprise Sizing, Bangkok Single-Region Pricing & Worksheet."""
    del mode  # Worksheet & sizing calculator are core to both Track A and Track B
    failures: list[str] = []
    notes: list[str] = []

    calc_path = REPO_ROOT / "solution" / "tools" / "sizing_calculator.py"
    if calc_path.is_file():
        proc = _run_py(
            [
                sys.executable,
                "solution/tools/sizing_calculator.py",
                "--preset",
                "all",
                "--region",
                "asia-southeast3",
                "--json",
            ],
            "sizing_calculator.py --preset all --json",
            failures,
        )
        if proc is not None:
            try:
                data = json.loads(proc.stdout)
                if data.get("primary_region") != "asia-southeast3":
                    failures.append(f"Expected primary_region='asia-southeast3', got {data.get('primary_region')!r}")
                if data.get("dr_backup_region") is not None:
                    failures.append(
                        f"Expected single-region Bangkok default dr_backup_region=None, got {data.get('dr_backup_region')!r}"
                    )
                bkk_rate = data["pricing_regions"]["asia-southeast3"]["compute_per_node_hour_usd"]["enterprise"]
                if abs(bkk_rate - 1.2915) > 1e-6:
                    failures.append(f"Bangkok Enterprise rate mismatch: {bkk_rate} != 1.2915")
                by_name = {s["name"]: s for s in data.get("scenarios", [])}
                if set(by_name.keys()) != {"workshop", "small", "medium", "large"}:
                    failures.append(f"Unexpected preset set: {sorted(by_name.keys())}")
                else:
                    for pname, sc in by_name.items():
                        dr_info = sc["spanner_costs"]["dr_backup"]
                        if dr_info.get("enabled") is not False or dr_info.get("total_dr_monthly_recurring_usd", 0.0) != 0.0:
                            failures.append(
                                f"Preset {pname} must default to single-region Bangkok backups ($0.00/mo cross-region egress)"
                            )
                    if by_name["workshop"]["compute"]["is_production_eligible"] is not False:
                        failures.append("workshop 100-PU preset must report is_production_eligible=False")
                    for prod_preset in ("small", "medium", "large"):
                        sc = by_name[prod_preset]
                        if sc["compute"]["processing_units"] < 1000:
                            failures.append(f"Preset {prod_preset} fell below 1,000 PU production floor")
                        if sc["legacy_3_store_tco"]["savings_vs_infra_only"]["spanner_cud_3yr"]["monthly_usd"] <= 0:
                            failures.append(f"Preset {prod_preset} expected positive 3-store TCO savings")
                notes.append("Verified Bangkok single-region 2026 rate card ($1.2915/node-hr, $0.00/mo cross-region egress) & all 4 sizing presets")
            except (KeyError, json.JSONDecodeError) as exc:
                failures.append(f"Invalid JSON from sizing_calculator.py: {exc}")
    elif track == "solution":
        failures.append("Missing solution/tools/sizing_calculator.py")

    worksheet_rel = "starter/tools/customer_scale_worksheet.json"
    worksheet_path = REPO_ROOT / worksheet_rel
    if track == "starter":
        raw_ws = _read_text(worksheet_path, failures)
        if raw_ws is not None:
            before = len(failures)
            _assert_no_todo(raw_ws, worksheet_rel, failures)
            try:
                ws_obj = json.loads(raw_ws)
                todo_status = str(ws_obj.get("todo_status", "")).strip()
                if "TODO" in todo_status.upper() or todo_status.upper() != "COMPLETED":
                    failures.append(
                        f"{worksheet_rel} todo_status is {todo_status!r}; update to 'COMPLETED' after customizing values"
                    )
            except json.JSONDecodeError as exc:
                failures.append(f"{worksheet_rel} is invalid JSON: {exc}")
            if calc_path.is_file():
                if _run_py(
                    [
                        sys.executable,
                        "solution/tools/sizing_calculator.py",
                        "--worksheet",
                        worksheet_rel,
                        "--json",
                    ],
                    f"sizing_calculator.py --worksheet {worksheet_rel}",
                    failures,
                ):
                    _note_if_clean(before, failures, notes, f"Validated completed worksheet {worksheet_rel}")
            else:
                _note_if_clean(before, failures, notes, f"Validated JSON & COMPLETED status in {worksheet_rel}")
    else:
        if worksheet_path.is_file() and calc_path.is_file():
            ws_proc = _run_py(
                [
                    sys.executable,
                    "solution/tools/sizing_calculator.py",
                    "--worksheet",
                    worksheet_rel,
                    "--json",
                ],
                f"sizing_calculator.py --worksheet {worksheet_rel}",
                failures,
            )
            if ws_proc is not None:
                try:
                    ws_data = json.loads(ws_proc.stdout)
                    if ws_data.get("primary_region") != "asia-southeast3" or ws_data.get("dr_backup_region") is not None:
                        failures.append(
                            f"{worksheet_rel} must default to Bangkok single-region (asia-southeast3, dr_backup_region=none)"
                        )
                    else:
                        notes.append(f"Verified single-region Bangkok worksheet compatibility ({worksheet_rel})")
                except json.JSONDecodeError as exc:
                    failures.append(f"Invalid JSON from worksheet evaluation: {exc}")

    return not failures, failures, notes


def verify_lab_8(track: str, mode: str = "full") -> tuple[bool, list[str], list[str]]:
    """Lab 8: Observability, Spanner Studio Viz, Provisioning Defaults & 8 Skills."""
    del mode  # Observability pack is core to both Track A and Track B
    failures: list[str] = []
    notes: list[str] = []

    rel_11 = f"{track}/sql/11_observability_and_studio_viz.sql"
    raw_11 = _read_text(REPO_ROOT / rel_11, failures)
    if raw_11 is not None:
        before = len(failures)
        _assert_no_todo(raw_11, rel_11, failures)
        sql_11 = strip_sql_comments(raw_11)
        _assert_patterns(
            sql_11,
            rel_11,
            [
                ("Spanner Studio SAFE_TO_JSON path visualization", r"\bSAFE_TO_JSON\s*\("),
                ("Query Insights (SPANNER_SYS.QUERY_STATS_TOP_HOUR)", r"SPANNER_SYS\.QUERY_STATS_TOP_HOUR\b"),
                ("Lock Insights (SPANNER_SYS.LOCK_STATS_TOP_HOUR)", r"SPANNER_SYS\.LOCK_STATS_TOP_HOUR\b"),
                ("Microsecond-to-second lock percentile conversion (/ 1000000.0 or / 1e6)", r"(?:1000000(?:\.0+)?|1e6)"),
                ("Pre-splitting introspection (SPANNER_SYS.USER_SPLIT_POINTS)", r"SPANNER_SYS\.USER_SPLIT_POINTS\b"),
            ],
            failures,
        )
        _note_if_clean(before, failures, notes, f"Verified Spanner Studio viz & SPANNER_SYS observability pack in {rel_11}")

    prov_path = REPO_ROOT / "scripts" / "provision_workshop_env.sh"
    raw_prov = _read_text(prov_path, failures)
    if raw_prov is not None:
        if not os.access(prov_path, os.X_OK):
            failures.append("scripts/provision_workshop_env.sh is not executable (chmod +x required)")
        for token in (
            "asia-southeast3",
            "regional-${REGION}",
            "asia-southeast1",
            "ENTERPRISE",
            "--default-backup-schedule-type=NONE",
            "apply-postload",
            "cleanup",
        ):
            if token not in raw_prov:
                failures.append(f"scripts/provision_workshop_env.sh missing expected default/command '{token}'")
        notes.append("Verified Bangkok single-region 100-PU Enterprise provisioning & cleanup defaults")

    spec = json.loads((REPO_ROOT / "WORKSHOP_SPEC.json").read_text(encoding="utf-8"))
    missing_skills = [
        s["name"]
        for s in spec.get("skills_manifest", [])
        if not (REPO_ROOT / s["path"] / "SKILL.md").is_file()
    ]
    if missing_skills:
        failures.append(f"Missing Antigravity skill(s) under .agents/skills/: {missing_skills}")
    else:
        notes.append("Verified all 8 Antigravity skills under .agents/skills/")

    return not failures, failures, notes


LAB_VERIFIERS = {
    1: verify_lab_1,
    2: verify_lab_2,
    3: verify_lab_3,
    4: verify_lab_4,
    5: verify_lab_5,
    6: verify_lab_6,
    7: verify_lab_7,
    8: verify_lab_8,
}


def run_labs(track: str, labs: list[int], mode: str = "full") -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    all_passed = True
    for lab_id in labs:
        passed, failures, notes = LAB_VERIFIERS[lab_id](track, mode=mode)
        if not passed:
            all_passed = False
        results.append(
            {
                "lab": lab_id,
                "track": track,
                "mode": mode,
                "passed": passed,
                "notes": notes,
                "failures": failures,
            }
        )
    return {
        "track": track,
        "mode": mode,
        "labs_checked": labs,
        "all_passed": all_passed,
        "results": results,
    }


def run_verify_starter_stubs() -> tuple[bool, dict[str, Any]]:
    """Confirm every lab 1..8 fails on unsolved starter/ and passes 100% on solution/."""
    starter_report = run_labs("starter", list(ALL_LABS))
    solution_report = run_labs("solution", list(ALL_LABS))

    starter_unexpected_passes = [r["lab"] for r in starter_report["results"] if r["passed"]]
    solution_failures = [r["lab"] for r in solution_report["results"] if not r["passed"]]

    ok = (not starter_unexpected_passes) and (not solution_failures)
    return ok, {
        "mode": "verify-starter-stubs",
        "passed": ok,
        "starter_all_armed_failed_as_expected": not starter_unexpected_passes,
        "starter_unexpected_passes": starter_unexpected_passes,
        "solution_all_passed": not solution_failures,
        "solution_failures": solution_failures,
        "starter": starter_report,
        "solution": solution_report,
    }


def _parse_lab_arg(value: str) -> list[int]:
    v = value.strip().lower()
    if v == "all":
        return list(ALL_LABS)
    if v.isdigit() and int(v) in LAB_VERIFIERS:
        return [int(v)]
    raise argparse.ArgumentTypeError(f"Invalid --lab {value!r}; choose 1..8 or 'all'.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline green/red lab verifier for starter/ and solution/ tracks (Labs 1-8)."
    )
    parser.add_argument(
        "--track",
        choices=("starter", "solution"),
        default="solution",
        help="Track directory to verify (default: solution).",
    )
    parser.add_argument(
        "--lab",
        default="all",
        help="Lab number 1..8 or 'all' (default: all).",
    )
    parser.add_argument(
        "--mode",
        choices=("full", "express"),
        default="full",
        help="Verification scope: 'full' (Track B 8h all 15 files, default) or 'express' (Track A 4h core subset).",
    )
    parser.add_argument(
        "--verify-starter-stubs",
        action="store_true",
        help="Assert all 8 labs fail on unsolved starter/ stubs AND pass 100% on solution/.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    args = parser.parse_args(argv)

    if args.verify_starter_stubs:
        ok, payload = run_verify_starter_stubs()
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print("==============================================================================")
            print("Starter Stub Arming & Solution Parity Verification (Labs 1-8)")
            print("==============================================================================")
            for r in payload["starter"]["results"]:
                tag = "[ARMED-FAIL]" if not r["passed"] else "[UNEXPECTED-PASS]"
                print(f"  starter/  Lab {r['lab']}: {tag}")
            for r in payload["solution"]["results"]:
                tag = "[PASS]" if r["passed"] else "[FAIL]"
                print(f"  solution/ Lab {r['lab']}: {tag}")
                for fail in r["failures"]:
                    print(f"      - {fail}")
            print("------------------------------------------------------------------------------")
            if ok:
                print("[OK] All 8 starter/ labs are armed (fail before completion) and all 8 solution/ labs PASS 100%.")
            else:
                print(
                    f"[FAIL] starter unexpected passes={payload['starter_unexpected_passes']}, "
                    f"solution failures={payload['solution_failures']}",
                    file=sys.stderr,
                )
        return 0 if ok else 1

    labs = _parse_lab_arg(args.lab)
    report = run_labs(args.track, labs, mode=args.mode)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("==============================================================================")
        print(f"Workshop Lab Verifier — track={args.track} | lab={args.lab} | mode={args.mode}")
        print("==============================================================================")
        for r in report["results"]:
            badge = "[PASS]" if r["passed"] else "[FAIL]"
            print(f"{badge} Lab {r['lab']} ({args.track}/, mode={args.mode})")
            for note in r["notes"]:
                print(f"    + {note}")
            for fail in r["failures"]:
                print(f"    x {fail}")
        print("------------------------------------------------------------------------------")
        if report["all_passed"]:
            print(f"[OK] Verified {len(labs)}/{len(labs)} lab(s) on track={args.track} (mode={args.mode}).")
        else:
            failed_ids = [r["lab"] for r in report["results"] if not r["passed"]]
            print(
                f"[FAIL] Lab(s) {failed_ids} did not pass on track={args.track} (mode={args.mode}). "
                "Complete remaining TODO(Lab N) items and re-run.",
                file=sys.stderr,
            )

    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
