#!/usr/bin/env python3
"""
Bridge the workcell SQLite result store to the ASMI_new analysis pipeline.

Reads ASMI rows for ``EXPERIMENT_ID`` from ``results/polymer_indent.db``, writes
each well as a CSV in the 5-column layout that ``src.analysis.IndentationAnalyzer``
expects, then hands off to the helpers in ``main_asmi_with_curetime.py``:

    split_up_down_csv  · analyze_file  · write_summary_csv

The template's module-level imports require a few ASMI_new modules
(``src.ForceMonitoring``, ``src.CNCController``, ``src.ForceSensor``) that the
*measurement* path needs but the *analysis* path doesn't. We stub them in
``sys.modules`` so the import succeeds and only the analysis helpers run.

Edit the SETTINGS block and run with a Python that has numpy/scipy/matplotlib
(e.g. the ASMI_new venv, not polymer_indent's minimal controller venv):
    /path/to/ASMI_new/.venv/bin/python scripts/analyze_experiment.py
"""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
import types
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent

# =============================================================================
# SETTINGS — edit these
# =============================================================================
EXPERIMENT_ID = "bioadhesives_pilot_full_loop"
RESULTS_DB = REPO_ROOT / "results/polymer_indent.db"
OUTPUT_ROOT = REPO_ROOT / "results/measurements"

# Path to an ASMI_new checkout. We add it to ``sys.path`` so ``src.analysis`` /
# ``src.plot`` / ``src.version`` resolve when the template imports them.
ASMI_NEW_PATH = Path("/Users/charl/Programming/panda/ASMI_new")

CONTACT_METHOD = "retrospective"   # "extrapolation" | "retrospective" | "simple_threshold"
FIT_METHOD = "hertzian"            # "hertzian" | "linear"
APPLY_SYSTEM_CORRECTION = True
# =============================================================================


def _stub_missing_template_modules() -> None:
    """Insert empty stand-ins for the ASMI_new measurement modules.

    ``main_asmi_with_curetime.py`` imports symbols from ``src.ForceMonitoring``,
    ``src.CNCController``, ``src.ForceSensor`` at module scope. Those modules
    don't exist in this repo (the template's measurement path runs against the
    standalone ASMI rig). The analysis helpers we want — ``split_up_down_csv``,
    ``analyze_file``, ``write_summary_csv``, ``plot_results_via_plotter`` —
    don't use any of those symbols, so stubbing them lets the template import
    cleanly.
    """
    stubs = {
        "src.ForceMonitoring": ["simple_indentation_measurement",
                                "simple_indentation_with_return_measurement",
                                "get_and_increment_run_count"],
        "src.CNCController":   ["CNCController"],
        "src.ForceSensor":     ["ForceSensor"],
    }
    # Ensure the parent ``src`` package exists so the dotted imports resolve.
    sys.modules.setdefault("src", types.ModuleType("src"))
    for mod_name, names in stubs.items():
        if mod_name in sys.modules:
            continue
        mod = types.ModuleType(mod_name)
        for name in names:
            setattr(mod, name, None)
        sys.modules[mod_name] = mod


def _bootstrap_template() -> Any:
    """Make the template importable and return the module."""
    sys.path.insert(0, str(ASMI_NEW_PATH))  # src.analysis, src.plot, src.version
    sys.path.insert(0, str(REPO_ROOT))      # main_asmi_with_curetime
    _stub_missing_template_modules()
    import main_asmi_with_curetime  # noqa: PLC0415
    return main_asmi_with_curetime


def load_asmi_runs(db_path: Path, experiment_id: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (well, indentation_dict) for each successful ASMI step in ``experiment_id``.

    ``result_json`` is the cubos ``scan`` return — a single ``{well_id: indentation_result}``
    entry because the workcell rewrites the YAML to one well per run.
    """
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT well, result_json FROM runs "
            "WHERE experiment_id = ? AND kind = 'asmi' AND success = 1 "
            "ORDER BY started_at",
            (experiment_id,),
        ).fetchall()
    finally:
        con.close()
    for well, result_json in rows:
        if not result_json:
            continue
        payload = json.loads(result_json)
        for _well_id, indentation in payload.items():
            if isinstance(indentation, dict) and "measurements" in indentation:
                yield well, indentation


def write_well_csv(out_path: Path, well: str, indentation: dict[str, Any]) -> None:
    """Write one well's indentation in the 5-column layout IndentationAnalyzer reads.

    Metadata mirrors the legacy ``simple_indentation_measurement`` header
    closely enough for ``determine_poisson_ratio`` and ``detect_force_limit_reached``.
    """
    measurements = indentation["measurements"]
    if not measurements:
        return
    baseline_avg = indentation.get("baseline_avg", 0.0)
    baseline_std = indentation.get("baseline_std", 0.0)
    force_exceeded = indentation.get("force_exceeded", False)
    t0 = measurements[0]["timestamp"]
    target_z = min(m["z_mm"] for m in measurements)

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Test_Time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        w.writerow(["Well", well])
        w.writerow(["Target_Z(mm)", f"{target_z:.3f}"])
        w.writerow(["Force_Exceeded", str(force_exceeded)])
        w.writerow(["Baseline_Force(N)", f"{baseline_avg:.4f}"])
        w.writerow(["Baseline_Std(N)", f"{baseline_std:.4f}"])
        w.writerow([])
        w.writerow(["Timestamp(s)", "Z_Position(mm)", "Raw_Force(N)",
                    "Corrected_Force(N)", "Direction"])
        for m in measurements:
            w.writerow([
                f"{m['timestamp'] - t0:.3f}",
                f"{m['z_mm']:.4f}",
                f"{m.get('raw_force_n', 0.0):.4f}",
                f"{m.get('corrected_force_n', 0.0):.4f}",
                m.get("direction", "down"),
            ])


def main() -> int:
    if not RESULTS_DB.exists():
        print(f"❌ results DB not found: {RESULTS_DB}")
        return 1

    runs = list(load_asmi_runs(RESULTS_DB, EXPERIMENT_ID))
    if not runs:
        print(f"❌ no successful ASMI rows for experiment {EXPERIMENT_ID!r}")
        return 1

    template = _bootstrap_template()  # imports split_up_down_csv / analyze_file / write_summary_csv

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_folder_name = f"run_{EXPERIMENT_ID}_{stamp}"
    run_dir = OUTPUT_ROOT / run_folder_name
    run_dir.mkdir(parents=True, exist_ok=True)

    measure_with_return = any(idn.get("measure_with_return") for _, idn in runs)
    print(f"📁 writing {len(runs)} per-well CSV(s) to {run_dir}  (measure_with_return={measure_with_return})")

    # First pass: emit per-well CSVs, splitting direction-tagged data if present.
    analysis_specs: list[tuple[Path, str]] = []  # (csv_path, well_label)
    for well, indentation in runs:
        csv_path = run_dir / f"well_{well}_{stamp}.csv"
        write_well_csv(csv_path, well, indentation)
        if measure_with_return:
            down_path, up_path = template.split_up_down_csv(str(csv_path))
            if down_path:
                analysis_specs.append((Path(down_path), f"{well}_down"))
            if up_path:
                analysis_specs.append((Path(up_path), f"{well}_up"))
        else:
            analysis_specs.append((csv_path, well.upper()))

    # Second pass: reuse the template's analyze_file (calls IndentationAnalyzer
    # + plot_results_via_plotter) and write_summary_csv.
    results = []
    for csv_path, well_label in analysis_specs:
        result = template.analyze_file(
            datafile=str(csv_path),
            well=well_label,
            contact_method=CONTACT_METHOD,
            fit_method=FIT_METHOD,
            apply_system_correction=APPLY_SYSTEM_CORRECTION,
        )
        if result:
            results.append(result)

    summary_csv = template.write_summary_csv(run_folder_name, results)
    print(f"📊 fit {len(results)}/{len(analysis_specs)} wells · summary={summary_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
