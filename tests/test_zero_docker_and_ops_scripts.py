#!/usr/bin/env python3
"""Module 8: Zero-Docker Lab 1 Simulator & Operational Dry-Run Scripts Suite.

Verifies all Stage A + Stage B offline developer experience & ops scripts cleanly
without requiring Docker or live GCP mutations:
  1. `data/structured/no_docker_neo4j_sim.py` (`--demo --diff --json`, `--list-queries`,
     `--query` parity, WALK vs. TRAIL cycle explosion, 15/15 SHA-256 APOC parity).
  2. `scripts/run_live_sql.py --all-labs --dry-run` (all lab SQL files parse and plan).
  3. `scripts/preflight_check.sh` (offline readiness, Bangkok single-region primary
     + Singapore AI/Security companion defaults, Zero-Docker).
  4. `scripts/verify_zero_residual.sh --dry-run` (6-category post-teardown audit plan
     for Bangkok primary + Singapore DLP/Model Armor only).
  5. `scripts/verify_live_gcp.sh --dry-run` (5-check live verification plan for
     Bangkok single-region residency & Singapore Vertex AI remote model).
  6. `scripts/provision_workshop_env.sh` (`create`, `apply-postload`, `cleanup`,
     `cleanup --all` dry-runs and rejection of removed `backup-to-singapore`).
  7. `solution/mcp/iam_and_model_armor_setup.sh --cleanup --dry-run` (6-step cleanup,
     IAM propagation retry helper, and idempotent `could not be found` 404 matcher).
  8. Strict single-region Bangkok regression test confirming legacy Singapore Spanner DR
     tokens (`backup-to-singapore`, `sdlc-kg-dr-singapore`, `SPANNER_SECONDARY_INSTANCE`,
     `--require-dr`, `--dr-instance`) do not appear across scripts, Makefile, .env.example,
     root docs, lab guides, or the workshop guide skill.
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
        """scripts/preflight_check.sh succeeds offline with Bangkok primary + Singapore AI/Security defaults."""
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
        for marker in (
            "asia-southeast3",
            "regional-asia-southeast3",
            "asia-southeast1",
            "AI/Security services unavailable in Bangkok",
            "single-region backups only",
            "Ready for offline labs now",
        ):
            self.assertIn(marker, proc.stdout, f"preflight_check.sh missing {marker!r}")
        for forbidden in ("sdlc-kg-dr-singapore", "backup-to-singapore", "SPANNER_SECONDARY_INSTANCE"):
            self.assertNotIn(forbidden, proc.stdout)

        # Verify INV-09 guardrail flags --vertex-location global
        bad_vertex = subprocess.run(
            ["bash", str(preflight), "--vertex-location", "global"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn("INV-09 violation", bad_vertex.stdout)

    def test_verify_zero_residual_dry_run_audits_all_six_categories(self) -> None:
        """scripts/verify_zero_residual.sh --dry-run covers 6 Bangkok/Singapore categories with zero DR instance."""
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
            "regional-asia-southeast3",
            "Cloud DLP & Model Armor only",
            "sdlc-kg-enterprise",
            "sdlc-mcp-impact-analyst",
            "sdlc-tools-impact-analyst",
            "sa-fgac-readonly@demo-project.iam.gserviceaccount.com",
            "sdlc-pci-secret-inspector",
            "sdlc-agent-guardrail-sg",
            "gs://demo-project-sdlc-kg-asia-southeast3",
        ):
            self.assertIn(marker, proc.stdout, f"verify_zero_residual.sh missing audit target {marker!r}")
        self.assertNotIn("sdlc-kg-dr-singapore", proc.stdout)

        # Removed legacy --dr-instance flag must be rejected
        bad_flag = subprocess.run(
            ["bash", str(script), "--dry-run", "--project", "demo-project", "--dr-instance", "foo"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(bad_flag.returncode, 0)

    def test_verify_live_gcp_dry_run_checks_bangkok_residency_and_singapore_vertex(self) -> None:
        """scripts/verify_live_gcp.sh --dry-run plans all 5 single-region checks and rejects --require-dr."""
        script = REPO_ROOT / "scripts" / "verify_live_gcp.sh"
        self.assertTrue(script.is_file(), "Missing scripts/verify_live_gcp.sh")

        proc = subprocess.run(
            ["bash", str(script), "--dry-run", "--project", "demo-project"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        for marker in (
            "[CHECK 1/5]",
            "[CHECK 2/5]",
            "[CHECK 3/5]",
            "[CHECK 4/5]",
            "[CHECK 5/5]",
            "regional-asia-southeast3",
            "locations/asia-southeast1/publishers/google/models/gemini-embedding-001",
            "api-payments-charge-v2",
            "sdlc_readonly_role",
            "PublicApiCatalogView",
        ):
            self.assertIn(marker, proc.stdout, f"verify_live_gcp.sh --dry-run missing {marker!r}")
        self.assertNotIn("sdlc-kg-dr-singapore", proc.stdout)
        self.assertNotIn("--require-dr", proc.stdout)

        # Removed legacy --require-dr flag must be rejected
        bad_flag = subprocess.run(
            ["bash", str(script), "--dry-run", "--project", "demo-project", "--require-dr"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(bad_flag.returncode, 0)

    def test_provision_workshop_env_single_region_dry_runs_and_removed_dr_subcommand(self) -> None:
        """provision_workshop_env.sh dry-runs create/apply-postload/cleanup in Bangkok only & rejects backup-to-singapore."""
        script = REPO_ROOT / "scripts" / "provision_workshop_env.sh"
        self.assertTrue(script.is_file(), "Missing scripts/provision_workshop_env.sh")

        # 1. create --dry-run
        create_proc = subprocess.run(
            ["bash", str(script), "create", "--dry-run", "--project", "demo-project"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(create_proc.returncode, 0, create_proc.stderr or create_proc.stdout)
        for marker in (
            "regional-asia-southeast3",
            "--edition=ENTERPRISE",
            "--default-backup-schedule-type=NONE",
            "Vertex AI=asia-southeast1",
            "Model Armor & DLP=asia-southeast1",
        ):
            self.assertIn(marker, create_proc.stdout)
        self.assertNotIn("sdlc-kg-dr-singapore", create_proc.stdout)

        # 2. apply-postload --dry-run
        post_proc = subprocess.run(
            ["bash", str(script), "apply-postload", "--dry-run", "--project", "demo-project"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(post_proc.returncode, 0, post_proc.stderr or post_proc.stdout)
        self.assertIn("asia-southeast3", post_proc.stdout)
        self.assertIn("asia-southeast1", post_proc.stdout)

        # 3. cleanup --dry-run (Bangkok instance only)
        cleanup_proc = subprocess.run(
            ["bash", str(script), "cleanup", "--dry-run", "--project", "demo-project"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(cleanup_proc.returncode, 0, cleanup_proc.stderr or cleanup_proc.stdout)
        self.assertIn("sdlc-kg-enterprise", cleanup_proc.stdout)
        self.assertNotIn("sdlc-kg-dr-singapore", cleanup_proc.stdout)

        # 4. Removed backup-to-singapore subcommand must fail cleanly
        removed_cmd = subprocess.run(
            ["bash", str(script), "backup-to-singapore", "--dry-run", "--project", "demo-project"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(removed_cmd.returncode, 0)
        self.assertIn("Unknown command: backup-to-singapore", removed_cmd.stderr)

        # 5. INV-09 guard rejects --vertex-location global
        bad_vertex = subprocess.run(
            ["bash", str(script), "create", "--dry-run", "--project", "demo-project", "--vertex-location", "global"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(bad_vertex.returncode, 0)
        self.assertIn("INV-09 violation", bad_vertex.stderr)

    def test_no_legacy_singapore_spanner_dr_tokens_in_workshop_artifacts(self) -> None:
        """Regression guard: legacy Singapore Spanner DR tokens never reappear in scripts, docs, or skills."""
        forbidden_tokens = (
            "backup-to-singapore",
            "sdlc-kg-dr-singapore",
            "SPANNER_SECONDARY_INSTANCE",
            "--require-dr",
            "--dr-instance",
        )
        targets: list[Path] = [
            REPO_ROOT / "Makefile",
            REPO_ROOT / ".env.example",
            REPO_ROOT / "README.md",
            REPO_ROOT / "WORKSHOP_CONTENT_PLAN.md",
            REPO_ROOT / "GEMINI.md",
            REPO_ROOT / "WORKSHOP_SPEC.json",
        ]
        for dir_rel in (
            "scripts",
            "docs",
            ".agents/skills/sdlc-graphrag-workshop-guide",
        ):
            dir_path = REPO_ROOT / dir_rel
            self.assertTrue(dir_path.is_dir(), f"Missing regression scan directory: {dir_rel}")
            for p in sorted(dir_path.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                    targets.append(p)

        self.assertGreaterEqual(len(targets), 20, "Expected at least 20 workshop artifacts in regression scan")
        for path in targets:
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(REPO_ROOT).as_posix()
            for token in forbidden_tokens:
                self.assertNotIn(
                    token,
                    text,
                    f"Forbidden legacy Singapore Spanner DR token {token!r} found in {rel}",
                )

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
