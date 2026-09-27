from __future__ import annotations

import asyncio
import sys
from time import monotonic

import httpx

from atlas.config import load_config
from atlas.llm import OpenAICompatibleGenerator


async def main() -> None:
    model_id = sys.argv[1] if len(sys.argv) > 1 else None
    no_thinking = len(sys.argv) > 2 and sys.argv[2] == "none"
    config = load_config().llm
    if model_id is not None:
        if no_thinking:
            models = [
                item.model_copy(update={"reasoning_effort": None}) if item.id == model_id else item
                for item in config.models
            ]
            config = config.model_copy(update={"models": models})
        config = config.model_copy(update={"active_model": model_id})
    generator = OpenAICompatibleGenerator(config)
    try:
        started = monotonic()
        try:
            result = await generator.generate(
                [
                    {"role": "system", "content": "Return only JSON."},
                    {
                        "role": "user",
                        "content": (
                            'Return {"speech":"Bonjour","card_title":"Test","card_body":"OK",'
                            '"card_kind":"finding","working":"Done","tool":null}'
                        ),
                    },
                ],
                model_id,
            )
        except httpx.HTTPStatusError as error:
            body = error.response.json()
            detail = body.get("error", {}) if isinstance(body, dict) else {}
            print(
                f"status={error.response.status_code} type={detail.get('type', 'unknown')} "
                f"code={detail.get('code', 'unknown')}"
            )
            return
        elapsed_ms = round((monotonic() - started) * 1000)
        print(f"provider={result.provider} model={result.model} elapsed_ms={elapsed_ms}")
        print(result.content)
    finally:
        await generator.close()


if __name__ == "__main__":
    asyncio.run(main())
