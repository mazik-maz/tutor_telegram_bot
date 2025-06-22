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
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from datetime import datetime, timedelta

from app.keyboards.menus import TUTOR_MENU
from app.models.models import User, Lesson, Homework

router = Router()

# ──────────────────── FSM groups ──────────────────── #
class AddStudent(StatesGroup):
    waiting_id = State()
    waiting_name = State()
    waiting_tz = State()
    waiting_parent = State()

class ViewStudent(StatesGroup):
    choosing_student = State()
    waiting_new_value = State()
    field = State()
    choosing = State()


# ──────────────────── helper keyboards ──────────────────── #
def kb_students(students: list[User]) -> ReplyKeyboardMarkup:
    mas = []
    for st in students:
        mas.append([KeyboardButton(text=st.full_name)])
    mas.append([KeyboardButton(text="↩️ Отмена")])
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=mas)
    return kb

def kb_details() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [
            KeyboardButton(text="✏️ Имя"),
            KeyboardButton(text="🌍 UTC"),
            KeyboardButton(text="📞 Родитель"),
        ],
        [KeyboardButton(text="💬 Комментарий")],
        [KeyboardButton(text="↩️ Назад")],
    ])

async def show_student(msg: Message, st: User, *, next_lesson: Lesson | None, awaiting: int, pending: int):
    when = "не запланировано"
    if next_lesson:
        loc = next_lesson.scheduled_time + timedelta(hours=st.timezone_offset)
        when = loc.strftime("%d.%m %H:%M")
    txt = (
        f"<b>{st.full_name}</b>  (UTC{st.timezone_offset:+})\n"
        f"Контакт родителя: {st.parent_contact or '<i>не указан</i>'}\n\n"
        f"<b>Следующий урок:</b> {when}\n"
        f"<b>ДЗ:</b> {awaiting} ждут ответа, {pending} на проверке\n\n"
        f"<b>Комментарий:</b> {st.comment or '<i>нет</i>'}"
    )
    await msg.answer(txt, parse_mode="HTML", reply_markup=kb_details())

# ──────────────────── list & detail ──────────────────── #
@router.message(Command("students"))
@router.message(F.text == "👥 Мои ученики")
async def list_students(msg: Message, session, state: FSMContext):
    tutor: User | None = (
        await session.execute(select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    if not tutor or not tutor.is_tutor:
        await msg.answer("Команда доступна только для репетитора.")
        return

    students = (
        await session.execute(
            select(User).where(User.tutor_id == tutor.id, User.is_tutor.is_(False)).order_by(User.full_name)
        )
    ).scalars().all()

    if not students:
        await msg.answer(
            "У вас нет учеников. Используйте кнопку ➕ Ученика или команду /add_student.",
            reply_markup=TUTOR_MENU,
        )
        return

    await msg.answer("Выберите ученика:", reply_markup=kb_students(students))
    await state.update_data(st_map={s.full_name: s.id for s in students})
    await state.set_state(ViewStudent.choosing_student)


@router.message(ViewStudent.choosing_student)
async def show_student_detail(msg: Message, state: FSMContext, session):
    # Cancel
    if msg.text.strip().startswith("↩️"):
        await state.clear()
        await msg.answer("Отмена.", reply_markup=TUTOR_MENU)
        return

    data = await state.get_data()
    st_id = data.get("st_map", {}).get(msg.text.strip())
    if not st_id:
        await msg.answer("Выберите ученика из меню.")
        return

    student: User = (
        await session.execute(
            select(User)
            .options(selectinload(User.lessons_as_student))
            .where(User.id == st_id)
        )
    ).scalar()

    # Next lesson
    now = datetime.utcnow()
    next_less: Lesson | None = (
        await session.execute(
            select(Lesson)
            .where(
                Lesson.student_id == st_id,
                Lesson.is_canceled.is_(False),
                Lesson.scheduled_time >= now,
            )
            .order_by(Lesson.scheduled_time)
            .limit(1)
        )
    ).scalar()

    # Homework stats
    awaiting = (
        await session.execute(
            select(func.count())
            .select_from(Homework)
            .where(Homework.student_id == st_id, Homework.answered_at.is_(None))
        )
    ).scalar_one()
    pending = (
        await session.execute(
            select(func.count())
            .select_from(Homework)
            .where(
                Homework.student_id == st_id,
                Homework.answered_at.is_not(None),
                Homework.checked.is_(False),
            )
        )
    ).scalar_one()

    await state.update_data(cur=st_id)
    await show_student(msg, student, next_lesson=next_less, awaiting=awaiting, pending=pending)
    await state.set_state(ViewStudent.choosing)

# ╭────────────────────────── Запрос нового значения ─────────────────╮
@router.message(ViewStudent.choosing, F.text.in_(("✏️ Имя", "🌍 UTC", "📞 Родитель", "💬 Комментарий", "↩️ Назад")))
async def ask_new_value(msg: Message, state: FSMContext):
    field_map = {
        "✏️ Имя": ("name", "Введите новое имя:"),
        "🌍 UTC": ("tz", "Введите смещение UTC (−12…+14):"),
        "📞 Родитель": ("parent", "Введите контакт родителя («-» чтобы очистить):"),
        "💬 Комментарий": ("comment", "Введите комментарий («-» чтобы удалить):"),
        "↩️ Назад": ("back", "Отмена.")
    }
    field, prompt = field_map[msg.text]
    await state.update_data(edit_field=field)
    await msg.answer(prompt, reply_markup=ReplyKeyboardRemove())
    await state.set_state(ViewStudent.waiting_new_value)
    
@router.message(ViewStudent.choosing, F.text == "↩️ Назад")
async def ask_new_value(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Отмена.", reply_markup=TUTOR_MENU)
    return

# ╭────────────────────────── Сохраняем изменение ────────────────────╮
@router.message(ViewStudent.waiting_new_value)
async def save_new_value(msg: Message, state: FSMContext, session):
    data = await state.get_data()
    st: User = await session.get(User, data["cur"])
    field = data["edit_field"]

    if field == "name":
        value = msg.text.strip()
        if not value:
            await msg.answer("Имя не может быть пустым. Попробуйте снова:")
            return
        st.full_name = value
    elif field == "tz":
        try:
            offset = int(msg.text.strip())
            if offset < -12 or offset > 14:
                raise ValueError
        except ValueError:
            await msg.answer("Введите целое число от −12 до +14:")
            return
        st.timezone_offset = offset
    elif field == "parent":
        st.parent_contact = None if msg.text.strip() == "-" else msg.text.strip()
    elif field == "comment":
        st.comment = None if msg.text.strip() == "-" else msg.text.strip()
    else:
        await state.clear()
        await msg.answer("Отмена.", reply_markup=TUTOR_MENU)
        return

    await msg.answer("✅ Сохранено.", reply_markup=kb_details())
    # заново покажем карточку
    await state.clear()
    await list_students(msg, session, state)

# ╭────────────────────────── «Назад» из карточки ────────────────────╮
@router.message(ViewStudent.choosing_student, F.text == "↩️ Назад")
async def back_to_list(msg: Message, state: FSMContext, session):
    await state.clear()
    await list_students(msg, session, state)

# ──────────────────── add student ──────────────────── #
@router.message(Command("add_student"))
@router.message(F.text == "➕ Ученика")
async def add_student_start(msg: Message, state: FSMContext, session):
    tutor = (
        await session.execute(select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()

    if not tutor or not tutor.is_tutor:
        await msg.answer("Команда доступна только для репетитора.")
        return

    await msg.answer(
        "Введите числовой Telegram-ID ученика. Для его определения можете переслать сообщение ученика боту @getmyid_bot",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(AddStudent.waiting_id)


@router.message(AddStudent.waiting_id)
async def add_student_id(msg: Message, state: FSMContext):
    if not msg.text.isdigit():
        await msg.answer("ID должен быть числом. Попробуйте ещё раз:")
        return
    await state.update_data(st_id=int(msg.text))
    await msg.answer("Введите имя ученика:")
    await state.set_state(AddStudent.waiting_name)


@router.message(AddStudent.waiting_name)
async def add_student_name(msg: Message, state: FSMContext):
    name = msg.text.strip()
    if not name:
        await msg.answer("Имя не должно быть пустым. Повторите:")
        return
    await state.update_data(st_name=name)
    await msg.answer("Введите часовой пояс ученика (UTC, например +3 или -5):")
    await state.set_state(AddStudent.waiting_tz)


@router.message(AddStudent.waiting_tz)
async def add_student_tz(msg: Message, state: FSMContext):
    try:
        offset = int(msg.text.strip())
        if offset < -12 or offset > 14:
            raise ValueError
    except ValueError:
        await msg.answer("Введите целое число в диапазоне -12...+14:")
        return
    await state.update_data(st_tz=offset)
    await msg.answer(
        "Введите контакт родителя (телефон или @username) или '-' если нет:"
    )
    await state.set_state(AddStudent.waiting_parent)


@router.message(AddStudent.waiting_parent)
async def add_student_finish(msg: Message, state: FSMContext, session):
    data = await state.get_data()
    st_id = data["st_id"]

    # Проверить существование
    exists = (
        await session.execute(select(User).where(User.telegram_id == st_id))
    ).scalar()
    if exists:
        await msg.answer("Этот пользователь уже зарегистрирован.", reply_markup=TUTOR_MENU)
        await state.clear()
        return

    tutor: User = (
        await session.execute(select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()

    new_st = User(
        telegram_id=st_id,
        full_name=data["st_name"],
        is_tutor=False,
        tutor_id=tutor.id,
        timezone_offset=data["st_tz"],
        parent_contact=None if msg.text.strip() == "-" else msg.text.strip(),
        reminder_minutes=60,
    )
    session.add(new_st)
    await msg.answer("✅ Ученик добавлен.", reply_markup=TUTOR_MENU)

    # Уведомить ученика, если он уже общался с ботом
    try:
        await msg.bot.send_message(
            st_id,
            f"Вас добавил репетитор {tutor.full_name or '…'}. "
            "Бот будет присылать расписание и ДЗ.",
        )
    except Exception:
        await msg.answer("⚠️ Бот пока не может написать ученику — он ещё не нажал /start.")

    await state.clear()