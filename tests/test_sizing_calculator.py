#!/usr/bin/env python3
"""Module 4: Enterprise Sizing, Verified 2026 Regional Pricing & 3-Store TCO Tests.

Unit-tests `solution/tools/sizing_calculator.py` (both Python API and CLI) across:
  1. Verified 2026 rate cards:
       - Bangkok (`asia-southeast3`): $1.2915/node-hr Enterprise, SSD $0.315,
         HDD $0.063, backup $0.0315, Data Boost $1.2285/1k SPU-hr
       - Singapore (`asia-southeast1`): $1.40712/node-hr Enterprise, SSD $0.3432,
         HDD $0.06864, backup $0.03432, Data Boost $1.33848/1k SPU-hr
       - Iowa (`us-central1`): $1.23/node-hr Enterprise, SSD $0.30, HDD $0.06,
         backup $0.03, Data Boost $1.17/1k SPU-hr
  2. 1-Year (20% off) and 3-Year (40% off) CUD discounts across all regions/editions.
  3. All 4 T-shirt presets (`workshop`, `small`, `medium`, `large`).
  4. Mandatory production >= 1,000 PU floor & < 65% regional HP CPU threshold.
  5. Default single-region Bangkok architecture (`asia-southeast3`,
     `dr_backup_region` absent/`none`, `$0.00/mo` cross-region egress, same-region
     backups at `$0.0315/GiB-mo`), plus optional `--dr-backup-region` override
     math when explicitly passed (`$0.08/GiB` egress + `$0.03432/GiB-mo` storage).
  6. Customer worksheet JSON input (`starter/tools/customer_scale_worksheet.json`)
     defaulting to single-region Bangkok (`dr_backup_region: "none"`).
  7. Legacy 3-store consolidation TCO savings (Infra-Only & Fully Loaded).
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "solution" / "tools"
WORKSHEET_PATH = REPO_ROOT / "starter" / "tools" / "customer_scale_worksheet.json"


class TestSizingCalculator(unittest.TestCase):
    """Comprehensive unit & CLI tests for solution/tools/sizing_calculator.py."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = json.loads((REPO_ROOT / "WORKSHOP_SPEC.json").read_text(encoding="utf-8"))
        sys.path.insert(0, str(TOOLS_DIR))
        try:
            import sizing_calculator  # type: ignore[import-not-found]
        finally:
            if str(TOOLS_DIR) in sys.path:
                sys.path.remove(str(TOOLS_DIR))
        cls.calc = sizing_calculator

    def test_verified_2026_regional_rate_cards_match_spec_contract(self) -> None:
        """Bangkok, Singapore, and Iowa pricing constants match WORKSHOP_SPEC.json exactly."""
        regions = self.calc.PRICING_REGIONS
        reg_spec = self.spec["regional_architecture"]

        # Bangkok (asia-southeast3)
        bkk = regions["asia-southeast3"]
        self.assertAlmostEqual(bkk["compute_per_node_hour_usd"]["enterprise"], 1.2915, places=6)
        self.assertAlmostEqual(bkk["compute_per_node_hour_usd"]["standard"], 0.945, places=6)
        self.assertAlmostEqual(bkk["compute_per_node_hour_usd"]["enterprise_plus"], 1.7955, places=6)
        self.assertAlmostEqual(bkk["storage_per_gib_month_usd"]["ssd"], 0.315, places=6)
        self.assertAlmostEqual(bkk["storage_per_gib_month_usd"]["hdd"], 0.063, places=6)
        self.assertAlmostEqual(bkk["storage_per_gib_month_usd"]["backup"], 0.0315, places=6)
        self.assertAlmostEqual(bkk["data_boost_per_1000_spu_hour_usd"], 1.2285, places=6)
        self.assertAlmostEqual(bkk["regional_multiplier_vs_us_central1"], 1.05, places=6)
        self.assertAlmostEqual(bkk["workshop_100pu_8hr_compute_usd"], 1.0332, places=6)
        self.assertEqual(bkk["intra_region_replication_per_gib_usd"], 0.0)
        # 5x SSD -> HDD savings ratio
        self.assertAlmostEqual(
            bkk["storage_per_gib_month_usd"]["ssd"] / bkk["storage_per_gib_month_usd"]["hdd"],
            5.0,
            places=6,
        )

        # Singapore (asia-southeast1)
        sg = regions["asia-southeast1"]
        self.assertAlmostEqual(sg["compute_per_node_hour_usd"]["enterprise"], 1.40712, places=6)
        self.assertAlmostEqual(sg["compute_per_node_hour_usd"]["standard"], 1.0296, places=6)
        self.assertAlmostEqual(sg["compute_per_node_hour_usd"]["enterprise_plus"], 1.95624, places=6)
        self.assertAlmostEqual(sg["storage_per_gib_month_usd"]["ssd"], 0.3432, places=6)
        self.assertAlmostEqual(sg["storage_per_gib_month_usd"]["hdd"], 0.06864, places=6)
        self.assertAlmostEqual(sg["storage_per_gib_month_usd"]["backup"], 0.03432, places=6)
        self.assertAlmostEqual(sg["data_boost_per_1000_spu_hour_usd"], 1.33848, places=6)
        self.assertAlmostEqual(sg["regional_multiplier_vs_us_central1"], 1.144, places=6)

        # Iowa (us-central1)
        us = regions["us-central1"]
        self.assertAlmostEqual(us["compute_per_node_hour_usd"]["enterprise"], 1.23, places=6)
        self.assertAlmostEqual(us["compute_per_node_hour_usd"]["standard"], 0.90, places=6)
        self.assertAlmostEqual(us["compute_per_node_hour_usd"]["enterprise_plus"], 1.71, places=6)
        self.assertAlmostEqual(us["storage_per_gib_month_usd"]["ssd"], 0.30, places=6)
        self.assertAlmostEqual(us["storage_per_gib_month_usd"]["hdd"], 0.06, places=6)
        self.assertAlmostEqual(us["storage_per_gib_month_usd"]["backup"], 0.03, places=6)
        self.assertAlmostEqual(us["data_boost_per_1000_spu_hour_usd"], 1.17, places=6)

        # Cross-check exact parity with WORKSHOP_SPEC.json & single-region architecture flags
        self.assertTrue(self.spec["defaults"]["spanner_single_region_only"])
        self.assertEqual(self.spec["defaults"]["primary_region"], "asia-southeast3")
        self.assertEqual(self.spec["defaults"]["companion_ai_security_region"], "asia-southeast1")
        self.assertEqual(
            reg_spec["architecture_mode"],
            "single_region_bangkok_core_plus_singapore_companion_ai_security",
        )
        self.assertEqual(reg_spec["default_cross_region_dr_egress_usd"], 0.0)
        self.assertEqual(
            reg_spec["primary_region"]["architecture_role"],
            "primary_and_only_database_and_app_region",
        )
        self.assertEqual(
            reg_spec["secondary_region"]["architecture_role"],
            "companion_ai_and_security_only_no_spanner_dr",
        )
        for rkey, spec_key in (
            ("asia-southeast3", "primary_region"),
            ("asia-southeast1", "secondary_region"),
            ("us-central1", "reference_us_region"),
        ):
            self.assertEqual(
                regions[rkey]["compute_per_node_hour_usd"],
                reg_spec[spec_key]["compute_per_node_hour_usd"],
            )
            self.assertEqual(
                regions[rkey]["storage_per_gib_month_usd"],
                reg_spec[spec_key]["storage_per_gib_month_usd"],
            )
            self.assertEqual(
                regions[rkey]["data_boost_per_1000_spu_hour_usd"],
                reg_spec[spec_key]["data_boost_per_1000_spu_hour_usd"],
            )

    def test_cud_1yr_20pct_and_3yr_40pct_discounts_across_all_regions_and_editions(self) -> None:
        """1-Year CUD is exact 20% discount (0.8x) and 3-Year CUD is exact 40% discount (0.6x)."""
        for rkey, rdata in self.calc.PRICING_REGIONS.items():
            for edition in ("standard", "enterprise", "enterprise_plus"):
                od = rdata["compute_per_node_hour_usd"][edition]
                cud1 = rdata["compute_cud_1yr_per_node_hour_usd"][edition]
                cud3 = rdata["compute_cud_3yr_per_node_hour_usd"][edition]
                self.assertAlmostEqual(cud1, od * 0.80, places=6, msg=f"{rkey}/{edition} 1yr CUD")
                self.assertAlmostEqual(cud3, od * 0.60, places=6, msg=f"{rkey}/{edition} 3yr CUD")

        # Verify evaluated monthly compute & RO replica costs apply exact 20%/40% CUD discounts
        spec = self.calc.WorkloadSpec(
            name="cud_check",
            label="CUD Discount Verification",
            nodes=100_000,
            edges=500_000,
            chunks=250_000,
            qps=50.0,
            write_pct=15.0,
            vector_dim=768,
            cold_hdd_pct=30.0,
            region="asia-southeast3",
            edition="enterprise",
            ro_replicas=1,
        )
        res = self.calc.evaluate_scenario(spec)
        cmo = res["spanner_costs"]["compute_monthly_usd"]
        self.assertAlmostEqual(cmo["cud_1yr"], round(cmo["on_demand"] * 0.80, 2), delta=0.02)
        self.assertAlmostEqual(cmo["cud_3yr"], round(cmo["on_demand"] * 0.60, 2), delta=0.02)
        self.assertAlmostEqual(
            cmo["ro_replicas_cud_1yr"], round(cmo["ro_replicas_on_demand"] * 0.80, 2), delta=0.02
        )
        self.assertAlmostEqual(
            cmo["ro_replicas_cud_3yr"], round(cmo["ro_replicas_on_demand"] * 0.60, 2), delta=0.02
        )

    def test_four_tshirt_presets_and_workshop_8hr_budget(self) -> None:
        """All 4 presets default to single-region Bangkok ($0.00/mo egress) and scale monotonically."""
        # Default CLI invocation (no --region or --dr-backup-region) must default to single-region Bangkok
        proc = subprocess.run(
            [
                sys.executable,
                "solution/tools/sizing_calculator.py",
                "--preset",
                "all",
                "--json",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["primary_region"], "asia-southeast3")
        self.assertIsNone(payload["dr_backup_region"])
        scenarios = payload["scenarios"]
        self.assertEqual([s["name"] for s in scenarios], ["workshop", "small", "medium", "large"])

        by_name = {s["name"]: s for s in scenarios}

        # Every default scenario runs single-region Bangkok with $0.00/mo cross-region egress
        for pname, sc in by_name.items():
            self.assertEqual(sc["inputs"]["region"], "asia-southeast3", f"{pname} region")
            self.assertIsNone(sc["inputs"]["dr_backup_region"], f"{pname} dr_backup_region")
            dr_info = sc["spanner_costs"]["dr_backup"]
            self.assertFalse(dr_info["enabled"], f"{pname} should disable cross-region DR by default")
            self.assertIsNone(dr_info["dr_backup_region"])
            self.assertEqual(dr_info["egress_rate_per_gib_usd"], 0.0)
            self.assertEqual(dr_info["initial_full_copy_egress_usd"], 0.0)
            self.assertEqual(dr_info["monthly_delta_copy_egress_usd"], 0.0)
            self.assertEqual(dr_info["total_dr_monthly_recurring_usd"], 0.0)

        # Workshop sandbox (100 PU dev/lab only)
        ws = by_name["workshop"]
        self.assertEqual(ws["compute"]["processing_units"], 100)
        self.assertFalse(ws["compute"]["is_production_eligible"])
        self.assertFalse(ws["compute"]["managed_autoscaler"]["eligible"])
        self.assertFalse(ws["spanner_costs"]["storage_monthly_usd"]["primary_backup_enabled"])
        self.assertAlmostEqual(
            ws["spanner_costs"]["workshop_session_estimate"]["compute_usd"],
            1.0332,
            places=4,
        )
        self.assertTrue(any("DEV/LAB" in w for w in ws["compute"]["warnings"]))

        # Production presets: small, medium, large
        for pname in ("small", "medium", "large"):
            sc = by_name[pname]
            self.assertTrue(sc["compute"]["is_production_eligible"])
            self.assertGreaterEqual(sc["compute"]["processing_units"], 1000)
            self.assertTrue(sc["compute"]["production_min_pu_enforced"])
            self.assertLess(sc["compute"]["projected_hp_cpu_pct"], 65.0)
            self.assertTrue(sc["compute"]["hp_cpu_within_regional_threshold"])
            auto = sc["compute"]["managed_autoscaler"]
            self.assertTrue(auto["eligible"])
            self.assertGreaterEqual(auto["min_nodes"], 1)
            self.assertLessEqual(auto["max_nodes"], auto["min_nodes"] * 10)
            self.assertGreater(
                sc["spanner_costs"]["storage_monthly_usd"]["locality_group_savings_usd"], 0.0
            )

        # Monotonic scaling across small -> medium -> large
        self.assertLessEqual(
            by_name["small"]["compute"]["processing_units"],
            by_name["medium"]["compute"]["processing_units"],
        )
        self.assertLess(
            by_name["medium"]["compute"]["processing_units"],
            by_name["large"]["compute"]["processing_units"],
        )
        self.assertLess(by_name["small"]["storage"]["total_gib"], by_name["medium"]["storage"]["total_gib"])
        self.assertLess(by_name["medium"]["storage"]["total_gib"], by_name["large"]["storage"]["total_gib"])

    def test_production_1000pu_floor_storage_scaling_and_validation_errors(self) -> None:
        """Production workloads enforce >= 1,000 PU floor, < 65% HP CPU, storage floor, and input validation."""
        # Tiny production workload MUST still enforce 1,000 PU floor
        tiny_prod = self.calc.WorkloadSpec(
            name="tiny_prod",
            label="Tiny Production Floor Test",
            nodes=1_000,
            edges=5_000,
            chunks=2_000,
            qps=2.0,
            write_pct=10.0,
            vector_dim=768,
            cold_hdd_pct=0.0,
            region="asia-southeast3",
            edition="enterprise",
        )
        tiny_res = self.calc.evaluate_scenario(tiny_prod)
        self.assertEqual(tiny_res["compute"]["processing_units"], 1000)
        self.assertEqual(tiny_res["compute"]["sizing_driver"], "production_1000pu_minimum")
        self.assertTrue(tiny_res["compute"]["is_production_eligible"])

        # Storage-heavy, low-QPS workload must scale nodes on 7.5 TiB/node threshold
        storage_heavy = self.calc.WorkloadSpec(
            name="storage_heavy",
            label="Storage Bound Test",
            nodes=10_000_000,
            edges=50_000_000,
            chunks=1_500_000_000,  # ~12+ TiB
            qps=10.0,
            write_pct=10.0,
            vector_dim=768,
            cold_hdd_pct=50.0,
            region="asia-southeast3",
            edition="enterprise",
        )
        st_res = self.calc.evaluate_scenario(storage_heavy)
        self.assertEqual(st_res["compute"]["sizing_driver"], "storage_75pct_threshold")
        self.assertGreater(st_res["compute"]["processing_units"], 1000)

        # Standard edition is not production-eligible for Graph/FTS/ScaNN and emits warning
        std_spec = self.calc.WorkloadSpec(
            name="std_warn",
            label="Standard Edition Check",
            nodes=10_000,
            edges=50_000,
            chunks=10_000,
            qps=20.0,
            write_pct=10.0,
            vector_dim=768,
            cold_hdd_pct=0.0,
            region="asia-southeast3",
            edition="standard",
        )
        std_res = self.calc.evaluate_scenario(std_spec)
        self.assertFalse(std_res["compute"]["is_production_eligible"])
        self.assertFalse(std_res["compute"]["managed_autoscaler"]["eligible"])
        self.assertTrue(any("Standard edition" in w for w in std_res["compute"]["warnings"]))

        # Invalid inputs raise ValueError
        with self.assertRaises(ValueError):
            self.calc.evaluate_scenario(
                self.calc.WorkloadSpec(
                    name="bad_reg", label="x", nodes=1, edges=1, chunks=1,
                    qps=1.0, write_pct=10.0, vector_dim=768, cold_hdd_pct=0.0,
                    region="invalid-region-99",
                )
            )
        with self.assertRaises(ValueError):
            self.calc.evaluate_scenario(
                self.calc.WorkloadSpec(
                    name="bad_pu", label="x", nodes=1, edges=1, chunks=1,
                    qps=1.0, write_pct=10.0, vector_dim=768, cold_hdd_pct=0.0,
                    processing_units_override=250,
                )
            )

    def test_dr_backup_copy_bangkok_to_singapore(self) -> None:
        """Default single-region Bangkok incurs $0.00 egress; optional --dr-backup-region computes DR math."""
        base_kwargs = dict(
            name="dr_test",
            label="Bangkok Single-Region vs Optional DR Override",
            nodes=1_000_000,
            edges=5_000_000,
            chunks=2_500_000,
            qps=300.0,
            write_pct=15.0,
            vector_dim=768,
            cold_hdd_pct=40.0,
            edition="enterprise",
        )
        # WorkloadSpec defaults: Bangkok primary (asia-southeast3), dr_backup_region=None
        default_spec = self.calc.WorkloadSpec(**base_kwargs)
        self.assertEqual(default_spec.region, "asia-southeast3")
        self.assertIsNone(default_spec.dr_backup_region)

        no_dr = self.calc.evaluate_scenario(default_spec)
        with_dr = self.calc.evaluate_scenario(
            self.calc.WorkloadSpec(**base_kwargs, region="asia-southeast3", dr_backup_region="asia-southeast1")
        )
        same_reg = self.calc.evaluate_scenario(
            self.calc.WorkloadSpec(**base_kwargs, region="asia-southeast3", dr_backup_region="asia-southeast3")
        )

        self.assertFalse(no_dr["spanner_costs"]["dr_backup"]["enabled"])
        self.assertIsNone(no_dr["spanner_costs"]["dr_backup"]["dr_backup_region"])
        self.assertEqual(no_dr["spanner_costs"]["dr_backup"]["egress_rate_per_gib_usd"], 0.0)
        self.assertEqual(no_dr["spanner_costs"]["dr_backup"]["monthly_delta_copy_egress_usd"], 0.0)
        self.assertEqual(no_dr["spanner_costs"]["dr_backup"]["total_dr_monthly_recurring_usd"], 0.0)

        dr = with_dr["spanner_costs"]["dr_backup"]
        total_gib = with_dr["storage"]["total_gib"]
        self.assertTrue(dr["enabled"])
        self.assertEqual(dr["dr_backup_region"], "asia-southeast1")
        self.assertAlmostEqual(dr["egress_rate_per_gib_usd"], 0.08, places=6)
        self.assertAlmostEqual(dr["dr_backup_rate_per_gib_month_usd"], 0.03432, places=6)
        self.assertAlmostEqual(dr["initial_full_copy_egress_usd"], round(total_gib * 0.08, 4), places=3)
        self.assertAlmostEqual(
            dr["monthly_delta_copy_egress_usd"], round(total_gib * 0.15 * 0.08, 4), places=3
        )
        self.assertAlmostEqual(
            dr["dr_backup_storage_monthly_usd"], round(total_gib * 0.03432, 4), places=3
        )
        self.assertAlmostEqual(
            with_dr["spanner_costs"]["totals_monthly_usd"]["on_demand"]
            - no_dr["spanner_costs"]["totals_monthly_usd"]["on_demand"],
            dr["total_dr_monthly_recurring_usd"],
            delta=0.02,
        )

        # Same-region backup copy incurs $0.00 egress
        self.assertEqual(same_reg["spanner_costs"]["dr_backup"]["egress_rate_per_gib_usd"], 0.0)

        # CLI explicit `--dr-backup-region none` keeps DR disabled ($0.00 egress), while explicit
        # `--dr-backup-region asia-southeast1` activates optional cross-region DR math
        cli_none = subprocess.run(
            [
                sys.executable,
                "solution/tools/sizing_calculator.py",
                "--preset",
                "medium",
                "--dr-backup-region",
                "none",
                "--json",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(cli_none.returncode, 0, cli_none.stderr)
        none_payload = json.loads(cli_none.stdout)
        self.assertIsNone(none_payload["dr_backup_region"])
        self.assertFalse(none_payload["scenarios"][0]["spanner_costs"]["dr_backup"]["enabled"])
        self.assertEqual(
            none_payload["scenarios"][0]["spanner_costs"]["dr_backup"]["total_dr_monthly_recurring_usd"],
            0.0,
        )

        cli_opt = subprocess.run(
            [
                sys.executable,
                "solution/tools/sizing_calculator.py",
                "--preset",
                "medium",
                "--dr-backup-region",
                "asia-southeast1",
                "--json",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(cli_opt.returncode, 0, cli_opt.stderr)
        opt_payload = json.loads(cli_opt.stdout)
        self.assertEqual(opt_payload["dr_backup_region"], "asia-southeast1")
        opt_dr = opt_payload["scenarios"][0]["spanner_costs"]["dr_backup"]
        self.assertTrue(opt_dr["enabled"])
        self.assertAlmostEqual(opt_dr["egress_rate_per_gib_usd"], 0.08, places=6)
        self.assertGreater(opt_dr["total_dr_monthly_recurring_usd"], 0.0)

    def test_customer_scale_worksheet_json_input(self) -> None:
        """starter/tools/customer_scale_worksheet.json defaults to single-region Bangkok (dr_backup_region='none')."""
        raw_ws = json.loads(WORKSHEET_PATH.read_text(encoding="utf-8"))
        self.assertEqual(raw_ws["region"], "asia-southeast3")
        self.assertEqual(raw_ws["dr_backup_region"], "none")

        ws_dict, meta = self.calc.load_worksheet(WORKSHEET_PATH)
        self.assertEqual(ws_dict["nodes"], 500_000)
        self.assertEqual(ws_dict["edges"], 2_500_000)
        self.assertEqual(ws_dict["chunks"], 1_200_000)
        self.assertEqual(ws_dict["qps"], 180.0)
        self.assertEqual(ws_dict["cold_hdd_pct"], 40.0)
        self.assertEqual(ws_dict["region"], "asia-southeast3")
        self.assertIsNone(ws_dict["dr_backup_region"])
        self.assertIn("todo_status", meta)

        proc = subprocess.run(
            [
                sys.executable,
                "solution/tools/sizing_calculator.py",
                "--worksheet",
                str(WORKSHEET_PATH),
                "--json",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["mode"], "worksheet")
        self.assertEqual(data["primary_region"], "asia-southeast3")
        self.assertIsNone(data["dr_backup_region"])
        self.assertEqual(len(data["scenarios"]), 1)
        sc = data["scenarios"][0]
        self.assertGreaterEqual(sc["compute"]["processing_units"], 1000)
        self.assertLess(sc["compute"]["projected_hp_cpu_pct"], 65.0)
        self.assertFalse(sc["spanner_costs"]["dr_backup"]["enabled"])
        self.assertIsNone(sc["spanner_costs"]["dr_backup"]["dr_backup_region"])
        self.assertEqual(sc["spanner_costs"]["dr_backup"]["monthly_delta_copy_egress_usd"], 0.0)
        self.assertEqual(sc["spanner_costs"]["dr_backup"]["total_dr_monthly_recurring_usd"], 0.0)
        self.assertGreater(
            sc["legacy_3_store_tco"]["savings_vs_infra_only"]["spanner_cud_3yr"]["monthly_usd"],
            0.0,
        )

    def test_3_store_tco_savings_across_all_three_regions_and_text_report(self) -> None:
        """Spanner Enterprise beats legacy 3-store HA TCO across Bangkok, Singapore, and Iowa."""
        for region in ("asia-southeast3", "asia-southeast1", "us-central1"):
            for pname in ("small", "medium", "large"):
                p = self.calc.PRESETS[pname]
                spec = self.calc.WorkloadSpec(
                    name=p["name"],
                    label=p["label"],
                    nodes=p["nodes"],
                    edges=p["edges"],
                    chunks=p["chunks"],
                    qps=p["qps"],
                    write_pct=p["write_pct"],
                    vector_dim=p["vector_dim"],
                    cold_hdd_pct=p["cold_hdd_pct"],
                    region=region,
                    edition="enterprise",
                    dr_backup_region=None,
                )
                res = self.calc.evaluate_scenario(spec)
                tco = res["legacy_3_store_tco"]
                infra = tco["savings_vs_infra_only"]
                loaded = tco["savings_vs_fully_loaded"]

                self.assertGreater(
                    infra["spanner_on_demand"]["monthly_usd"],
                    0.0,
                    f"{region}/{pname} expected positive OD infra savings",
                )
                self.assertGreater(
                    infra["spanner_cud_1yr"]["monthly_usd"],
                    infra["spanner_on_demand"]["monthly_usd"],
                )
                self.assertGreater(
                    infra["spanner_cud_3yr"]["monthly_usd"],
                    infra["spanner_cud_1yr"]["monthly_usd"],
                )
                self.assertGreater(
                    loaded["spanner_cud_3yr"]["monthly_usd"],
                    infra["spanner_cud_3yr"]["monthly_usd"],
                )
                self.assertEqual(tco["architectural_benefits"]["sync_lag_ms"], 0)

        # Verify regional cost ordering: Iowa < Bangkok < Singapore for identical medium workload
        med_totals = {}
        for region in ("us-central1", "asia-southeast3", "asia-southeast1"):
            p = self.calc.PRESETS["medium"]
            res = self.calc.evaluate_scenario(
                self.calc.WorkloadSpec(
                    name=p["name"], label=p["label"], nodes=p["nodes"], edges=p["edges"],
                    chunks=p["chunks"], qps=p["qps"], write_pct=p["write_pct"],
                    vector_dim=p["vector_dim"], cold_hdd_pct=p["cold_hdd_pct"],
                    region=region, edition="enterprise",
                )
            )
            med_totals[region] = res["spanner_costs"]["totals_monthly_usd"]["on_demand"]
        self.assertLess(med_totals["us-central1"], med_totals["asia-southeast3"])
        self.assertLess(med_totals["asia-southeast3"], med_totals["asia-southeast1"])

        # Verify default CLI human-readable report uses Bangkok single-region ($0.00/mo egress)
        proc_default = subprocess.run(
            [
                sys.executable,
                "solution/tools/sizing_calculator.py",
                "--preset",
                "all",
                "--region",
                "asia-southeast3",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc_default.returncode, 0, proc_default.stderr)
        self.assertIn("BANGKOK SINGLE-REGION", proc_default.stdout)
        self.assertIn("Bangkok (asia-southeast3)", proc_default.stdout)
        self.assertIn("$0.00/mo cross-region egress", proc_default.stdout)
        self.assertIn("ONLY for Vertex AI", proc_default.stdout)
        self.assertIn("$1.2915", proc_default.stdout)
        self.assertNotIn("Optional DR Copy", proc_default.stdout)

        # Verify explicit optional --dr-backup-region CLI report still renders cleanly
        proc_dr = subprocess.run(
            [
                sys.executable,
                "solution/tools/sizing_calculator.py",
                "--preset",
                "medium",
                "--region",
                "asia-southeast3",
                "--dr-backup-region",
                "asia-southeast1",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc_dr.returncode, 0, proc_dr.stderr)
        self.assertIn("Optional DR Copy : Singapore (asia-southeast1", proc_dr.stdout)
        self.assertIn("$0.08/GiB", proc_dr.stdout)


if __name__ == "__main__":
    unittest.main()
