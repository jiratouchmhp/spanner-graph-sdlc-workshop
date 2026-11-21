#!/usr/bin/env python3
"""Module 2: Dataset Referential Integrity, Pipeline Dry-Runs & Hero Replay Suite.

Verifies:
  1. 100% referential integrity, exact row counts, PK uniqueness, dual-projected
     FK parity, and SHA-256 hash parity across all `data/structured/nodes/*.csv`,
     `data/structured/edges/*.csv`, `all_nodes.jsonl`, and `all_edges.jsonl`.
  2. Complete coverage and verifiable key-fact hashes across all 45 Markdown
     documents in `data/unstructured/`.
  3. Clean stdlib dry-run execution of:
       - `data/structured/stage_apoc_export.py`
       - `solution/pipelines/neo4j_to_spanner_loader.py --dry-run`
       - `solution/pipelines/unstructured_graphrag_ingest.py --dry-run`
       - `solution/pipelines/apply_postload.py --dry-run --strict`
  4. Exact Hero API (`api-payments-charge-v2`) offline replay against
     `solution/sql/fixtures/07_hero_expected_rows.json` confirming the exact
     7-row blast radius, 5 governing ADRs, 5 applicable requirements, and
     9 verifiable unstructured Markdown citations.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
STRUCTURED_DIR = REPO_ROOT / "data" / "structured"
UNSTRUCTURED_DIR = REPO_ROOT / "data" / "unstructured"
HERO_FIXTURE_PATH = REPO_ROOT / "solution" / "sql" / "fixtures" / "07_hero_expected_rows.json"
HASH_FIXTURE_PATH = REPO_ROOT / "solution" / "sql" / "fixtures" / "10_row_hash_expected.json"


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def _canonical_sha256(rows: list[dict[str, str]], cols: list[str]) -> str:
    lines = sorted("|".join(row[c].strip() for c in cols) for row in rows)
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


class TestDatasetAndHeroReplay(unittest.TestCase):
    """End-to-end offline checks for Path A/B datasets, pipelines, and Hero replay."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = json.loads((REPO_ROOT / "WORKSHOP_SPEC.json").read_text(encoding="utf-8"))
        cls.contract = cls.spec["dataset_contract"]
        cls.hero_spec = cls.spec["hero_scenario"]
        cls.hero_fixture = json.loads(HERO_FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.hash_fixture = json.loads(HASH_FIXTURE_PATH.read_text(encoding="utf-8"))

        cls.node_specs = {
            "owners": ("nodes/owners.csv", "owner_id", "Owner", "Owners"),
            "services": ("nodes/services.csv", "service_id", "Service", "Services"),
            "apis": ("nodes/apis.csv", "api_id", "Api", "Apis"),
            "adrs": ("nodes/adrs.csv", "adr_id", "Adr", "Adrs"),
            "requirements": ("nodes/requirements.csv", "req_id", "Requirement", "Requirements"),
        }
        cls.explicit_edge_specs = {
            "service_calls_api": ("edges/service_calls_api.csv", ("service_id", "api_id"), ("services", "apis"), "ServiceCallsApi"),
            "service_depends_on_service": ("edges/service_depends_on_service.csv", ("from_service_id", "to_service_id"), ("services", "services"), "ServiceDependsOnService"),
            "adr_governs_api": ("edges/adr_governs_api.csv", ("adr_id", "api_id"), ("adrs", "apis"), "AdrGovernsApi"),
            "adr_supersedes_adr": ("edges/adr_supersedes_adr.csv", ("adr_id", "supersedes_adr_id"), ("adrs", "adrs"), "AdrSupersedesAdr"),
            "req_applies_to_api": ("edges/req_applies_to_api.csv", ("req_id", "api_id"), ("requirements", "apis"), "ReqAppliesToApi"),
            "req_applies_to_service": ("edges/req_applies_to_service.csv", ("req_id", "service_id"), ("requirements", "services"), "ReqAppliesToService"),
        }
        cls.dual_edge_specs = {
            "api_exposes_from_service": ("edges/api_exposes_from_service.csv", ("service_id", "api_id"), ("services", "apis")),
            "owner_owns_service": ("edges/owner_owns_service.csv", ("owner_id", "service_id"), ("owners", "services")),
            "owner_owns_adr": ("edges/owner_owns_adr.csv", ("owner_id", "adr_id"), ("owners", "adrs")),
            "owner_owns_req": ("edges/owner_owns_req.csv", ("owner_id", "req_id"), ("owners", "requirements")),
        }

        cls.nodes: dict[str, list[dict[str, str]]] = {}
        cls.node_ids: dict[str, set[str]] = {}
        cls.node_cols: dict[str, list[str]] = {}
        for key, (rel, pk, _, _) in cls.node_specs.items():
            cols, rows = _read_csv(STRUCTURED_DIR / rel)
            cls.node_cols[key] = cols
            cls.nodes[key] = rows
            cls.node_ids[key] = {r[pk] for r in rows}

    def test_node_csv_counts_uniqueness_and_internal_fks(self) -> None:
        """All 5 node CSVs have exact locked counts (total 68), unique PKs, and valid FKs."""
        total = 0
        for key, (_, pk, _, spanner_table) in self.node_specs.items():
            rows = self.nodes[key]
            expected = self.contract["entity_counts"][key]
            self.assertEqual(len(rows), expected, f"Node count mismatch for {key}")
            pks = [r[pk] for r in rows]
            self.assertEqual(len(pks), len(set(pks)), f"Duplicate PK in {key}.csv")
            self.assertTrue(all(p.strip() for p in pks), f"Empty PK in {key}.csv")
            total += len(rows)

            # Verify canonical SHA-256 matches 10_row_hash_expected.json
            expected_sha = self.hash_fixture["table_sha256"][spanner_table]
            actual_sha = _canonical_sha256(rows, self.node_cols[key])
            self.assertEqual(actual_sha, expected_sha, f"SHA-256 drift on {spanner_table}")

        self.assertEqual(total, self.contract["entity_counts"]["total_nodes"])
        self.assertEqual(total, 68)

        # Verify folded N:1 foreign keys on node tables
        for row in self.nodes["services"]:
            self.assertIn(row["owner_id"], self.node_ids["owners"])
        for row in self.nodes["apis"]:
            self.assertIn(row["service_id"], self.node_ids["services"])
        for row in self.nodes["adrs"]:
            self.assertIn(row["owner_id"], self.node_ids["owners"])
        for row in self.nodes["requirements"]:
            self.assertIn(row["owner_id"], self.node_ids["owners"])

    def test_edge_csv_referential_integrity_and_dual_projection_parity(self) -> None:
        """All 6 explicit (174) + 4 dual-projected (60) edge CSVs have 100% FK & hash parity."""
        explicit_total = 0
        for key, (rel, (scol, tcol), (stbl, ttbl), spanner_table) in self.explicit_edge_specs.items():
            cols, rows = _read_csv(STRUCTURED_DIR / rel)
            expected = self.contract["explicit_relationship_counts"][key]
            self.assertEqual(len(rows), expected, f"Explicit edge count mismatch for {key}")
            seen: set[tuple[str, str]] = set()
            for r in rows:
                pair = (r[scol], r[tcol])
                self.assertNotIn(pair, seen, f"Duplicate composite PK {pair} in {rel}")
                seen.add(pair)
                self.assertIn(r[scol], self.node_ids[stbl], f"Dangling source FK in {rel}: {r[scol]}")
                self.assertIn(r[tcol], self.node_ids[ttbl], f"Dangling target FK in {rel}: {r[tcol]}")
            explicit_total += len(rows)

            expected_sha = self.hash_fixture["table_sha256"][spanner_table]
            self.assertEqual(_canonical_sha256(rows, cols), expected_sha)

        self.assertEqual(explicit_total, 174)

        # Dual-projected edges must match node FK columns 1:1
        expected_dual_pairs = {
            "api_exposes_from_service": {(r["service_id"], r["api_id"]) for r in self.nodes["apis"]},
            "owner_owns_service": {(r["owner_id"], r["service_id"]) for r in self.nodes["services"]},
            "owner_owns_adr": {(r["owner_id"], r["adr_id"]) for r in self.nodes["adrs"]},
            "owner_owns_req": {(r["owner_id"], r["req_id"]) for r in self.nodes["requirements"]},
        }
        dual_total = 0
        for key, (rel, (scol, tcol), (stbl, ttbl)) in self.dual_edge_specs.items():
            cols, rows = _read_csv(STRUCTURED_DIR / rel)
            expected = self.contract["dual_projected_edge_counts"][key]
            self.assertEqual(len(rows), expected)
            actual_pairs = {(r[scol], r[tcol]) for r in rows}
            self.assertEqual(actual_pairs, expected_dual_pairs[key])
            for src, dst in actual_pairs:
                self.assertIn(src, self.node_ids[stbl])
                self.assertIn(dst, self.node_ids[ttbl])
            dual_total += len(rows)
            self.assertEqual(_canonical_sha256(rows, cols), self.hash_fixture["table_sha256"][key])

        self.assertEqual(dual_total, 60)
        self.assertEqual(explicit_total + dual_total, 234)

    def test_jsonl_referential_integrity_and_parity_with_csvs(self) -> None:
        """all_nodes.jsonl (68) and all_edges.jsonl (234) match CSV nodes/edges with zero dangling refs."""
        nodes_jsonl_lines = [
            json.loads(line)
            for line in (STRUCTURED_DIR / "nodes" / "all_nodes.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        edges_jsonl_lines = [
            json.loads(line)
            for line in (STRUCTURED_DIR / "edges" / "all_edges.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        self.assertEqual(len(nodes_jsonl_lines), 68)
        self.assertEqual(len(edges_jsonl_lines), 234)

        label_to_ids = {lbl: self.node_ids[k] for k, (_, _, lbl, _) in self.node_specs.items()}
        seen_nodes: set[tuple[str, str]] = set()
        for rec in nodes_jsonl_lines:
            lbl, nid = rec["label"], rec["id"]
            self.assertIn(lbl, label_to_ids)
            self.assertIn(nid, label_to_ids[lbl])
            self.assertNotIn((lbl, nid), seen_nodes)
            seen_nodes.add((lbl, nid))

        expected_all_nodes = {(lbl, nid) for lbl, ids in label_to_ids.items() for nid in ids}
        self.assertEqual(seen_nodes, expected_all_nodes)

        explicit_count = 0
        dual_count = 0
        for rec in edges_jsonl_lines:
            self.assertIn(rec["source_label"], label_to_ids)
            self.assertIn(rec["source_id"], label_to_ids[rec["source_label"]])
            self.assertIn(rec["target_label"], label_to_ids)
            self.assertIn(rec["target_id"], label_to_ids[rec["target_label"]])
            if rec.get("is_dual_projected"):
                dual_count += 1
            else:
                explicit_count += 1
        self.assertEqual(explicit_count, 174)
        self.assertEqual(dual_count, 60)

    def test_topological_invariants_present_in_dataset(self) -> None:
        """Verify cycle, DATA_FEED consumer, svc-auth hub, legacy AT_RISK, and ADR supersessions."""
        _, deps = _read_csv(STRUCTURED_DIR / "edges" / "service_depends_on_service.csv")
        _, calls = _read_csv(STRUCTURED_DIR / "edges" / "service_calls_api.csv")
        _, supers = _read_csv(STRUCTURED_DIR / "edges" / "adr_supersedes_adr.csv")

        dep_triples = {(r["from_service_id"], r["to_service_id"], r["dependency_type"]) for r in deps}
        self.assertIn(("svc-orders", "svc-notifications", "ASYNC_EVENT"), dep_triples)
        self.assertIn(("svc-notifications", "svc-orders", "ASYNC_EVENT"), dep_triples)

        data_feed_targets = sorted(
            r["to_service_id"]
            for r in deps
            if r["from_service_id"] == "svc-analytics" and r["dependency_type"] == "DATA_FEED"
        )
        self.assertEqual(data_feed_targets, ["svc-inventory", "svc-ledger", "svc-orders"])

        self.assertEqual(sum(1 for r in deps if r["to_service_id"] == "svc-auth"), 10)
        self.assertEqual(sum(1 for r in calls if r["api_id"] == "api-auth-token-v2"), 10)

        legacy_hits = [
            r
            for r in calls
            if r["service_id"] == "svc-billing"
            and r["api_id"] == "api-payments-charge-v1"
            and r["migration_status"] == "AT_RISK"
        ]
        self.assertEqual(len(legacy_hits), 1)

        sup_pairs = {(r["adr_id"], r["supersedes_adr_id"]) for r in supers}
        self.assertEqual(
            sup_pairs,
            {("ADR-004", "ADR-001"), ("ADR-008", "ADR-002"), ("ADR-010", "ADR-003")},
        )

    def test_unstructured_markdown_corpus_coverage_and_verifiable_citations(self) -> None:
        """All 45 Markdown docs exist, cover canonical IDs, and match verifiable key facts & SHA-256."""
        expected_counts = self.contract["unstructured_markdown_docs"]
        total_md = 0
        for folder in ("services", "apis", "adrs", "requirements", "owners"):
            files = sorted((UNSTRUCTURED_DIR / folder).glob("*.md"))
            self.assertEqual(
                len(files),
                expected_counts[folder],
                f"Markdown count mismatch in data/unstructured/{folder}/",
            )
            total_md += len(files)
        self.assertEqual(total_md, 45)

        # Complete 1:1 Markdown doc coverage for services, ADRs, requirements, and owners charter
        self.assertEqual({p.stem for p in (UNSTRUCTURED_DIR / "services").glob("*.md")}, self.node_ids["services"])
        self.assertEqual({p.stem for p in (UNSTRUCTURED_DIR / "adrs").glob("*.md")}, self.node_ids["adrs"])
        self.assertEqual({p.stem for p in (UNSTRUCTURED_DIR / "requirements").glob("*.md")}, self.node_ids["requirements"])
        api_doc_ids = {p.stem for p in (UNSTRUCTURED_DIR / "apis").glob("*.md")}
        self.assertTrue(api_doc_ids.issubset(self.node_ids["apis"]))
        self.assertIn("api-payments-charge-v2", api_doc_ids)
        self.assertIn("api-payments-charge-v1", api_doc_ids)

        charter_text = (UNSTRUCTURED_DIR / "owners" / "team_charters.md").read_text(encoding="utf-8")
        for owner_id in self.node_ids["owners"]:
            self.assertIn(owner_id, charter_text, f"team_charters.md missing {owner_id}")

        # Verify all 9 hero citations, key facts, and file SHA-256 digests
        citations = self.hero_fixture["verifiable_unstructured_citations"]
        self.assertEqual(len(citations), 9)
        for cit in citations:
            fpath = REPO_ROOT / cit["source_path"]
            self.assertTrue(fpath.is_file(), f"Missing citation file {cit['source_path']}")
            content_bytes = fpath.read_bytes()
            self.assertEqual(
                hashlib.sha256(content_bytes).hexdigest(),
                cit["file_sha256"],
                f"SHA-256 mismatch on {cit['source_path']}",
            )
            text = content_bytes.decode("utf-8")
            for fact in cit["key_facts"]:
                self.assertIn(fact, text, f"Key fact {fact!r} missing from {cit['source_path']}")

    def test_neo4j_to_spanner_loader_and_unstructured_ingest_dry_runs(self) -> None:
        """Path A loader, Path B GraphRAG ingest, APOC stager, and post-load dry-runs succeed offline."""
        # 1. APOC stager
        apoc_proc = subprocess.run(
            [sys.executable, "data/structured/stage_apoc_export.py", "--json"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(apoc_proc.returncode, 0, apoc_proc.stderr)
        apoc_data = json.loads(apoc_proc.stdout)
        self.assertEqual(apoc_data["status"], "OK")
        self.assertEqual(apoc_data["counts"]["total_nodes"], 68)
        self.assertEqual(apoc_data["counts"]["total_logical_edges"], 234)

        # 2. Path A Neo4j -> Spanner loader (--dry-run + --load-dynamic-graph)
        loader_proc = subprocess.run(
            [
                sys.executable,
                "solution/pipelines/neo4j_to_spanner_loader.py",
                "--dry-run",
                "--load-dynamic-graph",
                "--json",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(loader_proc.returncode, 0, loader_proc.stderr)
        loader_data = json.loads(loader_proc.stdout)
        self.assertEqual(loader_data["status"], "DRY_RUN_OK")
        self.assertEqual(loader_data["spanner_target"]["region"], "asia-southeast3")
        self.assertEqual(loader_data["counts"]["total_nodes"], 68)
        self.assertEqual(loader_data["counts"]["explicit_edges"], 174)
        self.assertEqual(loader_data["counts"]["dual_projected_edges_folded_on_nodes"], 60)
        self.assertEqual(loader_data["counts"]["total_logical_graph_edges"], 234)
        self.assertTrue(loader_data["mutation_guardrails"]["within_limit"])
        self.assertEqual(
            loader_data["load_order"]["stage_1_nodes"],
            ["Owners", "Services", "Apis", "Adrs", "Requirements"],
        )
        dyn_by_tbl = {d["table"]: d["rows"] for d in loader_data["stage_3_dynamic_graph"]}
        self.assertEqual(dyn_by_tbl, {"GraphNode": 68, "GraphEdge": 234})

        # 3. Path B Unstructured GraphRAG ingest (--dry-run)
        ingest_proc = subprocess.run(
            [
                sys.executable,
                "solution/pipelines/unstructured_graphrag_ingest.py",
                "--dry-run",
                "--json",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(ingest_proc.returncode, 0, ingest_proc.stderr)
        ingest_data = json.loads(ingest_proc.stdout)
        self.assertEqual(ingest_data["status"], "DRY_RUN_OK")
        self.assertEqual(ingest_data["spanner_target"]["region"], "asia-southeast3")
        self.assertEqual(ingest_data["vertex_ai_embedding"]["location"], "asia-southeast1")
        self.assertEqual(ingest_data["vertex_ai_embedding"]["dimensions"], 768)
        self.assertEqual(ingest_data["stats"]["markdown_files_processed"], 45)
        self.assertGreater(ingest_data["stats"]["total_chunks"], 45)
        self.assertGreater(ingest_data["stats"]["total_mentions"], 100)
        self.assertEqual(ingest_data["hero_citation_checks"]["missing_sources"], [])
        self.assertEqual(ingest_data["hero_citation_checks"]["missing_facts"], [])
        self.assertEqual(
            ingest_data["commit_plan"]["write_order"],
            ["DocumentChunks", "ChunkEntityMentions"],
        )

        # Negative test: gemini-embedding-001 must reject `--vertex-location global` (INV-09)
        bad_ingest = subprocess.run(
            [
                sys.executable,
                "solution/pipelines/unstructured_graphrag_ingest.py",
                "--dry-run",
                "--vertex-location",
                "global",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(bad_ingest.returncode, 0)

        # 4. Post-load orchestrator (--dry-run --strict)
        post_proc = subprocess.run(
            [
                sys.executable,
                "solution/pipelines/apply_postload.py",
                "--dry-run",
                "--strict",
                "--json",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(post_proc.returncode, 0, post_proc.stderr)
        post_data = json.loads(post_proc.stdout)
        self.assertEqual(post_data["status"], "DRY_RUN_OK")
        self.assertEqual(post_data["vertex_ai_remote_model"]["location"], "asia-southeast1")
        for sz in post_data["stages"]["stage_3_03_postload_indexes"]["batch_sizes"]:
            self.assertLessEqual(sz, 10)

    def test_hero_blast_radius_offline_replay_matches_fixture_100_percent(self) -> None:
        """Hero replay confirms exact 7-row blast radius, 5 ADRs, 5 REQs, and 9 citations."""
        proc = subprocess.run(
            [
                sys.executable,
                "solution/pipelines/hero_blast_radius_offline.py",
                "--api-id",
                "api-payments-charge-v2",
                "--check-fixture",
                "solution/sql/fixtures/07_hero_expected_rows.json",
                "--json",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        data = json.loads(proc.stdout)

        self.assertTrue(data["fixture_check"]["passed"], data["fixture_check"]["errors"])
        self.assertEqual(data["fixture_check"]["errors"], [])
        self.assertEqual(data["api_id"], "api-payments-charge-v2")
        self.assertEqual(data["http_method"], "POST")
        self.assertEqual(data["http_path"], "/v2/payments/charge")
        self.assertEqual(data["exposing_service_id"], "svc-payments")
        self.assertEqual(data["impact_row_count"], 7)
        self.assertEqual(data["impact_rows"], self.hero_fixture["expected_impact_rows"])

        # Exact ordered impacted service IDs
        self.assertEqual(
            [r["impacted_service_id"] for r in data["impact_rows"]],
            [
                "svc-billing",
                "svc-checkout",
                "svc-orders",
                "svc-partner-gw",
                "svc-notifications",
                "svc-webhooks",
                "svc-analytics",
            ],
        )
        # Legacy v1 AT_RISK strictly on svc-billing; DATA_FEED strictly on svc-analytics
        self.assertTrue(data["impact_rows"][0]["has_legacy_v1_risk"])
        self.assertFalse(any(r["has_legacy_v1_risk"] for r in data["impact_rows"][1:]))
        self.assertEqual(data["impact_rows"][-1]["impact_category"], "DATA_FEED_CONSUMER")

        # Exact 5 governing ADRs and 5 applicable requirements
        self.assertEqual(len(data["governing_adrs"]), 5)
        self.assertEqual(
            [(a["adr_id"], a["governance_type"]) for a in data["governing_adrs"]],
            [
                ("ADR-004", "MANDATES"),
                ("ADR-005", "CONSTRAINS"),
                ("ADR-006", "CONSTRAINS"),
                ("ADR-008", "CONSTRAINS"),
                ("ADR-010", "CONSTRAINS"),
            ],
        )
        self.assertEqual(len(data["applicable_requirements"]), 5)
        self.assertEqual(
            data["applicable_requirement_ids"],
            ["REQ-001", "REQ-002", "REQ-003", "REQ-005", "REQ-006"],
        )

        # All 9 verifiable citation paths present in retrieved citations
        cited_paths = {c["source_path"] for c in data["unstructured_citations"]}
        expected_paths = {c["source_path"] for c in self.hero_fixture["verifiable_unstructured_citations"]}
        self.assertTrue(
            expected_paths.issubset(cited_paths),
            f"Missing hero citations: {sorted(expected_paths - cited_paths)}",
        )

        # Confirm MATCH TRAIL cycle prevention telemetry
        proof = data["traversal_telemetry"]["walk_vs_trail_depth_5_proof"]
        self.assertGreater(proof["cycle_edge_repetitions_blocked_by_trail"], 0)
        self.assertGreater(proof["walk_paths_total"], proof["trail_paths_total"])

        # Also check labs/run_hero_query.py CLI wrapper offline
        wrapper = subprocess.run(
            [
                sys.executable,
                "labs/run_hero_query.py",
                "--dry-run",
                "--query",
                "B",
                "--check-fixture",
                "solution/sql/fixtures/07_hero_expected_rows.json",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(wrapper.returncode, 0, wrapper.stderr or wrapper.stdout)

    @staticmethod
    def _load_module_from_path(module_name: str, file_path: Path) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_dotenv_example_and_var_expansion_across_python_and_bash_loaders(self) -> None:
        """Copying .env.example and setting GOOGLE_CLOUD_PROJECT=test-proj resolves cleanly everywhere."""
        env_example_text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        # .env.example must not set an active literal SPANNER_PROJECT=${GOOGLE_CLOUD_PROJECT}
        self.assertNotIn("\nSPANNER_PROJECT=${GOOGLE_CLOUD_PROJECT}", env_example_text)
        self.assertIn("# SPANNER_PROJECT=", env_example_text)

        copied_env_text = env_example_text.replace(
            "GOOGLE_CLOUD_PROJECT=your-gcp-project-id",
            "GOOGLE_CLOUD_PROJECT=test-proj",
        )
        legacy_env_text = (
            "GOOGLE_CLOUD_PROJECT=test-proj\n"
            "SPANNER_PROJECT=${GOOGLE_CLOUD_PROJECT}\n"
            "UNRESOLVED_VAR=${DOES_NOT_EXIST_ANYWHERE}\n"
        )

        python_targets = [
            ("apply_postload", REPO_ROOT / "solution" / "pipelines" / "apply_postload.py"),
            ("neo4j_loader_sol", REPO_ROOT / "solution" / "pipelines" / "neo4j_to_spanner_loader.py"),
            ("ingest_sol", REPO_ROOT / "solution" / "pipelines" / "unstructured_graphrag_ingest.py"),
            ("run_hero_query", REPO_ROOT / "labs" / "run_hero_query.py"),
            ("neo4j_loader_starter", REPO_ROOT / "starter" / "pipelines" / "neo4j_to_spanner_loader.py"),
            ("ingest_starter", REPO_ROOT / "starter" / "pipelines" / "unstructured_graphrag_ingest.py"),
        ]

        loaded_mods = {
            name: self._load_module_from_path(f"_regtest_{name}", path)
            for name, path in python_targets
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)

            # Scenario A: Copied .env.example with only GOOGLE_CLOUD_PROJECT=test-proj edited
            (tmp_root / ".env").write_text(copied_env_text, encoding="utf-8")
            for name, mod in loaded_mods.items():
                with self.subTest(scenario="env_example_copy", module=name):
                    with mock.patch.dict(os.environ, {}, clear=True):
                        mod.load_dotenv_stdlib(tmp_root)
                        self.assertEqual(os.environ.get("GOOGLE_CLOUD_PROJECT"), "test-proj")
                        self.assertNotIn("${GOOGLE_CLOUD_PROJECT}", os.environ.get("SPANNER_PROJECT", ""))
                        parsed = mod.build_arg_parser().parse_args([])
                        self.assertEqual(parsed.project, "test-proj")

            # Verify apply_postload --dry-run --strict succeeds with resolved test-proj (no template error)
            with mock.patch.dict(os.environ, {}, clear=True):
                ap = loaded_mods["apply_postload"]
                ap.load_dotenv_stdlib(tmp_root)
                ap_args = ap.build_arg_parser().parse_args(["--dry-run", "--strict"])
                self.assertEqual(ap_args.project, "test-proj")
                ap_summary = ap.apply_postload(
                    sql_dir=REPO_ROOT / "solution" / "sql",
                    project=ap_args.project,
                    dry_run=True,
                    strict=True,
                )
                self.assertEqual(ap_summary["status"], "DRY_RUN_OK")
                self.assertEqual(ap_summary["spanner_target"]["project"], "test-proj")
                self.assertIn(
                    "/projects/test-proj/locations/asia-southeast1/",
                    ap_summary["vertex_ai_remote_model"]["rendered_preview"],
                )

            # Scenario B: Legacy .env containing SPANNER_PROJECT=${GOOGLE_CLOUD_PROJECT} & unresolved var
            (tmp_root / ".env").write_text(legacy_env_text, encoding="utf-8")
            for name, mod in loaded_mods.items():
                with self.subTest(scenario="var_expansion", module=name):
                    with mock.patch.dict(os.environ, {}, clear=True):
                        mod.load_dotenv_stdlib(tmp_root)
                        self.assertEqual(os.environ.get("GOOGLE_CLOUD_PROJECT"), "test-proj")
                        self.assertEqual(os.environ.get("SPANNER_PROJECT"), "test-proj")
                        self.assertNotIn("UNRESOLVED_VAR", os.environ)
                        parsed = mod.build_arg_parser().parse_args([])
                        self.assertEqual(parsed.project, "test-proj")

            # Scenario C: Pre-existing literal ${...} in SPANNER_PROJECT env is ignored in favor of GOOGLE_CLOUD_PROJECT
            for name, mod in loaded_mods.items():
                with self.subTest(scenario="ignore_unresolved_env", module=name):
                    with mock.patch.dict(
                        os.environ,
                        {
                            "GOOGLE_CLOUD_PROJECT": "test-proj",
                            "SPANNER_PROJECT": "${GOOGLE_CLOUD_PROJECT}",
                        },
                        clear=True,
                    ):
                        parsed = mod.build_arg_parser().parse_args([])
                        self.assertEqual(parsed.project, "test-proj")

        # Scenario D: Verify all 3 Bash .env loader scripts expand ${GOOGLE_CLOUD_PROJECT} cleanly
        bash_scripts = [
            ("scripts/provision_workshop_env.sh", ["create", "--dry-run"]),
            ("scripts/verify_live_gcp.sh", ["--dry-run"]),
            ("solution/pipelines/dataflow_migration.sh", ["--dry-run"]),
        ]
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("GOOGLE_CLOUD_PROJECT", "SPANNER_PROJECT", "GCS_BUCKET")
        }
        with tempfile.TemporaryDirectory() as tmp:
            fake_repo = Path(tmp)
            (fake_repo / "data").symlink_to(REPO_ROOT / "data")
            (fake_repo / "labs").symlink_to(REPO_ROOT / "labs")
            (fake_repo / "scripts").mkdir(parents=True)
            (fake_repo / "solution" / "pipelines").mkdir(parents=True)
            (fake_repo / "solution" / "sql").symlink_to(REPO_ROOT / "solution" / "sql")
            for rel_script, _ in bash_scripts:
                dst = fake_repo / rel_script
                shutil.copy2(REPO_ROOT / rel_script, dst)

            for env_label, env_body in (
                ("env_example_copy", copied_env_text),
                ("legacy_var_ref", legacy_env_text),
            ):
                (fake_repo / ".env").write_text(env_body, encoding="utf-8")
                for rel_script, cli_args in bash_scripts:
                    with self.subTest(scenario=f"bash_{env_label}", script=rel_script):
                        proc = subprocess.run(
                            ["bash", str(fake_repo / rel_script), *cli_args],
                            cwd=str(fake_repo),
                            env=clean_env,
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        combined = proc.stdout + "\n" + proc.stderr
                        self.assertEqual(proc.returncode, 0, combined)
                        self.assertIn("test-proj", proc.stdout)
                        self.assertNotIn("${GOOGLE_CLOUD_PROJECT}", combined)

    def test_multi_use_snapshot_enforced_for_multi_query_contexts(self) -> None:
        """Assert multi_use=True is passed whenever multiple execute_sql calls share one snapshot."""

        class _FakeField:
            def __init__(self, name: str) -> None:
                self.name = name

        class _FakeResultSet:
            def __init__(self, rows: list[tuple[Any, ...]], field_names: list[str] | None = None) -> None:
                self._rows = rows
                self.fields = [_FakeField(n) for n in (field_names or [])]

            def __iter__(self) -> Any:
                return iter(self._rows)

        class _FakeSnapshot:
            def __init__(self, *, multi_use: bool, responder: Any) -> None:
                self.multi_use = multi_use
                self.responder = responder
                self.sql_calls: list[str] = []

            def __enter__(self) -> "_FakeSnapshot":
                return self

            def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
                return False

            def execute_sql(self, sql: str, **kwargs: Any) -> _FakeResultSet:
                if not self.multi_use and len(self.sql_calls) >= 1:
                    raise ValueError("Cannot re-use single-use snapshot")
                self.sql_calls.append(sql)
                return self.responder(sql, kwargs)

        class _FakeDatabase:
            def __init__(self, responder: Any) -> None:
                self.responder = responder
                self.snapshot_kwargs: list[dict[str, Any]] = []
                self.snapshots: list[_FakeSnapshot] = []

            def snapshot(self, **kwargs: Any) -> _FakeSnapshot:
                self.snapshot_kwargs.append(dict(kwargs))
                snap = _FakeSnapshot(multi_use=bool(kwargs.get("multi_use", False)), responder=self.responder)
                self.snapshots.append(snap)
                return snap

        # 1. unstructured_graphrag_ingest._introspect_live_schema executes 2 INFORMATION_SCHEMA queries
        ingest_mod = self._load_module_from_path(
            "_regtest_ingest_snapshot",
            REPO_ROOT / "solution" / "pipelines" / "unstructured_graphrag_ingest.py",
        )

        def _introspect_responder(sql: str, _kwargs: dict[str, Any]) -> _FakeResultSet:
            if "INFORMATION_SCHEMA.COLUMNS" in sql:
                return _FakeResultSet(
                    [
                        ("DocumentChunks", "chunk_id"),
                        ("DocumentChunks", "content"),
                        ("ChunkEntityMentions", "chunk_id"),
                        ("ChunkEntityMentions", "entity_type"),
                        ("ChunkEntityMentions", "entity_id"),
                    ]
                )
            if "INFORMATION_SCHEMA.VIEWS" in sql:
                return _FakeResultSet(
                    [("ChunkCitesApiView", "SELECT * FROM ChunkEntityMentions WHERE entity_type = 'API'")]
                )
            raise AssertionError(f"Unexpected SQL: {sql}")

        fake_ingest_db = _FakeDatabase(_introspect_responder)
        chunk_cols, mention_cols, entity_map = ingest_mod._introspect_live_schema(fake_ingest_db)
        self.assertEqual(len(fake_ingest_db.snapshot_kwargs), 1)
        self.assertTrue(
            fake_ingest_db.snapshot_kwargs[0].get("multi_use"),
            "_introspect_live_schema must call database.snapshot(multi_use=True)",
        )
        self.assertEqual(len(fake_ingest_db.snapshots[0].sql_calls), 2)
        self.assertEqual(chunk_cols, ["chunk_id", "content"])
        self.assertEqual(mention_cols, ["chunk_id", "entity_type", "entity_id"])
        self.assertEqual(entity_map["API"], "API")

        # 2. labs/run_hero_query.run_live_queries(['A', 'B', 'C']) executes 3 SQL queries in one snapshot
        hero_mod = self._load_module_from_path(
            "_regtest_hero_snapshot",
            REPO_ROOT / "labs" / "run_hero_query.py",
        )
        queries_sql = hero_mod.extract_hero_queries(
            REPO_ROOT / "solution" / "sql" / "07_hero_api_blast_radius_graphrag.sql"
        )

        def _hero_responder(_sql: str, _kwargs: dict[str, Any]) -> _FakeResultSet:
            return _FakeResultSet(
                [("svc-billing", 1, "DIRECT_SYNC")],
                field_names=["impacted_service_id", "hop_distance", "impact_category"],
            )

        fake_hero_db = _FakeDatabase(_hero_responder)

        fake_spanner_mod = types.ModuleType("google.cloud.spanner")

        class _FakeParamTypes:
            STRING = "STRING"
            FLOAT32 = "FLOAT32"

            @staticmethod
            def Array(elem: Any) -> tuple[str, Any]:
                return ("ARRAY", elem)

        class _FakeInstance:
            def database(self, _db_id: str) -> _FakeDatabase:
                return fake_hero_db

        class _FakeClient:
            def __init__(self, project: str) -> None:
                self.project = project

            def instance(self, _inst_id: str) -> _FakeInstance:
                return _FakeInstance()

        fake_spanner_mod.param_types = _FakeParamTypes  # type: ignore[attr-defined]
        fake_spanner_mod.Client = _FakeClient  # type: ignore[attr-defined]

        fake_google_mod = types.ModuleType("google")
        fake_cloud_mod = types.ModuleType("google.cloud")
        fake_cloud_mod.spanner = fake_spanner_mod  # type: ignore[attr-defined]
        fake_google_mod.cloud = fake_cloud_mod  # type: ignore[attr-defined]

        with (
            mock.patch.dict(
                sys.modules,
                {
                    "google": fake_google_mod,
                    "google.cloud": fake_cloud_mod,
                    "google.cloud.spanner": fake_spanner_mod,
                },
            ),
            mock.patch.object(
                hero_mod,
                "embed_search_query_vertex",
                return_value=[0.01] + [0.0] * 767,
            ),
        ):
            res = hero_mod.run_live_queries(
                ["A", "B", "C"],
                queries_sql,
                project="test-proj",
                instance_id="sdlc-kg-enterprise",
                database_id="sdlc_knowledge_graph",
                spanner_region="asia-southeast3",
                vertex_location="asia-southeast1",
                embedding_model="gemini-embedding-001",
                api_id="api-payments-charge-v2",
                search_query="POST /v2/payments/charge",
                check_fixture=None,
            )

        self.assertEqual(res["status"], "LIVE_OK")
        self.assertEqual(set(res["queries"].keys()), {"A", "B", "C"})
        self.assertEqual(len(fake_hero_db.snapshot_kwargs), 1)
        self.assertTrue(
            fake_hero_db.snapshot_kwargs[0].get("multi_use"),
            "run_live_queries must call database.snapshot(multi_use=True)",
        )
        self.assertEqual(len(fake_hero_db.snapshots[0].sql_calls), 3)


if __name__ == "__main__":
    unittest.main()
