from __future__ import annotations

import asyncio

from sidecar.config import load_config
from sidecar.llm import OpenAICompatibleGenerator


async def main() -> None:
    generator = OpenAICompatibleGenerator(load_config().llm)
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
            ]
        )
        print(result.content)
    finally:
        await generator.close()


if __name__ == "__main__":
    asyncio.run(main())
