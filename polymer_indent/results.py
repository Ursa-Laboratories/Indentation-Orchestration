"""SQLite bookkeeping for experiments / wells / station runs.

Lightweight on purpose — no ORM. The raw protocol YAML and result JSON are
stored as TEXT columns so a run is fully replayable from the DB alone, and the
``runs`` table doubles as the controller-side audit trail (the station Pi also
keeps its own run dirs).

Schema::

    experiments(experiment_id PK, created_at, status, config_json)
    wells(experiment_id, well, status, params_json, created_at, updated_at,
          error,                       PRIMARY KEY(experiment_id, well))
    runs(run_id PK, experiment_id, well, kind, station, started_at, finished_at,
         success, protocol_yaml, result_json, artifacts_json, error)

``kind`` is one of: ``opentrons_fill``, ``arm_transfer``, ``sharc``, ``asmi``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    created_at    REAL NOT NULL,
    status        TEXT NOT NULL,
    config_json   TEXT
);
CREATE TABLE IF NOT EXISTS wells (
    experiment_id TEXT NOT NULL,
    well          TEXT NOT NULL,
    status        TEXT NOT NULL,
    params_json   TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    error         TEXT,
    PRIMARY KEY (experiment_id, well)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    experiment_id  TEXT NOT NULL,
    well           TEXT,
    kind           TEXT NOT NULL,
    station        TEXT,
    started_at     REAL NOT NULL,
    finished_at    REAL,
    success        INTEGER,
    protocol_yaml  TEXT,
    result_json    TEXT,
    artifacts_json TEXT,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_exp_well ON runs (experiment_id, well);
"""


def _now() -> float:
    return time.time()


def _dump(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    return json.dumps(obj, default=str, sort_keys=True)


class ResultStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        if self.db_path.parent and not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ResultStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- experiments / wells --------------------------------------------

    def start_experiment(self, experiment) -> None:
        """Upsert the experiment + its wells (idempotent — supports --resume)."""
        now = _now()
        with self._conn:
            self._conn.execute(
                """INSERT INTO experiments (experiment_id, created_at, status, config_json)
                   VALUES (?, ?, 'running', ?)
                   ON CONFLICT(experiment_id) DO UPDATE SET status='running'""",
                (experiment.id, now, _dump(getattr(experiment, "raw", None))),
            )
            for well in experiment.wells:
                self._conn.execute(
                    """INSERT INTO wells (experiment_id, well, status, params_json,
                                          created_at, updated_at)
                       VALUES (?, ?, 'pending', ?, ?, ?)
                       ON CONFLICT(experiment_id, well) DO NOTHING""",
                    (experiment.id, well, _dump(experiment.params[well]), now, now),
                )

    def set_well_status(
        self,
        experiment_id: str,
        well: str,
        status: str,
        *,
        error: Optional[str] = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                """UPDATE wells SET status=?, error=?, updated_at=?
                   WHERE experiment_id=? AND well=?""",
                (status, error, _now(), experiment_id, well),
            )

    def well_status(self, experiment_id: str, well: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT status FROM wells WHERE experiment_id=? AND well=?",
            (experiment_id, well),
        ).fetchone()
        return row["status"] if row else None

    def done_wells(self, experiment_id: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT well FROM wells WHERE experiment_id=? AND status='done'",
            (experiment_id,),
        ).fetchall()
        return {r["well"] for r in rows}

    def finish_experiment(self, experiment_id: str, status: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE experiments SET status=? WHERE experiment_id=?",
                (status, experiment_id),
            )

    # -- runs ------------------------------------------------------------

    def record_run(
        self,
        *,
        run_id: str,
        experiment_id: str,
        well: Optional[str],
        kind: str,
        station: Optional[str] = None,
        success: Optional[bool] = None,
        started_at: Optional[float] = None,
        finished_at: Optional[float] = None,
        protocol_yaml: Optional[str] = None,
        result: Any = None,
        artifacts: Any = None,
        error: Optional[str] = None,
    ) -> None:
        """Insert or update a run row (keyed on run_id)."""
        started = started_at if started_at is not None else _now()
        with self._conn:
            self._conn.execute(
                """INSERT INTO runs (run_id, experiment_id, well, kind, station,
                        started_at, finished_at, success, protocol_yaml,
                        result_json, artifacts_json, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(run_id) DO UPDATE SET
                        finished_at=excluded.finished_at,
                        success=excluded.success,
                        result_json=excluded.result_json,
                        artifacts_json=excluded.artifacts_json,
                        error=excluded.error""",
                (
                    run_id, experiment_id, well, kind, station,
                    started, finished_at,
                    None if success is None else int(bool(success)),
                    protocol_yaml, _dump(result), _dump(artifacts), error,
                ),
            )

    # -- read-back -------------------------------------------------------

    def runs_for_well(self, experiment_id: str, well: str) -> Iterable[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM runs WHERE experiment_id=? AND well=? ORDER BY started_at",
            (experiment_id, well),
        ).fetchall()


__all__ = ["ResultStore"]
