"""Thin async wrapper around the Ollama HTTP API.

Only the two endpoints we need: `/api/tags` for model discovery and `/api/generate`
for single-shot completions. Streaming is disabled — we assemble the full response
per call, which is what the chunked-translation flow expects.
"""
from __future__ import annotations

from typing import Any

import httpx


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 600.0,
        think: bool | None = None,
        num_ctx: int | None = None,
    ):
        """Construct a client.

        Args:
          think: When set, is forwarded as the `think` flag on /api/generate so
            thinking-capable models (deepseek-r1, qwq, gpt-oss, …) skip the
            chain-of-thought prelude. `None` omits the field entirely, which is
            Ollama's own default behaviour.
          num_ctx: Optional override of the model's context window, sent as
            `options.num_ctx`. A falsy value (None / 0) omits it and lets the
            model decide. Surfaced up to the UI so the operator can tell
            whether a context value is attached to each request.
        """
        if not base_url:
            raise ValueError("Ollama base URL is empty")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.think = think
        self.num_ctx = num_ctx
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
        )

    async def list_models(self) -> list[dict]:
        r = await self._client.get("/api/tags")
        r.raise_for_status()
        return r.json().get("models", [])

    def _build_generate_payload(self, model: str, prompt: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
        if self.think is not None:
            payload["think"] = self.think
        if self.num_ctx:
            payload["options"] = {"num_ctx": int(self.num_ctx)}
        return payload

    async def generate(self, model: str, prompt: str) -> str:
        r = await self._client.post(
            "/api/generate",
            json=self._build_generate_payload(model, prompt),
        )
        r.raise_for_status()
        return r.json().get("response", "")

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "OllamaClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
