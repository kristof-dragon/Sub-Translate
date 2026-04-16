"""WebVTT parser + writer.

Differences from SRT we care about:

- Starts with a `WEBVTT` header followed by a blank line.
- Timestamps use `.` for the millisecond separator (not `,`).
- The hours field may be omitted (`MM:SS.mmm`); we pad to `HH:MM:SS.mmm`.
- Optional cue identifiers and `NOTE` blocks are tolerated and dropped on output.
"""
from __future__ import annotations

import re

from .cue import Cue

_TS_RE = re.compile(
    r"((?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{3})\s*-->\s*((?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{3})"
)


def _normalise(content: str) -> str:
    return content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def _pad_hours(ts: str) -> str:
    parts = ts.split(":")
    if len(parts) == 2:  # MM:SS.mmm -> 00:MM:SS.mmm
        return f"00:{parts[0].zfill(2)}:{parts[1]}"
    if len(parts[0]) == 1:
        parts[0] = "0" + parts[0]
    return ":".join(parts)


def _norm_ts(ts: str) -> str:
    return _pad_hours(ts.replace(",", "."))


def parse_vtt(content: str) -> list[Cue]:
    content = _normalise(content).strip()
    if not content:
        return []

    # Strip the WEBVTT header block (first paragraph) if present.
    paragraphs = re.split(r"\n[ \t]*\n", content)
    if paragraphs and paragraphs[0].lstrip().upper().startswith("WEBVTT"):
        paragraphs = paragraphs[1:]

    cues: list[Cue] = []
    idx = 0

    for block in paragraphs:
        lines = block.split("\n")
        # Drop NOTE / STYLE / REGION blocks.
        first = lines[0].strip().upper() if lines else ""
        if first.startswith(("NOTE", "STYLE", "REGION")):
            continue

        # Find the timestamp line; anything before it is a cue identifier (ignored).
        ts_line = None
        ts_pos = -1
        for j, line in enumerate(lines):
            if "-->" in line:
                ts_line = line
                ts_pos = j
                break
        if ts_line is None:
            continue

        m = _TS_RE.search(ts_line)
        if not m:
            continue

        text_lines = lines[ts_pos + 1 :]
        idx += 1
        cues.append(
            Cue(
                index=idx,
                start=_norm_ts(m.group(1)),
                end=_norm_ts(m.group(2)),
                text="\n".join(text_lines).rstrip(),
            )
        )

    return cues


def write_vtt(cues: list[Cue]) -> str:
    parts: list[str] = ["WEBVTT", ""]
    for c in cues:
        parts.append(f"{_norm_ts(c.start)} --> {_norm_ts(c.end)}")
        parts.append(c.text)
        parts.append("")
    return "\n".join(parts).rstrip("\n") + "\n"
