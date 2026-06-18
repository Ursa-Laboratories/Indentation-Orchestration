"""Loop behavior with fake device clients — asserts the per-well call order,
last-well routing, bookkeeping, resume, and failure handling.
"""

import pytest

from polymer_indent.experiment import load_experiment
from polymer_indent.loop import RunSafetyChecks, StationBundle, run_experiment
from polymer_indent.results import ResultStore

_SHARC_BASE = "protocol:\n  - home:\n  - measure:\n      instrument: uv_curing\n      position: plate_holder.plate.A1\n  - home:\n"
_ASMI_BASE = "protocol:\n  - home:\n  - measure:\n      instrument: asmi\n      position: plate.A1\n  - home:\n"


class FakeOpentrons:
    def __init__(self):
        self.calls = []

    def run_fill(self, *, well, volume_ul, source_well=None, formulation=None, run_id=None, **kwargs):
        self.calls.append(("fill", well, volume_ul, source_well, formulation, run_id, kwargs))
        return {"success": True, "well": well, "volume_dispensed": volume_ul}


class FakeArm:
    def __init__(self):
        self.transfers = []

    def transfer(self, *, from_location, to_location, run_id=None, mock_mode=None, skip_safe_prelude=False):
        self.transfers.append((from_location, to_location, run_id, mock_mode, skip_safe_prelude))
        return {"success": True, "from": from_location, "to": to_location}


class FakeStation:
    def __init__(self, name, *, fail_on_well=None):
        self.name = name
        self.runs = []
        self.fail_on_well = fail_on_well

    def run_protocol(self, *, run_id, protocol_yaml, metadata=None, mock_mode=None):
        self.runs.append((run_id, protocol_yaml, metadata, mock_mode))
        well = (metadata or {}).get("well")
        step = (metadata or {}).get("step")
        # Only fail the measurement step, not the pre-deposit home — the
        # failure tests are asserting cure/indent failures, not home failures.
        if well == self.fail_on_well and step == self.name:
            from polymer_indent.clients import StationRunError

            raise StationRunError(self.name, run_id, {"error": "boom"})
        return {"success": True, "run_id": run_id, "station_id": self.name,
                "results": [None, {"ok": True}, None], "artifacts": {"run_dir": f"/runs/{run_id}"}}


def _exp(tmp_path, wells=("A1", "A2", "A3"), final="storage_end"):
    if isinstance(wells, str):
        wells = [wells]
    lines = ["experiment:", "  id: e1", "  wells:"]
    lines += [f"    {w}: {{}}" for w in wells]
    lines.append(f"final_well_return_location: {final}")
    p = tmp_path / "exp.yaml"
    p.write_text("\n".join(lines) + "\n")
    return load_experiment(p)


def _bundles():
    return (
        StationBundle(client=FakeStation("sharc"), base_protocol_yaml=_SHARC_BASE),
        StationBundle(client=FakeStation("asmi"), base_protocol_yaml=_ASMI_BASE),
    )


def test_per_well_sequence_and_last_well_routing(tmp_path):
    exp = _exp(tmp_path)
    ot, arm = FakeOpentrons(), FakeArm()
    sharc, asmi = _bundles()
    with ResultStore(tmp_path / "r.db") as results:
        failed = run_experiment(exp, opentrons=ot, arm=arm, sharc=sharc, asmi=asmi,
                                results=results, mock_mode=True)
        assert failed == 0
        # 3 wells * 3 transfers each
        assert len(arm.transfers) == 9
        # first well's three legs:
        assert arm.transfers[0][:2] == ("opentrons", "uv_station")
        assert arm.transfers[1][:2] == ("uv_station", "asmi")
        assert arm.transfers[2][:2] == ("asmi", "opentrons")          # non-last well returns to opentrons
        # last well's return leg goes to storage_end
        assert arm.transfers[-1][:2] == ("asmi", "storage_end")
        # each station ran twice per well: pre-deposit home + the actual step
        assert [r[0] for r in sharc.client.runs] == [
            "e1:A1:home-sharc", "e1:A1:sharc",
            "e1:A2:home-sharc", "e1:A2:sharc",
            "e1:A3:home-sharc", "e1:A3:sharc",
        ]
        assert [r[0] for r in asmi.client.runs] == [
            "e1:A1:home-asmi", "e1:A1:asmi",
            "e1:A2:home-asmi", "e1:A2:asmi",
            "e1:A3:home-asmi", "e1:A3:asmi",
        ]
        # protocol sent to SHARC for well A2's cure has the well swapped in
        a2_proto = next(p for rid, p, *_ in sharc.client.runs if rid == "e1:A2:sharc")
        assert "plate_holder.plate.A2" in a2_proto and "plate_holder.plate.A1" not in a2_proto
        # bookkeeping
        assert results.well_status("e1", "A3") == "done"
        kinds = {row["kind"] for row in results.runs_for_well("e1", "A1")}
        assert kinds == {"opentrons_fill", "arm_transfer", "sharc_home", "sharc", "asmi_home", "asmi"}


def test_source_well_params_are_passed_to_opentrons(tmp_path):
    exp = _exp(tmp_path, wells=["A1"])
    exp.params["A1"].update({
        "source_well": "B1",
        "volume_ul": 100,
        "flow_rate_ul_min": 150,
        "air_expulsion_ul": 20,
        "tip_lift_height_mm": 8,
    })
    ot, arm = FakeOpentrons(), FakeArm()
    sharc, asmi = _bundles()
    with ResultStore(tmp_path / "r.db") as results:
        run_experiment(exp, opentrons=ot, arm=arm, sharc=sharc, asmi=asmi,
                       results=results, mock_mode=True)
    assert ot.calls == [
        ("fill", "A1", 100, "B1", None, "e1:A1:fill",
         {"flow_rate_ul_min": 150, "air_expulsion_ul": 20, "tip_lift_height_mm": 8,
          "tip_rack_slot": "A2", "tube_rack_slot": "B2", "plate_slot": "D2",
          "plate_labware": "corning_96_wellplate_360ul_flat"})
    ]


def test_per_well_uv_exposure_overrides_sharc_yaml(tmp_path):
    """Each well's params['uv_exposure_s'] should be injected into the SHARC YAML
    before render, so different wells get different cure times."""
    exp = _exp(tmp_path, wells=["A1", "A2"])
    exp.params["A1"]["uv_exposure_s"] = 1.0
    exp.params["A2"]["uv_exposure_s"] = 3.0
    sharc, asmi = _bundles()
    with ResultStore(tmp_path / "r.db") as results:
        run_experiment(exp, opentrons=FakeOpentrons(), arm=FakeArm(),
                       sharc=sharc, asmi=asmi, results=results, mock_mode=True)
    cure_yamls = {rid: p for rid, p, *_ in sharc.client.runs
                  if rid.endswith(":sharc")}
    assert "exposure_time: 1.0" in cure_yamls["e1:A1:sharc"]
    assert "exposure_time: 3.0" in cure_yamls["e1:A2:sharc"]
    # The pre-deposit home YAML is unaffected — no measure step to override.
    home_yaml = next(p for rid, p, *_ in sharc.client.runs if rid == "e1:A1:home-sharc")
    assert "exposure_time" not in home_yaml


def test_per_well_asmi_overrides_apply_to_protocol(tmp_path):
    exp = _exp(tmp_path, wells=["A1", "A2"])
    exp.params["A1"]["asmi_scalar"] = {"indentation_limit_height": -4.0}
    exp.params["A1"]["asmi_method_kwargs"] = {"force_limit": 4.0, "step_size": 0.02}
    exp.params["A2"]["asmi_indentation_limit_height"] = -6.0
    exp.params["A2"]["asmi_force_limit"] = 6.0
    sharc, asmi = _bundles()
    with ResultStore(tmp_path / "r.db") as results:
        run_experiment(exp, opentrons=FakeOpentrons(), arm=FakeArm(),
                       sharc=sharc, asmi=asmi, results=results, mock_mode=True)

    asmi_yamls = {rid: p for rid, p, *_ in asmi.client.runs if rid.endswith(":asmi")}
    assert "indentation_limit_height: -4.0" in asmi_yamls["e1:A1:asmi"]
    assert "force_limit: 4.0" in asmi_yamls["e1:A1:asmi"]
    assert "step_size: 0.02" in asmi_yamls["e1:A1:asmi"]
    assert "indentation_limit_height: -6.0" in asmi_yamls["e1:A2:asmi"]
    assert "force_limit: 6.0" in asmi_yamls["e1:A2:asmi"]


def test_asmi_safety_checks_pause_slide_out_and_position_before_indent(tmp_path):
    exp = _exp(tmp_path, wells=["A1"])
    sharc, asmi = _bundles()
    arm = FakeArm()
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return True

    with ResultStore(tmp_path / "r.db") as results:
        run_experiment(
            exp, opentrons=FakeOpentrons(), arm=arm, sharc=sharc, asmi=asmi,
            results=results, mock_mode=True,
            safety_checks=RunSafetyChecks(confirm=confirm),
        )

    assert prompts == [
        "Plate is at ASMI slide-out. Type 'yes' to push into ASMI; "
        "anything else returns the plate to Opentrons and aborts: ",
        "ASMI is positioned at A1 +10 mm. "
        "Type 'yes' to continue with indentation; anything else aborts: ",
    ]
    assert arm.transfers[1][:2] == ("uv_station", "asmi_pre_push")
    assert arm.transfers[2][:2] == ("asmi_pre_push", "asmi")
    assert arm.transfers[2][4] is True
    run_ids = [run_id for run_id, *_ in asmi.client.runs]
    assert run_ids == ["e1:A1:home-asmi", "e1:A1:asmi-position-check", "e1:A1:asmi"]
    position_check = next(p for rid, p, *_ in asmi.client.runs
                          if rid == "e1:A1:asmi-position-check")
    assert "position: plate.A1" in position_check
    assert "method: measure" in position_check
    assert "measurement_height: 10.0" in position_check
    assert "indentation_limit_height" not in position_check


def test_asmi_slide_out_reject_returns_to_opentrons_and_aborts(tmp_path):
    exp = _exp(tmp_path, wells=["A1"])
    sharc, asmi = _bundles()
    arm = FakeArm()

    with ResultStore(tmp_path / "r.db") as results:
        with pytest.raises(RuntimeError, match="operator aborted before ASMI slide-in"):
            run_experiment(
                exp, opentrons=FakeOpentrons(), arm=arm, sharc=sharc, asmi=asmi,
                results=results, mock_mode=True,
                safety_checks=RunSafetyChecks(confirm=lambda _prompt: False),
            )

    assert arm.transfers[1][:2] == ("uv_station", "asmi_pre_push")
    assert arm.transfers[2][:2] == ("asmi_pre_push", "opentrons")
    assert arm.transfers[2][4] is True
    assert [run_id for run_id, *_ in asmi.client.runs] == ["e1:A1:home-asmi"]


def test_asmi_position_check_reject_aborts_before_indent(tmp_path):
    exp = _exp(tmp_path, wells=["A1"])
    sharc, asmi = _bundles()
    answers = iter([True, False])

    with ResultStore(tmp_path / "r.db") as results:
        with pytest.raises(RuntimeError, match="operator aborted after ASMI position check"):
            run_experiment(
                exp, opentrons=FakeOpentrons(), arm=FakeArm(), sharc=sharc, asmi=asmi,
                results=results, mock_mode=True,
                safety_checks=RunSafetyChecks(confirm=lambda _prompt: next(answers)),
            )

    assert [run_id for run_id, *_ in asmi.client.runs] == [
        "e1:A1:home-asmi",
        "e1:A1:asmi-position-check",
    ]


def test_mock_mode_propagates_to_stations(tmp_path):
    exp = _exp(tmp_path, wells=["A1"])
    sharc, asmi = _bundles()
    with ResultStore(tmp_path / "r.db") as results:
        run_experiment(exp, opentrons=FakeOpentrons(), arm=FakeArm(), sharc=sharc, asmi=asmi,
                       results=results, mock_mode=True)
    # both the pre-deposit home and the per-well step honor mock_mode
    assert all(r[3] is True for r in sharc.client.runs)
    assert all(r[3] is True for r in asmi.client.runs)


def test_mock_modes_per_device_overrides(tmp_path):
    """run_experiment(mock_modes={...}) routes per-device, overriding the universal mock_mode."""
    exp = _exp(tmp_path, wells=["A1"])
    sharc, asmi = _bundles()
    arm = FakeArm()
    with ResultStore(tmp_path / "r.db") as results:
        run_experiment(
            exp, opentrons=FakeOpentrons(), arm=arm, sharc=sharc, asmi=asmi,
            results=results,
            mock_mode=False,
            mock_modes={"sharc": True, "asmi": False, "arm": True},
        )
    # SHARC got mock=True, ASMI got mock=False, arm got mock=True
    assert sharc.client.runs[0][3] is True, "sharc should have been mocked"
    assert asmi.client.runs[0][3] is False, "asmi should NOT have been mocked"
    # arm.transfers tuples: (from, to, run_id, mock_mode); _transfer sends True if arm_mock else None
    assert all(t[3] is True for t in arm.transfers), f"all arm transfers should have mock_mode=True, got {arm.transfers}"


def test_failure_records_failed_row_for_the_failed_step(tmp_path):
    """When SHARC fails, a runs row with success=0 + error column should be recorded
    BEFORE the exception propagates — the audit trail must capture the failed leg."""
    exp = _exp(tmp_path, wells=["A1"])
    sharc = StationBundle(client=FakeStation("sharc", fail_on_well="A1"), base_protocol_yaml=_SHARC_BASE)
    asmi = StationBundle(client=FakeStation("asmi"), base_protocol_yaml=_ASMI_BASE)
    with ResultStore(tmp_path / "r.db") as results:
        with pytest.raises(Exception):
            run_experiment(exp, opentrons=FakeOpentrons(), arm=FakeArm(), sharc=sharc, asmi=asmi,
                           results=results, mock_mode=False)
        rows = list(results.runs_for_well("e1", "A1"))
        kinds_by_success = {(r["kind"], r["success"]) for r in rows}
        # opentrons fill (success), opentrons->uv arm transfer (success), then SHARC FAIL
        assert ("opentrons_fill", 1) in kinds_by_success
        assert ("arm_transfer", 1) in kinds_by_success
        assert ("sharc", 0) in kinds_by_success, f"SHARC failure row should be recorded; got {kinds_by_success}"
        # The SHARC row should have the error column populated
        sharc_row = next(r for r in rows if r["kind"] == "sharc")
        assert sharc_row["error"] and "boom" in sharc_row["error"], sharc_row["error"]


def test_only_wells(tmp_path):
    exp = _exp(tmp_path)
    sharc, asmi = _bundles()
    with ResultStore(tmp_path / "r.db") as results:
        run_experiment(exp, opentrons=FakeOpentrons(), arm=FakeArm(), sharc=sharc, asmi=asmi,
                       results=results, mock_mode=True, only_wells=["A2"])
    assert [r[0] for r in sharc.client.runs] == ["e1:A2:home-sharc", "e1:A2:sharc"]


def test_failure_aborts_by_default(tmp_path):
    exp = _exp(tmp_path)
    sharc = StationBundle(client=FakeStation("sharc", fail_on_well="A2"), base_protocol_yaml=_SHARC_BASE)
    asmi = StationBundle(client=FakeStation("asmi"), base_protocol_yaml=_ASMI_BASE)
    with ResultStore(tmp_path / "r.db") as results:
        with pytest.raises(Exception):
            run_experiment(exp, opentrons=FakeOpentrons(), arm=FakeArm(), sharc=sharc, asmi=asmi,
                           results=results, mock_mode=True)
        assert results.well_status("e1", "A1") == "done"
        assert results.well_status("e1", "A2") == "failed"
        assert results.well_status("e1", "A3") == "pending"


def test_continue_on_error(tmp_path):
    exp = _exp(tmp_path)
    sharc = StationBundle(client=FakeStation("sharc", fail_on_well="A2"), base_protocol_yaml=_SHARC_BASE)
    asmi = StationBundle(client=FakeStation("asmi"), base_protocol_yaml=_ASMI_BASE)
    with ResultStore(tmp_path / "r.db") as results:
        failed = run_experiment(exp, opentrons=FakeOpentrons(), arm=FakeArm(), sharc=sharc, asmi=asmi,
                                results=results, mock_mode=True, continue_on_error=True)
        assert failed == 1
        assert results.well_status("e1", "A1") == "done"
        assert results.well_status("e1", "A2") == "failed"
        assert results.well_status("e1", "A3") == "done"


def test_resume_skips_done(tmp_path):
    exp = _exp(tmp_path)
    sharc, asmi = _bundles()
    with ResultStore(tmp_path / "r.db") as results:
        results.start_experiment(exp)
        results.set_well_status("e1", "A1", "done")
        run_experiment(exp, opentrons=FakeOpentrons(), arm=FakeArm(), sharc=sharc, asmi=asmi,
                       results=results, mock_mode=True, resume=True)
    assert [r[0] for r in sharc.client.runs] == [
        "e1:A2:home-sharc", "e1:A2:sharc",
        "e1:A3:home-sharc", "e1:A3:sharc",
    ]
