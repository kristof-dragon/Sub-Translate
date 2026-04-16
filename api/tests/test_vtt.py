from app.subtitles.vtt import parse_vtt, write_vtt


SAMPLE = """WEBVTT

00:00:01.000 --> 00:00:04.000
Hello world
Second line

NOTE this is a comment block

cue-2
00:00:05.500 --> 00:00:07.250
<i>Italic text</i>
"""


def test_parse_vtt_basic():
    cues = parse_vtt(SAMPLE)
    assert len(cues) == 2
    assert cues[0].start == "00:00:01.000"
    assert cues[0].end == "00:00:04.000"
    assert cues[0].text == "Hello world\nSecond line"
    assert cues[1].text == "<i>Italic text</i>"


def test_parse_vtt_without_hours_pads_hours():
    content = """WEBVTT

01:00.000 --> 01:04.000
Short
"""
    cues = parse_vtt(content)
    assert len(cues) == 1
    assert cues[0].start == "00:01:00.000"
    assert cues[0].end == "00:01:04.000"


def test_parse_vtt_drops_note_blocks():
    content = """WEBVTT

NOTE this block should be ignored

00:00:01.000 --> 00:00:02.000
Real cue
"""
    cues = parse_vtt(content)
    assert len(cues) == 1
    assert cues[0].text == "Real cue"


def test_vtt_roundtrip():
    cues = parse_vtt(SAMPLE)
    out = write_vtt(cues)
    assert out.startswith("WEBVTT\n")
    reparsed = parse_vtt(out)
    assert len(reparsed) == len(cues)
    for a, b in zip(cues, reparsed):
        assert a.start == b.start
        assert a.end == b.end
        assert a.text == b.text
