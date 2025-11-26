import asyncio
import logging
from typing import Dict

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Tenant
from app.bots.child.bot_instance import run_child_bot

logger = logging.getLogger("pocket_saas.children")


class ChildrenManager:
    """
    Менеджер детских ботов.

    Периодически читает из БД список активных тенантов и:
    - поднимает новые инстансы ботов для тех, у кого ещё нет задач;
    - останавливает ботов для деактивированных тенантов.
    """

    def __init__(self) -> None:
        self.tasks: Dict[int, asyncio.Task] = {}

    async def tick(self) -> None:
        # читаем актуальный список тенантов
        async with SessionLocal() as session:
            res = await session.execute(
                select(Tenant.id, Tenant.bot_token, Tenant.is_active)
            )
            rows = res.all()

        active_ids: set[int] = set()

        for tenant_id, bot_token, is_active in rows:
            if not is_active:
                continue
            if not bot_token:
                continue

            active_ids.add(tenant_id)

            # если бот ещё не запущен или задача упала — поднимаем
            task = self.tasks.get(tenant_id)
            if task is None or task.done():
                logger.info("Starting child bot for tenant %s", tenant_id)
                self.tasks[tenant_id] = asyncio.create_task(
                    run_child_bot(bot_token=bot_token, tenant_id=tenant_id)
                )

        # останавливаем ботов для деактивированных тенантов
        for tenant_id, task in list(self.tasks.items()):
            if tenant_id not in active_ids:
                logger.info("Stopping child bot for tenant %s", tenant_id)
                task.cancel()
                del self.tasks[tenant_id]


async def run_children_loop() -> None:
    """
    Главный цикл для менеджера детских ботов.
    Вызывается из run_children.py
    """
    logger.info("Children runner started")
    manager = ChildrenManager()

    while True:
        try:
            await manager.tick()
        except Exception as e:  # noqa: BLE001
            logger.exception("Children manager tick error: %s", e)

        await asyncio.sleep(5)