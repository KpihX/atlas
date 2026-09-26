from __future__ import annotations

import re

STRUCTURED = re.compile(r'("[A-Za-z_][\w-]*"\s*:|tool_call|request_id|arguments\s*=)', re.I)
URL = re.compile(r"https?://\S+")
MARKDOWN_LINK = re.compile(r"\[([^]]+)]\([^)]*\)")


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
