from __future__ import annotations

import json

import httpx
import pytest

from atlas.config import AppConfig, default_config_text
from atlas.llm import OpenAICompatibleGenerator


@pytest.mark.asyncio
async def test_responses_transport_uses_configured_openai_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    config = AppConfig.model_validate(json.loads(default_config_text()))
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"output_text": "hello"})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleGenerator(config.llm, transport)
    result = await client.generate([{"role": "user", "content": "hi"}], config.llm.roles.worker)
    assert captured["model"] == "gpt-4.1-mini"
    assert "input" in captured and result.content == "hello"
    await transport.aclose()


@pytest.mark.asyncio
async def test_chat_transport_is_selected_by_model_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "test")
    config = AppConfig.model_validate(json.loads(default_config_text()))
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleGenerator(config.llm, transport)
    result = await client.generate([{"role": "user", "content": "hi"}], "opencode-zen/deepseek-v4-flash")
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["reasoning_effort"] == "none"
    assert "messages" in captured and result.content == "hello"
    await transport.aclose()


@pytest.mark.asyncio
async def test_role_model_falls_back_to_configured_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "test")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    config = AppConfig.model_validate(json.loads(default_config_text()))
    models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        models.append(payload["model"])
        if payload["model"] == "gpt-5-mini":
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"output_text": "fallback"})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleGenerator(config.llm, transport)
    result = await client.generate(
        [{"role": "user", "content": "hi"}],
        config.llm.roles.speaker,
    )
    assert models == ["gpt-5-mini", "muse-spark-1.3"]
    assert result.content == "fallback"
    await transport.aclose()
