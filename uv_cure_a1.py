#!/usr/bin/env python3
"""Demo helper: send the SHARC A1 UV-cure protocol to the station worker."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from polymer_indent.clients import CubOSStationClient, StationRunError  # noqa: E402
from polymer_indent.clients._http import HttpError  # noqa: E402
from polymer_indent.protocol_render import apply_overrides, render_protocol  # noqa: E402

SHARC_URL = "http://10.210.29.12:8000"
GANTRY_YAML = REPO_ROOT / "configs/gantry/sharc_gantry.yaml"
DECK_YAML = REPO_ROOT / "configs/deck/sharc_deck.yaml"
PROTOCOL_YAML = REPO_ROOT / "configs/protocol/sharc_uv_one_well.yaml"

WELL = "A1"
UV_INTENSITY = 1
EXPOSURE_TIME_S = 11.0
MOCK_MODE = False
TIMEOUT_S = 900.0


def main() -> int:
    gantry_yaml = GANTRY_YAML.read_text()
    deck_yaml = DECK_YAML.read_text()
    protocol_yaml = render_protocol(PROTOCOL_YAML.read_text(), WELL)
    protocol_yaml = apply_overrides(
        protocol_yaml,
        method_kwargs={"intensity": UV_INTENSITY, "exposure_time": EXPOSURE_TIME_S},
    )

    run_id = f"uv_cure_a1:{int(time.time())}"
    client = CubOSStationClient(
        SHARC_URL,
        "sharc",
        gantry_config_yaml=gantry_yaml,
        deck_config_yaml=deck_yaml,
        timeout_s=TIMEOUT_S,
        mock_mode=MOCK_MODE,
    )

    try:
        health = client.health()
        print(f"health: {health}")

        validation = client.validate_protocol(protocol_yaml)
        if not validation.get("valid"):
            print(f"validate-protocol invalid: {validation.get('error')}", file=sys.stderr)
            return 2
        print(f"validate-protocol: OK ({validation.get('steps')} steps)")

        response = client.run_protocol(
            run_id=run_id,
            protocol_yaml=protocol_yaml,
            metadata={"source": "uv_cure_a1.py", "well": WELL},
        )
        
    except StationRunError as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        print(json.dumps(exc.payload, indent=2, default=str), file=sys.stderr)
        return 1
    except HttpError as exc:
        print(f"HTTP error: {exc}", file=sys.stderr)
        return 1

    print("RUN OK")
    print(json.dumps(response, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
