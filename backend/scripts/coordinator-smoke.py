from __future__ import annotations

import asyncio

from sidecar.config import load_config, load_product
from sidecar.core.coordinator import Coordinator
from sidecar.core.models import Decision, MeetingState, Utterance
from sidecar.core.tools import ToolRegistry, ToolSpec
from sidecar.llm import OpenAICompatibleGenerator


async def unavailable(_: dict[str, object]) -> dict[str, object]:
    return {"status": "smoke-only"}


async def main() -> None:
    config, product = load_config(), load_product()
    generator = OpenAICompatibleGenerator(config.llm)
    tools = ToolRegistry(1)
    tools.register(
        ToolSpec(
            name="exa_search",
            description="Search the current web.",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            effect="read",
            handler=unavailable,
        )
    )
    coordinator = Coordinator(generator, tools, config.policy)
    state = MeetingState(
        project_id=product.project_id,
        protocol_version=product.protocol_version,
        assistant_name="Assistant",
        language="fr",
        transcript=[Utterance(text="Assistant, cherche avec Exa la documentation officielle de Python.")],
    )
    decision = Decision(route="act", addressed_probability=1, speech_value=2, timing="next_gap")
    try:
        response = await generator.generate(coordinator._messages(state, decision))
        print("RAW")
        print(response.content)
        print("PARSED")
        print(coordinator._parse(response.content, state, decision).model_dump_json(indent=2))
    finally:
        await generator.close()


if __name__ == "__main__":
    asyncio.run(main())
