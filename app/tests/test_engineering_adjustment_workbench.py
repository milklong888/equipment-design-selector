from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = APP_DIR.parent
SCRIPTS_DIR = PACKAGE_ROOT / "scripts"
for path in (APP_DIR, SCRIPTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import app_core
import customer_delivery
import derivation_workbench
import equipment_design_agent
import result_presentation
from equipment_design_app import EquipmentDesignApi


class EngineeringAdjustmentWorkbenchTests(unittest.TestCase):
    def test_large_exchanger_has_concrete_parallel_plan(self) -> None:
        result = app_core.manual_match(
            "block:HEATX",
            {
                "equipment_tag": "E-LARGE",
                "heat_duty_kw": 1000,
                "heat_transfer_area_m2": 1350,
            },
        )["result"]
        plan = result["engineering_adjustment_plan"]
        configuration = plan["configuration"]
        self.assertEqual(
            plan["status"],
            "RECOMMENDED_ALGORITHMIC_MODIFICATION",
        )
        self.assertEqual(
            configuration["parallel_train_count_estimate"],
            3,
        )
        self.assertEqual(
            configuration["operating_unit_count_estimate"],
            3,
        )
        self.assertEqual(
            configuration["per_unit_target"][
                "heat_transfer_area_m2"
            ],
            450.0,
        )
        self.assertIn(
            "3×33.3%并联",
            configuration["candidate_model_or_designation"],
        )
        self.assertNotIn(
            "非标准型",
            configuration["candidate_model_or_designation"],
        )
        self.assertTrue(plan["algorithmic_selection_warning"])

    def test_missing_exchanger_conditions_keep_a_complete_program_unit(
        self,
    ) -> None:
        result = app_core.manual_match(
            "block:HEATX",
            {
                "equipment_tag": "E-MISSING",
                "phase": "liquid",
            },
        )["result"]
        plan = result["engineering_adjustment_plan"]
        designation = plan["configuration"][
            "candidate_model_or_designation"
        ]
        self.assertEqual(
            plan["input_completeness"]["status"],
            "COMPLETE_PROGRAM_CANDIDATE_WITH_ANNOTATED_FALLBACKS",
        )
        self.assertTrue(
            {
                "heat_duty_kw",
                "overall_u_w_m2k",
                "lmtd_k",
                "shell_material_grade",
                "tube_material_grade",
            }.issubset(
                set(plan["input_completeness"]["fallback_fields"])
            )
        )
        self.assertIn(
            "STHE-FT-1S2T-A19.6-D25-L3000-N84-Q345R-10",
            designation,
        )
        self.assertNotIn("厂家型号待定", designation)
        self.assertNotIn("非标准型", designation)

    def test_high_flow_medium_head_uses_complete_mixed_flow_route(
        self,
    ) -> None:
        result = app_core.manual_match(
            "block:PUMP",
            {
                "equipment_tag": "P-HIGH-FLOW",
                "phase": "liquid",
                "flow_m3_h": 4000,
                "density_kg_m3": 1000,
                "head_m": 60,
            },
        )["result"]
        configuration = result[
            "engineering_adjustment_plan"
        ]["configuration"]
        self.assertEqual(
            configuration["candidate_equipment_type"],
            "立式混流泵",
        )
        self.assertEqual(
            configuration["parallel_train_count_estimate"],
            2,
        )
        self.assertEqual(
            configuration["installed_unit_count_estimate"],
            3,
        )
        self.assertIsNone(
            configuration["candidate_standard_marking"]
        )
        designation = configuration[
            "candidate_model_or_designation"
        ]
        self.assertIn("立式导叶式混流泵", designation)
        self.assertIn(
            "PMF-VERTICAL-DIFFUSER-1ST-Q2000.000-H60.000-P2S1",
            designation,
        )
        self.assertIn("泵壳", designation)
        self.assertIn("叶轮", designation)
        self.assertIn("机械密封", designation)
        self.assertIn("法兰承压路线=PN16", designation)
        self.assertNotIn("GB/T 5662", designation)
        self.assertNotIn("厂家型号待定", designation)
        self.assertNotIn("非标准型", designation)
        curve_action = next(
            row["action"]
            for row in result["engineering_adjustment_plan"][
                "required_actions"
            ]
            if row["action_code"] == "VENDOR_CURVE_AND_BEP_REVIEW"
        )
        self.assertIn("没有跨泵型借用GB/T 5662", curve_action)
        options = result["engineering_adjustment_plan"][
            "equivalent_recommendations"
        ]
        self.assertEqual(len(options), 2)
        self.assertFalse(
            options[0]["equivalence_basis"][
                "system_curve_and_vendor_curve_equivalence_proven"
            ]
        )

    def test_normal_pump_stays_single_instead_of_artificial_series_split(
        self,
    ) -> None:
        result = app_core.manual_match(
            "block:PUMP",
            {
                "equipment_tag": "P-NORMAL",
                "phase": "liquid",
                "main_medium": "water",
                "flow_m3_h": 120,
                "head_m": 60,
                "density_kg_m3": 1000,
                "inlet_pressure_mpa": 0.2,
                "pressure_basis": "gauge",
            },
        )["result"]
        plan = result["engineering_adjustment_plan"]
        configuration = plan["configuration"]
        self.assertEqual(
            configuration["arrangement_code"],
            "SINGLE_PUMP_REFERENCE_POINT",
        )
        self.assertEqual(
            configuration["parallel_train_count_estimate"],
            1,
        )
        self.assertEqual(
            configuration["series_units_per_train_estimate"],
            1,
        )
        self.assertEqual(
            configuration["installed_unit_count_estimate"],
            2,
        )
        self.assertIn(
            "PES-END-SUCTION-1ST-Q120.000-H60.000-P1S1",
            configuration["candidate_model_or_designation"],
        )

    def test_missing_pump_conditions_still_yield_complete_warned_candidate(
        self,
    ) -> None:
        result = app_core.manual_match(
            "block:PUMP",
            {
                "equipment_tag": "P-MISSING",
                "phase": "liquid",
            },
        )["result"]
        plan = result["engineering_adjustment_plan"]
        selection = result["pump_engineering_selection"]
        designation = plan["configuration"][
            "candidate_model_or_designation"
        ]
        self.assertEqual(
            plan["input_completeness"]["status"],
            "COMPLETE_PROGRAM_CANDIDATE_WITH_ANNOTATED_FALLBACKS",
        )
        self.assertTrue(
            {"flow_m3_h", "head_m", "density_kg_m3"}.issubset(
                set(plan["input_completeness"]["fallback_fields"])
            )
        )
        self.assertIn("Q10.000-H30.000", designation)
        self.assertIn("法兰承压路线=PN16", designation)
        for component in (
            "pump_casing",
            "impeller",
            "shaft",
            "mechanical_seal",
            "gasket",
        ):
            self.assertIn(
                component,
                selection["material_and_seal"][
                    "selected_components"
                ],
            )
        self.assertEqual(
            selection["pressure_and_flange"]["status"],
            "CALCULATED_AND_PRESSURE_CLASS_SELECTED",
        )
        pressure_warning_codes = {
            row["code"]
            for row in selection["pressure_and_flange"]["warnings"]
        }
        self.assertIn(
            "PUMP_SUCTION_PRESSURE_FALLBACK",
            pressure_warning_codes,
        )
        self.assertIn(
            "design_temperature_c",
            plan["input_completeness"]["fallback_fields"],
        )
        self.assertNotIn("厂家型号待定", designation)

    def test_very_high_head_pump_has_bb5_program_route_and_pressure_class(
        self,
    ) -> None:
        result = app_core.manual_match(
            "block:PUMP",
            {
                "equipment_tag": "P-HIGH-HEAD",
                "phase": "liquid",
                "flow_m3_h": 120,
                "head_m": 800,
                "density_kg_m3": 850,
                "inlet_pressure_mpa": 0.2,
                "pressure_basis": "gauge",
            },
        )["result"]
        configuration = result["engineering_adjustment_plan"][
            "configuration"
        ]
        designation = configuration["candidate_model_or_designation"]
        self.assertEqual(
            configuration["candidate_equipment_type"],
            "多级离心泵",
        )
        self.assertEqual(
            configuration["hydraulic_stage_count_estimate"],
            10,
        )
        self.assertIn("BB5类工程型式", designation)
        self.assertIn("PMS-BB5-DOUBLE-CASING-10ST", designation)
        self.assertIn("法兰承压路线=PN100", designation)
        self.assertNotIn("厂家型号待定", designation)

    def test_very_large_exchanger_has_conserved_comparison_options(
        self,
    ) -> None:
        result = app_core.manual_match(
            "block:HEATX",
            {
                "equipment_tag": "E-VERY-LARGE",
                "phase": "liquid",
                "heat_duty_kw": 50000,
                "overall_u_w_m2k": 450,
                "lmtd_k": 20,
            },
        )["result"]
        plan = result["engineering_adjustment_plan"]
        configuration = plan["configuration"]
        options = plan["equivalent_recommendations"]
        self.assertEqual(
            configuration["parallel_train_count_estimate"],
            4,
        )
        self.assertEqual(
            configuration["series_units_per_train_estimate"],
            4,
        )
        self.assertEqual(
            configuration["operating_unit_count_estimate"],
            16,
        )
        self.assertEqual(len(options), 3)
        self.assertEqual(
            [
                (
                    row["parallel_train_count"],
                    row["series_units_per_train"],
                )
                for row in options
            ],
            [(4, 4), (14, 1), (1, 14)],
        )
        total_area = result["exchanger_default_parameter_package"][
            "parameters"
        ]["heat_transfer_area_m2"]["value"]
        for option in options:
            reconstructed_area = (
                option["operating_unit_count"]
                * option["per_unit_target"][
                    "heat_transfer_area_m2"
                ]
            )
            self.assertAlmostEqual(
                reconstructed_area,
                total_area,
                places=3,
            )
            self.assertTrue(
                option["equivalence_basis"]["total_area_conserved"]
            )
            self.assertFalse(
                option["equivalence_basis"][
                    "thermal_hydraulic_equivalence_proven"
                ]
            )
            self.assertNotIn(
                "厂家型号待定",
                option["system_candidate_designation"],
            )

    def test_large_tower_plan_keeps_formal_geometry_open(self) -> None:
        result = app_core.manual_match(
            "block:RADFRAC",
            {
                "equipment_tag": "T-LARGE",
                "stage_count": 30,
                "inner_diameter_mm": 6000,
                "height_mm": 70000,
            },
        )["result"]
        plan = result["engineering_adjustment_plan"]
        designation = plan["configuration"][
            "candidate_model_or_designation"
        ]
        self.assertEqual(
            plan["configuration"][
                "parallel_train_count_estimate"
            ],
            3,
        )
        self.assertIn("3列并联", designation)
        self.assertIn("正式塔径/塔高", designation)
        self.assertIn("OPEN", designation)
        self.assertNotIn("6000", designation)
        self.assertNotIn("70000", designation)
        overview = result["customer_delivery"][
            "equipment_overview_table"
        ]["rows"][0]
        self.assertEqual(
            overview["model_or_specification"],
            designation,
        )
        self.assertEqual(
            overview["model_or_specification_status"],
            "algorithmic_modification_screening_only",
        )

    def test_constraint_fail_overview_keeps_concrete_program_candidate(
        self,
    ) -> None:
        cases = (
            (
                "block:COMPR",
                {
                    "equipment_tag": "C-SURGE-BLOCKED",
                    "phase": "vapor",
                    "flow_m3_h": 2000,
                    "suction_pressure_mpa": 0.1,
                    "discharge_pressure_mpa": 0.5,
                    "surge_margin_percent": 5,
                    "required_surge_margin_percent": 10,
                },
                "COMP-CENT-1STG-Q2000-PR3.00-P92.6-M110",
            ),
            (
                "family:family_storage_vessel",
                {
                    "equipment_tag": "V-STORAGE-BLOCKED",
                    "equipment_type": "立式储罐",
                    "flow_m3_h": 10,
                    "retention_time_min": 60,
                    "fill_fraction": 0.8,
                    "volume_m3": 5,
                    "volume_basis": "nominal_total",
                },
                "Vreq=12.5 m3 | V=5 m3",
            ),
        )
        for selection_id, values, expected in cases:
            with self.subTest(selection_id=selection_id):
                result = app_core.manual_match(
                    selection_id,
                    values,
                )["result"]
                overview = result["customer_delivery"][
                    "equipment_overview_table"
                ]["rows"][0]
                designation = overview["model_or_specification"]
                self.assertIn(expected, designation)
                self.assertNotIn("厂家型号待定", designation)
                self.assertEqual(
                    overview["model_or_specification_status"],
                    "algorithmic_configuration_review_required",
                )
                self.assertEqual(
                    overview["engineering_adjustment_status"],
                    "REVIEW_REQUIRED_NO_SAFE_AUTOMATIC_CONFIGURATION",
                )
                self.assertTrue(
                    overview["algorithmic_selection_warning"]
                )

    def test_agent_control_distinguishes_existing_target(self) -> None:
        result = app_core.manual_match(
            "block:PUMP",
            {
                "equipment_tag": "P-HEAD",
                "phase": "liquid",
                "flow_m3_h": 40,
                "density_kg_m3": 1000,
                "head_m": 35,
            },
        )["result"]
        calculate = result["selection_agent_control"][
            "calculate_before_select"
        ]
        self.assertIn(
            "pump_head_from_pressure",
            calculate["satisfied_by_existing_target_ids"],
        )
        self.assertFalse(
            calculate["all_registered_calculations_attempted"]
        )
        self.assertTrue(
            calculate["calculation_execution_satisfied"]
        )
        self.assertEqual(
            calculate["unsatisfied_calculation_ids"],
            [],
        )
        components = result["selection_agent_control"][
            "ambiguous_choice_resolution"
        ]["connection_components"]
        self.assertEqual(
            components["status"],
            "COMPLETED_REGISTERED_DETERMINISTIC_SELECTION",
        )
        self.assertTrue(
            components["selection_package_sha256"]
        )

    def test_customer_delivery_rejects_tampered_plan(self) -> None:
        result = app_core.manual_match(
            "block:HEATX",
            {
                "equipment_tag": "E-TAMPER",
                "heat_duty_kw": 1000,
                "heat_transfer_area_m2": 1350,
            },
        )["result"]
        forged = copy.deepcopy(result)
        forged["engineering_adjustment_plan"][
            "configuration"
        ]["parallel_train_count_estimate"] = 99
        with self.assertRaises(customer_delivery.CustomerDeliveryError):
            customer_delivery.build_customer_delivery(forged)

    def test_workbench_has_clickable_chain_and_translated_options(
        self,
    ) -> None:
        result = app_core.manual_match(
            "block:PUMP",
            {
                "equipment_tag": "P-WB",
                "phase": "liquid",
                "flow_m3_h": 4000,
                "density_kg_m3": 1000,
                "head_m": 60,
            },
        )["result"]
        workbench = derivation_workbench.build_workbench(
            result,
            app_core.load_catalog(),
            model_rules=app_core.matcher.load_model_rules(),
            selection_id="block:PUMP",
            overrides={"equipment_type": "轴流泵"},
        )
        self.assertEqual(
            [item["node_id"] for item in workbench["nodes"]],
            [
                "source",
                "template",
                "calculation",
                "terminal",
                "adjustment",
                "delivery",
            ],
        )
        template_field = workbench["nodes"][1][
            "editable_fields"
        ][0]
        self.assertEqual(template_field["edit_kind"], "select")
        self.assertTrue(
            all(
                option["label"]
                and option["internal_code"]
                for option in template_field["options"]
            )
        )
        terminal_fields = workbench["nodes"][3][
            "editable_fields"
        ]
        equipment_type = next(
            item
            for item in terminal_fields
            if item["field_id"] == "equipment_type"
        )
        labels = {
            option["label"]
            for option in equipment_type["options"]
        }
        self.assertIn("轴流泵", labels)
        self.assertIn("多级离心泵", labels)
        self.assertTrue(workbench["controls"][
            "single_equipment_recalculate"
        ])
        self.assertTrue(workbench["controls"][
            "restore_program_defaults"
        ])
        self.assertFalse(workbench["controls"][
            "formal_evidence_gate_overridable"
        ])

    def test_override_audit_preserves_default_and_new_hash(
        self,
    ) -> None:
        baseline = app_core.manual_match(
            "block:PUMP",
            {
                "equipment_tag": "P-AUDIT",
                "phase": "liquid",
                "flow_m3_h": 100,
                "density_kg_m3": 1000,
                "head_m": 40,
            },
        )["result"]
        recalculated = app_core.manual_match(
            "block:PUMP",
            {
                "equipment_tag": "P-AUDIT",
                "phase": "liquid",
                "flow_m3_h": 120,
                "density_kg_m3": 1000,
                "head_m": 40,
            },
        )["result"]
        audit = derivation_workbench.build_override_audit(
            baseline,
            {"flow_m3_h": 120},
            recalculated,
        )
        self.assertEqual(
            audit["status"],
            "USER_SCENARIO_RECALCULATED",
        )
        self.assertNotEqual(
            audit["baseline_result_sha256"],
            audit["recalculated_result_sha256"],
        )
        self.assertEqual(
            audit["changes"][0]["program_default_value"],
            100,
        )
        self.assertEqual(
            audit["changes"][0]["user_override_value"],
            120,
        )
        self.assertFalse(
            audit[
                "formal_model_promotion_allowed_by_override_alone"
            ]
        )

    def test_organized_answer_has_fixed_sections_and_bindings(
        self,
    ) -> None:
        result = app_core.manual_match(
            "block:PUMP",
            {
                "equipment_tag": "P-REPORT",
                "phase": "liquid",
                "flow_m3_h": 4000,
                "density_kg_m3": 1000,
                "head_m": 60,
            },
        )
        answer = result_presentation.build_organized_answer(
            result
        )
        self.assertEqual(
            answer["section_order"],
            [
                "基本信息",
                "分支选择与大模型调控",
                "详细计算链条",
                "候选与系统修改方案",
                "强制警告",
                "待补证据",
                "下一步",
            ],
        )
        self.assertTrue(
            answer["authority"][
                "deterministic_facts_immutable"
            ]
        )
        self.assertFalse(
            answer["authority"][
                "llm_may_change_counts_models_or_open_gates"
            ]
        )
        equipment = answer["equipment"][0]
        self.assertTrue(
            equipment["mandatory_warnings"]
        )
        self.assertTrue(
            equipment["fact_binding"][
                "engineering_adjustment_plan_sha256"
            ]
        )
        markdown = (
            result_presentation.render_organized_markdown(answer)
        )
        for heading in (
            "### 基本信息",
            "### 分支选择与大模型调控",
            "### 详细计算链条",
            "### 候选与系统修改方案",
            "### 强制警告",
            "### 待补证据",
            "### 下一步",
        ):
            self.assertIn(heading, markdown)

    def test_agent_exports_markdown_and_organizes_answer(
        self,
    ) -> None:
        source = {
            "input": {
                "operation": "manual_match",
                "payload": {
                    "selection_id": "block:PUMP",
                    "values": {
                        "equipment_tag": "P-AGENT-REPORT",
                        "phase": "liquid",
                        "flow_m3_h": 4000,
                        "density_kg_m3": 1000,
                        "head_m": 60,
                    },
                },
            },
            "format": "markdown",
        }
        with tempfile.TemporaryDirectory() as temporary:
            output_path = Path(temporary) / "report.md"
            report, artifacts = equipment_design_agent._execute(
                "render_report",
                {
                    **source,
                    "output_path": str(output_path),
                },
                EquipmentDesignApi(),
            )
            self.assertTrue(output_path.is_file())
            self.assertIn(str(output_path), artifacts)
            self.assertEqual(
                report["report_manifest"]["output_file_sha256"],
                equipment_design_agent.sha256_file(output_path),
            )
            self.assertEqual(
                report["organized_answer"]["schema"],
                "equipment-agent-organized-answer-v1",
            )
            organized, _ = equipment_design_agent._execute(
                "organize_answer",
                source,
                EquipmentDesignApi(),
            )
            self.assertTrue(
                organized["markdown"].startswith(
                    "# 设备设计选型报告"
                )
            )
            self.assertEqual(
                organized["organized_answer"][
                    "organized_answer_sha256"
                ],
                report["organized_answer"][
                    "organized_answer_sha256"
                ],
            )

    def test_agent_request_schema_registers_new_operations(
        self,
    ) -> None:
        schema = json.loads(
            equipment_design_agent.REQUEST_SCHEMA.read_text(
                encoding="utf-8"
            )
        )
        operations = schema["properties"]["operation"]["enum"]
        self.assertIn("organize_answer", operations)
        self.assertIn("answer.organize", operations)
        report_rule = next(
            item
            for item in schema["allOf"]
            if "render_report"
            in item["if"]["properties"]["operation"]["enum"]
        )
        formats = report_rule["then"]["properties"]["payload"][
            "properties"
        ]["format"]["enum"]
        self.assertIn("markdown", formats)
        self.assertIn("md", formats)


if __name__ == "__main__":
    unittest.main()
