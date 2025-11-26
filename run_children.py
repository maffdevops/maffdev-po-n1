import asyncio

from app.utils.logging import setup_logging
from app.bots.children_runner import run_children_loop
from app.db import init_db


async def main() -> None:
    setup_logging()
    await init_db()
    await run_children_loop()


if __name__ == "__main__":
    asyncio.run(main())
