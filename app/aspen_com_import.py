from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


FROZEN_ROOT = getattr(sys, "_MEIPASS", None)
if FROZEN_ROOT:
    PACKAGE_ROOT = Path(FROZEN_ROOT).resolve()
    APP_DIR = PACKAGE_ROOT / "app"
    WORKSPACE_ROOT = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "EquipmentDesignGraphApp"
else:
    APP_DIR = Path(__file__).resolve().parent
    PACKAGE_ROOT = APP_DIR.parent
    WORKSPACE_ROOT = PACKAGE_ROOT.parent
SCRIPTS_DIR = PACKAGE_ROOT / "scripts"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import aspen_equipment_derivation as derivation  # noqa: E402
import aspen_pfd  # noqa: E402


CONTROL_MESSAGES: list[str] = []
PROBLEM_PATTERNS = (
    r"(?im)^\s*\*\s*WARNING\b",
    r"(?im)^\s*WARNING IN THE\b",
    r"(?im)^\s*WARNING WHILE\b",
    r"(?im)^\s*SEVERE ERROR\b",
    r"(?im)^\s*ERROR IN THE\b",
    r"(?im)^\s*ERROR WHILE EXECUTING\b",
    r"(?im)^\s*TERMINAL ERROR\b(?!S\s+\d)",
    r"(?im)^.*CHECK THE RUN STATUS.*$",
)

# Aspen V14 HAPCompStatusCode values from the installed COM type library.
# A node may still expose numeric zero-valued Output children when its owning
# stream/block has no results.  Those are tree placeholders, not process data.
HAP_RESULTS_SUCCESS = 1
HAP_NORESULTS = 2
HAP_NOT_RUN = 2097152


ASPEN_EXTRACTION_REGISTRY_REVISION = "2026-08-20.1"


def _field_spec(
    *paths: str,
    value_kind: str = "continuous",
    requirement: str = "when_available",
    description: str,
) -> dict[str, Any]:
    """Build one immutable-by-convention COM extraction registry entry."""

    return {
        "paths": list(paths),
        "value_kind": value_kind,
        "requirement": requirement,
        "description": description,
    }


STREAM_FIELD_REGISTRY: dict[str, dict[str, Any]] = {
    "TEMP_OUT": _field_spec(
        r"Output\TEMP_OUT\MIXED",
        r"Output\TEMP_OUT",
        description="mixed-stream temperature",
    ),
    "PRES_OUT": _field_spec(
        r"Output\PRES_OUT\MIXED",
        r"Output\PRES_OUT",
        description="mixed-stream pressure",
    ),
    "MASSFLMX": _field_spec(
        r"Output\MASSFLMX\MIXED",
        r"Output\MASSFLMX",
        description="mixed-stream mass flow",
    ),
    "VOLFLMX": _field_spec(
        r"Output\VOLFLMX\MIXED",
        r"Output\VOLFLMX",
        description="mixed-stream volumetric flow",
    ),
    # Aspen V14 exposes transport-property results below STRM_UPP and keeps
    # phase values as separate real-valued leaves.  The MUMX and MIXED parent
    # nodes are integer tree-card placeholders and must not be used as the
    # physical viscosity.
    "MUMX_LIQUID": _field_spec(
        r"Output\STRM_UPP\MUMX\MIXED\LIQUID",
        description="liquid-phase dynamic viscosity",
    ),
    "MUMX_VAPOR": _field_spec(
        r"Output\STRM_UPP\MUMX\MIXED\VAPOR",
        description="vapor-phase dynamic viscosity",
    ),
    "VFRAC_OUT": _field_spec(
        r"Output\VFRAC_OUT\MIXED",
        r"Output\VFRAC_OUT",
        description="mixed-stream vapor fraction",
    ),
    "SFRAC_OUT": _field_spec(
        r"Output\SFRAC_OUT\MIXED",
        r"Output\SFRAC_OUT",
        r"Output\SOLIDFRAC_OUT",
        description="mixed-stream solid fraction",
    ),
    "SOLID_MASSFLMX": _field_spec(
        r"Output\MASSFLMX\CISOLID",
        r"Output\MASSFLMX\SOLID",
        description="solid-substream mass flow",
    ),
    "molecular_weight": _field_spec(
        r"Output\MW_OUT\MIXED",
        r"Output\MW\MIXED",
        r"Output\MW",
        description="mixed-stream molecular weight",
    ),
    "density_kg_m3": _field_spec(
        r"Output\RHOMX_MASS\MIXED",
        r"Output\RHO_MASS\MIXED",
        description="mixed-stream mass density",
    ),
    "compressibility_factor": _field_spec(
        r"Output\ZMX\MIXED",
        r"Output\Z_FACTOR\MIXED",
        description="mixed-stream compressibility factor",
    ),
}


BLOCK_FIELD_REGISTRY: dict[str, dict[str, Any]] = {
    "BLKSTAT": _field_spec(
        r"Output\BLKSTAT",
        value_kind="scalar",
        description="Aspen block calculation status card",
    ),
    "QCALC": _field_spec(
        r"Output\QCALC",
        r"Output\B_QCALC",
        description="block heat duty",
    ),
    # Keep Aspen's generic net-work card separate from the three PUMP power
    # channels.  In particular, a PUMP WNET observation can be the electrical
    # utility demand; it must never inherit BRAKE_POWER and be relabelled as
    # shaft power.
    "WNET": _field_spec(
        r"Output\WNET",
        r"Output\B_WNET",
        description="Aspen net work or electrical utility rate; not pump shaft power",
    ),
    "FLUID_POWER": _field_spec(
        r"Output\FLUID_POWER",
        description="pump hydraulic fluid power",
    ),
    "BRAKE_POWER": _field_spec(
        r"Output\BRAKE_POWER",
        description="pump brake or shaft power",
    ),
    "ELEC_POWER": _field_spec(
        r"Output\ELEC_POWER",
        description="pump electrical input power",
    ),
    "DEFF": _field_spec(
        r"Input\DEFF",
        description="pump driver efficiency",
    ),
    "AREA": _field_spec(
        r"Output\AREA",
        r"Output\HX_AREAP",
        description="reported heat-transfer or filtration area",
    ),
    "HEAD_CAL": _field_spec(
        r"Output\HEAD_CAL",
        r"Output\HEAD",
        description="calculated pump head",
    ),
    "NPSHA": _field_spec(
        r"Output\NPSHA",
        r"Output\NPSH_AVAIL",
        r"Output\NPSH-A",
        r"Output\NPSHAVAIL",
        description="available suction head or pressure margin; unit defines quantity kind",
    ),
    "CEFF": _field_spec(
        r"Output\CEFF",
        r"Input\CEFF",
        r"Input\SEFF",
        description="compressor, pump, or rotating-equipment efficiency",
    ),
    "DELP_CAL": _field_spec(
        r"Output\DELP_CAL",
        r"Output\PDRP",
        description="calculated pressure change or drop",
    ),
    "PRES_RATIO": _field_spec(
        r"Output\PRES_RATIO",
        r"Output\PRATIO",
        description="pressure ratio",
    ),
    "NSTAGE": _field_spec(
        r"Input\NSTAGE",
        r"Output\NSTAGE",
        value_kind="integer",
        description="declared or calculated stage count",
    ),
    "VOLUME": _field_spec(
        r"Input\VOLUME",
        r"Output\VOLUME",
        description="declared or calculated active volume",
    ),
    "DIAMETER": _field_spec(
        r"Input\DIAMETER",
        r"Output\DIAMETER",
        description="declared or calculated characteristic diameter",
    ),
    "HEIGHT": _field_spec(
        r"Input\HEIGHT",
        r"Output\HEIGHT",
        description="declared or calculated characteristic height",
    ),
}


def _module_spec(process_function: str, *fields: str) -> dict[str, Any]:
    return {
        "process_function": process_function,
        "fields": list(fields),
        "equipment_mapping_status": "REGISTERED_PROCESS_MODULE",
    }


BLOCK_MODULE_REGISTRY: dict[str, dict[str, Any]] = {
    "PUMP": _module_spec(
        "liquid pressure boosting",
        "WNET", "FLUID_POWER", "BRAKE_POWER", "ELEC_POWER", "DEFF",
        "HEAD_CAL", "NPSHA", "CEFF", "DELP_CAL", "PRES_RATIO",
    ),
    "COMPR": _module_spec(
        "gas compression", "WNET", "CEFF", "DELP_CAL", "PRES_RATIO",
    ),
    "MCOMPR": _module_spec(
        "multistage gas compression",
        "WNET", "CEFF", "DELP_CAL", "PRES_RATIO", "NSTAGE",
    ),
    "MIXER": _module_spec("stream mixing", "DELP_CAL"),
    "HEATER": _module_spec("process heating or cooling", "QCALC", "DELP_CAL"),
    "HEATX": _module_spec("two-stream heat exchange", "QCALC", "AREA", "DELP_CAL"),
    "RADFRAC": _module_spec(
        "rigorous staged separation", "QCALC", "NSTAGE", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "RATEFRAC": _module_spec(
        "rate-based staged separation", "QCALC", "NSTAGE", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "ABSBR": _module_spec(
        "staged absorption or stripping", "NSTAGE", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "EXTRACT": _module_spec(
        "staged liquid-liquid extraction", "NSTAGE", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "DSTWU": _module_spec(
        "shortcut distillation", "QCALC", "NSTAGE", "DIAMETER", "HEIGHT",
    ),
    "FLASH2": _module_spec(
        "vapor-liquid flash separation", "QCALC", "VOLUME", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "FLASH3": _module_spec(
        "three-phase flash separation", "QCALC", "VOLUME", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "DECANTER": _module_spec(
        "liquid-liquid separation", "VOLUME", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "SEP": _module_spec(
        "general separation; construction subtype requires confirmation",
        "QCALC", "VOLUME", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "SEP2": _module_spec(
        "general separation; construction subtype requires confirmation",
        "QCALC", "VOLUME", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "BATCHSEP": _module_spec(
        "batch separation; construction subtype requires confirmation",
        "QCALC", "VOLUME", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "CRYSTALLIZER": _module_spec(
        "solid crystallization; operating mode and heat-removal route require confirmation",
        "QCALC", "AREA", "VOLUME", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "FILTER": _module_spec(
        "solid-liquid filtration; area and cycle require confirmation",
        "AREA", "VOLUME", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "DRYER": _module_spec(
        "solids drying; evaporation duty and drying route require confirmation",
        "QCALC", "AREA", "VOLUME", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "RPLUG": _module_spec(
        "plug-flow reaction", "QCALC", "VOLUME", "DIAMETER", "DELP_CAL",
    ),
    "RCSTR": _module_spec(
        "continuous stirred-tank reaction", "QCALC", "VOLUME", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "RSTOIC": _module_spec(
        "specified-conversion reaction", "QCALC", "VOLUME", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "RYIELD": _module_spec(
        "specified-yield reaction", "QCALC", "VOLUME", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "RGIBBS": _module_spec(
        "equilibrium reaction", "QCALC", "VOLUME", "DIAMETER", "HEIGHT", "DELP_CAL",
    ),
    "VALVE": _module_spec("pressure reduction", "DELP_CAL", "PRES_RATIO"),
    "PIPE": _module_spec(
        "process fluid transport through Aspen PIPE hydraulic block",
        "DELP_CAL", "DIAMETER", "HEIGHT",
    ),
}


# Compatibility views retained for callers/tests that imported the historical
# path-only dictionaries.  Extraction itself consumes the richer registries.
STREAM_FIELDS: dict[str, list[str]] = {
    field: list(spec["paths"])
    for field, spec in STREAM_FIELD_REGISTRY.items()
}
BLOCK_COMMON_FIELDS = ("BLKSTAT",)
BLOCK_FIELDS: dict[str, list[str]] = {
    field: list(spec["paths"])
    for field, spec in BLOCK_FIELD_REGISTRY.items()
    if field not in BLOCK_COMMON_FIELDS
}
PROCESS_FUNCTIONS = {
    module: str(spec["process_function"])
    for module, spec in BLOCK_MODULE_REGISTRY.items()
}


# Composition is a dynamic vector, so its component leaves cannot be enumerated
# as fixed field IDs.  These prefixes are still registered and therefore do not
# appear as false-positive unmapped COM leaves.
STREAM_DYNAMIC_MAPPED_PREFIXES = (
    r"Output\MOLEFRAC\MIXED",
    r"Output\MASSFRAC\MIXED",
)
TREE_DISCOVERY_MAX_NODES_PER_OBJECT = 5000
TREE_DISCOVERY_MAX_DEPTH = 24
ROOT_COLLECTION_MAX_OBJECTS = 10000
TREE_DISCOVERY_MAX_NODES_PER_CASE = 25000
TREE_DISCOVERY_MAX_METADATA_BYTES_PER_CASE = 16 * 1024 * 1024
TREE_DISCOVERY_HASH_CHUNK_CHARS = 4096
TREE_DISCOVERY_MAX_NAME_CHARS = 256
TREE_DISCOVERY_MAX_PATH_CHARS = 4096
TREE_DISCOVERY_MAX_UNIT_CHARS = 128
TREE_DISCOVERY_MAX_TYPE_CHARS = 128

WORKER_SELECTION_ALLOWED_STATUSES = frozenset({
    "PASS",
    # A transport-property gap may retain a preliminary result for review; it
    # is never formal Aspen evidence. COM/registry coverage gaps are different
    # and are always blocked below.
    "BLOCKED_TRANSPORT_PROPERTY_VERIFICATION",
})


# Aspen leaves UnitString empty for a narrow set of named, dimensionless (or
# definition-fixed) cards. These defaults come from the field definition, not
# from the numeric magnitude. Every other missing unit remains explicit.
SEMANTIC_UNIT_DEFAULTS = {
    "BLKSTAT": "-",
    "VFRAC_OUT": "-",
    "SFRAC_OUT": "-",
    "molecular_weight": "kg/kmol",
    "compressibility_factor": "-",
    "CEFF": "fraction",
    "DEFF": "fraction",
    "PRES_RATIO": "-",
    "NSTAGE": "-",
}


RAW_FIELD_TO_IN_UNITS_KEY = {
    "TEMP_OUT": "TEMPERATURE",
    "PRES_OUT": "PRESSURE",
    "MASSFLMX": "MASS-FLOW",
    "SOLID_MASSFLMX": "MASS-FLOW",
    "VOLFLMX": "VOLUME-FLOW",
    "VOLFLMX_LIQ": "VOLUME-FLOW",
    "VOLFLMX_GAS": "VOLUME-FLOW",
    "density_kg_m3": "MASS-DENSITY",
    "MUMX": "VISCOSITY",
    "MUMX_LIQUID": "VISCOSITY",
    "MUMX_VAPOR": "VISCOSITY",
    "molecular_weight": "MOLE-WEIGHT",
    "QCALC": "ENTHALPY-FLO",
    "QNET": "ENTHALPY-FLO",
    "DUTY_OUT": "ENTHALPY-FLO",
    "WNET": "POWER",
    "FLUID_POWER": "POWER",
    "BRAKE_POWER": "POWER",
    "ELEC_POWER": "POWER",
    "HEAD_CAL": "HEAD",
    "DELP_CAL": "PDROP",
    "PDRP": "PDROP",
    "AREA": "AREA",
    "VOLUME": "VOLUME",
    "DIAMETER": "SHORT-LENGTH",
    "HEIGHT": "SHORT-LENGTH",
}


PUMP_POWER_QUANTITY_KINDS = {
    "FLUID_POWER": "pump_hydraulic_fluid_power",
    "BRAKE_POWER": "pump_brake_shaft_power",
    "ELEC_POWER": "pump_electrical_input_power",
    "DEFF": "pump_driver_efficiency_fraction",
}


def block_field_quantity_kind(field: str, block_type: str) -> str | None:
    """Return an explicit semantic label for ambiguous Aspen block cards."""

    model = str(block_type or "").strip().upper()
    if field == "WNET":
        if model == "PUMP":
            return "pump_electrical_utility_power_not_shaft_power"
        return "aspen_net_work_rate_not_assumed_shaft_power"
    if model == "PUMP":
        return PUMP_POWER_QUANTITY_KINDS.get(field)
    return None


def parse_in_units_cards(text: str) -> list[dict[str, Any]]:
    """Parse the IN-UNITS cards Aspen writes when a BKP is exported to INP."""

    lines = text.splitlines()
    cards: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        match = re.match(r"^(?P<indent>\s*)IN-UNITS\b", lines[index], re.IGNORECASE)
        if match is None:
            index += 1
            continue
        start = index
        parts: list[str] = []
        while index < len(lines):
            part = lines[index].strip()
            continued = part.endswith("&")
            if continued:
                part = part[:-1].rstrip()
            parts.append(part)
            index += 1
            if not continued:
                break
        raw_card = " ".join(part for part in parts if part)
        tokens = shlex.split(raw_card, posix=True)
        if len(tokens) < 2 or tokens[0].upper() != "IN-UNITS":
            continue
        fields: dict[str, str] = {}
        conflicts: list[dict[str, str]] = []
        for token in tokens[2:]:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            key = key.strip().upper()
            value = value.strip()
            if not key or not value:
                continue
            if key in fields and fields[key] != value:
                conflicts.append({"field": key, "first": fields[key], "second": value})
                continue
            fields[key] = value
        indent = len(match.group("indent").replace("\t", "    "))
        cards.append({
            "line_number": start + 1,
            "scope": "GLOBAL" if indent == 0 else "LOCAL_CARD",
            "indent": indent,
            "unit_set": tokens[1],
            "fields": fields,
            "conflicts": conflicts,
            "raw_card": raw_card,
        })
    return cards


def global_in_units(cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    globals_found = [card for card in cards if card.get("scope") == "GLOBAL"]
    if not globals_found:
        return None
    return globals_found[0]


def resolve_aspen_unit(
    field: str,
    unit: str,
    in_units_fields: dict[str, str] | None = None,
) -> tuple[str | None, bool]:
    declared = str(unit or "").strip()
    if declared:
        return declared, False
    unit_set_key = RAW_FIELD_TO_IN_UNITS_KEY.get(field)
    if unit_set_key and isinstance(in_units_fields, dict):
        from_unit_set = str(in_units_fields.get(unit_set_key) or "").strip()
        if from_unit_set:
            return from_unit_set, True
    semantic = SEMANTIC_UNIT_DEFAULTS.get(field)
    return (semantic, True) if semantic else (None, False)


def project_stream_phase_observation(
    row: dict[str, Any],
    *,
    source_field: str,
    target_field: str,
    phase: str,
) -> bool:
    """Project one Aspen stream result without breaking its raw evidence path."""

    if source_field not in row:
        return False
    row[target_field] = row[source_field]
    raw_paths = row.setdefault("aspen_raw_paths", {})
    raw_values = row.setdefault("aspen_raw_values", {})
    source_path = raw_paths.get(source_field)
    source_record = raw_values.get(source_field)
    if source_path:
        raw_paths[target_field] = source_path
    if isinstance(source_record, dict):
        raw_values[target_field] = {
            **source_record,
            "phase_projection": phase,
            "projection_source_field": source_field,
            "phase_source_field": row.get("phase_source_field"),
        }
    return True


def stage_aspen_sidecars(source: Path, staged: Path) -> list[dict[str, Any]]:
    """Copy bounded local Aspen sidecars into the isolated work directory."""
    copied: list[dict[str, Any]] = []
    for suffix in (".def", ".ads", ".appdf"):
        sidecar = source.with_suffix(suffix)
        if not sidecar.is_file():
            continue
        destination = staged.with_suffix(suffix)
        shutil.copy2(sidecar, destination)
        copied.append({
            "kind": suffix.lstrip(".").upper(),
            "source": str(sidecar),
            "staged": str(destination),
            "sha256": sha256(sidecar),
            "staged_sha256": sha256(destination),
        })
    # EDR links commonly use equipment-tag filenames, not the BKP stem. Copy
    # only same-directory EDR files; never recurse or follow arbitrary paths.
    for sidecar in sorted(
        (item for item in source.parent.iterdir() if item.is_file() and item.suffix.casefold() == ".edr"),
        key=lambda item: item.name.casefold(),
    ):
        destination = staged.parent / sidecar.name
        shutil.copy2(sidecar, destination)
        copied.append({
            "kind": "EDR",
            "source": str(sidecar),
            "staged": str(destination),
            "sha256": sha256(sidecar),
            "staged_sha256": sha256(destination),
        })
    return copied


class AspenEvents:
    def OnControlPanelMessage(self, *args: Any) -> None:
        for arg in args:
            if isinstance(arg, str) and arg.strip():
                CONTROL_MESSAGES.append(arg.rstrip())


class AspenLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "AspenLock":
        try:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(f"Aspen COM 正由其他任务占用：{self.path}") from exc
        os.write(self.fd, f"pid={os.getpid()}\nstarted={time.strftime('%Y-%m-%dT%H:%M:%S')}\n".encode("ascii"))
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def sha256_text_latin1(text: str) -> str:
    return hashlib.sha256(text.encode("latin-1")).hexdigest().upper()


def inp_card_spans(text: str, keyword: str) -> list[tuple[int, int]]:
    """Return byte-stable text spans for Aspen top-level input cards."""

    wanted = keyword.upper()
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    starts = [
        index
        for index, line in enumerate(lines)
        if re.match(rf"^{re.escape(wanted)}(?:\s|$)", line, flags=re.IGNORECASE)
    ]
    spans: list[tuple[int, int]] = []
    for start_index in starts:
        end_index = len(lines)
        for index in range(start_index + 1, len(lines)):
            line = lines[index]
            if re.match(r"^[A-Z][A-Z0-9-]*(?:\s|$)", line, flags=re.IGNORECASE):
                end_index = index
                break
        start_offset = offsets[start_index]
        end_offset = offsets[end_index] if end_index < len(lines) else len(text)
        spans.append((start_offset, end_offset))
    return spans


def _unused_transport_prop_set_name(text: str) -> str:
    names = {
        match.group(1).upper()
        for match in re.finditer(
            r"(?im)^PROP-SET\s+([A-Z0-9_-]+)\b",
            text,
        )
    }
    for candidate in ("TXPORT", "EDGTXPRT", "EDGMU001", "EDGMU002"):
        if candidate not in names:
            return candidate
    raise RuntimeError("BLOCKED_NO_AVAILABLE_TRANSPORT_PROPERTY_SET_NAME")


def augment_transport_property_inp(text: str) -> tuple[str, dict[str, Any]]:
    """Ensure an isolated Aspen INP requests stream dynamic viscosity.

    The operation is intentionally text-only and idempotent.  The caller must
    reload the returned INP through Aspen COM and rerun the staged case; this
    function never writes to or mutates the source BKP.
    """

    before_sha = sha256_text_latin1(text)
    property_set_name: str | None = None
    property_set_created = False
    report_updated = False

    for start, end in inp_card_spans(text, "PROP-SET"):
        card = text[start:end]
        if re.search(r"(?i)\bMUMX\b", card):
            match = re.match(r"(?i)PROP-SET\s+([A-Z0-9_-]+)\b", card)
            if match:
                property_set_name = match.group(1)
                break

    if property_set_name is None:
        property_set_name = _unused_transport_prop_set_name(text)
        card = (
            f"PROP-SET {property_set_name} MUMX SUBSTREAM=MIXED PHASE=V L\n"
            ";  Added in an isolated copy by EquipmentDesignApp for viscosity extraction\n"
            "\n"
        )
        insertion_matches = list(
            re.finditer(r"(?im)^(?:STREAM|BLOCK)\s+", text)
        )
        insertion = insertion_matches[0].start() if insertion_matches else len(text)
        text = text[:insertion] + card + text[insertion:]
        property_set_created = True

    report_spans = inp_card_spans(text, "STREAM-REPOR")
    if report_spans:
        start, end = report_spans[0]
        card = text[start:end]
        if not re.search(
            rf"(?i)\bPROPERTIES\s*=[^\r\n]*(?:\b|/){re.escape(property_set_name)}\b",
            card,
        ):
            if re.search(r"(?i)\bPROPERTIES\s*=", card):
                card = re.sub(
                    r"(?i)(\bPROPERTIES\s*=\s*[A-Z0-9_-]+)",
                    rf"\1 / {property_set_name}",
                    card,
                    count=1,
                )
            else:
                line_end_match = re.search(r"\r?\n", card)
                line_end = line_end_match.start() if line_end_match else len(card)
                card = (
                    card[:line_end].rstrip()
                    + f" PROPERTIES={property_set_name}"
                    + card[line_end:]
                )
            text = text[:start] + card + text[end:]
            report_updated = True
    else:
        suffix = f"\nSTREAM-REPOR PROPERTIES={property_set_name}\n"
        text = text.rstrip() + suffix
        report_updated = True

    after_sha = sha256_text_latin1(text)
    return text, {
        "schema": "aspen-transport-property-augmentation-v1",
        "status": "CHANGED" if before_sha != after_sha else "ALREADY_PRESENT",
        "property_set_name": property_set_name,
        "requested_property": "MUMX",
        "property_set_created": property_set_created,
        "stream_report_updated": report_updated,
        "before_inp_sha256": before_sha,
        "after_inp_sha256": after_sha,
        "com_reload_required": before_sha != after_sha,
        "mutation_scope": "ISOLATED_INP_COPY_ONLY",
        "source_bkp_mutated": False,
    }


def prepare_transport_property_augmentation(
    app: Any,
    work: Path,
    out_dir: Path,
    source: Path,
) -> tuple[Path | None, dict[str, Any], Path]:
    """Export, augment and audit an isolated Aspen input deck."""

    before = work / "TRANSPORT_BEFORE.INP"
    after = work / "TRANSPORT_AUGMENTED.INP"
    app.Export(4, str(before.resolve()))
    if not before.is_file() or before.stat().st_size <= 0:
        raise RuntimeError("BLOCKED_ASPEN_INP_EXPORT_FOR_TRANSPORT_AUGMENTATION")
    original_text = before.read_text(encoding="latin-1")
    augmented_text, manifest = augment_transport_property_inp(original_text)
    manifest.update({
        "source_case_path": str(source),
        "source_case_sha256": sha256(source),
        "original_source_read_only": True,
        "com_export_method": "Document.Export(4, isolated_path)",
        "com_reload_method": "Document.InitFromFile2(isolated_augmented_inp)",
        "rerun_method": "Engine.Run2(False)",
    })
    before_copy = out_dir / "transport_property_before.inp"
    shutil.copy2(before, before_copy)
    manifest["before_artifact"] = {
        "path": before_copy.name,
        "sha256": sha256(before_copy),
    }
    if manifest["status"] == "CHANGED":
        after.write_text(augmented_text, encoding="latin-1")
        after_copy = out_dir / "transport_property_augmented.inp"
        shutil.copy2(after, after_copy)
        manifest["after_artifact"] = {
            "path": after_copy.name,
            "sha256": sha256(after_copy),
        }
        reload_path: Path | None = after
    else:
        reload_path = None
    manifest_path = out_dir / "transport_property_augmentation_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return reload_path, manifest, manifest_path


def verify_stream_transport_properties(bundle: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    unverifiable_phase: list[dict[str, Any]] = []
    for stream in bundle.get("streams", []):
        if str(stream.get("stream_record_type", "")).upper() != "MATERIAL":
            continue
        stream_id = str(stream.get("stream_id", ""))
        phase = str(stream.get("phase", "")).casefold()
        if phase in {"liquid", "solid_liquid"}:
            required = ["MUMX"]
        elif phase == "vapor":
            required = ["MUMX"]
        elif phase == "two_phase":
            required = ["MUMX_LIQUID", "MUMX_VAPOR"]
        elif phase in {"solid", "solid_bearing"}:
            required = []
        else:
            required = ["PHASE", "MUMX"]
        absent = [
            field
            for field in required
            if (
                field == "PHASE"
                or (
                    field == "MUMX"
                    and not any(
                        isinstance(stream.get(candidate), (int, float))
                        for candidate in (
                            "MUMX",
                            "MUMX_LIQUID",
                            "MUMX_VAPOR",
                        )
                    )
                )
                or (
                    field not in {"PHASE", "MUMX"}
                    and not isinstance(stream.get(field), (int, float))
                )
            )
        ]
        row = {
            "stream_id": stream_id,
            "phase": phase or None,
            "required_fields": required,
            "missing_fields": absent,
            "selected_mumx_path": (stream.get("aspen_raw_paths") or {}).get("MUMX"),
            "liquid_mumx_path": (stream.get("aspen_raw_paths") or {}).get("MUMX_LIQUID"),
            "vapor_mumx_path": (stream.get("aspen_raw_paths") or {}).get("MUMX_VAPOR"),
            "verification_state": (
                "NOT_APPLICABLE_SOLID_STREAM"
                if phase in {"solid", "solid_bearing"}
                else (
                    "BLOCKED_UNVERIFIABLE_PHASE"
                    if "PHASE" in absent
                    else (
                        "BLOCKED_MISSING_ASPEN_VISCOSITY"
                        if absent
                        else "VERIFIED"
                    )
                )
            ),
        }
        rows.append(row)
        if absent:
            missing.append(row)
        if "PHASE" in absent:
            unverifiable_phase.append(row)
    if not rows:
        status = "BLOCKED_NO_MATERIAL_STREAMS_FOR_TRANSPORT_VERIFICATION"
    elif unverifiable_phase:
        status = "BLOCKED_UNVERIFIABLE_ASPEN_PHASE_AND_VISCOSITY"
    elif missing:
        status = "BLOCKED_MISSING_ASPEN_VISCOSITY"
    else:
        status = "PASS"
    return {
        "schema": "aspen-stream-transport-verification-v1",
        "status": status,
        "material_stream_count": len(rows),
        "verified_stream_count": sum(
            1 for row in rows if row["verification_state"] == "VERIFIED"
        ),
        "not_applicable_solid_stream_count": sum(
            1
            for row in rows
            if row["verification_state"] == "NOT_APPLICABLE_SOLID_STREAM"
        ),
        "unverifiable_phase_stream_count": len(unverifiable_phase),
        "missing_stream_count": len(missing),
        "missing": missing,
        "rows": rows,
    }


def classify_aspen_worker_status(
    transport_verification: dict[str, Any],
    extraction_blockers: list[dict[str, Any]] | None,
    extraction_coverage: dict[str, Any] | None = None,
) -> str:
    """Give COM extraction blockers precedence over transport verification."""

    coverage = extraction_coverage or {}
    coverage_status = str(
        coverage.get("registry_completeness_status") or ""
    ).strip().upper()
    coverage_blocked = bool(
        coverage.get("root_diagnostics")
        or coverage.get("tree_discovery_truncated_object_count")
        or (coverage.get("case_discovery_budget") or {}).get("node_budget_exhausted")
        or (coverage.get("case_discovery_budget") or {}).get("metadata_budget_exhausted")
        or coverage_status.startswith("NOT_SCORABLE_")
    )
    if extraction_blockers or coverage_blocked:
        return "BLOCKED_COM_EXTRACTION"
    if transport_verification.get("status") != "PASS":
        return "BLOCKED_TRANSPORT_PROPERTY_VERIFICATION"
    return "PASS"


def worker_selection_result_available(
    worker_status: str,
    result: Any,
) -> bool:
    """Return whether a worker status may expose a preliminary selection."""

    return bool(
        str(worker_status or "").strip().upper()
        in WORKER_SELECTION_ALLOWED_STATUSES
        and isinstance(result, dict)
    )


def persist_stream_transport_evidence(
    out_dir: Path,
    source: Path,
    verification: dict[str, Any],
    *,
    augmentation_requested: bool,
    run_result: dict[str, Any] | None,
    manifest: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Path, Path]:
    """Persist verification even when Aspen already exposes all required MUMX."""

    verification_path = out_dir / "stream_transport_verification.json"
    verification_path.write_text(
        json.dumps(verification, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if manifest is None:
        manifest = {
            "schema": "aspen-transport-property-augmentation-v2",
            "status": (
                "NO_CHANGE_ALREADY_AVAILABLE"
                if verification.get("status") == "PASS"
                else "NO_AUGMENTATION_REQUESTED_VERIFICATION_BLOCKED"
            ),
            "method": "READ_ONLY_COM_TREE_EXTRACTION_AND_VERIFICATION",
            "requested_property": "MUMX",
            "property_set_created": False,
            "stream_report_updated": False,
            "operations": [],
            "augmentation_requested": augmentation_requested,
            "claim_boundary": (
                "The extracted Aspen stream results already contained the required "
                "phase-specific MUMX observations; no report-configuration claim is made."
                if verification.get("status") == "PASS"
                else "Transport-property completeness was not established; no "
                "already-available or successful-augmentation claim is made."
            ),
            "source_case_path": str(source),
            "source_case_sha256": sha256(source),
            "original_source_read_only": True,
            "source_bkp_mutated": False,
            "mutation_scope": "NONE",
        }
    else:
        manifest = dict(manifest)
        manifest["augmentation_requested"] = augmentation_requested
        if (
            manifest.get("status") == "ALREADY_PRESENT"
            and verification.get("status") == "PASS"
        ):
            manifest["configuration_status"] = "ALREADY_PRESENT"
            manifest["status"] = "NO_CHANGE_ALREADY_AVAILABLE"
    manifest["rerun_completed"] = bool(
        isinstance(run_result, dict) and run_result.get("status") == "returned"
    )
    manifest["verification"] = verification
    manifest["verification_artifact"] = {
        "path": verification_path.name,
        "sha256": sha256(verification_path),
    }
    manifest_path = out_dir / "transport_property_augmentation_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return manifest, manifest_path, verification_path


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def strip_unit(unit: Any) -> str:
    text = str(unit or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    replacements = {"C": "C", "degC": "C", "kmol/hr": "kmol/h", "kg/hr": "kg/h"}
    return replacements.get(text, text)


def finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


# This is only a floating-point endpoint tolerance.  It is deliberately many
# orders of magnitude smaller than an engineering phase split and must never be
# interpreted as a permissible vapor/liquid carry-over threshold.
VAPOR_FRACTION_ENDPOINT_TOLERANCE = 1.0e-9


def phase_from_vapor_fraction(value: Any) -> dict[str, Any]:
    """Classify only numerical 0/1 endpoints as pure phases.

    Every material value strictly between the endpoints remains mixed.  Values
    outside [0, 1] beyond the stated numerical tolerance are invalid rather
    than silently clamped.
    """
    vapor_fraction = finite(value)
    result: dict[str, Any] = {
        "source_field": "VFRAC_OUT",
        "raw_vapor_fraction": vapor_fraction,
        "policy": "numeric_endpoint_tolerance_only; every material 0<VFRAC_OUT<1 is two_phase",
        "endpoint_tolerance": VAPOR_FRACTION_ENDPOINT_TOLERANCE,
        "evidence_class": "D",
    }
    if vapor_fraction is None:
        return {**result, "status": "UNAVAILABLE", "phase": None}
    tolerance = VAPOR_FRACTION_ENDPOINT_TOLERANCE
    if vapor_fraction < -tolerance or vapor_fraction > 1.0 + tolerance:
        return {**result, "status": "INVALID_OUT_OF_RANGE", "phase": None}
    if vapor_fraction <= tolerance:
        return {**result, "status": "PURE_ENDPOINT", "phase": "liquid"}
    if vapor_fraction >= 1.0 - tolerance:
        return {**result, "status": "PURE_ENDPOINT", "phase": "vapor"}
    return {**result, "status": "MIXED", "phase": "two_phase"}


def node_elements(node: Any, max_count: int | None = None) -> list[Any]:
    if node is None:
        return []
    try:
        elements = node.Elements
    except Exception:
        return []
    try:
        count = int(elements.Count)
    except Exception:
        count = None

    def collect(start: int) -> list[Any]:
        rows: list[Any] = []
        limit = count if count is not None and count >= 0 else 10000
        if max_count is not None:
            limit = min(limit, max(0, int(max_count)))
        for offset in range(limit):
            index = start + offset
            try:
                child = elements(index)
            except Exception:
                try:
                    child = elements.Item(index)
                except Exception:
                    break
            if child is None:
                break
            rows.append(child)
        return rows

    zero_based = collect(0)
    one_based = collect(1)
    return one_based if len(one_based) > len(zero_based) else zero_based


def strict_node_elements(
    node: Any,
    max_count: int,
) -> dict[str, Any]:
    """Enumerate a COM collection while retaining Count/Item failures."""

    result: dict[str, Any] = {
        "rows": [],
        "errors": [],
        "index_basis": None,
        "declared_count": None,
        "truncated": False,
    }
    if node is None:
        return result
    try:
        elements = node.Elements
    except Exception as exc:
        result["errors"].append({
            "operation": "IHNode.Elements",
            "error": _safe_error_text(exc),
        })
        return result
    try:
        count = int(elements.Count)
        if count < 0:
            raise ValueError(f"negative Elements.Count={count}")
        result["declared_count"] = count
    except Exception as exc:
        count = None
        result["errors"].append({
            "operation": "IHNode.Elements.Count",
            "error": _safe_error_text(exc),
        })

    def get_item(index: int) -> tuple[Any | None, dict[str, Any] | None]:
        call_error: BaseException | None = None
        try:
            return elements(index), None
        except Exception as exc:
            call_error = exc
        try:
            return elements.Item(index), None
        except Exception as item_exc:
            return None, {
                "operation": "IHNode.Elements.Item",
                "index": index,
                "error": _safe_error_text(item_exc),
                "call_fallback_error": _safe_error_text(call_error),
            }

    if count == 0:
        result["index_basis"] = "empty"
        return result

    zero, zero_error = get_item(0)
    if zero_error is None and zero is not None:
        start = 0
        first = zero
    else:
        one, one_error = get_item(1)
        if one_error is None and one is not None:
            start = 1
            first = one
        else:
            result["errors"].append({
                "operation": "IHNode.Elements.Item.basis_probe",
                "zero_based_error": zero_error,
                "one_based_error": one_error,
            })
            return result
    result["index_basis"] = "zero_based" if start == 0 else "one_based"
    limit = max(0, int(max_count))
    expected = min(count, limit) if count is not None else limit
    if count is not None and count > limit:
        result["truncated"] = True
    if expected <= 0:
        result["truncated"] = bool(count)
        return result
    result["rows"].append(first)
    for offset in range(1, expected):
        index = start + offset
        child, error = get_item(index)
        if error is not None or child is None:
            result["errors"].append(
                error
                or {
                    "operation": "IHNode.Elements.Item",
                    "index": index,
                    "error": "NULL_CHILD_WITHIN_DECLARED_COUNT",
                }
            )
            break
        result["rows"].append(child)
    if count is not None and len(result["rows"]) != expected:
        result["errors"].append({
            "operation": "IHNode.Elements.enumeration",
            "error": "DECLARED_COUNT_NOT_ENUMERATED",
            "declared_count": count,
            "expected_with_limit": expected,
            "enumerated_count": len(result["rows"]),
        })
    return result


def node_name(node: Any) -> str:
    try:
        return str(node.Name)
    except Exception:
        return ""


def node_value(node: Any) -> Any:
    try:
        return node.Value
    except Exception:
        return None


def node_unit(node: Any) -> str:
    try:
        return strip_unit(node.UnitString)
    except Exception:
        return ""


def node_value_type(node: Any) -> Any:
    try:
        return node.ValueType
    except Exception:
        return None


def extract_stream_composition(
    tree: Any,
    base: str,
    stream_id: str,
    stream_compstatus: Any,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows, _ = extract_stream_composition_with_status(
        tree,
        base,
        stream_id,
        stream_compstatus,
        warnings,
    )
    return rows


def extract_stream_composition_with_status(
    tree: Any,
    base: str,
    stream_id: str,
    stream_compstatus: Any,
    warnings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    r"""Read the complete MIXED-stream composition vector from Aspen.

    The path order follows the existing workspace Aspen COM references:
    ``Output\MOLEFRAC\MIXED\<component>`` first, then MASSFRAC.  Component
    names are preserved only as identifiers; no hazard or compatibility label
    is inferred here.
    """

    coverage: dict[str, Any] = {
        "stream_id": stream_id,
        "stream_record_type": "MATERIAL",
        "status": "MISSING_NO_COMPOSITION_VECTOR",
        "requested_vector_count": 1,
        "found_vector_count": 0,
        "component_count": 0,
        "invalid_component_count": 0,
        "enumeration_error_count": 0,
        "independent_of_registry_field_hit_rate": True,
        "candidate_attempts": [],
        "component_observations": [],
    }
    if owner_has_no_results(stream_compstatus):
        coverage["status"] = "MISSING_OWNER_HAS_NO_RESULTS"
        warnings.append({
            "code": "BLOCKED_COMPOSITION_VECTOR_OWNER_HAS_NO_RESULTS",
            "object": stream_id,
            "action": "do_not_claim_a_complete_composition_vector",
        })
        return [], coverage

    failure_priority = {
        "ENUMERATION_ERROR": 4,
        "INVALID_COMPONENT_VALUES": 3,
        "NOT_CLOSED": 2,
        "EMPTY_VECTOR": 1,
        "MISSING_ROOT": 0,
    }
    selected_failure: dict[str, Any] | None = None
    for relative, basis in (
        (r"Output\MOLEFRAC\MIXED", "mole_fraction"),
        (r"Output\MASSFRAC\MIXED", "mass_fraction"),
    ):
        root_path = base + "\\" + relative
        attempt: dict[str, Any] = {
            "basis": basis,
            "path": root_path,
            "status": "MISSING_ROOT",
            "declared_count": None,
            "enumerated_count": 0,
            "enumeration_errors": [],
            "invalid_components": [],
            "fraction_sum": None,
            "closed": False,
        }
        try:
            root = tree.FindNode(root_path)
        except Exception as exc:
            attempt["status"] = "ENUMERATION_ERROR"
            attempt["enumeration_errors"].append({
                "operation": "Tree.FindNode",
                "error": _safe_error_text(exc),
            })
            coverage["candidate_attempts"].append(attempt)
            selected_failure = attempt
            continue
        if root is None:
            coverage["candidate_attempts"].append(attempt)
            if selected_failure is None:
                selected_failure = attempt
            continue
        enumeration = strict_node_elements(root, ROOT_COLLECTION_MAX_OBJECTS)
        children = list(enumeration["rows"])
        attempt["declared_count"] = enumeration["declared_count"]
        attempt["enumerated_count"] = len(children)
        attempt["enumeration_errors"] = list(enumeration["errors"])
        attempt["truncated"] = bool(enumeration["truncated"])
        rows: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []
        component_observations: list[dict[str, Any]] = []
        for child in children:
            component_id = node_name(child).strip()
            raw_fraction = node_value(child)
            fraction = finite(raw_fraction)
            component_path = root_path + "\\" + component_id
            if not component_id or fraction is None or not 0.0 <= fraction <= 1.0:
                invalid.append({
                    "component_id": _safe_text(component_id, max_chars=128),
                    "path": component_path,
                    "value_metadata": _discovery_value_metadata(
                        raw_fraction,
                        node_value_type(child),
                    ),
                })
                continue
            rows.append({
                "component_id": component_id,
                "fraction": fraction,
                "basis": basis,
                "source_path": component_path,
            })
            component_observations.append({
                "component_id": component_id,
                "path": component_path,
                "unit": "-",
                "status": "found",
                "provenance": {
                    "source": "ASPEN_LIVE_COM_TREE",
                    "registry_revision": ASPEN_EXTRACTION_REGISTRY_REVISION,
                    "registry_field": "composition.*",
                    "dynamic_prefix": relative,
                    "selection_policy": "complete_component_vector_enumeration",
                },
            })
        attempt["invalid_components"] = invalid
        attempt["invalid_component_count"] = len(invalid)
        total = sum(float(item["fraction"]) for item in rows)
        tolerance = max(1.0e-8, 1.0e-6 * max(1.0, abs(total)))
        closed = bool(rows) and abs(total - 1.0) <= tolerance
        attempt["fraction_sum"] = total if rows else None
        attempt["closure_tolerance"] = tolerance
        attempt["closed"] = closed
        if enumeration["errors"] or enumeration["truncated"]:
            attempt["status"] = "ENUMERATION_ERROR"
        elif invalid:
            attempt["status"] = "INVALID_COMPONENT_VALUES"
        elif not rows:
            attempt["status"] = "EMPTY_VECTOR"
        elif not closed:
            attempt["status"] = "NOT_CLOSED"
        else:
            attempt["status"] = "FOUND_COMPLETE_VECTOR"
        coverage["candidate_attempts"].append(attempt)
        if attempt["status"] == "FOUND_COMPLETE_VECTOR":
            coverage.update({
                "status": "FOUND_COMPLETE_VECTOR",
                "found_vector_count": 1,
                "component_count": len(rows),
                "basis": basis,
                "fraction_sum": total,
                "closed": True,
                "component_observations": component_observations,
            })
            return rows, coverage
        if (
            selected_failure is None
            or failure_priority.get(str(attempt["status"]), -1)
            > failure_priority.get(str(selected_failure.get("status")), -1)
        ):
            selected_failure = attempt
    if selected_failure is not None:
        status = str(selected_failure.get("status") or "MISSING_ROOT")
        coverage["status"] = {
            "ENUMERATION_ERROR": "ERROR_COMPOSITION_ENUMERATION",
            "INVALID_COMPONENT_VALUES": "INVALID_COMPONENT_VALUES",
            "NOT_CLOSED": "INVALID_VECTOR_NOT_CLOSED",
            "EMPTY_VECTOR": "MISSING_EMPTY_COMPOSITION_VECTOR",
            "MISSING_ROOT": "MISSING_NO_COMPOSITION_VECTOR",
        }.get(status, "MISSING_NO_COMPOSITION_VECTOR")
        coverage["invalid_component_count"] = int(
            selected_failure.get("invalid_component_count") or 0
        )
        coverage["enumeration_error_count"] = len(
            selected_failure.get("enumeration_errors") or []
        )
        coverage["fraction_sum"] = selected_failure.get("fraction_sum")
        coverage["closed"] = bool(selected_failure.get("closed"))
    warnings.append({
        "code": "BLOCKED_COMPOSITION_VECTOR_INCOMPLETE",
        "object": stream_id,
        "status": coverage["status"],
        "invalid_component_count": coverage["invalid_component_count"],
        "enumeration_error_count": coverage["enumeration_error_count"],
        "action": "do_not_emit_or_claim_a_complete_composition_vector",
    })
    return [], coverage


def node_record_type(node: Any) -> tuple[str, str]:
    """Return Aspen semantic record type; node.Value may only be an icon token."""
    try:
        value = node.AttributeValue(6)  # V14 HAP_RECORDTYPE
        if value not in (None, ""):
            return str(value).strip().upper(), "AttributeValue(6):HAP_RECORDTYPE"
    except Exception:
        pass
    return "", "missing:HAP_RECORDTYPE"


def node_compstatus(node: Any) -> Any:
    try:
        return node.AttributeValue(12)  # V14 HAP_COMPSTATUS
    except Exception:
        return None


def named_child(node: Any, name: str) -> Any:
    try:
        return node.Elements(name)
    except Exception:
        wanted = name.casefold()
        return next((child for child in node_elements(node) if node_name(child).casefold() == wanted), None)


def find_node(tree: Any, path: str) -> Any:
    try:
        return tree.FindNode(path)
    except Exception:
        return None


def _aspen_child_names(node: Any) -> list[str]:
    return [
        node_name(child).strip()
        for child in node_elements(node)
        if node_name(child).strip()
    ]


def _aspen_child_values(node: Any) -> list[Any]:
    return [node_value(child) for child in node_elements(node)]


def _append_aspen_list_value(node: Any, value: Any) -> Any:
    if node is None:
        raise RuntimeError("BLOCKED_ASPEN_COM_LIST_NODE_NOT_FOUND")
    elements = node.Elements
    location = int(elements.RowCount(0))
    elements.InsertRow(0, location)
    row = elements.Item(location)
    row.Value = value
    return row


def _append_aspen_named_row(node: Any, name: str) -> Any:
    if node is None:
        raise RuntimeError("BLOCKED_ASPEN_COM_NAMED_LIST_NODE_NOT_FOUND")
    elements = node.Elements
    location = int(elements.RowCount(0))
    elements.InsertRow(0, location)
    elements.SetItemName(location, 0, False, name)
    return elements.Item(name)


def _set_required_aspen_value(tree: Any, path: str, value: Any) -> None:
    node = find_node(tree, path)
    if node is None:
        raise RuntimeError(f"BLOCKED_ASPEN_COM_CONFIGURATION_NODE_NOT_FOUND:{path}")
    node.Value = value


def _unused_transport_prop_set_name_from_tree(existing_names: set[str]) -> str:
    for candidate in ("TXPORT", "EDGTXPRT", "EDGMU001", "EDGMU002"):
        if candidate not in existing_names:
            return candidate
    raise RuntimeError("BLOCKED_NO_AVAILABLE_TRANSPORT_PROPERTY_SET_NAME")


def ensure_stream_transport_property_via_com(
    app: Any,
    out_dir: Path,
    source: Path,
) -> tuple[dict[str, Any], Path]:
    """Request Aspen MUMX directly in the isolated live COM document.

    This avoids an INP export/reload round trip.  Some valid Aspen BKPs contain
    GUI-side records that do not round-trip through the text deck, so changing
    only the staged live document is the safest general path.
    """

    tree = app.Tree
    prop_sets = find_node(tree, r"\Data\Properties\Prop-Sets")
    report_properties = find_node(tree, r"\Data\Setup\Main\Input\PROPERTIES")
    if prop_sets is None:
        raise RuntimeError("BLOCKED_COM_PROP_SETS_NODE_NOT_FOUND")
    if report_properties is None:
        raise RuntimeError("BLOCKED_COM_REPORT_PROPERTIES_NODE_NOT_FOUND")

    existing_names = {
        name.upper()
        for name in _aspen_child_names(prop_sets)
    }
    report_before = [
        str(value).strip()
        for value in _aspen_child_values(report_properties)
        if value not in (None, "")
    ]
    candidates: list[str] = []
    for name in existing_names:
        base = rf"\Data\Properties\Prop-Sets\{name}\Input"
        units = find_node(tree, base + r"\UNITS")
        phases = {
            str(value).strip().upper()
            for value in _aspen_child_values(find_node(tree, base + r"\PHASE"))
            if value not in (None, "")
        }
        substream = str(
            node_value(find_node(tree, base + r"\SUBSTREAM")) or ""
        ).strip().upper()
        property_names = {
            item.upper()
            for item in _aspen_child_names(units)
        }
        if (
            "MUMX" in property_names
            and substream == "MIXED"
            and {"V", "L"}.issubset(phases)
        ):
            candidates.append(name)
    candidates.sort(key=lambda name: (name != "TXPORT", name))

    operations: list[dict[str, Any]] = []
    property_set_created = False
    if candidates:
        property_set_name = candidates[0]
    else:
        property_set_name = _unused_transport_prop_set_name_from_tree(existing_names)
        prop_sets.Elements.Add(property_set_name)
        property_set_created = True
        operations.append({
            "operation": "Prop-Sets.Elements.Add",
            "argument": property_set_name,
        })
        base = rf"\Data\Properties\Prop-Sets\{property_set_name}\Input"
        for field, value in (
            ("DESCRIPTION", "Dynamic viscosity requested by EquipmentDesignApp"),
            ("SUBSTREAM", "MIXED"),
            ("SYSPRES", "YES"),
            ("SYSTEMP", "YES"),
        ):
            path = base + "\\" + field
            _set_required_aspen_value(tree, path, value)
            operations.append({
                "operation": "set_value",
                "path": path,
                "value": value,
            })
        phase = find_node(tree, base + r"\PHASE")
        for value in ("V", "L"):
            row = _append_aspen_list_value(phase, value)
            operations.append({
                "operation": "PHASE.Elements.InsertRow",
                "value": value,
                "returned_name": node_name(row),
            })
        units = find_node(tree, base + r"\UNITS")
        property_node = _append_aspen_named_row(units, "MUMX")
        operations.append({
            "operation": "UNITS.Elements.InsertRow+SetItemName",
            "argument": "MUMX",
            "returned_name": node_name(property_node),
        })

    report_updated = property_set_name.upper() not in {
        value.upper()
        for value in report_before
    }
    if report_updated:
        row = _append_aspen_list_value(report_properties, property_set_name)
        operations.append({
            "operation": "Setup.Main.PROPERTIES.Elements.InsertRow",
            "value": property_set_name,
            "returned_name": node_name(row),
        })

    base = rf"\Data\Properties\Prop-Sets\{property_set_name}\Input"
    manifest: dict[str, Any] = {
        "schema": "aspen-transport-property-augmentation-v2",
        "status": (
            "CHANGED"
            if property_set_created or report_updated
            else "ALREADY_PRESENT"
        ),
        "method": "DIRECT_COM_TREE_MUTATION_ON_ISOLATED_DOCUMENT",
        "property_set_name": property_set_name,
        "requested_property": "MUMX",
        "property_set_created": property_set_created,
        "stream_report_updated": report_updated,
        "operations": operations,
        "tree_configuration_before": {
            "property_set_names": sorted(existing_names),
            "stream_report_property_sets": report_before,
        },
        "tree_configuration_after": {
            "property_set_exists": find_node(
                tree,
                rf"\Data\Properties\Prop-Sets\{property_set_name}",
            ) is not None,
            "properties": _aspen_child_names(find_node(tree, base + r"\UNITS")),
            "phases": _aspen_child_values(find_node(tree, base + r"\PHASE")),
            "substream": node_value(find_node(tree, base + r"\SUBSTREAM")),
            "stream_report_property_sets": _aspen_child_values(report_properties),
        },
        "source_case_path": str(source),
        "source_case_sha256": sha256(source),
        "original_source_read_only": True,
        "mutation_scope": "ISOLATED_LIVE_COM_DOCUMENT_ONLY",
        "source_bkp_mutated": False,
        "com_reload_required": False,
        "rerun_method": "Engine.Run2(False)",
    }
    manifest_path = out_dir / "transport_property_augmentation_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return manifest, manifest_path


def owner_has_no_results(compstatus: Any) -> bool:
    if isinstance(compstatus, bool):
        return False
    try:
        status = int(compstatus)
    except (TypeError, ValueError):
        return False
    return bool(status & (HAP_NORESULTS | HAP_NOT_RUN))


def read_first(
    tree: Any,
    base: str,
    relative_paths: list[str],
    owner_compstatus: Any = None,
    require_continuous: bool = False,
) -> tuple[float | None, str, str, Any, str]:
    skipped_output = ""
    skipped_integer_node = ""
    no_results = owner_has_no_results(owner_compstatus)
    for relative in relative_paths:
        node = find_node(tree, base + "\\" + relative)
        if (
            node is not None
            and no_results
            and relative.lstrip("\\").upper().startswith("OUTPUT\\")
        ):
            skipped_output = skipped_output or relative
            continue
        value = finite(node_value(node))
        if value is not None:
            value_type = node_value_type(node)
            # The official IHNode ValueType contract uses 1 for integer and 2
            # for real. Aspen's generic tree exposes integer 0/1 placeholders
            # for several inapplicable continuous result cards (for example
            # RADFRAC CEFF and MCOMPR PRES_RATIO). Do not promote those into a
            # physical continuous quantity merely because they are numeric.
            if require_continuous and value_type == 1:
                skipped_integer_node = skipped_integer_node or relative
                continue
            return value, node_unit(node), relative, value_type, "defined"
    if skipped_output:
        return None, "", skipped_output, None, "skipped_owner_no_results"
    if skipped_integer_node:
        return None, "", skipped_integer_node, 1, "skipped_integer_node_for_continuous_field"
    return None, "", "", None, "undefined_or_missing"


def audit_extraction_registry() -> dict[str, Any]:
    """Return a machine-readable self-audit of the Aspen extraction registry."""

    issues: list[dict[str, Any]] = []
    for scope, registry in (
        ("stream", STREAM_FIELD_REGISTRY),
        ("block", BLOCK_FIELD_REGISTRY),
    ):
        for field, spec in registry.items():
            paths = spec.get("paths") if isinstance(spec, dict) else None
            if not isinstance(paths, list) or not paths:
                issues.append({
                    "code": "REGISTRY_FIELD_WITHOUT_PATH",
                    "scope": scope,
                    "field": field,
                })
                continue
            normalized = [str(path).strip().casefold() for path in paths]
            if len(normalized) != len(set(normalized)):
                issues.append({
                    "code": "REGISTRY_FIELD_DUPLICATE_PATH",
                    "scope": scope,
                    "field": field,
                })
            invalid_paths = [
                path for path in paths
                if not str(path).startswith(("Input\\", "Output\\"))
            ]
            if invalid_paths:
                issues.append({
                    "code": "REGISTRY_FIELD_PATH_OUTSIDE_INPUT_OUTPUT",
                    "scope": scope,
                    "field": field,
                    "paths": invalid_paths,
                })
            if spec.get("value_kind") not in {"continuous", "integer", "scalar"}:
                issues.append({
                    "code": "REGISTRY_FIELD_INVALID_VALUE_KIND",
                    "scope": scope,
                    "field": field,
                    "value_kind": spec.get("value_kind"),
                })
    for module, spec in BLOCK_MODULE_REGISTRY.items():
        if module != module.upper():
            issues.append({
                "code": "REGISTRY_MODULE_ID_NOT_UPPERCASE",
                "module": module,
            })
        process_function = str(spec.get("process_function") or "").strip()
        if not process_function:
            issues.append({
                "code": "REGISTRY_MODULE_WITHOUT_SEMANTIC_ROLE",
                "module": module,
            })
        module_fields = list(spec.get("fields") or [])
        if len(module_fields) != len(set(module_fields)):
            issues.append({
                "code": "REGISTRY_MODULE_DUPLICATE_FIELD",
                "module": module,
            })
        for field in module_fields:
            if field not in BLOCK_FIELD_REGISTRY:
                issues.append({
                    "code": "REGISTRY_MODULE_REFERENCES_UNKNOWN_FIELD",
                    "module": module,
                    "field": field,
                })
    for field in BLOCK_COMMON_FIELDS:
        if field not in BLOCK_FIELD_REGISTRY:
            issues.append({
                "code": "REGISTRY_COMMON_BLOCK_FIELD_UNKNOWN",
                "field": field,
            })
    stream_view_matches = STREAM_FIELDS == {
        field: list(spec["paths"])
        for field, spec in STREAM_FIELD_REGISTRY.items()
    }
    block_view_matches = BLOCK_FIELDS == {
        field: list(spec["paths"])
        for field, spec in BLOCK_FIELD_REGISTRY.items()
        if field not in BLOCK_COMMON_FIELDS
    }
    process_view_matches = PROCESS_FUNCTIONS == {
        module: str(spec["process_function"])
        for module, spec in BLOCK_MODULE_REGISTRY.items()
    }
    if not stream_view_matches or not block_view_matches:
        issues.append({
            "code": "LEGACY_PATH_VIEW_DIVERGED_FROM_REGISTRY",
            "stream_view_matches": stream_view_matches,
            "block_view_matches": block_view_matches,
        })
    if not process_view_matches:
        issues.append({
            "code": "PROCESS_FUNCTIONS_VIEW_DIVERGED_FROM_MODULE_REGISTRY",
        })
    return {
        "schema": "aspen-com-extraction-registry-audit-v1",
        "registry_revision": ASPEN_EXTRACTION_REGISTRY_REVISION,
        "status": "PASS" if not issues else "FAIL",
        "counts": {
            "stream_fields": len(STREAM_FIELD_REGISTRY),
            "block_fields": len(BLOCK_FIELD_REGISTRY),
            "registered_block_modules": len(BLOCK_MODULE_REGISTRY),
            "issues": len(issues),
        },
        "legacy_compatibility_views": {
            "STREAM_FIELDS": stream_view_matches,
            "BLOCK_FIELDS": block_view_matches,
            "PROCESS_FUNCTIONS": process_view_matches,
        },
        "unknown_module_policy": (
            "UNSUPPORTED_MODULE_WITH_NULL_PROCESS_FUNCTION; never assign a "
            "generic physical-equipment role"
        ),
        "issues": issues,
    }


def resolve_unit_with_provenance(
    field: str,
    raw_unit: str,
    in_units_fields: dict[str, str] | None,
) -> dict[str, Any]:
    """Resolve one unit and retain the exact source of that decision."""

    declared = str(raw_unit or "").strip()
    resolved, fallback_used = resolve_aspen_unit(field, declared, in_units_fields)
    if declared:
        source = "IHNode.UnitString"
    else:
        unit_set_key = RAW_FIELD_TO_IN_UNITS_KEY.get(field)
        from_unit_set = bool(
            unit_set_key
            and isinstance(in_units_fields, dict)
            and str(in_units_fields.get(unit_set_key) or "").strip()
        )
        if from_unit_set:
            source = "Aspen_BKP_global_IN-UNITS_card"
        elif field in SEMANTIC_UNIT_DEFAULTS:
            source = "named_Aspen_card_semantics"
        else:
            source = "missing"
    return {
        "unit": resolved,
        "raw_unit": declared or None,
        "fallback_used": fallback_used,
        "source": source,
        "in_units_field": RAW_FIELD_TO_IN_UNITS_KEY.get(field),
    }


def read_registered_field(
    tree: Any,
    base: str,
    field: str,
    spec: dict[str, Any],
    owner_compstatus: Any = None,
) -> dict[str, Any]:
    """Read one registry field without hiding COM lookup or value errors."""

    relative_paths = [str(path) for path in spec.get("paths") or []]
    candidate_paths = [base + "\\" + path for path in relative_paths]
    value_kind = str(spec.get("value_kind") or "continuous")
    require_continuous = value_kind == "continuous"
    no_results = owner_has_no_results(owner_compstatus)
    lookup_errors: list[dict[str, str]] = []
    skipped_output: list[str] = []
    skipped_integer: list[str] = []
    undefined_nodes: list[str] = []

    provenance = {
        "source": "ASPEN_LIVE_COM_TREE",
        "registry_revision": ASPEN_EXTRACTION_REGISTRY_REVISION,
        "registry_field": field,
        "registry_description": str(spec.get("description") or ""),
        "registry_requirement": str(spec.get("requirement") or "when_available"),
        "selection_policy": "first_defined_candidate_in_registry_order",
    }

    for relative, absolute in zip(relative_paths, candidate_paths):
        if no_results and relative.lstrip("\\").upper().startswith("OUTPUT\\"):
            skipped_output.append(absolute)
            continue
        try:
            node = tree.FindNode(absolute)
        except Exception as exc:
            lookup_errors.append({
                "operation": "Tree.FindNode",
                "path": absolute,
                "error": _safe_error_text(exc),
            })
            continue
        if node is None:
            continue
        try:
            raw_value = node.Value
        except Exception as exc:
            lookup_errors.append({
                "operation": "IHNode.Value",
                "path": absolute,
                "error": _safe_error_text(exc),
            })
            continue
        try:
            if value_kind == "scalar":
                value = (
                    _json_safe_com_value(raw_value)
                    if raw_value not in (None, "")
                    else None
                )
            else:
                value = finite(raw_value)
        except (TypeError, ValueError) as exc:
            lookup_errors.append({
                "operation": "json_safe_scalar_conversion",
                "path": absolute,
                "error": _safe_error_text(exc),
                "value_metadata": _discovery_value_metadata(
                    raw_value,
                    node_value_type(node),
                ),
            })
            continue
        if value is None:
            if raw_value not in (None, ""):
                lookup_errors.append({
                    "operation": "numeric_conversion",
                    "path": absolute,
                    "error": "NON_FINITE_OR_NON_NUMERIC_COM_VALUE",
                    "value_metadata": _discovery_value_metadata(
                        raw_value,
                        node_value_type(node),
                    ),
                })
            else:
                undefined_nodes.append(absolute)
            continue
        try:
            value_type = node.ValueType
        except Exception:
            value_type = None
        if require_continuous and value_type == 1:
            skipped_integer.append(absolute)
            continue
        try:
            raw_unit = strip_unit(node.UnitString)
            unit_error = None
        except Exception as exc:
            raw_unit = ""
            unit_error = _safe_error_text(exc)
        field_provenance = {
            **provenance,
            "selected_relative_path": relative,
            "selected_candidate_index": relative_paths.index(relative),
            "unit_read": "IHNode.UnitString" if unit_error is None else "IHNode.UnitString_error",
        }
        if unit_error is not None:
            field_provenance["unit_read_error"] = unit_error
        return {
            "field": field,
            "path": absolute,
            "candidate_paths": candidate_paths,
            "unit": raw_unit or None,
            "raw_unit": raw_unit or None,
            "status": "found",
            "detail_status": "defined",
            "value": value,
            "value_type": value_type,
            "provenance": field_provenance,
            "errors": lookup_errors,
            "recovered_error_count": len(lookup_errors),
        }

    if lookup_errors:
        status = "error"
        detail_status = "com_or_value_error"
        selected_path = lookup_errors[0]["path"]
    elif skipped_output:
        status = "missing"
        detail_status = "skipped_owner_no_results"
        selected_path = skipped_output[0]
    elif skipped_integer:
        status = "missing"
        detail_status = "skipped_integer_node_for_continuous_field"
        selected_path = skipped_integer[0]
    else:
        status = "missing"
        detail_status = "undefined_or_missing"
        selected_path = candidate_paths[0] if candidate_paths else None
    return {
        "field": field,
        "path": selected_path,
        "candidate_paths": candidate_paths,
        "unit": None,
        "raw_unit": None,
        "status": status,
        "detail_status": detail_status,
        "value": None,
        "value_type": 1 if skipped_integer else None,
        "provenance": {
            **provenance,
            "selected_relative_path": None,
            "selected_candidate_index": None,
        },
        "errors": lookup_errors,
        "recovered_error_count": 0,
        "skipped_paths": skipped_output + skipped_integer,
        "undefined_paths": undefined_nodes,
    }


SAFE_TEXT_MAX_CHARS = 512


def _safe_text(value: Any, max_chars: int = SAFE_TEXT_MAX_CHARS) -> str:
    """Return bounded, UTF-8-safe diagnostic text without recursive repr use."""

    try:
        text = str(value)
    except Exception:
        text = f"<{type(value).__name__}:unprintable>"
    text = text.encode("utf-8", errors="replace").decode("utf-8")
    # Cover both plain assignments and JSON/header renderings.  In particular,
    # the two supported LLM environment-variable names do not contain a word
    # boundary immediately before API_KEY, so the historical generic pattern
    # alone did not redact them.
    text = re.sub(
        r"(?i)([\"']?(?:OPENAI_API_KEY|EQUIPMENT_DESIGN_LLM_API_KEY)[\"']?"
        r"\s*[:=]\s*[\"']?)[^\"'\s,;]+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(
        r"(?i)([\"']?authorization[\"']?\s*(?:[:=]\s*)?[\"']?bearer\s+)"
        r"[^\"'\s,;]+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(
        r"(?i)\b(api[_ -]?key|password|secret|token)\s*[:=]\s*[^\s,;]+",
        r"\1=<redacted>",
        text,
    )
    if len(text) > max_chars:
        return text[:max_chars] + "…<truncated>"
    return text


def _safe_error_text(exc: BaseException) -> str:
    return _safe_text(f"{type(exc).__name__}: {_safe_text(exc)}")


def _json_safe_com_value(value: Any) -> Any:
    """Return a bounded JSON scalar for explicit local debugging only.

    The production discovery path never calls this function or persists its
    return value.  It exists to make any future opt-in debugger fail closed on
    NaN/Inf, unbounded strings, invalid Unicode, or opaque COM objects.
    """

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NON_FINITE_COM_VALUE_REJECTED")
        return value
    if isinstance(value, str):
        return _safe_text(value)
    raise TypeError(f"UNSUPPORTED_COM_VALUE_TYPE:{type(value).__name__}")


def new_case_discovery_budget() -> dict[str, Any]:
    """Create one mutable, bounded inventory budget shared by the whole case."""

    return {
        "max_nodes": TREE_DISCOVERY_MAX_NODES_PER_CASE,
        "nodes_visited": 0,
        "node_budget_exhausted": False,
        "max_metadata_bytes": TREE_DISCOVERY_MAX_METADATA_BYTES_PER_CASE,
        "metadata_bytes_hashed": 0,
        "metadata_bytes_output": 0,
        "metadata_bytes_consumed": 0,
        "metadata_budget_exhausted": False,
        "metadata_values_truncated": 0,
        "metadata_text_values_truncated": 0,
    }


def _consume_discovery_node(case_budget: dict[str, Any] | None) -> bool:
    if case_budget is None:
        return True
    limit = max(0, int(case_budget.get("max_nodes") or 0))
    used = max(0, int(case_budget.get("nodes_visited") or 0))
    if used >= limit:
        case_budget["node_budget_exhausted"] = True
        return False
    case_budget["nodes_visited"] = used + 1
    return True


def _metadata_byte_allowance(case_budget: dict[str, Any] | None) -> int | None:
    if case_budget is None:
        return None
    limit = max(0, int(case_budget.get("max_metadata_bytes") or 0))
    used = max(
        0,
        int(
            case_budget.get(
                "metadata_bytes_consumed",
                case_budget.get("metadata_bytes_hashed") or 0,
            )
            or 0
        ),
    )
    return max(0, limit - used)


def _bounded_discovery_text(value: Any, max_chars: int) -> tuple[str, bool]:
    """Return one bounded metadata string and whether information was cut."""

    try:
        rendered = str(value)
    except Exception:
        rendered = f"<{type(value).__name__}:unprintable>"
    rendered = rendered.encode("utf-8", errors="replace").decode("utf-8")
    if len(rendered) <= max_chars:
        return rendered, False
    return rendered[:max_chars] + "…<truncated>", True


def _json_metadata_scalar(value: Any) -> tuple[Any, bool]:
    """Normalize COM metadata type cards to a bounded JSON scalar."""

    if value is None or isinstance(value, (bool, int)):
        return value, False
    if isinstance(value, float):
        return (value, False) if math.isfinite(value) else (None, True)
    rendered, truncated = _bounded_discovery_text(
        value,
        TREE_DISCOVERY_MAX_TYPE_CHARS,
    )
    return rendered, truncated


def _metadata_output_size(metadata: dict[str, Any]) -> int:
    """Return canonical UTF-8 bytes for the dynamic metadata actually emitted."""

    return len(
        json.dumps(
            metadata,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8", errors="replace")
    )


def _consume_discovery_metadata_output(
    case_budget: dict[str, Any] | None,
    metadata: dict[str, Any],
) -> bool:
    """Charge the exact emitted name/path/unit/type values to the case budget."""

    if case_budget is None:
        return True
    required = _metadata_output_size(metadata)
    allowance = _metadata_byte_allowance(case_budget)
    if allowance is not None and required > allowance:
        case_budget["metadata_budget_exhausted"] = True
        return False
    case_budget["metadata_bytes_output"] = (
        int(case_budget.get("metadata_bytes_output") or 0) + required
    )
    case_budget["metadata_bytes_consumed"] = (
        int(case_budget.get("metadata_bytes_consumed") or 0) + required
    )
    return True


def _mark_discovery_text_truncated(
    case_budget: dict[str, Any] | None,
) -> None:
    if case_budget is None:
        return
    # Per-value length bounds are part of the same fail-closed metadata budget.
    # A huge COM name must never become a cheap truncated record that silently
    # passes the total-byte gate.
    case_budget["metadata_budget_exhausted"] = True
    case_budget["metadata_text_values_truncated"] = (
        int(case_budget.get("metadata_text_values_truncated") or 0) + 1
    )


def _hash_scalar_bytes_bounded(
    value: Any,
    *,
    byte_allowance: int | None,
) -> tuple[str | None, int | None, int, bool, str]:
    """Hash strings/bytes incrementally; never allocate their full encoding."""

    digest = hashlib.sha256()
    consumed = 0

    def accept(chunk: bytes) -> bool:
        nonlocal consumed
        if byte_allowance is not None and consumed + len(chunk) > byte_allowance:
            permitted = max(0, byte_allowance - consumed)
            if permitted:
                digest.update(chunk[:permitted])
                consumed += permitted
            return False
        digest.update(chunk)
        consumed += len(chunk)
        return True

    if isinstance(value, str):
        basis = "utf8_surrogatepass_string_bytes"
        for start in range(0, len(value), TREE_DISCOVERY_HASH_CHUNK_CHARS):
            chunk = value[start:start + TREE_DISCOVERY_HASH_CHUNK_CHARS].encode(
                "utf-8",
                errors="surrogatepass",
            )
            if not accept(chunk):
                return None, None, consumed, False, basis
    elif isinstance(value, bytes):
        basis = "raw_bytes"
        view = memoryview(value)
        for start in range(0, len(view), TREE_DISCOVERY_HASH_CHUNK_CHARS):
            if not accept(bytes(view[start:start + TREE_DISCOVERY_HASH_CHUNK_CHARS])):
                return None, None, consumed, False, basis
    elif value is None or isinstance(value, (bool, int, float)):
        basis = "canonical_json_scalar_bytes"
        raw = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if not accept(raw):
            return None, None, consumed, False, basis
    else:
        basis = "type_marker_not_raw_value"
        marker = (
            f"UNSUPPORTED_TYPE:{type(value).__module__}.{type(value).__qualname__}"
        ).encode("utf-8")
        if not accept(marker):
            return None, None, consumed, False, basis
    return digest.hexdigest().upper(), consumed, consumed, True, basis


def _discovery_value_metadata(
    value: Any,
    value_type: Any,
    case_budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Hash a scalar without retaining its raw value in the export."""

    if isinstance(value, float) and not math.isfinite(value):
        marker = f"REJECTED_NON_FINITE:{value!r}".encode("ascii")
        return {
            "value_type": value_type,
            "value_size": 0,
            "value_sha256": hashlib.sha256(marker).hexdigest().upper(),
            "value_metadata_status": "rejected_non_finite",
            "hash_basis": "non_finite_rejection_marker",
        }
    allowance = _metadata_byte_allowance(case_budget)
    value_sha256, value_size, consumed, complete, basis = _hash_scalar_bytes_bounded(
        value,
        byte_allowance=allowance,
    )
    if case_budget is not None:
        case_budget["metadata_bytes_hashed"] = (
            int(case_budget.get("metadata_bytes_hashed") or 0) + consumed
        )
        case_budget["metadata_bytes_consumed"] = (
            int(case_budget.get("metadata_bytes_consumed") or 0) + consumed
        )
    if not complete:
        if case_budget is not None:
            case_budget["metadata_budget_exhausted"] = True
            case_budget["metadata_values_truncated"] = (
                int(case_budget.get("metadata_values_truncated") or 0) + 1
            )
        return {
            "value_type": value_type,
            "value_size": None,
            "value_size_lower_bound": consumed + 1,
            "value_sha256": None,
            "value_metadata_status": "metadata_budget_exceeded_not_hashed",
            "hash_basis": f"{basis}:incomplete_case_budget",
        }
    return {
        "value_type": value_type,
        "value_size": value_size,
        "value_sha256": value_sha256,
        "value_metadata_status": (
            "unsupported_type_metadata_only"
            if basis == "type_marker_not_raw_value"
            else "hashed_without_raw_value"
        ),
        "hash_basis": basis,
    }


def _normalized_relative_path(path: str) -> str:
    return str(path or "").strip("\\").casefold()


def _registered_tree_path(
    relative_path: str,
    mapped_paths: list[str],
    dynamic_prefixes: tuple[str, ...] = (),
) -> bool:
    relative = _normalized_relative_path(relative_path)
    candidates = [_normalized_relative_path(path) for path in mapped_paths]
    dynamic = [_normalized_relative_path(path) for path in dynamic_prefixes]
    if relative in candidates:
        return True
    if any(candidate.startswith(relative + "\\") for candidate in candidates):
        return True
    return any(
        relative == prefix
        or relative.startswith(prefix + "\\")
        or prefix.startswith(relative + "\\")
        for prefix in dynamic
    )


def discover_object_tree(
    tree: Any,
    *,
    scope: str,
    object_id: str,
    base: str,
    mapped_paths: list[str],
    module_type: str | None = None,
    dynamic_prefixes: tuple[str, ...] = (),
    case_budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Boundedly inventory Input/Output nodes and expose unregistered leaves."""

    nodes: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    visited: set[int] = set()
    truncated = False
    node_budget_exhausted = False
    metadata_budget_exhausted = False

    for section in ("Input", "Output"):
        absolute_root = base + "\\" + section
        try:
            root = tree.FindNode(absolute_root)
        except Exception as exc:
            errors.append({
                "scope": scope,
                "object_id": object_id,
                "module_type": module_type,
                "path": absolute_root,
                "unit": None,
                "status": "error",
                "provenance": {
                    "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                    "operation": "Tree.FindNode",
                    "error": _safe_error_text(exc),
                },
            })
            continue
        if root is None:
            continue
        stack: list[tuple[Any, str, str, int]] = [
            (root, absolute_root, section, 0)
        ]
        while stack:
            node, absolute_path, relative_path, depth = stack.pop()
            identity = id(node)
            if identity in visited:
                continue
            if len(visited) >= TREE_DISCOVERY_MAX_NODES_PER_OBJECT:
                truncated = True
                break
            if not _consume_discovery_node(case_budget):
                truncated = True
                node_budget_exhausted = True
                break
            visited.add(identity)
            enumeration_limit = TREE_DISCOVERY_MAX_NODES_PER_OBJECT - len(visited)
            if case_budget is not None:
                enumeration_limit = min(
                    enumeration_limit,
                    max(
                        0,
                        int(case_budget.get("max_nodes") or 0)
                        - int(case_budget.get("nodes_visited") or 0),
                    ),
                )
            enumeration = strict_node_elements(
                node,
                enumeration_limit,
            )
            children = list(enumeration["rows"])
            if enumeration["truncated"]:
                truncated = True
            for enumeration_error in enumeration["errors"]:
                errors.append({
                    "scope": scope,
                    "object_id": object_id,
                    "module_type": module_type,
                    "path": absolute_path,
                    "unit": None,
                    "status": "error",
                    "provenance": {
                        "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                        **enumeration_error,
                    },
                })
            mapped = _registered_tree_path(
                relative_path,
                mapped_paths,
                dynamic_prefixes,
            )
            emitted_name, name_truncated = _bounded_discovery_text(
                node_name(node),
                TREE_DISCOVERY_MAX_NAME_CHARS,
            )
            emitted_path, path_truncated = _bounded_discovery_text(
                absolute_path,
                TREE_DISCOVERY_MAX_PATH_CHARS,
            )
            emitted_relative_path, relative_path_truncated = _bounded_discovery_text(
                relative_path,
                TREE_DISCOVERY_MAX_PATH_CHARS,
            )
            emitted_unit, unit_truncated = _bounded_discovery_text(
                node_unit(node),
                TREE_DISCOVERY_MAX_UNIT_CHARS,
            )
            text_metadata_truncated = any((
                name_truncated,
                path_truncated,
                relative_path_truncated,
                unit_truncated,
            ))
            if text_metadata_truncated:
                truncated = True
                metadata_budget_exhausted = True
                _mark_discovery_text_truncated(case_budget)
            node_entry = {
                "scope": scope,
                "object_id": object_id,
                "module_type": module_type,
                "path": emitted_path,
                "relative_path": emitted_relative_path,
                "node_name": emitted_name,
                "node_kind": "container" if children else "leaf",
                "unit": emitted_unit or None,
                "status": "mapped" if mapped else "unsupported",
                "provenance": {
                    "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                    "registry_revision": ASPEN_EXTRACTION_REGISTRY_REVISION,
                    "mapping_policy": "exact_candidate_or_registered_dynamic_prefix",
                },
            }
            if children:
                if not _consume_discovery_metadata_output(
                    case_budget,
                    {
                        "node_name": node_entry["node_name"],
                        "path": node_entry["path"],
                        "relative_path": node_entry["relative_path"],
                        "unit": node_entry["unit"],
                    },
                ):
                    truncated = True
                    metadata_budget_exhausted = True
                    errors.append({
                        "scope": scope,
                        "object_id": object_id,
                        "module_type": module_type,
                        "path": "<metadata-budget-exhausted>",
                        "unit": None,
                        "status": "error",
                        "provenance": {
                            "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                            "operation": "metadata_output",
                            "error": "CASE_METADATA_BYTE_BUDGET_EXCEEDED",
                        },
                    })
                    stack.clear()
                    break
                nodes.append(node_entry)
                if text_metadata_truncated:
                    errors.append({
                        "scope": scope,
                        "object_id": object_id,
                        "module_type": module_type,
                        "path": emitted_path,
                        "unit": emitted_unit or None,
                        "status": "error",
                        "provenance": {
                            "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                            "operation": "metadata_output",
                            "error": "DISCOVERY_METADATA_TEXT_LIMIT_EXCEEDED",
                        },
                    })
                    stack.clear()
                    break
            if not children:
                try:
                    value = node.Value
                except Exception as exc:
                    value = None
                    errors.append({
                        "scope": scope,
                        "object_id": object_id,
                        "module_type": module_type,
                        "path": absolute_path,
                        "unit": node_entry["unit"],
                        "status": "error",
                        "provenance": {
                            "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                            "operation": "IHNode.Value",
                            "error": _safe_error_text(exc),
                        },
                    })
                if value not in (None, ""):
                    emitted_value_type, value_type_truncated = _json_metadata_scalar(
                        node_value_type(node)
                    )
                    if value_type_truncated:
                        text_metadata_truncated = True
                        truncated = True
                        metadata_budget_exhausted = True
                        _mark_discovery_text_truncated(case_budget)
                    if not _consume_discovery_metadata_output(
                        case_budget,
                        {
                            "field": emitted_name
                            or emitted_relative_path.rsplit("\\", 1)[-1],
                            "node_name": node_entry["node_name"],
                            "path": node_entry["path"],
                            "relative_path": node_entry["relative_path"],
                            "unit": node_entry["unit"],
                            "value_type": emitted_value_type,
                        },
                    ):
                        truncated = True
                        metadata_budget_exhausted = True
                        errors.append({
                            "scope": scope,
                            "object_id": object_id,
                            "module_type": module_type,
                            "path": "<metadata-budget-exhausted>",
                            "unit": None,
                            "status": "error",
                            "provenance": {
                                "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                                "operation": "metadata_output",
                                "error": "CASE_METADATA_BYTE_BUDGET_EXCEEDED",
                            },
                        })
                        stack.clear()
                        break
                    value_metadata = _discovery_value_metadata(
                        value,
                        emitted_value_type,
                        case_budget,
                    )
                    fields.append({
                        **node_entry,
                        "field": emitted_name or emitted_relative_path.rsplit("\\", 1)[-1],
                        **value_metadata,
                    })
                    if value_metadata["value_metadata_status"] == "rejected_non_finite":
                        errors.append({
                            "scope": scope,
                            "object_id": object_id,
                            "module_type": module_type,
                            "path": absolute_path,
                            "unit": node_entry["unit"],
                            "status": "error",
                            "provenance": {
                                "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                                "operation": "value_metadata",
                                "error": "NON_FINITE_COM_VALUE_REJECTED",
                            },
                        })
                    elif value_metadata["value_metadata_status"] == "metadata_budget_exceeded_not_hashed":
                        truncated = True
                        metadata_budget_exhausted = True
                        errors.append({
                            "scope": scope,
                            "object_id": object_id,
                            "module_type": module_type,
                            "path": absolute_path,
                            "unit": node_entry["unit"],
                            "status": "error",
                            "provenance": {
                                "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                                "operation": "value_metadata",
                                "error": "CASE_METADATA_BYTE_BUDGET_EXCEEDED",
                            },
                        })
                        stack.clear()
                        break
                    if text_metadata_truncated:
                        errors.append({
                            "scope": scope,
                            "object_id": object_id,
                            "module_type": module_type,
                            "path": emitted_path,
                            "unit": emitted_unit or None,
                            "status": "error",
                            "provenance": {
                                "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                                "operation": "metadata_output",
                                "error": "DISCOVERY_METADATA_TEXT_LIMIT_EXCEEDED",
                            },
                        })
                        stack.clear()
                        break
            if depth >= TREE_DISCOVERY_MAX_DEPTH:
                if children:
                    truncated = True
                continue
            for child in reversed(children):
                raw_name = node_name(child).strip()
                if not raw_name:
                    errors.append({
                        "scope": scope,
                        "object_id": object_id,
                        "module_type": module_type,
                        "path": absolute_path,
                        "unit": None,
                        "status": "error",
                        "provenance": {
                            "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                            "operation": "IHNode.Name",
                            "error": "EMPTY_CHILD_NAME",
                        },
                    })
                    continue
                name, child_name_truncated = _bounded_discovery_text(
                    raw_name,
                    TREE_DISCOVERY_MAX_NAME_CHARS,
                )
                if child_name_truncated:
                    truncated = True
                    metadata_budget_exhausted = True
                    _mark_discovery_text_truncated(case_budget)
                    errors.append({
                        "scope": scope,
                        "object_id": object_id,
                        "module_type": module_type,
                        "path": absolute_path,
                        "unit": None,
                        "status": "error",
                        "provenance": {
                            "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                            "operation": "IHNode.Name",
                            "error": "DISCOVERY_METADATA_TEXT_LIMIT_EXCEEDED",
                        },
                    })
                    stack.clear()
                    break
                stack.append((
                    child,
                    absolute_path + "\\" + name,
                    relative_path + "\\" + name,
                    depth + 1,
                ))
        if truncated:
            break
    return {
        "nodes": nodes,
        "fields": fields,
        "errors": errors,
        "truncated": truncated,
        "visited_node_count": len(visited),
        "budget_exhausted": {
            "node": node_budget_exhausted,
            "metadata": metadata_budget_exhausted,
        },
    }


def summarize_field_coverage(
    observations: dict[str, dict[str, Any]],
    *,
    unsupported_count: int = 0,
    registry_status: str = "supported",
    unmapped_field_count: int = 0,
    discovery_error_count: int = 0,
    discovery_truncated: bool = False,
) -> dict[str, Any]:
    requested = len(observations)
    counts = {
        "requested": requested,
        "found": sum(1 for item in observations.values() if item.get("status") == "found"),
        "missing": sum(1 for item in observations.values() if item.get("status") == "missing"),
        "error": sum(1 for item in observations.values() if item.get("status") == "error"),
        "unsupported": int(unsupported_count),
    }
    hit_rate = counts["found"] / requested if requested else None
    completeness_scorable = (
        registry_status == "supported"
        and not discovery_truncated
        and discovery_error_count == 0
        and unmapped_field_count == 0
        and counts["error"] == 0
    )
    if registry_status != "supported":
        completeness_status = f"NOT_SCORABLE_{registry_status.upper()}"
    elif discovery_truncated:
        completeness_status = "NOT_SCORABLE_DISCOVERY_TRUNCATED"
    elif discovery_error_count:
        completeness_status = "NOT_SCORABLE_DISCOVERY_ERRORS"
    elif counts["error"]:
        completeness_status = "NOT_SCORABLE_REGISTERED_FIELD_ERRORS"
    elif unmapped_field_count:
        completeness_status = "REVIEW_UNMAPPED_FIELDS"
    else:
        completeness_status = "SCORED_REGISTERED_OBJECT"
    return {
        "counts": counts,
        "recovered_error_count": sum(
            int(item.get("recovered_error_count") or 0)
            for item in observations.values()
        ),
        "registry_field_hit_rate": hit_rate,
        "registry_field_hit_percent": (
            round(100.0 * counts["found"] / requested, 6)
            if requested
            else None
        ),
        # Backward-compatible alias. This never claims semantic completeness.
        "coverage_rate": hit_rate,
        "coverage_percent": (
            round(100.0 * counts["found"] / requested, 6)
            if requested
            else None
        ),
        "registry_completeness_status": completeness_status,
        "registry_completeness_rate": hit_rate if completeness_scorable else None,
        "requested_status_invariant": (
            counts["found"] + counts["missing"] + counts["error"]
            == requested
        ),
    }


def parse_aspen_history(text: str) -> dict[str, Any]:
    clean_statement = bool(re.search(r"NO ERRORS OR WARNINGS (?:GENERATED|WERE ISSUED)", text, flags=re.I))
    chunks = list(re.finditer(r"(?:SUMMARY OF ERRORS|Summary of Simulation Errors)(.*?)(?:\f|\Z)", text, flags=re.S | re.I))
    counts: dict[str, int] | None = None
    if chunks:
        chunk = chunks[-1].group(1)
        counts = {}
        labels = {
            "terminal_errors": "TERMINAL ERRORS",
            "severe_errors": "SEVERE ERRORS",
            "errors": "ERRORS",
            "warnings": "WARNINGS",
        }
        for key, label in labels.items():
            match = re.search(rf"(?im)^\s*{label}\s+((?:\d+\s+)+\d+)\s*$", chunk)
            if not match:
                counts = None
                break
            counts[key] = sum(int(value) for value in re.findall(r"\d+", match.group(1)))
    if counts is None and clean_statement:
        counts = {"terminal_errors": 0, "severe_errors": 0, "errors": 0, "warnings": 0}
    problem_lines = [
        line.strip()
        for line in text.splitlines()
        if any(re.search(pattern, line) for pattern in PROBLEM_PATTERNS)
    ]
    return {
        "found": counts is not None,
        "counts": counts,
        "clean_statement": clean_statement,
        "problem_lines": problem_lines[:200],
    }


def parse_history_block_results(text: str) -> dict[str, dict[str, dict[str, Any]]]:
    """Recover final UOS results that Aspen omits from some COM output cards.

    Aspen's raw ``.his`` UOS trace uses SI working values: W for HEATX duty
    and the three distinct PUMP channels ``FLUID PWR``, ``BRAKE PWR`` and
    ``ELEC PWR``; and kPa of available suction-pressure margin for PUMP
    ``NPSH AVAIL``.  The latter is not metres of liquid head and must not be
    exposed as such until a downstream calculation converts pressure to head
    using density and gravity.  PUMP input-card ``DEFF`` is retained as a
    dimensionless fraction. Repeated recycle-iteration blocks are
    intentionally processed in order so the final ``GENERATING RESULTS``
    occurrence replaces earlier provisional iterations.
    """

    header = re.compile(
        r"(?im)^\s*(?P<final>GENERATING\s+RESULTS\s+FOR\s+)?"
        r"UOS\s+BLOCK\s+(?P<block>\S+)\s+MODEL:\s*(?P<model>\S+)"
    )
    matches = list(header.finditer(text))
    recovered: dict[str, dict[str, dict[str, Any]]] = {}
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"

    # The raw history contains a numbered echo of the Aspen input deck before
    # the UOS result trace. Bound each PARAM search by the next BLOCK card so a
    # missing DEFF can never be borrowed from a neighbouring pump.
    input_header = re.compile(
        r"(?im)^\s*(?:\d+\s+)?BLOCK\s+"
        r"(?P<block>\S+)\s+(?P<model>[A-Z0-9_-]+)\b"
    )
    input_matches = list(input_header.finditer(text))
    pump_driver_efficiency: dict[str, dict[str, Any]] = {}
    for index, input_match in enumerate(input_matches):
        if input_match.group("model").strip().upper() != "PUMP":
            continue
        segment_end = (
            input_matches[index + 1].start()
            if index + 1 < len(input_matches)
            else len(text)
        )
        segment = text[input_match.end():segment_end]
        efficiency = re.search(
            rf"(?im)\bDEFF\s*=\s*(?P<value>{number})",
            segment,
        )
        if efficiency is None:
            continue
        raw_fraction = float(efficiency.group("value"))
        block_id = input_match.group("block").strip()
        pump_driver_efficiency[block_id] = {
            "value": raw_fraction,
            "unit": "fraction",
            "raw_value": raw_fraction,
            "raw_unit": "fraction",
            "history_label": "DEFF",
            "quantity_kind": "pump_driver_efficiency_fraction",
            "transform": (
                "DEFF_fraction = Aspen_history_input_card_DEFF (identity)"
            ),
            "status": "raw_history_input_card_fallback",
            "source": "Aspen .his PUMP input PARAM card field DEFF",
        }

    for index, match in enumerate(matches):
        segment_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.end():segment_end]
        block_id = match.group("block").strip()
        model = match.group("model").strip().upper()
        observations: dict[str, dict[str, Any]] = {}
        if model == "PUMP":
            power_fields = (
                (
                    "FLUID_POWER",
                    r"FLUID\s+PWR",
                    "FLUID PWR",
                    "pump_hydraulic_fluid_power",
                ),
                (
                    "BRAKE_POWER",
                    r"BRAKE\s+PWR",
                    "BRAKE PWR",
                    "pump_brake_shaft_power",
                ),
                (
                    "ELEC_POWER",
                    r"ELEC\s+PWR",
                    "ELEC PWR",
                    "pump_electrical_input_power",
                ),
            )
            for field, pattern, label, quantity_kind in power_fields:
                power = re.search(
                    rf"(?im)\b{pattern}\s*=\s*(?P<value>{number})",
                    segment,
                )
                if power is None:
                    continue
                raw_power_w = float(power.group("value"))
                observations[field] = {
                    "value": raw_power_w / 1000.0,
                    "unit": "kW",
                    "raw_value": raw_power_w,
                    "raw_unit": "W",
                    "history_label": label,
                    "quantity_kind": quantity_kind,
                    "transform": (
                        f"{field}_kW = Aspen_history_{label.replace(' ', '_')}_W / 1000"
                    ),
                    "status": "raw_history_pump_power_fallback",
                    "source": f"Aspen .his UOS PUMP result field {label}",
                }
            npsh = re.search(rf"(?im)\bNPSH\s+AVAIL\s*=\s*(?P<value>{number})", segment)
            if npsh:
                raw_pressure_margin_kpa = float(npsh.group("value"))
                observations["NPSHA"] = {
                    "value": raw_pressure_margin_kpa,
                    "unit": "kPa",
                    "raw_value": raw_pressure_margin_kpa,
                    "raw_unit": "kPa",
                    "history_label": "NPSH AVAIL",
                    "quantity_kind": "available_suction_pressure_margin",
                    "transform": (
                        "NPSHA_pressure_margin_kPa = "
                        "Aspen_history_NPSH_AVAIL_kPa (identity; no pressure-to-head conversion)"
                    ),
                    "status": "raw_history_pressure_margin_fallback",
                    "source": "Aspen .his UOS PUMP result field NPSH AVAIL",
                }
            if block_id in pump_driver_efficiency:
                observations["DEFF"] = dict(pump_driver_efficiency[block_id])
        if model == "HEATX":
            area = re.search(rf"(?im)\bAREA\s*=\s*(?P<value>{number})", segment)
            if area:
                observations["AREA"] = {
                    "value": float(area.group("value")),
                    "unit": "m2",
                    "raw_value": float(area.group("value")),
                    "raw_unit": "m2",
                    "history_label": "AREA",
                    "status": "raw_history_fallback",
                }
            duty = re.search(rf"(?im)\bDUTY\s*=\s*(?P<value>{number})", segment)
            if duty:
                raw_w = float(duty.group("value"))
                observations["QCALC"] = {
                    "value": raw_w / 1000.0,
                    "unit": "kW",
                    "raw_value": raw_w,
                    "raw_unit": "W",
                    "history_label": "DUTY",
                    "transform": "QCALC_kW = Aspen_history_DUTY_W / 1000",
                    "status": "raw_history_fallback",
                }
        if observations:
            recovered[block_id] = observations
    return recovered


def merge_history_block_results(
    bundle: dict[str, Any],
    recovered: dict[str, dict[str, dict[str, Any]]],
    history_path: Path,
) -> list[dict[str, Any]]:
    """Fill only missing COM block values from the exact hashed raw history."""

    diagnostics: list[dict[str, Any]] = []
    units = bundle.setdefault("units", {})
    blocks = {
        str(row.get("block_id")): row
        for row in bundle.get("blocks", [])
        if isinstance(row, dict) and row.get("block_id")
    }
    history_hash = sha256(history_path)
    for block_id, observations in recovered.items():
        row = blocks.get(block_id)
        if row is None:
            continue
        raw_paths = row.setdefault("aspen_raw_paths", {})
        raw_values = row.setdefault("aspen_raw_values", {})
        for field, observation in observations.items():
            if finite(row.get(field)) is not None:
                continue
            value = finite(observation.get("value"))
            unit = str(observation.get("unit") or "").strip()
            if value is None or not unit:
                continue
            source_path = f"raw_history:{block_id}:{observation.get('history_label') or field}"
            row[field] = value
            raw_paths[field] = source_path
            raw_values[field] = {
                **observation,
                "value": value,
                "unit": unit,
                "path": source_path,
                "source_file_path": str(history_path),
                "source_file_sha256": history_hash,
            }
            units[f"block.{block_id}.{field}"] = unit
            unit_key = f"block.{field}"
            if not str(units.get(unit_key) or "").strip():
                units[unit_key] = unit
            diagnostics.append({
                "code": "RAW_HISTORY_RESULT_FALLBACK",
                "object": block_id,
                "field": field,
                "unit": unit,
                "raw_value": observation.get("raw_value"),
                "raw_unit": observation.get("raw_unit"),
                "quantity_kind": observation.get("quantity_kind"),
                "status": observation.get("status"),
                "source": observation.get("source"),
                "transform": observation.get("transform"),
                "source_file_sha256": history_hash,
                "basis": observation.get("transform") or "Aspen final UOS raw-history result",
            })
    return diagnostics


def verified_run_status(parsed: dict[str, Any]) -> dict[str, int] | None:
    counts = parsed.get("counts")
    if not parsed.get("found") or not isinstance(counts, dict):
        return None
    required = ("terminal_errors", "severe_errors", "errors", "warnings")
    if any(not isinstance(counts.get(name), int) or counts[name] < 0 for name in required):
        return None
    return {name: counts[name] for name in required}


def classify_run_evidence(
    parsed: dict[str, Any],
    *,
    run_requested: bool,
    raw_history_present: bool,
) -> dict[str, Any]:
    """Separate successful COM execution from formal clean-run evidence."""

    if not run_requested:
        return {
            "status": "NOT_REQUESTED_READ_ONLY",
            "clean": False,
            "run_requested": False,
            "raw_history_present": raw_history_present,
            "counts": None,
            "problem_lines": [],
        }
    counts = verified_run_status(parsed)
    problems = (
        [str(item) for item in parsed.get("problem_lines", [])]
        if isinstance(parsed.get("problem_lines"), list)
        else []
    )
    if counts is None or not raw_history_present:
        status = "RUN_EVIDENCE_MISSING"
        clean = False
    elif any(counts.values()) or problems:
        status = "DIRTY_RUN_EVIDENCE"
        clean = False
    else:
        status = "CLEAN_RUN_EVIDENCE"
        clean = True
    return {
        "status": status,
        "clean": clean,
        "run_requested": True,
        "raw_history_present": raw_history_present,
        "counts": counts,
        "problem_lines": problems[:200],
    }


def create_aspen() -> tuple[Any, str]:
    import win32com.client as win32

    errors: list[str] = []
    for progid in ("Apwn.Document.40.0", "Apwn.Document"):
        try:
            app = win32.DispatchEx(progid)
            try:
                app.SuppressDialogs = True
                app.Visible = False
            except Exception:
                pass
            return app, progid
        except Exception as exc:
            errors.append(f"{progid}: {exc}")
    raise RuntimeError("Aspen COM 创建失败；可改用手动输入或 LLM 辅助模式。" + " | ".join(errors))


def open_case(app: Any, source: Path) -> tuple[str, list[str]]:
    if source.suffix.lower() == ".bkp":
        name = "InitFromArchive2"
        action = lambda: app.InitFromArchive2(str(source.resolve()))
    else:
        name = "InitFromFile2"
        action = lambda: app.InitFromFile2(str(source.resolve()))
    try:
        action()
        return name, []
    except Exception as exc:
        raise RuntimeError(f"Aspen 文件打开失败（{name}）；源文件未被修改：{exc}") from exc


def run_async(app: Any, timeout_s: int) -> dict[str, Any]:
    import pythoncom

    CONTROL_MESSAGES.clear()
    started = time.time()
    result: dict[str, Any] = {"started": now(), "timeout_s": timeout_s}
    try:
        app.Engine.ProcessInput()
        result["process_input"] = "returned"
    except Exception as exc:
        # Some already-processed archives do not expose ProcessInput through
        # every COM build.  Run2 remains the deciding operation, but the
        # diagnostic is retained instead of silently treating input processing
        # as verified.
        result["process_input"] = "unavailable"
        result["process_input_warning"] = _safe_error_text(exc)
    result["run2_return"] = repr(app.Engine.Run2(False))
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pythoncom.PumpWaitingMessages()
        try:
            running = bool(app.Engine.IsRunning)
        except Exception:
            running = False
        if not running:
            for _ in range(20):
                pythoncom.PumpWaitingMessages()
                time.sleep(0.05)
            result.update({"status": "returned", "elapsed_s": round(time.time() - started, 3)})
            return result
        time.sleep(0.25)
    result.update({"status": "timeout", "elapsed_s": round(time.time() - started, 3)})
    return result


def request_run_artifacts(app: Any, work: Path, out_dir: Path) -> dict[str, Any]:
    """Ask Aspen to flush run evidence without touching the source archive.

    Aspen commonly finalizes the current ``.his`` beside a SaveAs target.  The
    target lives only in the isolated worker directory and is removed after the
    history has been copied and hashed.  REP/SUM/MSG exports are retained as
    diagnostics; only a parsed raw history can promote the formal process-basis
    gate.
    """

    result: dict[str, Any] = {"source_mutation_allowed": False}
    capture = work / "RUN_CAPTURE.bkp"
    try:
        app.SaveAs(str(capture.resolve()))
        result["save_as"] = {
            "status": "PASS" if capture.is_file() and capture.stat().st_size > 0 else "NO_FILE",
            "filename": capture.name,
            "size_bytes": capture.stat().st_size if capture.is_file() else 0,
        }
    except Exception as exc:
        result["save_as"] = {"status": "FAILED", "error": _safe_error_text(exc)}

    exports: dict[str, Any] = {}
    for name, export_type, filename in (
        ("report", 2, "raw_aspen_run_report.rep"),
        ("summary", 3, "raw_aspen_run_summary.sum"),
        ("messages", 6, "raw_aspen_run_messages.msg"),
    ):
        target = out_dir / filename
        try:
            app.Export(export_type, str(target.resolve()))
            exports[name] = {
                "status": "PASS" if target.is_file() and target.stat().st_size > 0 else "NO_FILE",
                "filename": target.name,
                "size_bytes": target.stat().st_size if target.is_file() else 0,
            }
        except Exception as exc:
            exports[name] = {"status": "FAILED", "error": _safe_error_text(exc)}
    result["exports"] = exports
    return result


def port_connections(block: Any) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    ports = named_child(block, "Ports")
    inlet: list[str] = []
    outlet: list[str] = []
    detail: list[dict[str, Any]] = []
    for port in node_elements(ports):
        name = node_name(port)
        streams = [node_name(stream) for stream in node_elements(port) if node_name(stream)]
        direction = "unknown"
        if "(IN)" in name.upper():
            inlet.extend(streams)
            direction = "in"
        elif "(OUT)" in name.upper():
            outlet.extend(streams)
            direction = "out"
        detail.append({"port": name, "direction": direction, "streams": streams})
    return list(dict.fromkeys(inlet)), list(dict.fromkeys(outlet)), detail


def connection_records(node: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    connections = named_child(node, "Connections")
    for child in node_elements(connections):
        rows.append({"name": node_name(child), "value": str(node_value(child) or "").strip()})
    return rows


def extract_bundle_with_coverage(
    tree: Any,
    case: dict[str, Any],
    in_units_fields: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    units: dict[str, str] = {}
    warnings: list[dict[str, Any]] = []
    registry_audit = audit_extraction_registry()
    coverage_objects: list[dict[str, Any]] = []
    unmapped_modules: list[dict[str, Any]] = []
    unmapped_stream_record_types: list[dict[str, Any]] = []
    unmapped_nodes: list[dict[str, Any]] = []
    unmapped_fields: list[dict[str, Any]] = []
    discovery_errors: list[dict[str, Any]] = []
    composition_coverage_rows: list[dict[str, Any]] = []
    case_discovery_budget = new_case_discovery_budget()

    def register_unit(
        scope: str,
        field: str,
        unit: str,
        object_id: str,
    ) -> dict[str, Any]:
        resolution = resolve_unit_with_provenance(field, unit, in_units_fields)
        resolved = resolution["unit"]
        fallback_used = bool(resolution["fallback_used"])
        if resolved is None:
            warnings.append({"code": "MISSING_UNITSTRING", "object": object_id, "field": field})
            return resolution
        normalized = resolved
        if fallback_used:
            unit_set_key = RAW_FIELD_TO_IN_UNITS_KEY.get(field)
            from_unit_set = bool(
                unit_set_key
                and isinstance(in_units_fields, dict)
                and str(in_units_fields.get(unit_set_key) or "").strip()
            )
            warnings.append({
                "code": "IN_UNITS_FIELD_FALLBACK" if from_unit_set else "SEMANTIC_UNIT_FALLBACK",
                "object": object_id,
                "field": field,
                "unit": normalized,
                "in_units_field": unit_set_key if from_unit_set else None,
                "basis": (
                    "Aspen BKP-derived global IN-UNITS card"
                    if from_unit_set
                    else "Aspen named-card field semantics"
                ),
            })
        units[f"{scope}.{object_id}.{field}"] = normalized
        key = f"{scope}.{field}"
        existing = units.get(key)
        if existing and existing != normalized:
            warnings.append({"code": "CONFLICTING_UNITSTRING", "object": object_id, "field": field, "units": [existing, normalized]})
            return resolution
        units[key] = normalized
        return resolution

    streams: list[dict[str, Any]] = []
    stream_record_types: dict[str, str] = {}
    stream_root_access_error = False
    try:
        stream_root = tree.FindNode(r"\Data\Streams")
    except Exception as exc:
        stream_root_access_error = True
        stream_root = None
        discovery_errors.append({
            "scope": "stream",
            "object_id": None,
            "module_type": None,
            "path": r"\Data\Streams",
            "unit": None,
            "status": "error",
            "provenance": {
                "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                "operation": "Tree.FindNode",
                "error": _safe_error_text(exc),
            },
        })
    if stream_root is None:
        warnings.append({
            "code": (
                "BLOCKED_COM_TREE_ROOT_ACCESS_ERROR"
                if stream_root_access_error
                else "BLOCKED_COM_TREE_ROOT_MISSING"
            ),
            "scope": "stream",
            "path": r"\Data\Streams",
        })
        stream_nodes: list[Any] = []
    else:
        stream_enumeration = strict_node_elements(
            stream_root,
            ROOT_COLLECTION_MAX_OBJECTS,
        )
        stream_nodes = list(stream_enumeration["rows"])
        for error in stream_enumeration["errors"]:
            discovery_errors.append({
                "scope": "stream",
                "object_id": None,
                "module_type": None,
                "path": r"\Data\Streams",
                "unit": None,
                "status": "error",
                "provenance": {
                    "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                    **error,
                },
            })
        if stream_enumeration["errors"]:
            warnings.append({
                "code": "BLOCKED_COM_TREE_ROOT_ENUMERATION_ERROR",
                "scope": "stream",
                "path": r"\Data\Streams",
                "error_count": len(stream_enumeration["errors"]),
            })
        if stream_enumeration["truncated"]:
            warnings.append({
                "code": "BLOCKED_COM_TREE_ROOT_ENUMERATION_TRUNCATED",
                "scope": "stream",
                "limit": ROOT_COLLECTION_MAX_OBJECTS,
            })
    for stream in stream_nodes:
        stream_id = node_name(stream)
        if not stream_id:
            continue
        record_type, record_type_source = node_record_type(stream)
        stream_record_types[stream_id] = record_type
        stream_compstatus = node_compstatus(stream)
        normalized_record_type = record_type or "UNKNOWN"
        if normalized_record_type == "MATERIAL":
            stream_registry_status = "supported"
            active_stream_registry = STREAM_FIELD_REGISTRY
        elif normalized_record_type in {"HEAT", "WORK", "ENERGY"}:
            stream_registry_status = "not_applicable_non_material_stream"
            active_stream_registry = {}
        else:
            stream_registry_status = "unsupported_stream_record_type"
            active_stream_registry = {}
            unmapped_stream_record_types.append({
                "stream_id": stream_id,
                "record_type": normalized_record_type,
                "status": "unsupported",
                "provenance": {
                    "source": record_type_source,
                    "registry_revision": ASPEN_EXTRACTION_REGISTRY_REVISION,
                },
            })
            warnings.append({
                "code": "UNSUPPORTED_STREAM_RECORD_TYPE",
                "object": stream_id,
                "record_type": normalized_record_type,
                "record_type_source": record_type_source,
                "action": "retain topology/discovery only; do not apply material-stream field coverage",
            })
        row: dict[str, Any] = {
            "stream_id": stream_id,
            "stream_record_type": normalized_record_type,
            "stream_record_type_source": record_type_source,
            "stream_registry_status": stream_registry_status,
            "stream_compstatus": stream_compstatus,
            "connections": connection_records(stream),
            "aspen_raw_paths": {},
            "aspen_raw_values": {},
            "field_observations": {},
        }
        base = rf"\Data\Streams\{stream_id}"
        for field, spec in active_stream_registry.items():
            observation = read_registered_field(
                tree,
                base,
                field,
                spec,
                stream_compstatus,
            )
            value_status = str(observation["detail_status"])
            value_type = observation.get("value_type")
            if observation["status"] != "found":
                unit_resolution = resolve_unit_with_provenance(
                    field,
                    "",
                    in_units_fields,
                )
                observation["unit"] = unit_resolution["unit"]
                observation["unit_provenance"] = unit_resolution
                if value_status == "skipped_owner_no_results":
                    warnings.append({
                        "code": "OUTPUT_SKIPPED_NO_RESULTS",
                        "object": stream_id,
                        "field": field,
                        "path": observation.get("path"),
                        "compstatus": stream_compstatus,
                        "basis": "HAP_COMPSTATUS includes HAP_NORESULTS or HAP_NOT_RUN",
                    })
                elif value_status == "skipped_integer_node_for_continuous_field":
                    warnings.append({
                        "code": "CONTINUOUS_FIELD_SKIPPED_INTEGER_NODE",
                        "object": stream_id,
                        "field": field,
                        "path": observation.get("path"),
                        "value_type": value_type,
                        "basis": "IHNode.ValueType=1 is integer; target field requires a real physical quantity",
                    })
                elif observation["status"] == "error":
                    warnings.append({
                        "code": "COM_FIELD_EXTRACTION_ERROR",
                        "object": stream_id,
                        "field": field,
                        "path": observation.get("path"),
                        "errors": observation.get("errors") or [],
                    })
                row["field_observations"][field] = observation
                continue
            if observation.get("recovered_error_count"):
                warnings.append({
                    "code": "COM_FIELD_EXTRACTION_RECOVERED_AFTER_ERROR",
                    "object": stream_id,
                    "field": field,
                    "selected_path": observation.get("path"),
                    "recovered_error_count": observation["recovered_error_count"],
                    "errors": observation.get("errors") or [],
                })
            value = observation["value"]
            raw_unit = str(observation.get("raw_unit") or "")
            unit_resolution = register_unit("stream", field, raw_unit, stream_id)
            observation["unit"] = unit_resolution["unit"]
            observation["unit_provenance"] = unit_resolution
            row["field_observations"][field] = observation
            row[field] = value
            row["aspen_raw_paths"][field] = observation["path"]
            row["aspen_raw_values"][field] = {
                "value": value,
                "value_type": value_type,
                "unit": raw_unit,
                "resolved_unit": unit_resolution["unit"],
                "unit_provenance": unit_resolution,
                "path": observation["path"],
                "status": value_status,
                "provenance": observation["provenance"],
            }
        solid_fraction = finite(row.get("SFRAC_OUT"))
        solid_fraction_source = "SFRAC_OUT"
        if solid_fraction is None:
            solid_mass = finite(row.get("SOLID_MASSFLMX"))
            total_mass = finite(row.get("MASSFLMX"))
            if solid_mass is not None and total_mass is not None and total_mass > 0:
                solid_fraction = solid_mass / total_mass
                solid_fraction_source = "SOLID_MASSFLMX/MASSFLMX"
        if solid_fraction is not None:
            if 0.0 <= solid_fraction <= 1.0:
                row["solid_fraction"] = solid_fraction
                row["solid_fraction_source"] = solid_fraction_source
                register_unit("stream", "solid_fraction", "-", stream_id)
            else:
                warnings.append({
                    "code": "SOLID_FRACTION_OUT_OF_RANGE",
                    "object": stream_id,
                    "field": solid_fraction_source,
                    "value": solid_fraction,
                    "allowed_range": [0.0, 1.0],
                    "action": "solid_phase_not_inferred",
                })
                solid_fraction = None
        phase_inference = phase_from_vapor_fraction(row.get("VFRAC_OUT"))
        if phase_inference["status"] != "UNAVAILABLE":
            row["phase_inference"] = phase_inference
        inferred_phase = (
            "solid_bearing"
            if solid_fraction is not None and solid_fraction > VAPOR_FRACTION_ENDPOINT_TOLERANCE
            else phase_inference.get("phase")
        )
        if inferred_phase is not None:
            row["phase"] = inferred_phase
            row["phase_origin"] = (
                "EXACT_DERIVATION_FROM_SOLID_FRACTION"
                if inferred_phase == "solid_bearing"
                else "EXACT_DERIVATION_FROM_VFRAC_OUT"
            )
            row["phase_source_field"] = solid_fraction_source if inferred_phase == "solid_bearing" else "VFRAC_OUT"
            if inferred_phase == "liquid" and project_stream_phase_observation(
                row,
                source_field="VOLFLMX",
                target_field="VOLFLMX_LIQ",
                phase="liquid",
            ):
                register_unit("stream", "VOLFLMX_LIQ", units.get("stream.VOLFLMX", ""), stream_id)
            elif inferred_phase == "vapor" and project_stream_phase_observation(
                row,
                source_field="VOLFLMX",
                target_field="VOLFLMX_GAS",
                phase="vapor",
            ):
                register_unit("stream", "VOLFLMX_GAS", units.get("stream.VOLFLMX", ""), stream_id)
        elif phase_inference["status"] == "INVALID_OUT_OF_RANGE":
            warnings.append({
                "code": "VAPOR_FRACTION_OUT_OF_RANGE",
                "object": stream_id,
                "field": "VFRAC_OUT",
                "value": phase_inference.get("raw_vapor_fraction"),
                "allowed_range": [0.0, 1.0],
                "endpoint_tolerance": VAPOR_FRACTION_ENDPOINT_TOLERANCE,
                "action": "phase_not_inferred",
            })
        viscosity_source_field = {
            "liquid": "MUMX_LIQUID",
            "solid_bearing": "MUMX_LIQUID",
            "vapor": "MUMX_VAPOR",
        }.get(str(inferred_phase or ""))
        if viscosity_source_field and viscosity_source_field in row:
            row["MUMX"] = row[viscosity_source_field]
            source_path = row["aspen_raw_paths"].get(viscosity_source_field)
            source_record = row["aspen_raw_values"].get(viscosity_source_field)
            if source_path:
                row["aspen_raw_paths"]["MUMX"] = source_path
            if isinstance(source_record, dict):
                row["aspen_raw_values"]["MUMX"] = {
                    **source_record,
                    "phase_selection": inferred_phase,
                    "phase_source_field": row.get("phase_source_field"),
                }
            source_unit = (
                units.get(f"stream.{stream_id}.{viscosity_source_field}")
                or units.get(f"stream.{viscosity_source_field}")
                or ""
            )
            register_unit("stream", "MUMX", source_unit, stream_id)
            row["dynamic_viscosity_phase_selection"] = {
                "status": "PURE_OR_CONTINUOUS_PHASE_SELECTED",
                "selected_raw_field": viscosity_source_field,
                "phase": inferred_phase,
                "source_path": source_path,
            }
        elif inferred_phase == "two_phase":
            available_phase_fields = [
                field for field in ("MUMX_LIQUID", "MUMX_VAPOR")
                if field in row
            ]
            row["dynamic_viscosity_phase_selection"] = {
                "status": "TWO_PHASE_NO_SINGLE_EFFECTIVE_VISCOSITY",
                "available_raw_fields": available_phase_fields,
                "phase": inferred_phase,
            }
            warnings.append({
                "code": "TWO_PHASE_EFFECTIVE_VISCOSITY_NOT_DEFINED",
                "object": stream_id,
                "available_raw_fields": available_phase_fields,
                "action": (
                    "retain Aspen liquid/vapor phase viscosities separately; "
                    "do not invent a single mixture viscosity"
                ),
            })
        if normalized_record_type == "MATERIAL":
            composition, composition_coverage = extract_stream_composition_with_status(
                tree,
                base,
                stream_id,
                stream_compstatus,
                warnings,
            )
        else:
            composition = []
            composition_coverage = {
                "stream_id": stream_id,
                "stream_record_type": normalized_record_type,
                "status": "NOT_APPLICABLE_NON_MATERIAL_STREAM",
                "requested_vector_count": 0,
                "found_vector_count": 0,
                "component_count": 0,
                "invalid_component_count": 0,
                "enumeration_error_count": 0,
                "candidate_attempts": [],
                "component_observations": [],
                "independent_of_registry_field_hit_rate": True,
            }
        if composition:
            row["composition"] = composition
            row["composition_basis"] = composition[0]["basis"]
        row["composition_extraction"] = composition_coverage
        composition_coverage_rows.append(composition_coverage)
        stream_mapped_paths = [
            path
            for spec in active_stream_registry.values()
            for path in spec["paths"]
        ]
        discovery = discover_object_tree(
            tree,
            scope="stream",
            object_id=stream_id,
            base=base,
            mapped_paths=stream_mapped_paths,
            dynamic_prefixes=(
                STREAM_DYNAMIC_MAPPED_PREFIXES
                if normalized_record_type == "MATERIAL"
                else ()
            ),
            case_budget=case_discovery_budget,
        )
        object_unmapped_nodes = [
            item for item in discovery["nodes"]
            if item["status"] == "unsupported"
            and item["node_kind"] == "container"
        ]
        object_unmapped_fields = [
            item for item in discovery["fields"]
            if item["status"] == "unsupported"
        ]
        unmapped_nodes.extend(object_unmapped_nodes)
        unmapped_fields.extend(object_unmapped_fields)
        discovery_errors.extend(discovery["errors"])
        stream_coverage = summarize_field_coverage(
            row["field_observations"],
            unsupported_count=(
                len(object_unmapped_fields)
                + (1 if stream_registry_status == "unsupported_stream_record_type" else 0)
            ),
            registry_status=stream_registry_status,
            unmapped_field_count=len(object_unmapped_fields),
            discovery_error_count=len(discovery["errors"]),
            discovery_truncated=bool(discovery["truncated"]),
        )
        row["extraction_coverage"] = {
            **stream_coverage,
            "tree_discovery_truncated": discovery["truncated"],
            "unmapped_node_count": len(object_unmapped_nodes),
            "unmapped_field_count": len(object_unmapped_fields),
            "discovery_error_count": len(discovery["errors"]),
        }
        coverage_objects.append({
            "scope": "stream",
            "object_id": stream_id,
            "record_type": normalized_record_type,
            "registry_status": stream_registry_status,
            "composition_extraction": composition_coverage,
            "fields": row["field_observations"],
            **row["extraction_coverage"],
        })
        streams.append(row)

    blocks: list[dict[str, Any]] = []
    equipment_map: list[dict[str, Any]] = []
    block_root_access_error = False
    try:
        block_root = tree.FindNode(r"\Data\Blocks")
    except Exception as exc:
        block_root_access_error = True
        block_root = None
        discovery_errors.append({
            "scope": "block",
            "object_id": None,
            "module_type": None,
            "path": r"\Data\Blocks",
            "unit": None,
            "status": "error",
            "provenance": {
                "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                "operation": "Tree.FindNode",
                "error": _safe_error_text(exc),
            },
        })
    if block_root is None:
        warnings.append({
            "code": (
                "BLOCKED_COM_TREE_ROOT_ACCESS_ERROR"
                if block_root_access_error
                else "BLOCKED_COM_TREE_ROOT_MISSING"
            ),
            "scope": "block",
            "path": r"\Data\Blocks",
        })
        block_nodes: list[Any] = []
    else:
        block_enumeration = strict_node_elements(
            block_root,
            ROOT_COLLECTION_MAX_OBJECTS,
        )
        block_nodes = list(block_enumeration["rows"])
        for error in block_enumeration["errors"]:
            discovery_errors.append({
                "scope": "block",
                "object_id": None,
                "module_type": None,
                "path": r"\Data\Blocks",
                "unit": None,
                "status": "error",
                "provenance": {
                    "source": "ASPEN_LIVE_COM_TREE_DISCOVERY",
                    **error,
                },
            })
        if block_enumeration["errors"]:
            warnings.append({
                "code": "BLOCKED_COM_TREE_ROOT_ENUMERATION_ERROR",
                "scope": "block",
                "path": r"\Data\Blocks",
                "error_count": len(block_enumeration["errors"]),
            })
        if block_enumeration["truncated"]:
            warnings.append({
                "code": "BLOCKED_COM_TREE_ROOT_ENUMERATION_TRUNCATED",
                "scope": "block",
                "limit": ROOT_COLLECTION_MAX_OBJECTS,
            })
    for block in block_nodes:
        block_id = node_name(block)
        if not block_id:
            continue
        block_type, block_type_source = node_record_type(block)
        icon_token = str(node_value(block) or "").strip()
        if not block_type:
            block_type = "UNKNOWN"
        module_spec = BLOCK_MODULE_REGISTRY.get(block_type)
        if block_type_source.startswith("missing:"):
            warnings.append({
                "code": "BLOCKED_MODEL_IDENTITY",
                "object": block_id,
                "icon_token": icon_token,
                "required": "AttributeValue(6):HAP_RECORDTYPE",
            })
        if module_spec is None:
            unsupported_module = {
                "block_id": block_id,
                "module_type": block_type,
                "icon_token": icon_token,
                "block_type_source": block_type_source,
                "status": "unsupported",
                "process_function": None,
                "provenance": {
                    "source": block_type_source,
                    "registry_revision": ASPEN_EXTRACTION_REGISTRY_REVISION,
                    "reason": "module_type_absent_from_BLOCK_MODULE_REGISTRY",
                },
            }
            unmapped_modules.append(unsupported_module)
            warnings.append({
                "code": "UNSUPPORTED_BLOCK_MODULE",
                "object": block_id,
                "block_type": block_type,
                "icon_token": icon_token,
                "action": (
                    "retain topology and raw discovery only; do not assign a "
                    "generic physical-equipment role"
                ),
            })
        inlet_all, outlet_all, port_detail = port_connections(block)
        block_compstatus = node_compstatus(block)
        inlet = [stream for stream in inlet_all if stream_record_types.get(stream, "MATERIAL") == "MATERIAL"]
        outlet = [stream for stream in outlet_all if stream_record_types.get(stream, "MATERIAL") == "MATERIAL"]
        connections = connection_records(block)
        connection_inlet = [item["name"] for item in connections if "(IN)" in item["value"].upper() and stream_record_types.get(item["name"], "MATERIAL") == "MATERIAL"]
        connection_outlet = [item["name"] for item in connections if "(OUT)" in item["value"].upper() and stream_record_types.get(item["name"], "MATERIAL") == "MATERIAL"]
        if connection_inlet and set(connection_inlet) != set(inlet):
            warnings.append({"code": "BLOCKED_CONNECTIVITY_CONFLICT", "object": block_id, "direction": "in", "ports": inlet, "connections": connection_inlet})
        if connection_outlet and set(connection_outlet) != set(outlet):
            warnings.append({"code": "BLOCKED_CONNECTIVITY_CONFLICT", "object": block_id, "direction": "out", "ports": outlet, "connections": connection_outlet})
        row: dict[str, Any] = {
            "block_id": block_id,
            "block_type": block_type,
            "block_type_source": block_type_source,
            "icon_token": icon_token,
            "block_compstatus": block_compstatus,
            "inlet_streams": inlet,
            "outlet_streams": outlet,
            "port_detail": port_detail,
            "connections": connections,
            "aspen_raw_paths": {},
            "aspen_raw_values": {},
            "field_observations": {},
            "module_registry_status": (
                "supported" if module_spec is not None else "unsupported"
            ),
        }
        base = rf"\Data\Blocks\{block_id}"
        active_field_ids = list(BLOCK_COMMON_FIELDS)
        if module_spec is not None:
            active_field_ids.extend(str(field) for field in module_spec["fields"])
        active_field_ids = list(dict.fromkeys(active_field_ids))
        for field in active_field_ids:
            spec = BLOCK_FIELD_REGISTRY[field]
            observation = read_registered_field(
                tree,
                base,
                field,
                spec,
                block_compstatus,
            )
            value_status = str(observation["detail_status"])
            value_type = observation.get("value_type")
            if observation["status"] != "found":
                unit_resolution = resolve_unit_with_provenance(
                    field,
                    "",
                    in_units_fields,
                )
                observation["unit"] = unit_resolution["unit"]
                observation["unit_provenance"] = unit_resolution
                if value_status == "skipped_owner_no_results":
                    warnings.append({
                        "code": "OUTPUT_SKIPPED_NO_RESULTS",
                        "object": block_id,
                        "field": field,
                        "path": observation.get("path"),
                        "compstatus": block_compstatus,
                        "basis": "HAP_COMPSTATUS includes HAP_NORESULTS or HAP_NOT_RUN",
                    })
                elif value_status == "skipped_integer_node_for_continuous_field":
                    warnings.append({
                        "code": "CONTINUOUS_FIELD_SKIPPED_INTEGER_NODE",
                        "object": block_id,
                        "field": field,
                        "path": observation.get("path"),
                        "value_type": value_type,
                        "basis": "IHNode.ValueType=1 is integer; target field requires a real physical quantity",
                    })
                elif observation["status"] == "error":
                    warnings.append({
                        "code": "COM_FIELD_EXTRACTION_ERROR",
                        "object": block_id,
                        "field": field,
                        "path": observation.get("path"),
                        "errors": observation.get("errors") or [],
                    })
                row["field_observations"][field] = observation
                continue
            if observation.get("recovered_error_count"):
                warnings.append({
                    "code": "COM_FIELD_EXTRACTION_RECOVERED_AFTER_ERROR",
                    "object": block_id,
                    "field": field,
                    "selected_path": observation.get("path"),
                    "recovered_error_count": observation["recovered_error_count"],
                    "errors": observation.get("errors") or [],
                })
            value = observation["value"]
            raw_unit = str(observation.get("raw_unit") or "")
            unit_resolution = (
                resolve_unit_with_provenance(field, raw_unit, in_units_fields)
                if field == "BLKSTAT"
                else register_unit("block", field, raw_unit, block_id)
            )
            observation["unit"] = unit_resolution["unit"]
            observation["unit_provenance"] = unit_resolution
            row["field_observations"][field] = observation
            if field == "BLKSTAT":
                row["block_status"] = value
                continue
            row[field] = value
            row["aspen_raw_paths"][field] = observation["path"]
            raw_observation = {
                "value": value,
                "value_type": value_type,
                "unit": raw_unit,
                "resolved_unit": unit_resolution["unit"],
                "unit_provenance": unit_resolution,
                "path": observation["path"],
                "status": value_status,
                "provenance": observation["provenance"],
            }
            quantity_kind = block_field_quantity_kind(field, block_type)
            if quantity_kind:
                raw_observation["quantity_kind"] = quantity_kind
            if field == "WNET":
                raw_observation["semantic_warning"] = (
                    "Aspen WNET is retained as its own raw net-work/utility "
                    "channel and must not be interpreted as pump brake/shaft "
                    "power."
                )
            row["aspen_raw_values"][field] = raw_observation
        block_mapped_paths = [
            path
            for field in active_field_ids
            for path in BLOCK_FIELD_REGISTRY[field]["paths"]
        ]
        discovery = discover_object_tree(
            tree,
            scope="block",
            object_id=block_id,
            base=base,
            mapped_paths=block_mapped_paths,
            module_type=block_type,
            case_budget=case_discovery_budget,
        )
        object_unmapped_nodes = [
            item for item in discovery["nodes"]
            if item["status"] == "unsupported"
            and item["node_kind"] == "container"
        ]
        object_unmapped_fields = [
            item for item in discovery["fields"]
            if item["status"] == "unsupported"
        ]
        unmapped_nodes.extend(object_unmapped_nodes)
        unmapped_fields.extend(object_unmapped_fields)
        discovery_errors.extend(discovery["errors"])
        unsupported_count = len(object_unmapped_fields) + (
            1 if module_spec is None else 0
        )
        block_coverage = summarize_field_coverage(
            row["field_observations"],
            unsupported_count=unsupported_count,
            registry_status=row["module_registry_status"],
            unmapped_field_count=len(object_unmapped_fields),
            discovery_error_count=len(discovery["errors"]),
            discovery_truncated=bool(discovery["truncated"]),
        )
        row["extraction_coverage"] = {
            **block_coverage,
            "tree_discovery_truncated": discovery["truncated"],
            "unmapped_node_count": len(object_unmapped_nodes),
            "unmapped_field_count": len(object_unmapped_fields),
            "discovery_error_count": len(discovery["errors"]),
        }
        coverage_objects.append({
            "scope": "block",
            "object_id": block_id,
            "module_type": block_type,
            "registry_status": row["module_registry_status"],
            "fields": row["field_observations"],
            **row["extraction_coverage"],
        })
        blocks.append(row)
        equipment_map.append({
            "block_id": block_id,
            "equipment_tag": block_id,
            "block_type": block_type,
            "mapping_status": (
                "REGISTERED_PROCESS_MODULE"
                if module_spec is not None
                else "UNSUPPORTED_MODULE"
            ),
            "process_function": (
                str(module_spec["process_function"])
                if module_spec is not None
                else None
            ),
        })

    total_counts = {
        key: sum(
            int((item.get("counts") or {}).get(key, 0))
            for item in coverage_objects
        )
        for key in ("requested", "found", "missing", "error", "unsupported")
    }
    requested = total_counts["requested"]
    registry_field_hit_rate = (
        total_counts["found"] / requested if requested else None
    )
    unsupported_registry_identity_count = (
        len(unmapped_modules) + len(unmapped_stream_record_types)
    )
    composition_requested = sum(
        int(item["requested_vector_count"])
        for item in composition_coverage_rows
    )
    composition_found = sum(
        int(item["found_vector_count"])
        for item in composition_coverage_rows
    )
    composition_failure_rows = [
        item
        for item in composition_coverage_rows
        if int(item["requested_vector_count"]) > int(item["found_vector_count"])
    ]
    composition_coverage = {
        "schema": "aspen-stream-composition-extraction-coverage-v1",
        "status": (
            "NOT_APPLICABLE_NO_MATERIAL_STREAMS"
            if composition_requested == 0
            else (
                "PASS"
                if composition_requested == composition_found
                else "BLOCKED_INCOMPLETE_MATERIAL_STREAM_COMPOSITION"
            )
        ),
        "independent_of_registry_field_hit_rate": True,
        "material_stream_count": composition_requested,
        "non_material_stream_count": sum(
            1
            for item in composition_coverage_rows
            if item["status"] == "NOT_APPLICABLE_NON_MATERIAL_STREAM"
        ),
        "requested_vector_count": composition_requested,
        "found_vector_count": composition_found,
        "missing_vector_count": composition_requested - composition_found,
        "invalid_or_error_vector_count": len(composition_failure_rows),
        "component_count": sum(
            int(item["component_count"])
            for item in composition_coverage_rows
        ),
        "rows": composition_coverage_rows,
    }
    root_diagnostics = [
        item for item in warnings
        if "COM_TREE_ROOT" in str(item.get("code") or "")
    ]
    tree_discovery_truncated_object_count = sum(
        1 for item in coverage_objects
        if item.get("tree_discovery_truncated")
    )
    object_review_count = sum(
        1
        for item in coverage_objects
        if item.get("registry_completeness_status") != "SCORED_REGISTERED_OBJECT"
    )
    case_budget_exhausted = bool(
        case_discovery_budget.get("node_budget_exhausted")
        or case_discovery_budget.get("metadata_budget_exhausted")
    )
    if registry_audit["status"] != "PASS":
        global_completeness_status = "NOT_SCORABLE_REGISTRY_AUDIT_FAILED"
    elif unsupported_registry_identity_count:
        global_completeness_status = "NOT_SCORABLE_UNSUPPORTED_REGISTRY_IDENTITIES"
    elif root_diagnostics:
        global_completeness_status = "NOT_SCORABLE_COM_TREE_ROOT_DIAGNOSTICS"
    elif case_budget_exhausted or tree_discovery_truncated_object_count:
        global_completeness_status = "NOT_SCORABLE_DISCOVERY_TRUNCATED"
    elif total_counts["error"]:
        global_completeness_status = "NOT_SCORABLE_REGISTERED_FIELD_ERRORS"
    elif object_review_count:
        global_completeness_status = "REVIEW_OBJECT_DISCOVERY_GAPS"
    else:
        global_completeness_status = "SCORED_REGISTERED_OBJECTS"
    global_completeness_scorable = (
        global_completeness_status == "SCORED_REGISTERED_OBJECTS"
    )
    global_review_reasons: list[str] = []
    if registry_audit["status"] != "PASS":
        global_review_reasons.append("REGISTRY_AUDIT_FAILED")
    if unsupported_registry_identity_count:
        global_review_reasons.append("UNSUPPORTED_REGISTRY_IDENTITIES")
    if root_diagnostics:
        global_review_reasons.append("COM_TREE_ROOT_DIAGNOSTICS")
    if case_budget_exhausted or tree_discovery_truncated_object_count:
        global_review_reasons.append("DISCOVERY_TRUNCATED")
    if total_counts["error"]:
        global_review_reasons.append("REGISTERED_FIELD_ERRORS")
    if object_review_count and not global_review_reasons:
        global_review_reasons.append("OBJECT_DISCOVERY_GAPS")
    coverage_report = {
        "schema": "aspen-com-extraction-coverage-v1",
        "registry_revision": ASPEN_EXTRACTION_REGISTRY_REVISION,
        "registry_audit": registry_audit,
        "source_contract": {
            "value_source": "active isolated Aspen document live COM tree",
            "read_only_extraction": True,
            "source_bkp_mutated": False,
            "unmapped_value_mode": "METADATA_ONLY_NO_RAW_VALUES",
            "raw_unmapped_values_persisted": False,
            "unmapped_value_metadata": [
                "value_type", "value_size", "value_sha256"
            ],
            "unknown_module_policy": "fail_explicit_without_generic_role",
            "discovery_bounds": {
                "max_nodes_per_object": TREE_DISCOVERY_MAX_NODES_PER_OBJECT,
                "max_depth": TREE_DISCOVERY_MAX_DEPTH,
                "max_nodes_per_case": TREE_DISCOVERY_MAX_NODES_PER_CASE,
                "max_metadata_bytes_per_case": TREE_DISCOVERY_MAX_METADATA_BYTES_PER_CASE,
                "max_name_chars": TREE_DISCOVERY_MAX_NAME_CHARS,
                "max_path_chars": TREE_DISCOVERY_MAX_PATH_CHARS,
                "max_unit_chars": TREE_DISCOVERY_MAX_UNIT_CHARS,
                "max_type_chars": TREE_DISCOVERY_MAX_TYPE_CHARS,
            },
        },
        "counts": total_counts,
        "recovered_error_count": sum(
            int(item.get("recovered_error_count") or 0)
            for item in coverage_objects
        ),
        "registry_field_hit_rate": registry_field_hit_rate,
        "registry_field_hit_percent": (
            round(100.0 * total_counts["found"] / requested, 6)
            if requested
            else None
        ),
        # Compatibility aliases retained for existing readers.
        "coverage_rate": registry_field_hit_rate,
        "coverage_percent": (
            round(100.0 * total_counts["found"] / requested, 6)
            if requested
            else None
        ),
        "registry_completeness_status": global_completeness_status,
        "global_review_required": bool(global_review_reasons),
        "global_review_reasons": global_review_reasons,
        "registry_completeness_rate": (
            registry_field_hit_rate
            if global_completeness_scorable
            else None
        ),
        "requested_status_invariant": (
            total_counts["found"]
            + total_counts["missing"]
            + total_counts["error"]
            == requested
        ),
        "count_semantics": {
            "requested": "registry fields requested for the detected object/module types",
            "found": "requested fields with a finite/scalar COM value",
            "missing": "requested fields absent, undefined, owner-not-run, or an integer placeholder",
            "error": "requested fields whose COM lookup/value access or numeric conversion failed",
            "unsupported": "unmapped module identities plus discovered non-registry leaf fields; not a requested-field subset",
            "composition": "reported independently in composition_extraction; non-material streams are not requested",
        },
        "object_count": len(coverage_objects),
        "object_review_count": object_review_count,
        "objects": coverage_objects,
        "unmapped_modules": unmapped_modules,
        "unmapped_stream_record_types": unmapped_stream_record_types,
        "unmapped_nodes": unmapped_nodes,
        "unmapped_fields": unmapped_fields,
        "composition_extraction": composition_coverage,
        "discovery_errors": discovery_errors,
        "discovery_error_count": len(discovery_errors),
        "root_diagnostic_count": len(root_diagnostics),
        "case_discovery_budget_exhausted": case_budget_exhausted,
        "root_diagnostics": root_diagnostics,
        "tree_discovery_truncated_object_count": tree_discovery_truncated_object_count,
        "case_discovery_budget": dict(case_discovery_budget),
    }
    case["com_extraction_coverage_summary"] = {
        "schema": coverage_report["schema"],
        "registry_revision": ASPEN_EXTRACTION_REGISTRY_REVISION,
        "counts": total_counts,
        "registry_field_hit_rate": coverage_report["registry_field_hit_rate"],
        "registry_field_hit_percent": coverage_report["registry_field_hit_percent"],
        "coverage_rate": coverage_report["coverage_rate"],
        "coverage_percent": coverage_report["coverage_percent"],
        "registry_completeness_status": coverage_report["registry_completeness_status"],
        "global_review_required": coverage_report["global_review_required"],
        "global_review_reasons": coverage_report["global_review_reasons"],
        "registry_completeness_rate": coverage_report["registry_completeness_rate"],
        "unmapped_module_count": len(unmapped_modules),
        "unmapped_stream_record_type_count": len(unmapped_stream_record_types),
        "unmapped_node_count": len(unmapped_nodes),
        "unmapped_field_count": len(unmapped_fields),
        "discovery_error_count": len(discovery_errors),
        "root_diagnostic_count": len(root_diagnostics),
        "tree_discovery_truncated_object_count": tree_discovery_truncated_object_count,
        "case_discovery_budget_exhausted": case_budget_exhausted,
        "composition_extraction": {
            key: value
            for key, value in composition_coverage.items()
            if key != "rows"
        },
    }
    if registry_audit["status"] != "PASS":
        warnings.append({
            "code": "BLOCKED_EXTRACTION_REGISTRY_AUDIT_FAILED",
            "issues": registry_audit["issues"],
        })
    if unsupported_registry_identity_count:
        warnings.append({
            "code": "BLOCKED_COM_UNSUPPORTED_REGISTRY_IDENTITIES",
            "count": unsupported_registry_identity_count,
            "unmapped_module_count": len(unmapped_modules),
            "unmapped_stream_record_type_count": len(unmapped_stream_record_types),
            "action": "extend and audit the explicit registry before equipment selection",
        })
    if total_counts["error"]:
        warnings.append({
            "code": "BLOCKED_COM_REGISTERED_FIELD_ERRORS",
            "count": total_counts["error"],
            "action": "review every registered field error; missing values remain distinct and are not fabricated",
        })
    if unmapped_fields:
        warnings.append({
            "code": "UNMAPPED_COM_TREE_FIELDS_DISCOVERED",
            "count": len(unmapped_fields),
            "objects": sorted({
                f"{item['scope']}:{item['object_id']}"
                for item in unmapped_fields
            }),
            "action": "review extraction_coverage.unmapped_fields and extend the registry explicitly",
        })
    if discovery_errors:
        warnings.append({
            "code": "BLOCKED_COM_TREE_DISCOVERY_ERRORS",
            "count": len(discovery_errors),
            "action": "review extraction_coverage.discovery_errors; no error was converted into a value",
        })
    if coverage_report["tree_discovery_truncated_object_count"]:
        warnings.append({
            "code": "BLOCKED_COM_TREE_DISCOVERY_TRUNCATED",
            "object_count": coverage_report["tree_discovery_truncated_object_count"],
            "limits": coverage_report["source_contract"]["discovery_bounds"],
        })
    if case_budget_exhausted:
        warnings.append({
            "code": "BLOCKED_COM_TREE_CASE_DISCOVERY_BUDGET_EXHAUSTED",
            "budget": dict(case_discovery_budget),
            "action": "treat registry completeness as not scorable and rerun with reviewed limits",
        })

    case["com_extraction_blockers"] = [
        item
        for item in warnings
        if str(item.get("code") or "").startswith("BLOCKED_")
    ]

    for row in streams:
        row.pop("field_observations", None)
        row.pop("extraction_coverage", None)
        row.pop("composition_extraction", None)
        row.pop("stream_registry_status", None)
        if isinstance(row.get("composition"), list):
            row["composition"] = [
                {
                    key: component[key]
                    for key in ("component_id", "fraction", "basis", "source_path")
                    if key in component
                }
                for component in row["composition"]
            ]
    for row in blocks:
        row.pop("field_observations", None)
        row.pop("extraction_coverage", None)
        row.pop("module_registry_status", None)

    legacy_equipment_map: list[dict[str, Any]] = []
    for item in equipment_map:
        legacy_item = {
            "block_id": item["block_id"],
            "equipment_tag": item["equipment_tag"],
        }
        process_function = item.get("process_function")
        if isinstance(process_function, str) and process_function.strip():
            legacy_item["process_function"] = process_function
        legacy_equipment_map.append(legacy_item)

    bundle = {
        "schema": "aspen-equipment-export-v1",
        "case": case,
        "units": units,
        "streams": streams,
        "blocks": blocks,
        "equipment_map": legacy_equipment_map,
    }
    return bundle, warnings, coverage_report


def extract_bundle(
    tree: Any,
    case: dict[str, Any],
    in_units_fields: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Backward-compatible v1 bundle API; detailed coverage is a sidecar."""

    bundle, warnings, _ = extract_bundle_with_coverage(
        tree,
        case,
        in_units_fields,
    )
    return bundle, warnings


def close_aspen(app: Any | None) -> None:
    if app is None:
        return
    try:
        app.Close(False)
    except Exception:
        pass
    try:
        app.Quit()
    except Exception:
        pass


def validate_extraction_coverage_sidecar(
    extraction_coverage: dict[str, Any],
) -> None:
    """Validate the full sidecar contract before any coverage bytes are written."""

    schema_path = PACKAGE_ROOT / "knowledge_graph" / "aspen_extraction_coverage.schema.json"
    if not schema_path.is_file():
        raise RuntimeError(
            "ASPEN_EXTRACTION_COVERAGE_SCHEMA_MISSING:knowledge_graph/"
            "aspen_extraction_coverage.schema.json"
        )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    error = next(iter(validator.iter_errors(extraction_coverage)), None)
    if error is None:
        return
    location = "/".join(str(item) for item in error.absolute_path) or "<root>"
    # Do not include error.instance or the validator's rendered message: an
    # invalid sidecar may contain a secret or an adversarially large value.
    raise ValueError(
        "ASPEN_EXTRACTION_COVERAGE_SCHEMA_INVALID:"
        f"path={_safe_text(location, max_chars=256)};validator={error.validator}"
    )


def write_and_derive(
    bundle: dict[str, Any],
    out_dir: Path,
    extraction_coverage: dict[str, Any] | None = None,
    *,
    allow_derivation: bool = True,
) -> dict[str, Any]:
    bundle_path = out_dir / "aspen_equipment_export.json"
    bundle_to_write = dict(bundle)
    bundle_to_write["case"] = dict(bundle.get("case") or {})
    embedded_coverage = bundle_to_write.pop("extraction_coverage", None)
    if extraction_coverage is None and isinstance(embedded_coverage, dict):
        extraction_coverage = embedded_coverage
    if extraction_coverage is not None and not isinstance(extraction_coverage, dict):
        raise TypeError("ASPEN_EXTRACTION_COVERAGE_SCHEMA_INVALID:expected_object")
    coverage_artifact: dict[str, Any] | None = None
    if isinstance(extraction_coverage, dict):
        validate_extraction_coverage_sidecar(extraction_coverage)
        coverage_path = out_dir / "aspen_extraction_coverage.json"
        coverage_path.write_text(
            json.dumps(
                extraction_coverage,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            ) + "\n",
            encoding="utf-8",
        )
        coverage_artifact = {
            "path": str(coverage_path),
            "sha256": sha256(coverage_path),
        }
        bundle_to_write["case"]["com_extraction_coverage_path"] = coverage_path.name
        bundle_to_write["case"]["com_extraction_coverage_sha256"] = coverage_artifact["sha256"]
    bundle_path.write_text(
        json.dumps(bundle_to_write, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    reloaded = json.loads(bundle_path.read_text(encoding="utf-8"))
    # First persist a topology-safe PFD.  Without the derivation adapter this
    # projection may display only explicitly canonical field names; raw Aspen
    # aliases are never relabelled with target units.  If derivation succeeds,
    # the same file is rewritten below with the unit-normalized cards.
    pfd_mapping = aspen_pfd.build_pfd_mapping(reloaded)
    pfd_path = out_dir / "aspen_pfd_mapping.json"
    pfd_path.write_text(
        json.dumps(pfd_mapping, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if not allow_derivation:
        pfd_summary = aspen_pfd.summarize_pfd_mapping(pfd_mapping)
        output: dict[str, Any] = {
            "bundle": str(bundle_path),
            "derivation": None,
            "result": None,
            "derivation_skipped": "BLOCKED_COM_EXTRACTION",
            "pfd_mapping": str(pfd_path),
            "pfd_mapping_file_sha256": sha256(pfd_path),
            "mapping_sha256": pfd_mapping["mapping_sha256"],
            "pfd_summary": pfd_summary,
        }
        if coverage_artifact is not None:
            output["extraction_coverage"] = coverage_artifact
        return output
    result = derivation.derive_bundle(reloaded, bundle_path)
    result_path = out_dir / "equipment_derivation_result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    canonical_blocks = aspen_pfd.canonical_parameters_by_block(result)
    canonical_streams = aspen_pfd.canonical_parameters_by_stream(result)
    pfd_mapping = aspen_pfd.build_pfd_mapping(
        reloaded,
        canonical_parameters_by_block=canonical_blocks,
        canonical_parameters_by_stream=canonical_streams,
        parameter_normalization_issues=(
            result.get("normalization_diagnostics")
            if isinstance(result.get("normalization_diagnostics"), list)
            else result.get("errors") if isinstance(result.get("errors"), list) else ()
        ),
    )
    pfd_path.write_text(
        json.dumps(pfd_mapping, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    pfd_summary = aspen_pfd.summarize_pfd_mapping(pfd_mapping)
    output = {
        "bundle": str(bundle_path),
        "derivation": str(result_path),
        "result": result,
        "pfd_mapping": str(pfd_path),
        "pfd_mapping_file_sha256": sha256(pfd_path),
        "mapping_sha256": pfd_mapping["mapping_sha256"],
        "pfd_summary": pfd_summary,
    }
    if coverage_artifact is not None:
        output["extraction_coverage"] = coverage_artifact
    return output


def build_run_evidence(case_id: str, history_path: Path, parsed: dict[str, Any], out_dir: Path) -> tuple[Path, dict[str, Any]]:
    if not parsed.get("found") or not history_path.is_file():
        raise ValueError("运行历史中未找到可复核的四类计数。")
    evidence = {
        "schema": "aspen-run-status-evidence-v1",
        "case_id": case_id,
        "raw_history_path": history_path.name,
        "raw_history_sha256": sha256(history_path),
        "raw_history_kind": "aspen_his",
        "run_status": parsed["counts"],
    }
    evidence_path = out_dir / "aspen_run_status_evidence.json"
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return evidence_path, evidence


def run_mock(
    fixture_path: Path,
    out_dir: Path,
    pressure_basis: str | None = None,
    atmospheric_pressure_mpa: float | None = None,
) -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    bundle = fixture["bundle"]
    if pressure_basis is not None:
        bundle["case"]["pressure_basis"] = pressure_basis
    if atmospheric_pressure_mpa is not None:
        bundle["case"]["atmospheric_pressure_mpa"] = atmospheric_pressure_mpa
    else:
        bundle["case"].pop("atmospheric_pressure_mpa", None)
    case_id = str(bundle["case"]["case_id"])
    history = out_dir / "mock_aspen_history.his"
    history.write_text(str(fixture.get("history_text", "NO ERRORS OR WARNINGS GENERATED\n")), encoding="utf-8")
    parsed = parse_aspen_history(history.read_text(encoding="utf-8"))
    evidence_path, evidence = build_run_evidence(case_id, history, parsed, out_dir)
    bundle["case"]["source_case_path"] = str(fixture_path)
    bundle["case"]["run_status"] = evidence["run_status"]
    bundle["case"]["run_status_evidence_path"] = evidence_path.name
    bundle["case"]["run_status_evidence_sha256"] = sha256(evidence_path)
    pipeline = write_and_derive(bundle, out_dir)
    return {
        "schema": "equipment-design-app-aspen-worker-v1",
        "status": "PASS_MOCK",
        "selection_result_available": True,
        "mock": True,
        "history_parse": parsed,
        **pipeline,
    }


def run_real(
    source: Path,
    out_dir: Path,
    pressure_basis: str,
    atmospheric_pressure_mpa: float | None,
    run: bool,
    timeout_s: int,
    ensure_stream_transport: bool = False,
) -> dict[str, Any]:
    import pythoncom
    import win32com.client as win32

    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="EquipmentDesignAspen_"))
    staged = work / f"SOURCE{source.suffix.lower()}"
    shutil.copy2(source, staged)
    source_sha256 = sha256(source)
    staged_copy_sha256 = sha256(staged)
    if source_sha256 != staged_copy_sha256:
        raise RuntimeError("BLOCKED_SOURCE_STAGE_HASH_MISMATCH")
    staged_sidecars = stage_aspen_sidecars(source, staged)
    app = None
    event_handler = None
    transport_manifest: dict[str, Any] | None = None
    transport_manifest_path: Path | None = None
    metadata: dict[str, Any] = {
        "source": str(source),
        "source_sha256": source_sha256,
        "staged": str(staged),
        "staged_copy_sha256": staged_copy_sha256,
        "staged_copy_hash_matches_source": True,
        "staged_sidecars": staged_sidecars,
        "started": now(),
    }
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    lock = WORKSPACE_ROOT / "_aspen_com_global.lock"
    old_cwd = Path.cwd()
    old_temp = os.environ.get("TEMP")
    old_tmp = os.environ.get("TMP")
    with AspenLock(lock):
        try:
            pythoncom.CoInitialize()
            os.chdir(work)
            os.environ["TEMP"] = str(work)
            os.environ["TMP"] = str(work)
            app, progid = create_aspen()
            metadata["progid"] = progid
            open_method, open_errors = open_case(app, staged)
            metadata.update({"open_method": open_method, "open_errors": open_errors})
            event_handler = win32.WithEvents(app, AspenEvents)
            if ensure_stream_transport:
                if not run:
                    raise ValueError(
                        "--ensure-stream-transport requires --run because "
                        "new Aspen properties must be recalculated"
                    )
                (
                    transport_manifest,
                    transport_manifest_path,
                ) = ensure_stream_transport_property_via_com(
                    app,
                    out_dir,
                    source,
                )
                metadata["transport_property_augmentation"] = transport_manifest
                metadata["transport_property_augmentation"][
                    "com_reload_status"
                ] = "NOT_USED_DIRECT_COM_TREE_MUTATION"
            if run:
                metadata["run"] = run_async(app, timeout_s)
                if metadata["run"].get("status") != "returned":
                    raise RuntimeError(f"Aspen 运行未正常返回：{metadata['run']}")
            else:
                metadata["run"] = {"status": "not_requested"}
            case_id = re.sub(r"[^A-Za-z0-9_-]+", "_", source.stem)[:64] or "ASPEN_CASE"
            case: dict[str, Any] = {
                "case_id": case_id,
                "pressure_basis": pressure_basis,
                "source_case_path": str(source),
                "source_case_sha256": sha256(source),
                "run_status": None,
                "run_requested": run,
            }
            if atmospheric_pressure_mpa is not None:
                case["atmospheric_pressure_mpa"] = atmospheric_pressure_mpa

            # Export the active BKP to text first so its own IN-UNITS field
            # names/values are available as a deterministic fallback. Export
            # is read-only for the source archive and does not replace the live
            # COM tree as the primary source of field values or UnitString.
            ascii_export = work / "AFTER.INP"
            in_units_cards: list[dict[str, Any]] = []
            in_units_global: dict[str, Any] | None = None
            try:
                app.Export(4, str(ascii_export.resolve()))
                if ascii_export.is_file():
                    ascii_text = ascii_export.read_text(encoding="utf-8", errors="replace")
                    in_units_cards = parse_in_units_cards(ascii_text)
                    in_units_global = global_in_units(in_units_cards)
                    shutil.copy2(ascii_export, out_dir / "after_run_or_current.inp")
            except Exception as exc:
                metadata["inp_export_warning"] = _safe_error_text(exc)
            case["aspen_in_units_cards"] = in_units_cards
            case["aspen_global_in_units"] = in_units_global
            metadata["aspen_in_units_card_count"] = len(in_units_cards)
            metadata["aspen_global_unit_set"] = (
                str((in_units_global or {}).get("unit_set") or "") or None
            )
            metadata["aspen_global_in_units_fields"] = dict(
                (in_units_global or {}).get("fields") or {}
            )

            # Read the live COM tree before SaveAs changes the active document
            # identity.  The source archive itself remains untouched.
            bundle, extraction_warnings, extraction_coverage = extract_bundle_with_coverage(
                app.Tree,
                case,
                (in_units_global or {}).get("fields") if in_units_global else None,
            )
            metadata["com_extraction_coverage_summary"] = dict(
                bundle["case"].get("com_extraction_coverage_summary") or {}
            )
            transport_verification = verify_stream_transport_properties(bundle)
            (
                transport_manifest,
                transport_manifest_path,
                transport_verification_path,
            ) = persist_stream_transport_evidence(
                out_dir,
                source,
                transport_verification,
                augmentation_requested=ensure_stream_transport,
                run_result=metadata.get("run"),
                manifest=transport_manifest,
            )
            metadata["stream_transport_verification"] = transport_verification
            metadata["stream_transport_verification_path"] = (
                transport_verification_path.name
            )
            metadata["stream_transport_verification_sha256"] = sha256(
                transport_verification_path
            )
            metadata["transport_property_augmentation"] = transport_manifest
            bundle["case"]["stream_transport_verification"] = transport_verification
            bundle["case"]["stream_transport_verification_path"] = (
                transport_verification_path.name
            )
            bundle["case"]["stream_transport_verification_sha256"] = sha256(
                transport_verification_path
            )
            if transport_verification["status"] != "PASS":
                extraction_warnings.append({
                    "code": "BLOCKED_TRANSPORT_PROPERTY_EXTRACTION_AFTER_RUN",
                    "missing_stream_count": transport_verification["missing_stream_count"],
                    "missing": transport_verification["missing"],
                    "augmentation_requested": ensure_stream_transport,
                    "action": (
                        "do_not_default_viscosity; retain blocked state and inspect "
                        "the isolated Aspen property-set rerun evidence"
                    ),
                })
            bundle["case"]["transport_property_augmentation_manifest_path"] = (
                transport_manifest_path.name
            )
            bundle["case"]["transport_property_augmentation_manifest_sha256"] = (
                sha256(transport_manifest_path)
            )
            bundle["case"]["com_extraction_warnings"] = extraction_warnings
            extraction_blockers = list(
                bundle["case"].get("com_extraction_blockers") or []
            )
            bundle["case"]["com_extraction_blockers"] = extraction_blockers
            if run:
                metadata["run_artifact_capture"] = request_run_artifacts(app, work, out_dir)

            # A SaveAs history is commonly finalized only when Aspen closes.
            # Close the isolated document now, then discover evidence before
            # deleting the worker directory.  The finally block remains a
            # harmless second-close guard.
            event_handler = None
            close_aspen(app)
            app = None
            time.sleep(0.1)

            control_path = out_dir / "control_panel_capture.txt"
            control_path.write_text("\n".join(CONTROL_MESSAGES) + ("\n" if CONTROL_MESSAGES else ""), encoding="utf-8")
            histories = sorted(work.rglob("*.his"), key=lambda path: path.stat().st_mtime, reverse=True)
            history_path: Path | None = None
            parsed = {"found": False, "counts": None, "problem_lines": []}
            if histories:
                history_path = out_dir / "raw_aspen_run_history.his"
                shutil.copy2(histories[0], history_path)
                parsed = parse_aspen_history(history_path.read_text(encoding="utf-8", errors="replace"))
                metadata["history_source"] = str(histories[0].relative_to(work))
            elif CONTROL_MESSAGES:
                parsed = parse_aspen_history(control_path.read_text(encoding="utf-8", errors="replace"))
                metadata["history_source"] = "control_panel_capture.txt"

            run_status = verified_run_status(parsed)
            case["run_status"] = run_status
            run_evidence_gate = classify_run_evidence(
                parsed,
                run_requested=run,
                raw_history_present=history_path is not None,
            )
            case["run_evidence_gate"] = run_evidence_gate
            metadata["run_evidence_gate"] = run_evidence_gate
            if history_path is not None and parsed.get("found"):
                history_text = history_path.read_text(encoding="utf-8", errors="replace")
                recovered_results = parse_history_block_results(history_text)
                history_fallback_diagnostics = merge_history_block_results(
                    bundle,
                    recovered_results,
                    history_path,
                )
                extraction_warnings.extend(history_fallback_diagnostics)
                metadata["history_result_fallback_count"] = sum(
                    1
                    for item in history_fallback_diagnostics
                    if item.get("code") == "RAW_HISTORY_RESULT_FALLBACK"
                )
                evidence_path, _ = build_run_evidence(case_id, history_path, parsed, out_dir)
                case["run_status_evidence_path"] = evidence_path.name
                case["run_status_evidence_sha256"] = sha256(evidence_path)

            worker_status = classify_aspen_worker_status(
                transport_verification,
                extraction_blockers,
                extraction_coverage,
            )
            pipeline = write_and_derive(
                bundle,
                out_dir,
                extraction_coverage=extraction_coverage,
                allow_derivation=worker_status != "BLOCKED_COM_EXTRACTION",
            )
            metadata.update({
                "status": worker_status,
                "selection_result_available": worker_selection_result_available(
                    worker_status,
                    pipeline.get("result"),
                ),
                "com_extraction_blockers": extraction_blockers,
                "formal_run_evidence_ready": bool(run_evidence_gate.get("clean")),
                "history_parse": parsed,
                "stream_count": len(bundle["streams"]),
                "block_count": len(bundle["blocks"]),
                "extraction_warning_count": len(extraction_warnings),
                "finished": now(),
            })
            return {"schema": "equipment-design-app-aspen-worker-v1", **metadata, **pipeline}
        finally:
            event_handler = None
            close_aspen(app)
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
            os.chdir(old_cwd)
            if old_temp is None:
                os.environ.pop("TEMP", None)
            else:
                os.environ["TEMP"] = old_temp
            if old_tmp is None:
                os.environ.pop("TMP", None)
            else:
                os.environ["TMP"] = old_tmp
            shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aspen BKP read/run/traverse worker for the equipment-design app.")
    parser.add_argument("--source")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--pressure-basis", choices=["absolute", "gauge"], required=True)
    parser.add_argument("--atmospheric-pressure-mpa", type=float)
    parser.add_argument("--run", action="store_true")
    parser.add_argument(
        "--ensure-stream-transport",
        action="store_true",
        help=(
            "On the isolated Aspen copy, add/request MUMX directly through the "
            "live COM tree and rerun when the report request is absent."
        ),
    )
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--mock-fixture")
    args = parser.parse_args(argv)
    if args.pressure_basis == "gauge" and args.atmospheric_pressure_mpa is None:
        parser.error("--pressure-basis gauge requires --atmospheric-pressure-mpa")
    if args.atmospheric_pressure_mpa is not None and args.atmospheric_pressure_mpa <= 0:
        parser.error("--atmospheric-pressure-mpa must be greater than zero")
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "worker_result.json"
    result: dict[str, Any]
    rc = 0
    try:
        if args.mock_fixture:
            result = run_mock(
                Path(args.mock_fixture).resolve(),
                out_dir,
                args.pressure_basis,
                args.atmospheric_pressure_mpa,
            )
        else:
            if not args.source:
                raise ValueError("缺少 --source。")
            source = Path(args.source).resolve()
            if not source.is_file():
                raise FileNotFoundError(source)
            if source.suffix.lower() not in {".bkp", ".apw", ".inp"}:
                raise ValueError("自动导入支持 .bkp、.apw、.inp；其他情况请用手动模式。")
            result = run_real(
                source,
                out_dir,
                args.pressure_basis,
                args.atmospheric_pressure_mpa,
                args.run,
                max(10, min(args.timeout, 7200)),
                args.ensure_stream_transport,
            )
    except Exception as exc:
        result = {
            "schema": "equipment-design-app-aspen-worker-v1",
            "status": "FAILED",
            "selection_result_available": False,
            "error": _safe_error_text(exc),
            "fallback": "manual_or_llm_mode",
            "traceback": _safe_text(traceback.format_exc(), max_chars=4096),
        }
        rc = 2
    if rc == 0 and result.get("status") not in {"PASS", "PASS_MOCK"}:
        rc = 3
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps(
        {"status": result.get("status"), "worker_result": str(result_path)},
        ensure_ascii=False,
        allow_nan=False,
    ))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
