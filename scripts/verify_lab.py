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


def _load_yaml_safe(path: Path) -> dict[str, Any]:
    """Load tools.yaml using PyYAML if available, else stdlib fallback from solution/mcp."""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("YAML root is not a mapping")
        return data
    except ImportError:
        sys.path.insert(0, str(REPO_ROOT / "solution" / "mcp"))
        from adk_multi_agent_demo import _parse_yaml_stdlib_fallback  # type: ignore

        return _parse_yaml_stdlib_fallback(text)


# -----------------------------------------------------------------------------
# Individual Lab Verifiers (Labs 1..8)
# -----------------------------------------------------------------------------


def verify_lab_1(track: str) -> tuple[bool, list[str], list[str]]:
    """Lab 1: Neo4j CE Export & openCypher -> ISO GQL Parity."""
    failures: list[str] = []
    notes: list[str] = []
    rel = f"{track}/sql/09_opencypher_to_gql_cookbook.sql"
    raw = _read_text(REPO_ROOT / rel, failures)
    if raw is not None:
        before = len(failures)
        _assert_no_todo(raw, rel, failures)
        sql = strip_sql_comments(raw)
        _assert_patterns(
            sql,
            rel,
            [
                ("GRAPH SdlcGraph header", r"\bGRAPH\s+SdlcGraph\b"),
                ("MATCH TRAIL cycle-safe traversal", r"\bMATCH\s+TRAIL\b"),
                ("FOR array unnest (replacing UNWIND)", r"\bFOR\s+[a-zA-Z_]\w*\s+IN\b"),
                ("SAFE_TO_JSON node serialization", r"\bSAFE_TO_JSON\s*\("),
                ("RETURN ... ORDER BY ... LIMIT", r"\bRETURN\b.*?\bORDER\s+BY\b.*?\bLIMIT\b"),
                ("OPTIONAL MATCH ... WHERE predicate", r"\bOPTIONAL\s+MATCH\b.*?\bWHERE\b"),
            ],
            failures,
        )
        _note_if_clean(before, failures, notes, f"Verified ISO GQL translations in {rel}")

    if _run_py(
        [sys.executable, "data/structured/stage_apoc_export.py"],
        "data/structured/stage_apoc_export.py",
        failures,
    ):
        notes.append("Path A Neo4j APOC export staging & parity validated")
    return not failures, failures, notes


def verify_lab_2(track: str) -> tuple[bool, list[str], list[str]]:
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
        _assert_no_todo(raw, rel, failures)
        sql = strip_sql_comments(raw)
        _assert_patterns(
            sql,
            rel,
            [
                ("DocumentChunks Path B check", r"\bDocumentChunks\b"),
                ("High-fanin hub svc-auth check", r"'svc-auth'"),
                ("Async DATA_FEED check", r"'DATA_FEED'"),
                ("Legacy AT_RISK consumer check", r"'AT_RISK'"),
                ("Logical edge parity (234)", r"\b234\b"),
                ("Tier 4 deterministic hash (SHA256 / FARM_FINGERPRINT)", r"\b(SHA256|FARM_FINGERPRINT)\s*\("),
            ],
            failures,
        )
        _note_if_clean(before, failures, notes, f"Verified 4-tier parity SQL in {rel}")

    return not failures, failures, notes


def verify_lab_3(track: str) -> tuple[bool, list[str], list[str]]:
    """Lab 3: Schema Design, Post-Load Indexes & Path A/B Ingestion (4 files)."""
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
                ("ServiceCallsApi interleaved in parent Services ON DELETE CASCADE", r"CREATE\s+TABLE\s+ServiceCallsApi\b.*?\bINTERLEAVE\s+IN\s+PARENT\s+Services\s+ON\s+DELETE\s+CASCADE"),
                ("ServiceDependsOnService table definition", r"CREATE\s+TABLE\s+ServiceDependsOnService\b.*?\bPRIMARY\s+KEY\s*\(\s*from_service_id\s*,\s*to_service_id\s*\)"),
                ("ChunkCitesApiView with SQL SECURITY INVOKER", r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+ChunkCitesApiView\s+SQL\s+SECURITY\s+INVOKER\b"),
                ("CREATE PROPERTY GRAPH SdlcGraph", r"CREATE\s+(?:OR\s+REPLACE\s+)?PROPERTY\s+GRAPH\s+SdlcGraph\b"),
                ("768-dim FLOAT32 embedding column (INV-08)", r"ARRAY\s*<\s*FLOAT32\s*>\s*\(\s*vector_length\s*=>\s*768\s*\)"),
            ],
            failures,
        )
        # INV-06 check: ServiceDependsOnService must NOT combine INTERLEAVE IN PARENT Services ON DELETE CASCADE with self-ref cascading FK
        dep_stmt = re.search(r"CREATE\s+TABLE\s+ServiceDependsOnService\b.*?;", sql_01, re.IGNORECASE | re.DOTALL)
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


def verify_lab_4(track: str) -> tuple[bool, list[str], list[str]]:
    """Lab 4: Multi-Model Search Strategies & Data Boost Graph Algorithms (2 files)."""
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
                ("ServiceDependsOnAlgo table", r"CREATE\s+TABLE\s+ServiceDependsOnAlgo\b"),
                ("INTERLEAVE IN PARENT Services ON DELETE CASCADE", r"INTERLEAVE\s+IN\s+PARENT\s+Services\s+ON\s+DELETE\s+CASCADE"),
                ("NOT ENFORCED destination FK (INV-06)", r"FOREIGN\s+KEY\s*\(\s*to_service_id\s*\)\s+REFERENCES\s+Services\s*\(\s*service_id\s*\)\s+NOT\s+ENFORCED"),
                ("Table-backed SdlcAlgoGraph", r"CREATE\s+(?:OR\s+REPLACE\s+)?PROPERTY\s+GRAPH\s+SdlcAlgoGraph\b"),
                ("Data Boost EXPORT DATA + PageRank", r"EXPORT\s+DATA\b.*?\bPageRank\s*\("),
            ],
            failures,
        )
        _note_if_clean(before, failures, notes, f"Verified Data Boost Graph Algorithms in {rel_08}")

    return not failures, failures, notes


def verify_lab_5(track: str) -> tuple[bool, list[str], list[str]]:
    """Lab 5: Hybrid Retrieval (RRF/RSF/Filtered) & Hero Blast-Radius GraphRAG."""
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

    return not failures, failures, notes


def verify_lab_6(track: str) -> tuple[bool, list[str], list[str]]:
    """Lab 6: MCP Toolbox, Per-Agent RBAC, FGAC Views & Prompt-Injection Demo (3 files)."""
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

            toolsets = cfg.get("toolsets") or {}
            for ts_name in ("impact-analyst-toolset", "adr-curator-toolset", "security-auditor-toolset"):
                if ts_name not in toolsets or not toolsets[ts_name]:
                    failures.append(f"{rel_yaml} missing required persona toolset '{ts_name}'")

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


def verify_lab_7(track: str) -> tuple[bool, list[str], list[str]]:
    """Lab 7: Enterprise Sizing, Verified Bangkok/Singapore Pricing & Worksheet."""
    failures: list[str] = []
    notes: list[str] = []

    proc = _run_py(
        [
            sys.executable,
            "solution/tools/sizing_calculator.py",
            "--preset",
            "all",
            "--region",
            "asia-southeast3",
            "--dr-backup-region",
            "asia-southeast1",
            "--json",
        ],
        "sizing_calculator.py --preset all --json",
        failures,
    )
    if proc is not None:
        try:
            data = json.loads(proc.stdout)
            bkk_rate = data["pricing_regions"]["asia-southeast3"]["compute_per_node_hour_usd"]["enterprise"]
            if abs(bkk_rate - 1.2915) > 1e-6:
                failures.append(f"Bangkok Enterprise rate mismatch: {bkk_rate} != 1.2915")
            by_name = {s["name"]: s for s in data.get("scenarios", [])}
            if set(by_name.keys()) != {"workshop", "small", "medium", "large"}:
                failures.append(f"Unexpected preset set: {sorted(by_name.keys())}")
            else:
                if by_name["workshop"]["compute"]["is_production_eligible"] is not False:
                    failures.append("workshop 100-PU preset must report is_production_eligible=False")
                for prod_preset in ("small", "medium", "large"):
                    sc = by_name[prod_preset]
                    if sc["compute"]["processing_units"] < 1000:
                        failures.append(f"Preset {prod_preset} fell below 1,000 PU production floor")
                    if sc["legacy_3_store_tco"]["savings_vs_infra_only"]["spanner_cud_3yr"]["monthly_usd"] <= 0:
                        failures.append(f"Preset {prod_preset} expected positive 3-store TCO savings")
            notes.append("Verified Bangkok 2026 rate card ($1.2915/node-hr) & all 4 sizing presets")
        except (KeyError, json.JSONDecodeError) as exc:
            failures.append(f"Invalid JSON from sizing_calculator.py: {exc}")

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
        if worksheet_path.is_file():
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
                notes.append(f"Verified worksheet schema compatibility ({worksheet_rel})")

    return not failures, failures, notes


def verify_lab_8(track: str) -> tuple[bool, list[str], list[str]]:
    """Lab 8: Observability, Spanner Studio Viz, Provisioning Defaults & 8 Skills."""
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
                ("Microsecond-to-second lock percentile conversion (/ 1000000)", r"1000000"),
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
        for token in ("asia-southeast3", "ENTERPRISE", "--default-backup-schedule-type=NONE", "backup-to-singapore"):
            if token not in raw_prov:
                failures.append(f"scripts/provision_workshop_env.sh missing expected default/command '{token}'")
        notes.append("Verified Bangkok 100-PU Enterprise provisioning & cleanup defaults")

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


def run_labs(track: str, labs: list[int]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    all_passed = True
    for lab_id in labs:
        passed, failures, notes = LAB_VERIFIERS[lab_id](track)
        if not passed:
            all_passed = False
        results.append(
            {
                "lab": lab_id,
                "track": track,
                "passed": passed,
                "notes": notes,
                "failures": failures,
            }
        )
    return {
        "track": track,
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
    report = run_labs(args.track, labs)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("==============================================================================")
        print(f"Workshop Lab Verifier — track={args.track} | lab={args.lab}")
        print("==============================================================================")
        for r in report["results"]:
            badge = "[PASS]" if r["passed"] else "[FAIL]"
            print(f"{badge} Lab {r['lab']} ({args.track}/)")
            for note in r["notes"]:
                print(f"    + {note}")
            for fail in r["failures"]:
                print(f"    x {fail}")
        print("------------------------------------------------------------------------------")
        if report["all_passed"]:
            print(f"[OK] Verified {len(labs)}/{len(labs)} lab(s) on track={args.track}.")
        else:
            failed_ids = [r["lab"] for r in report["results"] if not r["passed"]]
            print(
                f"[FAIL] Lab(s) {failed_ids} did not pass on track={args.track}. "
                "Complete remaining TODO(Lab N) items and re-run.",
                file=sys.stderr,
            )

    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
