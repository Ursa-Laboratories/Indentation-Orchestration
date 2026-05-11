import textwrap

import pytest

from polymer_indent.experiment import load_experiment


def _write(tmp_path, text):
    p = tmp_path / "exp.yaml"
    p.write_text(textwrap.dedent(text))
    return p


def test_loads_and_merges_defaults(tmp_path):
    p = _write(tmp_path, """
        experiment:
          id: e1
          defaults: { volume_ul: 350, uv_intensity: 20 }
          wells:
            A1: { formulation: pegda_5 }
            A2: { formulation: pegda_10, uv_intensity: 40 }
        final_well_return_location: storage_end
    """)
    exp = load_experiment(p)
    assert exp.id == "e1"
    assert exp.wells == ["A1", "A2"]
    assert exp.well_params("A1") == {"volume_ul": 350, "uv_intensity": 20, "formulation": "pegda_5"}
    # per-well overrides win over defaults
    assert exp.well_params("A2")["uv_intensity"] == 40
    assert exp.well_params("A2")["volume_ul"] == 350


def test_return_location_last_vs_nonlast(tmp_path):
    p = _write(tmp_path, """
        experiment:
          id: e2
          wells: { A1: {}, A2: {}, A3: {} }
        final_well_return_location: storage_end
    """)
    exp = load_experiment(p)
    assert exp.return_location("A1") == "opentrons"
    assert exp.return_location("A2") == "opentrons"
    assert exp.return_location("A3") == "storage_end"


def test_default_final_return_is_storage_end(tmp_path):
    p = _write(tmp_path, """
        experiment:
          id: e3
          wells: { A1: {} }
    """)
    exp = load_experiment(p)
    assert exp.return_location("A1") == "storage_end"


def test_well_ids_normalized_and_validated(tmp_path):
    p = _write(tmp_path, """
        experiment:
          id: e4
          wells: { a1: { x: 1 }, B12: {} }
    """)
    exp = load_experiment(p)
    assert exp.wells == ["A1", "B12"]


def test_bad_well_id_rejected(tmp_path):
    p = _write(tmp_path, """
        experiment:
          id: e5
          wells: { hello: {} }
    """)
    with pytest.raises(ValueError):
        load_experiment(p)


def test_missing_experiment_rejected(tmp_path):
    p = _write(tmp_path, "wells: { A1: {} }\n")
    with pytest.raises(ValueError):
        load_experiment(p)
