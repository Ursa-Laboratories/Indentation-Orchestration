"""Pre-flight guard: a received protocol may only use whitelisted commands /
instruments. Run this before handing the protocol to cubos.
"""

from __future__ import annotations

from typing import Iterable, Set

import yaml


class ProtocolNotAllowed(ValueError):
    """The protocol references a command or instrument outside the allow-list."""


def check_protocol_allowed(
    protocol_yaml: str,
    *,
    allow_commands: Iterable[str],
    allow_instruments: Iterable[str],
) -> None:
    allow_commands = set(allow_commands)
    allow_instruments = set(allow_instruments)

    try:
        doc = yaml.safe_load(protocol_yaml)
    except yaml.YAMLError as exc:
        raise ProtocolNotAllowed(f"protocol YAML is not parseable: {exc}") from exc
    if not isinstance(doc, dict) or "protocol" not in doc:
        raise ProtocolNotAllowed("protocol YAML has no top-level 'protocol:' list")
    steps = doc["protocol"]
    if not isinstance(steps, list) or not steps:
        raise ProtocolNotAllowed("'protocol:' must be a non-empty list of steps")

    seen_commands: Set[str] = set()
    seen_instruments: Set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or len(step) != 1:
            raise ProtocolNotAllowed(f"step {i}: each step must be a single-key mapping, got {step!r}")
        (command, body), = step.items()
        seen_commands.add(command)
        if command not in allow_commands:
            raise ProtocolNotAllowed(
                f"step {i}: command {command!r} not allowed (allowed: {sorted(allow_commands)})"
            )
        if isinstance(body, dict):
            instrument = body.get("instrument")
            if instrument is not None:
                seen_instruments.add(instrument)
                if instrument not in allow_instruments:
                    raise ProtocolNotAllowed(
                        f"step {i}: instrument {instrument!r} not allowed "
                        f"(allowed: {sorted(allow_instruments)})"
                    )
    # nothing to return — raising is the contract


__all__ = ["check_protocol_allowed", "ProtocolNotAllowed"]
