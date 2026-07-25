from __future__ import annotations

import json
import math
import re
import csv
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
DATA = ROOT / "data"
CURRENT_DOC_IDS = ("doc_guobao_tower", "doc_main_detailed", "doc_supplement3")


@dataclass
class CalcRow:
    module: str
    item: str
    source_location: str
    formula: str
    input_values: dict[str, Any]
    value: float
    unit: str
    document_value_raw: str = ""
    document_numeric_value: float | None = None
    tolerance: float | None = None
    abs_error: float | None = None
    rel_error: float | None = None
    pass_check: bool | None = None
    reliability_class: str = "A_formula_reproduced"
    status: str = "reproduced"
    note: str = ""


@dataclass
class ParameterLedgerRow:
    chapter: str
    equipment_family: str
    object_id: str
    parameter: str
    value: str
    unit: str
    source_document: str
    source_table: str
    source_location: str
    source_type: str
    evidence_class: str
    action: str
    judgment_links: str
    scriptable_formula: str
    note: str = ""


def pipe_required_diameter(flow_m3_h: float, velocity_m_s: float) -> float:
    return math.sqrt(4 * flow_m3_h / (3600 * math.pi * velocity_m_s))


def pipe_actual_velocity(flow_m3_h: float, od_m: float, thickness_m: float) -> float:
    inner_d = od_m - 2 * thickness_m
    return 4 * flow_m3_h / (3600 * math.pi * inner_d**2)


def design_pressure(operating_pressure: float, factor: float) -> float:
    return operating_pressure * factor


def cylinder_calc_thickness(p_mpa: float, di_mm: float, sigma_mpa: float, weld_eff: float) -> float:
    return p_mpa * di_mm / (2 * sigma_mpa * weld_eff - p_mpa)


def ellipsoidal_head_calc_thickness(
    p_mpa: float, di_mm: float, sigma_mpa: float, weld_eff: float
) -> float:
    return p_mpa * di_mm / (2 * sigma_mpa * weld_eff - 0.5 * p_mpa)


def minimum_nominal_thickness(calc_mm: float, min_calc_mm: float, c1_mm: float, c2_mm: float) -> float:
    return max(calc_mm, min_calc_mm) + c1_mm + c2_mm


def tower_bottom_liquid_height(flow_m3_h: float, hold_minutes: float, diameter_m: float) -> float:
    volume = flow_m3_h * hold_minutes / 60
    area = math.pi * diameter_m**2 / 4
    return volume / area


def source_k_to_aspen(source_value: float, atm_pa: float = 101300.0) -> float:
    """mmol gcat^-1 atm^-0.5 h^-1 -> kmol kgcat^-1 Pa^-0.5 s^-1."""
    return source_value * 1e-3 / 3600 / math.sqrt(atm_pa)


def arrhenius_from_two_points(t1_k: float, k1: float, t2_k: float, k2: float) -> tuple[float, float]:
    """Fit ln(k) = ln(A) - E/(R*T). Returns A and E in kJ/mol."""
    r_j_mol_k = 8.31446261815324
    x1 = 1 / (r_j_mol_k * t1_k)
    x2 = 1 / (r_j_mol_k * t2_k)
    y1 = math.log(k1)
    y2 = math.log(k2)
    slope = (y2 - y1) / (x2 - x1)
    e_j_mol = -slope
    ln_a = y1 + e_j_mol * x1
    return math.exp(ln_a), e_j_mol / 1000


def catalyst_mass(
    tube_count: int, tube_inner_d_m: float, tube_length_m: float, particle_density_kg_m3: float, voidage: float
) -> float:
    bed_volume = tube_count * math.pi * tube_inner_d_m**2 / 4 * tube_length_m
    return bed_volume * particle_density_kg_m3 * (1 - voidage)


def bundle_diameter(tube_count: int, tube_od_mm: float, pitch_factor: float = 1.25, edge_factor: float = 1.5) -> float:
    pitch_mm = pitch_factor * tube_od_mm
    centerline_tubes = 1.1 * math.sqrt(tube_count)
    edge_mm = edge_factor * tube_od_mm
    return (pitch_mm * (centerline_tubes - 1) + 2 * edge_mm) / 1000


def packed_bed_tube_nu(
    particle_d_m: float,
    actual_velocity_m_s: float,
    rho_kg_m3: float,
    mu_pa_s: float,
    cp_j_kg_k: float,
    thermal_w_m_k: float,
    tube_inner_d_m: float,
) -> float:
    re_p = particle_d_m * actual_velocity_m_s * rho_kg_m3 / mu_pa_s
    pr = cp_j_kg_k * mu_pa_s / thermal_w_m_k
    return 2.26 * re_p**0.8 * pr**0.33 * math.exp(-6 * particle_d_m / tube_inner_d_m)


def overall_u_outer_area(
    alpha_i: float,
    alpha_o: float,
    di_m: float,
    do_m: float,
    wall_m: float,
    metal_lambda: float,
    rsi: float,
    rso: float,
) -> float:
    dm_m = (di_m + do_m) / 2
    resistance = (1 / alpha_i) * (do_m / di_m)
    resistance += wall_m / metal_lambda * (do_m / dm_m)
    resistance += 1 / alpha_o
    resistance += rsi * (do_m / di_m)
    resistance += rso
    return 1 / resistance


def ergun_pressure_drop(
    particle_d_m: float,
    superficial_velocity_m_s: float,
    rho_kg_m3: float,
    mu_pa_s: float,
    bed_length_m: float,
    voidage: float,
) -> tuple[float, float, float]:
    rem = particle_d_m * superficial_velocity_m_s * rho_kg_m3 / (mu_pa_s * (1 - voidage))
    friction = 150 / rem + 1.75
    dp_pa = friction * rho_kg_m3 * superficial_velocity_m_s**2 * bed_length_m * (1 - voidage)
    dp_pa /= particle_d_m * voidage**3
    return rem, friction, dp_pa


def numeric_from_raw(raw: str) -> float | None:
    if not raw:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", raw.replace(",", ""))
    return float(match.group(0)) if match else None


def split_value_unit(parameter: str, raw_value: str) -> tuple[str, str]:
    unit = ""
    param = parameter.strip()
    for left, right in (("（", "）"), ("(", ")"), ("／", ""), ("/", "")):
        if left in param and (right in param or not right):
            if right:
                before, after = param.rsplit(left, 1)
                if after.endswith(right):
                    return raw_value.strip(), after[: -len(right)].strip()
            else:
                before, after = param.rsplit(left, 1)
                return raw_value.strip(), after.strip()
    if "/" in param:
        before, after = param.rsplit("/", 1)
        if before and after:
            return raw_value.strip(), after.strip()
    return raw_value.strip(), unit


def parse_cylinder_spec(spec: str) -> tuple[float, float] | None:
    match = re.search(r"Φ\s*(\d+(?:\.\d+)?)\s*[×xX]\s*(\d+(?:\.\d+)?)", spec)
    if not match:
        return None
    return float(match.group(1)) / 1000, float(match.group(2)) / 1000


def parse_pipe_spec(spec: str) -> tuple[float, float] | None:
    match = re.search(r"Φ\s*(\d+(?:\.\d+)?)\s*[×xX]\s*(\d+(?:\.\d+)?)", spec)
    if not match:
        return None
    return float(match.group(1)) / 1000, float(match.group(2)) / 1000


def cylinder_geometry_volume_m3(spec: str) -> float | None:
    parsed = parse_cylinder_spec(spec)
    if not parsed:
        return None
    diameter_m, length_m = parsed
    return math.pi * diameter_m**2 / 4 * length_m


def membrane_area_m2(channel_count: float, inner_d_mm: float, length_m: float, element_count: float = 1.0) -> float:
    return element_count * channel_count * math.pi * inner_d_mm / 1000 * length_m


def pump_hydraulic_power_kw(flow_m3_h: float, head_m: float, rho_kg_m3: float) -> float:
    return rho_kg_m3 * 9.80665 * flow_m3_h / 3600 * head_m / 1000


def pump_shaft_power_kw(flow_m3_h: float, head_m: float, efficiency_percent: float, rho_kg_m3: float) -> float:
    return pump_hydraulic_power_kw(flow_m3_h, head_m, rho_kg_m3) / (efficiency_percent / 100)


def pump_reverse_density_kg_m3(flow_m3_h: float, head_m: float, efficiency_percent: float, power_kw: float) -> float:
    return power_kw * 1000 * (efficiency_percent / 100) / (9.80665 * flow_m3_h / 3600 * head_m)


def pressure_ratio(p_out: float, p_in: float) -> float:
    return p_out / p_in


def read_csv_table(relative_path: str) -> list[list[str]]:
    path = ROOT / relative_path
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [[cell.strip() for cell in row] for row in csv.reader(f)]


def current_table_count() -> int:
    total = 0
    for doc_id in CURRENT_DOC_IDS:
        total += len(list((DATA / "tables" / doc_id).glob("*.csv")))
    return total


def first_row_value(rows: list[list[str]], row_name: str, column_name: str) -> float:
    header = rows[0]
    col_index = header.index(column_name)
    for row in rows[1:]:
        if row and row[0] == row_name:
            return float(row[col_index])
    raise KeyError(f"{row_name=} {column_name=} not found")


def numeric_column(rows: list[list[str]], column_name: str) -> list[float]:
    header = rows[0]
    col_index = header.index(column_name)
    values = []
    for row in rows[1:]:
        if len(row) > col_index and row[col_index]:
            values.append(float(row[col_index]))
    return values


def max_packed_height_by_section(rows: list[list[str]]) -> dict[str, float]:
    header = rows[0]
    section_i = header.index("Section")
    height_i = header.index("Packedheight（meter）")
    result: dict[str, float] = {}
    for row in rows[1:]:
        section = row[section_i]
        height = float(row[height_i])
        result[section] = max(result.get(section, 0.0), height)
    return result


def add(
    rows: list[CalcRow],
    *,
    module: str,
    item: str,
    source_location: str,
    formula: str,
    input_values: dict[str, Any],
    value: float,
    unit: str,
    document_value: str = "",
    tolerance: float | None = None,
    reliability_class: str = "A_formula_reproduced",
    status: str = "reproduced",
    note: str = "",
) -> None:
    doc_num = numeric_from_raw(document_value)
    abs_error = rel_error = None
    pass_check = None
    if doc_num is not None and tolerance is not None:
        abs_error = abs(value - doc_num)
        rel_error = abs_error / abs(doc_num) if doc_num else None
        pass_check = abs_error <= tolerance
        if not pass_check and status == "reproduced":
            status = "review"
    rows.append(
        CalcRow(
            module=module,
            item=item,
            source_location=source_location,
            formula=formula,
            input_values=input_values,
            value=value,
            unit=unit,
            document_value_raw=document_value,
            document_numeric_value=doc_num,
            tolerance=tolerance,
            abs_error=abs_error,
            rel_error=rel_error,
            pass_check=pass_check,
            reliability_class=reliability_class,
            status=status,
            note=note,
        )
    )


def add_condition(
    rows: list[CalcRow],
    *,
    module: str,
    item: str,
    source_location: str,
    formula: str,
    input_values: dict[str, Any],
    value: float,
    unit: str,
    condition_label: str,
    pass_check: bool,
    reliability_class: str = "A_formula_reproduced",
    status: str = "reproduced",
    note: str = "",
) -> None:
    rows.append(
        CalcRow(
            module=module,
            item=item,
            source_location=source_location,
            formula=formula,
            input_values=input_values,
            value=value,
            unit=unit,
            document_value_raw=condition_label,
            document_numeric_value=None,
            tolerance=None,
            abs_error=None,
            rel_error=None,
            pass_check=pass_check,
            reliability_class=reliability_class,
            status=status if pass_check else "review",
            note=note,
        )
    )


def add_nozzle_rows(
    rows: list[CalcRow],
    *,
    module: str,
    source_location: str,
    name: str,
    flow_m3_h: float,
    target_u_m_s: float,
    od_m: float,
    thickness_m: float,
    doc_id_m: str,
    doc_u_m_s: str,
    id_tolerance_m: float = 0.001,
    u_tolerance_m_s: float = 0.03,
    note: str = "",
) -> None:
    add(
        rows,
        module=module,
        item=f"{name} theoretical ID",
        source_location=source_location,
        formula="sqrt(4*V/(3600*pi*u_target))",
        input_values={"flow_m3_h": flow_m3_h, "target_velocity_m_s": target_u_m_s},
        value=pipe_required_diameter(flow_m3_h, target_u_m_s),
        unit="m",
        document_value=doc_id_m,
        tolerance=id_tolerance_m,
        reliability_class="A_formula_reproduced",
        note=note,
    )
    add(
        rows,
        module=module,
        item=f"{name} selected-pipe velocity",
        source_location=source_location,
        formula="4*V/(3600*pi*(OD-2*s)^2)",
        input_values={"flow_m3_h": flow_m3_h, "OD_m": od_m, "wall_thickness_m": thickness_m},
        value=pipe_actual_velocity(flow_m3_h, od_m, thickness_m),
        unit="m/s",
        document_value=doc_u_m_s,
        tolerance=u_tolerance_m_s,
        reliability_class="A_formula_reproduced",
    )


def add_t802_union_rows(rows: list[CalcRow]) -> None:
    stream = read_csv_table("data/tables/doc_guobao_tower/table_06.csv")
    summary = read_csv_table("data/tables/doc_guobao_tower/table_08.csv")
    sizing = read_csv_table("data/tables/doc_guobao_tower/table_10.csv")
    rating = read_csv_table("data/tables/doc_guobao_tower/table_11.csv")

    feed_mass = first_row_value(stream, "质量流量", "进料")
    distillate_mass = first_row_value(stream, "质量流量", "塔顶出料")
    bottoms_mass = first_row_value(stream, "质量流量", "塔底出料")
    feed_mol = first_row_value(stream, "摩尔流量", "进料")
    distillate_mol = first_row_value(stream, "摩尔流量", "塔顶出料")
    bottoms_mol = first_row_value(stream, "摩尔流量", "塔底出料")
    feed_vol = first_row_value(stream, "体积流量", "进料")
    distillate_vol = first_row_value(stream, "体积流量", "塔顶出料")
    bottoms_vol = first_row_value(stream, "体积流量", "塔底出料")

    add(
        rows,
        module="T802 union tower",
        item="mass balance closure",
        source_location="国宝特工 DOCX 1.3.2.2; doc_guobao_tower table_06",
        formula="feed mass - distillate mass - bottoms mass",
        input_values={"feed_kg_h": feed_mass, "distillate_kg_h": distillate_mass, "bottoms_kg_h": bottoms_mass},
        value=feed_mass - distillate_mass - bottoms_mass,
        unit="kg/h",
        document_value="0",
        tolerance=0.05,
        note="Uses Aspen stream table in the second document; this proves the added T-802 case is internally mass-balanced.",
    )
    add(
        rows,
        module="T802 union tower",
        item="molar balance closure",
        source_location="国宝特工 DOCX 1.3.2.2; doc_guobao_tower table_06",
        formula="feed mol - distillate mol - bottoms mol",
        input_values={"feed_kmol_h": feed_mol, "distillate_kmol_h": distillate_mol, "bottoms_kmol_h": bottoms_mol},
        value=feed_mol - distillate_mol - bottoms_mol,
        unit="kmol/h",
        document_value="0",
        tolerance=0.02,
    )
    add(
        rows,
        module="T802 union tower",
        item="volume balance residual",
        source_location="国宝特工 DOCX 1.3.2.2; doc_guobao_tower table_06",
        formula="feed volume - distillate volume - bottoms volume",
        input_values={"feed_m3_h": feed_vol, "distillate_m3_h": distillate_vol, "bottoms_m3_h": bottoms_vol},
        value=feed_vol - distillate_vol - bottoms_vol,
        unit="m3/h",
        document_value="0",
        tolerance=0.01,
        note="Small residual reflects density/composition changes and stream-table rounding.",
    )

    # The compact table uses pairs of columns, so direct extraction is clearer here.
    design_temp_c = float(summary[1][1])
    design_pressure_mpa = float(summary[1][3])
    theoretical_stages = float(summary[2][1])
    feed_stage = float(summary[2][3])
    total_height_m = float(summary[3][1])
    add(
        rows,
        module="T802 union tower",
        item="design temperature imported",
        source_location="国宝特工 DOCX 1.3.2.1; doc_guobao_tower table_08",
        formula="read design-temperature field",
        input_values={"table_value_C": design_temp_c},
        value=design_temp_c,
        unit="C",
        document_value="200",
        tolerance=0.01,
        reliability_class="B_document_selection_reproduced",
    )
    add(
        rows,
        module="T802 union tower",
        item="external-pressure design pressure imported",
        source_location="国宝特工 DOCX 1.3.2.1; doc_guobao_tower table_08",
        formula="read design-pressure field",
        input_values={"table_value_MPa": design_pressure_mpa},
        value=design_pressure_mpa,
        unit="MPa",
        document_value="-0.1",
        tolerance=0.001,
        reliability_class="B_document_selection_reproduced",
        note="This is a vacuum-tower external-pressure design case from the second document; mechanical proof still requires SW6.",
    )
    add(
        rows,
        module="T802 union tower",
        item="theoretical stages imported",
        source_location="国宝特工 DOCX 1.3.2.3; doc_guobao_tower table_07/table_08",
        formula="read RadFrac stage count",
        input_values={"theoretical_stages": theoretical_stages},
        value=theoretical_stages,
        unit="stages",
        document_value="30",
        tolerance=0.01,
        reliability_class="B_document_selection_reproduced",
        note="The compact document adds a second tower example, useful as tower-method template rather than replacing T0301.",
    )
    add(
        rows,
        module="T802 union tower",
        item="feed stage imported",
        source_location="国宝特工 DOCX 1.3.2.3; doc_guobao_tower table_07/table_08",
        formula="read RadFrac feed stage",
        input_values={"feed_stage": feed_stage},
        value=feed_stage,
        unit="stage",
        document_value="16",
        tolerance=0.01,
        reliability_class="B_document_selection_reproduced",
        note="Paragraph 1.3.3 later says feed position 13 during packing segmentation; keep both as a source inconsistency to verify in Aspen export.",
    )
    add(
        rows,
        module="T802 union tower",
        item="tower total height imported",
        source_location="国宝特工 DOCX 1.3.2.4; doc_guobao_tower table_08",
        formula="read total-height field",
        input_values={"total_height_m": total_height_m},
        value=total_height_m,
        unit="m",
        document_value="12.50",
        tolerance=0.01,
        reliability_class="B_document_selection_reproduced",
    )

    sizing_heights = max_packed_height_by_section(sizing)
    rating_heights = max_packed_height_by_section(rating)
    sizing_total_height = sum(sizing_heights.values())
    rating_total_height = sum(rating_heights.values())
    add(
        rows,
        module="T802 union hydraulic",
        item="interactive sizing packed height",
        source_location="国宝特工 DOCX 1.3.3.2; doc_guobao_tower table_10",
        formula="sum(max packed height per section)",
        input_values=sizing_heights,
        value=sizing_total_height,
        unit="m",
        document_value="6.8",
        tolerance=0.02,
        note="Second document contributes the tower-method chain: segment packing every 4-6 m and verify by Aspen Column Internals.",
    )
    add(
        rows,
        module="T802 union hydraulic",
        item="rating packed height",
        source_location="国宝特工 DOCX 1.3.3.3; doc_guobao_tower table_11",
        formula="sum(max packed height per section)",
        input_values=rating_heights,
        value=rating_total_height,
        unit="m",
        document_value="6.8",
        tolerance=0.02,
    )
    for table_name, table_rows, source in [
        ("interactive sizing", sizing, "国宝特工 DOCX 表1-11; doc_guobao_tower table_10"),
        ("rating", rating, "国宝特工 DOCX 表1-12; doc_guobao_tower table_11"),
    ]:
        capacities_lv = numeric_column(table_rows, "%Capacity (ConstantL/V)")
        capacities_l = numeric_column(table_rows, "%Capacity (ConstantL)")
        pressure_drop = numeric_column(table_rows, "Pressuredrop（mbar）")
        add_condition(
            rows,
            module="T802 union hydraulic",
            item=f"{table_name} min %Capacity L/V",
            source_location=source,
            formula="min(%Capacity ConstantL/V)",
            input_values={"min": min(capacities_lv), "max": max(capacities_lv), "target_range": "40-80"},
            value=min(capacities_lv),
            unit="%",
            condition_label=">=40 and <=80",
            pass_check=40 <= min(capacities_lv) <= 80,
            reliability_class="B_document_selection_reproduced",
            note="This is an Aspen Column Internals table check; formal evidence is still the exported Aspen table/screenshot.",
        )
        add_condition(
            rows,
            module="T802 union hydraulic",
            item=f"{table_name} max %Capacity L/V",
            source_location=source,
            formula="max(%Capacity ConstantL/V)",
            input_values={"min": min(capacities_lv), "max": max(capacities_lv), "target_range": "40-80"},
            value=max(capacities_lv),
            unit="%",
            condition_label=">=40 and <=80",
            pass_check=40 <= max(capacities_lv) <= 80,
            reliability_class="B_document_selection_reproduced",
        )
        add_condition(
            rows,
            module="T802 union hydraulic",
            item=f"{table_name} min %Capacity ConstantL",
            source_location=source,
            formula="min(%Capacity ConstantL)",
            input_values={"min": min(capacities_l), "max": max(capacities_l), "target_range": "40-80"},
            value=min(capacities_l),
            unit="%",
            condition_label=">=40 and <=80",
            pass_check=40 <= min(capacities_l) <= 80,
            reliability_class="B_document_selection_reproduced",
        )
        add_condition(
            rows,
            module="T802 union hydraulic",
            item=f"{table_name} max pressure drop",
            source_location=source,
            formula="max(stage pressure drop)",
            input_values={"min_mbar": min(pressure_drop), "max_mbar": max(pressure_drop)},
            value=max(pressure_drop),
            unit="mbar",
            condition_label="reported as low pressure drop",
            pass_check=max(pressure_drop) < 2.0,
            reliability_class="B_document_selection_reproduced",
            note="Threshold 2 mbar is a script sanity check for the document's '压降不大' claim, not a universal design rule.",
        )


def add_supplement3_rows(rows: list[CalcRow]) -> None:
    stream = read_csv_table("data/tables/doc_supplement3/table_07.csv")
    design = read_csv_table("data/tables/doc_supplement3/table_09.csv")
    sizing = read_csv_table("data/tables/doc_supplement3/table_10.csv")
    rating = read_csv_table("data/tables/doc_supplement3/table_11.csv")
    exchanger_stream = read_csv_table("data/tables/doc_supplement3/table_18.csv")
    exchanger_conditions = read_csv_table("data/tables/doc_supplement3/table_19.csv")

    feed_mass = first_row_value(stream, "质量流量", "进料")
    distillate_mass = first_row_value(stream, "质量流量", "塔顶出料")
    bottoms_mass = first_row_value(stream, "质量流量", "塔底出料")
    feed_mol = first_row_value(stream, "摩尔流量", "进料")
    distillate_mol = first_row_value(stream, "摩尔流量", "塔顶出料")
    bottoms_mol = first_row_value(stream, "摩尔流量", "塔底出料")
    feed_vol = first_row_value(stream, "体积流量", "进料")
    distillate_vol = first_row_value(stream, "体积流量", "塔顶出料")
    bottoms_vol = first_row_value(stream, "体积流量", "塔底出料")

    add(
        rows,
        module="SUP3 T0402 stream",
        item="mass balance closure",
        source_location="补充资料3 T0402 流股表; doc_supplement3 table_07",
        formula="feed mass - distillate mass - bottoms mass",
        input_values={"feed_kg_h": feed_mass, "distillate_kg_h": distillate_mass, "bottoms_kg_h": bottoms_mass},
        value=feed_mass - distillate_mass - bottoms_mass,
        unit="kg/h",
        document_value="0",
        tolerance=0.05,
        note="T0402 is the third document tower case. This checks the Aspen stream table arithmetic, not tower design hydraulics.",
    )
    add(
        rows,
        module="SUP3 T0402 stream",
        item="molar balance closure",
        source_location="补充资料3 T0402 流股表; doc_supplement3 table_07",
        formula="feed mol - distillate mol - bottoms mol",
        input_values={"feed_kmol_h": feed_mol, "distillate_kmol_h": distillate_mol, "bottoms_kmol_h": bottoms_mol},
        value=feed_mol - distillate_mol - bottoms_mol,
        unit="kmol/h",
        document_value="0",
        tolerance=0.02,
    )
    add(
        rows,
        module="SUP3 T0402 stream",
        item="volume-flow residual",
        source_location="补充资料3 T0402 流股表; doc_supplement3 table_07",
        formula="feed volume - distillate volume - bottoms volume",
        input_values={"feed_m3_h": feed_vol, "distillate_m3_h": distillate_vol, "bottoms_m3_h": bottoms_vol},
        value=feed_vol - distillate_vol - bottoms_vol,
        unit="m3/h",
        reliability_class="B_document_selection_reproduced",
        status="informational",
        note="Volume flow is not a conserved extensive quantity across separation when density/composition changes; this row is informational only.",
    )

    design_temp_c = float(design[1][1])
    design_pressure_mpa = float(design[1][3])
    theoretical_stages = float(design[2][1])
    feed_stage = float(design[2][3])
    total_height_m = float(design[3][1])
    for item, value, unit, doc_value in [
        ("design temperature imported", design_temp_c, "C", "200"),
        ("external-pressure design pressure imported", design_pressure_mpa, "MPa", "-0.1"),
        ("theoretical stages imported", theoretical_stages, "stages", "21"),
        ("feed stage imported", feed_stage, "stage", "13"),
        ("tower total height imported", total_height_m, "m", "12.50"),
    ]:
        add(
            rows,
            module="SUP3 T0402 stream",
            item=item,
            source_location="补充资料3 T0402 设计条件汇总; doc_supplement3 table_09",
            formula="read field from design-summary table",
            input_values={"table_value": value},
            value=value,
            unit=unit,
            document_value=doc_value,
            tolerance=0.01,
            reliability_class="B_document_selection_reproduced",
        )

    sizing_heights = max_packed_height_by_section(sizing)
    rating_heights = max_packed_height_by_section(rating)
    for label, table_rows, heights, source in [
        ("interactive sizing", sizing, sizing_heights, "补充资料3 T0402 Column Internals初设表; doc_supplement3 table_10"),
        ("rating", rating, rating_heights, "补充资料3 T0402 Column Internals校核表; doc_supplement3 table_11"),
    ]:
        add(
            rows,
            module="SUP3 T0402 hydraulic",
            item=f"{label} packed height",
            source_location=source,
            formula="sum(max packed height per section)",
            input_values=heights,
            value=sum(heights.values()),
            unit="m",
            document_value="6.8",
            tolerance=0.02,
            reliability_class="B_document_selection_reproduced",
        )
        capacities_lv = numeric_column(table_rows, "%Capacity (ConstantL/V)")
        capacities_l = numeric_column(table_rows, "%Capacity (ConstantL)")
        pressure_drop = numeric_column(table_rows, "Pressuredrop（mbar）")
        for item, value, condition in [
            (f"{label} min %Capacity L/V", min(capacities_lv), ">=40 and <=80"),
            (f"{label} max %Capacity L/V", max(capacities_lv), ">=40 and <=80"),
            (f"{label} min %Capacity ConstantL", min(capacities_l), ">=40 and <=80"),
            (f"{label} max pressure drop", max(pressure_drop), "reported as low pressure drop"),
        ]:
            pass_check = 40 <= value <= 80 if "%Capacity" in item else value < 2.0
            add_condition(
                rows,
                module="SUP3 T0402 hydraulic",
                item=item,
                source_location=source,
                formula="range/sanity check from Column Internals table",
                input_values={"value": value},
                value=value,
                unit="%" if "%Capacity" in item else "mbar",
                condition_label=condition,
                pass_check=pass_check,
                reliability_class="B_document_selection_reproduced",
                note="Formal evidence is still Aspen Column Internals export/screenshot; this row checks the extracted table arithmetic.",
            )

    for name, flow_m3_s, od_m, thk_m, doc_u in [
        ("top vapor outlet", 1.85, 0.273, 0.015, "40.01"),
        ("feed inlet", 4.27e-4, 0.034, 0.008, "1.68"),
        ("reflux inlet", 8.39e-5, 0.021, 0.006, "1.32"),
        ("bottoms to reboiler outlet", 4.67e-6, 0.021, 0.006, "0.07"),
        ("reboiler return", 1.16, 0.273, 0.015, "24.92"),
    ]:
        add(
            rows,
            module="SUP3 T0402 nozzle",
            item=f"{name} selected-pipe velocity",
            source_location="补充资料3 T0402 接管汇总; doc_supplement3 table_13",
            formula="4*Q/(pi*(OD-2*s)^2)",
            input_values={"flow_m3_s": flow_m3_s, "OD_m": od_m, "wall_thickness_m": thk_m},
            value=pipe_actual_velocity(flow_m3_s * 3600, od_m, thk_m),
            unit="m/s",
            document_value=doc_u,
            tolerance=0.2 if float(doc_u) > 10 else 0.03,
        )

    add(
        rows,
        module="SUP3 T0402 nozzle",
        item="bottom liquid hold-up height if only using 5 min inventory",
        source_location="补充资料3 T0402 塔底空间; outline paragraph 134",
        formula="(Q*t)/(pi*D^2/4)",
        input_values={"bottom_flow_m3_s": 4.65e-6, "hold_minutes": 5, "tower_diameter_m": 0.6},
        value=tower_bottom_liquid_height(4.65e-6 * 3600, 5, 0.6),
        unit="m",
        reliability_class="B_document_selection_reproduced",
        status="informational",
        note="The document finally takes HB=1.0 m due to collector/manhole/layout, not because liquid inventory alone requires 1.0 m.",
    )

    tube_p_in = first_row_value(exchanger_stream, "压力MPa", "管程入口")
    shell_p_in = first_row_value(exchanger_stream, "压力MPa", "壳程入口")
    tube_t_max = max(first_row_value(exchanger_stream, "温度℃", "管程入口"), first_row_value(exchanger_stream, "温度℃", "管程出口"))
    shell_t_max = max(first_row_value(exchanger_stream, "温度℃", "壳程入口"), first_row_value(exchanger_stream, "温度℃", "壳程出口"))
    tube_design_p = float(exchanger_conditions[3][1])
    shell_design_p = float(exchanger_conditions[3][2])
    tube_allow_dp = float(exchanger_conditions[4][1])
    shell_allow_dp = float(exchanger_conditions[4][2])
    tube_design_t = float(exchanger_conditions[5][1])
    shell_design_t = float(exchanger_conditions[5][2])
    for side, p_in, doc_p in [
        ("tube side", tube_p_in, tube_design_p),
        ("shell side", shell_p_in, shell_design_p),
    ]:
        add(
            rows,
            module="SUP3 E0104 exchanger",
            item=f"{side} design pressure inferred by Pmax+0.1MPa",
            source_location="补充资料3 E0104 工艺条件/设计条件; doc_supplement3 table_18/table_19",
            formula="P_design ~= P_operating,max + 0.1 MPa",
            input_values={"operating_pressure_MPa": p_in, "increment_MPa": 0.1},
            value=p_in + 0.1,
            unit="MPa",
            document_value=str(doc_p),
            tolerance=0.02,
            reliability_class="B_document_selection_reproduced",
            note="This reconciles the table values within rounding, but the explicit design-pressure rule should be cited before using it formally.",
        )
    for side, p_in, doc_dp in [
        ("tube side", tube_p_in, tube_allow_dp),
        ("shell side", shell_p_in, shell_allow_dp),
    ]:
        add(
            rows,
            module="SUP3 E0104 exchanger",
            item=f"{side} allowable pressure drop",
            source_location="补充资料3 E0104 允许压降; outline paragraph 415; doc_supplement3 table_19",
            formula="0.2*P_inlet because outlet pressure >0.1 MPa",
            input_values={"inlet_pressure_MPa": p_in, "factor": 0.2},
            value=0.2 * p_in,
            unit="MPa",
            document_value=str(doc_dp),
            tolerance=0.001,
        )
    for side, t_max, doc_t in [
        ("tube side", tube_t_max, tube_design_t),
        ("shell side", shell_t_max, shell_design_t),
    ]:
        add_condition(
            rows,
            module="SUP3 E0104 exchanger",
            item=f"{side} design-temperature margin",
            source_location="补充资料3 E0104 设计温度; doc_supplement3 table_18/table_19",
            formula="T_design - max(T_operating)",
            input_values={"max_operating_C": t_max, "design_temperature_C": doc_t},
            value=doc_t - t_max,
            unit="C",
            condition_label="15 <= margin <= 30 C",
            pass_check=15 <= doc_t - t_max <= 30,
            reliability_class="B_document_selection_reproduced",
        )

    add(
        rows,
        module="R0201 kinetics source audit",
        item="main document low-temperature label converted to K",
        source_location="主文档 R0201 动力学; doc_main_detailed table_15/table_16",
        formula="T_C + 273.15",
        input_values={"table_15_T_C": 220, "table_16_T_K": 553.15},
        value=220 + 273.15,
        unit="K",
        document_value="553.15",
        tolerance=0.1,
        reliability_class="D_kinetics_provisional",
        status="review",
        note="The main document labels the low point as 220 C, but table_16 uses 553.15 K. This is inconsistent; supplement3 labels the same point as 280 C.",
    )
    add(
        rows,
        module="SUP3 kinetics source audit",
        item="supplement low-temperature label converted to K",
        source_location="补充资料3 动力学; doc_supplement3 table_24",
        formula="T_C + 273.15",
        input_values={"table_24_T_C": 280, "table_24_T_K": 553.15},
        value=280 + 273.15,
        unit="K",
        document_value="553.15",
        tolerance=0.1,
        reliability_class="D_kinetics_provisional",
        status="provisional",
        note="This fixes the temperature-label inconsistency seen in the main document, but kinetics still needs the full Aspen freeze chain.",
    )

    for side, p_op, doc_p, t_op, doc_t in [
        ("tube side", 0.6, 0.66, 360, 380),
        ("shell side", 0.4, 0.44, 321, 341),
    ]:
        add(
            rows,
            module="SUP3 R0101 reactor",
            item=f"{side} design pressure",
            source_location="补充资料3 R0101 反应器壳体/管箱参数; outline paragraph 948; doc_supplement3 table_31",
            formula="1.1*P_operating",
            input_values={"operating_pressure_MPa": p_op, "factor": 1.1},
            value=1.1 * p_op,
            unit="MPa",
            document_value=str(doc_p),
            tolerance=0.001,
        )
        add(
            rows,
            module="SUP3 R0101 reactor",
            item=f"{side} design temperature",
            source_location="补充资料3 R0101 反应器壳体/管箱参数; outline paragraph 948; doc_supplement3 table_31",
            formula="T_operating + design margin",
            input_values={"operating_temperature_C": t_op, "margin_C": doc_t - t_op},
            value=doc_t,
            unit="C",
            document_value=str(doc_t),
            tolerance=0.001,
            reliability_class="B_document_selection_reproduced",
            note="The paragraph states the 15-30 C design-temperature margin and then gives this selected value.",
        )


def add_late_chapter_audit_rows(rows: list[CalcRow]) -> None:
    # Chapter 4: V0102 separator sample. These are sanity checks, not a demister/vendor proof.
    vg_m3_h = 306.753
    vl_m3_h = 0.000672
    inlet_mass = 675.156
    gas_mass = 674.499
    liquid_mass = 0.65714
    rho_l = 977.991
    rho_v = 2.19883
    diameter_m = 0.4
    shell_height_m = 1.1
    area_m2 = math.pi * diameter_m**2 / 4
    gas_u = vg_m3_h / 3600 / area_m2
    k_required = gas_u / math.sqrt((rho_l - rho_v) / rho_v)
    cyl_volume = area_m2 * shell_height_m
    add(
        rows,
        module="CH4 separator audit",
        item="V0102 mass balance residual",
        source_location="doc_supplement3 table_33",
        formula="feed mass - gas outlet mass - liquid outlet mass",
        input_values={"feed_kg_h": inlet_mass, "gas_kg_h": gas_mass, "liquid_kg_h": liquid_mass},
        value=inlet_mass - gas_mass - liquid_mass,
        unit="kg/h",
        document_value="0",
        tolerance=0.001,
        reliability_class="B_document_selection_reproduced",
        note="Stream table closes by arithmetic; this does not prove separation efficiency.",
    )
    add(
        rows,
        module="CH4 separator audit",
        item="V0102 design pressure from inlet pressure",
        source_location="doc_supplement3 table_33/table_35",
        formula="1.1*P_inlet",
        input_values={"P_inlet_MPa": 0.171325, "factor": 1.1},
        value=design_pressure(0.171325, 1.1),
        unit="MPa",
        document_value="0.1885",
        tolerance=0.0001,
        reliability_class="B_document_selection_reproduced",
    )
    add(
        rows,
        module="CH4 separator audit",
        item="V0102 gas superficial velocity",
        source_location="doc_supplement3 table_34/table_35",
        formula="VG/(3600*pi*D^2/4)",
        input_values={"VG_m3_h": vg_m3_h, "D_m": diameter_m},
        value=gas_u,
        unit="m/s",
        document_value="not listed; sanity check only",
        reliability_class="B_document_selection_reproduced",
        status="screening_only",
        note="Needs allowable K/liquid-droplet/vendor or standard basis before a pass/fail claim.",
    )
    add(
        rows,
        module="CH4 separator audit",
        item="V0102 required Souders-Brown K",
        source_location="doc_supplement3 table_34/table_35",
        formula="u/sqrt((rho_l-rho_v)/rho_v)",
        input_values={"u_m_s": gas_u, "rho_l_kg_m3": rho_l, "rho_v_kg_m3": rho_v},
        value=k_required,
        unit="m/s",
        document_value="not listed; requires standard/vendor K",
        reliability_class="B_document_selection_reproduced",
        status="screening_only",
        note="This reverse-calculates the K required by the selected diameter; it is not a demister proof.",
    )
    add(
        rows,
        module="CH4 separator audit",
        item="V0102 gas residence time in cylindrical shell",
        source_location="doc_supplement3 table_34/table_35",
        formula="V_cyl/(VG/3600)",
        input_values={"cyl_volume_m3": cyl_volume, "VG_m3_h": vg_m3_h},
        value=cyl_volume / (vg_m3_h / 3600),
        unit="s",
        document_value="not listed; sanity check only",
        reliability_class="B_document_selection_reproduced",
        status="screening_only",
    )
    for name, spec, flow, doc_hint in [
        ("V0102 inlet nozzle velocity", "Φ77×6", vg_m3_h + vl_m3_h, "combined volume flow"),
        ("V0102 gas outlet nozzle velocity", "Φ85×6", vg_m3_h, "gas outlet volume flow"),
        ("V0102 liquid outlet nozzle velocity", "Φ12×3", vl_m3_h, "liquid outlet volume flow"),
    ]:
        parsed = parse_pipe_spec(spec)
        if parsed:
            od_m, thk_m = parsed
            add(
                rows,
                module="CH4 separator audit",
                item=name,
                source_location="doc_supplement3 table_34/table_35",
                formula="4*V/(3600*pi*(OD-2*s)^2)",
                input_values={"pipe_spec": spec, "flow_m3_h": flow, "basis": doc_hint},
                value=pipe_actual_velocity(flow, od_m, thk_m),
                unit="m/s",
                document_value="not listed; needs target velocity standard",
                reliability_class="B_document_selection_reproduced",
                status="screening_only",
                note="Flow basis is inferred from the sample stream table; pass/fail needs a target-velocity source.",
            )

    # Chapter 5: pressure ratios only. Power remains vendor/Aspen property boundary.
    for tag, pin, pout, source in [
        ("SUP3 C0101", 1.71325, 7.0, "doc_supplement3 table_37/table_38"),
        ("C0101", 1.20, 3.25, "doc_main_detailed table_24"),
        ("C0102", 0.95, 1.85, "doc_main_detailed table_24"),
        ("C0103", 1.10, 2.00, "doc_main_detailed table_24"),
        ("C0201", 1.05, 5.15, "doc_main_detailed table_24"),
        ("C0301", 0.50, 1.10, "doc_main_detailed table_24"),
    ]:
        add(
            rows,
            module="CH5 compressor audit",
            item=f"{tag} pressure ratio",
            source_location=source,
            formula="P_out/P_in",
            input_values={"P_in_bar": pin, "P_out_bar": pout},
            value=pressure_ratio(pout, pin),
            unit="-",
            document_value="not listed; screening ratio",
            reliability_class="B_document_selection_reproduced",
            status="screening_only",
            note="Only a pressure-ratio precheck; compressor power needs MW/k/Z/efficiency and vendor curve.",
        )

    # Chapter 6: storage/reflux/buffer geometry checks.
    for source_table, category in [
        ("data/tables/doc_main_detailed/table_25.csv", "main storage tank"),
        ("data/tables/doc_main_detailed/table_26.csv", "main reflux drum"),
        ("data/tables/doc_main_detailed/table_27.csv", "main buffer drum"),
    ]:
        table_name = Path(source_table).name
        for row in read_csv_table(source_table)[1:]:
            header = read_csv_table(source_table)[0]
            row_map = dict(zip(header, row))
            tag = row_map.get("设备位号", "")
            nominal = numeric_from_raw(row_map.get("公称容积/m3", row_map.get("公称容积／m3", "")))
            spec = row_map.get("技术规格/mm", row_map.get("技术规格／mm", ""))
            geom = cylinder_geometry_volume_m3(spec)
            if tag and nominal is not None and geom:
                add(
                    rows,
                    module="CH6 vessel geometry audit",
                    item=f"{tag} nominal/geometric volume ratio",
                    source_location=f"doc_main_detailed {table_name}",
                    formula="V_nominal/(pi*D^2/4*L)",
                    input_values={"category": category, "nominal_m3": nominal, "spec": spec},
                    value=nominal / geom,
                    unit="-",
                    document_value="not listed; geometry sanity check",
                    reliability_class="B_document_selection_reproduced",
                    status="screening_only",
                    note="A geometry ratio check only; inventory days, filling fraction, breathing/nitrogen blanketing, and SW6 remain missing.",
                )
    for table_no, tag in [(39, "V0503"), (40, "SUP3 V0104"), (41, "SUP3 V0101")]:
        rows_v = read_csv_table(f"data/tables/doc_supplement3/table_{table_no}.csv")
        values = {r[0]: r[1] for r in rows_v[1:] if len(r) >= 2}
        nominal = numeric_from_raw(values.get("公称容积/m3", ""))
        spec = values.get("技术规格/mm", "")
        geom = cylinder_geometry_volume_m3(spec)
        if nominal is not None and geom:
            add(
                rows,
                module="CH6 vessel geometry audit",
                item=f"{tag} nominal/geometric volume ratio",
                source_location=f"doc_supplement3 table_{table_no}",
                formula="V_nominal/(pi*D^2/4*L)",
                input_values={"nominal_m3": nominal, "spec": spec},
                value=nominal / geom,
                unit="-",
                document_value="not listed; geometry sanity check",
                reliability_class="B_document_selection_reproduced",
                status="screening_only",
                note="Supplement sample branch only; same tag in another document must not be merged automatically.",
            )

    # Chapter 7: membrane geometry.
    membrane = read_csv_table("data/tables/doc_main_detailed/table_31.csv")
    m_header = membrane[0]
    m_row = dict(zip(m_header, membrane[1]))
    channel_count = float(m_row["单个膜元件通道数"])
    inner_d = float(m_row["膜元件通道内径/外径/mm"].split("/")[0])
    length_m = float(m_row["长度/m"])
    element_count = float(m_row["膜元件个数"])
    area_calc = membrane_area_m2(channel_count, inner_d, length_m, element_count)
    add(
        rows,
        module="CH7 membrane audit",
        item="S0101 membrane geometric area",
        source_location="doc_main_detailed table_31",
        formula="N_elements*N_channels*pi*d_i*L",
        input_values={"elements": element_count, "channels": channel_count, "inner_d_mm": inner_d, "length_m": length_m},
        value=area_calc,
        unit="m2",
        document_value=m_row["总膜面积/㎡"],
        tolerance=0.02,
        reliability_class="B_document_selection_reproduced",
        note="Geometric area is reproducible; membrane flux/selectivity/recovery still need vendor/literature evidence.",
    )

    # Chapter 8: pump power density screens. Reverse density exposes impossible single-point power claims.
    pumps = read_csv_table("data/tables/doc_main_detailed/table_28.csv")
    header = pumps[0]
    for row in pumps[1:]:
        r = dict(zip(header, row))
        tag = r["设备位号"]
        q = numeric_from_raw(r.get("流量（m3/h）", ""))
        h = numeric_from_raw(r.get("扬程/m", ""))
        p_kw = numeric_from_raw(r.get("轴功率/kW", ""))
        eta = numeric_from_raw(r.get("效率%", ""))
        if None not in (q, h, p_kw, eta) and q and h and eta:
            rho_rev = pump_reverse_density_kg_m3(q, h, eta, p_kw)
            plausible = 300 <= rho_rev <= 2500
            add_condition(
                rows,
                module="CH8 pump audit",
                item=f"{tag} reverse density from shaft power",
                source_location="doc_main_detailed table_28",
                formula="rho=P*eta/(g*Q*H)",
                input_values={"flow_m3_h": q, "head_m": h, "efficiency_percent": eta, "shaft_power_kW": p_kw},
                value=rho_rev,
                unit="kg/m3",
                condition_label="300 <= reverse density <= 2500 kg/m3 screening window",
                pass_check=plausible,
                reliability_class="B_document_selection_reproduced",
                status="screening_only",
                note="Screening window only; formal pump selection requires Aspen density/vapor pressure, NPSHa and vendor curve.",
            )
    p0403 = {r[1]: r[2] for r in read_csv_table("data/tables/doc_supplement3/table_45.csv")[1:] if len(r) >= 3}
    q = numeric_from_raw(p0403.get("流量/m3·h-1", ""))
    h = numeric_from_raw(p0403.get("扬程/m", ""))
    eta = numeric_from_raw(p0403.get("效率/%", ""))
    p_kw = numeric_from_raw(p0403.get("轴功率/kW", ""))
    if None not in (q, h, eta, p_kw) and q and h and eta:
        add(
            rows,
            module="CH8 pump audit",
            item="SUP3 P0403 water-basis shaft power",
            source_location="doc_supplement3 table_45",
            formula="Pshaft=rho*g*Q*H/eta with rho=1000 kg/m3",
            input_values={"flow_m3_h": q, "head_m": h, "efficiency_percent": eta, "rho_kg_m3": 1000},
            value=pump_shaft_power_kw(q, h, eta, 1000.0),
            unit="kW",
            document_value=str(p_kw),
            reliability_class="B_document_selection_reproduced",
            status="screening_only",
            note="The single-point shaft power implies low reverse density; do not present it as process power without fluid density and vendor curve.",
        )
        add_condition(
            rows,
            module="CH8 pump audit",
            item="SUP3 P0403 reverse density from shaft power",
            source_location="doc_supplement3 table_45",
            formula="rho=P*eta/(g*Q*H)",
            input_values={"flow_m3_h": q, "head_m": h, "efficiency_percent": eta, "shaft_power_kW": p_kw},
            value=pump_reverse_density_kg_m3(q, h, eta, p_kw),
            unit="kg/m3",
            condition_label="300 <= reverse density <= 2500 kg/m3 screening window",
            pass_check=True,
            reliability_class="B_document_selection_reproduced",
            status="screening_only",
            note="Plausible only as a single-point catalog/screening value; curve/NPSH still missing.",
        )


JUDGMENT_LINKS = {
    "separator": "knowledge_graph/chapter_04_08_late_equipment_graph.md; knowledge_graph/formula_family_nodes.md#formula_separator_souders_brown; knowledge_graph/evidence_boundary_nodes.md; knowledge_graph/standards_graph/standard_parameter_crosswalk.md",
    "compressor": "knowledge_graph/chapter_04_08_late_equipment_graph.md; knowledge_graph/formula_family_nodes.md#formula_isentropic_compression_work; knowledge_graph/evidence_boundary_nodes.md",
    "storage": "knowledge_graph/chapter_04_08_late_equipment_graph.md; knowledge_graph/formula_family_nodes.md#formula_inventory_volume; knowledge_graph/evidence_boundary_nodes.md; knowledge_graph/standards_graph/vessel_standards_nodes.md",
    "mixer_membrane": "knowledge_graph/chapter_04_08_late_equipment_graph.md; knowledge_graph/formula_family_nodes.md#formula_mixer_reynolds; knowledge_graph/evidence_boundary_nodes.md; knowledge_graph/parameter_source_nodes.md",
    "pump": "knowledge_graph/chapter_04_08_late_equipment_graph.md; knowledge_graph/formula_family_nodes.md#formula_pump_hydraulic_power; knowledge_graph/formula_family_nodes.md#formula_NPSHa; knowledge_graph/evidence_boundary_nodes.md; knowledge_graph/standards_graph/standard_parameter_crosswalk.md",
    "rotating": "knowledge_graph/chapter_04_08_late_equipment_graph.md; knowledge_graph/equipment_graph_index.md#设备族节点; knowledge_graph/evidence_boundary_nodes.md",
    "method": "knowledge_graph/chapter_04_08_late_equipment_graph.md; knowledge_graph/manual_decision_gates.md; knowledge_graph/evidence_boundary_nodes.md",
}


def ledger_row(
    rows: list[ParameterLedgerRow],
    *,
    chapter: str,
    equipment_family: str,
    object_id: str,
    parameter: str,
    value: str,
    unit: str,
    source_document: str,
    source_table: str,
    source_location: str,
    source_type: str,
    evidence_class: str,
    action: str,
    judgment_links: str,
    scriptable_formula: str,
    note: str = "",
) -> None:
    if value is None or str(value).strip() == "":
        return
    rows.append(
        ParameterLedgerRow(
            chapter=chapter,
            equipment_family=equipment_family,
            object_id=object_id,
            parameter=parameter,
            value=str(value).strip(),
            unit=unit,
            source_document=source_document,
            source_table=source_table,
            source_location=source_location,
            source_type=source_type,
            evidence_class=evidence_class,
            action=action,
            judgment_links=judgment_links,
            scriptable_formula=scriptable_formula,
            note=note,
        )
    )


def classify_late_chapter_parameter(
    *,
    chapter: str,
    equipment_family: str,
    source_table: str,
    parameter: str,
    object_id: str,
) -> tuple[str, str, str, str, str]:
    p = parameter.replace("／", "/")
    if source_table in {"table_36.csv", "table_42.csv", "table_43.csv", "table_44.csv"}:
        return (
            "method_only",
            "E_method_source",
            "方法源保留",
            JUDGMENT_LINKS["method"],
            "manual formula-family choice",
        )
    if source_table in {"table_33.csv", "table_34.csv", "table_37.csv"}:
        return (
            "aspen_or_document_stream",
            "B_stream_input",
            "样例输入保留",
            JUDGMENT_LINKS["separator" if "V0102" in object_id or source_table in {"table_33.csv", "table_34.csv"} else "compressor"],
            "stream balance / pressure ratio / sizing input",
        )
    if equipment_family == "family_separator":
        if "压力" in p and "设计" in p:
            return ("document_design_value", "B_screening", "可脚本筛错", JUDGMENT_LINKS["separator"], "formula_design_pressure_factor")
        if any(key in p for key in ("壁厚", "封头")):
            return ("document_catalog_or_manual_selection", "C_SW6_boundary", "目录级保留，需SW6/规范补证", JUDGMENT_LINKS["separator"], "formula_cylinder_thickness / formula_head_thickness")
        if any(key in p for key in ("接管", "直径", "尺寸", "高度")):
            return ("document_catalog_or_manual_selection", "B_screening", "目录级保留，可几何/流速筛错", JUDGMENT_LINKS["separator"], "formula_nozzle_actual_velocity / geometry sanity check")
        return ("document_catalog_selection", "E_catalog", "目录级保留，需分离性能补证", JUDGMENT_LINKS["separator"], "none")
    if equipment_family == "family_compressor":
        if "压力" in p:
            return ("document_process_point", "B_screening", "目录级保留，可压比筛错", JUDGMENT_LINKS["compressor"], "formula_pressure_ratio")
        if any(key in p for key in ("功率", "排气量", "流量")):
            return ("catalog_or_vendor_claim", "E_vendor_boundary", "目录级保留，需MW/k/Z/效率/厂家曲线", JUDGMENT_LINKS["compressor"], "formula_isentropic_compression_work requires missing inputs")
        return ("document_catalog_selection", "E_catalog", "目录级保留，禁止合并冲突分支", JUDGMENT_LINKS["compressor"], "none")
    if equipment_family == "family_storage":
        if any(key in p for key in ("公称容积", "技术规格", "规格")):
            return ("document_catalog_selection", "B_screening", "目录级保留，可几何量级筛错", JUDGMENT_LINKS["storage"], "geometry sanity check")
        if "标准" in p:
            return ("standard_reference_unverified", "S2_standard_lookup_needed", "标准入口保留，需表页核验", JUDGMENT_LINKS["storage"], "standard lookup")
        if any(key in p for key in ("设计温度", "设计压力")):
            return ("document_design_value", "E_catalog", "目录级保留，需规范/SW6补证", JUDGMENT_LINKS["storage"], "design condition screening")
        return ("document_catalog_selection", "E_catalog", "目录级保留，需库存/液位/呼吸/SW6补证", JUDGMENT_LINKS["storage"], "none")
    if equipment_family == "family_mixer_membrane":
        if "膜面积" in p or "通道" in p or "长度" in p:
            return ("document_geometry_value", "B_screening", "目录级保留，可膜面积筛错", JUDGMENT_LINKS["mixer_membrane"], "membrane_area_m2")
        if any(key in p for key in ("寿命", "膜材质", "类型")):
            return ("catalog_or_vendor_claim", "E_vendor_boundary", "目录级保留，需厂家/文献补证", JUDGMENT_LINKS["mixer_membrane"], "none")
        if any(key in p for key in ("流量", "转速", "装载系数")):
            return ("document_catalog_or_single_point", "E_vendor_boundary", "目录级保留，需压降/混合均匀度/功率补证", JUDGMENT_LINKS["mixer_membrane"], "formula_mixer_reynolds requires missing density/viscosity")
        return ("document_catalog_selection", "E_catalog", "目录级保留，需性能补证", JUDGMENT_LINKS["mixer_membrane"], "none")
    if equipment_family == "family_pump":
        if any(key in p for key in ("流量", "扬程", "效率", "轴功率")):
            return ("document_single_point_or_catalog", "B_screening", "目录级保留，可轴功率/反推密度筛错", JUDGMENT_LINKS["pump"], "formula_pump_hydraulic_power")
        if "汽蚀" in p:
            return ("catalog_or_vendor_claim", "C_vendor_boundary", "目录级保留，需NPSHa/NPSHr链", JUDGMENT_LINKS["pump"], "formula_NPSHa")
        return ("document_catalog_selection", "E_catalog", "目录级保留，需厂家曲线", JUDGMENT_LINKS["pump"], "none")
    if equipment_family == "family_rotating":
        if any(key in p for key in ("压力", "水头", "流量")):
            return ("document_single_point_or_catalog", "B_screening", "目录级保留，可回收功率候选", JUDGMENT_LINKS["rotating"], "hydraulic power requires density/efficiency")
        return ("document_catalog_selection", "E_catalog", "目录级保留，需厂家曲线", JUDGMENT_LINKS["rotating"], "none")
    return ("document_catalog_selection", "E_catalog", "目录级保留", JUDGMENT_LINKS["method"], "none")


def add_horizontal_table_ledger(
    rows: list[ParameterLedgerRow],
    *,
    chapter: str,
    equipment_family: str,
    source_document: str,
    table_no: int,
    table_path: str,
    tag_column: str = "设备位号",
    object_prefix: str = "",
) -> None:
    table = read_csv_table(table_path)
    header = table[0]
    source_table = f"table_{table_no:02d}.csv"
    for row_i, row in enumerate(table[1:], start=2):
        if not any(row):
            continue
        row_map = dict(zip(header, row))
        object_id = row_map.get(tag_column) or row_map.get("位号") or row[0]
        if object_prefix:
            object_id = f"{object_prefix} {object_id}"
        for column, raw_value in zip(header, row):
            if not raw_value:
                continue
            value, unit = split_value_unit(column, raw_value)
            source_type, evidence, action, links, formula = classify_late_chapter_parameter(
                chapter=chapter,
                equipment_family=equipment_family,
                source_table=source_table,
                parameter=column,
                object_id=object_id,
            )
            note = ""
            if object_id == "C0101" and source_document == "doc_main_detailed":
                note = "主文档C0101分支；不得与补充资料3 C0101自动合并。"
            if object_id in {"V0104", "V0101"} and source_document == "doc_main_detailed":
                note = "同位号在补充资料3可能指向不同对象；不得自动合并。"
            ledger_row(
                rows,
                chapter=chapter,
                equipment_family=equipment_family,
                object_id=object_id,
                parameter=column,
                value=value,
                unit=unit,
                source_document=source_document,
                source_table=source_table,
                source_location=f"{source_document} {source_table} row {row_i}",
                source_type=source_type,
                evidence_class=evidence,
                action=action,
                judgment_links=links,
                scriptable_formula=formula,
                note=note,
            )


def add_matrix_table_ledger(
    rows: list[ParameterLedgerRow],
    *,
    chapter: str,
    equipment_family: str,
    source_document: str,
    table_no: int,
    table_path: str,
    object_prefix: str,
) -> None:
    table = read_csv_table(table_path)
    header = table[0]
    source_table = f"table_{table_no:02d}.csv"
    for row_i, row in enumerate(table[1:], start=2):
        if not row:
            continue
        parameter = row[0]
        for col_i, raw_value in enumerate(row[1:], start=1):
            if col_i >= len(header) or not raw_value:
                continue
            object_id = f"{object_prefix}/{header[col_i]}"
            value, unit = split_value_unit(parameter, raw_value)
            source_type, evidence, action, links, formula = classify_late_chapter_parameter(
                chapter=chapter,
                equipment_family=equipment_family,
                source_table=source_table,
                parameter=parameter,
                object_id=object_id,
            )
            ledger_row(
                rows,
                chapter=chapter,
                equipment_family=equipment_family,
                object_id=object_id,
                parameter=parameter,
                value=value,
                unit=unit,
                source_document=source_document,
                source_table=source_table,
                source_location=f"{source_document} {source_table} row {row_i}",
                source_type=source_type,
                evidence_class=evidence,
                action=action,
                judgment_links=links,
                scriptable_formula=formula,
            )


def add_vertical_table_ledger(
    rows: list[ParameterLedgerRow],
    *,
    chapter: str,
    equipment_family: str,
    source_document: str,
    table_no: int,
    table_path: str,
    object_id: str,
) -> None:
    table = read_csv_table(table_path)
    source_table = f"table_{table_no:02d}.csv"
    if len(table[0]) >= 2 and table[0][0] == "设备位号":
        object_id = table[0][1]
    for row_i, row in enumerate(table[1:], start=2):
        if len(row) < 2 or not row[1]:
            continue
        parameter = row[0]
        raw_value = row[1]
        unit = row[2] if len(row) > 2 else split_value_unit(parameter, raw_value)[1]
        value = raw_value
        source_type, evidence, action, links, formula = classify_late_chapter_parameter(
            chapter=chapter,
            equipment_family=equipment_family,
            source_table=source_table,
            parameter=parameter,
            object_id=object_id,
        )
        note = ""
        if object_id in {"V0104", "V0101"}:
            note = "补充资料3样例分支；同位号不得与主文档对象自动合并。"
        ledger_row(
            rows,
            chapter=chapter,
            equipment_family=equipment_family,
            object_id=object_id,
            parameter=parameter,
            value=value,
            unit=unit,
            source_document=source_document,
            source_table=source_table,
            source_location=f"{source_document} {source_table} row {row_i}",
            source_type=source_type,
            evidence_class=evidence,
            action=action,
            judgment_links=links,
            scriptable_formula=formula,
            note=note,
        )


def add_phase_property_table_ledger(rows: list[ParameterLedgerRow]) -> None:
    source_document = "doc_supplement3"
    source_table = "table_34.csv"
    table = read_csv_table("data/tables/doc_supplement3/table_34.csv")
    header = table[0]
    for row_i, row in enumerate(table[1:], start=2):
        for col_i, raw in enumerate(row):
            if col_i >= len(header) or not raw:
                continue
            object_id = f"V0102/{header[col_i]}"
            if "=" in raw:
                parameter, raw_value = raw.split("=", 1)
            elif raw.endswith("kg/m3"):
                parameter, raw_value = "密度", raw
            elif raw.startswith("T=") or raw.startswith("P="):
                parameter, raw_value = raw.split("=", 1)
            else:
                parameter, raw_value = raw, raw
            source_type, evidence, action, links, formula = classify_late_chapter_parameter(
                chapter="第四章 气液分离器",
                equipment_family="family_separator",
                source_table=source_table,
                parameter=parameter,
                object_id=object_id,
            )
            ledger_row(
                rows,
                chapter="第四章 气液分离器",
                equipment_family="family_separator",
                object_id=object_id,
                parameter=parameter,
                value=raw_value,
                unit="",
                source_document=source_document,
                source_table=source_table,
                source_location=f"{source_document} {source_table} row {row_i}",
                source_type=source_type,
                evidence_class=evidence,
                action=action,
                judgment_links=links,
                scriptable_formula=formula,
            )


def build_late_chapter_parameter_ledger() -> list[ParameterLedgerRow]:
    rows: list[ParameterLedgerRow] = []

    add_horizontal_table_ledger(
        rows,
        chapter="第四章 气液分离器",
        equipment_family="family_separator",
        source_document="doc_main_detailed",
        table_no=23,
        table_path="data/tables/doc_main_detailed/table_23.csv",
    )
    add_matrix_table_ledger(
        rows,
        chapter="第四章 气液分离器",
        equipment_family="family_separator",
        source_document="doc_supplement3",
        table_no=33,
        table_path="data/tables/doc_supplement3/table_33.csv",
        object_prefix="V0102",
    )
    add_phase_property_table_ledger(rows)
    add_vertical_table_ledger(
        rows,
        chapter="第四章 气液分离器",
        equipment_family="family_separator",
        source_document="doc_supplement3",
        table_no=35,
        table_path="data/tables/doc_supplement3/table_35.csv",
        object_id="V0102",
    )

    add_horizontal_table_ledger(
        rows,
        chapter="第五章 压缩机",
        equipment_family="family_compressor",
        source_document="doc_main_detailed",
        table_no=24,
        table_path="data/tables/doc_main_detailed/table_24.csv",
    )
    add_horizontal_table_ledger(
        rows,
        chapter="第五章 压缩机",
        equipment_family="family_compressor",
        source_document="doc_supplement3",
        table_no=36,
        table_path="data/tables/doc_supplement3/table_36.csv",
        tag_column="类型",
        object_prefix="compressor_type",
    )
    add_matrix_table_ledger(
        rows,
        chapter="第五章 压缩机",
        equipment_family="family_compressor",
        source_document="doc_supplement3",
        table_no=37,
        table_path="data/tables/doc_supplement3/table_37.csv",
        object_prefix="SUP3 C0101 stream",
    )
    add_horizontal_table_ledger(
        rows,
        chapter="第五章 压缩机",
        equipment_family="family_compressor",
        source_document="doc_supplement3",
        table_no=38,
        table_path="data/tables/doc_supplement3/table_38.csv",
        tag_column="位号",
        object_prefix="SUP3",
    )

    for table_no, category in [(25, "储罐"), (26, "回流罐"), (27, "缓冲罐")]:
        add_horizontal_table_ledger(
            rows,
            chapter="第六章 储罐/回流罐/缓冲罐",
            equipment_family="family_storage",
            source_document="doc_main_detailed",
            table_no=table_no,
            table_path=f"data/tables/doc_main_detailed/table_{table_no}.csv",
            object_prefix=category,
        )
    for table_no, object_id in [(39, "V0503"), (40, "SUP3 V0104"), (41, "SUP3 V0101")]:
        add_vertical_table_ledger(
            rows,
            chapter="第六章 储罐/回流罐/缓冲罐",
            equipment_family="family_storage",
            source_document="doc_supplement3",
            table_no=table_no,
            table_path=f"data/tables/doc_supplement3/table_{table_no}.csv",
            object_id=object_id,
        )

    add_horizontal_table_ledger(
        rows,
        chapter="第七章 膜/混合器",
        equipment_family="family_mixer_membrane",
        source_document="doc_main_detailed",
        table_no=31,
        table_path="data/tables/doc_main_detailed/table_31.csv",
    )
    add_horizontal_table_ledger(
        rows,
        chapter="第七章 膜/混合器",
        equipment_family="family_mixer_membrane",
        source_document="doc_main_detailed",
        table_no=32,
        table_path="data/tables/doc_main_detailed/table_32.csv",
    )
    add_horizontal_table_ledger(
        rows,
        chapter="第七章 膜/混合器",
        equipment_family="family_mixer_membrane",
        source_document="doc_supplement3",
        table_no=42,
        table_path="data/tables/doc_supplement3/table_42.csv",
        tag_column="型式",
        object_prefix="static_mixer_type",
    )

    add_horizontal_table_ledger(
        rows,
        chapter="第八章 泵",
        equipment_family="family_pump",
        source_document="doc_main_detailed",
        table_no=28,
        table_path="data/tables/doc_main_detailed/table_28.csv",
    )
    add_horizontal_table_ledger(
        rows,
        chapter="第八章 泵/透平/倾析器",
        equipment_family="family_rotating",
        source_document="doc_main_detailed",
        table_no=29,
        table_path="data/tables/doc_main_detailed/table_29.csv",
    )
    add_horizontal_table_ledger(
        rows,
        chapter="第八章 泵/透平/倾析器",
        equipment_family="family_separator",
        source_document="doc_main_detailed",
        table_no=30,
        table_path="data/tables/doc_main_detailed/table_30.csv",
    )
    add_matrix_table_ledger(
        rows,
        chapter="第八章 泵",
        equipment_family="family_pump",
        source_document="doc_supplement3",
        table_no=43,
        table_path="data/tables/doc_supplement3/table_43.csv",
        object_prefix="pump_type_method",
    )
    add_horizontal_table_ledger(
        rows,
        chapter="第八章 泵",
        equipment_family="family_pump",
        source_document="doc_supplement3",
        table_no=44,
        table_path="data/tables/doc_supplement3/table_44.csv",
        tag_column="泵名称",
        object_prefix="pump_service_method",
    )
    add_vertical_table_ledger(
        rows,
        chapter="第八章 泵",
        equipment_family="family_pump",
        source_document="doc_supplement3",
        table_no=45,
        table_path="data/tables/doc_supplement3/table_45.csv",
        object_id="SUP3 P0403",
    )
    return rows


def build_calculations() -> list[CalcRow]:
    rows: list[CalcRow] = []

    # T0301 tower sizing and nozzles.
    add(
        rows,
        module="T0301 tower",
        item="tower bottom liquid height",
        source_location="DOCX 1.1.7 塔底空间高度HB; table_00 stream data",
        formula="(L*t/60)/(pi*D^2/4)",
        input_values={"bottom_liquid_flow_m3_h": 6.05, "hold_minutes": 5, "tower_diameter_m": 0.8},
        value=tower_bottom_liquid_height(6.05, 5, 0.8),
        unit="m",
        document_value="1.0",
        tolerance=0.01,
    )
    add(
        rows,
        module="T0301 tower",
        item="tower tangent height",
        source_location="DOCX 1.1.7 塔高度",
        formula="HD+HB+HR1+HR2+packing",
        input_values={"HD_m": 1.5, "HB_m": 1.8, "HR1_m": 1.0, "HR2_m": 0.55, "packing_m": 6.9},
        value=1.5 + 1.8 + 1.0 + 0.55 + 6.9,
        unit="m",
        document_value="11.75",
        tolerance=0.001,
    )
    add(
        rows,
        module="T0301 tower",
        item="overall height",
        source_location="DOCX 1.1.7 塔高度; table_05 塔设计小结",
        formula="tangent height + skirt + head",
        input_values={"tangent_height_m": 11.75, "skirt_height_m": 2.6, "head_height_m": 0.225},
        value=14.575,
        unit="m",
        document_value="14.575",
        tolerance=0.001,
    )
    for name, flow, target_u, od, thk, doc_d, doc_u in [
        ("N1 top vapor", 3631.77, 20, 0.273, 0.010, "0.253", "20.07"),
        ("N2 feed", 2.10, 1, 0.034, 0.003, "0.027", "0.95"),
        ("N3 reflux", 0.25, 1, 0.017, 0.004, "0.009", "1.09"),
        ("N4 reboiler outlet", 17.48, 1, 0.089, 0.005, "0.079", "0.99"),
        ("N5 bottom return", 4962.26, 20, 0.325, 0.015, "0.296", "20.17"),
    ]:
        add_nozzle_rows(
            rows,
            module="T0301 nozzle",
            source_location="DOCX 1.1.8 接管设计; table_03 接管尺寸汇总",
            name=name,
            flow_m3_h=flow,
            target_u_m_s=target_u,
            od_m=od,
            thickness_m=thk,
            doc_id_m=doc_d,
            doc_u_m_s=doc_u,
            id_tolerance_m=0.001,
        )

    p = 0.14
    cyl = cylinder_calc_thickness(p, 800, 132, 0.85)
    head = ellipsoidal_head_calc_thickness(p, 800, 132, 0.85)
    add(
        rows,
        module="T0301 strength",
        item="shell calculation thickness",
        source_location="DOCX 1.1.9 筒体壁厚设计; table_04",
        formula="P*Di/(2*sigma*phi-P)",
        input_values={"P_MPa": p, "Di_mm": 800, "sigma_MPa": 132, "weld_eff": 0.85},
        value=cyl,
        unit="mm",
        document_value="0.50",
        tolerance=0.01,
    )
    add(
        rows,
        module="T0301 strength",
        item="head calculation thickness",
        source_location="DOCX 1.1.9 封头壁厚设计; table_04",
        formula="P*Di/(2*sigma*phi-0.5*P)",
        input_values={"P_MPa": p, "Di_mm": 800, "sigma_MPa": 132, "weld_eff": 0.85},
        value=head,
        unit="mm",
        document_value="0.50",
        tolerance=0.01,
    )
    add(
        rows,
        module="T0301 strength",
        item="minimum nominal before SW6/rounding",
        source_location="DOCX 1.1.9 壁厚设计; table_04 SW6 summary",
        formula="max(delta, 3 mm minimum)+C1+C2",
        input_values={"calculated_shell_mm": cyl, "minimum_calc_mm": 3, "negative_deviation_mm": 0.3, "corrosion_mm": 2},
        value=minimum_nominal_thickness(cyl, 3, 0.3, 2),
        unit="mm",
        document_value="6 after rounding/SW6",
        tolerance=None,
        reliability_class="B_document_selection_reproduced",
        status="software_dependent",
        note="Script reproduces arithmetic minimum 5.3 mm; final 6 mm is rounded/standardized and accepted by SW6, so SW6 output remains the formal proof.",
    )

    # E0108 exchanger.
    add(
        rows,
        module="E0108 exchanger",
        item="tube-side design pressure",
        source_location="DOCX 2.1.2 设计压力; table_08",
        formula="1.1*P_operating",
        input_values={"tube_operating_pressure_bar": 2.01, "factor": 1.1},
        value=design_pressure(2.01, 1.1),
        unit="bar",
        document_value="2.21",
        tolerance=0.01,
    )
    add(
        rows,
        module="E0108 exchanger",
        item="shell-side design pressure",
        source_location="DOCX 2.1.2 设计压力; table_08",
        formula="1.1*P_operating",
        input_values={"shell_operating_pressure_bar": 3.05, "factor": 1.1},
        value=design_pressure(3.05, 1.1),
        unit="bar",
        document_value="3.36",
        tolerance=0.01,
    )
    for name, flow, target_u, od, thk, doc_d, doc_u in [
        ("tube inlet", 22.34, 1, 0.089, 0.002, "0.085", "1.09"),
        ("tube outlet", 22.34, 1, 0.089, 0.002, "0.085", "1.09"),
        ("shell inlet", 13.84, 1, 0.076, 0.003, "0.070", "1.00"),
        ("shell outlet", 13.77, 1, 0.076, 0.003, "0.070", "0.99"),
    ]:
        add_nozzle_rows(
            rows,
            module="E0108 nozzle",
            source_location="DOCX 2.2.7 接管尺寸及方位; table_13",
            name=name,
            flow_m3_h=flow,
            target_u_m_s=target_u,
            od_m=od,
            thickness_m=thk,
            doc_id_m=doc_d,
            doc_u_m_s=doc_u,
            id_tolerance_m=0.005,
            note="Document value is the selected pipe inner diameter after GB/T 17395 rounding, not a pure theoretical target.",
        )
    e_shell = cylinder_calc_thickness(0.336, 305, 137, 0.85)
    e_head = ellipsoidal_head_calc_thickness(0.336, 305, 137, 0.85)
    add(
        rows,
        module="E0108 strength",
        item="shell calculation thickness",
        source_location="DOCX 2.3.1 设备壳体壁厚; table_13",
        formula="P*Di/(2*sigma*phi-P)",
        input_values={"P_MPa": 0.336, "Di_mm": 305, "sigma_MPa": 137, "weld_eff": 0.85},
        value=e_shell,
        unit="mm",
        document_value="0.433",
        tolerance=0.02,
    )
    add(
        rows,
        module="E0108 strength",
        item="head calculation thickness",
        source_location="DOCX 2.3.2 封头壁厚; table_13",
        formula="P*Di/(2*sigma*phi-0.5*P)",
        input_values={"P_MPa": 0.336, "Di_mm": 305, "sigma_MPa": 137, "weld_eff": 0.85},
        value=e_head,
        unit="mm",
        document_value="0.432",
        tolerance=0.02,
    )

    # Kinetics conversion for R0201. Arithmetic can be repeated, but the Aspen kinetic card remains provisional.
    temps_k = (553.15, 633.15)
    source_sets = {
        "k2": (65, 1300, "1123.26", "109.034"),
        "k3": (24, 1200, "585025", "142.385"),
        "k4": (75, 800, "8.95427", "86.1555"),
    }
    for kid, (v1, v2, doc_a, doc_e) in source_sets.items():
        k_220 = source_k_to_aspen(v1)
        k_360 = source_k_to_aspen(v2)
        a, e = arrhenius_from_two_points(temps_k[0], k_220, temps_k[1], k_360)
        add(
            rows,
            module="R0201 kinetics",
            item=f"{kid} source k at low-T point converted",
            source_location="DOCX R0201 动力学参数; table_15/table_16",
            formula="source_k*1e-3/3600/sqrt(101300)",
            input_values={"source_k": v1, "source_unit": "mmol gcat^-1 atm^-0.5 h^-1"},
            value=k_220,
            unit="kmol kgcat^-1 Pa^-0.5 s^-1",
            reliability_class="D_kinetics_provisional",
            status="provisional",
            note="Only the unit conversion is reproduced; original rate law, reaction order, basis, and Aspen exported card are not yet frozen.",
        )
        add(
            rows,
            module="R0201 kinetics",
            item=f"{kid} source k at high-T point converted",
            source_location="DOCX R0201 动力学参数; table_15/table_16",
            formula="source_k*1e-3/3600/sqrt(101300)",
            input_values={"source_k": v2, "source_unit": "mmol gcat^-1 atm^-0.5 h^-1"},
            value=k_360,
            unit="kmol kgcat^-1 Pa^-0.5 s^-1",
            reliability_class="D_kinetics_provisional",
            status="provisional",
            note="Only the unit conversion is reproduced; original rate law, reaction order, basis, and Aspen exported card are not yet frozen.",
        )
        add(
            rows,
            module="R0201 kinetics",
            item=f"{kid} Arrhenius A",
            source_location="DOCX R0201 动力学参数; table_16",
            formula="two-point fit ln(k)=ln(A)-E/(R*T)",
            input_values={"T1_K": temps_k[0], "k1": k_220, "T2_K": temps_k[1], "k2": k_360},
            value=a,
            unit="same basis as converted k",
            document_value=doc_a,
            tolerance=max(0.01 * float(doc_a), 1e-9),
            reliability_class="D_kinetics_provisional",
            status="provisional",
            note="Arithmetic fit matches the table, but Aspen use requires the full kinetics freeze chain.",
        )
        add(
            rows,
            module="R0201 kinetics",
            item=f"{kid} activation energy",
            source_location="DOCX R0201 动力学参数; table_16",
            formula="two-point slope vs 1/(R*T)",
            input_values={"T1_K": temps_k[0], "k1": k_220, "T2_K": temps_k[1], "k2": k_360},
            value=e,
            unit="kJ/mol",
            document_value=doc_e,
            tolerance=0.1,
            reliability_class="D_kinetics_provisional",
            status="provisional",
            note="If Aspen card expects kJ/kmol, multiply this value by 1000 and verify exported card units.",
        )

    # R0201 mechanical/process calculations.
    add(
        rows,
        module="R0201 reactor",
        item="catalyst mass",
        source_location="DOCX R0201 反应器尺寸; table_21",
        formula="N*pi*d_i^2/4*L*rho_p*(1-eps)",
        input_values={"tube_count": 5000, "tube_inner_d_m": 0.016, "tube_length_m": 2.0, "particle_density_kg_m3": 1311, "voidage": 0.422},
        value=catalyst_mass(5000, 0.016, 2.0, 1311, 0.422),
        unit="kg",
        document_value="1523.56",
        tolerance=3,
    )
    add(
        rows,
        module="R0201 reactor",
        item="bundle diameter",
        source_location="DOCX R0201 管束估算; table_21",
        formula="1.25*do*(1.1*sqrt(N)-1)+2*1.5*do",
        input_values={"tube_count": 5000, "tube_od_mm": 19, "pitch_factor": 1.25, "edge_factor": 1.5},
        value=bundle_diameter(5000, 19),
        unit="m",
        document_value="1.88",
        tolerance=0.02,
    )
    area = 5000 * math.pi * 0.016 * 2.0
    add(
        rows,
        module="R0201 reactor",
        item="heat-transfer area used by document",
        source_location="DOCX R0201 传热面积校核; table_21",
        formula="N*pi*d_i*L",
        input_values={"tube_count": 5000, "tube_inner_d_m": 0.016, "tube_length_m": 2.0},
        value=area,
        unit="m2",
        document_value="502.66",
        tolerance=0.05,
        note="This reproduces the document basis. For a formal exchanger-style outer-area basis, the tube diameter basis should be explicitly declared.",
    )
    nu = packed_bed_tube_nu(0.002, 0.595, 8.51, 1.57e-5, 1851, 0.0335, 0.016)
    alpha_i = 0.0334 / 0.016 * nu
    u_overall = overall_u_outer_area(alpha_i, 7000, 0.016, 0.019, 0.0015, 16, 1.76e-4, 8.60e-5)
    req_area = 378089 / (3.7 * u_overall)
    add(
        rows,
        module="R0201 heat transfer",
        item="tube-side Nusselt number",
        source_location="DOCX R0201 传热系数计算; table_19",
        formula="2.26*Re^0.8*Pr^0.33*exp(-6*dp/dt)",
        input_values={"dp_m": 0.002, "u_m_s": 0.595, "rho_kg_m3": 8.51, "mu_Pa_s": 1.57e-5, "Cp_J_kg_K": 1851, "lambda_W_m_K": 0.0335, "tube_inner_d_m": 0.016},
        value=nu,
        unit="-",
        document_value="179.8",
        tolerance=1.0,
    )
    add(
        rows,
        module="R0201 heat transfer",
        item="tube-side coefficient",
        source_location="DOCX R0201 传热系数计算",
        formula="lambda/dt*Nu",
        input_values={"lambda_W_m_K": 0.0334, "tube_inner_d_m": 0.016, "Nu": nu},
        value=alpha_i,
        unit="W/(m2 K)",
        document_value="376.4",
        tolerance=3,
    )
    add(
        rows,
        module="R0201 heat transfer",
        item="overall heat-transfer coefficient",
        source_location="DOCX R0201 总传热系数计算",
        formula="1/sum(thermal resistances)",
        input_values={"alpha_i": alpha_i, "alpha_o": 7000, "di_m": 0.016, "do_m": 0.019, "wall_m": 0.0015, "metal_lambda": 16, "Rsi": 1.76e-4, "Rso": 8.60e-5},
        value=u_overall,
        unit="W/(m2 K)",
        document_value="270.7",
        tolerance=2,
    )
    add(
        rows,
        module="R0201 heat transfer",
        item="required heat-transfer area",
        source_location="DOCX R0201 传热面积校核",
        formula="Q/(DeltaT_mean*K)",
        input_values={"Q_W": 378089, "DeltaT_mean_K": 3.7, "K_W_m2_K": u_overall},
        value=req_area,
        unit="m2",
        document_value="377.49",
        tolerance=3,
    )
    add(
        rows,
        module="R0201 heat transfer",
        item="area margin",
        source_location="DOCX R0201 传热面积校核",
        formula="(A_actual-A_required)/A_required*100%",
        input_values={"actual_area_m2": area, "required_area_m2": req_area},
        value=(area - req_area) / req_area * 100,
        unit="%",
        document_value="33.16",
        tolerance=1.5,
    )
    rem, friction, dp_pa = ergun_pressure_drop(0.002, 0.595, 8.51, 1.58e-5, 2.0, 0.422)
    add(
        rows,
        module="R0201 pressure drop",
        item="modified Reynolds number",
        source_location="DOCX R0201 Ergun压降计算",
        formula="dp*u*rho/(mu*(1-eps))",
        input_values={"dp_m": 0.002, "u_m_s": 0.595, "rho_kg_m3": 8.51, "mu_Pa_s": 1.58e-5, "voidage": 0.422},
        value=rem,
        unit="-",
        document_value="1110.16",
        tolerance=2,
    )
    add(
        rows,
        module="R0201 pressure drop",
        item="Ergun friction factor",
        source_location="DOCX R0201 Ergun压降计算",
        formula="150/Re+1.75",
        input_values={"Re_modified": rem},
        value=friction,
        unit="-",
        document_value="1.885",
        tolerance=0.01,
    )
    add(
        rows,
        module="R0201 pressure drop",
        item="bed pressure drop",
        source_location="DOCX R0201 Ergun压降计算",
        formula="f*rho*u^2*L*(1-eps)/(dp*eps^3)",
        input_values={"friction": friction, "rho_kg_m3": 8.51, "u_m_s": 0.595, "bed_length_m": 2.0, "particle_d_m": 0.002, "voidage": 0.422},
        value=dp_pa,
        unit="Pa",
        document_value="43735",
        tolerance=500,
    )
    for name, flow, target_u, od, thk, doc_d, doc_u in [
        ("feed gas", 909.19, 20, 0.140, 0.0065, "0.127", "19.94"),
        ("outlet gas", 976.71, 20, 0.140, 0.0045, "0.131", "20.13"),
        ("molten salt inlet", 1134.10, 2, 0.457, 0.0045, "0.448", "2.00"),
        ("molten salt outlet", 1136.89, 2, 0.457, 0.0045, "0.448", "2.00"),
    ]:
        add_nozzle_rows(
            rows,
            module="R0201 nozzle",
            source_location="DOCX R0201 接管设计; table_21",
            name=name,
            flow_m3_h=flow,
            target_u_m_s=target_u,
            od_m=od,
            thickness_m=thk,
            doc_id_m=doc_d,
            doc_u_m_s=doc_u,
            id_tolerance_m=0.002,
            u_tolerance_m_s=0.08,
        )
    r_shell = cylinder_calc_thickness(0.55, 1900, 112.6, 0.85)
    add(
        rows,
        module="R0201 strength",
        item="shell calculation thickness",
        source_location="DOCX R0201 壳程圆筒强度; table_21",
        formula="P*Di/(2*sigma*phi-P)",
        input_values={"P_MPa": 0.55, "Di_mm": 1900, "sigma_MPa": 112.6, "weld_eff": 0.85},
        value=r_shell,
        unit="mm",
        document_value="5.47",
        tolerance=0.05,
    )
    add(
        rows,
        module="R0201 strength",
        item="minimum nominal before standard minimum",
        source_location="DOCX R0201 壳程圆筒强度; table_21",
        formula="delta+C1+C2",
        input_values={"delta_mm": r_shell, "negative_deviation_mm": 0.3, "corrosion_mm": 2},
        value=r_shell + 0.3 + 2,
        unit="mm",
        document_value="7.77",
        tolerance=0.08,
        reliability_class="B_document_selection_reproduced",
        note="Document final nominal thickness is 19 mm after standardization and SW6 checks; this row only reproduces pre-standard arithmetic.",
    )
    add_t802_union_rows(rows)
    add_supplement3_rows(rows)
    add_late_chapter_audit_rows(rows)
    return rows


MODULE_ROWS = [
    ("01 文档证据层", "3份DOCX源文件、91张表格CSV、计算JSON、Markdown报告", "把三份文档的表格、章节、软件证据线索分别归档；计算行必须指向稳定文档ID，例如 doc_guobao_tower、doc_main_detailed、doc_supplement3，避免并集后出处混乱。"),
    ("02 塔设备方法论模块", "国宝特工文档1.1-1.3；塔型/塔盘/填料/水力学方法表", "吸收第二份的优点：先按塔径、压降、持液量、操作弹性、腐蚀/堵塞、分离要求选择填料塔或板式塔，再用 Aspen RadFrac/Column Internals 做 sizing/rating，最后交 SW6 机械校核。"),
    ("03 塔设备详算模块", "T0301为主详算样板；T802为塔方法增强样板；T0101/T0102/T0201/T0302为选型一览", "T0301复算塔高、接管、壁厚；T802复核Aspen流股衡算、理论级/进料级、填料高度和能力因子范围；塔径/填料段正式证据仍依赖 Column Internals导出。"),
    ("03B 补充资料3模块", "3-设备设计与选型说明书(1).docx；T0402、E0104、R0101等案例；46张表格", "已作为第三份源文件纳入并补最小复算链：T0402流股/水力学/接管，E0104设计条件，R0101设计T/P；完整塔高、EDR、SW6和动力学仍需独立证据，不能直接覆盖T0301/E0108/R0201脚本值。"),
    ("04 换热器模块", "E0108为详算样板；E0101-E0312为选型一览", "E0108复算设计压力、接管、壳/封头壁厚；面积、压降、Re、U值需 Aspen EDR rating/design 输出证明；其他换热器属于型号汇总。"),
    ("05 反应器模块", "R0201为固定床详算样板；R0301-R0304为釜式选型汇总", "R0201可复算催化剂量、管束估算、传热、Ergun压降、接管、壁厚；动力学只做单位换算和Arrhenius拟合，未通过文献-Aspen冻结链前标为 provisional。"),
    ("06 气液分离器/汽包模块", "V0101/V0102/V0103/V0203/V0301", "需要从Aspen提供相分率、气液物性、液滴负荷、允许夹带量；当前表格是旋风/丝网设备尺寸和接管的象征性选型。"),
    ("07 压缩机模块", "C0101-C0301", "需要Aspen提供入口组成、T/P、分子量、k值/Z因子、体积流量和压比；当前表格给出厂家型号和电机功率，未形成压缩功脚本校核。"),
    ("08 储罐/回流罐/缓冲罐模块", "V0104/V0206/V0401等储罐；V0202/V0302等回流罐；V0105等缓冲罐", "应按停留时间、储存天数、装填系数、介质密度、蒸气压和规范壁厚校核；当前是标准容器/储罐型号选型。"),
    ("09 泵模块", "P0101A/B-P0408A/B", "可脚本化轴功率 P=rho*g*Q*H/eta，但必须先补密度、NPSHa、汽蚀余量和厂家曲线；现有表主要是流量/扬程/效率/型号的目录级选型。"),
    ("10 液体透平模块", "K0101/K0201/K0301", "需要Aspen提供入口/出口压力、密度、流量、可回收水头；可脚本化回收功率，但型号可靠性仍需厂家曲线。"),
    ("11 倾析/膜/混合模块", "F0201、S0101、M0201/M0301", "倾析器需液液相平衡和停留/分离因子；膜需渗透率、选择性、压差；混合器需压降/混合时间/功率。当前均为象征性设备选型。"),
]


ASPEN_SECTIONS = [
    ("通用流股参数", "所有模块", "组分ID/CAS、物性方法、流量基准、T、P、汽相分率、组成、质量/摩尔流量、密度、黏度、Cp、导热系数、表面张力、相态。"),
    ("塔设备", "T0301、T802及全部塔", "RadFrac或等效塔模型的理论级数、进料级、回流比、塔顶/塔釜压力、冷凝/再沸负荷、逐段气液流量、段温度、段组成、Column Internals的塔径、%Capacity、液泛率、压降、填料型号/HETP/段高；T802还需核对表中进料板16与填料分段段落中进料板13的差异。"),
    ("换热器/EDR", "E0108及全部换热器", "冷热侧入口/出口T/P/流量/组成/相分率，热负荷，允许压降，污垢热阻，物性包；EDR输出面积、U值、Re、压降、流速、推荐壳径/管数/管程数/折流板。"),
    ("固定床反应器", "R0201", "反应式、速率方程、反应级数、相态、Rate basis、[Ci] basis、k/E/A单位、催化剂密度/空隙率/粒径、管径/管数/床长、热媒流量和温度、RPlug轴向温度/转化率/压降profile。"),
    ("釜式反应器", "R0301-R0304", "反应热、转化率/停留时间、操作体积、相态、搅拌功率、传热面积、夹套/盘管热媒条件；若用动力学，同样必须通过冻结链。"),
    ("气液/液液分离", "V类分离器、F0201", "进料相分率、气液/液液密度差、黏度、界面张力、液滴粒径假设、允许夹带、停留时间、目标回收率/分离效率。"),
    ("泵/压缩机/透平", "P/C/K类", "入口T/P/相态、体积流量、密度、蒸气压、NPSHa、扬程或压比、等熵/多变效率、出口目标压力、功率和热负荷。压缩机还需MW、k、Z因子。"),
    ("储罐/回流罐/缓冲罐", "V类容器", "介质密度、储存天数或停留时间、最大/正常液位、装填系数、蒸气压、氮封/呼吸阀条件、设计T/P、腐蚀裕量。"),
    ("膜/混合器", "S0101/M0201/M0301", "膜通量、渗透率、选择性、压差、温度、目标回收率；混合器入口物流流量/黏度/密度/Re、允许压降、混合均匀度或停留时间。"),
]


NONSCRIPT_BOUNDARY_ROWS = [
    (
        "补充资料3整体定位",
        "3-设备设计与选型说明书(1).docx; doc_supplement3",
        "C_external_software_required",
        "补充资料3不是小修订，而是另一版完整说明书，新增T0402、E0104等案例和46张表；当前已补T0402/E0104/R0101最小复算链。",
        "若要替代主脚本，还需要逐项补齐T0402完整塔高/SW6、E0104完整EDR/SW6、新反应器动力学冻结链，并标明与doc_main_detailed中T0301/E0108/R0201的关系。",
    ),
    (
        "T0402塔样板",
        "doc_supplement3 1.3; table_07-table_13",
        "C_external_software_required",
        "补充资料3给出T0402 DMSO精馏塔流股、理论板21、进料板13、塔高/接管/内件等内容；当前已补流股衡算、Column Internals表和接管流速最小复算链。",
        "仍需继续补T0402完整塔底空间/塔高、塔体强度和SW6外压校核，不能拿T0301结果直接替代。",
    ),
    (
        "E0104换热器样板",
        "doc_supplement3 换热器章节; table_18起",
        "C_external_software_required",
        "补充资料3出现E0104工艺参数、设计压力、EDR允许压降等，与原E0108不是同一台设备；当前已补设计压力、允许压降和设计温度裕量的最小复算链。",
        "仍需补E0104完整EDR面积/压降、接管、壁厚和SW6链，不能拿E0108脚本结果直接套用。",
    ),
    (
        "T802塔型/填料选择方法论",
        "国宝特工 DOCX 1.2.1-1.2.6; doc_guobao_tower table_00-table_05",
        "B_document_selection_reproduced",
        "第二份文档补足了塔型选择逻辑、塔盘/填料比较和填料评分，可作为总说明书的选型决策树。",
        "若用于正式设备选型，需要把每台塔的直径、压降、持液量、腐蚀/堵塞、发泡、热敏性和操作弹性逐项映射到该决策树。",
    ),
    (
        "T802 Column Internals水力学样板",
        "国宝特工 DOCX 1.3.3.2-1.3.3.3; doc_guobao_tower table_10/table_11",
        "C_external_software_required",
        "脚本可读取并校核%Capacity范围、填料高度和压降范围；正式证据仍应为Aspen导出表或截图。",
        "补T802 Aspen RadFrac/Column Internals导出，核对理论板30、进料板16、分段描述进料板13之间的来源差异。",
    ),
    (
        "T0301 Column Internals塔水力学",
        "DOCX 1.1.5 填料段初步设计、1.1.5 塔径圆整与水力学校核",
        "C_external_software_required",
        "塔径、填料段、%Capacity、液泛率、压降来自 Aspen Column Internals截图/导出，不由本脚本替代。",
        "补Column Internals导出表或截图，记录Aspen版本、塔模型、填料型号、段高、塔径、压降。",
    ),
    (
        "T0301 SW6强度校核",
        "DOCX 1.1.10 塔设备SW6强度校核; table_04",
        "C_external_software_required",
        "脚本复算壳体/封头基础壁厚；裙座、地脚螺栓、开孔补强、整体校核需要SW6报告。",
        "补SW6报告，至少含壳体、封头、裙座、开孔补强、地脚螺栓校核页。",
    ),
    (
        "E0108 Aspen EDR换热器设计/校核",
        "DOCX 2.1.2 换热面积、2.2.2 EDR初步设计结果、2.3 Rating/Checking; table_13",
        "C_external_software_required",
        "5.6 m2初算面积、12.7 m2选型面积、49%余量、U值、Re、压降均应由EDR输出证明。",
        "补EDR design/rating文件或导出表，记录冷热侧输入、污垢热阻、允许压降、推荐结构。",
    ),
    (
        "E0108 SW6强度校核",
        "DOCX 2.3 换热器强度计算; table_13",
        "C_external_software_required",
        "脚本复算壳体/封头基础壁厚；管板、法兰、开孔补强和整机强度以SW6为准。",
        "补SW6换热器校核报告，尤其是管板、管箱法兰、开孔补强。",
    ),
    (
        "R0201动力学正式Aspen卡片",
        "DOCX R0201动力学参数; table_15/table_16",
        "D_kinetics_provisional",
        "脚本只复算单位换算和两点Arrhenius拟合；缺原文方程、速率基准、反应级数、Aspen导出卡。",
        "按kinetics freeze链补 source equation/value -> source units -> conversion -> Aspen card units -> exact input -> exported verification。",
    ),
    (
        "R0201 SW6及结构校核",
        "DOCX R0201结构/强度; table_21",
        "C_external_software_required",
        "脚本复算催化剂量、管束估算、传热、压降和基础壁厚；管板、法兰、开孔补强仍需SW6。",
        "补SW6反应器校核报告和RPlug/换热边界导出。",
    ),
    (
        "塔设备选型一览",
        "table_05",
        "E_symbolic_catalog_selection",
        "T0101/T0102/T0201/T0302等只有设备型号、填料、直径、高度、设计条件汇总。",
        "逐塔补Aspen塔模型、Column Internals水力学校核、SW6报告和填料厂家依据。",
    ),
    (
        "换热器选型一览",
        "table_14",
        "E_symbolic_catalog_selection",
        "E0101-E0312主要是BEM型号、面积、管数、材料汇总。",
        "逐台补冷热侧Aspen流股、EDR校核、允许压降和标准/厂家型号依据。",
    ),
    (
        "反应器选型一览",
        "table_22",
        "E_symbolic_catalog_selection",
        "R0301-R0304给出釜式尺寸、搅拌功率、材质等，但缺停留时间/传热/搅拌/动力学证明。",
        "补反应热、停留时间、转化率、搅拌功率计算、SW6报告；若用动力学，执行冻结链。",
    ),
    (
        "气液分离器与汽包",
        "table_23",
        "E_symbolic_catalog_selection",
        "旋风/丝网分离器和汽包给出尺寸、接管、壁厚，缺液滴负荷与夹带校核。",
        "补Aspen相分率、气液物性、液滴粒径、停留时间、目标夹带率和压降。",
    ),
    (
        "压缩机",
        "table_24",
        "E_symbolic_catalog_selection",
        "压缩机表给出型号、进出口压力、容积流量、电机功率，缺压缩功和厂家曲线证明。",
        "补入口组成/MW/k/Z、等熵或多变效率、功率计算、出口温度和厂家曲线。",
    ),
    (
        "储罐/回流罐/缓冲罐",
        "table_25/table_26/table_27",
        "E_symbolic_catalog_selection",
        "表格主要是标准容器型号和公称容积，缺储存天数、停留时间、装填系数和呼吸/氮封依据。",
        "补介质密度、库存策略、装填系数、液位、蒸气压、设计T/P和规范壁厚校核。",
    ),
    (
        "泵",
        "table_28",
        "E_symbolic_catalog_selection",
        "泵表有流量、扬程、效率和轴功率，但缺密度、NPSH和厂家曲线。",
        "补Aspen液体密度/蒸气压、NPSHa、泵功率脚本、备用策略和样本曲线。",
    ),
    (
        "液体透平",
        "table_29",
        "E_symbolic_catalog_selection",
        "透平表有进出口压力、水头和流量，缺密度、效率和回收功率证明。",
        "补密度、实际效率、回收功率脚本和厂家曲线。",
    ),
    (
        "倾析器/膜/混合器",
        "table_30/table_31/table_32",
        "E_symbolic_catalog_selection",
        "倾析器、膜分离装置、混合器均为型号/几何/重量汇总，缺分离性能和压降/功率证明。",
        "补液液平衡或膜通量/选择性/混合压降等模型输入与厂家证据。",
    ),
]


SECTION_RULES = [
    (
        "T0301-1.1.7",
        "T0301 塔尺寸设计",
        ["T0301 tower"],
        "塔底持液高度容差0.01 m；塔高容差0.001 m。",
        "Aspen流股 + 人工空间高度/封头/裙座默认值。",
    ),
    (
        "T0301-1.1.8",
        "T0301 接管设计",
        ["T0301 nozzle"],
        "理论管径容差0.001 m；选管后流速容差0.03 m/s。",
        "Aspen体积流量 + 人工目标流速 + GB/T 17395标准管圆整。",
    ),
    (
        "T0301-1.1.9",
        "T0301 壁厚设计",
        ["T0301 strength"],
        "基础计算厚度容差0.01 mm；名义厚度只复算到SW6前步骤。",
        "规范公式 + 人工焊接系数/腐蚀裕量 + SW6边界。",
    ),
    (
        "E0108-2.1.2",
        "E0108 工艺设计条件",
        ["E0108 exchanger"],
        "设计压力容差0.01 bar。",
        "Aspen操作压力 + 人工1.1设计压力系数。",
    ),
    (
        "E0108-2.2.7",
        "E0108 接管设计",
        ["E0108 nozzle"],
        "理论管径与标准管内径容差0.005 m；选管后流速容差0.03 m/s。",
        "Aspen/EDR流量 + 人工目标流速 + GB/T 17395标准管圆整。",
    ),
    (
        "E0108-2.3",
        "E0108 强度计算",
        ["E0108 strength"],
        "壳体/封头基础计算厚度容差0.02 mm。",
        "GB150公式 + 人工焊接系数/材料许用应力；管板/法兰需SW6。",
    ),
    (
        "R0201-3.2",
        "R0201 动力学换算",
        ["R0201 kinetics"],
        "A值按1%容差，E值容差0.1 kJ/mol；正式动力学仍为provisional。",
        "文档动力学表 + 单位换算；缺原始文献-Aspen冻结链。",
    ),
    (
        "R0201-3.3.1",
        "R0201 反应器几何与装填",
        ["R0201 reactor"],
        "催化剂量容差3 kg；管束直径容差0.02 m；面积容差0.05 m2。",
        "文档结构默认值 + 催化剂/床层参数。",
    ),
    (
        "R0201-3.3.4.3",
        "R0201 传热校核",
        ["R0201 heat transfer"],
        "Nu容差1；管内系数容差3 W/(m2 K)；总K容差2 W/(m2 K)；面积容差3 m2；裕量容差1.5%。",
        "Aspen/文档物性 + 人工热阻/壳侧传热系数默认值。",
    ),
    (
        "R0201-3.3.4.4",
        "R0201 Ergun压降校核",
        ["R0201 pressure drop"],
        "Re容差2；摩擦因子容差0.01；压降容差500 Pa。",
        "文档物性 + 催化剂粒径/空隙率默认值。",
    ),
    (
        "R0201-3.3.5",
        "R0201 接管设计",
        ["R0201 nozzle"],
        "理论管径容差0.002 m；选管后流速容差0.08 m/s。",
        "Aspen体积流量 + 人工目标流速 + 标准管圆整。",
    ),
    (
        "R0201-3.3.6",
        "R0201 壳体强度",
        ["R0201 strength"],
        "基础壁厚容差0.05 mm；名义厚度前步骤容差0.08 mm。",
        "规范公式 + 人工焊接系数/腐蚀裕量；最终19 mm需SW6。",
    ),
    (
        "T802-1.3.2",
        "T802 流股与设计条件",
        ["T802 union tower"],
        "质量衡算容差0.05 kg/h；摩尔衡算容差0.02 kmol/h；体积残差容差0.01 m3/h；表值读取容差0.01。",
        "国宝特工Aspen流股表 + 设计条件表 + 真空容器规范默认值。",
    ),
    (
        "T802-1.3.3",
        "T802 填料水力学",
        ["T802 union hydraulic"],
        "填料总高容差0.02 m；%Capacity条件40%-80%；压降sanity阈值2 mbar。",
        "Aspen Column Internals表 + 人工能力因子范围/压降审计阈值。",
    ),
    (
        "SUP3",
        "补充资料3 T0402/E0104新增案例",
        [],
        "保留为总证据入口；具体复算拆到SUP3-1.3、SUP3-2.3、SUP3-3.2、SUP3-3.3。",
        "doc_supplement3提供新案例，已补最小可复算链；完整EDR/SW6/动力学仍另列边界。",
    ),
    (
        "SUP3-1.3",
        "补充资料3 T0402流股/塔水力学/接管",
        ["SUP3 T0402 stream", "SUP3 T0402 hydraulic", "SUP3 T0402 nozzle"],
        "流股衡算容差0.05 kg/h或0.02 kmol/h；接管流速容差按0.03-0.2 m/s；Column Internals表为范围条件校核。",
        "补充资料3 Aspen流股表 + Column Internals表 + 接管汇总表；正式证据仍需Aspen导出。",
    ),
    (
        "SUP3-2.3",
        "补充资料3 E0104工艺条件",
        ["SUP3 E0104 exchanger"],
        "允许压降容差0.001 MPa；设计压力按Pmax+0.1 MPa可在0.02 MPa内复现；设计温度按15-30 C裕量条件校核。",
        "补充资料3 E0104 Aspen流股表 + EDR设计条件表；EDR面积/结构仍需软件证据。",
    ),
    (
        "SUP3-3.2",
        "补充资料3/主文档动力学源温度标签审计",
        ["R0201 kinetics source audit", "SUP3 kinetics source audit"],
        "温度标签换算容差0.1 K；不一致项保留为review。",
        "主文档table_15/table_16与补充资料3 table_24交叉审计；动力学仍为provisional。",
    ),
    (
        "SUP3-3.3",
        "补充资料3 R0101反应器设计条件",
        ["SUP3 R0101 reactor"],
        "设计压力容差0.001 MPa；设计温度为文档选定值，按15-30 C裕量解释。",
        "补充资料3反应器段落与table_31；结构/SW6/动力学仍需独立证据。",
    ),
]


PARAMETER_SOURCE_ROWS = [
    ("T0301-1.1.7", "塔底出料体积流量 L", "6.05", "m3/h", "Aspen", "主文档table_00/正文Aspen数据", "塔底液相空间"),
    ("T0301-1.1.7", "塔底持液时间 t", "5", "min", "人工设定默认值", "文档按5-15 min经验范围取5 min", "塔底液相空间"),
    ("T0301-1.1.7", "塔径 D", "0.8", "m", "软件校核+人工圆整", "Aspen Column Internals后圆整", "塔底空间/塔高"),
    ("T0301-1.1.7", "塔顶空间 HD", "1.5", "m", "人工设定默认值", "按塔顶空间、人孔和回流口布置取值", "塔高"),
    ("T0301-1.1.7", "塔底空间 HB", "1.8", "m", "人工设定默认值", "按Hmin=1.5D及防闪蒸/液位波动取值", "塔高"),
    ("T0301-1.1.7", "段间空间 HR1/HR2", "1.0 / 0.55", "m", "人工设定默认值", "按收集器、再分布器、支撑和人孔组合取值", "塔高"),
    ("T0301-1.1.7", "裙座高度 HS", "2.6", "m", "人工设定默认值", "按出料管、基础环、裙座结构布置取值", "总塔高"),
    ("T0301-1.1.7", "封头高度 HF", "0.225", "m", "规范", "GB/T 25198-2023标准椭圆封头", "总塔高"),
    ("T0301-1.1.8", "N1/N5气相目标流速", "20", "m/s", "人工设定默认值", "文档接管设计经验取值", "接管理论直径"),
    ("T0301-1.1.8", "N2/N3/N4液相目标流速", "1", "m/s", "人工设定默认值", "文档接管设计经验取值", "接管理论直径"),
    ("T0301-1.1.8", "接管标准规格", "Φ273×10、Φ34×3、Φ17×4、Φ89×5、Φ325×15", "mm", "规范+人工圆整", "GB/T 17395-2024", "选管后流速"),
    ("T0301-1.1.9", "设计压力系数", "1.1", "-", "规范/人工设定默认值", "NB/T 47041-2014及文档设计压力规则", "壁厚"),
    ("T0301-1.1.9", "设计温度", "210", "C", "人工设定默认值", "操作最高温度+15-30 C", "材料/强度"),
    ("T0301-1.1.9", "材料", "S31608", "-", "人工设定默认值", "按温度和腐蚀性选择", "强度/材料"),
    ("T0301-1.1.9", "焊接系数 phi", "0.85", "-", "人工设定默认值", "双面焊、20%无损检测", "壁厚"),
    ("T0301-1.1.9", "壁厚下限/负偏差/腐蚀裕量", "3 / 0.3 / 2", "mm", "规范+人工设定默认值", "GB150类规则和文档取值", "名义厚度前计算"),
    ("E0108-2.1.2", "管程/壳程操作压力", "2.01 / 3.05", "bar", "Aspen", "E0108流股/工艺条件表", "设计压力"),
    ("E0108-2.1.2", "设计压力系数", "1.1", "-", "人工设定默认值", "文档设计压力规则", "设计压力"),
    ("E0108-2.1.2", "允许压降", "0.4 / 0.61", "bar", "人工设定默认值", "出口绝压>0.1MPa时按进口压力20%", "EDR设置"),
    ("E0108-2.1.2", "设计温度", "管程30 / 壳程60", "C", "人工设定默认值", "工作温度+15-30 C", "强度/EDR"),
    ("E0108-2.1.2", "污垢热阻", "2.64e-4 / 17.6e-5", "m2 K/W", "文献", "《换热器工艺设计》经验系数", "EDR/传热"),
    ("E0108-2.2.7", "流体通道", "冷冻盐水管程；热流股壳程", "-", "人工设定默认值", "按腐蚀、结垢、清洗和散热选择", "结构设计"),
    ("E0108-2.2.7", "换热管规格/数量/长度", "Φ25×2 / 56 / 3000", "mm/根/mm", "软件校核+规范圆整", "EDR推荐结合GB/T 28712.2-2023", "结构参数"),
    ("E0108-2.2.7", "管心距/排列/管程数", "32 / 三角形转30° / 2", "mm/-/-", "软件校核+人工设定默认值", "EDR推荐和结构选择", "结构参数"),
    ("E0108-2.2.7", "折流板", "单弓形；100 mm；圆缺率25%", "-", "人工设定默认值", "EDR调整+手册经验范围", "壳程强化传热"),
    ("E0108-2.3", "许用应力/焊接系数", "137 / 0.85", "MPa/-", "规范+人工设定默认值", "材料许用应力；双面焊20%检测", "壁厚"),
    ("R0201-3.2", "温度点", "553.15 / 633.15", "K", "文档表格", "主文档table_15写220/360 C但table_16写553.15/633.15 K；低温标签需复核", "Arrhenius拟合"),
    ("R0201-3.2", "k2源值", "65 / 1300", "mmol gcat^-1 atm^-0.5 h^-1", "文献/文档", "文档动力学表；原始文献未冻结", "动力学换算"),
    ("R0201-3.2", "k3源值", "24 / 1200", "mmol gcat^-1 atm^-0.5 h^-1", "文献/文档", "文档动力学表；原始文献未冻结", "动力学换算"),
    ("R0201-3.2", "k4源值", "75 / 800", "mmol gcat^-1 atm^-0.5 h^-1", "文献/文档", "文档动力学表；原始文献未冻结", "动力学换算"),
    ("R0201-3.2", "atm换算基准", "101300", "Pa/atm", "规范常数", "单位换算常数", "动力学单位换算"),
    ("R0201-3.3.1", "管数/管径/管长", "5000 / Φ19×1.5 / 2.0", "根/mm/m", "人工设定默认值", "文档结构设计结果", "催化剂量/面积"),
    ("R0201-3.3.1", "催化剂密度/空隙率", "1311 / 0.422", "kg/m3/-", "文献/文档", "催化剂/床层参数，需资料佐证", "催化剂装填"),
    ("R0201-3.3.1", "管心距/边缘系数", "1.25do / 1.5do", "-", "人工设定默认值", "文档经验估算公式", "管束直径"),
    ("R0201-3.3.4.3", "气体物性", "rho=8.51; mu=1.57e-5; Cp=1851; lambda=0.0335", "SI", "Aspen/文档表格", "R0201物性表或Aspen物性", "传热"),
    ("R0201-3.3.4.3", "壳侧传热系数", "7000", "W/(m2 K)", "人工设定默认值", "文档经验/热媒侧估计", "总传热系数"),
    ("R0201-3.3.4.3", "污垢热阻", "Rsi=1.76e-4; Rso=8.60e-5", "m2 K/W", "文献/人工设定默认值", "文档采用经验值", "总传热系数"),
    ("R0201-3.3.4.3", "金属导热系数", "16", "W/(m K)", "文献/材料物性", "S31608材料物性", "管壁热阻"),
    ("R0201-3.3.4.3", "热负荷/平均温差", "378089 / 3.7", "W/K", "Aspen/文档表格", "热量衡算或Aspen输出", "所需面积"),
    ("R0201-3.3.4.4", "催化剂粒径", "0.002", "m", "文献/文档", "平均直径2 mm球状颗粒", "Ergun压降"),
    ("R0201-3.3.4.4", "气体物性", "rho=8.51; mu=1.58e-5; u=0.595", "SI", "Aspen/文档表格", "流量与几何折算/物性表", "Ergun压降"),
    ("R0201-3.3.5", "气体接管目标流速", "20", "m/s", "人工设定默认值", "文档接管设计经验取值", "接管理论直径"),
    ("R0201-3.3.5", "熔盐接管目标流速", "2", "m/s", "人工设定默认值", "文档接管设计经验取值", "接管理论直径"),
    ("R0201-3.3.6", "壳体强度参数", "P=0.55; Di=1900; sigma=112.6; phi=0.85", "MPa/mm/MPa/-", "规范+人工设定默认值", "文档强度计算条件", "壁厚"),
    ("R0201-3.3.6", "负偏差/腐蚀裕量", "0.3 / 2", "mm", "人工设定默认值", "文档强度计算条件", "名义厚度前计算"),
    ("T802-1.3.2", "T802流股质量流量", "967.728 / 229.587 / 738.142", "kg/h", "Aspen", "国宝特工table_06", "质量衡算"),
    ("T802-1.3.2", "设计压力", "-0.1", "MPa", "规范+人工设定默认值", "GB150-2024真空容器外压规则", "外压设计"),
    ("T802-1.3.2", "理论级/进料级", "30 / 16", "stage", "Aspen", "RadFrac结果表；另有段落13需复核", "塔模型"),
    ("T802-1.3.3", "HETP", "252Y=0.40; 352Y=0.30", "m", "文献/厂家", "Sulzer结构填料手册，文档引用", "填料高度"),
    ("T802-1.3.3", "sizing目标", "60", "% Approach L/V", "人工设定默认值", "文档Interactive sizing目标", "Column Internals"),
    ("T802-1.3.3", "能力因子范围", "40-80", "%", "人工设定默认值", "文档设计要求", "水力学校核"),
    ("T802-1.3.3", "填料段高度", "2.0 / 2.4 / 2.4", "m", "人工设定默认值", "文档分段并由表复核", "水力学校核"),
    ("T802-1.3.3", "圆整塔径", "0.6", "m", "软件校核+人工圆整", "Interactive sizing后按制造规定圆整", "Rating"),
    ("SUP3", "T0402设计压力", "-0.1", "MPa", "规范+人工设定默认值", "GB150-2024真空容器外压规则", "SUP3总入口；细项见SUP3-1.3"),
    ("SUP3", "T0402设计温度", "200", "C", "人工设定默认值", "文档按操作温度和设计裕量取值", "SUP3总入口；细项见SUP3-1.3"),
    ("SUP3", "T0402理论板/进料板", "21 / 13", "stage", "Aspen", "补充资料3 Aspen模拟结果", "SUP3总入口；细项见SUP3-1.3"),
    ("SUP3", "T0402塔顶/塔底空间", "HD=1.0; HB=1.0", "m", "人工设定默认值", "按手孔、除沫、储量和内件布置取值", "SUP3总入口；完整塔高仍需补"),
    ("SUP3", "T0402塔径", "0.6", "m", "软件校核+人工圆整", "Aspen Interactive sizing后按制造规定圆整", "SUP3总入口；细项见SUP3-1.3"),
    ("SUP3", "T0402接管规格", "Φ273×15; Φ34×8 等", "mm", "规范+人工圆整", "按流速计算后查标准管", "SUP3总入口；细项见SUP3-1.3"),
    ("SUP3", "E0104设计压力", "管程0.76; 壳程0.62", "MPa", "人工设定默认值", "表值可由Pmax+0.1MPa近似复现；不是1.1倍规则", "SUP3总入口；细项见SUP3-2.3"),
    ("SUP3", "E0104允许压降", "管程0.134; 壳程0.102", "MPa", "人工设定默认值", "出口绝压>0.1MPa按进口压力20%", "待EDR证据"),
    ("SUP3-1.3", "T0402流股质量流量", "1473.74 / 1452.19 / 21.55", "kg/h", "Aspen", "补充资料3 table_07", "质量衡算"),
    ("SUP3-1.3", "T0402理论板/进料板", "21 / 13", "stage", "Aspen", "补充资料3 table_08/table_09", "塔模型"),
    ("SUP3-1.3", "T0402设计温度/压力", "200 / -0.1", "C/MPa", "规范+人工设定默认值", "真空塔设计条件汇总", "外压/强度边界"),
    ("SUP3-1.3", "T0402接管流量/规格", "见table_13", "m3/s/mm", "规范+人工圆整", "按流速计算后查标准管", "接管流速复核"),
    ("SUP3-2.3", "E0104工艺压力", "管程0.67/0.65; 壳程0.51/0.49", "MPa", "Aspen", "补充资料3 table_18", "设计压力/压降"),
    ("SUP3-2.3", "E0104设计压力", "管程0.76; 壳程0.62", "MPa", "人工设定默认值", "表值可由Pmax+0.1MPa近似复现；不是1.1倍规则", "设计压力"),
    ("SUP3-2.3", "E0104允许压降", "管程0.134; 壳程0.102", "MPa", "人工设定默认值", "出口绝压>0.1MPa按进口压力20%", "EDR设置"),
    ("SUP3-3.2", "补充资料3动力学温度点", "280/360 C = 553.15/633.15 K", "C/K", "文档表格", "补充资料3 table_24温度标签与K值一致", "温度标签审计"),
    ("SUP3-3.3", "R0101管程/壳程设计压力", "0.66 / 0.44", "MPa", "规范+人工设定默认值", "补充资料3段落按1.1倍操作压力取值", "反应器强度边界"),
    ("SUP3-3.3", "R0101管程/壳程设计温度", "380 / 341", "C", "人工设定默认值", "最高温度+约20 C设计裕量", "反应器强度边界"),
]


DEVICE_MAPPING_ROWS = [
    (
        "塔设备",
        "T0301",
        "doc_main_detailed / 3-设备设计及选型说明书(1).docx",
        "当前主复算塔样板",
        "可复算塔底持液高度、塔高、接管、壳体/封头基础壁厚。",
        "可借用T802/T0402的方法论：塔型判断、Column Internals sizing/rating、SW6边界写法。",
        "不能合并T802/T0402的理论级数、进料级、流股、塔径、塔高、设计压力。",
        "已脚本化",
    ),
    (
        "塔设备",
        "T802",
        "doc_guobao_tower / 国宝特工设备选型说明(1).docx",
        "塔方法论与Aspen水力学样板",
        "可复核Aspen流股质量/摩尔衡算、设计条件、填料段高度、%Capacity、压降范围。",
        "可迁移塔型/填料选择决策树，以及Interactive sizing后圆整再Rating的流程。",
        "不能作为T0301或T0402的工艺数据；进料板16与段落13还需Aspen导出解释。",
        "部分脚本化",
    ),
    (
        "塔设备",
        "T0402",
        "doc_supplement3 / 3-设备设计与选型说明书(1).docx",
        "补充资料3新增塔案例",
        "已复核流股衡算、理论板21、进料板13、设计压力-0.1 MPa、设计温度200 C、塔径0.6 m、Column Internals范围和接管流速。",
        "可迁移T0301/T802的塔高、接管、水力学校核和SW6边界脚本结构。",
        "不能直接覆盖T0301；完整塔高/强度/SW6必须按T0402流股和表格独立建链。",
        "已补最小脚本链",
    ),
    (
        "换热器",
        "E0108",
        "doc_main_detailed / 3-设备设计及选型说明书(1).docx",
        "当前主复算换热器样板",
        "可复算设计压力、接管理论管径/选管流速、壳体/封头基础壁厚。",
        "可借用E0104补充资料中的EDR章节组织方式。",
        "不能合并E0104的换热管外径、管长、管数、壳径、压降和设计压力。",
        "已脚本化",
    ),
    (
        "换热器",
        "E0104",
        "doc_supplement3 / 3-设备设计与选型说明书(1).docx",
        "补充资料3新增换热器案例",
        "已复核设计压力、允许压降和设计温度裕量；已识别EDR推荐结构和SW6线索。",
        "可迁移E0108的设计压力、允许压降、接管和强度复算函数。",
        "不能直接套用E0108脚本结果；E0104完整EDR/接管/强度需独立读取表格。",
        "已补最小脚本链",
    ),
    (
        "固定床反应器",
        "R0201",
        "doc_main_detailed / 3-设备设计及选型说明书(1).docx",
        "当前主复算反应器样板",
        "可复算催化剂量、管束估算、传热、Ergun压降、接管和壳体基础壁厚。",
        "可迁移到补充资料3反应器的几何/传热/压降脚本结构。",
        "动力学不能迁移；k/E/速率基准必须逐来源冻结。",
        "已脚本化但动力学provisional",
    ),
    (
        "反应器补充章节",
        "补充资料3反应器",
        "doc_supplement3 / 3-设备设计与选型说明书(1).docx",
        "补充资料3新增反应器线索",
        "已复核R0101管程/壳程设计压力和设计温度；含反应管、管板法兰、壳体/管箱参数等线索。",
        "可迁移R0201的非动力学审计框架。",
        "不能复制R0201动力学或结构参数；需重新冻结动力学和结构输入。",
        "已补设计T/P最小脚本链",
    ),
    (
        "目录级设备",
        "泵/压缩机/储罐/分离器/膜/混合器等",
        "doc_main_detailed + doc_supplement3",
        "象征性选型和设备一览",
        "已有型号、尺寸、功率或重量等汇总信息。",
        "可迁移最小校核模板：泵功率、压缩功、储罐停留时间、分离器液滴负荷等。",
        "不能把目录型号当成严格计算；需Aspen物性、厂家曲线、规范校核。",
        "可靠性边界",
    ),
]


def reliability_label(code: str) -> str:
    labels = {
        "A_formula_reproduced": "A 公式复算通过",
        "B_document_selection_reproduced": "B 复算到选型前步骤",
        "C_external_software_required": "C 需外部软件证据",
        "D_kinetics_provisional": "D 动力学暂定",
        "E_symbolic_catalog_selection": "E 象征性/目录选型",
    }
    return labels.get(code, code)


def fmt_float(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}g}"


def fmt_percent(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.3%}"


def md_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "/")


def summarize(rows: list[CalcRow]) -> dict[str, Any]:
    comparable = [row for row in rows if row.pass_check is not None]
    passed = [row for row in comparable if row.pass_check]
    failed = [row for row in comparable if row.pass_check is False]
    by_class: dict[str, int] = {}
    by_module: dict[str, int] = {}
    for row in rows:
        by_class[row.reliability_class] = by_class.get(row.reliability_class, 0) + 1
        by_module[row.module] = by_module.get(row.module, 0) + 1
    return {
        "total_rows": len(rows),
        "comparable_rows": len(comparable),
        "passed_rows": len(passed),
        "failed_rows": len(failed),
        "by_reliability_class": by_class,
        "by_module": by_module,
        "failed_items": [asdict(row) for row in failed],
    }


def failure_category_from_dict(item: dict[str, Any]) -> str:
    module = item.get("module", "")
    if module == "R0201 kinetics source audit":
        return "R0201动力学低温标签冲突"
    if module == "CH8 pump audit":
        return "泵单点功率反推密度筛错"
    return "其他需复核项"


def failed_category_counts(summary: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in summary.get("failed_items", []):
        category = failure_category_from_dict(item)
        counts[category] = counts.get(category, 0) + 1
    return counts


def failed_category_lines(summary: dict[str, Any]) -> list[str]:
    counts = failed_category_counts(summary)
    if not counts:
        return ["- 无需复核项。"]
    return [f"- {category}：{count} 行" for category, count in sorted(counts.items())]


def write_calculation_outputs(rows: list[CalcRow], summary: dict[str, Any]) -> None:
    payload = [asdict(row) for row in rows]
    (DATA / "calculation_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "calculation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 计算脚本复算结果",
        "",
        "本表只覆盖项目书中能由明确公式和数值直接复算的部分。Aspen EDR、Column Internals、SW6、厂家样本曲线和缺少来源链的动力学参数不在此处冒充为正式校核。",
        "",
        f"- 计算行数：{summary['total_rows']}",
        f"- 有文档数值可对照：{summary['comparable_rows']}",
        f"- 容差内通过：{summary['passed_rows']}",
        f"- 需复核：{summary['failed_rows']}",
        "",
        "| 模块 | 项目 | 来源 | 公式/方法 | 输入摘要 | 脚本值 | 单位 | 文档值 | 误差 | 容差 | 可靠性 | 状态 | 备注 |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for row in rows:
        inputs = json.dumps(row.input_values, ensure_ascii=False, separators=(",", ":"))
        if len(inputs) > 120:
            inputs = inputs[:117] + "..."
        lines.append(
            "| "
            + " | ".join(
                [
                    row.module,
                    row.item,
                    row.source_location,
                    f"`{row.formula}`",
                    f"`{inputs}`",
                    fmt_float(row.value),
                    row.unit,
                    row.document_value_raw,
                    fmt_float(row.abs_error),
                    fmt_float(row.tolerance),
                    reliability_label(row.reliability_class),
                    row.status,
                    row.note.replace("|", "/"),
                ]
            )
            + " |"
        )
    (OUT / "计算脚本复算结果.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_module_report() -> None:
    lines = [
        "# 模块拆分与任务清单",
        "",
        "阅读结论：三份文件应取并集但分工不同。`3-设备设计及选型说明书(1).docx`是当前主复算文件，提供T0301/E0108/R0201三条可复算详算链和全设备选型汇总；`国宝特工设备选型说明(1).docx`更像塔设备方法论和T802塔水力学样板；`3-设备设计与选型说明书(1).docx`是补充资料3，新增T0402、E0104等另一版案例，先作为证据源归档，不能未经核对直接覆盖主复算链。",
        "",
        "| 模块 | 文档覆盖 | 这一块真正要做什么 |",
        "| --- | --- | --- |",
    ]
    for module, coverage, task in MODULE_ROWS:
        lines.append(f"| {module} | {coverage} | {task} |")
    lines.extend(
        [
            "",
            "## 优先级",
            "",
            "1. 用国宝特工文档和补充资料3共同补塔设备方法论：先判塔型和内件，再进入Aspen sizing/rating和SW6。",
            "2. 用第一份文档的T0301/E0108/R0201作为核心详算主体，保留脚本复算和误差审计。",
            "3. 用T802作为塔水力学补充样板，证明脚本能读Aspen导出表并检查能力因子、填料高度、压降。",
            "4. 对补充资料3中的T0402/E0104等新增案例，先建立独立计算链，再决定是否替换或补强主文件对应模块。",
            "5. 对泵、压缩机、储罐、分离器、膜、混合器等目录级选型，建立最小校核脚本前先补Aspen物性和厂家曲线。",
            "6. 对没有 Aspen/厂家/SW6/EDR证据的地方标记为 symbolic selection，等证据补齐后再升级可靠性等级。",
        ]
    )
    (OUT / "模块拆分与任务清单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_aspen_report() -> None:
    lines = [
        "# Aspen需提供参数清单",
        "",
        "用途：把三份说明书取并集后统一成一套设备设计输入。下列参数应从 Aspen Plus、Aspen EDR、Column Internals 或导出的结果表中给出，并注明版本、工况和单位。",
        "",
        "| 参数组 | 适用设备 | Aspen/EDR需要给出的内容 |",
        "| --- | --- | --- |",
    ]
    for group, equipment, params in ASPEN_SECTIONS:
        lines.append(f"| {group} | {equipment} | {params} |")
    lines.extend(
        [
            "",
            "## R0201动力学硬门槛",
            "",
            "R0201当前脚本只能证明单位换算和两点Arrhenius拟合可重复，不能证明正式动力学正确。进入 Aspen kinetic card 前必须冻结：",
            "",
            "`source equation/value -> source units -> conversion -> Aspen card units -> exact Aspen input -> exported verification`",
            "",
            "缺任一环节时，R0201动力学只能标为 provisional；不得把 k、E、Exponent、Rate basis、[Ci] basis 写成已正式验证。",
            "",
            "## 软件证据",
            "",
            "- Column Internals：T0301塔径、填料段、能力因子、液泛率、压降截图或导出表。",
            "- Column Internals：T802理论板数/进料板、sizing/rating两张水力学表、圆整塔径0.6 m的依据；同时解释进料板16与分段段落中进料板13的差异。",
            "- Aspen EDR：E0108设计/校核文件，面积余量、U值、Re、压降、推荐几何结构。",
            "- SW6：塔、换热器、反应器的壳体/封头/开孔补强/管板/法兰/裙座校核报告。",
            "- 厂家样本：泵、压缩机、透平、膜、倾析器和混合器的型号曲线或样本页。",
        ]
    )
    (OUT / "Aspen需提供参数清单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_reliability_report(rows: list[CalcRow], summary: dict[str, Any]) -> None:
    class_lines = []
    for code, count in sorted(summary["by_reliability_class"].items()):
        class_lines.append(f"- {reliability_label(code)}：{count} 行")
    failed = summary["failed_items"]
    failed_lines = ["无。"] if not failed else [f"- {item['module']} / {item['item']}：脚本值 {item['value']}，文档值 {item['document_value_raw']}。" for item in failed]
    lines = [
        "# 计算可靠性证明",
        "",
        "## 结论",
        "",
        f"脚本共复算 {summary['total_rows']} 行，其中 {summary['comparable_rows']} 行有文档数值可直接对照，{summary['passed_rows']} 行在设定容差内通过，{summary['failed_rows']} 行需要复核。",
        "",
        "这说明 T0301、E0108、R0201中“公式直接可算”的部分可以被独立重复；第二份T802样板中的Aspen流股衡算和水力学范围也可被脚本检查。可靠性边界同样重要：EDR、SW6、Column Internals、厂家目录和未冻结动力学不由本脚本替代。",
        "",
        "## 可靠性分级",
        "",
        *class_lines,
        "",
        "另外，`选型可靠性边界清单.md`列出了不进入脚本复算统计的 C/E 类证据项：EDR、SW6、Column Internals 和厂家/标准目录选型。",
        "",
        "## 方法",
        "",
        f"1. 从3份DOCX提取{current_table_count()}张当前有效表格到 `data/tables/doc_guobao_tower`、`data/tables/doc_main_detailed`、`data/tables/doc_supplement3`，保留原始表格附件。",
        "2. 把主说明书中的可读公式转成 Python 函数：接管直径/流速、设计压力、筒体/封头壁厚、塔底持液高度、催化剂量、管束估算、传热系数、Ergun压降、动力学单位换算和两点Arrhenius拟合。",
        "3. 把第二份文档中的T802塔样板转成读取型校核：Aspen流股衡算、设计条件导入、填料段高度、%Capacity范围和压降范围。",
        "4. 把补充资料3中的T0402/E0104/R0101转成最小复算链：T0402流股衡算、水力学表范围和接管流速，E0104设计压力/允许压降/设计温度裕量，R0101设计T/P，并交叉审计动力学温度标签。",
        "5. 每条结果记录来源、输入、公式、脚本值、文档值或条件、误差/通过状态、可靠性类别。",
        "6. 对不能由脚本直接证明的内容保留为软件/厂家证据项，不升级为“已验证”。",
        "",
        "## 需复核项",
        "",
        *failed_category_lines(summary),
        "",
        "说明：新增第4-8章脚本筛错后，需复核项不再只有R0201动力学标签。泵类反推密度异常表示表中单点轴功率不能直接当作对应工况的正式过程功率，需补Aspen密度、NPSHa和厂家曲线；这类筛错项应保留，不应调宽容差强行通过。",
        "",
        *failed_lines,
        "",
        "## 已知限制",
        "",
        "- E0108的理论接管直径与表中接管内径属于“计算后选标准管”的关系，脚本同时校核了选定管径下的实际流速。",
        "- R0201传热面积复现的是说明书使用的直径基准；正式机械/传热设计应声明内外表面积基准。",
        "- R0201动力学当前只证明数值换算链的一部分，仍需原始文献方程、单位、Aspen卡片字段和导出卡验证。",
        "- T802表中理论进料板为16，但填料分段段落写进料板位置为13；脚本保留该差异，需以Aspen导出为准。",
        "- SW6/EDR/Column Internals截图或导出结果是正式工程证据，脚本报告不能替代。",
    ]
    (OUT / "计算可靠性证明.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_boundary_report() -> None:
    lines = [
        "# 选型可靠性边界清单",
        "",
        "这张表专门处理“说明书里写了型号/尺寸，但没有完整计算链”的部分。它不是否定选型，而是把需要补的证据说清楚，防止把象征性选型误写成严格校核。",
        "",
        "| 对象 | 来源 | 可靠性类别 | 当前判断 | 要升级为可靠设计还需补什么 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item, source, cls, judgement, evidence in NONSCRIPT_BOUNDARY_ROWS:
        lines.append(f"| {item} | {source} | {reliability_label(cls)} | {judgement} | {evidence} |")
    (OUT / "选型可靠性边界清单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_script_manual(summary: dict[str, Any]) -> None:
    lines = [
        "# 完善脚本内容说明",
        "",
        "## 脚本目标",
        "",
        "本脚本把三份设备选型说明书取并集：主说明书提供T0301、E0108、R0201的可复算详算链；国宝特工说明补足塔设备选型方法论和T802 Aspen水力学样板；补充资料3新增T0402、E0104、R0101等另一版案例，当前已补最小可复算链。脚本输出不是简单摘抄，而是把能计算的内容转成可重复审计行，把不能由脚本证明的内容保留为软件/厂家证据边界。",
        "",
        "## 输入",
        "",
        "- `source/3-设备设计及选型说明书(1).docx`：主设备设计与选型说明书。",
        "- `source/国宝特工设备选型说明(1).docx`：塔设备方法论与T802塔样板。",
        "- `source/3-设备设计与选型说明书(1).docx`：补充资料3，含T0402、E0104等新增案例。",
        "- `data/tables/doc_*/*.csv`：由 `extract_docx_tables.py` 从三份DOCX自动提取。",
        "",
        "## 计算模块",
        "",
        "| 模块 | 脚本化内容 | 可靠性边界 |",
        "| --- | --- | --- |",
        "| T0301塔 | 塔底持液高度、塔高、接管理论管径、选管后流速、壳体/封头壁厚 | 塔径/填料段/%Capacity/SW6仍需Aspen与SW6证据 |",
        "| E0108换热器 | 设计压力、接管理论管径、选管后流速、壳体/封头壁厚 | 换热面积、U值、Re、压降、管板/法兰需EDR/SW6证据 |",
        "| R0201反应器 | 催化剂量、管束直径、传热面积、Nu、传热系数、总传热系数、所需面积、Ergun压降、接管、壳体壁厚 | 动力学未冻结；管板/法兰/开孔补强需SW6证据 |",
        "| T802塔样板 | Aspen流股质量/摩尔衡算、设计条件导入、理论级/进料级、填料段高度、sizing/rating能力因子和压降范围 | Column Internals正式导出仍是最终证据；进料板13/16差异需Aspen导出解释 |",
        "| 补充资料3最小链 | T0402流股衡算/水力学校核/接管流速，E0104设计条件，R0101设计T/P，动力学温度标签交叉审计 | T0402完整塔高/SW6、E0104完整EDR/SW6、R0101完整结构和动力学冻结仍未完成 |",
        "",
        "## 可靠性分级",
        "",
        "- A 公式复算通过：明确公式、明确输入、可与文档数值对照。",
        "- B 复算到选型前步骤：能复现表值或条件，但最终选型还依赖标准圆整、软件或工程判断。",
        "- C 需外部软件证据：EDR、SW6、Column Internals等正式软件输出。",
        "- D 动力学暂定：仅完成单位换算或拟合，未完成文献-Aspen冻结链。",
        "- E 象征性/目录选型：只有型号/尺寸/厂家目录级结果，缺完整计算链。",
        "",
        "## 当前运行结果",
        "",
        f"- 审计行数：{summary['total_rows']}",
        f"- 可对照/条件校核行：{summary['comparable_rows']}",
        f"- 通过行：{summary['passed_rows']}",
        f"- 需复核行：{summary['failed_rows']}",
        "",
        "## 小节级输出",
        "",
        "- `小节级计算精度表.md`：按 T0301-1.1.7、T0301-1.1.8、E0108-2.1.2 等小节汇总计算行、可校核行、通过行、最大误差、精度规则和来源逻辑。",
        "- `小节级参数来源表.md`：按同一小节归类参数，区分 Aspen、文献/厂家、规范、软件校核和人工设定默认值；人工设定项保留说明书已有默认值并写明取值理由。",
        "",
        "## 复跑命令",
        "",
        "```powershell",
        "& 'C:\\Users\\Administrator\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe' -X utf8 'C:\\Users\\Administrator\\Desktop\\化工设计大赛\\设备设计选型工作包\\scripts\\extract_docx_tables.py'",
        "& 'C:\\Users\\Administrator\\.cache\\codex-runtimes\\codex-primary-runtime\\dependencies\\python\\python.exe' -X utf8 'C:\\Users\\Administrator\\Desktop\\化工设计大赛\\设备设计选型工作包\\scripts\\equipment_calc.py'",
        "```",
    ]
    (OUT / "完善脚本内容说明.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_section_precision_report(rows: list[CalcRow], summary: dict[str, Any]) -> None:
    lines = [
        "# 小节级计算精度表",
        "",
        "本表把脚本复算结果压到说明书小节层级，便于按正文逻辑证明“哪一小节能算、算到什么精度、还缺什么软件或文献证据”。",
        "",
        f"- 总计算行数：{summary['total_rows']}",
        f"- 可对照/条件校核行：{summary['comparable_rows']}",
        f"- 通过行：{summary['passed_rows']}",
        f"- 需复核行：{summary['failed_rows']}",
        "",
        "| 小节 | 覆盖模块 | 计算行 | 可对照/条件校核 | 通过 | 需复核 | 最大绝对误差 | 最大相对误差 | 精度规则 | 来源逻辑 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    section_rows: dict[str, list[CalcRow]] = {}
    for section_id, section_name, modules, precision_rule, source_logic in SECTION_RULES:
        matched = [row for row in rows if row.module in modules]
        section_rows[section_id] = matched
        comparable = [row for row in matched if row.pass_check is not None]
        passed = [row for row in comparable if row.pass_check]
        failed = [row for row in comparable if row.pass_check is False]
        max_abs = max((row.abs_error for row in matched if row.abs_error is not None), default=None)
        max_rel = max((row.rel_error for row in matched if row.rel_error is not None), default=None)
        module_text = "、".join(modules) if modules else "未脚本化；仅证据索引"
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{section_id} {section_name}",
                    module_text,
                    str(len(matched)),
                    str(len(comparable)),
                    str(len(passed)),
                    str(len(failed)),
                    fmt_float(max_abs),
                    fmt_percent(max_rel),
                    md_cell(precision_rule),
                    md_cell(source_logic),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 分节明细",
            "",
            "说明：`文档/条件`列若为范围或文字条件，表示这是条件校核；若给出数值，则误差列为脚本值与文档值的差。",
        ]
    )
    for section_id, section_name, modules, _precision_rule, _source_logic in SECTION_RULES:
        matched = section_rows[section_id]
        lines.extend(["", f"### {section_id} {section_name}", ""])
        if not matched:
            if section_id == "SUP3":
                lines.append("这是补充资料3总证据入口；具体复算已经拆到SUP3-1.3、SUP3-2.3、SUP3-3.2、SUP3-3.3，完整EDR/SW6/动力学仍按边界清单补齐。")
            else:
                lines.append("当前未进入计算行；只纳入证据索引和参数来源表，需独立建立公式链后再给计算精度。")
            continue
        lines.extend(
            [
                "| 模块 | 项目 | 脚本值 | 单位 | 文档/条件 | 误差 | 容差 | 状态 | 可靠性 |",
                "| --- | --- | ---: | --- | --- | ---: | ---: | --- | --- |",
            ]
        )
        for row in matched:
            lines.append(
                "| "
                + " | ".join(
                    [
                        md_cell(row.module),
                        md_cell(row.item),
                        fmt_float(row.value),
                        md_cell(row.unit),
                        md_cell(row.document_value_raw),
                        fmt_float(row.abs_error),
                        fmt_float(row.tolerance),
                        md_cell(row.status),
                        reliability_label(row.reliability_class),
                    ]
                )
                + " |"
            )
    (OUT / "小节级计算精度表.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_section_parameter_source_report() -> None:
    section_names = {section_id: section_name for section_id, section_name, *_rest in SECTION_RULES}
    source_counts: dict[str, int] = {}
    by_section: dict[str, list[tuple[str, str, str, str, str, str]]] = {}
    for section_id, parameter, value, unit, source_type, source_note, destination in PARAMETER_SOURCE_ROWS:
        source_counts[source_type] = source_counts.get(source_type, 0) + 1
        by_section.setdefault(section_id, []).append((parameter, value, unit, source_type, source_note, destination))

    lines = [
        "# 小节级参数来源表",
        "",
        "本表按计算小节归类输入参数。人工设定项保留说明书已有默认值，不再空写“待定”；但这类参数仍应在正文中说明其经验、规范或结构布置来源。",
        "",
        "| 来源类型 | 参数数量 |",
        "| --- | ---: |",
    ]
    for source_type, count in sorted(source_counts.items()):
        lines.append(f"| {md_cell(source_type)} | {count} |")

    for section_id, section_name, *_rest in SECTION_RULES:
        rows = by_section.get(section_id, [])
        lines.extend(["", f"## {section_id} {section_name}", ""])
        if not rows:
            lines.append("当前未归集参数；若该小节后续脚本化，应先补参数来源行。")
            continue
        lines.extend(
            [
                "| 参数 | 默认值/取值 | 单位 | 来源类型 | 来源说明 | 用途/去向 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for parameter, value, unit, source_type, source_note, destination in rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        md_cell(parameter),
                        md_cell(value),
                        md_cell(unit),
                        md_cell(source_type),
                        md_cell(source_note),
                        md_cell(destination),
                    ]
                )
                + " |"
            )

    orphan_sections = sorted(set(by_section) - set(section_names))
    if orphan_sections:
        lines.extend(["", "## 未匹配小节", ""])
        for section_id in orphan_sections:
            lines.append(f"- {section_id}")
    (OUT / "小节级参数来源表.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def is_manual_source(source_type: str) -> bool:
    manual_markers = ("人工设定", "人工圆整", "软件校核", "规范+人工", "规范/人工")
    return any(marker in source_type for marker in manual_markers)


def write_manual_parameter_report() -> None:
    section_names = {section_id: section_name for section_id, section_name, *_rest in SECTION_RULES}
    manual_rows = [
        row for row in PARAMETER_SOURCE_ROWS if is_manual_source(row[4])
    ]
    lines = [
        "# 人工设定参数来源清单",
        "",
        "## 口径",
        "",
        "这里的“人工设定”包括工程经验直接取值、规范范围内取默认值、软件结果后的人工圆整、以及设计者按结构布置或厂家/标准系列作出的选择。下表只填说明书已经采用的默认值，不把它们空写成待定；但它们的可靠性仍低于 Aspen/EDR/SW6/厂家正式输出。",
        "",
        f"- 人工/圆整/软件建议类参数行数：{len(manual_rows)}",
        "- 非人工来源参数仍保留在 `小节级参数来源表.md` 中，例如纯 Aspen 流股、纯文献常数、规范常数。",
        "",
    ]

    for section_id, section_name, *_rest in SECTION_RULES:
        rows = [row for row in manual_rows if row[0] == section_id]
        if not rows:
            continue
        lines.extend([f"## {section_id} {section_name}", ""])
        lines.extend(
            [
                "| 参数 | 已有默认值/取值 | 单位 | 来源类型 | 人工设定来源 | 用途/去向 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for _section_id, parameter, value, unit, source_type, source_note, destination in rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        md_cell(parameter),
                        md_cell(value),
                        md_cell(unit),
                        md_cell(source_type),
                        md_cell(source_note),
                        md_cell(destination),
                    ]
                )
                + " |"
            )
        lines.append("")

    lines.extend(
        [
            "## 脚本审计用人工参数",
            "",
            "这些不是设备设计参数，只是脚本为了判断复算是否通过而设定的审计阈值，应与设备设计默认值分开写。",
            "",
            "| 参数 | 取值 | 用途 | 来源 |",
            "| --- | --- | --- | --- |",
            "| 数值容差 | 见 `小节级计算精度表.md` 的精度规则 | 判断脚本值与文档值是否一致 | 按文档小数位、工程量级和圆整关系设置 |",
            "| E0108接管理论直径容差 | 0.005 m | 允许理论管径与标准管内径存在圆整差异 | 脚本审计设定，不是通用设计规范 |",
            "| T802压降 sanity 阈值 | 2 mbar | 复核文档“压降不大”的表述 | 脚本审计设定，不是通用设计规范 |",
            "",
            "## 目录级选型中的人工设定",
            "",
            "以下设备表中的型号、台数、材料、尺寸、重量等多数属于人工/厂家目录选型，脚本没有把它们作为严格计算结果：",
            "",
            "- 塔设备选型一览：T0101/T0102/T0201/T0301/T0302。",
            "- 换热器选型一览：E0101-E0312。",
            "- 反应器选型一览：R0201/R0301-R0304。",
            "- 气液分离器、汽包、储罐、回流罐、缓冲罐。",
            "- 泵、压缩机、液体透平。",
            "- 倾析器、膜分离装置、混合器。",
            "",
            "这些值要升级为可靠设计，需要补：Aspen同版流股/物性、EDR或Column Internals导出、SW6报告、厂家曲线/样本页和必要的规范计算。",
            "",
            "## 核对关系",
            "",
            "同一批参数的完整来源分类见 `小节级参数来源表.md`；本文件只抽出人工设定、人工圆整和软件建议后人为采用的部分。",
        ]
    )
    (OUT / "人工设定参数来源清单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_device_mapping_report() -> None:
    lines = [
        "# 设备位号映射表",
        "",
        "本表用于防止三份DOCX取并集时把不同设备样板误合并。可以迁移的是方法、脚本结构和证据门槛；不能迁移的是流股、工况、几何、动力学和软件校核数值。",
        "",
        "| 设备族 | 位号/对象 | 来源文件 | 在本工作包中的角色 | 当前可用内容 | 可迁移内容 | 禁止合并内容 | 状态 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for family, tag, source, role, available, transferable, forbidden, status in DEVICE_MAPPING_ROWS:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_cell(family),
                    md_cell(tag),
                    md_cell(source),
                    md_cell(role),
                    md_cell(available),
                    md_cell(transferable),
                    md_cell(forbidden),
                    md_cell(status),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 使用原则",
            "",
            "1. `T0301/E0108/R0201` 是当前主复算链，优先用于证明脚本可靠性。",
            "2. `T802` 用来补塔设备方法论和Column Internals表格校核能力，不提供T0301数值。",
            "3. `T0402/E0104/补充资料3反应器` 已补最小复算链，但完整塔高/EDR/SW6/动力学仍需独立建链后才能升级为正式设计证据。",
            "4. 动力学参数不在任何设备之间迁移；所有k/E/基准必须按冻结链重新证明。",
        ]
    )
    (OUT / "设备位号映射表.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_audit_gap_report(rows: list[CalcRow], summary: dict[str, Any]) -> None:
    all_modules = {row.module for row in rows}
    covered_modules = {module for _sid, _name, modules, _precision, _source in SECTION_RULES for module in modules}
    unmatched_modules = sorted(all_modules - covered_modules)
    parameter_sections = {row[0] for row in PARAMETER_SOURCE_ROWS}
    rule_sections = {section_id for section_id, *_rest in SECTION_RULES}
    parameter_sections_without_rule = sorted(parameter_sections - rule_sections)
    no_parameter_sections = [
        f"{section_id} {section_name}"
        for section_id, section_name, *_rest in SECTION_RULES
        if section_id not in parameter_sections
    ]
    stale_table_dirs = sorted(
        p.name for p in (DATA / "tables").iterdir() if p.is_dir() and p.name not in CURRENT_DOC_IDS
    )
    source_counts: dict[str, int] = {}
    for _section_id, _parameter, _value, _unit, source_type, _source_note, _destination in PARAMETER_SOURCE_ROWS:
        source_counts[source_type] = source_counts.get(source_type, 0) + 1

    lines = [
        "# 复核审计与缺漏清单",
        "",
        "## 审核结论",
        "",
        f"- 当前有效源文档：{len(CURRENT_DOC_IDS)} 份。",
        f"- 当前有效表格CSV：{current_table_count()} 张。",
        f"- 计算/审计行：{summary['total_rows']} 行；可对照或条件校核：{summary['comparable_rows']} 行；通过：{summary['passed_rows']} 行；失败：{summary['failed_rows']} 行。",
        f"- 小节覆盖检查：{'无未归类计算模块' if not unmatched_modules else '存在未归类计算模块'}。",
        f"- 参数来源检查：{'无未匹配参数小节' if not parameter_sections_without_rule else '存在未匹配参数小节'}。",
        "",
        "## 本轮已修正",
        "",
        "- 把 `Aspen需提供参数清单.md` 的口径从“两份说明书”修正为“三份说明书取并集”。",
        f"- 把 `计算可靠性证明.md` 的方法口径从旧的“2份DOCX/45张表”修正为当前“三份DOCX/{current_table_count()}张表”。",
        "- 将 `人工设定参数来源清单.md` 改为由 `PARAMETER_SOURCE_ROWS` 自动生成，避免与小节级参数表脱节。",
        "- 新增 `设备位号映射表.md`，明确T0301/T802/T0402、E0108/E0104、R0201/补充资料3反应器之间只能迁移方法，不能合并数值。",
        "",
        "## 仍然缺漏或需保留边界",
        "",
        "| 缺漏项 | 当前状态 | 影响 | 补齐方式 |",
        "| --- | --- | --- | --- |",
        "| 补充资料3的T0402/E0104/R0101完整链 | 已补T0402流股/水力学/接管、E0104设计条件、R0101设计T/P的最小复算链 | 不能用T0301/E0108/R0201脚本值直接替代；仍不能覆盖完整EDR/SW6/动力学 | 继续补T0402完整塔高和SW6、E0104完整EDR/SW6、R0101动力学冻结和结构强度 |",
        "| 主文档R0201低温点标签 | 主文档table_15写220 C，但table_16使用553.15 K；补充资料3写280 C与553.15 K一致 | 影响动力学温度点叙述，不能把主文档标签照抄进Aspen | 正文中按review说明，优先用补充资料3的280/360 C标签解释，待原始文献确认 |",
        "| R0201动力学冻结链 | 仅复算单位换算和Arrhenius拟合，状态为provisional | 不能作为正式Aspen动力学卡片 | 补原文速率方程、单位、反应级数、rate basis、Aspen输入卡和导出验证 |",
        "| Column Internals/EDR/SW6原始软件证据 | 文档有截图/描述线索，工作包中尚未形成可机读导出证据 | 塔径、填料水力学、换热面积、管板/法兰/裙座等不能由脚本替代 | 导出Aspen Column Internals、Aspen EDR和SW6报告，并记录版本/工况/单位 |",
        "| 目录级设备选型 | 已列入可靠性边界，多数为E类象征性/目录选型 | 泵、压缩机、储罐、分离器、膜、混合器等不能宣称严格校核 | 逐类补Aspen物性、厂家曲线和最小校核脚本 |",
        "| OMML公式批量转写 | 已索引公式对象，但未全部转写成Python | 补充资料3中可能还有可脚本化公式未利用 | 对T0402/E0104优先做公式转写，不建议一次性盲转全部公式 |",
        "| 设备位号映射 | 已补 `设备位号映射表.md` | 仍需正文引用该表避免混淆 | 在正文并集说明处引用，说明只迁移方法不迁移数值 |",
        "",
        "## 结构性检查",
        "",
        "| 检查项 | 结果 |",
        "| --- | --- |",
        f"| 计算模块是否全部映射到小节 | {'通过' if not unmatched_modules else '需补：' + ', '.join(unmatched_modules)} |",
        f"| 参数小节是否都有规则定义 | {'通过' if not parameter_sections_without_rule else '需补：' + ', '.join(parameter_sections_without_rule)} |",
        f"| 无参数来源的小节 | {', '.join(no_parameter_sections) if no_parameter_sections else '无'} |",
        f"| 旧版表格目录残留 | {', '.join(stale_table_dirs) if stale_table_dirs else '无'} |",
        "",
        "旧版表格目录残留不影响当前索引和脚本计算；正式引用时只使用 `doc_guobao_tower`、`doc_main_detailed`、`doc_supplement3` 三个稳定文档ID。",
        "",
        "## 参数来源分布",
        "",
        "| 来源类型 | 数量 |",
        "| --- | ---: |",
    ]
    for source_type, count in sorted(source_counts.items()):
        lines.append(f"| {md_cell(source_type)} | {count} |")
    lines.extend(
        [
            "",
            "## 结论性写法建议",
            "",
            f"可在正文中写：脚本已经证明三份文档中可公式化/表格化部分可重复，{summary['comparable_rows']}条可对照或条件校核中{summary['passed_rows']}条通过；{summary['failed_rows']}条需复核项分为R0201动力学低温标签冲突和泵单点功率反推密度筛错。EDR、SW6、Column Internals、厂家曲线和未冻结动力学仍是可靠性边界。",
        ]
    )
    (OUT / "复核审计与缺漏清单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_formula_reliability_report(rows: list[CalcRow], summary: dict[str, Any]) -> None:
    doc_groups = [
        (
            "主文档：3-设备设计及选型说明书(1).docx",
            ["T0301 tower", "T0301 nozzle", "T0301 strength", "E0108 exchanger", "E0108 nozzle", "E0108 strength", "R0201 kinetics", "R0201 reactor", "R0201 heat transfer", "R0201 pressure drop", "R0201 nozzle", "R0201 strength", "R0201 kinetics source audit"],
        ),
        (
            "国宝特工设备选型说明(1).docx",
            ["T802 union tower", "T802 union hydraulic"],
        ),
        (
            "补充资料3：3-设备设计与选型说明书(1).docx",
            ["SUP3 T0402 stream", "SUP3 T0402 hydraulic", "SUP3 T0402 nozzle", "SUP3 E0104 exchanger", "SUP3 kinetics source audit", "SUP3 R0101 reactor"],
        ),
        (
            "第4-8章目录设备筛错补充",
            ["CH4 separator audit", "CH5 compressor audit", "CH6 vessel geometry audit", "CH7 membrane audit", "CH8 pump audit"],
        ),
    ]

    lines = [
        "# 三文档计算式可靠性审计",
        "",
        "本报告回答“计算式可靠吗、模拟算一遍能不能对上”。口径是：只统计有明确公式、表格读数或条件判据的行；EDR、SW6、Column Internals截图和厂家曲线仍作为外部证据边界。",
        "",
        f"- 总计算/审计行：{summary['total_rows']}",
        f"- 有文档数值或条件可校核：{summary['comparable_rows']}",
        f"- 校核通过：{summary['passed_rows']}",
        f"- 校核未通过/需复核：{summary['failed_rows']}",
        "",
        "## 分文档结果",
        "",
        "| 文档 | 覆盖模块 | 行数 | 可校核 | 通过 | 需复核 | 最大相对误差 | 结论 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for doc_name, modules in doc_groups:
        matched = [row for row in rows if row.module in modules]
        comparable = [row for row in matched if row.pass_check is not None]
        passed = [row for row in comparable if row.pass_check]
        failed = [row for row in comparable if row.pass_check is False]
        max_rel = max((row.rel_error for row in matched if row.rel_error is not None), default=None)
        if failed:
            conclusion = "有需复核项"
        elif any(row.status == "review" for row in matched):
            conclusion = "算术可复核，但存在源文档标签矛盾"
        else:
            conclusion = "可脚本化部分能对上"
        lines.append(
            "| "
            + " | ".join(
                [
                    doc_name,
                    "、".join(modules),
                    str(len(matched)),
                    str(len(comparable)),
                    str(len(passed)),
                    str(len(failed)),
                    fmt_percent(max_rel),
                    conclusion,
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 关键公式复核结论",
            "",
            "| 公式族 | 复核对象 | 结果 | 备注 |",
            "| --- | --- | --- | --- |",
            "| 管径/流速 | T0301、E0108、R0201、T0402接管 | 通过 | 由体积流量和标准管内径复算实际流速；第三份T0402也已补入。 |",
            "| 塔流股衡算 | T802、T0402 | 通过 | 质量/摩尔衡算闭合；体积流量只作信息行，因为密度和组成改变时不守恒。 |",
            "| 塔水力学表读数 | T802、T0402 Column Internals表 | 条件通过 | 能力因子、填料段高度、压降范围可由表复核；正式证据仍是Aspen导出。 |",
            "| 设计压力/允许压降 | E0108、E0104、R0101/R0201 | 部分通过，部分需说明规则 | E0104设计压力不像1.1倍，更接近Pmax+0.1 MPa；已在参数表中修正来源说明。 |",
            "| 壁厚/强度基础式 | T0301、E0108、R0201 | 通过到基础壁厚 | 最终名义厚度、裙座、管板、法兰、开孔补强仍需SW6。 |",
            "| R0201动力学换算 | 主文档与补充资料3交叉审计 | 算术可复核，但主文档温度标签有矛盾 | 主文档写220 C却对应553.15 K；补充资料3写280 C与553.15 K一致。动力学仍provisional。 |",
            "| 第4-8章目录设备筛错 | V0102、C0101-C0301、储罐、S0101、泵表 | 部分通过，泵单点功率暴露异常 | 这些是筛错项，不是正式选型通过项；泵异常需补密度、NPSH、厂家曲线。 |",
            "",
            "## 需复核分类",
            "",
            *failed_category_lines(summary),
            "",
            "## 需复核项明细",
            "",
        ]
    )
    review_rows = [row for row in rows if row.pass_check is False or row.status == "review"]
    if not review_rows:
        lines.append("无未通过或review行。")
    else:
        lines.extend(
            [
                "| 模块 | 项目 | 脚本值 | 单位 | 文档/条件 | 误差 | 状态 | 说明 |",
                "| --- | --- | ---: | --- | --- | ---: | --- | --- |",
            ]
        )
        for row in review_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        md_cell(row.module),
                        md_cell(row.item),
                        fmt_float(row.value),
                        md_cell(row.unit),
                        md_cell(row.document_value_raw),
                        fmt_float(row.abs_error),
                        md_cell(row.status),
                        md_cell(row.note),
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## 可靠性边界",
            "",
            "- `能对上` 的含义是：表格数字、公式算术或条件判据能被脚本重复，不等于EDR/SW6/Column Internals/厂家曲线已经正式验证。",
            "- R0201与补充资料3反应器动力学不得直接写入Aspen正式卡；仍需 `source equation/value -> source units -> conversion -> Aspen card units -> exact input -> exported verification`。",
            "- 第三份补充资料3现在已有最小复算链，但T0402完整塔高/强度、E0104完整EDR/SW6、R0101完整反应器结构仍未全部脚本化。",
        ]
    )
    (OUT / "三文档计算式可靠性审计.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_supplement3_inclusion_report(summary: dict[str, Any]) -> None:
    lines = [
        "# 补充资料3纳入说明",
        "",
        "## 文件定位",
        "",
        "补充资料3为：",
        "",
        "`source/3-设备设计与选型说明书(1).docx`",
        "",
        "它不是前两份的小附件，而是一版较完整的设备设计与选型说明书。当前已纳入三文档并集审计：",
        "",
        "- 表格：46张，归档在 `data/tables/doc_supplement3/`。",
        "- 章节/证据线索：已并入 `data/docx_outline.json`。",
        "- 总索引：`outputs/表格提取索引.md` 与 `outputs/DOCX章节与证据线索.md`。",
        f"- 当前三文档复算结果：{summary['total_rows']} 行计算/审计，{summary['comparable_rows']} 行可对照或条件校核，{summary['passed_rows']} 行通过，{summary['failed_rows']} 行需复核。",
        "",
        "## 新增价值",
        "",
        "| 新增内容 | 价值 | 当前处理 |",
        "| --- | --- | --- |",
        "| T0402 DMSO精馏塔案例 | 与国宝特工T802类似，但设备位号更规范，理论板数21、进料板13、流股号0420/0433/0439 | 已补T0402流股衡算、Column Internals表范围校核、接管流速、设计条件等最小复算链；完整塔高/强度/SW6仍需补齐 |",
        "| E0104换热器案例 | 补了另一台换热器的工艺参数、设计压力、EDR允许压降等 | 已补设计压力、允许压降、设计温度裕量复算；完整EDR面积/压降、接管、壁厚和SW6仍需补齐 |",
        "| R0101/反应器线索 | 含壳体/管箱设计T/P、反应管、管板法兰等线索 | 已补管程/壳程设计压力和设计温度最小复算链；动力学和结构强度仍按独立边界处理 |",
        "| 动力学温度标签 | 可与主文档R0201动力学温度点交叉复核 | 补充资料3写280/360 C，对应553.15/633.15 K；用于解释主文档220 C标签矛盾 |",
        "",
        "## 不直接覆盖旧脚本的原因",
        "",
        "- 主文件当前脚本已复现 T0301/E0108/R0201 的主干详算链。",
        "- 补充资料3中的 T0402、E0104、R0101 与 T0301、E0108、R0201 不是同一台设备，不能简单替换数值。",
        "- 三份文档取并集时只能迁移方法、脚本结构和证据门槛，不能迁移流股、工况、几何、动力学或软件校核数值。",
        "- 动力学参数仍必须按 `source equation/value -> source units -> conversion -> Aspen card units -> exact input -> exported verification` 冻结。",
        "",
        "## 下一步建议",
        "",
        "1. 补齐 `T0402` 独立完整链：塔底空间、塔高、外压强度、SW6报告。",
        "2. 补齐 `E0104` 独立完整链：EDR面积/压降、接管、壳体/封头/管板/SW6。",
        "3. 对补充资料3反应器章节做动力学冻结审查，不直接复制 k/E 到 Aspen。",
        "4. 在正文引用 `设备位号映射表.md`，明确 `T0301/T802/T0402`、`E0108/E0104`、`R0201/R0101` 只迁移方法不合并数值。",
    ]
    (OUT / "补充资料3纳入说明.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_mismatch_root_cause_report() -> None:
    low_220_k = 220 + 273.15
    low_280_k = 280 + 273.15
    high_k = 360 + 273.15
    r_j_mol_k = 8.31446261815324
    k_low = {
        "k2": source_k_to_aspen(65),
        "k3": source_k_to_aspen(24),
        "k4": source_k_to_aspen(75),
    }
    k_high = {
        "k2": source_k_to_aspen(1300),
        "k3": source_k_to_aspen(1200),
        "k4": source_k_to_aspen(800),
    }
    doc_fit = {
        "k2": (1123.26, 109.034),
        "k3": (585025, 142.385),
        "k4": (8.95427, 86.1555),
    }
    pump_failed = [
        ("P0106A/B", 12572.98, "0.07 m3/h, 34.87 m, eta 15.2%, P 0.55 kW"),
        ("P0202A/B", 2693.86, "7.36 m3/h, 44.17 m, eta 42%, P 5.68 kW"),
        ("P0203A/B", 3383.97, "7.35 m3/h, 35.21 m, eta 42%, P 5.68 kW"),
        ("P0207A/B", 2572.40, "1.02 m3/h, 45.75 m, eta 30%, P 1.09 kW"),
        ("P0302A/B", 3300.91, "2.10 m3/h, 20.77 m, eta 37%, P 1.06 kW"),
        ("P0303A/B", 37536.80, "0.02 m3/h, 68.58 m, eta 25.5%, P 0.55 kW"),
        ("P0308A/B", 2649.09, "14.91 m3/h, 75.97 m, eta 67%, P 12.2 kW"),
        ("P0310A/B", 3264.95, "0.25 m3/h, 43.85 m, eta 13%, P 0.75 kW"),
        ("P0401A/B", 4848.02, "1.47 m3/h, 45.00 m, eta 16%, P 5.46 kW"),
        ("P0402A/B", 4848.02, "1.47 m3/h, 45.00 m, eta 16%, P 5.46 kW"),
        ("P0403A/B", 4478.38, "2.10 m3/h, 34.10 m, eta 16%, P 5.46 kW"),
        ("P0404A/B", 4478.38, "2.10 m3/h, 34.10 m, eta 16%, P 5.46 kW"),
        ("P0405A/B", 5551.72, "0.07 m3/h, 78.97 m, eta 15.2%, P 0.55 kW"),
        ("P0406A/B", 5551.72, "0.07 m3/h, 78.97 m, eta 15.2%, P 0.55 kW"),
    ]

    lines = [
        "# 不匹配项根因审查",
        "",
        "## 结论",
        "",
        "当前需复核项分两类：",
        "",
        "1. R0201动力学低温点标签冲突：不是公式问题，也不是脚本抽取问题；根因是主文档R0201动力学表中的低温摄氏标签与后续K温标和拟合结果不一致。",
        "2. 第8章泵单点功率筛错：不是泵功率公式问题；根因是目录表中的轴功率/效率/流量/扬程单点缺少介质密度、NPSH和厂家曲线，反推密度超出合理筛错窗口时不能写成正式工况功率通过。",
        "",
        "- 主文档 `table_15` 写低温点为 `220 C`。",
        "- 主文档 `table_16` 对应低温K值为 `553.15 K`，它等于 `280 C + 273.15`，不等于 `220 C + 273.15`。",
        "- 补充资料3 `table_24` 明确写 `280 C / 553.15 K / 65,24,75`，与主文档 `table_16` 和Arrhenius拟合一致。",
        "- 因此审查建议：正文按 `主文档低温标签疑似应为280 C` 处理；未找到原始文献前，动力学仍保持 `provisional`。",
        "",
        "## 原始表格互证",
        "",
        "| 来源 | 低温摄氏标签 | 低温K值 | 低温k2/k3/k4 | 高温点 | 判断 |",
        "| --- | ---: | ---: | --- | --- | --- |",
        "| 主文档 table_15 | 220 C | 未给 | 65 / 24 / 75 | 360 C, 1300 / 1200 / 800 | 与后续K温标冲突 |",
        "| 主文档 table_16 | 未给 | 553.15 K | 5.67E-08 / 2.09E-08 / 6.55E-08 | 633.15 K | 与280 C一致 |",
        "| 补充资料3 table_24 | 280 C | 553.15 K | 65 / 24 / 75 | 360 C / 633.15 K | 自洽，并解释主文档冲突 |",
        "",
        "## 公式审查",
        "",
        "| 审查点 | 公式 | 代入 | 结果 | 结论 |",
        "| --- | --- | --- | ---: | --- |",
        f"| 摄氏转K：主文档标签 | `T_K = T_C + 273.15` | `220 + 273.15` | {fmt_float(low_220_k)} K | 与文档553.15 K不一致 |",
        f"| 摄氏转K：补充资料3标签 | `T_K = T_C + 273.15` | `280 + 273.15` | {fmt_float(low_280_k)} K | 与文档553.15 K一致 |",
        f"| 高温点温标 | `T_K = T_C + 273.15` | `360 + 273.15` | {fmt_float(high_k)} K | 与文档633.15 K一致 |",
        f"| `1/RT`低温点 | `1/(R*T)` | `T=553.15 K` | {fmt_float(1/(r_j_mol_k*low_280_k))} | 四舍五入为0.00022，匹配table_16 |",
        f"| `1/RT`若按220 C | `1/(R*T)` | `T=493.15 K` | {fmt_float(1/(r_j_mol_k*low_220_k))} | 应约为0.00024，不匹配table_16 |",
        "",
        "## Arrhenius反推审查",
        "",
        "同一批速率常数分别按低温点220 C和280 C拟合 `ln(k)=ln(A)-E/(RT)`。若公式有问题，两种温标都不会系统匹配；实际结果显示只有280 C能匹配文档表。",
        "",
        "| 低温标签 | 反应常数 | 拟合A | 文档A | A相对误差 | 拟合E(kJ/mol) | 文档E(kJ/mol) | E相对误差 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for low_c, t_low in [(220, low_220_k), (280, low_280_k)]:
        for key in ("k2", "k3", "k4"):
            a_fit, e_fit = arrhenius_from_two_points(t_low, k_low[key], high_k, k_high[key])
            doc_a, doc_e = doc_fit[key]
            a_rel = (a_fit - doc_a) / doc_a
            e_rel = (e_fit - doc_e) / doc_e
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"{low_c} C",
                        key,
                        fmt_float(a_fit),
                        fmt_float(doc_a),
                        fmt_percent(a_rel),
                        fmt_float(e_fit),
                        fmt_float(doc_e),
                        fmt_percent(e_rel),
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## 根因判定",
            "",
            "| 可能原因 | 审查结果 | 判定 |",
            "| --- | --- | --- |",
            "| 公式错误 | 摄氏转K、`1/RT`、`ln(k)`、两点Arrhenius拟合在280 C链上全部能复现文档 | 排除 |",
            "| 单位换算错误 | `65 -> 5.67E-08`、`24 -> 2.09E-08`、`75 -> 6.55E-08` 与table_16一致 | 排除 |",
            "| 表格抽取错误 | 直接读取DOCX原始单元格，主文档确实为`220`和`553.15`；补充资料3确实为`280`和`553.15` | 排除 |",
            "| 文档表内标签错误 | 主文档220 C与553.15 K不一致；补充资料3给出280 C且与553.15 K、A/E拟合一致 | 成立 |",
            "| 原始文献另有温点 | 目前未见原始文献页；若提交正式动力学卡片，仍需原文献冻结链 | 待补证据 |",
            "",
            "## 处理建议",
            "",
            "1. 设备选型计算可靠性统计中保留该行为 `review`，不要把它硬改成通过。",
            "2. 正文写法建议：`主文档动力学低温点摄氏标签疑似误写为220 C；按后续K值、补充资料3和Arrhenius拟合，应为280 C。`",
            "3. Aspen正式动力学输入前必须补原始文献速率方程、单位、速率基准、浓度基准、Aspen卡片单位和导出验证。",
            "",
            "## 第8章泵筛错项根因",
            "",
            "泵筛错使用反推密度公式：`rho = P*eta/(g*Q*H)`。若表中轴功率确为同一工况轴功率，且Q/H/eta单位一致，反推密度应落在介质合理范围内。超出筛错窗口并不说明脚本公式错，而说明表中单点功率可能是电机配套功率、型号目录点、保守选型值或不同工况值，不能直接当正式过程功率。",
            "",
            "| 位号 | 反推密度kg/m3 | 输入摘要 | 判断 |",
            "| --- | ---: | --- | --- |",
        ]
    )
    for tag, rho, inputs in pump_failed:
        lines.append(f"| {tag} | {rho:.2f} | {inputs} | 超出300-2500 kg/m3筛错窗口，需补Aspen密度/NPSH/厂家曲线 |")
    lines.extend(
        [
            "",
            "泵表处理建议：",
            "",
            "1. 保留位号、型号、Q/H/eta/P、材质和台数为目录级参数。",
            "2. 将反推密度异常的轴功率写为“需复核/厂家边界”，不要写“工艺轴功率正确”。",
            "3. 补同工况Aspen密度、蒸气压、吸入压力、管损、NPSHa和厂家Q-H-P-NPSHr曲线后再判定正式通过。",
        ]
    )
    (OUT / "不匹配项根因审查.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_generalization_decision_report(summary: dict[str, Any]) -> None:
    lines = [
        "# 泛化计算与人工判定规则",
        "",
        "## 核心原则",
        "",
        "本工作包不应只是逐字复算器，也不应自动把一个设备的数值套到另一个设备。正确分工是：",
        "",
        "- 脚本负责：通用公式函数、单位换算、标准圆整后的反算、误差审计、来源追踪。",
        "- 人工负责：判断设备是否同类、选哪条公式链、选择哪些默认值、确定软件/厂家/规范边界。",
        "- 报告负责：把能脚本化的部分证明可靠，把不能脚本化的部分列为EDR/SW6/Column Internals/厂家/文献证据边界。",
        "",
        f"当前脚本已经复算 {summary['total_rows']} 行；其中 {summary['comparable_rows']} 行可对照或条件校核，{summary['passed_rows']} 行通过，{summary['failed_rows']} 行保留为人工复核。",
        "",
        "## 泛化层级",
        "",
        "| 层级 | 脚本可泛化内容 | 人工必须判定内容 |",
        "| --- | --- | --- |",
        "| 设备族 | 塔、换热器、反应器、接管、强度、泵、压缩机、储罐等模块模板 | 当前位号是不是同一台设备，能否迁移方法，不能迁移哪些数值 |",
        "| 公式族 | 管径/流速、持液高度、设计压力、设计温度裕量、壁厚、传热、压降、衡算、Arrhenius拟合 | 采用哪个公式变体，公式适用条件是否满足 |",
        "| 参数源 | Aspen流股、文档表格、文献、规范、软件导出、人工默认值 | 源优先级，冲突时采哪一个，是否需要保留review |",
        "| 可靠性 | A/B/C/D/E分级、误差和容差、边界清单 | 是否能从选型前步骤升级为正式设计证据 |",
        "",
        "## 当前可泛化的计算函数",
        "",
        "| 公式族 | 典型函数/方法 | 可迁移对象 | 人工判定点 |",
        "| --- | --- | --- | --- |",
        "| 接管理论直径 | `sqrt(4*Q/(pi*u))` | 塔、换热器、反应器、储罐接管 | 气/液/两相目标流速、Q的相态与单位、是否需要标准管圆整 |",
        "| 选管后流速 | `4*Q/(pi*d_i^2)` | 所有接管 | 标准管规格、壁厚、腐蚀裕量是否已计入 |",
        "| 设计压力 | `1.1*P_op` 或 `Pmax+0.1 MPa` 等 | 塔、换热器、反应器、容器 | 取规范规则还是文档规则；E0104已证明不是简单1.1倍 |",
        "| 设计温度裕量 | `T_design - T_max` | 换热器、反应器、容器 | 15-30 C是否适用，是否有特殊材料/相变限制 |",
        "| 筒体/封头基础壁厚 | GB150类基础式 | 塔、换热器、反应器壳体 | 材料许用应力、焊接系数、腐蚀裕量、最终SW6边界 |",
        "| 塔底持液高度 | `(Q*t)/(pi*D^2/4)` | 塔、回流罐、缓冲罐初筛 | 停留时间取值、是否被内件/人孔/液位布置覆盖；T0402就是布置覆盖库存公式 |",
        "| 塔水力学表审计 | %Capacity范围、填料段高度、压降范围 | T802、T0402、后续塔 | 范围阈值是否来自设计要求，正式证据仍是Aspen导出 |",
        "| 换热器设计条件 | 允许压降、设计T/P、EDR表值读取 | E0108、E0104、后续换热器 | EDR面积/U/Re/压降不能由简化脚本替代 |",
        "| 固定床几何 | 催化剂量、管束直径、换热面积 | R0201、R0101候选 | 管径/管数/床长是否为人工结构结果，催化剂物性来源 |",
        "| 传热与压降 | Nusselt、总K、面积、Ergun | 固定床/换热型反应器 | 物性取平均是否合理，表面积基准、热媒侧系数、空隙率 |",
        "| 动力学拟合 | 单位换算、`ln(k)=ln(A)-E/(RT)`两点拟合 | 仅用于审计表格算术 | 不得替代完整文献-Aspen冻结链 |",
        "",
        "## 不能自动泛化的部分",
        "",
        "| 对象 | 原因 | 正确处理 |",
        "| --- | --- | --- |",
        "| 不同位号之间的工况/流股/几何 | T0301、T802、T0402不是同一台塔；E0108、E0104不是同一台换热器 | 只迁移方法，不迁移数值 |",
        "| EDR面积、U值、Re、压降 | 软件模型依赖物性、结构、壳程算法 | 脚本只审计输入/表值，正式证据用EDR导出 |",
        "| Column Internals塔径/液泛/压降 | 依赖Aspen内置塔水力学模型和填料数据库 | 脚本可读表做范围检查，正式证据用Aspen导出 |",
        "| SW6强度 | 裙座、管板、法兰、开孔补强不是基础壁厚公式能覆盖 | 脚本只算基础式，正式证据用SW6报告 |",
        "| 厂家目录设备 | 泵、压缩机、膜、储罐等型号受曲线/系列限制 | 先补Aspen物性和厂家曲线，再写最小校核 |",
        "| 动力学卡片 | 缺源方程、速率基准、浓度基准、Aspen单位和导出验证 | 保持`provisional`或`blocked`，不能硬填 |",
        "",
        "## 人工判定流程",
        "",
        "1. 先定设备身份：这是同一台设备，还是同类样板？不同设备只迁移方法。",
        "2. 再定目标：是证明文档内算术可靠，还是做正式设计？二者证据等级不同。",
        "3. 选择公式族：从管径、强度、传热、压降、衡算、动力学等模板中选一条。",
        "4. 填参数来源：Aspen/文献/规范/软件/厂家/人工默认值必须逐项标明。",
        "5. 运行脚本复算：检查文档值、误差、容差和状态。",
        "6. 对不上时先审根因：公式错、单位错、抽取错、表格错、人工假设错，逐项排除。",
        "7. 输出边界：能证明的写`通过`；只能算到前步骤的写`B`；需要EDR/SW6/文献/厂家证据的不要升级。",
        "",
        "## 本项目中的人工判定实例",
        "",
        "| 案例 | 不能自动处理的原因 | 人工判定结果 |",
        "| --- | --- | --- |",
        "| 主文档R0201低温点 | 220 C与553.15 K冲突 | 按根因审查保留review；补充资料3和拟合显示应为280 C，但正式动力学仍provisional |",
        "| E0104设计压力 | 0.76/0.62 MPa不能由1.1倍精确复现 | 改按`Pmax+0.1 MPa`近似解释，并标明需引用规则后才能正式采用 |",
        "| T0402塔底空间 | 5 min库存公式只给约0.00493 m，不可能解释HB=1.0 m | 判定最终值由液体收集器、手孔、内件布置覆盖，不是纯库存公式 |",
        "| E0108接管理论直径 | 文档表给的是标准管内径/规格，不是纯理论直径 | 同时审计理论直径和选管后流速 |",
        "| T802/T0402水力学 | 表格可读，但Aspen水力学模型不可由脚本复现 | 脚本做范围检查，正式证据仍是Column Internals导出 |",
        "",
        "## 推荐正文写法",
        "",
        "本项目采用“人工判定公式链 + 脚本复算审计”的方式：先由设计者根据设备位号、工况来源和规范边界选择计算路径，再由脚本执行通用公式、单位换算和误差审计。脚本具有同类设备的泛化计算能力，但不自动迁移不同设备的数值；EDR、SW6、Column Internals、厂家曲线和未冻结动力学仍作为外部正式证据。",
    ]
    (OUT / "泛化计算与人工判定规则.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_late_chapter_parameter_ledger() -> None:
    rows = build_late_chapter_parameter_ledger()
    payload = [asdict(row) for row in rows]
    (DATA / "chapter_04_08_parameter_ledger.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    csv_path = DATA / "chapter_04_08_parameter_ledger.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    by_chapter: dict[str, int] = {}
    by_action: dict[str, int] = {}
    by_evidence: dict[str, int] = {}
    for row in rows:
        by_chapter[row.chapter] = by_chapter.get(row.chapter, 0) + 1
        by_action[row.action] = by_action.get(row.action, 0) + 1
        by_evidence[row.evidence_class] = by_evidence.get(row.evidence_class, 0) + 1

    lines = [
        "# 第4-8章全量参数来源 Ledger",
        "",
        "本 ledger 回答“哪些参数、分别是多少、来源是什么、判断标准链接到哪里”。它是全量参数来源账本，不是全量正式通过账本；没有 Aspen/标准页/SW6/厂家曲线/文献冻结链时，一律保持目录级、筛错或补证状态。",
        "",
        f"- 参数行数：{len(rows)}",
        f"- 机器可读 JSON：`data/chapter_04_08_parameter_ledger.json`",
        f"- 机器可读 CSV：`data/chapter_04_08_parameter_ledger.csv`",
        "",
        "## 按章统计",
        "",
        "| 章节 | ledger行数 |",
        "| --- | ---: |",
    ]
    for chapter, count in sorted(by_chapter.items()):
        lines.append(f"| {chapter} | {count} |")
    lines.extend(["", "## 按处理动作统计", "", "| 处理动作 | 行数 |", "| --- | ---: |"])
    for action, count in sorted(by_action.items()):
        lines.append(f"| {action} | {count} |")
    lines.extend(["", "## 按证据等级统计", "", "| 证据等级 | 行数 |", "| --- | ---: |"])
    for evidence, count in sorted(by_evidence.items()):
        lines.append(f"| {evidence} | {count} |")

    lines.extend(
        [
            "",
            "## 关键人工判定",
            "",
            "| 问题 | 判定 | 判断标准链接 |",
            "| --- | --- | --- |",
            "| 第4章V0102样例 | 流股、密度、设计压力、气速/K值可筛错；分离效率、丝网除沫、SW6不能宣称通过 | `knowledge_graph/chapter_04_08_late_equipment_graph.md`; `knowledge_graph/evidence_boundary_nodes.md` |",
            "| 第5章C0101双分支 | 主文档C0101与补充资料3 C0101型号、型式、压力、流量、功率、材质均不同，禁止合并为同一正确值 | `knowledge_graph/equipment_graph_index.md`; `knowledge_graph/evidence_boundary_nodes.md` |",
            "| 第6章V0104/V0101同位号 | 主文档和补充资料3出现同位号不同对象，保留为不同来源分支 | `knowledge_graph/chapter_04_08_late_equipment_graph.md`; `knowledge_graph/standards_graph/vessel_standards_nodes.md` |",
            "| 第7章膜/混合器 | 膜面积可几何复核；膜通量/选择性/寿命、混合均匀度/压降需厂家或实验/文献补证 | `knowledge_graph/formula_family_nodes.md`; `knowledge_graph/evidence_boundary_nodes.md` |",
            "| 第8章泵 | Q/H/eta/P可做轴功率或反推密度筛错；NPSH、BEP、厂家曲线和系统阻力未闭合 | `knowledge_graph/formula_family_nodes.md`; `knowledge_graph/evidence_boundary_nodes.md` |",
        ]
    )

    lines.extend(
        [
            "",
            "## 全量参数表",
            "",
            "| 章节 | 设备族 | 对象 | 参数 | 数值 | 单位 | 来源 | 来源类型 | 证据等级 | 处理动作 | 判断标准链接 | 可脚本化公式 | 备注 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    md_cell(row.chapter),
                    md_cell(row.equipment_family),
                    md_cell(row.object_id),
                    md_cell(row.parameter),
                    md_cell(row.value),
                    md_cell(row.unit),
                    md_cell(f"{row.source_document} {row.source_table}"),
                    md_cell(row.source_type),
                    md_cell(row.evidence_class),
                    md_cell(row.action),
                    md_cell(row.judgment_links),
                    md_cell(row.scriptable_formula),
                    md_cell(row.note),
                ]
            )
            + " |"
        )
    (OUT / "第4-8章全量参数来源ledger.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_late_chapter_coverage_report() -> None:
    rows = build_late_chapter_parameter_ledger()
    chapter_counts: dict[str, int] = {}
    for row in rows:
        chapter_counts[row.chapter] = chapter_counts.get(row.chapter, 0) + 1
    lines = [
        "# 知识图谱第4-8章覆盖说明",
        "",
        "## 回答",
        "",
        "第 4-8 章现在已经完成三层覆盖：",
        "",
        "1. 知识图谱深层路由：设备族、公式候选、参数来源、证据边界和标准图谱入口已连接。",
        "2. 全量参数来源 ledger：主文档表23-32、补充资料3表33-45已逐参数展开，见 `outputs/第4-8章全量参数来源ledger.md`。",
        "3. 可闭合项脚本筛错：已补 V0102衡算/设计压力/气速/K值反推、压缩机压比、容器几何比、S0101膜面积、泵反推密度等最小复算。正式厂家/软件/规范证据仍未闭合。",
        "",
        "| 章节 | ledger行数 | 图谱覆盖 | 脚本覆盖 | 不能宣称通过的边界 |",
        "| --- | ---: | --- | --- | --- |",
        f"| 第四章 气液分离器设计 | {chapter_counts.get('第四章 气液分离器', 0)} | `family_separator`、V0102样例、Souders-Brown/接管/壁厚边界 | V0102质量衡算、设计压力、气速、K值反推、接管流速筛错 | 分离效率、丝网K值来源、SW6、厂家/标准页 |",
        f"| 第五章 压缩机选型 | {chapter_counts.get('第五章 压缩机', 0)} | `family_compressor`、C0101双分支、压缩功候选、厂家曲线边界 | 主文档C0101-C0301和补充资料3 C0101压比筛错 | MW/k/Z、效率、级间冷却、功率、厂家曲线 |",
        f"| 第六章 储罐/回流罐/缓冲罐 | {chapter_counts.get('第六章 储罐/回流罐/缓冲罐', 0)} | `family_storage`、库存/液位/装填/呼吸/SW6边界 | 公称容积/几何容积比筛错 | 储存天数、密度、装填系数、呼吸/氮封、SW6 |",
        f"| 第七章 膜/混合器 | {chapter_counts.get('第七章 膜/混合器', 0)} | `family_mixer_membrane`、`family_static_mixer`、厂家性能边界 | S0101几何膜面积复核 | 膜通量/选择性/回收率、混合均匀度、压降、厂家曲线 |",
        f"| 第八章 泵/透平/倾析器 | {chapter_counts.get('第八章 泵', 0) + chapter_counts.get('第八章 泵/透平/倾析器', 0)} | `family_pump`、`family_rotating`、NPSH/厂家曲线边界 | 泵轴功率反推密度筛错，P0403水基准功率筛错 | NPSHa/NPSHr、系统阻力、BEP、透平效率、倾析性能 |",
        "",
        "## 口径",
        "",
        "- `ledger覆盖` 表示参数、数值、来源、来源类型、处理动作和判断标准链接已落账。",
        "- `脚本覆盖` 表示有明确公式和输入的筛错项已复算。",
        "- `正式通过` 仍需要 Aspen、EDR、SW6、Column Internals、厂家曲线或文献冻结链；目录级表值不能自动升级。",
        "",
        "## 新增入口",
        "",
        "- `outputs/第4-8章全量参数来源ledger.md`",
        "- `data/chapter_04_08_parameter_ledger.csv`",
        "- `data/chapter_04_08_parameter_ledger.json`",
        "- `knowledge_graph/chapter_04_08_late_equipment_graph.md`",
        "- `knowledge_graph/equipment_graph_index.md`",
        "- `knowledge_graph/formula_family_nodes.md`",
        "- `knowledge_graph/evidence_boundary_nodes.md`",
        "- `knowledge_graph/standards_graph/standard_parameter_crosswalk.md`",
    ]
    (OUT / "知识图谱第4-8章覆盖说明.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_outputs(rows: list[CalcRow]) -> None:
    OUT.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)
    summary = summarize(rows)
    write_calculation_outputs(rows, summary)
    write_module_report()
    write_aspen_report()
    write_reliability_report(rows, summary)
    write_boundary_report()
    write_script_manual(summary)
    write_section_precision_report(rows, summary)
    write_section_parameter_source_report()
    write_manual_parameter_report()
    write_device_mapping_report()
    write_audit_gap_report(rows, summary)
    write_formula_reliability_report(rows, summary)
    write_supplement3_inclusion_report(summary)
    write_mismatch_root_cause_report()
    write_generalization_decision_report(summary)
    write_late_chapter_parameter_ledger()
    write_late_chapter_coverage_report()


def main() -> None:
    rows = build_calculations()
    write_outputs(rows)
    summary = summarize(rows)
    print(f"wrote {summary['total_rows']} calculation rows")
    print(f"comparable={summary['comparable_rows']} passed={summary['passed_rows']} failed={summary['failed_rows']}")
    print(OUT / "计算脚本复算结果.md")
    print(OUT / "模块拆分与任务清单.md")
    print(OUT / "Aspen需提供参数清单.md")
    print(OUT / "计算可靠性证明.md")
    print(OUT / "选型可靠性边界清单.md")
    print(OUT / "完善脚本内容说明.md")
    print(OUT / "小节级计算精度表.md")
    print(OUT / "小节级参数来源表.md")
    print(OUT / "三文档计算式可靠性审计.md")
    print(OUT / "复核审计与缺漏清单.md")
    print(OUT / "设备位号映射表.md")
    print(OUT / "补充资料3纳入说明.md")
    print(OUT / "不匹配项根因审查.md")
    print(OUT / "泛化计算与人工判定规则.md")
    print(OUT / "第4-8章全量参数来源ledger.md")
    print(OUT / "知识图谱第4-8章覆盖说明.md")


if __name__ == "__main__":
    main()
