from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.middlewares.db import DBSessionMiddleware
from app.handlers import common_router, lessons_router, homework_router, students_router
from app.models.db import Base, engine
from app.services import scheduler as sched_mod


async def _set_commands(bot: Bot):
    await bot.set_my_commands(
        [
            BotCommand(command="register", description="Регистрация репетитора"),
            BotCommand(command="add_lesson", description="Добавить урок"),
            BotCommand(command="lessons", description="Список уроков"),
            BotCommand(command="edit_lesson", description="Изменить/отменить урок"),
            BotCommand(command="give_homework", description="Задать ДЗ"),
            BotCommand(command="answer_homework", description="Ответить на ДЗ"),
            BotCommand(command="homeworks", description="Список ДЗ"),
            BotCommand(command="pending", description="Ожидают проверки"),
            BotCommand(command="check_homework", description="Отметить проверку ДЗ"),
            # BotCommand(command="", description=""),
            # BotCommand(command="", description=""),
            # BotCommand(command="", description=""),
        ]
    )


async def on_startup(bot: Bot):
    # создать таблицы (если нет) – для мелкого проекта хватает auto‑create
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _set_commands(bot)
    logging.info("DB ready & commands set")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    # Bot & storage
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    storage = RedisStorage.from_url(settings.REDIS_URL)

    # expose bot into scheduler module
    sched_mod.bot = bot
    sched_mod.scheduler.start()

    dp = Dispatcher(storage=storage)

    # middlewares (DB session)
    db_mw = DBSessionMiddleware()
    dp.message.middleware(db_mw)
    dp.callback_query.middleware(db_mw)

    # routers
    dp.include_router(common_router)
    dp.include_router(lessons_router)
    dp.include_router(homework_router)
    dp.include_router(students_router)

    # startup callback
    dp.startup.register(on_startup)

    logging.info("Tutor‑bot is up. Start polling …")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")