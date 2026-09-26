from __future__ import annotations

from pathlib import Path

import aiosqlite

from sidecar.core.models import MeetingState, SessionSummary, new_id


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        await self._db.execute(
            "CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL)"
        )
        await self._db.execute(
            "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        await self._migrate_singleton()
        await self._db.commit()

    async def load(self, session_id: str) -> MeetingState | None:
        db = self._require_db()
        async with db.execute("SELECT payload FROM sessions WHERE session_id = ?", (session_id,)) as cursor:
            row = await cursor.fetchone()
        return MeetingState.model_validate_json(row[0]) if row else None

    async def list_sessions(self) -> list[SessionSummary]:
        db = self._require_db()
        async with db.execute("SELECT payload FROM sessions") as cursor:
            rows = await cursor.fetchall()
        states = [MeetingState.model_validate_json(row[0]) for row in rows]
        states.sort(key=lambda item: item.updated_at, reverse=True)
        return [
            SessionSummary(
                session_id=state.session_id or "",
                title=state.title,
                status=state.session_status,
                preview=state.transcript[-1].text if state.transcript else "",
                utterance_count=len(state.transcript),
                created_at=state.created_at,
                updated_at=state.updated_at,
            )
            for state in states
            if state.session_id
        ]

    async def save(self, state: MeetingState) -> None:
        if state.session_id is None:
            return
        db = self._require_db()
        await db.execute(
            "INSERT INTO sessions(session_id, payload) VALUES(?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET payload = excluded.payload",
            (state.session_id, state.model_dump_json()),
        )
        await db.commit()

    async def delete(self, session_id: str) -> bool:
        db = self._require_db()
        cursor = await db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        await db.commit()
        return cursor.rowcount > 0

    async def _migrate_singleton(self) -> None:
        db = self._require_db()
        async with db.execute("SELECT payload FROM state WHERE id = 1") as cursor:
            row = await cursor.fetchone()
        if row is None:
            return
        state = MeetingState.model_validate_json(row[0])
        state.session_id = state.session_id or new_id("session")
        await db.execute(
            "INSERT OR IGNORE INTO sessions(session_id, payload) VALUES(?, ?)",
            (state.session_id, state.model_dump_json()),
        )

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLite store is not open")
        return self._db
