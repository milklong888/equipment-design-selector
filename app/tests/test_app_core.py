from __future__ import annotations

import hashlib
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import app_core
import equipment_calc
import llm_bridge
from equipment_design_app import EquipmentDesignApi


class AppCoreTests(unittest.TestCase):
    def test_catalog_contains_pump_and_one_field_per_parameter(self) -> None:
        catalog = app_core.load_catalog()
        pump = next(item for item in catalog["selections"] if item["block_type"] == "PUMP")
        names = [field["name"] for field in pump["fields"]]
        self.assertEqual(len(names), len(set(names)))
        for field in ("flow_m3_h", "density_kg_m3", "inlet_pressure_mpa", "outlet_pressure_mpa", "efficiency_percent"):
            self.assertIn(field, names)
        self.assertEqual(catalog["multiple_choice_policy"]["same_family_subtypes"], "retain_most_general_common_family_and_type_selected")

    def test_manual_catalog_separates_inputs_preferences_and_outputs(self) -> None:
        catalog = app_core.load_catalog()
        pump = next(item for item in catalog["selections"] if item["selection_id"] == "block:PUMP")
        fields = {item["name"]: item for item in pump["fields"]}
        self.assertEqual(fields["flow_m3_h"]["manual_role"], "required_input")
        self.assertEqual(fields["material"]["manual_role"], "optional_preference")
        self.assertTrue(fields["material"]["manual_default_visible"])
        self.assertEqual(fields["head_m"]["manual_role"], "known_result")
        self.assertFalse(fields["head_m"]["manual_default_visible"])
        self.assertIn("pump_hydraulic_power", fields["flow_m3_h"]["calculation_consumers"])

        exchanger = next(
            item for item in catalog["selections"]
            if item["selection_id"] == "family:family_fixed_tubesheet_exchanger"
        )
        exchanger_fields = {item["name"]: item for item in exchanger["fields"]}
        self.assertEqual(exchanger_fields["lmtd_correction_factor"]["manual_role"], "recommended_input")
        self.assertIn("保底值", exchanger_fields["lmtd_correction_factor"]["manual_blank_behavior"])
        self.assertIn("exchanger_area", exchanger_fields["lmtd_correction_factor"]["calculation_consumers"])

    def test_manual_mode_derives_service_profile_and_ignores_direct_labels(self) -> None:
        response = app_core.manual_match("block:PUMP", {
            "equipment_tag": "P-MANUAL",
            "phase": "liquid",
            "inlet_pressure_mpa": 0.2,
            "outlet_pressure_mpa": 0.7,
            "service_labels": ["safety.flammable"],
            "flammable": True,
        })
        profile = response["service_profile"]
        labels = {item["label_id"]: item for item in profile["service_labels"]}
        self.assertEqual(labels["module.intent"]["value"], "liquid_pressure_increase")
        self.assertEqual(labels["observed.operation.pressure_direction"]["value"], "increase")
        self.assertNotIn("safety.flammable", labels)
        self.assertTrue(any(item["code"] == "DIRECT_SERVICE_LABEL_INPUT_IGNORED" for item in profile["diagnostics"]))
        connection_package = response["connection_component_selections"]
        self.assertEqual(connection_package["schema"], "equipment-connection-selection-package-v1")
        self.assertIn("service_labels", connection_package["ignored_direct_label_fields"])
        self.assertIn("flammability", connection_package["ignored_direct_label_fields"])
        self.assertTrue(connection_package["connections"])
        for selected in connection_package["connections"][0]["component_types"].values():
            self.assertEqual(selected["terminal_count"], 1)
            self.assertEqual(selected["normalized_service_labels"]["flammability"], "unknown")
            self.assertIn(
                "W_DERIVED_INPUT_IGNORED",
                {item["warning_id"] for item in selected["warnings"]},
            )

    def test_manual_contract_exposes_primary_candidate_and_formal_layers(self) -> None:
        catalog = app_core.load_catalog()
        selection_ids = (
            "block:RADFRAC",
            "block:RCSTR",
            "family:family_storage_vessel",
        )
        for selection_id in selection_ids:
            with self.subTest(selection_id=selection_id):
                selection = next(
                    item for item in catalog["selections"]
                    if item["selection_id"] == selection_id
                )
                contract = selection["manual_input_contract"]
                self.assertTrue(contract["candidate_closure_required_fields"])
                self.assertTrue(contract["candidate_fields_may_be_blank"])
                self.assertTrue(contract["formal_release_requires_evidence"])
                self.assertTrue(contract["formal_evidence_gate"])

                fields = {item["name"]: item for item in selection["fields"]}
                for name in contract["candidate_closure_required_fields"]:
                    self.assertTrue(fields[name]["candidate_closure_required"])
                    self.assertIn("candidate_closure_required", fields[name]["manual_requirement_tiers"])
                self.assertTrue(any(not fields[name]["manual_default_visible"] for name in contract["candidate_closure_required_fields"]))
                self.assertEqual(fields["material"]["manual_role"], "optional_preference")
                self.assertFalse(fields["material"]["primary_calculation_required"])

                status = app_core.manual_requirement_status(selection, {})
                candidate_names = {
                    row["name"] for row in status["candidate_closure"]["input_side_gaps"]
                }
                self.assertEqual(candidate_names, set(contract["candidate_closure_required_fields"]))
                self.assertEqual(
                    status["formal_evidence"]["gate"],
                    contract["formal_evidence_gate"],
                )

    def test_manual_metadata_reserves_npsh_scope_and_head_type_without_defaults(self) -> None:
        catalog = app_core.load_catalog()
        pump = next(item for item in catalog["selections"] if item["selection_id"] == "block:PUMP")
        pump_fields = {item["name"]: item for item in pump["fields"]}
        margin = pump_fields["required_npsh_margin_m"]
        self.assertEqual(margin["unit"], "m")
        self.assertEqual(margin["manual_role"], "optional_input")
        self.assertTrue(margin["manual_default_visible"])
        self.assertIn("UNKNOWN", margin["manual_blank_behavior"])
        scope = pump_fields["npshr_evidence_scope"]
        self.assertEqual(scope["manual_role"], "advanced_evidence")
        self.assertEqual(scope["options"], ["", "same_duty_vendor_curve"])
        self.assertFalse(scope["manual_default_visible"])

        for selection_id in ("block:RADFRAC", "block:RCSTR", "family:family_storage_vessel"):
            selection = next(item for item in catalog["selections"] if item["selection_id"] == selection_id)
            head_type = next(item for item in selection["fields"] if item["name"] == "head_type")
            self.assertEqual(head_type["manual_role"], "advanced_design_input")
            self.assertEqual(head_type["options"], ["", "2:1_ellipsoidal"])
            self.assertFalse(head_type["manual_default_visible"])
            self.assertIn("不作为输入错误", head_type["manual_blank_behavior"])

    def test_static_aspen_atmospheric_input_has_no_silent_default(self) -> None:
        html = (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("表压↔绝压换算需要", html)
        self.assertIn('id="aspen-atmospheric" type="number" step="any" value=""', html)
        self.assertNotIn('id="aspen-atmospheric" type="number" step="any" value="0.101325"', html)

    def test_manual_pump_closes_head_hydraulic_and_shaft_power(self) -> None:
        result = app_core.manual_match("block:PUMP", {
            "equipment_tag": "P-101",
            "process_function": "liquid pressure boosting",
            "pressure_basis": "absolute",
            "flow_m3_h": 36.0,
            "density_kg_m3": 850.0,
            "inlet_pressure_mpa": 0.12,
            "outlet_pressure_mpa": 0.50,
            "efficiency_percent": 72.0,
        })["result"]
        self.assertEqual(result["match"]["family_id"], "family_pump")
        calculations = {item["calculation_id"]: item for item in result["calculations"]}
        self.assertAlmostEqual(calculations["pump_hydraulic_power"]["value"], 3.8, places=9)
        self.assertAlmostEqual(calculations["pump_shaft_power"]["value"], 5.277777777777777, places=9)
        self.assertTrue(result["calculation_notices"])
        pressure_head = calculations["pump_head_from_pressure"]["calculation_notice"]
        self.assertEqual(pressure_head["release_class"], "B")
        self.assertEqual(pressure_head["evidence_class"], "J")
        self.assertEqual(pressure_head["result_status"], "PROVISIONAL")
        self.assertFalse(pressure_head["embedded_empirical_default_used"])

    def test_every_executed_formula_has_reproducible_machine_trace(self) -> None:
        result = app_core.manual_match("block:PUMP", {
            "equipment_tag": "P-TRACE",
            "process_function": "liquid pressure boosting",
            "pressure_basis": "absolute",
            "flow_m3_h": 36.0,
            "density_kg_m3": 850.0,
            "inlet_pressure_mpa": 0.12,
            "outlet_pressure_mpa": 0.50,
            "efficiency_percent": 72.0,
        })["result"]
        calculations = {
            item["calculation_id"]: item
            for item in result["calculations"]
        }
        self.assertTrue(calculations)
        for calculation in calculations.values():
            trace = calculation["formula_trace"]
            self.assertEqual(trace["schema"], "equipment-formula-trace-v1")
            self.assertRegex(trace["formula_definition_sha256"], r"^[A-F0-9]{64}$")
            self.assertRegex(trace["calculation_trace_sha256"], r"^[A-F0-9]{64}$")
            definition_payload = json.dumps(
                trace["formula_definition"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            self.assertEqual(
                trace["formula_definition_sha256"],
                hashlib.sha256(definition_payload).hexdigest().upper(),
            )
            trace_payload = dict(trace)
            claimed_trace_sha256 = trace_payload.pop("calculation_trace_sha256")
            canonical_trace = json.dumps(
                trace_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            self.assertEqual(
                claimed_trace_sha256,
                hashlib.sha256(canonical_trace).hexdigest().upper(),
            )
            implementation = trace["formula_definition"]["implementation_binding"]
            self.assertEqual(
                implementation["implementation_ref"],
                "scripts/equipment_design_match.py#run_calculations",
            )
            self.assertEqual(
                implementation["binding_status"],
                "SOURCE_FILE_MATCHES_MANIFEST",
            )
            self.assertRegex(implementation["source_file_sha256"], r"^[A-F0-9]{64}$")
            self.assertTrue(trace["formula_definition"]["source_bindings"])
            self.assertTrue(trace["input_bindings"])

        hydraulic = calculations["pump_hydraulic_power"]["formula_trace"]
        input_by_field = {
            item["field_id"]: item
            for item in hydraulic["input_bindings"]
        }
        self.assertEqual(input_by_field["flow_m3_h"]["value"], 36.0)
        self.assertEqual(input_by_field["flow_m3_h"]["unit"], "m3/h")
        self.assertEqual(
            input_by_field["head_m"]["source_kind"],
            "upstream_registered_calculation",
        )
        self.assertEqual(
            input_by_field["head_m"]["upstream_formula_trace_sha256"],
            calculations["pump_head_from_pressure"]["formula_trace"][
                "calculation_trace_sha256"
            ],
        )
        self.assertIn(
            "input_source_provenance_open:flow_m3_h",
            hydraulic["open_traceability_gaps"],
        )
        source = hydraulic["formula_definition"]["source_bindings"][0]
        self.assertEqual(source["binding_status"], "FILE_AND_ANCHOR_BOUND")
        self.assertRegex(source["source_file_sha256"], r"^[A-F0-9]{64}$")

    def test_formula_definition_hash_is_stable_while_input_changes_trace_hash(self) -> None:
        base_payload = {
            "equipment_tag": "P-TRACE-HASH",
            "process_function": "liquid pressure boosting",
            "pressure_basis": "absolute",
            "density_kg_m3": 850.0,
            "inlet_pressure_mpa": 0.12,
            "outlet_pressure_mpa": 0.50,
            "efficiency_percent": 72.0,
        }
        first = app_core.manual_match(
            "block:PUMP",
            {**base_payload, "flow_m3_h": 36.0},
        )["result"]
        second = app_core.manual_match(
            "block:PUMP",
            {**base_payload, "flow_m3_h": 40.0},
        )["result"]
        first_trace = next(
            item["formula_trace"]
            for item in first["calculations"]
            if item["calculation_id"] == "pump_hydraulic_power"
        )
        second_trace = next(
            item["formula_trace"]
            for item in second["calculations"]
            if item["calculation_id"] == "pump_hydraulic_power"
        )
        self.assertEqual(
            first_trace["formula_definition_sha256"],
            second_trace["formula_definition_sha256"],
        )
        self.assertNotEqual(
            first_trace["calculation_trace_sha256"],
            second_trace["calculation_trace_sha256"],
        )
        self.assertNotEqual(
            next(
                item["field_value_sha256"]
                for item in first_trace["input_bindings"]
                if item["field_id"] == "flow_m3_h"
            ),
            next(
                item["field_value_sha256"]
                for item in second_trace["input_bindings"]
                if item["field_id"] == "flow_m3_h"
            ),
        )

    def test_every_registered_calculation_has_an_explicit_source_policy(self) -> None:
        self.assertEqual(
            set(app_core.matcher.CALCULATION_REQUIREMENTS),
            set(app_core.matcher.CALCULATION_POLICIES),
        )
        for calculation_id, policy in app_core.matcher.CALCULATION_POLICIES.items():
            with self.subTest(calculation_id=calculation_id):
                self.assertTrue(policy.get("formula_id"))
                self.assertTrue(policy.get("applicability"))
                self.assertTrue(policy.get("source_refs"))
                self.assertTrue(policy.get("does_not_prove"))

    def test_solids_aspen_block_types_end_in_registered_preliminary_forms(self) -> None:
        cases = {
            "CRYSTALLIZER": (
                "family_reactor_vessel_separator",
                "连续结晶器（预设计）",
                "DEFAULTED_TERMINAL_TYPE_SELECTED",
                True,
            ),
            "FILTER": (
                "family_package_equipment",
                "自动厢式压滤机",
                "PROGRAMMATIC_TERMINAL_TYPE_SELECTED",
                False,
            ),
            "DRYER": (
                "family_package_equipment",
                "连续带式热风干燥器",
                "PROGRAMMATIC_TERMINAL_TYPE_SELECTED",
                False,
            ),
        }
        for block_type, (
            family_id,
            recommended_type,
            terminal_status,
            default_applied,
        ) in cases.items():
            with self.subTest(block_type=block_type):
                result = app_core.manual_match(
                    f"block:{block_type}",
                    {
                        "equipment_tag": f"TEST-{block_type}",
                        "aspen_block_type": block_type,
                        "pressure_basis": "absolute",
                        "operating_pressure_mpa": 0.1,
                        "temperature_c": 25.0,
                    },
                )["result"]
                self.assertEqual(result["match"]["family_id"], family_id)
                terminal = result["model_recommendation"]["terminal_selection"]
                self.assertEqual(terminal["recommended_type"], recommended_type)
                self.assertEqual(terminal["status"], terminal_status)
                self.assertEqual(terminal["default_applied"], default_applied)
                self.assertEqual(terminal["evidence_class"], "J")
                self.assertTrue(terminal["provisional"])
                self.assertFalse(terminal["formal_model"])
                self.assertFalse(terminal["is_vendor_model"])

    def test_pump_power_helpers_have_no_silent_water_density_default(self) -> None:
        with self.assertRaises(TypeError):
            equipment_calc.pump_hydraulic_power_kw(10.0, 20.0)
        with self.assertRaises(TypeError):
            equipment_calc.pump_shaft_power_kw(10.0, 20.0, 75.0)

    def test_exchanger_area_uses_visible_fallback_f_and_preserves_provided_area(self) -> None:
        missing_f = app_core.manual_match("family:family_fixed_tubesheet_exchanger", {
            "heat_duty_kw": 1000,
            "overall_u_w_m2k": 500,
            "lmtd_k": 40,
        })["result"]
        self.assertAlmostEqual(missing_f["derived_parameters"]["heat_transfer_area_m2"], 58.8235294117647)
        fallback = next(
            item for item in missing_f["design_fallbacks"]
            if item["field_id"] == "lmtd_correction_factor"
        )
        self.assertEqual(fallback["value"], 0.85)
        self.assertEqual(fallback["evidence_class"], "J")
        self.assertEqual(fallback["promotion_cap"], "TYPE_SCREENING")

        with_f = app_core.manual_match("family:family_fixed_tubesheet_exchanger", {
            "heat_duty_kw": 1000,
            "overall_u_w_m2k": 500,
            "lmtd_k": 40,
            "lmtd_correction_factor": 0.9,
        })["result"]
        self.assertAlmostEqual(with_f["derived_parameters"]["heat_transfer_area_m2"], 55.55555555555556)
        area_calc = next(item for item in with_f["calculations"] if item["calculation_id"] == "exchanger_area")
        self.assertEqual(area_calc["formula_chain"]["formula"], "abs(Q)/(U*F*LMTD)")
        self.assertEqual(area_calc["calculation_notice"]["promotion_cap"], "TYPE_SCREENING")

        supplied = app_core.manual_match("family:family_fixed_tubesheet_exchanger", {
            "heat_duty_kw": 1000,
            "overall_u_w_m2k": 500,
            "lmtd_k": 40,
            "lmtd_correction_factor": 0.9,
            "heat_transfer_area_m2": 60,
        })["result"]
        self.assertNotIn("heat_transfer_area_m2", supplied["derived_parameters"])
        selected = supplied["design_parameter_package"]["selection_context"]["values"]
        self.assertEqual(selected["heat_transfer_area_m2"], 60.0)
        area_calc = next(item for item in supplied["calculations"] if item["calculation_id"] == "exchanger_area")
        self.assertEqual(area_calc["status"], "PROVISIONAL_SCREENING_DIFFERENCE")

    def test_knowledge_search_returns_structured_vector_hits(self) -> None:
        vector_rows = [{
            "vector_id": "abc123",
            "source_path": "knowledge_graph/pump.md",
            "title": "Pump evidence boundary",
            "text": "Q-H-eta and NPSH are required.",
            "score": 0.91,
        }]
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(vector_rows, ensure_ascii=False),
            stderr="",
        )
        with tempfile.TemporaryDirectory() as temporary:
            workspace_root = Path(temporary)
            vector_script = (
                workspace_root
                / "scripts"
                / "query_workspace_vectors.py"
            )
            vector_script.parent.mkdir(parents=True)
            vector_script.write_text(
                "# deterministic unit-test placeholder\n",
                encoding="utf-8",
            )
            with (
                patch.object(
                    app_core,
                    "WORKSPACE_ROOT",
                    workspace_root,
                ),
                patch.object(
                    app_core.subprocess,
                    "run",
                    return_value=completed,
                ) as mocked,
            ):
                result = app_core.knowledge_search("泵 NPSH", limit=3)
        self.assertEqual(result["status"], "PASS_VECTOR_INDEX")
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["hits"][0]["source_path"], "knowledge_graph/pump.md")
        self.assertEqual(result["hits"][0]["rank"], 1)
        self.assertIn("--json", mocked.call_args.args[0])

    def test_knowledge_search_is_limited_to_allowlisted_packages(self) -> None:
        vector_rows = [
            {"source_path": "设备设计选型工作包/knowledge_graph/pump.md", "title": "core", "text": "core"},
            {"source_path": "设备设计选型工作包/knowledge_graph/standards_graph/standard.md", "title": "standard", "text": "standard"},
            {"source_path": "设备选型一览表_知识图谱重构_20260712/knowledge_graph/model.md", "title": "model", "text": "model"},
        ]
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(vector_rows, ensure_ascii=False), stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            workspace_root = Path(temporary)
            vector_script = (
                workspace_root
                / "scripts"
                / "query_workspace_vectors.py"
            )
            vector_script.parent.mkdir(parents=True)
            vector_script.write_text(
                "# deterministic unit-test placeholder\n",
                encoding="utf-8",
            )
            patches = (
                patch.object(
                    app_core,
                    "WORKSPACE_ROOT",
                    workspace_root,
                ),
                patch.object(
                    app_core.subprocess,
                    "run",
                    return_value=completed,
                ),
            )
            with patches[0], patches[1]:
                result = app_core.knowledge_search(
                    "泵",
                    limit=3,
                    package_ids=["equipment_model_authority"],
                )
            with (
                patch.object(
                    app_core,
                    "WORKSPACE_ROOT",
                    workspace_root,
                ),
                patch.object(
                    app_core.subprocess,
                    "run",
                    return_value=completed,
                ),
            ):
                core_only = app_core.knowledge_search(
                    "泵",
                    limit=3,
                    package_ids=["equipment_core"],
                )
        self.assertEqual(result["selected_packages"], ["equipment_model_authority"])
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["hits"][0]["title"], "model")
        self.assertEqual([item["title"] for item in core_only["hits"]], ["core"])
        with self.assertRaisesRegex(ValueError, "未知知识包"):
            app_core.knowledge_search("泵", package_ids=["untrusted_external_pack"])

    def test_standards_sqlite_is_a_queryable_compact_authority_carrier(self) -> None:
        standards_root = app_core.PACKAGE_ROOT / "knowledge_graph" / "standards_graph"
        hits = app_core._standards_sqlite_search("管板", 3, standards_root)
        self.assertTrue(hits)
        self.assertTrue(all(item["source"] == "standards_sqlite_authority" for item in hits))
        self.assertTrue(all(item["source_pdf_sha256"] for item in hits))
        self.assertTrue(all(item["page_1based"] >= 1 for item in hits))
        self.assertTrue(all("reuse_boundary" in item for item in hits))

    def test_bootstrap_reports_runtime_bundle_verification(self) -> None:
        response = EquipmentDesignApi().bootstrap()
        self.assertTrue(response["ok"], response)
        verification = response["value"]["runtime_bundle"]
        self.assertTrue(verification["verified"])
        self.assertIn(
            verification["verification_status"],
            {"PASS", "NOT_APPLICABLE_SOURCE_TREE"},
        )
        self.assertTrue(verification["source_code_manifest"]["verified"])
        self.assertEqual(
            verification["source_code_manifest"]["status"],
            "SOURCE_TREE_VERIFIED",
        )

    def test_source_code_manifest_failure_is_fail_closed(self) -> None:
        with patch.object(
            app_core,
            "runtime_bundle_verification",
            return_value={"verified": True, "verification_status": "PASS"},
        ), patch.object(
            app_core,
            "source_code_manifest_verification",
            return_value={
                "verified": False,
                "issues": [{"code": "SOURCE_CODE_FILE_HASH_MISMATCH"}],
            },
        ):
            with self.assertRaises(app_core.source_code_manifest.SourceCodeManifestError):
                app_core.require_runtime_bundle()

    def test_cancel_active_operations_touches_only_registered_worker_tree(self) -> None:
        api = EquipmentDesignApi()

        class FakeProcess:
            pid = 43210
            alive = True

            def poll(self):
                return None if self.alive else 0

        process = FakeProcess()
        api._register_worker(process)  # type: ignore[arg-type]

        def kill(candidate):
            self.assertIs(candidate, process)
            process.alive = False

        with patch.object(api, "_kill_worker_tree", side_effect=kill) as mocked:
            result = api.cancel_active_operations()
        mocked.assert_called_once_with(process)
        self.assertEqual(result["terminated_worker_pids"], [43210])
        self.assertFalse(result["preexisting_user_aspen_processes_touched"])
        self.assertEqual(api.active_worker_count(), 0)
        api._unregister_worker(process)  # type: ignore[arg-type]

    def test_llm_proposal_is_allowlisted_and_cannot_override_hard_fields(self) -> None:
        validated = llm_bridge.validate_proposal({
            "summary": "review",
            "changes": [
                {"field": "equipment_type", "value": "centrifugal pump", "reason": "candidate"},
                {"field": "design_pressure_mpa", "value": "2.0", "reason": "invented"},
                {"field": "vendor_model", "value": "X-1", "reason": "invented"},
            ],
        })
        self.assertEqual([item["field"] for item in validated["accepted_changes"]], ["equipment_type"])
        self.assertEqual(len(validated["rejected_changes"]), 2)
        applied = llm_bridge.apply_proposal({"equipment_tag": "P-101"}, validated)
        self.assertEqual(applied["equipment_type"], "centrifugal pump")
        self.assertNotIn("design_pressure_mpa", applied)

    def test_llm_provider_timeout_metadata_never_echoes_key(self) -> None:
        response_body = {
            "choices": [{"message": {"content": json.dumps({"summary": "ok", "changes": []})}}]
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read() -> bytes:
                return json.dumps(response_body).encode("utf-8")

        with patch.object(llm_bridge, "_open_authenticated_request", return_value=FakeResponse()) as mocked:
            result = llm_bridge.request_review(
                {
                    "provider": "openai_compatible",
                    "base_url": "https://example.invalid/v1",
                    "model": "review-model",
                    "timeout_s": 17,
                    "api_key": "TOP-SECRET-KEY",
                },
                {"status": "MATCHED"},
                {"selected_packages": ["equipment_core"], "hits": []},
            )
        self.assertEqual(mocked.call_args.kwargs["timeout"], 17)
        self.assertEqual(result["provider"], "openai_compatible")
        self.assertEqual(result["timeout_s"], 17)
        self.assertFalse(result["api_key_persisted"])
        self.assertNotIn("TOP-SECRET-KEY", json.dumps(result))

    def test_llm_connection_check_uses_exact_model_and_redacts_key(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read() -> bytes:
                return b'{"choices":[{"message":{"content":"{\\"status\\":\\"pong\\"}"}}]}'

        with patch.object(llm_bridge, "_open_authenticated_request", return_value=FakeResponse()) as mocked:
            result = llm_bridge.test_provider_connection({
                "provider": "openai_compatible",
                "base_url": "https://example.invalid/v1",
                "model": "exact-model-id",
                "timeout_s": 17,
                "api_key": "TOP-SECRET-KEY",
            })
        self.assertEqual(result["schema"], "equipment-design-llm-connection-test-v1")
        self.assertEqual(result["status"], "CONNECTED")
        self.assertEqual(result["model_id"], "exact-model-id")
        self.assertEqual(result["endpoint_profile"], "remote_openai_compatible")
        self.assertEqual(mocked.call_args.kwargs["timeout"], 17)
        request_body = json.loads(mocked.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(request_body["model"], "exact-model-id")
        self.assertEqual(result["functional_probe"], "JSON_OBJECT_STATUS_PONG")
        self.assertNotIn("TOP-SECRET-KEY", json.dumps(result))

    def test_llm_connection_rejects_empty_or_wrong_function_probe(self) -> None:
        class FakeResponse:
            def __init__(self, content: str) -> None:
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return json.dumps({
                    "choices": [{"message": {"content": self.content}}],
                }).encode("utf-8")

        for label, content in (
            ("empty", ""),
            ("wrong_json", '{"status":"not-ready"}'),
            ("plain_text", "pong"),
        ):
            with self.subTest(label=label), patch.object(
                llm_bridge,
                "_open_authenticated_request",
                return_value=FakeResponse(content),
            ):
                result = llm_bridge.test_provider_connection({
                    "provider": "openai_compatible",
                    "base_url": "https://example.invalid/v1",
                    "model": "exact-model-id",
                    "api_key": "TOP-SECRET-KEY",
                })
            self.assertEqual(result["status"], "FAILED")
            self.assertNotIn("TOP-SECRET-KEY", json.dumps(result))

    def test_authenticated_llm_request_does_not_follow_redirect_or_forward_key(self) -> None:
        redirect_hits: list[str | None] = []
        target_hits: list[str | None] = []
        expected_key = "LOCAL-REDIRECT-SECRET"

        class TargetHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                target_hits.append(self.headers.get("Authorization"))
                response_body = b'{"choices":[{"message":{"content":"{\\"status\\":\\"pong\\"}"}}]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        target_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
        target_worker = threading.Thread(target=target_server.serve_forever, daemon=True)
        target_worker.start()
        self.addCleanup(target_server.server_close)
        self.addCleanup(target_server.shutdown)
        target_url = f"http://127.0.0.1:{target_server.server_port}/captured"

        class RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                redirect_hits.append(self.headers.get("Authorization"))
                self.send_response(302)
                self.send_header("Location", target_url)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, _format: str, *_args: object) -> None:
                return

        redirect_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        redirect_worker = threading.Thread(target=redirect_server.serve_forever, daemon=True)
        redirect_worker.start()
        self.addCleanup(redirect_server.server_close)
        self.addCleanup(redirect_server.shutdown)

        result = llm_bridge.test_provider_connection({
            "provider": "local_openai_compatible",
            "base_url": f"http://127.0.0.1:{redirect_server.server_port}/v1",
            "model": "redirect-test-model",
            "wire_api": "chat_completions",
            "timeout_s": 15,
            "api_key": expected_key,
        })

        self.assertEqual(result["status"], "FAILED")
        self.assertIn("302", result["message"])
        self.assertEqual(redirect_hits, [f"Bearer {expected_key}"])
        self.assertEqual(target_hits, [])
        self.assertNotIn(expected_key, json.dumps(result))

    def test_user_entered_url_key_and_model_complete_real_http_round_trip(self) -> None:
        received: list[dict[str, object]] = []
        expected_key = "LOCAL-ROUNDTRIP-SECRET"

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                length = int(self.headers.get("Content-Length", "0"))
                request_body = json.loads(self.rfile.read(length).decode("utf-8"))
                received.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": request_body,
                })
                last_message = request_body.get("messages", [{}])[-1].get("content", "")
                if '"status":"pong"' in last_message:
                    content = json.dumps({"status": "pong"})
                else:
                    content = json.dumps({
                        "summary": "URL、Key 和模型配置已完成真实 HTTP 往返。",
                        "recommended_action": "review",
                        "changes": [],
                    }, ensure_ascii=False)
                response_body = json.dumps({
                    "choices": [{"message": {"content": content}}],
                }, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base_url = f"http://127.0.0.1:{server.server_port}/user-supplied/v1"
        config = {
            "provider": "openai_compatible",
            "base_url": base_url,
            "model": "user-selected-model",
            "wire_api": "chat_completions",
            "timeout_s": 15,
            "api_key": expected_key,
        }

        connection = EquipmentDesignApi().test_llm_connection(config)
        review = llm_bridge.request_review(
            config,
            {"status": "MATCHED", "candidate_model": "PROGRAM-CANDIDATE"},
        )

        self.assertTrue(connection["ok"], connection)
        self.assertEqual(connection["value"]["status"], "CONNECTED")
        self.assertEqual(review["proposal"]["summary"], "URL、Key 和模型配置已完成真实 HTTP 往返。")
        self.assertEqual(len(received), 2)
        for item in received:
            self.assertEqual(item["path"], "/user-supplied/v1/chat/completions")
            self.assertEqual(item["authorization"], f"Bearer {expected_key}")
            self.assertEqual(item["body"]["model"], "user-selected-model")
        self.assertNotIn(expected_key, json.dumps([connection, review], ensure_ascii=False))

    def test_user_entered_url_key_and_model_reach_full_agent_real_http_round_trip(self) -> None:
        received: list[dict[str, object]] = []
        expected_key = "LOCAL-FULL-AGENT-SECRET"

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                length = int(self.headers.get("Content-Length", "0"))
                request_body = json.loads(self.rfile.read(length).decode("utf-8"))
                received.append({
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": request_body,
                })
                user_payload = json.loads(request_body["messages"][-1]["content"])
                step_output = {
                    "schema": llm_bridge.STEP_OUTPUT_SCHEMA,
                    "injection_point": user_payload["active_output_policy"]["injection_point"],
                    "context_sha256": user_payload["context_pack"]["context_sha256"],
                    "summary": "完整 Agent HTTP 往返成功。",
                    "citations": [],
                    "proposed_changes": [],
                    "condition_assessments": [],
                    "terminal_selection_assists": [],
                    "engineering_choice_assists": [],
                    "calculation_assists": [],
                    "retrieval_plan": [],
                    "ambiguity_decision": None,
                    "audit_findings": [],
                    "output_composition": {
                        "title": "辅助计算结果",
                        "blocks": [{
                            "block_id": "summary",
                            "operation": llm_bridge.OUTPUT_SECTION_OPERATIONS["summary"],
                            "section_ref": "summary",
                            "heading": "AI 摘要",
                            "citations": ["deterministic_result"],
                        }],
                    },
                }
                response_body = json.dumps({
                    "choices": [{
                        "message": {
                            "content": json.dumps(step_output, ensure_ascii=False),
                        },
                    }],
                }, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base_url = f"http://127.0.0.1:{server.server_port}/user-supplied/v1"
        source_input = {
            "operation": "manual_match",
            "payload": {
                "selection_id": "block:PUMP",
                "values": {
                    "equipment_tag": "P-USER-LLM",
                    "phase": "liquid",
                    "flow_m3_h": 20,
                    "head_m": 45,
                    "density_kg_m3": 900,
                    "efficiency_percent": 75,
                },
            },
        }
        results: list[dict[str, object]] = []
        with patch.dict(os.environ, {
            "EQUIPMENT_DESIGN_LLM_API_KEY": "PREVIOUS-RUNTIME-KEY",
            "EQUIPMENT_DESIGN_LLM_BASE_URL": "https://previous.invalid/v1",
        }, clear=False):
            for provider in ("openai_compatible", "local_openai_compatible"):
                with self.subTest(provider=provider):
                    response = EquipmentDesignApi().agent_hybrid_run(
                        source_input,
                        {
                            "enabled": True,
                            "provider": provider,
                            "base_url": base_url,
                            "model": "user-selected-agent-model",
                            "wire_api": "chat_completions",
                            "timeout_s": 15,
                            "api_key": expected_key,
                        },
                        {"enabled": False},
                        "audit",
                        "minimum",
                    )
                    self.assertTrue(response["ok"], response)
                    value = response["value"]
                    self.assertEqual(value["schema"], "equipment-design-hybrid-result-v2")
                    self.assertNotEqual(value["machine_state"]["state"], "FALLBACK_DETERMINISTIC")
                    self.assertEqual(value["llm_review"]["status"], "COMPLETED_STRICT")
                    self.assertEqual(
                        value["orchestration"]["step_output"]["summary"],
                        "完整 Agent HTTP 往返成功。",
                    )
                    results.append(response)
            self.assertEqual(
                os.environ["EQUIPMENT_DESIGN_LLM_API_KEY"],
                "PREVIOUS-RUNTIME-KEY",
            )
            self.assertEqual(
                os.environ["EQUIPMENT_DESIGN_LLM_BASE_URL"],
                "https://previous.invalid/v1",
            )

        self.assertEqual(len(received), 2)
        for item in received:
            self.assertEqual(item["path"], "/user-supplied/v1/chat/completions")
            self.assertEqual(item["authorization"], f"Bearer {expected_key}")
            self.assertEqual(item["body"]["model"], "user-selected-agent-model")
        self.assertNotIn(expected_key, json.dumps(results, ensure_ascii=False))

    def test_disabled_llm_never_rebinds_unused_runtime_secret(self) -> None:
        observed: list[dict[str, object]] = []
        unused_key = "UNUSED-DISABLED-RUNTIME-SECRET"

        def fake_execute(operation, payload, _api):
            observed.append({
                "operation": operation,
                "payload": payload,
                "runtime_key": os.environ.get("EQUIPMENT_DESIGN_LLM_API_KEY"),
                "runtime_base": os.environ.get("EQUIPMENT_DESIGN_LLM_BASE_URL"),
            })
            return {
                "schema": "equipment-design-hybrid-result-v2",
                "machine_state": {"state": "COMPLETED_DETERMINISTIC_ONLY"},
            }, []

        with patch.dict(os.environ, {
            "EQUIPMENT_DESIGN_LLM_API_KEY": "PREVIOUS-RUNTIME-KEY",
            "EQUIPMENT_DESIGN_LLM_BASE_URL": "https://previous.invalid/v1",
        }, clear=False), patch(
            "equipment_design_agent.execute_operation",
            side_effect=fake_execute,
        ):
            response = EquipmentDesignApi().agent_hybrid_run(
                {
                    "operation": "manual_match",
                    "payload": {
                        "selection_id": "block:PUMP",
                        "values": {"flow_m3_h": 20},
                    },
                },
                {
                    "enabled": False,
                    "provider": "openai_compatible",
                    "base_url": "https://unused.invalid/v1",
                    "model": "unused-model",
                    "api_key": unused_key,
                },
                {"enabled": False},
            )

        self.assertTrue(response["ok"], response)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["runtime_key"], "PREVIOUS-RUNTIME-KEY")
        self.assertEqual(observed[0]["runtime_base"], "https://previous.invalid/v1")
        self.assertFalse(observed[0]["payload"]["llm"]["enabled"])
        self.assertNotIn(
            unused_key,
            json.dumps([observed[0]["payload"], response], ensure_ascii=False),
        )

    def test_deepseek_connection_uses_official_chat_endpoint_and_current_model(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read() -> bytes:
                return b'{"choices":[{"message":{"content":"{\\"status\\":\\"pong\\"}"}}]}'

        with patch.object(llm_bridge, "_open_authenticated_request", return_value=FakeResponse()) as mocked:
            result = llm_bridge.test_provider_connection({
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
                "wire_api": "chat_completions",
                "reasoning_effort": "high",
                "timeout_s": 19,
                "api_key": "TOP-SECRET-KEY",
            })

        request = mocked.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["max_tokens"], 128)
        self.assertNotIn("temperature", payload)
        self.assertEqual(result["status"], "CONNECTED")
        self.assertEqual(result["provider"], "deepseek")
        self.assertNotIn("TOP-SECRET-KEY", json.dumps(result))

    def test_deepseek_rejects_responses_protocol_and_nonofficial_host(self) -> None:
        unsupported = llm_bridge.test_provider_connection({
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "wire_api": "responses",
            "api_key": "TOP-SECRET-KEY",
        })
        wrong_host = llm_bridge.test_provider_connection({
            "provider": "deepseek",
            "base_url": "https://attacker.invalid",
            "model": "deepseek-v4-flash",
            "wire_api": "chat_completions",
            "api_key": "TOP-SECRET-KEY",
        })
        self.assertEqual(unsupported["status"], "FAILED")
        self.assertIn("不支持 responses", unsupported["message"])
        self.assertEqual(wrong_host["status"], "FAILED")
        self.assertIn("api.deepseek.com", wrong_host["message"])
        self.assertNotIn("TOP-SECRET-KEY", json.dumps([unsupported, wrong_host]))

    def test_llm_responses_connection_uses_reasoning_and_disables_storage(self) -> None:
        response_body = {
            "id": "resp_test",
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": '{"status":"pong"}'}],
            }],
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read() -> bytes:
                return json.dumps(response_body).encode("utf-8")

        with patch.object(llm_bridge, "_open_authenticated_request", return_value=FakeResponse()) as mocked:
            result = llm_bridge.test_provider_connection({
                "provider": "openai_compatible",
                "base_url": "https://example.invalid/v1",
                "model": "reasoning-model",
                "wire_api": "responses",
                "reasoning_effort": "xhigh",
                "disable_response_storage": True,
                "timeout_s": 23,
                "api_key": "TOP-SECRET-KEY",
            })

        request = mocked.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertTrue(request.full_url.endswith("/v1/responses"))
        self.assertEqual(
            payload["input"],
            'Return exactly one JSON object with no markdown: {"status":"pong"}',
        )
        self.assertEqual(payload["reasoning"], {"effort": "xhigh"})
        self.assertFalse(payload["store"])
        self.assertEqual(payload["max_output_tokens"], 128)
        self.assertNotIn("messages", payload)
        self.assertNotIn("temperature", payload)
        self.assertEqual(result["status"], "CONNECTED")
        self.assertEqual(result["wire_api"], "responses")
        self.assertEqual(result["reasoning_effort"], "xhigh")
        self.assertTrue(result["response_storage_disabled"])
        self.assertNotIn("TOP-SECRET-KEY", json.dumps(result))

    def test_llm_review_parses_responses_output_text(self) -> None:
        review = {
            "summary": "确定性结果保持不变",
            "recommended_action": "review",
            "changes": [],
        }
        response_body = {
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(review, ensure_ascii=False)}],
            }],
        }
        captured: dict[str, object] = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read() -> bytes:
                return json.dumps(response_body, ensure_ascii=False).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        with patch.object(llm_bridge, "_open_authenticated_request", side_effect=fake_urlopen):
            result = llm_bridge.request_review(
                {
                    "provider": "openai_compatible",
                    "base_url": "https://example.invalid/v1/responses",
                    "model": "reasoning-model",
                    "wire_api": "responses",
                    "reasoning_effort": "high",
                    "disable_response_storage": True,
                    "timeout_s": 29,
                    "api_key": "TOP-SECRET-KEY",
                },
                {"status": "MATCHED"},
            )

        self.assertEqual(captured["url"], "https://example.invalid/v1/responses")
        self.assertEqual(captured["timeout"], 29)
        payload = captured["payload"]
        self.assertIn("instructions", payload)
        self.assertIn("input", payload)
        self.assertEqual(payload["reasoning"], {"effort": "high"})
        self.assertFalse(payload["store"])
        self.assertEqual(result["proposal"]["summary"], "确定性结果保持不变")
        self.assertEqual(result["wire_api"], "responses")
        self.assertNotIn("TOP-SECRET-KEY", json.dumps(result))

    def test_mock_connection_check_never_accesses_network_and_api_wraps_it(self) -> None:
        with patch.object(llm_bridge, "_open_authenticated_request", side_effect=AssertionError("network used")) as mocked:
            result = EquipmentDesignApi().test_llm_connection({"provider": "mock", "model": "offline-check"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"]["status"], "CONNECTED")
        mocked.assert_not_called()

    def test_knowledge_catalog_groups_deterministic_field_directory(self) -> None:
        result = EquipmentDesignApi().knowledge_catalog()
        self.assertTrue(result["ok"])
        catalog = result["value"]
        self.assertEqual(catalog["schema"], "equipment-design-knowledge-catalog-v1")
        self.assertTrue(catalog["families"])
        pump = next(item for item in catalog["families"] if item["family_id"] == "family_pump")
        field = next(item for group in pump["topics"] for item in group["fields"] if item["canonical_id"] == "flow_m3_h")
        self.assertTrue(field["label"])
        self.assertTrue(field["unit"])
        self.assertIn("manual_role", field)
        self.assertIn("evidence_boundary", field)
        self.assertIn("flow_m3_h", field["aliases"])
        self.assertIn("flow_m3_h", field["query_template"])

    def test_staged_hybrid_run_uses_strict_continue_contract(self) -> None:
        values = {
            "equipment_tag": "P-HYBRID",
            "phase": "liquid",
            "flow_m3_h": 20,
            "head_m": 45,
            "density_kg_m3": 900,
            "efficiency_percent": 75,
        }
        source_input = {
            "operation": "manual_match",
            "payload": {"selection_id": "block:PUMP", "values": values},
        }
        deterministic = app_core.manual_match("block:PUMP", values)
        api = EquipmentDesignApi()

        def strict_run(_config, prepared):
            output = {
                "schema": llm_bridge.STEP_OUTPUT_SCHEMA,
                "injection_point": "audit",
                "context_sha256": prepared["context_pack"]["context_sha256"],
                "summary": "strict audit completed",
                "citations": [],
                "proposed_changes": [],
                "condition_assessments": [],
                "calculation_assists": [],
                "retrieval_plan": [],
                "ambiguity_decision": None,
                "audit_findings": [],
                "output_composition": {
                    "title": "Strict audit",
                    "blocks": [{
                        "block_id": "summary",
                        "operation": "explain_result",
                        "section_ref": "summary",
                        "heading": "Summary",
                        "citations": ["deterministic_result"],
                    }],
                },
            }
            return llm_bridge.hybrid_continue(prepared, output)

        with patch.object(llm_bridge, "request_review", side_effect=AssertionError("legacy path used")), patch.object(
            llm_bridge, "hybrid_run", side_effect=strict_run
        ) as run_mock:
            response = api.staged_hybrid_run(
                {
                    "enabled": True,
                    "provider": "mock",
                },
                source_input,
                {"enabled": False},
            )
        self.assertTrue(response["ok"])
        value = response["value"]
        self.assertEqual(value["schema"], "equipment-design-hybrid-result-v2")
        self.assertEqual(
            value["machine_state"]["state"],
            "COMPLETED_HYBRID_SELECTION_COMPLETE",
        )
        self.assertEqual(value["deterministic_result"], deterministic)
        self.assertFalse(value["fallback"]["used"])
        self.assertEqual(
            value["llm_review"]["result"]["step_output"]["summary"],
            "strict audit completed",
        )
        self.assertEqual(run_mock.call_count, 1)

    def test_staged_hybrid_run_falls_back_without_losing_deterministic_result(self) -> None:
        values = {
            "equipment_tag": "P-HYBRID-FALLBACK",
            "phase": "liquid",
            "flow_m3_h": 20,
            "head_m": 45,
            "density_kg_m3": 900,
            "efficiency_percent": 75,
        }
        source_input = {
            "operation": "manual_match",
            "payload": {"selection_id": "block:PUMP", "values": values},
        }
        deterministic = app_core.manual_match("block:PUMP", values)
        api = EquipmentDesignApi()
        with patch.object(llm_bridge, "request_review", side_effect=AssertionError("legacy path used")), patch.object(
            llm_bridge, "hybrid_run", side_effect=RuntimeError("provider offline TOP-SECRET-KEY")
        ):
            response = api.staged_hybrid_run(
                {"enabled": True, "provider": "mock", "api_key": "TOP-SECRET-KEY"},
                source_input,
                {"enabled": False},
            )
        self.assertTrue(response["ok"])
        value = response["value"]
        self.assertEqual(value["machine_state"]["state"], "FALLBACK_DETERMINISTIC")
        self.assertEqual(value["deterministic_result"], deterministic)
        self.assertTrue(value["fallback"]["used"])
        self.assertNotIn("TOP-SECRET-KEY", json.dumps(value))

    def test_staged_hybrid_run_does_not_call_provider_when_llm_disabled(self) -> None:
        values = {
            "equipment_tag": "P-NO-LLM",
            "phase": "liquid",
            "flow_m3_h": 20,
            "head_m": 45,
            "density_kg_m3": 900,
            "efficiency_percent": 75,
        }
        source_input = {
            "operation": "manual_match",
            "payload": {"selection_id": "block:PUMP", "values": values},
        }
        deterministic = app_core.manual_match("block:PUMP", values)
        api = EquipmentDesignApi()
        with patch.object(llm_bridge, "request_review", side_effect=AssertionError("legacy path used")), patch.object(
            llm_bridge, "hybrid_run", side_effect=AssertionError("provider must not run")
        ):
            response = api.staged_hybrid_run(
                {"enabled": False}, source_input, {"enabled": False}
            )
        self.assertTrue(response["ok"])
        value = response["value"]
        self.assertEqual(value["machine_state"]["state"], "COMPLETED_DETERMINISTIC_ONLY")
        self.assertEqual(value["deterministic_result"], deterministic)
        self.assertTrue(value["prepared"]["prepared_sha256"])
        self.assertEqual(value["llm_review"]["status"], "NOT_REQUESTED")

    def test_staged_hybrid_run_rejects_naked_deterministic_result(self) -> None:
        api = EquipmentDesignApi()
        response = api.staged_hybrid_run(
            {"enabled": False},
            {"status": "MATCHED", "deterministic": True},
            {"enabled": False},
        )
        self.assertFalse(response["ok"])
        self.assertIn("不接受裸确定性结果", response["error"])

    def test_legacy_api_review_entries_cannot_bypass_staged_wrapper(self) -> None:
        source_input = {
            "operation": "manual_match",
            "payload": {"selection_id": "block:PUMP", "values": {}},
        }
        api = EquipmentDesignApi()
        sentinel = {"ok": True, "value": {"strict_staged_protocol": True}}
        with patch.object(llm_bridge, "request_review", side_effect=AssertionError("legacy path used")), patch.object(
            api, "staged_hybrid_run", return_value=sentinel
        ) as staged:
            self.assertIs(api.llm_review({"enabled": False}, source_input), sentinel)
            self.assertIs(api.hybrid_review({"enabled": False}, source_input), sentinel)
        self.assertEqual(staged.call_count, 2)
        self.assertEqual(staged.call_args_list[0].args[3:], ("audit", "minimum"))

    def test_manual_pipe_uses_default_hydraulics_and_feeds_design_conditions_to_connections(
        self,
    ) -> None:
        result = app_core.manual_match(
            "family:family_process_piping",
            {
                "equipment_tag": "L-DEFAULT-HYD",
                "main_medium": "water",
                "phase": "liquid",
                "flow_m3_h": 20.0,
                "design_temperature_c": 60.0,
                "design_pressure_mpa": 0.3,
                "target_velocity_m_s": 1.5,
                "material": "20",
            },
        )
        specification = result["result"][
            "programmatic_pipe_specification"
        ]
        self.assertEqual(
            specification["status"],
            "PRELIMINARY_CONCRETE_SPECIFICATION_SELECTED",
        )
        self.assertEqual(
            specification["hydraulic_property_input_ledger"][
                "default_fields"
            ],
            ["density_kg_m3", "dynamic_viscosity_mpa_s"],
        )
        self.assertIn(
            "水力学缺失值已由默认参数包补齐",
            specification["designation"],
        )
        self.assertEqual(
            specification["material_standard_table_route"]["status"],
            "STANDARD_TABLE_FOUND_NUMERIC_REUSE_BLOCKED",
        )
        connection = result["connection_component_selections"][
            "connections"
        ][0]
        raw_context = connection["raw_service_context"]
        self.assertEqual(raw_context["temperature_c"], 60.0)
        self.assertEqual(
            raw_context["temperature_origin"],
            "PROGRAMMATIC_PIPE_DESIGN_TEMPERATURE",
        )
        self.assertEqual(raw_context["pressure_mpa"], 0.3)
        self.assertEqual(
            raw_context["pressure_origin"],
            "PROGRAMMATIC_PIPE_DESIGN_PRESSURE",
        )
        self.assertEqual(
            connection["component_types"]["flange_type"][
                "normalized_service_labels"
            ]["temperature_c"],
            60.0,
        )

    def test_manual_pipe_maps_s30408_to_complete_stainless_route(self) -> None:
        result = app_core.manual_match(
            "family:family_process_piping",
            {
                "equipment_tag": "L-S30408",
                "main_medium": "water",
                "phase": "liquid",
                "flow_m3_h": 20.0,
                "design_temperature_c": 60.0,
                "design_pressure_mpa": 0.3,
                "target_velocity_m_s": 1.5,
                "material": "S30408",
            },
        )
        specification = result["result"][
            "programmatic_pipe_specification"
        ]
        fields = specification["fields"]
        selected_material = specification["material_parameter_ledger"][
            "selected_material"
        ]

        self.assertEqual(selected_material["material_code"], "SS304")
        self.assertEqual(
            selected_material["material_grade"],
            "S30408（06Cr19Ni10）",
        )
        self.assertEqual(
            specification["pressure_wall_screening"]["material_code"],
            "SS304",
        )
        standard_route = specification["material_standard_table_route"]
        self.assertEqual(standard_route["material_code"], "SS304")
        self.assertEqual(
            standard_route["status"],
            "STANDARD_TABLE_FOUND_NUMERIC_REUSE_BLOCKED",
        )
        self.assertFalse(standard_route["standard_numeric_value_adopted"])
        self.assertEqual(
            fields["material_grade"]["value"],
            "S30408（06Cr19Ni10）",
        )
        self.assertIn(
            "材料路线=S30408（06Cr19Ni10）",
            specification["designation"],
        )
        self.assertNotIn("材料路线=20钢", specification["designation"])
        self.assertEqual(
            fields["protective_layer"]["value"],
            "酸洗钝化（程序初选）",
        )
        piping_class = specification["piping_class_candidate"]
        self.assertIn("材料分支=SS304", piping_class["branch_explanation"])
        self.assertIn(
            "S30408（06Cr19Ni10）",
            piping_class["components"]["pipe"],
        )
        self.assertIn("CF8", piping_class["components"]["manual_isolation_valve"])
        self.assertFalse(piping_class["formal_project_piping_class"])

if __name__ == "__main__":
    unittest.main()
