from __future__ import annotations

import re

STRUCTURED = re.compile(r'("[A-Za-z_][\w-]*"\s*:|tool_call|request_id|arguments\s*=)', re.I)
URL = re.compile(r"https?://\S+")
MARKDOWN_LINK = re.compile(r"\[([^]]+)]\([^)]*\)")
SENTENCE = re.compile(r"(?<=[.!?])\s+")


def spoken_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned or STRUCTURED.search(cleaned) or cleaned.count("{") + cleaned.count("}") >= 2:
        return ""
    cleaned = MARKDOWN_LINK.sub(r"\1", cleaned)
    cleaned = URL.sub("the source shown on the board", cleaned)
    cleaned = re.sub(r"```.*?```", "", cleaned, flags=re.S)
    cleaned = cleaned.replace("`", "")
    cleaned = re.sub(r"(?m)^\s{0,3}[#>*+-]+\s*", "", cleaned)
    cleaned = cleaned.replace("\\", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def spoken_segments(text: str, max_chars: int = 360) -> list[str]:
    cleaned = spoken_text(text)
    if not cleaned:
        return []
    result: list[str] = []
    for sentence in SENTENCE.split(cleaned):
        pending = sentence.strip()
        while len(pending) > max_chars:
            split = pending.rfind(" ", 0, max_chars + 1)
            if split < max_chars // 2:
                split = max_chars
            result.append(pending[:split].strip())
            pending = pending[split:].strip()
        if pending:
            result.append(pending)
    return result


def bounded_spoken_turn(text: str, max_sentences: int) -> str:
    return " ".join(spoken_segments(text)[:max_sentences]).strip()
