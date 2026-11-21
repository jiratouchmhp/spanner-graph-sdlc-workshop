#!/usr/bin/env python3
"""Validate, diff, and stage Neo4j APOC / Path A CSVs for Cloud Spanner Dataflow.

Uses Python standard library only. Performs:
  1. Exact row-count and column-schema checks against WORKSHOP_SPEC.json.
  2. 100% referential integrity validation across all 15 CSVs and 2 JSONLs.
  3. Topological invariant checks (cycle, DATA_FEED, svc-auth hub, legacy v1 AT_RISK,
     and Hero API api-payments-charge-v2 7-row blast radius).
  4. Optional diff of live Neo4j APOC exports (--diff-apoc-dir) vs canonical corpus.
  5. Optional staging (--output-dir) emitting headerless CSVs plus Cloud Spanner
     GCS_Text_to_Cloud_Spanner ImportManifest JSON files (manifest_nodes.json and
     manifest_edges.json).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

NODE_SPECS: dict[str, dict[str, Any]] = {
    "owners": {
        "relpath": "nodes/owners.csv",
        "apoc_name": "nodes_owners.csv",
        "spanner_table": "Owners",
        "label": "Owner",
        "pk": ("owner_id",),
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
    "services": {
        "relpath": "nodes/services.csv",
        "apoc_name": "nodes_services.csv",
        "spanner_table": "Services",
        "label": "Service",
        "pk": ("service_id",),
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
    "apis": {
        "relpath": "nodes/apis.csv",
        "apoc_name": "nodes_apis.csv",
        "spanner_table": "Apis",
        "label": "Api",
        "pk": ("api_id",),
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
    "adrs": {
        "relpath": "nodes/adrs.csv",
        "apoc_name": "nodes_adrs.csv",
        "spanner_table": "Adrs",
        "label": "Adr",
        "pk": ("adr_id",),
        "columns": [
            ("adr_id", "STRING"),
            ("title", "STRING"),
            ("lifecycle_status", "STRING"),
            ("owner_id", "STRING"),
            ("decision_date", "DATE"),
            ("summary", "STRING"),
        ],
    },
    "requirements": {
        "relpath": "nodes/requirements.csv",
        "apoc_name": "nodes_requirements.csv",
        "spanner_table": "Requirements",
        "label": "Requirement",
        "pk": ("req_id",),
        "columns": [
            ("req_id", "STRING"),
            ("framework", "STRING"),
            ("title", "STRING"),
            ("severity", "STRING"),
            ("owner_id", "STRING"),
            ("description", "STRING"),
        ],
    },
}

EXPLICIT_EDGE_SPECS: dict[str, dict[str, Any]] = {
    "service_calls_api": {
        "relpath": "edges/service_calls_api.csv",
        "apoc_name": "edges_service_calls_api.csv",
        "spanner_table": "ServiceCallsApi",
        "edge_type": "CALLS_API",
        "source_table": "services",
        "source_col": "service_id",
        "target_table": "apis",
        "target_col": "api_id",
        "pk": ("service_id", "api_id"),
        "columns": [
            ("service_id", "STRING"),
            ("api_id", "STRING"),
            ("call_mode", "STRING"),
            ("is_critical", "BOOL"),
            ("migration_status", "STRING"),
            ("rps_estimate", "INT64"),
        ],
    },
    "service_depends_on_service": {
        "relpath": "edges/service_depends_on_service.csv",
        "apoc_name": "edges_service_depends_on_service.csv",
        "spanner_table": "ServiceDependsOnService",
        "edge_type": "DEPENDS_ON",
        "source_table": "services",
        "source_col": "from_service_id",
        "target_table": "services",
        "target_col": "to_service_id",
        "pk": ("from_service_id", "to_service_id"),
        "columns": [
            ("from_service_id", "STRING"),
            ("to_service_id", "STRING"),
            ("dependency_type", "STRING"),
            ("is_sync", "BOOL"),
            ("criticality", "STRING"),
        ],
    },
    "adr_governs_api": {
        "relpath": "edges/adr_governs_api.csv",
        "apoc_name": "edges_adr_governs_api.csv",
        "spanner_table": "AdrGovernsApi",
        "edge_type": "GOVERNS",
        "source_table": "adrs",
        "source_col": "adr_id",
        "target_table": "apis",
        "target_col": "api_id",
        "pk": ("adr_id", "api_id"),
        "columns": [
            ("adr_id", "STRING"),
            ("api_id", "STRING"),
            ("governance_type", "STRING"),
            ("effective_date", "DATE"),
        ],
    },
    "adr_supersedes_adr": {
        "relpath": "edges/adr_supersedes_adr.csv",
        "apoc_name": "edges_adr_supersedes_adr.csv",
        "spanner_table": "AdrSupersedesAdr",
        "edge_type": "SUPERSEDES",
        "source_table": "adrs",
        "source_col": "adr_id",
        "target_table": "adrs",
        "target_col": "supersedes_adr_id",
        "pk": ("adr_id", "supersedes_adr_id"),
        "columns": [
            ("adr_id", "STRING"),
            ("supersedes_adr_id", "STRING"),
            ("superseded_date", "DATE"),
        ],
    },
    "req_applies_to_api": {
        "relpath": "edges/req_applies_to_api.csv",
        "apoc_name": "edges_req_applies_to_api.csv",
        "spanner_table": "ReqAppliesToApi",
        "edge_type": "APPLIES_TO_API",
        "source_table": "requirements",
        "source_col": "req_id",
        "target_table": "apis",
        "target_col": "api_id",
        "pk": ("req_id", "api_id"),
        "columns": [
            ("req_id", "STRING"),
            ("api_id", "STRING"),
            ("compliance_status", "STRING"),
        ],
    },
    "req_applies_to_service": {
        "relpath": "edges/req_applies_to_service.csv",
        "apoc_name": "edges_req_applies_to_service.csv",
        "spanner_table": "ReqAppliesToService",
        "edge_type": "APPLIES_TO_SERVICE",
        "source_table": "requirements",
        "source_col": "req_id",
        "target_table": "services",
        "target_col": "service_id",
        "pk": ("req_id", "service_id"),
        "columns": [
            ("req_id", "STRING"),
            ("service_id", "STRING"),
            ("compliance_status", "STRING"),
        ],
    },
}

DUAL_PROJECTED_EDGE_SPECS: dict[str, dict[str, Any]] = {
    "api_exposes_from_service": {
        "relpath": "edges/api_exposes_from_service.csv",
        "apoc_name": "edges_api_exposes_from_service.csv",
        "edge_type": "EXPOSES",
        "source_table": "services",
        "source_col": "service_id",
        "target_table": "apis",
        "target_col": "api_id",
        "pk": ("service_id", "api_id"),
        "columns": [("service_id", "STRING"), ("api_id", "STRING")],
    },
    "owner_owns_service": {
        "relpath": "edges/owner_owns_service.csv",
        "apoc_name": "edges_owner_owns_service.csv",
        "edge_type": "OWNS_SERVICE",
        "source_table": "owners",
        "source_col": "owner_id",
        "target_table": "services",
        "target_col": "service_id",
        "pk": ("owner_id", "service_id"),
        "columns": [("owner_id", "STRING"), ("service_id", "STRING")],
    },
    "owner_owns_adr": {
        "relpath": "edges/owner_owns_adr.csv",
        "apoc_name": "edges_owner_owns_adr.csv",
        "edge_type": "OWNS_ADR",
        "source_table": "owners",
        "source_col": "owner_id",
        "target_table": "adrs",
        "target_col": "adr_id",
        "pk": ("owner_id", "adr_id"),
        "columns": [("owner_id", "STRING"), ("adr_id", "STRING")],
    },
    "owner_owns_req": {
        "relpath": "edges/owner_owns_req.csv",
        "apoc_name": "edges_owner_owns_req.csv",
        "edge_type": "OWNS_REQ",
        "source_table": "owners",
        "source_col": "owner_id",
        "target_table": "requirements",
        "target_col": "req_id",
        "pk": ("owner_id", "req_id"),
        "columns": [("owner_id", "STRING"), ("req_id", "STRING")],
    },
}


def read_csv_table(path: Path, expected_cols: list[str], has_header: bool = True) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing CSV file: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        if has_header:
            reader = csv.DictReader(f)
            if list(reader.fieldnames or []) != expected_cols:
                raise ValueError(
                    f"Schema mismatch in {path}: got {reader.fieldnames}, expected {expected_cols}"
                )
            return [dict(row) for row in reader]
        reader_raw = csv.reader(f)
        rows = []
        for idx, values in enumerate(reader_raw, start=1):
            if not values:
                continue
            if len(values) != len(expected_cols):
                raise ValueError(
                    f"Column count mismatch in headerless {path} line {idx}: "
                    f"got {len(values)}, expected {len(expected_cols)}"
                )
            rows.append(dict(zip(expected_cols, values)))
        return rows


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing JSONL file: {path}")
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_no} of {path}: {exc}") from exc
    return records


def canonical_table_sha256(rows: list[dict[str, str]], cols: list[str]) -> str:
    canonical_lines = sorted("|".join(row[c].strip() for c in cols) for row in rows)
    h = hashlib.sha256()
    for line in canonical_lines:
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def validate_corpus(source_dir: Path, spec_path: Path) -> dict[str, Any]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    contract = spec["dataset_contract"]
    errors: list[str] = []

    nodes: dict[str, list[dict[str, str]]] = {}
    node_ids: dict[str, set[str]] = {}
    hashes: dict[str, str] = {}

    # 1. Validate all 5 node CSVs
    for name, meta in NODE_SPECS.items():
        cols = [c for c, _ in meta["columns"]]
        rows = read_csv_table(source_dir / meta["relpath"], cols, has_header=True)
        expected_count = contract["entity_counts"][name]
        if len(rows) != expected_count:
            errors.append(f"nodes/{name}.csv row count {len(rows)} != expected {expected_count}")
        pk_col = meta["pk"][0]
        seen: set[str] = set()
        for row in rows:
            pk = row[pk_col]
            if not pk:
                errors.append(f"nodes/{name}.csv has empty PK {pk_col}")
            if pk in seen:
                errors.append(f"nodes/{name}.csv duplicate PK: {pk}")
            seen.add(pk)
        nodes[name] = rows
        node_ids[name] = seen
        hashes[meta["spanner_table"]] = canonical_table_sha256(rows, cols)

    # Check node-table internal FKs
    for row in nodes["services"]:
        if row["owner_id"] not in node_ids["owners"]:
            errors.append(f"services.csv dangling owner_id: {row['owner_id']}")
    for row in nodes["apis"]:
        if row["service_id"] not in node_ids["services"]:
            errors.append(f"apis.csv dangling service_id: {row['service_id']}")
    for row in nodes["adrs"]:
        if row["owner_id"] not in node_ids["owners"]:
            errors.append(f"adrs.csv dangling owner_id: {row['owner_id']}")
    for row in nodes["requirements"]:
        if row["owner_id"] not in node_ids["owners"]:
            errors.append(f"requirements.csv dangling owner_id: {row['owner_id']}")

    # 2. Validate all 6 explicit edge CSVs and 4 dual-projected CSVs
    edges: dict[str, list[dict[str, str]]] = {}
    all_edge_specs = {**EXPLICIT_EDGE_SPECS, **DUAL_PROJECTED_EDGE_SPECS}
    for name, meta in all_edge_specs.items():
        cols = [c for c, _ in meta["columns"]]
        rows = read_csv_table(source_dir / meta["relpath"], cols, has_header=True)
        if name in EXPLICIT_EDGE_SPECS:
            expected_count = contract["explicit_relationship_counts"][name]
        else:
            expected_count = contract["dual_projected_edge_counts"][name]
        if len(rows) != expected_count:
            errors.append(f"edges/{name}.csv row count {len(rows)} != expected {expected_count}")

        seen_pks: set[tuple[str, ...]] = set()
        src_valid = node_ids[meta["source_table"]]
        dst_valid = node_ids[meta["target_table"]]
        for row in rows:
            pk_tuple = tuple(row[c] for c in meta["pk"])
            if pk_tuple in seen_pks:
                errors.append(f"edges/{name}.csv duplicate PK {pk_tuple}")
            seen_pks.add(pk_tuple)
            if row[meta["source_col"]] not in src_valid:
                errors.append(
                    f"edges/{name}.csv dangling source {meta['source_col']}={row[meta['source_col']]}"
                )
            if row[meta["target_col"]] not in dst_valid:
                errors.append(
                    f"edges/{name}.csv dangling target {meta['target_col']}={row[meta['target_col']]}"
                )
        edges[name] = rows
        key = meta.get("spanner_table", name)
        hashes[key] = canonical_table_sha256(rows, cols)

    # 3. Verify dual-projected CSVs match node FK columns 1:1
    expected_dual = {
        "api_exposes_from_service": {(r["service_id"], r["api_id"]) for r in nodes["apis"]},
        "owner_owns_service": {(r["owner_id"], r["service_id"]) for r in nodes["services"]},
        "owner_owns_adr": {(r["owner_id"], r["adr_id"]) for r in nodes["adrs"]},
        "owner_owns_req": {(r["owner_id"], r["req_id"]) for r in nodes["requirements"]},
    }
    for name, expected_pairs in expected_dual.items():
        meta = DUAL_PROJECTED_EDGE_SPECS[name]
        actual_pairs = {(r[meta["source_col"]], r[meta["target_col"]]) for r in edges[name]}
        if actual_pairs != expected_pairs:
            errors.append(f"Dual-projected mismatch for {name}: diff={actual_pairs ^ expected_pairs}")

    # 4. Validate all_nodes.jsonl (68) & all_edges.jsonl (234)
    nodes_jsonl = read_jsonl(source_dir / "nodes/all_nodes.jsonl")
    edges_jsonl = read_jsonl(source_dir / "edges/all_edges.jsonl")
    if len(nodes_jsonl) != contract["entity_counts"]["total_nodes"]:
        errors.append(
            f"all_nodes.jsonl count {len(nodes_jsonl)} != {contract['entity_counts']['total_nodes']}"
        )
    if len(edges_jsonl) != contract["total_logical_graph_edges"]:
        errors.append(
            f"all_edges.jsonl count {len(edges_jsonl)} != {contract['total_logical_graph_edges']}"
        )

    label_to_valid_ids = {meta["label"]: node_ids[k] for k, meta in NODE_SPECS.items()}
    seen_jsonl_nodes: set[tuple[str, str]] = set()
    for rec in nodes_jsonl:
        lbl, nid = rec.get("label"), rec.get("id")
        if lbl not in label_to_valid_ids or nid not in label_to_valid_ids[lbl]:
            errors.append(f"all_nodes.jsonl invalid node ({lbl}, {nid})")
        if (lbl, nid) in seen_jsonl_nodes:
            errors.append(f"all_nodes.jsonl duplicate node ({lbl}, {nid})")
        seen_jsonl_nodes.add((lbl, nid))

    explicit_jsonl_count = 0
    dual_jsonl_count = 0
    for rec in edges_jsonl:
        sl, sid = rec.get("source_label"), rec.get("source_id")
        tl, tid = rec.get("target_label"), rec.get("target_id")
        if sl not in label_to_valid_ids or sid not in label_to_valid_ids[sl]:
            errors.append(f"all_edges.jsonl dangling source ({sl}, {sid})")
        if tl not in label_to_valid_ids or tid not in label_to_valid_ids[tl]:
            errors.append(f"all_edges.jsonl dangling target ({tl}, {tid})")
        if rec.get("is_dual_projected"):
            dual_jsonl_count += 1
        else:
            explicit_jsonl_count += 1

    if explicit_jsonl_count != contract["explicit_relationship_table_rows"]:
        errors.append(
            f"all_edges.jsonl explicit count {explicit_jsonl_count} != {contract['explicit_relationship_table_rows']}"
        )
    if dual_jsonl_count != contract["dual_projected_edges"]:
        errors.append(
            f"all_edges.jsonl dual-projected count {dual_jsonl_count} != {contract['dual_projected_edges']}"
        )

    # 5. Validate Topological Invariants & Hero Scenario Blast Radius
    deps = edges["service_depends_on_service"]
    calls = edges["service_calls_api"]
    dep_set = {(r["from_service_id"], r["to_service_id"], r["dependency_type"]) for r in deps}

    # Intentional cycle
    for ce in contract["topological_invariants"]["intentional_cycle"]["edges"]:
        key = (ce["from_service_id"], ce["to_service_id"], ce["dependency_type"])
        if key not in dep_set:
            errors.append(f"Missing intentional cycle edge: {key}")

    # DATA_FEED consumer
    df_inv = contract["topological_invariants"]["data_feed_consumer"]
    actual_df = sorted(
        r["to_service_id"]
        for r in deps
        if r["from_service_id"] == df_inv["service_id"]
        and r["dependency_type"] == df_inv["dependency_type"]
    )
    if actual_df != sorted(df_inv["upstream_targets"]):
        errors.append(f"DATA_FEED targets {actual_df} != {sorted(df_inv['upstream_targets'])}")

    # High-fanin hub svc-auth / api-auth-token-v2
    hub_inv = contract["topological_invariants"]["high_fanin_hub"]
    auth_in_deg = sum(1 for r in deps if r["to_service_id"] == hub_inv["service_id"])
    auth_api_callers = sum(1 for r in calls if r["api_id"] == hub_inv["api_id"])
    if auth_in_deg != hub_inv["expected_service_in_degree"]:
        errors.append(f"svc-auth in-degree {auth_in_deg} != {hub_inv['expected_service_in_degree']}")
    if auth_api_callers != hub_inv["expected_api_caller_count"]:
        errors.append(
            f"api-auth-token-v2 callers {auth_api_callers} != {hub_inv['expected_api_caller_count']}"
        )

    # Legacy AT_RISK consumer
    leg_inv = contract["topological_invariants"]["legacy_at_risk_consumer"]
    leg_matches = [
        r
        for r in calls
        if r["service_id"] == leg_inv["caller_service_id"]
        and r["api_id"] == leg_inv["api_id"]
        and r["migration_status"] == leg_inv["migration_status"]
    ]
    if len(leg_matches) != 1:
        errors.append(f"Legacy AT_RISK edge missing or duplicated: {leg_matches}")

    # Hero API api-payments-charge-v2 exact 7-row blast radius via TRAIL traversal
    hero = spec["hero_scenario"]
    hero_api = hero["api_id"]
    direct_callers = sorted(r["service_id"] for r in calls if r["api_id"] == hero_api)
    if direct_callers != ["svc-billing", "svc-checkout"]:
        errors.append(f"Hero API direct callers {direct_callers} != ['svc-billing', 'svc-checkout']")

    reverse_adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in deps:
        reverse_adj[r["to_service_id"]].append((r["from_service_id"], r["dependency_type"]))

    best_hop: dict[str, int] = {}
    categories: dict[str, set[str]] = defaultdict(set)
    for d in direct_callers:
        best_hop[d] = 1
        categories[d].add("DIRECT_SYNC")

    # TRAIL walk up to 3 DEPENDS_ON edges (no repeated edges)
    def dfs_trail(curr: str, used_edges: set[tuple[str, str]], dep_types: list[str]) -> None:
        if len(used_edges) >= 3:
            return
        for upstream, dtype in reverse_adj.get(curr, []):
            edge_key = (upstream, curr)
            if edge_key in used_edges:
                continue
            new_used = used_edges | {edge_key}
            new_types = dep_types + [dtype]
            hop = len(new_used) + 1
            cat = "DATA_FEED_CONSUMER" if "DATA_FEED" in new_types else "TRANSITIVE_UPSTREAM"
            if upstream not in best_hop or hop < best_hop[upstream]:
                best_hop[upstream] = hop
            categories[upstream].add(cat)
            dfs_trail(upstream, new_used, new_types)

    for d in direct_callers:
        dfs_trail(d, set(), [])

    # Collapse category per service: DIRECT_SYNC > TRANSITIVE_UPSTREAM > DATA_FEED_CONSUMER
    cat_rank = {"DIRECT_SYNC": 1, "TRANSITIVE_UPSTREAM": 2, "DATA_FEED_CONSUMER": 3}
    crit_rank = {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4}
    svc_by_id = {r["service_id"]: r for r in nodes["services"]}
    owner_by_id = {r["owner_id"]: r for r in nodes["owners"]}
    at_risk_svcs = {r["service_id"] for r in calls if r["migration_status"] == "AT_RISK"}

    computed_rows = []
    for sid, hop in best_hop.items():
        cat = min(categories[sid], key=lambda c: cat_rank[c])
        s = svc_by_id[sid]
        o = owner_by_id[s["owner_id"]]
        computed_rows.append(
            {
                "impacted_service_id": sid,
                "service_name": s["service_name"],
                "hop_distance": hop,
                "impact_category": cat,
                "tier": s["tier"],
                "criticality": s["criticality"],
                "owner_id": s["owner_id"],
                "slack_channel": o["slack_channel"],
                "has_legacy_v1_risk": sid in at_risk_svcs,
            }
        )
    computed_rows.sort(
        key=lambda r: (
            r["hop_distance"],
            cat_rank[r["impact_category"]],
            crit_rank[r["criticality"]],
            r["impacted_service_id"],
        )
    )
    if computed_rows != hero["expected_impact_rows"]:
        errors.append(
            f"Hero blast radius mismatch!\nGot:      {computed_rows}\nExpected: {hero['expected_impact_rows']}"
        )

    # Governing ADRs & applicable REQs for hero API
    hero_adrs = sorted(
        (r["adr_id"], r["governance_type"])
        for r in edges["adr_governs_api"]
        if r["api_id"] == hero_api
    )
    expected_hero_adrs = sorted(
        (x["adr_id"], x["governance_type"]) for x in hero["governing_adrs"]
    )
    if hero_adrs != expected_hero_adrs:
        errors.append(f"Hero ADRs {hero_adrs} != {expected_hero_adrs}")

    hero_reqs = sorted(
        r["req_id"] for r in edges["req_applies_to_api"] if r["api_id"] == hero_api
    )
    if hero_reqs != sorted(hero["applicable_requirements"]):
        errors.append(f"Hero REQs {hero_reqs} != {sorted(hero['applicable_requirements'])}")

    if errors:
        raise RuntimeError("Validation failed:\n  - " + "\n  - ".join(errors))

    return {
        "status": "OK",
        "counts": {
            "total_nodes": sum(len(v) for v in nodes.values()),
            "explicit_edges": sum(len(edges[k]) for k in EXPLICIT_EDGE_SPECS),
            "dual_projected_edges": sum(len(edges[k]) for k in DUAL_PROJECTED_EDGE_SPECS),
            "total_logical_edges": len(edges_jsonl),
            "hero_impact_rows": len(computed_rows),
        },
        "table_sha256": hashes,
        "nodes": nodes,
        "edges": edges,
    }


def diff_apoc_exports(
    apoc_dir: Path,
    nodes: dict[str, list[dict[str, str]]],
    edges: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    """Compare headerless APOC CSV exports against the canonical corpus."""
    diffs: dict[str, Any] = {}
    all_tables: list[tuple[str, dict[str, Any], list[dict[str, str]]]] = []
    for name, meta in NODE_SPECS.items():
        all_tables.append((name, meta, nodes[name]))
    for name, meta in EXPLICIT_EDGE_SPECS.items():
        all_tables.append((name, meta, edges[name]))
    for name, meta in DUAL_PROJECTED_EDGE_SPECS.items():
        all_tables.append((name, meta, edges[name]))

    mismatches = 0
    for name, meta, canonical_rows in all_tables:
        apoc_file = apoc_dir / meta["apoc_name"]
        cols = [c for c, _ in meta["columns"]]
        if not apoc_file.exists():
            diffs[name] = {"status": "MISSING", "expected_file": str(apoc_file)}
            mismatches += 1
            continue
        apoc_rows = read_csv_table(apoc_file, cols, has_header=False)
        h_canon = canonical_table_sha256(canonical_rows, cols)
        h_apoc = canonical_table_sha256(apoc_rows, cols)
        match = (len(apoc_rows) == len(canonical_rows)) and (h_canon == h_apoc)
        if not match:
            mismatches += 1
        diffs[name] = {
            "status": "MATCH" if match else "DRIFT",
            "canonical_rows": len(canonical_rows),
            "apoc_rows": len(apoc_rows),
            "canonical_sha256": h_canon,
            "apoc_sha256": h_apoc,
        }
    return {"mismatches": mismatches, "tables": diffs}


def build_dataflow_manifests(gcs_prefix: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build Cloud Spanner GCS_Text_to_Cloud_Spanner ImportManifest JSON documents."""
    prefix = gcs_prefix.rstrip("/")
    nodes_manifest = {
        "dialect": "GOOGLE_STANDARD_SQL",
        "tables": [
            {
                "table_name": meta["spanner_table"],
                "file_patterns": [f"{prefix}/nodes/{meta['apoc_name']}"],
                "columns": [
                    {"column_name": col, "type_name": col_type}
                    for col, col_type in meta["columns"]
                ],
            }
            for meta in NODE_SPECS.values()
        ],
    }
    edges_manifest = {
        "dialect": "GOOGLE_STANDARD_SQL",
        "tables": [
            {
                "table_name": meta["spanner_table"],
                "file_patterns": [f"{prefix}/edges/{meta['apoc_name']}"],
                "columns": [
                    {"column_name": col, "type_name": col_type}
                    for col, col_type in meta["columns"]
                ],
            }
            for meta in EXPLICIT_EDGE_SPECS.values()
        ],
    }
    return nodes_manifest, edges_manifest


def stage_headerless_files(
    output_dir: Path,
    gcs_prefix: str,
    nodes: dict[str, list[dict[str, str]]],
    edges: dict[str, list[dict[str, str]]],
) -> dict[str, str]:
    """Write headerless CSV files and Dataflow ImportManifest JSON files."""
    nodes_out = output_dir / "nodes"
    edges_out = output_dir / "edges"
    nodes_out.mkdir(parents=True, exist_ok=True)
    edges_out.mkdir(parents=True, exist_ok=True)

    for name, meta in NODE_SPECS.items():
        cols = [c for c, _ in meta["columns"]]
        out_path = nodes_out / meta["apoc_name"]
        with out_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
            writer.writerows(nodes[name])

    for name, meta in EXPLICIT_EDGE_SPECS.items():
        cols = [c for c, _ in meta["columns"]]
        out_path = edges_out / meta["apoc_name"]
        with out_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
            writer.writerows(edges[name])

    nodes_manifest, edges_manifest = build_dataflow_manifests(gcs_prefix)
    nodes_manifest_path = output_dir / "manifest_nodes.json"
    edges_manifest_path = output_dir / "manifest_edges.json"
    nodes_manifest_path.write_text(json.dumps(nodes_manifest, indent=2) + "\n", encoding="utf-8")
    edges_manifest_path.write_text(json.dumps(edges_manifest, indent=2) + "\n", encoding="utf-8")

    return {
        "nodes_manifest": str(nodes_manifest_path),
        "edges_manifest": str(edges_manifest_path),
    }


def main() -> int:
    default_source = Path(__file__).resolve().parent
    default_spec = default_source.parent.parent / "WORKSHOP_SPEC.json"

    parser = argparse.ArgumentParser(
        description="Validate, diff, and stage Acme Commerce Neo4j APOC exports for Cloud Spanner."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=default_source,
        help="Path to data/structured directory (default: script directory).",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=default_spec,
        help="Path to WORKSHOP_SPEC.json.",
    )
    parser.add_argument(
        "--diff-apoc-dir",
        type=Path,
        default=None,
        help="Optional directory containing headerless Neo4j APOC CSV exports to diff.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional output directory for headerless CSVs + Dataflow manifests.",
    )
    parser.add_argument(
        "--gcs-prefix",
        type=str,
        default="gs://REPLACE_WITH_BUCKET/neo4j-export",
        help="GCS URI prefix written into manifest_nodes.json and manifest_edges.json.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report.",
    )
    args = parser.parse_args()

    result = validate_corpus(args.source_dir, args.spec)
    report: dict[str, Any] = {
        "status": result["status"],
        "counts": result["counts"],
        "table_sha256": result["table_sha256"],
    }

    if args.diff_apoc_dir is not None:
        diff_report = diff_apoc_exports(args.diff_apoc_dir, result["nodes"], result["edges"])
        report["apoc_diff"] = diff_report
        if diff_report["mismatches"] > 0:
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print(f"APOC diff failed with {diff_report['mismatches']} mismatched/missing tables.")
            return 1

    if args.output_dir is not None:
        staged = stage_headerless_files(
            args.output_dir, args.gcs_prefix, result["nodes"], result["edges"]
        )
        report["staged_manifests"] = staged
    else:
        nodes_m, edges_m = build_dataflow_manifests(args.gcs_prefix)
        report["dry_run_manifest_summary"] = {
            "node_tables": [t["table_name"] for t in nodes_m["tables"]],
            "explicit_edge_tables": [t["table_name"] for t in edges_m["tables"]],
        }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        c = report["counts"]
        print(
            f"[OK] Path A structured corpus verified: "
            f"{c['total_nodes']} nodes, {c['explicit_edges']} explicit edges, "
            f"{c['dual_projected_edges']} dual-projected edges "
            f"({c['total_logical_edges']} total logical edges), "
            f"{c['hero_impact_rows']}-row Hero API blast radius."
        )
        if "staged_manifests" in report:
            print(f"[OK] Staged Dataflow manifests: {report['staged_manifests']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
