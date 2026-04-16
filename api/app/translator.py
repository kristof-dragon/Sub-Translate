"""Chunked subtitle translation.

We send ~N cues per Ollama call, each tagged with a stable `[N]` index. The
response is parsed back into a `{index: translated_text}` map and re-assembled
onto the original timestamps. Missing indices fall back to a per-cue retry.

Multi-line cues are preserved by using the literal token `<BR>` for internal
newlines in prompts, with explicit instructions to keep it intact.
"""
from __future__ import annotations

import re
from typing import Awaitable, Callable

from .ollama_client import OllamaClient
from .subtitles.cue import Cue

_LINE_RE = re.compile(r"^\s*\[(\d+)\]\s?(.*)$")
_BR = "<BR>"

_CHUNK_SYSTEM = (
    "You translate subtitles from {src} to {tgt}. "
    "Preserve HTML-like tags (<i>, <b>, <u>, <font>) exactly. "
    "Preserve the literal token <BR> which represents a line break inside a cue. "
    "Output each cue on its own line, prefixed with [N] where N matches the input index. "
    "Output nothing else: no commentary, no numbering changes, no blank lines."
)

_CHUNK_USER = "Translate the following cues:\n\n{cues}"


def _encode_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", _BR)


def _decode_text(text: str) -> str:
    return text.replace(_BR, "\n").strip()


def _format_cue_block(cues: list[Cue]) -> str:
    return "\n".join(f"[{c.index}] {_encode_text(c.text)}" for c in cues)


def _parse_response(text: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if m:
            try:
                idx = int(m.group(1))
            except ValueError:
                continue
            out[idx] = _decode_text(m.group(2))
    return out


async def _translate_block(
    client: OllamaClient,
    model: str,
    src_lang: str,
    tgt_lang: str,
    cues: list[Cue],
) -> dict[int, str]:
    prompt = (
        _CHUNK_SYSTEM.format(src=src_lang, tgt=tgt_lang)
        + "\n\n"
        + _CHUNK_USER.format(cues=_format_cue_block(cues))
    )
    raw = await client.generate(model=model, prompt=prompt)
    return _parse_response(raw)


ProgressCB = Callable[[int, int], Awaitable[None]]


async def translate_cues(
    client: OllamaClient,
    model: str,
    src_lang: str,
    tgt_lang: str,
    cues: list[Cue],
    chunk_size: int,
    progress_cb: ProgressCB | None = None,
) -> list[Cue]:
    """Translate a list of cues in order and return a new list with translated text.

    Timestamps and indices are preserved. If a cue fails to come back from the
    chunked call, we retry it alone once; if that still fails, the original
    source text is kept so the file remains playable rather than gapping.
    """
    if chunk_size <= 0:
        chunk_size = 30

    total = len(cues)
    done = 0
    translated: list[Cue] = []

    for start in range(0, total, chunk_size):
        chunk = cues[start : start + chunk_size]
        mapped = await _translate_block(client, model, src_lang, tgt_lang, chunk)

        for c in chunk:
            new_text = mapped.get(c.index)
            if new_text is None or not new_text.strip():
                # Single-cue retry.
                retry = await _translate_block(client, model, src_lang, tgt_lang, [c])
                new_text = retry.get(c.index) or c.text
            translated.append(Cue(index=c.index, start=c.start, end=c.end, text=new_text))
            done += 1
            if progress_cb is not None:
                await progress_cb(done, total)

    return translated
