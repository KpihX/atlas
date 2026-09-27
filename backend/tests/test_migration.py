from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from atlas.migration import migrate_config, migrate_database


def test_database_migration_preserves_sessions_and_rewrites_identity(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "atlas.db"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE sessions(session_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    connection.execute(
        "INSERT INTO sessions VALUES(?, ?)",
        ("session_one", json.dumps({"project_id": "legacy", "assistant_name": "Legacy"})),
    )
    connection.commit()
    connection.close()

    assert migrate_database(source, target, "Atlas", "atlas") == 1
    migrated = sqlite3.connect(target)
    payload = json.loads(migrated.execute("SELECT payload FROM atlas_sessions").fetchone()[0])
    tables = {row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    migrated.close()
    assert {"atlas_schema", "atlas_state", "atlas_sessions"}.issubset(tables)
    assert payload["project_id"] == "atlas"
    assert payload["identity_name"] == "Atlas"
    assert "assistant_name" not in payload


def test_config_migration_preserves_values_and_uses_default_role_map(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    defaults = tmp_path / "defaults.json"
    target = tmp_path / "atlas.json"
    source.write_text(
        json.dumps(
            {
                "session": {"assistant_name": "Legacy", "language": "fr"},
                "llm": {"roles": {"speaker": "legacy/model"}},
            }
        )
    )
    defaults.write_text(
        json.dumps(
            {
                "session": {"language": "en", "notes_interval_seconds": 20},
                "llm": {"roles": {"speaker": "openai/gpt-5-mini"}},
            }
        )
    )
    migrate_config(source, target, defaults)
    migrated = json.loads(target.read_text())
    assert migrated["session"] == {"language": "fr", "notes_interval_seconds": 20}
    assert migrated["llm"]["roles"]["speaker"] == "openai/gpt-5-mini"
