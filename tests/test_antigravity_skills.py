#!/usr/bin/env python3
"""Module 5: Antigravity IDE Skills & Workspace Rules Audit Suite.

Audits all 8 skills under `.agents/skills/` plus `.agents/rules/spanner-graph-workshop.md`:
  1. Exact 8-skill directory inventory matching `WORKSHOP_SPEC.json`.
  2. Valid YAML frontmatter (`name` matches folder, `description` < 1024 chars
     containing both `Use when` and `Don't use for`).
  3. Line-count budgets:
       - `SKILL.md`: 55 to 500 lines inclusive
       - `references/*.md`: 60 to 500 lines inclusive
  4. 1-level-deep linking: every expected `references/*.md` is directly linked
     from its `SKILL.md`, with zero orphan files and zero nested reference folders.
  5. Zero broken local Markdown links across all `SKILL.md` and `references/*.md`.
  6. Zero internal-tooling or corporate-domain leaks across all `.agents/` files.
  7. Zero Acme Commerce or workshop-filename hardcoding inside the 7 reusable
     production skills (`is_production_reusable: true`), while verifying the 1
     workshop guide skill (`sdlc-graphrag-workshop-guide`) covers Acme Commerce.
"""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / ".agents"
SKILLS_DIR = AGENTS_DIR / "skills"
RULE_PATH = AGENTS_DIR / "rules" / "spanner-graph-workshop.md"

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

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

# Workshop-specific identifiers forbidden inside the 7 reusable production skills
REUSABLE_SKILL_FORBIDDEN_RE = re.compile(
    r"\b(?:"
    r"Acme|acme-commerce|api-payments-charge-v[12]|"
    r"svc-payments|svc-checkout|svc-billing|svc-orders|svc-partner-gw|"
    r"svc-notifications|svc-webhooks|svc-analytics|svc-auth|svc-fraud|"
    r"svc-ledger|svc-inventory|"
    r"SdlcGraph|SdlcDynamicGraph|SdlcAlgoGraph|SdlcSynonymDict|"
    r"SdlcTextEmbedding|SdlcReranker|"
    r"01_path_a_schematized|02_path_b_schemaless|03_indexes_fts|"
    r"04_fgac_roles|05_search_strategies|06_hybrid_rrf|07_hero_api|"
    r"08_graph_algorithms|09_opencypher|10_validate_migration|11_observability|"
    r"neo4j_to_spanner_loader|unstructured_graphrag_ingest|"
    r"hero_blast_radius_offline|customer_scale_worksheet|WORKSHOP_SPEC\.json"
    r")\b|TODO\(\s*Lab",
    re.IGNORECASE,
)


def _strip_fenced_code(md_text: str) -> str:
    return re.sub(r"```.*?```", "", md_text, flags=re.DOTALL)


class TestAntigravitySkills(unittest.TestCase):
    """Audit all 8 Antigravity skills for structure, links, hygiene, and portability."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = json.loads((REPO_ROOT / "WORKSHOP_SPEC.json").read_text(encoding="utf-8"))
        cls.manifest = cls.spec["skills_manifest"]

    def test_exact_eight_skill_directories_and_setup_script(self) -> None:
        """Exact 8 skill directories match WORKSHOP_SPEC.json; setup script & always-on rule pass."""
        self.assertEqual(len(self.manifest), 8)
        reusable_count = sum(1 for s in self.manifest if s["is_production_reusable"])
        workshop_count = sum(1 for s in self.manifest if not s["is_production_reusable"])
        self.assertEqual(reusable_count, 7)
        self.assertEqual(workshop_count, 1)

        actual_dirs = sorted(p.name for p in SKILLS_DIR.iterdir() if p.is_dir())
        expected_dirs = sorted(s["name"] for s in self.manifest)
        self.assertEqual(actual_dirs, expected_dirs)

        # Always-on rule file check
        self.assertTrue(RULE_PATH.is_file(), "Missing .agents/rules/spanner-graph-workshop.md")
        rule_text = RULE_PATH.read_text(encoding="utf-8")
        fm_m = FRONTMATTER_RE.match(rule_text)
        self.assertIsNotNone(fm_m, "Always-on rule missing YAML frontmatter")
        rule_fm = yaml.safe_load(fm_m.group(1))
        self.assertEqual(rule_fm.get("trigger"), "always_on")

        # Run scripts/setup_antigravity_skills.sh
        proc = subprocess.run(
            ["bash", "scripts/setup_antigravity_skills.sh"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_yaml_frontmatter_and_description_trigger_contract(self) -> None:
        """Every SKILL.md has valid YAML frontmatter, matching name, and <1024 char trigger description."""
        for skill in self.manifest:
            sname = skill["name"]
            skill_md = REPO_ROOT / skill["path"] / "SKILL.md"
            self.assertTrue(skill_md.is_file(), f"Missing {skill_md}")
            text = skill_md.read_text(encoding="utf-8")

            fm_m = FRONTMATTER_RE.match(text)
            self.assertIsNotNone(fm_m, f"{sname}/SKILL.md missing YAML frontmatter")
            fm = yaml.safe_load(fm_m.group(1))
            self.assertIsInstance(fm, dict, f"{sname} frontmatter must be a mapping")
            self.assertEqual(fm.get("name"), sname)
            self.assertRegex(sname, r"^[a-z0-9-]+$")
            self.assertLessEqual(len(sname), 64)

            desc = str(fm.get("description", "")).strip()
            self.assertTrue(desc, f"{sname} description must be non-empty")
            self.assertLess(
                len(desc),
                1024,
                f"{sname} description length {len(desc)} must be < 1024 chars",
            )
            self.assertIn("Use when", desc, f"{sname} description missing 'Use when' trigger")
            self.assertIn(
                "Don't use for",
                desc,
                f"{sname} description missing \"Don't use for\" anti-trigger",
            )

    def test_line_count_budgets_for_skills_and_references(self) -> None:
        """All SKILL.md files are 55-500 lines and all references/*.md files are 60-500 lines."""
        for skill in self.manifest:
            sname = skill["name"]
            sdir = REPO_ROOT / skill["path"]
            skill_lines = len((sdir / "SKILL.md").read_text(encoding="utf-8").splitlines())
            self.assertGreaterEqual(
                skill_lines, 55, f"{sname}/SKILL.md too short ({skill_lines} < 55 lines)"
            )
            self.assertLessEqual(
                skill_lines, 500, f"{sname}/SKILL.md too long ({skill_lines} > 500 lines)"
            )

            for ref_name in skill["references"]:
                ref_path = sdir / "references" / ref_name
                self.assertTrue(ref_path.is_file(), f"Missing reference {ref_path}")
                ref_lines = len(ref_path.read_text(encoding="utf-8").splitlines())
                self.assertGreaterEqual(
                    ref_lines,
                    60,
                    f"{sname}/references/{ref_name} too short ({ref_lines} < 60 lines)",
                )
                self.assertLessEqual(
                    ref_lines,
                    500,
                    f"{sname}/references/{ref_name} too long ({ref_lines} > 500 lines)",
                )

    def test_one_level_deep_reference_links_zero_orphans_and_zero_broken_links(self) -> None:
        """Every expected reference is 1-level-deep linked from SKILL.md; zero orphans or broken links."""
        for skill in self.manifest:
            sname = skill["name"]
            sdir = REPO_ROOT / skill["path"]
            refs_dir = sdir / "references"
            self.assertTrue(refs_dir.is_dir(), f"{sname} missing references/ directory")

            # Enforce 1-level-deep directory structure (no nested subdirs under references/)
            nested_dirs = [p for p in refs_dir.iterdir() if p.is_dir()]
            self.assertEqual(
                nested_dirs,
                [],
                f"{sname}/references/ must be 1-level-deep (found nested dirs: {nested_dirs})",
            )

            # Zero orphan files anywhere inside the skill directory
            expected_files = {"SKILL.md"} | {f"references/{r}" for r in skill["references"]}
            actual_files = {
                p.relative_to(sdir).as_posix()
                for p in sdir.rglob("*")
                if p.is_file()
            }
            self.assertEqual(
                actual_files,
                expected_files,
                f"Orphan or missing files in {sname}: "
                f"extra={sorted(actual_files - expected_files)}, "
                f"missing={sorted(expected_files - actual_files)}",
            )

            # Verify every reference is explicitly linked in SKILL.md via Markdown link
            skill_text = _strip_fenced_code((sdir / "SKILL.md").read_text(encoding="utf-8"))
            skill_links = {
                m.group(1).strip().split("#", 1)[0]
                for m in LINK_RE.finditer(skill_text)
            }
            for ref_name in skill["references"]:
                expected_rel = f"references/{ref_name}"
                self.assertIn(
                    expected_rel,
                    skill_links,
                    f"{sname}/SKILL.md does not link 1-level-deep reference '{expected_rel}'",
                )

            # Verify zero broken local links across SKILL.md and all references/*.md
            for md_file in sorted(sdir.rglob("*.md")):
                cleaned = _strip_fenced_code(md_file.read_text(encoding="utf-8"))
                for m in LINK_RE.finditer(cleaned):
                    href = m.group(1).strip()
                    if href.startswith(("http://", "https://", "mailto:", "#")):
                        continue
                    target = href.split("#", 1)[0].split("?", 1)[0].strip()
                    if not target:
                        continue
                    resolved = (md_file.parent / target).resolve()
                    self.assertTrue(
                        resolved.exists(),
                        f"Broken local link {href!r} in {md_file.relative_to(REPO_ROOT)}",
                    )

    def test_zero_internal_leaks_across_all_agent_skills_and_rules(self) -> None:
        """All .agents/ Markdown files contain zero internal-only URLs, tools, or identifiers."""
        md_files = sorted(AGENTS_DIR.rglob("*.md"))
        self.assertGreaterEqual(len(md_files), 39)  # 8 SKILL.md + 30 refs + 1 rule
        for md_file in md_files:
            text = md_file.read_text(encoding="utf-8")
            rel = md_file.relative_to(REPO_ROOT)
            for label, pat in INTERNAL_LEAK_PATTERNS:
                match = pat.search(text)
                self.assertIsNone(
                    match,
                    f"Internal leak ({label}) in {rel}: {match.group(0)!r}" if match else "",
                )

    def test_zero_acme_or_workshop_hardcoding_in_seven_reusable_production_skills(self) -> None:
        """The 7 reusable production skills stay 100% generic; the 1 workshop guide covers Acme."""
        for skill in self.manifest:
            sname = skill["name"]
            sdir = REPO_ROOT / skill["path"]
            if skill["is_production_reusable"]:
                for md_file in sorted(sdir.rglob("*.md")):
                    text = md_file.read_text(encoding="utf-8")
                    hits = sorted(set(REUSABLE_SKILL_FORBIDDEN_RE.findall(text)))
                    self.assertEqual(
                        hits,
                        [],
                        f"Reusable production skill file {md_file.relative_to(REPO_ROOT)} "
                        f"contains workshop/Acme-specific hardcoding: {hits}",
                    )
            else:
                self.assertEqual(sname, "sdlc-graphrag-workshop-guide")
                combined = "\n".join(
                    p.read_text(encoding="utf-8") for p in sorted(sdir.rglob("*.md"))
                )
                for expected_token in ("Acme Commerce", "api-payments-charge-v2", "SdlcGraph", "TODO(Lab"):
                    self.assertIn(expected_token, combined)

    def test_spanner_instance_create_descriptions_within_api_length_bounds(self) -> None:
        """Every gcloud spanner instances create --description in skills stays within 4-30 chars."""
        bash_block_re = re.compile(r"```(?:bash|sh)\n(.*?)```", re.DOTALL)
        create_cmd_re = re.compile(
            r"gcloud\s+spanner\s+instances\s+create\s+(?:[^\n]*\\\n)*[^\n]*",
            re.MULTILINE,
        )
        desc_re = re.compile(r'--description=(?:"([^"]+)"|\'([^\']+)\'|(\S+))')
        checked = 0
        for md_file in sorted(SKILLS_DIR.rglob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            rel = md_file.relative_to(REPO_ROOT)
            for block_m in bash_block_re.finditer(text):
                for match in create_cmd_re.finditer(block_m.group(1)):
                    cmd_block = match.group(0)
                    dm = desc_re.search(cmd_block)
                    self.assertIsNotNone(
                        dm, f"Missing --description on gcloud spanner instances create in {rel}"
                    )
                    desc_val = next(g for g in dm.groups() if g is not None)
                    checked += 1
                    self.assertGreaterEqual(
                        len(desc_val),
                        4,
                        f"Spanner instance --description too short (<4 chars) in {rel}: {desc_val!r}",
                    )
                    self.assertLessEqual(
                        len(desc_val),
                        30,
                        f"Spanner instance --description exceeds 30-char API limit in {rel}: "
                        f"{desc_val!r} ({len(desc_val)} chars)",
                    )
        self.assertGreaterEqual(checked, 5)


if __name__ == "__main__":
    unittest.main()
