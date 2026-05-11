#!/usr/bin/env python3
"""Run a full ASMI indentation protocol on the ASMI station, for one well.

Run from the controller box (the package must be installed or this repo on disk):

    python scripts/test_asmi.py --well E5 --mock        # dry run (no hardware)
    python scripts/test_asmi.py --well E5                # real run (prompts first)
    python scripts/test_asmi.py --well B3 --force-limit 5 --indentation-limit-height -3
    python scripts/test_asmi.py --validate-only --well E5
    python scripts/test_asmi.py --url http://10.210.29.17:8000 --well E5

Targets the ASMI station from configs/controller.yaml (bear-den-asmi, :8000) with
configs/gantry/asmi_gantry.yaml + configs/deck/asmi_deck.yaml +
configs/protocol/asmi_indentation_test.yaml (well id swapped in). See --help.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from station_test import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run("asmi"))
