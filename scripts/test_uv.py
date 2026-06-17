#!/usr/bin/env python3
"""Run a full UV-curing protocol on the SHARC station, for one well.

Run from the controller box (the package must be installed or this repo on disk):

    python scripts/test_uv.py --well A1 --mock                       # dry run (no hardware)
    python scripts/test_uv.py --well A1                               # real run (prompts first)
    python scripts/test_uv.py --well C7 --intensity 20 --exposure-time 300
    python scripts/test_uv.py --validate-only --well A1
    python scripts/test_uv.py --url http://10.210.29.12:8000 --well A1

Targets the SHARC / UV-curing station from configs/controller.yaml
(bear-den-scale, :8000) with configs/gantry/sharc_gantry.yaml +
configs/deck/sharc_deck.yaml + configs/protocol/sharc_uv_one_well.yaml (well id
swapped in). The local station configs are frozen copies from BU-Configs; confirm
the SHARC `uv_curing.offline` setting before expecting a real run to fire the
OmniCure. Use --mock for a dry run. See --help.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from station_test import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run("sharc"))
