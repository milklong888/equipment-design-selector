from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PACKAGE_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import equipment_design_match as matcher


class EquipmentFamilyCalculationCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = matcher.load_rules()
        cls.graph = matcher.load_graph()

    def match(self, raw: dict[str, Any]) -> dict[str, Any]:
        return matcher.match_one(raw, self.rules, self.graph)

    @staticmethod
    def calculation(result: dict[str, Any], calculation_id: str) -> dict[str, Any]:
        return next(
            item for item in result["calculations"]
            if item["calculation_id"] == calculation_id
        )

    def test_static_mixer_registered_dn_upsize_and_locked_constraint_failure(
        self,
    ) -> None:
        base = {
            "equipment_type": "静态混合器",
            "flow_m3_h": 10.0,
            "target_velocity_m_s": 1.5,
            "allowable_pressure_drop_kpa": 2.0,
        }
        result = self.match(base)
        specification = result["programmatic_auxiliary_specification"]
        fields = specification["fields"]

        self.assertEqual(fields["selected_dn"]["value"], 80)
        self.assertAlmostEqual(
            fields["predicted_pressure_drop_kpa"]["value"],
            1.5285439090705306,
        )
        self.assertEqual(
            fields["hydraulic_status"]["value"],
            "PASS_AFTER_PRESSURE_DROP_DRIVEN_DN_UPSIZE",
        )
        self.assertEqual(
            specification["selection_branch"]["hydraulic_branch_id"],
            "PRESSURE_DROP_DRIVEN_DN_UPSIZE",
        )
        self.assertEqual(
            fields["selected_dn_standard_id"]["value"],
            "GB/T 12459-2025",
        )
        self.assertEqual(
            fields["selected_wall_basis"]["value"],
            "PROVISIONAL_HYDRAULIC_ALLOWANCE_NOT_PROVED_BY_GBT12459",
        )
        calculation = self.calculation(
            result, "static_mixer_hydraulic_train_screening"
        )
        self.assertEqual(
            calculation["calculation_notice"]["formula_id"],
            "STATIC_MIXER_HYDRAULIC_TRAIN_SCREENING",
        )
        self.assertEqual(
            calculation["branch_selection"]["selected_dn_standard_id"],
            "GB/T 12459-2025",
        )
        bound_fields = {
            item["field_id"] for item in calculation["formula_trace"]["input_bindings"]
        }
        self.assertNotIn("element_count", bound_fields)

        locked = self.match({**base, "selected_dn": 50})
        locked_spec = locked["programmatic_auxiliary_specification"]
        self.assertEqual(
            locked_spec["status"],
            "BLOCKED_STATIC_MIXER_PRESSURE_DROP_CONSTRAINT",
        )
        self.assertAlmostEqual(
            locked_spec["fields"]["predicted_pressure_drop_kpa"]["value"],
            7.523518514191547,
        )
        self.assertEqual(
            locked["model_decision"]["model_status"], "calculation_blocked"
        )
        self.assertIn(
            "BLOCKED_STATIC_MIXER_PRESSURE_DROP_CONSTRAINT",
            {item.get("status") for item in locked["calculation_pending"]},
        )

    def test_membrane_explicit_geometry_wins_and_target_sizing_is_monotonic(
        self,
    ) -> None:
        cylindrical = self.match({
            "equipment_type": "膜组件",
            "membrane_geometry_type": "cylindrical_channels",
            "element_count": 10,
            "channel_count": 100,
            "channel_inner_diameter_mm": 10.0,
            "element_length_m": 1.0,
            "operating_pressure_mpa": 1.5,
            "design_pressure_factor": 1.1,
            "pressure_basis": "gauge",
            "flux": 20.0,
            "recovery_percent": 80.0,
        })
        specification = cylindrical[
            "programmatic_membrane_package_specification"
        ]
        fields = specification["fields"]
        self.assertEqual(
            specification["selection_branch"]["membrane_package_branch_id"],
            "EXPLICIT_CYLINDRICAL_CHANNELS_ARRAY",
        )
        self.assertAlmostEqual(fields["membrane_area_m2"]["value"], 31.415926535897928)
        self.assertAlmostEqual(fields["design_pressure_mpa"]["value"], 1.65)
        self.assertTrue(fields["model_designation"]["value"].startswith("MEM-TUBULAR-"))
        self.assertNotIn("SW8040", fields["model_designation"]["value"])
        self.assertNotIn("PA-TFC", fields["model_designation"]["value"])
        self.assertIsNone(fields["selectivity"]["value"])
        self.assertEqual(
            fields["selectivity"]["origin"],
            "PROGRAMMATIC_GEOMETRY_SPECIFIC_ROUTE",
        )
        array_calculation = self.calculation(
            cylindrical, "membrane_flux_recovery_array_screening"
        )
        self.assertEqual(
            array_calculation["branch_selection"]["area_basis"],
            "CENTRAL_CYLINDRICAL_CHANNEL_GEOMETRY",
        )
        bindings = {
            item["field_id"]: item
            for item in array_calculation["formula_trace"]["input_bindings"]
        }
        self.assertNotIn("membrane_area_per_element_m2", bindings)
        self.assertNotIn("elements_per_pressure_vessel", bindings)
        self.assertEqual(
            bindings["membrane_area_m2"]["source_kind"],
            "upstream_registered_calculation",
        )

        base = {
            "equipment_type": "膜组件",
            "flux": 20.0,
            "recovery_percent": 80.0,
            "design_margin_percent": 10.0,
        }
        low = self.match({**base, "permeate_flow_m3_h": 10.0})
        high = self.match({**base, "permeate_flow_m3_h": 20.0})
        low_branch = self.calculation(
            low, "membrane_flux_recovery_array_screening"
        )["branch_selection"]
        high_branch = self.calculation(
            high, "membrane_flux_recovery_array_screening"
        )["branch_selection"]
        self.assertEqual(low_branch["required_element_count"], 15)
        self.assertEqual(high_branch["required_element_count"], 30)
        self.assertGreater(
            high_branch["required_membrane_area_m2"],
            low_branch["required_membrane_area_m2"],
        )

        undersized = self.match({
            **base,
            "permeate_flow_m3_h": 20.0,
            "element_count": 10,
        })
        undersized_spec = undersized[
            "programmatic_membrane_package_specification"
        ]
        self.assertEqual(
            undersized_spec["status"], "BLOCKED_MEMBRANE_ARRAY_CONSTRAINT"
        )
        self.assertEqual(
            undersized_spec["fields"]["element_count"]["value"], 10
        )
        self.assertEqual(
            undersized_spec["fields"]["required_element_count"]["value"], 30
        )
        self.assertEqual(
            undersized["model_decision"]["model_status"], "calculation_blocked"
        )

    def test_package_route_and_tsa_capacity_basis_hard_gate(self) -> None:
        tsa_base = {
            "equipment_type": "成套设备",
            "process_function": "TSA变温吸附脱水",
            "capacity": 100.0,
            "cycle_time_h": 8.0,
            "adsorption_time_h": 4.0,
        }
        open_result = self.match(tsa_base)
        open_spec = open_result["programmatic_membrane_package_specification"]
        self.assertEqual(
            open_spec["status"], "BLOCKED_CAPACITY_BASIS_OPEN"
        )
        self.assertEqual(
            open_spec["fields"]["cycle_balance_status"]["value"],
            "BLOCKED_CAPACITY_BASIS_OPEN",
        )
        self.assertEqual(
            open_result["model_decision"]["model_status"],
            "calculation_blocked",
        )
        self.assertIn(
            "BLOCKED_TSA_CAPACITY_BASIS_OPEN",
            {item.get("status") for item in open_result["calculation_pending"]},
        )

        closed = self.match({
            **tsa_base,
            "capacity_basis": "100 Nm3/h feed; H2O load 1 kg/h",
            "contaminant_load_kg_h": 1.0,
            "adsorbent_working_capacity_kg_kg": 0.08,
        })
        closed_spec = closed["programmatic_membrane_package_specification"]
        self.assertEqual(
            closed_spec["selection_branch"]["capacity_branch_id"],
            "DYNAMIC_WORKING_CAPACITY_MASS_BALANCE",
        )
        self.assertAlmostEqual(
            closed_spec["fields"]["required_adsorbent_mass_kg_per_tower"]["value"],
            60.0,
        )
        self.assertNotEqual(
            closed["model_decision"]["model_status"], "calculation_blocked"
        )

        psa = self.match({
            "equipment_type": "成套设备",
            "process_function": "PSA变压吸附制氢",
            "capacity": 100.0,
        })
        psa_spec = psa["programmatic_membrane_package_specification"]
        self.assertEqual(psa_spec["selection_branch"]["process_route"], "PSA")
        self.assertEqual(
            psa_spec["selection_branch"]["membrane_package_branch_id"],
            "PRESSURE_SWING_ADSORPTION_ROUTE_BLOCKED_BED_BASIS",
        )
        self.assertNotIn(
            "tsa_cycle_bed_capacity_screening",
            {item["calculation_id"] for item in psa["calculations"]},
        )
        self.assertEqual(
            psa["model_decision"]["model_status"], "calculation_blocked"
        )

    def test_gas_expander_user_properties_bypass_and_operating_hard_gates(
        self,
    ) -> None:
        base = {
            "equipment_type": "气体膨胀机",
            "phase": "vapor",
            "flow_m3_h": 1000.0,
            "mass_flow_kg_h": 7200.0,
            "gas_density_kg_m3": 3.6,
            "gas_molecular_weight": 28.97,
            "compressibility_factor": 1.0,
            "heat_capacity_ratio_k": 1.3,
            "inlet_temperature_c": 25.0,
            "inlet_pressure_mpa": 1.0,
            "outlet_pressure_mpa": 0.3,
            "pressure_basis": "absolute",
            "efficiency_percent": 80.0,
        }
        result = self.match(base)
        specification = result["programmatic_turbine_specification"]
        fields = specification["fields"]
        self.assertEqual(fields["gas_density_kg_m3"]["value"], 3.6)
        self.assertEqual(fields["mass_flow_kg_s"]["value"], 2.0)
        self.assertAlmostEqual(
            fields["calculated_shaft_power_kw"]["value"],
            143.91994782596427,
        )
        self.assertIn("P143.9-G160", fields["model_designation"]["value"])
        self.assertEqual(
            specification["selection_branch"]["density_basis"],
            "USER_OR_ASPEN_GAS_DENSITY",
        )
        self.assertEqual(
            specification["selection_branch"]["mass_flow_basis"],
            "USER_OR_ASPEN_MASS_FLOW_KG_H_CONVERTED_TO_KG_S",
        )
        calculation = self.calculation(
            result, "gas_expander_stage_power_bypass_screening"
        )
        bindings = {
            item["field_id"]: item
            for item in calculation["formula_trace"]["input_bindings"]
        }
        self.assertEqual(bindings["gas_density_kg_m3"]["source_kind"], "normalized_input")
        self.assertEqual(bindings["mass_flow_kg_h"]["value"], 7200.0)
        self.assertEqual(
            calculation["branch_selection"]["protective_bypass_capacity_percent"],
            100.0,
        )

        low_temperature = self.match({
            **base,
            "mass_flow_kg_h": None,
            "minimum_outlet_temperature_c": 10.0,
        })
        low_spec = low_temperature["programmatic_turbine_specification"]
        self.assertEqual(
            low_spec["status"], "BLOCKED_EXPANDER_OPERATING_ENVELOPE"
        )
        self.assertTrue(
            low_spec["fields"]["model_designation"]["value"].startswith(
                "EXP-ROUTE-BLOCKED"
            )
        )
        self.assertEqual(
            low_temperature["model_decision"]["model_status"],
            "calculation_blocked",
        )

        two_phase = self.match({**base, "phase": "two_phase"})
        two_phase_spec = two_phase["programmatic_turbine_specification"]
        self.assertEqual(
            two_phase_spec["fields"]["model_designation"]["value"],
            "EXP-ROUTE-BLOCKED-NONVAPOR",
        )
        self.assertIsNone(two_phase_spec["fields"]["shaft_power_kw"]["value"])
        self.assertFalse(
            two_phase_spec["fields"]["shaft_power_kw"][
                "active_in_selected_branch"
            ]
        )
        self.assertNotIn(
            "gas_expander_stage_power_bypass_screening",
            {item["calculation_id"] for item in two_phase["calculations"]},
        )

    def test_agitator_task_viscosity_baffle_and_user_power_branches(self) -> None:
        user_power = self.match({
            "equipment_type": "搅拌器",
            "volume_m3": 10.0,
            "rotational_speed_rpm": 100.0,
            "shaft_power_kw": 5.0,
            "density_kg_m3": 1000.0,
            "dynamic_viscosity_mpa_s": 1.0,
            "baffle_count": 3,
        })
        fields = user_power["programmatic_auxiliary_specification"]["fields"]
        self.assertTrue(fields["model_designation"]["value"].endswith("-3B"))
        self.assertEqual(fields["shaft_power_kw"]["value"], 5.0)
        self.assertEqual(
            fields["power_basis"]["value"],
            "USER_OR_ASPEN_SHAFT_POWER_PRESERVED_WITH_NP_CROSSCHECK",
        )
        self.assertIn("PURE_TORSION_LOWER_BOUND_ONLY", fields["shaft_diameter_basis"]["value"])

        viscous = self.match({
            "equipment_type": "搅拌器",
            "volume_m3": 10.0,
            "rotational_speed_rpm": 100.0,
            "density_kg_m3": 1000.0,
            "dynamic_viscosity_mpa_s": 1_000_000.0,
        })
        viscous_spec = viscous["programmatic_auxiliary_specification"]
        self.assertEqual(
            viscous_spec["selection_branch"]["impeller_family"],
            "HELICAL_RIBBON",
        )
        self.assertIn("HR2", viscous_spec["fields"]["model_designation"]["value"])
        self.assertNotIn("PBT45", viscous_spec["fields"]["model_designation"]["value"])
        self.assertEqual(
            viscous_spec["status"],
            "BLOCKED_AGITATOR_OVERSIZED_SCREENING_LOAD",
        )
        self.assertEqual(
            viscous["model_decision"]["model_status"], "calculation_blocked"
        )
        self.assertEqual(
            viscous["model_recommendation"]["leading_candidate"]["designation"],
            viscous_spec["fields"]["model_designation"]["value"],
        )
        viscous_calc = self.calculation(
            viscous, "agitator_re_np_power_screening"
        )
        viscous_bindings = {
            item["field_id"] for item in viscous_calc["formula_trace"]["input_bindings"]
        }
        self.assertNotIn("impeller_diameter_ratio", viscous_bindings)

        gas_dispersion = self.match({
            "equipment_type": "搅拌器",
            "process_function": "气体分散",
            "volume_m3": 10.0,
            "rotational_speed_rpm": 100.0,
            "density_kg_m3": 1000.0,
            "dynamic_viscosity_mpa_s": 1.0,
        })
        gas_spec = gas_dispersion["programmatic_auxiliary_specification"]
        self.assertEqual(
            gas_spec["selection_branch"]["impeller_family"],
            "RUSHTON_DISC_TURBINE",
        )
        self.assertIn("RDT6", gas_spec["fields"]["model_designation"]["value"])

    def test_programmatic_branch_is_the_single_terminal_result(self) -> None:
        membrane = self.match({
            "equipment_type": "膜组件",
            "membrane_geometry_type": "cylindrical_channels",
            "element_count": 10,
            "channel_count": 100,
            "channel_inner_diameter_mm": 10.0,
            "element_length_m": 1.0,
            "operating_pressure_mpa": 1.5,
            "design_pressure_factor": 1.1,
            "pressure_basis": "gauge",
            "flux": 20.0,
            "recovery_percent": 80.0,
        })
        membrane_spec = membrane["programmatic_membrane_package_specification"]
        membrane_type = membrane_spec["fields"]["equipment_type"]["value"]
        membrane_designation = membrane_spec["fields"]["model_designation"][
            "value"
        ]
        self.assertEqual(
            membrane["model_recommendation"]["recommended_type"],
            membrane_type,
        )
        self.assertEqual(
            membrane["model_recommendation"]["leading_candidate"]["designation"],
            membrane_designation,
        )
        self.assertEqual(
            membrane["model_decision"]["generated_candidate_designation"],
            membrane_designation,
        )
        self.assertNotIn("卷式", membrane_type)

        unresolved = self.match({
            "equipment_type": "成套设备",
            "process_function": "cyclic package service",
            "capacity": 100.0,
        })
        unresolved_spec = unresolved[
            "programmatic_membrane_package_specification"
        ]
        self.assertEqual(
            unresolved_spec["fields"]["model_designation"]["value"],
            "PKG-ROUTE-OPEN",
        )
        self.assertEqual(
            unresolved["model_recommendation"]["leading_candidate"]["designation"],
            "PKG-ROUTE-OPEN",
        )
        self.assertNotIn(
            "TSA", unresolved["model_recommendation"]["recommended_type"]
        )
        self.assertEqual(
            unresolved["model_recommendation"]["terminal_selection"]["status"],
            "PROGRAMMATIC_TERMINAL_ROUTE_BLOCKED",
        )

    def test_static_mixer_locked_dn_must_pass_velocity_and_pressure(self) -> None:
        result = self.match({
            "equipment_type": "静态混合器",
            "flow_m3_h": 10.0,
            "target_velocity_m_s": 1.5,
            "allowable_pressure_drop_kpa": 10000.0,
            "selected_dn": 15,
        })
        specification = result["programmatic_auxiliary_specification"]
        self.assertEqual(
            specification["status"],
            "BLOCKED_STATIC_MIXER_VELOCITY_CONSTRAINT",
        )
        self.assertEqual(
            specification["fields"]["hydraulic_status"]["value"],
            "FAIL_USER_FIXED_CONFIGURATION_EXCEEDS_TARGET_VELOCITY",
        )
        self.assertGreater(
            specification["fields"]["actual_velocity_m_s"]["value"],
            specification["fields"]["target_velocity_m_s"]["value"],
        )
        self.assertEqual(
            result["model_decision"]["model_status"], "calculation_blocked"
        )

    def test_tsa_capacity_basis_requires_registered_physical_unit(self) -> None:
        result = self.match({
            "equipment_type": "成套设备",
            "process_function": "TSA变温吸附脱水",
            "capacity": 100.0,
            "capacity_basis": "x",
            "cycle_time_h": 8.0,
            "adsorption_time_h": 4.0,
        })
        specification = result["programmatic_membrane_package_specification"]
        self.assertEqual(
            specification["status"], "BLOCKED_CAPACITY_BASIS_INVALID"
        )
        validation = specification["fields"]["capacity_basis_validation"][
            "value"
        ]
        self.assertEqual(
            validation["status"], "INVALID_OR_UNRECOGNIZED_UNIT"
        )
        self.assertEqual(
            result["model_decision"]["model_status"], "calculation_blocked"
        )

    def test_expander_stage_ratio_domain_is_fail_closed_without_crash(self) -> None:
        base = {
            "equipment_type": "气体膨胀机",
            "phase": "vapor",
            "flow_m3_h": 1000.0,
            "gas_molecular_weight": 28.97,
            "compressibility_factor": 1.0,
            "heat_capacity_ratio_k": 1.3,
            "inlet_temperature_c": 25.0,
            "inlet_pressure_mpa": 1.0,
            "outlet_pressure_mpa": 0.3,
            "pressure_basis": "absolute",
            "efficiency_percent": 80.0,
        }
        for invalid_ratio in (1.0, 0.5):
            with self.subTest(maximum_stage_pressure_ratio=invalid_ratio):
                result = self.match({
                    **base,
                    "maximum_stage_pressure_ratio": invalid_ratio,
                })
                specification = result["programmatic_turbine_specification"]
                self.assertEqual(
                    specification["status"],
                    "BLOCKED_EXPANDER_OPERATING_ENVELOPE",
                )
                self.assertEqual(
                    specification["fields"]["operating_envelope_status"][
                        "value"
                    ],
                    "FAIL_INVALID_MAXIMUM_STAGE_PRESSURE_RATIO",
                )
                self.assertIsNone(
                    specification["fields"]["stage_count"]["value"]
                )
                self.assertTrue(
                    specification["fields"]["model_designation"]["value"].startswith(
                        "EXP-ROUTE-BLOCKED"
                    )
                )

    def test_explicit_agitator_type_never_silently_uses_pbt45(self) -> None:
        base = {
            "equipment_type": "搅拌器",
            "volume_m3": 10.0,
            "rotational_speed_rpm": 100.0,
            "density_kg_m3": 1000.0,
            "dynamic_viscosity_mpa_s": 1.0,
        }
        propeller = self.match({
            **base,
            "agitator_type": "三叶推进式搅拌器",
        })
        propeller_spec = propeller["programmatic_auxiliary_specification"]
        self.assertEqual(
            propeller_spec["selection_branch"]["impeller_family"],
            "THREE_BLADE_PROPELLER",
        )
        self.assertIn(
            "PROP3", propeller_spec["fields"]["model_designation"]["value"]
        )
        self.assertNotIn(
            "PBT45", propeller_spec["fields"]["model_designation"]["value"]
        )

        unsupported = self.match({
            **base,
            "agitator_type": "用户自定义磁耦合异形桨",
        })
        unsupported_spec = unsupported["programmatic_auxiliary_specification"]
        self.assertEqual(
            unsupported_spec["status"],
            "BLOCKED_AGITATOR_TYPE_CORRELATION_UNSUPPORTED",
        )
        self.assertEqual(
            unsupported_spec["fields"]["model_designation"]["value"],
            "AGT-ROUTE-BLOCKED-NP-CORRELATION",
        )
        self.assertEqual(
            unsupported["model_decision"]["model_status"],
            "calculation_blocked",
        )


if __name__ == "__main__":
    unittest.main()
