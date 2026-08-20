from __future__ import annotations

import sys
import tkinter as tk
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import aspen_pfd
import pfd_canvas


def _stream(stream_id: str, **values: object) -> dict[str, object]:
    return {"stream_id": stream_id, **values}


def _block(block_id: str, block_type: str, inlet: list[str], outlet: list[str]) -> dict[str, object]:
    return {
        "block_id": block_id,
        "block_type": block_type,
        "inlet_streams": inlet,
        "outlet_streams": outlet,
    }


def _bundle(blocks: list[dict[str, object]], streams: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "aspen-equipment-export-v1",
        "case": {"case_id": "PFD-CANVAS-TEST", "pressure_basis": "absolute", "run_status": {}},
        "blocks": blocks,
        "streams": streams,
    }


def _bbox_overlap(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> tuple[int, int]:
    return (
        max(0, min(left[2], right[2]) - max(left[0], right[0])),
        max(0, min(left[3], right[3]) - max(left[1], right[1])),
    )


class PFDCanvasHelperTests(unittest.TestCase):
    def test_every_equipment_family_has_an_original_vector_symbol_route(self) -> None:
        expected = {
            "family_pump",
            "family_compressor",
            "family_liquid_power_recovery_turbine",
            "family_gas_expander_turbine",
            "family_tower",
            "family_fixed_tubesheet_exchanger",
            "family_other_heat_exchanger",
            "family_reactor_vessel_separator",
            "family_storage_vessel",
            "family_agitator",
            "family_static_mixer",
            "family_membrane",
            "family_package_equipment",
            "family_process_piping",
            "family_pipe_fitting",
            "family_flange_gasket",
            "family_valve",
        }
        self.assertEqual(set(pfd_canvas.FAMILY_SYMBOLS), expected)
        self.assertNotIn("", pfd_canvas.FAMILY_SYMBOLS.values())

    def test_source_block_type_precedes_family_symbol(self) -> None:
        node = {"family_id": "family_storage_vessel"}
        block = {"source": {"block_type": "PUMP"}}
        self.assertEqual(pfd_canvas.symbol_key(node, block), "pump")

    def test_family_symbol_and_unknown_fallback_are_stable(self) -> None:
        self.assertEqual(pfd_canvas.symbol_key({"family_id": "family_tower"}), "tower")
        self.assertEqual(pfd_canvas.symbol_key({"family_id": "family_missing"}), "generic")

    def test_compact_text_is_whitespace_normalized_and_bounded(self) -> None:
        self.assertEqual(pfd_canvas.compact_text("  离心泵\n 候选  ", 20), "离心泵 候选")
        text = pfd_canvas.compact_text("A" * 40, 12)
        self.assertEqual(len(text), 12)
        self.assertTrue(text.endswith("…"))

    def test_equipment_overlay_keeps_real_candidate_and_status(self) -> None:
        derivation = {
            "equipment": [
                {
                    "aspen_block_id": "P-101",
                    "match_result": {
                        "status": "MATCHED",
                        "match": {"family_name": "泵"},
                        "model_decision": {"model_status": "standard_candidate"},
                        "model_recommendation": {
                            "candidates": [{"designation": "GB/T 5662-2013 65-40-200"}],
                            "terminal_selection": {
                                "status": "DEFAULTED_TERMINAL_TYPE_SELECTED",
                                "selection_basis": "registered_default",
                                "default_applied": True,
                                "rule_id": "pump:registered_default:end_suction_centrifugal",
                            },
                        },
                    },
                }
            ]
        }
        overlay = pfd_canvas.selection_overlay_from_derivation(derivation)["P-101"]
        self.assertEqual(overlay["status"], "standard_candidate")
        self.assertEqual(overlay["designation"], "GB/T 5662-2013 65-40-200")
        self.assertEqual(overlay["terminal_selection_status"], "DEFAULTED_TERMINAL_TYPE_SELECTED")
        self.assertTrue(overlay["terminal_default_applied"])
        self.assertEqual(overlay["terminal_rule_id"], "pump:registered_default:end_suction_centrifugal")

    def test_equipment_overlay_keeps_candidate_but_waits_when_formal_evidence_is_missing(self) -> None:
        derivation = {
            "equipment": [{
                "aspen_block_id": "P-102",
                "match_result": {
                    "status": "MATCHED",
                    "model_decision": {
                        "model_status": "type_selected",
                        "verification_missing_fields": ["vendor_curve_path", "independent_audit_approval_required"],
                    },
                    "model_recommendation": {
                        "candidates": [{"designation": "GB/T 5662-2013 65-40-200"}],
                    },
                },
            }],
        }
        overlay = pfd_canvas.selection_overlay_from_derivation(derivation)["P-102"]
        self.assertEqual(overlay["status"], "WAITING_FORMAL_EVIDENCE")
        self.assertEqual(overlay["designation"], "GB/T 5662-2013 65-40-200")
        self.assertEqual(
            pfd_canvas.status_color("AUTO_EXACT", overlay["status"]),
            pfd_canvas.WARN,
        )

    def test_family_name_fallback_is_not_lost_by_conditional_precedence(self) -> None:
        derivation = {
            "equipment": [
                {
                    "aspen_block_id": "V-101",
                    "match_result": {
                        "status": "MATCHED",
                        "match": {"family_name": "储罐"},
                        "model_recommendation": {},
                    },
                }
            ]
        }
        overlay = pfd_canvas.selection_overlay_from_derivation(derivation)["V-101"]
        self.assertEqual(overlay["designation"], "储罐")

    def test_selection_status_color_is_independent_from_mapping_color(self) -> None:
        self.assertEqual(pfd_canvas.status_color("AUTO_EXACT", "BLOCKED_CALCULATION"), pfd_canvas.BAD)
        self.assertEqual(pfd_canvas.status_color("AUTO_AMBIGUOUS", "final_model"), pfd_canvas.SAFE)
        self.assertEqual(pfd_canvas.status_color("AUTO_EXACT", "WAITING_CALCULATED_PARAMETERS"), pfd_canvas.WARN)
        self.assertEqual(pfd_canvas.status_color("AUTO_EXACT", "NOT_APPLICABLE"), pfd_canvas.MUTED)
        self.assertEqual(pfd_canvas.status_color("AUTO_UNRESOLVED", None), pfd_canvas.MUTED)

    def test_symbol_port_anchors_are_inside_box_and_have_positive_span(self) -> None:
        keys = set(pfd_canvas.FAMILY_SYMBOLS.values()) | set(pfd_canvas.BLOCK_SYMBOLS.values()) | {"generic"}
        for key in keys:
            left, right, center_y = pfd_canvas.PFDCanvasView._symbol_ports(key, (8.0, 12.0, 56.0, 74.0))
            self.assertGreaterEqual(left, 8.0, key)
            self.assertLessEqual(right, 56.0, key)
            self.assertGreater(right, left, key)
            self.assertEqual(center_y, 43.0, key)

    def test_warning_color_has_normal_text_contrast_on_canvas(self) -> None:
        def luminance(color: str) -> float:
            channels = [int(color[index:index + 2], 16) / 255.0 for index in (1, 3, 5)]
            linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        foreground = luminance(pfd_canvas.WARN)
        background = luminance(pfd_canvas.CANVAS_BG)
        ratio = (max(foreground, background) + 0.05) / (min(foreground, background) + 0.05)
        self.assertGreaterEqual(ratio, 4.5)


class PFDCanvasTkRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk display unavailable: {exc}") from exc
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.root.destroy()

    def setUp(self) -> None:
        self.view = pfd_canvas.PFDCanvasView(
            self.root,
            on_block_open=lambda _block_id: None,
            on_block_menu=lambda *_args: None,
            on_stream_open=lambda _stream_id: None,
            on_stream_menu=lambda *_args: None,
        )
        self.view.pack()

    def tearDown(self) -> None:
        self.view.destroy()

    def _items(self, entity: str, role: str) -> list[int]:
        return [
            item
            for item in self.view.canvas.find_withtag(role)
            if entity in self.view.canvas.gettags(item)
        ]

    def _render(
        self,
        source: dict[str, object],
        overlays: dict[str, dict[str, object]] | None = None,
        *,
        mode: str = "standard",
        zoom: float = 1.0,
    ) -> dict[str, object]:
        mapping = aspen_pfd.build_pfd_mapping(source)
        self.view.zoom = zoom
        self.view.set_mapping(mapping, overlays or {}, detail_mode=mode)
        self.root.update_idletasks()
        return mapping

    def test_parallel_streams_have_disjoint_escape_rails_and_labels_at_all_zoom_levels(self) -> None:
        source = _bundle(
            [
                _block("B1", "HEATER", ["F"], ["S1", "S2"]),
                _block("B2", "HEATER", ["S1", "S2"], ["P"]),
            ],
            [_stream(item) for item in ("F", "S1", "S2", "P")],
        )
        for zoom in (0.45, 1.0, 2.2):
            self._render(source, zoom=zoom)
            label_1 = self.view.canvas.bbox(self._items("entity:stream:S1", "role:edge_label")[0])
            label_2 = self.view.canvas.bbox(self._items("entity:stream:S2", "role:edge_label")[0])
            self.assertEqual(_bbox_overlap(label_1, label_2)[0], 0)
            edge_1 = self.view.canvas.coords(self._items("entity:stream:S1", "role:edge")[0])
            edge_2 = self.view.canvas.coords(self._items("entity:stream:S2", "role:edge")[0])
            self.assertNotEqual(edge_1[2], edge_2[2])
            status_x = self.view.canvas.coords(self._items("entity:block:B1", "role:status_rule")[0])[0]
            for coords in (edge_1, edge_2):
                points = list(zip(coords[0::2], coords[1::2]))
                self.assertFalse(any(
                    abs(left[0] - right[0]) < 1e-9
                    and abs(left[0] - status_x) < 1e-9
                    and abs(left[1] - right[1]) > 1e-9
                    for left, right in zip(points, points[1:])
                ))

    def test_distinct_outputs_to_different_targets_get_separate_label_slots(self) -> None:
        source = _bundle(
            [
                _block("B1", "HEATER", ["F"], ["S1", "S2"]),
                _block("B2", "HEATER", ["S1"], ["P1"]),
                _block("B3", "HEATER", ["S2"], ["P2"]),
            ],
            [_stream(item) for item in ("F", "S1", "S2", "P1", "P2")],
        )
        self._render(source)
        label_1 = self.view.canvas.bbox(self._items("entity:stream:S1", "role:edge_label")[0])
        label_2 = self.view.canvas.bbox(self._items("entity:stream:S2", "role:edge_label")[0])
        width, height = _bbox_overlap(label_1, label_2)
        self.assertFalse(width > 0 and height > 0)

    def test_boundary_selection_lines_are_above_and_below_the_arrow(self) -> None:
        source = _bundle(
            [_block("P1", "PUMP", ["F"], ["P"])],
            [_stream("F"), _stream("P")],
        )
        self._render(source, {
            "stream:F": {
                "status": "standard_candidate",
                "designation": "DN100 SCH40 GB/T 8163-2018",
            }
        })
        arrow = self.view.canvas.bbox(self._items("entity:stream:F", "role:boundary")[0])
        arrow_coords = self.view.canvas.coords(self._items("entity:stream:F", "role:boundary")[0])
        process_coords = self.view.canvas.coords(self._items("entity:stream:F", "role:edge")[0])
        self.assertAlmostEqual(arrow_coords[-2], process_coords[0])
        labels = self._items("entity:stream:F", "role:boundary_label")
        self.assertEqual(len(labels), 2)
        for label in labels:
            self.assertEqual(_bbox_overlap(arrow, self.view.canvas.bbox(label))[1], 0)

    def test_simulation_logic_node_has_neutral_explicit_non_model_status(self) -> None:
        source = _bundle(
            [_block("F0101", "FSPLIT", ["F"], ["P1", "P2"])],
            [_stream("F"), _stream("P1"), _stream("P2")],
        )
        self._render(source, {
            "F0101": {
                "status": "NOT_APPLICABLE",
                "designation": "流程逻辑节点",
                "reason": "NOT_APPLICABLE_SIMULATION_LOGIC_NODE",
            }
        })
        status_item = self._items("entity:block:F0101", "role:model_status")[0]
        self.assertIn("流程逻辑节点", self.view.canvas.itemcget(status_item, "text"))
        self.assertNotIn("待闭合", self.view.canvas.itemcget(status_item, "text"))
        self.assertEqual(self.view.canvas.itemcget(status_item, "fill"), pfd_canvas.MUTED)

    def test_detailed_stream_label_is_limited_to_the_inter_equipment_corridor(self) -> None:
        source = _bundle(
            [
                _block("P-101", "PUMP", ["F"], ["S"]),
                _block("E-202", "HEATER", ["S"], ["P"]),
            ],
            [
                _stream("F"),
                _stream("S", temperature_c=123.45, pressure_bar=16.7, mass_flow_kg_h=12345.6),
                _stream("P"),
            ],
        )
        self._render(source, {
            "stream:S": {
                "status": "standard_candidate",
                "designation": "DN80 SCH40 GB/T 8163-2018 无缝钢管",
                "key_values": {
                    "temperature_c": "123.45 °C",
                    "pressure_bar": "16.7 bar",
                    "mass_flow_kg_h": "12345.6 kg/h",
                },
            }
        }, mode="detailed")
        label = self.view.canvas.bbox(self._items("entity:stream:S", "role:edge_label")[0])
        for block_id in ("P-101", "E-202"):
            for role in ("role:symbol", "role:label_primary", "role:label_secondary", "role:label_status", "role:model_status"):
                for item in self._items(f"entity:block:{block_id}", role):
                    width, height = _bbox_overlap(label, self.view.canvas.bbox(item))
                    self.assertFalse(width > 0 and height > 0, (block_id, role))

    def test_fit_zoom_can_enter_large_flowsheet_overview_range(self) -> None:
        self.view.set_zoom(0.15)
        self.assertAlmostEqual(self.view.zoom, 0.15)

    def test_left_and_right_click_callbacks_remain_separate(self) -> None:
        opened: list[str] = []
        menus: list[tuple[str, int, int]] = []
        self.view.on_block_open = opened.append
        self.view.on_block_menu = (
            lambda block_id, x_root, y_root: menus.append(
                (block_id, x_root, y_root)
            )
        )
        self.assertEqual(self.view._open_block(None, "P-101"), "break")
        self.assertEqual(opened, ["P-101"])
        self.assertEqual(menus, [])
        event = type("Event", (), {"x_root": 12, "y_root": 34})()
        self.assertEqual(self.view._menu_block(event, "P-101"), "break")
        self.assertEqual(opened, ["P-101"])
        self.assertEqual(menus, [("P-101", 12, 34)])
        self.assertEqual(self.view.selected_entity, "block:P-101")

    def test_shared_branch_has_one_junction_and_one_label(self) -> None:
        source = _bundle(
            [
                _block("B1", "HEATER", ["F"], ["S"]),
                _block("B2", "HEATER", ["S"], ["P2"]),
                _block("B3", "HEATER", ["S"], ["P3"]),
            ],
            [_stream(item) for item in ("F", "S", "P2", "P3")],
        )
        self._render(source)
        self.assertEqual(len(self._items("entity:stream:S", "role:junction")), 1)
        self.assertEqual(len(self._items("entity:stream:S", "role:edge_label")), 1)

    def test_invalidated_stream_is_dashed_warn_colored_and_never_shows_stale_selection(self) -> None:
        source = _bundle(
            [
                _block("P-101", "PUMP", ["F"], ["S"]),
                _block("E-101", "HEATER", ["S"], ["P"]),
            ],
            [_stream(item) for item in ("F", "S", "P")],
        )
        self._render(source, {
            "stream:S": {
                "status": "WAITING_CALCULATED_PARAMETERS",
                "designation": "OLD-DN80-SHOULD-NOT-RENDER",
                "recalculation_status": "RELATED_STREAM_RECALC_REQUIRED",
            }
        })
        edge = self._items("entity:stream:S", "role:edge")[0]
        label = self._items("entity:stream:S", "role:edge_label")[0]
        self.assertEqual(self.view.canvas.itemcget(edge, "fill"), pfd_canvas.WARN)
        self.assertTrue(self.view.canvas.itemcget(edge, "dash"))
        self.assertIn("state:waiting_recalculation", self.view.canvas.gettags(edge))
        label_text = self.view.canvas.itemcget(label, "text")
        self.assertIn("! 待复核", label_text)
        self.assertNotIn("OLD-DN80", label_text)


if __name__ == "__main__":
    unittest.main()
