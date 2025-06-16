from aiogram import BaseMiddleware
from aiogram.types import Update
from app.models.db import AsyncSessionLocal


class DBSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Update, data: dict):
        async with AsyncSessionLocal() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            return result