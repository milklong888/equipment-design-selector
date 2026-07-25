from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from PIL import Image


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

        with Image.open(assets / "equipment_design_app_icon.png") as png:
            self.assertEqual(png.size, (720, 720))
        with Image.open(assets / "equipment_design_app.ico") as ico:
            self.assertEqual(ico.format, "ICO")
            self.assertIn((256, 256), ico.ico.sizes())
            self.assertIn((16, 16), ico.ico.sizes())

    def test_tk_icon_resolver_finds_packaged_asset_layout(self) -> None:
        resolved = tk_gui._app_icon_path("equipment_design_app.ico")
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.is_file())


if __name__ == "__main__":
    unittest.main()
