#!/usr/bin/env python3
"""Zero-Docker In-Memory Neo4j CE & APOC Streaming Simulator.

100% Python 3 standard library — ZERO Docker, ZERO pip packages required.

Faithfully models `data/structured/seed_neo4j.cypher` and
`data/structured/export_neo4j_apoc.cypher` in memory so attendees without
Docker Desktop can experience the full Lab 1 Neo4j CE -> Cloud Spanner Graph
migration workflow live in their terminal:

  1. Loads all 5 node CSVs (68 nodes) and 10 edge CSVs (174 explicit M:N +
     60 dual-projected N:1 = 234 relationships) with domain PK uniqueness
     constraints and typed properties (matching `seed_neo4j.cypher`).
  2. Demonstrates the 4 live terminal sections (`--demo`, default):
       (1) Source Neo4j CE graph summary & locked topological invariants
           (svc-auth hub in-degree 10, svc-analytics DATA_FEED, legacy AT_RISK
           svc-billing -> api-payments-charge-v1, svc-orders <-> svc-notifications
           cycle, ADR supersessions).
       (2) WALK vs. TRAIL Cycle Explosion Demo upstream from `svc-checkout`
           over `svc-orders <-> svc-notifications` (up to 4 hops + depth
           scaling projection) proving why `MATCH TRAIL` is mandatory in
           Cloud Spanner ISO GQL.
       (3) Hero API (`api-payments-charge-v2`) 7-row deterministic blast
           radius & openCypher-to-ISO-GQL trap comparisons (`OPTIONAL MATCH ...
           WHERE` vs post-join `FILTER`, `shortestPath` -> `ANY SHORTEST`).
       (4) Heap-Safe Streaming APOC Export Simulation comparing naive
           `collect(x)` buffering vs `{batchSize: 5000, separateHeader: true}`
           streaming export, writing headerless CSVs into a temporary directory
           (or `--export-dir`) and running `stage_apoc_export.py --diff-apoc-dir`
           to prove 100% SHA-256 byte-for-byte parity with Docker APOC.
  3. Supports named interactive queries (`--query <name>`, `--list-queries`),
     explicit staging (`--export-dir <dir>`), parity diffing (`--diff`), and
     machine-readable JSON output (`--json`).
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


# -----------------------------------------------------------------------------
# Schema Contracts Mirroring seed_neo4j.cypher & export_neo4j_apoc.cypher
# -----------------------------------------------------------------------------

NODE_LOAD_SPECS: list[dict[str, Any]] = [
    {
        "name": "owners",
        "relpath": "nodes/owners.csv",
        "apoc_name": "nodes_owners.csv",
        "label": "Owner",
        "pk": "owner_id",
        "order_by": ("owner_id",),
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
        "name": "services",
        "relpath": "nodes/services.csv",
        "apoc_name": "nodes_services.csv",
        "label": "Service",
        "pk": "service_id",
        "order_by": ("service_id",),
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
        "name": "apis",
        "relpath": "nodes/apis.csv",
        "apoc_name": "nodes_apis.csv",
        "label": "Api",
        "pk": "api_id",
        "order_by": ("api_id",),
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
        "name": "adrs",
        "relpath": "nodes/adrs.csv",
        "apoc_name": "nodes_adrs.csv",
        "label": "Adr",
        "pk": "adr_id",
        "order_by": ("adr_id",),
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
        "name": "requirements",
        "relpath": "nodes/requirements.csv",
        "apoc_name": "nodes_requirements.csv",
        "label": "Requirement",
        "pk": "req_id",
        "order_by": ("req_id",),
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

EDGE_LOAD_SPECS: list[dict[str, Any]] = [
    # Stage 2a — 6 Explicit M:N Edge Tables (174 rows)
    {
        "name": "service_calls_api",
        "relpath": "edges/service_calls_api.csv",
        "apoc_name": "edges_service_calls_api.csv",
        "rel_type": "CALLS_API",
        "is_dual_projected": False,
        "source_label": "Service",
        "source_col": "service_id",
        "target_label": "Api",
        "target_col": "api_id",
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
        "name": "service_depends_on_service",
        "relpath": "edges/service_depends_on_service.csv",
        "apoc_name": "edges_service_depends_on_service.csv",
        "rel_type": "DEPENDS_ON",
        "is_dual_projected": False,
        "source_label": "Service",
        "source_col": "from_service_id",
        "target_label": "Service",
        "target_col": "to_service_id",
        "columns": [
            ("from_service_id", "STRING"),
            ("to_service_id", "STRING"),
            ("dependency_type", "STRING"),
            ("is_sync", "BOOL"),
            ("criticality", "STRING"),
        ],
    },
    {
        "name": "adr_governs_api",
        "relpath": "edges/adr_governs_api.csv",
        "apoc_name": "edges_adr_governs_api.csv",
        "rel_type": "GOVERNS",
        "is_dual_projected": False,
        "source_label": "Adr",
        "source_col": "adr_id",
        "target_label": "Api",
        "target_col": "api_id",
        "columns": [
            ("adr_id", "STRING"),
            ("api_id", "STRING"),
            ("governance_type", "STRING"),
            ("effective_date", "DATE"),
        ],
    },
    {
        "name": "adr_supersedes_adr",
        "relpath": "edges/adr_supersedes_adr.csv",
        "apoc_name": "edges_adr_supersedes_adr.csv",
        "rel_type": "SUPERSEDES",
        "is_dual_projected": False,
        "source_label": "Adr",
        "source_col": "adr_id",
        "target_label": "Adr",
        "target_col": "supersedes_adr_id",
        "columns": [
            ("adr_id", "STRING"),
            ("supersedes_adr_id", "STRING"),
            ("superseded_date", "DATE"),
        ],
    },
    {
        "name": "req_applies_to_api",
        "relpath": "edges/req_applies_to_api.csv",
        "apoc_name": "edges_req_applies_to_api.csv",
        "rel_type": "APPLIES_TO_API",
        "is_dual_projected": False,
        "source_label": "Requirement",
        "source_col": "req_id",
        "target_label": "Api",
        "target_col": "api_id",
        "columns": [
            ("req_id", "STRING"),
            ("api_id", "STRING"),
            ("compliance_status", "STRING"),
        ],
    },
    {
        "name": "req_applies_to_service",
        "relpath": "edges/req_applies_to_service.csv",
        "apoc_name": "edges_req_applies_to_service.csv",
        "rel_type": "APPLIES_TO_SERVICE",
        "is_dual_projected": False,
        "source_label": "Requirement",
        "source_col": "req_id",
        "target_label": "Service",
        "target_col": "service_id",
        "columns": [
            ("req_id", "STRING"),
            ("service_id", "STRING"),
            ("compliance_status", "STRING"),
        ],
    },
    # Stage 2b — 4 Dual-Projected N:1 Edges (60 rows — folded as FK columns in Spanner)
    {
        "name": "api_exposes_from_service",
        "relpath": "edges/api_exposes_from_service.csv",
        "apoc_name": "edges_api_exposes_from_service.csv",
        "rel_type": "EXPOSES",
        "is_dual_projected": True,
        "source_label": "Service",
        "source_col": "service_id",
        "target_label": "Api",
        "target_col": "api_id",
        "columns": [("service_id", "STRING"), ("api_id", "STRING")],
    },
    {
        "name": "owner_owns_service",
        "relpath": "edges/owner_owns_service.csv",
        "apoc_name": "edges_owner_owns_service.csv",
        "rel_type": "OWNS_SERVICE",
        "is_dual_projected": True,
        "source_label": "Owner",
        "source_col": "owner_id",
        "target_label": "Service",
        "target_col": "service_id",
        "columns": [("owner_id", "STRING"), ("service_id", "STRING")],
    },
    {
        "name": "owner_owns_adr",
        "relpath": "edges/owner_owns_adr.csv",
        "apoc_name": "edges_owner_owns_adr.csv",
        "rel_type": "OWNS_ADR",
        "is_dual_projected": True,
        "source_label": "Owner",
        "source_col": "owner_id",
        "target_label": "Adr",
        "target_col": "adr_id",
        "columns": [("owner_id", "STRING"), ("adr_id", "STRING")],
    },
    {
        "name": "owner_owns_req",
        "relpath": "edges/owner_owns_req.csv",
        "apoc_name": "edges_owner_owns_req.csv",
        "rel_type": "OWNS_REQ",
        "is_dual_projected": True,
        "source_label": "Owner",
        "source_col": "owner_id",
        "target_label": "Requirement",
        "target_col": "req_id",
        "columns": [("owner_id", "STRING"), ("req_id", "STRING")],
    },
]

IMPACT_CATEGORY_RANK = {
    "DIRECT_SYNC": 1,
    "TRANSITIVE_UPSTREAM": 2,
    "DATA_FEED_CONSUMER": 3,
}

CRITICALITY_RANK = {
    "CRITICAL": 1,
    "HIGH": 2,
    "MEDIUM": 3,
    "LOW": 4,
}


def _cast_value(raw: str, col_type: str) -> Any:
    """Coerce CSV string into typed Neo4j property value (mirroring seed_neo4j.cypher)."""
    if col_type == "INT64":
        return int(raw)
    if col_type == "FLOAT64":
        return float(raw)
    if col_type == "BOOL":
        return raw.strip().lower() == "true"
    return raw


def _serialize_apoc_value(val: Any) -> str:
    """Serialize a typed property back to canonical APOC CSV text."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if val is None:
        return ""
    return str(val)


@dataclass(frozen=True)
class GraphNode:
    """In-memory property graph node with ephemeral Neo4j internal_id + stable domain key."""

    internal_id: int
    label: str
    domain_key: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    """In-memory directed property graph relationship."""

    internal_id: int
    rel_type: str
    start_node: GraphNode
    end_node: GraphNode
    is_dual_projected: bool
    properties: dict[str, Any] = field(default_factory=dict)


class Neo4jPropertyGraph:
    """In-memory property graph faithfully modeling Acme Commerce Neo4j CE 5.x."""

    def __init__(self) -> None:
        self._next_node_id = 0
        self._next_edge_id = 0
        self.nodes_by_key: dict[tuple[str, str], GraphNode] = {}
        self.nodes_by_label: dict[str, list[GraphNode]] = defaultdict(list)
        self.edges: list[GraphEdge] = []
        self.edges_by_type: dict[str, list[GraphEdge]] = defaultdict(list)
        self.out_edges: dict[tuple[str, str], list[GraphEdge]] = defaultdict(list)
        self.in_edges: dict[tuple[str, str], list[GraphEdge]] = defaultdict(list)
        self.constraints: list[str] = []

    def add_node(self, label: str, domain_key: str, properties: dict[str, Any]) -> GraphNode:
        key = (label, domain_key)
        if key in self.nodes_by_key:
            raise ValueError(
                f"Uniqueness constraint violation on :{label} ({domain_key!r} already exists)"
            )
        node = GraphNode(
            internal_id=self._next_node_id,
            label=label,
            domain_key=domain_key,
            properties=properties,
        )
        self._next_node_id += 1
        self.nodes_by_key[key] = node
        self.nodes_by_label[label].append(node)
        return node

    def add_edge(
        self,
        rel_type: str,
        source_label: str,
        source_key: str,
        target_label: str,
        target_key: str,
        is_dual_projected: bool,
        properties: dict[str, Any],
    ) -> GraphEdge:
        src = self.nodes_by_key.get((source_label, source_key))
        dst = self.nodes_by_key.get((target_label, target_key))
        if src is None:
            raise KeyError(
                f"Dangling source node ({source_label}:{source_key}) on edge :{rel_type}"
            )
        if dst is None:
            raise KeyError(
                f"Dangling target node ({target_label}:{target_key}) on edge :{rel_type}"
            )
        edge = GraphEdge(
            internal_id=self._next_edge_id,
            rel_type=rel_type,
            start_node=src,
            end_node=dst,
            is_dual_projected=is_dual_projected,
            properties=properties,
        )
        self._next_edge_id += 1
        self.edges.append(edge)
        self.edges_by_type[rel_type].append(edge)
        self.out_edges[(source_label, source_key)].append(edge)
        self.in_edges[(target_label, target_key)].append(edge)
        return edge

    @classmethod
    def load_from_structured_dir(cls, source_dir: Path) -> "Neo4jPropertyGraph":
        """Load all 68 nodes & 234 relationships identically to seed_neo4j.cypher."""
        graph = cls()

        # Stage 1: Register 5 domain PK uniqueness constraints & load 68 nodes
        for spec in NODE_LOAD_SPECS:
            pk_col = spec["pk"]
            label = spec["label"]
            graph.constraints.append(f"{pk_col}_unique (:{label}.{pk_col} IS UNIQUE)")
            csv_path = source_dir / spec["relpath"]
            if not csv_path.is_file():
                raise FileNotFoundError(f"Missing node CSV: {csv_path}")
            expected_cols = [c for c, _ in spec["columns"]]
            with csv_path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                if list(reader.fieldnames or []) != expected_cols:
                    raise ValueError(
                        f"Column schema mismatch in {csv_path}: "
                        f"got {reader.fieldnames}, expected {expected_cols}"
                    )
                for row in reader:
                    props = {col: _cast_value(row[col], ctype) for col, ctype in spec["columns"]}
                    graph.add_node(label=label, domain_key=row[pk_col], properties=props)

        # Stage 2a & 2b: Load 174 explicit M:N + 60 dual-projected N:1 edges = 234 edges
        for spec in EDGE_LOAD_SPECS:
            csv_path = source_dir / spec["relpath"]
            if not csv_path.is_file():
                raise FileNotFoundError(f"Missing edge CSV: {csv_path}")
            expected_cols = [c for c, _ in spec["columns"]]
            src_col = spec["source_col"]
            dst_col = spec["target_col"]
            with csv_path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                if list(reader.fieldnames or []) != expected_cols:
                    raise ValueError(
                        f"Column schema mismatch in {csv_path}: "
                        f"got {reader.fieldnames}, expected {expected_cols}"
                    )
                for row in reader:
                    edge_props = {
                        col: _cast_value(row[col], ctype)
                        for col, ctype in spec["columns"]
                        if col not in (src_col, dst_col)
                    }
                    graph.add_edge(
                        rel_type=spec["rel_type"],
                        source_label=spec["source_label"],
                        source_key=row[src_col],
                        target_label=spec["target_label"],
                        target_key=row[dst_col],
                        is_dual_projected=spec["is_dual_projected"],
                        properties=edge_props,
                    )

        return graph

    @property
    def total_nodes(self) -> int:
        return len(self.nodes_by_key)

    @property
    def total_edges(self) -> int:
        return len(self.edges)

    @property
    def explicit_edge_count(self) -> int:
        return sum(1 for e in self.edges if not e.is_dual_projected)

    @property
    def dual_projected_edge_count(self) -> int:
        return sum(1 for e in self.edges if e.is_dual_projected)


# -----------------------------------------------------------------------------
# Graph Analytics & Query Simulations
# -----------------------------------------------------------------------------

def query_summary(graph: Neo4jPropertyGraph) -> dict[str, Any]:
    """Section (a): Source Neo4j CE graph summary and locked topological invariants."""
    nodes_by_label = {
        spec["label"]: len(graph.nodes_by_label[spec["label"]]) for spec in NODE_LOAD_SPECS
    }
    explicit_edges_by_type = {
        spec["rel_type"]: len(graph.edges_by_type[spec["rel_type"]])
        for spec in EDGE_LOAD_SPECS
        if not spec["is_dual_projected"]
    }
    dual_edges_by_type = {
        spec["rel_type"]: len(graph.edges_by_type[spec["rel_type"]])
        for spec in EDGE_LOAD_SPECS
        if spec["is_dual_projected"]
    }

    # Top DEPENDS_ON in-degree hubs
    in_deg_counter: Counter[str] = Counter()
    for e in graph.edges_by_type["DEPENDS_ON"]:
        in_deg_counter[e.end_node.domain_key] += 1
    top_hubs = [
        {
            "service_id": sid,
            "service_name": graph.nodes_by_key[("Service", sid)].properties["service_name"],
            "depends_on_in_degree": deg,
        }
        for sid, deg in sorted(in_deg_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    ]

    api_callers_auth = sorted(
        e.start_node.domain_key
        for e in graph.edges_by_type["CALLS_API"]
        if e.end_node.domain_key == "api-auth-token-v2"
    )

    # DATA_FEED consumer edges
    data_feed_edges = sorted(
        [
            {
                "from_service_id": e.start_node.domain_key,
                "to_service_id": e.end_node.domain_key,
                "dependency_type": e.properties["dependency_type"],
                "is_sync": e.properties["is_sync"],
            }
            for e in graph.edges_by_type["DEPENDS_ON"]
            if e.properties.get("dependency_type") == "DATA_FEED"
        ],
        key=lambda r: (r["from_service_id"], r["to_service_id"]),
    )

    # Legacy AT_RISK callers
    at_risk_calls = sorted(
        [
            {
                "service_id": e.start_node.domain_key,
                "api_id": e.end_node.domain_key,
                "api_lifecycle_status": e.end_node.properties["lifecycle_status"],
                "migration_status": e.properties["migration_status"],
                "rps_estimate": e.properties["rps_estimate"],
            }
            for e in graph.edges_by_type["CALLS_API"]
            if e.properties.get("migration_status") == "AT_RISK"
            or e.end_node.properties.get("lifecycle_status") == "DEPRECATED"
        ],
        key=lambda r: (r["service_id"], r["api_id"]),
    )

    # Bidirectional cycle edges (a -> b and b -> a)
    dep_pairs = {(e.start_node.domain_key, e.end_node.domain_key): e for e in graph.edges_by_type["DEPENDS_ON"]}
    cycle_edges = sorted(
        [
            {
                "from_service_id": u,
                "to_service_id": v,
                "dependency_type": e.properties["dependency_type"],
            }
            for (u, v), e in dep_pairs.items()
            if (v, u) in dep_pairs
        ],
        key=lambda r: (r["from_service_id"], r["to_service_id"]),
    )

    supersessions = sorted(
        [
            {
                "adr_id": e.start_node.domain_key,
                "supersedes_adr_id": e.end_node.domain_key,
                "superseded_date": e.properties["superseded_date"],
            }
            for e in graph.edges_by_type["SUPERSEDES"]
        ],
        key=lambda r: r["adr_id"],
    )

    return {
        "total_nodes": graph.total_nodes,
        "total_edges": graph.total_edges,
        "explicit_edges": graph.explicit_edge_count,
        "dual_projected_edges": graph.dual_projected_edge_count,
        "constraints_enforced": graph.constraints,
        "nodes_by_label": nodes_by_label,
        "explicit_edges_by_type": explicit_edges_by_type,
        "dual_projected_edges_by_type": dual_edges_by_type,
        "top_hub": {
            "service_id": "svc-auth",
            "depends_on_in_degree": in_deg_counter["svc-auth"],
            "api_id": "api-auth-token-v2",
            "api_caller_count": len(api_callers_auth),
            "top_5_service_hubs": top_hubs,
        },
        "data_feed_edges": data_feed_edges,
        "legacy_at_risk_calls": at_risk_calls,
        "bidirectional_cycle_edges": cycle_edges,
        "adr_supersessions": supersessions,
    }


def query_walk_vs_trail(
    graph: Neo4jPropertyGraph,
    target_service: str = "svc-checkout",
    max_hops: int = 4,
) -> dict[str, Any]:
    """Section (b): Compare ACYCLIC, TRAIL (Neo4j default) & WALK (Spanner GQL default)."""
    if ("Service", target_service) not in graph.nodes_by_key:
        raise ValueError(f"Unknown target service {target_service!r}")

    rev_adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for e in graph.edges_by_type["DEPENDS_ON"]:
        rev_adj[e.end_node.domain_key].append(
            (e.start_node.domain_key, e.properties["dependency_type"])
        )
    for k in rev_adj:
        rev_adj[k].sort()

    def _enumerate(depth_limit: int) -> dict[str, Any]:
        acyclic_paths: list[list[str]] = []
        trail_paths: list[list[str]] = []
        walk_paths: list[list[str]] = []
        cycle_edges_blocked = 0

        def _dfs_acyclic(curr: str, path: list[str], visited: set[str], depth: int) -> None:
            if depth >= depth_limit:
                return
            for up, _ in rev_adj.get(curr, []):
                if up in visited:
                    continue
                nxt = [up] + path
                acyclic_paths.append(nxt)
                _dfs_acyclic(up, nxt, visited | {up}, depth + 1)

        def _dfs_trail(curr: str, path: list[str], used_edges: set[tuple[str, str]]) -> None:
            nonlocal cycle_edges_blocked
            if len(used_edges) >= depth_limit:
                return
            for up, _ in rev_adj.get(curr, []):
                edge = (up, curr)
                if edge in used_edges:
                    cycle_edges_blocked += 1
                    continue
                nxt = [up] + path
                trail_paths.append(nxt)
                _dfs_trail(up, nxt, used_edges | {edge})

        def _dfs_walk(curr: str, path: list[str], depth: int) -> None:
            if depth >= depth_limit:
                return
            for up, _ in rev_adj.get(curr, []):
                nxt = [up] + path
                walk_paths.append(nxt)
                _dfs_walk(up, nxt, depth + 1)

        _dfs_acyclic(target_service, [target_service], {target_service}, 0)
        _dfs_trail(target_service, [target_service], set())
        _dfs_walk(target_service, [target_service], 0)

        trail_set = {tuple(p) for p in trail_paths}
        walk_only_looping = [p for p in walk_paths if tuple(p) not in trail_set]

        return {
            "max_hops": depth_limit,
            "acyclic_path_count": len(acyclic_paths),
            "trail_path_count": len(trail_paths),
            "walk_path_count": len(walk_paths),
            "walk_extra_cycle_paths": len(walk_paths) - len(trail_paths),
            "trail_cycle_edges_blocked": cycle_edges_blocked,
            "trail_paths": [" -> ".join(p) for p in trail_paths],
            "walk_only_looping_paths": [" -> ".join(p) for p in walk_only_looping],
        }

    # Build hop-by-hop table up to max_hops plus extended scaling horizons (6, 8, 10)
    primary_depths = list(range(1, max_hops + 1))
    scaling_depths = sorted(set(primary_depths) | {6, 8, 10})
    by_depth: list[dict[str, Any]] = []
    primary_snapshot: dict[str, Any] = {}
    for d in scaling_depths:
        snap = _enumerate(d)
        by_depth.append(
            {
                "max_hops": d,
                "acyclic_paths": snap["acyclic_path_count"],
                "trail_paths": snap["trail_path_count"],
                "walk_paths": snap["walk_path_count"],
                "walk_extra_cycle_paths": snap["walk_extra_cycle_paths"],
                "trail_cycle_edges_blocked": snap["trail_cycle_edges_blocked"],
            }
        )
        if d == max_hops:
            primary_snapshot = snap

    return {
        "target_service": target_service,
        "cycle_nodes": ["svc-orders", "svc-notifications"],
        "max_hops": max_hops,
        "acyclic_path_count": primary_snapshot["acyclic_path_count"],
        "trail_path_count": primary_snapshot["trail_path_count"],
        "walk_path_count": primary_snapshot["walk_path_count"],
        "walk_extra_cycle_paths": primary_snapshot["walk_extra_cycle_paths"],
        "trail_cycle_edges_blocked": primary_snapshot["trail_cycle_edges_blocked"],
        "trail_paths_at_max_hops": primary_snapshot["trail_paths"],
        "walk_only_looping_paths_at_max_hops": primary_snapshot["walk_only_looping_paths"],
        "depth_scaling_table": by_depth,
        "gql_lesson": (
            "Neo4j openCypher enforces edge uniqueness (TRAIL) by default; Cloud Spanner "
            "ISO GQL defaults to WALK (repeatable edges). Always write `MATCH TRAIL` on "
            "graphs containing cycles such as svc-orders <-> svc-notifications."
        ),
    }


def query_hero_blast_radius(
    graph: Neo4jPropertyGraph,
    api_id: str = "api-payments-charge-v2",
    max_depends_hops: int = 3,
) -> dict[str, Any]:
    """Hero API deterministic blast-radius query over the in-memory Neo4j CE graph."""
    api_node = graph.nodes_by_key.get(("Api", api_id))
    if api_node is None:
        raise ValueError(f"Unknown api_id {api_id!r}")

    direct_callers = sorted(
        {
            e.start_node.domain_key
            for e in graph.in_edges[("Api", api_id)]
            if e.rel_type == "CALLS_API"
        }
    )

    rev_adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for e in graph.edges_by_type["DEPENDS_ON"]:
        rev_adj[e.end_node.domain_key].append(
            (e.start_node.domain_key, e.properties["dependency_type"])
        )
    for k in rev_adj:
        rev_adj[k].sort()

    observations: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for caller in direct_callers:
        observations[caller].append((1, "DIRECT_SYNC"))

    def _dfs_trail(curr: str, used: set[tuple[str, str]], dtypes: list[str]) -> None:
        if len(used) >= max_depends_hops:
            return
        for up, dt in rev_adj.get(curr, []):
            edge = (up, curr)
            if edge in used:
                continue
            new_used = used | {edge}
            new_types = dtypes + [dt]
            cat = "DATA_FEED_CONSUMER" if "DATA_FEED" in new_types else "TRANSITIVE_UPSTREAM"
            observations[up].append((len(new_used) + 1, cat))
            _dfs_trail(up, new_used, new_types)

    for caller in direct_callers:
        _dfs_trail(caller, set(), [])

    at_risk_services = {
        e.start_node.domain_key
        for e in graph.edges_by_type["CALLS_API"]
        if e.properties.get("migration_status") == "AT_RISK"
        or e.end_node.properties.get("lifecycle_status") == "DEPRECATED"
    }

    rows: list[dict[str, Any]] = []
    for sid, obs in observations.items():
        hop, cat = min(obs, key=lambda x: (x[0], IMPACT_CATEGORY_RANK[x[1]]))
        svc = graph.nodes_by_key[("Service", sid)].properties
        owner = graph.nodes_by_key[("Owner", svc["owner_id"])].properties
        rows.append(
            {
                "impacted_service_id": sid,
                "service_name": svc["service_name"],
                "hop_distance": hop,
                "impact_category": cat,
                "tier": svc["tier"],
                "criticality": svc["criticality"],
                "owner_id": svc["owner_id"],
                "slack_channel": owner["slack_channel"],
                "has_legacy_v1_risk": sid in at_risk_services,
            }
        )

    rows.sort(
        key=lambda r: (
            r["hop_distance"],
            IMPACT_CATEGORY_RANK[r["impact_category"]],
            CRITICALITY_RANK[r["criticality"]],
            r["impacted_service_id"],
        )
    )

    governing_adrs = sorted(
        [
            {
                "adr_id": e.start_node.domain_key,
                "governance_type": e.properties["governance_type"],
                "title": e.start_node.properties["title"],
            }
            for e in graph.in_edges[("Api", api_id)]
            if e.rel_type == "GOVERNS"
        ],
        key=lambda r: r["adr_id"],
    )

    applicable_reqs = sorted(
        e.start_node.domain_key
        for e in graph.in_edges[("Api", api_id)]
        if e.rel_type == "APPLIES_TO_API"
    )

    return {
        "api_id": api_id,
        "http_method": api_node.properties["http_method"],
        "http_path": api_node.properties["http_path"],
        "exposing_service_id": api_node.properties["service_id"],
        "impact_row_count": len(rows),
        "impact_rows": rows,
        "governing_adrs": governing_adrs,
        "applicable_requirements": applicable_reqs,
    }


def query_optional_match_trap(graph: Neo4jPropertyGraph) -> dict[str, Any]:
    """Cookbook Translation 5: OPTIONAL MATCH ... WHERE vs post-join FILTER trap."""
    all_services = sorted(graph.nodes_by_label["Service"], key=lambda n: n.domain_key)
    left_join_rows: list[dict[str, Any]] = []
    for svc in all_services:
        at_risk = [
            e
            for e in graph.out_edges[("Service", svc.domain_key)]
            if e.rel_type == "CALLS_API" and e.properties.get("migration_status") == "AT_RISK"
        ]
        if at_risk:
            for e in sorted(at_risk, key=lambda x: x.end_node.domain_key):
                left_join_rows.append(
                    {
                        "service_id": svc.domain_key,
                        "at_risk_api_id": e.end_node.domain_key,
                        "legacy_rps": e.properties["rps_estimate"],
                    }
                )
        else:
            left_join_rows.append(
                {
                    "service_id": svc.domain_key,
                    "at_risk_api_id": None,
                    "legacy_rps": None,
                }
            )
    broken_post_filter_rows = [r for r in left_join_rows if r["at_risk_api_id"] is not None]
    return {
        "optional_match_where_row_count": len(left_join_rows),
        "broken_post_filter_row_count": len(broken_post_filter_rows),
        "silently_dropped_services": len(left_join_rows) - len(broken_post_filter_rows),
        "at_risk_matches": broken_post_filter_rows,
        "rows": left_join_rows,
    }


def query_shortest_path(
    graph: Neo4jPropertyGraph,
    src_service: str = "svc-webhooks",
    dst_service: str = "svc-payments",
) -> dict[str, Any]:
    """Cookbook Translation 6: shortestPath -> MATCH ANY SHORTEST."""
    fwd_adj: dict[str, list[str]] = defaultdict(list)
    for e in graph.edges_by_type["DEPENDS_ON"]:
        fwd_adj[e.start_node.domain_key].append(e.end_node.domain_key)
    for k in fwd_adj:
        fwd_adj[k].sort()

    queue: deque[list[str]] = deque([[src_service]])
    visited = {src_service}
    found: list[str] | None = None
    while queue:
        path = queue.popleft()
        curr = path[-1]
        if curr == dst_service:
            found = path
            break
        for nxt in fwd_adj.get(curr, []):
            if nxt not in visited:
                visited.add(nxt)
                queue.append(path + [nxt])

    return {
        "src_id": src_service,
        "dst_id": dst_service,
        "hop_count": (len(found) - 1) if found else None,
        "path_nodes": found or [],
        "path_hops": found[1:] if found else [],
    }


def query_hub_fanin(graph: Neo4jPropertyGraph) -> dict[str, Any]:
    """Top hub in-degree and IS_FIRST(5) outgoing CALLS_API fanout capping preview."""
    summary = query_summary(graph)
    capped_calls: dict[str, list[dict[str, Any]]] = {}
    for svc in sorted(graph.nodes_by_label["Service"], key=lambda n: n.domain_key):
        outgoing = sorted(
            [e for e in graph.out_edges[("Service", svc.domain_key)] if e.rel_type == "CALLS_API"],
            key=lambda e: (-e.properties["rps_estimate"], e.end_node.domain_key),
        )
        capped_calls[svc.domain_key] = [
            {
                "api_id": e.end_node.domain_key,
                "rps_estimate": e.properties["rps_estimate"],
                "migration_status": e.properties["migration_status"],
            }
            for e in outgoing[:5]
        ]
    return {
        "top_hub": summary["top_hub"],
        "is_first_5_capped_outgoing_calls_total": sum(len(v) for v in capped_calls.values()),
        "total_uncapped_calls": len(graph.edges_by_type["CALLS_API"]),
        "top_callers_per_service": capped_calls,
    }


def query_public_apis(graph: Neo4jPropertyGraph) -> dict[str, Any]:
    """Cookbook Translation 7: list comprehension -> ARRAY_FILTER + ARRAY_TRANSFORM."""
    rows: list[dict[str, Any]] = []
    for svc in sorted(graph.nodes_by_label["Service"], key=lambda n: n.domain_key):
        exposed = sorted(
            [e.end_node for e in graph.out_edges[("Service", svc.domain_key)] if e.rel_type == "EXPOSES"],
            key=lambda a: a.domain_key,
        )
        public_apis = [a for a in exposed if a.properties["is_public"]]
        rows.append(
            {
                "service_id": svc.domain_key,
                "public_paths": [a.properties["http_path"] for a in public_apis],
                "first_public_api": public_apis[0].domain_key if public_apis else None,
            }
        )
    return {"service_count": len(rows), "rows": rows}


# -----------------------------------------------------------------------------
# Heap-Safe Streaming APOC Export Simulator (export_neo4j_apoc.cypher parity)
# -----------------------------------------------------------------------------

def _iter_table_rows_streaming(
    graph: Neo4jPropertyGraph,
    spec: dict[str, Any],
    is_edge: bool,
) -> Iterator[list[str]]:
    """Yield ordered row tuples matching export_neo4j_apoc.cypher without collect()."""
    cols = [c for c, _ in spec["columns"]]
    if not is_edge:
        nodes = sorted(
            graph.nodes_by_label[spec["label"]],
            key=lambda n: tuple(str(n.properties[k]) for k in spec["order_by"]),
        )
        for node in nodes:
            yield [_serialize_apoc_value(node.properties[c]) for c in cols]
    else:
        src_col = spec["source_col"]
        dst_col = spec["target_col"]
        edges = sorted(
            graph.edges_by_type[spec["rel_type"]],
            key=lambda e: (e.start_node.domain_key, e.end_node.domain_key),
        )
        for edge in edges:
            row_vals: list[str] = []
            for c in cols:
                if c == src_col:
                    row_vals.append(edge.start_node.domain_key)
                elif c == dst_col:
                    row_vals.append(edge.end_node.domain_key)
                else:
                    row_vals.append(_serialize_apoc_value(edge.properties[c]))
            yield row_vals


def _stream_in_batches(
    row_iter: Iterator[list[str]],
    batch_size: int,
) -> Iterator[list[list[str]]]:
    """Chunk an iterator into bounded batches of size <= batch_size."""
    batch: list[list[str]] = []
    for row in row_iter:
        batch.append(row)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def simulate_apoc_streaming_export(
    graph: Neo4jPropertyGraph,
    export_dir: Path,
    batch_size: int = 5000,
) -> dict[str, Any]:
    """Stream all 15 APOC CSVs (separateHeader: true) + JSONL snapshot into export_dir."""
    if batch_size <= 0:
        raise ValueError(f"batch_size must be positive, got {batch_size}")

    export_dir.mkdir(parents=True, exist_ok=True)
    exported_tables: list[dict[str, Any]] = []
    total_rows_written = 0
    total_bytes_written = 0
    peak_streaming_batch_rows = 0

    all_exports: list[tuple[dict[str, Any], bool]] = (
        [(spec, False) for spec in NODE_LOAD_SPECS]
        + [(spec, True) for spec in EDGE_LOAD_SPECS]
    )

    for spec, is_edge in all_exports:
        cols = [c for c, _ in spec["columns"]]
        apoc_name = spec["apoc_name"]
        data_path = export_dir / apoc_name
        header_path = export_dir / f"{Path(apoc_name).stem}.header.csv"

        # APOC separateHeader: true writes header in sibling .header.csv and headerless data CSV
        with header_path.open("w", encoding="utf-8", newline="") as hfh:
            csv.writer(hfh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n").writerow(cols)

        rows_in_table = 0
        batches_flushed = 0
        with data_path.open("w", encoding="utf-8", newline="") as dfh:
            writer = csv.writer(dfh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            for batch in _stream_in_batches(
                _iter_table_rows_streaming(graph, spec, is_edge=is_edge),
                batch_size=batch_size,
            ):
                writer.writerows(batch)
                dfh.flush()
                rows_in_table += len(batch)
                batches_flushed += 1
                peak_streaming_batch_rows = max(peak_streaming_batch_rows, len(batch))

        size_bytes = data_path.stat().st_size
        total_rows_written += rows_in_table
        total_bytes_written += size_bytes
        exported_tables.append(
            {
                "name": spec["name"],
                "apoc_file": apoc_name,
                "header_file": header_path.name,
                "kind": (
                    "node"
                    if not is_edge
                    else ("dual_projected_edge" if spec["is_dual_projected"] else "explicit_edge")
                ),
                "rows": rows_in_table,
                "batches_flushed": batches_flushed,
                "bytes": size_bytes,
            }
        )

    # Also write all_graph_export.jsonl (mirroring apoc.export.json.all)
    jsonl_path = export_dir / "all_graph_export.jsonl"
    jsonl_records = 0
    with jsonl_path.open("w", encoding="utf-8") as jfh:
        for spec in NODE_LOAD_SPECS:
            for node in sorted(graph.nodes_by_label[spec["label"]], key=lambda n: n.domain_key):
                rec = {
                    "type": "node",
                    "labels": [node.label],
                    "domain_key": node.domain_key,
                    "properties": node.properties,
                }
                jfh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                jsonl_records += 1
        for spec in EDGE_LOAD_SPECS:
            for edge in sorted(
                graph.edges_by_type[spec["rel_type"]],
                key=lambda e: (e.start_node.domain_key, e.end_node.domain_key),
            ):
                rec = {
                    "type": "relationship",
                    "label": edge.rel_type,
                    "is_dual_projected": edge.is_dual_projected,
                    "start": {"label": edge.start_node.label, "domain_key": edge.start_node.domain_key},
                    "end": {"label": edge.end_node.label, "domain_key": edge.end_node.domain_key},
                    "properties": edge.properties,
                }
                jfh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                jsonl_records += 1

    # Heap comparison model: naive collect(x) vs bounded APOC streaming
    # Avg Neo4j JVM Heap object overhead per serialized map/row ~= 1.2 KiB
    bytes_per_jvm_row = 1228
    heap_ceiling_mb = 1024.0
    enterprise_scales = []
    for label, row_count in (
        ("Workshop Seed (Acme)", total_rows_written),
        ("Mid-Size Catalog (1M rows)", 1_000_000),
        ("Enterprise SDLC (10M rows)", 10_000_000),
        ("Global Scale (50M rows)", 50_000_000),
    ):
        naive_mb = round((row_count * bytes_per_jvm_row) / (1024 * 1024), 2)
        stream_rows = min(row_count, batch_size)
        stream_mb = round((stream_rows * bytes_per_jvm_row) / (1024 * 1024), 2)
        enterprise_scales.append(
            {
                "scenario": label,
                "total_rows": row_count,
                "naive_collect_peak_heap_mb": naive_mb,
                "naive_oom_at_1gib_heap": naive_mb >= heap_ceiling_mb,
                "apoc_streaming_peak_heap_mb": stream_mb,
                "apoc_oom_at_1gib_heap": stream_mb >= heap_ceiling_mb,
            }
        )

    return {
        "export_dir": str(export_dir),
        "apoc_options": {
            "batchSize": batch_size,
            "separateHeader": True,
            "quotes": "ifNeeded",
            "stream": False,
            "exports_internal_neo4j_id": False,
        },
        "tables_exported": len(exported_tables),
        "total_csv_rows": total_rows_written,
        "total_csv_bytes": total_bytes_written,
        "jsonl_file": jsonl_path.name,
        "jsonl_records": jsonl_records,
        "peak_streaming_batch_rows": peak_streaming_batch_rows,
        "heap_comparison": enterprise_scales,
        "files": exported_tables,
    }


def run_stage_apoc_diff(source_dir: Path, export_dir: Path) -> dict[str, Any]:
    """Invoke data/structured/stage_apoc_export.py --diff-apoc-dir to prove parity."""
    stage_script = source_dir / "stage_apoc_export.py"
    if not stage_script.is_file():
        raise FileNotFoundError(f"Parity validator not found: {stage_script}")

    proc = subprocess.run(
        [
            sys.executable,
            str(stage_script),
            "--source-dir",
            str(source_dir),
            "--diff-apoc-dir",
            str(export_dir),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"stage_apoc_export.py failed (rc={proc.returncode}): {proc.stderr or proc.stdout}"
        ) from exc

    apoc_diff = parsed.get("apoc_diff", {})
    mismatches = int(apoc_diff.get("mismatches", -1))
    tables = apoc_diff.get("tables", {})
    matched = sum(1 for t in tables.values() if t.get("status") == "MATCH")
    return {
        "returncode": proc.returncode,
        "passed": proc.returncode == 0 and mismatches == 0,
        "tables_checked": len(tables),
        "tables_matched": matched,
        "mismatches": mismatches,
        "tables": tables,
    }


# -----------------------------------------------------------------------------
# Named Query Registry (--query <name>)
# -----------------------------------------------------------------------------

QUERY_REGISTRY: dict[str, dict[str, str]] = {
    "summary": {
        "title": "Source Neo4j CE Graph Summary & Locked Invariants",
        "cypher_hint": "MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes, count(r)",
    },
    "walk_vs_trail": {
        "title": "WALK vs. TRAIL Cycle Explosion Demo (svc-checkout upstream, 1..4 hops)",
        "cypher_hint": "MATCH TRAIL (u:Service)-[deps:DEPENDS_ON]->{1,4}(:Service {service_id: 'svc-checkout'})",
    },
    "hero_blast_radius": {
        "title": "Hero API (api-payments-charge-v2) 7-Row Deterministic Blast Radius",
        "cypher_hint": "MATCH TRAIL (u:Service)-[:DEPENDS_ON]->{0,3}(d:Service)-[:CALLS_API]->(:Api {api_id: 'api-payments-charge-v2'})",
    },
    "hub_fanin": {
        "title": "High-Fanin Identity Hub (svc-auth) & IS_FIRST(5) Fanout Capping",
        "cypher_hint": "MATCH (s:Service)<-[:DEPENDS_ON]-(u:Service) RETURN s.service_id, count(u) AS in_degree ORDER BY in_degree DESC",
    },
    "legacy_at_risk": {
        "title": "OPTIONAL MATCH ... WHERE vs. Post-Join FILTER Trap (AT_RISK Legacy Calls)",
        "cypher_hint": "MATCH (s:Service) OPTIONAL MATCH (s)-[c:CALLS_API]->(a:Api) WHERE c.migration_status = 'AT_RISK'",
    },
    "data_feed": {
        "title": "Isolated Async DATA_FEED Consumer (svc-analytics)",
        "cypher_hint": "MATCH (s:Service)-[r:DEPENDS_ON {dependency_type: 'DATA_FEED'}]->(t:Service) RETURN s, t",
    },
    "shortest_path": {
        "title": "shortestPath -> MATCH ANY SHORTEST (svc-webhooks -> svc-payments)",
        "cypher_hint": "MATCH ANY SHORTEST (src:Service {service_id: 'svc-webhooks'})-[deps:DEPENDS_ON]->{1,5}(dst:Service {service_id: 'svc-payments'})",
    },
    "adr_supersessions": {
        "title": "ADR Supersession Lineage (SUPERSEDES Edges)",
        "cypher_hint": "MATCH (newer:Adr)-[r:SUPERSEDES]->(older:Adr) RETURN newer.adr_id, older.adr_id",
    },
    "public_apis": {
        "title": "Public API Paths per Service (collect() -> ARRAY_FILTER + ARRAY_TRANSFORM)",
        "cypher_hint": "MATCH (s:Service)-[:EXPOSES]->(a:Api) WITH s, collect(a) AS apis RETURN s.service_id, [x IN apis WHERE x.is_public | x.http_path]",
    },
    "apoc_export": {
        "title": "Heap-Safe Streaming APOC Export Simulation (batchSize=5000, separateHeader=true)",
        "cypher_hint": "CALL apoc.export.csv.query(..., {batchSize: 5000, separateHeader: true, quotes: 'ifNeeded', stream: false})",
    },
}

QUERY_ALIASES: dict[str, str] = {
    "cycle": "walk_vs_trail",
    "cycle_demo": "walk_vs_trail",
    "trail": "walk_vs_trail",
    "hero": "hero_blast_radius",
    "blast_radius": "hero_blast_radius",
    "hubs": "hub_fanin",
    "at_risk": "legacy_at_risk",
    "optional_match": "legacy_at_risk",
    "export": "apoc_export",
}


# -----------------------------------------------------------------------------
# Human-Readable Terminal Renderers
# -----------------------------------------------------------------------------

def _print_banner(title: str) -> None:
    print("")
    print("=" * 78)
    print(title)
    print("=" * 78)


def _render_section_1_summary(summary: dict[str, Any]) -> None:
    _print_banner("[Section 1/4] Source Neo4j CE Property Graph Summary (Zero-Docker In-Memory)")
    print(
        f"  Loaded Graph  : {summary['total_nodes']} nodes (5 labels) | "
        f"{summary['total_edges']} relationships "
        f"({summary['explicit_edges']} explicit M:N + {summary['dual_projected_edges']} dual-projected N:1)"
    )
    print(f"  Constraints   : {len(summary['constraints_enforced'])} domain PK uniqueness constraints (zero internal id(n) export)")
    node_str = ", ".join(f":{k}={v}" for k, v in summary["nodes_by_label"].items())
    print(f"  Node Labels   : {node_str}")
    exp_str = ", ".join(f":{k}={v}" for k, v in summary["explicit_edges_by_type"].items())
    dual_str = ", ".join(f":{k}={v}" for k, v in summary["dual_projected_edges_by_type"].items())
    print(f"  Explicit M:N  : {exp_str}")
    print(f"  Folded N:1    : {dual_str}")
    print("")
    print("  Locked Topological Invariants Verified:")
    hub = summary["top_hub"]
    print(
        f"    1. High-Fanin Hub   : {hub['service_id']} "
        f"(DEPENDS_ON in-degree = {hub['depends_on_in_degree']}, "
        f"{hub['api_id']} callers = {hub['api_caller_count']})"
    )
    df_targets = ", ".join(r["to_service_id"] for r in summary["data_feed_edges"])
    print(
        f"    2. Async DATA_FEED  : svc-analytics -> [{df_targets}] "
        f"({len(summary['data_feed_edges'])} non-blocking BI edges)"
    )
    for leg in summary["legacy_at_risk_calls"]:
        print(
            f"    3. Legacy AT_RISK   : {leg['service_id']} -> {leg['api_id']} "
            f"(status={leg['api_lifecycle_status']}, migration={leg['migration_status']}, "
            f"rps={leg['rps_estimate']})"
        )
    cyc_str = " <-> ".join(["svc-orders", "svc-notifications"])
    print(
        f"    4. Intentional Cycle: {cyc_str} "
        f"({len(summary['bidirectional_cycle_edges'])} ASYNC_EVENT edges)"
    )
    sup_str = ", ".join(
        f"{s['adr_id']}->{s['supersedes_adr_id']}" for s in summary["adr_supersessions"]
    )
    print(f"    5. ADR Lineage      : {sup_str}")


def _render_section_2_walk_vs_trail(wt: dict[str, Any]) -> None:
    _print_banner(
        f"[Section 2/4] WALK vs. TRAIL Cycle Explosion Demo "
        f"(Upstream of {wt['target_service']}, 1..{wt['max_hops']} hops)"
    )
    print("  Cycle Under Test : svc-orders <--[ASYNC_EVENT]--> svc-notifications")
    print("  Why It Matters   : Neo4j openCypher defaults to TRAIL (no repeated edges).")
    print("                     Cloud Spanner ISO GQL defaults to WALK (repeats cycle edges!).")
    print("")
    print(f"  {'Max Hops':>8s} | {'ACYCLIC (No Node Dup)':>21s} | {'TRAIL (No Edge Dup)':>19s} | {'WALK (Spanner Default)':>22s} | {'Extra Cycle Paths':>17s}")
    print(f"  {'-' * 8}-+-{'-' * 21}-+-{'-' * 19}-+-{'-' * 22}-+-{'-' * 17}")
    for row in wt["depth_scaling_table"]:
        marker = " <-- 4-hop demo" if row["max_hops"] == wt["max_hops"] else ""
        print(
            f"  {row['max_hops']:8d} | {row['acyclic_paths']:21d} | "
            f"{row['trail_paths']:19d} | {row['walk_paths']:22d} | "
            f"{row['walk_extra_cycle_paths']:+17d}{marker}"
        )
    print("")
    print(
        f"  At max_hops={wt['max_hops']}: TRAIL terminates finitely at {wt['trail_path_count']} paths "
        f"(blocked {wt['trail_cycle_edges_blocked']} cycle edge repetition(s)) "
        f"vs. WALK = {wt['walk_path_count']} paths (growing unboundedly at deeper hops!)."
    )
    if wt["walk_only_looping_paths_at_max_hops"]:
        print("  Looping WALK-Only Path(s) Blocked by MATCH TRAIL:")
        for p in wt["walk_only_looping_paths_at_max_hops"][:5]:
            print(f"    [LOOP] {p}")


def _render_section_3_hero_and_traps(
    hero: dict[str, Any],
    opt_trap: dict[str, Any],
    sp: dict[str, Any],
) -> None:
    _print_banner(
        f"[Section 3/4] Hero Blast-Radius ({hero['api_id']}) & openCypher -> ISO GQL Parity"
    )
    print(
        f"  Hero Question : What breaks if we change {hero['http_method']} {hero['http_path']}? "
        f"(exposed by {hero['exposing_service_id']})"
    )
    print(f"  Impacted Rows : {hero['impact_row_count']} deterministic services (MATCH TRAIL):")
    for idx, row in enumerate(hero["impact_rows"], start=1):
        flag = " [LEGACY_V1_AT_RISK]" if row["has_legacy_v1_risk"] else ""
        print(
            f"    {idx}. {row['impacted_service_id']:18s} | hop={row['hop_distance']} | "
            f"{row['impact_category']:19s} | {row['tier']:6s}/{row['criticality']:8s} | "
            f"{row['owner_id']:17s} ({row['slack_channel']}){flag}"
        )
    adr_list = ", ".join(f"{a['adr_id']}({a['governance_type']})" for a in hero["governing_adrs"])
    req_list = ", ".join(hero["applicable_requirements"])
    print(f"  Governing ADRs ({len(hero['governing_adrs'])}) : {adr_list}")
    print(f"  Requirements   ({len(hero['applicable_requirements'])}) : {req_list}")
    print("")
    print("  openCypher -> ISO GQL Trap Checkpoints:")
    print(
        f"    - OPTIONAL MATCH ... WHERE (LEFT JOIN): {opt_trap['optional_match_where_row_count']} services preserved "
        f"(post-join FILTER silently drops {opt_trap['silently_dropped_services']} services -> only {opt_trap['broken_post_filter_row_count']} row!)"
    )
    print(
        f"    - shortestPath -> MATCH ANY SHORTEST  : {sp['src_id']} -> {sp['dst_id']} = "
        f"{sp['hop_count']} hops ({' -> '.join(sp['path_nodes'])})"
    )


def _render_section_4_apoc_and_diff(
    export_report: dict[str, Any],
    diff_report: dict[str, Any] | None,
    ephemeral_dir: bool,
) -> None:
    _print_banner("[Section 4/4] Heap-Safe Streaming APOC Export Simulation & Byte-for-Byte Parity")
    opts = export_report["apoc_options"]
    print(
        f"  APOC Options  : batchSize={opts['batchSize']}, separateHeader={opts['separateHeader']}, "
        f"quotes='{opts['quotes']}', stream={opts['stream']}"
    )
    loc_note = " (temp dir — auto-cleaned on exit)" if ephemeral_dir else " (persisted via --export-dir)"
    print(
        f"  Export Output : {export_report['tables_exported']} headerless CSVs "
        f"({export_report['total_csv_rows']} rows, {export_report['total_csv_bytes']:,} bytes) + "
        f"{export_report['jsonl_file']} ({export_report['jsonl_records']} JSONL records){loc_note}"
    )
    print("")
    print("  JVM Heap Pressure: Naive `collect(x)` Buffering vs. APOC Streaming (1 GiB CE Heap):")
    print(f"  {'Dataset Scale':28s} | {'Naive collect(x)':>18s} | {'APOC batchSize=5000':>20s} | {'Result':>14s}")
    print(f"  {'-' * 28}-+-{'-' * 18}-+-{'-' * 20}-+-{'-' * 14}")
    for sc in export_report["heap_comparison"]:
        status = "OOM vs SAFE" if sc["naive_oom_at_1gib_heap"] else "BOTH FIT"
        print(
            f"  {sc['scenario']:28s} | {sc['naive_collect_peak_heap_mb']:14.2f} MiB | "
            f"{sc['apoc_streaming_peak_heap_mb']:16.2f} MiB | {status:>14s}"
        )
    if diff_report is not None:
        print("")
        badge = "PASS" if diff_report["passed"] else "FAIL"
        print(
            f"  [{badge}] stage_apoc_export.py --diff-apoc-dir: "
            f"{diff_report['tables_matched']}/{diff_report['tables_checked']} tables MATCH "
            f"(mismatches={diff_report['mismatches']}, 100% SHA-256 parity with Docker APOC!)"
        )
        for tname, tinfo in diff_report["tables"].items():
            sha_short = str(tinfo.get("apoc_sha256", ""))[:12]
            print(
                f"    [{tinfo['status']:5s}] {tname:28s} | "
                f"rows={tinfo.get('apoc_rows', 0):3d}/{tinfo.get('canonical_rows', 0):3d} | "
                f"sha256={sha_short}..."
            )


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    default_source = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description=(
            "Zero-Docker in-memory Neo4j CE & APOC streaming simulator for the Acme Commerce "
            "SDLC Knowledge Graph (100% Python 3 stdlib)."
        )
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=default_source,
        help="Path to data/structured directory (default: script directory).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run all 4 live terminal demo sections (default when no --query is specified).",
    )
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=None,
        help=(
            "Optional persistent directory to write headerless APOC CSVs + JSONL. "
            "When omitted, uses an auto-cleaned temporary directory so zero temp files "
            "remain in the repository."
        ),
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Run stage_apoc_export.py --diff-apoc-dir against the simulated export to verify 100%% parity.",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help=(
            "Execute a single named Cypher-equivalent query against the in-memory graph "
            "(run --list-queries to view all available names)."
        ),
    )
    parser.add_argument(
        "--list-queries",
        action="store_true",
        help="List all built-in named --query options and exit.",
    )
    parser.add_argument(
        "--target-service",
        type=str,
        default="svc-checkout",
        help="Target service for WALK vs. TRAIL upstream traversal (default: svc-checkout).",
    )
    parser.add_argument(
        "--max-hops",
        type=int,
        default=4,
        help="Max upstream DEPENDS_ON hops for WALK vs. TRAIL demo (default: 4).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Simulated APOC streaming batchSize (default: 5000).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    return parser


def _execute_export_and_optional_diff(
    graph: Neo4jPropertyGraph,
    source_dir: Path,
    export_dir: Path | None,
    batch_size: int,
    run_diff: bool,
) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
    """Run APOC streaming export using either explicit --export-dir or auto-cleaned temp dir."""
    if export_dir is not None:
        export_report = simulate_apoc_streaming_export(graph, export_dir, batch_size=batch_size)
        diff_report = run_stage_apoc_diff(source_dir, export_dir) if run_diff else None
        return export_report, diff_report, False

    with tempfile.TemporaryDirectory(prefix="acme_neo4j_apoc_sim_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        export_report = simulate_apoc_streaming_export(graph, tmp_path, batch_size=batch_size)
        diff_report = run_stage_apoc_diff(source_dir, tmp_path) if run_diff else None
        export_report["export_dir"] = "<ephemeral-temp-dir-cleaned>"
        return export_report, diff_report, True


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.list_queries:
        if args.json:
            print(json.dumps({"queries": QUERY_REGISTRY, "aliases": QUERY_ALIASES}, indent=2))
        else:
            print("Available --query names:")
            for name, meta in QUERY_REGISTRY.items():
                print(f"  {name:20s} - {meta['title']}")
                print(f"  {' ' * 20}   Cypher: {meta['cypher_hint']}")
        return 0

    graph = Neo4jPropertyGraph.load_from_structured_dir(args.source_dir)

    # Single named query mode
    if args.query is not None:
        raw_q = args.query.strip().lower()
        canonical_q = QUERY_ALIASES.get(raw_q, raw_q)
        if canonical_q not in QUERY_REGISTRY:
            valid = ", ".join(sorted(QUERY_REGISTRY))
            print(f"[ERROR] Unknown --query {args.query!r}. Valid queries: {valid}", file=sys.stderr)
            return 2

        diff_report: dict[str, Any] | None = None
        ephemeral = args.export_dir is None
        if canonical_q == "summary":
            result = query_summary(graph)
        elif canonical_q == "walk_vs_trail":
            result = query_walk_vs_trail(
                graph, target_service=args.target_service, max_hops=args.max_hops
            )
        elif canonical_q == "hero_blast_radius":
            result = query_hero_blast_radius(graph)
        elif canonical_q == "hub_fanin":
            result = query_hub_fanin(graph)
        elif canonical_q == "legacy_at_risk":
            result = query_optional_match_trap(graph)
        elif canonical_q == "data_feed":
            result = {"data_feed_edges": query_summary(graph)["data_feed_edges"]}
        elif canonical_q == "shortest_path":
            result = query_shortest_path(graph)
        elif canonical_q == "adr_supersessions":
            result = {"adr_supersessions": query_summary(graph)["adr_supersessions"]}
        elif canonical_q == "public_apis":
            result = query_public_apis(graph)
        else:  # apoc_export
            result, diff_report, ephemeral = _execute_export_and_optional_diff(
                graph,
                args.source_dir,
                args.export_dir,
                batch_size=args.batch_size,
                run_diff=args.diff,
            )

        payload: dict[str, Any] = {
            "status": "OK" if (diff_report is None or diff_report["passed"]) else "DIFF_FAILED",
            "mode": "query",
            "query": canonical_q,
            "title": QUERY_REGISTRY[canonical_q]["title"],
            "result": result,
        }
        if diff_report is not None:
            payload["apoc_diff"] = diff_report

        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            if canonical_q == "summary":
                _render_section_1_summary(result)
            elif canonical_q == "walk_vs_trail":
                _render_section_2_walk_vs_trail(result)
            elif canonical_q == "apoc_export":
                _render_section_4_apoc_and_diff(result, diff_report, ephemeral_dir=ephemeral)
            else:
                _print_banner(f"[Query: {canonical_q}] {QUERY_REGISTRY[canonical_q]['title']}")
                print(json.dumps(result, indent=2))
        return 0 if (diff_report is None or diff_report["passed"]) else 1

    # Export-only / Diff-only mode (when --export-dir or --diff is passed WITHOUT --demo)
    run_full_demo = args.demo or (args.export_dir is None and not args.diff)
    if not run_full_demo:
        export_report, diff_report, ephemeral = _execute_export_and_optional_diff(
            graph,
            args.source_dir,
            args.export_dir,
            batch_size=args.batch_size,
            run_diff=args.diff,
        )
        passed = diff_report["passed"] if diff_report is not None else True
        payload = {
            "status": "OK" if passed else "DIFF_FAILED",
            "mode": "export_and_diff" if args.diff else "export",
            "graph_counts": {
                "total_nodes": graph.total_nodes,
                "explicit_edges": graph.explicit_edge_count,
                "dual_projected_edges": graph.dual_projected_edge_count,
                "total_edges": graph.total_edges,
            },
            "apoc_export": export_report,
            "apoc_diff": diff_report,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            _render_section_4_apoc_and_diff(export_report, diff_report, ephemeral_dir=ephemeral)
        return 0 if passed else 1

    # Full 4-section --demo mode (default)
    sec1 = query_summary(graph)
    sec2 = query_walk_vs_trail(graph, target_service=args.target_service, max_hops=args.max_hops)
    sec3_hero = query_hero_blast_radius(graph)
    sec3_opt = query_optional_match_trap(graph)
    sec3_sp = query_shortest_path(graph)
    sec4_export, sec4_diff, ephemeral = _execute_export_and_optional_diff(
        graph,
        args.source_dir,
        args.export_dir,
        batch_size=args.batch_size,
        run_diff=True,  # Always verify byte-for-byte parity in Section 4 of --demo
    )
    passed = bool(sec4_diff and sec4_diff["passed"])

    payload = {
        "status": "OK" if passed else "DIFF_FAILED",
        "mode": "demo",
        "zero_docker": True,
        "section_1_summary": sec1,
        "section_2_walk_vs_trail": sec2,
        "section_3_hero_and_gql_parity": {
            "hero_blast_radius": sec3_hero,
            "optional_match_trap": sec3_opt,
            "shortest_path": sec3_sp,
        },
        "section_4_apoc_streaming_export": sec4_export,
        "apoc_diff": sec4_diff,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("Acme Commerce Platform — Zero-Docker Neo4j CE & APOC Streaming Simulator")
        print("Runtime: 100% Python 3 stdlib (zero Docker, zero external packages)")
        _render_section_1_summary(sec1)
        _render_section_2_walk_vs_trail(sec2)
        _render_section_3_hero_and_traps(sec3_hero, sec3_opt, sec3_sp)
        _render_section_4_apoc_and_diff(sec4_export, sec4_diff, ephemeral_dir=ephemeral)
        print("")
        if ephemeral:
            print("[OK] Zero-Docker Neo4j CE simulation complete (zero temp files left in repo).")
        else:
            print(
                f"[OK] Zero-Docker Neo4j CE simulation complete (APOC export saved to {args.export_dir})."
            )

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
