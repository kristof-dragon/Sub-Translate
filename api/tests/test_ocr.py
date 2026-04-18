"""Tests for the OCR module's pure helpers.

The pgsrip + tesseract and subtile-ocr end-to-end paths are exercised
by the docker smoke test against real bitmap files — both require
binaries and language packs that only ship inside the API image. Here
we cover the pieces that don't need any of that: language normalisation,
filename hint parsing, the VobSub shell-out wrapper (with subprocess
stubbed), and the LLM cleanup loop with a stubbed Ollama client.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from app import ocr
from app.subtitles.cue import Cue


# ---------------------------------------------------------------------------
# normalize_lang_for_tesseract
# ---------------------------------------------------------------------------

def test_normalize_lang_two_letter_known():
    assert ocr.normalize_lang_for_tesseract("en") == "eng"
    assert ocr.normalize_lang_for_tesseract("hu") == "hun"
    assert ocr.normalize_lang_for_tesseract("zh") == "chi_sim"


def test_normalize_lang_three_letter_known():
    # ffprobe-style ISO-639-2 codes pass through unchanged.
    assert ocr.normalize_lang_for_tesseract("eng") == "eng"
    assert ocr.normalize_lang_for_tesseract("hun") == "hun"
    assert ocr.normalize_lang_for_tesseract("kor") == "kor"


def test_normalize_lang_locale_form():
    # "en_US" / "en-US" → take the language part and resolve.
    assert ocr.normalize_lang_for_tesseract("en_US") == "eng"
    assert ocr.normalize_lang_for_tesseract("en-GB") == "eng"
    assert ocr.normalize_lang_for_tesseract("hu_HU") == "hun"


def test_normalize_lang_unknown_falls_back_to_eng():
    assert ocr.normalize_lang_for_tesseract("xx") == "eng"
    assert ocr.normalize_lang_for_tesseract("zzz") == "eng"
    assert ocr.normalize_lang_for_tesseract("") == "eng"
    assert ocr.normalize_lang_for_tesseract(None) == "eng"


# ---------------------------------------------------------------------------
# lang_hint_from_filename
# ---------------------------------------------------------------------------

def test_lang_hint_from_extraction_filename():
    # Extraction format: <stem>.<lang>.stream<n>.<ext>
    assert ocr.lang_hint_from_filename("Movie.eng.stream3.sup") == "eng"
    assert ocr.lang_hint_from_filename("My Movie.hu.stream0.sup") == "hu"


def test_lang_hint_returns_none_for_filenames_without_tag():
    assert ocr.lang_hint_from_filename("plain.sup") is None
    assert ocr.lang_hint_from_filename("a.b.sup") is None


def test_lang_hint_rejects_non_alpha_tag():
    # The slot has to look like a language code, not a number / random token.
    assert ocr.lang_hint_from_filename("Movie.123.stream0.sup") is None


# ---------------------------------------------------------------------------
# ocr_vobsub — subtile-ocr shell-out wrapper, subprocess stubbed
# ---------------------------------------------------------------------------

_SAMPLE_SRT = (
    "1\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "Hello world\n"
    "\n"
    "2\n"
    "00:00:03,000 --> 00:00:04,000\n"
    "Second cue\n"
)


def _make_vobsub_pair(tmp_path: Path) -> Path:
    """Write a `.sub` and matching `.idx` and return the .sub path."""
    sub_path = tmp_path / "track.sub"
    sub_path.write_bytes(b"\x00\x00\x01\xba")  # MPEG PS pack header magic
    sub_path.with_suffix(".idx").write_text("# VobSub index\n")
    return sub_path


def test_ocr_vobsub_raises_when_idx_sidecar_missing(tmp_path):
    sub_path = tmp_path / "lonely.sub"
    sub_path.write_bytes(b"\x00\x00\x01\xba")  # no sibling .idx written

    with pytest.raises(RuntimeError, match=r"\.idx sidecar missing"):
        ocr.ocr_vobsub(sub_path, "en")


def test_ocr_vobsub_invokes_subtile_ocr_with_correct_args(tmp_path, monkeypatch):
    sub_path = _make_vobsub_pair(tmp_path)
    captured_cmd: list[str] = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        # subtile-ocr writes its output to the path passed via -o.
        out_idx = cmd.index("-o") + 1
        Path(cmd[out_idx]).write_text(_SAMPLE_SRT)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)

    cues = ocr.ocr_vobsub(sub_path, "en")

    # Argument shape we documented in the plan.
    assert captured_cmd[0] == "subtile-ocr"
    assert "-l" in captured_cmd
    assert captured_cmd[captured_cmd.index("-l") + 1] == "eng"
    # The .idx (not .sub) is the input subtile-ocr expects.
    assert captured_cmd[-1].endswith(".idx")
    assert [c.text for c in cues] == ["Hello world", "Second cue"]


def test_ocr_vobsub_uses_normalized_lang_code(tmp_path, monkeypatch):
    sub_path = _make_vobsub_pair(tmp_path)
    seen_lang: list[str] = []

    def fake_run(cmd, **kwargs):
        seen_lang.append(cmd[cmd.index("-l") + 1])
        Path(cmd[cmd.index("-o") + 1]).write_text(_SAMPLE_SRT)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)

    ocr.ocr_vobsub(sub_path, "hu")    # ISO-639-1 → hun
    ocr.ocr_vobsub(sub_path, None)    # missing → falls back to eng

    assert seen_lang == ["hun", "eng"]


def test_ocr_vobsub_surfaces_subprocess_stderr(tmp_path, monkeypatch):
    sub_path = _make_vobsub_pair(tmp_path)

    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=1, cmd=cmd, output="", stderr="tesseract: language data missing"
        )

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="tesseract: language data missing"):
        ocr.ocr_vobsub(sub_path, "en")


def test_ocr_vobsub_raises_when_binary_missing(tmp_path, monkeypatch):
    sub_path = _make_vobsub_pair(tmp_path)

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("subtile-ocr")

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="subtile-ocr is not installed"):
        ocr.ocr_vobsub(sub_path, "en")


def test_ocr_vobsub_raises_when_output_is_empty(tmp_path, monkeypatch):
    sub_path = _make_vobsub_pair(tmp_path)

    def fake_run(cmd, **kwargs):
        # Process succeeds but produces no SRT (e.g. tesseract had nothing
        # to extract from a blank track) — caller should surface this as
        # an OCR error rather than silently flipping to ocr_done with 0 cues.
        Path(cmd[cmd.index("-o") + 1]).write_text("")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="produced no .srt output"):
        ocr.ocr_vobsub(sub_path, "en")


# ---------------------------------------------------------------------------
# llm_cleanup_cues — stubbed Ollama client
# ---------------------------------------------------------------------------

class _StubOllama:
    """Minimal stand-in: records prompts and returns scripted responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def generate(self, model, prompt):
        self.calls.append((model, prompt))
        if not self._responses:
            return ""
        head = self._responses.pop(0)
        if isinstance(head, Exception):
            raise head
        return head


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _cue(idx, text):
    return Cue(index=idx, start="00:00:01,000", end="00:00:02,000", text=text)


def test_llm_cleanup_replaces_text_per_cue(event_loop):
    cues = [_cue(1, "Helo wodd"), _cue(2, "Lne 2")]
    stub = _StubOllama(["Hello world", "Line 2"])

    cleaned = event_loop.run_until_complete(
        ocr.llm_cleanup_cues(stub, "tinyllama", cues, "en")
    )

    assert [c.text for c in cleaned] == ["Hello world", "Line 2"]
    assert len(stub.calls) == 2
    # Indices and timestamps preserved.
    assert cleaned[0].index == 1
    assert cleaned[0].start == "00:00:01,000"


def test_llm_cleanup_keeps_original_on_error(event_loop):
    cues = [_cue(1, "good"), _cue(2, "stay-as-is"), _cue(3, "fixed")]
    stub = _StubOllama(["good!", RuntimeError("boom"), "fixed!"])

    cleaned = event_loop.run_until_complete(
        ocr.llm_cleanup_cues(stub, "m", cues, "en")
    )

    assert [c.text for c in cleaned] == ["good!", "stay-as-is", "fixed!"]


def test_llm_cleanup_keeps_original_on_empty_response(event_loop):
    cues = [_cue(1, "keep me")]
    stub = _StubOllama([""])

    cleaned = event_loop.run_until_complete(
        ocr.llm_cleanup_cues(stub, "m", cues, None)
    )

    # Empty / whitespace-only response → defensive fallback to original text.
    assert cleaned[0].text == "keep me"


def test_llm_cleanup_invokes_progress_callback(event_loop):
    cues = [_cue(1, "a"), _cue(2, "b"), _cue(3, "c")]
    stub = _StubOllama(["A", "B", "C"])
    seen = []

    async def progress(done, total):
        seen.append((done, total))

    event_loop.run_until_complete(
        ocr.llm_cleanup_cues(stub, "m", cues, "en", progress_cb=progress)
    )

    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_llm_cleanup_with_empty_cues_is_noop(event_loop):
    stub = _StubOllama([])
    out = event_loop.run_until_complete(
        ocr.llm_cleanup_cues(stub, "m", [], "en")
    )
    assert out == []
    assert stub.calls == []


def test_llm_cleanup_encodes_newlines_in_prompt(event_loop):
    """Newlines in cue text are sent as the literal <BR> token so the
    model sees the structure of the line; the response is decoded back."""
    cues = [_cue(1, "first\nsecond")]
    stub = _StubOllama(["FIRST<BR>SECOND"])

    cleaned = event_loop.run_until_complete(
        ocr.llm_cleanup_cues(stub, "m", cues, "en")
    )

    # Prompt should contain the encoded form...
    assert "<BR>" in stub.calls[0][1]
    # ...and the decoded reply should restore real newlines.
    assert cleaned[0].text == "FIRST\nSECOND"
