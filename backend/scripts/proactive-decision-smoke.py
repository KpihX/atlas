from __future__ import annotations

import asyncio

from atlas.adapters.typesafe import TypeSafeDecision
from atlas.config import load_config
from atlas.core.models import AtlasState


async def main() -> None:
    config = load_config()
    router = TypeSafeDecision(config.decision)
    try:
        state = AtlasState(
            project_id="atlas",
            protocol_version=11,
            title="Deployment planning",
        )
        decision = await router.evaluate(
            state,
            "We are about to commit to this plan tomorrow, but nobody has checked whether the required "
            "venue is legally open on that date.",
        )
        print(decision.model_dump_json())
    finally:
        await router.close()


if __name__ == "__main__":
    asyncio.run(main())
