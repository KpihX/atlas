from __future__ import annotations

import asyncio
import json
import sys

from atlas.adapters.sqlite import SQLiteStore
from atlas.config import load_config
from atlas.core.coordinator import Coordinator
from atlas.core.tools import ToolRegistry
from atlas.llm import OpenAICompatibleGenerator


async def main(session_id: str, model_id: str | None = None) -> None:
    config = load_config()
    store = SQLiteStore(config.storage.resolved_path())
    await store.open()
    state = await store.load(session_id)
    if state is None:
        raise SystemExit(f"unknown session: {session_id}")
    generator = OpenAICompatibleGenerator(config.llm)
    roles = config.llm.roles.model_copy(update={"board": model_id}) if model_id else config.llm.roles
    coordinator = Coordinator(generator, ToolRegistry(1), config.policy, roles)
    operations = await coordinator.curate_board(state)
    print(f"raw: {coordinator.last_board_response!r}")
    print(json.dumps([item.model_dump(mode="json") for item in operations], ensure_ascii=False, indent=2))
    await generator.close()
    await store.close()


if __name__ == "__main__":
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("usage: board-smoke.py <session_id> [model_id]")
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
