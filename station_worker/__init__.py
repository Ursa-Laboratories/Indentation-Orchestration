"""station_worker — the generic CubOS station HTTP worker.

Runs on each station Pi (SHARC, ASMI). It exposes a small HTTP API; on
``/run-protocol`` it receives the gantry + deck YAML (frozen, identical every
run) and a protocol YAML (the base protocol with one well id swapped), writes
all three into a per-run directory, and runs them through the Pi's local cubos
install (``setup_protocol`` -> connect gantry + instruments -> ``protocol.run``
-> disconnect). One CubOS protocol at a time per Pi (a process-level lock).

Only this package imports cubos. The cubos imports are lazy (inside the run
function) so the Flask app and config can be imported on a machine without cubos.
"""

__version__ = "0.1.0"
