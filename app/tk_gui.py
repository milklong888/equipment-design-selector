from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import tkinter as tk
from collections import defaultdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Mapping

try:
    from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP, TkinterDnD
except ImportError:  # The normal file picker remains available in source-only environments.
    COPY = DND_FILES = REFUSE_DROP = TkinterDnD = None

import aspen_pfd
import derivation_workbench
import result_presentation
import llm_bridge
import pfd_canvas
import user_guide


COLORS = {
    "canvas": "#F1F4F7",
    "panel": "#FFFFFF",
    "ink": "#17212B",
    "muted": "#5D6B78",
    "line": "#D7DEE5",
    "accent": "#126E82",
    "accent_dark": "#0C5261",
    "safe": "#1F7A55",
    "warn": "#A86713",
    "bad": "#B13A3A",
    "soft": "#EAF1F4",
}


def _app_icon_path(filename: str) -> Path | None:
    """Resolve a bundled UI asset in source and PyInstaller one-file layouts."""

    bases: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))
    bases.append(Path(__file__).resolve().parent)
    for base in bases:
        candidate = base / "assets" / filename
        if candidate.is_file():
            return candidate
    return None


EQUATION_META: dict[str, tuple[str, str]] = {
    "pump_head_from_pressure": ("压差折算压头（非完整总扬程）", "HΔp"),
    "pump_hydraulic_power": ("水力功率", "Pₕ"),
    "pump_shaft_power": ("轴功率", "Pₛ"),
    "pump_cavitation_margin": ("汽蚀余量裕度", "ΔNPSH"),
    "pressure_ratio": ("压力比", "π"),
    "pipe_required_diameter": ("所需内径", "dᵢ"),
    "pipe_actual_velocity": ("实际流速", "u"),
    "design_pressure_basis_conversion": ("设计压力基准换算", "P_d,g"),
    "design_pressure": ("候选设计压力（初筛）", "P_d"),
    "cylinder_thickness": ("筒体内压基础计算厚度", "δ_c"),
    "head_thickness": ("封头内压基础计算厚度", "δ_h"),
    "tower_cross_section": ("塔圆形总截面积", "A_T"),
    "tower_bottom_liquid_height": ("塔底持液高度初筛", "H_b"),
    "cylinder_volume": ("圆筒直段几何容积", "V_straight"),
    "storage_required_volume": ("最低所需总容积", "V_req"),
    "liquid_turbine_pressure_head": ("压差水头分量（初筛）", "H_Δp"),
    "liquid_turbine_hydraulic_power": ("压差功率分量（初筛）", "P_Δp"),
    "liquid_turbine_shaft_power": ("压差分量轴功率初筛", "P_screen"),
    "membrane_area": ("膜面积", "A_m"),
    "exchanger_area": ("换热面积初筛", "A"),
}


UI_OPTION_LABELS: dict[str, str] = {
    "": "（留空 / 由程序判断）",
    "absolute": "绝压",
    "gauge": "表压",
    "liquid": "液相",
    "vapor": "气相",
    "mixed": "气液混相",
    "solid": "固相",
    "nominal_total": "名义总容积",
    "effective_working": "有效工作容积",
    "geometric_total": "几何总容积",
    "2:1_ellipsoidal": "2:1 椭圆形封头",
    "cylindrical_channels": "圆柱通道膜组件",
    "flat_sheet": "平板膜组件",
    "hollow_fiber": "中空纤维膜组件",
    "spiral_wound": "卷式膜组件",
    "same_duty_vendor_curve": "同工况厂家性能曲线",
    "same_duty_performance_map": "同工况完整性能图",
    "mock": "离线模拟（不联网）",
    "openai": "OpenAI 官方接口",
    "openai_compatible": "OpenAI 兼容接口",
    "local_openai_compatible": "本机 OpenAI 兼容接口",
    "chat_completions": "Chat Completions（传统兼容）",
    "responses": "Responses（推理模型）",
    "ambiguity_resolution": "模糊项判断",
    "audit": "结果审核",
    "engineering_choice": "设备型式、材料与零部件受控选择",
    "kg_retrieval_planning": "知识检索规划",
    "semantic_extraction": "语义信息提取",
    "textual_condition_judgment": "文字工况判断",
    "minimum": "最小必要上下文",
    "routed": "按设备路由的上下文",
    "full_family": "当前设备族完整上下文",
    "full_bundle": "完整知识包上下文",
}


class TranslatedCombobox(ttk.Combobox):
    """Show translated choices while keeping canonical values in application state."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        textvariable: tk.StringVar,
        values: Any = (),
        option_labels: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        self.canonical_variable = textvariable
        canonical_values = list(
            dict.fromkeys(str(value) for value in values)
        )
        labels = dict(UI_OPTION_LABELS)
        labels.update(
            {
                str(key): str(value)
                for key, value in (option_labels or {}).items()
            }
        )
        self.canonical_to_display = {
            value: labels.get(value, value)
            for value in canonical_values
        }
        self.display_to_canonical = {
            display: canonical
            for canonical, display in self.canonical_to_display.items()
        }
        if len(self.display_to_canonical) != len(self.canonical_to_display):
            raise ValueError("下拉框中文显示值必须唯一。")
        self.display_variable = tk.StringVar(master=master)
        self._syncing_translation = False
        super().__init__(
            master,
            textvariable=self.display_variable,
            values=tuple(
                self.canonical_to_display[value]
                for value in canonical_values
            ),
            **kwargs,
        )
        self._canonical_trace_id = self.canonical_variable.trace_add(
            "write",
            self._sync_display_from_canonical,
        )
        self._display_trace_id = self.display_variable.trace_add(
            "write",
            self._sync_canonical_from_display,
        )
        self._sync_display_from_canonical()

    def _sync_display_from_canonical(self, *_args: Any) -> None:
        if self._syncing_translation:
            return
        self._syncing_translation = True
        try:
            canonical = self.canonical_variable.get()
            self.display_variable.set(
                self.canonical_to_display.get(canonical, canonical)
            )
        finally:
            self._syncing_translation = False

    def _sync_canonical_from_display(self, *_args: Any) -> None:
        if self._syncing_translation:
            return
        self._syncing_translation = True
        try:
            display = self.display_variable.get()
            self.canonical_variable.set(
                self.display_to_canonical.get(display, display)
            )
        finally:
            self._syncing_translation = False


class HoverHelp:
    """Small delayed, disposable explanation popup for dense engineering forms."""

    def __init__(self, widget: tk.Widget, text_provider: str | Callable[[], str], delay_ms: int = 450) -> None:
        self.widget = widget
        self.text_provider = text_provider
        self.delay_ms = delay_ms
        self.after_id: str | None = None
        self.popup: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event: Any = None) -> None:
        self._cancel()
        self.after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self.after_id is not None:
            try:
                self.widget.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None

    def _show(self) -> None:
        self.after_id = None
        if not self.widget.winfo_exists() or self.popup is not None:
            return
        text_value = self.text_provider() if callable(self.text_provider) else self.text_provider
        text_value = str(text_value or "").strip()
        if not text_value:
            return
        popup = tk.Toplevel(self.widget)
        popup.wm_overrideredirect(True)
        try:
            popup.attributes("-topmost", True)
        except tk.TclError:
            pass
        label = tk.Label(
            popup,
            text=text_value,
            justify="left",
            anchor="w",
            wraplength=390,
            bg="#FFF8DF",
            fg=COLORS["ink"],
            relief="solid",
            borderwidth=1,
            padx=11,
            pady=9,
            font=("Microsoft YaHei UI", 9),
        )
        label.pack()
        popup.update_idletasks()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + max(0, (self.widget.winfo_height() - popup.winfo_reqheight()) // 2)
        x = min(x, max(0, self.widget.winfo_screenwidth() - popup.winfo_reqwidth() - 12))
        y = min(max(0, y), max(0, self.widget.winfo_screenheight() - popup.winfo_reqheight() - 48))
        popup.wm_geometry(f"+{x}+{y}")
        self.popup = popup

    def _hide(self, _event: Any = None) -> None:
        self._cancel()
        if self.popup is not None:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
            self.popup = None


def _math_text(value: str) -> str:
    replacements = (
        ("Pout_abs", "P_out,abs"),
        ("Pin_abs", "P_in,abs"),
        ("Pout", "P_out"),
        ("Pin", "P_in"),
        ("rho", "ρ"),
        ("eta", "η"),
        ("phi", "φ"),
        ("sigma", "σ"),
        ("pi", "π"),
        ("*", " × "),
        ("^2", "²"),
        ("^6", "⁶"),
    )
    result = value
    for source, target in replacements:
        result = result.replace(source, target)
    return " ".join(result.split())


def _pretty_equation(item: dict[str, Any]) -> str:
    chain = str(item.get("equation_chain", "")).strip()
    parts = chain.split(" = ")
    calc_id = str(item.get("calculation_id", ""))
    title, symbol = EQUATION_META.get(calc_id, (calc_id or "计算结果", _math_text(parts[0]) if parts else "Y"))
    if len(parts) >= 4:
        formula = _math_text(parts[1])
        substitution = _math_text(" = ".join(parts[2:-1]))
        answer = _math_text(parts[-1])
        return f"{title}：{symbol} = {formula}\n    = {substitution}\n    = {answer}"
    return f"{title}：{_math_text(chain)}" if chain else title


def _formula_trace_text(item: dict[str, Any]) -> str:
    trace = item.get("formula_trace")
    if not isinstance(trace, dict):
        return "追溯：当前结果没有机器公式追溯记录"
    definition = trace.get("formula_definition")
    definition = definition if isinstance(definition, dict) else {}
    implementation = definition.get("implementation_binding")
    implementation = implementation if isinstance(implementation, dict) else {}
    lines = [
        f"追溯状态：{trace.get('traceability_status') or 'OPEN'}",
        f"公式 ID：{trace.get('formula_id') or 'OPEN'}",
        f"公式定义 SHA-256：{trace.get('formula_definition_sha256') or 'OPEN'}",
        f"本次计算 SHA-256：{trace.get('calculation_trace_sha256') or 'OPEN'}",
        (
            "代码实现："
            f"{implementation.get('implementation_ref') or 'OPEN'} / "
            f"{implementation.get('binding_status') or 'OPEN'}"
        ),
        f"代码 SHA-256：{implementation.get('source_file_sha256') or 'OPEN'}",
        "输入绑定：",
    ]
    input_bindings = [
        binding
        for binding in trace.get("input_bindings", [])
        if isinstance(binding, dict)
    ]
    if input_bindings:
        for binding in input_bindings:
            lines.append(
                "  - "
                f"{binding.get('field_id')}={binding.get('value')} "
                f"{binding.get('unit') or ''}；"
                f"{binding.get('source_kind')}；"
                f"{binding.get('binding_status')}；"
                f"SHA-256={binding.get('field_value_sha256')}"
            )
    else:
        lines.append("  - 无登记输入绑定")
    lines.append("公式来源：")
    source_bindings = [
        binding
        for binding in definition.get("source_bindings", [])
        if isinstance(binding, dict)
    ]
    if source_bindings:
        for binding in source_bindings:
            lines.append(
                "  - "
                f"{binding.get('reference')}；"
                f"{binding.get('binding_status')}；"
                f"定位行={binding.get('locator_line_1based') or 'OPEN'}；"
                f"SHA-256={binding.get('source_file_sha256') or 'OPEN'}"
            )
    else:
        lines.append("  - 未登记来源")
    gaps = trace.get("open_traceability_gaps", [])
    lines.append(
        "尚未闭合："
        + ("；".join(map(str, gaps)) if isinstance(gaps, list) and gaps else "无")
    )
    return "\n".join(lines)


def _set_text(widget: tk.Text, text: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", text)
    widget.configure(state="disabled")


def _display_cell(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _customer_overview_rows(overview: Any) -> list[dict[str, Any]]:
    """Pure adapter kept outside Tk so the GUI display contract is testable."""
    return result_presentation.customer_overview_display_rows(overview)


def _result_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if isinstance(value.get("result"), dict) and not isinstance(value["result"].get("equipment"), list):
        return value["result"]
    nested = value.get("value")
    if isinstance(nested, dict) and isinstance(nested.get("result"), dict):
        return nested["result"]
    equipment = value.get("equipment")
    if isinstance(equipment, list) and equipment:
        first = equipment[0]
        if isinstance(first, dict):
            return first.get("match_result", first)
    result = value.get("result")
    if isinstance(result, dict) and isinstance(result.get("equipment"), list) and result["equipment"]:
        first = result["equipment"][0]
        if isinstance(first, dict):
            return first.get("match_result", first)
    return value


def _equipment_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    if isinstance(value.get("equipment"), list):
        return [row for row in value["equipment"] if isinstance(row, dict)]
    result = value.get("result")
    if isinstance(result, dict) and isinstance(result.get("equipment"), list):
        return [row for row in result["equipment"] if isinstance(row, dict)]
    return []


class EquipmentDesignTkApp:
    def __init__(self, root: tk.Tk, api: Any, core: Any) -> None:
        self.root = root
        self.api = api
        self.core = core
        self.catalog = core.load_catalog()
        self.com = core.com_capability()
        self.skill = core.skill_entry()
        self.knowledge_registry = core.knowledge_packages()
        self.knowledge_field_catalog = self._load_knowledge_field_catalog()
        self.llm_provider_registry = llm_bridge.provider_catalog()
        self.last_result: dict[str, Any] | None = None
        self.last_deterministic_result: dict[str, Any] | None = None
        self.last_manual: dict[str, Any] | None = None
        self.last_source_input: dict[str, Any] | None = None
        self.llm_proposal: dict[str, Any] | None = None
        self._tested_llm_connection_fingerprint: str | None = None
        self._applied_llm_settings: dict[str, Any] | None = None
        self._applied_llm_settings_fingerprint: str | None = None
        self.session_dir: str | None = None
        self.presentation: dict[str, Any] = {"equipment": []}
        self.derivation_sessions: dict[str, dict[str, Any]] = {}
        self.derivation_result_to_baseline: dict[str, str] = {}
        self.current_derivation_workbench: dict[str, Any] = {}
        self.current_derivation_node_id = "source"
        self.current_derivation_field: dict[str, Any] | None = None
        self.aspen_bundle: dict[str, Any] = {}
        self.aspen_derivation: dict[str, Any] = {}
        self.aspen_pfd_mapping: dict[str, Any] = {}
        self.aspen_type_overrides: dict[str, str] = {}
        self.pfd_parameter_overrides: dict[str, dict[str, Any]] = {}
        self.pfd_recalculated_results: dict[str, dict[str, Any]] = {}
        self.pfd_invalidated_blocks: set[str] = set()
        self.pfd_invalidated_streams: set[str] = set()
        self.pfd_overlays: dict[str, dict[str, Any]] = {}
        self._background_jobs = 0
        self._closing = False
        self._pfd_equipment_by_block: dict[str, dict[str, Any]] = {}
        self._pfd_piping_by_stream: dict[str, dict[str, Any]] = {}
        self._active_pfd_block_id: str | None = None
        self.pfd_parameter_window: tk.Toplevel | None = None
        self.field_vars: dict[str, tk.StringVar] = {}
        self.manual_value_cache: dict[str, dict[str, str]] = {}
        self._rendered_selection_id: str | None = None
        self.selection_by_display = {row["display_name"]: row for row in self.catalog["selections"]}
        self.dnd_available = self._enable_drag_and_drop()
        self._configure_root()
        self._configure_styles()
        self._build_header()
        self._build_workspace()
        self._set_default_selection()
        self.root.bind("<F1>", lambda _event: self._show_user_guide())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _enable_drag_and_drop(self) -> bool:
        self.dnd_error = ""
        if TkinterDnD is None or DND_FILES is None:
            self.dnd_error = "未安装 tkinterdnd2"
            return False
        try:
            TkinterDnD.require(self.root)
            return True
        except Exception as exc:
            self.dnd_error = str(exc)
            return False

    def _configure_root(self) -> None:
        icon_path = _app_icon_path("equipment_design_app.ico")
        if icon_path is not None:
            try:
                self.root.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass
        self.root.title("设备设计图谱与脚本")
        self.root.geometry("1480x900")
        self.root.minsize(1180, 740)
        self.root.configure(bg=COLORS["canvas"])
        self.root.option_add("*Font", "{Microsoft YaHei UI} 10")

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("App.TFrame", background=COLORS["canvas"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("Header.TFrame", background=COLORS["ink"])
        style.configure("HeaderTitle.TLabel", background=COLORS["ink"], foreground="#FFFFFF", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("HeaderSub.TLabel", background=COLORS["ink"], foreground="#B9C4CE", font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabel", background=COLORS["panel"], foreground=COLORS["ink"], font=("Microsoft YaHei UI", 14, "bold"))
        style.configure("Body.TLabel", background=COLORS["panel"], foreground=COLORS["muted"])
        style.configure("Field.TLabel", background=COLORS["panel"], foreground=COLORS["ink"])
        style.configure("Muted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Microsoft YaHei UI", 9))
        style.configure("RequiredBadge.TLabel", background="#FCE8E6", foreground="#9E2F2F", padding=(7, 3), font=("Microsoft YaHei UI", 8, "bold"))
        style.configure("RecommendedBadge.TLabel", background="#FFF1D8", foreground="#8A580B", padding=(7, 3), font=("Microsoft YaHei UI", 8))
        style.configure("OptionalBadge.TLabel", background="#E8F1F3", foreground=COLORS["accent_dark"], padding=(7, 3), font=("Microsoft YaHei UI", 8))
        style.configure("AdvancedBadge.TLabel", background="#ECEFF2", foreground=COLORS["muted"], padding=(7, 3), font=("Microsoft YaHei UI", 8))
        style.configure("Status.TLabel", background="#24323E", foreground="#E7EEF4", padding=(10, 5), font=("Microsoft YaHei UI", 9))
        style.configure("Header.TButton", background="#24323E", foreground="#E7EEF4", padding=(10, 5), borderwidth=0, font=("Microsoft YaHei UI", 9))
        style.map("Header.TButton", background=[("active", "#314454")], foreground=[("active", "#FFFFFF")])
        style.configure("Primary.TButton", background=COLORS["accent"], foreground="#FFFFFF", padding=(14, 9), borderwidth=0, font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", COLORS["accent_dark"]), ("disabled", "#91A0AA")])
        style.configure("Secondary.TButton", background="#E6EDF1", foreground=COLORS["ink"], padding=(12, 8), borderwidth=0)
        style.map("Secondary.TButton", background=[("active", "#D5E0E6")])
        style.layout(
            "Toggle.TCheckbutton",
            [("Checkbutton.padding", {"sticky": "nswe", "children": [("Checkbutton.label", {"sticky": "nswe"})]})],
        )
        style.configure(
            "Toggle.TCheckbutton",
            background=COLORS["panel"],
            foreground=COLORS["ink"],
            padding=(6, 4),
            font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "Toggle.TCheckbutton",
            background=[("active", "#E8F1F3"), ("selected", "#DCECEF")],
            foreground=[("selected", COLORS["accent_dark"])],
        )
        style.configure("TNotebook", background=COLORS["canvas"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 10), font=("Microsoft YaHei UI", 10))
        style.map("TNotebook.Tab", background=[("selected", COLORS["panel"])], foreground=[("selected", COLORS["accent_dark"])])
        style.configure("TEntry", padding=6)
        style.configure("TCombobox", padding=5)

    def _build_header(self) -> None:
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(26, 18))
        header.pack(fill="x")
        title = ttk.Frame(header, style="Header.TFrame")
        title.pack(side="left", fill="x", expand=True)
        ttk.Label(title, text="设备设计图谱与脚本", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(title, text="DETERMINISTIC EQUIPMENT DESIGN · KNOWLEDGE GRAPH · CONTROLLED REVIEW", style="HeaderSub.TLabel").pack(anchor="w", pady=(4, 0))
        status = ttk.Frame(header, style="Header.TFrame")
        status.pack(side="right")
        ttk.Label(status, text=f"规则 {self.catalog.get('rule_version', '—')}", style="Status.TLabel").pack(side="left", padx=4)
        com_text = "Aspen COM 可选 / 可用" if self.com["available"] else "Aspen COM 可选 / 未检测"
        ttk.Label(status, text=com_text, style="Status.TLabel").pack(side="left", padx=4)
        ttk.Label(status, text="LLM 可选", style="Status.TLabel").pack(side="left", padx=4)
        ttk.Button(status, text="使用说明", style="Header.TButton", command=self._show_user_guide).pack(side="left", padx=(4, 0))

    def _show_user_guide(self) -> None:
        existing = getattr(self, "guide_window", None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return
        window = tk.Toplevel(self.root)
        self.guide_window = window
        window.title("使用说明")
        window.geometry("780x680")
        window.minsize(640, 520)
        window.configure(bg=COLORS["canvas"])
        window.transient(self.root)

        heading = ttk.Frame(window, style="Header.TFrame", padding=(24, 16))
        heading.pack(fill="x")
        ttk.Label(heading, text="使用说明", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(heading, text="先选入口，再看结果；不需要先懂全部术语。", style="HeaderSub.TLabel").pack(anchor="w", pady=(3, 0))

        body = ttk.Frame(window, style="Panel.TFrame", padding=(20, 16))
        body.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        scroll = ttk.Scrollbar(body, orient="vertical")
        text = tk.Text(
            body,
            wrap="word",
            yscrollcommand=scroll.set,
            bg=COLORS["panel"],
            fg=COLORS["ink"],
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=6,
            spacing1=2,
            spacing3=5,
            font=("Microsoft YaHei UI", 10),
        )
        scroll.configure(command=text.yview)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.tag_configure("section", foreground=COLORS["accent_dark"], font=("Microsoft YaHei UI", 11, "bold"), spacing1=10, spacing3=5)
        for line in user_guide.IN_APP_GUIDE_TEXT.splitlines(keepends=True):
            text.insert("end", line, "section" if line.startswith("【") else ())
        text.configure(state="disabled")

        footer = ttk.Frame(window, style="App.TFrame", padding=(16, 4, 16, 14))
        footer.pack(fill="x")
        ttk.Label(footer, text="完整说明也随软件放在《使用说明.md》中。按 F1 可再次打开。", style="Muted.TLabel").pack(side="left")
        ttk.Button(footer, text="关闭", style="Secondary.TButton", command=window.destroy).pack(side="right")

    def _build_workspace(self) -> None:
        paned = ttk.Panedwindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=18, pady=18)
        left = ttk.Frame(paned, style="Panel.TFrame", padding=16)
        right = ttk.Frame(paned, style="Panel.TFrame", padding=18)
        paned.add(left, weight=2)
        paned.add(right, weight=3)
        self.tabs = ttk.Notebook(left)
        self.tabs.pack(fill="both", expand=True)
        self._build_aspen_tab()
        self._build_manual_tab()
        self._build_llm_tab()
        self._build_knowledge_tab()
        self._build_results(right)

    def _tab(self) -> ttk.Frame:
        return ttk.Frame(self.tabs, style="Panel.TFrame", padding=18)

    def _intro(self, parent: ttk.Frame, title: str, body: str) -> None:
        ttk.Label(parent, text=title, style="Section.TLabel").pack(anchor="w")
        ttk.Label(parent, text=body, style="Body.TLabel", wraplength=720, justify="left").pack(anchor="w", pady=(5, 18))

    def _build_aspen_tab(self) -> None:
        tab = self._tab()
        self.tabs.add(tab, text="01  Aspen 文件")
        self._intro(tab, "Aspen 文件自动导入", "COM 是可选能力。可用时对源文件做只读复制，在独立子进程中遍历模块、流股、单位、状态和连接，并逐台匹配；不可用时直接选择其他页。")
        file_row = ttk.Frame(tab, style="Panel.TFrame")
        file_row.pack(fill="x", pady=(0, 14))
        self.aspen_path = tk.StringVar()
        drop_hint = "把 .bkp / .apw / .inp 文件拖到这里\n也可以点右边的“选择文件”" if self.dnd_available else "点右边的“选择文件”\n当前环境没有加载拖放组件"
        self.aspen_drop_text = tk.StringVar(value=drop_hint)
        self.aspen_drop_label = tk.Label(
            file_row,
            textvariable=self.aspen_drop_text,
            bg=COLORS["soft"],
            fg=COLORS["accent_dark"],
            activebackground="#DDECEF",
            activeforeground=COLORS["accent_dark"],
            relief="solid",
            borderwidth=1,
            padx=14,
            pady=12,
            justify="left",
            anchor="w",
            wraplength=610,
            font=("Microsoft YaHei UI", 9),
            cursor="hand2",
        )
        self.aspen_drop_label.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.aspen_drop_label.bind("<Button-1>", lambda _event: self._choose_aspen())
        if self.dnd_available:
            self.aspen_drop_label.drop_target_register(DND_FILES)
            self.aspen_drop_label.dnd_bind("<<DropEnter>>", self._on_aspen_drop_enter)
            self.aspen_drop_label.dnd_bind("<<DropLeave>>", self._on_aspen_drop_leave)
            self.aspen_drop_label.dnd_bind("<<Drop>>", self._on_aspen_drop)
        ttk.Button(file_row, text="选择文件", style="Secondary.TButton", command=self._choose_aspen).pack(side="right")
        grid = ttk.Frame(tab, style="Panel.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        self.aspen_basis = tk.StringVar(value="")
        self.aspen_atmospheric = tk.StringVar(value="")
        self.aspen_timeout = tk.StringVar(value="900")
        self.aspen_run = tk.BooleanVar(value=True)
        self.aspen_basis_combo = TranslatedCombobox(
            grid,
            textvariable=self.aspen_basis,
            values=("", "absolute", "gauge"),
            option_labels={"": "请选择压力基准"},
            state="readonly",
        )
        self._labeled_widget(
            grid,
            0,
            "压力基准",
            self.aspen_basis_combo,
        )
        self._labeled_widget(
            grid,
            1,
            "当地大气压 / MPa（表压↔绝压换算需要）",
            ttk.Entry(grid, textvariable=self.aspen_atmospheric),
        )
        self._labeled_widget(grid, 2, "COM 运行超时 / s", ttk.Entry(grid, textvariable=self.aspen_timeout))
        ttk.Checkbutton(grid, text="打开后重新运行并采集原始历史证据", variable=self.aspen_run).grid(row=3, column=1, sticky="w", pady=8)
        note = "COM 当前可用，但不影响其他入口。" if self.com["available"] else "未检测到可用 COM；手动输入、LLM 辅助和图谱查询仍可使用。"
        ttk.Label(tab, text=note, style="Muted.TLabel", wraplength=700).pack(anchor="w", pady=(16, 10))
        self.aspen_button = ttk.Button(tab, text="自动遍历并匹配", style="Primary.TButton", command=self._run_aspen)
        self.aspen_button.pack(fill="x", pady=(8, 0))
        self.aspen_progress = ttk.Label(tab, text="", style="Muted.TLabel")
        self.aspen_progress.pack(anchor="w", pady=8)

    def _build_manual_tab(self) -> None:
        tab = self._tab()
        self.tabs.add(tab, text="02  手动输入")
        self._intro(tab, "用工况和目标条件选设备", "填写入口流股或 Aspen 可导出的物性，再填写要达到的目标条件。算法只推导它真正覆盖的设备参数；材料、结构等不指定时保留为可选推荐或明确缺口。")
        select_frame = ttk.Frame(tab, style="Panel.TFrame")
        select_frame.pack(fill="x", pady=(0, 12))
        ttk.Label(select_frame, text="模块类型 / 设备族", style="Field.TLabel").pack(anchor="w", pady=(0, 5))
        self.manual_selection = tk.StringVar()
        self.manual_combo = ttk.Combobox(select_frame, textvariable=self.manual_selection, values=list(self.selection_by_display), state="readonly")
        self.manual_combo.pack(fill="x")
        self.manual_combo.bind("<<ComboboxSelected>>", lambda _event: self._render_manual_fields())
        guide_row = ttk.Frame(tab, style="Panel.TFrame")
        guide_row.pack(fill="x", pady=(0, 8))
        ttk.Label(
            guide_row,
            text="主计算必填、候选闭合必需和正式证据必需是三个不同层级；候选字段可留空。",
            style="Muted.TLabel",
        ).pack(side="left")
        self.manual_advanced = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            guide_row,
            text="显示候选闭合/已有结果/正式证据项",
            variable=self.manual_advanced,
            command=self._render_manual_fields,
        ).pack(side="right")
        requirement_bar = tk.Frame(tab, bg="#FFF8E8", padx=11, pady=9)
        requirement_bar.pack(fill="x", pady=(0, 10))
        self.manual_requirement_summary_var = tk.StringVar(value="正在读取输入分层……")
        tk.Label(
            requirement_bar,
            textvariable=self.manual_requirement_summary_var,
            bg="#FFF8E8",
            fg="#67490B",
            justify="left",
            anchor="w",
            wraplength=620,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", fill="x", expand=True)
        self.manual_expand_button = ttk.Button(
            requirement_bar,
            text="展开候选/证据项",
            style="Secondary.TButton",
            command=self._toggle_manual_advanced,
        )
        self.manual_expand_button.pack(side="right", padx=(10, 0))
        field_host = ttk.Frame(tab, style="Panel.TFrame")
        field_host.pack(fill="both", expand=True)
        self.field_canvas = tk.Canvas(field_host, bg=COLORS["panel"], highlightthickness=0)
        field_scroll = ttk.Scrollbar(field_host, orient="vertical", command=self.field_canvas.yview)
        self.field_canvas.configure(yscrollcommand=field_scroll.set)
        field_scroll.pack(side="right", fill="y")
        self.field_canvas.pack(side="left", fill="both", expand=True)
        self.field_frame = ttk.Frame(self.field_canvas, style="Panel.TFrame")
        self.field_window = self.field_canvas.create_window((0, 0), window=self.field_frame, anchor="nw")
        self.field_frame.bind("<Configure>", lambda _event: self.field_canvas.configure(scrollregion=self.field_canvas.bbox("all")))
        self.field_canvas.bind("<Configure>", lambda event: self.field_canvas.itemconfigure(self.field_window, width=event.width))
        ttk.Button(tab, text="确定性匹配与计算", style="Primary.TButton", command=self._run_manual).pack(fill="x", pady=(14, 0))

    def _build_llm_tab(self) -> None:
        tab = self._tab()
        self.tabs.add(tab, text="03  Agent 协同")
        self._intro(
            tab,
            "Agent 接管与可选大模型协同",
            "确定性结果始终保留；知识检索和大模型均可关闭。API Key 仅在本进程内存中使用，不写盘、不回显。",
        )
        grid = ttk.Frame(tab, style="Panel.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        provider_ids = [item["id"] for item in self.llm_provider_registry.get("providers", [])]
        self.llm_enabled = tk.BooleanVar(value=True)
        self.llm_knowledge_enabled = tk.BooleanVar(value=True)
        self.llm_provider = tk.StringVar(value="openai_compatible")
        self.llm_key = tk.StringVar()
        self.llm_base = tk.StringVar(value="https://api.openai.com/v1")
        self.llm_model = tk.StringVar(value=os.environ.get("EQUIPMENT_DESIGN_LLM_MODEL_ID", ""))
        self.llm_timeout = tk.StringVar(value=str(self.llm_provider_registry.get("timeout_s", {}).get("default", 90)))
        self.llm_wire_api = tk.StringVar(
            value=os.environ.get("EQUIPMENT_DESIGN_LLM_WIRE_API", "chat_completions")
        )
        self.llm_reasoning_effort = tk.StringVar(
            value=os.environ.get("EQUIPMENT_DESIGN_LLM_REASONING_EFFORT", "medium")
        )
        self.llm_disable_response_storage = tk.BooleanVar(value=True)
        self.llm_injection_point = tk.StringVar(value="engineering_choice")
        self.llm_context_scope = tk.StringVar(value="minimum")
        provider = TranslatedCombobox(
            grid,
            textvariable=self.llm_provider,
            values=provider_ids,
            state="readonly",
        )
        self.llm_provider_combo = provider
        provider.bind("<<ComboboxSelected>>", lambda _event: self._sync_llm_provider())
        self._labeled_widget(grid, 0, "服务接口", provider)
        self._labeled_widget(grid, 1, "API Base URL", ttk.Entry(grid, textvariable=self.llm_base))
        self._labeled_widget(grid, 2, "模型 ID", ttk.Entry(grid, textvariable=self.llm_model))
        self._labeled_widget(
            grid,
            3,
            "API 协议",
            TranslatedCombobox(
                grid,
                textvariable=self.llm_wire_api,
                values=tuple(self.llm_provider_registry.get("wire_apis", ("chat_completions", "responses"))),
                state="readonly",
            ),
        )
        self._labeled_widget(
            grid,
            4,
            "推理强度（Responses）",
            TranslatedCombobox(
                grid,
                textvariable=self.llm_reasoning_effort,
                values=tuple(self.llm_provider_registry.get("reasoning_efforts", ("low", "medium", "high"))),
                option_labels={
                    "minimal": "最低",
                    "low": "低",
                    "medium": "中",
                    "high": "高",
                    "xhigh": "超高（xhigh）",
                },
                state="readonly",
            ),
        )
        self._labeled_widget(grid, 5, "超时 / s", ttk.Entry(grid, textvariable=self.llm_timeout))
        self._labeled_widget(grid, 6, "API Key（仅内存）", ttk.Entry(grid, textvariable=self.llm_key, show="•"))
        option_row = ttk.Frame(grid, style="Panel.TFrame")
        option_row.grid(row=7, column=1, sticky="w", pady=5)
        self.llm_enabled_label = tk.StringVar()
        self.llm_knowledge_enabled_label = tk.StringVar()
        ttk.Checkbutton(
            option_row,
            textvariable=self.llm_enabled_label,
            variable=self.llm_enabled,
            style="Toggle.TCheckbutton",
        ).pack(side="left")
        ttk.Checkbutton(
            option_row,
            textvariable=self.llm_knowledge_enabled_label,
            variable=self.llm_knowledge_enabled,
            style="Toggle.TCheckbutton",
        ).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(
            option_row,
            text="禁止服务端存储响应",
            variable=self.llm_disable_response_storage,
            style="Toggle.TCheckbutton",
        ).pack(side="left", padx=(12, 0))
        self._sync_llm_toggle_labels()

        pack_frame = ttk.Frame(grid, style="Panel.TFrame")
        pack_frame.grid(row=8, column=1, sticky="ew", pady=4)
        pack_frame.columnconfigure(0, weight=1)
        pack_frame.columnconfigure(1, weight=1)
        self.knowledge_pack_vars: dict[str, tk.BooleanVar] = {}
        for index, item in enumerate(self.knowledge_registry.get("packages", [])):
            variable = tk.BooleanVar(value=bool(item.get("default_selected") and item.get("available")))
            self.knowledge_pack_vars[str(item["id"])] = variable
            check = ttk.Checkbutton(pack_frame, text=str(item.get("label") or item["id"]), variable=variable)
            if not item.get("available"):
                check.state(["disabled"])
            check.grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 12), pady=1)
        self._labeled_widget(
            grid,
            9,
            "注入点",
            TranslatedCombobox(
                grid,
                textvariable=self.llm_injection_point,
                values=tuple(llm_bridge.INJECTION_POINT_POLICIES),
                state="readonly",
            ),
        )
        self._labeled_widget(
            grid,
            10,
            "上下文范围",
            TranslatedCombobox(
                grid,
                textvariable=self.llm_context_scope,
                values=("minimum", "routed", "full_family", "full_bundle"),
                state="readonly",
            ),
        )
        self.llm_knowledge_query = tk.StringVar(value="设备选型 公式 证据门 型号状态")
        self._labeled_widget(grid, 11, "检索问题", ttk.Entry(grid, textvariable=self.llm_knowledge_query))
        ttk.Label(grid, text="Agent 任务", style="Field.TLabel").grid(row=12, column=0, sticky="nw", padx=(0, 14), pady=8)
        self.llm_task = tk.Text(grid, height=3, wrap="word", bg="#F8FAFB", fg=COLORS["ink"], relief="solid", borderwidth=1, font=("Microsoft YaHei UI", 10))
        self.llm_task.insert("1.0", "审核当前确定性结果；若候选证据足以唯一化，可提出白名单内的草稿决策，否则保留最泛用类型。")
        self.llm_task.grid(row=12, column=1, sticky="ew", pady=8)
        ttk.Label(
            tab,
            text=(
                "模型只可使用已登记条件、补算配方和候选引用；数值、单位、压力基准、证据与型号状态由程序锁定。"
            ),
            style="Muted.TLabel",
            wraplength=720,
        ).pack(anchor="w", pady=(14, 10))
        self.llm_connection_state = tk.StringVar(value="连接状态：未测试；设置尚未应用")
        ttk.Label(tab, textvariable=self.llm_connection_state, style="Muted.TLabel", wraplength=720).pack(anchor="w", pady=(0, 5))
        self.hybrid_state = tk.StringVar(value="机器状态：等待 Agent 协同运行")
        ttk.Label(tab, textvariable=self.hybrid_state, style="Muted.TLabel", wraplength=720).pack(anchor="w", pady=(0, 8))
        settings_actions = ttk.Frame(tab, style="Panel.TFrame")
        settings_actions.pack(fill="x", pady=(8, 4))
        self.llm_test_button = ttk.Button(
            settings_actions,
            text="测试连接",
            style="Secondary.TButton",
            command=self._test_llm_connection,
        )
        self.llm_test_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.llm_apply_settings_button = ttk.Button(
            settings_actions,
            text="应用设置",
            style="Secondary.TButton",
            command=self._apply_llm_settings,
        )
        self.llm_apply_settings_button.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.llm_apply_settings_button.state(["disabled"])
        self.llm_button = ttk.Button(tab, text="开始协同计算", style="Primary.TButton", command=self._run_llm)
        self.llm_button.pack(fill="x", pady=4)
        self.llm_button.state(["disabled"])
        self.apply_llm_button = ttk.Button(tab, text="接受白名单草案并重新复算", style="Secondary.TButton", command=self._apply_llm)
        self.apply_llm_button.pack(fill="x", pady=4)
        self.apply_llm_button.state(["disabled"])
        for variable in (
            self.llm_enabled,
            self.llm_provider,
            self.llm_key,
            self.llm_base,
            self.llm_model,
            self.llm_timeout,
            self.llm_wire_api,
            self.llm_reasoning_effort,
            self.llm_disable_response_storage,
        ):
            variable.trace_add("write", self._on_llm_connection_setting_changed)
        for variable in (
            self.llm_knowledge_enabled,
            self.llm_injection_point,
            self.llm_context_scope,
            self.llm_knowledge_query,
            *self.knowledge_pack_vars.values(),
        ):
            variable.trace_add("write", self._on_llm_workflow_setting_changed)
        self.llm_task.bind("<KeyRelease>", lambda _event: self._invalidate_applied_llm_settings())

    def _sync_llm_provider(self) -> None:
        selected = self.llm_provider.get()
        definition = next(
            (item for item in self.llm_provider_registry.get("providers", []) if item.get("id") == selected),
            None,
        )
        if isinstance(definition, dict):
            self.llm_base.set(str(definition.get("default_base_url", self.llm_base.get())))

    @staticmethod
    def _llm_settings_sha256(value: Mapping[str, Any]) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest().upper()

    def _llm_connection_config(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.llm_enabled.get()),
            "provider": self.llm_provider.get().strip(),
            "base_url": self.llm_base.get().strip(),
            "model": self.llm_model.get().strip(),
            "timeout_s": self.llm_timeout.get().strip(),
            "wire_api": self.llm_wire_api.get().strip(),
            "reasoning_effort": self.llm_reasoning_effort.get().strip(),
            "disable_response_storage": bool(self.llm_disable_response_storage.get()),
            "api_key": self.llm_key.get(),
        }

    def _collect_llm_settings(self) -> dict[str, Any]:
        selected_packages = sorted(
            package_id
            for package_id, variable in self.knowledge_pack_vars.items()
            if variable.get()
        )
        return {
            "config": self._llm_connection_config(),
            "knowledge_config": {
                "enabled": bool(self.llm_knowledge_enabled.get()),
                "query": self.llm_knowledge_query.get().strip(),
                "package_ids": selected_packages,
                "limit": 8,
            },
            "injection_point": self.llm_injection_point.get(),
            "context_scope": self.llm_context_scope.get(),
            "task": self.llm_task.get("1.0", "end").strip(),
        }

    def _invalidate_applied_llm_settings(self) -> None:
        self._applied_llm_settings = None
        self._applied_llm_settings_fingerprint = None
        if hasattr(self, "llm_button"):
            self.llm_button.state(["disabled"])
        if hasattr(self, "llm_apply_settings_button"):
            connection = self._llm_connection_config()
            tested = self._llm_settings_sha256(connection) == self._tested_llm_connection_fingerprint
            if tested or not connection["enabled"]:
                self.llm_apply_settings_button.state(["!disabled"])
            else:
                self.llm_apply_settings_button.state(["disabled"])
        if hasattr(self, "llm_connection_state"):
            self.llm_connection_state.set("连接状态：设置已更改；请重新应用" if self._tested_llm_connection_fingerprint else "连接状态：设置已更改；请重新测试并应用")

    def _sync_llm_toggle_labels(self) -> None:
        if hasattr(self, "llm_enabled_label"):
            self.llm_enabled_label.set(
                "☑ 勾选=启用大模型协同" if self.llm_enabled.get() else "☐ 勾选=启用大模型协同（当前关闭）"
            )
        if hasattr(self, "llm_knowledge_enabled_label"):
            self.llm_knowledge_enabled_label.set(
                "☑ 勾选=启用知识检索" if self.llm_knowledge_enabled.get() else "☐ 勾选=启用知识检索（当前关闭）"
            )

    def _on_llm_connection_setting_changed(self, *_args: Any) -> None:
        self._sync_llm_toggle_labels()
        self._tested_llm_connection_fingerprint = None
        self._invalidate_applied_llm_settings()

    def _on_llm_workflow_setting_changed(self, *_args: Any) -> None:
        self._sync_llm_toggle_labels()
        self._invalidate_applied_llm_settings()

    def _test_llm_connection(self) -> None:
        config = self._llm_connection_config()
        connection_fingerprint = self._llm_settings_sha256(config)
        if not config["enabled"]:
            self._tested_llm_connection_fingerprint = connection_fingerprint
            self.llm_apply_settings_button.state(["!disabled"])
            self.llm_connection_state.set("连接状态：大模型未启用；可直接应用确定性模式")
            return
        self.llm_apply_settings_button.state(["disabled"])
        self.llm_button.state(["disabled"])
        self.llm_connection_state.set("连接状态：正在测试实际模型 ID……")

        def done(response: dict[str, Any]) -> None:
            if connection_fingerprint != self._llm_settings_sha256(self._llm_connection_config()):
                self.llm_connection_state.set("连接状态：测试期间设置已改变；测试结果已作废")
                return
            value = response.get("value", {}) if isinstance(response.get("value"), Mapping) else {}
            connected = bool(response.get("ok")) and value.get("status") == "CONNECTED"
            if not connected:
                self._tested_llm_connection_fingerprint = None
                detail = str(response.get("error") or value.get("message") or "未知错误")
                self.llm_connection_state.set(f"连接状态：失败 · {detail}")
                messagebox.showerror("连接测试失败", detail, parent=self.root)
                return
            self._tested_llm_connection_fingerprint = connection_fingerprint
            self.llm_apply_settings_button.state(["!disabled"])
            self.llm_connection_state.set(
                f"连接状态：成功 · {value.get('provider', config['provider'])} / {value.get('model_id', config['model'])}；等待应用设置"
            )
            messagebox.showinfo("连接测试成功", value.get("message", "连接成功，模型可调用。"), parent=self.root)

        self._background(
            self.llm_test_button,
            "测试模型连接中…",
            lambda: self.api.test_llm_connection(config),
            done,
        )

    def _apply_llm_settings(self) -> None:
        settings = self._collect_llm_settings()
        connection = settings["config"]
        if connection["enabled"] and self._llm_settings_sha256(connection) != self._tested_llm_connection_fingerprint:
            messagebox.showwarning("尚未通过连接测试", "请先用当前 Provider、Base URL、模型 ID 和 Key 测试连接。", parent=self.root)
            return
        knowledge = settings["knowledge_config"]
        if knowledge["enabled"] and not knowledge["package_ids"]:
            messagebox.showwarning("未选择知识包", "请至少选择一个知识包，或取消“勾选=启用知识检索”。", parent=self.root)
            return
        self._applied_llm_settings = json.loads(json.dumps(settings, ensure_ascii=False))
        self._applied_llm_settings_fingerprint = self._llm_settings_sha256(settings)
        self.llm_button.state(["!disabled"])
        state = "大模型关闭，确定性模式已应用" if not connection["enabled"] else f"已应用 · {connection['provider']} / {connection['model']}"
        self.llm_connection_state.set(f"连接状态：{state}")

    def _build_knowledge_tab(self) -> None:
        tab = self._tab()
        self.tabs.add(tab, text="KG  知识图谱")
        self._intro(tab, "知识图谱与 Skill 入口", "先从目录选择设备族和字段，不需要记 canonical 名称；也可以直接输入自然语言。工作区优先用向量索引，独立 EXE 使用随包图谱。")
        catalog_frame = ttk.Frame(tab, style="Panel.TFrame")
        catalog_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(catalog_frame, text="可查询字段目录", style="Field.TLabel").pack(anchor="w", pady=(0, 5))
        self._knowledge_group_by_display: dict[str, Mapping[str, Any]] = {}
        catalog_groups = self.knowledge_field_catalog.get("groups")
        if not isinstance(catalog_groups, list):
            catalog_groups = []
            for family in self.knowledge_field_catalog.get("families", []):
                if not isinstance(family, Mapping):
                    continue
                fields: list[dict[str, Any]] = []
                for topic in family.get("topics", []):
                    if not isinstance(topic, Mapping):
                        continue
                    for field in topic.get("fields", []):
                        if isinstance(field, Mapping):
                            fields.append({
                                **dict(field),
                                "topic_id": topic.get("topic_id"),
                                "topic_label": topic.get("label"),
                            })
                catalog_groups.append({
                    "group_id": family.get("family_id"),
                    "label": family.get("label") or family.get("family_id"),
                    "fields": fields,
                })
        for group in catalog_groups:
            if not isinstance(group, Mapping):
                continue
            display = str(group.get("label") or group.get("family_name") or group.get("group_id") or "未命名分组")
            if display in self._knowledge_group_by_display:
                display = f"{display} · {group.get('group_id')}"
            self._knowledge_group_by_display[display] = group
        self.kg_family = tk.StringVar()
        self.kg_family_combo = ttk.Combobox(
            catalog_frame,
            textvariable=self.kg_family,
            values=tuple(self._knowledge_group_by_display),
            state="readonly",
        )
        self.kg_family_combo.pack(fill="x")
        self.kg_family_combo.bind("<<ComboboxSelected>>", lambda _event: self._render_knowledge_field_catalog())
        tree_frame = ttk.Frame(catalog_frame, style="Panel.TFrame")
        tree_frame.pack(fill="x", pady=(7, 0))
        self.kg_field_tree = ttk.Treeview(
            tree_frame,
            columns=("label", "canonical_id", "unit", "boundary"),
            show="headings",
            height=6,
        )
        for column, heading, width in (
            ("label", "字段", 145),
            ("canonical_id", "Canonical ID", 170),
            ("unit", "单位", 70),
            ("boundary", "输入/证据定位", 180),
        ):
            self.kg_field_tree.heading(column, text=heading)
            self.kg_field_tree.column(column, width=width, minwidth=55, stretch=column != "unit")
        field_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.kg_field_tree.yview)
        self.kg_field_tree.configure(yscrollcommand=field_scroll.set)
        field_scroll.pack(side="right", fill="y")
        self.kg_field_tree.pack(side="left", fill="x", expand=True)
        self.kg_field_tree.bind("<Double-1>", lambda _event: self._use_selected_knowledge_field(run_query=False))
        self._knowledge_field_by_iid: dict[str, Mapping[str, Any]] = {}
        use_row = ttk.Frame(catalog_frame, style="Panel.TFrame")
        use_row.pack(fill="x", pady=(7, 0))
        ttk.Label(use_row, text="选择一行会自动生成可读查询；双击也可以。", style="Muted.TLabel").pack(side="left")
        ttk.Button(
            use_row,
            text="用选中字段生成查询",
            style="Secondary.TButton",
            command=lambda: self._use_selected_knowledge_field(run_query=False),
        ).pack(side="right")
        row = ttk.Frame(tab, style="Panel.TFrame")
        row.pack(fill="x")
        self.kg_query = tk.StringVar(value="")
        ttk.Entry(row, textvariable=self.kg_query).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.kg_search_button = ttk.Button(row, text="开始查询", style="Primary.TButton", command=self._search_knowledge)
        self.kg_search_button.pack(side="right")
        self.kg_output = tk.Text(tab, height=10, wrap="word", bg="#F8FAFB", fg=COLORS["ink"], relief="solid", borderwidth=1, font=("Consolas", 9), padx=10, pady=10)
        self.kg_output.pack(fill="both", expand=True, pady=(12, 12))
        self.kg_output.insert("1.0", "先从上面的字段目录选择，或输入自然语言，再点“开始查询”。")
        self.kg_output.configure(state="disabled")
        skill_text = f"${self.skill['skill_name']}\n{self.skill['prompt']}\n安装：{self.skill['global_skill_path']}"
        ttk.Label(tab, text=skill_text, style="Muted.TLabel", wraplength=720, justify="left").pack(anchor="w")
        if self._knowledge_group_by_display:
            self.kg_family.set(next(iter(self._knowledge_group_by_display)))
            self._render_knowledge_field_catalog()

    def _load_knowledge_field_catalog(self) -> dict[str, Any]:
        try:
            if hasattr(self.api, "knowledge_catalog"):
                response = self.api.knowledge_catalog()
                if isinstance(response, Mapping) and response.get("ok") and isinstance(response.get("value"), Mapping):
                    return dict(response["value"])
            if hasattr(self.core, "knowledge_catalog"):
                value = self.core.knowledge_catalog()
                if isinstance(value, Mapping):
                    return dict(value)
        except Exception:
            pass
        groups: dict[str, dict[str, Any]] = {}
        for selection in self.catalog.get("selections", []):
            if not isinstance(selection, Mapping):
                continue
            group_id = str(selection.get("family_id") or selection.get("selection_id") or "other")
            group = groups.setdefault(group_id, {
                "group_id": group_id,
                "label": selection.get("family_name") or selection.get("display_name") or group_id,
                "fields": {},
            })
            for field in selection.get("fields", []):
                if not isinstance(field, Mapping) or not field.get("name"):
                    continue
                canonical_id = str(field["name"])
                role = str(field.get("manual_role") or "optional_input")
                boundary = {
                    "delivery_output": "结果输出",
                    "advanced_evidence": "同设备正式证据",
                    "required_input": "主计算输入",
                    "recommended_input": "建议工况输入",
                    "optional_preference": "可选偏好",
                }.get(role, "可选输入")
                group["fields"].setdefault(canonical_id, {
                    "canonical_id": canonical_id,
                    "label": field.get("label") or canonical_id,
                    "unit": field.get("unit"),
                    "manual_role": role,
                    "evidence_boundary": boundary,
                    "aliases": [field.get("label") or canonical_id],
                    "query_template": f"{group['label']} {field.get('label') or canonical_id} {canonical_id}",
                })
        return {
            "schema": "equipment-design-knowledge-field-catalog-v1",
            "groups": [
                {**group, "fields": list(group["fields"].values())}
                for group in groups.values()
            ],
        }

    def _render_knowledge_field_catalog(self) -> None:
        if not hasattr(self, "kg_field_tree"):
            return
        for item in self.kg_field_tree.get_children():
            self.kg_field_tree.delete(item)
        self._knowledge_field_by_iid = {}
        group = self._knowledge_group_by_display.get(self.kg_family.get(), {})
        fields = group.get("fields", []) if isinstance(group, Mapping) else []
        if isinstance(fields, Mapping):
            fields = list(fields.values())
        for field in fields if isinstance(fields, list) else []:
            if not isinstance(field, Mapping):
                continue
            iid = self.kg_field_tree.insert("", "end", values=(
                field.get("label") or field.get("canonical_id"),
                field.get("canonical_id"),
                field.get("unit") or "—",
                field.get("evidence_boundary") or field.get("manual_role") or "—",
            ))
            self._knowledge_field_by_iid[iid] = field

    def _use_selected_knowledge_field(self, *, run_query: bool) -> None:
        selected = self.kg_field_tree.selection()
        if not selected:
            messagebox.showwarning("尚未选择字段", "请先在字段目录中选择一行。", parent=self.root)
            return
        field = self._knowledge_field_by_iid.get(selected[0], {})
        group = self._knowledge_group_by_display.get(self.kg_family.get(), {})
        query = str(field.get("query_template") or "").strip()
        if not query:
            query = " ".join(str(value) for value in (
                group.get("label"),
                field.get("label"),
                field.get("canonical_id"),
            ) if value)
        self.kg_query.set(query)
        if run_query:
            self._search_knowledge()

    def _build_results(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="匹配与推导结果", style="Section.TLabel").pack(anchor="w")
        ttk.Label(parent, text="DETERMINISTIC RESULT", style="Muted.TLabel").pack(anchor="w", pady=(2, 14))
        summary = ttk.Frame(parent, style="Panel.TFrame")
        summary.pack(fill="x")
        self.summary_vars: dict[str, tk.StringVar] = {}
        for column, label in enumerate(("状态", "设备族", "型号状态", "待闭合")):
            cell = tk.Frame(summary, bg="#F5F8FA", highlightbackground=COLORS["line"], highlightthickness=1, padx=9, pady=8)
            cell.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 4, 0))
            summary.columnconfigure(column, weight=1)
            tk.Label(cell, text=label, bg="#F5F8FA", fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w")
            variable = tk.StringVar(value="—")
            self.summary_vars[label] = variable
            tk.Label(cell, textvariable=variable, bg="#F5F8FA", fg=COLORS["ink"], font=("Microsoft YaHei UI", 10, "bold"), wraplength=105, justify="left").pack(anchor="w", pady=(4, 0))
        ttk.Label(parent, text="多选时保留共同上位设备族 / 型式与候选集；证据唯一闭合后才下钻。", style="Muted.TLabel", wraplength=500).pack(anchor="w", pady=(12, 12))
        device_row = ttk.Frame(parent, style="Panel.TFrame")
        device_row.pack(fill="x", pady=(0, 8))
        ttk.Label(device_row, text="当前设备", style="Field.TLabel").pack(side="left", padx=(0, 8))
        self.result_device_var = tk.StringVar(value="—")
        self.result_device_combo = ttk.Combobox(device_row, textvariable=self.result_device_var, state="readonly")
        self.result_device_combo.pack(side="left", fill="x", expand=True)
        self.result_device_combo.bind("<<ComboboxSelected>>", lambda _event: self._render_selected_presentation())
        self.result_tabs = ttk.Notebook(parent)
        self.result_tabs.pack(fill="both", expand=True, pady=(6, 0))
        pfd_panel = ttk.Frame(self.result_tabs, style="Panel.TFrame", padding=1)
        derivation_panel = ttk.Frame(
            self.result_tabs,
            style="Panel.TFrame",
            padding=1,
        )
        customer_panel = ttk.Frame(self.result_tabs, style="Panel.TFrame", padding=1)
        branch_panel = ttk.Frame(self.result_tabs, style="Panel.TFrame", padding=1)
        llm_result_panel = ttk.Frame(self.result_tabs, style="Panel.TFrame", padding=1)
        parameter_panel = ttk.Frame(self.result_tabs, style="Panel.TFrame", padding=1)
        candidate_panel = ttk.Frame(self.result_tabs, style="Panel.TFrame", padding=1)
        issue_panel = ttk.Frame(self.result_tabs, style="Panel.TFrame", padding=1)
        equation_panel = ttk.Frame(self.result_tabs, style="Panel.TFrame", padding=1)
        organized_panel = ttk.Frame(self.result_tabs, style="Panel.TFrame", padding=1)
        raw_panel = ttk.Frame(self.result_tabs, style="Panel.TFrame", padding=1)
        self.pfd_panel = pfd_panel
        self.derivation_panel = derivation_panel
        self.parameter_panel = parameter_panel
        self.issue_panel = issue_panel
        self.result_tabs.add(pfd_panel, text="PFD 流程图")
        self.result_tabs.add(
            derivation_panel,
            text="推导流程（可修改）",
        )
        self.result_tabs.add(customer_panel, text="客户交付")
        self.result_tabs.add(branch_panel, text="分支选择")
        self.result_tabs.add(llm_result_panel, text="大模型调控")
        self.result_tabs.add(parameter_panel, text="参数卡")
        self.result_tabs.add(candidate_panel, text="候选型号")
        self.result_tabs.add(issue_panel, text="校核与缺口")
        self.result_tabs.add(equation_panel, text="公式链")
        self.result_tabs.add(organized_panel, text="Agent 组织答案")
        self.result_tabs.add(raw_panel, text="机器 JSON")

        pfd_toolbar = ttk.Frame(pfd_panel, style="Panel.TFrame", padding=(8, 7))
        pfd_toolbar.pack(fill="x")
        ttk.Label(pfd_toolbar, text="显示", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        self.pfd_detail_var = tk.StringVar(value="标准")
        pfd_detail = ttk.Combobox(
            pfd_toolbar,
            textvariable=self.pfd_detail_var,
            values=("紧凑", "标准", "详细"),
            state="readonly",
            width=7,
        )
        pfd_detail.pack(side="left")
        pfd_detail.bind("<<ComboboxSelected>>", lambda _event: self._change_pfd_detail())
        ttk.Button(pfd_toolbar, text="适应窗口", style="Secondary.TButton", command=self._fit_pfd).pack(side="left", padx=(8, 4))
        ttk.Button(pfd_toolbar, text="100%", style="Secondary.TButton", command=lambda: self.pfd_view.set_zoom(1.0)).pack(side="left")
        self.pfd_status_var = tk.StringVar(value="等待 Aspen 导入")
        ttk.Label(pfd_toolbar, textvariable=self.pfd_status_var, style="Muted.TLabel").pack(side="right")
        self.pfd_view = pfd_canvas.PFDCanvasView(
            pfd_panel,
            on_block_open=self._open_pfd_block,
            on_block_menu=self._show_pfd_block_menu,
            on_stream_open=self._open_pfd_stream,
            on_stream_menu=self._show_pfd_stream_menu,
        )
        self.pfd_view.pack(fill="both", expand=True)

        derivation_toolbar = ttk.Frame(
            derivation_panel,
            style="Panel.TFrame",
            padding=(8, 7),
        )
        derivation_toolbar.pack(fill="x")
        self.derivation_status_var = tk.StringVar(
            value=(
                "点流程框查看参数；修改值只生成用户场景，"
                "不会绕过正式证据门。"
            )
        )
        ttk.Label(
            derivation_toolbar,
            textvariable=self.derivation_status_var,
            style="Muted.TLabel",
        ).pack(side="left", fill="x", expand=True)
        self.derivation_recalculate_button = ttk.Button(
            derivation_toolbar,
            text="仅重算当前设备",
            style="Primary.TButton",
            command=self._recalculate_derivation_equipment,
        )
        self.derivation_recalculate_button.pack(
            side="right",
            padx=(8, 0),
        )
        self.derivation_restore_button = ttk.Button(
            derivation_toolbar,
            text="恢复程序默认",
            style="Secondary.TButton",
            command=self._restore_derivation_defaults,
        )
        self.derivation_restore_button.pack(side="right")

        flow_frame = ttk.Frame(
            derivation_panel,
            style="Panel.TFrame",
        )
        flow_frame.pack(fill="x", padx=8, pady=(0, 8))
        self.derivation_canvas = tk.Canvas(
            flow_frame,
            height=132,
            bg="#F8FAFB",
            highlightbackground=COLORS["line"],
            highlightthickness=1,
        )
        derivation_scroll = ttk.Scrollbar(
            flow_frame,
            orient="horizontal",
            command=self.derivation_canvas.xview,
        )
        self.derivation_canvas.configure(
            xscrollcommand=derivation_scroll.set
        )
        self.derivation_canvas.pack(fill="x", expand=True)
        derivation_scroll.pack(fill="x")

        derivation_body = ttk.Panedwindow(
            derivation_panel,
            orient="horizontal",
        )
        derivation_body.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=(0, 8),
        )
        derivation_fields_panel = ttk.Frame(
            derivation_body,
            style="Panel.TFrame",
        )
        derivation_detail_panel = ttk.Frame(
            derivation_body,
            style="Panel.TFrame",
            padding=(12, 10),
        )
        derivation_body.add(derivation_fields_panel, weight=3)
        derivation_body.add(derivation_detail_panel, weight=2)
        derivation_columns = (
            "group",
            "parameter",
            "default",
            "current",
            "state",
        )
        self.derivation_field_tree = ttk.Treeview(
            derivation_fields_panel,
            columns=derivation_columns,
            show="headings",
            height=13,
        )
        derivation_headings = {
            "group": "环节/分组",
            "parameter": "参数或选择项",
            "default": "程序默认",
            "current": "当前场景",
            "state": "状态",
        }
        derivation_widths = {
            "group": 105,
            "parameter": 160,
            "default": 145,
            "current": 145,
            "state": 95,
        }
        for column in derivation_columns:
            self.derivation_field_tree.heading(
                column,
                text=derivation_headings[column],
            )
            self.derivation_field_tree.column(
                column,
                width=derivation_widths[column],
                minwidth=70,
                stretch=column
                in {"parameter", "default", "current"},
            )
        derivation_field_y = ttk.Scrollbar(
            derivation_fields_panel,
            orient="vertical",
            command=self.derivation_field_tree.yview,
        )
        derivation_field_x = ttk.Scrollbar(
            derivation_fields_panel,
            orient="horizontal",
            command=self.derivation_field_tree.xview,
        )
        self.derivation_field_tree.configure(
            yscrollcommand=derivation_field_y.set,
            xscrollcommand=derivation_field_x.set,
        )
        derivation_field_y.pack(side="right", fill="y")
        derivation_field_x.pack(side="bottom", fill="x")
        self.derivation_field_tree.pack(
            side="left",
            fill="both",
            expand=True,
        )
        self.derivation_field_tree.bind(
            "<<TreeviewSelect>>",
            lambda _event: self._select_derivation_field(),
        )

        ttk.Label(
            derivation_detail_panel,
            text="节点 / 参数详情",
            style="Section.TLabel",
        ).pack(anchor="w")
        self.derivation_detail_title_var = tk.StringVar(
            value="请选择流程框或参数"
        )
        ttk.Label(
            derivation_detail_panel,
            textvariable=self.derivation_detail_title_var,
            style="Field.TLabel",
            wraplength=390,
        ).pack(anchor="w", pady=(8, 5))
        self.derivation_default_var = tk.StringVar(value="程序默认：—")
        ttk.Label(
            derivation_detail_panel,
            textvariable=self.derivation_default_var,
            style="Muted.TLabel",
            wraplength=390,
        ).pack(anchor="w", pady=(0, 5))
        self.derivation_edit_var = tk.StringVar(value="")
        self.derivation_edit_combo = ttk.Combobox(
            derivation_detail_panel,
            textvariable=self.derivation_edit_var,
            state="normal",
        )
        self.derivation_edit_combo.pack(fill="x", pady=(2, 7))
        self.derivation_option_map: dict[str, Any] = {}
        derivation_edit_actions = ttk.Frame(
            derivation_detail_panel,
            style="Panel.TFrame",
        )
        derivation_edit_actions.pack(fill="x")
        self.derivation_apply_button = ttk.Button(
            derivation_edit_actions,
            text="应用到当前场景",
            style="Secondary.TButton",
            command=self._apply_derivation_field_override,
        )
        self.derivation_apply_button.pack(side="left")
        self.derivation_clear_field_button = ttk.Button(
            derivation_edit_actions,
            text="恢复此项默认",
            style="Secondary.TButton",
            command=self._clear_derivation_field_override,
        )
        self.derivation_clear_field_button.pack(
            side="left",
            padx=(8, 0),
        )
        self.derivation_detail_text = tk.Text(
            derivation_detail_panel,
            height=9,
            wrap="word",
            bg="#F8FAFB",
            fg=COLORS["ink"],
            relief="solid",
            borderwidth=1,
            font=("Microsoft YaHei UI", 9),
            padx=9,
            pady=8,
        )
        self.derivation_detail_text.pack(
            fill="both",
            expand=True,
            pady=(9, 0),
        )
        self.derivation_detail_text.configure(state="disabled")

        customer_columns = ("section", "field", "value", "unit", "state", "gate", "profile")
        self.customer_tree = ttk.Treeview(customer_panel, columns=customer_columns, show="headings", height=18)
        customer_headings = {
            "section": "分区", "field": "字段", "value": "值", "unit": "单位",
            "state": "状态", "gate": "证据门", "profile": "Profile",
        }
        customer_widths = {"section": 95, "field": 155, "value": 240, "unit": 72, "state": 110, "gate": 145, "profile": 85}
        for column in customer_columns:
            self.customer_tree.heading(column, text=customer_headings[column])
            self.customer_tree.column(column, width=customer_widths[column], minwidth=55, stretch=column in {"field", "value", "gate"})
        customer_y = ttk.Scrollbar(customer_panel, orient="vertical", command=self.customer_tree.yview)
        customer_x = ttk.Scrollbar(customer_panel, orient="horizontal", command=self.customer_tree.xview)
        self.customer_tree.configure(yscrollcommand=customer_y.set, xscrollcommand=customer_x.set)
        customer_y.pack(side="right", fill="y")
        customer_x.pack(side="bottom", fill="x")
        self.customer_tree.pack(side="left", fill="both", expand=True)

        branch_notice = ttk.Label(
            branch_panel,
            text=(
                "这里显示程序实际选了什么、读取了哪些条件、走了哪个分支、"
                "哪些分支被排除或因条件不足保持待核；连接口小部件也逐项列出。"
            ),
            style="Muted.TLabel",
            wraplength=820,
        )
        branch_notice.pack(anchor="w", padx=10, pady=(9, 6))
        self.branch_output_text = tk.Text(
            branch_panel,
            height=18,
            wrap="word",
            bg="#F8FAFB",
            fg=COLORS["ink"],
            relief="solid",
            borderwidth=1,
            font=("Microsoft YaHei UI", 9),
            padx=12,
            pady=10,
        )
        branch_y = ttk.Scrollbar(
            branch_panel,
            orient="vertical",
            command=self.branch_output_text.yview,
        )
        self.branch_output_text.configure(yscrollcommand=branch_y.set)
        branch_y.pack(side="right", fill="y")
        self.branch_output_text.pack(
            side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8)
        )
        self.branch_output_text.insert(
            "1.0", "运行后显示自然语言分支选择和部件选择账本。"
        )
        self.branch_output_text.configure(state="disabled")

        llm_notice = ttk.Label(
            llm_result_panel,
            text=(
                "这里单独披露大模型的条件判断、补值/分支建议、程序校验结果、"
                "实际带入重算的内容和失败回退；没有调用时会明确写“未调用”。"
            ),
            style="Muted.TLabel",
            wraplength=820,
        )
        llm_notice.pack(anchor="w", padx=10, pady=(9, 6))
        self.llm_result_text = tk.Text(
            llm_result_panel,
            height=18,
            wrap="word",
            bg="#EEF8FB",
            fg=COLORS["ink"],
            relief="solid",
            borderwidth=1,
            font=("Microsoft YaHei UI", 9),
            padx=12,
            pady=10,
        )
        llm_y = ttk.Scrollbar(
            llm_result_panel,
            orient="vertical",
            command=self.llm_result_text.yview,
        )
        self.llm_result_text.configure(yscrollcommand=llm_y.set)
        llm_y.pack(side="right", fill="y")
        self.llm_result_text.pack(
            side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8)
        )
        self.llm_result_text.insert(
            "1.0", "本次大模型调控结果会显示在这里。"
        )
        self.llm_result_text.configure(state="disabled")

        parameter_columns = ("group", "parameter", "symbol", "value", "unit", "source", "state")
        parameter_toolbar = ttk.Frame(parameter_panel, style="Panel.TFrame", padding=(8, 7))
        parameter_toolbar.pack(fill="x")
        ttk.Label(
            parameter_toolbar,
            text="PFD 设备补录只写独立参数层，不改 Aspen 源文件；重算后仍按证据门停在相应状态。",
            style="Muted.TLabel",
        ).pack(side="left", fill="x", expand=True)
        self.pfd_parameter_button = ttk.Button(
            parameter_toolbar,
            text="补充/修改本设备参数并重算",
            style="Secondary.TButton",
            command=lambda: self._open_pfd_parameter_editor(self._active_pfd_block_id or ""),
        )
        self.pfd_parameter_button.pack(side="right", padx=(8, 0))
        self.pfd_parameter_button.state(["disabled"])
        self.parameter_tree = ttk.Treeview(parameter_panel, columns=parameter_columns, show="headings", height=18)
        parameter_headings = {
            "group": "分组", "parameter": "参数", "symbol": "符号", "value": "值",
            "unit": "单位", "source": "来源", "state": "状态",
        }
        parameter_widths = {"group": 115, "parameter": 150, "symbol": 70, "value": 105, "unit": 78, "source": 125, "state": 90}
        for column in parameter_columns:
            self.parameter_tree.heading(column, text=parameter_headings[column])
            self.parameter_tree.column(column, width=parameter_widths[column], minwidth=55, stretch=column in {"parameter", "source"})
        parameter_y = ttk.Scrollbar(parameter_panel, orient="vertical", command=self.parameter_tree.yview)
        parameter_x = ttk.Scrollbar(parameter_panel, orient="horizontal", command=self.parameter_tree.xview)
        self.parameter_tree.configure(yscrollcommand=parameter_y.set, xscrollcommand=parameter_x.set)
        parameter_y.pack(side="right", fill="y")
        parameter_x.pack(side="bottom", fill="x")
        self.parameter_tree.pack(side="left", fill="both", expand=True)

        candidate_columns = ("rank", "kind", "designation", "status", "score", "predicates", "missing")
        self.candidate_tree = ttk.Treeview(candidate_panel, columns=candidate_columns, show="headings", height=18)
        candidate_headings = {
            "rank": "排名", "kind": "类别", "designation": "候选型号 / 工程规格",
            "status": "状态", "score": "评分", "predicates": "PASS/FAIL/UNKNOWN", "missing": "待闭合",
        }
        candidate_widths = {"rank": 48, "kind": 105, "designation": 290, "status": 145, "score": 70, "predicates": 130, "missing": 190}
        for column in candidate_columns:
            self.candidate_tree.heading(column, text=candidate_headings[column])
            self.candidate_tree.column(column, width=candidate_widths[column], minwidth=45, stretch=column in {"designation", "missing"})
        candidate_y = ttk.Scrollbar(candidate_panel, orient="vertical", command=self.candidate_tree.yview)
        candidate_x = ttk.Scrollbar(candidate_panel, orient="horizontal", command=self.candidate_tree.xview)
        self.candidate_tree.configure(yscrollcommand=candidate_y.set, xscrollcommand=candidate_x.set)
        candidate_y.pack(side="right", fill="y")
        candidate_x.pack(side="bottom", fill="x")
        self.candidate_tree.pack(side="left", fill="both", expand=True)

        self.issue_text = tk.Text(issue_panel, height=18, wrap="word", bg="#F8FAFB", fg=COLORS["ink"], relief="solid", borderwidth=1, font=("Microsoft YaHei UI", 9), padx=12, pady=10)
        issue_scroll = ttk.Scrollbar(issue_panel, orient="vertical", command=self.issue_text.yview)
        self.issue_text.configure(yscrollcommand=issue_scroll.set)
        issue_scroll.pack(side="right", fill="y")
        self.issue_text.pack(side="left", fill="both", expand=True)
        self.issue_text.insert("1.0", "运行后显示约束校核、最小缺失集、冲突和同设备证据门。")
        self.issue_text.configure(state="disabled")
        formula_notice_bar = tk.Frame(equation_panel, bg="#FFF3CD", padx=10, pady=8)
        formula_notice_bar.pack(fill="x", pady=(0, 6))
        self.formula_notice_var = tk.StringVar(value="内置公式结果会在这里明确提示来源、适用边界和不能证明的结论。")
        tk.Label(
            formula_notice_bar,
            textvariable=self.formula_notice_var,
            bg="#FFF3CD",
            fg="#7A4B00",
            anchor="w",
            justify="left",
            wraplength=650,
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="left", fill="x", expand=True)
        self._formula_help_text = ""
        formula_help = tk.Label(
            formula_notice_bar,
            text="ⓘ",
            bg="#FFF3CD",
            fg="#7A4B00",
            cursor="question_arrow",
            font=("Microsoft YaHei UI", 11, "bold"),
            padx=5,
        )
        formula_help.pack(side="right")
        HoverHelp(formula_help, lambda: self._formula_help_text)
        self.equation_text = tk.Text(equation_panel, height=18, wrap="word", bg="#F8FAFB", fg=COLORS["ink"], relief="solid", borderwidth=1, font=("Microsoft YaHei UI", 10), padx=14, pady=12, spacing1=0, spacing2=0, spacing3=2)
        self.equation_text.pack(fill="both", expand=True)
        self.equation_text.insert("1.0", "选择任一导入方式后，这里显示：目标量 = 公式 = 代入式 = 答案")
        self.equation_text.configure(state="disabled")
        organized_notice = ttk.Label(
            organized_panel,
            text=(
                "固定顺序：基本信息 → 分支选择与大模型调控 → 详细计算链条 → "
                "候选/修改方案 → 强制警告 → 待补证据 → 下一步。"
            ),
            style="Muted.TLabel",
            wraplength=780,
        )
        organized_notice.pack(anchor="w", padx=10, pady=(9, 6))
        self.organized_answer_text = tk.Text(
            organized_panel,
            height=18,
            wrap="word",
            bg="#F8FAFB",
            fg=COLORS["ink"],
            relief="solid",
            borderwidth=1,
            font=("Microsoft YaHei UI", 9),
            padx=12,
            pady=10,
        )
        organized_y = ttk.Scrollbar(
            organized_panel,
            orient="vertical",
            command=self.organized_answer_text.yview,
        )
        self.organized_answer_text.configure(
            yscrollcommand=organized_y.set
        )
        organized_y.pack(side="right", fill="y")
        self.organized_answer_text.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(8, 0),
            pady=(0, 8),
        )
        self.organized_answer_text.configure(state="disabled")
        self.raw_text = tk.Text(raw_panel, height=18, wrap="none", bg="#F8FAFB", fg="#263642", relief="solid", borderwidth=1, font=("Consolas", 8), padx=8, pady=8)
        raw_y = ttk.Scrollbar(raw_panel, orient="vertical", command=self.raw_text.yview)
        raw_x = ttk.Scrollbar(raw_panel, orient="horizontal", command=self.raw_text.xview)
        self.raw_text.configure(yscrollcommand=raw_y.set, xscrollcommand=raw_x.set)
        raw_y.pack(side="right", fill="y")
        raw_x.pack(side="bottom", fill="x")
        self.raw_text.pack(side="left", fill="both", expand=True)
        self.raw_text.configure(state="disabled")
        actions = ttk.Frame(parent, style="Panel.TFrame")
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="保存 JSON", style="Secondary.TButton", command=self._save_result).pack(side="left")
        ttk.Button(
            actions,
            text="导出报告",
            style="Primary.TButton",
            command=self._export_report,
        ).pack(side="left", padx=(8, 0))
        self.open_button = ttk.Button(actions, text="打开证据目录", style="Secondary.TButton", command=self._open_session)
        self.open_button.pack(side="left", padx=8)
        self.open_button.state(["disabled"])
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(actions, textvariable=self.status_var, style="Muted.TLabel").pack(side="right")

    @staticmethod
    def _labeled_widget(parent: ttk.Frame, row: int, label: str, widget: tk.Widget) -> None:
        ttk.Label(parent, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 14), pady=7)
        widget.grid(row=row, column=1, sticky="ew", pady=7)

    def _set_default_selection(self) -> None:
        pump = next((row for row in self.catalog["selections"] if row.get("block_type") == "PUMP"), self.catalog["selections"][0])
        self.manual_selection.set(pump["display_name"])
        self._render_manual_fields()

    def _selection(self) -> dict[str, Any]:
        selected = self.selection_by_display.get(self.manual_selection.get())
        if not selected:
            raise ValueError("请选择模块类型或设备族。")
        return selected

    @staticmethod
    def _manual_help_text(field: Mapping[str, Any]) -> str:
        role = str(field.get("manual_role", "optional_input"))
        role_explanation = {
            "required_input": "定位：当前主计算路径需要这个输入。",
            "recommended_input": "定位：建议从 Aspen 或可靠工艺数据提供；缺少时只影响依赖它的步骤。",
            "optional_input": "定位：可选补充量，不填不会被当作漏填。",
            "optional_preference": "定位：用户偏好或约束；留空时系统保留最泛用候选或列明待确认项。",
            "known_result": "定位：已有计算/专业软件/厂家结果；通常不需要手工填写。",
            "advanced_evidence": "定位：正式证据字段；基础计算阶段可以留空。",
            "advanced_design_input": "定位：可选的方法分支或高级设计条件；没有依据时不要猜填。",
        }.get(role, "定位：可选输入。")
        lines = [str(field.get("label") or field.get("name")), role_explanation]
        group_title = str(field.get("manual_group_title") or field.get("group_title") or "")
        if group_title == "工艺任务":
            lines.append("推荐来源：用户给定的设计任务或项目说明。")
        elif group_title == "入口流股 / Aspen 物性":
            lines.append("推荐来源：同工况 Aspen 导出或可靠工艺数据。")
        elif group_title == "目标条件":
            lines.append("推荐来源：用户明确给定的目标工况或设计目标。")
        elif role == "optional_preference":
            lines.append("推荐来源：项目要求、材料标准或用户偏好；无依据时留空。")
        elif role == "known_result":
            lines.append("推荐来源：同一设备、同一工况的专业软件或厂家结果。")
        elif role == "advanced_evidence":
            lines.append("推荐来源：同一设备的可校验文件、哈希和独立审核记录。")
        unit = field.get("unit")
        if unit:
            lines.append(f"填写单位：{unit}。")
        blank = str(field.get("manual_blank_behavior") or "").strip()
        if blank:
            lines.append(f"留空后：{blank}")
        consumers = [str(value) for value in field.get("calculation_consumers", [])]
        if consumers:
            names = [EQUATION_META.get(calc_id, (calc_id, ""))[0] for calc_id in consumers]
            lines.append("影响的内置公式：" + "、".join(names) + "。公式输出会显示黄色提示。")
        if field.get("candidate_required"):
            lines.append("候选影响：这是候选闭合字段；可以留空。算法无法推导时，结果停在待闭合并保留最小补充清单。")
        elif field.get("sizing_required"):
            lines.append("计算影响：这是本设备族的尺寸/能力字段之一，但不会因此要求你编造数值。")
        if field.get("built_in_formula_output"):
            lines.append("通常是输出：只有已有同工况权威结果时才在“高级项”里填写并用于交叉核对。")
        return "\n".join(lines)

    def _toggle_manual_advanced(self) -> None:
        self.manual_advanced.set(not self.manual_advanced.get())
        self._render_manual_fields()

    def _update_manual_requirement_summary(self) -> None:
        selection = self._selection()
        selection_id = str(selection["selection_id"])
        values = dict(self.manual_value_cache.get(selection_id, {}))
        values.update({name: variable.get().strip() for name, variable in self.field_vars.items()})
        status = self.core.manual_requirement_status(selection, values)

        primary = status["primary_calculation"]
        primary_missing = [row["label"] for row in primary["missing_fields"]]
        primary_total = len(primary["required_fields"])
        if primary_missing:
            primary_text = "主计算必填还缺：" + "、".join(primary_missing)
        elif primary_total:
            primary_text = f"主计算必填：已提供 {primary_total} 项"
        else:
            primary_text = "主计算必填：本族无预设统一主计算必填；先给可靠工况，算法按可闭合链条计算"

        candidate = status["candidate_closure"]
        candidate_gaps = [row["label"] for row in candidate["input_side_gaps"]]
        if candidate_gaps:
            candidate_text = (
                "候选闭合必需（可留空）当前表单未给："
                + "、".join(candidate_gaps)
                + "；算法可推导则自动补入，否则停在待闭合"
            )
        else:
            candidate_text = "候选闭合必需：表单字段已提供，仍以确定性计算和证据门复核"

        evidence = status["formal_evidence"]
        gate = str(evidence.get("gate") or "同设备正式计算、软件或厂家证据及独立审核")
        evidence_text = "正式证据必需（不影响基础计算）：" + gate
        self.manual_requirement_summary_var.set("\n".join((primary_text, candidate_text, evidence_text)))
        self.manual_expand_button.configure(
            text="收起高级项" if self.manual_advanced.get() else "展开候选/证据项"
        )

    def _render_manual_fields(self) -> None:
        self._stash_manual_values()
        for child in self.field_frame.winfo_children():
            child.destroy()
        self.field_vars = {}
        selection = self._selection()
        selection_id = str(selection["selection_id"])
        self._rendered_selection_id = selection_id
        cached = self.manual_value_cache.setdefault(selection_id, {})
        self.field_frame.columnconfigure(1, weight=1)
        self.field_frame.columnconfigure(3, weight=0)
        grid_row = 0
        current_group = None
        group_order = {
            "工艺任务": 0,
            "入口流股 / Aspen 物性": 1,
            "目标条件": 2,
            "可选限制与偏好": 3,
            "候选/校核可选输入": 4,
            "其他可选输入": 5,
            "高级设计条件（可选）": 6,
            "已有计算或规格（高级，可选）": 7,
            "正式证据（正式定型必需，基础计算可选）": 8,
        }
        indexed_fields = list(enumerate(selection.get("fields", [])))
        visible_fields = [
            (index, field)
            for index, field in indexed_fields
            if field.get("manual_default_visible") or self.manual_advanced.get() and field.get("manual_role") in {"known_result", "advanced_evidence", "advanced_design_input"}
        ]
        visible_fields.sort(key=lambda item: (group_order.get(str(item[1].get("manual_group_title", "其他可选输入")), 99), item[0]))
        group_notes = {
            "工艺任务": "用于识别设备和服务，可以留空的项目不会阻断计算。",
            "入口流股 / Aspen 物性": "优先填 Aspen 或可靠工艺数据；软件不会用设备公式猜物性。",
            "目标条件": "说明入口流股要被处理到什么状态。",
            "可选限制与偏好": "不指定时由规则保留泛用候选，或在结果中说明仍需确认。",
            "候选/校核可选输入": "可留空；留空时对应候选或校核约束保持 UNKNOWN，不会静默判为通过。",
            "其他可选输入": "有可靠数据再填，没有可以留空。",
            "高级设计条件（可选）": "仅在方法分支已有依据时填写；留空使依赖公式等待，不算输入错误。",
            "已有计算或规格（高级，可选）": "只在已有 Aspen、专业软件、计算书或厂家结果时填写。",
            "正式证据（正式定型必需，基础计算可选）": "基础计算可留空；正式定型必须满足同设备证据门，不能用跨设备或旧项目结果代替。",
        }
        for _index, field in visible_fields:
            group_title = field.get("manual_group_title", "其他可选输入")
            if group_title != current_group:
                ttk.Label(self.field_frame, text=group_title, style="Section.TLabel").grid(
                    row=grid_row, column=0, columnspan=3, sticky="w", pady=(14 if grid_row else 0, 3)
                )
                current_group = group_title
                grid_row += 1
                ttk.Label(self.field_frame, text=group_notes.get(str(group_title), ""), style="Muted.TLabel", wraplength=650).grid(
                    row=grid_row, column=0, columnspan=3, sticky="w", pady=(0, 6)
                )
                grid_row += 1
            unit = f"  [{field['unit']}]" if field.get("unit") else ""
            ttk.Label(self.field_frame, text=f"{field['label']}{unit}", style="Field.TLabel").grid(row=grid_row, column=0, sticky="w", padx=(0, 14), pady=6)
            variable = tk.StringVar(value=cached.get(field["name"], str(field.get("default", ""))))
            self.field_vars[field["name"]] = variable
            variable.trace_add("write", lambda *_args: self._update_manual_requirement_summary())
            if field.get("type") == "select":
                widget = TranslatedCombobox(
                    self.field_frame,
                    textvariable=variable,
                    values=field.get("options", []),
                    state="readonly",
                )
            else:
                widget = ttk.Entry(self.field_frame, textvariable=variable)
            widget.grid(row=grid_row, column=1, sticky="ew", pady=6)
            role = str(field.get("manual_role", "optional_input"))
            if field.get("primary_calculation_required"):
                badge_text, badge_style = "主计算必填", "RequiredBadge.TLabel"
            elif field.get("candidate_closure_required"):
                if role == "optional_preference":
                    badge_text, badge_style = "可选偏好·闭合时确认", "OptionalBadge.TLabel"
                else:
                    badge_text, badge_style = "候选闭合·可留空", "RecommendedBadge.TLabel"
            elif field.get("formal_evidence_input"):
                badge_text, badge_style = "正式证据", "AdvancedBadge.TLabel"
            else:
                badge_text, badge_style = {
                    "required_input": ("主计算必填", "RequiredBadge.TLabel"),
                    "recommended_input": ("建议", "RecommendedBadge.TLabel"),
                    "optional_input": ("可选", "OptionalBadge.TLabel"),
                    "optional_preference": ("可选偏好", "OptionalBadge.TLabel"),
                    "known_result": ("已有结果", "AdvancedBadge.TLabel"),
                    "advanced_evidence": ("正式证据", "AdvancedBadge.TLabel"),
                    "advanced_design_input": ("方法分支·可留空", "AdvancedBadge.TLabel"),
                }.get(role, ("可选", "OptionalBadge.TLabel"))
            ttk.Label(self.field_frame, text=badge_text, style=badge_style).grid(row=grid_row, column=2, sticky="e", padx=(10, 0), pady=6)
            help_control = tk.Label(
                self.field_frame,
                text="ⓘ",
                bg=COLORS["panel"],
                fg=COLORS["accent_dark"],
                cursor="question_arrow",
                font=("Microsoft YaHei UI", 10, "bold"),
                padx=5,
            )
            help_control.grid(row=grid_row, column=3, sticky="e", padx=(4, 0), pady=6)
            HoverHelp(help_control, self._manual_help_text(field))
            grid_row += 1
        self._update_manual_requirement_summary()
        self.field_canvas.yview_moveto(0)

    def _collect_manual(self) -> dict[str, Any]:
        self._stash_manual_values()
        selection_id = self._rendered_selection_id or str(self._selection()["selection_id"])
        return {name: value for name, value in self.manual_value_cache.get(selection_id, {}).items() if value != ""}

    def _stash_manual_values(self) -> None:
        if not self._rendered_selection_id:
            return
        cached = self.manual_value_cache.setdefault(self._rendered_selection_id, {})
        for name, variable in self.field_vars.items():
            cached[name] = variable.get().strip()

    def _fill_manual(self, values: dict[str, Any]) -> None:
        for name, value in values.items():
            if name in self.field_vars:
                self.field_vars[name].set(str(value))
        self._stash_manual_values()

    def _choose_aspen(self) -> None:
        path = filedialog.askopenfilename(title="选择 Aspen 文件", filetypes=[("Aspen files", "*.bkp *.apw *.inp"), ("All files", "*.*")])
        if path:
            self._set_aspen_file(path, source="选择")

    def _split_drop_paths(self, raw_data: Any) -> list[str]:
        try:
            return [str(value) for value in self.root.tk.splitlist(str(raw_data)) if str(value) != ""]
        except tk.TclError:
            return []

    def _set_aspen_file(self, raw_path: str, *, source: str) -> bool:
        if hasattr(self, "aspen_button") and self.aspen_button.instate(["disabled"]):
            messagebox.showwarning(
                "Aspen 正在处理",
                "当前后台仍在处理已选案例。完成后再更换文件，避免页面路径和实际运行对象不一致。",
                parent=self.root,
            )
            return False
        candidate = Path(raw_path).expanduser()
        if not candidate.is_file():
            messagebox.showwarning("文件找不到", "这个路径不是可读取的文件，请重新拖入或选择。", parent=self.root)
            return False
        if candidate.suffix.casefold() not in {".bkp", ".apw", ".inp"}:
            messagebox.showwarning("文件类型不对", "这里只接收 Aspen 的 .bkp、.apw 或 .inp 文件。", parent=self.root)
            return False
        resolved = candidate.resolve()
        self.aspen_path.set(str(resolved))
        self.aspen_drop_text.set(f"已{source}：{resolved.name}\n{resolved}")
        self.aspen_drop_label.configure(bg="#E1F0F2", fg=COLORS["accent_dark"])
        if hasattr(self, "status_var"):
            self.status_var.set(f"已{source} Aspen 文件；点击“自动遍历并匹配”后才会运行。")
        return True

    def _on_aspen_drop_enter(self, _event: Any) -> str:
        self.aspen_drop_label.configure(bg="#D6EAEE")
        return str(COPY or "copy")

    def _on_aspen_drop_leave(self, _event: Any) -> str:
        if self.aspen_path.get().strip():
            self.aspen_drop_label.configure(bg="#E1F0F2")
        else:
            self.aspen_drop_label.configure(bg=COLORS["soft"])
        return str(COPY or "copy")

    def _on_aspen_drop(self, event: Any) -> str:
        paths = self._split_drop_paths(getattr(event, "data", ""))
        if len(paths) != 1:
            self._on_aspen_drop_leave(event)
            messagebox.showwarning("一次拖一个文件", "请一次只拖入一个 Aspen 文件，避免选错案例。", parent=self.root)
            return str(REFUSE_DROP or "refuse_drop")
        accepted = self._set_aspen_file(paths[0], source="拖入")
        if not accepted:
            self._on_aspen_drop_leave(event)
            return str(REFUSE_DROP or "refuse_drop")
        return str(COPY or "copy")

    def _background(self, button: ttk.Button, busy: str, task: Callable[[], Any], done: Callable[[Any], None]) -> None:
        if self._closing:
            return
        button.state(["disabled"])
        self._background_jobs += 1
        self.status_var.set(busy)

        def worker() -> None:
            value: Any = None
            error: Exception | None = None
            try:
                value = task()
            except Exception as exc:
                error = exc

            def finish() -> None:
                self._background_jobs = max(0, self._background_jobs - 1)
                if self._closing:
                    return
                try:
                    if error is not None:
                        messagebox.showerror("操作失败", str(error), parent=self.root)
                    else:
                        done(value)
                finally:
                    button.state(["!disabled"])
                    self.status_var.set("就绪")

            try:
                if not self._closing and self.root.winfo_exists():
                    self.root.after(0, finish)
            except tk.TclError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _on_close(self) -> None:
        if self._closing:
            return
        active_workers = 0
        try:
            active_workers = int(self.api.active_worker_count())
        except Exception:
            active_workers = 0
        if self._background_jobs > 0 or active_workers > 0:
            confirmed = messagebox.askyesno(
                "仍有任务在运行",
                "现在关闭会终止本软件本次启动的 Aspen 隔离工作进程；不会关闭你原来已经打开的 Aspen。\n\n是否继续关闭？",
                parent=self.root,
            )
            if not confirmed:
                return
        self._closing = True
        try:
            self.api.cancel_active_operations()
        except Exception:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _run_aspen(self) -> None:
        if not self.aspen_path.get().strip():
            messagebox.showwarning("缺少文件", "请先选择 Aspen 文件。", parent=self.root)
            return
        if self.aspen_basis.get() not in {"absolute", "gauge"}:
            messagebox.showwarning(
                "缺少压力基准",
                "请选择 absolute（绝压）或 gauge（表压）。程序不会替你默认。",
                parent=self.root,
            )
            return
        config = {
            "source_path": self.aspen_path.get(),
            "pressure_basis": self.aspen_basis.get(),
            "atmospheric_pressure_mpa": self.aspen_atmospheric.get(),
            "timeout_s": self.aspen_timeout.get(),
            "run": self.aspen_run.get(),
        }
        self.aspen_progress.configure(text="独立子进程正在处理；COM 异常不会冻结主界面。")

        def done(response: dict[str, Any]) -> None:
            self.aspen_progress.configure(text="")
            if not response.get("ok"):
                messagebox.showerror("Aspen 导入失败", response.get("error", "可改用手动输入或 LLM 辅助。"), parent=self.root)
                return
            self.session_dir = response.get("session_dir")
            if self.session_dir:
                self.open_button.state(["!disabled"])
            value = response.get("value", {})
            deterministic = value.get("result", value) if isinstance(value, dict) else value
            self.last_deterministic_result = deterministic if isinstance(deterministic, dict) else {"value": deterministic}
            self.last_source_input = {
                "operation": "aspen_import",
                "payload": json.loads(json.dumps(config, ensure_ascii=False)),
            }
            self._load_aspen_visual_artifacts(value if isinstance(value, dict) else {})
            self._render_result(deterministic)
            equipment_count = (
                len(self.aspen_derivation.get("equipment", []))
                if isinstance(self.aspen_derivation.get("equipment"), list)
                else 0
            )
            diagnostic_count = int(self.aspen_derivation.get("normalization_diagnostic_count") or 0)
            if diagnostic_count:
                self.status_var.set(
                    f"已输出 {equipment_count} 台/模块结果；忽略 {diagnostic_count} 个无法使用的 Aspen 字段，仅相关目标量待补。"
                )
            else:
                self.status_var.set(f"Aspen 遍历与确定性计算完成：{equipment_count} 台/模块结果。")
            if self.aspen_pfd_mapping:
                self.result_tabs.select(self.pfd_panel)
                self.root.after(60, self._fit_pfd)

        self._background(self.aspen_button, "Aspen 处理中…", lambda: self.api.import_aspen(config), done)

    @staticmethod
    def _read_json_artifact(path_value: Any) -> dict[str, Any]:
        text = str(path_value or "").strip()
        if not text:
            return {}
        path = Path(text)
        if not path.is_file():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}

    def _pfd_canonical_context(self) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
        blocks = aspen_pfd.canonical_parameters_by_block(self.aspen_derivation)
        streams = aspen_pfd.canonical_parameters_by_stream(self.aspen_derivation)
        issue_source = self.aspen_derivation.get("normalization_diagnostics")
        if not isinstance(issue_source, list):
            issue_source = self.aspen_derivation.get("errors", [])
        issues = [dict(item) for item in issue_source if isinstance(item, Mapping)]
        return blocks, streams, issues

    def _load_aspen_visual_artifacts(self, worker: Mapping[str, Any]) -> None:
        self.aspen_bundle = self._read_json_artifact(worker.get("bundle"))
        self.aspen_derivation = (
            dict(worker.get("result"))
            if isinstance(worker.get("result"), Mapping)
            else self._read_json_artifact(worker.get("derivation"))
        )
        self.aspen_pfd_mapping = self._read_json_artifact(worker.get("pfd_mapping"))
        if not self.aspen_pfd_mapping and self.aspen_bundle:
            canonical_blocks, canonical_streams, normalization_issues = self._pfd_canonical_context()
            self.aspen_pfd_mapping = aspen_pfd.build_pfd_mapping(
                self.aspen_bundle,
                catalog=self.catalog,
                canonical_parameters_by_block=canonical_blocks,
                canonical_parameters_by_stream=canonical_streams,
                parameter_normalization_issues=normalization_issues,
            )
        self.aspen_type_overrides = {
            str(key): str(value)
            for key, value in (self.aspen_pfd_mapping.get("overrides", {}) or {}).items()
        }
        self.pfd_parameter_overrides = {}
        self.pfd_recalculated_results = {}
        self.pfd_invalidated_blocks = set()
        self.pfd_invalidated_streams = set()
        self._active_pfd_block_id = None
        if hasattr(self, "pfd_parameter_button"):
            self.pfd_parameter_button.state(["disabled"])
        equipment = self.aspen_derivation.get("equipment") if isinstance(self.aspen_derivation.get("equipment"), list) else []
        piping = self.aspen_derivation.get("piping") if isinstance(self.aspen_derivation.get("piping"), list) else []
        self._pfd_equipment_by_block = {
            str(item.get("aspen_block_id") or item.get("equipment_tag")): dict(item)
            for item in equipment
            if isinstance(item, Mapping) and (item.get("aspen_block_id") or item.get("equipment_tag"))
        }
        self._pfd_piping_by_stream = {
            str(item.get("stream_id")): dict(item)
            for item in piping
            if isinstance(item, Mapping) and item.get("stream_id")
        }
        self._sync_pfd_view()

    def _change_pfd_detail(self) -> None:
        mode = {"紧凑": "compact", "标准": "standard", "详细": "detailed"}.get(self.pfd_detail_var.get(), "standard")
        self.pfd_view.set_detail_mode(mode)

    def _fit_pfd(self) -> None:
        self.pfd_view.fit_to_window()

    def _sync_pfd_view(self) -> None:
        overlays = pfd_canvas.selection_overlay_from_derivation(self.aspen_derivation)
        for block_id in self.pfd_invalidated_blocks:
            overlays[block_id] = {
                "status": "WAITING_CALCULATED_PARAMETERS",
                "designation": "受改型影响，等待确定性复核",
                "match_status": "TYPE_ROUTE_CHANGE_IMPACT_RECALC_REQUIRED",
            }
        for stream_id in self.pfd_invalidated_streams:
            overlays[f"stream:{stream_id}"] = {
                "status": "WAITING_CALCULATED_PARAMETERS",
                "designation": "关联设备改型后待复核",
                "match_status": "RELATED_STREAM_RECALC_REQUIRED",
            }
        for block_id, payload in self.pfd_recalculated_results.items():
            match = _result_object(payload)
            if not match:
                continue
            derived = pfd_canvas.selection_overlay_from_derivation({
                "equipment": [{"aspen_block_id": block_id, "match_result": match}]
            })
            overlays.update(derived)
        self.pfd_overlays = overlays
        mode = {"紧凑": "compact", "标准": "standard", "详细": "detailed"}.get(self.pfd_detail_var.get(), "standard")
        self.pfd_view.set_mapping(self.aspen_pfd_mapping, overlays, detail_mode=mode)
        if not self.aspen_pfd_mapping:
            self.pfd_status_var.set("等待 Aspen 导入")
            return
        summary = aspen_pfd.summarize_pfd_mapping(self.aspen_pfd_mapping)
        topology = summary.get("topology_gate", {})
        self.pfd_status_var.set(
            f"{summary.get('equipment_node_count', 0)} 设备 · {summary.get('edge_count', 0)} 管线 · "
            f"拓扑 {topology.get('status', '—')} · {summary.get('override_count', 0)} 改型 · "
            f"{len(self.pfd_invalidated_blocks)} 设备/{len(self.pfd_invalidated_streams)} 管线待复核"
        )

    def _pfd_block_row(self, block_id: str) -> dict[str, Any]:
        for row in self.aspen_pfd_mapping.get("blocks", []) if isinstance(self.aspen_pfd_mapping.get("blocks"), list) else []:
            if isinstance(row, Mapping) and str(row.get("block_id")) == block_id:
                return dict(row)
        return {}

    def _pfd_edge_row(self, stream_id: str) -> dict[str, Any]:
        pfd = self.aspen_pfd_mapping.get("pfd") if isinstance(self.aspen_pfd_mapping.get("pfd"), Mapping) else {}
        for row in pfd.get("edges", []) if isinstance(pfd.get("edges"), list) else []:
            if isinstance(row, Mapping) and str(row.get("stream_id")) == stream_id:
                return dict(row)
        return {}

    @staticmethod
    def _clear_tree(tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def _open_pfd_block(self, block_id: str) -> None:
        self._active_pfd_block_id = block_id
        if hasattr(self, "pfd_parameter_button"):
            self.pfd_parameter_button.state(["!disabled"])
        if block_id in self.pfd_invalidated_blocks and block_id not in self.pfd_recalculated_results:
            row = self._pfd_block_row(block_id)
            self._render_pfd_raw_parameters(
                title=f"{block_id} · 改型影响待复核",
                family=(row.get("effective_mapping") or {}).get("family_name") if isinstance(row.get("effective_mapping"), Mapping) else None,
                status="TYPE_ROUTE_CHANGE_IMPACT_RECALC_REQUIRED",
                model_status="WAITING_CALCULATED_PARAMETERS",
                parameters=row.get("parameters", []),
                evidence={
                    "recalculation_status": row.get("recalculation_status"),
                    "change_impact": self.aspen_pfd_mapping.get("change_impact"),
                    "stale_overlay_hidden": True,
                },
            )
            self.result_tabs.select(self.parameter_panel)
            return
        card: dict[str, Any] | None = None
        recalculated = self.pfd_recalculated_results.get(block_id)
        if recalculated:
            cards = result_presentation.build_presentation(recalculated).get("equipment", [])
            card = dict(cards[0]) if cards else None
        if card is None:
            label = self._presentation_label_by_id.get(block_id) if hasattr(self, "_presentation_label_by_id") else None
            if label:
                self.result_device_var.set(label)
                card = self._presentation_by_label.get(label)
        if card is not None:
            self._render_presentation_card(card)
            header = card.get("header", {})
            self.result_device_var.set(
                f"{block_id} · {header.get('recommended_type') or header.get('family_name') or '设备'}"
            )
        else:
            row = self._pfd_block_row(block_id)
            equipment = self._pfd_equipment_by_block.get(block_id, {})
            match_result = equipment.get("match_result") if isinstance(equipment.get("match_result"), Mapping) else {}
            model_decision = match_result.get("model_decision") if isinstance(match_result.get("model_decision"), Mapping) else {}
            model_status = model_decision.get("model_status") or match_result.get("status")
            self._render_pfd_raw_parameters(
                title=f"{block_id} · Aspen 模块",
                family=(row.get("effective_mapping") or {}).get("family_name") if isinstance(row.get("effective_mapping"), Mapping) else None,
                status=(row.get("effective_mapping") or {}).get("status") if isinstance(row.get("effective_mapping"), Mapping) else None,
                model_status=model_status,
                parameters=row.get("parameters", []),
                evidence=row,
            )
        self._append_pfd_equipment_lineage(block_id)
        self._append_pfd_parameter_override_state(block_id)
        self.result_tabs.select(self.parameter_panel)

    def _append_pfd_equipment_lineage(self, block_id: str) -> None:
        equipment = self._pfd_equipment_by_block.get(block_id, {})
        lineage = equipment.get("parameter_lineage") if isinstance(equipment.get("parameter_lineage"), list) else []
        if not lineage:
            return
        lines = []
        for item in lineage:
            if not isinstance(item, Mapping):
                continue
            evidence_class = str(item.get("evidence_class") or "U")
            result_status = str(item.get("result_status") or "UNKNOWN")
            equation = _math_text(str(item.get("equation_chain") or ""))
            warning = str(item.get("warning") or "").strip()
            line = f"[{evidence_class}/{result_status}] {equation}"
            if warning:
                line += f"\n    ⚠ {warning}"
            lines.append(line)
        if lines:
            existing = self.equation_text.get("1.0", "end").strip()
            section = "\n\nAspen 流程侧参数证据链（D=确定性推导，J=工程判断）\n" + "\n".join(lines)
            _set_text(self.equation_text, existing + section)
        boundary = {
            "input_provenance": equipment.get("input_provenance"),
            "evidence_boundary": equipment.get("evidence_boundary"),
            "parameter_lineage": lineage,
        }
        existing_issue = self.issue_text.get("1.0", "end").strip()
        _set_text(
            self.issue_text,
            existing_issue + "\n\nAspen 设备参数来源与适用边界\n"
            + json.dumps(boundary, ensure_ascii=False, indent=2, sort_keys=True),
        )

    def _append_pfd_parameter_override_state(self, block_id: str) -> None:
        values = self.pfd_parameter_overrides.get(block_id, {})
        if not values:
            return
        existing_issue = self.issue_text.get("1.0", "end").strip()
        boundary = {
            "schema": "equipment-design-pfd-parameter-override-view-v1",
            "block_id": block_id,
            "values": values,
            "source": "USER_SUPPLIED_PER_BLOCK",
            "aspen_bundle_modified": False,
            "formal_design_evidence": False,
            "model_promotion_allowed_by_override_alone": False,
        }
        _set_text(
            self.issue_text,
            existing_issue + "\n\nPFD 本设备补录（独立层，不是 Aspen/终证）\n"
            + json.dumps(boundary, ensure_ascii=False, indent=2, sort_keys=True),
        )

    def _open_pfd_stream(self, stream_id: str) -> None:
        self._active_pfd_block_id = None
        if hasattr(self, "pfd_parameter_button"):
            self.pfd_parameter_button.state(["disabled"])
        if stream_id in self.pfd_invalidated_streams:
            edge = self._pfd_edge_row(stream_id)
            self._render_pfd_raw_parameters(
                title=f"{stream_id} · 改型关联管线待复核",
                family="工业管道",
                status="RELATED_STREAM_RECALC_REQUIRED",
                model_status="WAITING_CALCULATED_PARAMETERS",
                parameters=edge.get("parameters", []),
                evidence={
                    "recalculation_status": edge.get("recalculation_status"),
                    "change_impact": self.aspen_pfd_mapping.get("change_impact"),
                    "stale_overlay_hidden": True,
                },
            )
            self.result_tabs.select(self.parameter_panel)
            return
        pipe = self._pfd_piping_by_stream.get(stream_id, {})
        match = pipe.get("match_result") if isinstance(pipe.get("match_result"), Mapping) else {}
        cards = result_presentation.build_presentation(match).get("equipment", []) if match else []
        if cards:
            self._render_presentation_card(cards[0])
            self.result_device_var.set(f"{stream_id} · 工业管道")
            lineage = pipe.get("derivation_chain") if isinstance(pipe.get("derivation_chain"), list) else []
            if lineage:
                existing = self.equation_text.get("1.0", "end").strip()
                direct = "\n\nAspen 字段与单位血缘\n" + "\n".join(_math_text(str(item)) for item in lineage)
                _set_text(self.equation_text, existing + direct)
            evidence = {
                "pfd_edge_label_data": pipe.get("pfd_edge_label_data"),
                "evidence_boundary": pipe.get("evidence_boundary"),
            }
            existing_issue = self.issue_text.get("1.0", "end").strip()
            _set_text(self.issue_text, existing_issue + "\n\nPFD 管线边界\n" + json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            edge = self._pfd_edge_row(stream_id)
            self._render_pfd_raw_parameters(
                title=f"{stream_id} · 工艺流股/管线",
                family="工业管道",
                status=edge.get("topology_status"),
                parameters=edge.get("parameters", []),
                evidence=edge,
            )
        self.result_tabs.select(self.parameter_panel)

    def _render_pfd_raw_parameters(
        self,
        *,
        title: str,
        family: Any,
        status: Any,
        model_status: Any = None,
        parameters: Any,
        evidence: Mapping[str, Any],
    ) -> None:
        self.result_device_var.set(title)
        self.summary_vars["状态"].set(result_presentation.code_label(status or "—"))
        self.summary_vars["设备族"].set(str(family or "—"))
        self.summary_vars["型号状态"].set(
            result_presentation.code_label(model_status)
            if model_status else "未执行 / 待闭合"
        )
        self.summary_vars["待闭合"].set("不适用" if str(model_status).upper() == "NOT_APPLICABLE" else "—")
        for tree in (self.customer_tree, self.parameter_tree, self.candidate_tree):
            self._clear_tree(tree)
        for item in parameters if isinstance(parameters, list) else []:
            if not isinstance(item, Mapping):
                continue
            self.parameter_tree.insert("", "end", values=(
                "Aspen 工艺侧", item.get("label") or item.get("field"), item.get("field"),
                _display_cell(item.get("value")), item.get("unit") or "—",
                item.get("source_status") or "Aspen", "流程数据 / 非机械终证",
            ))
        _set_text(self.issue_text, json.dumps(dict(evidence), ensure_ascii=False, indent=2, sort_keys=True))
        _set_text(self.equation_text, "当前对象尚无可闭合的设备选型公式链；原始 Aspen 参数已保留在参数卡。")

    def _show_pfd_block_menu(self, block_id: str, x_root: int, y_root: int) -> None:
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="查看参数", command=lambda: self._open_pfd_block(block_id))
        menu.add_command(
            label="补充/修改本设备参数并重算…",
            command=lambda: self._open_pfd_parameter_editor(block_id),
        )
        menu.add_separator()
        type_menu = tk.Menu(menu, tearoff=False)
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        catalog = self.aspen_pfd_mapping.get("catalog") if isinstance(self.aspen_pfd_mapping.get("catalog"), Mapping) else {}
        options = catalog.get("selection_options") if isinstance(catalog.get("selection_options"), list) else []
        for option in options:
            if isinstance(option, Mapping):
                grouped[str(option.get("family_name") or "其他")].append(dict(option))
        for family_name in sorted(grouped):
            family_menu = tk.Menu(type_menu, tearoff=False)
            for option in sorted(grouped[family_name], key=lambda item: str(item.get("display_name") or item.get("selection_id"))):
                selection_id = str(option.get("selection_id"))
                family_menu.add_command(
                    label=str(option.get("display_name") or selection_id),
                    command=lambda sid=selection_id: self._apply_pfd_override(block_id, sid),
                )
            type_menu.add_cascade(label=family_name, menu=family_menu)
        menu.add_cascade(label="更改指定类型", menu=type_menu)
        menu.add_command(
            label="恢复自动识别",
            state="normal" if block_id in self.aspen_type_overrides else "disabled",
            command=lambda: self._apply_pfd_override(block_id, None),
        )
        menu.add_command(label="按当前类型重新计算", command=lambda: self._recalculate_pfd_block(block_id))
        menu.add_separator()
        menu.add_command(label="查看识别证据", command=lambda: self._show_pfd_block_evidence(block_id))
        menu.add_command(label="复制对象 ID", command=lambda: self._copy_text(block_id))
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def _show_pfd_stream_menu(self, stream_id: str, x_root: int, y_root: int) -> None:
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="查看管线参数", command=lambda: self._open_pfd_stream(stream_id))
        edge = self._pfd_edge_row(stream_id)
        source = str(edge.get("source_node_id") or "")
        target = str(edge.get("target_node_id") or "")
        menu.add_command(
            label="定位上游设备",
            state="normal" if source.startswith("block:") else "disabled",
            command=lambda: self.pfd_view.focus_block(source.removeprefix("block:")),
        )
        menu.add_command(
            label="定位下游设备",
            state="normal" if target.startswith("block:") else "disabled",
            command=lambda: self.pfd_view.focus_block(target.removeprefix("block:")),
        )
        menu.add_separator()
        menu.add_command(label="查看管线证据", command=lambda: self._open_pfd_stream(stream_id))
        menu.add_command(label="复制流股 ID", command=lambda: self._copy_text(stream_id))
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def _show_pfd_block_evidence(self, block_id: str) -> None:
        _set_text(self.issue_text, json.dumps(self._pfd_block_row(block_id), ensure_ascii=False, indent=2, sort_keys=True))
        self.result_tabs.select(self.issue_panel)

    def _copy_text(self, value: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.status_var.set(f"已复制：{value}")

    def _pfd_selection_for_block(self, block_id: str) -> dict[str, Any]:
        row = self._pfd_block_row(block_id)
        effective = row.get("effective_mapping") if isinstance(row.get("effective_mapping"), Mapping) else {}
        selection_id = str(effective.get("selection_id") or "").strip()
        return next(
            (
                dict(item) for item in self.catalog.get("selections", [])
                if isinstance(item, Mapping) and str(item.get("selection_id")) == selection_id
            ),
            {},
        )

    def _pfd_base_match_input(self, block_id: str) -> dict[str, Any]:
        equipment = self._pfd_equipment_by_block.get(block_id, {})
        values = equipment.get("canonical_match_input") if isinstance(equipment.get("canonical_match_input"), Mapping) else {}
        return dict(values)

    @staticmethod
    def _pfd_parameter_editor_fields(
        selection: Mapping[str, Any],
        *,
        show_advanced: bool,
    ) -> list[dict[str, Any]]:
        advanced_roles = {"known_result", "advanced_design_input", "advanced_evidence"}
        fields = [
            dict(field) for field in selection.get("fields", [])
            if isinstance(field, Mapping)
            and field.get("name")
            and str(field.get("name")) not in aspen_pfd.PARAMETER_OVERRIDE_FORBIDDEN_FIELDS
            and str(field.get("manual_role") or "") != "delivery_output"
            and (
                bool(field.get("manual_default_visible"))
                or show_advanced and str(field.get("manual_role") or "") in advanced_roles
            )
        ]
        fields.sort(key=lambda field: (
            0 if field.get("candidate_closure_required") else 1,
            0 if field.get("primary_calculation_required") else 1,
            str(field.get("manual_group_title") or ""),
            str(field.get("label") or field.get("name")),
        ))
        return fields

    def _pfd_parameter_help_text(self, field: Mapping[str, Any], base_value: Any) -> str:
        base = "当前没有 Aspen/已有规范值。" if base_value in (None, "") else f"当前 Aspen/已有规范值：{base_value}。"
        return (
            self._manual_help_text(field)
            + "\n"
            + base
            + "\n留空：沿用 Aspen/已有规范值，不会删除或改写源 bundle。"
            + "\n补录：只作为本设备的用户输入参与确定性重算；补录动作本身不是正式证据，仍受原证据门限制。"
        )

    def _open_pfd_parameter_editor(self, block_id: str) -> None:
        if not block_id:
            messagebox.showwarning("未选择设备", "请先在 PFD 中左键选择一台设备。", parent=self.root)
            return
        if not self.aspen_bundle:
            messagebox.showwarning("缺少 Aspen 数据", "请先完成 Aspen 导入。", parent=self.root)
            return
        selection = self._pfd_selection_for_block(block_id)
        if not selection:
            messagebox.showwarning(
                "类型尚未唯一",
                f"{block_id} 还没有唯一设备类型。请先用右键菜单指定类型，再补充参数。",
                parent=self.root,
            )
            return
        existing = self.pfd_parameter_window
        if existing is not None and existing.winfo_exists():
            existing.destroy()

        window = tk.Toplevel(self.root)
        self.pfd_parameter_window = window
        window.title(f"{block_id} · 补充/修改设备参数")
        window.geometry("860x720")
        window.minsize(700, 520)
        window.transient(self.root)
        window.configure(bg=COLORS["canvas"])

        header = ttk.Frame(window, style="Header.TFrame", padding=(20, 14))
        header.pack(fill="x")
        ttk.Label(header, text=f"{block_id} · {selection.get('display_name')}", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="空框表示沿用 Aspen/原计算输入；填写值只进入本设备独立参数层，确定后立即无 LLM 重算。",
            style="HeaderSub.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        host = ttk.Frame(window, style="Panel.TFrame", padding=(16, 12))
        host.pack(fill="both", expand=True, padx=14, pady=14)
        advanced_var = tk.BooleanVar(value=False)
        editor_bar = ttk.Frame(host, style="Panel.TFrame")
        editor_bar.pack(fill="x", pady=(0, 10))
        ttk.Label(
            editor_bar,
            text="默认只显示真实输入与可选偏好；客户交付输出不在这里填写。",
            style="Muted.TLabel",
        ).pack(side="left", fill="x", expand=True)
        advanced_toggle = ttk.Checkbutton(
            editor_bar,
            text="显示已有结果 / 高级设计 / 正式证据项",
            variable=advanced_var,
        )
        advanced_toggle.pack(side="right", padx=(10, 0))

        body = ttk.Frame(host, style="Panel.TFrame")
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, bg=COLORS["panel"], highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        form = ttk.Frame(canvas, style="Panel.TFrame")
        form_window = canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(form_window, width=event.width))
        form.columnconfigure(1, weight=1)

        base_values = self._pfd_base_match_input(block_id)
        current_values = dict(self.pfd_parameter_overrides.get(block_id, {}))
        all_fields = self._pfd_parameter_editor_fields(selection, show_advanced=True)
        editor_vars: dict[str, tk.StringVar] = {
            str(field["name"]): tk.StringVar(value=str(current_values.get(str(field["name"]), "")))
            for field in all_fields
        }

        def render_editor_fields() -> None:
            for child in form.winfo_children():
                child.destroy()
            fields = self._pfd_parameter_editor_fields(selection, show_advanced=advanced_var.get())
            row_index = 0
            current_group = None
            for field in fields:
                group_title = str(field.get("manual_group_title") or "其他可选输入")
                if group_title != current_group:
                    ttk.Label(form, text=group_title, style="Section.TLabel").grid(
                        row=row_index, column=0, columnspan=4, sticky="w", pady=(12 if row_index else 0, 4)
                    )
                    current_group = group_title
                    row_index += 1
                name = str(field["name"])
                unit = f" [{field.get('unit')}]" if field.get("unit") else ""
                role = str(field.get("manual_role") or "optional_input")
                if field.get("primary_calculation_required"):
                    badge, badge_style = "主计算必填", "RequiredBadge.TLabel"
                elif field.get("candidate_closure_required"):
                    badge, badge_style = "候选闭合·可留空", "RecommendedBadge.TLabel"
                else:
                    badge, badge_style = {
                        "required_input": ("主计算必填", "RequiredBadge.TLabel"),
                        "recommended_input": ("建议输入", "RecommendedBadge.TLabel"),
                        "optional_input": ("可选输入", "OptionalBadge.TLabel"),
                        "optional_preference": ("可选偏好", "OptionalBadge.TLabel"),
                        "known_result": ("已有结果·高级", "AdvancedBadge.TLabel"),
                        "advanced_design_input": ("高级设计·可留空", "AdvancedBadge.TLabel"),
                        "advanced_evidence": ("正式证据·高级", "AdvancedBadge.TLabel"),
                    }.get(role, ("可选输入", "OptionalBadge.TLabel"))
                ttk.Label(form, text=f"{field.get('label') or name}{unit}", style="Field.TLabel").grid(
                    row=row_index, column=0, sticky="w", padx=(0, 12), pady=5
                )
                variable = editor_vars[name]
                if field.get("type") == "select":
                    options = tuple(field.get("options") or ())
                    widget: tk.Widget = TranslatedCombobox(
                        form,
                        textvariable=variable,
                        values=("", *options),
                        state="readonly",
                    )
                else:
                    widget = ttk.Entry(form, textvariable=variable)
                widget.grid(row=row_index, column=1, sticky="ew", pady=5)
                base = base_values.get(name)
                base_text = f"已有 {base}" if base not in (None, "") else "已有 —"
                ttk.Label(form, text=f"{badge} · {base_text}", style=badge_style).grid(
                    row=row_index, column=2, sticky="w", padx=(10, 0), pady=5
                )
                help_control = tk.Label(
                    form,
                    text="ⓘ",
                    bg=COLORS["panel"],
                    fg=COLORS["accent_dark"],
                    cursor="question_arrow",
                    font=("Microsoft YaHei UI", 10, "bold"),
                    padx=5,
                )
                help_control.grid(row=row_index, column=3, sticky="e", padx=(4, 0), pady=5)
                HoverHelp(
                    help_control,
                    lambda selected_field=field, existing_value=base: self._pfd_parameter_help_text(
                        selected_field,
                        existing_value,
                    ),
                )
                row_index += 1
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.yview_moveto(0)

        advanced_toggle.configure(command=render_editor_fields)
        render_editor_fields()

        footer = ttk.Frame(window, style="App.TFrame", padding=(14, 0, 14, 14))
        footer.pack(fill="x")

        def submit() -> None:
            values = {
                name: variable.get().strip()
                for name, variable in editor_vars.items()
                if variable.get().strip()
            }
            if self._apply_pfd_parameter_overrides(block_id, values):
                window.destroy()

        def clear_values() -> None:
            if not messagebox.askyesno(
                "清空本设备补录",
                "将移除本设备的独立补录值，并仅用原 Aspen/已有规范输入重新计算。是否继续？",
                parent=window,
            ):
                return
            if self._apply_pfd_parameter_overrides(block_id, {}, clear=True):
                window.destroy()

        ttk.Button(footer, text="取消", style="Secondary.TButton", command=window.destroy).pack(side="right")
        ttk.Button(footer, text="清空补录并重算", style="Secondary.TButton", command=clear_values).pack(side="right", padx=8)
        ttk.Button(footer, text="确定性重算", style="Primary.TButton", command=submit).pack(side="right")

    def _apply_pfd_parameter_overrides(
        self,
        block_id: str,
        values: Mapping[str, Any],
        *,
        clear: bool = False,
    ) -> bool:
        if not self.aspen_bundle:
            messagebox.showwarning("缺少 Aspen 数据", "请先完成 Aspen 导入。", parent=self.root)
            return False
        try:
            canonical_blocks, canonical_streams, normalization_issues = self._pfd_canonical_context()
            result = aspen_pfd.update_parameter_override(
                self.aspen_bundle,
                self.aspen_type_overrides,
                self.pfd_parameter_overrides,
                block_id,
                values,
                clear=clear,
                catalog=self.catalog,
                canonical_parameters_by_block=canonical_blocks,
                canonical_parameters_by_stream=canonical_streams,
                parameter_normalization_issues=normalization_issues,
            )
            self.pfd_parameter_overrides = {
                str(key): dict(value)
                for key, value in result.get("parameter_overrides", {}).items()
                if isinstance(value, Mapping)
            }
            self.aspen_pfd_mapping = dict(result["mapping"])
            impact = result.get("change_impact") if isinstance(result.get("change_impact"), Mapping) else {}
            affected_blocks = {
                str(item)
                for key in ("changed_blocks", "immediate_upstream_blocks", "immediate_downstream_blocks")
                for item in impact.get(key, [])
            }
            affected_streams = {str(item) for item in impact.get("affected_streams", [])}
            for affected_block in affected_blocks:
                self.pfd_recalculated_results.pop(affected_block, None)
            self.pfd_invalidated_blocks.update(affected_blocks)
            self.pfd_invalidated_streams.update(affected_streams)
            self._sync_pfd_view()
            self._recalculate_pfd_block(block_id, refresh=False)
            self._save_pfd_parameter_state(result)
            self._sync_pfd_view()
            self.status_var.set(
                "本设备补录已进入确定性重算；相邻设备与关联管线保持待复核，补录本身不构成证据。"
            )
            self._open_pfd_block(block_id)
            return True
        except aspen_pfd.AspenPFDMappingError as exc:
            messagebox.showerror("设备参数补录失败", f"{exc.code}：{exc}", parent=self.root)
        except Exception as exc:
            messagebox.showerror("设备参数补录失败", str(exc), parent=self.root)
        return False

    def _apply_pfd_override(self, block_id: str, selection_id: str | None) -> None:
        if not self.aspen_bundle:
            messagebox.showwarning("缺少 Aspen 数据", "请先完成 Aspen 导入。", parent=self.root)
            return
        try:
            canonical_blocks, canonical_streams, normalization_issues = self._pfd_canonical_context()
            result = aspen_pfd.update_type_override(
                self.aspen_bundle,
                self.aspen_type_overrides,
                block_id,
                selection_id,
                catalog=self.catalog,
                canonical_parameters_by_block=canonical_blocks,
                canonical_parameters_by_stream=canonical_streams,
                parameter_overrides=self.pfd_parameter_overrides,
                parameter_normalization_issues=normalization_issues,
            )
            self.aspen_type_overrides = dict(result["overrides"])
            self.aspen_pfd_mapping = dict(result["mapping"])
            impact = result.get("change_impact") if isinstance(result.get("change_impact"), Mapping) else {}
            affected_blocks = {
                str(item)
                for key in ("changed_blocks", "immediate_upstream_blocks", "immediate_downstream_blocks")
                for item in impact.get(key, [])
            }
            affected_streams = {str(item) for item in impact.get("affected_streams", [])}
            for affected_block in affected_blocks:
                self.pfd_recalculated_results.pop(affected_block, None)
            self.pfd_invalidated_blocks.update(affected_blocks)
            self.pfd_invalidated_streams.update(affected_streams)
            self._sync_pfd_view()
            try:
                self._recalculate_pfd_block(block_id, selection_id=selection_id, refresh=False)
            except Exception:
                self._save_pfd_override_state(result)
                self._sync_pfd_view()
                raise
            self._save_pfd_override_state(result)
            self._sync_pfd_view()
            self.status_var.set("改型设备已按确定性链重算；相邻设备与关联管线仍明确标为待复核。")
            self._open_pfd_block(block_id)
        except aspen_pfd.AspenPFDMappingError as exc:
            messagebox.showerror("类型修改失败", f"{exc.code}：{exc}", parent=self.root)
        except Exception as exc:
            messagebox.showerror("类型修改失败", str(exc), parent=self.root)

    def _recalculate_pfd_block(
        self,
        block_id: str,
        selection_id: str | None = None,
        *,
        refresh: bool = True,
    ) -> None:
        self.pfd_recalculated_results.pop(block_id, None)
        self.pfd_invalidated_blocks.add(block_id)
        if refresh:
            self._sync_pfd_view()
        row = self._pfd_block_row(block_id)
        effective = row.get("effective_mapping") if isinstance(row.get("effective_mapping"), Mapping) else {}
        chosen = selection_id or effective.get("selection_id")
        if not chosen:
            self.status_var.set(f"{block_id} 尚无唯一类型；保留候选，不执行越级选型。")
            return
        equipment = self._pfd_equipment_by_block.get(block_id, {})
        values = equipment.get("canonical_match_input") if isinstance(equipment.get("canonical_match_input"), Mapping) else {}
        if not values:
            self.status_var.set(f"{block_id} 缺少可回放的规范输入；映射已更新，计算保持待闭合。")
            return
        parameter_values = self.pfd_parameter_overrides.get(block_id, {})
        selection = next(
            (
                item for item in self.catalog.get("selections", [])
                if isinstance(item, Mapping) and str(item.get("selection_id")) == str(chosen)
            ),
            {},
        )
        allowed_fields = {
            str(field.get("name"))
            for field in selection.get("fields", [])
            if isinstance(field, Mapping) and field.get("name")
        }
        unknown_fields = sorted(set(parameter_values) - allowed_fields)
        if unknown_fields:
            raise ValueError(
                "当前补录包含不属于所选类型的字段："
                + "、".join(unknown_fields)
                + "。请打开参数补录窗口重新保存或清空本设备补录。"
            )
        cleaned = aspen_pfd.merge_canonical_input_with_parameter_overrides(
            block_id,
            values,
            parameter_values,
        )
        response = self.api.manual_match(str(chosen), cleaned)
        if not response.get("ok"):
            raise RuntimeError(response.get("error", f"{block_id} 复算失败"))
        self.pfd_recalculated_results[block_id] = dict(response["value"])
        self.pfd_invalidated_blocks.discard(block_id)
        self.aspen_pfd_mapping = aspen_pfd.mark_block_recalculated(self.aspen_pfd_mapping, block_id)
        if refresh:
            self._sync_pfd_view()
            self._open_pfd_block(block_id)

    def _save_pfd_override_state(self, override_result: Mapping[str, Any]) -> None:
        if not self.session_dir:
            return
        path = Path(self.session_dir) / "aspen_pfd_overrides.json"
        payload = {
            "schema": "equipment-design-pfd-overrides-v1",
            "source_mapping_sha256": self.aspen_pfd_mapping.get("source", {}).get("canonical_content_sha256"),
            "mapping_sha256": self.aspen_pfd_mapping.get("mapping_sha256"),
            "overrides": self.aspen_type_overrides,
            "change_impact": override_result.get("change_impact"),
            "source_bkp_modified": False,
            "model_promotion_allowed": False,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _save_pfd_parameter_state(self, parameter_result: Mapping[str, Any]) -> None:
        if not self.session_dir:
            return
        path = Path(self.session_dir) / "aspen_pfd_parameter_overrides.json"
        payload = {
            "schema": "equipment-design-pfd-parameter-overrides-v1",
            "source_bundle_schema": self.aspen_bundle.get("schema"),
            "source_bundle_canonical_sha256": self.aspen_pfd_mapping.get("source", {}).get("canonical_content_sha256"),
            "mapping_sha256": self.aspen_pfd_mapping.get("mapping_sha256"),
            "type_overrides": self.aspen_type_overrides,
            "parameter_overrides": self.pfd_parameter_overrides,
            "recalculated_results": self.pfd_recalculated_results,
            "change_impact": parameter_result.get("change_impact"),
            "input_provenance": "USER_SUPPLIED_PER_BLOCK_NOT_EVIDENCE_BY_ITSELF",
            "source_bundle_modified": False,
            "model_promotion_allowed_by_override_alone": False,
            "llm_used": False,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _run_manual(self) -> None:
        try:
            selection = self._selection()
            values = self._collect_manual()
            response = self.api.manual_match(selection["selection_id"], values)
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "匹配失败"))
            self.last_manual = {"selection_id": selection["selection_id"], "values": values}
            self.last_source_input = {
                "operation": "manual_match",
                "payload": {
                    "selection_id": selection["selection_id"],
                    "values": json.loads(json.dumps(values, ensure_ascii=False)),
                },
            }
            self.last_deterministic_result = response["value"]
            self._render_result(self.last_deterministic_result)
            self.status_var.set("确定性匹配完成")
        except Exception as exc:
            messagebox.showerror("匹配失败", str(exc), parent=self.root)

    def _run_llm(self) -> None:
        deterministic = self.last_deterministic_result or self.last_result
        source_input = self.last_source_input
        if not deterministic or not source_input:
            messagebox.showwarning("缺少确定性结果", "请先完成 Aspen 或手动匹配。", parent=self.root)
            return
        current_settings = self._collect_llm_settings()
        current_fingerprint = self._llm_settings_sha256(current_settings)
        if (
            self._applied_llm_settings is None
            or self._applied_llm_settings_fingerprint != current_fingerprint
        ):
            self._invalidate_applied_llm_settings()
            messagebox.showwarning("设置尚未应用", "请先测试当前连接并点击“应用设置”，再开始协同计算。", parent=self.root)
            return
        applied = json.loads(json.dumps(self._applied_llm_settings, ensure_ascii=False))
        config = applied["config"]
        config["task"] = applied["task"]
        knowledge_config = applied["knowledge_config"]
        self.llm_proposal = None
        self.apply_llm_button.state(["disabled"])
        self.hybrid_state.set("机器状态：RUNNING · 确定性结果已冻结")

        def done(response: dict[str, Any]) -> None:
            if not response.get("ok"):
                self.hybrid_state.set("机器状态：FAILED · 确定性结果仍保留")
                messagebox.showerror("Agent 协同失败", response.get("error", "未知错误"), parent=self.root)
                return
            hybrid = response["value"]
            machine_state = hybrid.get("machine_state", {})
            state = str(machine_state.get("state", "COMPLETED"))
            fallback = hybrid.get("fallback", {})
            review = hybrid.get("llm_review", {})
            if review.get("status") == "COMPLETED_STRICT" and isinstance(review.get("result"), dict):
                self.llm_proposal = review["result"]
            validated = self.llm_proposal.get("validated_proposal", {}) if self.llm_proposal else {}
            step_output = self.llm_proposal.get("step_output", {}) if self.llm_proposal else {}
            fallback_errors = fallback.get("errors", []) if isinstance(fallback, dict) else []
            detail = "；".join(str(item.get("message", "")) for item in fallback_errors if isinstance(item, dict))
            prepared = hybrid.get("prepared", {}) if isinstance(hybrid.get("prepared"), dict) else {}
            context_pack = prepared.get("context_pack", {}) if isinstance(prepared.get("context_pack"), dict) else {}
            context_hash = str(context_pack.get("context_sha256") or "")
            context_note = f" · context {context_hash[:12]}" if context_hash else ""
            self.hybrid_state.set(
                f"机器状态：{state}{context_note}" + (f" · 回退原因：{detail}" if detail else "")
            )
            self._render_result(hybrid)
            if validated.get("accepted_changes") and self.last_manual:
                self.apply_llm_button.state(["!disabled"])
            if fallback.get("used"):
                messagebox.showwarning("已回退到确定性结果", detail or "可选协同层失败；确定性结果未丢失。", parent=self.root)
            else:
                messagebox.showinfo(
                    "Agent 协同完成",
                    step_output.get("summary") or validated.get("summary") or "机器流程已完成。",
                    parent=self.root,
                )

        self._background(
            self.llm_button,
            "Agent 协同运行中…",
            lambda: self.api.agent_hybrid_run(
                source_input,
                config,
                knowledge_config,
                applied["injection_point"],
                applied["context_scope"],
            ),
            done,
        )

    def _apply_llm(self) -> None:
        if not self.last_manual or not self.llm_proposal:
            return
        replay = self.llm_proposal.get("replay_contract", {})
        replay_input = replay.get("input", {}) if isinstance(replay, dict) else {}
        replay_payload = replay_input.get("payload", {}) if isinstance(replay_input, dict) else {}
        current_selection = self._selection()["selection_id"]
        current_values = self._collect_manual()
        frozen_selection = replay_payload.get("selection_id")
        frozen_values = replay_payload.get("values")
        if (
            replay_input.get("operation") != "manual_match"
            or current_selection != frozen_selection
            or current_values != frozen_values
        ):
            self.llm_proposal = None
            self.apply_llm_button.state(["disabled"])
            messagebox.showwarning(
                "草案已失效",
                "审核后手动输入或模块选择已经变化。请先重新做确定性匹配，再重新运行 Agent 协同。",
                parent=self.root,
            )
            return
        validated = self.llm_proposal.get("validated_proposal", {})
        approved_ids = [
            str(item.get("change_id"))
            for item in validated.get("accepted_changes", [])
            if isinstance(item, dict) and str(item.get("change_id", "")).strip()
        ]
        approval = {
            "approved": True,
            "approved_change_ids": approved_ids,
            "approved_by": "GUI_USER_EXPLICIT_CLICK",
            "context_sha256": self.llm_proposal.get("context_sha256"),
            "orchestration_sha256": self.llm_proposal.get("orchestration_sha256"),
        }
        response = self.api.agent_llm_apply(self.llm_proposal, approval)
        if not response.get("ok"):
            messagebox.showerror("草案应用失败", response.get("error", "未知错误"), parent=self.root)
            return
        application = response["value"]
        values = application.get("applied_draft", {})
        recalculated = application.get("deterministic_recalculation")
        if not isinstance(values, dict) or not isinstance(recalculated, dict):
            messagebox.showerror("草案应用失败", "Agent llm_apply 没有返回确定性复算结果。", parent=self.root)
            return
        self.tabs.select(1)
        self._fill_manual(values)
        self.last_manual["values"] = values
        self.last_source_input = {
            "operation": "manual_match",
            "payload": {
                "selection_id": self.last_manual["selection_id"],
                "values": json.loads(json.dumps(values, ensure_ascii=False)),
            },
        }
        self.last_deterministic_result = recalculated
        self.llm_proposal = None
        self.apply_llm_button.state(["disabled"])
        self._render_result(application)

    def _search_knowledge(self) -> None:
        query = self.kg_query.get().strip()
        if not query:
            return
        response = self.api.search_knowledge(query)
        if not response.get("ok"):
            _set_text(self.kg_output, response.get("error", "查询失败"))
            return
        value = response["value"]
        _set_text(self.kg_output, value.get("text") or value.get("stderr") or value.get("status", "无结果"))

    def _current_derivation_session(
        self,
    ) -> tuple[str, dict[str, Any]] | tuple[None, None]:
        cards = self.presentation.get("equipment", [])
        if not cards:
            return None, None
        label = self.result_device_var.get()
        card = self._presentation_by_label.get(label, cards[0])
        current_hash = str(
            card.get("header", {}).get("source_result_sha256")
            or ""
        )
        baseline_hash = self.derivation_result_to_baseline.get(
            current_hash,
            current_hash,
        )
        session = self.derivation_sessions.get(baseline_hash)
        if session is None:
            return None, None
        return baseline_hash, session

    def _render_derivation_workbench(
        self,
        card: Mapping[str, Any],
    ) -> None:
        current_hash = str(
            card.get("header", {}).get("source_result_sha256")
            or ""
        )
        baseline_hash = self.derivation_result_to_baseline.get(
            current_hash,
            current_hash,
        )
        session = self.derivation_sessions.get(baseline_hash)
        if not session:
            self.current_derivation_workbench = {}
            self.derivation_canvas.delete("all")
            for item in self.derivation_field_tree.get_children():
                self.derivation_field_tree.delete(item)
            self.derivation_status_var.set(
                "当前结果没有可编辑的单设备确定性推导链。"
            )
            return
        try:
            model_rules = self.core.matcher.load_model_rules()
            workbench = derivation_workbench.build_workbench(
                session["baseline_result"],
                self.catalog,
                model_rules=model_rules,
                selection_id=session.get("selection_id"),
                overrides=session.get("overrides", {}),
                active_result=session.get("current_result"),
            )
        except Exception as exc:
            self.derivation_status_var.set(
                f"推导链生成失败：{exc}"
            )
            return
        if not session.get("selection_id"):
            session["selection_id"] = workbench.get(
                "default_selection_id"
            )
        self.current_derivation_workbench = workbench
        node_ids = {
            str(item.get("node_id"))
            for item in workbench.get("nodes", [])
        }
        if self.current_derivation_node_id not in node_ids:
            self.current_derivation_node_id = "source"
        self._draw_derivation_flow()
        self._show_derivation_node(
            self.current_derivation_node_id
        )
        override_count = int(workbench.get("override_count", 0))
        self.derivation_status_var.set(
            (
                f"当前有 {override_count} 项人工覆盖，尚需点击“仅重算当前设备”。"
                if override_count
                else "当前采用全部程序默认值；点流程框查看或修改允许项。"
            )
        )

    def _draw_derivation_flow(self) -> None:
        canvas = self.derivation_canvas
        canvas.delete("all")
        nodes = self.current_derivation_workbench.get(
            "nodes", []
        )
        x = 18
        box_width = 205
        box_height = 88
        gap = 38
        y = 18
        for index, node in enumerate(nodes):
            node_id = str(node.get("node_id") or index)
            selected = (
                node_id == self.current_derivation_node_id
            )
            status = str(node.get("status") or "UNKNOWN")
            warning = (
                "REVIEW" in status
                or "BLOCKED" in status
                or (
                    node_id == "adjustment"
                    and node.get("status")
                    == "RECOMMENDED_ALGORITHMIC_MODIFICATION"
                )
            )
            fill = (
                "#DDEFF3"
                if selected
                else "#FFF0D5"
                if warning
                else "#FFFFFF"
            )
            outline = (
                COLORS["accent"]
                if selected
                else COLORS["warn"]
                if warning
                else COLORS["line"]
            )
            tag = f"derivation-node:{node_id}"
            canvas.create_rectangle(
                x,
                y,
                x + box_width,
                y + box_height,
                fill=fill,
                outline=outline,
                width=3 if selected else 2,
                tags=(tag, "derivation-node"),
            )
            canvas.create_text(
                x + 10,
                y + 10,
                text=str(node.get("title") or node_id),
                anchor="nw",
                width=box_width - 20,
                fill=COLORS["ink"],
                font=("Microsoft YaHei UI", 10, "bold"),
                tags=(tag, "derivation-node"),
            )
            canvas.create_text(
                x + 10,
                y + 38,
                text=str(node.get("summary") or "—"),
                anchor="nw",
                width=box_width - 20,
                fill=COLORS["muted"],
                font=("Microsoft YaHei UI", 8),
                tags=(tag, "derivation-node"),
            )
            canvas.tag_bind(
                tag,
                "<Button-1>",
                lambda _event, selected_id=node_id: (
                    self._show_derivation_node(selected_id)
                ),
            )
            if index < len(nodes) - 1:
                arrow_start = x + box_width + 4
                arrow_end = x + box_width + gap - 4
                canvas.create_line(
                    arrow_start,
                    y + box_height / 2,
                    arrow_end,
                    y + box_height / 2,
                    arrow=tk.LAST,
                    width=2,
                    fill=COLORS["accent_dark"],
                )
            x += box_width + gap
        canvas.configure(
            scrollregion=(0, 0, max(x, 900), 125)
        )

    def _show_derivation_node(self, node_id: str) -> None:
        nodes = self.current_derivation_workbench.get(
            "nodes", []
        )
        node = next(
            (
                item
                for item in nodes
                if str(item.get("node_id")) == node_id
            ),
            None,
        )
        if not isinstance(node, dict):
            return
        self.current_derivation_node_id = node_id
        self._draw_derivation_flow()
        for item in self.derivation_field_tree.get_children():
            self.derivation_field_tree.delete(item)
        self._derivation_fields_by_iid: dict[str, dict[str, Any]] = {}
        editable_fields = [
            item
            for item in node.get("editable_fields", [])
            if isinstance(item, dict)
        ]
        for index, field in enumerate(editable_fields):
            iid = f"{node_id}:{index}"
            self._derivation_fields_by_iid[iid] = field
            state = (
                "人工覆盖"
                if field.get("override_active")
                else "程序默认"
            )
            if field.get("editable") is False:
                state = "只读保护"
            self.derivation_field_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    field.get("group_title")
                    or node.get("title"),
                    field.get("label"),
                    _display_cell(field.get("default_value")),
                    _display_cell(field.get("current_value")),
                    state,
                ),
            )
        self.current_derivation_field = None
        self.derivation_detail_title_var.set(
            str(node.get("title") or node_id)
        )
        self.derivation_default_var.set(
            f"状态：{result_presentation.code_label(node.get('status'))}"
        )
        self.derivation_edit_var.set("")
        self.derivation_edit_combo.configure(
            values=(),
            state="disabled",
        )
        self.derivation_apply_button.state(["disabled"])
        self.derivation_clear_field_button.state(["disabled"])
        details = {
            "说明": node.get("summary"),
            "状态": node.get("status"),
            "详细链条": node.get("details", {}),
        }
        _set_text(
            self.derivation_detail_text,
            json.dumps(
                details,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )

    def _select_derivation_field(self) -> None:
        selection = self.derivation_field_tree.selection()
        if not selection:
            return
        field = getattr(
            self,
            "_derivation_fields_by_iid",
            {},
        ).get(selection[0])
        if not isinstance(field, dict):
            return
        self.current_derivation_field = field
        unit = (
            f" [{field.get('unit')}]"
            if field.get("unit")
            else ""
        )
        self.derivation_detail_title_var.set(
            f"{field.get('label')}{unit}"
        )
        self.derivation_default_var.set(
            "程序默认："
            + _display_cell(field.get("default_value"))
            + "；当前："
            + _display_cell(field.get("current_value"))
        )
        options = [
            item
            for item in field.get("options", [])
            if isinstance(item, dict)
        ]
        self.derivation_option_map = {
            str(item.get("label")): item.get("value")
            for item in options
        }
        reverse = {
            item.get("value"): str(item.get("label"))
            for item in options
        }
        current_value = field.get("current_value")
        display_value = reverse.get(
            current_value,
            _display_cell(current_value)
            if current_value not in (None, "")
            else "",
        )
        self.derivation_edit_var.set(display_value)
        editable = field.get("editable") is not False
        self.derivation_edit_combo.configure(
            values=list(self.derivation_option_map),
            state=(
                "readonly"
                if editable and options
                else "normal"
                if editable
                else "disabled"
            ),
        )
        if editable:
            self.derivation_apply_button.state(["!disabled"])
            self.derivation_clear_field_button.state(
                ["!disabled"]
            )
        else:
            self.derivation_apply_button.state(["disabled"])
            self.derivation_clear_field_button.state(["disabled"])
        _set_text(
            self.derivation_detail_text,
            json.dumps(
                {
                    "内部字段代码": field.get("field_id"),
                    "中文名称": field.get("label"),
                    "程序默认": field.get("default_value"),
                    "当前场景": field.get("current_value"),
                    "来源": field.get("source_kind"),
                    "计算状态": field.get("state"),
                    "提示": field.get("warning"),
                    "可选项": [
                        {
                            "中文": item.get("label"),
                            "内部代码": item.get(
                                "internal_code"
                            ),
                        }
                        for item in options
                    ],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )

    def _apply_derivation_field_override(self) -> None:
        field = self.current_derivation_field
        baseline_hash, session = (
            self._current_derivation_session()
        )
        if not field or not baseline_hash or not session:
            return
        display_value = self.derivation_edit_var.get().strip()
        value = self.derivation_option_map.get(
            display_value,
            display_value,
        )
        field_id = str(field.get("field_id") or "")
        overrides = session.setdefault("overrides", {})
        if value in (None, "") or value == field.get(
            "default_value"
        ):
            overrides.pop(field_id, None)
        else:
            overrides[field_id] = value
        self._render_derivation_workbench(
            self._presentation_by_label.get(
                self.result_device_var.get(),
                self.presentation.get("equipment", [{}])[0],
            )
        )

    def _clear_derivation_field_override(self) -> None:
        field = self.current_derivation_field
        baseline_hash, session = (
            self._current_derivation_session()
        )
        if not field or not baseline_hash or not session:
            return
        session.setdefault("overrides", {}).pop(
            str(field.get("field_id") or ""),
            None,
        )
        self._render_derivation_workbench(
            self._presentation_by_label.get(
                self.result_device_var.get(),
                self.presentation.get("equipment", [{}])[0],
            )
        )

    def _derivation_scenario_payload(
        self,
        replacement_index: int,
        replacement_result: Mapping[str, Any],
        audit: Mapping[str, Any],
    ) -> dict[str, Any]:
        results = list(getattr(self, "_current_match_results", []))
        if 0 <= replacement_index < len(results):
            results[replacement_index] = dict(replacement_result)
        else:
            results = [dict(replacement_result)]
        return {
            "schema": "equipment-derivation-user-scenario-batch-v1",
            "equipment": [
                {"match_result": item}
                for item in results
            ],
            "active_equipment_index": replacement_index,
            "user_override_audit": dict(audit),
            "source_baseline_modified": False,
            "formal_model_promotion_allowed_by_override_alone": False,
            "llm_used": False,
        }

    def _recalculate_derivation_equipment(self) -> None:
        baseline_hash, session = (
            self._current_derivation_session()
        )
        if not baseline_hash or not session:
            messagebox.showwarning(
                "没有可重算设备",
                "请先运行或导入一个确定性设备结果。",
                parent=self.root,
            )
            return
        overrides = dict(session.get("overrides", {}))
        baseline = session["baseline_result"]
        baseline_input = baseline.get(
            "normalized_input", {}
        )
        values = (
            dict(baseline_input)
            if isinstance(baseline_input, dict)
            else {}
        )
        values.update({
            key: value
            for key, value in overrides.items()
            if key != "__selection_id__"
        })
        selection_id = str(
            overrides.get("__selection_id__")
            or session.get("selection_id")
            or ""
        )
        if not selection_id:
            messagebox.showerror(
                "模板未闭合",
                "当前设备没有可复算模板，请在“设备族与计算模板”节点选择一个中文模板。",
                parent=self.root,
            )
            return
        try:
            recalculated = self.core.manual_match(
                selection_id,
                values,
            )
            current_result = recalculated.get("result")
            if not isinstance(current_result, dict):
                raise RuntimeError("单设备重算没有返回确定性结果。")
            audit = derivation_workbench.build_override_audit(
                baseline,
                overrides,
                current_result,
            )
            current_result = {
                **current_result,
                "user_derivation_override_audit": audit,
            }
            new_hash = derivation_workbench.canonical_sha256(
                current_result
            )
            session["current_result"] = current_result
            session["selection_id"] = selection_id
            self.derivation_result_to_baseline[
                new_hash
            ] = baseline_hash
            self.last_manual = {
                "selection_id": selection_id,
                "values": values,
            }
            self.last_source_input = {
                "operation": "manual_match",
                "payload": {
                    "selection_id": selection_id,
                    "values": json.loads(
                        json.dumps(values, ensure_ascii=False)
                    ),
                },
            }
            self.last_deterministic_result = recalculated
            index = getattr(
                self,
                "_current_presentation_index",
                0,
            )
            payload = self._derivation_scenario_payload(
                index,
                current_result,
                audit,
            )
            self._render_result(payload)
            labels = list(
                self.result_device_combo.cget("values")
            )
            if labels:
                self.result_device_var.set(
                    labels[min(index, len(labels) - 1)]
                )
                self._render_selected_presentation()
            self.status_var.set(
                "当前设备已按人工场景重算；默认值和修改审计均已保留"
            )
        except Exception as exc:
            messagebox.showerror(
                "单设备重算失败",
                str(exc),
                parent=self.root,
            )

    def _restore_derivation_defaults(self) -> None:
        baseline_hash, session = (
            self._current_derivation_session()
        )
        if not baseline_hash or not session:
            return
        session["overrides"] = {}
        session["current_result"] = session["baseline_result"]
        index = getattr(
            self,
            "_current_presentation_index",
            0,
        )
        audit = derivation_workbench.build_override_audit(
            session["baseline_result"],
            {},
            session["baseline_result"],
        )
        payload = self._derivation_scenario_payload(
            index,
            session["baseline_result"],
            audit,
        )
        self._render_result(payload)
        labels = list(self.result_device_combo.cget("values"))
        if labels:
            self.result_device_var.set(
                labels[min(index, len(labels) - 1)]
            )
            self._render_selected_presentation()
        self.status_var.set(
            "当前设备已恢复程序默认结果；Aspen/原始来源未被修改"
        )

    def _render_selected_presentation(self) -> None:
        cards = self.presentation.get("equipment", [])
        if not cards:
            return
        label = self.result_device_var.get()
        card = self._presentation_by_label.get(label, cards[0])
        labels = list(self.result_device_combo.cget("values"))
        self._current_presentation_index = (
            labels.index(label) if label in labels else 0
        )
        self._render_presentation_card(card)

    def _render_presentation_card(self, card: Mapping[str, Any]) -> None:
        header = card.get("header", {})
        axes = card.get("status_axes", {})
        issues = card.get("issues", {})
        unresolved = sum(
            len(issues.get(key, []))
            for key in ("hard_blockers", "conflicts", "missing_by_goal", "evidence_gaps")
            if isinstance(issues.get(key), list)
        )
        self.summary_vars["状态"].set(result_presentation.code_label(axes.get("identity", "—")))
        self.summary_vars["设备族"].set(str(header.get("family_name") or header.get("family_id") or "—"))
        self.summary_vars["型号状态"].set(result_presentation.code_label(axes.get("formal_model", "—")))
        self.summary_vars["待闭合"].set(str(unresolved))
        self._render_derivation_workbench(card)

        for item in self.customer_tree.get_children():
            self.customer_tree.delete(item)
        customer_overview = card.get("customer_overview") or {}
        for overview_item in _customer_overview_rows(customer_overview):
            self.customer_tree.insert("", "end", values=(
                "一览表", overview_item["label"],
                _display_cell(overview_item["value"]),
                "—", "—", "—", "GLOBAL",
            ))
        customer_datasheet = card.get("customer_datasheet") or {}
        for field in customer_datasheet.get("fields", []):
            self.customer_tree.insert("", "end", values=(
                "族数据表", field.get("label"), _display_cell(field.get("value")),
                field.get("unit") or "—", field.get("state"),
                _display_cell(field.get("evidence_gate")), _display_cell(field.get("profile_ids")),
            ))

        selected_output = (
            card.get("selected_output")
            if isinstance(card.get("selected_output"), Mapping)
            else {}
        )
        branch = (
            card.get("branch_selection")
            if isinstance(card.get("branch_selection"), Mapping)
            else {}
        )
        branch_lines = [
            "程序实际选择",
            f"设备族：{selected_output.get('family_name') or selected_output.get('family_id') or 'OPEN'}",
            f"型式：{selected_output.get('recommended_type') or 'OPEN'}",
            f"型号/工程规格：{selected_output.get('model_or_specification') or 'OPEN'}",
            f"型号状态：{result_presentation.code_label(selected_output.get('model_status'))}",
            f"型式规则：{selected_output.get('terminal_rule_id') or 'OPEN'}",
            "",
            "自然语言分支选择",
        ]
        natural_branches = branch.get("natural_language", [])
        branch_lines.extend(
            f"{index}. {text}"
            for index, text in enumerate(natural_branches, start=1)
        )
        predicate_rows = branch.get(
            "leading_candidate_predicate_branches", []
        )
        if predicate_rows:
            branch_lines.extend(["", "首位候选判断节点"])
            branch_lines.extend(
                f"- {item.get('branch_narrative') or item.get('predicate_id')}"
                for item in predicate_rows
                if isinstance(item, Mapping)
            )
        components = card.get("component_selections", [])
        if components:
            branch_lines.extend(["", "连接口小部件选择"])
            branch_lines.extend(
                f"- {item.get('branch_narrative')}"
                for item in components
                if isinstance(item, Mapping)
            )
        calculation_branches = branch.get("calculation_branches", [])
        if calculation_branches:
            branch_lines.extend(["", "计算分支执行状态"])
            branch_lines.extend(
                f"- {item.get('branch_narrative')}"
                for item in calculation_branches
                if isinstance(item, Mapping)
            )
        branch_lines.extend([
            "",
            "机器分支账本 SHA-256",
            str(branch.get("branch_output_sha256") or "OPEN"),
        ])
        _set_text(self.branch_output_text, "\n".join(branch_lines))

        llm_control = (
            card.get("llm_control_result")
            if isinstance(card.get("llm_control_result"), Mapping)
            else {}
        )
        llm_lines = [
            f"状态：{llm_control.get('status') or 'NOT_REQUESTED'}",
            (
                "模型："
                f"{llm_control.get('provider') or '—'} / "
                f"{llm_control.get('model') or '—'}"
            ),
            f"注入点：{llm_control.get('injection_point') or '—'}",
            f"是否实际改变重算输入：{llm_control.get('llm_changed_active_inputs') is True}",
            "",
            "自然语言结果",
        ]
        llm_lines.extend(
            f"- {text}"
            for text in llm_control.get("natural_language", [])
        )
        llm_lines.extend([
            "",
            "大模型组织后的输出块（按模型给出的顺序）",
            json.dumps(
                llm_control.get("organized_output_blocks", []),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "",
            "LLM 条件判断",
            json.dumps(
                llm_control.get("condition_assessments", []),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "",
            "LLM 补值建议及程序复核",
            json.dumps(
                {
                    "suggestions": llm_control.get(
                        "calculation_assists", []
                    ),
                    "validation": llm_control.get(
                        "calculation_assist_validation", []
                    ),
                    "applied_calculation_inputs": llm_control.get(
                        "applied_calculation_inputs", {}
                    ),
                    "applied_model_estimates": llm_control.get(
                        "applied_model_estimates", {}
                    ),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "",
            "LLM 终选分支建议及程序复核",
            json.dumps(
                {
                    "suggestions": llm_control.get(
                        "terminal_selection_assists", []
                    ),
                    "validation": llm_control.get(
                        "terminal_selection_assist_validation", []
                    ),
                    "applied_overrides": llm_control.get(
                        "applied_terminal_overrides", {}
                    ),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "",
            "LLM 审核发现与回退",
            json.dumps(
                {
                    "audit_findings": llm_control.get(
                        "audit_findings", []
                    ),
                    "fallback_errors": llm_control.get(
                        "fallback_errors", []
                    ),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        ])
        _set_text(self.llm_result_text, "\n".join(llm_lines))

        for item in self.parameter_tree.get_children():
            self.parameter_tree.delete(item)
        for group in card.get("parameter_groups", []):
            for row in group.get("rows", []):
                self.parameter_tree.insert("", "end", values=(
                    group.get("title"), row.get("label"), row.get("symbol"),
                    row.get("display_value"), row.get("unit") or "—",
                    result_presentation.code_label(row.get("source", {}).get("kind")),
                    result_presentation.code_label(row.get("state")),
                ))

        for item in self.candidate_tree.get_children():
            self.candidate_tree.delete(item)
        for candidate in card.get("candidates", []):
            predicates = candidate.get("predicate_summary", {})
            predicate_text = f"{predicates.get('PASS', 0)}/{predicates.get('FAIL', 0)}/{predicates.get('UNKNOWN', 0)}"
            score = candidate.get("ranking_score")
            score_text = f"{float(score):.4f}" if isinstance(score, (int, float)) else "—"
            missing = candidate.get("missing_gates") or candidate.get("completeness", {}).get("missing_fields", [])
            self.candidate_tree.insert("", "end", values=(
                candidate.get("rank"), result_presentation.code_label(candidate.get("candidate_kind")), candidate.get("designation"),
                result_presentation.code_label(candidate.get("status")), score_text, predicate_text, ", ".join(map(str, missing)),
            ))

        checks = card.get("constraint_checks", [])
        non_formula_issues = {key: value for key, value in issues.items() if key != "calculation_notices"}
        issue_lines = [
            "状态轴",
            json.dumps(axes, ensure_ascii=False, indent=2, sort_keys=True),
            "\n大模型工程估算披露",
            json.dumps(card.get("model_estimate_disclosure", {}), ensure_ascii=False, indent=2, sort_keys=True),
            "\n约束校核",
            json.dumps(checks, ensure_ascii=False, indent=2, sort_keys=True),
            "\n缺失、冲突与证据门",
            json.dumps(non_formula_issues, ensure_ascii=False, indent=2, sort_keys=True),
            "\n内置公式提示",
            json.dumps(issues.get("calculation_notices", []), ensure_ascii=False, indent=2, sort_keys=True),
            "\n非标/多台组合修改方案",
            json.dumps(card.get("engineering_adjustment_plan", {}), ensure_ascii=False, indent=2, sort_keys=True),
            "\nAgent 计算后选型控制",
            json.dumps(card.get("selection_agent_control", {}), ensure_ascii=False, indent=2, sort_keys=True),
            f"\n正式型号门\n{card.get('formal_model_gate') or '—'}",
            f"\n禁止声称\n{card.get('prohibited_claim') or '—'}",
        ]
        _set_text(self.issue_text, "\n".join(issue_lines))

        equations: list[str] = []
        notices: list[dict[str, Any]] = []
        for item in card.get("calculation_chain", []):
            if item.get("equation_chain"):
                notice = item.get("calculation_notice") if isinstance(item.get("calculation_notice"), dict) else None
                if notice:
                    notices.append(notice)
                    release_class = str(notice.get("release_class", "A"))
                    result_status = "暂定初筛" if notice.get("result_status") == "PROVISIONAL" else "公式推导"
                    equations.append(
                        f"⚠ {release_class} 类 · {result_status} · {notice.get('title', '内置公式')}\n"
                        + _pretty_equation(item)
                        + "\n"
                        + _formula_trace_text(item)
                    )
                else:
                    equations.append(
                        _pretty_equation(item)
                        + "\n"
                        + _formula_trace_text(item)
                    )
            else:
                missing = ", ".join(item.get("missing_fields", []))
                suffix = f"；缺少 {missing}" if missing else ""
                calc_id = str(item.get("calculation_id", "calculation"))
                title = EQUATION_META.get(calc_id, (calc_id, ""))[0]
                status = result_presentation.code_label(item.get("status", "未执行"))
                equations.append(f"{title}：{status}{suffix}")
        if notices:
            provisional_count = sum(1 for notice in notices if notice.get("result_status") == "PROVISIONAL")
            self.formula_notice_var.set(
                f"⚠ {len(notices)} 项由软件内置公式生成，并非 Aspen / 用户直接值；"
                f"其中 {provisional_count} 项仅供暂定初筛。公式页已显示输入、出处、代码与双哈希。"
            )
            help_blocks = []
            for notice in notices:
                does_not_prove = "、".join(map(str, notice.get("does_not_prove", []))) or "未列明"
                source_bindings = notice.get("source_bindings", [])
                source_text = "；".join(
                    f"{item.get('reference')}[{item.get('binding_status')}]"
                    for item in source_bindings
                    if isinstance(item, dict)
                ) or "未登记"
                gaps = "；".join(
                    map(str, notice.get("open_traceability_gaps", []))
                ) or "无"
                help_blocks.append(
                    f"{notice.get('title', '内置公式')}\n"
                    f"公式 ID：{notice.get('formula_id', 'OPEN')}\n"
                    f"状态：{notice.get('release_class', 'A')} 类 / {notice.get('result_status', 'DERIVED')}\n"
                    f"说明：{notice.get('message', '')}\n"
                    f"适用：{notice.get('applicability', '')}\n"
                    f"不能证明：{does_not_prove}\n"
                    f"公式来源：{source_text}\n"
                    f"公式定义 SHA-256：{notice.get('formula_definition_sha256', 'OPEN')}\n"
                    f"本次计算 SHA-256：{notice.get('calculation_trace_sha256', 'OPEN')}\n"
                    f"追溯缺口：{gaps}"
                )
            self._formula_help_text = "\n\n".join(help_blocks)
        else:
            self.formula_notice_var.set("当前设备没有已执行的内置公式；缺少的输入只影响相应计算。")
            self._formula_help_text = "内置公式未执行时，程序不会填默认效率、默认密度、默认流速或默认材料。"
        _set_text(self.equation_text, "\n\n".join(equations) if equations else "当前没有可闭合的计算链。")
        organized = result_presentation.build_organized_answer({
            "schema": "equipment-design-presentation-v1",
            "equipment": [dict(card)],
            "deterministic": True,
            "llm_used": bool(card.get("llm_used")),
        })
        _set_text(
            self.organized_answer_text,
            result_presentation.render_organized_markdown(
                organized
            ),
        )

    def _render_result(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            payload = {"value": payload}
        self.last_result = payload
        self.presentation = result_presentation.build_presentation(payload)
        self._current_match_results = (
            result_presentation.extract_match_results(payload)
        )
        cards = self.presentation.get("equipment", [])
        self._presentation_by_label: dict[str, dict[str, Any]] = {}
        self._presentation_label_by_id: dict[str, str] = {}
        labels: list[str] = []
        for index, card in enumerate(cards):
            header = card.get("header", {})
            if index < len(self._current_match_results):
                match_result = self._current_match_results[index]
                result_hash = str(
                    header.get("source_result_sha256")
                    or derivation_workbench.canonical_sha256(
                        match_result
                    )
                )
                baseline_hash = (
                    self.derivation_result_to_baseline.get(
                        result_hash,
                        result_hash,
                    )
                )
                session = self.derivation_sessions.get(
                    baseline_hash
                )
                if session is None:
                    selection_id = (
                        self.last_manual.get("selection_id")
                        if (
                            len(cards) == 1
                            and isinstance(self.last_manual, dict)
                        )
                        else None
                    )
                    session = {
                        "baseline_result": match_result,
                        "current_result": match_result,
                        "selection_id": selection_id,
                        "overrides": {},
                    }
                    self.derivation_sessions[
                        baseline_hash
                    ] = session
                    self.derivation_result_to_baseline[
                        result_hash
                    ] = baseline_hash
                else:
                    session["current_result"] = match_result
            label = f"{card.get('equipment_id') or index + 1} · {header.get('recommended_type') or header.get('family_name') or header.get('family_id') or '设备'}"
            labels.append(label)
            self._presentation_by_label[label] = card
            self._presentation_label_by_id[str(card.get("equipment_id") or "")] = label
        self.result_device_combo.configure(values=labels or ["—"])
        self.result_device_var.set(labels[0] if labels else "—")
        if cards:
            self._render_selected_presentation()
        else:
            self.summary_vars["状态"].set(str(payload.get("status", "—")))
            self.summary_vars["设备族"].set("—")
            self.summary_vars["型号状态"].set("—")
            self.summary_vars["待闭合"].set("0")
            _set_text(self.equation_text, "当前结果不含确定性设备参数包。")
            _set_text(self.issue_text, "当前结果不含可展示的设备参数、候选或证据门。")
            _set_text(
                self.branch_output_text,
                "当前结果不含可展示的程序分支选择。",
            )
            _set_text(
                self.llm_result_text,
                "当前结果不含可展示的大模型调控记录。",
            )
            _set_text(
                self.organized_answer_text,
                "当前结果不含可由 Agent 组织的确定性设备事实。",
            )
        _set_text(self.raw_text, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))

    def _save_result(self) -> None:
        if not self.last_result:
            messagebox.showwarning("没有结果", "当前没有可保存的结果。", parent=self.root)
            return
        path = filedialog.asksaveasfilename(title="保存设备设计结果", defaultextension=".json", initialfile="equipment_design_result.json", filetypes=[("JSON", "*.json")])
        if path:
            Path(path).write_text(json.dumps(self.last_result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            self.status_var.set(f"已保存：{path}")

    def _export_report(self) -> None:
        if not self.presentation.get("equipment"):
            messagebox.showwarning(
                "没有结果",
                "当前没有可导出的设备报告。",
                parent=self.root,
            )
            return
        path_value = filedialog.asksaveasfilename(
            title="导出设备设计选型报告",
            defaultextension=".html",
            initialfile="设备设计选型报告.html",
            filetypes=[
                ("网页报告（推荐）", "*.html"),
                ("Markdown 报告", "*.md"),
                ("完整 JSON 报告", "*.json"),
            ],
        )
        if not path_value:
            return
        try:
            path = Path(path_value)
            suffix = path.suffix.casefold()
            organized = (
                result_presentation.build_organized_answer(
                    self.presentation
                )
            )
            if suffix in {".md", ".markdown"}:
                report_format = "markdown"
                rendered = (
                    result_presentation.render_organized_markdown(
                        organized
                    )
                )
            elif suffix == ".json":
                report_format = "json"
                rendered = json.dumps(
                    {
                        "presentation": self.presentation,
                        "organized_answer": organized,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ) + "\n"
            else:
                report_format = "html"
                rendered = result_presentation.render_html(
                    self.presentation
                )
            path.write_text(
                rendered,
                encoding="utf-8",
                newline="\n",
            )
            report_sha256 = hashlib.sha256(
                path.read_bytes()
            ).hexdigest().upper()
            manifest = {
                "schema": "equipment-design-report-manifest-v1",
                "format": report_format,
                "report_path": str(path.resolve()),
                "report_sha256": report_sha256,
                "source_payload_sha256": self.presentation.get(
                    "source_payload_sha256"
                ),
                "source_result_sha256": [
                    item.get("header", {}).get(
                        "source_result_sha256"
                    )
                    for item in self.presentation.get(
                        "equipment", []
                    )
                ],
                "organized_answer_sha256": organized.get(
                    "organized_answer_sha256"
                ),
                "program_generated": True,
                "llm_used": bool(self.presentation.get("llm_used")),
            }
            manifest["manifest_sha256"] = hashlib.sha256(
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest().upper()
            manifest_path = path.with_name(
                path.stem + ".manifest.json"
            )
            manifest_path.write_text(
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            self.status_var.set(
                f"报告已导出：{path}（附带哈希清单）"
            )
            messagebox.showinfo(
                "导出完成",
                (
                    f"报告：{path}\n"
                    f"哈希清单：{manifest_path}\n"
                    f"SHA-256：{report_sha256}"
                ),
                parent=self.root,
            )
        except Exception as exc:
            messagebox.showerror(
                "报告导出失败",
                str(exc),
                parent=self.root,
            )

    def _open_session(self) -> None:
        if self.session_dir:
            response = self.api.open_folder(self.session_dir)
            if not response.get("ok"):
                messagebox.showerror("打开失败", response.get("error", "未知错误"), parent=self.root)


def run_tk_gui(api: Any, core: Any) -> int:
    root = tk.Tk()
    EquipmentDesignTkApp(root, api, core)
    root.mainloop()
    return 0
