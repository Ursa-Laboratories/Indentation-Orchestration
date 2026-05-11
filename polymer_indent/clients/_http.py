"""Tiny shared HTTP helpers for the device clients."""

from __future__ import annotations

import logging
from typing import Any, Dict

import requests

log = logging.getLogger("polymer_indent.http")


class HttpError(RuntimeError):
    """A device endpoint returned a transport error or a non-2xx status."""


def post_json(
    session: requests.Session,
    url: str,
    payload: Dict[str, Any],
    *,
    timeout: float,
) -> Dict[str, Any]:
    """POST ``payload`` as JSON; return the decoded JSON body.

    Raises:
        HttpError: on connection failure, timeout, non-2xx status, or a body
            that isn't a JSON object.
    """
    log.debug("POST %s payload-keys=%s", url, sorted(payload))
    try:
        resp = session.post(url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise HttpError(f"POST {url} failed: {exc}") from exc
    return _decode(resp, url)


def get_json(
    session: requests.Session,
    url: str,
    *,
    timeout: float,
) -> Dict[str, Any]:
    log.debug("GET %s", url)
    try:
        resp = session.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise HttpError(f"GET {url} failed: {exc}") from exc
    return _decode(resp, url)


def _decode(resp: requests.Response, url: str) -> Dict[str, Any]:
    if resp.status_code >= 400:
        body = _safe_body(resp)
        raise HttpError(f"{url} -> HTTP {resp.status_code}: {body}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise HttpError(f"{url} -> non-JSON response: {resp.text[:200]!r}") from exc
    if not isinstance(data, dict):
        raise HttpError(f"{url} -> JSON response is not an object: {data!r}")
    return data


def _safe_body(resp: requests.Response) -> str:
    try:
        return str(resp.json())
    except ValueError:
        return resp.text[:300]


def new_session() -> requests.Session:
    return requests.Session()


__all__ = ["HttpError", "post_json", "get_json", "new_session"]
