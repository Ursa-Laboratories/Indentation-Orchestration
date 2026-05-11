"""Client for the xArm + Vention-rail transfer worker.

Matches the existing denos arm worker contract:
    GET  /health
    POST /run    {"from": <location>, "to": <location>}   -> {"success": bool, ...}
    POST /stop

Locations: ``opentrons``, ``uv_station``, ``asmi``, ``storage_end`` (and
``storage_start``). The worker has a fixed route table; the controller only
names the endpoints.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ._http import HttpError, get_json, new_session, post_json

log = logging.getLogger("polymer_indent.arm")


class ArmTransferError(RuntimeError):
    def __init__(self, from_location: str, to_location: str, payload: Dict[str, Any]):
        self.from_location = from_location
        self.to_location = to_location
        self.payload = payload
        msg = payload.get("error") or "transfer failed (no error message)"
        super().__init__(f"arm transfer {from_location} -> {to_location} failed: {msg}")


class ArmRailClient:
    def __init__(self, base_url: str, *, timeout_s: float = 300.0, session: Any | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._session = session or new_session()

    def health(self) -> Dict[str, Any]:
        return get_json(self._session, f"{self.base_url}/health", timeout=15.0)

    def transfer(
        self,
        *,
        from_location: str,
        to_location: str,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Move the plate from one workcell location to another.

        ``run_id`` is for logging only (the existing worker doesn't accept it);
        it's sent as a best-effort hint and ignored if unsupported.
        """
        payload: Dict[str, Any] = {"from": from_location, "to": to_location}
        if run_id:
            payload["run_id"] = run_id
        log.info("arm transfer %s -> %s (run_id=%s)", from_location, to_location, run_id)
        resp = post_json(
            self._session, f"{self.base_url}/run", payload, timeout=self.timeout_s
        )
        if not resp.get("success", False):
            raise ArmTransferError(from_location, to_location, resp)
        return resp

    def stop(self) -> Dict[str, Any]:
        return post_json(self._session, f"{self.base_url}/stop", {}, timeout=15.0)


__all__ = ["ArmRailClient", "ArmTransferError", "HttpError"]
