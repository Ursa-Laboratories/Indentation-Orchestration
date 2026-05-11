"""Load the controller config and build the device clients.

Paths in ``controller.yaml`` (gantry_config / deck_config / base_protocol /
results.db_path) are resolved relative to the config file's directory unless
absolute.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml

from .clients import ArmRailClient, CubOSStationClient, OpentronsClient
from .loop import StationBundle
from .results import ResultStore


# Repo root = parent of the `polymer_indent` package directory. Relative paths
# in controller.yaml (gantry_config / deck_config / base_protocol / db_path)
# resolve against this, so the config is "repo-relative" regardless of where the
# controller.yaml file itself sits.
_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ControllerConfig:
    raw: Dict[str, Any]
    root: Path                       # repo root, for resolving relative paths
    db_path: Path
    mock_mode: bool

    def _abs(self, p: str | Path) -> Path:
        p = Path(p).expanduser()
        return p if p.is_absolute() else (self.root / p)

    # -- builders --------------------------------------------------------

    def station_bundle(self, name: str, *, mock_mode: bool | None = None) -> StationBundle:
        st = self.raw["stations"][name]
        gantry_yaml = self._abs(st["gantry_config"]).read_text()
        deck_yaml = self._abs(st["deck_config"]).read_text()
        base_protocol_yaml = self._abs(st["base_protocol"]).read_text()
        client = CubOSStationClient(
            base_url=st["base_url"],
            station=name,
            gantry_config_yaml=gantry_yaml,
            deck_config_yaml=deck_yaml,
            timeout_s=float(st.get("timeout_s", 900.0)),
            mock_mode=self.mock_mode if mock_mode is None else mock_mode,
        )
        return StationBundle(client=client, base_protocol_yaml=base_protocol_yaml)

    def arm_client(self) -> ArmRailClient:
        a = self.raw.get("arm", {})
        return ArmRailClient(a["base_url"], timeout_s=float(a.get("timeout_s", 300.0)))

    def opentrons_client(self) -> OpentronsClient:
        o = self.raw.get("opentrons", {})
        return OpentronsClient(o.get("base_url"), timeout_s=float(o.get("timeout_s", 600.0)))

    def result_store(self, db_path_override: str | Path | None = None) -> ResultStore:
        path = self._abs(db_path_override) if db_path_override else self.db_path
        return ResultStore(path)


def load_controller_config(path: str | Path) -> ControllerConfig:
    path = Path(path).resolve()
    with path.open() as f:
        raw = yaml.safe_load(f) or {}
    if "stations" not in raw or not isinstance(raw["stations"], dict):
        raise ValueError(f"{path}: missing 'stations:' mapping")
    for required in ("sharc", "asmi"):
        if required not in raw["stations"]:
            raise ValueError(f"{path}: stations.{required} is required")

    db_path = Path(str(raw.get("results", {}).get("db_path", "results/polymer_indent.db"))).expanduser()
    if not db_path.is_absolute():
        db_path = _REPO_ROOT / db_path

    return ControllerConfig(
        raw=raw,
        root=_REPO_ROOT,
        db_path=db_path,
        mock_mode=bool(raw.get("mock_mode", False)),
    )


__all__ = ["ControllerConfig", "load_controller_config"]
