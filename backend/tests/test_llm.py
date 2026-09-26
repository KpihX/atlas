from __future__ import annotations

import json

import httpx
import pytest

from sidecar.config import AppConfig, default_config_text
from sidecar.llm import OpenAICompatibleGenerator


@pytest.mark.asyncio
async def test_responses_transport_uses_configured_zen_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "test")
    config = AppConfig.model_validate(json.loads(default_config_text()))
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"output_text": "hello"})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleGenerator(config.llm, transport)
    result = await client.generate([{"role": "user", "content": "hi"}])
    assert captured["model"] == "muse-spark-1.3"
    assert "input" in captured and result.content == "hello"
    await transport.aclose()


@pytest.mark.asyncio
async def test_chat_transport_is_selected_by_model_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "test")
    config = AppConfig.model_validate(json.loads(default_config_text()))
    llm = config.llm.model_copy(update={"active_model": "opencode-zen-mimo-v2-6-flash-free"})
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleGenerator(llm, transport)
    result = await client.generate([{"role": "user", "content": "hi"}])
    assert captured["model"] == "mimo-v2.6-flash-free"
    assert "messages" in captured and result.content == "hello"
    await transport.aclose()


@pytest.mark.asyncio
async def test_utility_model_falls_back_to_active_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "test")
    config = AppConfig.model_validate(json.loads(default_config_text()))
    config = config.model_copy(
        update={"llm": config.llm.model_copy(update={"utility_model": "opencode-zen-mimo-v2-6-flash-free"})}
    )
    models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        models.append(payload["model"])
        if payload["model"] == "mimo-v2.6-flash-free":
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"output_text": "fallback"})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = OpenAICompatibleGenerator(config.llm, transport)
    result = await client.generate(
        [{"role": "user", "content": "hi"}],
        config.llm.utility_model,
    )
    assert models == ["mimo-v2.6-flash-free", "muse-spark-1.3"]
    assert result.content == "fallback"
    await transport.aclose()
