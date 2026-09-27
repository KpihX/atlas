from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import AsyncIterator
from time import monotonic
from typing import Any, cast

import httpx

from atlas.config import LLMConfig, LLMModelConfig, secret
from atlas.core.models import LLMResult

logger = logging.getLogger("uvicorn.error").getChild("llm")


class OpenAICompatibleGenerator:
    def __init__(self, config: LLMConfig, transport: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._transport = transport or httpx.AsyncClient(timeout=config.timeout_seconds)
        self._owns_transport = transport is None
        self._model_slots: dict[str, asyncio.Semaphore] = {}

    @property
    def available(self) -> bool:
        return secret(self.config.select(self.config.roles.fallback).secret_env) is not None

    async def close(self) -> None:
        if self._owns_transport:
            await self._transport.aclose()

    async def generate(self, messages: list[dict[str, str]], model_id: str | None = None) -> LLMResult:
        model = self.config.select(model_id)
        try:
            return await self._generate_locked(messages, model)
        except httpx.HTTPError as error:
            fallback = self.config.select(self.config.roles.fallback)
            if fallback.id == model.id:
                raise
            logger.warning(
                "llm.fallback from=%s to=%s error=%s",
                model.id,
                fallback.id,
                type(error).__name__,
            )
            return await self._generate_locked(messages, fallback)

    async def stream(self, messages: list[dict[str, str]], model_id: str | None = None) -> AsyncIterator[str]:
        model = self.config.select(model_id)
        if model.provider is None or model.model is None:
            raise RuntimeError(f"unresolved model reference: {model.id}")
        api_key = secret(model.secret_env)
        if api_key is None:
            raise RuntimeError(f"missing configured LLM secret: {model.secret_env}")
        self._guard_cost(messages, model)
        payload = self._payload(messages, model) | {"stream": True}
        slots = self._model_slots.setdefault(model.id, asyncio.Semaphore(model.max_concurrency))
        started = monotonic()
        first_text = True
        async with (
            slots,
            self._transport.stream(
                "POST",
                model.endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=model.timeout_seconds or self.config.timeout_seconds,
            ) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                raw_value: object = json.loads(line[6:])
                if not isinstance(raw_value, dict):
                    continue
                raw = cast(dict[str, Any], raw_value)
                token = self._stream_content(raw, model)
                if not token:
                    continue
                if first_text:
                    logger.info(
                        "llm.first_text provider=%s model=%s latency_ms=%d",
                        model.provider,
                        model.model,
                        round((monotonic() - started) * 1000),
                    )
                    first_text = False
                yield token

    async def _generate_locked(self, messages: list[dict[str, str]], model: LLMModelConfig) -> LLMResult:
        slots = self._model_slots.setdefault(model.id, asyncio.Semaphore(model.max_concurrency))
        async with slots:
            return await self._generate_with_model(messages, model)

    async def _generate_with_model(self, messages: list[dict[str, str]], model: LLMModelConfig) -> LLMResult:
        if model.provider is None or model.model is None:
            raise RuntimeError(f"unresolved model reference: {model.id}")
        api_key = secret(model.secret_env)
        if api_key is None:
            raise RuntimeError(f"missing configured LLM secret: {model.secret_env}")
        self._guard_cost(messages, model)
        payload = self._payload(messages, model)
        logger.info(
            "llm.request provider=%s model=%s messages=%d characters=%d",
            model.provider,
            model.model,
            len(messages),
            sum(len(item.get("content", "")) for item in messages),
        )
        response = await self._transport.post(
            model.endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=model.timeout_seconds or self.config.timeout_seconds,
        )
        response.raise_for_status()
        raw_value: object = response.json()
        if not isinstance(raw_value, dict):
            raise TypeError("LLM response must be a JSON object")
        raw = cast(dict[str, Any], raw_value)
        content = self._content(raw, model)
        logger.info("llm.response characters=%d text=%r", len(content), content[:1200])
        return LLMResult(
            content=content,
            model=model.model,
            provider=model.provider,
            raw=raw,
        )

    def _payload(self, messages: list[dict[str, str]], model: LLMModelConfig) -> dict[str, object]:
        if model.transport == "responses":
            payload: dict[str, object] = {
                "model": model.model,
                "input": messages,
                "max_output_tokens": model.max_output_tokens,
            }
            if model.reasoning_effort is not None:
                payload["reasoning"] = {"effort": model.reasoning_effort}
            return payload
        payload = {
            "model": model.model,
            "messages": messages,
            "max_completion_tokens": model.max_output_tokens,
        }
        if model.reasoning_effort is not None:
            payload["reasoning_effort"] = model.reasoning_effort
        return payload

    def _content(self, raw: dict[str, Any], model: LLMModelConfig) -> str:
        if model.transport == "chat_completions":
            choices_value: object = raw.get("choices")
            if not isinstance(choices_value, list) or not choices_value:
                raise ValueError("chat completion returned no choice")
            choices = cast(list[object], choices_value)
            first_value = choices[0]
            if not isinstance(first_value, dict):
                raise ValueError("chat completion returned an invalid message")
            first = cast(dict[str, Any], first_value)
            if not isinstance(first.get("message"), dict):
                raise ValueError("chat completion returned an invalid message")
            message = cast(dict[str, Any], first["message"])
            content = message.get("content", "")
            return content if isinstance(content, str) else ""
        direct = raw.get("output_text")
        if isinstance(direct, str):
            return direct
        output_value: object = raw.get("output")
        if not isinstance(output_value, list):
            return ""
        parts: list[str] = []
        for value in cast(list[Any], output_value):
            if not isinstance(value, dict):
                continue
            item = cast(dict[str, Any], value)
            if not isinstance(item.get("content"), list):
                continue
            for value_part in cast(list[Any], item["content"]):
                if not isinstance(value_part, dict):
                    continue
                part = cast(dict[str, Any], value_part)
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
        return "\n".join(parts)

    @staticmethod
    def _stream_content(raw: dict[str, Any], model: LLMModelConfig) -> str:
        if model.transport == "chat_completions":
            choices_value: object = raw.get("choices")
            if not isinstance(choices_value, list) or not choices_value:
                return ""
            first_value = cast(list[object], choices_value)[0]
            if not isinstance(first_value, dict):
                return ""
            delta_value: object = cast(dict[str, object], first_value).get("delta")
            if not isinstance(delta_value, dict):
                return ""
            content = cast(dict[str, object], delta_value).get("content")
            return content if isinstance(content, str) else ""
        if raw.get("type") != "response.output_text.delta":
            return ""
        delta = raw.get("delta")
        return delta if isinstance(delta, str) else ""

    def _guard_cost(self, messages: list[dict[str, str]], model: LLMModelConfig) -> None:
        serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        input_tokens = max(1, math.ceil(len(serialized) / 4))
        estimated = (
            input_tokens * model.input_usd_per_million_tokens
            + model.max_output_tokens * model.output_usd_per_million_tokens
        ) / 1_000_000
        if estimated > self.config.max_request_cost_usd:
            raise ValueError("estimated LLM request cost exceeds configured cap")
