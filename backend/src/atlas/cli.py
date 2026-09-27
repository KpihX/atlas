from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from rich.console import Console

from atlas.config import USER_CONFIG, install_user_config, load_config

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("config-init")
def config_init(
    force: Annotated[bool, typer.Option(help="Replace the existing user configuration.")] = False,
) -> None:
    existed = USER_CONFIG.exists()
    path = install_user_config(force=force)
    action = "kept" if existed and not force else "installed"
    console.print(f"Configuration {action}: {path}")


@app.command()
def serve(
    reload: Annotated[bool, typer.Option(help="Reload backend source during development.")] = False,
) -> None:
    config = load_config()
    uvicorn.run(
        "atlas.api.app:create_app",
        factory=True,
        host=config.server.host,
        port=config.server.port,
        reload=reload,
    )


@app.command("export-openapi")
def export_openapi(destination: Path) -> None:
    from atlas.api.app import create_app

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(create_app().openapi(), indent=2), encoding="utf-8")
    console.print(f"OpenAPI exported: {destination}")
