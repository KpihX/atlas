from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from atlas.config import PolicyConfig

from .models import Speech


@dataclass
class SpeechGate:
    policy: PolicyConfig
    _last_proactive_at: float = 0
    _last_text: str = ""

    def validate(self, speech: Speech) -> str | None:
        text = " ".join(speech.text.split()).strip()
        if not text:
            return "empty"
        if text.casefold() == self._last_text.casefold():
            return "duplicate"
        speech.text = text
        if speech.reason == "critical_finding":
            elapsed = monotonic() - self._last_proactive_at
            if elapsed < self.policy.unsolicited_speech_cooldown_seconds:
                return "proactive_cooldown"
        return None

    def delivered(self, speech: Speech) -> None:
        self._last_text = speech.text
        if speech.reason == "critical_finding":
            self._last_proactive_at = monotonic()
