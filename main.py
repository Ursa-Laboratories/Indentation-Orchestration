#!/usr/bin/env python3
"""Entrypoint for the polymer_indent main controller.

    python main.py run examples/pegda_screen.yaml --mock
    python main.py validate examples/pegda_screen.yaml
    python main.py health

(Equivalent to the ``polymer-indent`` console script installed by pyproject.)
"""

import sys

from polymer_indent.cli import main

if __name__ == "__main__":
    sys.exit(main())
