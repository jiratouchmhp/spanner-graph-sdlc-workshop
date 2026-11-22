#!/usr/bin/env python3
"""Module 8: Zero-Docker Lab 1 Simulator & Operational Dry-Run Scripts Suite.

Verifies all Stage A + Stage B offline developer experience & ops scripts cleanly
without requiring Docker or live GCP mutations:
  1. `data/structured/no_docker_neo4j_sim.py` (`--demo --diff --json`, `--list-queries`,
     `--query` parity, WALK vs. TRAIL cycle explosion, 15/15 SHA-256 APOC parity).
  2. `scripts/run_live_sql.py --all-labs --dry-run` (all lab SQL files parse and plan).
  3. `scripts/preflight_check.sh` (offline readiness, Bangkok/Singapore defaults, Zero-Docker).
  4. `scripts/verify_zero_residual.sh --dry-run` (6-category post-teardown audit plan).
  5. `solution/mcp/iam_and_model_armor_setup.sh --cleanup --dry-run` (6-step cleanup,
     IAM propagation retry helper, and idempotent `could not be found` 404 matcher).
  6. `scripts/provision_workshop_env.sh cleanup --all --dry-run` (full teardown chain).
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestZeroDockerAndOpsScripts(unittest.TestCase):
    """End-to-end offline regression suite for Zero-Docker simulator and ops scripts."""

    def test_no_docker_neo4j_simulator_demo_diff_and_queries(self) -> None:
        """Zero-Docker Neo4j CE simulator proves WALK>TRAIL cycle explosion & 15/15 SHA-256 APOC parity."""
        sim_script = REPO_ROOT / "data" / "structured" / "no_docker_neo4j_sim.py"
        self.assertTrue(sim_script.is_file(), "Missing data/structured/no_docker_neo4j_sim.py")

        proc = subprocess.run(
            [sys.executable, str(sim_script), "--demo", "--diff", "--json"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(proc.stdout)

        self.assertEqual(payload["status"], "OK")
        self.assertTrue(payload["zero_docker"])

        sec1 = payload["section_1_summary"]
        self.assertEqual(sec1["total_nodes"], 68)
        self.assertEqual(sec1["total_edges"], 234)
        self.assertEqual(sec1["explicit_edges"], 174)
        self.assertEqual(sec1["dual_projected_edges"], 60)
        self.assertEqual(sec1["top_hub"]["service_id"], "svc-auth")
        self.assertEqual(sec1["top_hub"]["depends_on_in_degree"], 10)

        sec2 = payload["section_2_walk_vs_trail"]
        self.assertGreater(
            sec2["walk_path_count"],
            sec2["trail_path_count"],
            "Expected default WALK mode to produce more cycle paths than TRAIL",
        )
        self.assertGreater(sec2["walk_extra_cycle_paths"], 0)

        apoc_diff = payload["apoc_diff"]
        self.assertEqual(apoc_diff["returncode"], 0)
        self.assertTrue(apoc_diff["passed"])
        self.assertEqual(apoc_diff["tables_checked"], 15)
        self.assertEqual(apoc_diff["tables_matched"], 15)
        self.assertEqual(apoc_diff["mismatches"], 0)

        # Verify --list-queries succeeds cleanly
        list_proc = subprocess.run(
            [sys.executable, str(sim_script), "--list-queries"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(list_proc.returncode, 0, list_proc.stderr or list_proc.stdout)
        self.assertIn("hero", list_proc.stdout.lower())

    def test_run_live_sql_all_labs_dry_run(self) -> None:
        """scripts/run_live_sql.py --all-labs --dry-run plans all lab SQL/GQL files without live GCP."""
        runner = REPO_ROOT / "scripts" / "run_live_sql.py"
        self.assertTrue(runner.is_file(), "Missing scripts/run_live_sql.py")

        proc = subprocess.run(
            [sys.executable, str(runner), "--all-labs", "--dry-run"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        for expected_sql in (
            "01_path_a_schematized.sql",
            "02_path_b_schemaless_comparison.sql",
            "03_indexes_fts_vector_postload.sql",
            "04_fgac_roles_views.sql",
            "05_search_strategies_benchmark.sql",
            "06_hybrid_rrf_rsf_filtered.sql",
            "07_hero_api_blast_radius_graphrag.sql",
            "08_graph_algorithms_criticality.sql",
            "09_opencypher_to_gql_cookbook.sql",
            "10_validate_migration_parity.sql",
            "11_observability_and_studio_viz.sql",
        ):
            self.assertIn(expected_sql, proc.stdout, f"Missing {expected_sql} in --all-labs --dry-run output")

    def test_preflight_check_offline_passes(self) -> None:
        """scripts/preflight_check.sh succeeds offline and verifies Bangkok/Singapore regional defaults."""
        preflight = REPO_ROOT / "scripts" / "preflight_check.sh"
        self.assertTrue(preflight.is_file(), "Missing scripts/preflight_check.sh")

        proc = subprocess.run(
            ["bash", str(preflight)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("asia-southeast3", proc.stdout)
        self.assertIn("asia-southeast1", proc.stdout)
        self.assertIn("Ready for offline labs now", proc.stdout)

    def test_verify_zero_residual_dry_run_audits_all_six_categories(self) -> None:
        """scripts/verify_zero_residual.sh --dry-run covers all 6 GCP cleanup categories cleanly."""
        script = REPO_ROOT / "scripts" / "verify_zero_residual.sh"
        self.assertTrue(script.is_file(), "Missing scripts/verify_zero_residual.sh")

        proc = subprocess.run(
            ["bash", str(script), "--dry-run", "--project", "demo-project"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        for marker in (
            "[1/6]",
            "[2/6]",
            "[3/6]",
            "[4/6]",
            "[5/6]",
            "[6/6]",
            "sdlc-kg-enterprise",
            "sdlc-mcp-impact-analyst",
            "sdlc-tools-impact-analyst",
            "sa-fgac-readonly@demo-project.iam.gserviceaccount.com",
            "sdlc-pci-secret-inspector",
            "sdlc-agent-guardrail-sg",
            "gs://demo-project-sdlc-kg-asia-southeast3",
        ):
            self.assertIn(marker, proc.stdout, f"verify_zero_residual.sh missing audit target {marker!r}")

    def test_iam_and_model_armor_cleanup_dry_run_and_live_hardening(self) -> None:
        """iam_and_model_armor_setup.sh --cleanup --dry-run succeeds and includes SA retry + 404 matcher."""
        script = REPO_ROOT / "solution" / "mcp" / "iam_and_model_armor_setup.sh"
        self.assertTrue(script.is_file(), "Missing solution/mcp/iam_and_model_armor_setup.sh")

        proc = subprocess.run(
            ["bash", str(script), "--cleanup", "--dry-run", "--project", "demo-project"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        for marker in (
            "[Cleanup 1/6]",
            "[Cleanup 2/6]",
            "[Cleanup 3/6]",
            "[Cleanup 4/6]",
            "[Cleanup 5/6]",
            "[Cleanup 6/6]",
            "gcloud run services delete sdlc-mcp-impact-analyst",
            "gcloud secrets delete sdlc-tools-impact-analyst",
            "gcloud iam service-accounts delete sa-fgac-security-auditor@demo-project.iam.gserviceaccount.com",
            "sdlc-pci-secret-inspector",
            "sdlc-agent-guardrail-sg",
        ):
            self.assertIn(marker, proc.stdout, f"iam_and_model_armor_setup.sh --cleanup missing {marker!r}")

        # Verify live-hardening helpers: SA propagation retry loop + gcloud run 404 'not be found' matcher
        source_text = script.read_text(encoding="utf-8")
        self.assertIn("run_cmd_iam_retry()", source_text)
        self.assertIn("not be found", source_text)

        # Also verify provision_workshop_env.sh cleanup --all --dry-run chains Module 6 + Spanner + audit
        prov_proc = subprocess.run(
            [
                "bash",
                "scripts/provision_workshop_env.sh",
                "cleanup",
                "--all",
                "--dry-run",
                "--project",
                "demo-project",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(prov_proc.returncode, 0, prov_proc.stderr or prov_proc.stdout)
        self.assertIn("[Cleanup 1/6]", prov_proc.stdout)
        self.assertIn("Zero-residual audit plan complete", prov_proc.stdout)
        self.assertIn("Full workshop GCP teardown (--all)", prov_proc.stdout)


if __name__ == "__main__":
    unittest.main()
