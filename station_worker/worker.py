"""The thin CubOS adapter: take three YAML paths, run the protocol, return results.

Mirrors cubos' own ``setup/run_protocol.py`` for the real-hardware path:
load gantry config -> ``setup_protocol`` -> ``gantry.connect()`` ->
``gantry.prepare_for_protocol_run()`` -> ``context.gantry.connect_instruments()`` ->
health check -> ``protocol.execute()`` -> ``finally`` disconnect instruments + gantry.

Mock path: ``setup_protocol(gantry=None, mock_mode=True)`` -> execute -> done (cubos
constructs offline drivers; ``connect_instruments`` is a no-op for them, and no
GRBL serial port is opened).

All cubos imports are lazy so this module imports fine on a machine without
cubos installed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List

log = logging.getLogger("station_worker.cubos")


def cubos_version() -> str:
    try:
        from importlib.metadata import version

        return version("cubos")
    except Exception:  # noqa: BLE001
        return "unknown"


def run_cubos_protocol(
    *,
    gantry_path: str | Path,
    deck_path: str | Path,
    protocol_path: str | Path,
    mock_mode: bool,
) -> List[Any]:
    """Run the protocol and return cubos' per-step results list.

    Raises whatever cubos raises (loader / validation / runtime errors), plus a
    ``RuntimeError`` if the real gantry fails its health check.
    """
    import yaml  # noqa: PLC0415
    from protocol_engine.setup import setup_protocol  # noqa: PLC0415

    gantry_path, deck_path, protocol_path = (str(gantry_path), str(deck_path), str(protocol_path))

    if mock_mode:
        log.info("running protocol in MOCK mode (no hardware)")
        protocol, context = setup_protocol(
            gantry_path, deck_path, protocol_path, gantry=None, mock_mode=True
        )
        context.gantry.connect_instruments()  # no-op for offline drivers
        try:
            return protocol.execute(context)
        finally:
            try:
                context.gantry.disconnect_instruments()
            except Exception:  # noqa: BLE001
                log.exception("disconnect_instruments() failed during cleanup (mock mode)")

    # --- real hardware ---
    from gantry import Gantry  # noqa: PLC0415

    with open(gantry_path) as f:
        raw_config = yaml.safe_load(f)
    gantry = Gantry(config=raw_config)

    protocol, context = setup_protocol(gantry_path, deck_path, protocol_path, gantry=gantry)
    log.info("protocol loaded: %d steps", len(protocol))

    gantry.connect()
    try:
        gantry.prepare_for_protocol_run()
        context.gantry.connect_instruments()
        try:
            if not gantry.is_healthy():
                raise RuntimeError("gantry health check failed after connect")
            return protocol.execute(context)
        finally:
            # Don't let a cleanup failure mask the original cubos exception —
            # log it and continue.
            log.info("disconnecting instruments")
            try:
                context.gantry.disconnect_instruments()
            except Exception:  # noqa: BLE001
                log.exception("disconnect_instruments() failed during cleanup")
    finally:
        log.info("disconnecting gantry")
        try:
            gantry.disconnect()
        except Exception:  # noqa: BLE001
            log.exception("gantry.disconnect() failed during cleanup")


def validate_cubos_protocol(
    *,
    gantry_path: str | Path,
    deck_path: str | Path,
    protocol_path: str | Path,
) -> int:
    """Offline validation only (no hardware). Returns the step count.

    Raises whatever cubos raises on invalid configs/protocols.
    """
    from protocol_engine.setup import setup_protocol  # noqa: PLC0415

    protocol, _context = setup_protocol(
        str(gantry_path), str(deck_path), str(protocol_path), gantry=None, mock_mode=True
    )
    return len(protocol)


__all__ = ["run_cubos_protocol", "validate_cubos_protocol", "cubos_version"]
