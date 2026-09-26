from __future__ import annotations

from .models import Decision, MeetingState


def conservative_fallback(state: MeetingState, text: str) -> Decision:
    name = state.assistant_name.casefold().strip()
    addressed = bool(name and name in text.casefold())
    if addressed:
        return Decision(
            route="respond",
            addressed_probability=1,
            salience=1,
            speech_value=1,
            timing="next_gap",
            rationale="Explicit wake name matched while semantic decision service was unavailable.",
        )
    return Decision(route="capture", rationale="Conservative fallback captures without proactive speech.")
