from __future__ import annotations

import json
import hashlib
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator


APP_DIR = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = APP_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import aspen_com_import


def complete_legal_coverage_sidecar() -> dict[str, object]:
    found = {
        "field": "HEAD_CAL",
        "path": r"\Data\Blocks\P-1\Output\HEAD_CAL",
        "candidate_paths": [r"\Data\Blocks\P-1\Output\HEAD_CAL"],
        "unit": "m",
        "status": "found",
        "value_type": 2,
        "provenance": {"source": "ASPEN_LIVE_COM_TREE"},
        "recovered_error_count": 0,
    }
    missing = {
        "field": "NPSHA",
        "path": r"\Data\Blocks\P-1\Output\NPSHA",
        "candidate_paths": [r"\Data\Blocks\P-1\Output\NPSHA"],
        "unit": "m",
        "status": "missing",
        "value_type": None,
        "provenance": {"source": "ASPEN_LIVE_COM_TREE"},
        "recovered_error_count": 0,
    }
    counts = {
        "requested": 2,
        "found": 1,
        "missing": 1,
        "error": 0,
        "unsupported": 0,
    }
    return {
        "schema": "aspen-com-extraction-coverage-v1",
        "registry_revision": aspen_com_import.ASPEN_EXTRACTION_REGISTRY_REVISION,
        "registry_audit": {
            "schema": "aspen-com-extraction-registry-audit-v1",
            "status": "PASS",
        },
        "source_contract": {
            "read_only_extraction": True,
            "source_bkp_mutated": False,
            "unmapped_value_mode": "METADATA_ONLY_NO_RAW_VALUES",
            "raw_unmapped_values_persisted": False,
            "discovery_bounds": {
                "max_nodes_per_object": 5000,
                "max_depth": 24,
                "max_nodes_per_case": 25000,
                "max_metadata_bytes_per_case": 16777216,
            },
        },
        "counts": counts,
        "recovered_error_count": 0,
        "registry_field_hit_rate": 0.5,
        "registry_field_hit_percent": 50.0,
        "coverage_rate": 0.5,
        "coverage_percent": 50.0,
        "registry_completeness_status": "SCORED_REGISTERED_OBJECTS",
        "global_review_required": False,
        "global_review_reasons": [],
        "registry_completeness_rate": 0.5,
        "requested_status_invariant": True,
        "object_count": 1,
        "object_review_count": 0,
        "objects": [{
            "scope": "block",
            "object_id": "P-1",
            "module_type": "PUMP",
            "registry_status": "supported",
            "fields": {"HEAD_CAL": found, "NPSHA": missing},
            "counts": counts,
            "registry_field_hit_rate": 0.5,
            "registry_completeness_status": "SCORED_REGISTERED_OBJECT",
            "registry_completeness_rate": 0.5,
            "requested_status_invariant": True,
            "tree_discovery_truncated": False,
            "unmapped_field_count": 0,
            "discovery_error_count": 0,
        }],
        "unmapped_modules": [],
        "unmapped_stream_record_types": [],
        "unmapped_nodes": [],
        "unmapped_fields": [],
        "composition_extraction": {
            "schema": "aspen-stream-composition-extraction-coverage-v1",
            "status": "NOT_APPLICABLE_NO_MATERIAL_STREAMS",
            "independent_of_registry_field_hit_rate": True,
            "material_stream_count": 0,
            "non_material_stream_count": 0,
            "requested_vector_count": 0,
            "found_vector_count": 0,
            "missing_vector_count": 0,
            "invalid_or_error_vector_count": 0,
            "component_count": 0,
            "rows": [],
        },
        "discovery_errors": [],
        "discovery_error_count": 0,
        "root_diagnostics": [],
        "root_diagnostic_count": 0,
        "tree_discovery_truncated_object_count": 0,
        "case_discovery_budget_exhausted": False,
        "case_discovery_budget": {
            "max_nodes": 25000,
            "nodes_visited": 10,
            "node_budget_exhausted": False,
            "max_metadata_bytes": 16777216,
            "metadata_bytes_hashed": 10,
            "metadata_bytes_output": 100,
            "metadata_bytes_consumed": 110,
            "metadata_budget_exhausted": False,
            "metadata_values_truncated": 0,
            "metadata_text_values_truncated": 0,
        },
    }


class StrictReadOnlyElements:
    def __init__(self, rows=None) -> None:
        self.rows = list(rows or [])

    @property
    def Count(self) -> int:
        return len(self.rows)

    def __call__(self, key):
        return self.Item(key)

    def Item(self, key):
        if isinstance(key, str):
            return next(row for row in self.rows if row.Name == key)
        return self.rows[key]


class StrictReadOnlyNode:
    def __init__(
        self,
        name: str,
        value=None,
        *,
        unit: str = "",
        value_type: int = 2,
        record_type: str = "",
        compstatus: int = 1,
        children=None,
    ) -> None:
        self.Name = name
        self._value = value
        self.UnitString = unit
        self.ValueType = value_type
        self._record_type = record_type
        self._compstatus = compstatus
        self.Elements = StrictReadOnlyElements(children)

    @property
    def Value(self):
        return self._value

    def AttributeValue(self, index: int):
        if index == 6:
            if not self._record_type:
                raise RuntimeError("record type unavailable")
            return self._record_type
        if index == 12:
            return self._compstatus
        raise RuntimeError(index)


class StrictReadOnlyTree:
    def __init__(self, mapping, *, failing_paths=()) -> None:
        self.mapping = dict(mapping)
        self.failing_paths = set(failing_paths)
        self.find_calls: list[str] = []

    def FindNode(self, path: str):
        self.find_calls.append(path)
        if path in self.failing_paths:
            raise RuntimeError(f"fixture COM lookup failure: {path}")
        return self.mapping.get(path)


class AspenComImportTests(unittest.TestCase):
    def test_worker_cli_requires_explicit_pressure_basis(self) -> None:
        output = PACKAGE_ROOT / "outputs" / "app_test_runs" / f"missing_basis_{uuid.uuid4().hex[:10]}"
        with self.assertRaises(SystemExit) as context:
            aspen_com_import.main(["--out-dir", str(output)])
        self.assertNotEqual(context.exception.code, 0)

    def test_worker_mock_cli_propagates_absolute_atmosphere_end_to_end(self) -> None:
        fixture = APP_DIR / "fixtures" / "mock_aspen_pump.json"
        output = PACKAGE_ROOT / "outputs" / "app_test_runs" / f"mock_absolute_atmosphere_{uuid.uuid4().hex[:10]}"
        rc = aspen_com_import.main([
            "--out-dir", str(output),
            "--pressure-basis", "absolute",
            "--atmospheric-pressure-mpa", "0.101325",
            "--mock-fixture", str(fixture),
        ])
        self.assertEqual(rc, 0)
        worker = json.loads((output / "worker_result.json").read_text(encoding="utf-8"))
        self.assertEqual(worker["status"], "PASS_MOCK")
        equipment_input = worker["result"]["equipment"][0]["canonical_match_input"]
        self.assertEqual(equipment_input["pressure_basis"], "absolute")
        self.assertAlmostEqual(equipment_input["atmospheric_pressure_mpa"], 0.101325)
        for pipe in worker["result"]["piping"]:
            self.assertEqual(pipe["canonical_match_input"]["pressure_basis"], "absolute")
            self.assertAlmostEqual(pipe["canonical_match_input"]["atmospheric_pressure_mpa"], 0.101325)

    def test_worker_cli_returns_nonzero_for_explicit_blocked_result(self) -> None:
        output = PACKAGE_ROOT / "outputs" / "app_test_runs" / f"blocked_worker_{uuid.uuid4().hex[:10]}"
        source = output / "isolated_source.bkp"
        output.mkdir(parents=True, exist_ok=False)
        source.write_bytes(b"test-only")
        blocked = {
            "schema": "equipment-design-app-aspen-worker-v1",
            "status": "BLOCKED_TRANSPORT_PROPERTY_VERIFICATION",
        }
        with patch.object(aspen_com_import, "run_real", return_value=blocked):
            rc = aspen_com_import.main([
                "--source", str(source),
                "--out-dir", str(output),
                "--pressure-basis", "absolute",
            ])
        self.assertEqual(rc, 3)
        worker = json.loads((output / "worker_result.json").read_text(encoding="utf-8"))
        self.assertEqual(worker["status"], "BLOCKED_TRANSPORT_PROPERTY_VERIFICATION")

    def test_worker_pfd_parameters_follow_the_same_canonical_unit_conversions_as_derivation(self) -> None:
        fixture = json.loads(
            (APP_DIR / "fixtures" / "mock_aspen_pump.json").read_text(encoding="utf-8")
        )
        fixture["bundle"]["units"].update({
            "block.WNET": "Watt",
            "block.BRAKE_POWER": "Watt",
            "block.HEAD_CAL": "J/kg",
            "block.CEFF": "fraction",
            "block.DELP_CAL": "bar",
        })
        fixture["bundle"]["blocks"][0].update({
            # PUMP shaft power is the explicit BRAKE_POWER channel. WNET stays
            # independent and is not used as a shaft-power alias.
            "WNET": 52699.9023,
            "BRAKE_POWER": 51645.9043,
            "HEAD_CAL": 2386.01438,
            "CEFF": 0.649309122,
            "DELP_CAL": 20.27825,
        })
        fixture["bundle"]["units"].update({
            "stream.TEMP_OUT": "K",
            "stream.PRES_OUT": "kPa",
            "stream.MASSFLMX": "kg/s",
            "stream.VOLFLMX": "m3/s",
        })
        fixture["bundle"]["streams"][0].update({
            "TEMP_OUT": 298.15,
            "PRES_OUT": 120.0,
            "MASSFLMX": 8.5,
            "VOLFLMX": 0.01,
        })
        fixture["bundle"]["streams"][1].update({
            "TEMP_OUT": 298.65,
            "PRES_OUT": 500.0,
            "MASSFLMX": 8.5,
            "VOLFLMX": 0.01,
        })

        root = (
            PACKAGE_ROOT
            / "outputs"
            / "app_test_runs"
            / f"pfd_unit_binding_{uuid.uuid4().hex[:10]}"
        )
        root.mkdir(parents=True, exist_ok=False)
        try:
            fixture_path = root / "real_unit_shapes.json"
            output = root / "worker"
            output.mkdir()
            fixture_path.write_text(
                json.dumps(fixture, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            worker = aspen_com_import.run_mock(
                fixture_path,
                output,
                pressure_basis="absolute",
            )
            pfd = json.loads((output / "aspen_pfd_mapping.json").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(root, ignore_errors=True)

        canonical = worker["result"]["equipment"][0]["canonical_match_input"]
        pfd_block = next(item for item in pfd["blocks"] if item["block_id"] == "P-101")
        rows = {item["field"]: item for item in pfd_block["parameters"]}
        for field in (
            "shaft_power_kw",
            "head_m",
            "efficiency_percent",
            "pressure_drop_kpa",
        ):
            self.assertAlmostEqual(rows[field]["value"], canonical[field])
            self.assertEqual(rows[field]["source_status"], "ASPEN_DERIVED_PROCESS_SIDE")

        piping = next(item for item in worker["result"]["piping"] if item["stream_id"] == "S-IN")
        pfd_edge = next(item for item in pfd["pfd"]["edges"] if item["stream_id"] == "S-IN")
        stream_rows = {item["field"]: item for item in pfd_edge["parameters"]}
        for field, expected in {
            "temperature_c": 25.0,
            "pressure_mpa": 0.12,
            "mass_flow_kg_h": 30600.0,
            "volumetric_flow_m3_h": 36.0,
        }.items():
            self.assertAlmostEqual(stream_rows[field]["value"], expected)
            self.assertAlmostEqual(stream_rows[field]["value"], piping["pfd_parameters"][field]["value"])
            self.assertEqual(stream_rows[field]["source_status"], "ASPEN_DERIVED_PROCESS_SIDE")

    def test_vapor_fraction_phase_inference_uses_only_numeric_endpoint_tolerance(self) -> None:
        expected = {
            0.0: "liquid",
            1.0: "vapor",
            0.04: "two_phase",
            0.96: "two_phase",
            0.5: "two_phase",
        }
        for vapor_fraction, phase in expected.items():
            with self.subTest(vapor_fraction=vapor_fraction):
                inference = aspen_com_import.phase_from_vapor_fraction(vapor_fraction)
                self.assertEqual(inference["phase"], phase)
                self.assertIn("every material 0<VFRAC_OUT<1 is two_phase", inference["policy"])
        invalid = aspen_com_import.phase_from_vapor_fraction(1.01)
        self.assertEqual(invalid["status"], "INVALID_OUT_OF_RANGE")
        self.assertIsNone(invalid["phase"])

    def test_semantic_unit_fallback_is_narrow_and_explicit(self) -> None:
        self.assertEqual(aspen_com_import.resolve_aspen_unit("CEFF", ""), ("fraction", True))
        self.assertEqual(aspen_com_import.resolve_aspen_unit("molecular_weight", ""), ("kg/kmol", True))
        self.assertEqual(aspen_com_import.resolve_aspen_unit("TEMP_OUT", ""), (None, False))
        self.assertEqual(aspen_com_import.resolve_aspen_unit("TEMP_OUT", "C"), ("C", False))

    def test_bkp_in_units_card_fields_are_parsed_and_used_after_unitstring(self) -> None:
        text = """
IN-UNITS MET VOLUME-FLOW='cum/hr' ENTHALPY-FLO='Gcal/hr'  &
        PRESSURE=bar TEMPERATURE=C VISCOSITY='N-sec/sqm' PDROP=atm SHORT-LENGTH=mm

PROP-DATA HENRY-1
    IN-UNITS MET PRESSURE=psia TEMPERATURE=F PDROP=atm
"""
        cards = aspen_com_import.parse_in_units_cards(text)
        self.assertEqual(len(cards), 2)
        global_card = aspen_com_import.global_in_units(cards)
        self.assertIsNotNone(global_card)
        self.assertEqual(global_card["unit_set"], "MET")
        self.assertEqual(global_card["fields"]["ENTHALPY-FLO"], "Gcal/hr")
        self.assertEqual(global_card["fields"]["PDROP"], "atm")
        self.assertEqual(cards[1]["scope"], "LOCAL_CARD")

        fields = global_card["fields"]
        self.assertEqual(aspen_com_import.resolve_aspen_unit("DELP_CAL", "", fields), ("atm", True))
        self.assertEqual(aspen_com_import.resolve_aspen_unit("QCALC", "", fields), ("Gcal/hr", True))
        self.assertEqual(aspen_com_import.resolve_aspen_unit("TEMP_OUT", "", fields), ("C", True))
        self.assertEqual(aspen_com_import.resolve_aspen_unit("MUMX", "", fields), ("N-sec/sqm", True))
        self.assertEqual(aspen_com_import.resolve_aspen_unit("TEMP_OUT", "K", fields), ("K", False))

    def test_stream_transport_viscosity_uses_aspen_mumx_output(self) -> None:
        self.assertEqual(
            aspen_com_import.STREAM_FIELDS["MUMX_LIQUID"][0],
            r"Output\STRM_UPP\MUMX\MIXED\LIQUID",
        )
        self.assertEqual(
            aspen_com_import.STREAM_FIELDS["MUMX_VAPOR"][0],
            r"Output\STRM_UPP\MUMX\MIXED\VAPOR",
        )
        self.assertEqual(
            aspen_com_import.RAW_FIELD_TO_IN_UNITS_KEY["MUMX_LIQUID"],
            "VISCOSITY",
        )

    def test_phase_volume_projection_preserves_raw_com_lineage(self) -> None:
        source_path = r"\Data\Streams\S1\Output\VOLFLMX\MIXED"
        row = {
            "VOLFLMX": 12.5,
            "phase_source_field": "VFRAC_OUT",
            "aspen_raw_paths": {"VOLFLMX": source_path},
            "aspen_raw_values": {
                "VOLFLMX": {
                    "value": 12.5,
                    "unit": "cum/hr",
                    "status": "defined",
                }
            },
        }

        projected = aspen_com_import.project_stream_phase_observation(
            row,
            source_field="VOLFLMX",
            target_field="VOLFLMX_LIQ",
            phase="liquid",
        )

        self.assertTrue(projected)
        self.assertEqual(row["VOLFLMX_LIQ"], 12.5)
        self.assertEqual(row["aspen_raw_paths"]["VOLFLMX_LIQ"], source_path)
        projected_record = row["aspen_raw_values"]["VOLFLMX_LIQ"]
        self.assertEqual(projected_record["projection_source_field"], "VOLFLMX")
        self.assertEqual(projected_record["phase_projection"], "liquid")
        self.assertEqual(projected_record["phase_source_field"], "VFRAC_OUT")

    def test_transport_property_inp_augmentation_is_idempotent(self) -> None:
        source = """TITLE 'PUMP'

PROPERTIES NRTL

STREAM IN
    SUBSTREAM MIXED TEMP=20 PRES=1

BLOCK P1 PUMP

STREAM-REPOR MOLEFLOW
;
"""
        augmented, manifest = aspen_com_import.augment_transport_property_inp(
            source
        )
        self.assertEqual(manifest["status"], "CHANGED")
        self.assertTrue(manifest["property_set_created"])
        self.assertTrue(manifest["stream_report_updated"])
        self.assertIn(
            "PROP-SET TXPORT MUMX SUBSTREAM=MIXED PHASE=V L",
            augmented,
        )
        self.assertIn(
            "STREAM-REPOR MOLEFLOW PROPERTIES=TXPORT",
            augmented,
        )
        second, second_manifest = (
            aspen_com_import.augment_transport_property_inp(augmented)
        )
        self.assertEqual(second, augmented)
        self.assertEqual(second_manifest["status"], "ALREADY_PRESENT")
        self.assertFalse(second_manifest["source_bkp_mutated"])

    def test_existing_mumx_property_request_is_not_rewritten(self) -> None:
        source = """PROP-SET TXPORT RHOMX MUMX SIGMAMX SUBSTREAM=MIXED PHASE=V L

STREAM S1
    SUBSTREAM MIXED TEMP=20 PRES=1

STREAM-REPOR MOLEFLOW PROPERTIES=TXPORT
"""
        augmented, manifest = aspen_com_import.augment_transport_property_inp(
            source
        )
        self.assertEqual(augmented, source)
        self.assertEqual(manifest["status"], "ALREADY_PRESENT")
        self.assertEqual(manifest["property_set_name"], "TXPORT")

    def test_direct_com_transport_configuration_reuses_existing_mumx_set(self) -> None:
        class FakeNode:
            def __init__(self, name: str, value=None, children=None) -> None:
                self.Name = name
                self.Value = value
                self.Elements = FakeElements(children or [])

        class FakeElements:
            def __init__(self, rows) -> None:
                self.rows = list(rows)
                self.add_calls = []

            @property
            def Count(self):
                return len(self.rows)

            def __call__(self, index):
                return self.Item(index)

            def Item(self, index):
                if isinstance(index, str):
                    return next(row for row in self.rows if row.Name == index)
                return self.rows[index]

            def RowCount(self, _dimension):
                return len(self.rows)

            def InsertRow(self, _dimension, location):
                self.rows.insert(location, FakeNode(f"#{location}"))

            def SetItemName(self, location, _dimension, _force, name):
                self.rows[location].Name = name

            def Add(self, name):
                self.add_calls.append(name)
                row = FakeNode(name)
                self.rows.append(row)
                return row

        class FakeTree:
            def __init__(self, mapping) -> None:
                self.mapping = mapping

            def FindNode(self, path):
                return self.mapping.get(path)

        txport = FakeNode("TXPORT")
        prop_sets = FakeNode("Prop-Sets", children=[txport])
        report = FakeNode("PROPERTIES")
        units = FakeNode(
            "UNITS",
            children=[FakeNode("RHOMX"), FakeNode("MUMX"), FakeNode("SIGMAMX")],
        )
        phases = FakeNode(
            "PHASE",
            children=[FakeNode("#0", "V"), FakeNode("#1", "L")],
        )
        substream = FakeNode("SUBSTREAM", "MIXED")
        base = r"\Data\Properties\Prop-Sets\TXPORT\Input"
        mapping = {
            r"\Data\Properties\Prop-Sets": prop_sets,
            r"\Data\Properties\Prop-Sets\TXPORT": txport,
            r"\Data\Setup\Main\Input\PROPERTIES": report,
            base + r"\UNITS": units,
            base + r"\PHASE": phases,
            base + r"\SUBSTREAM": substream,
        }
        app = type("FakeApp", (), {"Tree": FakeTree(mapping)})()
        output = PACKAGE_ROOT / "outputs" / "app_test_runs" / f"direct_com_{uuid.uuid4().hex[:10]}"
        output.mkdir(parents=True, exist_ok=False)
        source = output / "source.bkp"
        source.write_bytes(b"read-only-source")

        manifest, manifest_path = (
            aspen_com_import.ensure_stream_transport_property_via_com(
                app,
                output,
                source,
            )
        )
        self.assertEqual(manifest["status"], "CHANGED")
        self.assertEqual(manifest["property_set_name"], "TXPORT")
        self.assertFalse(manifest["property_set_created"])
        self.assertTrue(manifest["stream_report_updated"])
        self.assertEqual([row.Value for row in report.Elements.rows], ["TXPORT"])
        self.assertEqual(prop_sets.Elements.add_calls, [])
        self.assertTrue(manifest_path.is_file())

        second, _ = aspen_com_import.ensure_stream_transport_property_via_com(
            app,
            output,
            source,
        )
        self.assertEqual(second["status"], "ALREADY_PRESENT")
        self.assertEqual([row.Value for row in report.Elements.rows], ["TXPORT"])

    def test_transport_verification_never_replaces_missing_viscosity_with_default(self) -> None:
        verification = aspen_com_import.verify_stream_transport_properties({
            "streams": [
                {
                    "stream_id": "L1",
                    "stream_record_type": "MATERIAL",
                    "phase": "liquid",
                },
                {
                    "stream_id": "V1",
                    "stream_record_type": "MATERIAL",
                    "phase": "vapor",
                    "MUMX": 0.012,
                    "aspen_raw_paths": {
                        "MUMX": r"\Data\Streams\V1\Output\STRM_UPP\MUMX\MIXED\VAPOR",
                    },
                },
            ]
        })
        self.assertEqual(
            verification["status"],
            "BLOCKED_MISSING_ASPEN_VISCOSITY",
        )
        self.assertEqual(verification["missing"][0]["stream_id"], "L1")
        self.assertNotIn("default", verification["missing"][0])

    def test_transport_verification_blocks_unknown_phase_instead_of_vacuous_pass(self) -> None:
        verification = aspen_com_import.verify_stream_transport_properties({
            "streams": [
                {
                    "stream_id": "NO-RESULTS",
                    "stream_record_type": "MATERIAL",
                    "phase": None,
                }
            ]
        })

        self.assertEqual(
            verification["status"],
            "BLOCKED_UNVERIFIABLE_ASPEN_PHASE_AND_VISCOSITY",
        )
        self.assertEqual(verification["verified_stream_count"], 0)
        self.assertEqual(verification["unverifiable_phase_stream_count"], 1)
        self.assertEqual(
            verification["missing"][0]["missing_fields"],
            ["PHASE", "MUMX"],
        )

    def test_transport_verification_blocks_empty_material_set_and_accepts_solid_bearing(self) -> None:
        empty = aspen_com_import.verify_stream_transport_properties({
            "streams": [
                {"stream_id": "Q-1", "stream_record_type": "HEAT"},
            ],
        })
        self.assertEqual(
            empty["status"],
            "BLOCKED_NO_MATERIAL_STREAMS_FOR_TRANSPORT_VERIFICATION",
        )
        self.assertEqual(empty["material_stream_count"], 0)

        solid = aspen_com_import.verify_stream_transport_properties({
            "streams": [
                {
                    "stream_id": "SLURRY-1",
                    "stream_record_type": "MATERIAL",
                    "phase": "solid_bearing",
                },
            ],
        })
        self.assertEqual(solid["status"], "PASS")
        self.assertEqual(solid["not_applicable_solid_stream_count"], 1)

    def test_worker_status_precedence_blocks_com_roots_before_transport(self) -> None:
        root_blocker = [{
            "code": "BLOCKED_COM_TREE_ROOT_MISSING",
            "scope": "block",
        }]
        coverage = {
            "root_diagnostics": root_blocker,
            "tree_discovery_truncated_object_count": 0,
            "case_discovery_budget": {},
        }
        self.assertEqual(
            aspen_com_import.classify_aspen_worker_status(
                {"status": "PASS"},
                root_blocker,
                coverage,
            ),
            "BLOCKED_COM_EXTRACTION",
        )
        self.assertEqual(
            aspen_com_import.classify_aspen_worker_status(
                {"status": "BLOCKED_MISSING_ASPEN_VISCOSITY"},
                [],
                {},
            ),
            "BLOCKED_TRANSPORT_PROPERTY_VERIFICATION",
        )
        self.assertEqual(
            aspen_com_import.classify_aspen_worker_status(
                {"status": "PASS"},
                [],
                {},
            ),
            "PASS",
        )

    def test_transport_evidence_is_persisted_when_no_com_change_is_needed(self) -> None:
        root = PACKAGE_ROOT / "outputs" / "app_test_runs" / f"transport_no_change_{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        source = root / "source.bkp"
        source.write_bytes(b"read-only-source")
        verification = {
            "schema": "aspen-stream-transport-verification-v1",
            "status": "PASS",
            "material_stream_count": 1,
            "verified_stream_count": 1,
            "rows": [{"stream_id": "S1", "verification_state": "VERIFIED"}],
        }
        try:
            manifest, manifest_path, verification_path = (
                aspen_com_import.persist_stream_transport_evidence(
                    root,
                    source,
                    verification,
                    augmentation_requested=False,
                    run_result={"status": "returned"},
                )
            )
            persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
            verification_sha256 = aspen_com_import.sha256(verification_path)
        finally:
            shutil.rmtree(root, ignore_errors=True)

        self.assertEqual(manifest["status"], "NO_CHANGE_ALREADY_AVAILABLE")
        self.assertEqual(
            manifest["method"],
            "READ_ONLY_COM_TREE_EXTRACTION_AND_VERIFICATION",
        )
        self.assertTrue(manifest["rerun_completed"])
        self.assertEqual(
            persisted["verification_artifact"]["sha256"],
            verification_sha256,
        )
        self.assertFalse(manifest["source_bkp_mutated"])

    def test_sidecar_staging_is_bounded_and_hashes_each_copy(self) -> None:
        root = PACKAGE_ROOT / "outputs" / "app_test_runs" / f"sidecars_{uuid.uuid4().hex}"
        source_root = root / "source"
        work_root = root / "work"
        source_root.mkdir(parents=True, exist_ok=False)
        work_root.mkdir(parents=True, exist_ok=False)
        source = source_root / "case.bkp"
        source.write_bytes(b"bkp")
        source.with_suffix(".def").write_text("definition", encoding="utf-8")
        (source_root / "unrelated.def").write_text("do-not-copy", encoding="utf-8")
        (source_root / "E0201.EDR").write_bytes(b"edr-1")
        (source_root / "E0203.edr").write_bytes(b"edr-2")
        staged = work_root / "SOURCE.bkp"
        staged.write_bytes(source.read_bytes())

        manifest = aspen_com_import.stage_aspen_sidecars(source, staged)

        self.assertTrue((work_root / "SOURCE.def").is_file())
        self.assertTrue((work_root / "E0201.EDR").is_file())
        self.assertTrue((work_root / "E0203.edr").is_file())
        self.assertFalse((work_root / "unrelated.def").exists())
        self.assertEqual({item["kind"] for item in manifest}, {"DEF", "EDR"})
        self.assertTrue(all(len(item["sha256"]) == 64 for item in manifest))
        self.assertTrue(
            all(item["sha256"] == item["staged_sha256"] for item in manifest)
        )

    def test_node_elements_supports_one_based_com_collections(self) -> None:
        class Elements:
            Count = 2

            @staticmethod
            def Item(index: int):
                if index == 1:
                    return "A"
                if index == 2:
                    return "B"
                raise IndexError(index)

        class Node:
            pass

        node = Node()
        node.Elements = Elements()

        self.assertEqual(aspen_com_import.node_elements(node), ["A", "B"])

    def test_strict_element_enumerator_records_count_and_item_failures(self) -> None:
        child = StrictReadOnlyNode("A", 1.0)

        class BrokenCountElements:
            @property
            def Count(self):
                raise RuntimeError("count failed")

            @staticmethod
            def Item(index: int):
                if index == 0:
                    return child
                raise IndexError(index)

        count_node = type("CountNode", (), {"Elements": BrokenCountElements()})()
        count_result = aspen_com_import.strict_node_elements(count_node, 5)
        self.assertEqual(count_result["rows"], [child])
        self.assertIn(
            "IHNode.Elements.Count",
            {item["operation"] for item in count_result["errors"]},
        )
        self.assertIn(
            "IHNode.Elements.Item",
            {item["operation"] for item in count_result["errors"]},
        )

        class BrokenItemElements:
            Count = 2

            @staticmethod
            def Item(index: int):
                if index == 0:
                    return child
                raise RuntimeError("item failed")

        item_node = type("ItemNode", (), {"Elements": BrokenItemElements()})()
        item_result = aspen_com_import.strict_node_elements(item_node, 5)
        self.assertEqual(item_result["rows"], [child])
        self.assertIn(
            "IHNode.Elements.Item",
            {item["operation"] for item in item_result["errors"]},
        )
        self.assertIn(
            "IHNode.Elements.enumeration",
            {item["operation"] for item in item_result["errors"]},
        )

    def test_strict_element_enumerator_accepts_one_based_collections_without_false_error(self) -> None:
        class OneBasedElements:
            Count = 2

            @staticmethod
            def Item(index: int):
                if index == 1:
                    return "A"
                if index == 2:
                    return "B"
                raise IndexError(index)

        node = type("OneBasedNode", (), {"Elements": OneBasedElements()})()
        result = aspen_com_import.strict_node_elements(node, 10)

        self.assertEqual(result["rows"], ["A", "B"])
        self.assertEqual(result["index_basis"], "one_based")
        self.assertFalse(result["errors"])

    def test_discovery_bounds_deep_wide_trees_and_breaks_cycles(self) -> None:
        cycle = StrictReadOnlyNode("CYCLE", None, value_type=0)
        cycle.Elements.rows.append(cycle)
        cycle_output = StrictReadOnlyNode(
            "Output",
            None,
            value_type=0,
            children=[cycle],
        )
        cycle_tree = StrictReadOnlyTree(
            {r"\Data\Blocks\CYCLE\Output": cycle_output}
        )
        cycle_result = aspen_com_import.discover_object_tree(
            cycle_tree,
            scope="block",
            object_id="CYCLE",
            base=r"\Data\Blocks\CYCLE",
            mapped_paths=[],
        )
        self.assertLessEqual(cycle_result["visited_node_count"], 2)
        self.assertFalse(cycle_result["truncated"])

        wide_children = [
            StrictReadOnlyNode(f"F{index}", float(index))
            for index in range(20)
        ]
        wide_output = StrictReadOnlyNode(
            "Output",
            None,
            value_type=0,
            children=wide_children,
        )
        wide_tree = StrictReadOnlyTree(
            {r"\Data\Blocks\WIDE\Output": wide_output}
        )
        with patch.object(
            aspen_com_import,
            "TREE_DISCOVERY_MAX_NODES_PER_OBJECT",
            4,
        ):
            wide_result = aspen_com_import.discover_object_tree(
                wide_tree,
                scope="block",
                object_id="WIDE",
                base=r"\Data\Blocks\WIDE",
                mapped_paths=[],
            )
        self.assertTrue(wide_result["truncated"])
        self.assertLessEqual(wide_result["visited_node_count"], 4)

    def test_complete_stream_composition_is_read_without_hazard_inference(self) -> None:
        class Child:
            def __init__(self, name: str, value: float) -> None:
                self.Name = name
                self.Value = value

        children = [Child("A", 0.75), Child("B", 0.25)]
        warnings: list[dict[str, object]] = []
        root = StrictReadOnlyNode("MIXED", None, value_type=0, children=children)
        tree = StrictReadOnlyTree({
            r"\Data\Streams\S1\Output\MOLEFRAC\MIXED": root,
        })
        composition = aspen_com_import.extract_stream_composition(
            tree, r"\Data\Streams\S1", "S1", None, warnings
        )
        self.assertEqual([item["component_id"] for item in composition], ["A", "B"])
        self.assertEqual(composition[0]["basis"], "mole_fraction")
        self.assertAlmostEqual(sum(item["fraction"] for item in composition), 1.0)
        self.assertFalse(warnings)
        self.assertTrue(all("hazard" not in item for item in composition))

    def test_composition_invalid_unclosed_and_enumeration_error_never_claim_complete(self) -> None:
        cases = {
            "invalid": (
                StrictReadOnlyNode(
                    "MIXED",
                    None,
                    value_type=0,
                    children=[StrictReadOnlyNode("A", 1.2)],
                ),
                "INVALID_COMPONENT_VALUES",
            ),
            "unclosed": (
                StrictReadOnlyNode(
                    "MIXED",
                    None,
                    value_type=0,
                    children=[
                        StrictReadOnlyNode("A", 0.4),
                        StrictReadOnlyNode("B", 0.4),
                    ],
                ),
                "INVALID_VECTOR_NOT_CLOSED",
            ),
        }
        for label, (root, expected_status) in cases.items():
            with self.subTest(label=label):
                warnings: list[dict[str, object]] = []
                tree = StrictReadOnlyTree({
                    rf"\Data\Streams\{label}\Output\MOLEFRAC\MIXED": root,
                })
                rows, coverage = aspen_com_import.extract_stream_composition_with_status(
                    tree,
                    rf"\Data\Streams\{label}",
                    label,
                    None,
                    warnings,
                )
                self.assertFalse(rows)
                self.assertEqual(coverage["found_vector_count"], 0)
                self.assertEqual(coverage["status"], expected_status)
                self.assertIn(
                    "BLOCKED_COMPOSITION_VECTOR_INCOMPLETE",
                    {item["code"] for item in warnings},
                )

        class BrokenCompositionElements:
            @property
            def Count(self):
                raise RuntimeError("composition count unavailable")

            @staticmethod
            def Item(index: int):
                raise RuntimeError(f"composition item unavailable {index}")

        broken_root = type(
            "BrokenCompositionRoot",
            (),
            {"Elements": BrokenCompositionElements()},
        )()
        warnings = []
        rows, coverage = aspen_com_import.extract_stream_composition_with_status(
            StrictReadOnlyTree({
                r"\Data\Streams\BROKEN\Output\MOLEFRAC\MIXED": broken_root,
            }),
            r"\Data\Streams\BROKEN",
            "BROKEN",
            None,
            warnings,
        )
        self.assertFalse(rows)
        self.assertEqual(coverage["status"], "ERROR_COMPOSITION_ENUMERATION")
        self.assertGreater(coverage["enumeration_error_count"], 0)
        json.dumps(coverage, allow_nan=False)

    def test_semantic_record_type_uses_attribute_not_icon_value(self) -> None:
        class FakeNode:
            Value = "ICON1"

            @staticmethod
            def AttributeValue(index: int):
                return {6: "PUMP", 12: 0}[index]

        record_type, source = aspen_com_import.node_record_type(FakeNode())
        self.assertEqual(record_type, "PUMP")
        self.assertEqual(source, "AttributeValue(6):HAP_RECORDTYPE")
        self.assertEqual(aspen_com_import.node_compstatus(FakeNode()), 0)

    def test_no_results_compstatus_is_explicitly_recognized(self) -> None:
        self.assertTrue(aspen_com_import.owner_has_no_results(2))
        self.assertTrue(aspen_com_import.owner_has_no_results(2097152))
        self.assertTrue(aspen_com_import.owner_has_no_results(2105474))
        self.assertFalse(aspen_com_import.owner_has_no_results(1))
        self.assertFalse(aspen_com_import.owner_has_no_results(None))

    def test_read_first_does_not_export_zero_output_placeholder_without_results(self) -> None:
        class Node:
            def __init__(self, value, unit="", value_type=1):
                self.Value = value
                self.UnitString = unit
                self.ValueType = value_type

        class Tree:
            @staticmethod
            def FindNode(path: str):
                if path.endswith(r"\Output\VOLUME"):
                    return Node(0.0)
                return None

        observation = aspen_com_import.read_first(
            Tree(), r"\Data\Blocks\R0101", [r"Output\VOLUME"], 2105474
        )
        self.assertEqual(
            observation,
            (None, "", r"Output\VOLUME", None, "skipped_owner_no_results"),
        )

    def test_read_first_keeps_input_and_successful_output_values(self) -> None:
        class Node:
            def __init__(self, value, unit, value_type=2):
                self.Value = value
                self.UnitString = unit
                self.ValueType = value_type

        class Tree:
            @staticmethod
            def FindNode(path: str):
                if path.endswith(r"\Input\VOLUME"):
                    return Node(3.5, "cum")
                if path.endswith(r"\Output\VOLUME"):
                    return Node(4.5, "cum")
                return None

        no_results_input = aspen_com_import.read_first(
            Tree(), r"\Data\Blocks\R0101", [r"Input\VOLUME", r"Output\VOLUME"], 2105474
        )
        successful_output = aspen_com_import.read_first(
            Tree(), r"\Data\Blocks\R0101", [r"Output\VOLUME"], 1
        )
        self.assertEqual(no_results_input, (3.5, "cum", r"Input\VOLUME", 2, "defined"))
        self.assertEqual(successful_output, (4.5, "cum", r"Output\VOLUME", 2, "defined"))

    def test_continuous_field_skips_integer_tree_placeholder_but_keeps_real_zero(self) -> None:
        class Node:
            def __init__(self, value, unit, value_type):
                self.Value = value
                self.UnitString = unit
                self.ValueType = value_type

        class PlaceholderTree:
            @staticmethod
            def FindNode(path: str):
                return Node(1, "", 1) if path.endswith(r"\Output\PRES_RATIO") else None

        class RealZeroTree:
            @staticmethod
            def FindNode(path: str):
                return Node(0.0, "Watt", 2) if path.endswith(r"\Output\QCALC") else None

        placeholder = aspen_com_import.read_first(
            PlaceholderTree(),
            r"\Data\Blocks\C0101",
            [r"Output\PRES_RATIO"],
            1,
            require_continuous=True,
        )
        real_zero = aspen_com_import.read_first(
            RealZeroTree(),
            r"\Data\Blocks\FL0201",
            [r"Output\QCALC"],
            1,
            require_continuous=True,
        )
        self.assertEqual(
            placeholder,
            (None, "", r"Output\PRES_RATIO", 1, "skipped_integer_node_for_continuous_field"),
        )
        self.assertEqual(real_zero, (0.0, "Watt", r"Output\QCALC", 2, "defined"))

    def test_icon_value_is_not_fallback_record_type(self) -> None:
        class FakeNode:
            Value = "ICON1"

            @staticmethod
            def AttributeValue(index: int):
                raise RuntimeError(index)

        record_type, source = aspen_com_import.node_record_type(FakeNode())
        self.assertEqual(record_type, "")
        self.assertTrue(source.startswith("missing:"))

    def test_registry_driven_extraction_reports_coverage_and_unmapped_tree(self) -> None:
        def container(name: str, *children: StrictReadOnlyNode) -> StrictReadOnlyNode:
            return StrictReadOnlyNode(
                name,
                None,
                value_type=0,
                children=list(children),
            )

        temp_mixed = StrictReadOnlyNode("MIXED", 25.0, unit="C")
        temp_out = container("TEMP_OUT", temp_mixed)
        pressure_mixed = StrictReadOnlyNode("MIXED", 1.5, unit="bar")
        pressure_out = container("PRES_OUT", pressure_mixed)
        mass_mixed = StrictReadOnlyNode("MIXED", 1000.0, unit="kg/hr")
        mass_out = container("MASSFLMX", mass_mixed)
        volume_mixed = StrictReadOnlyNode("MIXED", 1.1, unit="cum/hr")
        volume_out = container("VOLFLMX", volume_mixed)
        vapor_mixed = StrictReadOnlyNode("MIXED", 0.0, unit="")
        vapor_out = container("VFRAC_OUT", vapor_mixed)
        liquid_mu = StrictReadOnlyNode("LIQUID", 1.2, unit="cP")
        mumx_mixed = container("MIXED", liquid_mu)
        mumx = container("MUMX", mumx_mixed)
        strm_upp = container("STRM_UPP", mumx)
        comp_a = StrictReadOnlyNode("A", 0.75, unit="")
        comp_b = StrictReadOnlyNode("B", 0.25, unit="")
        mole_mixed = container("MIXED", comp_a, comp_b)
        molefrac = container("MOLEFRAC", mole_mixed)
        stream_output = container(
            "Output",
            temp_out,
            pressure_out,
            mass_out,
            volume_out,
            vapor_out,
            strm_upp,
            molefrac,
        )
        stream = StrictReadOnlyNode(
            "S-1",
            "MATERIAL_ICON",
            record_type="MATERIAL",
            children=[stream_output],
        )
        streams_root = container("Streams", stream)

        driver_efficiency = StrictReadOnlyNode("DEFF", 0.95, unit="")
        pump_input = container("Input", driver_efficiency)
        block_status = StrictReadOnlyNode("BLKSTAT", 0, unit="", value_type=1)
        pump_head = StrictReadOnlyNode("HEAD_CAL", 35.0, unit="m")
        pump_wnet_fallback = StrictReadOnlyNode("B_WNET", 4.5, unit="kW")
        brake_power = StrictReadOnlyNode("BRAKE_POWER", 4.2, unit="kW")
        vendor_card = StrictReadOnlyNode("VENDOR_SPECIAL", 123.0, unit="widget")
        pump_output = container(
            "Output",
            block_status,
            pump_head,
            pump_wnet_fallback,
            brake_power,
            vendor_card,
        )
        pump = StrictReadOnlyNode(
            "P-101",
            "PUMP_ICON",
            record_type="PUMP",
            children=[pump_input, pump_output],
        )

        unknown_status = StrictReadOnlyNode("BLKSTAT", 0, unit="", value_type=1)
        unknown_card = StrictReadOnlyNode("CUSTOM_FLOW", 7.5, unit="kg/hr")
        unknown_output = container("Output", unknown_status, unknown_card)
        unknown = StrictReadOnlyNode(
            "X-1",
            "CUSTOM_ICON",
            record_type="VENDORX",
            children=[unknown_output],
        )
        blocks_root = container("Blocks", pump, unknown)

        mapping = {
            r"\Data\Streams": streams_root,
            r"\Data\Streams\S-1": stream,
            r"\Data\Streams\S-1\Output": stream_output,
            r"\Data\Streams\S-1\Output\TEMP_OUT": temp_out,
            r"\Data\Streams\S-1\Output\TEMP_OUT\MIXED": temp_mixed,
            r"\Data\Streams\S-1\Output\PRES_OUT": pressure_out,
            r"\Data\Streams\S-1\Output\PRES_OUT\MIXED": pressure_mixed,
            r"\Data\Streams\S-1\Output\MASSFLMX": mass_out,
            r"\Data\Streams\S-1\Output\MASSFLMX\MIXED": mass_mixed,
            r"\Data\Streams\S-1\Output\VOLFLMX": volume_out,
            r"\Data\Streams\S-1\Output\VOLFLMX\MIXED": volume_mixed,
            r"\Data\Streams\S-1\Output\VFRAC_OUT": vapor_out,
            r"\Data\Streams\S-1\Output\VFRAC_OUT\MIXED": vapor_mixed,
            r"\Data\Streams\S-1\Output\STRM_UPP": strm_upp,
            r"\Data\Streams\S-1\Output\STRM_UPP\MUMX": mumx,
            r"\Data\Streams\S-1\Output\STRM_UPP\MUMX\MIXED": mumx_mixed,
            r"\Data\Streams\S-1\Output\STRM_UPP\MUMX\MIXED\LIQUID": liquid_mu,
            r"\Data\Streams\S-1\Output\MOLEFRAC": molefrac,
            r"\Data\Streams\S-1\Output\MOLEFRAC\MIXED": mole_mixed,
            r"\Data\Blocks": blocks_root,
            r"\Data\Blocks\P-101": pump,
            r"\Data\Blocks\P-101\Input": pump_input,
            r"\Data\Blocks\P-101\Input\DEFF": driver_efficiency,
            r"\Data\Blocks\P-101\Output": pump_output,
            r"\Data\Blocks\P-101\Output\BLKSTAT": block_status,
            r"\Data\Blocks\P-101\Output\HEAD_CAL": pump_head,
            r"\Data\Blocks\P-101\Output\B_WNET": pump_wnet_fallback,
            r"\Data\Blocks\P-101\Output\BRAKE_POWER": brake_power,
            r"\Data\Blocks\P-101\Output\VENDOR_SPECIAL": vendor_card,
            r"\Data\Blocks\X-1": unknown,
            r"\Data\Blocks\X-1\Output": unknown_output,
            r"\Data\Blocks\X-1\Output\BLKSTAT": unknown_status,
            r"\Data\Blocks\X-1\Output\CUSTOM_FLOW": unknown_card,
        }
        failing_npsha_path = r"\Data\Blocks\P-101\Output\NPSHA"
        failing_wnet_path = r"\Data\Blocks\P-101\Output\WNET"
        tree = StrictReadOnlyTree(
            mapping,
            failing_paths=[failing_npsha_path, failing_wnet_path],
        )
        bundle, warnings, coverage = aspen_com_import.extract_bundle_with_coverage(
            tree,
            {
                "case_id": "STRICT-FAKE",
                "pressure_basis": "absolute",
                "run_status": {
                    "terminal_errors": 0,
                    "severe_errors": 0,
                    "errors": 0,
                    "warnings": 0,
                },
            },
            {
                "POWER": "kW",
                "HEAD": "m",
                "PRESSURE": "bar",
                "TEMPERATURE": "C",
                "MASS-FLOW": "kg/hr",
                "VOLUME-FLOW": "cum/hr",
                "VISCOSITY": "cP",
            },
        )

        self.assertEqual(bundle["schema"], "aspen-equipment-export-v1")
        json.dumps(bundle, ensure_ascii=False, allow_nan=False)
        schema = json.loads(
            (
                PACKAGE_ROOT
                / "knowledge_graph"
                / "aspen_equipment_export.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(bundle)
        self.assertNotIn("extraction_coverage", bundle)
        coverage_schema = json.loads(
            (
                PACKAGE_ROOT
                / "knowledge_graph"
                / "aspen_extraction_coverage.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(coverage_schema)
        Draft202012Validator(coverage_schema).validate(coverage)
        self.assertEqual(coverage["registry_audit"]["status"], "PASS")
        self.assertTrue(coverage["requested_status_invariant"])
        self.assertGreater(coverage["counts"]["requested"], 0)
        self.assertGreater(coverage["counts"]["found"], 0)
        self.assertGreater(coverage["counts"]["missing"], 0)
        self.assertEqual(coverage["counts"]["error"], 1)
        self.assertEqual(coverage["counts"]["unsupported"], 3)
        self.assertEqual(coverage["recovered_error_count"], 1)
        self.assertEqual(
            bundle["case"]["com_extraction_coverage_summary"]["counts"],
            coverage["counts"],
        )

        pump_row = next(row for row in bundle["blocks"] if row["block_id"] == "P-101")
        pump_coverage = next(
            row
            for row in coverage["objects"]
            if row["scope"] == "block" and row["object_id"] == "P-101"
        )
        self.assertAlmostEqual(pump_row["BRAKE_POWER"], 4.2)
        self.assertEqual(
            pump_row["aspen_raw_paths"]["BRAKE_POWER"],
            r"\Data\Blocks\P-101\Output\BRAKE_POWER",
        )
        self.assertEqual(pump_row["aspen_raw_values"]["BRAKE_POWER"]["unit"], "kW")
        self.assertNotIn("field_observations", pump_row)
        self.assertNotIn("VOLUME", pump_coverage["fields"])
        self.assertEqual(pump_coverage["fields"]["NPSHA"]["status"], "error")
        self.assertEqual(pump_coverage["fields"]["NPSHA"]["path"], failing_npsha_path)
        self.assertEqual(pump_coverage["fields"]["WNET"]["status"], "found")
        self.assertEqual(pump_coverage["fields"]["WNET"]["recovered_error_count"], 1)
        self.assertEqual(
            pump_coverage["fields"]["WNET"]["path"],
            r"\Data\Blocks\P-101\Output\B_WNET",
        )
        for observation in pump_coverage["fields"].values():
            self.assertTrue({"path", "unit", "status", "provenance"}.issubset(observation))
        self.assertEqual(
            pump_coverage["registry_completeness_status"],
            "NOT_SCORABLE_REGISTERED_FIELD_ERRORS",
        )
        self.assertIsNone(pump_coverage["registry_completeness_rate"])

        stream_row = bundle["streams"][0]
        self.assertAlmostEqual(stream_row["TEMP_OUT"], 25.0)
        self.assertNotIn("field_observations", stream_row)
        self.assertEqual(
            set(stream_row["composition"][0]),
            {"component_id", "fraction", "basis", "source_path"},
        )
        component_observation = coverage["composition_extraction"]["rows"][0][
            "component_observations"
        ][0]
        self.assertTrue(
            {"path", "unit", "status", "provenance"}.issubset(component_observation)
        )

        unmapped_paths = {
            row["path"] for row in coverage["unmapped_fields"]
        }
        self.assertIn(r"\Data\Blocks\P-101\Output\VENDOR_SPECIAL", unmapped_paths)
        self.assertIn(r"\Data\Blocks\X-1\Output\CUSTOM_FLOW", unmapped_paths)
        self.assertEqual(len(coverage["unmapped_modules"]), 1)
        unknown_coverage = next(
            row
            for row in coverage["objects"]
            if row["scope"] == "block" and row["object_id"] == "X-1"
        )
        self.assertEqual(unknown_coverage["registry_field_hit_rate"], 1.0)
        self.assertIsNone(unknown_coverage["registry_completeness_rate"])
        self.assertIn("NOT_SCORABLE", unknown_coverage["registry_completeness_status"])
        self.assertIsNone(coverage["registry_completeness_rate"])
        self.assertEqual(
            coverage["registry_completeness_status"],
            "NOT_SCORABLE_UNSUPPORTED_REGISTRY_IDENTITIES",
        )
        self.assertTrue(coverage["global_review_required"])
        self.assertIn(
            "REGISTERED_FIELD_ERRORS",
            coverage["global_review_reasons"],
        )
        self.assertIn(
            "UNSUPPORTED_REGISTRY_IDENTITIES",
            coverage["global_review_reasons"],
        )
        unknown_map = next(
            row for row in bundle["equipment_map"] if row["block_id"] == "X-1"
        )
        self.assertNotIn("mapping_status", unknown_map)
        self.assertNotIn("block_type", unknown_map)
        self.assertNotIn("process_function", unknown_map)
        self.assertNotIn("physical equipment role requires review", json.dumps(unknown_map))
        self.assertTrue(all("value" not in row for row in coverage["unmapped_fields"]))
        self.assertIn(
            "UNSUPPORTED_BLOCK_MODULE",
            {item["code"] for item in warnings},
        )
        self.assertIn(
            "COM_FIELD_EXTRACTION_RECOVERED_AFTER_ERROR",
            {item["code"] for item in warnings},
        )
        self.assertIn(
            "BLOCKED_COM_UNSUPPORTED_REGISTRY_IDENTITIES",
            {item["code"] for item in warnings},
        )
        self.assertIn(
            "BLOCKED_COM_REGISTERED_FIELD_ERRORS",
            {item["code"] for item in warnings},
        )
        worker_status = aspen_com_import.classify_aspen_worker_status(
            {"status": "PASS"},
            bundle["case"]["com_extraction_blockers"],
            coverage,
        )
        self.assertEqual(worker_status, "BLOCKED_COM_EXTRACTION")
        self.assertFalse(
            aspen_com_import.worker_selection_result_available(
                worker_status,
                {"status": "PASS"},
            )
        )

    def test_registry_compatibility_views_are_derived_and_audited(self) -> None:
        audit = aspen_com_import.audit_extraction_registry()

        self.assertEqual(audit["status"], "PASS")
        self.assertTrue(all(audit["legacy_compatibility_views"].values()))
        self.assertEqual(
            aspen_com_import.STREAM_FIELDS,
            {
                field: list(spec["paths"])
                for field, spec in aspen_com_import.STREAM_FIELD_REGISTRY.items()
            },
        )
        self.assertEqual(
            aspen_com_import.BLOCK_FIELDS,
            {
                field: list(spec["paths"])
                for field, spec in aspen_com_import.BLOCK_FIELD_REGISTRY.items()
                if field not in aspen_com_import.BLOCK_COMMON_FIELDS
            },
        )
        self.assertIn("never assign a generic", audit["unknown_module_policy"])
        schema = json.loads(
            (
                PACKAGE_ROOT
                / "knowledge_graph"
                / "aspen_equipment_export.schema.json"
            ).read_text(encoding="utf-8")
        )
        legacy_bundle = json.loads(
            (APP_DIR / "fixtures" / "mock_aspen_pump.json").read_text(
                encoding="utf-8"
            )
        )["bundle"]
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(legacy_bundle)
        read_only_bundle = json.loads(json.dumps(legacy_bundle))
        read_only_bundle["case"]["run_status"] = None
        Draft202012Validator(schema).validate(read_only_bundle)
        with patch.dict(
            aspen_com_import.PROCESS_FUNCTIONS,
            {"BROKEN_VIEW": "must fail audit"},
        ):
            broken = aspen_com_import.audit_extraction_registry()
        self.assertEqual(broken["status"], "FAIL")
        self.assertIn(
            "PROCESS_FUNCTIONS_VIEW_DIVERGED_FROM_MODULE_REGISTRY",
            {item["code"] for item in broken["issues"]},
        )

    def test_registered_field_error_is_not_scorable_and_blocks_worker(self) -> None:
        summary = aspen_com_import.summarize_field_coverage({
            "NPSHA": {
                "status": "error",
                "recovered_error_count": 0,
            },
            "HEAD_CAL": {
                "status": "found",
                "recovered_error_count": 0,
            },
        })
        self.assertEqual(
            summary["registry_completeness_status"],
            "NOT_SCORABLE_REGISTERED_FIELD_ERRORS",
        )
        self.assertIsNone(summary["registry_completeness_rate"])
        coverage = {
            "registry_completeness_status": (
                "NOT_SCORABLE_REGISTERED_FIELD_ERRORS"
            ),
            "root_diagnostics": [],
            "tree_discovery_truncated_object_count": 0,
            "case_discovery_budget": {
                "node_budget_exhausted": False,
                "metadata_budget_exhausted": False,
            },
        }
        self.assertEqual(
            aspen_com_import.classify_aspen_worker_status(
                {"status": "PASS"},
                [],
                coverage,
            ),
            "BLOCKED_COM_EXTRACTION",
        )

        def container(name: str, *children: StrictReadOnlyNode) -> StrictReadOnlyNode:
            return StrictReadOnlyNode(
                name,
                None,
                value_type=0,
                children=list(children),
            )

        status_leaf = StrictReadOnlyNode(
            "BLKSTAT",
            0,
            unit="",
            value_type=1,
        )
        output = container("Output", status_leaf)
        pump = StrictReadOnlyNode(
            "P-ERROR",
            "PUMP_ICON",
            record_type="PUMP",
            children=[output],
        )
        tree = StrictReadOnlyTree(
            {
                r"\Data\Streams": container("Streams"),
                r"\Data\Blocks": container("Blocks", pump),
                r"\Data\Blocks\P-ERROR": pump,
                r"\Data\Blocks\P-ERROR\Output": output,
                r"\Data\Blocks\P-ERROR\Output\BLKSTAT": status_leaf,
            },
            failing_paths=[r"\Data\Blocks\P-ERROR\Output\NPSHA"],
        )
        bundle, warnings, extracted_coverage = (
            aspen_com_import.extract_bundle_with_coverage(
                tree,
                {
                    "case_id": "REGISTERED-ERROR",
                    "pressure_basis": "absolute",
                    "run_status": None,
                },
            )
        )
        self.assertEqual(
            extracted_coverage["registry_completeness_status"],
            "NOT_SCORABLE_REGISTERED_FIELD_ERRORS",
        )
        self.assertEqual(
            extracted_coverage["global_review_reasons"],
            ["REGISTERED_FIELD_ERRORS"],
        )
        self.assertTrue(extracted_coverage["global_review_required"])
        self.assertIn(
            "BLOCKED_COM_REGISTERED_FIELD_ERRORS",
            {item["code"] for item in warnings},
        )
        self.assertTrue(bundle["case"]["com_extraction_blockers"])
        coverage_schema = json.loads(
            (
                PACKAGE_ROOT
                / "knowledge_graph"
                / "aspen_extraction_coverage.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(coverage_schema).validate(extracted_coverage)

    def test_discovery_never_persists_sensitive_long_or_nonfinite_raw_values(self) -> None:
        secret = "sentinel-sensitive-test-value"
        long_value = "Z" * 10000
        password = StrictReadOnlyNode("PASSWORD", secret, unit="")
        long_note = StrictReadOnlyNode("LONG_NOTE", long_value, unit="")
        nan_card = StrictReadOnlyNode("NAN_CARD", float("nan"), unit="")
        output = StrictReadOnlyNode(
            "Output",
            None,
            value_type=0,
            children=[password, long_note, nan_card],
        )
        tree = StrictReadOnlyTree({r"\Data\Blocks\X-2\Output": output})

        discovery = aspen_com_import.discover_object_tree(
            tree,
            scope="block",
            object_id="X-2",
            base=r"\Data\Blocks\X-2",
            mapped_paths=[],
            module_type="VENDORX",
        )
        fields = {item["field"]: item for item in discovery["fields"]}
        serialized = json.dumps(discovery, ensure_ascii=False, allow_nan=False)

        self.assertEqual(set(fields), {"PASSWORD", "LONG_NOTE", "NAN_CARD"})
        self.assertTrue(all("value" not in item for item in fields.values()))
        self.assertNotIn(secret, serialized)
        self.assertNotIn("Z" * 100, serialized)
        self.assertEqual(fields["PASSWORD"]["value_size"], len(secret))
        self.assertEqual(
            fields["PASSWORD"]["value_sha256"],
            hashlib.sha256(secret.encode("utf-8")).hexdigest().upper(),
        )
        self.assertEqual(fields["LONG_NOTE"]["value_size"], 10000)
        self.assertEqual(
            fields["NAN_CARD"]["value_metadata_status"],
            "rejected_non_finite",
        )
        self.assertIn(
            "NON_FINITE_COM_VALUE_REJECTED",
            {
                item["provenance"].get("error")
                for item in discovery["errors"]
            },
        )
        with self.assertRaisesRegex(ValueError, "NON_FINITE"):
            aspen_com_import._json_safe_com_value(float("inf"))
        scalar_path = r"\Data\Blocks\X-2\Output\BLKSTAT"
        scalar_tree = StrictReadOnlyTree({
            scalar_path: StrictReadOnlyNode(
                "BLKSTAT",
                float("inf"),
                unit="",
                value_type=1,
            ),
        })
        scalar_observation = aspen_com_import.read_registered_field(
            scalar_tree,
            r"\Data\Blocks\X-2",
            "BLKSTAT",
            aspen_com_import.BLOCK_FIELD_REGISTRY["BLKSTAT"],
        )
        self.assertEqual(scalar_observation["status"], "error")
        self.assertEqual(
            scalar_observation["errors"][0]["value_metadata"]["value_metadata_status"],
            "rejected_non_finite",
        )
        bounded = aspen_com_import._json_safe_com_value("A" * 5000)
        self.assertLess(len(bounded), 600)
        self.assertIn("<truncated>", bounded)
        surrogate = aspen_com_import._json_safe_com_value("\ud800" * 1000)
        surrogate.encode("utf-8")
        error_text = aspen_com_import._safe_error_text(
            RuntimeError("password=do-not-retain " + "E" * 5000)
        )
        self.assertNotIn("do-not-retain", error_text)
        self.assertLess(len(error_text), 600)
        self.assertIn("<truncated>", error_text)
        redacted = aspen_com_import._safe_text(
            'OPENAI_API_KEY=sk-fake-one '
            '"EQUIPMENT_DESIGN_LLM_API_KEY": "sk-fake-two" '
            'Authorization: Bearer fake-bearer-token '
            'Authorization Bearer fake-bearer-token-two'
        )
        self.assertNotIn("sk-fake-one", redacted)
        self.assertNotIn("sk-fake-two", redacted)
        self.assertNotIn("fake-bearer-token", redacted)
        self.assertNotIn("fake-bearer-token-two", redacted)
        self.assertGreaterEqual(redacted.count("<redacted>"), 4)

    def test_case_discovery_node_and_metadata_budgets_fail_closed(self) -> None:
        long_leaf = StrictReadOnlyNode("LONG", "X" * 10000)
        output = StrictReadOnlyNode(
            "Output",
            None,
            value_type=0,
            children=[long_leaf],
        )
        tree = StrictReadOnlyTree({r"\Data\Blocks\BUDGET\Output": output})
        metadata_budget = aspen_com_import.new_case_discovery_budget()
        metadata_budget["max_metadata_bytes"] = 256
        result = aspen_com_import.discover_object_tree(
            tree,
            scope="block",
            object_id="BUDGET",
            base=r"\Data\Blocks\BUDGET",
            mapped_paths=[],
            case_budget=metadata_budget,
        )
        self.assertTrue(result["truncated"])
        self.assertTrue(result["budget_exhausted"]["metadata"])
        self.assertTrue(metadata_budget["metadata_budget_exhausted"])
        field = result["fields"][0]
        self.assertIsNone(field["value_size"])
        self.assertIsNone(field["value_sha256"])
        self.assertEqual(
            field["value_metadata_status"],
            "metadata_budget_exceeded_not_hashed",
        )
        self.assertNotIn("X" * 100, json.dumps(result))
        self.assertGreater(metadata_budget["metadata_bytes_output"], 0)
        self.assertEqual(
            metadata_budget["metadata_bytes_consumed"],
            metadata_budget["max_metadata_bytes"],
        )

        hostile_name = "N" * 200000
        hostile_output = StrictReadOnlyNode(
            "Output",
            None,
            value_type=0,
            children=[StrictReadOnlyNode(hostile_name, 1.0, unit="kg/hr")],
        )
        hostile_tree = StrictReadOnlyTree({
            r"\Data\Blocks\HOSTILE\Output": hostile_output,
        })
        hostile_budget = aspen_com_import.new_case_discovery_budget()
        hostile = aspen_com_import.discover_object_tree(
            hostile_tree,
            scope="block",
            object_id="HOSTILE",
            base=r"\Data\Blocks\HOSTILE",
            mapped_paths=[],
            case_budget=hostile_budget,
        )
        hostile_serialized = json.dumps(hostile, ensure_ascii=False)
        self.assertTrue(hostile["truncated"])
        self.assertTrue(hostile["budget_exhausted"]["metadata"])
        self.assertTrue(hostile_budget["metadata_budget_exhausted"])
        self.assertEqual(hostile_budget["metadata_text_values_truncated"], 1)
        self.assertNotIn("N" * 1000, hostile_serialized)
        self.assertLess(len(hostile_serialized), 10000)

        node_budget = aspen_com_import.new_case_discovery_budget()
        node_budget["max_nodes"] = 1
        first = aspen_com_import.discover_object_tree(
            tree,
            scope="block",
            object_id="FIRST",
            base=r"\Data\Blocks\BUDGET",
            mapped_paths=[],
            case_budget=node_budget,
        )
        second = aspen_com_import.discover_object_tree(
            tree,
            scope="block",
            object_id="SECOND",
            base=r"\Data\Blocks\BUDGET",
            mapped_paths=[],
            case_budget=node_budget,
        )
        self.assertTrue(first["truncated"])
        self.assertTrue(second["truncated"])
        self.assertTrue(second["budget_exhausted"]["node"])
        self.assertTrue(node_budget["node_budget_exhausted"])

    def test_extract_bundle_surfaces_root_collection_enumeration_failures(self) -> None:
        class BrokenRootElements:
            @property
            def Count(self):
                raise RuntimeError("root count unavailable")

            @staticmethod
            def Item(index: int):
                raise RuntimeError(f"root item unavailable {index}")

        broken_stream_root = type(
            "BrokenStreamRoot",
            (),
            {"Elements": BrokenRootElements()},
        )()
        empty_block_root = StrictReadOnlyNode(
            "Blocks",
            None,
            value_type=0,
        )
        tree = StrictReadOnlyTree({
            r"\Data\Streams": broken_stream_root,
            r"\Data\Blocks": empty_block_root,
        })

        bundle, warnings, coverage = aspen_com_import.extract_bundle_with_coverage(
            tree,
            {
                "case_id": "BROKEN-ROOT",
                "pressure_basis": "absolute",
                "run_status": {
                    "terminal_errors": 0,
                    "severe_errors": 0,
                    "errors": 0,
                    "warnings": 0,
                },
            },
        )

        self.assertFalse(bundle["streams"])
        self.assertGreater(
            coverage["discovery_error_count"],
            0,
        )
        self.assertIn(
            "BLOCKED_COM_TREE_ROOT_ENUMERATION_ERROR",
            {item["code"] for item in warnings},
        )
        self.assertTrue(coverage["root_diagnostics"])
        self.assertEqual(
            aspen_com_import.classify_aspen_worker_status(
                {"status": "PASS"},
                bundle["case"]["com_extraction_blockers"],
                coverage,
            ),
            "BLOCKED_COM_EXTRACTION",
        )

    def test_non_material_streams_are_excluded_from_material_field_and_composition_rates(self) -> None:
        duty = StrictReadOnlyNode("DUTY", 50.0, unit="kW")
        output = StrictReadOnlyNode(
            "Output",
            None,
            value_type=0,
            children=[duty],
        )
        heat_stream = StrictReadOnlyNode(
            "Q-1",
            "HEAT_ICON",
            record_type="HEAT",
            children=[output],
        )
        streams_root = StrictReadOnlyNode(
            "Streams",
            None,
            value_type=0,
            children=[heat_stream],
        )
        blocks_root = StrictReadOnlyNode("Blocks", None, value_type=0)
        tree = StrictReadOnlyTree({
            r"\Data\Streams": streams_root,
            r"\Data\Streams\Q-1\Output": output,
            r"\Data\Blocks": blocks_root,
        })

        bundle, _, coverage = aspen_com_import.extract_bundle_with_coverage(
            tree,
            {
                "case_id": "HEAT-STREAM",
                "pressure_basis": "absolute",
                "run_status": {
                    "terminal_errors": 0,
                    "severe_errors": 0,
                    "errors": 0,
                    "warnings": 0,
                },
            },
        )

        row = bundle["streams"][0]
        row_coverage = next(
            item for item in coverage["objects"] if item["object_id"] == "Q-1"
        )
        self.assertEqual(row_coverage["counts"]["requested"], 0)
        self.assertIsNone(row_coverage["registry_field_hit_rate"])
        self.assertIsNone(row_coverage["registry_completeness_rate"])
        self.assertEqual(
            row_coverage["composition_extraction"]["status"],
            "NOT_APPLICABLE_NON_MATERIAL_STREAM",
        )
        composition = coverage["composition_extraction"]
        self.assertEqual(composition["material_stream_count"], 0)
        self.assertEqual(composition["non_material_stream_count"], 1)
        self.assertEqual(composition["requested_vector_count"], 0)
        self.assertEqual(composition["status"], "NOT_APPLICABLE_NO_MATERIAL_STREAMS")

    def test_history_clean_statement_is_zero(self) -> None:
        parsed = aspen_com_import.parse_aspen_history("NO ERRORS OR WARNINGS GENERATED\n")
        self.assertTrue(parsed["found"])
        self.assertEqual(parsed["counts"], {"terminal_errors": 0, "severe_errors": 0, "errors": 0, "warnings": 0})
        self.assertEqual(parsed["problem_lines"], [])

    def test_history_summary_sums_all_columns(self) -> None:
        text = """*** SUMMARY OF ERRORS ***
                 PHYSICAL
                 PROPERTY  SYSTEM  SIMULATION
TERMINAL ERRORS      0        0         0
  SEVERE ERRORS      0        0         1
         ERRORS      0        0         2
       WARNINGS      1        0         3
"""
        parsed = aspen_com_import.parse_aspen_history(text)
        self.assertEqual(parsed["counts"], {"terminal_errors": 0, "severe_errors": 1, "errors": 2, "warnings": 4})

    def test_missing_history_never_becomes_fabricated_zero_status(self) -> None:
        parsed = aspen_com_import.parse_aspen_history("no run status was exported")
        self.assertFalse(parsed["found"])
        self.assertIsNone(aspen_com_import.verified_run_status(parsed))

    def test_clean_counts_without_raw_history_are_not_formal_run_evidence(self) -> None:
        parsed = aspen_com_import.parse_aspen_history(
            "NO ERRORS OR WARNINGS GENERATED\n"
        )
        missing = aspen_com_import.classify_run_evidence(
            parsed,
            run_requested=True,
            raw_history_present=False,
        )
        clean = aspen_com_import.classify_run_evidence(
            parsed,
            run_requested=True,
            raw_history_present=True,
        )
        self.assertEqual(missing["status"], "RUN_EVIDENCE_MISSING")
        self.assertFalse(missing["clean"])
        self.assertEqual(clean["status"], "CLEAN_RUN_EVIDENCE")
        self.assertTrue(clean["clean"])

    def test_pump_com_power_cards_are_independent_and_wnet_is_not_brake_power(self) -> None:
        self.assertEqual(
            aspen_com_import.BLOCK_FIELDS["WNET"],
            [r"Output\WNET", r"Output\B_WNET"],
        )
        self.assertEqual(
            aspen_com_import.BLOCK_FIELDS["FLUID_POWER"],
            [r"Output\FLUID_POWER"],
        )
        self.assertEqual(
            aspen_com_import.BLOCK_FIELDS["BRAKE_POWER"],
            [r"Output\BRAKE_POWER"],
        )
        self.assertEqual(
            aspen_com_import.BLOCK_FIELDS["ELEC_POWER"],
            [r"Output\ELEC_POWER"],
        )
        self.assertEqual(
            aspen_com_import.BLOCK_FIELDS["DEFF"],
            [r"Input\DEFF"],
        )
        self.assertEqual(
            aspen_com_import.block_field_quantity_kind("WNET", "PUMP"),
            "pump_electrical_utility_power_not_shaft_power",
        )
        self.assertEqual(
            aspen_com_import.block_field_quantity_kind("BRAKE_POWER", "PUMP"),
            "pump_brake_shaft_power",
        )
        self.assertEqual(
            aspen_com_import.resolve_aspen_unit("DEFF", ""),
            ("fraction", True),
        )

    def test_final_history_separates_three_pump_power_channels_and_input_deff(self) -> None:
        text = """
        320    BLOCK P-1 PUMP
        321        PARAM PRES=25. EFF=0.75 DEFF=.98 PUMP-TYPE=PUMP
        322        UTILITY UTILITY-ID=ELEC
        323
        324    BLOCK P-2 PUMP
        325        PARAM PRES=25. EFF=0.75 DEFF=.91

      UOS BLOCK P-1      MODEL: PUMP
      FLUID PWR  =   1000.     , BRAKE PWR  =   2000.     , ELEC PWR   =   3000.

      GENERATING RESULTS FOR UOS BLOCK P-1      MODEL: PUMP
      VOL-FLOW   =  0.2535E-02 , DELTA P    =  0.1252E+07 , PUMP EFF   =  0.7500
      FLUID PWR  =   3173.     , BRAKE PWR  =   4230.     , ELEC PWR   =   4316.
      NPSH AVAIL =   63.41
"""

        recovered = aspen_com_import.parse_history_block_results(text)
        pump = recovered["P-1"]

        self.assertNotIn("WNET", pump)
        expected = {
            "FLUID_POWER": (3.173, 3173.0, "pump_hydraulic_fluid_power"),
            "BRAKE_POWER": (4.230, 4230.0, "pump_brake_shaft_power"),
            "ELEC_POWER": (4.316, 4316.0, "pump_electrical_input_power"),
        }
        for field, (value_kw, raw_w, quantity_kind) in expected.items():
            with self.subTest(field=field):
                self.assertAlmostEqual(pump[field]["value"], value_kw)
                self.assertEqual(pump[field]["unit"], "kW")
                self.assertAlmostEqual(pump[field]["raw_value"], raw_w)
                self.assertEqual(pump[field]["raw_unit"], "W")
                self.assertEqual(pump[field]["quantity_kind"], quantity_kind)
                self.assertIn("/ 1000", pump[field]["transform"])
                self.assertEqual(
                    pump[field]["source"],
                    f"Aspen .his UOS PUMP result field {pump[field]['history_label']}",
                )
        self.assertAlmostEqual(pump["DEFF"]["value"], 0.98)
        self.assertEqual(pump["DEFF"]["unit"], "fraction")
        self.assertAlmostEqual(pump["DEFF"]["raw_value"], 0.98)
        self.assertEqual(
            pump["DEFF"]["quantity_kind"],
            "pump_driver_efficiency_fraction",
        )
        self.assertIn("identity", pump["DEFF"]["transform"])
        self.assertEqual(
            pump["DEFF"]["source"],
            "Aspen .his PUMP input PARAM card field DEFF",
        )

    def test_pump_history_merge_is_hash_bound_and_fills_only_missing_channels(self) -> None:
        text = """
        320    BLOCK P-1 PUMP
        321        PARAM PRES=25. EFF=0.75 DEFF=.98 PUMP-TYPE=PUMP
        324    BLOCK P-2 PUMP
        325        PARAM PRES=25. EFF=0.75 DEFF=.91

      GENERATING RESULTS FOR UOS BLOCK P-1      MODEL: PUMP
      FLUID PWR  =   3173.     , BRAKE PWR  =   4230.     , ELEC PWR   =   4316.
"""
        root = (
            PACKAGE_ROOT
            / "outputs"
            / "app_test_runs"
            / f"pump_power_history_{uuid.uuid4().hex}"
        )
        root.mkdir(parents=True, exist_ok=False)
        history = root / "raw_aspen_run_history.his"
        history.write_text(text, encoding="utf-8")
        bundle = {
            "units": {
                "block.P-1.WNET": "kW",
                "block.WNET": "kW",
                "block.P-1.BRAKE_POWER": "kW",
                "block.BRAKE_POWER": "kW",
            },
            "blocks": [{
                "block_id": "P-1",
                "block_type": "PUMP",
                "WNET": 4.31638144,
                # A defined COM value is authoritative and must not be
                # overwritten by the history fallback.
                "BRAKE_POWER": 4.231,
                "aspen_raw_paths": {
                    "WNET": r"\Data\Blocks\P-1\Output\WNET",
                    "BRAKE_POWER": r"\Data\Blocks\P-1\Output\BRAKE_POWER",
                },
                "aspen_raw_values": {
                    "WNET": {
                        "value": 4.31638144,
                        "unit": "kW",
                        "quantity_kind": (
                            "pump_electrical_utility_power_not_shaft_power"
                        ),
                    },
                    "BRAKE_POWER": {
                        "value": 4.231,
                        "unit": "kW",
                        "quantity_kind": "pump_brake_shaft_power",
                    },
                },
            }],
        }
        recovered = aspen_com_import.parse_history_block_results(text)
        try:
            diagnostics = aspen_com_import.merge_history_block_results(
                bundle,
                recovered,
                history,
            )
            history_hash = aspen_com_import.sha256(history)
        finally:
            shutil.rmtree(root, ignore_errors=True)

        pump = bundle["blocks"][0]
        self.assertAlmostEqual(pump["WNET"], 4.31638144)
        self.assertAlmostEqual(pump["BRAKE_POWER"], 4.231)
        self.assertAlmostEqual(pump["FLUID_POWER"], 3.173)
        self.assertAlmostEqual(pump["ELEC_POWER"], 4.316)
        self.assertAlmostEqual(pump["DEFF"], 0.98)
        self.assertEqual(bundle["units"]["block.P-1.FLUID_POWER"], "kW")
        self.assertEqual(bundle["units"]["block.P-1.ELEC_POWER"], "kW")
        self.assertEqual(bundle["units"]["block.P-1.DEFF"], "fraction")
        self.assertNotIn(
            "BRAKE_POWER",
            {row["field"] for row in diagnostics},
        )
        for field in ("FLUID_POWER", "ELEC_POWER", "DEFF"):
            with self.subTest(field=field):
                observation = pump["aspen_raw_values"][field]
                self.assertEqual(observation["source_file_sha256"], history_hash)
                self.assertTrue(observation["source"])
                self.assertTrue(observation["transform"])
                diagnostic = next(
                    row for row in diagnostics if row["field"] == field
                )
                self.assertEqual(diagnostic["source_file_sha256"], history_hash)
                self.assertEqual(
                    diagnostic["quantity_kind"],
                    observation["quantity_kind"],
                )
                self.assertEqual(
                    diagnostic["transform"],
                    observation["transform"],
                )

    def test_final_history_results_recover_missing_pump_npsha_and_heatx_duty(self) -> None:
        text = """
      UOS BLOCK B4       MODEL: HEATX
                              DUTY=0.11111E+07
      GENERATING RESULTS FOR UOS BLOCK B1       MODEL: PUMP
      VOL-FLOW   =  0.1504E-01 , DELTA P    =  0.4000E+06
      NPSH AVAIL =   24.08
      GENERATING RESULTS FOR UOS BLOCK B4       MODEL: HEATX
      AREA= 29.452           DUTY=0.23267E+07       FT=1.00000
"""

        recovered = aspen_com_import.parse_history_block_results(text)

        self.assertAlmostEqual(recovered["B1"]["NPSHA"]["value"], 24.08)
        self.assertEqual(recovered["B1"]["NPSHA"]["unit"], "kPa")
        self.assertAlmostEqual(recovered["B1"]["NPSHA"]["raw_value"], 24.08)
        self.assertEqual(recovered["B1"]["NPSHA"]["raw_unit"], "kPa")
        self.assertEqual(
            recovered["B1"]["NPSHA"]["quantity_kind"],
            "available_suction_pressure_margin",
        )
        self.assertIn(
            "no pressure-to-head conversion",
            recovered["B1"]["NPSHA"]["transform"],
        )
        self.assertEqual(
            recovered["B1"]["NPSHA"]["status"],
            "raw_history_pressure_margin_fallback",
        )
        self.assertEqual(
            recovered["B1"]["NPSHA"]["source"],
            "Aspen .his UOS PUMP result field NPSH AVAIL",
        )
        self.assertAlmostEqual(recovered["B4"]["QCALC"]["value"], 2326.7)
        self.assertEqual(recovered["B4"]["QCALC"]["unit"], "kW")
        self.assertAlmostEqual(recovered["B4"]["AREA"]["value"], 29.452)
        self.assertEqual(recovered["B4"]["AREA"]["unit"], "m2")

    def test_history_npsh_fallback_persists_kpa_pressure_margin_lineage(self) -> None:
        root = PACKAGE_ROOT / "outputs" / "app_test_runs" / f"npsh_history_fallback_{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        history = root / "raw_aspen_run_history.his"
        history.write_text(
            """
      GENERATING RESULTS FOR UOS BLOCK P-101       MODEL: PUMP
      NPSH AVAIL =   63.41
""",
            encoding="utf-8",
        )
        bundle = {
            "units": {},
            "blocks": [{
                "block_id": "P-101",
                "aspen_raw_paths": {},
                "aspen_raw_values": {},
            }],
        }
        recovered = aspen_com_import.parse_history_block_results(
            history.read_text(encoding="utf-8")
        )
        try:
            diagnostics = aspen_com_import.merge_history_block_results(
                bundle,
                recovered,
                history,
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

        block = bundle["blocks"][0]
        observation = block["aspen_raw_values"]["NPSHA"]
        self.assertAlmostEqual(block["NPSHA"], 63.41)
        self.assertEqual(bundle["units"]["block.P-101.NPSHA"], "kPa")
        self.assertEqual(bundle["units"]["block.NPSHA"], "kPa")
        self.assertEqual(block["aspen_raw_paths"]["NPSHA"], "raw_history:P-101:NPSH AVAIL")
        self.assertAlmostEqual(observation["raw_value"], 63.41)
        self.assertEqual(observation["raw_unit"], "kPa")
        self.assertAlmostEqual(observation["value"], 63.41)
        self.assertEqual(observation["unit"], "kPa")
        self.assertEqual(
            observation["status"],
            "raw_history_pressure_margin_fallback",
        )
        self.assertEqual(
            observation["source"],
            "Aspen .his UOS PUMP result field NPSH AVAIL",
        )
        self.assertIn("no pressure-to-head conversion", observation["transform"])
        self.assertEqual(len(observation["source_file_sha256"]), 64)
        self.assertEqual(diagnostics[0]["unit"], "kPa")
        self.assertEqual(diagnostics[0]["raw_unit"], "kPa")
        self.assertEqual(
            diagnostics[0]["status"],
            "raw_history_pressure_margin_fallback",
        )
        self.assertEqual(diagnostics[0]["source"], observation["source"])
        self.assertEqual(diagnostics[0]["transform"], observation["transform"])

    def test_history_fallback_fills_only_missing_com_values_with_hashed_lineage(self) -> None:
        root = PACKAGE_ROOT / "outputs" / "app_test_runs" / f"history_fallback_{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        history = root / "raw_aspen_run_history.his"
        history.write_text("final history", encoding="utf-8")
        bundle = {
            "units": {"block.AREA": "m2"},
            "blocks": [{
                "block_id": "B4",
                "AREA": 31.0,
                "aspen_raw_paths": {"AREA": r"\Data\Blocks\B4\Output\AREA"},
                "aspen_raw_values": {"AREA": {"value": 31.0, "unit": "m2"}},
            }],
        }
        recovered = {
            "B4": {
                "AREA": {"value": 29.452, "unit": "m2", "history_label": "AREA"},
                "QCALC": {
                    "value": 2326.7,
                    "unit": "kW",
                    "history_label": "DUTY",
                    "transform": "QCALC_kW = Aspen_history_DUTY_W / 1000",
                },
            }
        }
        try:
            diagnostics = aspen_com_import.merge_history_block_results(bundle, recovered, history)
        finally:
            shutil.rmtree(root, ignore_errors=True)

        block = bundle["blocks"][0]
        self.assertEqual(block["AREA"], 31.0)
        self.assertAlmostEqual(block["QCALC"], 2326.7)
        self.assertEqual(bundle["units"]["block.QCALC"], "kW")
        self.assertEqual(len(block["aspen_raw_values"]["QCALC"]["source_file_sha256"]), 64)
        self.assertEqual([row["code"] for row in diagnostics], ["RAW_HISTORY_RESULT_FALLBACK"])

    def test_block_specific_unit_overrides_a_different_case_wide_field_unit(self) -> None:
        normalized, errors = aspen_com_import.derivation.normalize_block(
            {
                "block_id": "B4",
                "block_type": "HEATX",
                "QCALC": 2326.7,
            },
            {
                "block.QCALC": "Gcal/hr",
                "block.B4.QCALC": "kW",
            },
        )

        self.assertFalse(errors)
        self.assertAlmostEqual(normalized["heat_duty_kw"], 2326.7)
        self.assertEqual(normalized["_sources"]["heat_duty_kw"]["source_unit"], "kW")

    def test_aspen_block_npsha_is_projected_into_the_pump_match_input(self) -> None:
        bundle = json.loads(
            (APP_DIR / "fixtures" / "mock_aspen_pump.json").read_text(encoding="utf-8")
        )["bundle"]
        pump = bundle["blocks"][0]
        pump["NPSHA"] = 8.4
        bundle["units"][f"block.{pump['block_id']}.NPSHA"] = "m"
        root = PACKAGE_ROOT / "outputs" / "app_test_runs" / f"npsha_projection_{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        export = root / "aspen_equipment_export.json"
        export.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        try:
            result = aspen_com_import.derivation.derive_bundle(bundle, export)
        finally:
            shutil.rmtree(root, ignore_errors=True)

        canonical = result["equipment"][0]["canonical_match_input"]
        self.assertAlmostEqual(canonical["npsha_m"], 8.4)

    def test_run_artifact_capture_uses_only_isolated_saveas_and_keeps_diagnostics(self) -> None:
        root = PACKAGE_ROOT / "outputs" / "app_test_runs" / f"run_capture_{uuid.uuid4().hex}"
        work = root / "isolated"
        output = root / "output"
        work.mkdir(parents=True, exist_ok=False)
        output.mkdir(parents=True, exist_ok=False)

        class FakeApp:
            @staticmethod
            def SaveAs(path: str) -> None:
                Path(path).write_bytes(b"isolated-bkp")

            @staticmethod
            def Export(export_type: int, path: str) -> None:
                Path(path).write_text(f"export={export_type}\n", encoding="utf-8")

        result = aspen_com_import.request_run_artifacts(FakeApp(), work, output)

        self.assertFalse(result["source_mutation_allowed"])
        self.assertEqual(result["save_as"]["status"], "PASS")
        self.assertEqual(result["save_as"]["filename"], "RUN_CAPTURE.bkp")
        self.assertTrue((work / "RUN_CAPTURE.bkp").is_file())
        self.assertEqual({item["status"] for item in result["exports"].values()}, {"PASS"})
        self.assertTrue((output / "raw_aspen_run_report.rep").is_file())
        self.assertTrue((output / "raw_aspen_run_summary.sum").is_file())
        self.assertTrue((output / "raw_aspen_run_messages.msg").is_file())

    def test_mock_pipeline_derives_pump_without_com(self) -> None:
        fixture = APP_DIR / "fixtures" / "mock_aspen_pump.json"
        output = PACKAGE_ROOT / "outputs" / "app_test_runs" / f"direct_mock_{uuid.uuid4().hex[:10]}"
        output.mkdir(parents=True, exist_ok=False)
        result = aspen_com_import.run_mock(fixture, output)
        self.assertEqual(result["status"], "PASS_MOCK")
        derived = result["result"]
        self.assertEqual(derived["formal_use_gate"], "ELIGIBLE_AS_PROCESS_BASIS")
        self.assertEqual(derived["equipment"][0]["match_result"]["match"]["family_id"], "family_pump")
        pfd_path = Path(result["pfd_mapping"])
        self.assertTrue(pfd_path.is_file())
        self.assertEqual(pfd_path.name, "aspen_pfd_mapping.json")
        self.assertEqual(result["pfd_summary"]["equipment_node_count"], 1)
        self.assertEqual(result["pfd_summary"]["edge_count"], 2)
        self.assertEqual(result["pfd_summary"]["topology_gate"]["status"], "PASS")
        self.assertEqual(result["mapping_sha256"], result["pfd_summary"]["mapping_sha256"])
        self.assertEqual(len(result["pfd_mapping_file_sha256"]), 64)

    def test_write_and_derive_persists_registry_coverage_as_separate_artifact(self) -> None:
        fixture = json.loads(
            (APP_DIR / "fixtures" / "mock_aspen_pump.json").read_text(
                encoding="utf-8"
            )
        )
        coverage = complete_legal_coverage_sidecar()
        output = (
            PACKAGE_ROOT
            / "outputs"
            / "app_test_runs"
            / f"coverage_artifact_{uuid.uuid4().hex[:10]}"
        )
        output.mkdir(parents=True, exist_ok=False)
        try:
            result = aspen_com_import.write_and_derive(
                fixture["bundle"],
                output,
                extraction_coverage=coverage,
            )
            artifact = result["extraction_coverage"]
            artifact_path = Path(artifact["path"])
            persisted = json.loads(artifact_path.read_text(encoding="utf-8"))
            persisted_bundle = json.loads(
                Path(result["bundle"]).read_text(encoding="utf-8")
            )
            self.assertEqual(artifact_path.name, "aspen_extraction_coverage.json")
            self.assertEqual(persisted["counts"]["found"], 1)
            self.assertEqual(artifact["sha256"], aspen_com_import.sha256(artifact_path))
            self.assertNotIn("extraction_coverage", persisted_bundle)
            self.assertEqual(
                persisted_bundle["case"]["com_extraction_coverage_path"],
                artifact_path.name,
            )
            self.assertEqual(
                persisted_bundle["case"]["com_extraction_coverage_sha256"],
                artifact["sha256"],
            )
            main_schema = json.loads(
                (
                    PACKAGE_ROOT
                    / "knowledge_graph"
                    / "aspen_equipment_export.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(main_schema).validate(persisted_bundle)
        finally:
            shutil.rmtree(output, ignore_errors=True)

    def test_write_and_derive_rejects_invalid_coverage_before_any_artifact_write(self) -> None:
        fixture = json.loads(
            (APP_DIR / "fixtures" / "mock_aspen_pump.json").read_text(
                encoding="utf-8"
            )
        )
        output = (
            PACKAGE_ROOT
            / "outputs"
            / "app_test_runs"
            / f"invalid_coverage_{uuid.uuid4().hex[:10]}"
        )
        output.mkdir(parents=True, exist_ok=False)
        invalid = {
            "schema": "aspen-com-extraction-coverage-v1",
            "counts": {"requested": 1, "found": 1},
        }
        try:
            with self.assertRaisesRegex(
                ValueError,
                "ASPEN_EXTRACTION_COVERAGE_SCHEMA_INVALID",
            ):
                aspen_com_import.write_and_derive(
                    fixture["bundle"],
                    output,
                    extraction_coverage=invalid,
                )
            self.assertFalse((output / "aspen_extraction_coverage.json").exists())
            self.assertFalse((output / "aspen_equipment_export.json").exists())
        finally:
            shutil.rmtree(output, ignore_errors=True)

    def test_blocked_com_extraction_persists_evidence_without_running_derivation(self) -> None:
        fixture = json.loads(
            (APP_DIR / "fixtures" / "mock_aspen_pump.json").read_text(
                encoding="utf-8"
            )
        )
        coverage = complete_legal_coverage_sidecar()
        coverage["registry_completeness_status"] = (
            "NOT_SCORABLE_UNSUPPORTED_REGISTRY_IDENTITIES"
        )
        coverage["registry_completeness_rate"] = None
        coverage["global_review_required"] = True
        coverage["global_review_reasons"] = [
            "UNSUPPORTED_REGISTRY_IDENTITIES"
        ]
        output = (
            PACKAGE_ROOT
            / "outputs"
            / "app_test_runs"
            / f"blocked_no_derivation_{uuid.uuid4().hex[:10]}"
        )
        output.mkdir(parents=True, exist_ok=False)
        try:
            with patch.object(
                aspen_com_import.derivation,
                "derive_bundle",
                side_effect=AssertionError("blocked extraction reached derivation"),
            ):
                result = aspen_com_import.write_and_derive(
                    fixture["bundle"],
                    output,
                    extraction_coverage=coverage,
                    allow_derivation=False,
                )
            self.assertIsNone(result["result"])
            self.assertIsNone(result["derivation"])
            self.assertEqual(
                result["derivation_skipped"],
                "BLOCKED_COM_EXTRACTION",
            )
            self.assertTrue((output / "aspen_equipment_export.json").is_file())
            self.assertTrue((output / "aspen_extraction_coverage.json").is_file())
            self.assertTrue((output / "aspen_pfd_mapping.json").is_file())
            self.assertFalse((output / "equipment_derivation_result.json").exists())
        finally:
            shutil.rmtree(output, ignore_errors=True)

    def test_pfd_is_written_immediately_after_export_even_if_later_derivation_fails(self) -> None:
        fixture = json.loads((APP_DIR / "fixtures" / "mock_aspen_pump.json").read_text(encoding="utf-8"))
        output = PACKAGE_ROOT / "outputs" / "app_test_runs" / f"pfd_before_derive_{uuid.uuid4().hex[:10]}"
        output.mkdir(parents=True, exist_ok=False)
        with patch.object(aspen_com_import.derivation, "derive_bundle", side_effect=RuntimeError("forced derivation failure")):
            with self.assertRaisesRegex(RuntimeError, "forced derivation failure"):
                aspen_com_import.write_and_derive(fixture["bundle"], output)
        pfd_path = output / "aspen_pfd_mapping.json"
        self.assertTrue((output / "aspen_equipment_export.json").is_file())
        self.assertTrue(pfd_path.is_file())
        mapping = json.loads(pfd_path.read_text(encoding="utf-8"))
        self.assertEqual(mapping["schema"], "equipment-design-pfd-mapping-v1")


if __name__ == "__main__":
    unittest.main()
