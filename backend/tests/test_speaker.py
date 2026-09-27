# pyright: reportPrivateUsage=false
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest

from atlas.core.models import AtlasState, Decision, LLMResult, Utterance
from atlas.core.speaker import SpeakerAgent


class VerboseGenerator:
    available = True

    async def generate(self, messages: list[dict[str, str]], model_id: str | None = None) -> LLMResult:
        return LLMResult(content="{}", model="test", provider="test")

    async def stream(self, messages: list[dict[str, str]], model_id: str | None = None) -> AsyncIterator[str]:
        yield "First essential point. Second useful point. Third detail. Fourth detail."


class LongSentenceGenerator(VerboseGenerator):
    async def stream(self, messages: list[dict[str, str]], model_id: str | None = None) -> AsyncIterator[str]:
        yield (
            "A long answer can contain substantial context and several clauses without becoming multiple "
            "spoken sentences, so Atlas must wait for the actual semantic boundary before counting it. "
            "Second complete sentence. Third detail."
        )


@pytest.mark.asyncio
async def test_brief_speech_plan_emits_complete_bounded_turn() -> None:
    phrases: list[str] = []

    async def on_phrase(phrase: str) -> None:
        phrases.append(phrase)

    speaker = SpeakerAgent(VerboseGenerator())
    result = await speaker.stream_answer(
        AtlasState(project_id="atlas", protocol_version=1),
        Utterance(text="Atlas, summarize."),
        Decision(route="respond", addressee="atlas", speech_depth="brief", timing="next_gap"),
        on_phrase,
    )
    assert phrases == ["First essential point.", "Second useful point."]
    assert result.spoken_core == "First essential point. Second useful point."


@pytest.mark.asyncio
async def test_long_sentence_is_never_split_by_character_count() -> None:
    phrases: list[str] = []

    async def on_phrase(phrase: str) -> None:
        phrases.append(phrase)

    speaker = SpeakerAgent(LongSentenceGenerator())
    result = await speaker.stream_answer(
        AtlasState(project_id="atlas", protocol_version=1),
        Utterance(text="Atlas, explain this briefly."),
        Decision(route="respond", addressee="atlas", speech_depth="brief", timing="next_gap"),
        on_phrase,
    )
    assert len(phrases) == 2
    assert phrases[0].endswith("counting it.")
    assert "actual semantic boundary" in phrases[0]
    assert result.spoken_core.endswith("Second complete sentence.")


def test_compact_tool_projection_cannot_flood_speaker_context() -> None:
    projected = SpeakerAgent._compact_result(
        {"results": [{"title": "T" * 500, "highlights": ["H" * 2000, "ignored"]} for _ in range(8)]}
    )
    assert projected is not None
    sources = cast(list[dict[str, object]], projected["sources"])
    assert len(sources) == 3
    assert all(len(str(source["title"])) <= 160 for source in sources)
    for source in sources:
        highlights = cast(list[object], source["highlights"])
        assert len(str(highlights[0])) <= 240
