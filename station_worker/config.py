"""Station worker config (configs/stations/<station>.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Set

import yaml


@dataclass
class StationConfig:
    station_id: str
    host: str = "0.0.0.0"
    port: int = 8000
    run_dir: Path = field(default_factory=lambda: Path.home() / "polymer_indent_runs")
    mock_mode_default: bool = False
    allow_instruments: Set[str] = field(default_factory=set)
    allow_commands: Set[str] = field(default_factory=lambda: {"home", "measure", "move"})
    expected_gantry_sha256: Optional[str] = None
    expected_deck_sha256: Optional[str] = None


def load_station_config(path: str | Path) -> StationConfig:
    path = Path(path).expanduser().resolve()
    with path.open() as f:
        raw = yaml.safe_load(f) or {}

    station_id = raw.get("station_id")
    if not station_id:
        raise ValueError(f"{path}: station_id is required")

    execution = raw.get("execution", {}) or {}
    allow = raw.get("allow", {}) or {}

    run_dir = Path(str(raw.get("run_dir", Path.home() / "polymer_indent_runs"))).expanduser()

    return StationConfig(
        station_id=str(station_id),
        host=str(raw.get("host", "0.0.0.0")),
        port=int(raw.get("port", 8000)),
        run_dir=run_dir,
        mock_mode_default=bool(execution.get("mock_mode", False)),
        allow_instruments=set(allow.get("instruments", []) or []),
        allow_commands=set(allow.get("commands", []) or ["home", "measure", "move"]),
        expected_gantry_sha256=raw.get("expected_gantry_sha256"),
        expected_deck_sha256=raw.get("expected_deck_sha256"),
    )


__all__ = ["StationConfig", "load_station_config"]
