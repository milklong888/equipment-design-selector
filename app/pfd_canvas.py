"""Original engineering-style Tk Canvas renderer for deterministic Aspen PFD data.

The renderer uses only Tk vector primitives.  It does not copy Aspen or DWSIM
icons.  The machine mapping remains in :mod:`aspen_pfd`; this module is a view
and interaction adapter only.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont, ttk
from typing import Any, Callable, Mapping


CANVAS_BG = "#F1F4F7"
INK = "#17212B"
MUTED = "#5D6B78"
LINE = "#D7DEE5"
ACCENT = "#126E82"
ACCENT_DARK = "#0C5261"
SAFE = "#1F7A55"
WARN = "#8A5308"
BAD = "#B13A3A"
CARD = "#FFFFFF"


FAMILY_SYMBOLS = {
    "family_pump": "pump",
    "family_compressor": "compressor",
    "family_liquid_power_recovery_turbine": "turbine",
    "family_gas_expander_turbine": "turbine",
    "family_tower": "tower",
    "family_fixed_tubesheet_exchanger": "exchanger",
    "family_other_heat_exchanger": "exchanger",
    "family_reactor_vessel_separator": "vessel",
    "family_storage_vessel": "tank",
    "family_agitator": "agitator",
    "family_static_mixer": "mixer",
    "family_membrane": "membrane",
    "family_package_equipment": "package",
    "family_process_piping": "pipeline",
    "family_pipe_fitting": "fitting",
    "family_flange_gasket": "flange",
    "family_valve": "valve",
}


BLOCK_SYMBOLS = {
    "PUMP": "pump",
    "COMPR": "compressor",
    "MCOMPR": "compressor",
    "RADFRAC": "tower",
    "DSTWU": "tower",
    "ABSBR": "tower",
    "HEATX": "exchanger",
    "HEATER": "heater",
    "FLASH2": "vessel",
    "FLASH3": "vessel",
    "DECANTER": "vessel",
    "RPLUG": "pfr",
    "RCSTR": "agitator",
    "RSTOIC": "reactor",
    "RYIELD": "reactor",
    "RGIBBS": "reactor",
    "MIXER": "mixer",
    "FSPLIT": "splitter",
    "VALVE": "valve",
}


STATUS_LABELS = {
    "final_model": "正式型号",
    "same_equipment_verified": "同设备已验证",
    "standard_candidate": "标准候选",
    "vendor_candidate": "厂家候选",
    "type_selected": "型式已确定",
    "NOT_READY": "待闭合",
    "WAITING_CALCULATED_PARAMETERS": "等待参数闭合",
    "WAITING_FORMAL_EVIDENCE": "型式/候选已算，等待正式证据",
    "BLOCKED_CALCULATION": "计算阻断",
    "NOT_APPLICABLE": "流程逻辑节点 / 无独立设备型号",
    "AUTO_EXACT": "类别字段",
    "AUTO_INFERRED_UNIQUE": "特征识别",
    "AUTO_AMBIGUOUS": "保留候选",
    "AUTO_UNRESOLVED": "待识别",
    "USER_OVERRIDE": "手工指定",
}

STATUS_SHORT_LABELS = {
    "final_model": "✓ 正式",
    "same_equipment_verified": "✓ 验证",
    "standard_candidate": "◇ 标准",
    "vendor_candidate": "◇ 厂家",
    "type_selected": "型式",
    "NOT_READY": "! 待闭合",
    "WAITING_CALCULATED_PARAMETERS": "! 待闭合",
    "WAITING_FORMAL_EVIDENCE": "! 待证据",
    "BLOCKED_CALCULATION": "× 阻断",
    "NOT_APPLICABLE": "流程逻辑节点 · 无型号",
}


def compact_text(value: Any, limit: int = 34) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def status_color(mapping_status: Any, model_status: Any, mode: Any = None) -> str:
    model = str(model_status or "").upper()
    mapping = str(mapping_status or "").upper()
    if any(token in model for token in ("BLOCK", "FAIL", "CONFLICT", "ERROR")):
        return BAD
    if model == "NOT_APPLICABLE":
        return MUTED
    if model in {"FINAL_MODEL", "SAME_EQUIPMENT_VERIFIED"}:
        return SAFE
    if any(token in model for token in ("WAIT", "NOT_READY", "PENDING", "INCOMPLETE", "UNKNOWN")):
        return WARN
    if any(token in model for token in ("CANDIDATE", "TYPE_SELECTED", "READY")):
        return ACCENT
    if str(mode or "").casefold() == "user_override":
        return ACCENT_DARK
    if mapping in {"AUTO_AMBIGUOUS", "AUTO_UNRESOLVED"}:
        return WARN if mapping == "AUTO_AMBIGUOUS" else MUTED
    return ACCENT


def symbol_key(node: Mapping[str, Any], block: Mapping[str, Any] | None = None) -> str:
    family_id = str(node.get("family_id") or "")
    if str(node.get("mapping_mode") or "").casefold() == "user_override" and family_id in FAMILY_SYMBOLS:
        return FAMILY_SYMBOLS[family_id]
    source_type = str((block or {}).get("source", {}).get("block_type") or "").upper()
    if source_type in BLOCK_SYMBOLS:
        return BLOCK_SYMBOLS[source_type]
    return FAMILY_SYMBOLS.get(family_id, "generic")


def selection_overlay_from_derivation(derivation: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(derivation, Mapping):
        return result
    for item in derivation.get("equipment", []) if isinstance(derivation.get("equipment"), list) else []:
        if not isinstance(item, Mapping):
            continue
        block_id = str(item.get("aspen_block_id") or item.get("equipment_tag") or "").strip()
        match = item.get("match_result") if isinstance(item.get("match_result"), Mapping) else {}
        decision = match.get("model_decision") if isinstance(match.get("model_decision"), Mapping) else {}
        recommendation = match.get("model_recommendation") if isinstance(match.get("model_recommendation"), Mapping) else {}
        terminal_selection = (
            recommendation.get("terminal_selection")
            if isinstance(recommendation.get("terminal_selection"), Mapping)
            else {}
        )
        candidates = recommendation.get("candidates") if isinstance(recommendation.get("candidates"), list) else []
        first = candidates[0] if candidates and isinstance(candidates[0], Mapping) else {}
        designation = (
            recommendation.get("formal_model")
            or first.get("designation")
            or recommendation.get("recommended_type")
            or (
                match.get("match", {}).get("family_name")
                if isinstance(match.get("match"), Mapping)
                else None
            )
        )
        model_status = decision.get("model_status") or recommendation.get("status") or match.get("status")
        verification_missing = decision.get("verification_missing_fields")
        if (
            isinstance(verification_missing, list)
            and verification_missing
            and str(model_status or "").casefold() not in {"final_model", "same_equipment_verified"}
        ):
            model_status = "WAITING_FORMAL_EVIDENCE"
        result[block_id] = {
            "status": model_status,
            "designation": designation,
            "match_status": match.get("status"),
            "terminal_selection_status": terminal_selection.get("status"),
            "terminal_selection_basis": terminal_selection.get("selection_basis"),
            "terminal_default_applied": bool(terminal_selection.get("default_applied", False)),
            "terminal_rule_id": terminal_selection.get("rule_id"),
            "terminal_assumption": terminal_selection.get("assumption"),
            "result": dict(match),
        }
    for item in derivation.get("piping", []) if isinstance(derivation.get("piping"), list) else []:
        if not isinstance(item, Mapping):
            continue
        stream_id = str(item.get("stream_id") or "").strip()
        match = item.get("match_result") if isinstance(item.get("match_result"), Mapping) else {}
        decision = match.get("model_decision") if isinstance(match.get("model_decision"), Mapping) else {}
        recommendation = match.get("model_recommendation") if isinstance(match.get("model_recommendation"), Mapping) else {}
        candidates = recommendation.get("candidates") if isinstance(recommendation.get("candidates"), list) else []
        first = candidates[0] if candidates and isinstance(candidates[0], Mapping) else {}
        label_data = item.get("pfd_edge_label_data") if isinstance(item.get("pfd_edge_label_data"), Mapping) else {}
        compact_label = label_data.get("compact_label") if isinstance(label_data.get("compact_label"), Mapping) else {}
        result[f"stream:{stream_id}"] = {
            "status": compact_label.get("status") or decision.get("model_status") or recommendation.get("status") or match.get("status"),
            "designation": compact_label.get("type_or_model") or first.get("designation") or recommendation.get("recommended_type") or "工业管道",
            "match_status": match.get("status"),
            "key_values": dict(compact_label.get("key_values", {})) if isinstance(compact_label.get("key_values"), Mapping) else {},
            "details": dict(label_data.get("details", {})) if isinstance(label_data.get("details"), Mapping) else {},
            "result": dict(match),
        }
    return result


class PFDCanvasView(ttk.Frame):
    """Scrollable deterministic PFD view with distinct entity interactions.

    A left click is always routed to the object's detail/editor callback.  A
    right click is routed only to its context-choice callback; the canvas never
    treats a context-menu action as parameter input.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_block_open: Callable[[str], None],
        on_block_menu: Callable[[str, int, int], None],
        on_stream_open: Callable[[str], None],
        on_stream_menu: Callable[[str, int, int], None],
    ) -> None:
        super().__init__(parent)
        self.on_block_open = on_block_open
        self.on_block_menu = on_block_menu
        self.on_stream_open = on_stream_open
        self.on_stream_menu = on_stream_menu
        self.mapping: dict[str, Any] = {}
        self.overlays: dict[str, dict[str, Any]] = {}
        self.detail_mode = "standard"
        self.zoom = 1.0
        self.selected_entity: str | None = None
        self._block_rows: dict[str, dict[str, Any]] = {}
        self._node_rows: dict[str, dict[str, Any]] = {}
        self._edge_rows: dict[str, dict[str, Any]] = {}

        self.canvas = tk.Canvas(self, bg=CANVAS_BG, highlightthickness=0, takefocus=True)
        xbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        ybar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas.bind("<Control-MouseWheel>", self._wheel_zoom)
        self.canvas.bind("<Button-1>", self._clear_selection_from_background, add="+")
        self.canvas.bind("<Configure>", self._redraw_empty_state, add="+")
        self.redraw()

    def _redraw_empty_state(self, _event: Any = None) -> None:
        pfd = self.mapping.get("pfd") if isinstance(self.mapping.get("pfd"), Mapping) else {}
        nodes = pfd.get("nodes") if isinstance(pfd.get("nodes"), list) else []
        if not nodes:
            self.redraw()

    def set_mapping(
        self,
        mapping: Mapping[str, Any] | None,
        overlays: Mapping[str, Mapping[str, Any]] | None = None,
        *,
        detail_mode: str | None = None,
    ) -> None:
        self.mapping = dict(mapping or {})
        self.overlays = {str(key): dict(value) for key, value in (overlays or {}).items()}
        if detail_mode in {"compact", "standard", "detailed"}:
            self.detail_mode = str(detail_mode)
        self._block_rows = {
            str(item.get("block_id")): dict(item)
            for item in self.mapping.get("blocks", [])
            if isinstance(item, Mapping) and item.get("block_id")
        }
        self.redraw()

    def set_detail_mode(self, mode: str) -> None:
        if mode not in {"compact", "standard", "detailed"}:
            return
        self.detail_mode = mode
        self.redraw()

    def set_zoom(self, value: float) -> None:
        self.zoom = max(0.12, min(float(value), 2.2))
        self.redraw()

    def fit_to_window(self) -> None:
        pfd = self.mapping.get("pfd") if isinstance(self.mapping.get("pfd"), Mapping) else {}
        layout = pfd.get("layout") if isinstance(pfd.get("layout"), Mapping) else {}
        size = layout.get("canvas") if isinstance(layout.get("canvas"), Mapping) else {}
        width = float(size.get("width") or 1.0)
        height = float(size.get("height") or 1.0)
        self.update_idletasks()
        viewport_w = max(1, self.canvas.winfo_width() - 24)
        viewport_h = max(1, self.canvas.winfo_height() - 24)
        self.set_zoom(min(viewport_w / width, viewport_h / height, 1.35))
        self.update_idletasks()
        bbox = self.canvas.bbox("all")
        if bbox:
            pad_x = max(0.0, (viewport_w - (bbox[2] - bbox[0])) / 2.0)
            pad_y = max(0.0, (viewport_h - (bbox[3] - bbox[1])) / 2.0)
            self.canvas.configure(scrollregion=(
                bbox[0] - pad_x,
                bbox[1] - pad_y,
                bbox[2] + pad_x,
                bbox[3] + pad_y,
            ))
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def focus_block(self, block_id: str) -> None:
        tag = f"entity:block:{block_id}"
        bbox = self.canvas.bbox(tag)
        if not bbox:
            return
        region = self.canvas.cget("scrollregion").split()
        if len(region) != 4:
            return
        total_w = max(1.0, float(region[2]) - float(region[0]))
        total_h = max(1.0, float(region[3]) - float(region[1]))
        center_x = (bbox[0] + bbox[2]) / 2.0
        center_y = (bbox[1] + bbox[3]) / 2.0
        self.canvas.xview_moveto(max(0.0, min(1.0, center_x / total_w - 0.25)))
        self.canvas.yview_moveto(max(0.0, min(1.0, center_y / total_h - 0.25)))

    def redraw(self) -> None:
        self.canvas.delete("all")
        self._node_rows = {}
        self._edge_rows = {}
        pfd = self.mapping.get("pfd") if isinstance(self.mapping.get("pfd"), Mapping) else {}
        nodes = pfd.get("nodes") if isinstance(pfd.get("nodes"), list) else []
        edges = pfd.get("edges") if isinstance(pfd.get("edges"), list) else []
        if not nodes:
            width = max(620, self.canvas.winfo_width())
            height = max(360, self.canvas.winfo_height())
            card_width = min(560, width - 48)
            card_height = 188
            left = (width - card_width) / 2
            top = max(42, (height - card_height) / 2)
            self.canvas.create_rectangle(
                left,
                top,
                left + card_width,
                top + card_height,
                fill=CARD,
                outline=LINE,
                width=1,
                tags=("role:empty_state",),
            )
            self.canvas.create_rectangle(
                left,
                top,
                left + 5,
                top + card_height,
                fill=ACCENT,
                outline=ACCENT,
                tags=("role:empty_state",),
            )
            self.canvas.create_text(
                left + 28,
                top + 25,
                anchor="nw",
                text="尚未生成流程视图",
                fill=INK,
                font=self._font(14, bold=True),
                tags=("role:empty_state",),
            )
            self.canvas.create_text(
                left + 28,
                top + 59,
                anchor="nw",
                width=card_width - 56,
                text=(
                    "导入 Aspen 文件并运行后，这里会显示可交互 PFD；"
                    "手动选型结果请查看“选型结果”。"
                ),
                fill=MUTED,
                font=self._font(9),
                tags=("role:empty_state",),
            )
            steps = ("1  导入 Aspen", "2  运行确定性计算", "3  点设备查看参数")
            chip_width = (card_width - 72) / 3
            for index, label in enumerate(steps):
                chip_left = left + 28 + index * (chip_width + 8)
                self.canvas.create_rectangle(
                    chip_left,
                    top + 113,
                    chip_left + chip_width,
                    top + 151,
                    fill="#EAF2F4",
                    outline="#D5E3E7",
                    width=1,
                    tags=("role:empty_state",),
                )
                self.canvas.create_text(
                    chip_left + chip_width / 2,
                    top + 132,
                    text=label,
                    fill=ACCENT_DARK,
                    font=self._font(8, bold=True),
                    tags=("role:empty_state",),
                )
            self.canvas.create_text(
                left + 28,
                top + 167,
                anchor="w",
                text="快捷键：Ctrl+O 导入  ·  Ctrl+Enter 运行",
                fill=MUTED,
                font=self._font(8),
                tags=("role:empty_state",),
            )
            self.canvas.configure(scrollregion=(0, 0, width, height))
            return
        self._node_rows = {
            str(item.get("node_id")): dict(item)
            for item in nodes
            if isinstance(item, Mapping) and item.get("node_id")
        }
        self._labeled_streams: set[str] = set()
        self._stream_edge_counts: dict[str, int] = {}
        edges_by_stream: dict[str, list[dict[str, Any]]] = {}
        for item in edges:
            if isinstance(item, Mapping):
                stream_id = str(item.get("stream_id") or item.get("edge_id") or "")
                self._stream_edge_counts[stream_id] = self._stream_edge_counts.get(stream_id, 0) + 1
                edges_by_stream.setdefault(stream_id, []).append(dict(item))
        self._stream_junctions: dict[str, list[float]] = {}
        for stream_id, stream_edges in edges_by_stream.items():
            if len(stream_edges) < 2:
                continue
            routes = [
                self._display_route_points(
                    edge,
                    edge.get("route", {}).get("points", []) if isinstance(edge.get("route"), Mapping) else [],
                )
                for edge in stream_edges
            ]
            common: list[list[float]] = []
            for points_at_index in zip(*routes):
                first = points_at_index[0]
                if all(math.dist(first, item) < 1e-9 for item in points_at_index[1:]):
                    common.append(first)
                else:
                    break
            if common:
                self._stream_junctions[stream_id] = common[-1]
        for item in edges:
            if isinstance(item, Mapping):
                self._draw_edge(dict(item), labels_only=False)
        for stream_id, point in self._stream_junctions.items():
            x, y = self._point(point[0], point[1])
            radius = max(2.0, 3.0 * self.zoom)
            entity = f"entity:stream:{stream_id}"
            self.canvas.create_oval(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                outline=ACCENT,
                fill=ACCENT,
                tags=(entity, "role:junction"),
            )
        for item in nodes:
            if isinstance(item, Mapping):
                self._draw_node(dict(item))
        for item in edges:
            if isinstance(item, Mapping):
                self._draw_edge(dict(item), labels_only=True)
        bbox = self.canvas.bbox("all") or (0, 0, 760, 480)
        self.canvas.configure(scrollregion=(min(0, bbox[0] - 40), min(0, bbox[1] - 40), bbox[2] + 80, bbox[3] + 80))

    def _point(self, x: float, y: float) -> tuple[float, float]:
        return x * self.zoom, y * self.zoom

    def _draw_edge(self, edge: dict[str, Any], *, labels_only: bool) -> None:
        raw_points = edge.get("route", {}).get("points", []) if isinstance(edge.get("route"), Mapping) else []
        if len(raw_points) < 2:
            return
        points = self._display_route_points(edge, raw_points)
        coords = [coordinate * self.zoom for point in points for coordinate in point]
        stream_id = str(edge.get("stream_id") or edge.get("edge_id") or "")
        entity = f"entity:stream:{stream_id}"
        selected = self.selected_entity == f"stream:{stream_id}"
        overlay = self.overlays.get(f"stream:{stream_id}", {})
        recalculation_status = str(
            overlay.get("status")
            or overlay.get("recalculation_status")
            or edge.get("recalculation_status")
            or ""
        )
        pending_recalculation = recalculation_status in {
            "WAITING_CALCULATED_PARAMETERS",
            "RELATED_STREAM_RECALC_REQUIRED",
            "RELATED_STREAM_PENDING_RECALC",
            "TYPE_ROUTE_CHANGE_IMPACT_RECALC_REQUIRED",
            "PARAMETERS_CHANGED_PENDING_RECALC",
            "UPSTREAM_RELATED_PENDING_RECALC",
            "DOWNSTREAM_RELATED_PENDING_RECALC",
        }
        edge_color = WARN if pending_recalculation else (ACCENT_DARK if selected else ACCENT)
        state_tag = "state:waiting_recalculation" if pending_recalculation else "state:current"
        self._edge_rows[stream_id] = edge
        if not labels_only:
            self.canvas.create_line(*coords, fill=CANVAS_BG, width=max(8, int(11 * self.zoom)), tags=(entity, "role:edge_hit"))
            dash = (
                (4, 3)
                if pending_recalculation
                else (7, 4)
                if edge.get("route", {}).get("kind") in {"recycle_orthogonal", "self_loop_orthogonal"}
                else None
            )
            self.canvas.create_line(
                *coords,
                fill=edge_color,
                width=max(2, int((3 if selected else 2) * self.zoom)),
                arrow="last",
                arrowshape=(10, 12, 4),
                dash=dash,
                joinstyle="miter",
                tags=(entity, "role:edge", state_tag),
            )
            self.canvas.tag_bind(entity, "<Button-1>", lambda event, sid=stream_id: self._open_stream(event, sid))
            self.canvas.tag_bind(entity, "<Button-3>", lambda event, sid=stream_id: self._menu_stream(event, sid))
            return
        if stream_id in self._labeled_streams:
            return
        self._labeled_streams.add(stream_id)
        if self.zoom < 0.30:
            return
        if (
            str(edge.get("source_node_id", "")).startswith("boundary:")
            or str(edge.get("target_node_id", "")).startswith("boundary:")
        ):
            return
        route = edge.get("route") if isinstance(edge.get("route"), Mapping) else {}
        label_x, label_y, label_span = self._edge_label_geometry(
            raw_points,
            int(route.get("parallel_lane_index") or 0),
            int(route.get("parallel_lane_count") or 1),
        )
        source_fan_count = max(1, int(route.get("source_fan_count") or 1))
        source_fan_index = max(0, min(int(route.get("source_fan_index") or 0), source_fan_count - 1))
        source_node = self._node_rows.get(str(edge.get("source_node_id")), {})
        target_node = self._node_rows.get(str(edge.get("target_node_id")), {})
        source_geometry = source_node.get("geometry") if isinstance(source_node.get("geometry"), Mapping) else {}
        target_geometry = target_node.get("geometry") if isinstance(target_node.get("geometry"), Mapping) else {}
        source_right = float(source_geometry.get("x") or 0.0) + float(source_geometry.get("width") or 0.0)
        target_left = float(target_geometry.get("x") or 0.0)
        if source_fan_count > 1 and target_left > source_right:
            corridor_slot = (target_left - source_right) / source_fan_count
            label_x = source_right + corridor_slot * (source_fan_index + 0.5)
            label_span = corridor_slot
        designation = compact_text(overlay.get("designation") or "管道待水力闭合", 28)
        status_code = str(overlay.get("status") or "")
        status = STATUS_LABELS.get(status_code, status_code or "待闭合")
        real_selection = status_code in {
            "final_model",
            "same_equipment_verified",
            "standard_candidate",
            "vendor_candidate",
            "type_selected",
        } and designation not in {"工业管道", "管道待水力闭合"}
        if pending_recalculation:
            label = f"{stream_id} · !" if self.detail_mode == "compact" else f"{stream_id} · ! 待复核"
        elif self.detail_mode == "compact":
            label = stream_id
        elif self.detail_mode == "standard":
            status_short = STATUS_SHORT_LABELS.get(status_code, "")
            proposed = f"{stream_id} · {designation}  {status_short}" if real_selection else stream_id
            label = self._fit_text(proposed, max(28.0, label_span * self.zoom - 12.0), self._font(8))
        else:
            values = overlay.get("key_values") if isinstance(overlay.get("key_values"), Mapping) else {}
            if not values:
                values = {str(item.get("field")): item.get("display") for item in edge.get("parameters", []) if isinstance(item, Mapping)}
            extras = " | ".join(str(values[key]) for key in ("operating_temperature_c", "temperature_c", "operating_pressure_mpa", "pressure_bar", "flow_m3_h", "mass_flow_kg_h") if values.get(key) not in (None, ""))
            detailed_width = max(
                54.0 * self.zoom,
                min(180.0 * self.zoom, max(1.0, label_span * self.zoom - 12.0)),
            )
            first_line = self._fit_text(f"{stream_id} · {designation}", detailed_width, self._font(8))
            second_line = self._fit_text(
                status + (f" · {extras}" if extras else ""),
                detailed_width,
                self._font(8),
            )
            label = first_line + (f"\n{second_line}" if second_line else "")
        tx, ty = self._point(label_x, label_y)
        text_id = self.canvas.create_text(
            tx,
            ty,
            text=label,
            anchor="s",
            justify="center",
            fill=WARN if pending_recalculation else INK,
            font=self._font(8),
            tags=(entity, "role:edge_label", state_tag),
        )
        bbox = self.canvas.bbox(text_id)
        if bbox:
            rect = self.canvas.create_rectangle(
                bbox[0] - 5,
                bbox[1] - 3,
                bbox[2] + 5,
                bbox[3] + 3,
                fill=CANVAS_BG,
                outline="",
                tags=(entity, "role:edge_label_bg"),
            )
            self.canvas.tag_lower(rect, text_id)

    @staticmethod
    def _edge_label_geometry(
        points: list[list[float]], parallel_index: int = 0, parallel_count: int = 1,
    ) -> tuple[float, float, float]:
        normalized: list[list[float]] = []
        for point in points:
            candidate = [float(point[0]), float(point[1])]
            if not normalized or math.dist(normalized[-1], candidate) > 1e-9:
                normalized.append(candidate)
        horizontal_runs: list[tuple[float, float, float, int]] = []
        current: tuple[float, float, float, int] | None = None
        for segment_index, (left, right) in enumerate(zip(normalized, normalized[1:])):
            if abs(left[1] - right[1]) >= 1e-9:
                current = None
                continue
            low, high = sorted((left[0], right[0]))
            if current and abs(current[2] - left[1]) < 1e-9 and abs(current[1] - low) < 1e-9:
                current = (current[0], high, current[2], current[3])
                horizontal_runs[-1] = current
            else:
                current = (low, high, left[1], segment_index)
                horizontal_runs.append(current)
        if horizontal_runs:
            low, high, y, _ = max(horizontal_runs, key=lambda item: (item[1] - item[0], -item[3]))
            count = max(1, int(parallel_count))
            index = max(0, min(int(parallel_index), count - 1))
            slot_span = max(0.0, (high - low) / count)
            return low + slot_span * (index + 0.5), y - 6.0, slot_span
        best = None
        for segment_index, (left, right) in enumerate(zip(normalized, normalized[1:])):
            length = math.dist(left, right)
            if best is None or length > best[0]:
                best = (length, (left[0] + right[0]) / 2.0, (left[1] + right[1]) / 2.0, segment_index)
        return (best[1], best[2] - 6.0, best[0]) if best else (0.0, 0.0, 0.0)

    @staticmethod
    def _edge_label_point(points: list[list[float]]) -> tuple[float, float]:
        x, y, _ = PFDCanvasView._edge_label_geometry(points)
        return x, y

    def _display_route_points(self, edge: Mapping[str, Any], raw_points: list[list[float]]) -> list[list[float]]:
        points = [[float(point[0]), float(point[1])] for point in raw_points]
        if len(points) < 2:
            return points
        route = edge.get("route") if isinstance(edge.get("route"), Mapping) else {}
        lane_offset = float(route.get("parallel_lane_offset") or 0.0)

        source_node = self._node_rows.get(str(edge.get("source_node_id")), {})
        if source_node.get("kind") == "equipment":
            geometry = source_node.get("geometry") if isinstance(source_node.get("geometry"), Mapping) else {}
            block_id = str(source_node.get("block_id") or "")
            block = self._block_rows.get(block_id, {})
            x = float(geometry.get("x") or 0.0)
            y = float(geometry.get("y") or 0.0)
            width = float(geometry.get("width") or 170.0)
            height = float(geometry.get("height") or 86.0)
            icon_box = (x + 8.0, y + 12.0, x + 56.0, y + height - 12.0)
            source_offset = float(route.get("source_port_offset", lane_offset) or 0.0)
            _, right, center_y = self._symbol_ports(symbol_key(source_node, block), icon_box, source_offset)
            source_y = center_y + source_offset
            fan_index = int(route.get("source_fan_index") or 0)
            fan_count = max(1, int(route.get("source_fan_count") or 1))
            fraction = fan_index / max(1, fan_count - 1) if fan_count > 1 else 0.0
            escape_min = max(right + 4.0, x + 58.0)
            escape_max = max(escape_min, x + 66.0)
            escape_x = escape_min + (escape_max - escape_min) * fraction
            bracket_y = y + height + 8.0 + fan_index * 10.0
            next_x = float(points[1][0])
            reentry_min = x + width + 6.0
            reentry_max = max(reentry_min, next_x - 6.0)
            reentry_x = reentry_min + (reentry_max - reentry_min) * fraction
            points = [
                [right, source_y],
                [escape_x, source_y],
                [escape_x, bracket_y],
                [reentry_x, bracket_y],
                [reentry_x, source_y],
                *points[1:],
            ]

        target_node = self._node_rows.get(str(edge.get("target_node_id")), {})
        if target_node.get("kind") == "equipment":
            geometry = target_node.get("geometry") if isinstance(target_node.get("geometry"), Mapping) else {}
            block_id = str(target_node.get("block_id") or "")
            block = self._block_rows.get(block_id, {})
            x = float(geometry.get("x") or 0.0)
            y = float(geometry.get("y") or 0.0)
            height = float(geometry.get("height") or 86.0)
            icon_box = (x + 8.0, y + 12.0, x + 56.0, y + height - 12.0)
            target_offset = float(route.get("target_port_offset", lane_offset) or 0.0)
            left, _, center_y = self._symbol_ports(symbol_key(target_node, block), icon_box, target_offset)
            target_y = center_y + target_offset
            points = [*points[:-1], [points[-1][0], points[-1][1]], [left, target_y]]
        return points

    @staticmethod
    def _symbol_ports(
        key: str, box: tuple[float, float, float, float], y_offset: float = 0.0,
    ) -> tuple[float, float, float]:
        x1, y1, x2, y2 = box
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        width, height = x2 - x1, y2 - y1
        if key == "pump" or key in {"exchanger", "heater"}:
            radius = min(width, height) * 0.38
            horizontal_radius = math.sqrt(max(0.0, radius * radius - float(y_offset) ** 2))
            return cx - horizontal_radius, cx + horizontal_radius, cy
        if key == "compressor":
            return x1 + width * 0.18, x2 - width * 0.10, cy
        if key == "turbine":
            return x1 + width * 0.10, x2 - width * 0.18, cy
        if key == "tower":
            return cx - width * 0.24, cx + width * 0.24, cy
        if key in {"vessel", "tank", "reactor", "agitator"}:
            return cx - width * 0.27, cx + width * 0.27, cy
        if key == "pfr":
            return x1 + 2.0, x2 - 2.0, cy
        if key in {"mixer", "splitter"}:
            return x1 + width * 0.14, x2 - width * 0.14, cy
        if key in {"membrane", "package", "fitting", "flange", "generic"}:
            return x1 + width * 0.12, x2 - width * 0.12, cy
        if key == "valve":
            return x1 + width * 0.18, x2 - width * 0.18, cy
        if key == "pipeline":
            return x1 + width * 0.10, x2 - width * 0.10, cy
        return x1 + width * 0.12, x2 - width * 0.12, cy

    def _draw_node(self, node: dict[str, Any]) -> None:
        geometry = node.get("geometry") if isinstance(node.get("geometry"), Mapping) else {}
        x = float(geometry.get("x") or 0.0) * self.zoom
        y = float(geometry.get("y") or 0.0) * self.zoom
        width = float(geometry.get("width") or 120.0) * self.zoom
        height = float(geometry.get("height") or 52.0) * self.zoom
        node_id = str(node.get("node_id") or "")
        self._node_rows[node_id] = node
        if node.get("kind") != "equipment":
            self._draw_boundary(node, x, y, width, height)
            return
        block_id = str(node.get("block_id") or node_id.removeprefix("block:"))
        entity = f"entity:block:{block_id}"
        block = self._block_rows.get(block_id, {})
        overlay = self.overlays.get(block_id, {})
        mapping_status = str(node.get("mapping_status") or "")
        mode = str(node.get("mapping_mode") or "")
        mapping_color = status_color(mapping_status, None, mode)
        selection_color = status_color(None, overlay.get("status") or "NOT_READY")
        # The geometry rectangle is an invisible hit/route mask.  Normal nodes
        # intentionally have no decorative card; the engineering symbol and
        # typographic hierarchy carry the identity.  A border appears only for
        # the currently selected entity.
        self.canvas.create_rectangle(
            x,
            y,
            x + width,
            y + height,
            fill="",
            outline="",
            tags=(entity, "role:hit_area"),
        )
        if self.selected_entity == f"block:{block_id}":
            self.canvas.create_rectangle(
                x - 4 * self.zoom,
                y - 4 * self.zoom,
                x + width + 4 * self.zoom,
                y + height + 4 * self.zoom,
                fill="",
                outline=ACCENT_DARK,
                width=max(2, int(2 * self.zoom)),
                tags=(entity, "role:selection"),
            )
        icon_box = (x + 8 * self.zoom, y + 12 * self.zoom, x + 56 * self.zoom, y + height - 12 * self.zoom)
        icon_key = symbol_key(node, block)
        self._draw_symbol(icon_key, icon_box, entity)
        self.canvas.create_line(
            x + 70 * self.zoom,
            y + 13 * self.zoom,
            x + 70 * self.zoom,
            y + height - 13 * self.zoom,
            fill=mapping_color,
            width=max(2, int(2 * self.zoom)),
            tags=(entity, "role:status_rule"),
        )
        if self.zoom < 0.22:
            self.canvas.tag_bind(entity, "<Button-1>", lambda event, bid=block_id: self._open_block(event, bid))
            self.canvas.tag_bind(entity, "<Button-3>", lambda event, bid=block_id: self._menu_block(event, bid))
            return
        if self.zoom < 0.45:
            overview_left = x + 74 * self.zoom
            overview_width = max(1.0, x + width - 6 * self.zoom - overview_left)
            self.canvas.create_text(
                overview_left,
                y + 32 * self.zoom,
                anchor="nw",
                text=self._fit_text(block_id, overview_width, self._font(7, bold=True)),
                justify="left",
                fill=INK,
                font=self._font(7, bold=True),
                tags=(entity, "role:label_primary"),
            )
            self.canvas.tag_bind(entity, "<Button-1>", lambda event, bid=block_id: self._open_block(event, bid))
            self.canvas.tag_bind(entity, "<Button-3>", lambda event, bid=block_id: self._menu_block(event, bid))
            return
        source_type = str(block.get("source", {}).get("block_type") or "UNKNOWN")
        effective = block.get("effective_mapping") if isinstance(block.get("effective_mapping"), Mapping) else {}
        family = str(effective.get("family_name") or node.get("subtitle") or node.get("family_id") or "待识别")
        basis = "手工指定" if mode == "user_override" else STATUS_LABELS.get(mapping_status, mapping_status or "待识别")
        basis = {
            "类别字段": "类别",
            "特征识别": "特征",
            "保留候选": "候选",
        }.get(basis, basis)
        designation = str(overlay.get("designation") or "选型待参数闭合")
        model_status = STATUS_LABELS.get(str(overlay.get("status") or ""), str(overlay.get("status") or "待闭合"))
        model_status_short = STATUS_SHORT_LABELS.get(
            str(overlay.get("status") or ""),
            "! 待闭合" if not overlay.get("status") else compact_text(model_status, 6),
        )
        label_left = x + 74 * self.zoom
        label_right = x + width - 6 * self.zoom
        label_width = max(1.0, label_right - label_left)
        primary_font = self._font(9, bold=True)
        secondary_font = self._font(8)
        status_font = self._font(7)
        status_code = str(overlay.get("status") or "")
        real_selection = status_code in {
            "final_model", "same_equipment_verified", "standard_candidate", "vendor_candidate", "type_selected",
        } and designation != "选型待参数闭合"
        primary_text = self._fit_text(f"{block_id}  ·  {source_type}", label_width, primary_font)
        basis_text = self._fit_text(basis, label_width * 0.38, status_font)
        basis_width = tkfont.Font(font=status_font).measure(basis_text)
        family_text = self._fit_text(family, max(1.0, label_width - basis_width - 5 * self.zoom), secondary_font)
        model_status_text = self._fit_text(model_status_short, label_width * 0.42, status_font)
        model_status_width = tkfont.Font(font=status_font).measure(model_status_text)
        designation_text = self._fit_text(designation, max(1.0, label_width - model_status_width - 5 * self.zoom), secondary_font)
        if self.detail_mode == "compact":
            compact_line = self._fit_text(f"{block_id} · {family}", label_width, primary_font)
            self.canvas.create_text(
                label_left,
                y + 32 * self.zoom,
                anchor="nw",
                text=compact_line,
                justify="left",
                fill=INK,
                font=primary_font,
                tags=(entity, "role:label_primary"),
            )
        elif self.detail_mode == "standard":
            basis_suffix = f" · {basis}" if mode == "user_override" or mapping_status not in {"AUTO_EXACT", ""} else ""
            full_mapping_line = f"{block_id} · {source_type}→{family}{basis_suffix}"
            mapping_line = self._fit_text(full_mapping_line, label_width, primary_font)
            if mapping_line.endswith("…") and family not in mapping_line:
                mapping_line = self._fit_text(f"{block_id} · {family}{basis_suffix}", label_width, primary_font)
            selection_line = (
                f"{designation}  {model_status_short}" if real_selection else model_status_short
            )
            selection_line = self._fit_text(selection_line, label_width, secondary_font)
            self.canvas.create_text(
                label_left,
                y + 23 * self.zoom,
                anchor="nw",
                text=mapping_line,
                justify="left",
                fill=INK,
                font=primary_font,
                tags=(entity, "role:label_primary"),
            )
            self.canvas.create_text(
                label_left,
                y + 51 * self.zoom,
                anchor="nw",
                text=selection_line,
                justify="left",
                fill=selection_color,
                font=secondary_font,
                tags=(entity, "role:model_status"),
            )
        else:
            self.canvas.create_text(
                label_left, y + 10 * self.zoom, anchor="nw", text=primary_text,
                justify="left", fill=INK, font=primary_font, tags=(entity, "role:label_primary"),
            )
            self.canvas.create_text(
                label_left, y + 33 * self.zoom, anchor="nw", text=family_text,
                justify="left", fill=MUTED, font=secondary_font, tags=(entity, "role:label_secondary"),
            )
            self.canvas.create_text(
                x + width - 6 * self.zoom, y + 33 * self.zoom, anchor="ne", text=basis_text,
                justify="right", fill=mapping_color, font=status_font, tags=(entity, "role:mapping_basis"),
            )
            self.canvas.create_text(
                label_left, y + 54 * self.zoom, anchor="nw", text=designation_text,
                justify="left", fill=INK, font=secondary_font, tags=(entity, "role:label_status"),
            )
            self.canvas.create_text(
                x + width - 6 * self.zoom, y + 54 * self.zoom, anchor="ne", text=model_status_text,
                justify="right", fill=selection_color, font=status_font, tags=(entity, "role:model_status"),
            )
        self.canvas.tag_bind(entity, "<Button-1>", lambda event, bid=block_id: self._open_block(event, bid))
        self.canvas.tag_bind(entity, "<Button-3>", lambda event, bid=block_id: self._menu_block(event, bid))

    def _draw_boundary(self, node: Mapping[str, Any], x: float, y: float, width: float, height: float) -> None:
        stream_id = str(node.get("stream_id") or "")
        entity = f"entity:stream:{stream_id}"
        overlay = self.overlays.get(f"stream:{stream_id}", {})
        recalculation_status = str(overlay.get("status") or overlay.get("recalculation_status") or "")
        pending_recalculation = recalculation_status in {
            "WAITING_CALCULATED_PARAMETERS",
            "RELATED_STREAM_RECALC_REQUIRED",
            "RELATED_STREAM_PENDING_RECALC",
            "TYPE_ROUTE_CHANGE_IMPACT_RECALC_REQUIRED",
            "PARAMETERS_CHANGED_PENDING_RECALC",
            "UPSTREAM_RELATED_PENDING_RECALC",
            "DOWNSTREAM_RELATED_PENDING_RECALC",
        }
        line_color = WARN if pending_recalculation else ACCENT
        line_dash = (4, 3) if pending_recalculation else None
        state_tag = "state:waiting_recalculation" if pending_recalculation else "state:current"
        cy = y + height / 2.0
        if node.get("kind") == "boundary_feed":
            left = x + 10 * self.zoom
            right = x + width
        else:
            left = x
            right = x + width - 10 * self.zoom
        self.canvas.create_line(
            left,
            cy,
            right,
            cy,
            arrow="last",
            fill=line_color,
            width=2,
            dash=line_dash,
            tags=(entity, "role:boundary", state_tag),
        )
        if self.zoom < 0.22:
            self.canvas.tag_bind(entity, "<Button-1>", lambda event, sid=stream_id: self._open_stream(event, sid))
            self.canvas.tag_bind(entity, "<Button-3>", lambda event, sid=stream_id: self._menu_stream(event, sid))
            return
        if self.zoom < 0.45:
            self.canvas.create_text(
                x + width / 2.0,
                cy - 5 * self.zoom,
                anchor="s",
                text=compact_text(f"{stream_id} · !" if pending_recalculation else stream_id, 12),
                fill=WARN if pending_recalculation else INK,
                font=self._font(7, bold=True),
                justify="center",
                tags=(entity, "role:boundary_label"),
            )
            self.canvas.tag_bind(entity, "<Button-1>", lambda event, sid=stream_id: self._open_stream(event, sid))
            self.canvas.tag_bind(entity, "<Button-3>", lambda event, sid=stream_id: self._menu_stream(event, sid))
            return
        status_code = str(overlay.get("status") or "")
        designation = str(overlay.get("designation") or "")
        real_selection = status_code in {
            "final_model", "same_equipment_verified", "standard_candidate", "vendor_candidate", "type_selected",
        } and designation not in {"", "工业管道", "管道待水力闭合"}
        label = compact_text(node.get("label"), 24)
        if pending_recalculation:
            label = compact_text(f"{stream_id} · ! 待复核", 24)
        if self.detail_mode != "compact" and real_selection and not pending_recalculation:
            status_short = STATUS_SHORT_LABELS.get(status_code, "")
            model_line = self._fit_text(f"{designation}  {status_short}", max(24.0, width - 12 * self.zoom), self._font(7))
            self.canvas.create_text(
                x + width / 2.0,
                cy - 5 * self.zoom,
                anchor="s",
                text=label,
                fill=WARN if pending_recalculation else INK,
                font=self._font(8),
                justify="center",
                tags=(entity, "role:boundary_label"),
            )
            self.canvas.create_text(
                x + width / 2.0,
                cy + 5 * self.zoom,
                anchor="n",
                text=model_line,
                fill=INK,
                font=self._font(7),
                justify="center",
                tags=(entity, "role:boundary_label"),
            )
        else:
            self.canvas.create_text(
                x + width / 2.0,
                cy - 5 * self.zoom,
                anchor="s",
                text=label,
                fill=WARN if pending_recalculation else INK,
                font=self._font(8),
                justify="center",
                tags=(entity, "role:boundary_label", state_tag),
            )
        self.canvas.tag_bind(entity, "<Button-1>", lambda event, sid=stream_id: self._open_stream(event, sid))
        self.canvas.tag_bind(entity, "<Button-3>", lambda event, sid=stream_id: self._menu_stream(event, sid))

    def _draw_symbol(self, key: str, box: tuple[float, float, float, float], entity: str) -> None:
        x1, y1, x2, y2 = box
        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        w, h = x2 - x1, y2 - y1
        tags = (entity, "role:symbol")
        line = {"fill": INK, "width": max(1, int(2 * self.zoom)), "tags": tags}
        outline = {"outline": INK, "fill": "", "width": max(1, int(2 * self.zoom)), "tags": tags}
        if key == "pump":
            r = min(w, h) * 0.38
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, **outline)
            self.canvas.create_polygon(cx - r * 0.35, cy - r * 0.65, cx + r * 0.7, cy, cx - r * 0.35, cy + r * 0.65, outline=INK, fill="", width=2, tags=tags)
        elif key in {"compressor", "turbine"}:
            if key == "compressor":
                points = (x1 + w * .18, y1 + h * .18, x2 - w * .10, y1 + h * .32, x2 - w * .10, y2 - h * .32, x1 + w * .18, y2 - h * .18)
            else:
                points = (x1 + w * .10, y1 + h * .32, x2 - w * .18, y1 + h * .18, x2 - w * .18, y2 - h * .18, x1 + w * .10, y2 - h * .32)
            self.canvas.create_polygon(*points, outline=INK, fill="", width=2, tags=tags)
        elif key == "tower":
            self.canvas.create_oval(cx - w * .24, y1 + 2, cx + w * .24, y1 + h * .22, **outline)
            self.canvas.create_rectangle(cx - w * .24, y1 + h * .11, cx + w * .24, y2 - h * .11, outline=INK, width=2, tags=tags)
            self.canvas.create_oval(cx - w * .24, y2 - h * .22, cx + w * .24, y2 - 2, **outline)
        elif key in {"exchanger", "heater"}:
            r = min(w, h) * .38
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, **outline)
            if key == "exchanger":
                self.canvas.create_line(cx - r * .65, cy - r * .65, cx + r * .65, cy + r * .65, **line)
                self.canvas.create_line(cx - r * .65, cy + r * .65, cx + r * .65, cy - r * .65, **line)
            else:
                self.canvas.create_line(cx - r * .65, cy, cx + r * .65, cy, **line)
                self.canvas.create_line(cx, cy - r * .65, cx, cy + r * .65, **line)
        elif key in {"vessel", "tank", "reactor", "agitator"}:
            self.canvas.create_oval(cx - w * .27, y1 + 2, cx + w * .27, y1 + h * .25, **outline)
            self.canvas.create_rectangle(cx - w * .27, y1 + h * .12, cx + w * .27, y2 - h * .12, outline=INK, width=2, tags=tags)
            self.canvas.create_oval(cx - w * .27, y2 - h * .25, cx + w * .27, y2 - 2, **outline)
            if key == "agitator":
                self.canvas.create_line(cx, y1 + h * .20, cx, y2 - h * .22, **line)
                self.canvas.create_line(cx - w * .18, cy + h * .12, cx + w * .18, cy + h * .12, **line)
        elif key == "pfr":
            self.canvas.create_oval(x1 + 2, cy - h * .25, x2 - 2, cy + h * .25, **outline)
            self.canvas.create_line(x1 + w * .22, cy, x2 - w * .22, cy, arrow="last", **line)
        elif key in {"mixer", "splitter"}:
            self.canvas.create_polygon(cx, y1 + h * .12, x2 - w * .14, cy, cx, y2 - h * .12, x1 + w * .14, cy, outline=INK, fill="", width=2, tags=tags)
            self.canvas.create_line(x1 + w * .25, cy, x2 - w * .25, cy, arrow="last", **line)
        elif key == "membrane":
            self.canvas.create_rectangle(x1 + w * .12, y1 + h * .18, x2 - w * .12, y2 - h * .18, outline=INK, width=2, tags=tags)
            for fraction in (.35, .50, .65):
                self.canvas.create_line(x1 + w * .25, y1 + h * fraction, x2 - w * .25, y1 + h * fraction, **line)
        elif key == "valve":
            self.canvas.create_polygon(x1 + w * .18, cy - h * .25, cx, cy, x1 + w * .18, cy + h * .25, outline=INK, fill="", width=2, tags=tags)
            self.canvas.create_polygon(x2 - w * .18, cy - h * .25, cx, cy, x2 - w * .18, cy + h * .25, outline=INK, fill="", width=2, tags=tags)
        elif key == "pipeline":
            self.canvas.create_line(x1 + w * .10, cy, x2 - w * .10, cy, arrow="last", **line)
        elif key in {"package", "fitting", "flange"}:
            self.canvas.create_rectangle(x1 + w * .12, y1 + h * .18, x2 - w * .12, y2 - h * .18, outline=INK, width=2, dash=(4, 3) if key == "package" else None, tags=tags)
            self.canvas.create_text(cx, cy, text={"package": "PKG", "fitting": "FIT", "flange": "FLG"}[key], fill=INK, font=self._font(7, bold=True, family="Segoe UI"), tags=tags)
        else:
            self.canvas.create_rectangle(x1 + w * .12, y1 + h * .18, x2 - w * .12, y2 - h * .18, outline=INK, width=2, tags=tags)
            self.canvas.create_text(cx, cy, text="?", fill=MUTED, font=self._font(12, bold=True, family="Segoe UI"), tags=tags)

    def _font(self, size: int, *, bold: bool = False, family: str = "Microsoft YaHei UI") -> tuple[str, int, str]:
        scaled = max(5, int(round(size * self.zoom)))
        return (family, scaled, "bold" if bold else "normal")

    @staticmethod
    def _fit_text(value: Any, max_width: float, font_spec: tuple[str, int, str]) -> str:
        text = " ".join(str(value or "").split())
        if not text or max_width <= 0:
            return ""
        measured = tkfont.Font(font=font_spec)
        if measured.measure(text) <= max_width:
            return text
        ellipsis = "…"
        if measured.measure(ellipsis) > max_width:
            return ""
        left, right = 0, len(text)
        while left < right:
            middle = (left + right + 1) // 2
            if measured.measure(text[:middle] + ellipsis) <= max_width:
                left = middle
            else:
                right = middle - 1
        return text[:left] + ellipsis

    def _wheel_zoom(self, event: tk.Event) -> str:
        factor = 1.12 if event.delta > 0 else 1 / 1.12
        self.set_zoom(self.zoom * factor)
        return "break"

    def _select(self, entity_id: str | None) -> None:
        if self.selected_entity == entity_id:
            return
        self.selected_entity = entity_id
        self.redraw()

    def _open_block(self, _event: tk.Event, block_id: str) -> str:
        self._select(f"block:{block_id}")
        self.on_block_open(block_id)
        return "break"

    def _menu_block(self, event: tk.Event, block_id: str) -> str:
        self._select(f"block:{block_id}")
        self.on_block_menu(block_id, event.x_root, event.y_root)
        return "break"

    def _open_stream(self, _event: tk.Event, stream_id: str) -> str:
        self._select(f"stream:{stream_id}")
        self.on_stream_open(stream_id)
        return "break"

    def _menu_stream(self, event: tk.Event, stream_id: str) -> str:
        self._select(f"stream:{stream_id}")
        self.on_stream_menu(stream_id, event.x_root, event.y_root)
        return "break"

    def _clear_selection_from_background(self, event: tk.Event) -> None:
        current = self.canvas.find_withtag("current")
        if current:
            return
        self._select(None)


__all__ = [
    "BLOCK_SYMBOLS",
    "FAMILY_SYMBOLS",
    "PFDCanvasView",
    "STATUS_LABELS",
    "compact_text",
    "selection_overlay_from_derivation",
    "status_color",
    "symbol_key",
]
