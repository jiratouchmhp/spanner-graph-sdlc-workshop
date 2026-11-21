#!/usr/bin/env python3
"""Module 3: MCP Toolbox YAML Security, Persona RBAC & Prompt-Injection Suite.

Parses `solution/mcp/tools.yaml` and `solution/mcp/personas/*.yaml` with PyYAML
and verifies:
  1. Valid `type: spanner` keys ONLY (`project`, `instance`, `database`, `dialect`)
     and strict rejection of unsupported `databaseRole` / `database_role`.
  2. Regional secretless Vertex AI `embeddingModels.gemini-embedder` (768-dim,
     `gemini-embedding-001`, never `global`, no active static `apiKey`).
  3. Zero raw SQL execution (`spanner-execute-sql`) and zero `templateParameters`.
  4. Explicit `readOnly: true` on all 5 read / GraphRAG tools and scoped
     `PROPOSED`-only write behavior on `draft_adr_proposal`.
  5. Server-side OIDC `authServices` parameter binding (`@caller_email`) on
     `list_my_team_assets` and `draft_adr_proposal`, stripped from LLM-visible
     parameter schemas.
  6. Strict 3-persona toolset isolation (`impact-analyst`, `adr-curator`,
     `security-auditor`) with zero drift vs. `render_persona_tools.py --check`.
  7. Offline execution of `solution/mcp/adk_multi_agent_demo.py --mock` proving
     prompt-injection fail-closed behavior (zero `team-security` leakage).
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = REPO_ROOT / "solution" / "mcp"
PERSONAS_DIR = MCP_DIR / "personas"
TOOLS_YAML_PATH = MCP_DIR / "tools.yaml"
ANTIGRAVITY_MCP_CONFIG = REPO_ROOT / ".antigravity" / "mcp_config.json"


class TestMcpToolboxRbac(unittest.TestCase):
    """Audit MCP Toolbox YAML contracts, persona isolation, and ADK mock RBAC."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = json.loads((REPO_ROOT / "WORKSHOP_SPEC.json").read_text(encoding="utf-8"))
        cls.mcp_contract = cls.spec["mcp_and_security_contract"]
        cls.master_cfg = yaml.safe_load(TOOLS_YAML_PATH.read_text(encoding="utf-8"))
        cls.persona_cfgs: dict[str, dict[str, Any]] = {}
        for persona in cls.mcp_contract["personas"]:
            ypath = REPO_ROOT / persona["yaml_path"]
            cls.persona_cfgs[persona["id"]] = yaml.safe_load(ypath.read_text(encoding="utf-8"))

        sys.path.insert(0, str(MCP_DIR))
        try:
            import adk_multi_agent_demo  # type: ignore[import-not-found]
            import render_persona_tools  # type: ignore[import-not-found]
        finally:
            if str(MCP_DIR) in sys.path:
                sys.path.remove(str(MCP_DIR))
        cls.adk_demo = adk_multi_agent_demo
        cls.renderer = render_persona_tools

    def test_master_and_persona_yamls_parse_and_have_zero_drift(self) -> None:
        """All 4 MCP YAML files parse via PyYAML and pass render_persona_tools.py --check."""
        self.assertIsInstance(self.master_cfg, dict)
        self.assertEqual(
            set(self.persona_cfgs.keys()),
            {"impact-analyst", "adr-curator", "security-auditor"},
        )
        for pid, cfg in self.persona_cfgs.items():
            self.assertIsInstance(cfg, dict, f"Persona YAML {pid} did not parse to mapping")

        proc = subprocess.run(
            [sys.executable, "solution/mcp/render_persona_tools.py", "--check"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_valid_spanner_source_keys_and_reject_database_role(self) -> None:
        """type: spanner sources allow ONLY valid keys and reject databaseRole/database_role."""
        allowed_keys = set(self.mcp_contract["valid_spanner_source_keys"]) | {"type"}
        forbidden_keys = set(self.mcp_contract["forbidden_source_keys"])

        all_configs = [("tools.yaml", self.master_cfg)] + [
            (f"tools.{pid}.yaml", cfg) for pid, cfg in self.persona_cfgs.items()
        ]
        for label, cfg in all_configs:
            sources = cfg.get("sources") or {}
            self.assertIn("sdlc-spanner", sources, f"{label} missing sdlc-spanner source")
            for sname, scfg in sources.items():
                if scfg.get("type") == "spanner":
                    keys = set(scfg.keys())
                    self.assertFalse(
                        keys & forbidden_keys,
                        f"{label} source '{sname}' contains forbidden key(s): {keys & forbidden_keys}",
                    )
                    self.assertEqual(
                        keys,
                        allowed_keys,
                        f"{label} source '{sname}' key mismatch: {keys} != {allowed_keys}",
                    )
                    self.assertEqual(scfg["dialect"], "googlesql")

        # Negative validation: renderer's validate_master_config MUST reject databaseRole & database_role
        for bad_key in ("databaseRole", "database_role"):
            mutated = copy.deepcopy(self.master_cfg)
            mutated["sources"]["sdlc-spanner"][bad_key] = "sdlc_readonly_role"
            with self.assertRaises(ValueError, msg=f"Validator failed to reject {bad_key}"):
                self.renderer.validate_master_config(mutated)

    def test_embedding_model_regional_secretless_vertex_ai(self) -> None:
        """embeddingModels.gemini-embedder uses 768-dim regional Vertex AI without static apiKey."""
        emb_contract = self.mcp_contract["embedding_model_config"]
        all_configs = [("tools.yaml", self.master_cfg)] + [
            (f"tools.{pid}.yaml", cfg) for pid, cfg in self.persona_cfgs.items()
        ]
        for label, cfg in all_configs:
            emb = (cfg.get("embeddingModels") or {}).get("gemini-embedder")
            self.assertIsInstance(emb, dict, f"{label} missing embeddingModels.gemini-embedder")
            self.assertNotIn("apiKey", emb, f"{label} must NOT declare active static apiKey")
            self.assertEqual(emb["type"], emb_contract["type"])
            self.assertEqual(emb["model"], emb_contract["model"])
            self.assertEqual(emb["dimension"], emb_contract["dimension"])
            self.assertEqual(emb["project"], emb_contract["project"])
            self.assertEqual(emb["location"], emb_contract["location"])
            self.assertNotEqual(str(emb["location"]).strip().lower(), "global")

        # Negative tests: reject static apiKey and location: global
        bad_key_cfg = copy.deepcopy(self.master_cfg)
        bad_key_cfg["embeddingModels"]["gemini-embedder"]["apiKey"] = "AIzaFakeStaticKey"
        with self.assertRaises(ValueError):
            self.renderer.validate_master_config(bad_key_cfg)

        bad_loc_cfg = copy.deepcopy(self.master_cfg)
        bad_loc_cfg["embeddingModels"]["gemini-embedder"]["location"] = "global"
        with self.assertRaises(ValueError):
            self.renderer.validate_master_config(bad_loc_cfg)

    def test_curated_tools_forbid_raw_sql_and_template_params_and_enforce_readonly(self) -> None:
        """Zero spanner-execute-sql, zero templateParameters, explicit readOnly: true on reads."""
        expected_tools = {t["name"]: t for t in self.mcp_contract["tools"]}
        master_tools = self.master_cfg["tools"]
        self.assertEqual(set(master_tools.keys()), set(expected_tools.keys()))

        all_configs = [("tools.yaml", self.master_cfg)] + [
            (f"tools.{pid}.yaml", cfg) for pid, cfg in self.persona_cfgs.items()
        ]
        for label, cfg in all_configs:
            for tname, tcfg in cfg["tools"].items():
                self.assertEqual(
                    tcfg.get("type"),
                    "spanner-sql",
                    f"{label} tool '{tname}' must be curated 'spanner-sql', not {tcfg.get('type')!r}",
                )
                self.assertNotIn(
                    "templateParameters",
                    tcfg,
                    f"{label} tool '{tname}' must NEVER declare templateParameters",
                )
                self.assertIsInstance(
                    tcfg.get("readOnly"),
                    bool,
                    f"{label} tool '{tname}' must declare boolean readOnly",
                )
                self.assertEqual(
                    tcfg["readOnly"],
                    expected_tools[tname]["readOnly"],
                    f"{label} tool '{tname}' readOnly mismatch",
                )

                # Tool SQL must respect INV-08 & INV-09 (no ROW_NUMBER(), ScaNN WHERE embedding IS NOT NULL)
                stmt = str(tcfg.get("statement", ""))
                self.assertIsNone(
                    re.search(r"\bROW_NUMBER\s*\(", stmt, re.IGNORECASE),
                    f"{label} tool '{tname}' uses forbidden ROW_NUMBER()",
                )
                if "APPROX_COSINE_DISTANCE" in stmt:
                    self.assertRegex(stmt, r"\bWHERE\s+(?:[A-Za-z_]\w*\.)?embedding\s+IS\s+NOT\s+NULL\b")
                if tname == "draft_adr_proposal":
                    self.assertIn("'PROPOSED'", stmt)
                    self.assertIn("WHERE o.email = @caller_email", stmt)

        # Negative tests: validate_master_config rejects spanner-execute-sql and templateParameters
        raw_sql_cfg = copy.deepcopy(self.master_cfg)
        raw_sql_cfg["tools"]["bad_raw_tool"] = {
            "type": "spanner-execute-sql",
            "source": "sdlc-spanner",
            "readOnly": True,
        }
        with self.assertRaises(ValueError):
            self.renderer.validate_master_config(raw_sql_cfg)

        tmpl_cfg = copy.deepcopy(self.master_cfg)
        tmpl_cfg["tools"]["get_service_ownership_and_slas"]["templateParameters"] = [
            {"name": "table_name", "type": "string"}
        ]
        with self.assertRaises(ValueError):
            self.renderer.validate_master_config(tmpl_cfg)

    def test_oidc_auth_services_parameter_binding_on_team_and_write_tools(self) -> None:
        """list_my_team_assets and draft_adr_proposal bind @caller_email server-side via OIDC."""
        auth_svcs = self.master_cfg.get("authServices") or {}
        self.assertIn("google-corp-oidc", auth_svcs)
        self.assertEqual(auth_svcs["google-corp-oidc"]["type"], "google")
        self.assertIn("clientId", auth_svcs["google-corp-oidc"])

        for tool_name in ("list_my_team_assets", "draft_adr_proposal"):
            tcfg = self.master_cfg["tools"][tool_name]
            self.assertIn("google-corp-oidc", tcfg.get("authRequired") or [])
            params_by_name = {p["name"]: p for p in tcfg.get("parameters") or []}
            self.assertIn("caller_email", params_by_name)
            ce_param = params_by_name["caller_email"]
            self.assertEqual(
                ce_param.get("authServices"),
                [{"name": "google-corp-oidc", "field": "email"}],
            )
            # caller_email MUST be stripped from LLM-visible parameter list
            visible = self.adk_demo.get_llm_visible_parameters(tcfg)
            oidc_bound = self.adk_demo.get_oidc_bound_parameters(tcfg)
            self.assertNotIn("caller_email", visible)
            self.assertIn("caller_email", oidc_bound)
            self.assertEqual(oidc_bound["caller_email"], {"service": "google-corp-oidc", "field": "email"})

    def test_strict_persona_toolset_isolation_and_antigravity_mcp_config(self) -> None:
        """Each persona YAML exposes ONLY its allowed tools & toolset; read-only personas have zero writes."""
        for persona in self.mcp_contract["personas"]:
            pid = persona["id"]
            expected_toolset = persona["toolset"]
            expected_tools = set(persona["allowed_tools"])
            cfg = self.persona_cfgs[pid]

            # Exact single toolset and exact tool set (no extra tools from other personas!)
            self.assertEqual(list(cfg["toolsets"].keys()), [expected_toolset])
            self.assertEqual(set(cfg["toolsets"][expected_toolset]), expected_tools)
            self.assertEqual(set(cfg["tools"].keys()), expected_tools)

            # Master toolset matches persona allowed_tools
            self.assertEqual(set(self.master_cfg["toolsets"][expected_toolset]), expected_tools)

            if pid in ("impact-analyst", "security-auditor"):
                self.assertNotIn("draft_adr_proposal", cfg["tools"])
                for tname, tcfg in cfg["tools"].items():
                    self.assertTrue(
                        tcfg["readOnly"],
                        f"Read-only persona '{pid}' exposes non-readOnly tool '{tname}'",
                    )

        # Cross-persona isolation assertions
        self.assertNotIn("audit_compliance_blast_radius", self.persona_cfgs["impact-analyst"]["tools"])
        self.assertNotIn("audit_compliance_blast_radius", self.persona_cfgs["adr-curator"]["tools"])
        self.assertNotIn("list_my_team_assets", self.persona_cfgs["security-auditor"]["tools"])
        self.assertNotIn("authServices", self.persona_cfgs["security-auditor"])
        self.assertIn("draft_adr_proposal", self.persona_cfgs["adr-curator"]["tools"])

        # Audit .antigravity/mcp_config.json
        mcp_json = json.loads(ANTIGRAVITY_MCP_CONFIG.read_text(encoding="utf-8"))
        servers = mcp_json.get("mcpServers") or {}
        self.assertGreaterEqual(len(servers), 3)
        raw_text = ANTIGRAVITY_MCP_CONFIG.read_text(encoding="utf-8")
        self.assertNotRegex(raw_text, r"AIza[0-9A-Za-z\-_]{20,}|ya29\.|GOCSPX-")
        for srv_name, srv_cfg in servers.items():
            env = srv_cfg.get("env") or {}
            if "VERTEX_AI_LOCATION" in env:
                self.assertEqual(
                    env["VERTEX_AI_LOCATION"],
                    "asia-southeast1",
                    f"Server {srv_name} must target regional asia-southeast1",
                )
            args_joined = " ".join(str(a) for a in srv_cfg.get("args", []))
            self.assertNotRegex(
                args_joined,
                r"solution/mcp/tools\.yaml$",
                f"Server {srv_name} should mount sliced persona YAML, not monolithic tools.yaml",
            )

    def test_adk_multi_agent_demo_mock_prompt_injection_fail_closed(self) -> None:
        """adk_multi_agent_demo.py --mock verifies 6 RBAC checks and prompt-injection fail-closed."""
        # 1. Machine-readable JSON check
        proc_json = subprocess.run(
            [sys.executable, "solution/mcp/adk_multi_agent_demo.py", "--mock", "--json"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc_json.returncode, 0, proc_json.stderr or proc_json.stdout)
        report = json.loads(proc_json.stdout)
        self.assertTrue(report["all_passed"])
        self.assertEqual(report["region_primary"], "asia-southeast3")
        self.assertEqual(report["region_ai_security"], "asia-southeast1")
        self.assertEqual(
            report["prompt_injection_probe"],
            self.mcp_contract["prompt_injection_probe"],
        )

        checks_by_id = {c["id"]: c for c in report["checks"]}
        self.assertEqual(
            set(checks_by_id.keys()),
            {
                "C1_IMPACT_ANALYST_HERO_BLAST_RADIUS",
                "C2_IMPACT_ANALYST_TOOL_ISOLATION",
                "C3_PROMPT_INJECTION_OIDC_FAIL_CLOSED",
                "C4_ADR_CURATOR_SCOPED_WRITE",
                "C5_SECURITY_AUDITOR_READONLY_AUDIT",
                "C6_UNAUTHENTICATED_OIDC_REJECTED",
            },
        )
        for cid, chk in checks_by_id.items():
            self.assertTrue(chk["passed"], f"Check {cid} did not pass")

        c3 = checks_by_id["C3_PROMPT_INJECTION_OIDC_FAIL_CLOSED"]
        self.assertIn("caller_email", c3["llm_injected_params_stripped"])
        self.assertEqual(c3["returned_owner_id"], "team-checkout")
        self.assertEqual(c3["returned_services"], ["svc-checkout", "svc-orders"])
        self.assertFalse(c3["team_security_leaked"])
        self.assertTrue(c3["model_armor_prescreen"]["blocked"])
        self.assertEqual(
            c3["model_armor_prescreen"]["endpoint"],
            "modelarmor.asia-southeast1.rep.googleapis.com",
        )

        # 2. Human-readable CLI check (matches `make demo-mcp-mock`)
        proc_txt = subprocess.run(
            [sys.executable, "solution/mcp/adk_multi_agent_demo.py", "--mock"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc_txt.returncode, 0, proc_txt.stderr or proc_txt.stdout)
        self.assertIn("PASSED", proc_txt.stdout)

    def test_rrf_array_subqueries_preserve_explicit_order_and_rbac_column_restrictions(self) -> None:
        """RRF ARRAY(...) subqueries explicitly ORDER BY score/distance and readonly tools hide restricted Owners cols."""
        starter_yaml = yaml.safe_load(
            (REPO_ROOT / "starter" / "mcp" / "tools.yaml").read_text(encoding="utf-8")
        )
        all_configs = [("tools.yaml", self.master_cfg)] + [
            (f"tools.{pid}.yaml", cfg) for pid, cfg in self.persona_cfgs.items()
        ]

        # 1. Verify RRF tools project approx_distance/fts_score and order explicitly inside ARRAY(...)
        for label, cfg in all_configs:
            for rrf_tool in ("analyze_api_blast_radius_graphrag", "search_sdlc_knowledge_hybrid"):
                self.assertIn(rrf_tool, cfg["tools"])
                stmt = str(cfg["tools"][rrf_tool]["statement"])
                self.assertRegex(
                    stmt,
                    re.compile(r"APPROX_COSINE_DISTANCE\s*\(.*?\)\s+AS\s+approx_distance", re.DOTALL),
                    f"{label}:{rrf_tool} must project APPROX_COSINE_DISTANCE(...) AS approx_distance",
                )
                self.assertRegex(
                    stmt,
                    re.compile(r"SCORE\s*\(.*?\)\s+AS\s+fts_score", re.DOTALL),
                    f"{label}:{rrf_tool} must project SCORE(...) AS fts_score",
                )
                self.assertRegex(
                    stmt,
                    r"ARRAY\s*\(\s*SELECT\s+chunk_id\s+FROM\s+vec_top\s+ORDER\s+BY\s+approx_distance\s+ASC\s*\)",
                    f"{label}:{rrf_tool} must explicitly ORDER BY approx_distance ASC inside vec_top ARRAY(...)",
                )
                self.assertRegex(
                    stmt,
                    r"ARRAY\s*\(\s*SELECT\s+chunk_id\s+FROM\s+fts_top\s+ORDER\s+BY\s+fts_score\s+DESC\s*\)",
                    f"{label}:{rrf_tool} must explicitly ORDER BY fts_score DESC inside fts_top ARRAY(...)",
                )

        # 2. Verify restricted Owners columns (oncall_pager, clearance_tier, escalation_lead)
        #    are excluded from readonly/curator tools and retained ONLY in audit_compliance_blast_radius
        restricted_owner_cols = ("oncall_pager", "clearance_tier", "escalation_lead")
        for label, cfg in all_configs + [("starter/mcp/tools.yaml", starter_yaml)]:
            for tname in ("get_service_ownership_and_slas", "list_my_team_assets"):
                if tname not in (cfg.get("tools") or {}):
                    continue
                stmt = str(cfg["tools"][tname]["statement"])
                for col in restricted_owner_cols:
                    self.assertNotRegex(
                        stmt,
                        rf"\b{col}\b",
                        f"{label}:{tname} exposes restricted Owners column '{col}'",
                    )

        for non_auditor in ("impact-analyst", "adr-curator"):
            for tname, tcfg in self.persona_cfgs[non_auditor]["tools"].items():
                stmt = str(tcfg["statement"])
                for col in restricted_owner_cols:
                    self.assertNotRegex(
                        stmt,
                        rf"\b{col}\b",
                        f"tools.{non_auditor}.yaml:{tname} must not expose restricted column '{col}'",
                    )

        # Security auditor's compliance tool retains o.clearance_tier
        for label, cfg in [
            ("tools.yaml", self.master_cfg),
            ("tools.security-auditor.yaml", self.persona_cfgs["security-auditor"]),
        ]:
            audit_stmt = str(cfg["tools"]["audit_compliance_blast_radius"]["statement"])
            self.assertIn("o.clearance_tier", audit_stmt, f"{label} audit tool should retain o.clearance_tier")

        # Mock list_my_team_assets output must also omit restricted owner columns
        corpus = self.adk_demo.load_acme_corpus(REPO_ROOT)
        team_assets = self.adk_demo._mock_exec_list_my_team_assets(
            corpus, "checkout-orders@acme-commerce.example"
        )
        for col in restricted_owner_cols:
            self.assertNotIn(col, team_assets)


if __name__ == "__main__":
    unittest.main()
