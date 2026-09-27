from __future__ import annotations

import re
from pathlib import Path

LEGACY = re.compile(r"meeting-sidecar|Meeting Sidecar|SIDECAR_|\bsidecar\b")


def test_product_sources_use_only_atlas_identity() -> None:
    project = Path(__file__).resolve().parents[2]
    suffixes = {".py", ".ts", ".tsx", ".json", ".toml", ".md", ".html", ".css"}
    excluded = {"PBS.md", "migration.py", "test_identity.py"}
    failures: list[str] = []
    for path in project.rglob("*"):
        if not path.is_file() or path.name in excluded:
            continue
        if path.suffix not in suffixes and path.name != "Makefile":
            continue
        if any(part in {"node_modules", "dist", ".venv", ".git"} for part in path.parts):
            continue
        if LEGACY.search(path.read_text(encoding="utf-8")):
            failures.append(str(path.relative_to(project)))
    assert failures == []
