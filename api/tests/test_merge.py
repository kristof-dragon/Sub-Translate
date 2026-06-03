"""Unit tests for the subtitle-merge core (subtitles/merge.py).

Pure-function coverage only; the /merge router endpoints (disk + DB) are
exercised by the Docker smoke test, matching the test_rename.py convention.
"""
from app.subtitles.cue import Cue
from app.subtitles.merge import (
    analyze,
    combine,
    default_name,
    srt_ts,
    to_ms,
)
from app.subtitles.srt import write_srt


def cue(start: str, end: str, text: str) -> Cue:
    return Cue(index=0, start=start, end=end, text=text)


# --- timestamp helpers ------------------------------------------------------

def test_to_ms_tolerates_both_separators():
    assert to_ms("00:00:01,500") == 1500
    assert to_ms("00:00:01.500") == 1500
    assert to_ms("01:02:03,004") == ((1 * 60 + 2) * 60 + 3) * 1000 + 4


def test_srt_ts_roundtrips_through_to_ms():
    for ms in (0, 1, 1500, 59_999, 3_661_004):
        assert to_ms(srt_ts(ms)) == ms


# --- clean union (no overlap) ----------------------------------------------

def test_clean_union_interleaves_sorted_by_start():
    forced = [cue("00:00:10,000", "00:00:12,000", "forced one")]
    full = [
        cue("00:00:01,000", "00:00:03,000", "full one"),
        cue("00:00:20,000", "00:00:22,000", "full two"),
    ]
    rep = analyze(forced, full)
    assert rep.clean is True
    assert rep.overlap_count == 0
    assert rep.result_cues == 3

    merged = combine(forced, full)
    assert [c.text for c in merged] == ["full one", "forced one", "full two"]
    starts = [to_ms(c.start) for c in merged]
    assert starts == sorted(starts)


def test_empty_inputs():
    assert combine([], []) == []
    rep = analyze([], [])
    assert rep.clean is True
    assert rep.result_cues == 0
    only = [cue("00:00:01,000", "00:00:02,000", "solo")]
    assert [c.text for c in combine([], only)] == ["solo"]


# --- overlap handling: combine into one cue --------------------------------

def test_overlap_combines_into_single_cue_spanning_union():
    forced = [cue("00:00:01,000", "00:00:04,000", "Bonjour")]
    full = [cue("00:00:02,000", "00:00:05,000", "Hello there")]
    rep = analyze(forced, full)
    assert rep.clean is False
    assert rep.overlap_count == 1
    assert rep.result_cues == 1

    merged = combine(forced, full)
    assert len(merged) == 1
    assert merged[0].start == "00:00:01,000"
    assert merged[0].end == "00:00:05,000"
    assert merged[0].text == "Bonjour\nHello there"


def test_identical_overlapping_text_is_deduplicated():
    forced = [cue("00:00:01,000", "00:00:03,000", "Same line")]
    full = [cue("00:00:01,000", "00:00:03,000", "  same   LINE ")]  # whitespace/case differ
    merged = combine(forced, full)
    assert len(merged) == 1
    assert merged[0].text == "Same line"  # first occurrence kept, dup dropped


def test_chained_overlaps_form_one_cluster_and_trip_long_warning():
    # Five cues each overlapping the next -> a single transitive cluster.
    forced = [
        cue("00:00:00,000", "00:00:02,500", "f1"),
        cue("00:00:04,000", "00:00:06,500", "f2"),
        cue("00:00:08,000", "00:00:10,500", "f3"),
    ]
    full = [
        cue("00:00:02,000", "00:00:04,500", "u1"),
        cue("00:00:06,000", "00:00:08,500", "u2"),
    ]
    rep = analyze(forced, full)
    assert rep.overlap_count == 1
    assert rep.result_cues == 1
    assert rep.long_combined == 1
    assert rep.combined[0].count == 5
    assert rep.combined[0].long is True

    merged = combine(forced, full)
    assert len(merged) == 1
    assert merged[0].start == "00:00:00,000"
    assert merged[0].end == "00:00:10,500"


# --- tolerance --------------------------------------------------------------

def test_tolerance_ignores_subtolerance_boundary_overlap():
    forced = [cue("00:00:01,000", "00:00:02,050", "a")]
    full = [cue("00:00:02,000", "00:00:03,000", "b")]  # 50 ms overlap
    assert analyze(forced, full).overlap_count == 1               # strict default
    assert analyze(forced, full, tolerance_ms=100).clean is True  # within tolerance


# --- format normalisation ---------------------------------------------------

def test_vtt_dot_timestamps_emit_valid_srt():
    forced = [cue("00:00:01.000", "00:00:02.000", "dot one")]   # vtt-style separators
    full = [cue("00:00:10.000", "00:00:11.500", "dot two")]
    out = write_srt(combine(forced, full))
    assert "00:00:01,000 --> 00:00:02,000" in out
    assert "00:00:10,000 --> 00:00:11,500" in out


# --- default output name ----------------------------------------------------

def test_default_name_uses_common_prefix_and_merged_suffix():
    name = default_name("Movie.en.stream3", "Movie.en.stream4")
    assert name.startswith("Movie.en.stream")
    assert name.endswith(".merged")


def test_default_name_falls_back_when_no_common_prefix():
    assert default_name("aaa", "bbb").endswith(".merged")
    assert default_name("", "") == "merged.merged"


# --- per-clash keep/drop (the two-tick UI) ----------------------------------

def test_report_members_carry_track_labels():
    forced = [cue("00:00:01,000", "00:00:04,000", "Bonjour")]
    full = [cue("00:00:02,000", "00:00:05,000", "Hello")]
    members = analyze(forced, full).combined[0].members
    assert {m.track for m in members} == {"forced", "full"}
    by_track = {m.track: m.text for m in members}
    assert by_track == {"forced": "Bonjour", "full": "Hello"}


def test_drop_full_keeps_forced_only_with_original_timing():
    forced = [cue("00:00:01,000", "00:00:04,000", "Bonjour")]
    full = [cue("00:00:02,000", "00:00:05,000", "Hello")]
    merged = combine(forced, full, drop_full={0})
    assert len(merged) == 1
    assert merged[0].text == "Bonjour"
    assert merged[0].start == "00:00:01,000"
    assert merged[0].end == "00:00:04,000"  # forced's own timing, not the fused span


def test_drop_forced_keeps_full_only():
    forced = [cue("00:00:01,000", "00:00:04,000", "Bonjour")]
    full = [cue("00:00:02,000", "00:00:05,000", "Hello")]
    merged = combine(forced, full, drop_forced={0})
    assert [c.text for c in merged] == ["Hello"]
    assert merged[0].start == "00:00:02,000"


def test_drop_both_removes_clash_entirely():
    forced = [cue("00:00:01,000", "00:00:04,000", "Bonjour")]
    full = [cue("00:00:02,000", "00:00:05,000", "Hello")]
    assert combine(forced, full, drop_forced={0}, drop_full={0}) == []


def test_drop_indexes_only_the_named_clash():
    forced = [
        cue("00:00:01,000", "00:00:03,000", "F0"),
        cue("00:00:10,000", "00:00:12,000", "F1"),
    ]
    full = [
        cue("00:00:02,000", "00:00:04,000", "U0"),
        cue("00:00:11,000", "00:00:13,000", "U1"),
    ]
    # clash 0 = F0/U0, clash 1 = F1/U1. Drop forced on clash 0 only.
    merged = combine(forced, full, drop_forced={0})
    assert [c.text for c in merged] == ["U0", "F1\nU1"]


def test_drops_are_noops_when_there_is_no_overlap():
    forced = [cue("00:00:10,000", "00:00:12,000", "lonely forced")]
    full = [cue("00:00:01,000", "00:00:03,000", "lonely full")]
    merged = combine(forced, full, drop_forced={0}, drop_full={0})
    assert [c.text for c in merged] == ["lonely full", "lonely forced"]
