"""station_worker Flask app tests.

The allow-list / request-shape tests need only Flask. The end-to-end mock-run
test additionally needs cubos (``protocol_engine``) importable and is skipped
otherwise.
"""

import json
from pathlib import Path

import pytest

flask = pytest.importorskip("flask")  # noqa: F841

from station_worker.app import create_app  # noqa: E402
from station_worker.config import StationConfig  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIGS = REPO_ROOT / "configs"


@pytest.fixture
def asmi_cfg(tmp_path):
    return StationConfig(
        station_id="asmi",
        run_dir=tmp_path / "runs",
        mock_mode_default=True,
        allow_instruments={"asmi"},
        allow_commands={"home", "measure", "move"},
    )


@pytest.fixture
def client(asmi_cfg):
    app = create_app(asmi_cfg)
    app.testing = True
    return app.test_client()


def _bundle(name="asmi"):
    return {
        "gantry": (CONFIGS / "gantry" / f"{name}_gantry.yaml").read_text(),
        "deck": (CONFIGS / "deck" / f"{name}_deck.yaml").read_text(),
        "protocol": (CONFIGS / "protocol" / "asmi_indentation_test.yaml").read_text(),
    }


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["station_id"] == "asmi"
    assert body["allow"]["instruments"] == ["asmi"]


def test_run_protocol_rejects_disallowed_instrument(client):
    b = _bundle()
    bad_protocol = "protocol:\n  - home:\n  - measure:\n      instrument: uv_curing\n      position: plate.A1\n      method: cure\n  - home:\n"
    r = client.post("/run-protocol", json={
        "run_id": "x:A1:asmi", "gantry_config": b["gantry"], "deck_config": b["deck"],
        "protocol_yaml": bad_protocol, "mock_mode": True,
    })
    assert r.status_code == 400
    assert "not allowed" in r.get_json()["error"]


def test_run_protocol_rejects_disallowed_command(client):
    b = _bundle()
    bad_protocol = "protocol:\n  - home:\n  - scan:\n      plate: plate\n      instrument: asmi\n      method: indentation\n"
    r = client.post("/run-protocol", json={
        "run_id": "x:A1:asmi", "gantry_config": b["gantry"], "deck_config": b["deck"],
        "protocol_yaml": bad_protocol, "mock_mode": True,
    })
    assert r.status_code == 400


def test_run_protocol_missing_fields(client):
    r = client.post("/run-protocol", json={"run_id": "x"})
    assert r.status_code == 400


def test_get_run_404(client):
    assert client.get("/runs/does-not-exist").status_code == 404


def test_stop_is_best_effort(client):
    r = client.post("/stop", json={})
    assert r.status_code == 200
    assert r.get_json()["success"] is True


# ---- end-to-end mock run (needs cubos) -------------------------------------

@pytest.mark.parametrize("station", ["asmi", "sharc"])
def test_mock_run_roundtrip(tmp_path, station):
    pytest.importorskip("protocol_engine")  # cubos must be installed

    if station == "asmi":
        cfg = StationConfig(station_id="asmi", run_dir=tmp_path / "runs",
                            allow_instruments={"asmi"})
        gantry = (CONFIGS / "gantry" / "asmi_gantry.yaml").read_text()
        deck = (CONFIGS / "deck" / "asmi_deck.yaml").read_text()
        protocol = (CONFIGS / "protocol" / "asmi_indentation_test.yaml").read_text()
        run_id = "plate_001:E5:asmi"
    else:
        cfg = StationConfig(station_id="sharc", run_dir=tmp_path / "runs",
                            allow_instruments={"uv_curing"})
        gantry = (CONFIGS / "gantry" / "sharc_gantry.yaml").read_text()
        deck = (CONFIGS / "deck" / "sharc_deck.yaml").read_text()
        protocol = (CONFIGS / "protocol" / "sharc_uv_one_well.yaml").read_text()
        run_id = "plate_001:A1:sharc"

    app = create_app(cfg)
    app.testing = True
    c = app.test_client()

    # offline validation
    rv = c.post("/validate-protocol", json={"gantry_config": gantry, "deck_config": deck,
                                            "protocol_yaml": protocol})
    assert rv.status_code == 200, rv.get_data(as_text=True)
    vbody = rv.get_json()
    if not vbody.get("valid"):
        err = vbody.get("error", "")
        # A stale installed cubos may not ship every load_name (e.g. the SHARC
        # holder definition). The Pis run a fresh cubos@main; skip here.
        if "DeckLoaderError" in err or "labware/definitions" in err:
            pytest.skip(f"installed cubos can't load this deck: {err.splitlines()[0]}")
        pytest.fail(f"validate-protocol said invalid: {vbody}")

    # mock run
    rr = c.post("/run-protocol", json={"run_id": run_id, "gantry_config": gantry,
                                       "deck_config": deck, "protocol_yaml": protocol,
                                       "mock_mode": True,
                                       "metadata": {"experiment_id": "plate_001"}})
    assert rr.status_code == 200, rr.get_data(as_text=True)
    body = rr.get_json()
    assert body["success"] is True
    assert body["station_id"] == station
    assert isinstance(body["results"], list)
    assert body["protocol_sha256"]
    # run dir + result.json written
    run_dir = Path(body["artifacts"]["run_dir"])
    assert (run_dir / "gantry.yaml").exists()
    assert (run_dir / "deck.yaml").exists()
    assert (run_dir / "protocol.yaml").exists()
    assert json.loads((run_dir / "result.json").read_text())["success"] is True

    # /runs/<id> read-back
    rg = c.get(f"/runs/{run_id}")
    assert rg.status_code == 200
    assert rg.get_json()["result"]["success"] is True


def test_busy_returns_409(tmp_path, monkeypatch):
    """While one run holds the lock, a second /run-protocol gets 409."""
    import threading

    cfg = StationConfig(station_id="asmi", run_dir=tmp_path / "runs", allow_instruments={"asmi"})

    started = threading.Event()
    release = threading.Event()

    def _blocking_run(**_kwargs):
        started.set()
        release.wait(timeout=10)
        return [None]

    # Patch the name as imported into station_worker.app.
    monkeypatch.setattr("station_worker.app.run_cubos_protocol", _blocking_run)

    app = create_app(cfg)
    app.testing = True
    b = _bundle()
    payload = {"run_id": "x:A1:asmi", "gantry_config": b["gantry"], "deck_config": b["deck"],
               "protocol_yaml": b["protocol"], "mock_mode": True}

    result = {}

    def _fire_first():
        result["first"] = app.test_client().post("/run-protocol", json=payload).status_code

    t = threading.Thread(target=_fire_first)
    t.start()
    assert started.wait(timeout=5), "first run never started"

    second = app.test_client().post("/run-protocol", json={**payload, "run_id": "x:A2:asmi"})
    assert second.status_code == 409
    assert "busy" in second.get_json()["error"]

    release.set()
    t.join(timeout=5)
    assert result["first"] == 200
