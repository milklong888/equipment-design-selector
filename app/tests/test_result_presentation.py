from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import result_presentation
import app_core


class ResultPresentationTests(unittest.TestCase):
    def test_formula_trace_is_visible_in_html_markdown_and_agent_answer(self) -> None:
        result = app_core.manual_match("block:PUMP", {
            "equipment_tag": "P-REPORT-TRACE",
            "process_function": "liquid pressure boosting",
            "pressure_basis": "absolute",
            "flow_m3_h": 24.0,
            "density_kg_m3": 910.0,
            "inlet_pressure_mpa": 0.15,
            "outlet_pressure_mpa": 0.45,
            "efficiency_percent": 75.0,
        })
        presentation = result_presentation.build_presentation(result)
        html = result_presentation.render_html(presentation)
        self.assertIn("公式可追溯性", html)
        self.assertIn("公式定义 SHA-256", html)
        self.assertIn("本次计算追溯 SHA-256", html)
        self.assertIn("scripts/equipment_design_match.py#run_calculations", html)
        self.assertIn("formula_pump_hydraulic_power", html)

        answer = result_presentation.build_organized_answer(presentation)
        calculations = answer["equipment"][0]["calculations"]
        traced = next(
            item
            for item in calculations
            if item["calculation_id"] == "pump_hydraulic_power"
        )
        self.assertEqual(traced["formula_id"], "A_PUMP_HYDRAULIC_POWER")
        self.assertEqual(
            traced["formula_trace"]["schema"],
            "equipment-formula-trace-v1",
        )
        markdown = result_presentation.render_organized_markdown(answer)
        self.assertIn("公式 ID：`A_PUMP_HYDRAULIC_POWER`", markdown)
        self.assertIn("公式定义 SHA-256", markdown)
        self.assertIn("输入绑定", markdown)
        self.assertIn("公式来源", markdown)
        self.assertIn("追溯缺口", markdown)

    def test_overview_schema_requires_quantity_and_visible_core_columns(self) -> None:
        schema = json.loads(
            (APP_DIR / "schemas" / "equipment_overview_table.schema.json").read_text(
                encoding="utf-8"
            )
        )
        row_schema = schema["properties"]["rows"]["items"]
        self.assertIn("quantity_and_standby", row_schema["required"])
        self.assertIn("quantity_and_standby", row_schema["properties"])
        required_columns = {
            clause["contains"]["const"]
            for clause in schema["properties"]["columns"]["allOf"]
        }
        self.assertTrue({
            "sequence_number",
            "process_section",
            "equipment_tag",
            "equipment_name",
            "quantity_and_standby",
            "equipment_type",
            "model_or_specification",
            "authority_information_coverage",
            "selection_specificity_gate",
            "formal_readiness_gate",
        }.issubset(required_columns))

    def test_authoritative_overview_core_fields_and_gates_are_visible_in_html(self) -> None:
        overview = {
            "sequence_number": 7,
            "process_section": "精馏段",
            "equipment_tag": "P-701",
            "equipment_name": "回流泵",
            "quantity_and_standby": {"installed": 2, "operating": 1, "standby": 1},
            "equipment_type": "卧式离心泵",
            "model_or_specification": "OH2-DN80-32m",
            "model_or_specification_status": "type_selected",
            "authority_structural_completeness": {
                "state": "PASS", "required": 12, "emitted": 12,
            },
            "authority_information_coverage": {
                "state": "BLOCKED",
                "required": 12,
                "covered": 11,
                "blocking_fields": ["seal_plan"],
            },
            "customer_information_coverage": {
                "state": "PASS", "blocking_fields": [],
            },
            "selection_specificity_gate": {
                "state": "PASS",
                "required_fields": ["flow_m3_h"],
                "resolved_fields": ["flow_m3_h"],
                "blocking_fields": [],
            },
            "formal_readiness_gate": {
                "state": "BLOCKED",
                "required_fields": ["vendor_curve_sha256"],
                "blocking_fields": ["vendor_curve_sha256"],
                "model_status": "type_selected",
            },
            "standards_and_versions": ["API 610"],
            "evidence_ids": ["CALC-P-701"],
            "evidence_level": {"value": "A2"},
            "customer_table_missing_fields": [],
            "algorithm_evidence_missing_fields": ["vendor_curve_sha256"],
            "model_estimate_disclosure": {"status": "NOT_USED"},
            "delivery_state": "NOT_READY",
        }
        display_rows = result_presentation.customer_overview_display_rows(overview)
        self.assertEqual(
            [row["field_id"] for row in display_rows],
            [field_id for field_id, _label in result_presentation.CUSTOMER_OVERVIEW_DISPLAY_FIELDS],
        )
        presentation = {
            "schema": "equipment-design-presentation-v1",
            "equipment": [{
                "equipment_id": "P-701",
                "header": {"family_name": "泵"},
                "status_axes": {},
                "parameter_groups": [],
                "calculation_chain": [],
                "candidates": [],
                "issues": {},
                "customer_overview": overview,
            }],
        }
        rendered = result_presentation.render_html(presentation)
        for label in (
            "序号",
            "工艺段 / 装置",
            "设备位号 / 管线号",
            "设备名称",
            "数量及备用",
            "型式 / 结构",
            "型号 / 工程规格",
            "权威表信息覆盖",
            "具体选型门",
            "正式就绪门",
        ):
            self.assertIn(label, rendered)
        for value in (
            "精馏段",
            "P-701",
            "回流泵",
            "OH2-DN80-32m",
            "状态：未通过",
            "已覆盖 11/12",
            "seal_plan",
            "vendor_curve_sha256",
        ):
            self.assertIn(value, rendered)

    def test_compact_status_labels_preserve_machine_codes_outside_the_view(self) -> None:
        self.assertEqual(result_presentation.code_label("MATCHED"), "身份已匹配")
        self.assertEqual(result_presentation.code_label("type_selected"), "型式已确定")
        self.assertEqual(
            result_presentation.code_label("NEAR_STANDARD_DESIGN_POINT"),
            "旧版近标准设计点（已停用）",
        )
        self.assertEqual(
            result_presentation.code_label("HEURISTIC_NEAREST_STANDARD_REFERENCE_POINT"),
            "启发式最近标准参考点（非性能曲线适配）",
        )

    def test_html_uses_structured_engineering_equation_chain_and_compact_numbers(self) -> None:
        presentation = {
            "schema": "equipment-design-presentation-v1",
            "equipment": [{
                "equipment_id": "P-TEST",
                "header": {"family_name": "泵"},
                "status_axes": {},
                "parameter_groups": [{
                    "title": "水力与功率计算",
                    "rows": [{
                        "field_id": "hydraulic_power_kw",
                        "label": "水力功率",
                        "symbol": "Ph",
                        "raw_value": 2.222222222,
                        "display_value": "2.22222",
                        "unit": "kW",
                        "source": {"kind": "deterministic_calculation"},
                        "state": "CALCULATED",
                        "formula_chain": {
                            "target": "hydraulic_power_kw",
                            "formula": "rho*g*Q*H",
                            "substitution": "900*9.80665*(20/3600)*45.3207/1000",
                            "answer": "2.22222 kW",
                        },
                    }],
                }],
                "calculation_chain": [{
                    "target_field": "hydraulic_power_kw",
                    "status": "CALCULATED_WITH_EXPLICIT_INPUTS",
                    "formula_chain": {
                        "target": "hydraulic_power_kw",
                        "formula": "rho*g*Q*H",
                        "substitution": "900*9.80665*(20/3600)*45.3207/1000",
                        "answer": "2.22222 kW",
                    },
                }],
                "candidates": [],
                "issues": {},
            }],
        }
        rendered = result_presentation.render_html(presentation)
        self.assertIn("P<sub>h</sub>", rendered)
        self.assertIn("ρ · g · Q · H", rendered)
        self.assertIn("2.2222", rendered)
        self.assertNotIn("hydraulic_power_kw = hydraulic_power_kw", rendered)

    def test_candidate_gates_are_not_overwritten_by_completeness(self) -> None:
        payload = {
            "schema": "equipment-deterministic-match-result-v1",
            "engine_version": "test",
            "status": "MATCHED",
            "normalized_input": {"equipment_tag": "P-TEST"},
            "match": {"family_id": "family_pump", "family_name": "泵"},
            "model_decision": {"model_status": "type_selected"},
            "model_recommendation": {
                "formal_model_gate": "model-level formal gate",
                "candidates": [
                    {
                        "candidate_id": "candidate-specific",
                        "completeness": {"missing_fields": ["head_m"]},
                        "missing_gates": ["vendor_curve_sha256", "npsha_m", "npshr_m"],
                        "formal_model_gate": "candidate-specific formal gate",
                    },
                    {
                        "candidate_id": "fallback",
                        "completeness": {"missing_fields": ["flow_m3_h"]},
                    },
                    {
                        "candidate_id": "explicit-empty",
                        "completeness": {"missing_fields": ["density_kg_m3"]},
                        "missing_gates": [],
                        "formal_model_gate": "",
                    },
                ],
            },
        }

        candidates = result_presentation.build_presentation(payload)["equipment"][0]["candidates"]
        by_id = {candidate["candidate_id"]: candidate for candidate in candidates}

        self.assertEqual(
            by_id["candidate-specific"]["missing_gates"],
            ["vendor_curve_sha256", "npsha_m", "npshr_m"],
        )
        self.assertEqual(
            by_id["candidate-specific"]["formal_model_gate"],
            "candidate-specific formal gate",
        )
        self.assertEqual(by_id["fallback"]["missing_gates"], ["flow_m3_h"])
        self.assertEqual(by_id["fallback"]["formal_model_gate"], "model-level formal gate")
        self.assertEqual(by_id["explicit-empty"]["missing_gates"], [])
        self.assertEqual(by_id["explicit-empty"]["formal_model_gate"], "")

    def test_terminal_form_source_is_visible_in_card_and_html_without_a_text_box_dump(self) -> None:
        terminal = {
            "status": "DEFAULTED_TERMINAL_TYPE_SELECTED",
            "recommended_type": "单溢流筛板塔",
            "selection_basis": "registered_default",
            "default_applied": True,
            "rule_id": "tower:registered_default:single_pass_sieve_tray",
            "assumption": "未给出专门型式条件，采用设备族登记默认型式。",
            "evidence_class": "J",
            "provisional": True,
        }
        payload = {
            "schema": "equipment-deterministic-match-result-v1",
            "engine_version": "test",
            "status": "MATCHED",
            "normalized_input": {"equipment_tag": "T-DEFAULT", "aspen_block_type": "RADFRAC"},
            "match": {"family_id": "family_tower", "family_name": "塔器"},
            "model_decision": {"model_status": "type_selected"},
            "model_recommendation": {
                "status": "PARTIAL_ENGINEERING_CANDIDATE",
                "recommended_type": "单溢流筛板塔",
                "terminal_selection": terminal,
                "candidates": [],
            },
            "design_parameter_package": {"groups": [], "calculation_chain": []},
        }

        presentation = result_presentation.build_presentation(payload)
        card = presentation["equipment"][0]
        self.assertEqual(card["terminal_selection"], terminal)
        rendered = result_presentation.render_html(presentation)
        self.assertIn("默认选定", rendered)
        self.assertIn("未给出专门型式条件，采用设备族登记默认型式。", rendered)
        self.assertIn("tower:registered_default:single_pass_sieve_tray", rendered)

    def test_every_registered_programmatic_branch_is_visible_and_translated(self) -> None:
        fixture = json.loads(
            (
                APP_DIR
                / "fixtures"
                / "all_family_minimum_meaningful_inputs.json"
            ).read_text(encoding="utf-8")
        )
        expected_branch_families = {
            "family_tower",
            "family_reactor_vessel_separator",
            "family_storage_vessel",
            "family_compressor",
            "family_agitator",
            "family_static_mixer",
            "family_membrane",
            "family_package_equipment",
            "family_liquid_power_recovery_turbine",
            "family_gas_expander_turbine",
        }
        observed: set[str] = set()
        for case in fixture["cases"]:
            family_id = case["family_id"]
            if family_id not in expected_branch_families:
                continue
            response = app_core.auto_match({
                "equipment_family": family_id,
                "equipment_tag": f"BRANCH-{family_id}",
                **case["values"],
            })
            presentation = result_presentation.build_presentation(response)
            card = presentation["equipment"][0]
            branches = card["branch_selection"][
                "programmatic_selection_branches"
            ]
            with self.subTest(family_id=family_id):
                self.assertEqual(len(branches), 1)
                branch = branches[0]
                self.assertTrue(branch["deterministic"])
                self.assertFalse(branch["llm_used"])
                self.assertTrue(branch["choices"])
                self.assertIn("设备专用算法分支", branch["branch_narrative"])
                self.assertIn(
                    branch["specification_label"],
                    branch["branch_narrative"],
                )
                for field_id, raw_value in branch[
                    "selection_branch"
                ].items():
                    if not field_id.endswith("_branch_id") or not raw_value:
                        continue
                    self.assertIn(
                        str(raw_value),
                        "\n".join(
                            card["branch_selection"]["natural_language"]
                        ),
                    )
                self.assertTrue(
                    all(
                        choice["label"] != choice["field_id"]
                        for choice in branch["choices"]
                        if choice["field_id"] in {
                            "recommended_type",
                            "fallback_profile_id",
                        }
                        or choice["field_id"].endswith("_branch_id")
                    )
                )
            observed.add(family_id)
        self.assertEqual(observed, expected_branch_families)

        tower_case = next(
            item
            for item in fixture["cases"]
            if item["family_id"] == "family_tower"
        )
        tower = app_core.auto_match({
            "equipment_family": "family_tower",
            "equipment_tag": "T-BRANCH-TRACE",
            **tower_case["values"],
        })
        tower_presentation = result_presentation.build_presentation(tower)
        html = result_presentation.render_html(tower_presentation)
        answer = result_presentation.build_organized_answer(
            tower_presentation
        )
        markdown = result_presentation.render_organized_markdown(answer)
        for visible_text in (
            "设备专用算法分支",
            "塔器专用选型器",
            "SINGLE_PASS_SIEVE_TRAY_REGISTERED_DEFAULT",
            "登记默认的单溢流筛板塔盘",
            "塔板数×按直径选取的板间距",
        ):
            self.assertIn(visible_text, html)
            self.assertIn(visible_text, markdown)

    def test_five_family_branch_details_are_translated_with_units_and_narrative(
        self,
    ) -> None:
        expected_branch_labels = {
            "TURBULENT_PITCHED_BLADE_TURBINE_45_CONSTANT_POWER_NUMBER": (
                "45°折叶涡轮充分湍流恒功率准数分支"
            ),
            "PRESSURE_DROP_DRIVEN_DN_UPSIZE": "压降驱动的 DN 增径分支",
            "FLUX_RECOVERY_INTEGER_ARRAY_SIZING": (
                "通量—回收率整数阵列定容分支"
            ),
            "DYNAMIC_WORKING_CAPACITY_MASS_BALANCE": (
                "按动态工作容量进行质量衡算"
            ),
            "RADIAL_INFLOW_STAGE_POWER_SCREENING": (
                "径向流膨胀机级数与功率初筛"
            ),
        }
        expected_status_and_basis_labels = {
            "power_number_branch_id": {
                "power_basis": "程序 Re-Np 公式功率初筛",
                "impeller_family": "45°折叶开启涡轮桨",
            },
            "hydraulic_branch_id": {
                "hydraulic_status": "增径后压降通过",
            },
            "array_branch_id": {
                "area_basis": "登记值或用户提供的单元有效膜面积",
                "array_sizing_status": "膜阵列预设计能力通过",
                "target_flow_basis": "用户给定产水/渗透流量目标",
            },
            "capacity_branch_id": {
                "cycle_balance_status": "TSA 循环与床层容量预设计通过",
                "process_route": "变温吸附路线（TSA）",
            },
            "expander_branch_id": {
                "operating_envelope_status": "级数与功率预设计通过",
                "density_basis": "用户或 Aspen 提供气体密度",
                "mass_flow_basis": "用户或 Aspen 的 kg/h 质量流量换算为 kg/s",
            },
        }
        cases = [
            (
                {
                    "equipment_type": "搅拌器",
                    "volume_m3": 10.0,
                    "rotational_speed_rpm": 100.0,
                    "density_kg_m3": 1000.0,
                    "dynamic_viscosity_mpa_s": 1.0,
                },
                "power_number_branch_id",
                "reynolds_number",
                "-",
            ),
            (
                {
                    "equipment_type": "静态混合器",
                    "flow_m3_h": 10.0,
                    "target_velocity_m_s": 1.5,
                    "allowable_pressure_drop_kpa": 2.0,
                },
                "hydraulic_branch_id",
                "selected_dn",
                "DN",
            ),
            (
                {
                    "equipment_type": "膜组件",
                    "flux": 20.0,
                    "recovery_percent": 80.0,
                    "design_margin_percent": 10.0,
                    "permeate_flow_m3_h": 10.0,
                },
                "array_branch_id",
                "array_stage_count",
                "段",
            ),
            (
                {
                    "equipment_type": "成套设备",
                    "process_function": "TSA变温吸附脱水",
                    "capacity": 100.0,
                    "capacity_basis": "100 Nm3/h feed; H2O load 1 kg/h",
                    "contaminant_load_kg_h": 1.0,
                    "adsorbent_working_capacity_kg_kg": 0.08,
                    "cycle_time_h": 8.0,
                    "adsorption_time_h": 4.0,
                },
                "capacity_branch_id",
                "parallel_train_count",
                "列",
            ),
            (
                {
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
                },
                "expander_branch_id",
                "protective_bypass_capacity_percent",
                "%",
            ),
        ]
        for raw, branch_key, unit_key, expected_unit in cases:
            with self.subTest(branch_key=branch_key):
                response = app_core.auto_match(raw)
                presentation = result_presentation.build_presentation(response)
                card = presentation["equipment"][0]
                branches = card["branch_selection"][
                    "programmatic_selection_branches"
                ]
                self.assertEqual(len(branches), 1)
                branch = branches[0]
                self.assertEqual(
                    branch["specification_status_label"],
                    "已形成具体初步规格",
                )
                self.assertIn(
                    str(branch["specification_status"]),
                    branch["branch_narrative"],
                )
                choices = {
                    item["field_id"]: item for item in branch["choices"]
                }
                self.assertNotEqual(
                    choices[branch_key]["label"], branch_key
                )
                self.assertEqual(choices[unit_key]["unit"], expected_unit)
                raw_branch_code = str(
                    branch["selection_branch"][branch_key]
                )
                self.assertEqual(
                    choices[branch_key]["raw_value"], raw_branch_code
                )
                self.assertEqual(
                    choices[branch_key]["value_label"],
                    expected_branch_labels[raw_branch_code],
                )
                for field_id, expected_label in (
                    expected_status_and_basis_labels[branch_key].items()
                ):
                    self.assertEqual(
                        choices[field_id]["value_label"], expected_label
                    )
                detailed_narrative = str(
                    branch["selection_branch"]["branch_narrative"]
                )
                self.assertIn(raw_branch_code, branch["branch_narrative"])
                self.assertIn(
                    detailed_narrative, branch["branch_narrative"]
                )
                self.assertIn(
                    branch["branch_narrative"],
                    card["branch_selection"]["natural_language"],
                )
                html = result_presentation.render_html(presentation)
                self.assertIn(raw_branch_code, html)
                self.assertIn(expected_branch_labels[raw_branch_code], html)
                for expected_label in expected_status_and_basis_labels[
                    branch_key
                ].values():
                    self.assertIn(expected_label, html)
                self.assertIn(detailed_narrative, html)
                if "power_calculation_id" in choices:
                    self.assertEqual(
                        choices["power_calculation_id"]["label"],
                        "功率计算链",
                    )
                if "stage_count" in choices:
                    self.assertEqual(
                        choices["stage_count"]["label"], "膨胀级数"
                    )

    def test_program_selected_main_equipment_and_small_component_branches_are_visible(self) -> None:
        result = app_core.manual_match("block:PUMP", {
            "equipment_tag": "P-BRANCH-OUTPUT",
            "aspen_block_type": "PUMP",
            "phase": "liquid",
            "main_medium": "water",
            "flow_m3_h": 120,
            "head_m": 60,
            "density_kg_m3": 998,
            "efficiency_percent": 72,
            "pressure_basis": "gauge",
            "inlet_pressure_mpa": 0.25,
            "outlet_pressure_mpa": 0.84,
            "temperature_c": 80,
            "design_temperature_c": 80,
        })

        presentation = result_presentation.build_presentation(result)
        card = presentation["equipment"][0]
        self.assertEqual(
            card["selected_output"]["recommended_type"],
            "轴向吸入离心泵",
        )
        self.assertTrue(card["branch_selection"]["natural_language"])
        branch_text = "\n".join(
            card["branch_selection"]["natural_language"]
        )
        self.assertIn("系统构型分支依据", branch_text)
        self.assertIn("等价比较方案 1", branch_text)
        self.assertIn("未证明系统曲线或热工水力等价", branch_text)
        self.assertIn(
            "HT250",
            card["selected_output"]["pump_material_and_seal"][
                "pump_casing"
            ],
        )
        self.assertRegex(
            card["selected_output"]["selected_flange_pressure_class"],
            r"^PN\d+$",
        )
        self.assertTrue(
            {
                "pump_per_unit_shutoff_head_screening",
                "pump_series_final_shutoff_pressure",
                "pump_flange_pressure_class_selection",
            }.issubset({
                item.get("calculation_id")
                for item in card["calculation_chain"]
            })
        )
        self.assertTrue(
            card["branch_selection"][
                "leading_candidate_predicate_branches"
            ]
        )
        families = {
            item["component_family"]
            for item in card["component_selections"]
        }
        self.assertTrue({
            "flange_type", "facing", "gasket_type", "fastener_type",
        }.issubset(families))
        self.assertTrue(
            all(
                item.get("branch_narrative")
                for item in card["component_selections"]
            )
        )
        propagated_contexts = [
            connection["raw_service_context"]
            for connection in result["connection_component_selections"][
                "connections"
            ]
        ]
        self.assertEqual(
            {
                context["program_selected_pressure_class"]
                for context in propagated_contexts
            },
            {card["selected_output"]["selected_flange_pressure_class"]},
        )
        self.assertEqual(
            {
                context["program_selected_nozzle_dn_mm"]
                for context in propagated_contexts
            },
            {100, 125},
        )
        delivery_fields = {
            field["field_id"]: field
            for field in result["result"]["customer_delivery"][
                "equipment_family_datasheet"
            ]["equipment"][0]["fields"]
        }
        for field_id in (
            "pump_casing_material",
            "impeller_material",
            "shaft_material",
            "shaft_sleeve_material",
            "seal_type",
            "secondary_seal_material",
            "gasket_material",
            "maximum_final_discharge_pressure_mpa_gauge",
            "pressure_class",
            "pump_16bar_scope_check",
        ):
            with self.subTest(field_id=field_id):
                self.assertEqual(
                    delivery_fields[field_id]["state"],
                    "PROGRAM_PRELIMINARY_SELECTED",
                )
                self.assertIsNotNone(delivery_fields[field_id]["value"])
        self.assertEqual(
            delivery_fields["vendor_curve_ref"]["state"],
            "OPEN_FORMAL_EVIDENCE_GATE",
        )
        self.assertIn(
            "完整Q-H、效率、功率和NPSHr",
            delivery_fields["vendor_curve_ref"]["label"],
        )

        html = result_presentation.render_html(presentation)
        self.assertIn("基本信息与程序实际选择", html)
        self.assertIn("分支选择（自然文字）", html)
        self.assertIn("连接口小部件选择分支", html)
        self.assertIn("带颈对焊法兰", html)
        self.assertIn("泵材料与密封", html)
        self.assertIn("HT250", html)
        self.assertIn("程序选择法兰压力等级", html)
        self.assertIn("系统构型分支依据", html)
        self.assertIn("等价比较方案 1", html)
        self.assertIn("详细计算链条", html)
        self.assertLess(
            html.index("分支选择（自然文字）"),
            html.index("详细计算链条"),
        )

        answer = result_presentation.build_organized_answer(presentation)
        equipment = answer["equipment"][0]
        self.assertTrue(equipment["basic_information"]["program_selected"])
        self.assertTrue(equipment["component_selections"])
        markdown = result_presentation.render_organized_markdown(answer)
        self.assertIn("### 基本信息", markdown)
        self.assertIn("### 分支选择与大模型调控", markdown)
        self.assertIn("#### 连接部件选择", markdown)
        self.assertIn("### 详细计算链条", markdown)
        self.assertLess(
            markdown.index("### 分支选择与大模型调控"),
            markdown.index("### 详细计算链条"),
        )

    def test_hybrid_llm_judgments_validation_and_applied_values_are_visible(self) -> None:
        deterministic = app_core.manual_match("block:PUMP", {
            "equipment_tag": "P-LLM-OUTPUT",
            "phase": "liquid",
            "flow_m3_h": 20,
            "head_m": 45,
            "density_kg_m3": 900,
            "efficiency_percent": 75,
        })
        hybrid = {
            "schema": "equipment-design-hybrid-result-v2",
            "machine_state": {
                "state": "COMPLETED_HYBRID_RECALCULATED",
                "llm_requested": True,
            },
            "deterministic_result": deterministic,
            "deterministic_recalculation": deterministic,
            "orchestration": {
                "provider": "openai_compatible",
                "model": "test-engineering-model",
                "injection_point": "audit",
                "context_scope": "full_family",
                "context_sha256": "A" * 64,
                "orchestration_sha256": "B" * 64,
                "step_output": {
                    "summary": "建议补充 NPSHr 初筛值并复核汽蚀分支。",
                    "condition_assessments": [{
                        "condition_id": "pump_low_npsh_margin",
                        "status": "supported",
                        "reason": "当前缺少厂家曲线，需保守初筛。",
                        "citations": ["deterministic_result"],
                    }],
                    "calculation_assists": [{
                        "assist_id": "npshr_screen",
                        "target_field": "npshr_m",
                        "proposed_value": 3.2,
                    }],
                    "terminal_selection_assists": [],
                    "ambiguity_decision": None,
                    "audit_findings": [],
                },
                "calculation_assist_validation": [{
                    "assist_id": "npshr_screen",
                    "status": "VERIFIED_PROVISIONAL_ENGINEERING_ESTIMATE",
                }],
                "terminal_selection_assist_validation": [],
                "output_composition": {
                    "title": "泵选型调控",
                    "blocks": [{
                        "block_id": "summary",
                        "heading": "大模型工程复核摘要",
                        "operation": "explain_result",
                        "section_ref": "summary",
                        "citations": ["deterministic_result"],
                    }],
                },
            },
            "calculation_assist_application": {
                "status": "VERIFIED_INPUTS_APPLIED_AND_RECALCULATED",
                "applied_inputs": {},
                "applied_model_estimate_inputs": {"npshr_m": 3.2},
            },
            "terminal_selection_application": {
                "status": "NOT_NEEDED",
                "applied_overrides": {},
            },
            "fallback": {"used": False, "errors": []},
        }

        presentation = result_presentation.build_presentation(hybrid)
        control = presentation["llm_control_result"]
        self.assertTrue(presentation["llm_used"])
        self.assertEqual(control["status"], "COMPLETED_AND_RECALCULATED")
        self.assertEqual(control["model"], "test-engineering-model")
        self.assertEqual(control["applied_model_estimates"], {"npshr_m": 3.2})
        self.assertTrue(control["condition_assessments"])
        self.assertEqual(
            control["organized_output_blocks"][0]["heading"],
            "大模型工程复核摘要",
        )
        card = presentation["equipment"][0]
        self.assertEqual(card["llm_control_result"]["model"], "test-engineering-model")

        html = result_presentation.render_html(presentation)
        self.assertIn("大模型调控结果", html)
        self.assertIn("建议补充 NPSHr 初筛值并复核汽蚀分支", html)
        markdown = result_presentation.render_organized_markdown(
            result_presentation.build_organized_answer(presentation)
        )
        self.assertIn("#### 大模型调控结果", markdown)
        self.assertIn("test-engineering-model", markdown)
        self.assertIn("大模型工程复核摘要", markdown)
        self.assertIn("LLM补值建议及程序复核", markdown)

    def test_hybrid_envelope_renders_only_the_active_recalculation(self) -> None:
        def result(recommended_type: str) -> dict:
            return {
                "schema": "equipment-deterministic-match-result-v1",
                "engine_version": "test",
                "deterministic": True,
                "llm_used": False,
                "status": "MATCHED",
                "normalized_input": {"equipment_tag": "P-HYBRID"},
                "match": {"family_id": "family_pump", "family_name": "泵"},
                "model_decision": {"model_status": "type_selected"},
                "model_recommendation": {
                    "status": "PARTIAL_ENGINEERING_CANDIDATE",
                    "recommended_type": recommended_type,
                    "candidates": [],
                },
                "design_parameter_package": {"groups": [], "calculation_chain": []},
            }

        presentation = result_presentation.build_presentation({
            "schema": "equipment-design-hybrid-result-v2",
            "deterministic_result": result("旧初算型式"),
            "deterministic_recalculation": result("复算生效型式"),
        })
        self.assertEqual(presentation["equipment_count"], 1)
        self.assertEqual(presentation["equipment"][0]["header"]["recommended_type"], "复算生效型式")


if __name__ == "__main__":
    unittest.main()
