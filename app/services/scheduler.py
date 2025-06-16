from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.models.db import AsyncSessionLocal
from app.models.models import Lesson, User

scheduler = AsyncIOScheduler(timezone=timezone.utc)

bot = None  # подставляется из main.py


# ───────────────────────── helpers ───────────────────────── #
def _local(dt: datetime, offset: int) -> str:
    """Дата-время в строку DD.MM HH:MM по UTC-смещению."""
    return (dt + timedelta(hours=offset)).strftime("%d.%m %H:%M")


# ───────────────────────── reminder ───────────────────────── #
async def send_lesson_reminder(lesson_id: int, target: Literal["tutor", "student"]):
    async with AsyncSessionLocal() as s:
        lesson: Lesson | None = await s.get(Lesson, lesson_id)
        if not lesson or lesson.is_canceled:
            return

        tutor: User = await s.get(User, lesson.tutor_id)
        student: User = await s.get(User, lesson.student_id)

        if target == "student":
            time_str = _local(lesson.scheduled_time, student.timezone_offset)
            contact = (
                f"@{tutor.telegram_id}" if not tutor.parent_contact else tutor.parent_contact
            )
            text = (
                f"Напоминание: через {student.reminder_minutes} мин урок "
                f"с репетитором. Время: {time_str}. Контакт: {contact}"
            )
            await bot.send_message(student.telegram_id, text)
        else:  # tutor
            time_str = _local(lesson.scheduled_time, tutor.timezone_offset)
            text = (
                f"Напоминание: через {tutor.reminder_minutes} мин урок "
                f"с учеником {student.full_name}. Время: {time_str}. "
                f"Контакт ученика: @{student.telegram_id}"
            )
            await bot.send_message(tutor.telegram_id, text)


# ───────────────────────── follow-up ───────────────────────── #
async def send_lesson_followup(lesson_id: int):
    async with AsyncSessionLocal() as s:
        lesson: Lesson | None = await s.get(Lesson, lesson_id)
        if not lesson or lesson.is_canceled:
            return
        tutor: User = await s.get(User, lesson.tutor_id)
        student: User = await s.get(User, lesson.student_id)

        await bot.send_message(
            tutor.telegram_id,
            f"Занятие с {student.full_name} завершилось.\n"
            "Не забудьте выдать домашнее задание и отметить результат.",
        )


# ───────────────────────── scheduler API ───────────────────────── #
def schedule_jobs(lesson: Lesson, tutor: User, student: User):
    """Ставим два напоминания (репетитор/ученик) + follow-up."""
    now = datetime.utcnow()

    # reminders
    for who, user in (("tutor", tutor), ("student", student)):
        lead = user.reminder_minutes or 60
        run_time = lesson.scheduled_time - timedelta(minutes=lead)
        if run_time > now:
            scheduler.add_job(
                send_lesson_reminder,
                "date",
                run_date=run_time,
                args=[lesson.id, who],
                id=f"rem_{lesson.id}_{who}",
                replace_existing=True,
            )

    # follow-up
    end_time = lesson.scheduled_time + timedelta(minutes=lesson.duration)
    if end_time > now:
        scheduler.add_job(
            send_lesson_followup,
            "date",
            run_date=end_time,
            args=[lesson.id],
            id=f"fol_{lesson.id}",
            replace_existing=True,
        )
