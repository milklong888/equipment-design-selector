from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PACKAGE_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import audit_llm_multiflow_bridge as audit


class _FakeApi:
    def __init__(self, baseline: dict, hybrid: dict) -> None:
        self.baseline = baseline
        self.hybrid = hybrid

    def manual_match(self, selection_id: str, values: dict) -> dict:
        return {"ok": True, "value": self.baseline}

    def agent_hybrid_run(self, *args, **kwargs) -> dict:
        return {"ok": True, "value": self.hybrid}


class LlmMultiflowAuditTests(unittest.TestCase):
    def test_cli_has_no_key_value_or_base_url_option(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            audit.parse_args(["--api-key", "secret"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            audit.parse_args(["--base-url", "https://example.com"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            audit.parse_args(["--key-stdin"])

    def test_remote_scope_is_exactly_six_synthetic_cases(self) -> None:
        cases = audit.resolve_cases(remote=True, include_local_aspen=False)
        self.assertEqual(len(cases), 6)
        self.assertEqual(
            {item["case_id"] for item in cases},
            {item["case_id"] for item in audit.SYNTHETIC_CASES},
        )
        with self.assertRaisesRegex(ValueError, "禁止发送"):
            audit.resolve_cases(remote=True, include_local_aspen=True)

    def test_deepseek_endpoint_gate_rejects_nonofficial_destinations(self) -> None:
        self.assertEqual(
            audit._assert_deepseek_endpoint("https://api.deepseek.com/models"),
            "https://api.deepseek.com/models",
        )
        rejected = (
            "http://api.deepseek.com/models",
            "https://api.deepseek.com.evil.example/models",
            "https://api.deepseek.com:8443/models",
            "https://user@api.deepseek.com/models",
            "https://api.deepseek.com/models?next=evil",
        )
        for endpoint in rejected:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                audit._assert_deepseek_endpoint(endpoint)

    def test_model_catalog_uses_official_host_and_no_redirect_opener(self) -> None:
        payload = io.BytesIO(
            json.dumps({"data": [{"id": "deepseek-chat"}]}).encode("utf-8")
        )
        with patch.object(
            audit.llm_bridge,
            "_open_authenticated_request",
            return_value=payload,
        ) as opened:
            models = audit.request_models("memory-only-secret")
        self.assertEqual(models, ["deepseek-chat"])
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/models")
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer memory-only-secret",
        )

    def test_redacted_report_never_writes_or_returns_key(self) -> None:
        key = "sk-test-memory-only-1234567890"
        report = {
            "error": f"request Bearer {key} failed",
            "nested": [{"message": key}],
        }
        serialized = audit.serialize_redacted(report, key)
        self.assertNotIn(key, serialized)
        self.assertIn("[REDACTED]", serialized)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "audit.json"
            audit.write_redacted_report(output, report, key)
            persisted = output.read_text(encoding="utf-8")
        self.assertNotIn(key, persisted)
        self.assertIn("[REDACTED]", persisted)

    def test_case_checks_hash_concrete_type_input_preservation_and_formal_gaps(self) -> None:
        case = {
            "case_id": "pump",
            "label": "泵",
            "selection_id": "block:PUMP",
            "values": {"equipment_tag": "P-1", "material": "S30408"},
        }
        baseline = {
            "input": dict(case["values"]),
            "result": {
                "status": "MATCHED",
                "match": {"family_id": "family_pump"},
                "model_recommendation": {
                    "family_id": "family_pump",
                    "recommended_type": "轴向吸入离心泵",
                    "leading_candidate": {"designation": "80-50-200"},
                    "formal_model": None,
                    "formal_model_status": "type_selected",
                    "formal_promotion_blockers": ["vendor_curve_path"],
                },
            },
        }
        hybrid = {
            "deterministic_result": baseline,
            "deterministic_recalculation": None,
            "machine_state": {"state": "COMPLETED"},
            "llm_review": {"status": "COMPLETED_STRICT"},
            "fallback": {"used": False, "errors": []},
            "orchestration": {
                "step_output": {"summary": "已审核，保留确定性泵型。"},
                "verified_calculation_inputs": {},
                "verified_model_estimate_inputs": {},
                "engineering_choice_assist_validation": [],
            },
            "selection_completeness": {"acceptance": "PASS"},
            "knowledge_context": {"hits": []},
        }
        fake = _FakeApi(baseline, hybrid)
        with patch.object(audit, "default_knowledge_config", return_value={}):
            result = audit.run_case(
                fake,
                case,
                {"enabled": True},
                remote=True,
                api_key="memory-only-secret",
            )
        self.assertTrue(result["passed"])
        self.assertTrue(result["deterministic_baseline_exactly_preserved"])
        self.assertTrue(result["concrete_output"])
        self.assertTrue(result["supplied_fields_preserved"])
        self.assertTrue(result["agent_summary_in_chinese"])
        self.assertTrue(result["strict_completion_ok"])
        self.assertTrue(result["fallback_check_ok"])
        self.assertTrue(result["formal_gap_reporting_ok"])

        hybrid["orchestration"]["verified_model_estimate_inputs"] = {
            "material": "S31603"
        }
        with patch.object(audit, "default_knowledge_config", return_value={}):
            overwritten = audit.run_case(
                fake,
                case,
                {"enabled": True},
                remote=True,
                api_key="memory-only-secret",
            )
        self.assertFalse(overwritten["passed"])
        self.assertEqual(overwritten["user_field_overwrites"], ["material"])

    def test_execute_remote_audit_never_loads_local_aspen(self) -> None:
        fake_api = unittest.mock.Mock()
        fake_api.test_llm_connection.return_value = {
            "ok": True,
            "value": {"status": "CONNECTED"},
        }
        observed: list[str] = []

        def fake_run_case(api, case, config, *, remote, api_key):
            observed.append(case["case_id"])
            return {"case_id": case["case_id"], "passed": True}

        with (
            patch.object(audit, "request_models", return_value=["deepseek-chat"]),
            patch.object(audit, "EquipmentDesignApi", return_value=fake_api),
            patch.object(audit, "run_case", side_effect=fake_run_case),
            patch.object(
                audit,
                "load_local_aspen_cases",
                side_effect=AssertionError("local Aspen must not be loaded"),
            ),
        ):
            report, exit_code = audit.execute_audit(
                remote=True,
                include_local_aspen=False,
                api_key="memory-only-secret",
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(observed), 6)
        self.assertTrue(report["summary"]["overall_pass"])
        self.assertFalse(report["security"]["local_aspen_sent_remote"])

    def test_tk_smoke_runs_before_remote_batch_to_preserve_provider_quota(self) -> None:
        fake_api = unittest.mock.Mock()
        fake_api.test_llm_connection.return_value = {
            "ok": True,
            "value": {"status": "CONNECTED"},
        }
        events: list[str] = []

        def fake_tk_smoke(*, api_key, model):
            events.append("tk")
            return {"passed": True}

        def fake_run_case(api, case, config, *, remote, api_key):
            events.append(f"case:{case['case_id']}")
            return {"case_id": case["case_id"], "passed": True}

        with (
            patch.object(audit, "request_models", return_value=["deepseek-chat"]),
            patch.object(audit, "EquipmentDesignApi", return_value=fake_api),
            patch.object(audit, "run_tk_smoke", side_effect=fake_tk_smoke),
            patch.object(audit, "run_case", side_effect=fake_run_case),
        ):
            report, exit_code = audit.execute_audit(
                remote=True,
                include_local_aspen=False,
                api_key="memory-only-secret",
                include_tk_smoke=True,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(events[0], "tk")
        self.assertEqual(len(events), 7)
        self.assertTrue(report["summary"]["tk_smoke_passed"])


if __name__ == "__main__":
    unittest.main()
