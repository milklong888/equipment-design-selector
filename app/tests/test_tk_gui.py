from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import app_core
import aspen_pfd
import result_presentation
from equipment_design_app import EquipmentDesignApi
from tk_gui import (
    EQUATION_META,
    EquipmentDesignTkApp,
    TranslatedCombobox,
    _customer_overview_rows,
    _pretty_equation,
)


class EquationFormattingTests(unittest.TestCase):
    def test_new_formula_titles_and_symbols_are_engineering_readable(self) -> None:
        cases = {
            "design_pressure_basis_conversion": "设计压力基准换算：P_d,g",
            "storage_required_volume": "最低所需总容积：V_req",
            "liquid_turbine_pressure_head": "压差水头分量（初筛）：H_Δp",
            "liquid_turbine_hydraulic_power": "压差功率分量（初筛）：P_Δp",
            "liquid_turbine_shaft_power": "压差分量轴功率初筛：P_screen",
            "cylinder_volume": "圆筒直段几何容积：V_straight",
        }
        for calculation_id, prefix in cases.items():
            with self.subTest(calculation_id=calculation_id):
                rendered = _pretty_equation({
                    "calculation_id": calculation_id,
                    "equation_chain": "target = formula = 2+3 = 5 unit",
                })
                self.assertTrue(rendered.startswith(prefix))
                self.assertNotIn(calculation_id, rendered)
                self.assertIn("\n    =", rendered)
                self.assertIn(calculation_id, EQUATION_META)


class OverviewDisplayContractTests(unittest.TestCase):
    def test_gui_uses_the_complete_shared_authoritative_overview_field_set(self) -> None:
        rows = _customer_overview_rows({})
        self.assertEqual(
            [row["field_id"] for row in rows],
            [
                field_id
                for field_id, _label
                in result_presentation.CUSTOMER_OVERVIEW_DISPLAY_FIELDS
            ],
        )
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
        }.issubset({row["field_id"] for row in rows}))


@unittest.skipUnless(sys.platform == "win32", "Windows GUI smoke test")
class TkGuiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = EquipmentDesignTkApp(self.root, EquipmentDesignApi(), app_core)
        self.root.update_idletasks()

    def tearDown(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def test_four_routes_and_optional_com_status_are_present(self) -> None:
        self.assertEqual(len(self.app.tabs.tabs()), 4)
        self.assertTrue(self.app.com["optional"])
        self.assertEqual(len(self.app.catalog["selections"]), 36)
        self.assertEqual(self.app.llm_provider.get(), "openai_compatible")
        self.assertEqual(self.app.llm_timeout.get(), "90")
        self.assertTrue(self.app.knowledge_pack_vars)
        self.assertTrue(self.app.llm_enabled.get())
        self.assertEqual(self.app.llm_injection_point.get(), "audit")
        self.assertEqual(self.app.llm_context_scope.get(), "minimum")

    def test_dropdowns_show_chinese_but_keep_canonical_values(self) -> None:
        self.assertEqual(
            tuple(self.app.aspen_basis_combo.cget("values")),
            ("请选择压力基准", "绝压", "表压"),
        )
        self.app.aspen_basis_combo.set("绝压")
        self.assertEqual(self.app.aspen_basis.get(), "absolute")
        self.app.aspen_basis.set("gauge")
        self.assertEqual(
            self.app.aspen_basis_combo.display_variable.get(),
            "表压",
        )

        provider_values = tuple(
            self.app.llm_provider_combo.cget("values")
        )
        self.assertIn("OpenAI 兼容接口", provider_values)
        self.assertIn("离线模拟（不联网）", provider_values)
        self.app.llm_provider_combo.set("离线模拟（不联网）")
        self.assertEqual(self.app.llm_provider.get(), "mock")

        phase_widget = next(
            child
            for child in self.app.field_frame.winfo_children()
            if isinstance(child, TranslatedCombobox)
            and "液相" in tuple(child.cget("values"))
        )
        phase_widget.set("液相")
        self.assertEqual(self.app.field_vars["phase"].get(), "liquid")

    def test_llm_settings_require_connection_test_then_apply_before_collaboration(self) -> None:
        self.assertEqual(self.app.llm_test_button.cget("text"), "测试连接")
        self.assertEqual(self.app.llm_apply_settings_button.cget("text"), "应用设置")
        self.assertEqual(self.app.llm_button.cget("text"), "开始协同计算")
        self.assertEqual(self.app.llm_enabled_label.get(), "☑ 勾选=启用大模型协同")
        self.assertEqual(self.app.llm_knowledge_enabled_label.get(), "☑ 勾选=启用知识检索")
        self.assertTrue(self.app.llm_apply_settings_button.instate(["disabled"]))
        self.assertTrue(self.app.llm_button.instate(["disabled"]))

        self.app.llm_enabled.set(False)
        self.app.llm_knowledge_enabled.set(False)
        self.assertEqual(self.app.llm_enabled_label.get(), "☐ 勾选=启用大模型协同（当前关闭）")
        self.assertEqual(self.app.llm_knowledge_enabled_label.get(), "☐ 勾选=启用知识检索（当前关闭）")
        self.app.llm_enabled.set(True)
        self.app.llm_knowledge_enabled.set(True)

        self.app.llm_model.set("deepseek-chat")
        self.app.llm_key.set("TEST-SECRET")

        def immediate(_button, _label, work, done):
            done(work())

        connected = {
            "ok": True,
            "value": {
                "schema": "equipment-design-llm-connection-test-v1",
                "status": "CONNECTED",
                "provider": "openai_compatible",
                "model_id": "deepseek-chat",
                "message": "连接成功，模型可调用。",
            },
        }
        with patch.object(self.app, "_background", side_effect=immediate), patch.object(
            self.app.api, "test_llm_connection", return_value=connected
        ) as test_connection, patch("tk_gui.messagebox.showinfo"):
            self.app._test_llm_connection()
        test_connection.assert_called_once()
        self.assertTrue(self.app.llm_connection_state.get().startswith("连接状态：成功"))
        self.assertFalse(self.app.llm_apply_settings_button.instate(["disabled"]))

        self.app._apply_llm_settings()
        self.assertFalse(self.app.llm_button.instate(["disabled"]))
        self.assertEqual(self.app._applied_llm_settings["config"]["model"], "deepseek-chat")
        self.assertEqual(self.app._applied_llm_settings["config"]["api_key"], "TEST-SECRET")

        self.app.llm_model.set("deepseek-reasoner")
        self.root.update_idletasks()
        self.assertTrue(self.app.llm_button.instate(["disabled"]))
        self.assertIn("设置已更改", self.app.llm_connection_state.get())

    def test_llm_connection_failure_keeps_apply_disabled_and_shows_specific_reason(self) -> None:
        self.app.llm_model.set("bad-model-id")
        self.app.llm_key.set("TEST-SECRET")

        def immediate(_button, _label, work, done):
            done(work())

        failed = {
            "ok": True,
            "value": {
                "schema": "equipment-design-llm-connection-test-v1",
                "status": "FAILED",
                "provider": "openai_compatible",
                "model_id": "bad-model-id",
                "message": "HTTP 404: model not found",
            },
        }
        with patch.object(self.app, "_background", side_effect=immediate), patch.object(
            self.app.api, "test_llm_connection", return_value=failed
        ), patch("tk_gui.messagebox.showerror") as show_error:
            self.app._test_llm_connection()
        self.assertTrue(self.app.llm_apply_settings_button.instate(["disabled"]))
        self.assertTrue(self.app.llm_button.instate(["disabled"]))
        self.assertIn("model not found", self.app.llm_connection_state.get())
        self.assertNotIn("TEST-SECRET", self.app.llm_connection_state.get())
        self.assertIn("model not found", show_error.call_args.args[1])

    def test_disabled_llm_mode_can_be_applied_without_network_test(self) -> None:
        self.app.llm_enabled.set(False)
        self.app.llm_knowledge_enabled.set(False)
        self.root.update_idletasks()
        self.assertFalse(self.app.llm_apply_settings_button.instate(["disabled"]))
        with patch.object(self.app.api, "test_llm_connection") as test_connection:
            self.app._apply_llm_settings()
        test_connection.assert_not_called()
        self.assertFalse(self.app.llm_button.instate(["disabled"]))
        self.assertFalse(self.app._applied_llm_settings["config"]["enabled"])
        self.assertIn("确定性模式已应用", self.app.llm_connection_state.get())

    def test_manual_fields_are_one_variable_per_parameter(self) -> None:
        self.assertIn("equipment_tag", self.app.field_vars)
        self.assertIn("flow_m3_h", self.app.field_vars)
        self.assertIsNot(self.app.field_vars["equipment_tag"], self.app.field_vars["flow_m3_h"])
        help_controls = [
            child for child in self.app.field_frame.winfo_children()
            if isinstance(child, tk.Label) and child.cget("text") == "ⓘ"
        ]
        self.assertEqual(len(help_controls), len(self.app.field_vars))

    def test_knowledge_tab_exposes_browsable_fields_before_free_text_query(self) -> None:
        self.assertEqual(self.app.kg_search_button.cget("text"), "开始查询")
        self.assertGreater(len(self.app.kg_family_combo.cget("values")), 0)
        rows = self.app.kg_field_tree.get_children()
        self.assertGreater(len(rows), 0)
        first = rows[0]
        values = self.app.kg_field_tree.item(first, "values")
        self.assertTrue(values[0])  # 中文标签
        self.assertTrue(values[1])  # canonical ID

        self.app.kg_field_tree.selection_set(first)
        self.app._use_selected_knowledge_field(run_query=False)
        query = self.app.kg_query.get()
        self.assertIn(str(values[0]), query)
        self.assertIn(str(values[1]), query)

    def test_manual_hover_help_names_recommended_data_source(self) -> None:
        inlet = self.app._manual_help_text({
            "name": "density_kg_m3",
            "label": "密度",
            "manual_role": "required_input",
            "manual_group_title": "入口流股 / Aspen 物性",
        })
        target = self.app._manual_help_text({
            "name": "outlet_pressure_mpa",
            "label": "出口压力",
            "manual_role": "required_input",
            "manual_group_title": "目标条件",
        })
        evidence = self.app._manual_help_text({
            "name": "vendor_curve_path",
            "label": "厂家曲线",
            "manual_role": "advanced_evidence",
            "manual_group_title": "正式证据（正式定型必需，基础计算可选）",
        })
        self.assertIn("同工况 Aspen 导出或可靠工艺数据", inlet)
        self.assertIn("用户明确给定的目标工况或设计目标", target)
        self.assertIn("同一设备的可校验文件", evidence)

    def test_manual_page_shows_candidate_and_formal_closure_before_advanced_expand(self) -> None:
        tower = next(
            item for item in self.app.catalog["selections"]
            if item["selection_id"] == "block:RADFRAC"
        )
        self.app.manual_selection.set(tower["display_name"])
        self.app.manual_advanced.set(False)
        self.app._render_manual_fields()

        summary = self.app.manual_requirement_summary_var.get()
        self.assertIn("主计算必填", summary)
        self.assertIn("候选闭合必需（可留空）", summary)
        self.assertIn("计算内径", summary)
        self.assertIn("正式证据必需（不影响基础计算）", summary)
        self.assertIn("material", self.app.field_vars)
        self.assertNotIn("diameter_mm", self.app.field_vars)
        self.assertNotIn("head_type", self.app.field_vars)
        self.assertEqual(self.app.manual_expand_button.cget("text"), "展开候选/证据项")

        self.app.manual_expand_button.invoke()
        self.root.update_idletasks()
        self.assertTrue(self.app.manual_advanced.get())
        self.assertIn("diameter_mm", self.app.field_vars)
        self.assertIn("head_type", self.app.field_vars)
        self.assertIn("mechanical_result_path", self.app.field_vars)
        self.assertEqual(self.app.manual_expand_button.cget("text"), "收起高级项")

    def test_pump_npsh_optional_input_and_evidence_scope_are_separated(self) -> None:
        self.assertIn("required_npsh_margin_m", self.app.field_vars)
        self.assertNotIn("npshr_evidence_scope", self.app.field_vars)
        self.app.manual_advanced.set(True)
        self.app._render_manual_fields()
        self.assertIn("npshr_evidence_scope", self.app.field_vars)

    def test_pfd_parameter_editor_separates_default_advanced_and_delivery_output_fields(self) -> None:
        tower = next(
            item for item in self.app.catalog["selections"]
            if item["selection_id"] == "block:RADFRAC"
        )
        default_fields = self.app._pfd_parameter_editor_fields(tower, show_advanced=False)
        advanced_fields = self.app._pfd_parameter_editor_fields(tower, show_advanced=True)
        default_names = {field["name"] for field in default_fields}
        advanced_names = {field["name"] for field in advanced_fields}

        self.assertIn("material", default_names)
        self.assertNotIn("head_type", default_names)
        self.assertNotIn("diameter_mm", default_names)
        self.assertNotIn("mechanical_result_path", default_names)
        self.assertIn("head_type", advanced_names)
        self.assertIn("diameter_mm", advanced_names)
        self.assertIn("mechanical_result_path", advanced_names)
        self.assertNotIn("equipment_name", advanced_names)
        self.assertNotIn("quantity_count", advanced_names)
        self.assertTrue(all(field.get("manual_role") != "delivery_output" for field in advanced_fields))

        material = next(field for field in default_fields if field["name"] == "material")
        help_text = self.app._pfd_parameter_help_text(material, "Aspen-base")
        self.assertIn("沿用 Aspen/已有规范值", help_text)
        self.assertIn("补录动作本身不是正式证据", help_text)

    def test_pfd_parameter_editor_renders_help_per_visible_input_and_folds_advanced(self) -> None:
        source = {
            "schema": "aspen-equipment-export-v1",
            "case": {"case_id": "TK-PFD-EDITOR", "pressure_basis": "absolute"},
            "blocks": [{"block_id": "T-101", "block_type": "RADFRAC", "inlet_streams": ["F"], "outlet_streams": ["P"]}],
            "streams": [{"stream_id": "F"}, {"stream_id": "P"}],
        }
        self.app.aspen_bundle = copy.deepcopy(source)
        self.app.aspen_pfd_mapping = aspen_pfd.build_pfd_mapping(source, catalog=self.app.catalog)
        self.app._pfd_equipment_by_block = {
            "T-101": {
                "aspen_block_id": "T-101",
                "canonical_match_input": {"equipment_tag": "T-101", "aspen_block_type": "RADFRAC"},
            },
        }
        selection = self.app._pfd_selection_for_block("T-101")

        self.app._open_pfd_parameter_editor("T-101")
        self.root.update_idletasks()
        window = self.app.pfd_parameter_window
        self.assertIsNotNone(window)

        def descendants(widget: tk.Misc) -> list[tk.Misc]:
            result: list[tk.Misc] = []
            for child in widget.winfo_children():
                result.append(child)
                result.extend(descendants(child))
            return result

        default_fields = self.app._pfd_parameter_editor_fields(selection, show_advanced=False)
        controls = descendants(window)
        help_controls = [item for item in controls if isinstance(item, tk.Label) and item.cget("text") == "ⓘ"]
        labels = [str(item.cget("text")) for item in controls if isinstance(item, (tk.Label, ttk.Label))]
        self.assertEqual(len(help_controls), len(default_fields))
        self.assertFalse(any("设备名称" in label for label in labels))
        self.assertFalse(any("封头型式" in label for label in labels))

        toggle = next(
            item for item in controls
            if isinstance(item, ttk.Checkbutton) and "显示已有结果" in str(item.cget("text"))
        )
        toggle.invoke()
        self.root.update_idletasks()
        advanced_fields = self.app._pfd_parameter_editor_fields(selection, show_advanced=True)
        controls = descendants(window)
        help_controls = [item for item in controls if isinstance(item, tk.Label) and item.cget("text") == "ⓘ"]
        labels = [str(item.cget("text")) for item in controls if isinstance(item, (tk.Label, ttk.Label))]
        self.assertEqual(len(help_controls), len(advanced_fields))
        self.assertTrue(any("封头型式" in label for label in labels))
        self.assertFalse(any("设备名称" in label for label in labels))
        window.destroy()

    def test_aspen_atmospheric_pressure_is_blank_and_label_explains_conversion(self) -> None:
        self.assertEqual(self.app.aspen_atmospheric.get(), "")

        def descendants(widget: tk.Widget) -> list[tk.Widget]:
            rows: list[tk.Widget] = []
            for child in widget.winfo_children():
                rows.append(child)
                rows.extend(descendants(child))
            return rows

        labels = [
            str(widget.cget("text"))
            for widget in descendants(self.root)
            if widget.winfo_class() in {"Label", "TLabel"}
        ]
        self.assertIn("当地大气压 / MPa（表压↔绝压换算需要）", labels)

    def test_empty_aspen_atmospheric_pressure_is_forwarded_as_empty(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".bkp", delete=False) as handle:
            path = Path(handle.name)
        self.addCleanup(path.unlink, missing_ok=True)
        self.app.aspen_path.set(str(path))
        self.app.aspen_basis.set("absolute")
        self.assertEqual(self.app.aspen_atmospheric.get(), "")
        with patch.object(self.app, "_background") as background:
            self.app._run_aspen()
        task = background.call_args.args[2]
        with patch.object(self.app.api, "import_aspen", return_value={"ok": True}) as importer:
            task()
        config = importer.call_args.args[0]
        self.assertEqual(config["atmospheric_pressure_mpa"], "")

    def test_drop_path_parser_preserves_spaces_and_unicode(self) -> None:
        raw = r"{C:\Folder With Space\测试案例.BKP}"
        self.assertEqual(self.app._split_drop_paths(raw), [r"C:\Folder With Space\测试案例.BKP"])

    def test_aspen_file_cannot_change_while_worker_button_is_disabled(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".bkp", delete=False) as handle:
            path = Path(handle.name)
        self.addCleanup(path.unlink, missing_ok=True)
        self.app.aspen_button.state(["disabled"])
        with patch("tk_gui.messagebox.showwarning") as warning:
            accepted = self.app._set_aspen_file(str(path), source="拖入")
        self.assertFalse(accepted)
        self.assertEqual(self.app.aspen_path.get(), "")
        warning.assert_called_once()

    def test_close_running_job_confirms_and_cancels_only_app_workers(self) -> None:
        self.app._background_jobs = 1
        with patch.object(self.app.api, "active_worker_count", return_value=1), patch.object(
            self.app.api, "cancel_active_operations", return_value={"ok": True}
        ) as cancel, patch("tk_gui.messagebox.askyesno", return_value=True):
            self.app._on_close()
        cancel.assert_called_once()
        self.assertTrue(self.app._closing)

    def test_manual_pump_result_renders_equation_chain(self) -> None:
        values = {
            "equipment_tag": "P-TK",
            "phase": "liquid",
            "pressure_basis": "absolute",
            "inlet_pressure_mpa": "0.2",
            "outlet_pressure_mpa": "0.6",
            "density_kg_m3": "900",
            "flow_m3_h": "20",
            "efficiency_percent": "75",
        }
        self.app._fill_manual(values)
        self.app._run_manual()
        self.assertIsNotNone(self.app.last_result)
        equation_text = self.app.equation_text.get("1.0", "end").strip()
        self.assertIn("水力功率", equation_text)
        self.assertIn(" = ", equation_text)
        self.assertIn("    = ", equation_text)
        self.assertIn("kW", equation_text)
        self.assertGreater(len(self.app.parameter_tree.get_children()), 0)
        self.assertGreater(len(self.app.candidate_tree.get_children()), 0)
        parameter_values = [self.app.parameter_tree.item(item, "values") for item in self.app.parameter_tree.get_children()]
        self.assertTrue(any("水力功率" in values for values in parameter_values))
        self.assertIn("内置公式生成", self.app.formula_notice_var.get())
        self.assertIn("压差折算压头", self.app._formula_help_text)
        self.assertIn("不能证明", self.app._formula_help_text)

    def test_gui_agent_run_calls_only_the_protocol_17_bridge(self) -> None:
        values = {
            "equipment_tag": "P-TK-STAGED",
            "phase": "liquid",
            "flow_m3_h": 20,
            "head_m": 45,
            "density_kg_m3": 900,
            "efficiency_percent": 75,
        }
        deterministic = app_core.manual_match("block:PUMP", values)
        self.app.last_deterministic_result = deterministic
        self.app.last_source_input = {
            "operation": "manual_match",
            "payload": {"selection_id": "block:PUMP", "values": values},
        }
        self.app.llm_enabled.set(False)
        self.app.llm_knowledge_enabled.set(False)
        self.app._apply_llm_settings()
        staged_response = {
            "ok": True,
            "value": {
                "schema": "equipment-design-hybrid-result-v2",
                "machine_state": {
                    "state": "COMPLETED_DETERMINISTIC_ONLY",
                },
                "deterministic_result": deterministic,
                "prepared": {
                    "context_pack": {
                        "context_sha256": "A" * 64,
                        "injection_point": "audit",
                        "context_scope": "minimum",
                    },
                },
                "llm_review": {"status": "NOT_REQUESTED", "result": None},
                "fallback": {"used": False, "errors": []},
            },
        }

        def immediate(_button, _label, work, done):
            done(work())

        with patch.object(self.app, "_background", side_effect=immediate), patch.object(
            self.app.api, "agent_hybrid_run", return_value=staged_response
        ) as staged, patch.object(
            self.app.api, "staged_hybrid_run", side_effect=AssertionError("legacy path used")
        ), patch("tk_gui.messagebox.showinfo"):
            self.app._run_llm()
        staged.assert_called_once()
        args = staged.call_args.args
        self.assertEqual(args[3], "audit")
        self.assertEqual(args[4], "minimum")
        self.assertIn("COMPLETED_DETERMINISTIC_ONLY", self.app.hybrid_state.get())

    def test_gui_edit_after_review_invalidates_proposal_before_apply(self) -> None:
        values = {
            "equipment_tag": "P-TK-STALE",
            "phase": "liquid",
            "pressure_basis": "absolute",
            "flow_m3_h": "20",
            "head_m": "45",
            "density_kg_m3": "900",
            "efficiency_percent": "75",
        }
        self.app._fill_manual(values)
        self.app._run_manual()
        frozen_input = copy.deepcopy(self.app.last_source_input)
        self.app.llm_proposal = {
            "schema": "equipment-design-app-llm-orchestration-v1",
            "replay_contract": {
                "schema": "equipment-design-deterministic-replay-v1",
                "input": frozen_input,
            },
            "validated_proposal": {
                "accepted_changes": [{"change_id": "C-1"}],
            },
            "context_sha256": "A" * 64,
            "orchestration_sha256": "B" * 64,
        }
        self.app.field_vars["flow_m3_h"].set("21")
        with patch.object(self.app.api, "agent_llm_apply") as apply_call, patch(
            "tk_gui.messagebox.showwarning"
        ) as warning:
            self.app._apply_llm()
        apply_call.assert_not_called()
        warning.assert_called_once()
        self.assertIsNone(self.app.llm_proposal)
        self.assertTrue(self.app.apply_llm_button.instate(["disabled"]))

    def test_gui_apply_binds_explicit_approval_and_both_hashes(self) -> None:
        values = {
            "equipment_tag": "P-TK-APPROVE",
            "phase": "liquid",
            "pressure_basis": "absolute",
            "flow_m3_h": "20",
            "head_m": "45",
            "density_kg_m3": "900",
            "efficiency_percent": "75",
        }
        self.app._fill_manual(values)
        self.app._run_manual()
        proposal = {
            "schema": "equipment-design-app-llm-orchestration-v1",
            "replay_contract": {
                "schema": "equipment-design-deterministic-replay-v1",
                "input": copy.deepcopy(self.app.last_source_input),
            },
            "validated_proposal": {
                "accepted_changes": [{"change_id": "C-APPROVED"}],
            },
            "context_sha256": "C" * 64,
            "orchestration_sha256": "D" * 64,
        }
        self.app.llm_proposal = proposal
        recalculated = copy.deepcopy(self.app.last_deterministic_result)
        response = {
            "ok": True,
            "value": {
                "applied_draft": copy.deepcopy(values),
                "deterministic_recalculation": recalculated,
            },
        }
        with patch.object(
            self.app.api, "agent_llm_apply", return_value=response
        ) as apply_call, patch.object(self.app, "_render_result"):
            self.app._apply_llm()
        apply_call.assert_called_once()
        submitted_proposal, approval = apply_call.call_args.args
        self.assertIs(submitted_proposal, proposal)
        self.assertTrue(approval["approved"])
        self.assertEqual(approval["approved_change_ids"], ["C-APPROVED"])
        self.assertEqual(approval["context_sha256"], "C" * 64)
        self.assertEqual(approval["orchestration_sha256"], "D" * 64)
        self.assertEqual(approval["approved_by"], "GUI_USER_EXPLICIT_CLICK")

    def test_pfd_canvas_and_override_are_deterministic_and_do_not_mutate_bundle(self) -> None:
        source = {
            "schema": "aspen-equipment-export-v1",
            "case": {
                "case_id": "TK-PFD",
                "pressure_basis": "absolute",
                "run_status": {"terminal_errors": 0, "severe_errors": 0, "errors": 0, "warnings": 0},
            },
            "blocks": [{"block_id": "P-101", "block_type": "PUMP", "inlet_streams": ["S-IN"], "outlet_streams": ["S-OUT"]}],
            "streams": [
                {"stream_id": "S-IN", "pressure_bar": 2.0, "phase": "liquid"},
                {"stream_id": "S-OUT", "pressure_bar": 6.0, "phase": "liquid"},
            ],
        }
        values = {
            "equipment_tag": "P-101",
            "phase": "liquid",
            "pressure_basis": "absolute",
            "inlet_pressure_mpa": 0.2,
            "outlet_pressure_mpa": 0.6,
            "density_kg_m3": 900,
            "flow_m3_h": 20,
            "efficiency_percent": 75,
        }
        match = app_core.manual_match("block:PUMP", values)["result"]
        self.app.aspen_bundle = copy.deepcopy(source)
        self.app.aspen_derivation = {
            "equipment": [{
                "aspen_block_id": "P-101",
                "canonical_match_input": values,
                "match_result": match,
                "input_provenance": {"status": "ASPEN_DERIVED_PROCESS_SIDE"},
                "evidence_boundary": {
                    "mechanical_design_pressure_established": False,
                    "connected_stream_pressure_role": "PROVISIONAL_PROCESS_SIDE_ENVELOPE_NOT_MECHANICAL_DESIGN_PRESSURE",
                },
                "parameter_lineage": [{
                    "target_field": "operating_pressure_mpa",
                    "evidence_class": "J",
                    "result_status": "PROVISIONAL",
                    "equation_chain": "operating_pressure_mpa = max(P_connected) = max(0.2,0.6) = 0.6 MPa",
                    "warning": "not mechanical design pressure",
                }],
            }],
            "piping": [],
        }
        self.app.aspen_pfd_mapping = aspen_pfd.build_pfd_mapping(source, catalog=self.app.catalog)
        self.app._pfd_equipment_by_block = {"P-101": self.app.aspen_derivation["equipment"][0]}
        frozen = json.dumps(source, ensure_ascii=False, sort_keys=True)
        self.app._sync_pfd_view()
        self.root.update_idletasks()
        self.assertIn("1 设备", self.app.pfd_status_var.get())
        self.assertGreater(len(self.app.pfd_view.canvas.find_all()), 0)
        self.app._open_pfd_block("P-101")
        self.assertIn("[J/PROVISIONAL]", self.app.equation_text.get("1.0", "end"))
        self.assertIn("not mechanical design pressure", self.app.equation_text.get("1.0", "end"))
        self.assertIn("ASPEN_DERIVED_PROCESS_SIDE", self.app.issue_text.get("1.0", "end"))
        self.assertIn("mechanical_design_pressure_established", self.app.issue_text.get("1.0", "end"))

        self.app._apply_pfd_override("P-101", "family:family_storage_vessel")
        self.root.update_idletasks()
        row = self.app._pfd_block_row("P-101")
        self.assertEqual(row["effective_mapping"]["mode"], "user_override")
        self.assertEqual(row["effective_mapping"]["family_id"], "family_storage_vessel")
        self.assertIn("P-101", self.app.pfd_recalculated_results)
        self.assertNotIn("P-101", self.app.pfd_invalidated_blocks)
        self.assertEqual(json.dumps(self.app.aspen_bundle, ensure_ascii=False, sort_keys=True), frozen)

    def test_pfd_parameter_supplement_recalculates_one_block_and_keeps_neighbors_stale(self) -> None:
        source = {
            "schema": "aspen-equipment-export-v1",
            "case": {"case_id": "TK-PFD-PARAM", "pressure_basis": "absolute"},
            "blocks": [
                {"block_id": "P-101", "block_type": "PUMP", "inlet_streams": ["F"], "outlet_streams": ["S1"]},
                {"block_id": "E-101", "block_type": "HEATER", "inlet_streams": ["S1"], "outlet_streams": ["P"]},
            ],
            "streams": [
                {"stream_id": "F", "phase": "liquid"},
                {"stream_id": "S1", "phase": "liquid"},
                {"stream_id": "P", "phase": "liquid"},
            ],
        }
        pump_values = {
            "equipment_tag": "P-101",
            "phase": "liquid",
            "pressure_basis": "absolute",
            "flow_m3_h": 20,
            "head_m": 45,
            "density_kg_m3": 900,
            "efficiency_percent": 75,
        }
        neighbor_values = {"equipment_tag": "E-101", "heat_duty_kw": 80}
        self.app.aspen_bundle = copy.deepcopy(source)
        self.app.aspen_derivation = {
            "equipment": [
                {
                    "aspen_block_id": "P-101",
                    "canonical_match_input": pump_values,
                    "match_result": app_core.manual_match("block:PUMP", pump_values)["result"],
                },
                {
                    "aspen_block_id": "E-101",
                    "canonical_match_input": neighbor_values,
                    "match_result": app_core.manual_match("block:HEATER", neighbor_values)["result"],
                },
            ],
            "piping": [],
        }
        self.app.aspen_pfd_mapping = aspen_pfd.build_pfd_mapping(source, catalog=self.app.catalog)
        self.app._pfd_equipment_by_block = {
            item["aspen_block_id"]: item for item in self.app.aspen_derivation["equipment"]
        }
        frozen = json.dumps(self.app.aspen_bundle, ensure_ascii=False, sort_keys=True)
        self.assertTrue(self.app.pfd_parameter_button.instate(["disabled"]))
        self.assertEqual(self.app.pfd_parameter_button.cget("text"), "补充/修改本设备参数并重算")

        self.app._open_pfd_block("P-101")
        self.assertFalse(self.app.pfd_parameter_button.instate(["disabled"]))
        applied = self.app._apply_pfd_parameter_overrides(
            "P-101",
            {"required_npsh_margin_m": "0.5", "npsha_m": "3.0", "npshr_m": "2.0"},
        )

        self.assertTrue(applied)
        self.assertEqual(self.app.pfd_parameter_overrides["P-101"]["required_npsh_margin_m"], "0.5")
        self.assertIn("P-101", self.app.pfd_recalculated_results)
        recalculated = self.app.pfd_recalculated_results["P-101"]
        self.assertEqual(recalculated["input"]["equipment_tag"], "P-101")
        self.assertEqual(recalculated["input"]["required_npsh_margin_m"], "0.5")
        self.assertNotEqual(recalculated["result"].get("model_decision", {}).get("model_status"), "FINAL_MODEL")
        self.assertEqual(self.app.pfd_overlays["P-101"]["status"], "WAITING_FORMAL_EVIDENCE")
        self.assertNotIn("P-101", self.app.pfd_invalidated_blocks)
        self.assertIn("E-101", self.app.pfd_invalidated_blocks)
        self.assertIn("S1", self.app.pfd_invalidated_streams)
        self.assertEqual(self.app._pfd_block_row("P-101")["recalculation_status"], "RECALCULATED_CURRENT")
        self.assertEqual(json.dumps(self.app.aspen_bundle, ensure_ascii=False, sort_keys=True), frozen)
        self.assertIn("USER_SUPPLIED_PER_BLOCK", self.app.issue_text.get("1.0", "end"))

        cleared = self.app._apply_pfd_parameter_overrides("P-101", {}, clear=True)
        self.assertTrue(cleared)
        self.assertNotIn("P-101", self.app.pfd_parameter_overrides)
        self.assertNotIn("required_npsh_margin_m", self.app.pfd_recalculated_results["P-101"]["input"])

        self.app._open_pfd_stream("S1")
        self.assertTrue(self.app.pfd_parameter_button.instate(["disabled"]))

    def test_logic_node_left_click_reports_model_not_applicable(self) -> None:
        source = {
            "schema": "aspen-equipment-export-v1",
            "case": {"case_id": "TK-LOGIC", "pressure_basis": "absolute"},
            "blocks": [
                {"block_id": "F-101", "block_type": "FSPLIT", "inlet_streams": ["S-IN"], "outlet_streams": ["S-OUT"]},
            ],
            "streams": [
                {"stream_id": "S-IN", "phase": "liquid"},
                {"stream_id": "S-OUT", "phase": "liquid"},
            ],
        }
        logic_match = {
            "schema": "aspen-simulation-logic-node-classification-v1",
            "status": "NOT_APPLICABLE",
            "model_decision": {
                "model_status": "NOT_APPLICABLE",
                "reason_code": "NOT_APPLICABLE_SIMULATION_LOGIC_NODE",
                "formal_model": None,
            },
        }
        self.app.aspen_pfd_mapping = aspen_pfd.build_pfd_mapping(source, catalog=self.app.catalog)
        self.app._pfd_equipment_by_block = {
            "F-101": {"aspen_block_id": "F-101", "match_result": logic_match},
        }

        self.app._open_pfd_block("F-101")

        self.assertEqual(self.app.summary_vars["型号状态"].get(), "不适用（流程逻辑节点）")
        self.assertEqual(self.app.summary_vars["待闭合"].get(), "不适用")

    def test_type_override_invalidates_adjacent_device_and_stream_overlays(self) -> None:
        source = {
            "schema": "aspen-equipment-export-v1",
            "case": {"case_id": "TK-PFD-IMPACT", "pressure_basis": "absolute"},
            "blocks": [
                {"block_id": "P-101", "block_type": "PUMP", "inlet_streams": ["F"], "outlet_streams": ["S1"]},
                {"block_id": "E-101", "block_type": "HEATER", "inlet_streams": ["S1"], "outlet_streams": ["S2"]},
                {"block_id": "V-101", "block_type": "FLASH2", "inlet_streams": ["S2"], "outlet_streams": ["P"]},
            ],
            "streams": [
                {"stream_id": stream_id, "phase": "liquid"}
                for stream_id in ("F", "S1", "S2", "P")
            ],
        }
        pump_values = {
            "equipment_tag": "P-101",
            "phase": "liquid",
            "pressure_basis": "absolute",
            "flow_m3_h": 20,
            "head_m": 45,
            "density_kg_m3": 900,
            "efficiency_percent": 75,
        }
        pump_match = app_core.manual_match("block:PUMP", pump_values)["result"]
        stale_neighbor = app_core.manual_match(
            "block:HEATER",
            {"equipment_tag": "E-101", "heat_duty_kw": 80},
        )["result"]
        self.app.aspen_bundle = copy.deepcopy(source)
        self.app.aspen_derivation = {
            "equipment": [
                {"aspen_block_id": "P-101", "canonical_match_input": pump_values, "match_result": pump_match},
                {"aspen_block_id": "E-101", "canonical_match_input": {"heat_duty_kw": 80}, "match_result": stale_neighbor},
            ],
            "piping": [],
        }
        self.app.aspen_pfd_mapping = aspen_pfd.build_pfd_mapping(source, catalog=self.app.catalog)
        self.app._pfd_equipment_by_block = {
            item["aspen_block_id"]: item for item in self.app.aspen_derivation["equipment"]
        }
        self.app._sync_pfd_view()

        self.app._apply_pfd_override("P-101", "family:family_storage_vessel")

        self.assertIn("P-101", self.app.pfd_recalculated_results)
        self.assertNotIn("P-101", self.app.pfd_invalidated_blocks)
        self.assertIn("E-101", self.app.pfd_invalidated_blocks)
        self.assertIn("S1", self.app.pfd_invalidated_streams)
        self.assertEqual(
            self.app.pfd_overlays["E-101"]["status"],
            "WAITING_CALCULATED_PARAMETERS",
        )
        self.assertEqual(
            self.app.pfd_overlays["stream:S1"]["status"],
            "WAITING_CALCULATED_PARAMETERS",
        )
        self.assertNotEqual(
            self.app.pfd_overlays["E-101"].get("designation"),
            stale_neighbor.get("model_recommendation", {}).get("leading_candidate", {}).get("designation"),
        )

    def test_derivation_flow_supports_single_recalculation_and_restore(
        self,
    ) -> None:
        payload = app_core.manual_match(
            "block:PUMP",
            {
                "equipment_tag": "P-WORKBENCH",
                "phase": "liquid",
                "flow_m3_h": 100,
                "density_kg_m3": 1000,
                "head_m": 40,
            },
        )
        self.app._render_result(payload)
        self.root.update_idletasks()
        self.assertEqual(
            [
                node["node_id"]
                for node in self.app.current_derivation_workbench[
                    "nodes"
                ]
            ],
            [
                "source",
                "template",
                "calculation",
                "terminal",
                "adjustment",
                "delivery",
            ],
        )
        self.assertGreaterEqual(
            len(
                self.app.derivation_canvas.find_withtag(
                    "derivation-node"
                )
            ),
            12,
        )
        baseline_hash, session = (
            self.app._current_derivation_session()
        )
        self.assertTrue(baseline_hash)
        session["overrides"]["flow_m3_h"] = "120"
        with patch("tk_gui.messagebox.showerror"):
            self.app._recalculate_derivation_equipment()
        current = self.app._current_match_results[0]
        audit = current["user_derivation_override_audit"]
        self.assertEqual(
            audit["status"],
            "USER_SCENARIO_RECALCULATED",
        )
        self.assertEqual(
            audit["changes"][0]["program_default_value"],
            100,
        )
        self.assertEqual(
            audit["changes"][0]["user_override_value"],
            "120",
        )
        self.app._restore_derivation_defaults()
        restored = self.app._current_match_results[0]
        self.assertEqual(
            restored["normalized_input"]["flow_m3_h"],
            100,
        )

    def test_gui_exports_report_and_hash_manifest(self) -> None:
        self.app._render_result(
            app_core.manual_match(
                "block:PUMP",
                {
                    "equipment_tag": "P-EXPORT",
                    "phase": "liquid",
                    "flow_m3_h": 4000,
                    "density_kg_m3": 1000,
                    "head_m": 60,
                },
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "report.md"
            with (
                patch(
                    "tk_gui.filedialog.asksaveasfilename",
                    return_value=str(report_path),
                ),
                patch("tk_gui.messagebox.showinfo"),
            ):
                self.app._export_report()
            manifest_path = report_path.with_name(
                "report.manifest.json"
            )
            self.assertTrue(report_path.is_file())
            self.assertTrue(manifest_path.is_file())
            self.assertIn(
                "### 强制警告",
                report_path.read_text(encoding="utf-8"),
            )
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["format"], "markdown")
            self.assertEqual(
                manifest["report_sha256"],
                hashlib.sha256(
                    report_path.read_bytes()
                ).hexdigest().upper(),
            )
            self.assertTrue(
                manifest["organized_answer_sha256"]
            )


if __name__ == "__main__":
    unittest.main()
