from app.subtitles.srt import parse_srt, write_srt


SAMPLE = """1
00:00:01,000 --> 00:00:04,000
Hello world
Second line

2
00:00:05,500 --> 00:00:07,250
<i>Italic text</i>
"""


def test_parse_srt_basic():
    cues = parse_srt(SAMPLE)
    assert len(cues) == 2
    assert cues[0].index == 1
    assert cues[0].start == "00:00:01,000"
    assert cues[0].end == "00:00:04,000"
    assert cues[0].text == "Hello world\nSecond line"
    assert cues[1].text == "<i>Italic text</i>"


def test_parse_srt_tolerates_crlf_and_bom():
    content = "\ufeff" + SAMPLE.replace("\n", "\r\n")
    cues = parse_srt(content)
    assert len(cues) == 2
    assert cues[0].text == "Hello world\nSecond line"


def test_parse_srt_missing_index_line():
    content = """00:00:01,000 --> 00:00:04,000
Hello

00:00:05,000 --> 00:00:07,000
World
"""
    cues = parse_srt(content)
    assert len(cues) == 2
    assert cues[0].text == "Hello"
    assert cues[1].text == "World"


def test_srt_roundtrip_preserves_timestamps_and_text():
    cues = parse_srt(SAMPLE)
    out = write_srt(cues)
    reparsed = parse_srt(out)
    assert len(reparsed) == len(cues)
    for a, b in zip(cues, reparsed):
        assert a.start == b.start
        assert a.end == b.end
        assert a.text == b.text


def test_srt_writer_renumbers_sequentially():
    cues = parse_srt(SAMPLE)
    cues[0].index = 99  # simulate out-of-order indices after translation
    cues[1].index = 42
    out = write_srt(cues)
    assert out.splitlines()[0] == "1"
    assert "\n2\n" in out
