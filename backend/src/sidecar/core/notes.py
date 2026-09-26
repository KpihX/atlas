from __future__ import annotations

import re

from .models import Utterance

SOURCE_ID = re.compile(r"\[(utt_[^\]]+)]")


def preserve_notes(previous: str, candidate: str, new_turns: list[Utterance]) -> str:
    if not previous:
        return candidate
    previous_ids = set(SOURCE_ID.findall(previous))
    candidate_ids = set(SOURCE_ID.findall(candidate))
    loses_history = not previous_ids.issubset(candidate_ids)
    shrinks_materially = len(candidate) < len(previous) * 0.9
    base = previous if loses_history or shrinks_materially else candidate
    base_ids = set(SOURCE_ID.findall(base))
    missing = [turn for turn in new_turns if turn.id not in base_ids]
    if not missing:
        return base
    additions = "\n".join(f"- {turn.text} [{turn.id}]" for turn in missing)
    return f"{base.rstrip()}\n\n## Latest additions\n{additions}\n"
