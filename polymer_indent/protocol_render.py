"""Render a per-well protocol YAML from a frozen base protocol.

The only thing that changes between iterations is the well id. ``render_protocol``
takes a base cubos-format protocol YAML (text), rewrites the well referenced by
the single ``measure`` / ``move`` / ``scan`` command, and returns the new YAML
text — comments and formatting preserved (it's a targeted text substitution, not
a parse-and-redump).

Supported well references in the base file:
  - an explicit well, e.g. ``position: plate.E5`` or ``position: plate_holder.plate.A1``
  - a placeholder token ``{{WELL}}``, e.g. ``position: plate_holder.plate.{{WELL}}``
"""

from __future__ import annotations

import re
from pathlib import Path

# A1 .. H12 style well ids (one or more letters then one or more digits).
_WELL_RE = re.compile(r"^[A-Za-z]+[0-9]+$")

# Matches  `<key>: <dotted.prefix>.<WELL>`  on a single line (optionally with a
# trailing `# comment`), where <key> is `position` or `plate`. Captures the
# leading text up to and including the last dot (`head`), the well id (`well`),
# and any trailing whitespace/comment (`tail`) so the comment survives the swap.
_REF_LINE_RE = re.compile(
    r"(?P<head>^[ \t]*(?:position|plate)[ \t]*:[ \t]*[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*\.)"
    r"(?P<well>[A-Za-z]+[0-9]+)"
    r"(?P<tail>[ \t]*(?:#.*)?)$",
    re.MULTILINE,
)

_PLACEHOLDER = "{{WELL}}"


def _normalize_well(well: str) -> str:
    well = well.strip().upper()
    if not _WELL_RE.match(well):
        raise ValueError(f"not a well id: {well!r} (expected like 'A1', 'H12')")
    return well


def render_protocol(base_protocol: str | Path, well: str) -> str:
    """Return the base protocol YAML text with the well id swapped to ``well``.

    Args:
        base_protocol: path to a cubos-format protocol YAML, or its text.
        well: target well id, e.g. ``"B5"`` (case-insensitive).

    Raises:
        ValueError: if ``well`` isn't a well id, or the base text has neither a
            ``{{WELL}}`` placeholder nor a single rewritable well reference.
        FileNotFoundError: if ``base_protocol`` is a path that doesn't exist.
    """
    well = _normalize_well(well)

    text = (
        Path(base_protocol).read_text()
        if _looks_like_path(base_protocol)
        else str(base_protocol)
    )

    if _PLACEHOLDER in text:
        return text.replace(_PLACEHOLDER, well)

    matches = list(_REF_LINE_RE.finditer(text))
    if not matches:
        raise ValueError(
            "base protocol has no '{{WELL}}' placeholder and no rewritable "
            "'position:'/'plate:' well reference (e.g. 'position: plate.A1')"
        )

    # Rewrite every matching reference (there's normally exactly one, but a
    # protocol that touches the same well in several steps is fine too).
    def _sub(m: re.Match) -> str:
        return f"{m.group('head')}{well}{m.group('tail')}"

    return _REF_LINE_RE.sub(_sub, text)


def _looks_like_path(value: str | Path) -> bool:
    if isinstance(value, Path):
        return True
    # Heuristic: protocol text always contains a newline and a "protocol:" key;
    # a path won't.
    return "\n" not in value and value.strip().endswith((".yaml", ".yml"))
