from __future__ import annotations

import json
import os
from importlib.resources import files
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas.languages import AtlasLanguage
from atlas.prompts import PROMPTS as PROMPTS

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]
USER_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "atlas" / "atlas.json"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductConfig(StrictModel):
    project_id: str
    display_name: str
    companion_name: str
    protocol_version: int


class ServerConfig(StrictModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65535)


class SessionConfig(StrictModel):
    language: AtlasLanguage = "en"
    notes_interval_seconds: float = Field(default=20, gt=0)


class PolicyConfig(StrictModel):
    stable_floor_gap_seconds: float = Field(default=0.8, ge=0)
    direct_floor_gap_seconds: float = Field(default=0.3, ge=0)
    unsolicited_speech_cooldown_seconds: float = Field(default=90, ge=0)
    research_max_concurrency: int = Field(default=3, ge=1, le=16)
    max_tool_steps: int = Field(default=4, ge=1, le=16)


class StorageConfig(StrictModel):
    database_path: str = ""

    def resolved_path(self) -> Path:
        if self.database_path:
            return Path(self.database_path).expanduser()
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        return data_home / "atlas" / "atlas.db"


class STTConfig(StrictModel):
    provider: Literal["gradium"]
    endpoint: str
    secret_env: str
    model: str
    input_format: str
    language: str
    delay_in_frames: int = Field(ge=7, le=55)
    rotate_after_seconds: float = Field(default=285, gt=0, lt=300)


class TTSConfig(StrictModel):
    provider: Literal["gradium"]
    endpoint: str
    secret_env: str
    voice_id_env: str
    voice_id: str
    model: str
    output_format: str
    temperature: float = Field(default=0.9, ge=0, le=1.4)
    cfg_coef: float = Field(default=2.2, ge=1, le=4)
    padding_bonus: float = Field(default=-0.5, ge=-4, le=4)


class VoiceConfig(StrictModel):
    stt: STTConfig
    tts: TTSConfig


class DecisionConfig(StrictModel):
    provider: Literal["typesafe"]
    endpoint: str
    secret_env: str
    model: str
    expected_speaker_latency_ms: int = Field(default=1100, ge=0)


class LLMModelConfig(StrictModel):
    id: str
    provider: Literal["openai", "opencode-zen"] | None = None
    model: str | None = None
    endpoint: str
    transport: Literal["responses", "chat_completions"]
    secret_env: str
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    max_output_tokens: int = Field(ge=1, le=8192)
    max_concurrency: int = Field(default=2, ge=1, le=16)
    timeout_seconds: float | None = Field(default=None, gt=0)
    input_usd_per_million_tokens: float = Field(ge=0)
    output_usd_per_million_tokens: float = Field(ge=0)

    @model_validator(mode="after")
    def resolve_reference(self) -> LLMModelConfig:
        try:
            inferred_provider, inferred_model = self.id.split("/", 1)
        except ValueError as error:
            raise ValueError("LLM model IDs must use provider/model") from error
        if inferred_provider not in {"openai", "opencode-zen"}:
            raise ValueError(f"unsupported LLM provider: {inferred_provider}")
        if self.provider is not None and self.provider != inferred_provider:
            raise ValueError("LLM provider conflicts with its provider/model ID")
        if self.model is not None and self.model != inferred_model:
            raise ValueError("LLM model conflicts with its provider/model ID")
        object.__setattr__(self, "provider", inferred_provider)
        object.__setattr__(self, "model", inferred_model)
        return self


class LLMRolesConfig(StrictModel):
    speaker: str
    worker: str
    notes: str
    board: str
    naming: str
    fallback: str


class LLMConfig(StrictModel):
    roles: LLMRolesConfig
    timeout_seconds: float = Field(default=30, gt=0)
    max_request_cost_usd: float = Field(default=6, ge=0)
    models: list[LLMModelConfig]

    @model_validator(mode="after")
    def validate_registry(self) -> LLMConfig:
        ids = [item.id for item in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("LLM model IDs must be unique")
        for role, model_id in self.roles.model_dump().items():
            if model_id not in ids:
                raise ValueError(f"llm.roles.{role} must reference a registered model")
        return self

    @property
    def active_model(self) -> str:
        return self.roles.speaker

    def select(self, model_id: str | None = None) -> LLMModelConfig:
        selected = model_id or self.roles.worker
        return next(item for item in self.models if item.id == selected)


class JinkoConfig(StrictModel):
    enabled: bool = True
    base_url: str
    secret_env: str


class ExaConfig(StrictModel):
    enabled: bool = True
    endpoint: str
    secret_env: str
    search_type: Literal["instant", "fast", "auto", "deep-lite", "deep", "deep-reasoning"] = "auto"
    num_results: int = Field(default=5, ge=1, le=20)


class ToolsConfig(StrictModel):
    jinko: JinkoConfig
    exa: ExaConfig


class AppConfig(StrictModel):
    server: ServerConfig
    session: SessionConfig
    policy: PolicyConfig
    storage: StorageConfig
    voice: VoiceConfig
    decision: DecisionConfig
    llm: LLMConfig
    tools: ToolsConfig


def default_config_text() -> str:
    return files("atlas").joinpath("default_config.json").read_text(encoding="utf-8")


def install_user_config(*, force: bool = False) -> Path:
    if USER_CONFIG.exists() and not force:
        return USER_CONFIG
    USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    USER_CONFIG.write_text(default_config_text(), encoding="utf-8")
    return USER_CONFIG


def load_environment() -> None:
    env_file = Path(os.environ.get("ATLAS_ENV_FILE", BACKEND_ROOT / ".env"))
    if env_file.is_file():
        load_dotenv(env_file, override=False)


def load_product() -> ProductConfig:
    path = Path(os.environ.get("ATLAS_PRODUCT_FILE", PROJECT_ROOT / "product.json"))
    return ProductConfig.model_validate_json(path.read_text(encoding="utf-8"))


def load_config(path: Path | None = None) -> AppConfig:
    load_environment()
    selected = path or Path(os.environ.get("ATLAS_CONFIG", USER_CONFIG))
    if not selected.exists():
        install_user_config()
    return AppConfig.model_validate(json.loads(selected.read_text(encoding="utf-8")))


def secret(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None
