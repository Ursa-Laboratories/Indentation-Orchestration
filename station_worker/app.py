"""Flask app for a CubOS station Pi.

    GET  /health
    POST /validate-protocol   {protocol_yaml, gantry_config, deck_config}
    POST /run-protocol        {run_id, gantry_config, deck_config, protocol_yaml, mock_mode?, metadata?}
    POST /stop
    GET  /runs/<run_id>

One CubOS protocol runs at a time per Pi (a process-level lock). If a run is in
progress, /run-protocol returns 409. /stop is best-effort — cubos has no clean
mid-``protocol.run()`` abort, so it just disconnects the gantry; the running
request will then fail. The worker writes every received bundle and result under
``run_dir/<run_id>/`` for replay/audit.
"""

from __future__ import annotations

import logging
import threading
import traceback

from flask import Flask, jsonify, request

from .allow import ProtocolNotAllowed, check_protocol_allowed
from .config import StationConfig
from .jsonify import to_jsonable
from .runs import RunDir, now
from .worker import cubos_version, run_cubos_protocol, validate_cubos_protocol

log = logging.getLogger("station_worker.app")


def create_app(cfg: StationConfig) -> Flask:
    app = Flask(__name__)
    app.config["STATION_CONFIG"] = cfg

    run_lock = threading.Lock()
    state = {"busy": False, "current_run_id": None}

    # ----- helpers -----------------------------------------------------

    def _bad_request(msg, code=400):
        return jsonify({"success": False, "error": msg}), code

    def _check_config_pins(gantry_yaml: str, deck_yaml: str):
        from .runs import sha256_text

        if cfg.expected_gantry_sha256 and sha256_text(gantry_yaml) != cfg.expected_gantry_sha256:
            raise ProtocolNotAllowed("gantry_config sha256 does not match the station's pinned value")
        if cfg.expected_deck_sha256 and sha256_text(deck_yaml) != cfg.expected_deck_sha256:
            raise ProtocolNotAllowed("deck_config sha256 does not match the station's pinned value")

    # ----- routes ------------------------------------------------------

    @app.get("/health")
    def health():
        return jsonify({
            "status": "ok",
            "station_id": cfg.station_id,
            "cubos_version": cubos_version(),
            "mock_mode_default": cfg.mock_mode_default,
            "busy": state["busy"],
            "current_run_id": state["current_run_id"],
            "allow": {"instruments": sorted(cfg.allow_instruments),
                      "commands": sorted(cfg.allow_commands)},
        })

    @app.post("/validate-protocol")
    def validate_protocol():
        body = request.get_json(silent=True) or {}
        protocol_yaml = body.get("protocol_yaml")
        gantry_yaml = body.get("gantry_config")
        deck_yaml = body.get("deck_config")
        if not (protocol_yaml and gantry_yaml and deck_yaml):
            return _bad_request("validate-protocol requires gantry_config, deck_config, protocol_yaml")
        try:
            check_protocol_allowed(protocol_yaml,
                                   allow_commands=cfg.allow_commands,
                                   allow_instruments=cfg.allow_instruments)
            _check_config_pins(gantry_yaml, deck_yaml)
        except ProtocolNotAllowed as exc:
            return jsonify({"valid": False, "error": str(exc)}), 400

        rd = RunDir(cfg.run_dir, body.get("run_id") or f"validate-{int(now())}")
        rd.write_inputs(gantry_yaml=gantry_yaml, deck_yaml=deck_yaml, protocol_yaml=protocol_yaml)
        try:
            steps = validate_cubos_protocol(
                gantry_path=rd.gantry_path, deck_path=rd.deck_path, protocol_path=rd.protocol_path,
            )
        except Exception as exc:  # noqa: BLE001 — surface cubos validation errors verbatim
            return jsonify({"valid": False, "error": f"{type(exc).__name__}: {exc}"}), 200
        return jsonify({"valid": True, "steps": steps, "station_id": cfg.station_id})

    @app.post("/run-protocol")
    def run_protocol():
        body = request.get_json(silent=True) or {}
        run_id = body.get("run_id")
        protocol_yaml = body.get("protocol_yaml")
        gantry_yaml = body.get("gantry_config")
        deck_yaml = body.get("deck_config")
        if not (run_id and protocol_yaml and gantry_yaml and deck_yaml):
            return _bad_request("run-protocol requires run_id, gantry_config, deck_config, protocol_yaml")
        mock_mode = bool(body.get("mock_mode", cfg.mock_mode_default))
        metadata = body.get("metadata") or {}

        try:
            check_protocol_allowed(protocol_yaml,
                                   allow_commands=cfg.allow_commands,
                                   allow_instruments=cfg.allow_instruments)
            _check_config_pins(gantry_yaml, deck_yaml)
        except ProtocolNotAllowed as exc:
            return _bad_request(str(exc))

        if not run_lock.acquire(blocking=False):
            return jsonify({
                "success": False, "run_id": run_id,
                "error": f"station busy with run {state['current_run_id']!r}",
            }), 409

        state["busy"] = True
        state["current_run_id"] = run_id
        rd = RunDir(cfg.run_dir, run_id)
        started = now()
        digests = rd.write_inputs(gantry_yaml=gantry_yaml, deck_yaml=deck_yaml, protocol_yaml=protocol_yaml)
        rd.write_meta({
            "run_id": run_id, "station_id": cfg.station_id, "mock_mode": mock_mode,
            "started_at": started, "metadata": metadata, **digests,
        })
        log.info("run-protocol run_id=%s mock=%s -> %s", run_id, mock_mode, rd.dir)
        try:
            raw_results = run_cubos_protocol(
                gantry_path=rd.gantry_path, deck_path=rd.deck_path, protocol_path=rd.protocol_path,
                mock_mode=mock_mode,
            )
            results = to_jsonable(raw_results)
            payload = {
                "success": True,
                "run_id": run_id,
                "station_id": cfg.station_id,
                "results": results,
                "cubos_version": cubos_version(),
                "protocol_sha256": digests["protocol_sha256"],
                "mock_mode": mock_mode,
                "artifacts": {
                    "run_dir": str(rd.dir),
                    "gantry_path": str(rd.gantry_path),
                    "deck_path": str(rd.deck_path),
                    "protocol_path": str(rd.protocol_path),
                    "result_path": str(rd.result_path),
                },
                "started_at": started,
                "finished_at": now(),
            }
            rd.write_result(payload)
            return jsonify(payload)
        except Exception as exc:  # noqa: BLE001 — report, don't crash the server
            tb = traceback.format_exc()
            log.exception("run %s failed", run_id)
            rd.write_error(tb)
            return jsonify({
                "success": False,
                "run_id": run_id,
                "station_id": cfg.station_id,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": tb,
                "protocol_sha256": digests["protocol_sha256"],
                "artifacts": {"run_dir": str(rd.dir), "error_path": str(rd.error_path)},
                "started_at": started,
                "finished_at": now(),
            }), 500
        finally:
            state["busy"] = False
            state["current_run_id"] = None
            run_lock.release()

    @app.post("/stop")
    def stop():
        # No-op. cubos has no graceful mid-``protocol.run()`` abort and the
        # in-flight gantry handle is owned by the running request, not reachable
        # from here. A real emergency stop must be a hardware kill switch /
        # GRBL feed-hold, not this HTTP endpoint.
        return jsonify({
            "success": True,
            "station_id": cfg.station_id,
            "note": "stop is a no-op; cubos has no mid-protocol abort — "
                    "use a hardware kill switch for a real emergency stop.",
            "busy": state["busy"],
            "current_run_id": state["current_run_id"],
        })

    @app.get("/runs/<path:run_id>")
    def get_run(run_id: str):
        rd = RunDir(cfg.run_dir, run_id, create=False)
        if not rd.exists:
            return jsonify({"error": f"no such run: {run_id!r}"}), 404
        out = {
            "run_id": run_id,
            "station_id": cfg.station_id,
            "run_dir": str(rd.dir),
            "protocol_yaml": rd.read_protocol(),
            "result": rd.read_result(),
        }
        if rd.error_path.exists():
            out["error"] = rd.error_path.read_text()
        return jsonify(out)

    return app


__all__ = ["create_app"]
