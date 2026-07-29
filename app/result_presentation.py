from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any

import customer_delivery


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()


def _is_match_result(value: Any) -> bool:
    return isinstance(value, dict) and (
        value.get("schema") == "equipment-deterministic-match-result-v1"
        or "design_parameter_package" in value
    )


def extract_match_results(payload: Any) -> list[dict[str, Any]]:
    """Extract deterministic equipment results without merging different devices."""
    found: list[dict[str, Any]] = []
    seen: set[int] = set()

    def walk(value: Any) -> None:
        if id(value) in seen:
            return
        if isinstance(value, (dict, list)):
            seen.add(id(value))
        if isinstance(value, dict) and value.get("schema") == "equipment-design-hybrid-result-v2":
            # A hybrid envelope intentionally retains both the initial and the
            # replayed result.  The GUI/report must render the active replay
            # once, not show two cards with the same equipment ID and default
            # to the stale initial card.
            active = value.get("deterministic_recalculation") or value.get("deterministic_result")
            walk(active)
            return
        if _is_match_result(value):
            found.append(value)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        if isinstance(value.get("match_result"), dict):
            walk(value["match_result"])
        for key in (
            "result", "value", "equipment", "items", "deterministic_result",
            "deterministic_recalculation", "llm_applied_draft",
        ):
            if key in value:
                walk(value[key])

    walk(payload)
    return found


def _predicate_summary(candidate: dict[str, Any]) -> dict[str, int]:
    counts = {"PASS": 0, "FAIL": 0, "UNKNOWN": 0}
    for predicate in candidate.get("predicate_trace", []):
        state = str(predicate.get("status", "UNKNOWN")).upper()
        counts[state if state in counts else "UNKNOWN"] += 1
    return counts


_COMPONENT_FAMILY_LABELS = {
    "flange_type": "法兰型式",
    "facing": "法兰密封面",
    "gasket_type": "垫片型式",
    "fastener_type": "紧固件型式",
    "valve_type": "阀门结构型式",
    "pipe_fitting_type": "管件型式",
    "end_connection": "端部连接",
}


def _human_branch_state(value: Any) -> str:
    state = str(value or "UNKNOWN").upper()
    return {
        "PASS": "条件满足，进入该分支",
        "FAIL": "条件不满足，排除该分支",
        "UNKNOWN": "条件不足，保留待核且不得宣称通过",
        "CALCULATED": "该计算分支已执行",
        "BLOCKED": "该计算分支被输入或证据门阻断",
    }.get(state, _code_label(value))


def _compact_known_inputs(values: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    if not isinstance(values, dict):
        return []
    rows: list[dict[str, Any]] = []
    for field_id, value in values.items():
        if value in (None, "", "unknown", "none", "UNKNOWN"):
            continue
        rows.append({"field_id": str(field_id), "value": value})
        if len(rows) >= limit:
            break
    return rows


def _component_branch_narrative(
    connection: dict[str, Any],
    component_family: str,
    selection: dict[str, Any],
) -> str:
    terminal = (
        selection.get("terminal_type")
        if isinstance(selection.get("terminal_type"), dict)
        else {}
    )
    selected_name = terminal.get("name_zh") or terminal.get("code") or "OPEN"
    selected_code = terminal.get("code")
    known = _compact_known_inputs(selection.get("normalized_service_labels"), limit=7)
    condition_text = "、".join(
        f"{item['field_id']}={item['value']}" for item in known
    ) or "没有足够的已知区分条件"
    chain = (
        selection.get("decision_chain")
        if isinstance(selection.get("decision_chain"), dict)
        else {}
    )
    missing = selection.get("minimum_missing_fields", [])
    status = str(selection.get("status") or "UNKNOWN")
    connection_name = (
        f"{connection.get('block_id') or '设备'}"
        f" {connection.get('end_role') or '连接口'}"
        f"（{connection.get('stream_id') or connection.get('connection_id') or '未命名流股'}）"
    )
    score = chain.get("chosen_score")
    rule_counts = (
        len(chain.get("hard_exclusion_rules", [])),
        len(chain.get("mandatory_or_preferred_compatibility", [])),
        len(chain.get("applicability_score_rules", [])),
    )
    selected_text = f"{selected_name}" + (f"（{selected_code}）" if selected_code else "")
    missing_text = (
        "；但仍缺 " + "、".join(map(str, missing)) + "，所以只能作为暂定分支"
        if missing else ""
    )
    return (
        f"{connection_name}的{_COMPONENT_FAMILY_LABELS.get(component_family, component_family)}："
        f"程序读取 {condition_text}；依次执行硬排除 {rule_counts[0]} 条、"
        f"相容性/优选 {rule_counts[1]} 条、适用性评分 {rule_counts[2]} 条，"
        f"按 {chain.get('tie_break') or '登记优先级'} 收敛到 {selected_text}"
        f"（状态 {status}"
        + (f"，得分 {score}" if score is not None else "")
        + f"）{missing_text}。"
    )


def _build_component_selections(result: dict[str, Any]) -> list[dict[str, Any]]:
    package = result.get("connection_component_selections")
    if not isinstance(package, dict):
        package = result.get("_aspen_connection_component_selections")
    if not isinstance(package, dict):
        return []
    rows: list[dict[str, Any]] = []
    for connection in package.get("connections", []):
        if not isinstance(connection, dict):
            continue
        component_types = connection.get("component_types")
        if not isinstance(component_types, dict):
            continue
        for component_family, selection in component_types.items():
            if not isinstance(selection, dict):
                continue
            terminal = (
                selection.get("terminal_type")
                if isinstance(selection.get("terminal_type"), dict)
                else {}
            )
            decision_chain = (
                selection.get("decision_chain")
                if isinstance(selection.get("decision_chain"), dict)
                else {}
            )
            decision_steps: list[dict[str, Any]] = []
            for key, phase_label in (
                ("hard_exclusion_rules", "硬排除"),
                ("mandatory_or_preferred_compatibility", "强制相容/优选"),
                ("applicability_score_rules", "适用性评分"),
            ):
                for rule in decision_chain.get(key, []):
                    decision_steps.append({
                        "phase": phase_label,
                        "rule": rule,
                    })
            rows.append({
                "connection_id": connection.get("connection_id"),
                "stream_id": connection.get("stream_id"),
                "block_id": connection.get("block_id"),
                "end_role": connection.get("end_role"),
                "port_index": connection.get("port_index"),
                "component_family": component_family,
                "component_label": _COMPONENT_FAMILY_LABELS.get(
                    component_family, component_family
                ),
                "selected": {
                    "candidate_id": terminal.get("candidate_id"),
                    "code": terminal.get("code"),
                    "name": terminal.get("name_zh"),
                    "system_series": terminal.get("system_series"),
                },
                "status": selection.get("status"),
                "branch_narrative": _component_branch_narrative(
                    connection, component_family, selection
                ),
                "known_branch_inputs": _compact_known_inputs(
                    selection.get("normalized_service_labels")
                ),
                "decision_steps": decision_steps,
                "decision_chain": decision_chain,
                "hard_excluded_candidates": selection.get(
                    "hard_excluded_candidates", []
                ),
                "minimum_missing_fields": selection.get(
                    "minimum_missing_fields", []
                ),
                "assumptions": selection.get("assumptions", []),
                "warnings": selection.get("warnings", []),
                "source_refs": selection.get("source_refs", []),
                "deterministic": selection.get("deterministic") is True,
                "llm_used": selection.get("llm_used") is True,
            })
    return rows


def _predicate_narrative(predicate: dict[str, Any]) -> str:
    predicate_id = str(predicate.get("predicate_id") or "未命名判断")
    status = str(predicate.get("status") or "UNKNOWN").upper()
    details = []
    for key in (
        "fact", "criterion", "evaluation", "observed_maximum_mpa",
        "standard_limit_mpa_gauge", "comparison_policy",
    ):
        if predicate.get(key) not in (None, ""):
            details.append(f"{key}={predicate[key]}")
    missing = predicate.get("missing_fields", [])
    if missing:
        details.append("缺少=" + "、".join(map(str, missing)))
    suffix = "；".join(details) or "未登记更多数值"
    return f"{predicate_id}：{_human_branch_state(status)}；{suffix}。"


_PROGRAM_SPECIFICATION_LABELS = {
    "programmatic_tower_specification": "塔器专用选型器",
    "programmatic_vessel_separator_specification": "容器/分离器专用选型器",
    "programmatic_reactor_specification": "反应器专用选型器",
    "programmatic_crystallizer_specification": "结晶器专用选型器",
    "programmatic_storage_vessel_specification": "储罐专用选型器",
    "programmatic_auxiliary_specification": "辅助设备专用选型器",
    "programmatic_membrane_package_specification": "膜与成套设备专用选型器",
    "programmatic_turbine_specification": "能量回收透平专用选型器",
}

_PROGRAM_BRANCH_FIELD_LABELS = {
    "terminal_rule_id": "终选规则",
    "terminal_status": "终选状态",
    "terminal_selection_status": "终选状态",
    "selection_basis": "选型依据",
    "default_applied": "是否采用登记默认",
    "recommended_type": "程序选择的具体型式",
    "aspen_block_type": "Aspen 模块类型",
    "internals_branch_id": "塔内件结构分支",
    "packed_tower_branch": "是否进入填料塔分支",
    "material_route_id": "材料路线",
    "diameter_branch": "直径确定分支",
    "height_branch": "高度确定分支",
    "separator_branch_id": "分离器结构分支",
    "orientation": "安装方向",
    "liquid_liquid_branch": "是否进入液液分离分支",
    "three_phase_branch": "是否进入三相分离分支",
    "demister_branch_active": "是否配置除沫器",
    "reactor_branch_id": "反应器结构分支",
    "tubular_branch": "是否进入管式反应器分支",
    "stirred_tank_branch": "是否进入搅拌釜分支",
    "fallback_profile_id": "采用的保底参数包",
    "crystallizer_branch_id": "结晶器结构分支",
    "crystallization_route": "结晶工艺路线",
    "storage_vessel_branch_id": "储罐结构分支",
    "geometry_ratio_name": "几何比名称",
    "geometry_ratio": "几何比取值",
    "auxiliary_branch_id": "辅助设备结构分支",
    "membrane_package_branch_id": "膜/成套设备结构分支",
    "turbine_branch_id": "透平结构分支",
}

_PROGRAM_BRANCH_VALUE_LABELS = {
    "registered_default": "登记默认规则",
    "SINGLE_PASS_SIEVE_TRAY_REGISTERED_DEFAULT": "登记默认的单溢流筛板塔盘",
    "PACKED_TOWER_REGISTERED_250Y_FALLBACK_OR_USER_OVERRIDE": (
        "250Y 规整填料保底或用户覆盖"
    ),
    "Q345R_S30408_GENERAL_TOWER": "Q345R 壳体 + S30408 塔内件通用路线",
    "Q345R_S30408_GENERAL_VESSEL": "Q345R 壳体 + S30408 内件通用路线",
    "volume_flow_divided_by_registered_superficial_velocity": (
        "体积流量÷登记表观气速"
    ),
    "registered_minimum_or_supplied_geometry": "登记最小尺寸或用户给定几何尺寸",
    "stage_count_times_registered_HETP_plus_allowances": (
        "理论级数×登记 HETP，再加顶部/底部余量"
    ),
    "tray_count_times_diameter_conditioned_spacing_plus_allowances": (
        "塔板数×按直径选取的板间距，再加顶部/底部余量"
    ),
    "VERTICAL_GAS_LIQUID_SEPARATOR": "立式气液分离器",
    "TUBULAR_PFR_MINIMUM_OR_VOLUME_CLOSED": "管式平推流反应器，按最小尺寸或体积闭合",
    "STIRRED_TANK_GENERAL_LIQUID_MIXING_FALLBACK": (
        "通用液相混合搅拌釜保底"
    ),
    "CONTINUOUS_DTB_EXTERNAL_COOLING_FALLBACK": "连续 DTB 外冷式结晶器保底",
    "HORIZONTAL_REFLUX_DRUM": "卧式回流罐",
    "VERTICAL_BUFFER_VESSEL": "立式缓冲罐",
    "VERTICAL_PROCESS_VESSEL": "立式工艺容器",
    "VERTICAL_STORAGE_VESSEL": "立式圆筒储罐",
    "CENTRIFUGAL_COMPRESSOR": "离心式压缩机",
    "RECIPROCATING_COMPRESSOR": "往复式压缩机",
    "TOP_ENTRY_PITCHED_BLADE_TURBINE_AGITATOR": "顶入式斜叶涡轮搅拌器",
    "HELICAL_KENICS_STATIC_MIXER": "螺旋元件 Kenics 静态混合器",
    "SPIRAL_WOUND_8040_PA_TFC_ARRAY": "8040 聚酰胺复合膜卷式阵列",
    "AUTOMATIC_RECESSED_CHAMBER_FILTER_PRESS": "自动厢式压滤机",
    "CONTINUOUS_BELT_HOT_AIR_DRYER": "连续带式热风干燥机",
    "TWIN_TOWER_TEMPERATURE_SWING_ADSORPTION_PACKAGE": "双塔变温吸附成套装置",
    "SINGLE_STAGE_RADIAL_PAT_LIQUID_RECOVERY_TURBINE": (
        "单级径向泵反转式液力回收透平"
    ),
    "MULTISTAGE_RADIAL_INFLOW_GAS_EXPANDER": "多级径向流气体膨胀透平",
    "vertical_cylindrical_storage_vessel_preliminary": "立式圆筒储罐预选参数包",
    "centrifugal_compressor_motor_drive_preliminary": "电机驱动离心压缩机预选参数包",
    "reciprocating_compressor_motor_drive_preliminary": "电机驱动往复压缩机预选参数包",
    "top_entry_pitched_blade_agitator_preliminary": "顶入式斜叶搅拌器预选参数包",
    "dn_series_helical_static_mixer_preliminary": "DN 系列螺旋静态混合器预选参数包",
    "spiral_wound_8040_pa_tfc_preliminary": "8040 聚酰胺复合膜卷式组件预选参数包",
    "twin_tower_temperature_swing_adsorption_preliminary": (
        "双塔变温吸附预选参数包"
    ),
    "single_stage_radial_pat_power_recovery_preliminary": (
        "单级径向泵反转式液力回收预选参数包"
    ),
    "multistage_radial_inflow_gas_expander_preliminary": (
        "多级径向流气体膨胀透平预选参数包"
    ),
}


def _program_branch_value_label(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if value in (None, ""):
        return "未提供"
    text = str(value)
    if text in _PROGRAM_BRANCH_VALUE_LABELS:
        return _PROGRAM_BRANCH_VALUE_LABELS[text]
    if text.upper() in {
        "PASS", "FAIL", "UNKNOWN", "CALCULATED", "BLOCKED",
        "DEFAULTED_TERMINAL_TYPE_SELECTED",
        "EXPLICIT_TERMINAL_TYPE_SELECTED",
    }:
        return _code_label(text)
    return text


def _build_programmatic_selection_branches(
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for specification_key, specification in result.items():
        if (
            specification_key not in _PROGRAM_SPECIFICATION_LABELS
            or not isinstance(specification, dict)
        ):
            continue
        selection_branch = specification.get("selection_branch")
        if not isinstance(selection_branch, dict) or not selection_branch:
            continue
        choices = []
        for field_id, raw_value in selection_branch.items():
            if raw_value is None:
                continue
            choices.append({
                "field_id": str(field_id),
                "label": _PROGRAM_BRANCH_FIELD_LABELS.get(
                    str(field_id), str(field_id)
                ),
                "raw_value": raw_value,
                "value_label": _program_branch_value_label(raw_value),
            })
        recommended_type = selection_branch.get("recommended_type")
        choice_text = "；".join(
            f"{item['label']}={item['value_label']} [{item['raw_value']}]"
            if (
                isinstance(item["raw_value"], str)
                and str(item["raw_value"]) != item["value_label"]
            )
            else f"{item['label']}={item['value_label']}"
            for item in choices
            if item["field_id"] != "recommended_type"
        )
        narrative = (
            f"设备专用算法分支：{_PROGRAM_SPECIFICATION_LABELS[specification_key]}"
            f"执行“{choice_text or '未登记细分条件'}”；"
            f"据此选择“{recommended_type or '未登记具体型式'}”。"
            f"原始分支值保留在 {specification_key}.selection_branch，可逐项追溯。"
        )
        rows.append({
            "specification_key": specification_key,
            "specification_label": _PROGRAM_SPECIFICATION_LABELS[
                specification_key
            ],
            "specification_status": specification.get("status"),
            "specification_sha256": specification.get(
                "program_specification_sha256"
            ),
            "recommended_type": recommended_type,
            "choices": choices,
            "selection_branch": selection_branch,
            "branch_narrative": narrative,
            "deterministic": specification.get("deterministic") is True,
            "llm_used": specification.get("llm_used") is True,
        })
    return rows


def _build_branch_selection(
    result: dict[str, Any],
    candidates: list[dict[str, Any]],
    component_selections: list[dict[str, Any]],
) -> dict[str, Any]:
    match = result.get("match", {}) if isinstance(result.get("match"), dict) else {}
    model = (
        result.get("model_recommendation", {})
        if isinstance(result.get("model_recommendation"), dict)
        else {}
    )
    decision = (
        result.get("model_decision", {})
        if isinstance(result.get("model_decision"), dict)
        else {}
    )
    terminal = (
        model.get("terminal_selection")
        if isinstance(model.get("terminal_selection"), dict)
        else {}
    )
    adjustment = (
        result.get("engineering_adjustment_plan", {})
        if isinstance(result.get("engineering_adjustment_plan"), dict)
        else {}
    )
    pump_selection = (
        result.get("pump_engineering_selection", {})
        if isinstance(result.get("pump_engineering_selection"), dict)
        else {}
    )
    programmatic_branches = _build_programmatic_selection_branches(result)
    natural_language: list[str] = []
    family_name = match.get("family_name") or match.get("family_id") or "OPEN"
    family_reasons = "、".join(map(str, match.get("reasons", []))) or "未登记理由"
    natural_language.append(
        f"设备族分支：程序按 {family_reasons} 选择“{family_name}”；"
        f"本次身份状态为 {match.get('status') or result.get('status') or 'UNKNOWN'}。"
    )
    if terminal:
        natural_language.append(
            f"设备型式分支：执行规则 {terminal.get('rule_id') or 'OPEN'}，"
            f"选择“{terminal.get('recommended_type') or model.get('recommended_type') or 'OPEN'}”；"
            f"依据为“{terminal.get('assumption') or terminal.get('selection_basis') or '未登记'}”，"
            f"状态 {_code_label(terminal.get('status'))}。"
        )
    leading = candidates[0] if candidates else {}
    if leading:
        natural_language.append(
            f"规格候选分支：程序完成 {len(candidates)} 个候选的确定性筛选/排序，"
            f"把“{leading.get('designation') or leading.get('candidate_id') or 'OPEN'}”列为首位；"
            f"这一步状态为 {_code_label(leading.get('status'))}，"
            f"不是未验证的厂家正式型号。"
        )
    natural_language.extend(
        item["branch_narrative"] for item in programmatic_branches
    )
    if adjustment:
        if adjustment.get("triggered") is True:
            configuration = (
                adjustment.get("configuration")
                if isinstance(adjustment.get("configuration"), dict)
                else {}
            )
            natural_language.append(
                f"非标/多台分支：触发 {adjustment.get('status') or 'REVIEW'}，"
                f"程序选择 {configuration.get('arrangement_code') or '专项评审方案'}，"
                f"系统标记为“{configuration.get('candidate_model_or_designation') or 'OPEN'}”。"
            )
        else:
            natural_language.append(
                f"非标/多台分支：{adjustment.get('status') or '本次未触发登记阈值'}。"
            )
        if adjustment.get("branch_narrative"):
            natural_language.append(
                "系统构型分支依据："
                + str(adjustment["branch_narrative"])
            )
        for option in adjustment.get(
            "equivalent_recommendations", []
        ):
            if not isinstance(option, dict):
                continue
            natural_language.append(
                f"等价比较方案 {option.get('rank') or '—'}："
                f"{option.get('system_candidate_designation') or 'OPEN'}；"
                f"适用考虑={option.get('suitability') or '未登记'}；"
                "当前只完成算术/总量守恒，"
                "未证明系统曲线或热工水力等价。"
            )
    pump_material = (
        pump_selection.get("material_and_seal", {})
        if isinstance(pump_selection.get("material_and_seal"), dict)
        else {}
    )
    pump_pressure = (
        pump_selection.get("pressure_and_flange", {})
        if isinstance(pump_selection.get("pressure_and_flange"), dict)
        else {}
    )
    if pump_material:
        natural_language.append(
            "泵材料与密封分支："
            + str(
                pump_material.get("selection_narrative")
                or pump_material.get("route_basis")
                or "程序未生成说明"
            )
        )
    if pump_pressure:
        natural_language.append(
            "泵承压与法兰分支：程序按"
            f"{pump_pressure.get('series_units_per_train') or 1}台串联、"
            f"单机关死扬程 {pump_pressure.get('per_unit_shutoff_head_m')} m，"
            "逐级累计关死压力；"
            f"末级最大表压={pump_pressure.get('maximum_final_discharge_pressure_mpa_gauge')} MPa，"
            f"选择 {pump_pressure.get('selected_flange_pressure_class') or 'OPEN'}，"
            f"GB/T 5662 16 bar范围判断={pump_pressure.get('gbt5662_16bar_scope_check', {}).get('status') or 'UNKNOWN'}。"
        )
    predicate_steps: list[dict[str, Any]] = []
    for predicate in leading.get("predicate_trace", []) if isinstance(leading, dict) else []:
        if not isinstance(predicate, dict):
            continue
        predicate_steps.append({
            **predicate,
            "branch_narrative": _predicate_narrative(predicate),
        })
    calculation_branches: list[dict[str, Any]] = []
    for calculation in result.get("calculations", []):
        if not isinstance(calculation, dict):
            continue
        status = str(calculation.get("status") or "UNKNOWN")
        missing = calculation.get("missing_fields", [])
        narrative = (
            f"{calculation.get('calculation_id') or calculation.get('target_field') or '计算'}："
            f"{_human_branch_state(status)}"
            + (
                "；缺少 " + "、".join(map(str, missing))
                if missing else ""
            )
            + "。"
        )
        calculation_branches.append({
            "calculation_id": calculation.get("calculation_id"),
            "target_field": calculation.get("target_field"),
            "status": status,
            "missing_fields": missing,
            "branch_narrative": narrative,
        })
    natural_language.extend(
        item["branch_narrative"] for item in component_selections
    )
    branch = {
        "schema": "equipment-selection-branch-output-v1",
        "program_generated": True,
        "selected_main_equipment": {
            "family_id": match.get("family_id"),
            "family_name": family_name,
            "recommended_type": (
                terminal.get("recommended_type")
                or model.get("recommended_type")
            ),
            "terminal_rule_id": terminal.get("rule_id"),
            "terminal_selection_basis": terminal.get("selection_basis"),
            "model_or_specification": (
                decision.get("generated_system_designation")
                or decision.get("generated_candidate_designation")
                or leading.get("designation")
            ),
            "model_status": decision.get("model_status"),
            "leading_candidate_id": leading.get("candidate_id"),
            "pump_material_and_seal": pump_material.get(
                "selected_components", {}
            ),
            "maximum_final_discharge_pressure_mpa_gauge": (
                pump_pressure.get(
                    "maximum_final_discharge_pressure_mpa_gauge"
                )
            ),
            "selected_flange_pressure_class": pump_pressure.get(
                "selected_flange_pressure_class"
            ),
        },
        "natural_language": natural_language,
        "family_route": {
            "selected_family_id": match.get("family_id"),
            "selected_family_name": match.get("family_name"),
            "status": match.get("status"),
            "reasons": match.get("reasons", []),
            "candidate_families": match.get("candidates", []),
        },
        "terminal_route": terminal,
        "programmatic_selection_branches": programmatic_branches,
        "leading_candidate_predicate_branches": predicate_steps,
        "candidate_comparison": [
            {
                "rank": item.get("rank"),
                "candidate_id": item.get("candidate_id"),
                "designation": item.get("designation"),
                "status": item.get("status"),
                "ranking_score": item.get("ranking_score"),
                "rejection_reasons": item.get(
                    "candidate_rejection_reasons", []
                ),
                "selected_as_leading": index == 0,
            }
            for index, item in enumerate(candidates)
        ],
        "calculation_branches": calculation_branches,
        "engineering_adjustment_branch": adjustment,
        "pump_engineering_selection": pump_selection,
        "component_selections": component_selections,
    }
    branch["branch_output_sha256"] = _canonical_sha256(branch)
    return branch


def _find_hybrid_envelope(payload: Any) -> dict[str, Any] | None:
    seen: set[int] = set()

    def walk(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, (dict, list)):
            return None
        if id(value) in seen:
            return None
        seen.add(id(value))
        if isinstance(value, dict):
            if value.get("schema") == "equipment-design-hybrid-result-v2":
                return value
            if isinstance(value.get("orchestration"), dict) and (
                "machine_state" in value or "llm_review" in value
            ):
                return value
            for key in (
                "value", "result", "hybrid_result", "llm_applied_draft",
                "application",
            ):
                if key in value:
                    found = walk(value[key])
                    if found is not None:
                        return found
            return None
        for item in value:
            found = walk(item)
            if found is not None:
                return found
        return None

    return walk(payload)


def _build_llm_control_result(payload: Any) -> dict[str, Any]:
    if (
        isinstance(payload, dict)
        and payload.get("schema") == "equipment-design-presentation-v1"
        and isinstance(payload.get("llm_control_result"), dict)
    ):
        return dict(payload["llm_control_result"])
    envelope = _find_hybrid_envelope(payload)
    if envelope is None:
        return {
            "schema": "equipment-llm-control-result-v1",
            "status": "NOT_REQUESTED",
            "llm_requested": False,
            "llm_executed": False,
            "llm_changed_active_inputs": False,
            "natural_language": [
                "本次没有调用大模型；所有可见选择和计算均来自程序确定性链。"
            ],
            "model": None,
            "provider": None,
            "step_output": None,
            "condition_assessments": [],
            "calculation_assists": [],
            "calculation_assist_validation": [],
            "terminal_selection_assists": [],
            "terminal_selection_assist_validation": [],
            "engineering_choice_assists": [],
            "engineering_choice_assist_validation": [],
            "audit_findings": [],
            "organized_output_blocks": [],
            "applied_calculation_inputs": {},
            "applied_model_estimates": {},
            "applied_terminal_overrides": {},
            "applied_engineering_choices": {},
            "fallback_errors": [],
        }
    machine_state = (
        envelope.get("machine_state")
        if isinstance(envelope.get("machine_state"), dict)
        else {}
    )
    orchestration = (
        envelope.get("orchestration")
        if isinstance(envelope.get("orchestration"), dict)
        else None
    )
    requested = bool(machine_state.get("llm_requested"))
    executed = orchestration is not None
    fallback = (
        envelope.get("fallback")
        if isinstance(envelope.get("fallback"), dict)
        else {}
    )
    fallback_errors = [
        dict(item) for item in fallback.get("errors", [])
        if isinstance(item, dict)
    ]
    step_output = (
        orchestration.get("step_output")
        if executed and isinstance(orchestration.get("step_output"), dict)
        else {}
    )
    assist_application = (
        envelope.get("calculation_assist_application")
        if isinstance(envelope.get("calculation_assist_application"), dict)
        else {}
    )
    terminal_application = (
        envelope.get("terminal_selection_application")
        if isinstance(envelope.get("terminal_selection_application"), dict)
        else {}
    )
    engineering_choice_application = (
        envelope.get("engineering_choice_application")
        if isinstance(envelope.get("engineering_choice_application"), dict)
        else {}
    )
    applied_calculation_inputs = assist_application.get("applied_inputs", {})
    if not isinstance(applied_calculation_inputs, dict):
        applied_calculation_inputs = {}
    applied_model_estimates = assist_application.get(
        "applied_model_estimate_inputs", {}
    )
    if not isinstance(applied_model_estimates, dict):
        applied_model_estimates = {}
    applied_terminal_overrides = terminal_application.get(
        "applied_overrides", {}
    )
    if not isinstance(applied_terminal_overrides, dict):
        applied_terminal_overrides = {}
    if (
        not applied_terminal_overrides
        and terminal_application.get("applied_rule_id")
    ):
        applied_terminal_overrides = {
            str(
                terminal_application.get(
                    "selection_context_sha256"
                )
                or "active_selection_context"
            ): terminal_application.get("applied_rule_id")
        }
    applied_engineering_choices = engineering_choice_application.get(
        "applied_inputs", {}
    )
    if not isinstance(applied_engineering_choices, dict):
        applied_engineering_choices = {}
    changed = bool(
        applied_calculation_inputs
        or applied_model_estimates
        or applied_terminal_overrides
        or applied_engineering_choices
    )
    natural_language: list[str] = []
    if executed:
        natural_language.append(
            f"大模型已执行：provider={orchestration.get('provider') or '未披露'}，"
            f"model={orchestration.get('model') or '未披露'}；"
            f"模型摘要为“{step_output.get('summary') or '未返回摘要'}”。"
        )
        if changed:
            natural_language.append(
                "程序接受并复核后实际带入重算的内容："
                f"确定性补值 {applied_calculation_inputs or '无'}；"
                f"J/provisional 模型估算 {applied_model_estimates or '无'}；"
                f"终选分支覆盖 {applied_terminal_overrides or '无'}；"
                f"登记材料/零部件选择 {applied_engineering_choices or '无'}。"
            )
        else:
            natural_language.append(
                "大模型结果已展示，但没有任何建议通过程序白名单与复核门后改写当前计算输入或终选分支。"
            )
    elif requested:
        natural_language.append(
            "已请求大模型，但调用、配置或回包校验失败；程序回退并保留确定性结果。"
        )
    else:
        natural_language.append(
            "混合运行已准备上下文，但本次没有请求大模型。"
        )
    for assessment in step_output.get("condition_assessments", []):
        if isinstance(assessment, dict):
            natural_language.append(
                f"LLM条件判断 {assessment.get('condition_id') or '未命名条件'}："
                f"{assessment.get('status') or 'unknown'}；"
                f"{assessment.get('reason') or '未给理由'}。"
            )
    output_composition = (
        orchestration.get("output_composition", {})
        if executed
        and isinstance(orchestration.get("output_composition"), dict)
        else {}
    )
    organized_output_blocks: list[dict[str, Any]] = []
    for block in output_composition.get("blocks", []):
        if not isinstance(block, dict):
            continue
        section_ref = str(block.get("section_ref") or "")
        organized_output_blocks.append({
            "block_id": block.get("block_id"),
            "heading": block.get("heading"),
            "operation": block.get("operation"),
            "section_ref": section_ref,
            "content": step_output.get(section_ref),
            "citations": block.get("citations", []),
        })
    result = {
        "schema": "equipment-llm-control-result-v1",
        "status": (
            "COMPLETED_AND_RECALCULATED"
            if executed and changed
            else "COMPLETED_REVIEW_ONLY"
            if executed
            else "FAILED_FALLBACK"
            if requested
            else "NOT_REQUESTED"
        ),
        "machine_state": machine_state.get("state"),
        "llm_requested": requested,
        "llm_executed": executed,
        "llm_changed_active_inputs": changed,
        "natural_language": natural_language,
        "provider": orchestration.get("provider") if executed else None,
        "model": orchestration.get("model") if executed else None,
        "injection_point": (
            orchestration.get("injection_point") if executed else None
        ),
        "context_scope": (
            orchestration.get("context_scope") if executed else None
        ),
        "context_sha256": (
            orchestration.get("context_sha256") if executed else None
        ),
        "orchestration_sha256": (
            orchestration.get("orchestration_sha256") if executed else None
        ),
        "step_output": step_output if executed else None,
        "condition_assessments": step_output.get(
            "condition_assessments", []
        ),
        "calculation_assists": step_output.get(
            "calculation_assists", []
        ),
        "calculation_assist_validation": (
            orchestration.get("calculation_assist_validation", [])
            if executed else []
        ),
        "terminal_selection_assists": step_output.get(
            "terminal_selection_assists", []
        ),
        "terminal_selection_assist_validation": (
            orchestration.get(
                "terminal_selection_assist_validation", []
            )
            if executed else []
        ),
        "engineering_choice_assists": step_output.get(
            "engineering_choice_assists", []
        ),
        "engineering_choice_assist_validation": (
            orchestration.get("engineering_choice_assist_validation", [])
            if executed else []
        ),
        "ambiguity_decision": step_output.get("ambiguity_decision"),
        "audit_findings": step_output.get("audit_findings", []),
        "output_composition": output_composition,
        "organized_output_blocks": organized_output_blocks,
        "applied_calculation_inputs": applied_calculation_inputs,
        "applied_model_estimates": applied_model_estimates,
        "applied_terminal_overrides": applied_terminal_overrides,
        "applied_engineering_choices": applied_engineering_choices,
        "calculation_assist_application": assist_application,
        "terminal_selection_application": terminal_application,
        "engineering_choice_application": engineering_choice_application,
        "fallback_errors": fallback_errors,
        "deterministic_authority_preserved": True,
    }
    result["llm_control_sha256"] = _canonical_sha256(result)
    return result


def _equipment_card(
    result: dict[str, Any],
    index: int,
    llm_control_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    match = result.get("match", {})
    package = result.get("design_parameter_package", {})
    executed_by_key = {
        (item.get("calculation_id"), item.get("target_field")): item
        for item in result.get("calculations", [])
        if isinstance(item, dict)
    }
    calculation_chain: list[dict[str, Any]] = []
    for packaged_item in package.get("calculation_chain", []):
        if not isinstance(packaged_item, dict):
            continue
        item = dict(packaged_item)
        executed = executed_by_key.get(
            (item.get("calculation_id"), item.get("target_field"))
        )
        if isinstance(executed, dict):
            for field in (
                "formula_trace",
                "calculation_notice",
                "canonical_value",
                "evidence_status",
            ):
                if field in executed:
                    item[field] = executed[field]
        calculation_chain.append(item)
    pump_engineering_selection = (
        result.get("pump_engineering_selection", {})
        if isinstance(result.get("pump_engineering_selection"), dict)
        else {}
    )
    pump_pressure = (
        pump_engineering_selection.get("pressure_and_flange", {})
        if isinstance(
            pump_engineering_selection.get("pressure_and_flange"), dict
        )
        else {}
    )
    pump_target_fields = {
        "pump_per_unit_shutoff_head_screening": "shutoff_head_m",
        "pump_series_final_shutoff_pressure": (
            "maximum_final_discharge_pressure_mpa_gauge"
        ),
        "pump_flange_pressure_class_selection": "pressure_class",
    }
    for raw_item in pump_pressure.get("calculation_chain", []):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        target_field = pump_target_fields.get(
            str(item.get("calculation_id") or ""),
            item.get("target_field"),
        )
        answer = item.get("value")
        if answer not in (None, "") and item.get("unit"):
            answer = f"{answer} {item['unit']}"
        item["target_field"] = target_field
        item["formula_chain"] = {
            "target": target_field,
            "formula": item.get("formula"),
            "substitution": item.get("substitution"),
            "answer": answer,
        }
        item["equation_chain"] = " = ".join(
            str(value)
            for value in (
                item.get("formula"),
                item.get("substitution"),
                answer,
            )
            if value not in (None, "")
        )
        item["calculation_notice"] = {
            "title": "泵串联系统承压与法兰等级程序初筛",
            "applicability": (
                "关死扬程为估算时仅限承压初筛；提供厂家关死扬程后自动重算。"
            ),
            "evidence_class": pump_pressure.get("evidence_class"),
            "promotion_cap": pump_pressure.get("promotion_cap"),
        }
        calculation_chain.append(item)
    model = result.get("model_recommendation", {})
    decision = result.get("model_decision", {})
    normalized = result.get("normalized_input", {})
    candidates: list[dict[str, Any]] = []
    for candidate in model.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        candidate_missing_gates = candidate.get("missing_gates")
        if candidate_missing_gates is None:
            candidate_missing_gates = candidate.get("completeness", {}).get("missing_fields", [])
        candidate_formal_model_gate = candidate.get("formal_model_gate")
        if candidate_formal_model_gate is None:
            candidate_formal_model_gate = model.get("formal_model_gate")
        candidates.append({
            **candidate,
            "predicate_summary": _predicate_summary(candidate),
            "model_status_ceiling": (
                "final_model" if candidate.get("formal_model")
                else "vendor_candidate" if candidate.get("is_vendor_model")
                else "engineering_or_standard_candidate"
            ),
            "missing_gates": candidate_missing_gates,
            "formal_model_gate": candidate_formal_model_gate,
        })
    hard_blockers = [
        item for item in result.get("calculation_pending", [])
        if str(item.get("status", "")).startswith("BLOCKED_")
    ]
    conflicts = list(result.get("normalization_conflicts", []))
    conflicts.extend(result.get("parameter_errors", []))
    progress = result.get("progress", {})
    equipment_id = str(
        normalized.get("equipment_tag")
        or normalized.get("aspen_block_type")
        or f"equipment-{index + 1}"
    )
    model_status = decision.get("model_status") or model.get("formal_model_status") or "unknown"
    terminal_selection = model.get("terminal_selection", {})
    delivery = "READY" if model_status == "final_model" else "NOT_READY"
    customer_bundle = result.get("customer_delivery")
    if not isinstance(customer_bundle, dict):
        try:
            customer_bundle = customer_delivery.build_customer_delivery(result)
        except customer_delivery.CustomerDeliveryError:
            customer_bundle = None
    overview_rows = (
        customer_bundle.get("equipment_overview_table", {}).get("rows", [])
        if isinstance(customer_bundle, dict) else []
    )
    datasheet_rows = (
        customer_bundle.get("equipment_family_datasheet", {}).get("equipment", [])
        if isinstance(customer_bundle, dict) else []
    )
    evidence_records = (
        customer_bundle.get("equipment_evidence_index", {}).get("records", [])
        if isinstance(customer_bundle, dict) else []
    )
    model_estimate_inputs = [
        dict(item) for item in result.get("model_estimate_inputs", [])
        if isinstance(item, dict)
    ]
    engineering_choice_inputs = [
        dict(item) for item in result.get("ai_engineering_choice_inputs", [])
        if isinstance(item, dict)
    ]
    estimate_fields = sorted({
        str(item.get("field_id")) for item in model_estimate_inputs if item.get("field_id")
    })
    applied_estimate_fields = sorted({
        str(item.get("field_id"))
        for item in model_estimate_inputs
        if item.get("field_id") and item.get("auto_applied") is True
        and not str(item.get("state") or "").startswith("SUPERSEDED_")
    })
    superseded_estimate_fields = sorted(set(estimate_fields) - set(applied_estimate_fields))
    model_estimate_disclosure = (
        datasheet_rows[0].get("model_estimate_disclosure")
        if datasheet_rows
        and isinstance(datasheet_rows[0].get("model_estimate_disclosure"), dict)
        else {
            "llm_used": bool(result.get("llm_used")) and bool(model_estimate_inputs),
            "status": "PROVISIONAL_ESTIMATES_USED" if applied_estimate_fields else (
                "MODEL_ESTIMATES_SUPERSEDED" if superseded_estimate_fields else "NOT_USED"
            ),
            "model_estimate_fields": estimate_fields,
            "applied_model_estimate_fields": applied_estimate_fields,
            "superseded_model_estimate_fields": superseded_estimate_fields,
            "estimates": model_estimate_inputs,
            "evidence_class": "J" if model_estimate_inputs else None,
            "promotion_cap": "TYPE_SCREENING" if applied_estimate_fields else None,
            "statement": (
                "含大模型 J/provisional 工程估算；仅限 TYPE_SCREENING，正式选型前必须替换并重算。"
                if model_estimate_inputs else "未使用大模型工程估算。"
            ),
        }
    )
    component_selections = _build_component_selections(result)
    branch_selection = _build_branch_selection(
        result, candidates, component_selections
    )
    llm_control = llm_control_result or _build_llm_control_result(result)
    return {
        "equipment_id": equipment_id,
        "header": {
            "equipment_tag": normalized.get("equipment_tag"),
            "equipment_type": normalized.get("equipment_type"),
            "process_function": normalized.get("process_function"),
            "aspen_block_type": normalized.get("aspen_block_type"),
            "family_id": match.get("family_id"),
            "family_name": match.get("family_name"),
            "recommended_type": model.get("recommended_type"),
            "source_result_sha256": _canonical_sha256(result),
            "engine_version": result.get("engine_version"),
        },
        "status_axes": {
            "identity": result.get("status", "UNKNOWN"),
            "process_basis": result.get("input_provenance", {}).get("status", "UNKNOWN"),
            "calculation": package.get("status_axes", {}).get("calculation", "UNKNOWN"),
            "candidate_matching": package.get("status_axes", {}).get("candidate_matching", "UNKNOWN"),
            "model": model.get("status", "UNKNOWN"),
            "terminal_form": terminal_selection.get("status", "UNKNOWN"),
            "formal_model": model_status,
            "delivery": delivery,
        },
        "parameter_groups": package.get("groups", []),
        "terminal_selection": terminal_selection,
        "selected_output": branch_selection.get(
            "selected_main_equipment", {}
        ),
        "branch_selection": branch_selection,
        "component_selections": component_selections,
        "llm_control_result": llm_control,
        "engineering_adjustment_plan": result.get(
            "engineering_adjustment_plan", {}
        ),
        "pump_engineering_selection": pump_engineering_selection,
        "selection_agent_control": result.get(
            "selection_agent_control", {}
        ),
        "calculation_chain": calculation_chain,
        "constraint_checks": package.get("constraint_checks", []),
        "selection_feature_vector": package.get("selection_feature_vector", {}),
        "candidates": candidates,
        "issues": {
            "hard_blockers": hard_blockers,
            "conflicts": conflicts,
            "ignored_input_diagnostics": result.get("ignored_input_diagnostics", []),
            "ignored_parameter_diagnostics": result.get("ignored_parameter_diagnostics", []),
            "missing_by_goal": progress.get("next_fields", []),
            "minimum_missing_sets": progress.get("minimum_missing_sets", []),
            "evidence_gaps": decision.get("verification_missing_fields", []),
            "formal_promotion_blockers": model.get("formal_promotion_blockers", []),
            "calculation_notices": package.get("calculation_notices", []),
            "design_fallbacks": result.get("design_fallbacks", package.get("design_fallbacks", [])),
            "input_recommendations": result.get("input_recommendations", package.get("input_recommendations", {})),
            "model_estimate_inputs": model_estimate_inputs,
            "ai_engineering_choice_inputs": engineering_choice_inputs,
        },
        "formal_model_gate": model.get("formal_model_gate"),
        "prohibited_claim": model.get("prohibited_claim"),
        "customer_overview": overview_rows[0] if overview_rows else None,
        "customer_datasheet": datasheet_rows[0] if datasheet_rows else None,
        "customer_evidence_records": evidence_records,
        "llm_used": bool(
            llm_control.get("llm_executed")
            or model_estimate_disclosure.get("llm_used")
            or engineering_choice_inputs
        ),
        "model_estimate_inputs": model_estimate_inputs,
        "ai_engineering_choice_inputs": engineering_choice_inputs,
        "model_estimate_disclosure": model_estimate_disclosure,
    }


def build_presentation(payload: Any) -> dict[str, Any]:
    results = extract_match_results(payload)
    llm_control_result = _build_llm_control_result(payload)
    cards = [
        _equipment_card(result, index, llm_control_result)
        for index, result in enumerate(results)
    ]
    llm_used = any(bool(card.get("llm_used")) for card in cards)
    disclosure_equipment = [
        {
            "equipment_id": card.get("equipment_id"),
            **card.get("model_estimate_disclosure", {}),
        }
        for card in cards
        if card.get("model_estimate_disclosure", {}).get("model_estimate_fields")
        or card.get("model_estimate_disclosure", {}).get("equipment")
    ]
    disclosure_status = (
        "PROVISIONAL_ESTIMATES_USED"
        if any(item.get("applied_model_estimate_fields") for item in disclosure_equipment)
        else "MODEL_ESTIMATES_SUPERSEDED"
        if any(item.get("superseded_model_estimate_fields") for item in disclosure_equipment)
        else "NOT_USED"
    )
    return {
        "schema": "equipment-design-presentation-v1",
        "source_payload_sha256": _canonical_sha256(payload),
        "equipment_count": len(cards),
        "equipment": cards,
        "status_color_policy": {
            "green": ["final_model", "same_equipment_verified", "READY"],
            "blue": ["MATCHED", "READY_FOR_CANDIDATE_MATCHING", "type_selected"],
            "yellow": ["PARTIAL", "INCOMPLETE", "NOT_READY", "UNKNOWN"],
            "red": ["BLOCKED", "FAIL", "CONFLICT"],
            "grey": ["NOT_APPLICABLE"],
        },
        "deterministic": True,
        "llm_used": llm_used,
        "llm_control_result": llm_control_result,
        "model_estimate_disclosure": {
            "llm_used": llm_used,
            "status": disclosure_status,
            "equipment": disclosure_equipment,
            "statement": (
                "本展示含已披露的大模型 J/provisional 工程估算；仅限 TYPE_SCREENING，正式选型前必须替换并重算。"
                if llm_used else "本展示未使用大模型工程估算。"
            ),
        },
    }


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return html.escape(str(value))


_CODE_LABELS = {
    "normalized_input": "输入值",
    "deterministic_calculation": "内置公式计算 ⚠",
    "provisional_screening_calculation": "内置初筛公式 ⚠",
    "not_available": "待补充",
    "PROVIDED": "已提供",
    "CALCULATED": "已计算",
    "MISSING": "待补充",
    "EXTERNAL_REQUIRED": "需外部证据",
    "MATCHED": "身份已匹配",
    "AMBIGUOUS": "待消歧",
    "UNMATCHED": "未匹配",
    "BLOCKED_CALCULATION": "计算已阻断",
    "PARTIAL": "部分闭合",
    "READY": "已具备",
    "NOT_READY": "未闭合",
    "type_selected": "型式已确定",
    "EXPLICIT_TERMINAL_TYPE_SELECTED": "已指定型式",
    "CONDITIONED_TERMINAL_TYPE_SELECTED": "条件选定",
    "DEFAULTED_TERMINAL_TYPE_SELECTED": "默认选定",
    "controlled_registered_condition_rule": "登记条件规则",
    "condition_rule": "确定性条件规则",
    "condition_rule_with_registered_default": "条件默认规则",
    "aspen_block_registered_default": "Aspen 模块登记默认",
    "registered_default": "设备族登记默认",
    "same_equipment_verified": "同设备已验证",
    "calculation_blocked": "计算已阻断",
    "custom_equipment_no_universal_model": "非通用定制设备",
    "candidate": "候选",
    "vendor_candidate": "厂家候选",
    "standard_candidate": "标准候选",
    "final_model": "正式型号",
    "NOT_ESTABLISHED_BY_MATCHER_INPUT": "流程依据待核",
    "READY_FOR_CANDIDATE_MATCHING": "可执行候选匹配",
    "STANDARD_MARKING_CANDIDATES": "标准标记候选",
    "standard_marking": "标准规格",
    "engineered_designation": "具体工程型式（非厂家型号）",
    "component_marking": "具体部件规格（非厂家型号）",
    "generic_type_placeholder": "旧版泛型占位（不合格）",
    "NEAR_STANDARD_DESIGN_POINT": "旧版近标准设计点（已停用）",
    "HEURISTIC_NEAREST_STANDARD_REFERENCE_POINT": "启发式最近标准参考点（非性能曲线适配）",
    "RANKED_STANDARD_REFERENCE_POINT": "标准参考点",
    "ENGINEERING_CANDIDATE_READY": "工程候选已就绪",
    "PARTIAL_ENGINEERING_CANDIDATE": "工程候选待补充",
    "SATISFIED_BY_PROVIDED_VALUE": "已采用输入值",
    "ENGINEERING_SPEC_CANDIDATES": "工程规格候选",
    "VENDOR_CANDIDATES": "厂家型号候选",
    "WAITING_CALCULATED_PARAMETERS": "等待参数闭合",
    "CALCULATED_WITH_EXPLICIT_INPUTS": "已按显式输入计算",
    "PROVISIONAL_SCREENING_CROSSCHECK_PASS": "初筛交叉核对一致",
    "PROVISIONAL_SCREENING_DIFFERENCE": "初筛值与提供值不同",
    "WARNING_PROVISIONAL_SCREENING_DIFFERENCE": "初筛差异（不覆盖提供值）",
    "MISSING_INPUTS": "缺少输入",
    "PASS": "通过",
    "BLOCKED": "未通过",
    "COMPLETE": "完整",
    "PROVISIONAL_WITH_VISIBLE_GAPS": "有明确缺项",
    "NOT_APPLICABLE": "不适用（流程逻辑节点）",
    "NOT_APPLICABLE_SIMULATION_LOGIC_NODE": "Aspen 流程逻辑节点不对应独立设备型号",
    "RECOMMENDED_ALGORITHMIC_MODIFICATION": "程序已给出非标/多台组合修改方案",
    "REVIEW_REQUIRED_NO_SAFE_AUTOMATIC_CONFIGURATION": "程序已给出评审路线，尚无安全自动配置",
    "NOT_TRIGGERED_WITHIN_SCREENING_POLICY": "未触发程序非标拆分阈值",
    "CONTROLLED_WITH_ADJUSTMENT_WARNING": "已执行计算后选型并强制显示修改警告",
    "CONTROLLED_CALCULATE_THEN_SELECT": "已执行计算后选型控制",
    "algorithmic_modification_screening_only": "算法多台/非标方案（仅初筛）",
    "algorithmic_configuration_review_required": "算法配置需专项评审",
}

_AXIS_LABELS = {
    "identity": "设备身份",
    "process_basis": "流程依据",
    "calculation": "计算闭合",
    "candidate_matching": "候选匹配",
    "model": "型号状态",
    "terminal_form": "终选型式来源",
    "formal_model": "正式状态",
    "delivery": "交付状态",
}


# One shared, user-facing contract is consumed by both the HTML report and the
# desktop GUI.  Keeping this list here prevents either view from silently
# reducing the authoritative overview to a handful of convenient fields.
CUSTOMER_OVERVIEW_DISPLAY_FIELDS = (
    ("sequence_number", "序号"),
    ("process_section", "工艺段 / 装置"),
    ("equipment_tag", "设备位号 / 管线号"),
    ("equipment_name", "设备名称"),
    ("quantity_and_standby", "数量及备用"),
    ("equipment_type", "型式 / 结构"),
    ("model_or_specification", "型号 / 工程规格"),
    ("model_or_specification_status", "型号状态"),
    ("engineering_adjustment_status", "非标/多台修改状态"),
    ("engineering_adjustment_plan", "程序修改方案"),
    ("algorithmic_selection_warning", "算法选型强制警告"),
    ("selection_agent_control_status", "Agent计算后选型控制"),
    ("authority_structural_completeness", "权威表结构完整性"),
    ("authority_information_coverage", "权威表信息覆盖"),
    ("customer_information_coverage", "客户信息覆盖"),
    ("selection_specificity_gate", "具体选型门"),
    ("formal_readiness_gate", "正式就绪门"),
    ("standards_and_versions", "采用标准及版本"),
    ("evidence_ids", "计算书 / 软件 / 厂家证据号"),
    ("evidence_level", "证据等级"),
    ("customer_table_missing_fields", "客户表缺项"),
    ("algorithm_evidence_missing_fields", "算法 / 证据门缺项"),
    ("model_estimate_disclosure", "大模型工程估算披露"),
    ("delivery_state", "交付状态"),
)


def _code_label(value: Any) -> str:
    text = str(value or "")
    return _CODE_LABELS.get(text, text.replace("_", " "))


def code_label(value: Any) -> str:
    """Return a compact human label while preserving canonical codes in JSON."""
    return _code_label(value)


def _overview_gate_display(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    parts = [f"状态：{_code_label(value.get('state') or 'UNKNOWN')}"]
    required = value.get("required")
    covered = value.get("covered")
    emitted = value.get("emitted")
    resolved = value.get("resolved_fields")
    if isinstance(required, int) and isinstance(covered, int):
        parts.append(f"已覆盖 {covered}/{required}")
    elif isinstance(required, int) and isinstance(emitted, int):
        parts.append(f"已输出 {emitted}/{required}")
    if isinstance(resolved, list) and resolved:
        parts.append("已解析：" + "、".join(map(str, resolved)))
    for key, label in (
        ("blocking_fields", "待补字段"),
        ("blocking_reasons", "阻断原因"),
        ("explicit_open_gate_fields", "显式开放门"),
    ):
        entries = value.get(key)
        if isinstance(entries, list) and entries:
            parts.append(f"{label}：" + "、".join(map(str, entries)))
    if value.get("model_status"):
        parts.append(f"型号状态：{_code_label(value.get('model_status'))}")
    return "；".join(parts)


def customer_overview_display_rows(overview: Any) -> list[dict[str, Any]]:
    """Return the shared, backwards-compatible authoritative overview rows."""
    source = overview if isinstance(overview, dict) else {}
    gate_fields = {
        "authority_structural_completeness",
        "authority_information_coverage",
        "customer_information_coverage",
        "selection_specificity_gate",
        "formal_readiness_gate",
    }
    code_fields = {"model_or_specification_status", "delivery_state"}
    rows: list[dict[str, Any]] = []
    for field_id, label in CUSTOMER_OVERVIEW_DISPLAY_FIELDS:
        value = source.get(field_id)
        if field_id in gate_fields:
            display_value = _overview_gate_display(value)
        elif field_id in code_fields:
            display_value = _code_label(value)
        else:
            display_value = value
        rows.append({
            "field_id": field_id,
            "label": label,
            "value": display_value,
        })
    return rows


def _pretty_unit(value: Any) -> str:
    text = str(value or "—")
    return (
        text.replace("m3", "m³")
        .replace("m2", "m²")
        .replace("degC", "°C")
        .replace("kg/m³", "kg·m⁻³")
        .replace("W/m²/K", "W·m⁻²·K⁻¹")
    )


def _pretty_number_html(row: dict[str, Any]) -> str:
    raw = row.get("raw_value")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return _cell(row.get("display_value"))
    value = float(raw)
    if value == 0:
        return "0"
    rendered = format(value, ".5g")
    if "e" not in rendered.casefold():
        return html.escape(rendered)
    mantissa, exponent = re.split("[eE]", rendered, maxsplit=1)
    return f"{html.escape(mantissa)} × 10<sup>{int(exponent)}</sup>"


def _symbol_html(value: Any) -> str:
    symbol = str(value or "")
    known = {
        "Ph": "P<sub>h</sub>",
        "Ps": "P<sub>s</sub>",
        "Pd": "P<sub>d</sub>",
        "Pin": "P<sub>in</sub>",
        "Pout": "P<sub>out</sub>",
        "NPSHa": "NPSH<sub>a</sub>",
        "NPSHr": "NPSH<sub>r</sub>",
        "rho": "ρ",
        "eta": "η",
    }
    return known.get(symbol, html.escape(symbol))


def _math_html(value: Any) -> str:
    text = html.escape(str(value or ""))
    for token, replacement in (
        ("NPSHa", "NPSH<sub>a</sub>"),
        ("NPSHr", "NPSH<sub>r</sub>"),
        ("Pout", "P<sub>out</sub>"),
        ("Pin", "P<sub>in</sub>"),
        ("rho", "ρ"),
        ("eta", "η"),
    ):
        text = text.replace(token, replacement)
    text = re.sub(r"\^(-?\d+(?:\.\d+)?)", r"<sup>\1</sup>", text)
    return text.replace("*", " · ")


def _equation_html(item: dict[str, Any], symbols: dict[str, str]) -> str:
    chain = item.get("formula_chain")
    if not isinstance(chain, dict):
        return "—"
    target_field = str(item.get("target_field") or chain.get("target") or "")
    target_symbol = symbols.get(target_field) or target_field
    parts = (
        f"<span class='eq-target'>{_symbol_html(target_symbol)}</span>",
        f"<span>{_math_html(chain.get('formula'))}</span>",
        f"<span>{_math_html(chain.get('substitution'))}</span>",
        f"<span class='eq-answer'>{_math_html(chain.get('answer'))}</span>",
    )
    return "<span class='eq-sep'> = </span>".join(parts)


def _formula_trace_html(item: dict[str, Any]) -> str:
    trace = item.get("formula_trace")
    if not isinstance(trace, dict):
        return "<span class='state missing'>未生成机器公式追溯记录</span>"
    definition = trace.get("formula_definition")
    definition = definition if isinstance(definition, dict) else {}
    implementation = definition.get("implementation_binding")
    implementation = implementation if isinstance(implementation, dict) else {}
    input_rows = "".join(
        "<tr>"
        f"<td>{_cell(binding.get('field_id'))}</td>"
        f"<td>{_cell(binding.get('value'))}</td>"
        f"<td>{_cell(_pretty_unit(binding.get('unit')))}</td>"
        f"<td>{_cell(_code_label(binding.get('source_kind')))}</td>"
        f"<td>{_cell(_code_label(binding.get('binding_status')))}</td>"
        f"<td class='meta'>{_cell(binding.get('field_value_sha256'))}</td>"
        "</tr>"
        for binding in trace.get("input_bindings", [])
        if isinstance(binding, dict)
    )
    source_rows = "".join(
        "<tr>"
        f"<td>{_cell(binding.get('reference'))}</td>"
        f"<td>{_cell(_code_label(binding.get('binding_status')))}</td>"
        f"<td>{_cell(binding.get('locator_line_1based'))}</td>"
        f"<td class='meta'>{_cell(binding.get('source_file_sha256'))}</td>"
        "</tr>"
        for binding in definition.get("source_bindings", [])
        if isinstance(binding, dict)
    )
    gaps = trace.get("open_traceability_gaps", [])
    return (
        "<div class='formula-trace'>"
        f"<p><strong>{_cell(trace.get('formula_id'))}</strong> · "
        f"{_cell(_code_label(trace.get('traceability_status')))}</p>"
        f"<p class='meta'>公式定义 SHA-256={_cell(trace.get('formula_definition_sha256'))}<br>"
        f"本次计算追溯 SHA-256={_cell(trace.get('calculation_trace_sha256'))}</p>"
        f"<p>实现：{_cell(implementation.get('implementation_ref'))} · "
        f"{_cell(_code_label(implementation.get('binding_status')))}<br>"
        f"<span class='meta'>源码 SHA-256={_cell(implementation.get('source_file_sha256'))}<br>"
        f"源码集合 SHA-256={_cell(implementation.get('source_code_set_sha256'))}</span></p>"
        "<h4>输入绑定</h4>"
        "<table><thead><tr><th>字段</th><th>值</th><th>单位</th><th>来源类型</th><th>绑定状态</th><th>值 SHA-256</th></tr></thead>"
        f"<tbody>{input_rows}</tbody></table>"
        "<h4>公式来源绑定</h4>"
        "<table><thead><tr><th>来源</th><th>绑定状态</th><th>定位行</th><th>源文件 SHA-256</th></tr></thead>"
        f"<tbody>{source_rows}</tbody></table>"
        f"<p class='warn'><strong>尚未闭合的追溯缺口：</strong>{_cell(gaps or ['无'])}</p>"
        "</div>"
    )


def build_organized_answer(payload: Any) -> dict[str, Any]:
    """Organize immutable program facts into a fixed Agent answer contract."""

    presentation = (
        payload
        if isinstance(payload, dict)
        and payload.get("schema") == "equipment-design-presentation-v1"
        else build_presentation(payload)
    )
    organized_equipment: list[dict[str, Any]] = []
    for card in presentation.get("equipment", []):
        header = card.get("header", {})
        overview = card.get("customer_overview") or {}
        selected_output = (
            card.get("selected_output")
            if isinstance(card.get("selected_output"), dict)
            else {}
        )
        branch_selection = (
            card.get("branch_selection")
            if isinstance(card.get("branch_selection"), dict)
            else {}
        )
        llm_control_result = (
            card.get("llm_control_result")
            if isinstance(card.get("llm_control_result"), dict)
            else _build_llm_control_result(card)
        )
        plan = card.get("engineering_adjustment_plan") or {}
        control = card.get("selection_agent_control") or {}
        configuration = (
            plan.get("configuration", {})
            if isinstance(plan, dict)
            else {}
        )
        warnings: list[str] = []
        if isinstance(plan, dict):
            if plan.get("algorithmic_selection_warning"):
                warnings.append(
                    str(plan["algorithmic_selection_warning"])
                )
            warnings.extend(
                str(item.get("message"))
                for item in plan.get("warnings", [])
                if isinstance(item, dict) and item.get("message")
            )
        pump_selection = card.get("pump_engineering_selection") or {}
        if isinstance(pump_selection, dict):
            warnings.extend(
                str(item.get("message"))
                for item in pump_selection.get("warnings", [])
                if isinstance(item, dict) and item.get("message")
            )
        warnings = list(dict.fromkeys(warnings))
        calculations = [
            {
                "calculation_id": item.get("calculation_id"),
                "target_field": item.get("target_field"),
                "status": item.get("status"),
                "equation_chain": item.get("equation_chain"),
                "evidence_class": (
                    item.get("calculation_notice", {}).get(
                        "evidence_class"
                    )
                    if isinstance(
                        item.get("calculation_notice"), dict
                    )
                    else None
                ),
                "promotion_cap": (
                    item.get("calculation_notice", {}).get(
                        "promotion_cap"
                    )
                    if isinstance(
                        item.get("calculation_notice"), dict
                    )
                    else None
                ),
                "formula_id": (
                    item.get("formula_trace", {}).get("formula_id")
                    if isinstance(item.get("formula_trace"), dict)
                    else None
                ),
                "traceability_status": (
                    item.get("formula_trace", {}).get("traceability_status")
                    if isinstance(item.get("formula_trace"), dict)
                    else None
                ),
                "formula_definition_sha256": (
                    item.get("formula_trace", {}).get("formula_definition_sha256")
                    if isinstance(item.get("formula_trace"), dict)
                    else None
                ),
                "calculation_trace_sha256": (
                    item.get("formula_trace", {}).get("calculation_trace_sha256")
                    if isinstance(item.get("formula_trace"), dict)
                    else None
                ),
                "formula_trace": item.get("formula_trace"),
            }
            for item in card.get("calculation_chain", [])
            if isinstance(item, dict)
        ]
        issues = card.get("issues", {})
        pending_evidence = sorted({
            str(value)
            for key in (
                "evidence_gaps",
                "formal_promotion_blockers",
                "missing_by_goal",
            )
            for value in (
                issues.get(key, [])
                if isinstance(issues.get(key), list)
                else []
            )
            if value not in (None, "")
        })
        next_actions = [
            {
                "action_code": item.get("action_code"),
                "action": item.get("action"),
            }
            for item in (
                plan.get("required_actions", [])
                if isinstance(plan, dict)
                else []
            )
            if isinstance(item, dict)
        ]
        next_actions.extend(
            {
                "action_code": (
                    f"SUPPLY_{str(item.get('field_id') or 'FIELD').upper()}"
                ),
                "action": item.get("reason")
                or item.get("recommended_action"),
            }
            for item in (
                issues.get("input_recommendations", {}).get(
                    "items", []
                )
                if isinstance(
                    issues.get("input_recommendations"), dict
                )
                else []
            )
            if isinstance(item, dict)
            and item.get("status") == "RECOMMENDATION_OPEN"
        )
        organized_equipment.append({
            "equipment_id": card.get("equipment_id"),
            "basic_information": {
                "equipment_tag": header.get("equipment_tag")
                or card.get("equipment_id"),
                "equipment_type": header.get("equipment_type"),
                "process_function": header.get("process_function"),
                "aspen_block_type": header.get("aspen_block_type"),
                "family_id": header.get("family_id"),
                "family_name": header.get("family_name"),
                "selected_type": selected_output.get(
                    "recommended_type"
                )
                or header.get("recommended_type"),
                "selected_model_or_specification": (
                    selected_output.get("model_or_specification")
                    or overview.get("model_or_specification")
                ),
                "model_status": (
                    selected_output.get("model_status")
                    or overview.get("model_or_specification_status")
                ),
                "quantity_and_standby": overview.get(
                    "quantity_and_standby"
                ),
                "pump_material_and_seal": selected_output.get(
                    "pump_material_and_seal", {}
                ),
                "maximum_final_discharge_pressure_mpa_gauge": (
                    selected_output.get(
                        "maximum_final_discharge_pressure_mpa_gauge"
                    )
                ),
                "selected_flange_pressure_class": selected_output.get(
                    "selected_flange_pressure_class"
                ),
                "delivery_state": overview.get("delivery_state"),
                "program_selected": True,
            },
            "branch_selection": branch_selection,
            "llm_control_result": llm_control_result,
            "detailed_calculation_chain": calculations,
            "component_selections": branch_selection.get(
                "component_selections", []
            ),
            "conclusion": {
                "family": header.get("family_name")
                or header.get("family_id"),
                "recommended_type": header.get("recommended_type"),
                "model_or_specification": overview.get(
                    "model_or_specification"
                ),
                "model_or_specification_status": overview.get(
                    "model_or_specification_status"
                ),
                "delivery_state": overview.get("delivery_state"),
                "engineering_adjustment_status": (
                    plan.get("status")
                    if isinstance(plan, dict)
                    else None
                ),
            },
            "calculations": calculations,
            "candidates_and_system_plan": {
                "configuration": configuration,
                "top_candidates": [
                    {
                        "rank": item.get("rank"),
                        "candidate_kind": item.get("candidate_kind"),
                        "designation": item.get("designation"),
                        "status": item.get("status"),
                    }
                    for item in card.get("candidates", [])[:5]
                    if isinstance(item, dict)
                ],
            },
            "mandatory_warnings": warnings,
            "pending_evidence": pending_evidence,
            "next_actions": next_actions,
            "agent_control": {
                "status": control.get("status")
                if isinstance(control, dict)
                else None,
                "calculate_before_select": (
                    control.get("calculate_before_select", {})
                    if isinstance(control, dict)
                    else {}
                ),
                "agent_control_sha256": (
                    control.get("agent_control_sha256")
                    if isinstance(control, dict)
                    else None
                ),
            },
            "fact_binding": {
                "source_result_sha256": header.get(
                    "source_result_sha256"
                ),
                "engineering_adjustment_plan_sha256": (
                    plan.get("plan_sha256")
                    if isinstance(plan, dict)
                    else None
                ),
            },
        })
    answer = {
        "schema": "equipment-agent-organized-answer-v1",
        "section_order": [
            "基本信息",
            "分支选择与大模型调控",
            "详细计算链条",
            "候选与系统修改方案",
            "强制警告",
            "待补证据",
            "下一步",
        ],
        "equipment_count": len(organized_equipment),
        "equipment": organized_equipment,
        "authority": {
            "deterministic_facts_immutable": True,
            "llm_may_organize_language_only": True,
            "llm_may_change_counts_models_or_open_gates": False,
            "program_generated": True,
            "llm_used": bool(presentation.get("llm_used")),
            "llm_result_visible": True,
        },
        "llm_control_result": presentation.get(
            "llm_control_result",
            _build_llm_control_result(presentation),
        ),
        "source_presentation_sha256": _canonical_sha256(
            presentation
        ),
    }
    answer["organized_answer_sha256"] = _canonical_sha256(answer)
    return answer


def render_organized_markdown(answer: dict[str, Any]) -> str:
    """Render the fixed organized-answer contract as a readable report."""

    lines = [
        "# 设备设计选型报告",
        "",
        (
            f"程序组织答案 SHA-256："
            f"`{answer.get('organized_answer_sha256', '—')}`"
        ),
        "",
    ]
    for item in answer.get("equipment", []):
        basic = item.get("basic_information", {})
        branch = item.get("branch_selection", {})
        llm_control = item.get("llm_control_result", {})
        conclusion = item.get("conclusion", {})
        system = item.get("candidates_and_system_plan", {})
        configuration = system.get("configuration", {})
        lines.extend([
            f"## {item.get('equipment_id') or '未命名设备'}",
            "",
            "### 基本信息",
            "",
            f"- 设备位号：{basic.get('equipment_tag') or 'OPEN'}",
            (
                "- 工艺功能："
                f"{basic.get('process_function') or 'OPEN'}"
            ),
            (
                "- Aspen 模块："
                f"{basic.get('aspen_block_type') or 'OPEN'}"
            ),
            (
                "- 程序选择设备族："
                f"{basic.get('family_name') or basic.get('family_id') or conclusion.get('family') or 'OPEN'}"
            ),
            (
                "- 程序选择型式："
                f"{basic.get('selected_type') or conclusion.get('recommended_type') or 'OPEN'}"
            ),
            (
                "- 程序选择型号/工程规格："
                f"{basic.get('selected_model_or_specification') or conclusion.get('model_or_specification') or 'OPEN'}"
            ),
            (
                "- 型号状态："
                f"{basic.get('model_status') or conclusion.get('model_or_specification_status') or 'OPEN'}"
            ),
            (
                "- 数量及备用："
                + json.dumps(
                    basic.get("quantity_and_standby"),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            ),
            *(
                [
                    "- 泵材料与密封："
                    + json.dumps(
                        basic.get("pump_material_and_seal"),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    (
                        "- 串联系统末级最大表压："
                        f"{basic.get('maximum_final_discharge_pressure_mpa_gauge')} MPa"
                    ),
                    (
                        "- 程序选择法兰压力等级："
                        f"{basic.get('selected_flange_pressure_class') or 'OPEN'}"
                    ),
                ]
                if basic.get("pump_material_and_seal")
                else []
            ),
            (
                "- 非标/多台修改状态："
                f"{conclusion.get('engineering_adjustment_status') or '未触发'}"
            ),
            "",
            "### 分支选择与大模型调控",
            "",
        ])
        branch_lines = branch.get("natural_language", [])
        lines.extend(
            [f"- {text}" for text in branch_lines]
            if branch_lines
            else ["- 当前结果没有生成可见的分支选择说明。"]
        )
        predicate_branches = branch.get(
            "leading_candidate_predicate_branches", []
        )
        if predicate_branches:
            lines.extend(["", "#### 首位候选的判断节点", ""])
            lines.extend(
                f"- {row.get('branch_narrative') or row.get('predicate_id')}"
                for row in predicate_branches
                if isinstance(row, dict)
            )
        components = item.get("component_selections", [])
        if components:
            lines.extend(["", "#### 连接部件选择", ""])
            lines.extend(
                f"- {row.get('branch_narrative')}"
                for row in components
                if isinstance(row, dict)
            )
        lines.extend(["", "#### 大模型调控结果", ""])
        lines.extend(
            [
                f"- 状态：{llm_control.get('status') or 'NOT_REQUESTED'}",
                (
                    "- 模型："
                    f"{llm_control.get('provider') or '—'} / "
                    f"{llm_control.get('model') or '—'}"
                ),
            ]
        )
        lines.extend(
            f"- {text}"
            for text in llm_control.get("natural_language", [])
        )
        if llm_control.get("organized_output_blocks"):
            lines.extend(["", "##### 大模型组织后的输出块", ""])
            for block in llm_control.get(
                "organized_output_blocks", []
            ):
                if not isinstance(block, dict):
                    continue
                lines.extend([
                    (
                        f"- {block.get('heading') or block.get('section_ref') or '未命名块'}"
                        f"（{block.get('operation') or 'operation'}）"
                    ),
                    (
                        "  - 内容："
                        + json.dumps(
                            block.get("content"),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    ),
                    (
                        "  - 引用："
                        + json.dumps(
                            block.get("citations", []),
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    ),
                ])
        if llm_control.get("calculation_assist_validation"):
            lines.append(
                "- LLM补值建议及程序复核："
                + json.dumps(
                    llm_control.get(
                        "calculation_assist_validation", []
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        if llm_control.get("terminal_selection_assist_validation"):
            lines.append(
                "- LLM分支建议及程序复核："
                + json.dumps(
                    llm_control.get(
                        "terminal_selection_assist_validation", []
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        if llm_control.get("engineering_choice_assist_validation"):
            lines.append(
                "- LLM材料/零部件登记选择及程序复核："
                + json.dumps(
                    llm_control.get(
                        "engineering_choice_assist_validation", []
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        lines.extend([
            "",
            "### 详细计算链条",
            "",
        ])
        calculations = item.get("calculations", [])
        if calculations:
            for calculation in calculations:
                lines.append(
                    "- "
                    + str(
                        calculation.get("equation_chain")
                        or calculation.get("calculation_id")
                        or "未执行计算"
                    )
                    + f"（{calculation.get('status') or 'UNKNOWN'}）"
                )
                trace = calculation.get("formula_trace")
                if isinstance(trace, dict):
                    definition = trace.get("formula_definition")
                    definition = definition if isinstance(definition, dict) else {}
                    implementation = definition.get("implementation_binding")
                    implementation = implementation if isinstance(implementation, dict) else {}
                    lines.extend([
                        f"  - 公式 ID：`{trace.get('formula_id') or 'OPEN'}`",
                        f"  - 追溯状态：`{trace.get('traceability_status') or 'OPEN'}`",
                        f"  - 公式定义 SHA-256：`{trace.get('formula_definition_sha256') or 'OPEN'}`",
                        f"  - 本次计算追溯 SHA-256：`{trace.get('calculation_trace_sha256') or 'OPEN'}`",
                        (
                            "  - 实现绑定："
                            f"`{implementation.get('implementation_ref') or 'OPEN'}` / "
                            f"`{implementation.get('source_file_sha256') or 'OPEN'}`"
                        ),
                        (
                            "  - 输入绑定："
                            + json.dumps(
                                trace.get("input_bindings", []),
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        ),
                        (
                            "  - 公式来源："
                            + json.dumps(
                                definition.get("source_bindings", []),
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        ),
                        (
                            "  - 追溯缺口："
                            + json.dumps(
                                trace.get("open_traceability_gaps", []),
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        ),
                    ])
        else:
            lines.append("- 当前设备没有已执行的内置计算。")
        lines.extend([
            "",
            "### 候选与系统修改方案",
            "",
            (
                "- 系统标记："
                f"{configuration.get('candidate_model_or_designation') or 'OPEN'}"
            ),
            (
                "- 组合方式："
                f"{configuration.get('arrangement_code') or 'OPEN'}"
            ),
            (
                "- 并联列数 / 每列串联台数 / 估算安装总数："
                f"{configuration.get('parallel_train_count_estimate', 'OPEN')} / "
                f"{configuration.get('series_units_per_train_estimate', 'OPEN')} / "
                f"{configuration.get('installed_unit_count_estimate', 'OPEN')}"
            ),
            (
                "- 单台目标："
                + json.dumps(
                    configuration.get("per_unit_target", {}),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            ),
            "",
            "### 强制警告",
            "",
        ])
        warnings = item.get("mandatory_warnings", [])
        lines.extend(
            [f"- {warning}" for warning in warnings]
            if warnings
            else ["- 当前未触发额外算法拆分警告；正式证据门仍然有效。"]
        )
        lines.extend(["", "### 待补证据", ""])
        pending = item.get("pending_evidence", [])
        lines.extend(
            [f"- {entry}" for entry in pending]
            if pending else ["- 当前程序记录中无新增待补项。"]
        )
        lines.extend(["", "### 下一步", ""])
        actions = item.get("next_actions", [])
        lines.extend(
            [
                f"- {action.get('action_code') or 'ACTION'}："
                f"{action.get('action') or '按项目要求复核'}"
                for action in actions
            ]
            if actions
            else ["- 按正式证据门完成同设备复核。"]
        )
        binding = item.get("fact_binding", {})
        lines.extend([
            "",
            (
                "事实绑定："
                f"result={binding.get('source_result_sha256') or '—'}；"
                f"plan={binding.get('engineering_adjustment_plan_sha256') or '—'}"
            ),
            "",
        ])
    lines.extend([
        "---",
        "",
        (
            "本报告先输出基本信息和程序实际选择，再以自然文字列出算法/部件分支及大模型调控结果，"
            "之后给出详细计算链条。大模型建议只有通过程序白名单、校验和重算后才会影响当前结果；"
            "不能覆盖硬门、伪造厂家型号或关闭 OPEN 证据门。"
        ),
        "",
    ])
    return "\n".join(lines)


def render_markdown(presentation: dict[str, Any]) -> str:
    return render_organized_markdown(
        build_organized_answer(presentation)
    )


def render_html(presentation: dict[str, Any]) -> str:
    """Render a self-contained engineering parameter/candidate report."""
    sections: list[str] = []
    for equipment in presentation.get("equipment", []):
        header = equipment.get("header", {})
        axes = equipment.get("status_axes", {})
        terminal = equipment.get("terminal_selection", {})
        adjustment_plan = equipment.get(
            "engineering_adjustment_plan", {}
        )
        agent_control = equipment.get(
            "selection_agent_control", {}
        )
        adjustment_line = ""
        if (
            isinstance(adjustment_plan, dict)
            and adjustment_plan.get("triggered") is True
        ):
            adjustment_line = (
                "<div class='algorithmic-adjustment-warning'>"
                "<strong>非标/多台组合方案（程序算法初筛）</strong><br>"
                f"{_cell(adjustment_plan.get('algorithmic_selection_warning'))}"
                "<pre>"
                f"{_cell(adjustment_plan.get('configuration', {}))}"
                "</pre>"
                "<strong>必须执行的复核：</strong>"
                f"{_cell(adjustment_plan.get('required_actions', []))}"
                "</div>"
            )
        agent_control_line = (
            "<p class='agent-control-line'>"
            "<strong>Agent 控制：</strong>"
            f"{_cell(_code_label(agent_control.get('status')))}"
            " · 先计算后选型="
            f"{_cell(agent_control.get('calculate_before_select', {}).get('calculation_execution_satisfied'))}"
            " · SHA-256="
            f"{_cell(agent_control.get('agent_control_sha256'))}"
            "</p>"
            if isinstance(agent_control, dict) and agent_control
            else ""
        )
        estimate_disclosure = equipment.get("model_estimate_disclosure", {})
        estimate_line = (
            "<div class='model-estimate-warning'>"
            "<strong>大模型工程估算披露（J / provisional / TYPE_SCREENING）</strong><br>"
            f"{_cell(estimate_disclosure.get('statement'))}"
            "</div>"
            if isinstance(estimate_disclosure, dict)
            and estimate_disclosure.get("model_estimate_fields")
            else ""
        )
        terminal_line = (
            "<p class='terminal-line'>"
            f"<strong>终选型式：{_cell(_code_label(terminal.get('status')))}</strong>"
            f" · {_cell(_code_label(terminal.get('selection_basis')))}"
            f" · rule={_cell(terminal.get('rule_id'))}"
            f"<br><span>{_cell(terminal.get('assumption'))}</span>"
            "</p>"
            if isinstance(terminal, dict) and terminal else ""
        )
        axis_html = "".join(
            f"<div class='axis'><span>{_cell(_AXIS_LABELS.get(name, name))}</span><strong>{_cell(_code_label(value))}</strong></div>"
            for name, value in axes.items()
        )
        symbols = {
            str(row.get("field_id")): str(row.get("symbol") or "")
            for group in equipment.get("parameter_groups", [])
            for row in group.get("rows", [])
            if row.get("field_id")
        }
        group_html: list[str] = []
        for group in equipment.get("parameter_groups", []):
            rows = "".join(
                "<tr>"
                f"<td>{_cell(row.get('label'))}</td>"
                f"<td class='symbol'>{_symbol_html(row.get('symbol'))}</td>"
                f"<td class='num'>{_pretty_number_html(row)}</td>"
                f"<td>{_cell(_pretty_unit(row.get('unit')))}</td>"
                f"<td>{_cell(_code_label(row.get('source', {}).get('kind')))}</td>"
                f"<td><span class='state {_cell(row.get('state')).lower()}'>{_cell(_code_label(row.get('state')))}</span></td>"
                f"<td class='equation'>{_equation_html(row, symbols)}</td>"
                "</tr>"
                for row in group.get("rows", [])
            )
            group_html.append(
                f"<h3>{_cell(group.get('title'))}</h3>"
                "<table><thead><tr><th>参数</th><th>符号</th><th>值</th><th>单位</th><th>来源</th><th>状态</th><th>公式链</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
            )
        calculation_rows = "".join(
            "<tr>"
            f"<td class='symbol'>{_symbol_html(symbols.get(str(item.get('target_field'))) or item.get('target_field'))}</td>"
            f"<td>{_cell(_code_label(item.get('status')))}</td>"
            f"<td class='equation equation-chain'>{_equation_html(item, symbols)}</td>"
            f"<td>{_cell((item.get('calculation_notice') or {}).get('title'))}<br><small>{_cell((item.get('calculation_notice') or {}).get('applicability'))}</small></td>"
            f"<td>{_cell(item.get('missing_fields'))}</td>"
            "</tr>"
            for item in equipment.get("calculation_chain", [])
        )
        traceability_rows = "".join(
            "<tr>"
            f"<td>{_cell(item.get('calculation_id'))}</td>"
            f"<td>{_formula_trace_html(item)}</td>"
            "</tr>"
            for item in equipment.get("calculation_chain", [])
            if isinstance(item, dict)
        )
        fallback_rows = "".join(
            "<tr>"
            f"<td>{_cell(item.get('field_id'))}</td>"
            f"<td>{_cell(item.get('value'))}</td>"
            f"<td>{_cell(_code_label(item.get('state')))}</td>"
            f"<td>{_cell(_code_label(item.get('tier')))}</td>"
            f"<td>{_cell(item.get('reason'))}</td>"
            f"<td>{_cell(item.get('warning'))}</td>"
            "</tr>"
            for item in equipment.get("issues", {}).get("design_fallbacks", [])
        )
        recommendation_rows = "".join(
            "<tr>"
            f"<td>{_cell(item.get('field_id'))}</td>"
            f"<td>{_cell(item.get('label'))}</td>"
            f"<td>{_cell(_code_label(item.get('priority')))}</td>"
            f"<td>{_cell(_code_label(item.get('recommended_action')))}</td>"
            f"<td>{_cell(item.get('reason'))}</td>"
            "</tr>"
            for item in equipment.get("issues", {}).get("input_recommendations", {}).get("items", [])
            if item.get("status") == "RECOMMENDATION_OPEN"
        )
        candidate_rows = "".join(
            "<tr>"
            f"<td>{_cell(item.get('rank'))}</td>"
            f"<td>{_cell(item.get('candidate_kind'))}</td>"
            f"<td>{_cell(item.get('designation'))}</td>"
            f"<td>{_cell(item.get('status'))}</td>"
            f"<td>{_cell(item.get('ranking_score'))}</td>"
            f"<td>{_cell(item.get('predicate_summary'))}</td>"
            f"<td>{_cell(item.get('missing_gates'))}</td>"
            "</tr>"
            for item in equipment.get("candidates", [])
        )
        selected_output = (
            equipment.get("selected_output")
            if isinstance(equipment.get("selected_output"), dict)
            else {}
        )
        branch_selection = (
            equipment.get("branch_selection")
            if isinstance(equipment.get("branch_selection"), dict)
            else {}
        )
        branch_narrative_html = "".join(
            f"<li>{_cell(item)}</li>"
            for item in branch_selection.get("natural_language", [])
        ) or "<li>当前结果没有生成可见的分支选择说明。</li>"
        predicate_branch_rows = "".join(
            "<tr>"
            f"<td>{_cell(item.get('predicate_id'))}</td>"
            f"<td>{_cell(_code_label(item.get('status')))}</td>"
            f"<td>{_cell(item.get('branch_narrative'))}</td>"
            "</tr>"
            for item in branch_selection.get(
                "leading_candidate_predicate_branches", []
            )
            if isinstance(item, dict)
        )
        component_rows = "".join(
            "<tr>"
            f"<td>{_cell(item.get('end_role'))}<br><small>{_cell(item.get('stream_id'))}</small></td>"
            f"<td>{_cell(item.get('component_label'))}</td>"
            f"<td>{_cell(item.get('selected', {}).get('name'))}<br>"
            f"<small>{_cell(item.get('selected', {}).get('code'))} · "
            f"{_cell(item.get('selected', {}).get('candidate_id'))}</small></td>"
            f"<td>{_cell(_code_label(item.get('status')))}</td>"
            f"<td>{_cell(item.get('branch_narrative'))}</td>"
            f"<td>{_cell(item.get('minimum_missing_fields'))}</td>"
            f"<td>{_cell(item.get('source_refs'))}</td>"
            "</tr>"
            for item in equipment.get("component_selections", [])
            if isinstance(item, dict)
        )
        llm_control = (
            equipment.get("llm_control_result")
            if isinstance(equipment.get("llm_control_result"), dict)
            else {}
        )
        llm_narrative_html = "".join(
            f"<li>{_cell(item)}</li>"
            for item in llm_control.get("natural_language", [])
        ) or "<li>本次没有可显示的大模型调控记录。</li>"
        llm_control_html = (
            "<div class='llm-control-result'>"
            "<h3>大模型调控结果</h3>"
            f"<p><strong>状态：</strong>{_cell(llm_control.get('status'))} · "
            f"<strong>模型：</strong>{_cell(llm_control.get('provider'))} / "
            f"{_cell(llm_control.get('model'))} · "
            f"<strong>是否实际改变重算输入：</strong>{_cell(llm_control.get('llm_changed_active_inputs'))}</p>"
            f"<ul>{llm_narrative_html}</ul>"
            "<details><summary>查看 LLM 判断、建议、程序复核与应用账本</summary>"
            f"<pre>{_cell(llm_control)}</pre>"
            "</details>"
            "</div>"
        )
        basic_selection_rows = "".join(
            "<tr><th>" + _cell(label) + "</th><td>" + _cell(value) + "</td></tr>"
            for label, value in (
                ("设备位号", equipment.get("equipment_id")),
                ("设备族", header.get("family_name") or header.get("family_id")),
                ("程序选择型式", selected_output.get("recommended_type") or header.get("recommended_type")),
                ("程序选择型号/工程规格", selected_output.get("model_or_specification")),
                ("型号状态", _code_label(selected_output.get("model_status"))),
                ("型式规则", selected_output.get("terminal_rule_id")),
                ("首位候选 ID", selected_output.get("leading_candidate_id")),
                ("泵材料与密封", selected_output.get("pump_material_and_seal")),
                (
                    "串联系统末级最大表压 / MPa(g)",
                    selected_output.get(
                        "maximum_final_discharge_pressure_mpa_gauge"
                    ),
                ),
                (
                    "程序选择法兰压力等级",
                    selected_output.get("selected_flange_pressure_class"),
                ),
            )
            if value not in (None, "", {})
        )
        customer_overview = equipment.get("customer_overview") or {}
        customer_datasheet = equipment.get("customer_datasheet") or {}
        customer_overview_rows = "".join(
            "<tr>"
            f"<th>{_cell(item.get('label'))}</th><td>{_cell(item.get('value'))}</td>"
            "</tr>"
            for item in customer_overview_display_rows(customer_overview)
        )
        customer_field_rows = "".join(
            "<tr>"
            f"<td>{_cell(item.get('label'))}</td>"
            f"<td>{_cell(item.get('value'))}</td>"
            f"<td>{_cell(_pretty_unit(item.get('unit')))}</td>"
            f"<td>{_cell(_code_label(item.get('state')))}</td>"
            f"<td>{_cell(item.get('evidence_gate'))}</td>"
            f"<td>{_cell(item.get('profile_ids'))}</td>"
            "</tr>"
            for item in customer_datasheet.get("fields", [])
        )
        issues = equipment.get("issues", {})
        sections.append(
            "<section>"
            f"<h1>{_cell(equipment.get('equipment_id'))} · {_cell(header.get('recommended_type') or header.get('family_name'))}</h1>"
            f"<p class='meta'>family={_cell(header.get('family_id'))} · engine={_cell(header.get('engine_version'))} · result SHA-256={_cell(header.get('source_result_sha256'))}</p>"
            f"{estimate_line}"
            f"{adjustment_line}"
            f"{agent_control_line}"
            f"{terminal_line}"
            f"<div class='axes'>{axis_html}</div>"
            "<h2>基本信息与程序实际选择</h2>"
            "<table class='overview'><tbody>" + basic_selection_rows + "</tbody></table>"
            "<h2>分支选择（自然文字）</h2>"
            f"<ol class='branch-narrative'>{branch_narrative_html}</ol>"
            "<h3>首位候选判断节点</h3>"
            "<table><thead><tr><th>判断节点</th><th>结果</th><th>走向说明</th></tr></thead>"
            f"<tbody>{predicate_branch_rows}</tbody></table>"
            "<h3>连接口小部件选择分支</h3>"
            "<table><thead><tr><th>连接口</th><th>部件</th><th>程序所选</th><th>状态</th><th>分支说明</th><th>待补条件</th><th>来源</th></tr></thead>"
            f"<tbody>{component_rows}</tbody></table>"
            f"{llm_control_html}"
            + "<h2>详细计算链条</h2><p class='warn'><strong>提示：</strong>带公式的值由本应用生成，不是 Aspen / 用户直接输出；B 类结果仅供暂定初筛。先显示分支选择，再展开以下计算细节。</p><table><thead><tr><th>目标量</th><th>状态</th><th>目标量 = 公式 = 代入式 = 答案</th><th>适用边界</th><th>缺失</th></tr></thead>"
            f"<tbody>{calculation_rows}</tbody></table>"
            + "<h3>公式可追溯性</h3><p class='warn'><strong>判定：</strong>公式、输入值和代码哈希齐全只证明可复算；输入来源或外部标准未绑定时，仍会明确列为开放缺口。</p>"
            + "<table><thead><tr><th>计算 ID</th><th>机器追溯记录</th></tr></thead>"
            f"<tbody>{traceability_rows}</tbody></table>"
            "<h2>客户权威一览表完整输出</h2>"
            "<table class='overview'><tbody>" + customer_overview_rows + "</tbody></table>"
            "<h3>族数据表字段</h3>"
            "<table><thead><tr><th>字段</th><th>值</th><th>单位</th><th>状态</th><th>证据门</th><th>Profile</th></tr></thead>"
            f"<tbody>{customer_field_rows}</tbody></table>"
            "<h2>参数卡</h2>"
            + "".join(group_html)
            + "<h2>保底值与未给条件推荐</h2><p class='warn'><strong>边界：</strong>保底值会让预设计和设备候选继续输出，但全部保持 J/provisional；同工况数据到位后必须自动重算，不能据此宣称正式定型。</p>"
            + "<h3>已应用的保底值</h3><table><thead><tr><th>字段</th><th>值</th><th>状态</th><th>层级</th><th>依据</th><th>警告</th></tr></thead><tbody>" + fallback_rows + "</tbody></table>"
            + "<h3>仍需闭合但不阻断其他结果</h3><table><thead><tr><th>字段</th><th>名称</th><th>优先级</th><th>推荐动作</th><th>原因</th></tr></thead><tbody>" + recommendation_rows + "</tbody></table>"
            "<h2>候选型号 / 工程规格</h2><table><thead><tr><th>排名</th><th>类别</th><th>候选</th><th>状态</th><th>评分</th><th>谓词</th><th>待闭合</th></tr></thead>"
            f"<tbody>{candidate_rows}</tbody></table>"
            f"<h2>问题与证据门</h2><pre>{_cell(issues)}</pre>"
            f"<p class='gate'><strong>正式门：</strong>{_cell(equipment.get('formal_model_gate'))}</p>"
            f"<p class='warn'><strong>禁止声称：</strong>{_cell(equipment.get('prohibited_claim'))}</p>"
            "</section>"
        )
    body = "".join(sections) or "<p>没有可展示的确定性设备结果。</p>"
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>设备设计参数与选型报告</title>
<style>
:root{--ink:#172630;--muted:#60717d;--line:#d8e0e5;--blue:#0f6275;--paper:#fff;--bg:#eef2f4;--amber:#a76300;--red:#a52a2a;--green:#1f6a43}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 "Microsoft YaHei UI","Noto Sans CJK SC",sans-serif}main{max-width:1500px;margin:28px auto;padding:0 24px}section{background:var(--paper);padding:28px 32px;margin:0 0 24px;border:1px solid var(--line);box-shadow:0 8px 24px rgba(20,40,50,.06)}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 10px;border-bottom:2px solid var(--blue);padding-bottom:6px}h3{font-size:15px;margin:20px 0 7px}.meta{color:var(--muted);font-family:Consolas,monospace;font-size:12px;overflow-wrap:anywhere}.terminal-line{margin:10px 0 4px;padding-left:10px;border-left:3px solid var(--amber)}.terminal-line span{color:var(--muted)}.axes{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:16px 0}.axis{border-top:2px solid var(--blue);padding:8px 2px 5px}.axis span{display:block;color:var(--muted);font-size:11px}.axis strong{display:block;margin-top:3px;font-weight:600}table{width:100%;border-collapse:collapse;table-layout:auto}th,td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}th{background:#edf4f6;white-space:nowrap}.num{text-align:right;font-family:"Segoe UI",Arial,sans-serif;font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}.symbol{text-align:center;white-space:nowrap;font-family:"Cambria Math","Times New Roman",serif;font-style:italic}.equation{font-family:"Cambria Math","Times New Roman",serif;min-width:300px;line-height:1.75}.equation-chain{white-space:nowrap}.eq-target{font-style:italic;font-weight:600}.eq-sep{padding:0 .28em;color:#4f626e}.eq-answer{font-weight:700;color:#123e4a;white-space:nowrap}.state{font-size:11px;font-weight:700}.state.calculated{color:var(--green)}.state.provided{color:var(--blue)}.state.recommended{color:var(--amber)}.state.defaulted{color:var(--red)}.state.missing,.state.external_required{color:var(--amber)}pre{white-space:pre-wrap;background:#f7f8f9;border:1px solid var(--line);padding:12px}.gate{border-left:4px solid var(--blue);padding-left:10px}.warn{border-left:4px solid var(--amber);padding-left:10px}@media print{body{background:#fff}main{max-width:none;margin:0;padding:0}section{box-shadow:none;border:0;page-break-after:always}}
.model-estimate-warning{margin:14px 0;padding:10px 12px;border:1px solid #e1b75b;border-left:5px solid var(--amber);background:#fff8e8;color:#6f4300}
.algorithmic-adjustment-warning{margin:14px 0;padding:12px 14px;border:2px solid #d58b16;border-left:7px solid var(--red);background:#fff2d8;color:#663800}.algorithmic-adjustment-warning pre{background:#fffaf0}.algorithmic-adjustment-warning strong{color:#8b2600}
.agent-control-line{padding:8px 10px;background:#edf7f4;border-left:4px solid var(--green);overflow-wrap:anywhere}
.formula-trace{min-width:760px}.formula-trace h4{margin:12px 0 5px}.formula-trace table{font-size:12px}.formula-trace .meta{word-break:break-all}
.branch-narrative{padding-left:24px}.branch-narrative li{margin:8px 0;padding-left:4px}.llm-control-result{margin:18px 0;padding:14px 16px;border:2px solid #46839a;border-left:7px solid var(--blue);background:#eef8fb}.llm-control-result h3{margin-top:0}.llm-control-result details{margin-top:10px}.llm-control-result summary{cursor:pointer;font-weight:700}
</style></head><body><main>""" + body + "</main></body></html>"
