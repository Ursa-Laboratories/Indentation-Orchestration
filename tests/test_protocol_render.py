import re

import pytest
import yaml

from polymer_indent.protocol_render import render_protocol


def test_swaps_well_in_asmi_base(asmi_base_protocol):
    out = render_protocol(asmi_base_protocol, "B7")
    doc = yaml.safe_load(out)
    positions = [
        step["measure"]["position"]
        for step in doc["protocol"]
        if isinstance(step.get("measure"), dict) and "position" in step["measure"]
    ]
    assert positions == ["plate.B7"]
    # other content untouched
    assert "park_position" in doc["positions"]
    assert any("indentation" == step.get("measure", {}).get("method") for step in doc["protocol"])


def test_swaps_well_in_sharc_base(sharc_base_protocol):
    out = render_protocol(sharc_base_protocol, "h12")  # lowercase ok
    doc = yaml.safe_load(out)
    positions = [
        step["measure"]["position"]
        for step in doc["protocol"]
        if isinstance(step.get("measure"), dict) and "position" in step["measure"]
    ]
    assert positions == ["plate_holder.plate.H12"]
    # comments preserved (it's a text edit, not a redump)
    assert out.lstrip().startswith("#")


def test_placeholder_token():
    base = "protocol:\n  - measure:\n      position: plate_holder.plate.{{WELL}}\n"
    assert "plate_holder.plate.C3" in render_protocol(base, "C3")


def test_idempotent_full_scan_is_unchanged_when_no_single_well():
    # A scan protocol references `plate:` (a labware key, no well) — there's no
    # rewritable well reference, so render_protocol should refuse rather than
    # silently no-op.
    scan = "protocol:\n  - scan:\n      plate: plate\n      instrument: asmi\n"
    with pytest.raises(ValueError):
        render_protocol(scan, "A1")


def test_rejects_bad_well():
    with pytest.raises(ValueError):
        render_protocol("protocol:\n  - measure:\n      position: plate.A1\n", "not-a-well")


def test_multiple_references_all_rewritten():
    base = (
        "protocol:\n"
        "  - move:\n      instrument: asmi\n      position: plate.A1\n"
        "  - measure:\n      instrument: asmi\n      position: plate.A1\n"
    )
    out = render_protocol(base, "D4")
    assert len(re.findall(r"plate\.D4", out)) == 2
    assert "plate.A1" not in out
