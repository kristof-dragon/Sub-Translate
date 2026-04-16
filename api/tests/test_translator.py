"""Translator chunking + response parsing — no real Ollama calls."""
import asyncio

from app.subtitles.cue import Cue
from app.translator import _parse_response, translate_cues


def test_parse_response_basic():
    out = _parse_response("[1] Hola mundo\n[2] Adios\n")
    assert out == {1: "Hola mundo", 2: "Adios"}


def test_parse_response_preserves_br_as_newline():
    out = _parse_response("[1] First<BR>Second")
    assert out == {1: "First\nSecond"}


def test_parse_response_ignores_commentary():
    raw = """Here are the translations:

[1] Hola
[2] Adios

Hope this helps."""
    out = _parse_response(raw)
    assert out == {1: "Hola", 2: "Adios"}


class FakeClient:
    """Mock Ollama client that returns canned translations per index."""

    def __init__(self, mapping: dict[int, str]):
        self.mapping = mapping
        self.calls = 0

    async def generate(self, model: str, prompt: str) -> str:
        self.calls += 1
        # Pull indices out of the prompt and return a "translated" line per index.
        import re

        idxs = [int(m) for m in re.findall(r"\[(\d+)\]", prompt)]
        return "\n".join(f"[{i}] {self.mapping.get(i, '')}" for i in idxs)


def test_translate_cues_chunked():
    cues = [Cue(index=i, start="00:00:01,000", end="00:00:02,000", text=f"src{i}") for i in range(1, 6)]
    mapping = {i: f"tgt{i}" for i in range(1, 6)}
    client = FakeClient(mapping)

    result = asyncio.run(
        translate_cues(client, "fake-model", "English", "Spanish", cues, chunk_size=2)  # type: ignore[arg-type]
    )

    assert [c.text for c in result] == ["tgt1", "tgt2", "tgt3", "tgt4", "tgt5"]
    # 5 cues, chunk 2 -> 3 chunk calls, no retries.
    assert client.calls == 3
    # Timestamps preserved.
    assert all(c.start == "00:00:01,000" for c in result)


def test_translate_cues_progress_callback_monotonic():
    cues = [Cue(index=i, start="00:00:01,000", end="00:00:02,000", text=f"src{i}") for i in range(1, 4)]
    client = FakeClient({i: f"tgt{i}" for i in range(1, 4)})
    seen = []

    async def cb(done, total):
        seen.append((done, total))

    asyncio.run(translate_cues(client, "m", "en", "es", cues, chunk_size=10, progress_cb=cb))  # type: ignore[arg-type]
    assert seen == [(1, 3), (2, 3), (3, 3)]
