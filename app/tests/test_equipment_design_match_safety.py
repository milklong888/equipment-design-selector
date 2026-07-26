from __future__ import annotations

import sys
import math
import unittest
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PACKAGE_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import equipment_design_match as matcher


class EquipmentDesignMatchConstraintSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = matcher.load_rules()
        cls.graph = matcher.load_graph()

    def _match_constraint_failure(
        self,
        raw: dict[str, Any],
        *,
        constraint_gate: str,
        rejected_reason: str,
    ) -> dict[str, Any]:
        result = matcher.match_one(raw, self.rules, self.graph)
        recommendation = result["model_recommendation"]

        self.assertEqual(
            recommendation["status"],
            "ENGINEERING_CONSTRAINT_FAILED",
        )
        self.assertEqual(recommendation["formal_ready_candidate_count"], 0)

        leading = recommendation["leading_candidate"]
        self.assertEqual(leading["candidate_kind"], "engineered_designation")
        self.assertEqual(
            leading["status"],
            "ENGINEERING_FAMILY_RETAINED_CONSTRAINT_FAIL",
        )
        self.assertTrue(leading["eligible_for_leading_candidate"])
        self.assertFalse(leading["eligible_for_formal_selection"])
        self.assertFalse(leading["formal_model"])
        self.assertIn(constraint_gate, leading["missing_gates"])

        non_engineering_candidates = [
            candidate
            for candidate in recommendation["candidates"]
            if candidate["candidate_kind"] != "engineered_designation"
        ]
        for candidate in non_engineering_candidates:
            self.assertEqual(candidate["status"], "REJECTED_CONSTRAINT_FAIL")
            self.assertEqual(candidate["candidate_eligibility"], "REJECTED")
            self.assertFalse(candidate["eligible_for_leading_candidate"])
            self.assertFalse(candidate["eligible_for_formal_selection"])
            self.assertIn(
                rejected_reason,
                candidate["candidate_rejection_reasons"],
            )

        return recommendation

    def test_pump_npsh_failure_retains_family_and_rejects_standard_candidates(
        self,
    ) -> None:
        recommendation = self._match_constraint_failure(
            {
                "aspen_block_type": "PUMP",
                "phase": "liquid",
                "flow_m3_h": 20,
                "head_m": 30,
                "npsha_m": 2,
                "npshr_m": 3,
                "required_npsh_margin_m": 0.5,
            },
            constraint_gate="engineering_constraint_fail:pump_npsh_margin",
            rejected_reason="PUMP_NPSH_CONSTRAINT_FAILED",
        )

        standard_candidates = [
            candidate
            for candidate in recommendation["candidates"]
            if candidate["candidate_kind"] == "standard_marking"
        ]
        self.assertTrue(standard_candidates)
        self.assertTrue(
            all(
                candidate["candidate_rejection_reasons"]
                for candidate in standard_candidates
            )
        )

    def test_pump_program_selects_material_seal_pressure_and_flange(self) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "P-PRESSURE-MATERIAL",
                "aspen_block_type": "PUMP",
                "phase": "liquid",
                "main_medium": "water",
                "flow_m3_h": 120.0,
                "head_m": 60.0,
                "density_kg_m3": 1000.0,
                "inlet_pressure_mpa": 0.2,
                "outlet_pressure_mpa": 0.79,
                "pressure_basis": "gauge",
                "design_temperature_c": 50.0,
            },
            self.rules,
            self.graph,
        )

        selection = result["pump_engineering_selection"]
        material = selection["material_and_seal"]
        pressure = selection["pressure_and_flange"]

        self.assertEqual(material["route_id"], "CLEAN_WATER_STANDARD")
        self.assertIn("HT250", material["selected_components"]["pump_casing"])
        self.assertIn("ZCuSn10P1", material["selected_components"]["impeller"])
        self.assertIn("20Cr13", material["selected_components"]["shaft"])
        self.assertIn("机械密封", material["selected_components"]["mechanical_seal"])
        self.assertTrue(material["program_generated"])

        self.assertGreaterEqual(
            pressure["maximum_final_discharge_pressure_mpa_gauge"],
            0.79,
        )
        self.assertRegex(
            pressure["selected_flange_pressure_class"],
            r"^PN\d+$",
        )
        self.assertEqual(len(pressure["calculation_chain"]), 3)
        self.assertTrue(
            all(
                row["status"] == "CALCULATED"
                for row in pressure["calculation_chain"]
            )
        )
        self.assertEqual(
            result["engineering_adjustment_plan"]["configuration"][
                "program_selected_flange_pressure_class"
            ],
            pressure["selected_flange_pressure_class"],
        )

        standard_candidate = next(
            candidate
            for candidate in result["model_recommendation"]["candidates"]
            if candidate.get("candidate_kind") == "standard_marking"
        )
        material_predicate = next(
            predicate
            for predicate in standard_candidate["predicate_trace"]
            if predicate["predicate_id"] == "family_pump:materials_and_seal"
        )
        self.assertEqual(material_predicate["status"], "PASS")
        self.assertEqual(
            material_predicate["formal_compatibility_status"],
            "UNKNOWN",
        )

    def test_pump_series_shutoff_pressure_can_reject_16bar_route(self) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "P-HIGH-PRESSURE",
                "aspen_block_type": "PUMP",
                "phase": "liquid",
                "main_medium": "hydrocarbon",
                "flow_m3_h": 120.0,
                "head_m": 60.0,
                "density_kg_m3": 850.0,
                "inlet_pressure_mpa": 1.2,
                "outlet_pressure_mpa": 1.7,
                "pressure_basis": "gauge",
                "design_temperature_c": 80.0,
            },
            self.rules,
            self.graph,
        )
        pressure = result["pump_engineering_selection"][
            "pressure_and_flange"
        ]
        self.assertEqual(
            pressure["gbt5662_16bar_scope_check"]["status"],
            "FAIL",
        )
        self.assertNotEqual(
            pressure["selected_flange_pressure_class"],
            "PN16",
        )
        self.assertTrue(
            any(
                item["code"] == "GBT5662_16BAR_SCOPE_EXCEEDED_AT_SHUTOFF"
                for item in pressure["warnings"]
            )
        )

    def test_compressor_surge_failure_retains_family_without_formal_selection(
        self,
    ) -> None:
        recommendation = self._match_constraint_failure(
            {
                "aspen_block_type": "COMPR",
                "phase": "vapor",
                "flow_m3_h": 2000,
                "suction_pressure_mpa": 0.1,
                "discharge_pressure_mpa": 0.5,
                "surge_margin_percent": 5,
                "required_surge_margin_percent": 10,
            },
            constraint_gate=(
                "engineering_constraint_fail:compressor_surge_margin"
            ),
            rejected_reason="COMPRESSOR_SURGE_MARGIN_CONSTRAINT_FAILED",
        )

        self.assertEqual(
            recommendation["leading_candidate"]["candidate_eligibility"],
            "CONSTRAINT_FAIL_FAMILY_ONLY",
        )

    def test_storage_volume_failure_retains_family_without_formal_selection(
        self,
    ) -> None:
        recommendation = self._match_constraint_failure(
            {
                "equipment_family": "family_storage_vessel",
                "equipment_type": "立式储罐",
                "flow_m3_h": 10,
                "retention_time_min": 60,
                "fill_fraction": 0.8,
                "volume_m3": 5,
                "volume_basis": "nominal_total",
            },
            constraint_gate=(
                "engineering_constraint_fail:storage_required_volume"
            ),
            rejected_reason="STORAGE_REQUIRED_VOLUME_CONSTRAINT_FAILED",
        )

        self.assertEqual(
            recommendation["leading_candidate"]["candidate_eligibility"],
            "CONSTRAINT_FAIL_FAMILY_ONLY",
        )

    def test_only_liquid_valve_uses_the_incompressible_liquid_cv_formula(
        self,
    ) -> None:
        rule = next(
            item
            for item in self.rules["families"]
            if item["id"] == "family_valve"
        )

        for phase in ("vapor", "mixed", "solid", "unknown", None):
            with self.subTest(phase=phase):
                calculations, pending, derived = matcher.run_calculations(
                    rule,
                    {
                        "aspen_block_type": "VALVE",
                        "phase": phase,
                        "flow_m3_h": 100.0,
                        "density_kg_m3": 6.0,
                        "inlet_pressure_mpa": 1.2,
                        "outlet_pressure_mpa": 0.12,
                        "pressure_drop_kpa": 1080.0,
                    },
                )

                self.assertNotIn("cv", derived)
                self.assertFalse(
                    any(
                        item.get("target_field") == "cv"
                        for item in calculations
                    )
                )
                blocker = next(
                    item
                    for item in pending
                    if item.get("calculation_id")
                    == "valve_liquid_equivalent_cv_screening"
                )
                self.assertEqual(
                    blocker["status"],
                    "BLOCKED_PHASE_INCOMPATIBLE_FORMULA",
                )
                self.assertEqual(
                    blocker["prohibited_output_field"],
                    "cv",
                )

        liquid_calculations, liquid_pending, liquid_derived = (
            matcher.run_calculations(
                rule,
                {
                    "aspen_block_type": "VALVE",
                    "phase": "liquid",
                    "flow_m3_h": 100.0,
                    "density_kg_m3": 1_000.0,
                    "inlet_pressure_mpa": 1.2,
                    "outlet_pressure_mpa": 0.12,
                    "pressure_drop_kpa": 1080.0,
                },
            )
        )
        self.assertIn("cv", liquid_derived)
        self.assertTrue(
            any(
                item.get("target_field") == "cv"
                for item in liquid_calculations
            )
        )
        self.assertFalse(
            any(
                item.get("status") == "BLOCKED_PHASE_INCOMPATIBLE_FORMULA"
                for item in liquid_pending
            )
        )

    def test_rplug_never_relabels_one_tube_diameter_as_whole_vessel(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "aspen_block_type": "RPLUG",
                "diameter_mm": 76.3,
                "operating_pressure_mpa": 2.45,
                "pressure_basis": "absolute",
                "atmospheric_pressure_mpa": 0.101325,
            },
            self.rules,
            self.graph,
        )
        values = result["design_parameter_package"]["selection_context"]["values"]
        for forbidden_field in (
            "diameter_mm",
            "inner_diameter_mm",
            "height_mm",
            "straight_shell_length_mm",
            "volume_m3",
            "volume_basis",
        ):
            self.assertNotIn(forbidden_field, values)
        calculation_ids = {
            row["calculation_id"]
            for row in result["calculations"]
        }
        self.assertNotIn("cylinder_volume", calculation_ids)
        self.assertNotIn("cylinder_thickness", calculation_ids)
        self.assertNotIn("head_thickness", calculation_ids)

    def test_exchanger_default_package_is_visible_and_user_values_win(
        self,
    ) -> None:
        defaulted = matcher.match_one(
            {
                "equipment_tag": "E-DEFAULT-PACKAGE",
                "aspen_block_type": "HEATX",
                "phase": "liquid",
                "heat_duty_kw": 1000.0,
                "overall_u_w_m2k": 650.0,
                "lmtd_k": 40.0,
            },
            self.rules,
            self.graph,
        )
        package = defaulted["exchanger_default_parameter_package"]
        parameters = package["parameters"]

        self.assertEqual(package["policy_id"], "HEX-DEFAULT-PARAMETERS-2026-01")
        self.assertEqual(
            package["status"],
            "PRELIMINARY_WITH_REGISTERED_DEFAULTS",
        )
        self.assertEqual(
            parameters["overall_u_w_m2k"]["origin"],
            "USER_PROJECT_OR_ASPEN_INPUT",
        )
        self.assertEqual(
            parameters["heat_transfer_area_m2"]["origin"],
            "DETERMINISTIC_CALCULATION",
        )
        self.assertEqual(
            parameters["hot_side_allowable_pressure_drop_kpa"]["value"],
            50.0,
        )
        self.assertEqual(
            parameters["hot_side_fouling_resistance_m2k_w"]["value"],
            0.0002,
        )
        self.assertEqual(parameters["tube_pass_count"]["value"], 2)
        self.assertEqual(
            parameters["tube_layout"]["value"],
            "30°三角形布管（程序保底）",
        )
        self.assertEqual(
            parameters["shell_material_grade"]["value"],
            "Q345R",
        )
        self.assertEqual(
            parameters["tube_material_grade"]["value"],
            "10",
        )
        self.assertIn(
            "material=Q345R壳体+10换热管",
            defaulted["model_recommendation"]["leading_candidate"][
                "designation"
            ],
        )
        self.assertIn(
            "hot_side_allowable_pressure_drop_kpa",
            package["default_fields_used"],
        )
        self.assertEqual(
            defaulted["effective_normalized_input"][
                "exchanger_default_parameter_package"
            ]["package_sha256"],
            package["package_sha256"],
        )

        overridden = matcher.match_one(
            {
                "equipment_tag": "E-USER-OVERRIDE",
                "aspen_block_type": "HEATX",
                "phase": "liquid",
                "heat_duty_kw": 1000.0,
                "overall_u_w_m2k": 650.0,
                "lmtd_k": 40.0,
                "hot_side_allowable_pressure_drop_kpa": 35.0,
                "hot_side_fouling_resistance_m2k_w": 0.0005,
                "hot_side_target_velocity_m_s": 2.2,
                "tube_pass_count": 4,
                "tube_layout": "90°方形布管（用户指定）",
            },
            self.rules,
            self.graph,
        )
        override_package = overridden["exchanger_default_parameter_package"]
        override_parameters = override_package["parameters"]
        for field_id, expected in {
            "hot_side_allowable_pressure_drop_kpa": 35.0,
            "hot_side_fouling_resistance_m2k_w": 0.0005,
            "hot_side_target_velocity_m_s": 2.2,
            "tube_pass_count": 4.0,
            "tube_layout": "90°方形布管（用户指定）",
        }.items():
            self.assertEqual(override_parameters[field_id]["value"], expected)
            self.assertEqual(
                override_parameters[field_id]["origin"],
                "USER_PROJECT_OR_ASPEN_INPUT",
            )
            self.assertNotIn(field_id, override_package["default_fields_used"])

    def test_exchanger_material_route_selects_concrete_chloride_grades(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "E-CHLORIDE-MATERIAL",
                "aspen_block_type": "HEATX",
                "main_medium": "含氯离子水溶液",
                "phase": "liquid",
            },
            self.rules,
            self.graph,
        )
        package = result["exchanger_default_parameter_package"]
        parameters = package["parameters"]

        self.assertEqual(
            parameters["shell_material_grade"]["value"],
            "Q345R+S31603复合板",
        )
        self.assertEqual(
            parameters["tube_material_grade"]["value"],
            "S31603",
        )
        self.assertIn(
            "CHLORIDE_S31603_PRELIMINARY_ROUTE",
            " ".join(parameters["tube_material_grade"]["basis"]),
        )
        self.assertIn(
            "不得把S31603候选视为抗点蚀保证",
            parameters["tube_material_grade"]["warning"],
        )

    def test_plate_exchanger_uses_plate_not_shell_and_tube_fallback(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "E-PLATE-FALLBACK",
                "equipment_family": "非标/其他换热器",
                "equipment_type": "板式换热器",
            },
            self.rules,
            self.graph,
        )
        package = result["exchanger_default_parameter_package"]
        parameters = package["parameters"]
        construction = package["construction_selection"]

        self.assertEqual(
            construction["branch_id"],
            "GASKETED_CHEVRON_PLATE_HEAT_EXCHANGER",
        )
        self.assertEqual(
            construction["preliminary_model_designation"],
            "PHE-GASKETED-CHEVRON-A19.6-N42-S31603-EPDM",
        )
        self.assertEqual(parameters["tube_or_plate_count"]["value"], 42)
        self.assertEqual(
            parameters["heat_transfer_plate_material_grade"]["value"],
            "S31603",
        )
        self.assertIn(
            "fallback_profile:gasketed_chevron_plate_s31603_epdm_preliminary",
            parameters["heat_transfer_plate_material_grade"]["basis"],
        )
        self.assertEqual(
            parameters["plate_gasket_material_grade"]["value"],
            "EPDM",
        )
        self.assertEqual(parameters["plate_gap_mm"]["value"], 3.0)
        self.assertEqual(
            parameters["plate_pass_arrangement"]["value"],
            "1×1单流程（程序保底）",
        )
        self.assertNotIn("tube_layout", parameters)
        self.assertNotIn("baffle_cut_percent", parameters)
        self.assertIn(
            "S31603传热板+EPDM垫片",
            result["model_recommendation"]["leading_candidate"][
                "designation"
            ],
        )

        overridden = matcher.match_one(
            {
                "equipment_tag": "E-PLATE-OVERRIDE",
                "equipment_family": "非标/其他换热器",
                "equipment_type": "板式换热器",
                "heat_transfer_plate_material_grade": "TA2",
                "plate_gasket_material_grade": "FKM",
                "plate_gap_mm": 5.0,
                "plate_effective_area_m2": 1.0,
            },
            self.rules,
            self.graph,
        )
        override_package = overridden["exchanger_default_parameter_package"]
        override_parameters = override_package["parameters"]
        self.assertEqual(override_parameters["tube_or_plate_count"]["value"], 22)
        self.assertEqual(
            override_parameters["heat_transfer_plate_material_grade"][
                "value"
            ],
            "TA2",
        )
        self.assertEqual(
            override_parameters["heat_transfer_plate_material_grade"][
                "origin"
            ],
            "USER_PROJECT_OR_ASPEN_INPUT",
        )
        self.assertEqual(
            override_package["construction_selection"][
                "preliminary_model_designation"
            ],
            "PHE-GASKETED-CHEVRON-A19.6-N22-TA2-FKM",
        )

    def test_tower_total_height_is_derived_from_stages_not_generic_default(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "aspen_block_type": "RADFRAC",
                "phase": "liquid",
                "flow_m3_h": 120.0,
                "stage_count": 30,
                "operating_pressure_mpa": 0.2,
                "pressure_basis": "absolute",
                "atmospheric_pressure_mpa": 0.101325,
            },
            self.rules,
            self.graph,
        )
        calculations = {
            item["calculation_id"]: item
            for item in result["calculations"]
        }
        internal_height = calculations["tower_internal_height"]["value"]
        total_height = calculations["tower_preliminary_height"]["value"]
        values = result["design_parameter_package"]["selection_context"]["values"]

        self.assertEqual(total_height, 16_050.0)
        self.assertEqual(values["height_mm"], total_height)
        self.assertEqual(values["inner_diameter_mm"], 600.0)
        self.assertNotIn(
            "diameter_mm",
            result["model_decision"]["sizing_missing_fields"],
        )
        self.assertGreaterEqual(total_height, internal_height * 1000.0)
        self.assertFalse(
            any(
                item["field_id"] == "height_mm"
                for item in result["design_fallbacks"]
            )
        )

    def test_tower_programmatic_spec_reports_tray_branch_materials_and_gates(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "T-TRAY-PROGRAM",
                "aspen_block_type": "RADFRAC",
                "phase": "mixed",
                "flow_m3_h": 120.0,
                "stage_count": 30,
                "operating_pressure_mpa": 0.2,
                "pressure_basis": "absolute",
                "atmospheric_pressure_mpa": 0.101325,
            },
            self.rules,
            self.graph,
        )
        specification = result["programmatic_tower_specification"]
        fields = specification["fields"]

        self.assertEqual(
            specification["status"],
            "PRELIMINARY_CONCRETE_SPECIFICATION_SELECTED",
        )
        self.assertTrue(specification["program_generated"])
        self.assertTrue(specification["deterministic"])
        self.assertFalse(specification["llm_used"])
        self.assertFalse(specification["formal_geometry_selected"])
        self.assertEqual(
            specification["selection_branch"]["internals_branch_id"],
            "SINGLE_PASS_SIEVE_TRAY_REGISTERED_DEFAULT",
        )
        self.assertEqual(fields["shell_material_grade"]["value"], "Q345R")
        self.assertEqual(
            fields["internals_material_grade"]["value"],
            "S30408",
        )
        self.assertEqual(fields["skirt_material_grade"]["value"], "Q345R")
        self.assertEqual(fields["corrosion_allowance_mm"]["value"], 2.0)
        self.assertEqual(
            fields["tower_internals_type"]["value"],
            "单溢流筛板塔盘",
        )
        self.assertEqual(
            fields["packing_type"]["state"],
            "INACTIVE_ALTERNATIVE",
        )
        self.assertIsNone(fields["packing_bed_height_m"]["value"])
        self.assertFalse(
            fields["nominal_shell_wall_thickness_selected"]["value"]
        )
        self.assertEqual(
            fields["nominal_shell_wall_thickness_selected"]["state"],
            "OPEN_FORMAL_EVIDENCE_GATE",
        )
        self.assertEqual(
            result["effective_normalized_input"][
                "programmatic_tower_specification"
            ]["program_specification_sha256"],
            specification["program_specification_sha256"],
        )

    def test_tower_packing_defaults_are_specific_and_user_values_win(
        self,
    ) -> None:
        base = {
            "equipment_tag": "T-PACKING-PROGRAM",
            "equipment_type": "规整填料塔",
            "aspen_block_type": "RADFRAC",
            "phase": "mixed",
            "flow_m3_h": 120.0,
            "stage_count": 30,
            "operating_pressure_mpa": 0.2,
            "pressure_basis": "absolute",
            "atmospheric_pressure_mpa": 0.101325,
        }
        result = matcher.match_one(base, self.rules, self.graph)
        fields = result["programmatic_tower_specification"]["fields"]

        self.assertEqual(
            result["programmatic_tower_specification"]["selection_branch"][
                "internals_branch_id"
            ],
            "PACKED_TOWER_REGISTERED_250Y_FALLBACK_OR_USER_OVERRIDE",
        )
        self.assertEqual(
            fields["packing_type"]["value"],
            "250Y金属孔板波纹规整填料（程序保底）",
        )
        self.assertEqual(fields["packing_material_grade"]["value"], "S30408")
        self.assertEqual(fields["packing_specific_area_m2_m3"]["value"], 250.0)
        self.assertEqual(fields["packing_void_fraction"]["value"], 0.97)
        self.assertEqual(fields["packing_design_flood_fraction"]["value"], 0.70)
        self.assertEqual(fields["packing_bed_height_m"]["value"], 15.0)
        self.assertEqual(fields["packing_section_count"]["value"], 3)
        self.assertEqual(fields["liquid_redistributor_count"]["value"], 2)
        self.assertEqual(fields["packing_total_pressure_drop_kpa"]["value"], 6.0)
        self.assertTrue(
            fields["model_designation"]["value"].startswith(
                "TWR-PACK250Y-"
            )
        )
        self.assertIn(
            "内件=250Y金属孔板波纹规整填料",
            fields["technical_specification"]["value"],
        )

        overridden = matcher.match_one(
            {
                **base,
                "packing_type": "125Y金属规整填料（用户指定）",
                "packing_material_grade": "S31603",
                "packing_specific_area_m2_m3": 125.0,
                "packing_void_fraction": 0.985,
                "packing_hetp_m": 0.8,
                "packing_pressure_drop_kpa_m": 0.25,
                "packing_bed_section_max_height_m": 5.0,
            },
            self.rules,
            self.graph,
        )
        override_fields = overridden["programmatic_tower_specification"][
            "fields"
        ]
        self.assertEqual(
            override_fields["packing_type"]["value"],
            "125Y金属规整填料（用户指定）",
        )
        self.assertEqual(
            override_fields["packing_type"]["origin"],
            "USER_PROJECT_OR_ASPEN_INPUT",
        )
        self.assertEqual(
            override_fields["packing_material_grade"]["value"],
            "S31603",
        )
        self.assertEqual(
            override_fields["packing_bed_height_m"]["value"],
            24.0,
        )
        self.assertEqual(override_fields["packing_section_count"]["value"], 5)
        self.assertEqual(
            override_fields["packing_total_pressure_drop_kpa"]["value"],
            6.0,
        )

    def test_separator_hydraulic_fallback_builds_specific_program_spec(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "V-SEPARATOR-PROGRAM",
                "equipment_family": "反应器/容器/分离器",
                "aspen_block_type": "FLASH2",
                "inner_diameter_mm": 2000.0,
                "straight_shell_length_mm": 6000.0,
                "operating_pressure_mpa": 1.2,
                "pressure_basis": "gauge",
                "temperature_c": 160.0,
                "phase": "mixed",
            },
            self.rules,
            self.graph,
        )
        specification = result[
            "programmatic_vessel_separator_specification"
        ]
        fields = specification["fields"]

        self.assertEqual(
            specification["selection_branch"]["separator_branch_id"],
            "VERTICAL_GAS_LIQUID_SEPARATOR",
        )
        self.assertEqual(fields["orientation"]["value"], "立式")
        self.assertEqual(fields["shell_material_grade"]["value"], "Q345R")
        self.assertEqual(
            fields["internals_material_grade"]["value"],
            "S30408",
        )
        self.assertEqual(fields["gas_flow_m3_h"]["value"], 100.0)
        self.assertEqual(fields["liquid_flow_m3_h"]["value"], 10.0)
        self.assertEqual(fields["gas_density_kg_m3"]["value"], 1.2)
        self.assertEqual(fields["liquid_density_kg_m3"]["value"], 1000.0)
        self.assertEqual(fields["souders_brown_k_m_s"]["value"], 0.107)
        self.assertIn("HG/T 21618-1998 SP型", fields["demister_type"]["value"])
        self.assertEqual(fields["demister_nominal_diameter_mm"]["value"], 2000)
        self.assertEqual(fields["inlet_nozzle_dn"]["value"], 65)
        self.assertEqual(fields["gas_outlet_nozzle_dn"]["value"], 50)
        self.assertEqual(fields["liquid_outlet_nozzle_dn"]["value"], 50)
        self.assertEqual(
            fields["selected_wall_thickness_mm"]["state"],
            "PRELIMINARY_CANDIDATE_NOT_FORMAL",
        )
        self.assertFalse(specification["formal_design_ready"])
        self.assertFalse(specification["llm_used"])
        self.assertEqual(
            fields["technical_specification"][
                "program_specification_sha256"
            ],
            specification["program_specification_sha256"],
        )

    def test_separator_user_hydraulic_overrides_win_and_recalculate(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "V-SEPARATOR-OVERRIDE",
                "equipment_family": "反应器/容器/分离器",
                "aspen_block_type": "FLASH2",
                "inner_diameter_mm": 1200.0,
                "straight_shell_length_mm": 4000.0,
                "gas_flow_m3_h": 3600.0,
                "liquid_flow_m3_h": 36.0,
                "gas_density_kg_m3": 4.0,
                "liquid_density_kg_m3": 800.0,
                "souders_brown_k_m_s": 0.08,
                "liquid_retention_time_min": 8.0,
                "normal_liquid_level_percent": 45.0,
                "demister_type": "用户指定高效S31603丝网除沫器",
                "inlet_nozzle_target_velocity_m_s": 8.0,
                "gas_outlet_nozzle_target_velocity_m_s": 12.0,
                "liquid_outlet_nozzle_target_velocity_m_s": 1.0,
            },
            self.rules,
            self.graph,
        )
        fields = result["programmatic_vessel_separator_specification"][
            "fields"
        ]

        self.assertEqual(fields["gas_flow_m3_h"]["origin"], "USER_PROJECT_OR_ASPEN_INPUT")
        self.assertEqual(fields["demister_type"]["value"], "用户指定高效S31603丝网除沫器")
        self.assertEqual(fields["demister_type"]["origin"], "USER_PROJECT_OR_ASPEN_INPUT")
        self.assertEqual(fields["liquid_holdup_required_volume_m3"]["value"], 4.8)
        self.assertGreater(
            fields["separator_gas_capacity_diameter_mm"]["value"],
            1000.0,
        )
        self.assertEqual(fields["gas_outlet_nozzle_dn"]["value"], 350)
        self.assertIn(
            "Dreq=sqrt",
            fields["gas_outlet_nozzle_dn"]["equation_chain"],
        )

    def test_rplug_builds_specific_tube_count_shell_and_material_spec(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "R-PFR-PROGRAM",
                "equipment_family": "反应器/容器/分离器",
                "aspen_block_type": "RPLUG",
                "equipment_type": "管式平推流反应器",
                "required_total_reactor_volume_m3": 0.1,
                "operating_pressure_mpa": 1.5,
                "pressure_basis": "gauge",
                "temperature_c": 250.0,
            },
            self.rules,
            self.graph,
        )
        specification = result["programmatic_reactor_specification"]
        fields = specification["fields"]

        self.assertEqual(
            specification["selection_branch"]["reactor_branch_id"],
            "TUBULAR_PFR_MINIMUM_OR_VOLUME_CLOSED",
        )
        self.assertEqual(fields["active_tube_inner_diameter_mm"]["value"], 50.0)
        self.assertEqual(fields["active_tube_length_screening_mm"]["value"], 3000.0)
        self.assertEqual(fields["nominal_process_tube_wall_thickness_mm"]["value"], 3.0)
        self.assertEqual(fields["selected_tube_count"]["value"], 17)
        self.assertEqual(fields["reactor_shell_inner_diameter_mm"]["value"], 400.0)
        self.assertEqual(fields["reaction_tube_material_grade"]["value"], "S30408")
        self.assertEqual(fields["shell_material_grade"]["value"], "Q345R")
        self.assertIn(
            "RPLUG-PFR-17×Φ56×3-3000-S30408-Q345R-DN400",
            fields["technical_specification"]["value"],
        )
        self.assertEqual(
            fields["selected_wall_thickness_mm"]["state"],
            "PRELIMINARY_CANDIDATE_NOT_FORMAL",
        )
        self.assertFalse(specification["llm_used"])

        overridden = matcher.match_one(
            {
                "equipment_tag": "R-PFR-OVERRIDE",
                "equipment_family": "反应器/容器/分离器",
                "aspen_block_type": "RPLUG",
                "required_total_reactor_volume_m3": 0.2,
                "active_tube_inner_diameter_mm": 100.0,
                "active_tube_length_screening_mm": 5000.0,
            },
            self.rules,
            self.graph,
        )
        override_fields = overridden["programmatic_reactor_specification"][
            "fields"
        ]
        self.assertEqual(
            override_fields["active_tube_inner_diameter_mm"]["origin"],
            "USER_PROJECT_OR_ASPEN_INPUT",
        )
        self.assertEqual(override_fields["selected_tube_count"]["value"], 6)
        self.assertEqual(
            override_fields["selected_tube_count"]["equation_chain"],
            "Ntube=ceil(Vtotal/V1)",
        )

    def test_rcstr_builds_specific_agitator_jacket_and_power_spec(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "R-CSTR-PROGRAM",
                "equipment_family": "反应器/容器/分离器",
                "aspen_block_type": "RCSTR",
                "equipment_type": "连续搅拌釜式反应器",
                "volume_m3": 10.0,
                "inner_diameter_mm": 2000.0,
                "height_mm": 3500.0,
                "agitator_power_density_kw_m3": 1.2,
            },
            self.rules,
            self.graph,
        )
        specification = result["programmatic_reactor_specification"]
        fields = specification["fields"]

        self.assertEqual(
            specification["selection_branch"]["reactor_branch_id"],
            "STIRRED_TANK_GENERAL_LIQUID_MIXING_FALLBACK",
        )
        self.assertEqual(fields["working_volume_m3"]["value"], 8.0)
        self.assertIn("六叶45°折叶", fields["agitator_type"]["value"])
        self.assertEqual(fields["baffle_count"]["value"], 4)
        self.assertEqual(fields["impeller_diameter_ratio"]["value"], 0.33)
        self.assertEqual(
            fields["agitator_power_density_kw_m3"]["origin"],
            "USER_PROJECT_OR_ASPEN_INPUT",
        )
        self.assertEqual(fields["shaft_power_kw"]["value"], 9.6)
        self.assertEqual(fields["motor_power_kw"]["value"], 11.0)
        self.assertIn("整体夹套", fields["jacket_type"]["value"])
        self.assertEqual(fields["agitator_material_grade"]["value"], "S30408")
        self.assertEqual(
            fields["reaction_tube_material_grade"]["state"],
            "NOT_APPLICABLE",
        )
        self.assertFalse(
            fields["reaction_tube_material_grade"][
                "active_in_selected_branch"
            ]
        )

    def test_crystallizer_builds_specific_dtb_fallback_and_recalculates(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "X-DTB-PROGRAM",
                "equipment_family": "反应器/容器/分离器",
                "aspen_block_type": "CRYSTALLIZER",
            },
            self.rules,
            self.graph,
        )
        specification = result["programmatic_crystallizer_specification"]
        fields = specification["fields"]

        self.assertEqual(
            specification["selection_branch"]["crystallizer_branch_id"],
            "CONTINUOUS_DTB_EXTERNAL_COOLING_FALLBACK",
        )
        self.assertEqual(fields["slurry_flow_m3_h"]["value"], 10.0)
        self.assertEqual(fields["retention_time_min"]["value"], 60.0)
        self.assertEqual(fields["working_volume_m3"]["value"], 10.0)
        self.assertEqual(fields["volume_m3"]["value"], 12.5)
        self.assertEqual(fields["diameter_mm"]["value"], 2400.0)
        self.assertEqual(fields["height_mm"]["value"], 2800.0)
        self.assertAlmostEqual(
            fields["heat_transfer_area_m2"]["value"],
            19.6078431373,
        )
        self.assertEqual(fields["shaft_power_kw"]["value"], 12.0)
        self.assertEqual(fields["motor_power_kw"]["value"], 15.0)
        self.assertIn("三叶轴流推进式", fields["agitator_type"]["value"])
        self.assertIn("中心导流筒", fields["draft_tube_specification"]["value"])
        self.assertEqual(
            fields["wetted_surface_material_grade"]["value"],
            "S30408复合/衬里湿接触表面",
        )
        self.assertIn(
            "CRYST-DTB-EXTCOOL-V12.5-DN2400-H2800",
            fields["technical_specification"]["value"],
        )

        overridden = matcher.match_one(
            {
                "equipment_tag": "X-DTB-OVERRIDE",
                "equipment_family": "反应器/容器/分离器",
                "aspen_block_type": "CRYSTALLIZER",
                "slurry_flow_m3_h": 20.0,
                "retention_time_min": 120.0,
                "heat_duty_kw": 300.0,
                "overall_u_w_m2k": 500.0,
                "lmtd_k": 15.0,
                "lmtd_correction_factor": 0.90,
                "agitator_power_density_kw_m3": 1.5,
            },
            self.rules,
            self.graph,
        )
        override_fields = overridden[
            "programmatic_crystallizer_specification"
        ]["fields"]
        self.assertEqual(
            override_fields["slurry_flow_m3_h"]["origin"],
            "USER_PROJECT_OR_ASPEN_INPUT",
        )
        self.assertEqual(override_fields["working_volume_m3"]["value"], 40.0)
        self.assertEqual(override_fields["volume_m3"]["value"], 50.0)
        self.assertEqual(override_fields["diameter_mm"]["value"], 3800.0)
        self.assertEqual(override_fields["height_mm"]["value"], 4500.0)
        self.assertAlmostEqual(
            override_fields["heat_transfer_area_m2"]["value"],
            44.4444444444,
        )
        self.assertEqual(override_fields["shaft_power_kw"]["value"], 60.0)
        self.assertEqual(override_fields["motor_power_kw"]["value"], 75.0)

    def test_storage_vessel_subtypes_get_distinct_specific_program_specs(
        self,
    ) -> None:
        expected = {
            "储罐": (
                "VERTICAL_STORAGE_VESSEL",
                "立式圆筒储罐",
                "立式",
                0.80,
                2100.0,
                2900.0,
            ),
            "回流罐": (
                "HORIZONTAL_REFLUX_DRUM",
                "卧式回流罐",
                "卧式",
                0.50,
                1700.0,
                4500.0,
            ),
            "缓冲罐": (
                "VERTICAL_BUFFER_VESSEL",
                "立式缓冲罐",
                "立式",
                0.70,
                1900.0,
                3600.0,
            ),
            "其他罐": (
                "VERTICAL_PROCESS_VESSEL",
                "立式工艺容器",
                "立式",
                0.80,
                2100.0,
                2900.0,
            ),
        }
        for input_type, expected_values in expected.items():
            with self.subTest(input_type=input_type):
                result = matcher.match_one(
                    {
                        "equipment_tag": f"V-{input_type}",
                        "equipment_family": "储罐/缓冲罐/回流罐",
                        "equipment_type": input_type,
                    },
                    self.rules,
                    self.graph,
                )
                specification = result[
                    "programmatic_storage_vessel_specification"
                ]
                fields = specification["fields"]
                (
                    branch_id,
                    concrete_type,
                    orientation,
                    fill_fraction,
                    diameter,
                    height_or_length,
                ) = expected_values
                self.assertEqual(
                    specification["selection_branch"][
                        "storage_vessel_branch_id"
                    ],
                    branch_id,
                )
                self.assertEqual(fields["equipment_type"]["value"], concrete_type)
                self.assertEqual(fields["orientation"]["value"], orientation)
                self.assertEqual(
                    fields["fill_fraction"]["value"],
                    fill_fraction,
                )
                self.assertEqual(fields["diameter_mm"]["value"], diameter)
                self.assertEqual(
                    fields["height_or_length_mm"]["value"],
                    height_or_length,
                )
                self.assertEqual(
                    fields["shell_material_grade"]["value"],
                    "Q345R",
                )
                self.assertEqual(
                    fields["selected_wall_thickness_mm"]["value"],
                    6.0,
                )
                self.assertEqual(
                    fields["selected_wall_thickness_mm"]["state"],
                    "PRELIMINARY_CANDIDATE_NOT_FORMAL",
                )
                self.assertIn(
                    concrete_type,
                    fields["technical_specification"]["value"],
                )

    def test_reflux_drum_user_basis_overrides_defaults_and_recalculates(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "V-REFLUX-OVERRIDE",
                "equipment_family": "储罐/缓冲罐/回流罐",
                "equipment_type": "回流罐",
                "flow_m3_h": 120.0,
                "retention_time_min": 15.0,
                "vessel_internals_specification": "用户指定S31603高效入口分配器+除沫器",
            },
            self.rules,
            self.graph,
        )
        specification = result["programmatic_storage_vessel_specification"]
        fields = specification["fields"]

        self.assertEqual(fields["required_volume_m3"]["value"], 60.0)
        self.assertEqual(fields["volume_m3"]["value"], 60.0)
        self.assertEqual(fields["diameter_mm"]["value"], 3000.0)
        self.assertEqual(fields["height_or_length_mm"]["value"], 8500.0)
        self.assertEqual(
            fields["vessel_internals_specification"]["origin"],
            "USER_PROJECT_OR_ASPEN_INPUT",
        )
        self.assertEqual(
            fields["vessel_internals_specification"]["value"],
            "用户指定S31603高效入口分配器+除沫器",
        )
        terminal = result["model_recommendation"]["leading_candidate"][
            "terminal_selection"
        ]
        self.assertEqual(terminal["recommended_type"], "卧式回流罐")

    def test_compressor_builds_specific_stage_power_material_and_seal_spec(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "C-PROGRAM",
                "aspen_block_type": "COMPR",
                "flow_m3_h": 1000.0,
                "inlet_pressure_mpa": 0.1,
                "outlet_pressure_mpa": 0.3,
                "pressure_basis": "absolute",
                "inlet_temperature_c": 25.0,
                "heat_capacity_ratio_k": 1.4,
                "efficiency_percent": 75.0,
                "driver_efficiency_percent": 95.0,
                "auxiliary_power_fraction": 0.05,
            },
            self.rules,
            self.graph,
        )
        specification = result["programmatic_auxiliary_specification"]
        fields = specification["fields"]

        self.assertEqual(
            specification["selection_branch"]["auxiliary_branch_id"],
            "CENTRIFUGAL_COMPRESSOR",
        )
        self.assertEqual(
            fields["model_designation"]["value"],
            "COMP-CENT-1STG-Q1000-PR3.00-P47.8-M55",
        )
        self.assertEqual(fields["stage_count"]["value"], 1)
        self.assertAlmostEqual(
            fields["per_stage_pressure_ratio"]["value"],
            3.0,
        )
        self.assertEqual(fields["intercooler_count"]["value"], 0)
        self.assertAlmostEqual(fields["shaft_power_kw"]["value"], 47.7993841944)
        self.assertAlmostEqual(fields["total_power_kw"]["value"], 52.8308983190)
        self.assertEqual(fields["motor_power_kw"]["value"], 55.0)
        self.assertEqual(fields["casing_material_grade"]["value"], "ZG230-450")
        self.assertEqual(
            fields["impeller_material_grade"]["value"],
            "05Cr17Ni4Cu4Nb",
        )
        self.assertEqual(fields["shaft_material_grade"]["value"], "42CrMo")
        self.assertIn("干气密封", fields["seal_type"]["value"])
        self.assertIn(
            "vendor_capacity_head_efficiency_and_power_map",
            specification["formal_open_gates"],
        )

    def test_agitator_builds_specific_impeller_torque_shaft_and_drive_spec(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "A-PROGRAM",
                "equipment_type": "搅拌器",
                "volume_m3": 10.0,
                "rotational_speed_rpm": 100.0,
                "shaft_power_kw": 5.0,
            },
            self.rules,
            self.graph,
        )
        specification = result["programmatic_auxiliary_specification"]
        fields = specification["fields"]

        self.assertEqual(
            specification["selection_branch"]["auxiliary_branch_id"],
            "TOP_ENTRY_PITCHED_BLADE_TURBINE_AGITATOR",
        )
        self.assertEqual(
            fields["model_designation"]["value"],
            "AGT-TE-PBT45-D750-N100-P5-M7.5-SHAFT45-S30408-4B",
        )
        self.assertEqual(fields["inner_diameter_mm"]["value"], 2200.0)
        self.assertEqual(fields["impeller_diameter_mm"]["value"], 750.0)
        self.assertEqual(fields["baffle_count"]["value"], 4)
        self.assertEqual(fields["motor_power_kw"]["value"], 7.5)
        self.assertEqual(fields["torque_nm"]["value"], 477.5)
        self.assertEqual(fields["shaft_diameter_mm"]["value"], 45.0)
        self.assertEqual(fields["gearbox_ratio"]["value"], 15.0)
        self.assertEqual(
            fields["agitator_material_grade"]["origin"],
            "REGISTERED_AUXILIARY_EQUIPMENT_FALLBACK_PROFILE",
        )
        self.assertTrue(
            specification["user_control"]["restore_registered_default_supported"]
        )

    def test_static_mixer_builds_registered_dn_elements_length_and_pressure_drop(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "M-PROGRAM",
                "equipment_type": "静态混合器",
                "flow_m3_h": 10.0,
                "target_velocity_m_s": 1.5,
            },
            self.rules,
            self.graph,
        )
        specification = result["programmatic_auxiliary_specification"]
        fields = specification["fields"]

        self.assertEqual(
            specification["selection_branch"]["auxiliary_branch_id"],
            "HELICAL_KENICS_STATIC_MIXER",
        )
        self.assertEqual(
            fields["model_designation"]["value"],
            "SMX-KENICS-DN50-6E-L500-S30408-PN16-BW",
        )
        self.assertEqual(fields["selected_dn"]["value"], 50)
        self.assertEqual(fields["selected_outer_diameter_mm"]["value"], 60.3)
        self.assertEqual(fields["selected_wall_thickness_mm"]["value"], 4.0)
        self.assertEqual(fields["element_count"]["value"], 6)
        self.assertEqual(fields["length_mm"]["value"], 500.0)
        self.assertAlmostEqual(
            fields["pressure_drop_kpa"]["value"],
            7.5235185142,
        )
        self.assertEqual(fields["flow_regime"]["value"], "湍流")
        self.assertEqual(
            fields["dynamic_viscosity_mpa_s"]["origin"],
            "REGISTERED_AUXILIARY_EQUIPMENT_FALLBACK_PROFILE",
        )
        self.assertIn("可拆芯", fields["blockage_cleaning_boundary"]["value"])

    def test_membrane_builds_8040_array_area_flow_material_and_override_chain(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "MEM-PROGRAM",
                "equipment_type": "膜组件",
            },
            self.rules,
            self.graph,
        )
        specification = result[
            "programmatic_membrane_package_specification"
        ]
        fields = specification["fields"]

        self.assertEqual(
            specification["selection_branch"]["membrane_package_branch_id"],
            "SPIRAL_WOUND_8040_PA_TFC_ARRAY",
        )
        self.assertEqual(
            fields["model_designation"]["value"],
            "MEM-SW8040-10E-2PV5-PA-TFC-A370-PN16",
        )
        self.assertEqual(fields["element_count"]["value"], 10)
        self.assertEqual(fields["pressure_vessel_count"]["value"], 2)
        self.assertEqual(fields["membrane_area_m2"]["value"], 370.0)
        self.assertEqual(fields["permeate_flow_m3_h"]["value"], 7.4)
        self.assertEqual(fields["feed_flow_m3_h"]["value"], 9.25)
        self.assertAlmostEqual(
            fields["concentrate_flow_m3_h"]["value"],
            1.85,
        )
        self.assertIn("PA-TFC", fields["membrane_material_grade"]["value"])
        self.assertIn("FRP", fields["pressure_vessel_material_grade"]["value"])

        overridden = matcher.match_one(
            {
                "equipment_tag": "MEM-OVERRIDE",
                "equipment_type": "膜组件",
                "element_count": 20,
                "membrane_area_per_element_m2": 37.0,
                "elements_per_pressure_vessel": 5,
                "flux": 15.0,
                "recovery_percent": 75.0,
            },
            self.rules,
            self.graph,
        )
        override_fields = overridden[
            "programmatic_membrane_package_specification"
        ]["fields"]
        self.assertEqual(override_fields["pressure_vessel_count"]["value"], 4)
        self.assertEqual(override_fields["membrane_area_m2"]["value"], 740.0)
        self.assertAlmostEqual(
            override_fields["permeate_flow_m3_h"]["value"],
            11.1,
        )
        self.assertAlmostEqual(
            override_fields["feed_flow_m3_h"]["value"],
            14.8,
        )
        self.assertEqual(
            override_fields["flux"]["origin"],
            "USER_PROJECT_OR_ASPEN_INPUT",
        )

    def test_package_filter_dryer_and_tsa_get_separate_concrete_branches(
        self,
    ) -> None:
        cases = [
            (
                {"equipment_tag": "F-PROGRAM", "aspen_block_type": "FILTER"},
                "AUTOMATIC_RECESSED_CHAMBER_FILTER_PRESS",
                "FP-RECESSED-800-10C-A8-增强PP-P06",
                {
                    "calculated_filter_area_m2": 2.0,
                    "selected_filter_area_m2": 8.0,
                    "chamber_count": 10,
                },
            ),
            (
                {"equipment_tag": "D-PROGRAM", "aspen_block_type": "DRYER"},
                "CONTINUOUS_BELT_HOT_AIR_DRYER",
                "DRY-BELT-HA-W1.5-L4-A6-E100-Q97.2-2Z-S30408",
                {
                    "belt_area_m2": 6.0,
                    "drying_zone_count": 2,
                    "total_installed_power_kw": 13.2,
                },
            ),
            (
                {"equipment_tag": "PKG-PROGRAM", "equipment_type": "成套设备"},
                "TWIN_TOWER_TEMPERATURE_SWING_ADSORPTION_PACKAGE",
                "PKG-TSA-2T-DN500-BED0.2M3-ALUMINA-C8H-PN16",
                {
                    "tower_count": 2,
                    "bed_volume_m3_per_tower": 0.2,
                    "adsorbent_mass_kg_per_tower": 150.0,
                },
            ),
        ]
        for raw, branch_id, designation, expected_fields in cases:
            with self.subTest(branch_id=branch_id):
                result = matcher.match_one(raw, self.rules, self.graph)
                specification = result[
                    "programmatic_membrane_package_specification"
                ]
                fields = specification["fields"]
                self.assertEqual(
                    specification["selection_branch"][
                        "membrane_package_branch_id"
                    ],
                    branch_id,
                )
                self.assertEqual(
                    fields["model_designation"]["value"],
                    designation,
                )
                for field_id, expected in expected_fields.items():
                    self.assertEqual(fields[field_id]["value"], expected)
                self.assertFalse(specification["llm_used"])
                self.assertTrue(
                    specification["user_control"][
                        "single_equipment_recalculation_supported"
                    ]
                )

    def test_liquid_recovery_turbine_builds_power_generator_and_rotor_spec(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "HPRT-PROGRAM",
                "equipment_type": "液力透平",
                "flow_m3_h": 50.0,
                "density_kg_m3": 1000.0,
                "inlet_pressure_mpa": 0.5,
                "outlet_pressure_mpa": 0.2,
                "pressure_basis": "absolute",
                "efficiency_percent": 75.0,
            },
            self.rules,
            self.graph,
        )
        specification = result["programmatic_turbine_specification"]
        fields = specification["fields"]

        self.assertEqual(
            specification["selection_branch"]["turbine_branch_id"],
            "SINGLE_STAGE_RADIAL_PAT_LIQUID_RECOVERY_TURBINE",
        )
        self.assertEqual(
            fields["model_designation"]["value"],
            "HPRT-PAT-1STG-Q50-PR2.50-P3.1-G5.5-N2900",
        )
        self.assertAlmostEqual(fields["shaft_power_kw"]["value"], 3.125)
        self.assertAlmostEqual(fields["electrical_power_kw"]["value"], 2.96875)
        self.assertEqual(fields["generator_power_kw"]["value"], 5.5)
        self.assertEqual(fields["runaway_speed_rpm"]["value"], 3625.0)
        self.assertEqual(
            fields["impeller_material_grade"]["value"],
            "ZG06Cr13Ni4Mo",
        )
        self.assertIn("机械密封", fields["seal_type"]["value"])
        self.assertIn(
            "vendor_q_head_power_efficiency_speed_curve",
            specification["formal_open_gates"],
        )

    def test_gas_expander_builds_stage_specific_work_and_generator_spec(
        self,
    ) -> None:
        result = matcher.match_one(
            {
                "equipment_tag": "EXP-PROGRAM",
                "equipment_type": "气体膨胀机",
                "flow_m3_h": 1000.0,
                "gas_molecular_weight": 28.97,
                "compressibility_factor": 1.0,
                "heat_capacity_ratio_k": 1.3,
                "gas_density_kg_m3": 3.6,
                "inlet_temperature_c": 25.0,
                "inlet_pressure_mpa": 1.0,
                "outlet_pressure_mpa": 0.3,
                "pressure_basis": "absolute",
                "efficiency_percent": 80.0,
            },
            self.rules,
            self.graph,
        )
        specification = result["programmatic_turbine_specification"]
        fields = specification["fields"]

        self.assertEqual(
            specification["selection_branch"]["turbine_branch_id"],
            "MULTISTAGE_RADIAL_INFLOW_GAS_EXPANDER",
        )
        self.assertEqual(fields["stage_count"]["value"], 2)
        self.assertAlmostEqual(
            fields["expander_actual_specific_work_kj_kg"]["value"],
            71.95997391298214,
        )
        self.assertAlmostEqual(
            fields["shaft_power_kw"]["value"],
            233.59739369368378,
        )
        self.assertEqual(
            fields["model_designation"]["value"],
            "EXP-RAD-2STG-Q1000-PR3.33-P233.6-G250-N30000",
        )
        self.assertEqual(fields["generator_power_kw"]["value"], 250.0)
        self.assertEqual(fields["runaway_speed_rpm"]["value"], 36000.0)
        self.assertIn("干气密封", fields["seal_type"]["value"])
        self.assertIn("高速齿轮箱", fields["coupling_type"]["value"])

    def test_all_registered_families_produce_concrete_program_candidates(
        self,
    ) -> None:
        family_inputs = {
            "family_fixed_tubesheet_exchanger": {
                "equipment_type": "固定管板式换热器",
            },
            "family_other_heat_exchanger": {
                "equipment_type": "板式换热器",
            },
            "family_tower": {"equipment_type": "填料塔"},
            "family_reactor_vessel_separator": {
                "equipment_type": "反应器",
            },
            "family_storage_vessel": {"equipment_type": "储罐"},
            "family_pump": {"equipment_type": "离心泵"},
            "family_compressor": {"aspen_block_type": "COMPR"},
            "family_agitator": {"equipment_type": "搅拌器"},
            "family_static_mixer": {"equipment_type": "静态混合器"},
            "family_membrane": {"equipment_type": "膜组件"},
            "family_package_equipment": {"equipment_type": "成套设备"},
            "family_liquid_power_recovery_turbine": {
                "equipment_type": "液力透平",
            },
            "family_gas_expander_turbine": {
                "equipment_type": "气体膨胀机",
            },
            "family_process_piping": {"equipment_type": "工艺管道"},
            "family_pipe_fitting": {"equipment_type": "弯头"},
            "family_flange_gasket": {"equipment_type": "法兰"},
            "family_valve": {"equipment_type": "阀门"},
        }
        registered_families = {
            family["id"]: family for family in self.rules["families"]
        }
        self.assertEqual(
            set(family_inputs),
            set(registered_families),
        )

        for family_id, family in registered_families.items():
            with self.subTest(family_id=family_id):
                result = matcher.match_one(
                    {
                        "equipment_tag": f"AUDIT-{family_id}",
                        "equipment_family": family["aliases"][0],
                        **family_inputs[family_id],
                    },
                    self.rules,
                    self.graph,
                )
                recommendation = result["model_recommendation"]
                terminal = recommendation["terminal_selection"]
                leading = recommendation["leading_candidate"]
                designation = str(leading.get("designation") or "")

                self.assertEqual(result["status"], "MATCHED")
                self.assertTrue(
                    terminal["type_name_quality"]["is_concrete"]
                )
                self.assertTrue(designation.strip())
                self.assertNotIn("非标准型", designation)
                self.assertNotIn("其他设备", designation)
                self.assertTrue(
                    str(leading.get("program_origin") or "").startswith(
                        "DETERMINISTIC_"
                    )
                )
                self.assertFalse(result["llm_used"])
                self.assertFalse(leading["formal_model"])


if __name__ == "__main__":
    unittest.main()
