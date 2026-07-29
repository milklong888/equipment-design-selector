from __future__ import annotations

import json
import struct
import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import tk_gui


class IconAssetTests(unittest.TestCase):
    def test_frozen_source_and_generated_assets_match_manifest(self) -> None:
        assets = APP_DIR / "assets"
        manifest = json.loads((assets / "equipment_design_icon_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "equipment-design-icon-manifest-v1")
        self.assertEqual(manifest["source"]["sha256"], "4BF6C9D0D6BF5F820D25400FA19CD2DD0AC367DD43690FF227B7CA45E014CD54")
        self.assertEqual(manifest["source"]["width_px"], 720)
        self.assertEqual(manifest["source"]["height_px"], 658)
        self.assertEqual(manifest["square_adaptation"]["method"], "center_on_white_square_without_crop_or_stretch")
        self.assertEqual(manifest["square_adaptation"]["canvas_px"], [720, 720])
        self.assertEqual(manifest["square_adaptation"]["offset_px"], [0, 31])

        png = (assets / "equipment_design_app_icon.png").read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(
            struct.unpack(">II", png[16:24]),
            (720, 720),
        )

        ico = (assets / "equipment_design_app.ico").read_bytes()
        reserved, image_type, image_count = struct.unpack("<HHH", ico[:6])
        self.assertEqual((reserved, image_type), (0, 1))
        sizes: set[tuple[int, int]] = set()
        for index in range(image_count):
            offset = 6 + index * 16
            width_byte, height_byte = struct.unpack(
                "BB",
                ico[offset : offset + 2],
            )
            sizes.add(
                (
                    256 if width_byte == 0 else width_byte,
                    256 if height_byte == 0 else height_byte,
                )
            )
        self.assertIn((256, 256), sizes)
        self.assertIn((16, 16), sizes)

    def test_tk_icon_resolver_finds_packaged_asset_layout(self) -> None:
        resolved = tk_gui._app_icon_path("equipment_design_app.ico")
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.is_file())


if __name__ == "__main__":
    unittest.main()
