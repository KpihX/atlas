from __future__ import annotations

import asyncio

from atlas.adapters.exa import ExaSearch
from atlas.adapters.jinko import JinkoFlights
from atlas.config import load_config


async def main() -> None:
    config = load_config()
    exa = ExaSearch(config.tools.exa)
    jinko = JinkoFlights(config.tools.jinko)
    try:
        exa_result = await exa.search(
            {"query": "official Nepal flood response information", "num_results": 2}
        )
        print("exa", exa_result.get("status"), len(exa_result.get("results", [])))
        jinko_result = await jinko.flight_calendar(
            {
                "origins": ["CDG"],
                "destinations": ["DLA"],
                "departure_date": "2026-10-15",
                "currency": "EUR",
            }
        )
        print("jinko", jinko_result.get("status"), bool(jinko_result.get("calendar")))
    finally:
        await exa.close()
        await jinko.close()


if __name__ == "__main__":
    asyncio.run(main())
