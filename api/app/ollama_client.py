"""Thin async wrapper around the Ollama HTTP API.

Only the two endpoints we need: `/api/tags` for model discovery and `/api/generate`
for single-shot completions. Streaming is disabled — we assemble the full response
per call, which is what the chunked-translation flow expects.
"""
from __future__ import annotations

import httpx


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 600.0,
    ):
        if not base_url:
            raise ValueError("Ollama base URL is empty")
        self.base_url = base_url.rstrip("/")
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

    async def generate(self, model: str, prompt: str) -> str:
        r = await self._client.post(
            "/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        r.raise_for_status()
        return r.json().get("response", "")

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "OllamaClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
