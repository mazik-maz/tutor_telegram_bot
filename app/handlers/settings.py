"""User settings: timezone & reminder-minutes."""
from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, ReplyKeyboardRemove

from sqlalchemy import select

from app.models.models import User
from app.keyboards.menus import TUTOR_MENU, STUDENT_MENU

router = Router()


class SetTZ(StatesGroup):
    waiting_offset = State()


class SetReminder(StatesGroup):
    waiting_minutes = State()


# ─────────────  /settings (или кнопка «⚙️ Настройки»)  ─────────────
@router.message(Command("settings"))
@router.message(F.text == "⚙️ Настройки")
async def settings_menu(msg: Message):
    text = (
        "<b>Настройки</b>\n"
        "• /set_timezone – указать часовой пояс (UTC ±n)\n"
        "• /set_reminder – время напоминания, мин"
    )
    await msg.answer(text, parse_mode="HTML")


# ─────────────  /set_timezone  ─────────────
@router.message(Command("set_timezone"))
async def tz_start(msg: Message, state: FSMContext):
    await msg.answer(
        "Введите смещение UTC (целое −12…+14).\n"
        "Напр.: <code>+3</code> или <code>-5</code>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(SetTZ.waiting_offset)


@router.message(SetTZ.waiting_offset)
async def tz_save(msg: Message, session, state: FSMContext):
    try:
        offset = int(msg.text.strip())
        if offset < -12 or offset > 14:
            raise ValueError
    except ValueError:
        await msg.answer("Нужно целое число от −12 до +14. Попробуйте снова:")
        return

    user: User = (
        await session.execute(select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()

    user.timezone_offset = offset
    await msg.answer(f"✅ Часовой пояс установлен: UTC{offset:+}")
    await state.clear()

    menu = TUTOR_MENU if user.is_tutor else STUDENT_MENU
    await msg.answer("Готово.", reply_markup=menu)


# ─────────────  /set_reminder  ─────────────
@router.message(Command("set_reminder"))
async def rem_start(msg: Message, state: FSMContext):
    await msg.answer(
        "За сколько минут до урока присылать напоминания?\n"
        "Введите число (1–180):",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(SetReminder.waiting_minutes)


@router.message(SetReminder.waiting_minutes)
async def rem_save(msg: Message, session, state: FSMContext):
    if not msg.text.isdigit() or not (1 <= int(msg.text) <= 180):
        await msg.answer("Нужно число от 1 до 180. Попробуйте ещё раз:")
        return

    user: User = (
        await session.execute(select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    user.reminder_minutes = int(msg.text)
    await msg.answer(f"✅ Напоминание будет за {user.reminder_minutes} мин.")
    await state.clear()

    menu = TUTOR_MENU if user.is_tutor else STUDENT_MENU
    await msg.answer("Готово.", reply_markup=menu)
