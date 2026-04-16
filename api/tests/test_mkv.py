"""Tests for mkv.py — focused on the path-traversal guard and codec mapping.

We do not shell out to mkvmerge/mkvextract here; those are covered by the
docker smoke test against a real MKV.
"""
from pathlib import Path

import pytest

from app import mkv
from app.mkv import CODEC_ID_MAP, BrowseEntry, MediaPathError, _track_from_json


def test_codec_map_srt_is_supported():
    assert CODEC_ID_MAP["S_TEXT/UTF8"] == ("srt", True)


def test_codec_map_webvtt_is_supported():
    assert CODEC_ID_MAP["S_TEXT/WEBVTT"] == ("vtt", True)


def test_codec_map_ass_is_detected_but_unsupported():
    assert CODEC_ID_MAP["S_TEXT/ASS"] == ("ass", False)


def test_codec_map_pgs_is_detected_but_unsupported():
    assert CODEC_ID_MAP["S_HDMV/PGS"] == ("sup", False)


def test_track_from_json_extracts_all_fields():
    payload = {
        "id": 3,
        "codec": "SubRip/SRT",
        "type": "subtitles",
        "properties": {
            "codec_id": "S_TEXT/UTF8",
            "language": "hun",
            "language_ietf": "hu",
            "track_name": "Hungarian forced",
        },
    }
    t = _track_from_json(payload)
    assert t.id == 3
    assert t.codec_id == "S_TEXT/UTF8"
    assert t.language == "hu"  # ietf preferred when present
    assert t.name == "Hungarian forced"
    assert t.ext == "srt"
    assert t.supported is True


def test_track_from_json_unknown_codec_is_unsupported():
    payload = {
        "id": 0,
        "codec": "PGS",
        "properties": {"codec_id": "S_HDMV/PGS", "language": "eng"},
    }
    t = _track_from_json(payload)
    assert t.supported is False
    assert t.ext == "sup"


@pytest.fixture
def media_root(tmp_path, monkeypatch):
    """Point MEDIA_DIR at a temp dir populated with a tiny folder layout."""
    root = tmp_path / "media"
    root.mkdir()
    (root / "movies").mkdir()
    (root / "movies" / "Foo.mkv").write_bytes(b"\x1a\x45\xdf\xa3")  # EBML magic, unparseable
    (root / "movies" / "notes.txt").write_text("ignore me")
    (root / ".hidden.mkv").write_bytes(b"")
    monkeypatch.setattr(mkv, "MEDIA_DIR", root)
    return root


def test_browse_root_lists_only_dirs_and_mkvs(media_root):
    r = mkv.browse("")
    names = [e["name"] for e in r["entries"]]
    assert "movies" in names
    assert ".hidden.mkv" not in names  # dotfiles hidden
    assert r["parent"] is None
    assert r["path"] == ""


def test_browse_subdir_lists_mkv_and_hides_txt(media_root):
    r = mkv.browse("movies")
    names = [(e["name"], e["is_mkv"]) for e in r["entries"]]
    assert ("Foo.mkv", True) in names
    assert all(n != "notes.txt" for n, _ in names)
    assert r["parent"] == ""  # parent of 'movies' is root


def test_browse_rejects_path_traversal(media_root):
    with pytest.raises(MediaPathError):
        mkv.browse("../etc")


def test_browse_rejects_absolute_paths_outside_root(media_root):
    with pytest.raises(MediaPathError):
        mkv.resolve_media_path("/etc/passwd")


def test_browse_rejects_missing_path(media_root):
    with pytest.raises(MediaPathError):
        mkv.browse("does-not-exist")


def test_extract_rejects_non_mkv(media_root, tmp_path):
    with pytest.raises(MediaPathError):
        mkv.extract_track("movies/notes.txt", 0, tmp_path / "out.srt")


def test_list_tracks_rejects_non_mkv(media_root):
    with pytest.raises(MediaPathError):
        mkv.list_mkv_tracks("movies/notes.txt")
