from __future__ import annotations

"""Reproducible safety audit for the GUI-imported LLM bridge.

Safe defaults and examples::

    python scripts/audit_llm_multiflow_bridge.py
    python scripts/audit_llm_multiflow_bridge.py --remote
    python scripts/audit_llm_multiflow_bridge.py --remote --key-stdin
    python scripts/audit_llm_multiflow_bridge.py --local-aspen
    python scripts/audit_llm_multiflow_bridge.py --output audit.json

The default run is local and uses six synthetic representative duties.  A
remote run is opt-in, is restricted to ``https://api.deepseek.com``, refuses
redirects, and never includes local Aspen exports.  ``--local-aspen`` is an
explicit local-only extension.  API keys are accepted only through getpass or
one stdin line; there is deliberately no command-line, environment-variable,
configuration-file, or report field for a key.
"""

import argparse
import getpass
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PACKAGE_ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import app_core  # noqa: E402
import aspen_equipment_derivation  # noqa: E402
import llm_bridge  # noqa: E402
from equipment_design_app import EquipmentDesignApi  # noqa: E402


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_HOST = "api.deepseek.com"
MODEL_PREFERENCE = (
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-chat",
    "deepseek-reasoner",
)
GUI_DEFAULT_TASK = (
    "审核当前确定性结果；若候选证据足以唯一化，可提出白名单内的草稿决策，"
    "否则保留最泛用类型。"
)


SYNTHETIC_CASES: tuple[dict[str, Any], ...] = (
    {
        "case_id": "pump_liquid_boost",
        "label": "液体增压泵",
        "selection_id": "block:PUMP",
        "values": {
            "equipment_tag": "P-GUI-101",
            "aspen_block_type": "PUMP",
            "process_function": "clean liquid pressure boosting",
            "phase": "liquid",
            "pressure_basis": "absolute",
            "flow_m3_h": 36.0,
            "head_m": 45.0,
            "density_kg_m3": 850.0,
            "efficiency_percent": 72.0,
            "npsha_m": 5.2,
            "npshr_m": 3.1,
            "npshr_evidence_scope": "same_duty_vendor_curve",
            "design_temperature_c": 80.0,
            "material": "S30408",
        },
    },
    {
        "case_id": "shell_tube_exchanger",
        "label": "管壳式换热器",
        "selection_id": "block:HEATX",
        "values": {
            "equipment_tag": "E-GUI-301",
            "aspen_block_type": "HEATX",
            "process_function": "clean liquid-liquid heat exchange",
            "heat_duty_kw": 800.0,
            "overall_u_w_m2k": 650.0,
            "lmtd_k": 32.0,
            "lmtd_correction_factor": 0.9,
            "design_pressure_mpa": 1.6,
            "design_pressure_basis": "gauge",
            "design_temperature_c": 180.0,
            "material": "S30408",
        },
    },
    {
        "case_id": "vacuum_distillation_tower",
        "label": "真空精馏塔",
        "selection_id": "block:RADFRAC",
        "values": {
            "equipment_tag": "T-GUI-201",
            "aspen_block_type": "RADFRAC",
            "process_function": (
                "vacuum distillation; low pressure drop; clean non-fouling service"
            ),
            "inner_diameter_mm": 1200.0,
            "height_mm": 18000.0,
            "stage_count": 40,
            "design_pressure_mpa": 0.6,
            "design_pressure_basis": "gauge",
            "design_temperature_c": 140.0,
            "material": "S30408",
        },
    },
    {
        "case_id": "gas_compressor",
        "label": "气体压缩机",
        "selection_id": "block:COMPR",
        "values": {
            "equipment_tag": "K-GUI-401",
            "aspen_block_type": "COMPR",
            "process_function": "continuous gas compression",
            "phase": "vapor",
            "pressure_basis": "absolute",
            "flow_m3_h": 2400.0,
            "inlet_pressure_mpa": 0.12,
            "outlet_pressure_mpa": 0.72,
            "inlet_temperature_c": 35.0,
            "gas_molecular_weight": 28.4,
            "compressibility_factor": 0.96,
            "heat_capacity_ratio_k": 1.3,
            "efficiency_percent": 76.0,
            "driver_efficiency_percent": 95.0,
            "design_temperature_c": 100.0,
        },
    },
    {
        "case_id": "process_pipe",
        "label": "液体工艺管道",
        "selection_id": "family:family_process_piping",
        "values": {
            "equipment_tag": "PL-GUI-501",
            "equipment_type": "工艺管道",
            "main_medium": "water",
            "phase": "liquid",
            "flow_m3_h": 100.0,
            "density_kg_m3": 997.0,
            "dynamic_viscosity_mpa_s": 0.89,
            "target_velocity_m_s": 1.8,
            "design_pressure_mpa": 2.5,
            "design_pressure_basis": "gauge",
            "design_temperature_c": 120.0,
            "material": "S30408",
        },
    },
    {
        "case_id": "flow_control_valve",
        "label": "流量调节阀",
        "selection_id": "block:VALVE",
        "values": {
            "equipment_tag": "CV-GUI-601",
            "aspen_block_type": "VALVE",
            "process_function": "liquid flow control",
            "phase": "liquid",
            "flow_m3_h": 25.0,
            "density_kg_m3": 900.0,
            "pressure_drop_kpa": 180.0,
            "selected_dn": 50,
            "pressure_class": "PN25",
            "design_pressure_mpa": 2.5,
            "design_pressure_basis": "gauge",
            "design_temperature_c": 100.0,
            "material": "S30408",
            "valve_function": "flow_control",
        },
    },
)


LOCAL_ASPEN_CASE_SPECS: tuple[dict[str, str], ...] = (
    {
        "case_id": "aspen_exercise2_4_pump",
        "label": "Aspen EXERCISE2-4 泵节点",
        "relative_path": (
            "outputs/real_bkp_stage1_20260723/"
            "exercise2_4_augmented_run/aspen_equipment_export.json"
        ),
        "equipment_tag": "PUMP",
        "selection_id": "block:PUMP",
    },
    {
        "case_id": "aspen_mch_radfrac",
        "label": "Aspen MCH 精馏塔节点",
        "relative_path": (
            "outputs/real_bkp_stage1_20260723/"
            "mch_com_rerun_stage1_v1_8_elevated/aspen_equipment_export.json"
        ),
        "equipment_tag": "B1",
        "selection_id": "block:RADFRAC",
    },
)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="审计 GUI 导入的 DeepSeek 设备协同链（默认离线六案例）。"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--remote",
        action="store_true",
        help="连接 DeepSeek 官方 API；远程始终只发送六个合成工况。",
    )
    mode.add_argument(
        "--local-aspen",
        action="store_true",
        help="仅在本机额外回放两个 Aspen 导出案例，绝不启用远程模型。",
    )
    parser.add_argument(
        "--key-stdin",
        action="store_true",
        help="从标准输入读取一行 Key；默认使用不回显的 getpass。仅可与 --remote 同用。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="把脱敏后的完整审计结果写入 JSON 文件。",
    )
    parser.add_argument(
        "--tk-smoke",
        action="store_true",
        help="在远程六案例后追加隐藏 Tk 窗口的真实 GUI 链冒烟测试。",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if args.key_stdin and not args.remote:
        parser.error("--key-stdin 只能与 --remote 同用。")
    if args.tk_smoke and not args.remote:
        parser.error("--tk-smoke 会实际调用模型，只能与 --remote 同用。")
    return args


def _assert_deepseek_endpoint(url: str, *, base_only: bool = False) -> str:
    parsed = urllib.parse.urlsplit(str(url).strip())
    if parsed.scheme != "https":
        raise ValueError("认证请求只允许 HTTPS。")
    if parsed.hostname != DEEPSEEK_HOST:
        raise ValueError("认证请求只允许发送至 api.deepseek.com。")
    if parsed.username or parsed.password:
        raise ValueError("认证请求 URL 不允许包含用户信息。")
    if parsed.port not in (None, 443):
        raise ValueError("认证请求只允许使用默认 HTTPS 端口。")
    if parsed.query or parsed.fragment:
        raise ValueError("认证请求 URL 不允许包含查询参数或片段。")
    if base_only and parsed.path not in ("", "/"):
        raise ValueError("DeepSeek Base URL 必须是官方根地址。")
    return urllib.parse.urlunsplit(parsed)


def read_api_key(*, from_stdin: bool) -> str:
    if from_stdin:
        api_key = sys.stdin.readline().strip()
    else:
        api_key = getpass.getpass("DeepSeek API Key（输入不回显）: ").strip()
    if not api_key:
        raise ValueError("未收到 DeepSeek API Key。")
    return api_key


def request_models(api_key: str, *, timeout_s: int = 30) -> list[str]:
    endpoint = _assert_deepseek_endpoint(f"{DEEPSEEK_BASE_URL}/models")
    request = urllib.request.Request(
        endpoint,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="GET",
    )
    try:
        # The bridge opener deliberately refuses every redirect before urllib
        # can forward the Authorization header to another location.
        with llm_bridge._open_authenticated_request(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", errors="replace")
        raise RuntimeError(
            f"模型目录 HTTP {exc.code}: {redact_text(detail, api_key)}"
        ) from exc
    models = payload.get("data", []) if isinstance(payload, dict) else []
    return sorted(
        str(item.get("id", "")).strip()
        for item in models
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    )


def choose_model(models: Sequence[str]) -> str:
    for candidate in MODEL_PREFERENCE:
        if candidate in models:
            return candidate
    if not models:
        raise RuntimeError("模型目录为空。")
    return str(models[0])


def build_llm_config(*, api_key: str, model: str, enabled: bool) -> dict[str, Any]:
    _assert_deepseek_endpoint(DEEPSEEK_BASE_URL, base_only=True)
    return {
        "enabled": enabled,
        "provider": "openai_compatible",
        "base_url": DEEPSEEK_BASE_URL,
        "model": model,
        "wire_api": "chat_completions",
        "reasoning_effort": "medium",
        "disable_response_storage": True,
        "timeout_s": 180,
        "api_key": api_key,
        "task": GUI_DEFAULT_TASK,
    }


def load_local_aspen_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    missing_paths: list[str] = []
    for spec in LOCAL_ASPEN_CASE_SPECS:
        source_path = PACKAGE_ROOT / spec["relative_path"]
        if not source_path.is_file():
            missing_paths.append(spec["relative_path"])
            continue
        source = json.loads(source_path.read_text(encoding="utf-8"))
        derived = aspen_equipment_derivation.derive_bundle(source, source_path)
        equipment = next(
            (
                item
                for item in derived.get("equipment", [])
                if isinstance(item, dict)
                and str(item.get("aspen_block_id") or item.get("equipment_tag"))
                == spec["equipment_tag"]
            ),
            None,
        )
        if not isinstance(equipment, dict) or not isinstance(
            equipment.get("canonical_match_input"), dict
        ):
            raise RuntimeError(f"Aspen 流程未生成可回放输入：{spec['case_id']}")
        cases.append(
            {
                "case_id": spec["case_id"],
                "label": spec["label"],
                "selection_id": spec["selection_id"],
                "values": equipment["canonical_match_input"],
                "expected_preliminary_acceptance": "PASS",
                "expected_formal_model_incomplete": True,
                "aspen_source_gate": derived.get("formal_use_gate"),
            }
        )
    if missing_paths:
        raise FileNotFoundError(
            "--local-aspen 所需本地导出不存在：" + "、".join(missing_paths)
        )
    return cases


def resolve_cases(*, remote: bool, include_local_aspen: bool) -> list[dict[str, Any]]:
    if remote and include_local_aspen:
        raise ValueError("真实 Aspen 案例禁止发送到远程模型。")
    cases = [dict(case) for case in SYNTHETIC_CASES]
    if include_local_aspen:
        cases.extend(load_local_aspen_cases())
    return cases


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def design_payload(value: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = value.get("result")
    return nested if isinstance(nested, Mapping) else value


def result_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    design = design_payload(value)
    match = design.get("match", {}) if isinstance(design.get("match"), Mapping) else {}
    recommendation = (
        design.get("model_recommendation", {})
        if isinstance(design.get("model_recommendation"), Mapping)
        else {}
    )
    leading = (
        recommendation.get("leading_candidate", {})
        if isinstance(recommendation.get("leading_candidate"), Mapping)
        else {}
    )
    pipe_spec = (
        design.get("programmatic_pipe_specification", {})
        if isinstance(design.get("programmatic_pipe_specification"), Mapping)
        else {}
    )
    designation = (
        leading.get("designation")
        or recommendation.get("recommended_system_designation")
        or pipe_spec.get("designation")
    )
    return {
        "status": design.get("status"),
        "family_id": match.get("family_id") or recommendation.get("family_id"),
        "recommended_type": recommendation.get("recommended_type"),
        "candidate_designation": designation,
        "formal_model_status": recommendation.get("formal_model_status"),
        "formal_model": recommendation.get("formal_model"),
    }


def is_concrete(identity: Mapping[str, Any]) -> bool:
    equipment_type = str(identity.get("recommended_type") or "").strip()
    designation = str(identity.get("candidate_designation") or "").strip()
    forbidden = ("非标准", "non-standard", "unspecified", "unknown")
    combined = f"{equipment_type} {designation}".lower()
    return bool(equipment_type and designation) and not any(
        token.lower() in combined for token in forbidden
    )


def contains_chinese(value: Any) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in str(value or ""))


def default_knowledge_config() -> dict[str, Any]:
    packages = app_core.knowledge_packages().get("packages", [])
    selected = sorted(
        str(item.get("id"))
        for item in packages
        if isinstance(item, dict)
        and item.get("available")
        and item.get("default_selected")
        and item.get("id")
    )
    return {
        "enabled": True,
        "query": "设备选型 公式 证据门 型号状态",
        "package_ids": selected,
        "limit": 8,
    }


def redact_text(value: Any, api_key: str) -> str:
    text = str(value or "")
    return text.replace(api_key, "[REDACTED]") if api_key else text


def redact_sensitive(value: Any, api_key: str) -> Any:
    if isinstance(value, Mapping):
        return {
            redact_text(key, api_key): redact_sensitive(item, api_key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item, api_key) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive(item, api_key) for item in value]
    if isinstance(value, str):
        return redact_text(value, api_key)
    return value


def serialize_redacted(value: Any, api_key: str, *, indent: int | None = 2) -> str:
    sanitized = redact_sensitive(value, api_key)
    serialized = json.dumps(
        sanitized,
        ensure_ascii=False,
        sort_keys=True,
        indent=indent,
    )
    if api_key and api_key in serialized:
        raise RuntimeError("安全检查失败：审计输出意外包含 API Key。")
    return serialized


def write_redacted_report(path: Path, report: Mapping[str, Any], api_key: str) -> None:
    serialized = serialize_redacted(report, api_key)
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialized + "\n", encoding="utf-8")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def run_case(
    api: EquipmentDesignApi,
    case: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    remote: bool,
    api_key: str,
) -> dict[str, Any]:
    source_input = {
        "operation": "manual_match",
        "payload": {
            "selection_id": case["selection_id"],
            "values": case["values"],
        },
    }
    baseline_response = api.manual_match(case["selection_id"], case["values"])
    if baseline_response.get("ok") is not True:
        return {
            "case_id": case["case_id"],
            "label": case["label"],
            "passed": False,
            "failure_stage": "deterministic_baseline",
            "error": redact_text(baseline_response.get("error", ""), api_key),
        }
    baseline = baseline_response["value"]
    hybrid_response = api.agent_hybrid_run(
        source_input,
        dict(config),
        default_knowledge_config(),
        "engineering_choice",
        "minimum",
    )
    api_key_echoed = bool(
        api_key
        and api_key in json.dumps(hybrid_response, ensure_ascii=False, sort_keys=True)
    )
    if hybrid_response.get("ok") is not True:
        return {
            "case_id": case["case_id"],
            "label": case["label"],
            "passed": False,
            "failure_stage": "gui_agent_bridge",
            "error": redact_text(hybrid_response.get("error", ""), api_key),
            "api_key_echoed": api_key_echoed,
        }

    hybrid = _mapping(hybrid_response["value"])
    deterministic = _mapping(hybrid.get("deterministic_result"))
    active = _mapping(hybrid.get("deterministic_recalculation")) or deterministic
    fallback = _mapping(hybrid.get("fallback"))
    review = _mapping(hybrid.get("llm_review"))
    orchestration = _mapping(hybrid.get("orchestration"))
    step_output = _mapping(orchestration.get("step_output"))
    completeness = _mapping(hybrid.get("selection_completeness"))

    baseline_identity = result_identity(_mapping(baseline))
    active_identity = result_identity(active)
    active_design = design_payload(active)
    active_model_recommendation = _mapping(
        active_design.get("model_recommendation")
    )
    formal_promotion_blockers = active_model_recommendation.get(
        "formal_promotion_blockers", []
    )
    if not isinstance(formal_promotion_blockers, list):
        formal_promotion_blockers = []
    formal_model = active_model_recommendation.get("formal_model")
    formal_model_incomplete = bool(not formal_model and formal_promotion_blockers)
    formal_gap_reporting_ok = bool(formal_model or formal_promotion_blockers)
    expected_formal_model_incomplete = bool(
        case.get("expected_formal_model_incomplete", False)
    )
    formal_state_as_expected = bool(
        not expected_formal_model_incomplete or formal_model_incomplete
    )

    terminal_application = _mapping(hybrid.get("terminal_selection_application"))
    registered_terminal_change = "APPLIED" in str(
        terminal_application.get("status", "")
    ).upper()
    same_family = bool(
        baseline_identity.get("family_id")
        and baseline_identity.get("family_id") == active_identity.get("family_id")
    )
    same_or_registered_type = bool(
        baseline_identity.get("recommended_type")
        == active_identity.get("recommended_type")
        or registered_terminal_change
    )
    exact_baseline_preserved = canonical_sha256(baseline) == canonical_sha256(
        deterministic
    )

    strict_completed = review.get("status") == "COMPLETED_STRICT"
    strict_completion_ok = bool(not remote or strict_completed)
    no_fallback = fallback.get("used") is False
    summary = step_output.get("summary")
    summary_present = bool(str(summary or "").strip())
    summary_in_chinese = contains_chinese(summary)
    chinese_summary_ok = bool(not remote or (summary_present and summary_in_chinese))

    preliminary_acceptance = completeness.get("acceptance")
    expected_preliminary_acceptance = case.get(
        "expected_preliminary_acceptance", "PASS"
    )
    preliminary_as_expected = (
        preliminary_acceptance == expected_preliminary_acceptance
    )
    concrete_output = is_concrete(active_identity)

    verified_calculation_inputs = dict(
        _mapping(orchestration.get("verified_calculation_inputs"))
    )
    verified_model_estimates = dict(
        _mapping(orchestration.get("verified_model_estimate_inputs"))
    )
    supplied_names = set(_mapping(case.get("values")))
    proposed_existing_field_overwrites = sorted(
        supplied_names.intersection(verified_calculation_inputs)
        | supplied_names.intersection(verified_model_estimates)
    )
    active_input = _mapping(active.get("input"))
    active_input_overwrites = sorted(
        field
        for field, original in _mapping(case.get("values")).items()
        if field not in active_input or active_input.get(field) != original
    )
    user_field_overwrites = sorted(
        set(proposed_existing_field_overwrites) | set(active_input_overwrites)
    )
    supplied_fields_preserved = not user_field_overwrites

    passed = bool(
        exact_baseline_preserved
        and same_family
        and same_or_registered_type
        and concrete_output
        and supplied_fields_preserved
        and not api_key_echoed
        and preliminary_as_expected
        and formal_state_as_expected
        and formal_gap_reporting_ok
        and strict_completion_ok
        and no_fallback
        and chinese_summary_ok
    )

    validation = orchestration.get("engineering_choice_assist_validation", [])
    if not isinstance(validation, list):
        validation = []
    verified_choices = [
        {
            "assist_id": item.get("assist_id"),
            "axis_id": item.get("axis_id"),
            "choice_id": item.get("choice_id"),
            "status": item.get("status"),
        }
        for item in validation
        if isinstance(item, Mapping)
    ]
    return {
        "case_id": case["case_id"],
        "label": case["label"],
        "passed": passed,
        "machine_state": _mapping(hybrid.get("machine_state")).get("state"),
        "llm_review_status": review.get("status"),
        "strict_completion_required": remote,
        "strict_completion_ok": strict_completion_ok,
        "fallback_used": fallback.get("used"),
        "fallback_check_ok": no_fallback,
        "fallback_errors": [
            {
                "code": item.get("code"),
                "message": redact_text(item.get("message", ""), api_key),
            }
            for item in fallback.get("errors", [])
            if isinstance(item, Mapping)
        ],
        "api_key_echoed": api_key_echoed,
        "deterministic_baseline_sha256": canonical_sha256(baseline),
        "returned_deterministic_sha256": canonical_sha256(deterministic),
        "deterministic_baseline_exactly_preserved": exact_baseline_preserved,
        "same_equipment_family": same_family,
        "same_or_registered_terminal_type": same_or_registered_type,
        "preliminary_selection_acceptance": preliminary_acceptance,
        "expected_preliminary_selection_acceptance": (
            expected_preliminary_acceptance
        ),
        "preliminary_missing_fields": completeness.get(
            "preliminary_missing_fields", []
        ),
        "preliminary_blocking_reasons": completeness.get(
            "blocking_reasons", []
        ),
        "formal_model_incomplete": formal_model_incomplete,
        "expected_formal_model_incomplete": expected_formal_model_incomplete,
        "formal_gap_reporting_ok": formal_gap_reporting_ok,
        "formal_promotion_blocker_count": len(formal_promotion_blockers),
        "formal_promotion_blockers": formal_promotion_blockers,
        "concrete_output": concrete_output,
        "supplied_fields_preserved": supplied_fields_preserved,
        "user_field_overwrites": user_field_overwrites,
        "baseline_identity": baseline_identity,
        "active_identity": active_identity,
        "agent_summary": summary,
        "agent_summary_required": remote,
        "agent_summary_in_chinese": summary_in_chinese,
        "verified_engineering_choices": verified_choices,
        "verified_calculation_inputs": verified_calculation_inputs,
        "verified_model_estimate_inputs": verified_model_estimates,
        "retrieval_hit_count": len(
            _mapping(hybrid.get("knowledge_context")).get("hits", [])
            if isinstance(_mapping(hybrid.get("knowledge_context")).get("hits"), list)
            else []
        ),
    }


def run_tk_smoke(*, api_key: str, model: str) -> dict[str, Any]:
    """Run the real hidden-Tk connection/apply/manual/Agent path.

    Only Tk's thread dispatcher is made synchronous.  The API facade, the
    connection test, the LLM bridge, deterministic calculations, and the Agent
    orchestration remain the production implementations.
    """

    # Imported lazily so default/headless audits do not require a display/Tk.
    import tkinter as tk
    from tkinter import messagebox

    from tk_gui import EquipmentDesignTkApp

    _assert_deepseek_endpoint(DEEPSEEK_BASE_URL, base_only=True)
    root = tk.Tk()
    root.withdraw()
    app: EquipmentDesignTkApp | None = None
    background_responses: list[Any] = []
    original_messages = {
        "showinfo": messagebox.showinfo,
        "showwarning": messagebox.showwarning,
        "showerror": messagebox.showerror,
        "askyesno": messagebox.askyesno,
    }
    try:
        app = EquipmentDesignTkApp(root, EquipmentDesignApi(), app_core)
        root.update_idletasks()

        def synchronous_background(
            _button: Any,
            _busy: str,
            task: Any,
            done: Any,
            **_kwargs: Any,
        ) -> None:
            response = task()
            background_responses.append(response)
            done(response)

        # Popup suppression is required for a non-interactive audit.  The
        # captured calls remain available for failure checks below.
        popup_calls: dict[str, list[tuple[Any, ...]]] = {
            "showinfo": [],
            "showwarning": [],
            "showerror": [],
            "askyesno": [],
        }

        def capture_popup(name: str, default: Any = None) -> Any:
            def callback(*args: Any, **kwargs: Any) -> Any:
                popup_calls[name].append(args)
                return default

            return callback

        app._background = synchronous_background  # type: ignore[method-assign]
        messagebox.showinfo = capture_popup("showinfo")
        messagebox.showwarning = capture_popup("showwarning")
        messagebox.showerror = capture_popup("showerror")
        messagebox.askyesno = capture_popup("askyesno", True)

        app.llm_provider.set("deepseek")
        app._sync_llm_provider()
        app.llm_model.set(model)
        app.llm_key.set(api_key)
        app.llm_timeout.set("180")
        app.llm_wire_api.set("chat_completions")
        app.llm_reasoning_effort.set("high")
        app.llm_disable_response_storage.set(True)
        app.llm_enabled.set(True)
        app.llm_knowledge_enabled.set(True)
        app.llm_injection_point.set("engineering_choice")
        app.llm_context_scope.set("minimum")
        root.update_idletasks()

        app._test_llm_connection()
        connection_ok = bool(
            app._tested_llm_connection_fingerprint
            and app.llm_connection_state.get().startswith("连接状态：成功")
        )
        app._apply_llm_settings()
        applied = _mapping(app._applied_llm_settings)
        applied_config = _mapping(applied.get("config"))
        applied_snapshot_ok = bool(
            app._applied_llm_settings_fingerprint
            and applied_config.get("provider") == "deepseek"
            and applied_config.get("base_url") == DEEPSEEK_BASE_URL
            and applied_config.get("model") == model
            and applied_config.get("api_key") == api_key
            and applied_config.get("wire_api") == "chat_completions"
            and applied_config.get("disable_response_storage") is True
        )

        tower_case = next(
            case
            for case in SYNTHETIC_CASES
            if case["case_id"] == "vacuum_distillation_tower"
        )
        tower_selection = next(
            row
            for row in app.catalog["selections"]
            if row["selection_id"] == tower_case["selection_id"]
        )
        app.manual_selection.set(tower_selection["display_name"])
        app.manual_advanced.set(True)
        app._render_manual_fields()
        missing_widgets = sorted(set(tower_case["values"]) - set(app.field_vars))
        if missing_widgets:
            raise RuntimeError(
                "Tk 手动页未呈现审计字段：" + "、".join(missing_widgets)
            )
        app._fill_manual(dict(tower_case["values"]))
        app._run_manual()

        source_input = _mapping(app.last_source_input)
        source_payload = _mapping(source_input.get("payload"))
        source_values = _mapping(source_payload.get("values"))
        manual_source_ok = bool(
            source_input.get("operation") == "manual_match"
            and source_payload.get("selection_id") == "block:RADFRAC"
            and set(tower_case["values"]).issubset(source_values)
            and all(
                str(source_values.get(field)) == str(expected)
                for field, expected in tower_case["values"].items()
            )
            and app.last_source_context == {"kind": "manual_form"}
        )
        baseline = _mapping(app.last_deterministic_result)
        baseline_identity = result_identity(baseline)
        deterministic_concrete = is_concrete(baseline_identity)

        app._run_llm()
        hybrid_response = _mapping(background_responses[-1])
        hybrid = _mapping(hybrid_response.get("value"))
        review = _mapping(hybrid.get("llm_review"))
        fallback = _mapping(hybrid.get("fallback"))
        deterministic = _mapping(hybrid.get("deterministic_result"))
        active = _mapping(hybrid.get("deterministic_recalculation")) or deterministic
        active_identity = result_identity(active)
        deterministic_hash_ok = bool(
            baseline and canonical_sha256(baseline) == canonical_sha256(deterministic)
        )
        strict_completed = review.get("status") == "COMPLETED_STRICT"
        no_fallback = fallback.get("used") is False
        concrete_output = is_concrete(active_identity)
        terminal_status = str(
            _mapping(hybrid.get("terminal_selection_application")).get("status")
            or ""
        )
        visible_state = "\n".join(
            (
                app.llm_connection_state.get(),
                app.hybrid_state.get(),
                app.status_var.get(),
            )
        )
        key_leaked = bool(
            api_key
            and (
                api_key
                in json.dumps(hybrid_response, ensure_ascii=False, sort_keys=True)
                or api_key in visible_state
            )
        )
        error_popup_count = len(popup_calls["showerror"])
        passed = bool(
            connection_ok
            and applied_snapshot_ok
            and manual_source_ok
            and deterministic_concrete
            and deterministic_hash_ok
            and strict_completed
            and no_fallback
            and concrete_output
            and not key_leaked
            and error_popup_count == 0
        )
        return {
            "schema": "llm-multiflow-tk-smoke-v1",
            "passed": passed,
            "connection_ok": connection_ok,
            "applied_snapshot_ok": applied_snapshot_ok,
            "manual_source_input_ok": manual_source_ok,
            "deterministic_baseline_exactly_preserved": deterministic_hash_ok,
            "llm_review_status": review.get("status"),
            "fallback_used": fallback.get("used"),
            "concrete_output": concrete_output,
            "baseline_identity": baseline_identity,
            "active_identity": active_identity,
            "terminal_selection_status": terminal_status,
            "api_key_echoed": key_leaked,
            "error_popup_count": error_popup_count,
        }
    finally:
        if app is not None:
            app.llm_key.set("")
            app._applied_llm_settings = None
            app._applied_llm_settings_fingerprint = None
        background_responses.clear()
        for name, callback in original_messages.items():
            setattr(messagebox, name, callback)
        try:
            root.destroy()
        except tk.TclError:
            pass


def _connection_summary(response: Mapping[str, Any], api_key: str) -> dict[str, Any]:
    value = _mapping(response.get("value"))
    connected = bool(response.get("ok") is True and value.get("status") == "CONNECTED")
    return {
        "connected": connected,
        "status": value.get("status"),
        "error": redact_text(response.get("error", ""), api_key),
    }


def execute_audit(
    *,
    remote: bool,
    include_local_aspen: bool,
    api_key: str = "",
    include_tk_smoke: bool = False,
) -> tuple[dict[str, Any], int]:
    if remote and not api_key:
        raise ValueError("远程审计必须提供仅驻留内存的 API Key。")
    if include_tk_smoke and not remote:
        raise ValueError("Tk 远程冒烟测试只能在远程审计中运行。")
    cases = resolve_cases(remote=remote, include_local_aspen=include_local_aspen)
    models: list[str] = []
    selected_model = "disabled-local-check"
    connection: dict[str, Any] = {
        "connected": False,
        "status": "NOT_REQUESTED",
        "error": "",
    }
    if remote:
        models = request_models(api_key)
        selected_model = choose_model(models)

    config = build_llm_config(
        api_key=api_key,
        model=selected_model,
        enabled=remote,
    )
    api = EquipmentDesignApi()
    if remote:
        connection = _connection_summary(api.test_llm_connection(config), api_key)
        if not connection["connected"]:
            report = {
                "schema": "llm-multiflow-bridge-audit-v1",
                "mode": "deepseek_remote",
                "security": {
                    "credential_destination": DEEPSEEK_BASE_URL,
                    "redirects_allowed": False,
                    "api_key_source": "getpass_or_stdin_only",
                    "api_key_persisted": False,
                    "api_key_echoed": False,
                    "local_aspen_sent_remote": False,
                },
                "connection": connection,
                "results": [],
                "summary": {
                    "case_count": 0,
                    "passed_count": 0,
                    "failed_case_ids": [],
                    "overall_pass": False,
                },
            }
            return report, 2

    started = time.monotonic()
    # When the caller explicitly asks for the real GUI journey, exercise it
    # before the batch matrix.  This keeps the highest-value acceptance path
    # from being starved by a provider quota/balance limit after earlier cases.
    tk_smoke: dict[str, Any] | None = None
    if include_tk_smoke:
        tk_smoke = run_tk_smoke(api_key=api_key, model=selected_model)

    results: list[dict[str, Any]] = []
    for case in cases:
        case_started = time.monotonic()
        result = run_case(
            api,
            case,
            config,
            remote=remote,
            api_key=api_key,
        )
        result["elapsed_s"] = round(time.monotonic() - case_started, 3)
        results.append(result)

    all_checks = [bool(item.get("passed")) for item in results]
    if tk_smoke is not None:
        all_checks.append(bool(tk_smoke.get("passed")))
    summary = {
        "case_count": len(results),
        "passed_count": sum(1 for item in results if item.get("passed")),
        "failed_case_ids": [
            str(item.get("case_id")) for item in results if not item.get("passed")
        ],
        "tk_smoke_requested": include_tk_smoke,
        "tk_smoke_passed": (
            bool(tk_smoke.get("passed")) if tk_smoke is not None else None
        ),
        "overall_pass": bool(all_checks) and all(all_checks),
        "elapsed_s": round(time.monotonic() - started, 3),
    }
    report = {
        "schema": "llm-multiflow-bridge-audit-v1",
        "mode": "deepseek_remote" if remote else "local_only",
        "case_scope": (
            "synthetic_and_local_aspen"
            if include_local_aspen
            else "synthetic_representative_only"
        ),
        "selected_model": selected_model,
        "available_models": models,
        "gui_defaults_exercised": {
            "provider": "openai_compatible",
            "base_url": DEEPSEEK_BASE_URL,
            "knowledge_enabled": True,
            "injection_point": "engineering_choice",
            "context_scope": "minimum",
            "task": GUI_DEFAULT_TASK,
        },
        "security": {
            "credential_destination": DEEPSEEK_BASE_URL,
            "redirects_allowed": False,
            "api_key_source": "getpass_or_stdin_only",
            "api_key_persisted": False,
            "api_key_echoed": any(item.get("api_key_echoed") for item in results),
            "local_aspen_sent_remote": False,
        },
        "connection": connection,
        "results": results,
        "tk_smoke": tk_smoke,
        "summary": summary,
    }
    return report, 0 if summary["overall_pass"] else 3


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    api_key = ""
    try:
        if args.remote:
            api_key = read_api_key(from_stdin=args.key_stdin)
        report, exit_code = execute_audit(
            remote=args.remote,
            include_local_aspen=args.local_aspen,
            api_key=api_key,
            include_tk_smoke=args.tk_smoke,
        )
    except Exception as exc:
        report = {
            "schema": "llm-multiflow-bridge-audit-v1",
            "summary": {"overall_pass": False},
            "error": redact_text(exc, api_key),
        }
        exit_code = 2

    if args.output:
        write_redacted_report(args.output, report, api_key)
    print(serialize_redacted(report, api_key), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
