import csv
import json

from polymer_indent.bioadhesives import (
    HealthTarget,
    WorkflowWell,
    build_workflow_experiment,
    export_joined_well_csv,
    failed_health_names,
    failed_validation_labels,
    format_health_report,
    format_validation_report,
    run_health_checks,
    validate_workflow_protocols,
)
from polymer_indent.loop import StationBundle
from polymer_indent.results import ResultStore


def test_build_workflow_experiment_merges_shared_and_per_well_settings():
    exp = build_workflow_experiment(
        experiment_id="bio1",
        wells=[
            WorkflowWell(
                target_well="a1",
                source_well="b1",
                uv_exposure_s=12.5,
                asmi_scalar={"indentation_limit_height": -4.0},
                asmi_method_kwargs={"force_limit": 4.0},
            )
        ],
        shared_params={
            "volume_ul": 100,
            "uv_intensity": 1,
            "asmi_scalar": {"measurement_height": -1.0, "indentation_limit_height": -3.0},
            "asmi_method_kwargs": {"step_size": 0.01, "force_limit": 3.0},
        },
        final_return_location="storage_end",
    )

    assert exp.wells == ["A1"]
    params = exp.well_params("A1")
    assert params["source_well"] == "B1"
    assert params["uv_exposure_s"] == 12.5
    assert params["volume_ul"] == 100
    assert params["asmi_scalar"] == {
        "measurement_height": -1.0,
        "indentation_limit_height": -4.0,
    }
    assert params["asmi_method_kwargs"] == {"step_size": 0.01, "force_limit": 4.0}


def test_health_report_uses_checkmarks_and_names_failed_devices():
    results = run_health_checks([
        HealthTarget("SHARC station", lambda: {"status": "ok", "station_id": "sharc"}),
        HealthTarget("ASMI station", lambda: (_ for _ in ()).throw(RuntimeError("no route"))),
    ])

    report = format_health_report(results)
    assert "✅ SHARC station" in report
    assert "❌ ASMI station" in report
    assert failed_health_names(results) == ["ASMI station"]


class _ValidationClient:
    def __init__(self, *, valid=True):
        self.valid = valid
        self.protocols = []

    def validate_protocol(self, protocol_yaml):
        self.protocols.append(protocol_yaml)
        if self.valid:
            return {"valid": True, "steps": 2}
        return {"valid": False, "error": "bad setup"}


def test_validate_workflow_protocols_checks_position_and_per_well_protocols():
    exp = build_workflow_experiment(
        experiment_id="bio1",
        wells=[WorkflowWell(target_well="B2", uv_exposure_s=7.0)],
        shared_params={"uv_intensity": 1, "asmi_method_kwargs": {"force_limit": 3.0}},
        final_return_location="storage_end",
    )
    sharc_client = _ValidationClient()
    asmi_client = _ValidationClient()
    sharc = StationBundle(
        client=sharc_client,
        base_protocol_yaml=(
            "protocol:\n"
            "  - measure:\n"
            "      instrument: uv_curing\n"
            "      position: plate_holder.plate.A1\n"
            "      method: cure\n"
            "      measurement_height: 1\n"
        ),
    )
    asmi = StationBundle(
        client=asmi_client,
        base_protocol_yaml=(
            "protocol:\n"
            "  - measure:\n"
            "      instrument: asmi\n"
            "      position: plate.A1\n"
            "      method: indentation\n"
            "      measurement_height: 2\n"
            "      indentation_limit_height: 1\n"
        ),
    )

    results = validate_workflow_protocols(
        exp,
        sharc=sharc,
        asmi=asmi,
        include_asmi_position_check=True,
    )

    assert failed_validation_labels(results) == []
    assert "✅ ASMI position check" in format_validation_report(results)
    assert len(sharc_client.protocols) == 1
    assert len(asmi_client.protocols) == 2
    assert "position: plate.A1" in asmi_client.protocols[0]
    assert "measurement_height: 10.0" in asmi_client.protocols[0]
    assert "position: plate_holder.plate.B2" in sharc_client.protocols[0]
    assert "exposure_time: 7.0" in sharc_client.protocols[0]
    assert "position: plate.B2" in asmi_client.protocols[1]
    assert "force_limit: 3.0" in asmi_client.protocols[1]


def test_validate_workflow_protocols_reports_invalid_setup():
    exp = build_workflow_experiment(
        experiment_id="bio1",
        wells=[WorkflowWell(target_well="A1")],
        shared_params={},
        final_return_location="storage_end",
    )
    sharc = StationBundle(
        client=_ValidationClient(valid=False),
        base_protocol_yaml="protocol:\n  - move:\n      instrument: uv_curing\n      position: plate_holder.plate.A1\n",
    )
    asmi = StationBundle(
        client=_ValidationClient(),
        base_protocol_yaml="protocol:\n  - move:\n      instrument: asmi\n      position: plate.A1\n",
    )

    results = validate_workflow_protocols(exp, sharc=sharc, asmi=asmi)

    assert failed_validation_labels(results) == ["SHARC A1"]
    assert "❌ SHARC A1" in format_validation_report(results)


def test_export_joined_well_csv_includes_data_and_artifact_paths(tmp_path):
    exp = build_workflow_experiment(
        experiment_id="bio1",
        wells=[WorkflowWell(target_well="A1", source_well="A1", uv_exposure_s=11.0)],
        shared_params={
            "volume_ul": 100,
            "uv_intensity": 1,
            "asmi_scalar": {"indentation_limit_height": -3.0},
            "asmi_method_kwargs": {"force_limit": 3.0, "measure_with_return": True},
        },
        final_return_location="storage_end",
    )
    db = tmp_path / "results.db"
    with ResultStore(db) as store:
        store.start_experiment(exp)
        store.set_well_status("bio1", "A1", "done")
        store.record_run(
            run_id="bio1:A1:fill",
            experiment_id="bio1",
            well="A1",
            kind="opentrons_fill",
            station="opentrons",
            success=True,
            result={"success": True, "source_well": "A1", "well": "A1", "volume_dispensed": 100},
        )
        store.record_run(
            run_id="bio1:A1:sharc",
            experiment_id="bio1",
            well="A1",
            kind="sharc",
            station="sharc",
            success=True,
            result=[None, {"exposure_time": 11.0, "intensity": 1}, None],
            artifacts={"run_dir": "/runs/sharc", "result_path": "/runs/sharc/result.json"},
        )
        store.record_run(
            run_id="bio1:A1:asmi",
            experiment_id="bio1",
            well="A1",
            kind="asmi",
            station="asmi",
            success=True,
            result=[
                None,
                {
                    "force_exceeded": False,
                    "measurements": [
                        {
                            "timestamp": 10.0,
                            "z_mm": -1.0,
                            "raw_force_n": 0.12,
                            "corrected_force_n": 0.05,
                            "direction": "down",
                        }
                    ],
                },
                None,
            ],
            artifacts={"run_dir": "/runs/asmi", "result_path": "/runs/asmi/result.json"},
        )

    out = export_joined_well_csv(db, "bio1", tmp_path / "joined.csv")
    with out.open(newline="") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    row = rows[0]
    assert row["uv_exposure_s"] == "11.0"
    assert row["asmi_force_limit"] == "3.0"
    assert row["sharc_result_path"] == "/runs/sharc/result.json"
    assert row["asmi_result_path"] == "/runs/asmi/result.json"
    assert json.loads(row["sharc_cure_json"])["exposure_time"] == 11.0
    assert json.loads(row["asmi_measurements_json"])[0]["z_mm"] == -1.0
    assert json.loads(row["asmi_force_distance_json"])[0]["corrected_force_n"] == 0.05
    assert json.loads(row["asmi_force_recording_json"])[0]["raw_force_n"] == 0.12
