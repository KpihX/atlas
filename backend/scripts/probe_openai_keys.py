import asyncio
from pathlib import Path

import httpx

root_env = Path(".env").read_text()
keys = {}
for line in root_env.splitlines():
    if line.startswith("OPENAI_API_KEY"):
        k, v = line.split("=", 1)
        keys[k.strip()] = v.strip().strip("'\"")


async def test_key(name: str, key: str) -> None:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        r_models = await client.get("https://api.openai.com/v1/models", headers=headers)
        print(f"KEY {name} models status: {r_models.status_code}")

        # Test standard chat/completions with gpt-4o-mini
        payload_chat = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5,
        }
        r_chat = await client.post(
            "https://api.openai.com/v1/chat/completions", headers=headers, json=payload_chat
        )
        print(f"KEY {name} chat (gpt-4o-mini): {r_chat.status_code}")
        if r_chat.status_code != 200:
            print(f"  detail chat: {r_chat.text[:140]}")

        # Test responses with gpt-5.6-luna
        payload_resp = {"model": "gpt-5.6-luna", "input": [{"role": "user", "content": "ping"}]}
        r_resp = await client.post("https://api.openai.com/v1/responses", headers=headers, json=payload_resp)
        print(f"KEY {name} responses (gpt-5.6-luna): {r_resp.status_code}")
        if r_resp.status_code != 200:
            print(f"  detail resp: {r_resp.text[:140]}")


async def main() -> None:
    for name, key in keys.items():
        await test_key(name, key)


if __name__ == "__main__":
    asyncio.run(main())
