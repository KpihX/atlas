import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from atlas.config import AppConfig
from atlas.core.coordinator import Coordinator
from atlas.core.models import (
    AtlasState,
    BoardConcept,
    BoardMergeEvidence,
    BoardProposal,
    BoardRetirement,
    Card,
    LLMResult,
)
from atlas.core.tools import ToolRegistry


class BoardGenerator:
    available = True

    def __init__(self, responses: list[object]) -> None:
        self.responses = [json.dumps(response) for response in responses]
        self.messages: list[list[dict[str, str]]] = []

    async def generate(self, messages: list[dict[str, str]], model_id: str | None = None) -> LLMResult:
        self.messages.append(messages)
        return LLMResult(content=self.responses.pop(0), model=model_id or "test", provider="test")

    async def stream(self, messages: list[dict[str, str]], model_id: str | None = None) -> AsyncIterator[str]:
        if False:
            yield ""


def card(card_id: str, concept_key: str, title: str, body: str, kind: str = "question") -> Card:
    return Card.model_validate(
        {
            "id": card_id,
            "concept_key": concept_key,
            "kind": kind,
            "title": title,
            "body": body,
        }
    )


def concept(
    concept_key: str,
    title: str,
    body: str,
    source_card_ids: list[str],
    kind: str = "question",
    merge_evidence: BoardMergeEvidence | None = None,
) -> BoardConcept:
    return BoardConcept.model_validate(
        {
            "concept_key": concept_key,
            "kind": kind,
            "title": title,
            "body": body,
            "source_card_ids": source_card_ids,
            "merge_evidence": merge_evidence,
        }
    )


def proposal(
    desired_cards: list[BoardConcept],
    retirements: list[BoardRetirement] | None = None,
) -> BoardProposal:
    return BoardProposal(desired_cards=desired_cards, retirements=retirements or [])


def equivalent_merge() -> BoardMergeEvidence:
    return BoardMergeEvidence(
        same_resolution=True,
        mutually_substitutable=True,
        loses_independent_value=False,
        rationale="Both cards ask the same question and either answer resolves both.",
    )


def test_stable_desired_board_is_a_noop() -> None:
    existing = [card("card-a", "scope", "Define scope", "Clarify goals and deliverables.")]
    desired = [concept("scope", "Define scope", "Clarify goals and deliverables.", ["card-a"])]

    assert Coordinator.reconcile_board(existing, proposal(desired)) == []


def test_clarification_updates_the_existing_concept() -> None:
    existing = [card("card-a", "scope", "Define scope", "Clarify goals.")]
    desired = [
        concept(
            "scope",
            "Define scope and deliverables",
            "Clarify goals, deliverables, boundaries and ownership.",
            ["card-a"],
        )
    ]

    operations = Coordinator.reconcile_board(existing, proposal(desired))

    assert len(operations) == 1
    assert operations[0].action == "update"
    assert operations[0].card_id == "card-a"


def test_overlapping_cards_merge_and_obsolete_card_deletes() -> None:
    existing = [
        card("card-a", "scope", "Define scope", "Clarify project goals."),
        card("card-b", "scope-tasks", "Assign tasks", "Define scope and divide tasks.", "suggestion"),
        card("card-c", "status-policing", "Monitor adherence", "Regularly police the timeline."),
    ]
    desired = [
        concept(
            "scope-and-ownership",
            "Define scope and ownership",
            "Agree on goals, boundaries, deliverables and task ownership.",
            ["card-a", "card-b"],
            "suggestion",
            equivalent_merge(),
        )
    ]

    operations = Coordinator.reconcile_board(
        existing,
        proposal(
            desired,
            [BoardRetirement(card_id="card-c", reason="The monitoring proposal was withdrawn.")],
        ),
    )

    assert [operation.action for operation in operations] == ["merge", "delete"]
    assert operations[0].card_id == "card-a"
    assert operations[0].merge_ids == ["card-b"]
    assert operations[1].card_id == "card-c"


def test_independent_new_concept_is_created_without_touching_survivors() -> None:
    existing = [card("card-a", "scope", "Define scope", "Clarify goals and deliverables.")]
    desired = [
        concept("scope", "Define scope", "Clarify goals and deliverables.", ["card-a"]),
        concept(
            "expert-validation",
            "Validate with domain experts",
            "Consult experts before committing to the intervention workflow.",
            [],
            "suggestion",
        ),
    ]

    operations = Coordinator.reconcile_board(existing, proposal(desired))

    assert len(operations) == 1
    assert operations[0].action == "create"
    assert operations[0].concept_key == "expert-validation"


@pytest.mark.asyncio
async def test_curator_review_merges_overlap_before_reconciliation() -> None:
    config_path = Path(__file__).parents[1] / "src" / "atlas" / "default_config.json"
    config = AppConfig.model_validate_json(config_path.read_text())
    draft = {
        "desired_cards": [
            {
                "kind": "question",
                "concept_key": "evaluate-platform",
                "title": "Evaluate platform relevance",
                "body": "Assess whether the platform fits the project.",
                "source_card_ids": ["card-a"],
            },
            {
                "kind": "suggestion",
                "concept_key": "analyze-platform",
                "title": "Analyze platform features",
                "body": "Review features that could improve the project.",
                "source_card_ids": ["card-b"],
            },
        ]
    }
    reviewed = {
        "desired_cards": [
            {
                "kind": "question",
                "concept_key": "platform-integration",
                "title": "Evaluate platform integration",
                "body": "Assess relevant features and whether they improve the project.",
                "source_card_ids": ["card-a", "card-b"],
                "merge_evidence": {
                    "same_resolution": True,
                    "mutually_substitutable": True,
                    "loses_independent_value": False,
                    "rationale": "Both cards evaluate the same platform integration decision.",
                },
            }
        ],
        "retirements": [],
    }
    generator = BoardGenerator([draft, reviewed])
    coordinator = Coordinator(generator, ToolRegistry(1), config.policy, config.llm.roles)
    state = AtlasState(
        project_id="atlas",
        protocol_version=1,
        cards=[
            card("card-a", "evaluate-platform", "Evaluate platform relevance", "Assess fit."),
            card(
                "card-b",
                "analyze-platform",
                "Analyze platform features",
                "Review useful features.",
                "suggestion",
            ),
        ],
    )

    operations = await coordinator.curate_board(state)

    assert len(generator.messages) == 2
    assert "Card count is neutral" in generator.messages[1][-1]["content"]
    assert len(operations) == 1
    assert operations[0].action == "merge"
    assert operations[0].card_id == "card-a"
    assert operations[0].merge_ids == ["card-b"]


def test_omitted_cards_are_preserved_without_explicit_retirement() -> None:
    existing = [
        card("card-a", "scope", "Define scope", "Clarify goals."),
        card("card-b", "expert-validation", "Validate with experts", "Consult practitioners."),
    ]

    operations = Coordinator.reconcile_board(
        existing,
        proposal([concept("scope", "Define scope", "Clarify goals.", ["card-a"])]),
    )

    assert operations == []


def test_unproven_merge_is_rejected_without_mutating_sources() -> None:
    existing = [
        card("card-a", "scope", "Define scope", "Clarify goals."),
        card("card-b", "expert-validation", "Validate with experts", "Consult practitioners."),
    ]
    destructive = concept(
        "project-readiness",
        "Prepare the project",
        "Define scope and consult experts.",
        ["card-a", "card-b"],
        "suggestion",
    )

    assert Coordinator.reconcile_board(existing, proposal([destructive])) == []


def test_related_independent_concepts_survive_together() -> None:
    existing = [
        card("scope", "project-scope", "Define scope", "Clarify goals and deliverables."),
        card("assets", "external-assets", "Inspect external assets", "Understand available repositories."),
        card("experts", "expert-validation", "Validate with experts", "Consult field practitioners."),
        card("method", "methodology", "Choose a methodology", "Decide how the system complements practice."),
    ]
    desired = [concept(item.concept_key, item.title, item.body, [item.id], item.kind) for item in existing]

    assert Coordinator.reconcile_board(existing, proposal(desired)) == []


def test_explicit_retirement_deletes_only_the_named_card() -> None:
    existing = [
        card("card-a", "scope", "Define scope", "Clarify goals."),
        card("card-b", "obsolete", "Old assumption", "An assumption disproven by evidence."),
    ]

    operations = Coordinator.reconcile_board(
        existing,
        proposal(
            [concept("scope", "Define scope", "Clarify goals.", ["card-a"])],
            [BoardRetirement(card_id="card-b", reason="Later evidence disproved this assumption.")],
        ),
    )

    assert len(operations) == 1
    assert operations[0].action == "delete"
    assert operations[0].card_id == "card-b"
