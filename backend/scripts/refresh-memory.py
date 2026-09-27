from __future__ import annotations

import argparse
import asyncio

from atlas.adapters.sqlite import SQLiteStore
from atlas.config import load_config
from atlas.core.coordinator import Coordinator
from atlas.core.models import NotesDocument, now_iso
from atlas.core.notes import render_notes
from atlas.core.tools import ToolRegistry
from atlas.llm.client import OpenAICompatibleGenerator


async def main(limit: int) -> None:
    config = load_config()
    store = SQLiteStore(config.storage.resolved_path())
    generator = OpenAICompatibleGenerator(config.llm)
    await store.open()
    try:
        sessions = sorted(await store.list_sessions(), key=lambda item: item.updated_at, reverse=True)[:limit]
        coordinator = Coordinator(generator, ToolRegistry(1), config.policy, config.llm.roles)
        for summary in sessions:
            state = await store.load(summary.session_id)
            if state is None or not state.transcript:
                continue
            state.notes_cursor = 0
            state.notes_document = NotesDocument()
            document = await coordinator.write_notes(state)
            state.notes_document = document
            state.notes = render_notes(document, state.language)
            state.notes_cursor = len(state.transcript)
            state.notes_version += 1
            state.title = await coordinator.name_session(state)
            state.title_cursor = len(state.transcript)
            state.title_version += 1
            state.updated_at = now_iso()
            await store.save(state)
            print(summary.session_id, state.title, len(state.notes))
    finally:
        await generator.close()
        await store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh Atlas shared memory")
    parser.add_argument("--limit", type=int, default=5)
    arguments = parser.parse_args()
    asyncio.run(main(arguments.limit))
