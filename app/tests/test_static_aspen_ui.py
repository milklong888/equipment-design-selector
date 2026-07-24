from __future__ import annotations

import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]


class StaticAspenUiTests(unittest.TestCase):
    def test_pressure_basis_has_no_default_and_submit_is_guarded(self) -> None:
        html = (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (APP_DIR / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('<option value="" selected>请选择；程序不默认</option>', html)
        self.assertNotIn('<option value="absolute" selected>', html)
        self.assertIn("if (!['absolute', 'gauge'].includes(pressureBasis))", javascript)
        self.assertIn("程序不会替你默认", javascript)

    def test_atmosphere_is_blank_visible_for_both_bases_and_explained(self) -> None:
        html = (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (APP_DIR / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="aspen-atmospheric" type="number" step="any" value=""', html)
        self.assertIn("机械初筛或标准表压限值把绝压换成表压时也需要", html)
        self.assertIn("!['absolute', 'gauge'].includes(event.target.value)", javascript)
        self.assertIn("atmospheric_pressure_mpa: $('aspen-atmospheric').value", javascript)


if __name__ == "__main__":
    unittest.main()
