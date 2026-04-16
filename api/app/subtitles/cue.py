"""Format-agnostic cue representation used by both SRT and VTT round-trips."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Cue:
    """One subtitle cue.

    `start` and `end` are stored in the format native to the source file:
    - SRT: `HH:MM:SS,mmm` (comma)
    - VTT: `HH:MM:SS.mmm` (dot)

    The writers normalise separators on output, so callers do not need to convert.
    """

    index: int
    start: str
    end: str
    text: str  # may contain newlines (`\n`)
