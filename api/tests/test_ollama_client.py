"""Payload-shape checks for OllamaClient — no real HTTP calls."""
from app.ollama_client import OllamaClient


def test_payload_omits_think_and_num_ctx_by_default():
    c = OllamaClient("http://x:11434")
    payload = c._build_generate_payload("m", "hi")
    assert payload == {"model": "m", "prompt": "hi", "stream": False}


def test_payload_includes_think_when_false():
    c = OllamaClient("http://x:11434", think=False)
    payload = c._build_generate_payload("m", "hi")
    assert payload["think"] is False


def test_payload_includes_num_ctx_when_positive():
    c = OllamaClient("http://x:11434", num_ctx=8192)
    payload = c._build_generate_payload("m", "hi")
    assert payload["options"] == {"num_ctx": 8192}


def test_payload_drops_num_ctx_when_zero():
    c = OllamaClient("http://x:11434", num_ctx=0)
    payload = c._build_generate_payload("m", "hi")
    assert "options" not in payload


def test_payload_combines_think_and_num_ctx():
    c = OllamaClient("http://x:11434", think=False, num_ctx=16384)
    payload = c._build_generate_payload("m", "hi")
    assert payload["think"] is False
    assert payload["options"] == {"num_ctx": 16384}
