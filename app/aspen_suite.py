from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


SUITE_SCHEMA = "equipment-design-aspen-bkp-suite-v1"
CASE_SCHEMA = "equipment-design-aspen-bkp-suite-case-v1"
SUPPORTED_SUFFIXES = {".bkp", ".apw", ".inp"}
RUN_STATUS_FIELDS = (
    "terminal_errors",
    "severe_errors",
    "errors",
    "warnings",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _safe_case_id(value: Any, source: Path, index: int) -> str:
    text = str(value or source.stem or f"CASE_{index:02d}").strip()
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")
    return (cleaned[:48] or f"CASE_{index:02d}") + f"_{index:02d}"


def _positive_timeout(value: Any, default: int = 900) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = default
    return max(10, min(timeout, 7200))


def _resolve_path(value: Any, base: Path) -> Path:
    path = Path(str(value or "")).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def resolve_cases(config: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve a JSON manifest or an explicit GUI queue into immutable case rows."""

    manifest_text = str(config.get("manifest_path") or "").strip()
    explicit_cases = config.get("cases")
    if manifest_text and explicit_cases:
        raise ValueError("批量运行只能选择 JSON 清单或界面队列中的一种输入。")

    manifest_record: dict[str, Any] = {
        "path": None,
        "sha256": None,
        "schema": None,
        "suite": None,
    }
    if manifest_text:
        manifest_path = Path(manifest_text).expanduser().resolve()
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Aspen 批量清单不存在：{manifest_path}")
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Aspen 批量清单顶层必须是 JSON 对象。")
        raw_cases = payload.get("cases")
        base = manifest_path.parent
        manifest_record = {
            "path": str(manifest_path),
            "sha256": sha256_file(manifest_path),
            "schema": payload.get("schema"),
            "suite": payload.get("suite"),
        }
    else:
        raw_cases = explicit_cases
        base_text = str(config.get("case_base_dir") or "").strip()
        base = (
            Path(base_text).expanduser().resolve()
            if base_text
            else Path.cwd().resolve()
        )
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Aspen 批量运行至少需要一个案例。")

    default_basis = str(config.get("pressure_basis") or "").strip()
    default_atmosphere = config.get("atmospheric_pressure_mpa")
    default_timeout = _positive_timeout(config.get("timeout_s"))
    default_run = bool(config.get("run", True))
    default_transport = bool(config.get("ensure_stream_transport", True))
    resolved: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_cases, 1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"批量清单第 {index} 项不是对象。")
        path_value = raw.get("source_path") or raw.get("path")
        if not str(path_value or "").strip():
            raise ValueError(f"批量清单第 {index} 项缺少 path/source_path。")
        source = _resolve_path(path_value, base)
        basis = str(raw.get("pressure_basis") or default_basis).strip()
        if basis not in {"absolute", "gauge"}:
            raise ValueError(
                f"{source.name} 缺少压力基准；必须明确选择 absolute（绝压）或 gauge（表压）。"
            )
        atmosphere = raw.get("atmospheric_pressure_mpa", default_atmosphere)
        if atmosphere in ("", None):
            atmosphere_value: float | None = None
        else:
            try:
                atmosphere_value = float(atmosphere)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{source.name} 的当地大气压不是有效 MPa 数值。") from exc
            if atmosphere_value <= 0:
                raise ValueError(f"{source.name} 的当地大气压必须大于 0 MPa。")
        if basis == "gauge" and atmosphere_value is None:
            raise ValueError(f"{source.name} 使用表压时必须填写当地大气压。")
        resolved.append({
            "index": index,
            "case_id": _safe_case_id(raw.get("id") or raw.get("case_id"), source, index),
            "group": str(raw.get("group") or "").strip() or None,
            "source_path": str(source),
            "expected_sha256": str(raw.get("sha256") or raw.get("source_sha256") or "").strip().upper() or None,
            "pressure_basis": basis,
            "atmospheric_pressure_mpa": atmosphere_value,
            "timeout_s": _positive_timeout(raw.get("timeout_s"), default_timeout),
            "run": bool(raw.get("run", default_run)),
            "ensure_stream_transport": bool(
                raw.get("ensure_stream_transport", default_transport)
            ),
            "known_run_status": raw.get("known_run_status"),
            "formal_delivery_status": raw.get("formal_delivery_status"),
        })
    return resolved, manifest_record


def _history_gate(
    worker: Mapping[str, Any],
    *,
    run_requested: bool,
    case_dir: Path,
) -> dict[str, Any]:
    if bool(worker.get("mock")):
        return {
            "status": "MOCK_NOT_FORMAL_EVIDENCE",
            "clean": False,
            "counts": (worker.get("history_parse") or {}).get("counts"),
            "problem_lines": [],
            "raw_history_path": None,
        }
    if not run_requested:
        return {
            "status": "NOT_REQUESTED_READ_ONLY",
            "clean": False,
            "counts": None,
            "problem_lines": [],
            "raw_history_path": None,
        }
    history = worker.get("history_parse")
    history = history if isinstance(history, Mapping) else {}
    counts = history.get("counts")
    problem_lines = history.get("problem_lines")
    problems = (
        [str(item) for item in problem_lines]
        if isinstance(problem_lines, list)
        else []
    )
    raw_history = case_dir / "raw_aspen_run_history.his"
    counts_valid = (
        isinstance(counts, Mapping)
        and all(
            isinstance(counts.get(field), int)
            and not isinstance(counts.get(field), bool)
            and int(counts[field]) >= 0
            for field in RUN_STATUS_FIELDS
        )
    )
    normalized_counts = (
        {field: int(counts[field]) for field in RUN_STATUS_FIELDS}
        if counts_valid
        else None
    )
    if not counts_valid or not raw_history.is_file():
        status = "RUN_EVIDENCE_MISSING"
        clean = False
    elif any(normalized_counts[field] for field in RUN_STATUS_FIELDS) or problems:
        status = "DIRTY_RUN_EVIDENCE"
        clean = False
    else:
        status = "CLEAN_RUN_EVIDENCE"
        clean = True
    return {
        "status": status,
        "clean": clean,
        "counts": normalized_counts,
        "problem_lines": problems[:200],
        "raw_history_path": str(raw_history) if raw_history.is_file() else None,
        "raw_history_sha256": sha256_file(raw_history) if raw_history.is_file() else None,
    }


def _artifact_paths(case_dir: Path) -> dict[str, str]:
    names = (
        "worker_result.json",
        "aspen_equipment_export.json",
        "aspen_equipment_derivation.json",
        "aspen_pfd_mapping.json",
        "aspen_run_status_evidence.json",
        "raw_aspen_run_history.his",
        "control_panel_capture.txt",
        "stream_transport_verification.json",
        "transport_property_augmentation_manifest.json",
    )
    return {
        name: str(case_dir / name)
        for name in names
        if (case_dir / name).is_file()
    }


def summarize_case(
    case: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    case_dir: Path,
    source_sha256_before: str,
    source_sha256_after: str | None,
    elapsed_s: float,
) -> dict[str, Any]:
    worker = response.get("value")
    worker = worker if isinstance(worker, Mapping) else {}
    derivation = worker.get("result")
    derivation = derivation if isinstance(derivation, Mapping) else {}
    equipment = derivation.get("equipment")
    piping = derivation.get("piping")
    equipment_rows = equipment if isinstance(equipment, list) else []
    piping_rows = piping if isinstance(piping, list) else []

    def selection_resolved(item: Any) -> bool:
        if not isinstance(item, Mapping):
            return False
        match_result = item.get("match_result")
        match_result = (
            match_result if isinstance(match_result, Mapping) else {}
        )
        model = match_result.get("model_recommendation")
        model = model if isinstance(model, Mapping) else {}
        leading = model.get("leading_candidate")
        leading = leading if isinstance(leading, Mapping) else {}
        designation = str(leading.get("designation") or "").strip()
        decision = match_result.get("model_decision")
        decision = decision if isinstance(decision, Mapping) else {}
        not_applicable = (
            str(match_result.get("status") or "") == "NOT_APPLICABLE"
            or str(decision.get("reason_code") or "")
            == "NOT_APPLICABLE_SIMULATION_LOGIC_NODE"
        )
        return bool(designation or not_applicable)

    unresolved_equipment = [
        str(item.get("aspen_block_id") or item.get("equipment_tag") or "UNKNOWN")
        for item in equipment_rows
        if isinstance(item, Mapping) and not selection_resolved(item)
    ]
    unresolved_piping = [
        str(item.get("stream_id") or item.get("equipment_tag") or "UNKNOWN")
        for item in piping_rows
        if isinstance(item, Mapping) and not selection_resolved(item)
    ]
    candidate_coverage_complete = not (
        unresolved_equipment or unresolved_piping
    )
    source_unchanged = source_sha256_after == source_sha256_before
    history_gate = _history_gate(
        worker,
        run_requested=bool(case.get("run")),
        case_dir=case_dir,
    )
    transport = worker.get("stream_transport_verification")
    transport = transport if isinstance(transport, Mapping) else {}
    derivation_present = bool(derivation) and (
        isinstance(equipment, list) or isinstance(piping, list)
    )
    worker_failed = str(worker.get("status") or "").upper() == "FAILED"
    usable = bool(
        derivation_present
        and source_unchanged
        and not worker_failed
        and candidate_coverage_complete
    )
    formal_ready = bool(
        usable
        and history_gate["clean"]
        and derivation.get("formal_use_gate") == "ELIGIBLE_AS_PROCESS_BASIS"
    )
    if formal_ready:
        case_status = "FORMAL_PROCESS_BASIS_READY"
    elif usable and not bool(case.get("run")):
        case_status = "READ_ONLY_SELECTION_READY"
    elif usable and response.get("ok"):
        case_status = "SELECTION_READY_FORMAL_EVIDENCE_OPEN"
    elif usable:
        case_status = "SELECTION_READY_WITH_WORKER_WARNING"
    else:
        case_status = "FAILED_TO_PRODUCE_SELECTION_RESULT"
    return {
        "schema": CASE_SCHEMA,
        "index": int(case["index"]),
        "case_id": case["case_id"],
        "group": case.get("group"),
        "source_path": case["source_path"],
        "source_sha256_before": source_sha256_before,
        "source_sha256_after": source_sha256_after,
        "source_unchanged": source_unchanged,
        "expected_sha256": case.get("expected_sha256"),
        "expected_hash_matches": (
            case.get("expected_sha256") in (None, source_sha256_before)
        ),
        "pressure_basis": case["pressure_basis"],
        "atmospheric_pressure_mpa": case.get("atmospheric_pressure_mpa"),
        "run_requested": bool(case.get("run")),
        "ensure_stream_transport": bool(case.get("ensure_stream_transport")),
        "known_run_status": case.get("known_run_status"),
        "known_formal_delivery_status": case.get("formal_delivery_status"),
        "status": case_status,
        "usable_for_equipment_selection": usable,
        "formal_process_basis_ready": formal_ready,
        "api_ok": bool(response.get("ok")),
        "api_error": response.get("error"),
        "worker_status": worker.get("status"),
        "worker_returncode": response.get("returncode"),
        "open_method": worker.get("open_method"),
        "progid": worker.get("progid"),
        "run_evidence_gate": history_gate,
        "transport_property_gate": {
            "status": transport.get("status") or "NOT_REPORTED",
            "missing_stream_count": transport.get("missing_stream_count"),
        },
        "derivation_status": derivation.get("status"),
        "derivation_formal_use_gate": derivation.get("formal_use_gate"),
        "block_count": int(worker.get("block_count") or 0),
        "stream_count": int(worker.get("stream_count") or 0),
        "equipment_count": len(equipment_rows),
        "piping_count": len(piping_rows),
        "equipment_with_resolved_selection_count": (
            len(equipment_rows) - len(unresolved_equipment)
        ),
        "piping_with_resolved_selection_count": (
            len(piping_rows) - len(unresolved_piping)
        ),
        "candidate_coverage_complete": candidate_coverage_complete,
        "unresolved_equipment_ids": unresolved_equipment,
        "unresolved_piping_ids": unresolved_piping,
        "normalization_diagnostic_count": int(
            derivation.get("normalization_diagnostic_count") or 0
        ),
        "case_output_dir": str(case_dir),
        "artifacts": _artifact_paths(case_dir),
        "elapsed_s": round(elapsed_s, 3),
    }


def _suite_status(
    rows: list[Mapping[str, Any]],
    *,
    cancelled: bool,
    require_clean: bool,
) -> str:
    if cancelled:
        return "CANCELLED"
    accepted = [
        bool(
            row.get(
                "formal_process_basis_ready"
                if require_clean
                else "usable_for_equipment_selection"
            )
        )
        for row in rows
    ]
    if accepted and all(accepted):
        return "PASS"
    if any(accepted):
        return "PARTIAL_SUCCESS"
    return "FAIL"


def _report(
    *,
    output_dir: Path,
    rows: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    manifest: Mapping[str, Any],
    started_at: str,
    started_monotonic: float,
    cancelled: bool,
    require_clean: bool,
    in_progress: bool,
) -> dict[str, Any]:
    completed = len(rows)
    usable = sum(bool(row.get("usable_for_equipment_selection")) for row in rows)
    formal = sum(bool(row.get("formal_process_basis_ready")) for row in rows)
    candidate_complete = sum(
        bool(row.get("candidate_coverage_complete")) for row in rows
    )
    return {
        "schema": SUITE_SCHEMA,
        "status": (
            "IN_PROGRESS"
            if in_progress
            else _suite_status(rows, cancelled=cancelled, require_clean=require_clean)
        ),
        "acceptance_mode": (
            "formal_process_basis" if require_clean else "equipment_selection"
        ),
        "strict_serial_execution": True,
        "continue_after_case_failure": True,
        "source_files_are_read_only_inputs": True,
        "started_at": started_at,
        "finished_at": None if in_progress else utc_now(),
        "elapsed_s": round(time.monotonic() - started_monotonic, 3),
        "output_dir": str(output_dir),
        "manifest": dict(manifest),
        "case_count": len(cases),
        "completed_count": completed,
        "usable_count": usable,
        "formal_ready_count": formal,
        "candidate_coverage_complete_count": candidate_complete,
        "failed_count": completed - usable,
        "cancelled": cancelled,
        "cases": rows,
    }


def _write_markdown(report: Mapping[str, Any], path: Path) -> None:
    lines = [
        "# Aspen BKP 批量运行报告",
        "",
        f"- 总状态：`{report['status']}`",
        f"- 验收口径：`{report['acceptance_mode']}`",
        f"- 设备选型可用：{report['usable_count']}/{report['case_count']}",
        f"- 设备与物理管线具体候选全覆盖：{report['candidate_coverage_complete_count']}/{report['case_count']}",
        f"- 正式流程基础就绪：{report['formal_ready_count']}/{report['case_count']}",
        f"- 严格串行：是；单案例失败后继续：是；源文件只读：是",
        "",
        "| # | 案例 | 文件 | 处理状态 | 运行证据 | 设备/管线 | 具体候选全覆盖 | 源文件未变 |",
        "| ---: | --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in report.get("cases", []):
        if not isinstance(row, Mapping):
            continue
        gate = row.get("run_evidence_gate")
        gate = gate if isinstance(gate, Mapping) else {}
        lines.append(
            f"| {row.get('index')} | {row.get('case_id')} | "
            f"{Path(str(row.get('source_path') or '')).name} | {row.get('status')} | "
            f"{gate.get('status')} | {row.get('equipment_count', 0)}/{row.get('piping_count', 0)} | "
            f"{'是' if row.get('candidate_coverage_complete') else '否'} | "
            f"{'是' if row.get('source_unchanged') else '否'} |"
        )
    lines.extend([
        "",
        "说明：设备选型可用不等于 Aspen 正式运行证据已经闭合。只有“正式流程基础就绪”案例，",
        "才同时通过原始运行历史、零错误/零警告、源文件未变化和派生正式门。",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def run_suite(
    config: Mapping[str, Any],
    import_case: Callable[[dict[str, Any]], Mapping[str, Any]],
    *,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run Aspen cases strictly serially while preserving per-case failures."""

    cases, manifest = resolve_cases(config)
    output_text = str(config.get("output_dir") or "").strip()
    if not output_text:
        raise ValueError("Aspen 批量运行缺少 output_dir。")
    output_dir = Path(output_text).expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"批量输出目录必须为空，避免旧结果污染：{output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "aspen_suite_report.json"
    markdown_path = output_dir / "aspen_suite_report.md"
    require_clean = bool(config.get("require_clean", False))
    started_at = utc_now()
    started_monotonic = time.monotonic()
    rows: list[dict[str, Any]] = []
    was_cancelled = False

    def emit(value: dict[str, Any]) -> None:
        if progress is not None:
            try:
                progress(value)
            except Exception:
                pass

    for case in cases:
        if cancelled is not None and cancelled():
            was_cancelled = True
            break
        index = int(case["index"])
        source = Path(str(case["source_path"]))
        case_dir = output_dir / f"case_{index:02d}_{case['case_id']}"
        emit({
            "event": "CASE_STARTED",
            "index": index,
            "case_id": case["case_id"],
            "source_path": str(source),
            "case_count": len(cases),
        })
        one_started = time.monotonic()
        if not source.is_file() or source.suffix.casefold() not in SUPPORTED_SUFFIXES:
            row = {
                "schema": CASE_SCHEMA,
                **case,
                "status": "SOURCE_FILE_INVALID",
                "usable_for_equipment_selection": False,
                "formal_process_basis_ready": False,
                "source_unchanged": None,
                "api_ok": False,
                "api_error": f"文件不存在或不是受支持的 Aspen 文件：{source}",
                "case_output_dir": str(case_dir),
                "equipment_count": 0,
                "piping_count": 0,
                "elapsed_s": round(time.monotonic() - one_started, 3),
            }
        else:
            source_before = sha256_file(source)
            expected = case.get("expected_sha256")
            if expected and expected != source_before:
                row = {
                    "schema": CASE_SCHEMA,
                    **case,
                    "status": "SOURCE_HASH_MISMATCH",
                    "usable_for_equipment_selection": False,
                    "formal_process_basis_ready": False,
                    "source_sha256_before": source_before,
                    "source_sha256_after": source_before,
                    "source_unchanged": True,
                    "expected_hash_matches": False,
                    "api_ok": False,
                    "api_error": "源文件 SHA-256 与清单不一致，已在启动 Aspen 前拒绝。",
                    "case_output_dir": str(case_dir),
                    "equipment_count": 0,
                    "piping_count": 0,
                    "elapsed_s": round(time.monotonic() - one_started, 3),
                }
            else:
                request = {
                    "source_path": str(source),
                    "output_dir": str(case_dir),
                    "pressure_basis": case["pressure_basis"],
                    "atmospheric_pressure_mpa": case.get("atmospheric_pressure_mpa"),
                    "timeout_s": case["timeout_s"],
                    "run": bool(case["run"]),
                    "ensure_stream_transport": bool(case["ensure_stream_transport"]),
                }
                try:
                    raw_response = import_case(request)
                    response = (
                        dict(raw_response)
                        if isinstance(raw_response, Mapping)
                        else {"ok": False, "error": "单案例接口返回值不是对象。"}
                    )
                except Exception as exc:
                    response = {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                source_after = sha256_file(source) if source.is_file() else None
                row = summarize_case(
                    case,
                    response,
                    case_dir=case_dir,
                    source_sha256_before=source_before,
                    source_sha256_after=source_after,
                    elapsed_s=time.monotonic() - one_started,
                )
        rows.append(row)
        emit({"event": "CASE_FINISHED", "case": row, "case_count": len(cases)})
        partial = _report(
            output_dir=output_dir,
            rows=rows,
            cases=cases,
            manifest=manifest,
            started_at=started_at,
            started_monotonic=started_monotonic,
            cancelled=False,
            require_clean=require_clean,
            in_progress=True,
        )
        atomic_write_json(report_path, partial)

    if cancelled is not None and cancelled():
        was_cancelled = True
    report = _report(
        output_dir=output_dir,
        rows=rows,
        cases=cases,
        manifest=manifest,
        started_at=started_at,
        started_monotonic=started_monotonic,
        cancelled=was_cancelled,
        require_clean=require_clean,
        in_progress=False,
    )
    report["report_path"] = str(report_path)
    report["markdown_report_path"] = str(markdown_path)
    atomic_write_json(report_path, report)
    _write_markdown(report, markdown_path)
    emit({"event": "SUITE_FINISHED", "report": report})
    return report
