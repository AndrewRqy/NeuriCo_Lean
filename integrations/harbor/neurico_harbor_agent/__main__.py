"""stdio entrypoint for the NeuriCo Harbor ACP agent."""

from __future__ import annotations

import asyncio

from acp import run_agent

from .agent import NeuricoHarborAgent


async def _run() -> None:
    await run_agent(NeuricoHarborAgent())


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
