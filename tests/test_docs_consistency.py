#!/usr/bin/env python3
"""Module 7: Documentation Consistency, Link Integrity & Spec Alignment Suite.

Audits `README.md`, `WORKSHOP_CONTENT_PLAN.md`, `GEMINI.md`, and `docs/00..08.md`:
  1. All expected top-level docs and `docs/00..08.md` guides exist.
  2. Zero broken local Markdown links across all 12 documentation files.
  3. Zero internal-tooling or corporate-domain leaks across docs, spec, and Makefile.
  4. Exact alignment with `WORKSHOP_SPEC.json`:
       - Primary region Bangkok (`asia-southeast3` / `regional-asia-southeast3`)
       - Secondary region Singapore (`asia-southeast1`)
       - Verified 2026 pricing figures in `docs/07_sizing_and_cost_tco.md`
       - All 8 Antigravity skill names in root docs & per-lab primary skills in `docs/01..08.md`
       - Hero API (`api-payments-charge-v2`, `POST /v2/payments/charge`), 7 impacted services,
         5 governing ADRs, and 5 applicable requirements
       - Exact `Makefile` target set parity with `WORKSHOP_SPEC.json` and zero stale `make` commands
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
MAKEFILE_PATH = REPO_ROOT / "Makefile"

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
MAKE_INVOCATION_RE = re.compile(r"\bmake\s+(?:[A-Z_]+=\S+\s+)*([a-z][a-z0-9_-]*)\b")

# Constructed from split fragments so the test suite itself contains zero
# literal proprietary tokens if scanned statically.
INTERNAL_LEAK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("corp_domain", re.compile(r"corp\." + r"google\.com", re.IGNORECASE)),
    ("plex_domain", re.compile(r"google" + r"plex\.com", re.IGNORECASE)),
    ("mono_repo", re.compile(r"\b" + "goo" + "gle3" + r"\b", re.IGNORECASE)),
    ("internal_doc_site", re.compile(r"\b" + "g3" + "doc" + r"\b", re.IGNORECASE)),
    ("workspace_fs", re.compile(r"\b" + "ci" + "tc" + r"\b", re.IGNORECASE)),
    ("review_tool", re.compile(r"\b" + "cri" + "tique" + r"\b", re.IGNORECASE)),
    ("vcs_name", re.compile(r"\b" + "pi" + "per" + r"\b", re.IGNORECASE)),
    ("issue_tracker", re.compile(r"\b" + "buga" + "nizer" + r"\b", re.IGNORECASE)),
    ("bug_shortlink", re.compile(r"(?<![a-zA-Z0-9_/])b/\d{6,}")),
    ("cl_shortlink", re.compile(r"(?<![a-zA-Z0-9_/])cl/\d{6,}")),
    ("go_shortlink", re.compile(r"(?<![a-zA-Z0-9_/.:-])go/[a-zA-Z0-9_-]{3,}")),
    ("intranet_search", re.compile(r"\b" + "mo" + "ma" + r"\b", re.IGNORECASE)),
    ("qa_platform", re.compile(r"\b" + "ya" + "qs" + r"\b", re.IGNORECASE)),
    ("rpc_stack", re.compile(r"\b" + "stu" + "bby" + r"\b", re.IGNORECASE)),
    ("cluster_mgr", re.compile(r"\b" + "bo" + "rg" + r"\b", re.IGNORECASE)),
    ("test_log_ui", re.compile(r"\b" + "spo" + "nge" + r"\b", re.IGNORECASE)),
    ("internal_agent", re.compile(r"\b" + "jet" + "ski" + r"\b", re.IGNORECASE)),
    ("src_mount", re.compile(r"/" + "goo" + "gle/src", re.IGNORECASE)),
    ("bin_mount", re.compile(r"/" + "goo" + "gle/bin", re.IGNORECASE)),
]


def _strip_fenced_code(md_text: str) -> str:
    return re.sub(r"```.*?```", "", md_text, flags=re.DOTALL)


class TestDocsConsistency(unittest.TestCase):
    """Audit workshop Markdown guides, links, regional defaults, skills, and Makefile parity."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = json.loads((REPO_ROOT / "WORKSHOP_SPEC.json").read_text(encoding="utf-8"))
        cls.expected_lab_docs = [
            "docs/00_facilitator_guide.md",
            "docs/01_neo4j_community_migration.md",
            "docs/02_two_path_sdlc_workshop.md",
            "docs/03_database_design_and_ingestion.md",
            "docs/04_search_strategy_options.md",
            "docs/05_hybrid_retrieval_and_graphrag.md",
            "docs/06_mcp_toolbox_agent_security.md",
            "docs/07_sizing_and_cost_tco.md",
            "docs/08_observability_and_troubleshooting.md",
        ]
        cls.root_docs = [
            "README.md",
            "WORKSHOP_CONTENT_PLAN.md",
            "GEMINI.md",
        ]
        cls.all_doc_paths = [REPO_ROOT / p for p in cls.root_docs + cls.expected_lab_docs]
        cls.texts: dict[str, str] = {
            p.relative_to(REPO_ROOT).as_posix(): p.read_text(encoding="utf-8")
            for p in cls.all_doc_paths
            if p.is_file()
        }

    def test_all_required_docs_exist_and_match_spec_lab_paths(self) -> None:
        """README.md, WORKSHOP_CONTENT_PLAN.md, GEMINI.md, and docs/00..08.md all exist."""
        for path in self.all_doc_paths:
            self.assertTrue(path.is_file(), f"Missing documentation file: {path.relative_to(REPO_ROOT)}")
            self.assertGreater(
                len(path.read_text(encoding="utf-8").strip()),
                200,
                f"Documentation file unexpectedly short: {path.relative_to(REPO_ROOT)}",
            )

        spec_lab_docs = [lab["doc"] for lab in self.spec["labs"]]
        self.assertEqual(spec_lab_docs, self.expected_lab_docs[1:])

        actual_docs = sorted(p.relative_to(REPO_ROOT).as_posix() for p in DOCS_DIR.glob("*.md"))
        self.assertEqual(actual_docs, self.expected_lab_docs)

    def test_zero_broken_local_file_links_across_all_docs(self) -> None:
        """Every local Markdown link across README, plan, GEMINI.md, and docs/00..08.md resolves."""
        total_local_links = 0
        for path in self.all_doc_paths:
            rel = path.relative_to(REPO_ROOT).as_posix()
            cleaned = _strip_fenced_code(self.texts[rel])
            for m in LINK_RE.finditer(cleaned):
                href = m.group(1).strip()
                if href.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                target = href.split("#", 1)[0].split("?", 1)[0].strip()
                if not target:
                    continue
                total_local_links += 1
                resolved = (path.parent / target).resolve()
                self.assertTrue(
                    resolved.exists(),
                    f"Broken local link {href!r} in {rel} (resolved to {resolved})",
                )
        self.assertGreaterEqual(total_local_links, 100)

    def test_zero_internal_leaks_across_docs_spec_and_makefile(self) -> None:
        """Zero internal-only URLs, tools, or identifiers in docs, WORKSHOP_SPEC.json, or Makefile."""
        files_to_scan = list(self.all_doc_paths) + [
            REPO_ROOT / "WORKSHOP_SPEC.json",
            MAKEFILE_PATH,
            REPO_ROOT / "starter" / "README.md",
            REPO_ROOT / "data" / "structured" / "README.md",
        ]
        for path in files_to_scan:
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(REPO_ROOT).as_posix()
            for label, pat in INTERNAL_LEAK_PATTERNS:
                match = pat.search(text)
                self.assertIsNone(
                    match,
                    f"Internal leak ({label}) in {rel}: {match.group(0)!r}" if match else "",
                )

    def test_regional_defaults_bangkok_primary_singapore_secondary_and_pricing_alignment(self) -> None:
        """Docs match WORKSHOP_SPEC.json Bangkok primary, Singapore secondary, and 2026 pricing."""
        primary_region = self.spec["defaults"]["google_cloud_region"]
        secondary_region = self.spec["defaults"]["google_cloud_secondary_region"]
        primary_cfg = self.spec["defaults"]["spanner_instance_config"]
        self.assertEqual(primary_region, "asia-southeast3")
        self.assertEqual(secondary_region, "asia-southeast1")
        self.assertEqual(primary_cfg, "regional-asia-southeast3")

        # Every root doc and every docs/00..08.md must reference Bangkok primary asia-southeast3
        for rel, text in self.texts.items():
            self.assertIn(
                primary_region,
                text,
                f"{rel} missing primary Bangkok region '{primary_region}'",
            )

        # Root docs and secondary-region architecture guides must reference Singapore asia-southeast1
        sg_required_docs = [
            "README.md",
            "WORKSHOP_CONTENT_PLAN.md",
            "GEMINI.md",
            "docs/00_facilitator_guide.md",
            "docs/02_two_path_sdlc_workshop.md",
            "docs/03_database_design_and_ingestion.md",
            "docs/05_hybrid_retrieval_and_graphrag.md",
            "docs/06_mcp_toolbox_agent_security.md",
            "docs/07_sizing_and_cost_tco.md",
            "docs/08_observability_and_troubleshooting.md",
        ]
        for rel in sg_required_docs:
            self.assertIn(
                secondary_region,
                self.texts[rel],
                f"{rel} missing secondary Singapore region '{secondary_region}'",
            )

        # Root docs explicitly reference regional-asia-southeast3 & Bangkok
        for rel in self.root_docs:
            self.assertIn(primary_cfg, self.texts[rel])
            self.assertIn("Bangkok", self.texts[rel])
            self.assertIn("Singapore", self.texts[rel])

        # Lab 7 sizing guide matches verified 2026 Bangkok, Singapore & Iowa pricing schedule
        doc07 = self.texts["docs/07_sizing_and_cost_tco.md"]
        for expected_rate in ("1.2915", "0.315", "0.063", "1.2285", "1.40712", "0.3432", "1.23"):
            self.assertIn(
                expected_rate,
                doc07,
                f"docs/07_sizing_and_cost_tco.md missing verified 2026 rate {expected_rate}",
            )

    def test_all_eight_skill_names_and_per_lab_primary_skills_documented(self) -> None:
        """Root docs list all 8 Antigravity skills; every Lab 1..8 doc references its primary skills."""
        all_skill_names = [s["name"] for s in self.spec["skills_manifest"]]
        self.assertEqual(len(all_skill_names), 8)

        for rel in self.root_docs:
            text = self.texts[rel]
            for sname in all_skill_names:
                self.assertIn(sname, text, f"{rel} missing Antigravity skill '{sname}'")

        for lab in self.spec["labs"]:
            doc_rel = lab["doc"]
            doc_text = self.texts[doc_rel]
            for primary_skill in lab["primary_skills"]:
                self.assertIn(
                    primary_skill,
                    doc_text,
                    f"{doc_rel} (Lab {lab['lab']}) missing primary skill '{primary_skill}'",
                )

    def test_hero_api_services_adrs_and_requirements_match_spec(self) -> None:
        """Root docs and Lab 5 match exact Hero API, 7 impacted services, 5 ADRs, and 5 REQs."""
        hero = self.spec["hero_scenario"]
        hero_api = hero["api_id"]
        hero_endpoint = f"{hero['http_method']} {hero['http_path']}"
        hero_services = [r["impacted_service_id"] for r in hero["expected_impact_rows"]]
        hero_adrs = [a["adr_id"] for a in hero["governing_adrs"]]
        hero_reqs = list(hero["applicable_requirements"])

        self.assertEqual(hero_api, "api-payments-charge-v2")
        self.assertEqual(len(hero_services), 7)
        self.assertEqual(len(hero_adrs), 5)
        self.assertEqual(len(hero_reqs), 5)

        hero_full_docs = [
            "README.md",
            "WORKSHOP_CONTENT_PLAN.md",
            "GEMINI.md",
            "docs/05_hybrid_retrieval_and_graphrag.md",
        ]
        for rel in hero_full_docs:
            text = self.texts[rel]
            self.assertIn(hero_api, text, f"{rel} missing hero API '{hero_api}'")
            self.assertIn(hero_endpoint, text, f"{rel} missing hero endpoint '{hero_endpoint}'")
            for svc_id in hero_services:
                self.assertIn(svc_id, text, f"{rel} missing hero impacted service '{svc_id}'")
            for adr_id in hero_adrs:
                self.assertIn(adr_id, text, f"{rel} missing hero governing ADR '{adr_id}'")
            for req_id in hero_reqs:
                self.assertIn(req_id, text, f"{rel} missing hero requirement '{req_id}'")

    def test_makefile_targets_match_spec_and_docs_use_only_valid_targets(self) -> None:
        """Makefile targets match WORKSHOP_SPEC.json 1:1 and every documented `make` target is valid."""
        makefile_text = MAKEFILE_PATH.read_text(encoding="utf-8")
        makefile_targets = set(
            re.findall(r"^([a-zA-Z0-9_-]+):\s*##", makefile_text, re.MULTILINE)
        )
        spec_targets = set(self.spec["makefile_targets"])
        self.assertEqual(
            makefile_targets,
            spec_targets,
            f"Makefile target drift vs WORKSHOP_SPEC.json: "
            f"extra={sorted(makefile_targets - spec_targets)}, "
            f"missing={sorted(spec_targets - makefile_targets)}",
        )

        # README.md and GEMINI.md must document all 11 Makefile targets
        for rel in ("README.md", "GEMINI.md"):
            text = self.texts[rel]
            for target in spec_targets:
                self.assertRegex(
                    text,
                    rf"\bmake\b[^\n]*\b{re.escape(target)}\b",
                    f"{rel} missing make target '{target}'",
                )

        # Every `make <target>` invocation across all 12 docs must reference a real Makefile target
        for rel, text in self.texts.items():
            for match in MAKE_INVOCATION_RE.finditer(text):
                target = match.group(1)
                self.assertIn(
                    target,
                    makefile_targets,
                    f"{rel} invokes undefined Makefile target 'make {target}'",
                )


if __name__ == "__main__":
    unittest.main()
