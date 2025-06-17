"""Background jobs: reminders & follow-up after lessons (включая misfire-grace)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.models.db import AsyncSessionLocal
from app.models.models import Lesson, User

# ── ГЛОБАЛЬНЫЙ планировщик ──
scheduler = AsyncIOScheduler(
    timezone=timezone.utc,
    job_defaults={
        "misfire_grace_time": 7200,   # ← 2 ч «запас» на сетевые зависания
        "coalesce": True,             # если пропустили несколько — сработает один
        "max_instances": 2,
    },
)

bot = None  # присваивается в main.py


# ───────────────── helper-ы ─────────────────
def _fmt(dt, offset):          # DD.MM HH:MM в локальном времени
    return (dt + timedelta(hours=offset)).strftime("%d.%m %H:%M")


def _contact(u: User):
    return f"@{u.telegram_id}"


# ───────────────── reminders ─────────────────
async def _send_reminder(lesson_id: int, target: Literal["student", "tutor"]):
    async with AsyncSessionLocal() as s:
        les: Lesson | None = await s.get(Lesson, lesson_id)
        if not les or les.is_canceled:
            return
        tutor: User = await s.get(User, les.tutor_id)
        student: User = await s.get(User, les.student_id)

        if target == "student":
            dt_str = _fmt(les.scheduled_time, student.timezone_offset)
            txt = (
                f"⏰ Через {student.reminder_minutes} мин урок с репетитором.\n"
                f"🕒 <b>{dt_str}</b>\n"
                f"Контакт: {_contact(tutor)}"
            )
            await bot.send_message(student.telegram_id, txt, parse_mode="HTML")
        else:
            dt_str = _fmt(les.scheduled_time, tutor.timezone_offset)
            parent = student.parent_contact or "не указано"
            txt = (
                f"⏰ Через {tutor.reminder_minutes} мин урок с <b>{student.full_name}</b>.\n"
                f"🕒 <b>{dt_str}</b>\n"
                f"Контакт ученика: {_contact(student)}\n"
                f"Контакт родителя: {parent}"
            )
            await bot.send_message(tutor.telegram_id, txt, parse_mode="HTML")


async def _send_followup(lesson_id: int):
    async with AsyncSessionLocal() as s:
        les: Lesson | None = await s.get(Lesson, lesson_id)
        if not les or les.is_canceled:
            return
        tutor: User = await s.get(User, les.tutor_id)
        student: User = await s.get(User, les.student_id)

        parent = student.parent_contact or "не указано"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✏️ Задать ДЗ", callback_data=f"give_hw:{student.id}")]]
        )
        await bot.send_message(
            tutor.telegram_id,
            f"📗 Урок с <b>{student.full_name}</b> завершился.\n"
            "Не забудьте выдать домашнее задание.\n"
            f"Контакт родителя: {parent}",
            parse_mode="HTML",
            reply_markup=kb,
        )


def schedule_jobs(lesson: Lesson, tutor: User, student: User):
    """Ставим два напоминания + follow-up, учитывая misfire-grace."""
    now = datetime.utcnow()

    for who, user in (("student", student), ("tutor", tutor)):
        lead = user.reminder_minutes or 60
        run_at = lesson.scheduled_time - timedelta(minutes=lead)
        if run_at > now:
            scheduler.add_job(
                _send_reminder,
                "date",
                run_date=run_at,
                args=[lesson.id, who],
                id=f"rem_{who}_{lesson.id}",
                replace_existing=True,
            )

    end_at = lesson.scheduled_time + timedelta(minutes=lesson.duration)
    if end_at > now:
        scheduler.add_job(
            _send_followup,
            "date",
            run_date=end_at,
            args=[lesson.id],
            id=f"fol_{lesson.id}",
            replace_existing=True,
        )
