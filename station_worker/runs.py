"""Per-run directory management: save the received YAMLs + the result JSON.

Layout for run_id ``plate_001:A1:asmi``::

    <run_dir>/plate_001_A1_asmi/
        gantry.yaml
        deck.yaml
        protocol.yaml
        result.json        (on success)
        error.txt          (on failure)
        meta.json          (run_id, timestamps, sha256s, metadata, mock_mode)
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

_SAFE_RE = re.compile(r"[^A-Za-z0-9_.\-]+")


def sanitize_run_id(run_id: str) -> str:
    cleaned = _SAFE_RE.sub("_", run_id.strip()) or "run"
    return cleaned[:200]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RunDir:
    def __init__(self, base_dir: Path, run_id: str, *, create: bool = True):
        self.run_id = run_id
        self.dir = Path(base_dir).expanduser() / sanitize_run_id(run_id)
        if create:
            self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def exists(self) -> bool:
        return self.dir.exists()

    # paths
    @property
    def gantry_path(self) -> Path: return self.dir / "gantry.yaml"
    @property
    def deck_path(self) -> Path: return self.dir / "deck.yaml"
    @property
    def protocol_path(self) -> Path: return self.dir / "protocol.yaml"
    @property
    def result_path(self) -> Path: return self.dir / "result.json"
    @property
    def error_path(self) -> Path: return self.dir / "error.txt"
    @property
    def meta_path(self) -> Path: return self.dir / "meta.json"

    # writes
    def write_inputs(self, *, gantry_yaml: str, deck_yaml: str, protocol_yaml: str) -> Dict[str, str]:
        self.gantry_path.write_text(gantry_yaml)
        self.deck_path.write_text(deck_yaml)
        self.protocol_path.write_text(protocol_yaml)
        return {
            "gantry_sha256": sha256_text(gantry_yaml),
            "deck_sha256": sha256_text(deck_yaml),
            "protocol_sha256": sha256_text(protocol_yaml),
        }

    def write_meta(self, meta: Dict[str, Any]) -> None:
        self.meta_path.write_text(json.dumps(meta, indent=2, default=str, sort_keys=True))

    def write_result(self, result: Dict[str, Any]) -> None:
        self.result_path.write_text(json.dumps(result, indent=2, default=str, sort_keys=True))

    def write_error(self, error: str) -> None:
        self.error_path.write_text(error)

    # reads
    def read_result(self) -> Optional[Dict[str, Any]]:
        if self.result_path.exists():
            return json.loads(self.result_path.read_text())
        return None

    def read_protocol(self) -> Optional[str]:
        return self.protocol_path.read_text() if self.protocol_path.exists() else None


def now() -> float:
    return time.time()


__all__ = ["RunDir", "sanitize_run_id", "sha256_text", "now"]
