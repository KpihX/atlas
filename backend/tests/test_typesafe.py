from __future__ import annotations

import httpx
import pytest

from atlas.adapters.typesafe import TypeSafeDecision
from atlas.config import DecisionConfig
from atlas.core.models import AtlasState, Decision


@pytest.mark.asyncio
async def test_semantic_router_returns_coherent_addressee_initiative_memory_and_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_TYPESAFE_KEY", "test")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read().decode()
        assert '"memory"' in payload
        assert "speech_depth" in payload
        assert "initiative" in payload
        return httpx.Response(
            200,
            json={
                "answers": {
                    "addressee": {"choice": "room"},
                    "route": {"choice": "investigate"},
                    "memory": {"choice": "capture"},
                    "initiative": {"choice": "proactive"},
                    "speech_depth": {"choice": "brief"},
                    "timing": {"choice": "next_gap"},
                }
            },
        )

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    decision = TypeSafeDecision(
        DecisionConfig(
            provider="typesafe",
            endpoint="https://example.test",
            secret_env="TEST_TYPESAFE_KEY",
            model="jev-test",
        ),
        transport,
    )
    result = await decision.evaluate(
        AtlasState(project_id="atlas", protocol_version=1),
        "Could you investigate this for the room?",
    )
    assert result.route == "investigate"
    assert result.addressee == "room"
    assert result.initiative == "proactive"
    assert result.memory == "capture"
    assert result.speech_depth == "brief"
    await transport.aclose()


def test_legacy_numeric_decision_is_migrated_without_restoring_score_behavior() -> None:
    decision = Decision.model_validate(
        {
            "route": "investigate",
            "addressed_probability": 0.91,
            "salience": 0.8,
            "speech_value": 0.7,
            "memory_value": 0.6,
            "mission_probability": 0.4,
            "intervention_urgency": 0.9,
            "speech_depth": "brief",
            "timing": "next_gap",
        }
    )
    assert decision.route == "investigate"
    assert decision.addressee == "uncertain"
    assert decision.memory == "capture"
    assert decision.initiative == "assigned"
    assert set(decision.model_dump()) == {
        "route",
        "addressee",
        "memory",
        "initiative",
        "speech_depth",
        "timing",
        "rationale",
    }
