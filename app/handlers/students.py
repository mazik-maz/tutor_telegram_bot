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
    waiting_comment = State()


# ──────────────────── helper keyboards ──────────────────── #
def _students_kb(students: list[User]) -> ReplyKeyboardMarkup:
    mas = []
    for st in students:
        mas.append([KeyboardButton(text=st.full_name)])
    mas.append([KeyboardButton(text="↩️ Отмена")])
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=mas)
    return kb

def _detail_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="✏️ Изменить комментарий")],
        [KeyboardButton(text="↩️ Назад")],
    ])


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
            select(User).where(User.tutor_id == tutor.id, User.is_tutor.is_(False))
        )
    ).scalars().all()

    if not students:
        await msg.answer(
            "У вас нет учеников. Используйте кнопку ➕ Ученика или команду /add_student.",
            reply_markup=TUTOR_MENU,
        )
        return

    await msg.answer("Выберите ученика:", reply_markup=_students_kb(students))
    await state.update_data(st_map={s.full_name: s.id for s in students})
    await state.set_state(ViewStudent.choosing_student)


# ——— edit comment ——— #
@router.message(ViewStudent.choosing_student, F.text == "✏️ Изменить комментарий")
async def edit_comment_prompt(msg: Message, state: FSMContext):
    await msg.answer(
        "Отправьте новый комментарий (или '-' чтобы удалить):",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(ViewStudent.waiting_comment)


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

    student: User = await session.get(User, st_id)

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

    if next_less:
        loc_time = next_less.scheduled_time + timedelta(hours=student.timezone_offset)
        lesson_str = loc_time.strftime("%d.%m %H:%M")
    else:
        lesson_str = "не запланировано"

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

    comment = student.comment or "<i>нет комментария</i>"

    text = (
        f"<b>{student.full_name}</b> (UTC{student.timezone_offset:+})\n"
        f"Следующий урок: {lesson_str}\n"
        f"ДЗ: {awaiting} ждут ответа, {pending} на проверке\n\n"
        f"<b>Комментарий:</b> {comment}"
    )
    await msg.answer(text, parse_mode="HTML", reply_markup=_detail_kb())
    await state.update_data(cur_student_id=st_id)


@router.message(ViewStudent.waiting_comment)
async def save_comment(msg: Message, state: FSMContext, session):
    st_id = (await state.get_data()).get("cur_student_id")
    if not st_id:
        await msg.answer("Ошибка состояния. Начните сначала.", reply_markup=TUTOR_MENU)
        await state.clear()
        return

    student: User = await session.get(User, st_id)
    new_text = msg.text.strip()
    student.comment = None if new_text == "-" else new_text
    await msg.answer("✅ Комментарий обновлён.", reply_markup=_detail_kb())

    # Вернуться к карточке
    await state.set_state(ViewStudent.choosing_student)
    await list_students(msg, session, state)


@router.message(ViewStudent.choosing_student, F.text == "↩️ Назад")
async def back_to_list(msg: Message, state: FSMContext, session):
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
        "Введите числовой Telegram-ID ученика:",
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