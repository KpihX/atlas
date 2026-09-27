# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio

from atlas.config import load_config, load_product
from atlas.core.coordinator import Coordinator
from atlas.core.models import AtlasState, Decision, Utterance
from atlas.core.tools import ToolRegistry, ToolSpec
from atlas.llm import OpenAICompatibleGenerator


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
    coordinator = Coordinator(generator, tools, config.policy, config.llm.roles)
    state = AtlasState(
        project_id=product.project_id,
        protocol_version=product.protocol_version,
        identity_name="Atlas",
        language="fr",
        transcript=[Utterance(text="Atlas, cherche avec Exa la documentation officielle de Python.")],
    )
    decision = Decision(
        route="act",
        addressee="atlas",
        memory="capture",
        initiative="assigned",
        timing="next_gap",
    )
    try:
        response = await generator.generate(coordinator._messages(state, decision), config.llm.roles.worker)
        print("RAW")
        print(response.content)
        print("PARSED")
        print(coordinator._parse(response.content, state, decision).model_dump_json(indent=2))
    finally:
        await generator.close()


if __name__ == "__main__":
    asyncio.run(main())
