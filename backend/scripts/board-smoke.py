from __future__ import annotations

import asyncio
import json
import sys

from sidecar.adapters.sqlite import SQLiteStore
from sidecar.config import load_config
from sidecar.core.coordinator import Coordinator
from sidecar.core.tools import ToolRegistry
from sidecar.llm import OpenAICompatibleGenerator


async def main(session_id: str, model_id: str | None = None) -> None:
    config = load_config()
    store = SQLiteStore(config.storage.resolved_path())
    await store.open()
    state = await store.load(session_id)
    if state is None:
        raise SystemExit(f"unknown session: {session_id}")
    generator = OpenAICompatibleGenerator(config.llm)
    coordinator = Coordinator(generator, ToolRegistry(1), config.policy, model_id or config.llm.utility_model)
    operations = await coordinator.curate_board(state)
    print(f"raw: {coordinator.last_board_response!r}")
    print(json.dumps([item.model_dump(mode="json") for item in operations], ensure_ascii=False, indent=2))
    await generator.close()
    await store.close()


if __name__ == "__main__":
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("usage: board-smoke.py <session_id> [model_id]")
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
