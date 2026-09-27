from __future__ import annotations

import asyncio
import json
from statistics import median
from time import monotonic

import httpx

from atlas.config import load_environment, secret

MODELS = (
    ("gpt-5.6-luna", "none"),
    ("gpt-5-mini", "minimal"),
    ("gpt-5-nano", "minimal"),
    ("gpt-4.1-mini", None),
    ("gpt-4.1-nano", None),
)
SPEAKER_INPUT = (
    "You are Atlas. Reply naturally in one short sentence. The participant asks: "
    "Where is the research now? No task is currently running."
)
NOTES_INPUT = (
    "Return JSON only with synthesis, participants, topics, hypotheses, questions, decisions, actions, "
    "current_work, source_ids. The team is considering an emergency-response hackathon. They need domain "
    "expertise and verified data. Pavel has not answered yet."
)


async def trial(
    client: httpx.AsyncClient,
    key: str,
    model: str,
    effort: str | None,
    text: str,
) -> tuple[float, bool]:
    started = monotonic()
    request: dict[str, object] = {
        "model": model,
        "input": [{"role": "user", "content": text}],
        "max_output_tokens": 512,
    }
    if effort is not None:
        request["reasoning"] = {"effort": effort}
    response = await client.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=request,
    )
    elapsed = monotonic() - started
    if response.status_code != 200:
        return elapsed, False
    payload = response.json()
    output = payload.get("output_text", "")
    if not output:
        output = "".join(
            item.get("text", "")
            for block in payload.get("output", [])
            for item in block.get("content", [])
            if isinstance(item, dict)
        )
    return elapsed, bool(str(output).strip())


async def main() -> None:
    load_environment()
    key = secret("OPENAI_API_KEY")
    if key is None:
        raise RuntimeError("OPENAI_API_KEY is unavailable")
    async with httpx.AsyncClient(timeout=30) as client:
        for model, effort in MODELS:
            speaker: list[float] = []
            notes: list[float] = []
            valid = True
            for _ in range(2):
                elapsed, ok = await trial(client, key, model, effort, SPEAKER_INPUT)
                speaker.append(elapsed)
                valid = valid and ok
            elapsed, ok = await trial(client, key, model, effort, NOTES_INPUT)
            notes.append(elapsed)
            valid = valid and ok
            print(
                json.dumps(
                    {
                        "model": model,
                        "valid": valid,
                        "speaker_median_seconds": round(median(speaker), 3),
                        "notes_seconds": round(notes[0], 3),
                    }
                )
            )


if __name__ == "__main__":
    asyncio.run(main())
