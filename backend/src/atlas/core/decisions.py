from __future__ import annotations

from .models import AtlasState, Decision


def conservative_fallback(state: AtlasState, text: str) -> Decision:
    del state, text
    return Decision(
        route="capture",
        addressee="uncertain",
        memory="capture",
        rationale="Semantic decision service unavailable; Atlas records context without guessing intent.",
    )
