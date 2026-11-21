#!/usr/bin/env python3
"""Pure-Python Offline Replay Engine for the Hero API Blast-Radius GraphRAG Query.

Reads the canonical Acme Commerce Platform Path A (`data/structured/`) and
Path B (`data/unstructured/`) corpora using ONLY the Python standard library
(zero external packages, zero GCP credentials required) and faithfully replays
`solution/sql/07_hero_api_blast_radius_graphrag.sql`:

  1. Direct synchronous callers (`MATCH (direct:Service)-[:CALLS_API]->(target:Api)`).
  2. Cycle-safe multi-hop upstream traversal (`MATCH TRAIL` over `DEPENDS_ON`
     up to 3 hops, preventing edge repetition across the intentional
     `svc-orders <-> svc-notifications` bidirectional cycle).
  3. Non-blocking async `DATA_FEED` separation (`DATA_FEED_CONSUMER` vs
     `DIRECT_SYNC` and `TRANSITIVE_UPSTREAM`).
  4. Legacy `AT_RISK` / `DEPRECATED` v1 payment exposure flagging (`svc-billing`).
  5. Deterministic sorting:
       hop_distance ASC -> impact_category_rank ASC -> criticality_rank ASC ->
       impacted_service_id ASC.
  6. Governing ADR, applicable Requirement, and verifiable unstructured Markdown
     citation enrichment (grounded directly in `data/unstructured/`).
  7. Strict `--check-fixture <path>` verification against
     `solution/sql/fixtures/07_hero_expected_rows.json` (or `WORKSHOP_SPEC.json`).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_HERO_API_ID = "api-payments-charge-v2"
DEFAULT_MAX_DEPENDS_HOPS = 3

IMPACT_CATEGORY_RANK: dict[str, int] = {
    "DIRECT_SYNC": 1,
    "TRANSITIVE_UPSTREAM": 2,
    "DATA_FEED_CONSUMER": 3,
}

CRITICALITY_RANK: dict[str, int] = {
    "CRITICAL": 1,
    "HIGH": 2,
    "MEDIUM": 3,
    "LOW": 4,
}

ENTITY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("API", re.compile(r"\b(api-[a-z0-9-]+)\b")),
    ("SERVICE", re.compile(r"\b(svc-[a-z0-9-]+)\b")),
    ("ADR", re.compile(r"\b(ADR-\d{3})\b")),
    ("REQUIREMENT", re.compile(r"\b(REQ-\d{3})\b")),
    ("OWNER", re.compile(r"\b(team-[a-z0-9-]+)\b")),
]

DIR_TO_DOC_TYPE: dict[str, str] = {
    "services": "service",
    "apis": "api",
    "adrs": "adr",
    "requirements": "requirement",
    "owners": "owner",
}

WORD_RE = re.compile(r"[a-z0-9_$-]{2,}", re.IGNORECASE)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Required structured CSV not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def load_structured_graph(structured_dir: Path) -> dict[str, Any]:
    """Load all Path A node and explicit edge tables into memory."""
    owners = {r["owner_id"]: r for r in _read_csv(structured_dir / "nodes/owners.csv")}
    services = {r["service_id"]: r for r in _read_csv(structured_dir / "nodes/services.csv")}
    apis = {r["api_id"]: r for r in _read_csv(structured_dir / "nodes/apis.csv")}
    adrs = {r["adr_id"]: r for r in _read_csv(structured_dir / "nodes/adrs.csv")}
    requirements = {
        r["req_id"]: r for r in _read_csv(structured_dir / "nodes/requirements.csv")
    }

    service_calls_api = _read_csv(structured_dir / "edges/service_calls_api.csv")
    service_depends_on = _read_csv(structured_dir / "edges/service_depends_on_service.csv")
    adr_governs_api = _read_csv(structured_dir / "edges/adr_governs_api.csv")
    adr_supersedes_adr = _read_csv(structured_dir / "edges/adr_supersedes_adr.csv")
    req_applies_to_api = _read_csv(structured_dir / "edges/req_applies_to_api.csv")
    req_applies_to_service = _read_csv(structured_dir / "edges/req_applies_to_service.csv")

    return {
        "owners": owners,
        "services": services,
        "apis": apis,
        "adrs": adrs,
        "requirements": requirements,
        "service_calls_api": service_calls_api,
        "service_depends_on": service_depends_on,
        "adr_governs_api": adr_governs_api,
        "adr_supersedes_adr": adr_supersedes_adr,
        "req_applies_to_api": req_applies_to_api,
        "req_applies_to_service": req_applies_to_service,
    }


def load_unstructured_chunks(
    unstructured_dir: Path,
    repo_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Chunk Path B Markdown files and index entity mentions offline (stdlib only)."""
    if not unstructured_dir.is_dir():
        raise FileNotFoundError(f"Unstructured corpus directory not found: {unstructured_dir}")

    chunks: list[dict[str, Any]] = []
    full_docs_by_source: dict[str, str] = {}

    for md_path in sorted(unstructured_dir.rglob("*.md")):
        folder = md_path.parent.name
        doc_type = DIR_TO_DOC_TYPE.get(folder, folder.rstrip("s"))
        try:
            source_path = md_path.relative_to(repo_root).as_posix()
        except ValueError:
            source_path = md_path.as_posix()

        raw_text = md_path.read_text(encoding="utf-8").strip()
        full_docs_by_source[source_path] = raw_text
        lines = raw_text.splitlines()
        doc_title = (
            lines[0].lstrip("#").strip() if lines and lines[0].startswith("#") else md_path.stem
        )

        sections: list[tuple[str, str]] = []
        curr_heading = "Overview"
        curr_lines: list[str] = []
        for line in lines:
            if line.startswith("## "):
                if curr_lines:
                    sections.append((curr_heading, "\n".join(curr_lines).strip()))
                curr_heading = line[3:].strip()
                curr_lines = [line]
            else:
                curr_lines.append(line)
        if curr_lines:
            sections.append((curr_heading, "\n".join(curr_lines).strip()))

        chunk_idx = 0
        for sec_idx, (heading, body) in enumerate(sections):
            if doc_type == "owner" and sec_idx == 0 and heading == "Overview" and len(body) < 350:
                continue
            content = body if body.startswith("# ") else f"# {doc_title}\n\n{body}"
            if doc_type == "owner":
                tm = re.match(r"(team-[a-z0-9-]+)", heading)
                entity_id = tm.group(1) if tm else "team-platform"
            else:
                entity_id = md_path.stem
            mentioned_ids: set[str] = set()
            for _, pat in ENTITY_PATTERNS:
                mentioned_ids.update(pat.findall(content))
            chunks.append(
                {
                    "chunk_id": f"{doc_type}__{entity_id}__c{chunk_idx:02d}",
                    "doc_type": doc_type,
                    "entity_id": entity_id,
                    "source_path": source_path,
                    "section_heading": heading,
                    "content": content,
                    "mentioned_ids": mentioned_ids,
                }
            )
            chunk_idx += 1

    return chunks, full_docs_by_source


def _count_walk_vs_trail_cycles(
    reverse_adj: dict[str, list[tuple[str, str]]],
    direct_callers: list[str],
    demo_depth: int = 5,
) -> dict[str, int]:
    """Compare WALK vs TRAIL path counts up to demo_depth to prove cycle safety."""
    trail_paths = 0
    trail_cycle_edges_blocked = 0
    walk_paths = 0

    def _dfs_trail(curr: str, used: set[tuple[str, str]]) -> None:
        nonlocal trail_paths, trail_cycle_edges_blocked
        if len(used) >= demo_depth:
            return
        for up, _ in reverse_adj.get(curr, []):
            edge = (up, curr)
            if edge in used:
                trail_cycle_edges_blocked += 1
                continue
            trail_paths += 1
            _dfs_trail(up, used | {edge})

    def _dfs_walk(curr: str, depth: int) -> None:
        nonlocal walk_paths
        if depth >= demo_depth:
            return
        for up, _ in reverse_adj.get(curr, []):
            walk_paths += 1
            _dfs_walk(up, depth + 1)

    for d in direct_callers:
        _dfs_trail(d, set())
        _dfs_walk(d, 0)

    return {
        "comparison_depth": demo_depth,
        "trail_paths_total": trail_paths,
        "walk_paths_total": walk_paths,
        "cycle_edge_repetitions_blocked_by_trail": trail_cycle_edges_blocked,
    }


def _score_and_collect_citations(
    chunks: list[dict[str, Any]],
    full_docs_by_source: dict[str, str],
    api_id: str,
    exposing_service_id: str,
    legacy_sibling_api_ids: set[str],
    impacted_service_ids: set[str],
    governing_adr_ids: set[str],
    applicable_req_ids: set[str],
    search_query: str,
) -> list[dict[str, Any]]:
    """Retrieve and rank Path B Markdown citations linked to the blast radius."""
    anchor_entities = (
        {api_id, exposing_service_id}
        | legacy_sibling_api_ids
        | governing_adr_ids
        | applicable_req_ids
    )
    all_scope_entities = anchor_entities | impacted_service_ids

    query_tokens = Counter(t.lower() for t in WORD_RE.findall(search_query))
    doc_freq: Counter[str] = Counter()
    chunk_tokens: list[Counter[str]] = []
    for chunk in chunks:
        toks = Counter(t.lower() for t in WORD_RE.findall(chunk["content"]))
        chunk_tokens.append(toks)
        for term in toks:
            doc_freq[term] += 1

    total_chunks = max(len(chunks), 1)
    best_by_source: dict[str, dict[str, Any]] = {}

    for chunk, toks in zip(chunks, chunk_tokens):
        overlap_anchor = sorted(chunk["mentioned_ids"] & anchor_entities)
        overlap_scope = sorted(chunk["mentioned_ids"] & all_scope_entities)
        is_primary_entity_doc = chunk["entity_id"] in anchor_entities
        if not overlap_scope and not is_primary_entity_doc:
            continue

        # Lightweight deterministic TF-IDF lexical relevance + graph citation boost
        lexical_score = 0.0
        for term, q_tf in query_tokens.items():
            c_tf = toks.get(term, 0)
            if c_tf > 0:
                idf = math.log((total_chunks + 1.0) / (doc_freq[term] + 1.0)) + 1.0
                lexical_score += q_tf * (1.0 + math.log(c_tf)) * idf

        graph_boost = (
            (3.0 if is_primary_entity_doc else 0.0)
            + 1.5 * len(overlap_anchor)
            + 0.4 * len(overlap_scope)
        )
        total_score = round(lexical_score + graph_boost, 4)

        excerpt_lines = [
            ln.strip()
            for ln in chunk["content"].splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        excerpt = " ".join(excerpt_lines)[:240]

        candidate = {
            "source_path": chunk["source_path"],
            "doc_type": chunk["doc_type"],
            "entity_id": chunk["entity_id"],
            "chunk_id": chunk["chunk_id"],
            "section_heading": chunk["section_heading"],
            "matched_entities": overlap_scope,
            "is_primary_governance_or_api_doc": is_primary_entity_doc,
            "relevance_score": total_score,
            "excerpt": excerpt,
        }
        prev = best_by_source.get(chunk["source_path"])
        if prev is None or (candidate["relevance_score"], candidate["chunk_id"]) > (
            prev["relevance_score"],
            prev["chunk_id"],
        ):
            best_by_source[chunk["source_path"]] = candidate

    ranked = sorted(
        best_by_source.values(),
        key=lambda item: (
            0 if item["is_primary_governance_or_api_doc"] else 1,
            -item["relevance_score"],
            item["source_path"],
        ),
    )
    # Ensure source text availability metadata is attached
    for item in ranked:
        item["document_char_length"] = len(full_docs_by_source.get(item["source_path"], ""))
    return ranked


def replay_blast_radius(
    api_id: str,
    structured_dir: Path,
    unstructured_dir: Path,
    max_depends_hops: int = DEFAULT_MAX_DEPENDS_HOPS,
    search_query: str = "",
) -> dict[str, Any]:
    """Execute the complete offline Hero API blast-radius GraphRAG replay."""
    repo_root = Path(__file__).resolve().parents[2]
    graph = load_structured_graph(structured_dir)
    chunks, full_docs_by_source = load_unstructured_chunks(unstructured_dir, repo_root)

    apis = graph["apis"]
    services = graph["services"]
    owners = graph["owners"]
    adrs = graph["adrs"]
    requirements = graph["requirements"]

    if api_id not in apis:
        raise ValueError(f"Unknown api_id {api_id!r}. Available APIs: {sorted(apis)}")

    target_api = apis[api_id]
    exposing_service_id = target_api["service_id"]
    exposing_service = services[exposing_service_id]
    exposing_owner_id = exposing_service["owner_id"]

    # 1. Direct synchronous callers: (direct:Service)-[:CALLS_API]->(target:Api)
    direct_call_rows = [
        r for r in graph["service_calls_api"] if r["api_id"] == api_id
    ]
    direct_callers = sorted({r["service_id"] for r in direct_call_rows})

    # 2. Reverse adjacency for upstream DEPENDS_ON traversal
    reverse_adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in graph["service_depends_on"]:
        reverse_adj[row["to_service_id"]].append(
            (row["from_service_id"], row["dependency_type"])
        )
    for key in reverse_adj:
        reverse_adj[key].sort()

    # Raw candidate blast observations mirroring SQL UNION ALL of hop 1 + TRAIL hops 1..3
    raw_observations: dict[str, list[tuple[int, str, list[str], list[str]]]] = defaultdict(list)
    for caller_id in direct_callers:
        raw_observations[caller_id].append((1, "DIRECT_SYNC", [], [caller_id]))

    trails_evaluated = 0
    cycle_edges_blocked = 0

    def _dfs_trail(
        curr: str,
        path_nodes: list[str],
        used_edges: set[tuple[str, str]],
        dep_types: list[str],
    ) -> None:
        nonlocal trails_evaluated, cycle_edges_blocked
        if len(used_edges) >= max_depends_hops:
            return
        for upstream, dep_type in reverse_adj.get(curr, []):
            edge_key = (upstream, curr)
            if edge_key in used_edges:
                cycle_edges_blocked += 1
                continue
            new_used = used_edges | {edge_key}
            new_types = dep_types + [dep_type]
            new_nodes = [upstream] + path_nodes
            hop_distance = len(new_used) + 1
            impact_cat = (
                "DATA_FEED_CONSUMER"
                if "DATA_FEED" in new_types
                else "TRANSITIVE_UPSTREAM"
            )
            trails_evaluated += 1
            raw_observations[upstream].append(
                (hop_distance, impact_cat, new_types, new_nodes)
            )
            _dfs_trail(upstream, new_nodes, new_used, new_types)

    for caller_id in direct_callers:
        _dfs_trail(caller_id, [caller_id], set(), [])

    # 3. Identify legacy AT_RISK / DEPRECATED sibling API exposure
    at_risk_calls = [
        r
        for r in graph["service_calls_api"]
        if r["migration_status"] == "AT_RISK"
        or apis[r["api_id"]]["lifecycle_status"] == "DEPRECATED"
    ]
    at_risk_service_ids = {r["service_id"] for r in at_risk_calls}
    legacy_sibling_api_ids = {
        a_id
        for a_id, a_row in apis.items()
        if a_row["service_id"] == exposing_service_id
        and a_row["lifecycle_status"] == "DEPRECATED"
    }

    # 4. Collapse observations per impacted_service_id & apply deterministic sort
    impact_rows: list[dict[str, Any]] = []
    impact_paths_by_service: dict[str, list[dict[str, Any]]] = {}

    for svc_id, obs_list in raw_observations.items():
        # Match SQL dedup_blast: MIN(hop_distance), tie-broken by impact_category_rank
        best_obs = min(
            obs_list,
            key=lambda item: (item[0], IMPACT_CATEGORY_RANK[item[1]]),
        )
        hop_distance, impact_category = best_obs[0], best_obs[1]
        svc = services[svc_id]
        owner = owners[svc["owner_id"]]
        impact_rows.append(
            {
                "impacted_service_id": svc_id,
                "service_name": svc["service_name"],
                "hop_distance": hop_distance,
                "impact_category": impact_category,
                "tier": svc["tier"],
                "criticality": svc["criticality"],
                "owner_id": svc["owner_id"],
                "slack_channel": owner["slack_channel"],
                "has_legacy_v1_risk": svc_id in at_risk_service_ids,
            }
        )
        impact_paths_by_service[svc_id] = [
            {
                "hop_distance": h,
                "impact_category": c,
                "dependency_types": dt,
                "service_chain": chain,
            }
            for h, c, dt, chain in sorted(
                obs_list, key=lambda item: (item[0], IMPACT_CATEGORY_RANK[item[1]], item[3])
            )
        ]

    impact_rows.sort(
        key=lambda row: (
            row["hop_distance"],
            IMPACT_CATEGORY_RANK[row["impact_category"]],
            CRITICALITY_RANK[row["criticality"]],
            row["impacted_service_id"],
        )
    )

    # 5. Enrich with governing ADRs (+ supersession lineage) and applicable Requirements
    supersedes_map: dict[str, list[str]] = defaultdict(list)
    for row in graph["adr_supersedes_adr"]:
        supersedes_map[row["adr_id"]].append(row["supersedes_adr_id"])

    governing_adrs = sorted(
        [
            {
                "adr_id": row["adr_id"],
                "governance_type": row["governance_type"],
                "title": adrs[row["adr_id"]]["title"],
                "lifecycle_status": adrs[row["adr_id"]]["lifecycle_status"],
                "owner_id": adrs[row["adr_id"]]["owner_id"],
                "effective_date": row["effective_date"],
                "supersedes_adr_ids": sorted(supersedes_map.get(row["adr_id"], [])),
            }
            for row in graph["adr_governs_api"]
            if row["api_id"] == api_id
        ],
        key=lambda item: item["adr_id"],
    )

    applicable_requirements = sorted(
        [
            {
                "req_id": row["req_id"],
                "framework": requirements[row["req_id"]]["framework"],
                "severity": requirements[row["req_id"]]["severity"],
                "title": requirements[row["req_id"]]["title"],
                "compliance_status": row["compliance_status"],
                "owner_id": requirements[row["req_id"]]["owner_id"],
            }
            for row in graph["req_applies_to_api"]
            if row["api_id"] == api_id
        ],
        key=lambda item: item["req_id"],
    )

    # 6. Enrich with grounded Path B unstructured Markdown citations
    effective_query = (
        search_query.strip()
        or f"{api_id} {target_api['http_method']} {target_api['http_path']} "
        "tok_pci_v2 45ms $500 fail-closed Idempotency-Key 180-day Tier-0 AT_RISK DEPRECATED"
    )
    citations = _score_and_collect_citations(
        chunks=chunks,
        full_docs_by_source=full_docs_by_source,
        api_id=api_id,
        exposing_service_id=exposing_service_id,
        legacy_sibling_api_ids=legacy_sibling_api_ids,
        impacted_service_ids={r["impacted_service_id"] for r in impact_rows},
        governing_adr_ids={a["adr_id"] for a in governing_adrs},
        applicable_req_ids={r["req_id"] for r in applicable_requirements},
        search_query=effective_query,
    )

    walk_vs_trail = _count_walk_vs_trail_cycles(reverse_adj, direct_callers, demo_depth=5)

    return {
        "api_id": api_id,
        "http_method": target_api["http_method"],
        "http_path": target_api["http_path"],
        "api_status": target_api["lifecycle_status"],
        "latency_budget_ms": int(target_api["latency_budget_ms"]),
        "exposing_service_id": exposing_service_id,
        "exposing_owner_id": exposing_owner_id,
        "impact_row_count": len(impact_rows),
        "impact_rows": impact_rows,
        "governing_adrs": governing_adrs,
        "applicable_requirements": applicable_requirements,
        "applicable_requirement_ids": [r["req_id"] for r in applicable_requirements],
        "legacy_at_risk_calls": [
            {
                "service_id": r["service_id"],
                "api_id": r["api_id"],
                "migration_status": r["migration_status"],
                "rps_estimate": int(r["rps_estimate"]),
            }
            for r in at_risk_calls
            if r["service_id"] in {x["impacted_service_id"] for x in impact_rows}
        ],
        "unstructured_citations": citations,
        "traversal_telemetry": {
            "path_mode": "TRAIL",
            "max_depends_hops": max_depends_hops,
            "direct_callers": direct_callers,
            "trails_evaluated_within_max_hops": trails_evaluated,
            "cycle_edges_blocked_within_max_hops": cycle_edges_blocked,
            "walk_vs_trail_depth_5_proof": walk_vs_trail,
            "impact_paths_by_service": impact_paths_by_service,
        },
        "_full_docs_by_source": full_docs_by_source,
    }


def verify_against_fixture(
    result: dict[str, Any],
    fixture_path: Path,
) -> tuple[bool, list[str]]:
    """Verify exact replay parity against 07_hero_expected_rows.json or WORKSHOP_SPEC.json."""
    if not fixture_path.is_file():
        return False, [f"Fixture file not found: {fixture_path}"]

    raw_fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    errors: list[str] = []

    # Support WORKSHOP_SPEC.json directly (unwrapping top-level hero_scenario)
    if isinstance(raw_fixture, dict) and "hero_scenario" in raw_fixture:
        fixture: Any = raw_fixture["hero_scenario"]
    else:
        fixture = raw_fixture

    # 1. Extract expected impact rows from list or object fixture shape
    expected_rows: list[dict[str, Any]] | None = None
    if isinstance(fixture, list):
        expected_rows = fixture
    elif isinstance(fixture, dict):
        if "api_id" in fixture and fixture["api_id"] != result["api_id"]:
            errors.append(
                f"api_id mismatch: got {result['api_id']!r}, fixture expects {fixture['api_id']!r}"
            )
        for key in ("expected_impact_rows", "impact_rows", "rows", "impacted_services"):
            if key in fixture and isinstance(fixture[key], list):
                expected_rows = fixture[key]
                break
        for count_key in ("expected_impact_row_count", "impact_row_count", "row_count"):
            if count_key in fixture and fixture[count_key] != len(result["impact_rows"]):
                errors.append(
                    f"Row count mismatch: got {len(result['impact_rows'])}, "
                    f"fixture {count_key}={fixture[count_key]}"
                )

    if expected_rows is None:
        return False, [
            f"Unsupported fixture format in {fixture_path}: expected a JSON list of rows "
            "or an object containing 'expected_impact_rows' / 'impact_rows' / 'rows'."
        ]

    # 2. Strict row-by-row, field-by-field ordering and value verification
    actual_rows = result["impact_rows"]
    if len(actual_rows) != len(expected_rows):
        errors.append(
            f"Impact row count mismatch: computed {len(actual_rows)} rows != "
            f"fixture {len(expected_rows)} rows"
        )
    else:
        for idx, (act, exp) in enumerate(zip(actual_rows, expected_rows), start=1):
            if not isinstance(exp, dict):
                errors.append(f"Row {idx} in fixture is not a JSON object: {exp!r}")
                continue
            for field, exp_val in exp.items():
                if field not in act:
                    errors.append(f"Row {idx} missing expected field {field!r}")
                    continue
                if act[field] != exp_val:
                    errors.append(
                        f"Row {idx} ({act.get('impacted_service_id')}) field {field!r} mismatch: "
                        f"got {act[field]!r}, expected {exp_val!r}"
                    )

    # 3. Verify governing_adrs if declared in fixture
    if isinstance(fixture, dict) and "governing_adrs" in fixture:
        exp_adrs = fixture["governing_adrs"]
        act_adrs = result["governing_adrs"]
        if exp_adrs and isinstance(exp_adrs[0], str):
            act_ids = [a["adr_id"] for a in act_adrs]
            if act_ids != sorted(exp_adrs):
                errors.append(f"governing_adrs mismatch: got {act_ids}, expected {exp_adrs}")
        elif exp_adrs and isinstance(exp_adrs[0], dict):
            act_by_id = {a["adr_id"]: a for a in act_adrs}
            if len(act_adrs) != len(exp_adrs):
                errors.append(
                    f"governing_adrs count mismatch: got {len(act_adrs)}, expected {len(exp_adrs)}"
                )
            for exp_a in exp_adrs:
                aid = exp_a.get("adr_id")
                if aid not in act_by_id:
                    errors.append(f"Missing governing ADR {aid!r}")
                    continue
                for k, v in exp_a.items():
                    if act_by_id[aid].get(k) != v:
                        errors.append(
                            f"Governing ADR {aid} field {k!r}: got {act_by_id[aid].get(k)!r}, expected {v!r}"
                        )

    # 4. Verify applicable_requirements if declared in fixture
    if isinstance(fixture, dict) and "applicable_requirements" in fixture:
        exp_reqs = fixture["applicable_requirements"]
        act_reqs = result["applicable_requirements"]
        if exp_reqs and isinstance(exp_reqs[0], str):
            act_req_ids = [r["req_id"] for r in act_reqs]
            if act_req_ids != sorted(exp_reqs):
                errors.append(
                    f"applicable_requirements mismatch: got {act_req_ids}, expected {sorted(exp_reqs)}"
                )
        elif exp_reqs and isinstance(exp_reqs[0], dict):
            act_by_id = {r["req_id"]: r for r in act_reqs}
            if len(act_reqs) != len(exp_reqs):
                errors.append(
                    f"applicable_requirements count mismatch: got {len(act_reqs)}, expected {len(exp_reqs)}"
                )
            for exp_r in exp_reqs:
                rid = exp_r.get("req_id")
                if rid not in act_by_id:
                    errors.append(f"Missing applicable requirement {rid!r}")
                    continue
                for k, v in exp_r.items():
                    if act_by_id[rid].get(k) != v:
                        errors.append(
                            f"Requirement {rid} field {k!r}: got {act_by_id[rid].get(k)!r}, expected {v!r}"
                        )

    # 5. Verify verifiable_unstructured_citations if declared in fixture
    if isinstance(fixture, dict):
        exp_cits = fixture.get("verifiable_unstructured_citations") or fixture.get("citations")
        if isinstance(exp_cits, list):
            cited_paths = {c["source_path"] for c in result["unstructured_citations"]}
            full_docs = result.get("_full_docs_by_source", {})
            for cit in exp_cits:
                if isinstance(cit, str):
                    if cit not in cited_paths:
                        errors.append(f"Expected citation source missing: {cit}")
                    continue
                src = cit.get("source_path")
                if src not in cited_paths:
                    errors.append(f"Expected citation source not retrieved: {src}")
                    continue
                doc_text = full_docs.get(src, "")
                for fact in cit.get("key_facts", []):
                    if fact not in doc_text:
                        errors.append(f"Verifiable fact {fact!r} not found in {src}")

    return len(errors) == 0, errors


def build_arg_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description=(
            "Offline stdlib replay engine for the Acme Commerce Hero API "
            "blast-radius GraphRAG query (MATCH TRAIL + DATA_FEED + citations)."
        )
    )
    parser.add_argument(
        "--api-id",
        type=str,
        default=DEFAULT_HERO_API_ID,
        help=f"Target API identifier (default: {DEFAULT_HERO_API_ID}).",
    )
    parser.add_argument(
        "--structured-dir",
        type=Path,
        default=repo_root / "data" / "structured",
        help="Path to data/structured directory.",
    )
    parser.add_argument(
        "--unstructured-dir",
        type=Path,
        default=repo_root / "data" / "unstructured",
        help="Path to data/unstructured directory.",
    )
    parser.add_argument(
        "--max-hops",
        type=int,
        default=DEFAULT_MAX_DEPENDS_HOPS,
        help=f"Max DEPENDS_ON hops in MATCH TRAIL (default: {DEFAULT_MAX_DEPENDS_HOPS}).",
    )
    parser.add_argument(
        "--query",
        type=str,
        default="",
        help="Optional natural-language question for offline lexical citation ranking.",
    )
    parser.add_argument(
        "--check-fixture",
        type=Path,
        default=None,
        help=(
            "Path to expected rows fixture JSON (e.g., "
            "solution/sql/fixtures/07_hero_expected_rows.json) to assert exact match."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No-op compatibility flag (engine always executes offline with stdlib only).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON replay output.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    result = replay_blast_radius(
        api_id=args.api_id,
        structured_dir=args.structured_dir,
        unstructured_dir=args.unstructured_dir,
        max_depends_hops=args.max_hops,
        search_query=args.query,
    )

    fixture_ok = True
    fixture_errors: list[str] = []
    if args.check_fixture is not None:
        fixture_ok, fixture_errors = verify_against_fixture(result, args.check_fixture)

    # Strip private full-document cache before serializing output
    public_result = {k: v for k, v in result.items() if not k.startswith("_")}
    if args.check_fixture is not None:
        public_result["fixture_check"] = {
            "fixture_path": str(args.check_fixture),
            "passed": fixture_ok,
            "errors": fixture_errors,
        }

    if args.json:
        print(json.dumps(public_result, indent=2))
    else:
        print(
            f"[OFFLINE REPLAY] {result['api_id']} "
            f"({result['http_method']} {result['http_path']}) — "
            f"exposed by {result['exposing_service_id']} ({result['exposing_owner_id']})"
        )
        print(f"  Impacted Services ({result['impact_row_count']} rows, MATCH TRAIL):")
        for idx, row in enumerate(result["impact_rows"], start=1):
            v1_flag = " [LEGACY_V1_AT_RISK]" if row["has_legacy_v1_risk"] else ""
            print(
                f"    {idx}. {row['impacted_service_id']:18s} | hop={row['hop_distance']} | "
                f"{row['impact_category']:19s} | {row['tier']:6s}/{row['criticality']:8s} | "
                f"{row['owner_id']} ({row['slack_channel']}){v1_flag}"
            )
        adr_summary = ", ".join(
            f"{a['adr_id']}({a['governance_type']})" for a in result["governing_adrs"]
        )
        req_summary = ", ".join(result["applicable_requirement_ids"])
        print(f"  Governing ADRs ({len(result['governing_adrs'])}): {adr_summary}")
        print(
            f"  Applicable Requirements ({len(result['applicable_requirements'])}): {req_summary}"
        )
        print(
            f"  Grounded Markdown Citations ({len(result['unstructured_citations'])} files): "
            + ", ".join(c["source_path"] for c in result["unstructured_citations"][:9])
        )
        wt = result["traversal_telemetry"]["walk_vs_trail_depth_5_proof"]
        print(
            f"  Cycle Proof (depth {wt['comparison_depth']}): "
            f"TRAIL={wt['trail_paths_total']} paths "
            f"(blocked {wt['cycle_edge_repetitions_blocked_by_trail']} cycle edge repeats) "
            f"vs WALK={wt['walk_paths_total']} paths"
        )
        if args.check_fixture is not None:
            if fixture_ok:
                print(f"[PASS] Exact fixture match verified: {args.check_fixture}")
            else:
                print(f"[FAIL] Fixture verification failed ({args.check_fixture}):", file=sys.stderr)
                for err in fixture_errors:
                    print(f"  - {err}", file=sys.stderr)

    return 0 if fixture_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
