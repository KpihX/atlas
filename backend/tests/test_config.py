from __future__ import annotations

import json

from sidecar.config import AppConfig, default_config_text


def test_closed_llm_matrix_and_zen_default() -> None:
    config = AppConfig.model_validate(json.loads(default_config_text()))
    assert config.llm.active_model == "opencode-zen-muse-spark-1-3"
    assert {(item.provider, item.model, item.transport) for item in config.llm.models} == {
        ("openai", "gpt-5.6-luna", "responses"),
        ("opencode-zen", "muse-spark-1.3", "responses"),
        ("opencode-zen", "mimo-v2.6-flash-free", "chat_completions"),
        ("opencode-zen", "mimo-v2.5-free", "chat_completions"),
    }


def test_initial_tools_are_exa_and_jinko() -> None:
    config = AppConfig.model_validate(json.loads(default_config_text()))
    assert config.tools.exa.secret_env == "EXA_API_KEY"
    assert config.tools.jinko.secret_env == "JINKO_API_KEY"
