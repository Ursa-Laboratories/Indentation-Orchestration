#!/usr/bin/env python3
"""Bioadhesives full workcell loop.

Operator flow:
  1. Health-check every machine used by this run.
  2. Prompt for readiness only if all required machines are reachable.
  3. Execute Opentrons -> SHARC cure -> ASMI indentation for each configured well.
  4. Export one joined CSV row per well from the SQLite result store.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from polymer_indent.bioadhesives import (  # noqa: E402
    WorkflowWell,
    build_workflow_experiment,
    controller_health_targets,
    export_joined_well_csv,
    failed_health_names,
    format_health_report,
    prompt_ready,
    run_health_checks,
)
from polymer_indent.clients import OpentronsClient  # noqa: E402
from polymer_indent.config import load_controller_config  # noqa: E402
from polymer_indent.loop import run_experiment  # noqa: E402

# =============================================================================
# SETTINGS - edit these
# =============================================================================
EXPERIMENT_ID = "bioadhesives_pilot_full_loop"
CONTROLLER_CONFIG = "configs/controller.yaml"

# Each entry is one complete workflow run for a target plate well.
# Per-well SHARC/ASMI settings override the shared defaults below.
WORKFLOW_WELLS = [
    WorkflowWell(
        target_well="A1",
        source_well="A1",
        uv_exposure_s=11.0,
        # Examples for per-well indentation customization:
        # asmi_scalar={"indentation_limit_height": -4.0},
        # asmi_method_kwargs={"force_limit": 4.0, "step_size": 0.02},
    ),
]

# Opentrons deck slots - must match the arm worker's Opentrons pickup point.
OPENTRONS_TIP_RACK_SLOT = "A2"
OPENTRONS_TUBE_RACK_SLOT = "B2"
OPENTRONS_PLATE_SLOT = "D1"
OPENTRONS_PLATE_LABWARE = "corning_96_wellplate_360ul_flat"

# Opentrons viscous transfer defaults.
OPENTRONS_VOLUME_UL = 100
OPENTRONS_FLOW_RATE_UL_MIN = 150
OPENTRONS_AIR_EXPULSION_UL = 20
OPENTRONS_TIP_LIFT_HEIGHT_MM = 8

# SHARC UV cure defaults. Per-well exposure comes from WORKFLOW_WELLS.
UV_INTENSITY = 1

# ASMI indentation defaults. Per-well overrides go in WorkflowWell(...).
ASMI_INDENT_LIMIT_HEIGHT = 1.5
ASMI_MEASURE_WITH_RETURN = True

# Where the plate goes after the final ASMI run.
FINAL_RETURN_LOCATION = "storage_end"

# True means the Opentrons fill row is a logged placeholder and Flex health is
# not required. False means the real Flex must pass health before the run starts.
SKIP_OPENTRONS_FILL = True

# True means SHARC + ASMI /run-protocol calls use cubos mock_mode. Workers must
# still be reachable; arm + rail still move for real unless the arm worker itself
# was launched with --mock.
MOCK_STATIONS = False

# Joined per-well export written after a run attempt.
REPORT_CSV = REPO_ROOT / "results" / f"{EXPERIMENT_ID}_joined.csv"
# =============================================================================


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("polymer_indent.bioadhesives")


def main() -> int:
    cfg = load_controller_config(CONTROLLER_CONFIG)
    experiment = _build_experiment()

    include_opentrons = not SKIP_OPENTRONS_FILL
    health_results = run_health_checks(
        controller_health_targets(cfg, include_opentrons=include_opentrons)
    )
    print(format_health_report(health_results))
    if SKIP_OPENTRONS_FILL:
        print("  Opentrons Flex skipped because SKIP_OPENTRONS_FILL=True")
    offline = failed_health_names(health_results)
    if offline:
        print(f"Aborting: offline or unready device(s): {', '.join(offline)}", file=sys.stderr)
        return 1

    _log_run_plan(experiment)
    if not prompt_ready(experiment):
        print("Aborted before starting hardware workflow.")
        return 130

    opentrons = OpentronsClient(None) if SKIP_OPENTRONS_FILL else cfg.opentrons_client()
    if SKIP_OPENTRONS_FILL:
        log.warning("SKIP_OPENTRONS_FILL=True - Opentrons step will be a no-op placeholder")

    mock_modes = {"sharc": True, "asmi": True} if MOCK_STATIONS else None
    if MOCK_STATIONS:
        log.warning("MOCK_STATIONS=True - SHARC + ASMI protocols run in cubos mock_mode")

    exit_code = 0
    try:
        with cfg.result_store() as results:
            failed = run_experiment(
                experiment,
                opentrons=opentrons,
                arm=cfg.arm_client(),
                sharc=cfg.station_bundle("sharc"),
                asmi=cfg.station_bundle("asmi"),
                results=results,
                mock_mode=False,
                mock_modes=mock_modes,
            )
        exit_code = 1 if failed else 0
    except Exception:  # noqa: BLE001 - keep the operator-facing export attempt
        log.exception("bioadhesives workflow failed")
        exit_code = 1
    finally:
        _export_report(cfg.db_path, experiment.id)
    return exit_code


def _build_experiment():
    shared_params = {
        "volume_ul": OPENTRONS_VOLUME_UL,
        "flow_rate_ul_min": OPENTRONS_FLOW_RATE_UL_MIN,
        "air_expulsion_ul": OPENTRONS_AIR_EXPULSION_UL,
        "tip_lift_height_mm": OPENTRONS_TIP_LIFT_HEIGHT_MM,
        "tip_rack_slot": OPENTRONS_TIP_RACK_SLOT,
        "tube_rack_slot": OPENTRONS_TUBE_RACK_SLOT,
        "plate_slot": OPENTRONS_PLATE_SLOT,
        "plate_labware": OPENTRONS_PLATE_LABWARE,
        "uv_intensity": UV_INTENSITY,
        "asmi_scalar": {
            "indentation_limit_height": ASMI_INDENT_LIMIT_HEIGHT,
        },
        "asmi_method_kwargs": {
            "measure_with_return": ASMI_MEASURE_WITH_RETURN,
        },
    }
    return build_workflow_experiment(
        experiment_id=EXPERIMENT_ID,
        wells=WORKFLOW_WELLS,
        shared_params=shared_params,
        final_return_location=FINAL_RETURN_LOCATION,
    )


def _log_run_plan(experiment) -> None:
    log.info("=" * 72)
    log.info("bioadhesives full loop: %d workflow well(s)", len(experiment.wells))
    for well, params in experiment.items():
        asmi_scalar = params.get("asmi_scalar") or {}
        asmi_kwargs = params.get("asmi_method_kwargs") or {}
        log.info(
            "  source %s -> plate %s | UV %ss @ intensity %s | ASMI limit=%s force=%s step=%s",
            params.get("source_well"),
            well,
            params.get("uv_exposure_s"),
            params.get("uv_intensity"),
            asmi_scalar.get("indentation_limit_height"),
            asmi_kwargs.get("force_limit", "<base protocol>"),
            asmi_kwargs.get("step_size", "<base protocol>"),
        )
    log.info("=" * 72)


def _export_report(db_path: Path, experiment_id: str) -> None:
    try:
        out = export_joined_well_csv(db_path, experiment_id, REPORT_CSV)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not export joined workflow CSV: %s", exc)
        return
    log.info("joined workflow CSV: %s", out)


if __name__ == "__main__":
    sys.exit(main())
