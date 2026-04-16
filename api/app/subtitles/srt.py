"""SRT parser + writer.

Format:

    1
    00:00:01,000 --> 00:00:04,000
    First line
    Second line

    2
    00:00:05,000 --> 00:00:07,500
    Next cue
"""
from __future__ import annotations

import re

from .cue import Cue

_TS_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{3})"
)


def _normalise(content: str) -> str:
    return content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def _norm_ts(ts: str) -> str:
    """Ensure SRT timestamp uses comma as the ms separator and is zero-padded to HH."""
    ts = ts.replace(".", ",")
    if ts.count(":") == 2 and len(ts.split(":", 1)[0]) == 1:
        ts = "0" + ts
    return ts


def parse_srt(content: str) -> list[Cue]:
    content = _normalise(content).strip()
    if not content:
        return []

    blocks = re.split(r"\n[ \t]*\n", content)
    cues: list[Cue] = []

    for fallback_idx, block in enumerate(blocks, start=1):
        lines = block.split("\n")
        if len(lines) < 2:
            continue

        # SRT usually has an index line, but some files omit it.
        if "-->" in lines[0]:
            idx = fallback_idx
            ts_line = lines[0]
            text_lines = lines[1:]
        else:
            try:
                idx = int(lines[0].strip())
            except ValueError:
                idx = fallback_idx
            if len(lines) < 3:
                continue
            ts_line = lines[1]
            text_lines = lines[2:]

        m = _TS_RE.search(ts_line)
        if not m:
            continue

        cues.append(
            Cue(
                index=idx,
                start=_norm_ts(m.group(1)),
                end=_norm_ts(m.group(2)),
                text="\n".join(text_lines).rstrip(),
            )
        )

    return cues


def write_srt(cues: list[Cue]) -> str:
    parts: list[str] = []
    for i, c in enumerate(cues, start=1):
        parts.append(str(i))
        parts.append(f"{_norm_ts(c.start)} --> {_norm_ts(c.end)}")
        parts.append(c.text)
        parts.append("")
    return "\n".join(parts).rstrip("\n") + "\n"
