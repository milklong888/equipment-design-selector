from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import aspen_suite


class AspenSuiteTests(unittest.TestCase):
    def test_worker_selection_uses_explicit_status_and_coverage_allowlists(self) -> None:
        base = {
            "selection_result_available": True,
            "result": {"status": "PASS"},
        }
        for status in (
            "PASS",
            "PASS_MOCK",
            "BLOCKED_TRANSPORT_PROPERTY_VERIFICATION",
        ):
            self.assertTrue(
                aspen_suite.worker_allows_equipment_selection({
                    **base,
                    "status": status,
                }),
                status,
            )
        for status in ("", "ERROR", "FAILED", "FAILED_PARTIAL", "PASS_UNKNOWN"):
            self.assertFalse(
                aspen_suite.worker_allows_equipment_selection({
                    **base,
                    "status": status,
                }),
                status,
            )

        valid_summary = {
            "registry_completeness_status": "SCORED_REGISTERED_OBJECTS",
            "counts": {"error": 0},
            "unmapped_module_count": 0,
            "unmapped_stream_record_type_count": 0,
            "case_discovery_budget_exhausted": False,
        }
        self.assertTrue(
            aspen_suite.worker_allows_equipment_selection({
                **base,
                "status": "PASS",
                "com_extraction_coverage_summary": valid_summary,
            })
        )
        for invalid_summary in (
            {
                **valid_summary,
                "registry_completeness_status": (
                    "NOT_SCORABLE_UNSUPPORTED_REGISTRY_IDENTITIES"
                ),
                "unmapped_module_count": 1,
            },
            {
                **valid_summary,
                "registry_completeness_status": "REVIEW_OBJECT_DISCOVERY_GAPS",
                "unmapped_stream_record_type_count": 1,
            },
            {
                **valid_summary,
                "registry_completeness_status": (
                    "NOT_SCORABLE_REGISTERED_FIELD_ERRORS"
                ),
                "counts": {"error": 1},
            },
        ):
            self.assertFalse(
                aspen_suite.worker_allows_equipment_selection({
                    **base,
                    "status": "PASS",
                    "com_extraction_coverage_summary": invalid_summary,
                })
            )

    def test_com_extraction_blocker_rejects_populated_selection_payload(self) -> None:
        with TemporaryDirectory() as temporary_text:
            root = Path(temporary_text)
            source = root / "incomplete-tree.bkp"
            source.write_bytes(b"incomplete")
            output = root / "suite-output"

            def fake_import(config: dict[str, object]) -> dict[str, object]:
                case_dir = Path(str(config["output_dir"]))
                case_dir.mkdir(parents=True, exist_ok=False)
                worker = {
                    "status": "PASS",
                    "selection_result_available": True,
                    "com_extraction_blockers": [
                        {"code": "BLOCKED_COM_TREE_ROOT_BLOCKS_ENUMERATION"}
                    ],
                    "result": {
                        "status": "PASS",
                        "equipment": [{
                            "aspen_block_id": "P-STALE",
                            "match_result": {
                                "model_recommendation": {
                                    "leading_candidate": {
                                        "designation": "不应采用的旧结果"
                                    }
                                }
                            },
                        }],
                        "piping": [],
                    },
                    "stream_transport_verification": {
                        "status": "PASS",
                        "missing_stream_count": 0,
                    },
                    "block_count": 0,
                    "stream_count": 1,
                }
                (case_dir / "worker_result.json").write_text(
                    json.dumps(worker), encoding="utf-8"
                )
                return {
                    "ok": False,
                    "value": worker,
                    "returncode": 3,
                    "completed_with_warnings": False,
                }

            report = aspen_suite.run_suite(
                {
                    "output_dir": str(output),
                    "pressure_basis": "absolute",
                    "run": False,
                    "cases": [{"id": "BLOCKED", "source_path": str(source)}],
                },
                fake_import,
            )

            case = report["cases"][0]
            self.assertEqual(case["status"], "BLOCKED_COM_EXTRACTION")
            self.assertFalse(case["usable_for_equipment_selection"])
            self.assertFalse(case["worker_selection_result_available"])
            self.assertEqual(report["usable_count"], 0)
            self.assertEqual(report["failed_count"], 1)

    def test_serial_suite_keeps_usable_warning_result_and_continues_after_hash_failure(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_text:
            root = Path(temporary_text)
            first = root / "clean.bkp"
            second = root / "dirty.bkp"
            rejected = root / "hash-mismatch.bkp"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            rejected.write_bytes(b"third")
            output = root / "suite-output"
            calls: list[str] = []

            def fake_import(config: dict[str, object]) -> dict[str, object]:
                source = Path(str(config["source_path"]))
                case_dir = Path(str(config["output_dir"]))
                case_dir.mkdir(parents=True, exist_ok=False)
                calls.append(source.name)
                history = case_dir / "raw_aspen_run_history.his"
                history.write_text("raw Aspen evidence\n", encoding="utf-8")
                clean = source == first
                counts = {
                    "terminal_errors": 0,
                    "severe_errors": 0,
                    "errors": 0,
                    "warnings": 0 if clean else 1,
                }
                worker = {
                    "status": (
                        "PASS"
                        if clean
                        else "BLOCKED_TRANSPORT_PROPERTY_VERIFICATION"
                    ),
                    "result": {
                        "status": "PASS" if clean else "PARTIAL",
                        "formal_use_gate": (
                            "ELIGIBLE_AS_PROCESS_BASIS"
                            if clean
                            else "BLOCKED_DIRTY_RUN"
                        ),
                        "equipment": [{
                            "aspen_block_id": source.stem,
                            "match_result": {
                                "model_recommendation": {
                                    "leading_candidate": {
                                        "designation": f"设备-{source.stem}"
                                    }
                                }
                            },
                        }],
                        "piping": [{
                            "stream_id": f"S-{source.stem}",
                            "match_result": {
                                "model_recommendation": {
                                    "leading_candidate": {
                                        "designation": f"DN25-{source.stem}"
                                    }
                                }
                            },
                        }],
                    },
                    "history_parse": {
                        "found": True,
                        "counts": counts,
                        "problem_lines": [] if clean else ["* WARNING simulated"],
                    },
                    "stream_transport_verification": {
                        "status": "PASS" if clean else "BLOCKED",
                        "missing_stream_count": 0 if clean else 1,
                    },
                    "block_count": 1,
                    "stream_count": 1,
                }
                (case_dir / "worker_result.json").write_text(
                    json.dumps(worker),
                    encoding="utf-8",
                )
                return {
                    "ok": True,
                    "value": worker,
                    "returncode": 0 if clean else 3,
                    "completed_with_warnings": not clean,
                }

            progress_events: list[str] = []
            report = aspen_suite.run_suite(
                {
                    "output_dir": str(output),
                    "pressure_basis": "absolute",
                    "run": True,
                    "cases": [
                        {
                            "id": "CLEAN",
                            "source_path": str(first),
                            "sha256": aspen_suite.sha256_file(first),
                        },
                        {
                            "id": "DIRTY",
                            "source_path": str(second),
                            "sha256": aspen_suite.sha256_file(second),
                        },
                        {
                            "id": "REJECTED",
                            "source_path": str(rejected),
                            "sha256": "A" * 64,
                        },
                    ],
                },
                fake_import,
                progress=lambda event: progress_events.append(str(event["event"])),
            )

            self.assertEqual(calls, ["clean.bkp", "dirty.bkp"])
            self.assertEqual(report["status"], "PARTIAL_SUCCESS")
            self.assertEqual(report["usable_count"], 2)
            self.assertEqual(report["formal_ready_count"], 1)
            self.assertEqual(report["failed_count"], 1)
            self.assertEqual(
                report["cases"][0]["status"],
                "FORMAL_PROCESS_BASIS_READY",
            )
            self.assertEqual(
                report["cases"][1]["status"],
                "SELECTION_READY_FORMAL_EVIDENCE_OPEN",
            )
            self.assertEqual(
                report["cases"][2]["status"],
                "SOURCE_HASH_MISMATCH",
            )
            self.assertTrue(report["cases"][0]["source_unchanged"])
            self.assertTrue(report["cases"][1]["source_unchanged"])
            self.assertTrue(
                report["cases"][0]["candidate_coverage_complete"]
            )
            self.assertEqual(progress_events.count("CASE_STARTED"), 3)
            self.assertEqual(progress_events.count("CASE_FINISHED"), 3)
            self.assertEqual(progress_events[-1], "SUITE_FINISHED")
            self.assertTrue((output / "aspen_suite_report.json").is_file())
            self.assertTrue((output / "aspen_suite_report.md").is_file())

    def test_manifest_paths_are_relative_to_manifest_not_process_directory(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_text:
            root = Path(temporary_text)
            cases_dir = root / "cases"
            cases_dir.mkdir()
            source = cases_dir / "one.bkp"
            source.write_bytes(b"one")
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps({
                    "schema": "equipment-design-bkp-stability-manifest-v1",
                    "suite": "smoke",
                    "cases": [{
                        "id": "ONE",
                        "path": "cases/one.bkp",
                        "pressure_basis": "absolute",
                        "sha256": aspen_suite.sha256_file(source),
                    }],
                }),
                encoding="utf-8",
            )

            cases, record = aspen_suite.resolve_cases({
                "manifest_path": str(manifest),
            })

            self.assertEqual(cases[0]["source_path"], str(source.resolve()))
            self.assertEqual(cases[0]["expected_sha256"], aspen_suite.sha256_file(source))
            self.assertEqual(record["path"], str(manifest.resolve()))
            self.assertEqual(record["suite"], "smoke")


if __name__ == "__main__":
    unittest.main()
