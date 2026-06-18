"""Bioadhesives workcell helpers.

This module keeps the operator-facing workcell script small: it turns a
per-well workflow table into an :class:`Experiment`, runs preflight health
checks, and exports one joined CSV row per well from the SQLite result store.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .config import ControllerConfig
from .experiment import Experiment

_WELL_RE = re.compile(r"^[A-Za-z]+[0-9]+$")
_NESTED_PARAM_KEYS = (
    "sharc_scalar",
    "sharc_method_kwargs",
    "asmi_scalar",
    "asmi_method_kwargs",
)


@dataclass(frozen=True)
class WorkflowWell:
    """One target well in the bioadhesives workflow.

    Use ``sharc_*`` and ``asmi_*`` mappings for protocol fields that should vary
    by well. ``*_scalar`` maps to fields directly on the CubOS ``measure`` body;
    ``*_method_kwargs`` maps to the nested ``method_kwargs`` body.
    """

    target_well: str
    source_well: str = "A1"
    uv_exposure_s: float = 11.0
    formulation: str | None = None
    opentrons: Mapping[str, Any] = field(default_factory=dict)
    sharc_scalar: Mapping[str, Any] = field(default_factory=dict)
    sharc_method_kwargs: Mapping[str, Any] = field(default_factory=dict)
    asmi_scalar: Mapping[str, Any] = field(default_factory=dict)
    asmi_method_kwargs: Mapping[str, Any] = field(default_factory=dict)
    params: Mapping[str, Any] = field(default_factory=dict)

    def to_params(self, shared_params: Mapping[str, Any]) -> dict[str, Any]:
        params = _merge_nested_params(shared_params)
        params.update(dict(self.opentrons))
        params.update(dict(self.params))
        _merge_nested_params_into(params, "sharc_scalar", self.sharc_scalar)
        _merge_nested_params_into(params, "sharc_method_kwargs", self.sharc_method_kwargs)
        _merge_nested_params_into(params, "asmi_scalar", self.asmi_scalar)
        _merge_nested_params_into(params, "asmi_method_kwargs", self.asmi_method_kwargs)
        params["source_well"] = _normalize_well(self.source_well)
        params["formulation"] = self.formulation or params["source_well"]
        params["uv_exposure_s"] = self.uv_exposure_s
        return params

    def raw(self) -> dict[str, Any]:
        return {
            "target_well": _normalize_well(self.target_well),
            "source_well": _normalize_well(self.source_well),
            "uv_exposure_s": self.uv_exposure_s,
            "formulation": self.formulation,
            "opentrons": dict(self.opentrons),
            "sharc_scalar": dict(self.sharc_scalar),
            "sharc_method_kwargs": dict(self.sharc_method_kwargs),
            "asmi_scalar": dict(self.asmi_scalar),
            "asmi_method_kwargs": dict(self.asmi_method_kwargs),
            "params": dict(self.params),
        }


def build_workflow_experiment(
    *,
    experiment_id: str,
    wells: Sequence[WorkflowWell],
    shared_params: Mapping[str, Any],
    final_return_location: str,
) -> Experiment:
    """Build the ordered :class:`Experiment` consumed by ``run_experiment``."""
    if not wells:
        raise ValueError("workflow needs at least one well")
    ordered_wells: list[str] = []
    params: dict[str, dict[str, Any]] = {}
    for spec in wells:
        well = _normalize_well(spec.target_well)
        if well in params:
            raise ValueError(f"workflow well {well} listed twice")
        ordered_wells.append(well)
        params[well] = spec.to_params(shared_params)
    return Experiment(
        id=experiment_id,
        wells=ordered_wells,
        params=params,
        defaults=dict(shared_params),
        final_well_return_location=final_return_location,
        raw={
            "experiment_id": experiment_id,
            "workflow_wells": [spec.raw() for spec in wells],
            "shared_params": _json_ready(shared_params),
            "final_well_return_location": final_return_location,
        },
    )


@dataclass(frozen=True)
class HealthTarget:
    name: str
    call: Callable[[], Mapping[str, Any]]


@dataclass(frozen=True)
class HealthResult:
    name: str
    ok: bool
    detail: str
    payload: Mapping[str, Any] | None = None
    error: str | None = None


def controller_health_targets(
    cfg: ControllerConfig,
    *,
    include_opentrons: bool,
) -> list[HealthTarget]:
    """Return the device health checks needed for this run."""
    targets = [
        HealthTarget("SHARC station", lambda: cfg.station_bundle("sharc").client.health()),
        HealthTarget("ASMI station", lambda: cfg.station_bundle("asmi").client.health()),
        HealthTarget("arm worker", lambda: cfg.arm_client().health()),
    ]
    if include_opentrons:
        targets.append(HealthTarget("Opentrons Flex", lambda: cfg.opentrons_client().health()))
    return targets


def run_health_checks(targets: Sequence[HealthTarget]) -> list[HealthResult]:
    results: list[HealthResult] = []
    for target in targets:
        try:
            payload = target.call()
        except Exception as exc:  # noqa: BLE001 - health report should name every failed device
            results.append(
                HealthResult(
                    name=target.name,
                    ok=False,
                    detail=f"offline: {type(exc).__name__}: {exc}",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        ok, detail = _health_payload_status(payload)
        results.append(HealthResult(name=target.name, ok=ok, detail=detail, payload=payload))
    return results


def format_health_report(results: Sequence[HealthResult]) -> str:
    lines = ["Preflight health check:"]
    for result in results:
        mark = "✅" if result.ok else "❌"
        lines.append(f"  {mark} {result.name:<14} {result.detail}")
    return "\n".join(lines)


def failed_health_names(results: Sequence[HealthResult]) -> list[str]:
    return [result.name for result in results if not result.ok]


def prompt_ready(experiment: Experiment, *, input_fn: Callable[[str], str] = input) -> bool:
    print()
    print(f"Ready to run {experiment.id} on wells: {', '.join(experiment.wells)}")
    try:
        answer = input_fn("Type 'ready' to start, or anything else to abort: ")
    except EOFError:
        return False
    return answer.strip().lower() == "ready"


EXPORT_COLUMNS = [
    "experiment_id",
    "well",
    "well_status",
    "well_error",
    "source_well",
    "formulation",
    "volume_ul",
    "uv_exposure_s",
    "uv_intensity",
    "asmi_measurement_height",
    "asmi_indentation_limit_height",
    "asmi_step_size",
    "asmi_force_limit",
    "asmi_measure_with_return",
    "opentrons_run_id",
    "opentrons_success",
    "opentrons_payload_json",
    "sharc_run_id",
    "sharc_success",
    "sharc_result_path",
    "sharc_run_dir",
    "sharc_cure_json",
    "sharc_payload_json",
    "asmi_run_id",
    "asmi_success",
    "asmi_result_path",
    "asmi_run_dir",
    "asmi_indentation_json",
    "asmi_measurements_json",
    "asmi_force_distance_json",
    "asmi_force_recording_json",
    "asmi_payload_json",
]


def export_joined_well_csv(
    db_path: str | Path,
    experiment_id: str,
    csv_path: str | Path,
) -> Path:
    """Write one joined Opentrons/SHARC/ASMI CSV row per well."""
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    records = load_joined_well_records(db_path, experiment_id)
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    return csv_path


def load_joined_well_records(db_path: str | Path, experiment_id: str) -> list[dict[str, str]]:
    """Read SQLite and return joined export rows without touching hardware."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        well_rows = con.execute(
            "SELECT rowid, well, status, params_json, error FROM wells "
            "WHERE experiment_id=? ORDER BY rowid",
            (experiment_id,),
        ).fetchall()
        run_rows = con.execute(
            "SELECT * FROM runs WHERE experiment_id=? "
            "AND kind IN ('opentrons_fill', 'sharc', 'asmi') "
            "ORDER BY started_at",
            (experiment_id,),
        ).fetchall()
    finally:
        con.close()

    runs_by_well: dict[str, dict[str, sqlite3.Row]] = {}
    for run in run_rows:
        if run["well"] is None:
            continue
        runs_by_well.setdefault(run["well"], {})[run["kind"]] = run

    return [
        _joined_record(experiment_id, well_row, runs_by_well.get(well_row["well"], {}))
        for well_row in well_rows
    ]


def _joined_record(
    experiment_id: str,
    well_row: sqlite3.Row,
    runs: Mapping[str, sqlite3.Row],
) -> dict[str, str]:
    params = _loads(well_row["params_json"], {})
    opentrons = runs.get("opentrons_fill")
    sharc = runs.get("sharc")
    asmi = runs.get("asmi")

    opentrons_payload = _loads(opentrons["result_json"], None) if opentrons else None
    sharc_payload = _loads(sharc["result_json"], None) if sharc else None
    sharc_artifacts = _loads(sharc["artifacts_json"], {}) if sharc else {}
    asmi_payload = _loads(asmi["result_json"], None) if asmi else None
    asmi_artifacts = _loads(asmi["artifacts_json"], {}) if asmi else {}

    sharc_cure = _extract_station_payload(sharc_payload, preferred_keys=("exposure_time", "readings", "mean_n"))
    asmi_indentation = _extract_station_payload(
        asmi_payload,
        preferred_keys=(
            "measurements",
            "data_points",
            "force_exceeded",
            "z_positions",
            "raw_forces",
            "corrected_forces",
        ),
    )
    asmi_measurements = _asmi_measurements(asmi_indentation)

    return {
        "experiment_id": experiment_id,
        "well": well_row["well"],
        "well_status": _cell(well_row["status"]),
        "well_error": _cell(well_row["error"]),
        "source_well": _cell(params.get("source_well")),
        "formulation": _cell(params.get("formulation")),
        "volume_ul": _cell(params.get("volume_ul")),
        "uv_exposure_s": _cell(params.get("uv_exposure_s")),
        "uv_intensity": _cell(_param_value(params, direct=("uv_intensity", "sharc_intensity"),
                                          nested=("sharc_method_kwargs", "intensity"))),
        "asmi_measurement_height": _cell(_param_value(
            params,
            direct=("asmi_measurement_height", "measurement_height"),
            nested=("asmi_scalar", "measurement_height"),
        )),
        "asmi_indentation_limit_height": _cell(_param_value(
            params,
            direct=("asmi_indentation_limit_height", "indentation_limit_height"),
            nested=("asmi_scalar", "indentation_limit_height"),
        )),
        "asmi_step_size": _cell(_param_value(
            params,
            direct=("asmi_step_size", "step_size"),
            nested=("asmi_method_kwargs", "step_size"),
        )),
        "asmi_force_limit": _cell(_param_value(
            params,
            direct=("asmi_force_limit", "force_limit"),
            nested=("asmi_method_kwargs", "force_limit"),
        )),
        "asmi_measure_with_return": _cell(_param_value(
            params,
            direct=("asmi_measure_with_return", "measure_with_return"),
            nested=("asmi_method_kwargs", "measure_with_return"),
        )),
        "opentrons_run_id": _run_cell(opentrons, "run_id"),
        "opentrons_success": _success_cell(opentrons),
        "opentrons_payload_json": _json_cell(opentrons_payload),
        "sharc_run_id": _run_cell(sharc, "run_id"),
        "sharc_success": _success_cell(sharc),
        "sharc_result_path": _cell(sharc_artifacts.get("result_path")),
        "sharc_run_dir": _cell(sharc_artifacts.get("run_dir")),
        "sharc_cure_json": _json_cell(sharc_cure),
        "sharc_payload_json": _json_cell(sharc_payload),
        "asmi_run_id": _run_cell(asmi, "run_id"),
        "asmi_success": _success_cell(asmi),
        "asmi_result_path": _cell(asmi_artifacts.get("result_path")),
        "asmi_run_dir": _cell(asmi_artifacts.get("run_dir")),
        "asmi_indentation_json": _json_cell(asmi_indentation),
        "asmi_measurements_json": _json_cell(asmi_measurements),
        "asmi_force_distance_json": _json_cell(_force_distance(asmi_indentation)),
        "asmi_force_recording_json": _json_cell(_force_recording(asmi_indentation)),
        "asmi_payload_json": _json_cell(asmi_payload),
    }


def _merge_nested_params(shared_params: Mapping[str, Any]) -> dict[str, Any]:
    params = dict(shared_params)
    for key in _NESTED_PARAM_KEYS:
        if key in params and params[key] is not None:
            params[key] = _require_mapping(params[key], key)
    return params


def _merge_nested_params_into(params: dict[str, Any], key: str, value: Mapping[str, Any]) -> None:
    if not value:
        return
    merged = dict(params.get(key) or {})
    merged.update(dict(value))
    params[key] = merged


def _normalize_well(well: str) -> str:
    well = well.strip().upper()
    if not _WELL_RE.match(well):
        raise ValueError(f"not a well id: {well!r}")
    return well


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return dict(value)


def _health_payload_status(payload: Mapping[str, Any]) -> tuple[bool, str]:
    if not isinstance(payload, Mapping):
        return False, f"unexpected response: {payload!r}"
    status = str(payload.get("status") or "").lower()
    if payload.get("busy"):
        current = payload.get("current_run_id") or "unknown run"
        return False, f"online but busy with {current}"
    ok = status in ("", "ok", "running", "healthy")
    return ok, _health_detail(payload)


def _health_detail(payload: Mapping[str, Any]) -> str:
    pieces = []
    status = payload.get("status")
    if status:
        pieces.append(f"status={status}")
    station_id = payload.get("station_id") or payload.get("device")
    if station_id:
        pieces.append(f"id={station_id}")
    cubos_version = payload.get("cubos_version")
    if cubos_version:
        pieces.append(f"cubos={cubos_version}")
    return " ".join(pieces) or "reachable"


def _json_ready(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _json_cell(value: Any) -> str:
    if value is None or value == {} or value == []:
        return ""
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _run_cell(row: sqlite3.Row | None, key: str) -> str:
    return "" if row is None else _cell(row[key])


def _success_cell(row: sqlite3.Row | None) -> str:
    if row is None or row["success"] is None:
        return ""
    return "true" if bool(row["success"]) else "false"


def _param_value(
    params: Mapping[str, Any],
    *,
    direct: Sequence[str],
    nested: tuple[str, str],
) -> Any:
    for key in direct:
        if key in params:
            return params[key]
    nested_value = params.get(nested[0])
    if isinstance(nested_value, Mapping) and nested[1] in nested_value:
        return nested_value[nested[1]]
    return None


def _extract_station_payload(value: Any, *, preferred_keys: Sequence[str]) -> Any:
    if isinstance(value, list):
        for item in value:
            found = _extract_station_payload(item, preferred_keys=preferred_keys)
            if isinstance(found, Mapping) and any(key in found for key in preferred_keys):
                return found
        for item in value:
            if isinstance(item, Mapping):
                return item
        return None
    if isinstance(value, Mapping):
        if any(key in value for key in preferred_keys):
            return value
        for child in value.values():
            found = _extract_station_payload(child, preferred_keys=preferred_keys)
            if isinstance(found, Mapping) and any(key in found for key in preferred_keys):
                return found
        return value
    return None


def _asmi_measurements(indentation: Any) -> Any:
    if not isinstance(indentation, Mapping):
        return None
    measurements = indentation.get("measurements")
    if measurements is not None:
        return measurements
    if "z_positions" in indentation:
        return {
            "z_positions": indentation.get("z_positions"),
            "raw_forces": indentation.get("raw_forces"),
            "corrected_forces": indentation.get("corrected_forces"),
            "sample_timestamps": indentation.get("sample_timestamps"),
            "directions": indentation.get("directions"),
        }
    return None


def _force_distance(indentation: Any) -> Any:
    measurements = _asmi_measurements(indentation)
    if isinstance(measurements, list):
        return [
            {
                "z_mm": sample.get("z_mm"),
                "raw_force_n": sample.get("raw_force_n"),
                "corrected_force_n": sample.get("corrected_force_n"),
            }
            for sample in measurements
            if isinstance(sample, Mapping)
        ]
    if isinstance(measurements, Mapping):
        z_positions = measurements.get("z_positions") or []
        raw_forces = measurements.get("raw_forces") or []
        corrected_forces = measurements.get("corrected_forces") or []
        return [
            {
                "z_mm": z,
                "raw_force_n": raw_forces[i] if i < len(raw_forces) else None,
                "corrected_force_n": corrected_forces[i] if i < len(corrected_forces) else None,
            }
            for i, z in enumerate(z_positions)
        ]
    return None


def _force_recording(indentation: Any) -> Any:
    measurements = _asmi_measurements(indentation)
    if isinstance(measurements, list):
        return [
            {
                "timestamp": sample.get("timestamp"),
                "raw_force_n": sample.get("raw_force_n"),
                "corrected_force_n": sample.get("corrected_force_n"),
                "direction": sample.get("direction"),
            }
            for sample in measurements
            if isinstance(sample, Mapping)
        ]
    if isinstance(measurements, Mapping):
        timestamps = measurements.get("sample_timestamps") or []
        raw_forces = measurements.get("raw_forces") or []
        corrected_forces = measurements.get("corrected_forces") or []
        directions = measurements.get("directions") or []
        count = max(len(timestamps), len(raw_forces), len(corrected_forces), len(directions))
        return [
            {
                "timestamp": timestamps[i] if i < len(timestamps) else None,
                "raw_force_n": raw_forces[i] if i < len(raw_forces) else None,
                "corrected_force_n": corrected_forces[i] if i < len(corrected_forces) else None,
                "direction": directions[i] if i < len(directions) else None,
            }
            for i in range(count)
        ]
    return None


__all__ = [
    "EXPORT_COLUMNS",
    "HealthResult",
    "HealthTarget",
    "WorkflowWell",
    "build_workflow_experiment",
    "controller_health_targets",
    "export_joined_well_csv",
    "failed_health_names",
    "format_health_report",
    "load_joined_well_records",
    "prompt_ready",
    "run_health_checks",
]
