"""Merge two source-language subtitle tracks into one union subtitle.

The classic case: a film ships a "forced" track (only the foreign-language
dialogue) and a "standard"/full track (the rest). Players won't union them, so
this stitches both into a single file *before* translation.

Pure functions, no I/O — mirrors subtitles/srt.py / vtt.py. The caller parses
each input into list[Cue] (srt.parse_srt / vtt.parse_vtt), passes both here, and
writes the result back with srt.write_srt.

Timestamps on `Cue` are native-format strings (srt "HH:MM:SS,mmm" or vtt
"HH:MM:SS.mmm"). We parse tolerantly to milliseconds for overlap math and emit
SRT-style strings for any cue we synthesise; srt.write_srt re-normalises and
re-indexes on output, so mixed-format inputs round-trip cleanly.

Overlap policy (chosen by the operator): where cues from the two tracks overlap
in time they are COMBINED into one cue spanning the union of their ranges, with
the texts joined by a newline and exact-duplicate texts dropped (the common
"the full track already contains the forced line" case). Non-overlapping cues
simply interleave by start time.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .cue import Cue

# A combined cluster is "long" (worth warning about) when it fuses more than this
# many source cues or spans more than this many milliseconds — both are signs the
# two tracks chained into an over-merge rather than a clean local overlap.
_LONG_COMBINED_CUES = 4
_LONG_COMBINED_MS = 15_000

_TS_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def to_ms(ts: str) -> int:
    """Parse an SRT/VTT timestamp string to integer milliseconds.

    Tolerant of either millisecond separator (',' or '.') and of a 1- or
    2-digit hour. Raises ValueError on anything unparseable.
    """
    m = _TS_RE.search(ts.strip())
    if not m:
        raise ValueError(f"Unparseable timestamp: {ts!r}")
    h, mnt, s, ms = m.groups()
    return ((int(h) * 60 + int(mnt)) * 60 + int(s)) * 1000 + int(ms.ljust(3, "0"))


def srt_ts(ms: int) -> str:
    """Render integer milliseconds as an SRT timestamp 'HH:MM:SS,mmm'."""
    if ms < 0:
        ms = 0
    h, rem = divmod(ms, 3_600_000)
    mnt, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    return f"{h:02d}:{mnt:02d}:{s:02d},{msec:03d}"


@dataclass
class _Timed:
    """A source cue with its time range resolved to milliseconds."""

    start_ms: int
    end_ms: int
    cue: Cue
    track: str  # "forced" | "full" — used only for deterministic ordering


@dataclass
class ClusterMember:
    """One source cue inside an overlap cluster, tagged with its track."""

    track: str  # "forced" | "full"
    text: str
    start_ms: int
    end_ms: int


@dataclass
class CombinedCluster:
    """One run of >=2 source cues that overlap in time (a "clash").

    `members` carries each cue with its track so the UI can show the forced and
    full sides separately and let the operator keep either, both, or none.
    """

    start_ms: int
    end_ms: int
    members: list[ClusterMember]  # in start order
    count: int                    # == len(members)

    @property
    def long(self) -> bool:
        return (
            self.count > _LONG_COMBINED_CUES
            or (self.end_ms - self.start_ms) > _LONG_COMBINED_MS
        )


@dataclass
class MergeReport:
    forced_cues: int
    full_cues: int
    overlap_count: int               # number of combined clusters (size >= 2)
    result_cues: int                 # cue count of the merged output
    clean: bool                      # True <=> no overlaps (pure interleave)
    combined: list[CombinedCluster]  # capped sample for the UI
    long_combined: int               # how many combined clusters tripped the warning
    truncated: int                   # combined clusters omitted from `combined`


def _timed(cues: list[Cue], track: str) -> list[_Timed]:
    out: list[_Timed] = []
    for c in cues:
        try:
            start = to_ms(c.start)
            end = to_ms(c.end)
        except ValueError:
            continue  # skip cues we can't time rather than aborting the whole merge
        if end < start:
            end = start
        out.append(_Timed(start, end, c, track))
    return out


def _cluster(a: list[Cue], b: list[Cue], tolerance_ms: int) -> list[list[_Timed]]:
    """Group cues from both tracks into runs of overlapping intervals.

    Standard merge-overlapping-intervals sweep: a cue joins the running cluster
    when it starts before the cluster's running end (by more than `tolerance_ms`,
    so sub-tolerance boundary touches don't fuse). Transitive by construction.
    """
    items = _timed(a, "forced") + _timed(b, "full")
    items.sort(key=lambda t: (t.start_ms, t.end_ms, t.track))

    clusters: list[list[_Timed]] = []
    cur: list[_Timed] = []
    cur_end = -1
    for it in items:
        if cur and it.start_ms < cur_end - tolerance_ms:
            cur.append(it)
            cur_end = max(cur_end, it.end_ms)
        else:
            if cur:
                clusters.append(cur)
            cur = [it]
            cur_end = it.end_ms
    if cur:
        clusters.append(cur)
    return clusters


def _dedup_join(texts: list[str]) -> str:
    """Join cue texts with newlines, dropping exact duplicates.

    Comparison is whitespace-collapsed + case-folded so "the full track already
    contains the forced line" collapses to a single line instead of doubling.
    """
    seen: set[str] = set()
    kept: list[str] = []
    for t in texts:
        key = " ".join(t.split()).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(t)
    return "\n".join(kept)


def combine(
    a: list[Cue],
    b: list[Cue],
    tolerance_ms: int = 0,
    drop_forced: frozenset[int] | set[int] = frozenset(),
    drop_full: frozenset[int] | set[int] = frozenset(),
) -> list[Cue]:
    """Merge two cue lists into one union list.

    Non-overlapping cues always pass through. For each overlap cluster ("clash"),
    the operator's per-side choice applies, keyed by the cluster's 0-based index
    among overlap clusters (the same order `analyze` reports them in):
      - keep both (default)  -> fuse the cluster into one cue (texts joined, dups
        dropped);
      - keep one side only   -> emit that side's cues unchanged (original timing);
      - keep neither         -> drop the clash entirely.
    `drop_forced` / `drop_full` hold the overlap-indices whose forced / full side
    the operator unticked.

    Output ordering is preserved (clusters are non-overlapping and start-ordered).
    `write_srt` re-numbers on output, so indices here are placeholders.
    """
    out: list[Cue] = []
    overlap_idx = -1
    for cl in _cluster(a, b, tolerance_ms):
        if len(cl) == 1:
            c = cl[0].cue
            out.append(Cue(index=0, start=c.start, end=c.end, text=c.text))
            continue

        overlap_idx += 1
        keep_forced = overlap_idx not in drop_forced
        keep_full = overlap_idx not in drop_full

        kept = [
            t for t in cl
            if (t.track == "forced" and keep_forced) or (t.track == "full" and keep_full)
        ]
        if not kept:
            continue  # whole clash excluded

        if keep_forced and keep_full:
            # Both sides kept -> fuse the cluster into a single cue.
            start = min(t.start_ms for t in cl)
            end = max(t.end_ms for t in cl)
            text = _dedup_join([t.cue.text for t in cl])
            out.append(Cue(index=0, start=srt_ts(start), end=srt_ts(end), text=text))
        else:
            # One side only -> keep those cues as-is (no fusing).
            for t in kept:
                c = t.cue
                out.append(Cue(index=0, start=c.start, end=c.end, text=c.text))

    for i, c in enumerate(out, start=1):
        c.index = i
    return out


def analyze(
    a: list[Cue],
    b: list[Cue],
    tolerance_ms: int = 0,
    sample_limit: int = 50,
) -> MergeReport:
    """Overlap report for the two tracks, without building the merged output."""
    clusters = _cluster(a, b, tolerance_ms)
    combined = [cl for cl in clusters if len(cl) > 1]

    sample: list[CombinedCluster] = []
    for cl in combined[:sample_limit]:
        sample.append(
            CombinedCluster(
                start_ms=min(t.start_ms for t in cl),
                end_ms=max(t.end_ms for t in cl),
                members=[
                    ClusterMember(
                        track=t.track,
                        text=t.cue.text,
                        start_ms=t.start_ms,
                        end_ms=t.end_ms,
                    )
                    for t in cl
                ],
                count=len(cl),
            )
        )

    long_combined = sum(
        1
        for cl in combined
        if len(cl) > _LONG_COMBINED_CUES
        or (max(t.end_ms for t in cl) - min(t.start_ms for t in cl)) > _LONG_COMBINED_MS
    )

    return MergeReport(
        forced_cues=len(a),
        full_cues=len(b),
        overlap_count=len(combined),
        result_cues=len(clusters),
        clean=len(combined) == 0,
        combined=sample,
        long_combined=long_combined,
        truncated=max(0, len(combined) - sample_limit),
    )


def report_to_dict(r: MergeReport) -> dict:
    """JSON-friendly shape for the API, with timestamps rendered as SRT strings."""
    return {
        "forced_cues": r.forced_cues,
        "full_cues": r.full_cues,
        "overlap_count": r.overlap_count,
        "result_cues": r.result_cues,
        "clean": r.clean,
        "long_combined": r.long_combined,
        "truncated": r.truncated,
        "combined": [
            {
                "start": srt_ts(c.start_ms),
                "end": srt_ts(c.end_ms),
                "count": c.count,
                "long": c.long,
                "members": [
                    {
                        "track": m.track,
                        "text": m.text,
                        "start": srt_ts(m.start_ms),
                        "end": srt_ts(m.end_ms),
                    }
                    for m in c.members
                ],
            }
            for c in r.combined
        ],
    }


def default_name(stem_a: str, stem_b: str) -> str:
    """Default base name (no extension) for the merged subtitle.

    Longest common prefix of the two source stems (trimmed of trailing
    separators), else the full/standard stem, else 'merged'. Always carries a
    '.merged' marker so the origin is obvious in the file list.
    """
    common = os.path.commonprefix([stem_a or "", stem_b or ""]).rstrip(" ._-")
    base = common or stem_b or stem_a or "merged"
    return f"{base}.merged"
