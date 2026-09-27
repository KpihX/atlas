from __future__ import annotations

import json

from atlas.config import AppConfig, default_config_text


def test_closed_llm_matrix_and_openai_default() -> None:
    config = AppConfig.model_validate(json.loads(default_config_text()))
    assert config.llm.roles.speaker == "openai/gpt-5-mini"
    assert config.llm.roles.worker == "openai/gpt-4.1-mini"
    assert config.llm.roles.notes == "openai/gpt-4.1-mini"
    assert config.llm.roles.naming == "openai/gpt-4.1-nano"
    assert config.llm.roles.fallback == "opencode-zen/muse-spark-1.3"
    assert {(item.provider, item.model, item.transport) for item in config.llm.models} == {
        ("openai", "gpt-5.6-luna", "responses"),
        ("openai", "gpt-5-mini", "responses"),
        ("openai", "gpt-4.1-mini", "responses"),
        ("openai", "gpt-4.1-nano", "responses"),
        ("opencode-zen", "deepseek-v4-flash", "chat_completions"),
        ("opencode-zen", "deepseek-v4.1-flash", "chat_completions"),
        ("opencode-zen", "muse-spark-1.3", "responses"),
    }


def test_initial_tools_are_exa_and_jinko() -> None:
    config = AppConfig.model_validate(json.loads(default_config_text()))
    assert config.tools.exa.secret_env == "EXA_API_KEY"
    assert config.tools.jinko.secret_env == "JINKO_API_KEY"
