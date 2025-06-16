from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select  # ← импортируем select для запросов
from sqlalchemy.orm import selectinload  # ← импортируем selectinload для подгрузки отношений
from app.models.db import AsyncSessionLocal
from app.models.models import Lesson

scheduler = AsyncIOScheduler(timezone=timezone.utc)
bot = None  # будет установлен из main

async def send_lesson_reminder(lesson_id: int):
    """Отправляет напоминание о предстоящем уроке (за 1 час до начала)."""
    from aiogram.utils.markdown import escape_html
    async with AsyncSessionLocal() as s:
        # Загружаем урок вместе с связанными объектами (учеником и репетитором)
        result = await s.execute(
            select(Lesson).options(selectinload(Lesson.student), selectinload(Lesson.tutor))
            .where(Lesson.id == lesson_id)
        )
        lesson: Lesson | None = result.scalar()
        if not lesson or lesson.is_canceled:
            return
        student = lesson.student
        tutor = lesson.tutor
        time_local = lesson.time
        text_student = f"Напоминание: у вас скоро занятие с {escape_html(tutor.full_name)} в {time_local.strftime('%d.%m %H:%M')}"
        text_tutor = f"Напоминание: занятие с {escape_html(student.full_name)} в {time_local.strftime('%d.%m %H:%M')}"
        try:
            await bot.send_message(student.telegram_id, text_student)
            await bot.send_message(tutor.telegram_id, text_tutor)
        except Exception as e:
            print("reminder error", e)

def schedule_job(lesson_id: int, when: datetime):
    scheduler.add_job(send_lesson_reminder, "date", run_date=when, args=[lesson_id], id=f"lesson_{lesson_id}", replace_existing=True)
