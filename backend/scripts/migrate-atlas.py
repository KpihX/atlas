from __future__ import annotations

import argparse
from pathlib import Path

from atlas.migration import migrate_config, migrate_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate configuration and sessions into Atlas")
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--target-config", type=Path, required=True)
    parser.add_argument("--defaults", type=Path, required=True)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--target-db", type=Path, required=True)
    parser.add_argument("--companion-name", default="Atlas")
    parser.add_argument("--project-id", default="atlas")
    args = parser.parse_args()
    migrate_config(args.source_config, args.target_config, args.defaults)
    count = migrate_database(args.source_db, args.target_db, args.companion_name, args.project_id)
    print(f"Migrated and verified {count} Atlas sessions")
    print(f"Migrated Atlas configuration to {args.target_config}")


if __name__ == "__main__":
    main()
