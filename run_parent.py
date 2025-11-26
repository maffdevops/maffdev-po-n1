import asyncio

from app.utils.logging import setup_logging
from app.bots.parent_bot import run_parent_bot
from app.db import init_db


async def main() -> None:
    setup_logging()
    await init_db()
    await run_parent_bot()


if __name__ == "__main__":
    asyncio.run(main())
