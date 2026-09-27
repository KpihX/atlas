from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, cast


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(
                cast(dict[str, Any], merged[key]),
                cast(dict[str, Any], value),
            )
        else:
            merged[key] = value
    return merged


def migrate_payload(raw: str, companion_name: str, project_id: str) -> str:
    payload = json.loads(raw)
    payload["project_id"] = project_id
    payload["identity_name"] = companion_name
    payload.pop("assistant_name", None)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def digest_rows(rows: list[tuple[str, str]]) -> str:
    digest = hashlib.sha256()
    for session_id, payload in sorted(rows):
        digest.update(session_id.encode())
        digest.update(b"\0")
        digest.update(payload.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def migrate_database(source: Path, target: Path, companion_name: str, project_id: str) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_db = sqlite3.connect(source)
    target_db = sqlite3.connect(target)
    try:
        target_db.executescript(
            """
            CREATE TABLE IF NOT EXISTS atlas_schema (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS atlas_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS atlas_sessions (
                session_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            INSERT OR IGNORE INTO atlas_schema(version) VALUES(1);
            """
        )
        source_rows = source_db.execute("SELECT session_id, payload FROM sessions").fetchall()
        migrated_rows = [
            (session_id, migrate_payload(payload, companion_name, project_id))
            for session_id, payload in source_rows
        ]
        target_db.executemany(
            "INSERT INTO atlas_sessions(session_id, payload) VALUES(?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET payload = excluded.payload",
            migrated_rows,
        )
        target_db.commit()
        target_rows = target_db.execute("SELECT session_id, payload FROM atlas_sessions").fetchall()
        if len(source_rows) != len(target_rows):
            raise RuntimeError("Atlas migration row count mismatch")
        if digest_rows(migrated_rows) != digest_rows(target_rows):
            raise RuntimeError("Atlas migration payload hash mismatch")
        return len(target_rows)
    finally:
        source_db.close()
        target_db.close()


def migrate_config(source: Path, target: Path, defaults: Path) -> None:
    source_data = json.loads(source.read_text(encoding="utf-8"))
    default_data = json.loads(defaults.read_text(encoding="utf-8"))
    source_data.get("session", {}).pop("assistant_name", None)
    migrated = deep_merge(default_data, source_data)
    migrated["llm"]["roles"] = default_data["llm"]["roles"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(migrated, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
