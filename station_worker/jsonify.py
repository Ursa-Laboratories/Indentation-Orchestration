"""Make arbitrary cubos result objects JSON-serializable.

cubos command results are a mix of plain dicts (ASMI ``indentation`` returns a
dict), dataclasses (``CureResult``, ``MeasurementResult``), ``None`` (home/move),
and ``scan`` returns ``{well: result}``. Possibly with numpy scalars/arrays.
``to_jsonable`` walks the structure and converts everything to JSON-native types.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
from typing import Any

_PRIMITIVES = (str, int, float, bool, type(None))
_MAX_DEPTH = 25


def to_jsonable(obj: Any, _depth: int = 0) -> Any:
    if _depth > _MAX_DEPTH:
        return repr(obj)

    if isinstance(obj, _PRIMITIVES):
        return obj

    if isinstance(obj, (bytes, bytearray)):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            return obj.hex()

    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v, _depth + 1) for k, v in dataclasses.asdict(obj).items()}

    if isinstance(obj, dict):
        return {str(k): to_jsonable(v, _depth + 1) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_jsonable(v, _depth + 1) for v in obj]

    # numpy without importing numpy
    if hasattr(obj, "tolist") and obj.__class__.__module__.startswith("numpy"):
        try:
            return to_jsonable(obj.tolist(), _depth + 1)
        except Exception:  # noqa: BLE001
            pass
    if hasattr(obj, "item") and obj.__class__.__module__.startswith("numpy"):
        try:
            return obj.item()
        except Exception:  # noqa: BLE001
            pass

    # generic objects: serialize their public attributes
    if hasattr(obj, "__dict__"):
        d = {k: v for k, v in vars(obj).items() if not k.startswith("_")}
        if d:
            return {"__type__": type(obj).__name__, **{k: to_jsonable(v, _depth + 1) for k, v in d.items()}}

    return repr(obj)


__all__ = ["to_jsonable"]
