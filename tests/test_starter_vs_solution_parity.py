#!/usr/bin/env python3
"""Module 6: Starter vs. Solution Parity & Armed-Stub Verification Suite.

Verifies:
  1. Every `starter/` exercise declared in `WORKSHOP_SPEC.json` (15 exercises
     spanning Labs 1..8) exists on disk and contains its `TODO(Lab <N>)` marker.
  2. Every non-worksheet starter file has a corresponding completed `solution/`
     counterpart containing ZERO unfinished `TODO(Lab` markers.
  3. Every individual lab `1..8` on `starter/` FAILS verification before
     completion (`scripts/verify_lab.py --track starter --lab <N>`).
  4. `solution/` passes 100% of `scripts/verify_lab.py --track solution --lab all`.
  5. `scripts/verify_lab.py --verify-starter-stubs` confirms full parity in one pass.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_lab.py"
TODO_RE = re.compile(r"TODO\(\s*Lab\s*[1-8]\s*\)", re.IGNORECASE)


class TestStarterVsSolutionParity(unittest.TestCase):
    """Confirm all starter stubs are armed and solution/ passes 100% of Labs 1..8."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = json.loads((REPO_ROOT / "WORKSHOP_SPEC.json").read_text(encoding="utf-8"))
        cls.exercises = cls.spec["starter_exercises"]

    def test_all_starter_exercise_files_exist_with_todo_markers_and_solution_counterparts(self) -> None:
        """All 15 starter/ exercise files exist with TODO(Lab N) markers; solution/ has zero TODOs."""
        self.assertEqual(len(self.exercises), 15)
        self.assertTrue((REPO_ROOT / "starter" / "README.md").is_file())

        labs_covered: set[int] = set()
        for ex in self.exercises:
            lab_num = int(ex["lab"])
            labs_covered.add(lab_num)
            rel = ex["file"]
            marker = ex["todo_marker"]
            starter_path = REPO_ROOT / rel

            self.assertTrue(starter_path.is_file(), f"Missing starter exercise file: {rel}")
            text = starter_path.read_text(encoding="utf-8")
            self.assertIn(
                marker,
                text,
                f"Starter file {rel} missing required marker {marker!r}",
            )
            self.assertTrue(
                TODO_RE.search(text),
                f"Starter file {rel} does not match TODO(Lab N) regex",
            )

            # Verify matching solution/ counterpart exists (except Lab 7 worksheet)
            if rel != "starter/tools/customer_scale_worksheet.json":
                sol_rel = rel.replace("starter/", "solution/", 1)
                sol_path = REPO_ROOT / sol_rel
                self.assertTrue(sol_path.is_file(), f"Missing solution counterpart: {sol_rel}")
                sol_text = sol_path.read_text(encoding="utf-8")
                self.assertIsNone(
                    TODO_RE.search(sol_text),
                    f"Solution file {sol_rel} unexpectedly contains unfinished TODO(Lab N) marker",
                )

        self.assertEqual(labs_covered, {1, 2, 3, 4, 5, 6, 7, 8})

        # Confirm NO file anywhere under solution/ contains unfinished TODO(Lab N) markers
        for sol_file in sorted((REPO_ROOT / "solution").rglob("*")):
            if not sol_file.is_file() or sol_file.suffix in {".pyc", ".png", ".jpg"}:
                continue
            if "__pycache__" in sol_file.parts:
                continue
            sol_text = sol_file.read_text(encoding="utf-8", errors="ignore")
            self.assertIsNone(
                TODO_RE.search(sol_text),
                f"Solution artifact {sol_file.relative_to(REPO_ROOT)} contains TODO(Lab marker",
            )

    def test_every_starter_lab_1_to_8_fails_before_completion(self) -> None:
        """Each lab 1..8 fails scripts/verify_lab.py --track starter before completion."""
        for lab_num in range(1, 9):
            with self.subTest(lab=lab_num):
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(VERIFY_SCRIPT),
                        "--track",
                        "starter",
                        "--lab",
                        str(lab_num),
                        "--json",
                    ],
                    cwd=str(REPO_ROOT),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(
                    proc.returncode,
                    0,
                    f"starter/ Lab {lab_num} unexpectedly PASSED before completion!",
                )
                payload = json.loads(proc.stdout)
                self.assertFalse(payload["all_passed"])
                self.assertEqual(len(payload["results"]), 1)
                lab_res = payload["results"][0]
                self.assertEqual(lab_res["lab"], lab_num)
                self.assertFalse(lab_res["passed"])
                self.assertGreater(len(lab_res["failures"]), 0)

    def test_solution_passes_100_percent_of_verify_lab_all(self) -> None:
        """scripts/verify_lab.py --track solution --lab all passes 100% across Labs 1..8."""
        proc = subprocess.run(
            [
                sys.executable,
                str(VERIFY_SCRIPT),
                "--track",
                "solution",
                "--lab",
                "all",
                "--json",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["track"], "solution")
        self.assertEqual(payload["labs_checked"], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertTrue(payload["all_passed"], payload)
        for res in payload["results"]:
            self.assertTrue(
                res["passed"],
                f"solution/ Lab {res['lab']} failed: {res['failures']}",
            )
            self.assertEqual(res["failures"], [])

    def test_verify_starter_stubs_flag_end_to_end(self) -> None:
        """scripts/verify_lab.py --verify-starter-stubs confirms armed starter + green solution."""
        proc = subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), "--verify-starter-stubs", "--json"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["passed"])
        self.assertTrue(payload["starter_all_armed_failed_as_expected"])
        self.assertEqual(payload["starter_unexpected_passes"], [])
        self.assertTrue(payload["solution_all_passed"])
        self.assertEqual(payload["solution_failures"], [])


if __name__ == "__main__":
    unittest.main()
