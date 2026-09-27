# pyright: reportUnusedFunction=false
from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter, ValidationError
from starlette.websockets import WebSocketDisconnected

from atlas.adapters.exa import ExaSearch
from atlas.adapters.gradium import GradiumSTT, GradiumTTS
from atlas.adapters.jinko import JinkoFlights
from atlas.adapters.openai_stt import OpenAISTT
from atlas.adapters.openai_tts import OpenAITTS
from atlas.adapters.sqlite import SQLiteStore
from atlas.adapters.stt_registry import STTRegistry
from atlas.adapters.tts_registry import TTSRegistry
from atlas.adapters.typesafe import TypeSafeDecision
from atlas.config import AppConfig, ProductConfig, load_config, load_product
from atlas.core.coordinator import Coordinator
from atlas.core.engine import AtlasEngine
from atlas.core.speaker import SpeakerAgent
from atlas.core.tools import ToolRegistry, ToolSpec
from atlas.llm import OpenAICompatibleGenerator

from .hub import WebSocketHub
from .schemas import (
    BackendHello,
    BoardCurate,
    BootstrapResponse,
    ClientHello,
    ClientMessage,
    FloorChanged,
    PlaybackChanged,
    ProtocolDocument,
    SessionCommand,
    SessionLanguage,
    SessionOpen,
    SessionRenameRequest,
    SessionStart,
    TranscriptInject,
    VoiceModeCommand,
)

CLIENT_ADAPTER: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def create_app() -> FastAPI:
    config = load_config()
    product = load_product()
    hub = WebSocketHub()
    resources = compose(product, config, hub)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        await resources.engine.start()
        try:
            yield
        finally:
            await resources.engine.stop()
            await resources.exa.close()
            await resources.jinko.close()

    app = FastAPI(title=product.project_id, version="0.1.0", lifespan=lifespan)
    app.state.resources = resources
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "process": resources.engine.state.process_status,
            "session": resources.engine.state.session_status,
            "components": {
                key: value.model_dump(mode="json") for key, value in resources.engine.state.health.items()
            },
        }

    @app.get("/v1/bootstrap", response_model=BootstrapResponse)
    async def bootstrap() -> BootstrapResponse:
        return BootstrapResponse(
            project_id=product.project_id,
            display_name=product.display_name,
            companion_name=product.companion_name,
            protocol_version=product.protocol_version,
            active_model=config.llm.active_model,
            models=[item.id for item in config.llm.models],
            state=resources.engine.client_state(),
            sessions=await resources.engine.list_sessions(),
        )

    @app.get("/v1/protocol", response_model=ProtocolDocument)
    async def protocol_document() -> ProtocolDocument:
        return ProtocolDocument(
            client=ClientHello(
                type="client.hello", protocol_version=product.protocol_version, client_id="example"
            ),
            server=BackendHello(
                type="backend.hello", project_id=product.project_id, protocol_version=product.protocol_version
            ),
        )

    @app.patch("/v1/sessions/{session_id}")
    async def rename_session(session_id: str, request: SessionRenameRequest) -> dict[str, bool]:
        if not await resources.engine.rename_session(session_id, request.title):
            raise HTTPException(status_code=404, detail="Session not found")
        return {"updated": True}

    @app.delete("/v1/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_session(session_id: str) -> Response:
        if not await resources.engine.delete_session(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/sessions/{session_id}/export")
    async def export_session(session_id: str) -> Response:
        content = await resources.engine.export_session(session_id)
        if content is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{session_id}.md"'},
        )

    @app.websocket("/v1/live")
    async def live(websocket: WebSocket) -> None:
        await hub.connect(websocket)
        await websocket.send_json(
            {
                "type": "backend.hello",
                "project_id": product.project_id,
                "protocol_version": product.protocol_version,
            }
        )
        await websocket.send_json(
            {
                "type": "state.snapshot",
                "state": resources.engine.client_state().model_dump(mode="json"),
                "sessions": [item.model_dump(mode="json") for item in await resources.engine.list_sessions()],
            }
        )
        try:
            while True:
                frame = await websocket.receive()
                if frame.get("bytes") is not None:
                    await resources.engine.ingest_audio(frame["bytes"])
                    continue
                text = frame.get("text")
                if not isinstance(text, str):
                    continue
                try:
                    message = CLIENT_ADAPTER.validate_json(text)
                except ValidationError as error:
                    await websocket.send_json(
                        {"type": "protocol.error", "code": "PROTOCOL_INVALID", "message": str(error)}
                    )
                    continue
                if isinstance(message, ClientHello) and message.protocol_version != product.protocol_version:
                    await websocket.send_json(
                        {
                            "type": "protocol.error",
                            "code": "PROTOCOL_MISMATCH",
                            "message": (
                                f"Client protocol {message.protocol_version} does not match "
                                f"backend protocol {product.protocol_version}. Reload the page."
                            ),
                        }
                    )
                    await websocket.close(code=1002)
                    return
                await dispatch_client_message(resources.engine, message)
        except (WebSocketDisconnect, WebSocketDisconnected):
            pass
        finally:
            hub.disconnect(websocket)
            if hub.client_count == 0:
                await resources.engine.client_disconnected()

    frontend = Path(os.environ.get("ATLAS_FRONTEND_DIR", ""))
    if frontend.is_dir() and (frontend / "index.html").is_file():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


class Resources:
    def __init__(self, engine: AtlasEngine, exa: ExaSearch, jinko: JinkoFlights) -> None:
        self.engine = engine
        self.exa = exa
        self.jinko = jinko


def compose(product: ProductConfig, config: AppConfig, hub: WebSocketHub) -> Resources:
    generator = OpenAICompatibleGenerator(config.llm)
    decision = TypeSafeDecision(config.decision)
    stt_providers = {
        provider.id: GradiumSTT(provider) if provider.provider == "gradium" else OpenAISTT(provider)
        for provider in config.voice.stt.providers
    }
    stt = STTRegistry(config.voice.stt, stt_providers)
    tts_providers = {
        provider.id: GradiumTTS(provider) if provider.provider == "gradium" else OpenAITTS(provider)
        for provider in config.voice.tts.providers
    }
    tts = TTSRegistry(config.voice.tts, tts_providers)
    exa = ExaSearch(config.tools.exa)
    jinko = JinkoFlights(config.tools.jinko)
    tools = ToolRegistry(config.policy.research_max_concurrency)
    tools.register(
        ToolSpec(
            name="exa_search",
            description="Search the current web and return titles, URLs, dates, and relevant highlights.",
            effect="read",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "num_results": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
            handler=exa.search,
        )
    )
    tools.register(
        ToolSpec(
            name="jinko_flight_calendar",
            description="Search read-only flight calendar availability for explicit airports and a date.",
            effect="read",
            input_schema={
                "type": "object",
                "properties": {
                    "origins": {"type": "array", "items": {"type": "string"}},
                    "destinations": {"type": "array", "items": {"type": "string"}},
                    "departure_date": {"type": "string"},
                    "currency": {"type": "string"},
                },
                "required": ["origins", "destinations", "departure_date"],
            },
            handler=jinko.flight_calendar,
        )
    )

    async def list_capabilities(_arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "capabilities": tools.prompt_catalog(),
            "message": ("These are the capabilities currently registered and available to Atlas."),
        }

    tools.register(
        ToolSpec(
            name="system_capabilities",
            description=(
                "List Atlas's currently registered tools and their effects. Use this when the room asks "
                "what Atlas can do, which tools are available, or whether a capability exists."
            ),
            effect="read",
            input_schema={"type": "object", "properties": {}},
            handler=list_capabilities,
        )
    )
    coordinator = Coordinator(generator, tools, config.policy, config.llm.roles)
    speaker = SpeakerAgent(generator, config.llm.roles.speaker)
    store = SQLiteStore(config.storage.resolved_path())
    engine = AtlasEngine(
        product=product,
        config=config,
        store=store,
        decision=decision,
        speaker=speaker,
        coordinator=coordinator,
        stt=stt,
        tts=tts,
        publish=hub.broadcast,
        tool_health={"exa": exa.available, "jinko": jinko.available},
    )
    return Resources(engine, exa, jinko)


async def dispatch_client_message(engine: AtlasEngine, message: ClientMessage) -> None:
    if isinstance(message, SessionStart):
        await engine.start_session(message.model_dump(mode="json", exclude={"type"}))
    elif isinstance(message, SessionOpen):
        await engine.open_session(message.session_id)
    elif isinstance(message, SessionLanguage):
        await engine.set_language(message.language)
    elif isinstance(message, VoiceModeCommand):
        await engine.set_voice_mode(message.mode)
    elif isinstance(message, BoardCurate):
        await engine.curate_board()
    elif isinstance(message, SessionCommand):
        await engine.session_command(message.command)
    elif isinstance(message, FloorChanged):
        await engine.floor_changed(message.busy)
    elif isinstance(message, TranscriptInject):
        await engine.commit_utterance(message.text, source="manual")
    elif isinstance(message, PlaybackChanged):
        await engine.playback_changed(message.speech_id, message.type.split(".", 1)[1])
    else:
        return
