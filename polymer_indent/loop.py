"""The per-well experiment loop.

For each well:
    1.  Opentrons fill                                (placeholder client)
    2a. SHARC: home-only protocol (park gantry before deposit)
    2.  arm transfer  opentrons -> uv_station
    3.  SHARC UV cure   (send gantry+deck+well-swapped protocol to the SHARC Pi)
    4a. ASMI:  home-only protocol (park gantry before deposit)
    4.  arm transfer  uv_station -> asmi
    5.  ASMI indentation (send gantry+deck+well-swapped protocol to the ASMI Pi)
    6.  arm transfer  asmi -> {storage_end if last well else opentrons}

Every step writes its own row to the result store immediately after the device
returns — including failure rows, written before the exception propagates — so
the audit trail in ``results/polymer_indent.db`` is never missing a leg.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

from .clients import ArmRailClient, CubOSStationClient, OpentronsClient
from .experiment import Experiment
from .protocol_render import apply_overrides, render_protocol
from .results import ResultStore

log = logging.getLogger("polymer_indent.loop")


@dataclass
class StationBundle:
    """A station client + its frozen base-protocol text."""

    client: CubOSStationClient
    base_protocol_yaml: str


@dataclass(frozen=True)
class RunSafetyChecks:
    """Operator-confirmed safety stops for hardware-sensitive run segments."""

    confirm: Callable[[str], bool]
    asmi_position_check_well: str = "A1"
    asmi_position_check_height: float = 10.0
    asmi_slide_abort_return_location: str = "opentrons"


def run_experiment(
    experiment: Experiment,
    *,
    opentrons: OpentronsClient,
    arm: ArmRailClient,
    sharc: StationBundle,
    asmi: StationBundle,
    results: ResultStore,
    mock_mode: bool = False,
    mock_modes: Optional[dict] = None,
    resume: bool = False,
    only_wells: Optional[Sequence[str]] = None,
    continue_on_error: bool = False,
    safety_checks: RunSafetyChecks | None = None,
) -> int:
    """Run the experiment. Returns the number of wells that failed.

    Args:
        mock_mode: default for every device when not in ``mock_modes``.
        mock_modes: per-device overrides, keys ``"sharc"``, ``"asmi"``, ``"arm"``
            (e.g. ``{"asmi": True}`` runs SHARC + arm real, ASMI in mock).
        resume: skip wells already marked ``done`` in the result store.
        only_wells: if given, only run these wells (still in declared order).
        continue_on_error: keep going after a well fails (default: stop).
    """
    results.start_experiment(experiment)

    overrides = mock_modes or {}
    sharc_mock = bool(overrides.get("sharc", mock_mode))
    asmi_mock  = bool(overrides.get("asmi",  mock_mode))
    arm_mock   = bool(overrides.get("arm",   mock_mode))
    log.info("run_experiment: mock_mode default=%s   per-device sharc=%s asmi=%s arm=%s",
             mock_mode, sharc_mock, asmi_mock, arm_mock)

    wells = list(_select_wells(experiment, only_wells))
    already_done = results.done_wells(experiment.id) if resume else set()
    experiment_id = experiment.id

    failed = 0
    for well in wells:
        if well in already_done:
            log.info("well %s: already done — skipping (resume)", well)
            continue
        params = experiment.well_params(well)
        step_id = f"{experiment_id}:{well}"
        return_location = experiment.return_location(well)
        try:
            # 1. Opentrons fill
            _record_step(
                results, run_id=f"{step_id}:fill", experiment_id=experiment_id, well=well,
                kind="opentrons_fill", station="opentrons",
                call=lambda: opentrons.run_fill(
                    well=well,
                    volume_ul=params.get("volume_ul", 350),
                    source_well=params.get("source_well"),
                    formulation=params.get("formulation"),
                    run_id=f"{step_id}:fill",
                    flow_rate_ul_min=params.get("flow_rate_ul_min", 150),
                    air_expulsion_ul=params.get("air_expulsion_ul", 20),
                    tip_lift_height_mm=params.get("tip_lift_height_mm", 8),
                    tip_rack_slot=params.get("tip_rack_slot", "A2"),
                    tube_rack_slot=params.get("tube_rack_slot", "B2"),
                    plate_slot=params.get("plate_slot", "D2"),
                    plate_labware=params.get("plate_labware", "corning_96_wellplate_360ul_flat"),
                ),
            )

            # 2a. Park the SHARC gantry at home so the arm can deposit safely.
            _home_station(sharc, "sharc", results,
                          experiment_id=experiment_id, well=well, step_id=step_id,
                          mock_mode=sharc_mock)

            # 2. arm: opentrons -> uv_station
            _transfer(arm, results, experiment_id, well, step_id, "move-to-sharc",
                      "opentrons", "uv_station", mock_mode=arm_mock)

            # 3. SHARC UV cure
            sharc_run_id = f"{step_id}:sharc"
            sharc_protocol_yaml = render_station_protocol(sharc, "sharc", well, params)
            _record_step(
                results, run_id=sharc_run_id, experiment_id=experiment_id, well=well,
                kind="sharc", station="sharc", protocol_yaml=sharc_protocol_yaml,
                call=lambda: sharc.client.run_protocol(
                    run_id=sharc_run_id,
                    protocol_yaml=sharc_protocol_yaml,
                    metadata={"experiment_id": experiment_id, "well": well, "step": "sharc"},
                    mock_mode=sharc_mock,
                ),
            )

            # 4a. Park the ASMI gantry at home so the arm can deposit safely.
            _home_station(asmi, "asmi", results,
                          experiment_id=experiment_id, well=well, step_id=step_id,
                          mock_mode=asmi_mock)

            # 4. arm: uv_station -> asmi
            if safety_checks:
                _transfer(arm, results, experiment_id, well, step_id, "move-to-asmi-slide-out",
                          "uv_station", "asmi_pre_push", mock_mode=arm_mock)
                if not safety_checks.confirm(
                    "Plate is at ASMI slide-out. Type 'yes' to push into ASMI; "
                    "anything else returns the plate to Opentrons and aborts: "
                ):
                    _transfer(
                        arm, results, experiment_id, well, step_id,
                        "abort-asmi-slide-out-return",
                        "asmi_pre_push",
                        safety_checks.asmi_slide_abort_return_location,
                        mock_mode=arm_mock,
                        skip_safe_prelude=True,
                    )
                    raise RuntimeError("operator aborted before ASMI slide-in")
                _transfer(arm, results, experiment_id, well, step_id, "move-to-asmi",
                          "asmi_pre_push", "asmi", mock_mode=arm_mock,
                          skip_safe_prelude=True)
            else:
                _transfer(arm, results, experiment_id, well, step_id, "move-to-asmi",
                          "uv_station", "asmi", mock_mode=arm_mock)

            if safety_checks:
                _run_asmi_position_check(
                    asmi,
                    results,
                    experiment_id=experiment_id,
                    well=well,
                    step_id=step_id,
                    target_well=safety_checks.asmi_position_check_well,
                    measurement_height=safety_checks.asmi_position_check_height,
                    mock_mode=asmi_mock,
                )
                if not safety_checks.confirm(
                    f"ASMI is positioned at {safety_checks.asmi_position_check_well} "
                    f"+{safety_checks.asmi_position_check_height:g} mm. "
                    "Type 'yes' to continue with indentation; anything else aborts: "
                ):
                    raise RuntimeError("operator aborted after ASMI position check")

            # 5. ASMI indentation
            asmi_run_id = f"{step_id}:asmi"
            asmi_protocol_yaml = render_station_protocol(asmi, "asmi", well, params)
            _record_step(
                results, run_id=asmi_run_id, experiment_id=experiment_id, well=well,
                kind="asmi", station="asmi", protocol_yaml=asmi_protocol_yaml,
                call=lambda: asmi.client.run_protocol(
                    run_id=asmi_run_id,
                    protocol_yaml=asmi_protocol_yaml,
                    metadata={"experiment_id": experiment_id, "well": well, "step": "asmi"},
                    mock_mode=asmi_mock,
                ),
            )

            # 6. arm: asmi -> {storage_end | opentrons}
            _transfer(arm, results, experiment_id, well, step_id, "return",
                      "asmi", return_location, mock_mode=arm_mock)

            results.set_well_status(experiment_id, well, "done")
            log.info("well %s: done", well)
        except Exception as exc:  # noqa: BLE001 — one bad well shouldn't be silent
            failed += 1
            results.set_well_status(experiment_id, well, "failed", error=repr(exc))
            log.exception("well %s: FAILED — %s", well, exc)
            if not continue_on_error:
                results.finish_experiment(experiment_id, "failed")
                raise

    status = "failed" if failed else "completed"
    results.finish_experiment(experiment_id, status)
    log.info("experiment %s: %s (%d/%d wells failed)",
             experiment_id, status, failed, len(wells))
    return failed


def _require_success(payload, kind: str) -> bool:
    """Read ``payload['success']`` strictly — missing key is a contract violation."""
    if "success" not in payload:
        raise RuntimeError(f"{kind} response missing 'success' field: {payload!r}")
    return bool(payload["success"])


def _record_step(results, *, run_id, experiment_id, well, kind, station, call,
                 protocol_yaml=None):
    """Run ``call``; persist a runs row (success or failure) before returning/re-raising."""
    started = time.time()
    try:
        resp = call()
    except Exception as exc:
        results.record_run(
            run_id=run_id, experiment_id=experiment_id, well=well,
            kind=kind, station=station,
            success=False, started_at=started, finished_at=time.time(),
            protocol_yaml=protocol_yaml,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    results.record_run(
        run_id=run_id, experiment_id=experiment_id, well=well,
        kind=kind, station=station,
        success=_require_success(resp, kind), started_at=started, finished_at=time.time(),
        protocol_yaml=protocol_yaml,
        result=resp.get("results", resp),
        artifacts=resp.get("artifacts"),
    )


_HOME_ONLY_PROTOCOL_YAML = "protocol:\n  - home:\n"

_SHARC_SCALAR_PARAMS = {
    "sharc_measurement_height": "measurement_height",
    "uv_measurement_height": "measurement_height",
}
_SHARC_METHOD_PARAMS = {
    "sharc_intensity": "intensity",
    "uv_intensity": "intensity",
    "sharc_exposure_time": "exposure_time",
    "uv_exposure_s": "exposure_time",
    "uv_exposure_time_s": "exposure_time",
}
_ASMI_SCALAR_PARAMS = {
    "asmi_measurement_height": "measurement_height",
    "asmi_indentation_limit_height": "indentation_limit_height",
    "asmi_interwell_scan_height": "interwell_scan_height",
    "measurement_height": "measurement_height",
    "indentation_limit_height": "indentation_limit_height",
    "interwell_scan_height": "interwell_scan_height",
}
_ASMI_METHOD_PARAMS = {
    "asmi_step_size": "step_size",
    "asmi_force_limit": "force_limit",
    "asmi_baseline_samples": "baseline_samples",
    "asmi_measure_with_return": "measure_with_return",
    "step_size": "step_size",
    "force_limit": "force_limit",
    "baseline_samples": "baseline_samples",
    "measure_with_return": "measure_with_return",
}


def _apply_protocol_param_overrides(protocol_yaml: str, params: dict, *, station: str) -> str:
    """Apply per-well station overrides from ``Experiment.params``.

    The direct keys are intentionally friendly for script authors
    (``uv_exposure_s``, ``asmi_force_limit``). The nested
    ``{station}_scalar`` and ``{station}_method_kwargs`` mappings are the escape
    hatch for any CubOS protocol field that does not deserve a new top-level key.
    """
    if station == "sharc":
        scalar, method_kwargs = _collect_overrides(
            params,
            station="sharc",
            scalar_params=_SHARC_SCALAR_PARAMS,
            method_params=_SHARC_METHOD_PARAMS,
        )
    elif station == "asmi":
        scalar, method_kwargs = _collect_overrides(
            params,
            station="asmi",
            scalar_params=_ASMI_SCALAR_PARAMS,
            method_params=_ASMI_METHOD_PARAMS,
        )
    else:
        raise ValueError(f"unknown station {station!r}")
    return apply_overrides(protocol_yaml, scalar=scalar, method_kwargs=method_kwargs)


def render_station_protocol(
    station_bundle: StationBundle,
    station: str,
    well: str,
    params: dict,
) -> str:
    """Render the exact per-well SHARC/ASMI protocol used for execution."""
    protocol_yaml = _apply_protocol_param_overrides(
        station_bundle.base_protocol_yaml,
        params,
        station=station,
    )
    return render_protocol(protocol_yaml, well)


def render_asmi_position_check_protocol(
    *,
    well: str = "A1",
    measurement_height: float = 10.0,
) -> str:
    """A harmless ASMI force-read at a safe height for visual position checks."""
    return (
        "protocol:\n"
        "  - measure:\n"
        "      instrument: asmi\n"
        f"      position: plate.{well.strip().upper()}\n"
        "      method: measure\n"
        f"      measurement_height: {float(measurement_height)!r}\n"
        "      method_kwargs:\n"
        "        n_samples: 1\n"
    )


def _collect_overrides(
    params: dict,
    *,
    station: str,
    scalar_params: dict[str, str],
    method_params: dict[str, str],
) -> tuple[dict, dict]:
    scalar = _mapping_param(params.get(f"{station}_scalar"))
    method_kwargs = _mapping_param(params.get(f"{station}_method_kwargs"))
    for source_key, protocol_key in scalar_params.items():
        if source_key in params and params[source_key] is not None:
            scalar[protocol_key] = params[source_key]
    for source_key, protocol_key in method_params.items():
        if source_key in params and params[source_key] is not None:
            method_kwargs[protocol_key] = params[source_key]
    return scalar, method_kwargs


def _mapping_param(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"protocol override must be a mapping, got {type(value).__name__}")
    return dict(value)


def _home_station(station: StationBundle, name: str, results, *,
                  experiment_id: str, well: str, step_id: str, mock_mode: bool) -> None:
    """Send a home-only protocol so the gantry is parked before the arm deposits."""
    run_id = f"{step_id}:home-{name}"
    _record_step(
        results, run_id=run_id, experiment_id=experiment_id, well=well,
        kind=f"{name}_home", station=name, protocol_yaml=_HOME_ONLY_PROTOCOL_YAML,
        call=lambda: station.client.run_protocol(
            run_id=run_id,
            protocol_yaml=_HOME_ONLY_PROTOCOL_YAML,
            metadata={"experiment_id": experiment_id, "well": well, "step": f"{name}_home"},
            mock_mode=mock_mode,
        ),
    )


def _run_asmi_position_check(
    station: StationBundle,
    results,
    *,
    experiment_id: str,
    well: str,
    step_id: str,
    target_well: str,
    measurement_height: float,
    mock_mode: bool,
) -> None:
    run_id = f"{step_id}:asmi-position-check"
    protocol_yaml = render_asmi_position_check_protocol(
        well=target_well,
        measurement_height=measurement_height,
    )
    _record_step(
        results, run_id=run_id, experiment_id=experiment_id, well=well,
        kind="asmi_position_check", station="asmi", protocol_yaml=protocol_yaml,
        call=lambda: station.client.run_protocol(
            run_id=run_id,
            protocol_yaml=protocol_yaml,
            metadata={
                "experiment_id": experiment_id,
                "well": well,
                "step": "asmi_position_check",
                "target_well": target_well,
                "measurement_height": measurement_height,
            },
            mock_mode=mock_mode,
        ),
    )


def _transfer(arm, results, experiment_id, well, step_id, tag, src, dst, *,
              mock_mode=False, skip_safe_prelude=False) -> None:
    run_id = f"{step_id}:{tag}"
    started = time.time()
    try:
        resp = arm.transfer(from_location=src, to_location=dst, run_id=run_id,
                            mock_mode=True if mock_mode else None,
                            skip_safe_prelude=skip_safe_prelude)
    except Exception as exc:
        results.record_run(
            run_id=run_id, experiment_id=experiment_id, well=well,
            kind="arm_transfer", station="xarm",
            success=False, started_at=started, finished_at=time.time(),
            result={"from": src, "to": dst},
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    results.record_run(
        run_id=run_id, experiment_id=experiment_id, well=well,
        kind="arm_transfer", station="xarm",
        success=_require_success(resp, "arm_transfer"),
        started_at=started, finished_at=time.time(),
        result={
            "from": src,
            "to": dst,
            "skip_safe_prelude": skip_safe_prelude,
            "response": resp,
        },
    )


def _select_wells(experiment: Experiment, only_wells: Optional[Sequence[str]]) -> Iterable[str]:
    if not only_wells:
        yield from experiment.wells
        return
    wanted = {w.strip().upper() for w in only_wells}
    unknown = wanted - set(experiment.wells)
    if unknown:
        raise ValueError(f"--only-well: unknown wells {sorted(unknown)}")
    for w in experiment.wells:
        if w in wanted:
            yield w


__all__ = [
    "RunSafetyChecks",
    "StationBundle",
    "render_asmi_position_check_protocol",
    "render_station_protocol",
    "run_experiment",
]
