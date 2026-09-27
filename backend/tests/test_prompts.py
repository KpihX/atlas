from __future__ import annotations

import inspect

from atlas import config
from atlas.adapters import typesafe
from atlas.core import coordinator, speaker


def test_prompt_catalog_formats_every_role() -> None:
    prompts = config.PROMPTS
    assert "Atlas" in prompts.identity("Atlas")
    assert "<SILENT>" in prompts.speaker_stream("Atlas")
    assert "speak" in prompts.speaker_structured("Atlas", '{"speak": true}')
    assert "source_ids" in prompts.notes("Atlas")
    assert "title" in prompts.naming("Atlas")
    assert "live board" in prompts.board("Atlas")
    assert "source_card_ids" in prompts.board("Atlas")
    assert "Card count is neutral" in prompts.board_review()
    assert "semantic equivalence" in prompts.board_review()
    assert "Omission never deletes" in prompts.board_review()
    assert "tool" in prompts.worker("Atlas", '{"tool": null}')
    assert set(prompts.jev_questions("Atlas")) == {
        "addressee",
        "route",
        "memory",
        "initiative",
        "speech_depth",
        "timing",
    }


def test_implementation_modules_reference_central_prompt_catalog() -> None:
    for module in (speaker, coordinator, typesafe):
        source = inspect.getsource(module)
        assert "PROMPTS" in source
        assert "You are {" not in source
        assert "system_identity" not in source


def test_semantic_decision_contract_contains_no_behavioral_scores_or_thresholds() -> None:
    questions = config.PROMPTS.jev_questions("Atlas")
    for question in questions.values():
        assert isinstance(question, dict)
        assert question.get("type") == "choice"
    assert not {
        "addressed_threshold",
        "mission_threshold",
        "memory_threshold",
    }.intersection(config.DecisionConfig.model_fields)
