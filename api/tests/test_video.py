"""Tests for video.py — path-traversal guard, codec map, ffprobe JSON parsing.

We do not shell out to real ffprobe/ffmpeg here; end-to-end coverage comes
from the docker smoke test against a real video file.
"""
from pathlib import Path

import pytest

from app import video
from app.video import CODEC_MAP, BrowseEntry, MediaPathError, _track_from_json


def test_codec_map_subrip_is_supported():
    assert CODEC_MAP["subrip"] == ("srt", True, "copy")


def test_codec_map_webvtt_is_supported():
    assert CODEC_MAP["webvtt"] == ("vtt", True, "copy")


def test_codec_map_mov_text_is_converted_to_srt():
    # MP4's mov_text is text but not line-based — ffmpeg `-c:s srt` transmuxes it.
    assert CODEC_MAP["mov_text"] == ("srt", True, "srt")


def test_codec_map_ass_is_detected_but_unsupported():
    ext, supported, _ = CODEC_MAP["ass"]
    assert (ext, supported) == ("ass", False)


def test_codec_map_pgs_is_supported_via_ocr():
    # v1.4.0 routes PGS through pgsrip OCR after extraction, so the
    # codec is now flagged supported even though it's a bitmap format.
    ext, supported, _ = CODEC_MAP["hdmv_pgs_subtitle"]
    assert (ext, supported) == ("sup", True)


def test_codec_map_vobsub_is_supported_via_ocr():
    # v1.5.0 routes VobSub through subtile-ocr after extraction, mirroring
    # the PGS pipeline. The .idx sidecar is produced alongside .sub by
    # ffmpeg automatically — see extract_track for the post-extract check.
    ext, supported, _ = CODEC_MAP["dvd_subtitle"]
    assert (ext, supported) == ("sub", True)


def test_codec_map_dvb_remains_unsupported():
    # No maintained Python or CLI decoder for DVB subtitles — still deferred.
    ext, supported, _ = CODEC_MAP["dvb_subtitle"]
    assert (ext, supported) == ("sub", False)


def test_source_format_for_pgs_returns_pgs():
    assert video.source_format_for("hdmv_pgs_subtitle") == "pgs"


def test_source_format_for_vobsub_returns_vobsub():
    assert video.source_format_for("dvd_subtitle") == "vobsub"


def test_source_format_for_text_codecs_is_empty():
    assert video.source_format_for("subrip") == ""
    assert video.source_format_for("webvtt") == ""
    assert video.source_format_for("mov_text") == ""


def test_track_from_json_extracts_all_fields():
    payload = {
        "index": 3,
        "codec_name": "subrip",
        "codec_type": "subtitle",
        "tags": {
            "language": "hun",
            "title": "Hungarian forced",
        },
    }
    t = _track_from_json(payload)
    assert t.id == 3
    assert t.codec == "subrip"
    assert t.codec_id == "subrip"
    assert t.language == "hun"
    assert t.name == "Hungarian forced"
    assert t.ext == "srt"
    assert t.supported is True


def test_track_from_json_pgs_is_supported_via_ocr():
    payload = {
        "index": 0,
        "codec_name": "hdmv_pgs_subtitle",
        "tags": {"language": "eng"},
    }
    t = _track_from_json(payload)
    assert t.supported is True
    assert t.ext == "sup"


def test_track_from_json_unknown_codec_is_unsupported():
    payload = {
        "index": 0,
        "codec_name": "wholly_unknown_codec",
        "tags": {"language": "eng"},
    }
    t = _track_from_json(payload)
    assert t.supported is False
    assert t.ext is None


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    """Point MEDIA_DIR at a temp dir populated with a tiny folder layout."""
    root = tmp_path / "media"
    root.mkdir()
    (root / "movies").mkdir()
    (root / "movies" / "Foo.mkv").write_bytes(b"\x1a\x45\xdf\xa3")  # EBML magic, unparseable
    (root / "movies" / "Bar.mp4").write_bytes(b"\x00\x00\x00\x20ftypisom")
    (root / "movies" / "notes.txt").write_text("ignore me")
    (root / ".hidden.mkv").write_bytes(b"")
    monkeypatch.setattr(video, "MEDIA_DIR", root)
    return root


def test_browse_root_lists_only_dirs_and_videos(media_root):
    r = video.browse("")
    names = [e["name"] for e in r["entries"]]
    assert "movies" in names
    assert ".hidden.mkv" not in names  # dotfiles hidden
    assert r["parent"] is None
    assert r["path"] == ""


def test_browse_subdir_lists_mkv_and_mp4_and_hides_txt(media_root):
    r = video.browse("movies")
    names = [(e["name"], e["is_video"]) for e in r["entries"]]
    assert ("Foo.mkv", True) in names
    assert ("Bar.mp4", True) in names
    assert all(n != "notes.txt" for n, _ in names)
    assert r["parent"] == ""  # parent of 'movies' is root


def test_browse_rejects_path_traversal(media_root):
    with pytest.raises(MediaPathError):
        video.browse("../etc")


def test_resolve_rejects_absolute_paths_outside_root(media_root):
    with pytest.raises(MediaPathError):
        video.resolve_media_path("/etc/passwd")


def test_browse_rejects_missing_path(media_root):
    with pytest.raises(MediaPathError):
        video.browse("does-not-exist")


def test_extract_rejects_non_video(media_root, tmp_path):
    with pytest.raises(MediaPathError):
        video.extract_track("movies/notes.txt", 0, tmp_path / "out.srt")


def test_list_tracks_rejects_non_video(media_root):
    with pytest.raises(MediaPathError):
        video.list_video_tracks("movies/notes.txt")
