"""Run the station worker:  python -m station_worker --config configs/stations/<station>.yaml

The gantry/deck/protocol YAMLs are NOT passed here — the controller sends all
three in every /run-protocol request. This only needs the station server config.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .app import create_app
from .config import load_station_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="station_worker", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="path to configs/stations/<station>.yaml")
    parser.add_argument("--host", default=None, help="override host from config")
    parser.add_argument("--port", type=int, default=None, help="override port from config")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    cfg = load_station_config(args.config)
    app = create_app(cfg)
    host = args.host or cfg.host
    port = args.port or cfg.port
    logging.getLogger("station_worker").info(
        "station %s listening on %s:%d (run_dir=%s, mock_default=%s)",
        cfg.station_id, host, port, cfg.run_dir, cfg.mock_mode_default,
    )
    # threaded=False keeps it strictly one-protocol-at-a-time at the WSGI layer
    # too (the lock already enforces it, but no point queueing concurrent runs).
    app.run(host=host, port=port, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
