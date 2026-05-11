from polymer_indent.experiment import load_experiment
from polymer_indent.results import ResultStore


def _exp(tmp_path):
    p = tmp_path / "exp.yaml"
    p.write_text(
        "experiment:\n  id: e1\n  wells:\n    A1: { formulation: pegda_5 }\n    A2: {}\n"
    )
    return load_experiment(p)


def test_start_experiment_is_idempotent(tmp_path):
    exp = _exp(tmp_path)
    db = tmp_path / "r.db"
    with ResultStore(db) as store:
        store.start_experiment(exp)
        store.start_experiment(exp)  # second call must not blow up or duplicate
        assert store.well_status("e1", "A1") == "pending"
        assert store.done_wells("e1") == set()


def test_well_status_and_done_wells(tmp_path):
    exp = _exp(tmp_path)
    with ResultStore(tmp_path / "r.db") as store:
        store.start_experiment(exp)
        store.set_well_status("e1", "A1", "done")
        store.set_well_status("e1", "A2", "failed", error="boom")
        assert store.well_status("e1", "A1") == "done"
        assert store.well_status("e1", "A2") == "failed"
        assert store.done_wells("e1") == {"A1"}


def test_store_records_two_station_runs(tmp_path):
    exp = _exp(tmp_path)
    with ResultStore(tmp_path / "r.db") as store:
        store.start_experiment(exp)
        store.store(
            experiment_id="e1",
            well="A1",
            sharc={"success": True, "results": [None, {"cure": "ok"}, None], "artifacts": {"run_dir": "/x"}},
            asmi={"success": True, "results": [None, {"force": [1, 2, 3]}, None, None]},
            sharc_run_id="e1:A1:sharc",
            asmi_run_id="e1:A1:asmi",
            sharc_protocol_yaml="protocol:\n  - home:\n",
            asmi_protocol_yaml="protocol:\n  - home:\n",
        )
        rows = list(store.runs_for_well("e1", "A1"))
        kinds = {r["kind"] for r in rows}
        assert kinds == {"sharc", "asmi"}
        sharc_row = next(r for r in rows if r["kind"] == "sharc")
        assert sharc_row["success"] == 1
        assert "cure" in sharc_row["result_json"]
        assert sharc_row["protocol_yaml"].startswith("protocol:")


def test_resume_skips_done(tmp_path):
    exp = _exp(tmp_path)
    with ResultStore(tmp_path / "r.db") as store:
        store.start_experiment(exp)
        store.set_well_status("e1", "A1", "done")
    # reopen — state persists
    with ResultStore(tmp_path / "r.db") as store:
        assert store.done_wells("e1") == {"A1"}
