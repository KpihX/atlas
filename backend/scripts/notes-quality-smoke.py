from __future__ import annotations

import asyncio

from atlas.adapters.sqlite import SQLiteStore
from atlas.config import load_config
from atlas.core.coordinator import Coordinator
from atlas.core.models import NotesDocument
from atlas.core.notes import render_notes
from atlas.core.tools import ToolRegistry
from atlas.llm.client import OpenAICompatibleGenerator


async def main() -> None:
    config = load_config()
    store = SQLiteStore(config.storage.resolved_path())
    generator = OpenAICompatibleGenerator(config.llm)
    await store.open()
    try:
        sessions = await store.list_sessions()
        source = next((item for item in sessions if item.utterance_count >= 20), None)
        if source is None:
            raise RuntimeError("No representative session is available")
        state = await store.load(source.session_id)
        if state is None:
            raise RuntimeError("Representative session could not be loaded")
        state.notes_cursor = 0
        state.notes_document = NotesDocument()
        coordinator = Coordinator(generator, ToolRegistry(1), config.policy, config.llm.roles)
        document = await coordinator.write_notes(state)
        rendered = render_notes(document, state.language)
        print(
            {
                "session": source.session_id,
                "turns": source.utterance_count,
                "synthesis_paragraphs": len(document.synthesis),
                "participants": len(document.participants),
                "topics": len(document.topics),
                "questions": len(document.questions),
                "actions": len(document.actions),
                "current_work": len(document.current_work),
                "rendered_characters": len(rendered),
                "contains_source_ids": "utt_" in rendered,
            }
        )
    finally:
        await generator.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
