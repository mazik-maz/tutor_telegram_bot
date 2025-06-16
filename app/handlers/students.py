from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
)
from sqlalchemy import select

from app.keyboards.menus import TUTOR_MENU
from app.models.models import User

router = Router()

# ───────── FSM for adding student ───────── #
class AddStudent(StatesGroup):
    waiting_id = State()
    waiting_name = State()
    waiting_tz = State()

# ------- list students -------- #
@router.message(Command("students"))
@router.message(F.text == "👥 Мои ученики")
async def list_students(msg: Message, session):
    tutor: User | None = (
        await session.execute(
            select(User).where(User.telegram_id == msg.from_user.id)
        )
    ).scalar()

    if not tutor or not tutor.is_tutor:
        await msg.answer("Команда доступна только для репетитора.")
        return

    # ── вот здесь вместо tutor.students делаем явный запрос ──
    students = (
        await session.execute(
            select(User).where(User.tutor_id == tutor.id, User.is_tutor.is_(False))
        )
    ).scalars().all()

    if not students:
        await msg.answer(
            "У вас нет учеников. Используйте кнопку ➕ Ученика или команду /add_student.",
            reply_markup=TUTOR_MENU,
        )
        return

    lines = ["<b>Ваши ученики:</b>"]
    for st in students:
        lines.append(f"• {st.full_name} — ID {st.telegram_id}")
    await msg.answer("\n".join(lines), parse_mode="HTML", reply_markup=TUTOR_MENU)


# ------- add student flow ------- #
@router.message(Command("add_student"))
@router.message(F.text == "➕ Ученика")
async def add_st_start(msg: Message, state: FSMContext, session):
    tutor = (await session.execute(select(User).where(User.telegram_id == msg.from_user.id))).scalar()
    if not tutor or not tutor.is_tutor:
        await msg.answer("Команда доступна только для репетитора.")
        return
    await msg.answer("Введите числовой Telegram‑ID ученика:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(AddStudent.waiting_id)

@router.message(AddStudent.waiting_id)
async def add_st_id(msg: Message, state: FSMContext):
    if not msg.text.isdigit():
        await msg.answer("ID должен быть числом. Попробуйте ещё раз:")
        return
    await state.update_data(st_id=int(msg.text))
    await msg.answer("Введите имя ученика (как будет отображаться):")
    await state.set_state(AddStudent.waiting_name)

@router.message(AddStudent.waiting_name)
async def add_st_name(msg: Message, state: FSMContext):
    name = msg.text.strip()
    if not name:
        await msg.answer("Имя не должно быть пустым. Повторите:")
        return
    await state.update_data(st_name=name)
    await msg.answer("Введите часовой пояс ученика (смещение от UTC, например +3 или -5):")
    await state.set_state(AddStudent.waiting_tz)

@router.message(AddStudent.waiting_tz)
async def add_st_tz(msg: Message, state: FSMContext, session):
    try:
        offset = int(msg.text.strip())
        if offset < -12 or offset > 14:
            raise ValueError
    except ValueError:
        await msg.answer("Введите целое число в диапазоне -12...+14:")
        return
    data = await state.get_data()
    st_id = data["st_id"]
    # проверяем, не существует ли уже
    exists = (
        await session.execute(select(User).where(User.telegram_id == st_id))
    ).scalar()
    if exists:
        await msg.answer("Такой пользователь уже зарегистрирован.", reply_markup=TUTOR_MENU)
        await state.clear()
        return
    tutor = (await session.execute(select(User).where(User.telegram_id == msg.from_user.id))).scalar()
    new_student = User(
        telegram_id=st_id,
        full_name=data["st_name"],
        is_tutor=False,
        tutor_id=tutor.id,
    )
    session.add(new_student)
    await msg.answer("✅ Ученик добавлен.", reply_markup=TUTOR_MENU)
    try:
        await msg.bot.send_message(st_id, f"Вас добавил репетитор {tutor.full_name or '…'}. Бот пришлёт вам расписание и ДЗ.")
    except Exception:
        await msg.answer("❗ Бот не смог написать ученику — он ещё не нажал /start.")
    await state.clear()