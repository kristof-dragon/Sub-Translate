"""Language detection via the configured Ollama model.

We send a short sample (first ~800 chars of cue text joined) and ask the model
to return only an ISO 639-1 code. The response is tolerant of extra text —
we scrape the first matching two-letter code.
"""
from __future__ import annotations

import re

from .ollama_client import OllamaClient

_DETECT_PROMPT = (
    "Identify the language of the following subtitle text. "
    "Respond with ONLY the ISO 639-1 two-letter lowercase code "
    "(examples: en, es, fr, de, hu, ja, zh). No other words, no punctuation.\n\n"
    "Text:\n{text}\n"
)

_CODE_RE = re.compile(r"\b([a-z]{2})\b")


async def detect_language(client: OllamaClient, model: str, sample_text: str) -> str:
    """Return a two-letter lowercase code, or 'unknown' if we couldn't extract one."""
    trimmed = sample_text.strip()[:800]
    if not trimmed:
        return "unknown"
    response = await client.generate(model=model, prompt=_DETECT_PROMPT.format(text=trimmed))
    m = _CODE_RE.search(response.lower())
    return m.group(1) if m else "unknown"
