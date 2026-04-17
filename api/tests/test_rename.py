"""Stem-validation checks for PATCH /files/:id/rename.

Only the pure validator is under test here; the disk-rename path is covered
by manual curl verification against a running container since it needs a real
file on the shared volume.
"""
import pytest
from fastapi import HTTPException

from app.routers.files import _validate_rename_stem


def test_accepts_plain_name():
    assert _validate_rename_stem("Movie.2024") == "Movie.2024"


def test_strips_surrounding_whitespace():
    assert _validate_rename_stem("  Movie  ") == "Movie"


@pytest.mark.parametrize(
    "bad",
    [
        "",               # empty
        "   ",            # whitespace only
        "foo/bar",        # path separator
        "foo\\bar",       # windows separator
        "foo\x00bar",     # null byte
        ".hidden",        # leading dot — would create a dotfile
        ".",              # current dir
        "..",             # parent dir
        "name.",          # trailing dot
        "name ",          # trailing space
    ],
)
def test_rejects_bad_stem(bad):
    with pytest.raises(HTTPException):
        _validate_rename_stem(bad)
