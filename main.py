#!/usr/bin/env python3
"""
polymer-indentation — one-well end-to-end cycle.

    opentrons.fill (placeholder)  →  arm: OT → uv_station
                                  →  sharc.run_protocol  (UV cure)
                                  →  arm: uv_station → asmi
                                  →  asmi.run_protocol   (indentation)
                                  →  arm: asmi → opentrons

Edit the SETTINGS block below and run:
    python main.py

For the YAML/CLI version (multi-well, --resume, --mock, --only-well, etc.)
the `polymer-indent` console script is still wired up:
    polymer-indent run examples/single_well_cycle.yaml
    polymer-indent health
    polymer-indent workers up arm
A bare `python main.py <subcommand> ...` also forwards to that CLI for
backward compat.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# =============================================================================
# SETTINGS — edit these
# =============================================================================
WELL                       = "E5"          # which well on the SBS 96-well plate (e.g. "A1", "B7", "E5")

# UV cure (SHARC station)
UV_INTENSITY               = 1             # OmniCure intensity, 1–100 %
UV_EXPOSURE_S              = 5.0           # OmniCure exposure time, seconds

# ASMI indentation
ASMI_INDENT_LIMIT_HEIGHT   = 1.5           # mm above well surface; lower (or negative) = deeper indent
                                           # 1.5 (≤ measurement_height 2.0) ≈ ~0.5 mm of non-touch motion
                                           # use e.g. -5.0 for a real indent (5 mm into the well)

# Where the plate goes after ASMI ("opentrons" or "storage_end")
RETURN_LOCATION            = "opentrons"

# Bookkeeping
EXPERIMENT_ID              = "single_well_cycle"
CONTROLLER_CONFIG          = "configs/controller.yaml"
# =============================================================================


# Make the package importable when running from the repo without `pip install -e .`
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from polymer_indent.config import load_controller_config  # noqa: E402
from polymer_indent.experiment import Experiment  # noqa: E402
from polymer_indent.loop import run_experiment  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("polymer_indent.main")


# -----------------------------------------------------------------------------
# OPENTRONS — placeholder. REPLACE THIS with the real Flex REST flow.
# -----------------------------------------------------------------------------
# The loop only needs an object with a `.run_fill(*, well, volume_ul, formulation,
# run_id) -> dict` method. The placeholder below logs the requested fill and
# returns a success-shaped dict so the rest of the cycle proceeds. To wire the
# actual Opentrons Flex (a working reference is denos's
# workers/opentrons_worker/opentrons_worker.py):
#
#   1. Template a protocol .py with the well id + volume.
#   2. POST {flex_base_url}/protocols                     (multipart: protocol + labware json)
#   3. POST {flex_base_url}/runs                          {"data": {"protocolId": <id>}}
#   4. POST {flex_base_url}/runs/<run_id>/actions         {"data": {"actionType": "play"}}
#   5. Poll GET {flex_base_url}/runs/<run_id>             until status ∈ {succeeded, failed}
#
class OpentronsClient:
    def __init__(self, base_url: str | None = None, timeout_s: float = 600.0):
        self.base_url = base_url
        self.timeout_s = timeout_s

    def run_fill(self, *, well: str, volume_ul: float,
                 formulation: str | None = None, run_id: str | None = None) -> dict:
        log.warning("[OPENTRONS PLACEHOLDER] fill well=%s volume_ul=%s formulation=%s "
                    "(no hardware; replace this method to wire the Flex)",
                    well, volume_ul, formulation)
        # TODO(opentrons): real Flex REST flow, see the docstring above.
        return {
            "success": True, "placeholder": True,
            "well": well, "volume_dispensed": volume_ul,
            "formulation": formulation, "run_id": run_id,
        }
# -----------------------------------------------------------------------------


def _apply_overrides(protocol_yaml: str, edits: dict) -> str:
    """Apply scalar / method_kwargs overrides to every measure/scan step in a
    cubos protocol YAML. The well id is left untouched — the loop's
    ``render_protocol`` swaps it later.
    """
    if not edits:
        return protocol_yaml
    doc = yaml.safe_load(protocol_yaml)
    for step in (doc.get("protocol") or []):
        if not isinstance(step, dict):
            continue
        for cmd, body in step.items():
            if cmd not in ("measure", "scan") or not isinstance(body, dict):
                continue
            for k, v in edits.items():
                if k in body:
                    body[k] = v
                elif isinstance(body.get("method_kwargs"), dict) and k in body["method_kwargs"]:
                    body["method_kwargs"][k] = v
    return yaml.safe_dump(doc, sort_keys=False)


def main() -> int:
    cfg = load_controller_config(CONTROLLER_CONFIG)

    # Build the experiment in code — no experiment.yaml needed for the one-well cycle.
    experiment = Experiment(
        id=EXPERIMENT_ID,
        wells=[WELL],
        params={WELL: {
            "volume_ul": 350,
            "uv_intensity": UV_INTENSITY,
            "uv_time": UV_EXPOSURE_S,
            "asmi_indentation_limit_height": ASMI_INDENT_LIMIT_HEIGHT,
        }},
        defaults={},
        final_well_return_location=RETURN_LOCATION,
    )

    # Apply the SETTINGS knobs as overrides on top of the frozen base protocols.
    # (well id is rewritten by the loop.)
    sharc = cfg.station_bundle("sharc")
    sharc.base_protocol_yaml = _apply_overrides(
        sharc.base_protocol_yaml,
        {"intensity": UV_INTENSITY, "exposure_time": UV_EXPOSURE_S},
    )
    asmi = cfg.station_bundle("asmi")
    asmi.base_protocol_yaml = _apply_overrides(
        asmi.base_protocol_yaml,
        {"indentation_limit_height": ASMI_INDENT_LIMIT_HEIGHT},
    )

    arm = cfg.arm_client()
    opentrons = OpentronsClient(base_url=(cfg.raw.get("opentrons") or {}).get("base_url"))

    log.info("=" * 72)
    log.info("polymer-indentation cycle  ·  well=%s  ·  uv: %s%% for %ss  ·  asmi_limit_h=%s  ·  return=%s",
             WELL, UV_INTENSITY, UV_EXPOSURE_S, ASMI_INDENT_LIMIT_HEIGHT, RETURN_LOCATION)
    log.info("=" * 72)

    with cfg.result_store() as results:
        failed = run_experiment(
            experiment,
            opentrons=opentrons, arm=arm, sharc=sharc, asmi=asmi,
            results=results,
            mock_mode=False,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    # If extra args are passed, forward to the polymer-indent CLI
    # (so `python main.py run ...`, `health`, `workers`, etc. still work).
    if len(sys.argv) > 1:
        from polymer_indent.cli import main as cli_main
        sys.exit(cli_main())
    sys.exit(main())
