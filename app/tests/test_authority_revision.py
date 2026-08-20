from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator


APP_DIR = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = APP_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import authority_revision


def _write_schema_tree(root: Path) -> None:
    (root / "app" / "schemas").mkdir(parents=True)
    (root / "knowledge_graph").mkdir(parents=True)
    (root / "app" / "schemas" / "protocol.schema.json").write_text(
        '{"type":"object"}\n',
        encoding="utf-8",
    )
    (root / "knowledge_graph" / "aspen_equipment_export.schema.json").write_text(
        '{"title":"export-a"}\n',
        encoding="utf-8",
    )
    (root / "knowledge_graph" / "aspen_extraction_coverage.schema.json").write_text(
        '{"title":"coverage-a"}\n',
        encoding="utf-8",
    )


def _source_revision(schema_assets: dict[str, str]) -> dict[str, object]:
    core_assets = {
        key: "A" * 64 for key in authority_revision.CORE_ASSET_KEYS
    }
    source_assets = {
        path: "B" * 64
        for path in authority_revision.source_code_manifest.CORE_SOURCE_PATHS
    }
    revision: dict[str, object] = {
        "schema": authority_revision.AUTHORITY_REVISION_SCHEMA,
        "agent_protocol_version": authority_revision.AGENT_PROTOCOL_VERSION,
        "matcher_engine_version": "test",
        "core_asset_sha256": core_assets,
        "core_asset_set_sha256": authority_revision.canonical_sha256(core_assets),
        "schema_asset_sha256": schema_assets,
        "schema_asset_set_sha256": authority_revision.canonical_sha256(schema_assets),
        "source_code_sha256": source_assets,
        "source_code_set_sha256": authority_revision.canonical_sha256(source_assets),
        "source_code_manifest": {
            "status": "SOURCE_TREE_VERIFIED",
            "manifest_sha256": "C" * 64,
            "manifest_payload_sha256": "D" * 64,
            "path_set_sha256": authority_revision.canonical_sha256(
                list(authority_revision.source_code_manifest.CORE_SOURCE_PATHS)
            ),
            "file_count": len(
                authority_revision.source_code_manifest.CORE_SOURCE_PATHS
            ),
        },
        "runtime_manifest": {
            "status": "NOT_PACKAGED",
            "manifest_sha256": None,
            "bundle_revision": "",
        },
    }
    _seal_source_revision(revision)
    return revision


def _seal_source_revision(revision: dict[str, object]) -> None:
    revision["schema_asset_set_sha256"] = authority_revision.canonical_sha256(
        revision["schema_asset_sha256"]
    )
    runtime_manifest = revision["runtime_manifest"]
    assert isinstance(runtime_manifest, dict)
    runtime_manifest["bundle_revision"] = authority_revision.canonical_sha256({
        "core_asset_set_sha256": revision["core_asset_set_sha256"],
        "schema_asset_set_sha256": revision["schema_asset_set_sha256"],
        "source_code_set_sha256": revision["source_code_set_sha256"],
    })
    revision["authority_revision_sha256"] = authority_revision.canonical_sha256({
        key: value
        for key, value in revision.items()
        if key != "authority_revision_sha256"
    })


class AuthorityRevisionAspenSchemaTests(unittest.TestCase):
    def test_external_json_schema_accepts_the_current_authority_contract(self) -> None:
        schema_document = json.loads(
            (APP_DIR / "schemas" / "equipment_design_authority_revision.schema.json")
            .read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema_document)
        validator = Draft202012Validator(schema_document)
        source_assets = {
            relative_path: authority_revision._sha256_file(
                PACKAGE_ROOT / Path(relative_path)
            )
            for relative_path in authority_revision.source_code_manifest.CORE_SOURCE_PATHS
        }
        source_binding = {
            "status": "SOURCE_TREE_VERIFIED",
            "manifest_sha256": "A" * 64,
            "manifest_payload_sha256": "B" * 64,
            "path_set_sha256": authority_revision.canonical_sha256(
                list(authority_revision.source_code_manifest.CORE_SOURCE_PATHS)
            ),
            "file_count": len(
                authority_revision.source_code_manifest.CORE_SOURCE_PATHS
            ),
        }
        with patch.object(
            authority_revision,
            "_current_source_code_binding",
            return_value=(
                source_assets,
                authority_revision.canonical_sha256(source_assets),
                source_binding,
            ),
        ), patch.object(sys, "_MEIPASS", None, create=True):
            revision = authority_revision.current_authority_revision()

        validator.validate(revision)
        authority_revision.validate_authority_revision(revision)
        self.assertEqual(
            schema_document["properties"]["core_asset_sha256"]["required"],
            list(authority_revision.CORE_ASSET_KEYS),
        )
        self.assertEqual(
            schema_document["properties"]["source_code_sha256"]["required"],
            list(authority_revision.source_code_manifest.CORE_SOURCE_PATHS),
        )
        self.assertEqual(
            list(schema_document["properties"]["source_code_sha256"]["properties"]),
            list(authority_revision.source_code_manifest.CORE_SOURCE_PATHS),
        )
        self.assertEqual(
            schema_document["properties"]["source_code_manifest"]
            ["properties"]["file_count"]["const"],
            len(authority_revision.source_code_manifest.CORE_SOURCE_PATHS),
        )

        missing = copy.deepcopy(revision)
        missing["schema_asset_sha256"].pop(
            "knowledge_graph/aspen_extraction_coverage.schema.json"
        )
        self.assertTrue(list(validator.iter_errors(missing)))

        tampered = copy.deepcopy(revision)
        tampered["schema_asset_sha256"][
            "knowledge_graph/aspen_equipment_export.schema.json"
        ] = "0" * 63
        self.assertTrue(list(validator.iter_errors(tampered)))

    def test_source_schema_binding_includes_both_aspen_schemas_and_detects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            _write_schema_tree(root)
            with patch.object(authority_revision.app_core, "PACKAGE_ROOT", root):
                initial = authority_revision._current_schema_asset_hashes()
                self.assertEqual(
                    set(initial),
                    {
                        "app/schemas/protocol.schema.json",
                        *authority_revision.REQUIRED_KNOWLEDGE_SCHEMA_PATHS,
                    },
                )
                target = (
                    root
                    / "knowledge_graph"
                    / "aspen_extraction_coverage.schema.json"
                )
                original = target.read_bytes()
                target.write_bytes(original.replace(b"coverage-a", b"coverage-b"))
                self.assertEqual(target.stat().st_size, len(original))
                drifted = authority_revision._current_schema_asset_hashes()
                self.assertNotEqual(
                    initial["knowledge_graph/aspen_extraction_coverage.schema.json"],
                    drifted["knowledge_graph/aspen_extraction_coverage.schema.json"],
                )

    def test_source_schema_binding_fails_closed_when_required_schema_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            _write_schema_tree(root)
            (
                root
                / "knowledge_graph"
                / "aspen_equipment_export.schema.json"
            ).unlink()
            with patch.object(authority_revision.app_core, "PACKAGE_ROOT", root):
                with self.assertRaisesRegex(
                    authority_revision.AuthorityRevisionError,
                    "authority asset is missing",
                ):
                    authority_revision._current_schema_asset_hashes()

    def test_packaged_manifest_must_bind_each_schema_hash(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            _write_schema_tree(root)
            with patch.object(authority_revision.app_core, "PACKAGE_ROOT", root):
                schema_assets = authority_revision._current_schema_asset_hashes()
                manifest = {
                    "schema": authority_revision.runtime_bundle.MANIFEST_SCHEMA,
                    "bundle_revision": "E" * 64,
                    "files": [
                        {"runtime_path": path, "sha256": digest}
                        for path, digest in schema_assets.items()
                    ],
                }
                manifest_path = root / authority_revision.runtime_bundle.MANIFEST_NAME
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False),
                    encoding="utf-8",
                )
                with patch.object(sys, "_MEIPASS", str(root), create=True):
                    binding = authority_revision._runtime_manifest_binding(
                        "F" * 64,
                        schema_assets,
                    )
                    self.assertEqual(binding["status"], "PACKAGED")
                    source_assets = {
                        relative_path: "A" * 64
                        for relative_path in (
                            authority_revision.source_code_manifest.CORE_SOURCE_PATHS
                        )
                    }
                    source_binding = {
                        "status": "PACKAGED_SNAPSHOT_VERIFIED",
                        "manifest_sha256": "B" * 64,
                        "manifest_payload_sha256": "C" * 64,
                        "path_set_sha256": authority_revision.canonical_sha256(
                            list(
                                authority_revision.source_code_manifest.CORE_SOURCE_PATHS
                            )
                        ),
                        "file_count": len(
                            authority_revision.source_code_manifest.CORE_SOURCE_PATHS
                        ),
                    }
                    with patch.object(
                        authority_revision,
                        "_current_source_code_binding",
                        return_value=(
                            source_assets,
                            authority_revision.canonical_sha256(source_assets),
                            source_binding,
                        ),
                    ):
                        packaged_revision = (
                            authority_revision.current_authority_revision()
                        )
                    self.assertEqual(
                        packaged_revision["runtime_manifest"]["status"],
                        "PACKAGED",
                    )
                    self.assertTrue(
                        authority_revision.REQUIRED_KNOWLEDGE_SCHEMA_PATHS
                        <= set(packaged_revision["schema_asset_sha256"])
                    )
                    authority_revision.validate_authority_revision(
                        packaged_revision
                    )

                    target = (
                        root
                        / "knowledge_graph"
                        / "aspen_equipment_export.schema.json"
                    )
                    original = target.read_bytes()
                    target.write_bytes(original.replace(b"export-a", b"export-b"))
                    self.assertEqual(target.stat().st_size, len(original))
                    drifted_assets = authority_revision._current_schema_asset_hashes()
                    with self.assertRaisesRegex(
                        authority_revision.AuthorityRevisionError,
                        "schema hash mismatch",
                    ):
                        authority_revision._runtime_manifest_binding(
                            "F" * 64,
                            drifted_assets,
                        )

    def test_packaged_manifest_fails_closed_when_schema_record_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            _write_schema_tree(root)
            with patch.object(authority_revision.app_core, "PACKAGE_ROOT", root):
                schema_assets = authority_revision._current_schema_asset_hashes()
                omitted = "knowledge_graph/aspen_extraction_coverage.schema.json"
                manifest = {
                    "schema": authority_revision.runtime_bundle.MANIFEST_SCHEMA,
                    "bundle_revision": "E" * 64,
                    "files": [
                        {"runtime_path": path, "sha256": digest}
                        for path, digest in schema_assets.items()
                        if path != omitted
                    ],
                }
                (root / authority_revision.runtime_bundle.MANIFEST_NAME).write_text(
                    json.dumps(manifest, ensure_ascii=False),
                    encoding="utf-8",
                )
                with patch.object(sys, "_MEIPASS", str(root), create=True):
                    with self.assertRaisesRegex(
                        authority_revision.AuthorityRevisionError,
                        "omits authority schema assets",
                    ):
                        authority_revision._runtime_manifest_binding(
                            "F" * 64,
                            schema_assets,
                        )

    def test_validation_allows_only_registered_knowledge_schema_paths(self) -> None:
        schema_assets = {
            "app/schemas/protocol.schema.json": "A" * 64,
            "knowledge_graph/aspen_equipment_export.schema.json": "B" * 64,
            "knowledge_graph/aspen_extraction_coverage.schema.json": "C" * 64,
        }
        valid = _source_revision(schema_assets)
        self.assertEqual(
            authority_revision.validate_authority_revision(valid),
            valid,
        )

        missing = copy.deepcopy(valid)
        missing_assets = missing["schema_asset_sha256"]
        assert isinstance(missing_assets, dict)
        missing_assets.pop("knowledge_graph/aspen_extraction_coverage.schema.json")
        _seal_source_revision(missing)
        with self.assertRaisesRegex(
            authority_revision.AuthorityRevisionError,
            "omits required Aspen schema assets",
        ):
            authority_revision.validate_authority_revision(missing)

        traversal = copy.deepcopy(valid)
        traversal_assets = traversal["schema_asset_sha256"]
        assert isinstance(traversal_assets, dict)
        traversal_assets["knowledge_graph/../escape.schema.json"] = "D" * 64
        _seal_source_revision(traversal)
        with self.assertRaisesRegex(
            authority_revision.AuthorityRevisionError,
            "invalid relative path",
        ):
            authority_revision.validate_authority_revision(traversal)


if __name__ == "__main__":
    unittest.main()
