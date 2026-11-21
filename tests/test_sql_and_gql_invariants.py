#!/usr/bin/env python3
"""Module 1: SQL, DDL & ISO GQL Structural Invariants Test Suite.

Parses every `solution/sql/*.sql` (and `*.sql.tmpl`) file and verifies all 9
Cloud Spanner Graph / Multi-Model structural invariants (INV-01 through INV-09)
defined in `WORKSHOP_SPEC.json`:
  - INV-01: GoogleSQL dialect & Cloud Spanner Enterprise features (no PG syntax)
  - INV-02: Unique element aliases in every CREATE PROPERTY GRAPH
  - INV-03: Exact PK arity & SQL type matching in SOURCE/DESTINATION REFERENCES
  - INV-04: Graph-wide property type uniqueness across all nodes & edges
  - INV-05: Explicit SQL SECURITY INVOKER/DEFINER on all views + explicit KEY on
            view-backed graph elements + table-backed SdlcAlgoGraph
  - INV-06: Never combine INTERLEAVE IN PARENT T ON DELETE CASCADE with a
            self-referencing cascading FK REFERENCES T ON DELETE CASCADE
  - INV-07: DYNAMIC LABEL isolation in SdlcDynamicGraph, lowercase CHECKs,
            and PROPERTIES ALL COLUMNS EXCEPT (properties)
  - INV-08: ARRAY<FLOAT32>(vector_length=>768), ScaNN CREATE VECTOR INDEX and
            single-table ANN queries with WHERE embedding IS NOT NULL
  - INV-09: Zero ROW_NUMBER() window functions, RRF via UNNEST(ARRAY(...)) WITH
            OFFSET, regional Vertex AI asia-southeast1 remote model (never global)
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = REPO_ROOT / "solution" / "sql"
PIPELINES_DIR = REPO_ROOT / "solution" / "pipelines"


def strip_sql_comments(sql_text: str) -> str:
    """Remove -- line comments, # line comments, and /* ... */ block comments."""
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
            if ch == "#":
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


def split_statements(clean_sql: str) -> list[str]:
    """Split comment-stripped SQL into semicolon-delimited statements."""
    stmts: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    in_backtick = False
    i = 0
    n = len(clean_sql)
    while i < n:
        ch = clean_sql[i]
        if ch == "\\" and (in_single or in_double) and i + 1 < n:
            buf.append(ch)
            buf.append(clean_sql[i + 1])
            i += 2
            continue
        if ch == "'" and not (in_double or in_backtick):
            in_single = not in_single
        elif ch == '"' and not (in_single or in_backtick):
            in_double = not in_double
        elif ch == "`" and not (in_single or in_double):
            in_backtick = not in_backtick
        elif ch == ";" and not (in_single or in_double or in_backtick):
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def extract_balanced_parens(text: str, open_idx: int) -> tuple[str, int]:
    """Given index of '(', return (inner_text, close_idx) handling quotes & nesting."""
    assert text[open_idx] == "("
    depth = 0
    in_single = False
    in_double = False
    in_backtick = False
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and (in_single or in_double) and i + 1 < n:
            i += 2
            continue
        if ch == "'" and not (in_double or in_backtick):
            in_single = not in_single
        elif ch == '"' and not (in_single or in_backtick):
            in_double = not in_double
        elif ch == "`" and not (in_single or in_double):
            in_backtick = not in_backtick
        elif not (in_single or in_double or in_backtick):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return text[open_idx + 1 : i], i
        i += 1
    raise ValueError("Unbalanced parenthesis in SQL statement")


def split_top_level_commas(text: str) -> list[str]:
    """Split text on commas outside nested parentheses, brackets, or quotes."""
    parts: list[str] = []
    buf: list[str] = []
    paren_depth = 0
    angle_depth = 0
    bracket_depth = 0
    in_single = False
    in_double = False
    in_backtick = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and (in_single or in_double) and i + 1 < n:
            buf.append(ch)
            buf.append(text[i + 1])
            i += 2
            continue
        if ch == "'" and not (in_double or in_backtick):
            in_single = not in_single
        elif ch == '"' and not (in_single or in_backtick):
            in_double = not in_double
        elif ch == "`" and not (in_single or in_double):
            in_backtick = not in_backtick
        elif not (in_single or in_double or in_backtick):
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth = max(0, paren_depth - 1)
            elif ch == "[":
                bracket_depth += 1
            elif ch == "]":
                bracket_depth = max(0, bracket_depth - 1)
            elif ch == "<":
                angle_depth += 1
            elif ch == ">" and angle_depth > 0:
                angle_depth -= 1
            elif ch == "," and paren_depth == 0 and bracket_depth == 0 and angle_depth == 0:
                part = "".join(buf).strip()
                if part:
                    parts.append(part)
                buf = []
                i += 1
                continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def normalize_sql_type(type_expr: str) -> str:
    """Normalize GoogleSQL column type for type-compatibility comparisons."""
    cleaned = re.sub(r"\s+", " ", type_expr.strip()).upper()
    if cleaned.startswith("STRING"):
        return "STRING"
    if cleaned.startswith("BYTES"):
        return "BYTES"
    if cleaned.startswith("ARRAY<"):
        # Preserve element type (e.g., ARRAY<FLOAT32>, ARRAY<STRING>)
        m = re.match(r"ARRAY\s*<\s*([A-Z0-9_]+)", cleaned)
        if m:
            elem = "STRING" if m.group(1).startswith("STRING") else m.group(1)
            return f"ARRAY<{elem}>"
    return cleaned.split("(", 1)[0].strip()


def parse_create_table(stmt: str) -> dict[str, Any] | None:
    """Parse a GoogleSQL CREATE TABLE statement into columns, PK, FKs, interleaving."""
    m = re.match(r"CREATE\s+TABLE\s+([A-Za-z_]\w*)\s*\(", stmt, re.IGNORECASE)
    if not m:
        return None
    table_name = m.group(1)
    open_idx = stmt.find("(", m.start())
    body, close_idx = extract_balanced_parens(stmt, open_idx)
    tail = stmt[close_idx + 1 :]

    columns: dict[str, str] = {}
    hidden_columns: set[str] = set()
    fks: list[dict[str, Any]] = []
    checks: list[str] = []

    for item in split_top_level_commas(body):
        item_clean = item.strip()
        if not item_clean:
            continue
        fk_m = re.search(
            r"FOREIGN\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+([A-Za-z_]\w*)\s*\(([^)]+)\)(.*)",
            item_clean,
            re.IGNORECASE | re.DOTALL,
        )
        if fk_m:
            fk_tail = fk_m.group(4)
            fks.append(
                {
                    "cols": [c.strip() for c in fk_m.group(1).split(",")],
                    "ref_table": fk_m.group(2),
                    "ref_cols": [c.strip() for c in fk_m.group(3).split(",")],
                    "on_delete_cascade": bool(
                        re.search(r"\bON\s+DELETE\s+CASCADE\b", fk_tail, re.IGNORECASE)
                    ),
                    "not_enforced": bool(
                        re.search(r"\bNOT\s+ENFORCED\b", fk_tail, re.IGNORECASE)
                    ),
                }
            )
            continue

        if re.match(r"(?:CONSTRAINT\s+\w+\s+)?CHECK\b", item_clean, re.IGNORECASE):
            checks.append(item_clean)
            continue

        # Parse column: <col_name> <type_expr> ...
        col_m = re.match(
            r"^([A-Za-z_]\w*)\s+(ARRAY\s*<[^>]+>(?:\s*\([^)]*\))?|STRING\s*\([^)]+\)|BYTES\s*\([^)]+\)|[A-Z0-9_]+)(.*)$",
            item_clean,
            re.IGNORECASE | re.DOTALL,
        )
        if col_m:
            col_name = col_m.group(1)
            raw_type = col_m.group(2)
            col_rest = col_m.group(3)
            columns[col_name] = normalize_sql_type(raw_type)
            if re.search(r"\bHIDDEN\b", col_rest, re.IGNORECASE):
                hidden_columns.add(col_name)

    pk_m = re.search(r"PRIMARY\s+KEY\s*\(([^)]*)\)", tail, re.IGNORECASE)
    pk_cols = [c.strip().split()[0] for c in pk_m.group(1).split(",") if c.strip()] if pk_m else []

    interleave_m = re.search(
        r"INTERLEAVE\s+IN\s+(PARENT\s+)?([A-Za-z_]\w*)(?:\s+ON\s+DELETE\s+(CASCADE|NO\s+ACTION))?",
        tail,
        re.IGNORECASE,
    )
    interleave = None
    if interleave_m:
        interleave = {
            "is_parent": bool(interleave_m.group(1)),
            "parent_table": interleave_m.group(2),
            "on_delete": (interleave_m.group(3) or "NO ACTION").upper(),
        }

    return {
        "name": table_name,
        "columns": columns,
        "hidden_columns": hidden_columns,
        "pk": pk_cols,
        "fks": fks,
        "checks": checks,
        "interleave": interleave,
        "raw": stmt,
    }


def parse_create_view(stmt: str, tables: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Parse CREATE [OR REPLACE] VIEW statement including SQL SECURITY & columns."""
    m = re.match(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+([A-Za-z_]\w*)\s+(.*)",
        stmt,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None
    view_name = m.group(1)
    rest = m.group(2)
    sec_m = re.match(r"SQL\s+SECURITY\s+(INVOKER|DEFINER)\s+AS\b", rest, re.IGNORECASE)
    security = sec_m.group(1).upper() if sec_m else None

    # Resolve projected column types for simple single-table views (ChunkCites*View)
    from_m = re.search(r"\bFROM\s+([A-Za-z_]\w*)(?:\s+AS\s+([A-Za-z_]\w*))?", rest, re.IGNORECASE)
    select_m = re.search(r"\bSELECT\b(.*?)\bFROM\b", rest, re.IGNORECASE | re.DOTALL)
    view_cols: dict[str, str] = {}
    if from_m and select_m:
        base_tbl = tables.get(from_m.group(1))
        for expr in split_top_level_commas(select_m.group(1)):
            cast_m = re.search(
                r"CAST\s*\(.*?\s+AS\s+([A-Z0-9_]+)\s*\)\s+AS\s+([A-Za-z_]\w*)$",
                expr.strip(),
                re.IGNORECASE | re.DOTALL,
            )
            if cast_m:
                view_cols[cast_m.group(2)] = normalize_sql_type(cast_m.group(1))
                continue
            col_ref = re.match(
                r"^(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)(?:\s+AS\s+([A-Za-z_]\w*))?$",
                expr.strip(),
                re.IGNORECASE,
            )
            if col_ref and base_tbl:
                src_col = col_ref.group(1)
                out_col = col_ref.group(2) or src_col
                if src_col in base_tbl["columns"]:
                    view_cols[out_col] = base_tbl["columns"][src_col]

    return {
        "name": view_name,
        "sql_security": security,
        "columns": view_cols,
        "raw": stmt,
    }


def parse_property_graph(stmt: str) -> dict[str, Any] | None:
    """Parse CREATE [OR REPLACE] PROPERTY GRAPH into node and edge definitions."""
    m = re.match(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?PROPERTY\s+GRAPH\s+([A-Za-z_]\w*)\b",
        stmt,
        re.IGNORECASE,
    )
    if not m:
        return None
    graph_name = m.group(1)

    def _extract_section(keyword: str) -> list[str]:
        sec_m = re.search(rf"\b{keyword}\s+TABLES\s*\(", stmt, re.IGNORECASE)
        if not sec_m:
            return []
        open_idx = stmt.find("(", sec_m.start())
        body, _ = extract_balanced_parens(stmt, open_idx)
        return split_top_level_commas(body)

    def _parse_element(raw_elem: str, is_edge: bool) -> dict[str, Any]:
        hdr_m = re.match(
            r"^([A-Za-z_]\w*)(?:\s+AS\s+([A-Za-z_]\w*))?\b",
            raw_elem.strip(),
            re.IGNORECASE,
        )
        if not hdr_m:
            raise ValueError(f"Unable to parse graph element header: {raw_elem[:60]!r}")
        base_name = hdr_m.group(1)
        alias = hdr_m.group(2) or base_name
        explicit_alias = hdr_m.group(2)

        key_m = re.search(r"\bKEY\s*\(([^)]+)\)", raw_elem, re.IGNORECASE)
        key_cols = [c.strip() for c in key_m.group(1).split(",")] if key_m else []

        src_m = re.search(
            r"\bSOURCE\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+([A-Za-z_]\w*)\s*\(([^)]+)\)",
            raw_elem,
            re.IGNORECASE,
        )
        dst_m = re.search(
            r"\bDESTINATION\s+KEY\s*\(([^)]+)\)\s+REFERENCES\s+([A-Za-z_]\w*)\s*\(([^)]+)\)",
            raw_elem,
            re.IGNORECASE,
        )

        has_dynamic_label = bool(re.search(r"\bDYNAMIC\s+LABEL\s*\(", raw_elem, re.IGNORECASE))
        has_dynamic_props = bool(
            re.search(r"\bDYNAMIC\s+PROPERTIES\s*\(", raw_elem, re.IGNORECASE)
        )

        props_mode = "ALL"
        props_list: list[str] = []
        props_except: list[str] = []
        except_m = re.search(
            r"\bPROPERTIES\s+ALL\s+COLUMNS\s+EXCEPT\s*\(([^)]+)\)",
            raw_elem,
            re.IGNORECASE,
        )
        if except_m:
            props_mode = "EXCEPT"
            props_except = [c.strip() for c in except_m.group(1).split(",")]
        elif re.search(r"\bPROPERTIES\s+ALL\s+COLUMNS\b", raw_elem, re.IGNORECASE):
            props_mode = "ALL"
        else:
            explicit_props = re.search(r"\bPROPERTIES\s*\(([^)]+)\)", raw_elem, re.IGNORECASE)
            if explicit_props:
                props_mode = "EXPLICIT"
                props_list = [c.strip() for c in explicit_props.group(1).split(",")]

        return {
            "base_name": base_name,
            "alias": alias,
            "explicit_alias": explicit_alias,
            "is_edge": is_edge,
            "key_cols": key_cols,
            "source_key": (
                {
                    "cols": [c.strip() for c in src_m.group(1).split(",")],
                    "ref_alias": src_m.group(2),
                    "ref_cols": [c.strip() for c in src_m.group(3).split(",")],
                }
                if src_m
                else None
            ),
            "dest_key": (
                {
                    "cols": [c.strip() for c in dst_m.group(1).split(",")],
                    "ref_alias": dst_m.group(2),
                    "ref_cols": [c.strip() for c in dst_m.group(3).split(",")],
                }
                if dst_m
                else None
            ),
            "has_dynamic_label": has_dynamic_label,
            "has_dynamic_properties": has_dynamic_props,
            "props_mode": props_mode,
            "props_list": props_list,
            "props_except": props_except,
            "raw": raw_elem,
        }

    nodes = [_parse_element(item, is_edge=False) for item in _extract_section("NODE")]
    edges = [_parse_element(item, is_edge=True) for item in _extract_section("EDGE")]
    return {"name": graph_name, "nodes": nodes, "edges": edges, "raw": stmt}


class TestSqlAndGqlInvariants(unittest.TestCase):
    """Verify all 9 Spanner Graph & Multi-Model SQL invariants across solution/sql/."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = json.loads((REPO_ROOT / "WORKSHOP_SPEC.json").read_text(encoding="utf-8"))
        cls.sql_files = sorted(SQL_DIR.glob("*.sql"))
        cls.tmpl_files = sorted(SQL_DIR.glob("*.sql.tmpl"))
        cls.raw_by_name: dict[str, str] = {}
        cls.clean_by_name: dict[str, str] = {}
        cls.stmts_by_name: dict[str, list[str]] = {}

        for path in cls.sql_files + cls.tmpl_files:
            raw = path.read_text(encoding="utf-8")
            clean = strip_sql_comments(raw)
            cls.raw_by_name[path.name] = raw
            cls.clean_by_name[path.name] = clean
            cls.stmts_by_name[path.name] = split_statements(clean)

        cls.tables: dict[str, dict[str, Any]] = {}
        cls.views: dict[str, dict[str, Any]] = {}
        cls.graphs: dict[str, dict[str, Any]] = {}

        for fname, stmts in cls.stmts_by_name.items():
            for stmt in stmts:
                t = parse_create_table(stmt)
                if t is not None:
                    t["file"] = fname
                    cls.tables[t["name"]] = t

        for fname, stmts in cls.stmts_by_name.items():
            for stmt in stmts:
                v = parse_create_view(stmt, cls.tables)
                if v is not None:
                    v["file"] = fname
                    cls.views[v["name"]] = v
                g = parse_property_graph(stmt)
                if g is not None:
                    g["file"] = fname
                    cls.graphs[g["name"]] = g

    def test_expected_solution_sql_inventory_and_balanced_syntax(self) -> None:
        """All 15 expected SQL and template files exist and parse cleanly."""
        expected_sql = {
            "01_path_a_schematized.sql",
            "02_path_b_schemaless_comparison.sql",
            "03_indexes_fts_vector_postload.sql",
            "03a_seed_synonyms_and_metadata.sql",
            "03d_backfill_embeddings_pdml.sql",
            "04_fgac_roles_views.sql",
            "04b_fgac_grants_rerunnable.sql",
            "05_search_strategies_benchmark.sql",
            "06_hybrid_rrf_rsf_filtered.sql",
            "07_hero_api_blast_radius_graphrag.sql",
            "08_graph_algorithms_criticality.sql",
            "09_opencypher_to_gql_cookbook.sql",
            "10_validate_migration_parity.sql",
            "11_observability_and_studio_viz.sql",
        }
        expected_tmpls = {
            "03b_remote_models.sql.tmpl",
            "03c_optional_reranker_model.sql.tmpl",
        }
        self.assertTrue(
            expected_sql.issubset({p.name for p in self.sql_files}),
            f"Missing SQL files: {sorted(expected_sql - {p.name for p in self.sql_files})}",
        )
        self.assertEqual({p.name for p in self.tmpl_files}, expected_tmpls)

        for fname, stmts in self.stmts_by_name.items():
            self.assertGreater(len(stmts), 0, f"{fname} yielded zero SQL statements")
            # Verify balanced parentheses in every statement (excluding quotes)
            clean = self.clean_by_name[fname]
            depth = 0
            in_q: str | None = None
            i = 0
            while i < len(clean):
                ch = clean[i]
                if ch == "\\" and in_q in ("'", '"') and i + 1 < len(clean):
                    i += 2
                    continue
                if in_q:
                    if ch == in_q:
                        in_q = None
                elif ch in ("'", '"', "`"):
                    in_q = ch
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    self.assertGreaterEqual(depth, 0, f"Unbalanced ')' in {fname}")
                i += 1
            self.assertEqual(depth, 0, f"Unclosed '(' in {fname}")
            self.assertIsNone(in_q, f"Unclosed quote {in_q!r} in {fname}")

    def test_inv01_googlesql_dialect_rules_no_postgres_syntax(self) -> None:
        """INV-01: Strictly GoogleSQL dialect — reject PostgreSQL types/operators."""
        pg_anti_patterns = [
            ("PostgreSQL VARCHAR", r"\bVARCHAR\s*\("),
            ("PostgreSQL SERIAL/BIGSERIAL", r"\b(?:BIG)?SERIAL\b"),
            ("PostgreSQL JSONB", r"\bJSONB\b"),
            ("PostgreSQL BYTEA", r"\bBYTEA\b"),
            ("PostgreSQL TIMESTAMPTZ", r"\bTIMESTAMPTZ\b"),
            ("PostgreSQL ILIKE", r"\bILIKE\b"),
            ("PostgreSQL :: typecast", r"[A-Za-z0-9_)\"]::[A-Za-z]"),
            ("PostgreSQL $1 positional param", r"(?<!\$)\$[1-9]\b"),
        ]
        for fname, clean in self.clean_by_name.items():
            for label, pat in pg_anti_patterns:
                self.assertIsNone(
                    re.search(pat, clean, re.IGNORECASE),
                    f"INV-01 violation in {fname}: detected forbidden {label}",
                )

        # Verify GoogleSQL Enterprise features are present in core DDL
        ddl_01 = self.clean_by_name["01_path_a_schematized.sql"]
        for required in (
            r"\bCREATE\s+LOCALITY\s+GROUP\b",
            r"\bROW\s+DELETION\s+POLICY\b",
            r"\bTOKENLIST\b",
            r"\bARRAY\s*<\s*FLOAT32\s*>",
            r"\bCREATE\s+OR\s+REPLACE\s+PROPERTY\s+GRAPH\s+SdlcGraph\b",
        ):
            self.assertRegex(ddl_01, required)

    def test_inv02_three_property_graphs_and_unique_element_aliases(self) -> None:
        """INV-02: SdlcGraph, SdlcDynamicGraph, SdlcAlgoGraph have unique element aliases."""
        self.assertEqual(
            set(self.graphs.keys()),
            {"SdlcGraph", "SdlcDynamicGraph", "SdlcAlgoGraph"},
        )

        # Verify exact alias uniqueness inside every property graph
        for gname, graph in self.graphs.items():
            seen_aliases: set[str] = set()
            for elem in graph["nodes"] + graph["edges"]:
                alias = elem["alias"]
                self.assertNotIn(
                    alias,
                    seen_aliases,
                    f"INV-02 violation in {gname}: duplicate element alias '{alias}'",
                )
                seen_aliases.add(alias)

        # Verify SdlcGraph exact counts (6 node tables, 14 edge tables) and explicit dual-projected AS aliases
        sdlc = self.graphs["SdlcGraph"]
        self.assertEqual(
            {n["alias"] for n in sdlc["nodes"]},
            {"Owners", "Services", "Apis", "Adrs", "Requirements", "DocumentChunks"},
        )
        self.assertEqual(len(sdlc["edges"]), 14)

        dual_projected_aliases = {
            e["base_name"]: e["explicit_alias"]
            for e in sdlc["edges"]
            if e["base_name"] in {"Apis", "Services", "Adrs", "Requirements"}
        }
        self.assertEqual(
            dual_projected_aliases,
            {
                "Apis": "ExposesApi",
                "Services": "TeamOwnsService",
                "Adrs": "TeamOwnsAdr",
                "Requirements": "TeamOwnsReq",
            },
        )

    def test_inv03_edge_references_match_node_alias_pk_arity_and_types(self) -> None:
        """INV-03: Every SOURCE/DESTINATION KEY REFERENCES matches exact node alias, PK & type."""
        all_relations: dict[str, dict[str, str]] = {}
        for tname, tinfo in self.tables.items():
            all_relations[tname] = tinfo["columns"]
        for vname, vinfo in self.views.items():
            all_relations[vname] = vinfo["columns"]

        for gname, graph in self.graphs.items():
            nodes_by_alias = {n["alias"]: n for n in graph["nodes"]}
            for edge in graph["edges"]:
                edge_cols = all_relations.get(edge["base_name"], {})
                self.assertTrue(
                    edge_cols,
                    f"INV-03: could not resolve schema for edge table/view '{edge['base_name']}' in {gname}",
                )
                for end_name in ("source_key", "dest_key"):
                    ref = edge[end_name]
                    self.assertIsNotNone(
                        ref, f"INV-03: {gname}.{edge['alias']} missing {end_name}"
                    )
                    target_alias = ref["ref_alias"]
                    self.assertIn(
                        target_alias,
                        nodes_by_alias,
                        f"INV-03: {gname}.{edge['alias']} {end_name} references unknown node alias '{target_alias}'",
                    )
                    target_node = nodes_by_alias[target_alias]
                    target_pk = target_node["key_cols"] or self.tables[target_node["base_name"]]["pk"]
                    self.assertEqual(
                        len(ref["cols"]),
                        len(target_pk),
                        f"INV-03: PK arity mismatch on {gname}.{edge['alias']} {end_name}",
                    )
                    self.assertEqual(
                        ref["ref_cols"],
                        target_pk,
                        f"INV-03: Referenced columns mismatch on {gname}.{edge['alias']} {end_name}",
                    )
                    target_cols = all_relations[target_node["base_name"]]
                    for ecol, tcol in zip(ref["cols"], ref["ref_cols"]):
                        self.assertIn(ecol, edge_cols, f"{edge['base_name']} missing column {ecol}")
                        self.assertIn(tcol, target_cols, f"{target_node['base_name']} missing column {tcol}")
                        self.assertEqual(
                            edge_cols[ecol],
                            target_cols[tcol],
                            f"INV-03: Type mismatch on {gname}.{edge['alias']} ({ecol}:{edge_cols[ecol]} vs {target_alias}.{tcol}:{target_cols[tcol]})",
                        )

    def test_inv04_graph_wide_property_type_uniqueness(self) -> None:
        """INV-04: No exposed property name has conflicting SQL types within any property graph."""
        for gname, graph in self.graphs.items():
            prop_types: dict[str, tuple[str, str]] = {}
            for elem in graph["nodes"] + graph["edges"]:
                base = elem["base_name"]
                if base in self.tables:
                    col_map = {
                        c: t
                        for c, t in self.tables[base]["columns"].items()
                        if c not in self.tables[base]["hidden_columns"]
                    }
                else:
                    col_map = dict(self.views[base]["columns"])

                if elem["props_mode"] == "ALL":
                    exposed = col_map
                elif elem["props_mode"] == "EXCEPT":
                    except_set = set(elem["props_except"])
                    exposed = {c: t for c, t in col_map.items() if c not in except_set}
                else:
                    exposed = {c: col_map[c] for c in elem["props_list"] if c in col_map}

                for prop_name, prop_type in exposed.items():
                    if prop_name in prop_types:
                        prev_type, prev_elem = prop_types[prop_name]
                        self.assertEqual(
                            prop_type,
                            prev_type,
                            f"INV-04 conflict in {gname}: property '{prop_name}' has type "
                            f"{prev_type} on {prev_elem} vs {prop_type} on {elem['alias']}",
                        )
                    else:
                        prop_types[prop_name] = (prop_type, elem["alias"])

    def test_inv05_views_explicit_sql_security_and_explicit_graph_keys(self) -> None:
        """INV-05: All SQL views declare SQL SECURITY INVOKER/DEFINER; view graph edges declare KEY."""
        expected_views = {
            "ChunkCitesApiView": "INVOKER",
            "ChunkCitesServiceView": "INVOKER",
            "ChunkCitesAdrView": "INVOKER",
            "ChunkCitesReqView": "INVOKER",
            "PublicApiCatalogView": "INVOKER",
            "SecurityComplianceImpactView": "INVOKER",
            "TeamScopedAssetsView": "DEFINER",
        }
        self.assertEqual(set(self.views.keys()), set(expected_views.keys()))
        for vname, expected_sec in expected_views.items():
            self.assertEqual(
                self.views[vname]["sql_security"],
                expected_sec,
                f"INV-05: View {vname} expected SQL SECURITY {expected_sec}",
            )

        # TeamScopedAssetsView must use SECURE_CONTEXT('caller_email')
        self.assertRegex(
            self.views["TeamScopedAssetsView"]["raw"],
            r"SECURE_CONTEXT\s*\(\s*'caller_email'\s*\)",
        )

        # Every view-backed graph element in SdlcGraph must declare explicit KEY (...)
        for elem in self.graphs["SdlcGraph"]["nodes"] + self.graphs["SdlcGraph"]["edges"]:
            if elem["base_name"] in self.views:
                self.assertGreater(
                    len(elem["key_cols"]),
                    0,
                    f"INV-05: View-backed element {elem['base_name']} missing explicit KEY (...)",
                )

        # SdlcAlgoGraph must be strictly table-backed (zero views)
        for elem in self.graphs["SdlcAlgoGraph"]["nodes"] + self.graphs["SdlcAlgoGraph"]["edges"]:
            self.assertIn(
                elem["base_name"],
                self.tables,
                f"SdlcAlgoGraph element {elem['base_name']} must be a base table, not a view",
            )

    def test_inv06_no_interleave_parent_cascade_plus_self_ref_cascading_fk(self) -> None:
        """INV-06: Forbid INTERLEAVE IN PARENT T ON DELETE CASCADE + enforced cascading FK to T."""
        for tname, tinfo in self.tables.items():
            inter = tinfo["interleave"]
            if inter and inter["is_parent"] and inter["on_delete"] == "CASCADE":
                parent = inter["parent_table"]
                for fk in tinfo["fks"]:
                    if fk["ref_table"] == parent and fk["on_delete_cascade"] and not fk["not_enforced"]:
                        self.fail(
                            f"INV-06 violation on {tname} ({tinfo['file']}): combines "
                            f"INTERLEAVE IN PARENT {parent} ON DELETE CASCADE with self-ref "
                            f"cascading FK to {parent}"
                        )

        # Confirm ServiceDependsOnService & AdrSupersedesAdr are standalone dual-FK tables
        for standalone_tbl in ("ServiceDependsOnService", "AdrSupersedesAdr"):
            tinfo = self.tables[standalone_tbl]
            self.assertIsNone(
                tinfo["interleave"],
                f"INV-06: {standalone_tbl} must be standalone (no INTERLEAVE IN PARENT)",
            )
            self.assertEqual(len(tinfo["fks"]), 2)
            self.assertTrue(all(fk["on_delete_cascade"] and not fk["not_enforced"] for fk in tinfo["fks"]))

        # Confirm ServiceDependsOnAlgo uses INTERLEAVE IN PARENT Services + NOT ENFORCED FK
        algo_tbl = self.tables["ServiceDependsOnAlgo"]
        self.assertIsNotNone(algo_tbl["interleave"])
        self.assertTrue(algo_tbl["interleave"]["is_parent"])
        self.assertEqual(algo_tbl["interleave"]["parent_table"], "Services")
        self.assertEqual(algo_tbl["interleave"]["on_delete"], "CASCADE")
        self.assertEqual(len(algo_tbl["fks"]), 1)
        self.assertTrue(algo_tbl["fks"][0]["not_enforced"])
        self.assertEqual(algo_tbl["fks"][0]["ref_table"], "Services")

    def test_inv07_dynamic_label_isolation_and_lowercase_constraints(self) -> None:
        """INV-07: SdlcDynamicGraph isolates DYNAMIC LABEL tables with lowercase CHECKs & EXCEPT (properties)."""
        # Static graphs MUST NOT declare DYNAMIC LABEL or DYNAMIC PROPERTIES
        for static_graph in ("SdlcGraph", "SdlcAlgoGraph"):
            for elem in self.graphs[static_graph]["nodes"] + self.graphs[static_graph]["edges"]:
                self.assertFalse(
                    elem["has_dynamic_label"],
                    f"INV-07 violation: static graph {static_graph} mixes DYNAMIC LABEL on {elem['alias']}",
                )
                self.assertFalse(
                    elem["has_dynamic_properties"],
                    f"INV-07 violation: static graph {static_graph} mixes DYNAMIC PROPERTIES on {elem['alias']}",
                )

        # SdlcDynamicGraph MUST contain ONLY dynamic tables (GraphNode & GraphEdge)
        dyn = self.graphs["SdlcDynamicGraph"]
        self.assertEqual({n["base_name"] for n in dyn["nodes"]}, {"GraphNode"})
        self.assertEqual({e["base_name"] for e in dyn["edges"]}, {"GraphEdge"})
        for elem in dyn["nodes"] + dyn["edges"]:
            self.assertTrue(elem["has_dynamic_label"])
            self.assertTrue(elem["has_dynamic_properties"])
            self.assertEqual(elem["props_mode"], "EXCEPT")
            self.assertEqual(elem["props_except"], ["properties"])

        # Lowercase label and JSON key CHECK constraints on both GraphNode and GraphEdge
        for tbl_name in ("GraphNode", "GraphEdge"):
            checks_joined = "\n".join(self.tables[tbl_name]["checks"])
            self.assertRegex(
                checks_joined,
                r"LOWER\s*\(\s*label\s*\)\s*=\s*label",
                f"INV-07: {tbl_name} missing lowercase label CHECK",
            )
            self.assertRegex(
                checks_joined,
                r"JSON_KEYS\s*\(\s*properties\s*,\s*1\s*\)",
                f"INV-07: {tbl_name} missing depth-1 JSON_KEYS lowercase CHECK",
            )

    def test_inv08_vector_dimensions_scann_index_and_ann_single_table_rules(self) -> None:
        """INV-08: 768-dim FLOAT32 vector column, ScaNN index & single-table ANN WHERE embedding IS NOT NULL."""
        ddl_01 = self.clean_by_name["01_path_a_schematized.sql"]
        self.assertRegex(
            ddl_01,
            r"\bembedding\s+ARRAY\s*<\s*FLOAT32\s*>\s*\(\s*vector_length\s*=>\s*768\s*\)",
        )

        # Every CREATE VECTOR INDEX must include WHERE embedding IS NOT NULL and omit large `content` from STORING
        vector_idx_found = 0
        for fname, stmts in self.stmts_by_name.items():
            for stmt in stmts:
                if re.search(r"\bCREATE\s+VECTOR\s+INDEX\b", stmt, re.IGNORECASE):
                    vector_idx_found += 1
                    self.assertRegex(
                        stmt,
                        r"\bON\s+DocumentChunks\s*\(\s*embedding\s*\)",
                        f"INV-08: CREATE VECTOR INDEX in {fname} must target DocumentChunks(embedding)",
                    )
                    self.assertRegex(
                        stmt,
                        r"\bWHERE\s+embedding\s+IS\s+NOT\s+NULL\b",
                        f"INV-08: CREATE VECTOR INDEX in {fname} missing WHERE embedding IS NOT NULL",
                    )
                    self.assertRegex(stmt, r"distance_type\s*=\s*'COSINE'")
                    storing_m = re.search(r"\bSTORING\s*\(([^)]+)\)", stmt, re.IGNORECASE)
                    if storing_m:
                        stored_cols = {c.strip().lower() for c in storing_m.group(1).split(",")}
                        self.assertNotIn(
                            "content",
                            stored_cols,
                            f"INV-08: ScaNN index in {fname} should not store large `content` blob",
                        )
        self.assertGreaterEqual(vector_idx_found, 1)

        # Inspect every APPROX_COSINE_DISTANCE CTE/SELECT block across all SQL files
        ann_blocks_checked = 0
        for fname, stmts in self.stmts_by_name.items():
            for stmt in stmts:
                if not re.search(r"\bAPPROX_COSINE_DISTANCE\s*\(", stmt, re.IGNORECASE):
                    continue
                # Extract each CTE or top-level SELECT containing APPROX_COSINE_DISTANCE
                for m in re.finditer(
                    r"\bSELECT\b[^;]*?\bAPPROX_COSINE_DISTANCE\s*\(.*?\bFROM\s+([A-Za-z_]\w*)\b(.*?)(?=\)\s*,|\)\s*SELECT\b|$)",
                    stmt,
                    re.IGNORECASE | re.DOTALL,
                ):
                    ann_blocks_checked += 1
                    from_tbl = m.group(1)
                    block_tail = m.group(2)
                    self.assertEqual(
                        from_tbl,
                        "DocumentChunks",
                        f"INV-08: ANN query in {fname} must scan single node table DocumentChunks, got {from_tbl}",
                    )
                    self.assertRegex(
                        block_tail,
                        r"\bWHERE\s+(?:[A-Za-z_]\w*\.)?embedding\s+IS\s+NOT\s+NULL\b",
                        f"INV-08: ANN block in {fname} missing WHERE embedding IS NOT NULL",
                    )
                    self.assertIsNone(
                        re.search(r"\bJOIN\b", block_tail, re.IGNORECASE),
                        f"INV-08: ANN block in {fname} must NOT contain JOIN inside ANN SELECT",
                    )
        self.assertGreaterEqual(ann_blocks_checked, 5)

        # Verify PDML backfill uses SAFE.ML.PREDICT + 768 outputDimensionality + throttling
        pdml = self.clean_by_name["03d_backfill_embeddings_pdml.sql"]
        self.assertRegex(pdml, r"@\{pdml_max_parallelism\s*=\s*\d+\}")
        self.assertRegex(pdml, r"\bSAFE\.ML\.PREDICT\s*\(")
        self.assertRegex(pdml, r"STRUCT\s*\(\s*768\s+AS\s+outputDimensionality\s*\)")
        self.assertRegex(pdml, r"\bWHERE\s+DocumentChunks\.embedding\s+IS\s+NULL\b")

    def test_inv09_no_row_number_and_regional_vertex_ai_asia_southeast1(self) -> None:
        """INV-09: Zero ROW_NUMBER(), RRF via UNNEST(ARRAY(...)) WITH OFFSET, Vertex AI asia-southeast1."""
        for fname, clean in self.clean_by_name.items():
            self.assertIsNone(
                re.search(r"\bROW_NUMBER\s*\(", clean, re.IGNORECASE),
                f"INV-09 violation: {fname} uses unsupported ROW_NUMBER() window function",
            )
            self.assertIsNone(
                re.search(r"\b(?:DENSE_RANK|RANK)\s*\(\s*\)\s+OVER\b", clean, re.IGNORECASE),
                f"INV-09 violation: {fname} uses unsupported analytic window ranking",
            )

        # Verify RRF & RSF idioms in 06 & 07
        for fname in ("06_hybrid_rrf_rsf_filtered.sql", "07_hero_api_blast_radius_graphrag.sql"):
            clean = self.clean_by_name[fname]
            self.assertRegex(
                clean,
                r"(?is)\bUNNEST\s*\(\s*ARRAY\s*\(.*?\bWITH\s+OFFSET\b",
                f"INV-09: {fname} must compute RRF ranks via UNNEST(ARRAY(...)) WITH OFFSET",
            )
            self.assertRegex(clean, r"60(?:\.0)?\s*\+")
        self.assertRegex(self.clean_by_name["06_hybrid_rrf_rsf_filtered.sql"], r"\bNULLIF\s*\(")

        # Verify Vertex AI remote model templates use regional asia-southeast1 (never global)
        tmpl_03b = self.clean_by_name["03b_remote_models.sql.tmpl"]
        self.assertRegex(tmpl_03b, r"CREATE\s+OR\s+REPLACE\s+MODEL\s+SdlcTextEmbedding\b")
        self.assertRegex(
            tmpl_03b,
            r"embeddings\s+STRUCT\s*<\s*statistics\s+STRUCT<[^>]+>\s*,\s*values\s+ARRAY\s*<\s*FLOAT32\s*>\s*>",
        )
        self.assertIn(
            "/locations/${VERTEX_AI_LOCATION}/publishers/google/models/gemini-embedding-001",
            tmpl_03b,
        )
        self.assertNotIn("/locations/global/", tmpl_03b.lower())

        tmpl_03c = self.clean_by_name["03c_optional_reranker_model.sql.tmpl"]
        self.assertIn("/locations/${VERTEX_AI_LOCATION}/endpoints/${RERANKER_ENDPOINT_ID}", tmpl_03c)
        self.assertNotIn("/locations/global/", tmpl_03c.lower())

        # Verify apply_postload.py renders 03b with default asia-southeast1 and rejects global
        sys.path.insert(0, str(PIPELINES_DIR))
        try:
            import apply_postload  # type: ignore[import-not-found]
        finally:
            if str(PIPELINES_DIR) in sys.path:
                sys.path.remove(str(PIPELINES_DIR))

        self.assertEqual(apply_postload.DEFAULT_VERTEX_AI_LOCATION, "asia-southeast1")
        summary = apply_postload.apply_postload(
            sql_dir=SQL_DIR,
            project="test-workshop-proj",
            dry_run=True,
            strict=True,
        )
        self.assertEqual(summary["vertex_ai_remote_model"]["location"], "asia-southeast1")
        self.assertIn(
            "/locations/asia-southeast1/publishers/google/models/gemini-embedding-001",
            summary["vertex_ai_remote_model"]["rendered_preview"],
        )
        with self.assertRaises(ValueError):
            apply_postload.validate_vertex_location("global")

    def test_fgac_graph_algorithms_and_observability_sql_guardrails(self) -> None:
        """Verify FGAC least privilege, Data Boost literal-only algos, and lock micros->seconds."""
        # FGAC scripts must never grant privileges to public or spanner_secure_context_reader
        for fgac_file in ("04_fgac_roles_views.sql", "04b_fgac_grants_rerunnable.sql"):
            clean = self.clean_by_name[fgac_file]
            self.assertIsNone(
                re.search(r"\bTO\s+ROLE\s+public\b", clean, re.IGNORECASE),
                f"{fgac_file} must never grant privileges to ROLE public",
            )
            self.assertIsNone(
                re.search(r"\bspanner_secure_context_reader\b", clean, re.IGNORECASE),
                f"{fgac_file} must never grant spanner_secure_context_reader",
            )

        # Data Boost Graph Algorithms: 3 algorithms, max_idle_time => '5m', zero @params in EXPORT DATA
        algo_stmts = self.stmts_by_name["08_graph_algorithms_criticality.sql"]
        export_stmts = [s for s in algo_stmts if re.match(r"^EXPORT\s+DATA\b", s, re.IGNORECASE)]
        self.assertEqual(len(export_stmts), 3)
        joined_exports = "\n".join(export_stmts)
        for algo_name in ("PageRank", "WeaklyConnectedComponents", "ShortestPath"):
            self.assertIn(algo_name, joined_exports)
        for exp in export_stmts:
            self.assertIn("max_idle_time => '5m'", exp)
            self.assertIsNone(
                re.search(r"@[A-Za-z_]\w*", exp),
                "Graph Algorithm EXPORT DATA statements must use literal values (no @params)",
            )

        # Lab 8 observability: microsecond-to-second lock wait conversion & SAFE_TO_JSON Studio viz
        obs = self.clean_by_name["11_observability_and_studio_viz.sql"]
        self.assertRegex(obs, r"SAFE_TO_JSON\s*\(\s*p\s*\)")
        self.assertRegex(obs, r"SPANNER_SYS\.QUERY_STATS_TOP_HOUR")
        self.assertRegex(obs, r"SPANNER_SYS\.LOCK_STATS_TOP_HOUR")
        self.assertRegex(obs, r"(?is)DISTRIBUTION_PERCENTILE\s*\(.*?\)\s*/\s*1000000")
        self.assertRegex(obs, r"SPANNER_SYS\.USER_SPLIT_POINTS")


if __name__ == "__main__":
    unittest.main()
