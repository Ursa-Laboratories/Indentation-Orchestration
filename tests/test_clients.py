"""Client tests against real local HTTP servers (a CubOS station worker app and
a tiny arm-worker stub)."""

import threading
from pathlib import Path

import pytest

pytest.importorskip("flask")
from werkzeug.serving import make_server  # noqa: E402

from polymer_indent.clients import ArmRailClient, CubOSStationClient  # noqa: E402
from polymer_indent.clients._http import HttpError  # noqa: E402
from polymer_indent.clients.arm_rail import ArmTransferError  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIGS = REPO_ROOT / "configs"


class _Server:
    def __init__(self, app):
        self._srv = make_server("127.0.0.1", 0, app)
        self.port = self._srv.server_port
        self.url = f"http://127.0.0.1:{self.port}"
        self._t = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._t.start()

    def stop(self):
        self._srv.shutdown()
        self._t.join(timeout=5)


@pytest.fixture
def station_server(tmp_path):
    from station_worker.app import create_app
    from station_worker.config import StationConfig

    cfg = StationConfig(station_id="asmi", run_dir=tmp_path / "runs",
                        mock_mode_default=True, allow_instruments={"asmi"})
    srv = _Server(create_app(cfg))
    yield srv
    srv.stop()


@pytest.fixture
def station_client(station_server):
    return CubOSStationClient(
        station_server.url, "asmi",
        gantry_config_yaml=(CONFIGS / "gantry" / "asmi_gantry.yaml").read_text(),
        deck_config_yaml=(CONFIGS / "deck" / "asmi_deck.yaml").read_text(),
        timeout_s=60.0, mock_mode=True,
    )


def test_station_health(station_client):
    info = station_client.health()
    assert info["station_id"] == "asmi"


def test_station_run_protocol_disallowed_instrument_raises_httperror(station_client):
    bad = "protocol:\n  - measure:\n      instrument: uv_curing\n      position: plate.A1\n      method: cure\n"
    with pytest.raises(HttpError) as ei:
        station_client.run_protocol(run_id="x:A1:asmi", protocol_yaml=bad)
    assert "400" in str(ei.value)


def test_station_run_protocol_mock_roundtrip(station_client):
    pytest.importorskip("protocol_engine")
    proto = (CONFIGS / "protocol" / "asmi_indentation_test.yaml").read_text()
    resp = station_client.run_protocol(
        run_id="plate_001:E5:asmi", protocol_yaml=proto,
        metadata={"experiment_id": "plate_001", "well": "E5"},
    )
    assert resp["success"] is True
    assert resp["station_id"] == "asmi"
    assert resp["protocol_sha256"]
    # read it back
    got = station_client.get_run("plate_001:E5:asmi")
    assert got["result"]["success"] is True


def test_station_validate_protocol(station_client):
    pytest.importorskip("protocol_engine")
    proto = (CONFIGS / "protocol" / "asmi_indentation_test.yaml").read_text()
    assert station_client.validate_protocol(proto).get("valid") is True


# ---- ArmRailClient against a stub --------------------------------------------

@pytest.fixture
def arm_server():
    from flask import Flask, jsonify, request

    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "device": "xarm"})

    @app.post("/run")
    def run():
        body = request.get_json(silent=True) or {}
        if body.get("from") == "nowhere":
            return jsonify({"success": False, "error": "no route from 'nowhere'"})
        return jsonify({"success": True, "from": body.get("from"), "to": body.get("to")})

    srv = _Server(app)
    yield srv
    srv.stop()


def test_arm_transfer_ok(arm_server):
    arm = ArmRailClient(arm_server.url)
    resp = arm.transfer(from_location="opentrons", to_location="uv_station", run_id="r1")
    assert resp == {"success": True, "from": "opentrons", "to": "uv_station"}
    assert arm.health()["device"] == "xarm"


def test_arm_transfer_failure_raises(arm_server):
    arm = ArmRailClient(arm_server.url)
    with pytest.raises(ArmTransferError):
        arm.transfer(from_location="nowhere", to_location="asmi")


def test_arm_unreachable_raises_httperror():
    arm = ArmRailClient("http://127.0.0.1:1", timeout_s=0.5)  # nothing listening
    with pytest.raises(HttpError):
        arm.health()
