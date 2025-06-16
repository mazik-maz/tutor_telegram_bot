"""Handlers: add / list / edit lessons with custom repeat & duration."""
from __future__ import annotations

from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.keyboards.menus import TUTOR_MENU, STUDENT_MENU
from app.models.models import User, Lesson
from app.services.scheduler import schedule_jobs

router = Router()

# ───────────────── FSM ───────────────── #
class LessonCreate(StatesGroup):
    waiting_student = State()
    waiting_datetime = State()
    waiting_duration = State()
    waiting_repeat = State()
    waiting_custom_days = State()


class LessonEdit(StatesGroup):
    waiting_pick = State()
    waiting_action = State()
    waiting_new_time = State()

# ---------- Helpers ---------- #

def _student_kb(students: list[User]):
    mas = []
    for s in students:
        mas.append([KeyboardButton(text=s.full_name)])
    mas.append([KeyboardButton(text="↩️ Отмена")])
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=mas)
    return kb

def _lessons_kb(lessons: list[Lesson]):
    mas = []
    mapping: dict[str, int] = {}
    for les in lessons:
        caption = f"{les.scheduled_time.strftime('%d.%m %H:%M')} — {les.student.full_name}"
        mapping[caption] = les.id
        mas.append([KeyboardButton(text=caption)])
    mas.append([KeyboardButton(text="↩️ Отмена")])
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=mas)
    return kb, mapping

# ─────────────────────── add lesson ─────────────────────── #
@router.message(Command("add_lesson"))
@router.message(F.text == "➕ Добавить урок")
async def add_lesson_start(msg: Message, session, state: FSMContext):
    tutor = (
        await session.execute(select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    if not tutor or not tutor.is_tutor:
        await msg.answer("Команда доступна только репетитору.")
        return

    students = (
        await session.execute(
            select(User).where(User.tutor_id == tutor.id, User.is_tutor.is_(False))
        )
    ).scalars().all()
    if not students:
        await msg.answer("Сначала добавьте учеников.")
        return

    await msg.answer("Выберите ученика:", reply_markup=_student_kb(students))
    await state.update_data(st_map={s.full_name: s.id for s in students})
    await state.set_state(LessonCreate.waiting_student)


@router.message(LessonCreate.waiting_student)
async def got_student(msg: Message, state: FSMContext):
    if msg.text.startswith("↩️"):
        await msg.answer("Отмена.", reply_markup=TUTOR_MENU)
        await state.clear()
        return

    mapping = (await state.get_data())["st_map"]
    st_id = mapping.get(msg.text.strip())
    if not st_id:
        await msg.answer("Пожалуйста, выберите ученика через кнопку.")
        return

    await state.update_data(student_id=st_id)
    await msg.answer(
        "Введите дату и время занятия (ДД.ММ.ГГГГ ЧЧ:ММ) – по времени ученика:",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(LessonCreate.waiting_datetime)


@router.message(LessonCreate.waiting_datetime)
async def got_datetime(msg: Message, state: FSMContext, session):
    try:
        dt_local = datetime.strptime(msg.text.strip(), "%d.%m.%Y %H:%M")
    except ValueError:
        await msg.answer("Формат неверен. Повторите (ДД.ММ.ГГГГ ЧЧ:ММ):")
        return

    data = await state.get_data()
    student: User = await session.get(User, data["student_id"])
    dt_utc = dt_local - timedelta(hours=student.timezone_offset)

    if dt_utc <= datetime.utcnow():
        await msg.answer("Нельзя назначить урок в прошлом.")
        return

    await state.update_data(lesson_dt=dt_utc.isoformat())
    await msg.answer("Введите длительность урока (в минутах):")
    await state.set_state(LessonCreate.waiting_duration)


@router.message(LessonCreate.waiting_duration)
async def got_duration(msg: Message, state: FSMContext):
    if not msg.text.isdigit() or int(msg.text) <= 0:
        await msg.answer("Введите положительное число – минуты:")
        return
    await state.update_data(duration=int(msg.text))

    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="Каждый день"), KeyboardButton(text="Каждую неделю")],
        [KeyboardButton(text="Другая периодичность"), KeyboardButton(text="Не повторять")],
    ])
    await msg.answer("Повторять занятие?", reply_markup=kb)
    await state.set_state(LessonCreate.waiting_repeat)


@router.message(LessonCreate.waiting_repeat)
async def got_repeat_choice(msg: Message, state: FSMContext):
    choice = msg.text.lower()
    if choice.startswith("↩️"):
        await msg.answer("Отмена.", reply_markup=TUTOR_MENU)
        await state.clear()
        return

    if choice == "другая периодичность":
        await msg.answer("Введите количество дней между уроками (целое число >0):", reply_markup=ReplyKeyboardRemove())
        await state.set_state(LessonCreate.waiting_custom_days)
        return

    interval_map = {
        "каждый день": "daily",
        "каждую неделю": "weekly",
        "не повторять": None,
    }
    interval = interval_map.get(choice)
    if interval is None and choice != "не повторять":
        await msg.answer("Выберите один из вариантов кнопками.")
        return

    await create_lesson(state, interval, msg, session=None)


@router.message(LessonCreate.waiting_custom_days)
async def got_custom_days(msg: Message, state: FSMContext):
    if not msg.text.isdigit() or int(msg.text) <= 0:
        await msg.answer("Введите положительное целое число дней:")
        return
    interval = msg.text.strip()  # число дней
    await create_lesson(state, interval, msg, session=None)  # session берём внутри


async def create_lesson(state: FSMContext, interval: str | None, msg: Message, session):
    """Фактическое создание урока + уведомления."""
    if session is None:
        # получили из предыдущего handler без параметра
        from app.models.db import AsyncSessionLocal

        session = AsyncSessionLocal()
        own = True
    else:
        own = False

    async with session:
        data = await state.get_data()
        tutor: User = (
            await session.execute(select(User).where(User.telegram_id == msg.from_user.id))
        ).scalar()
        student: User = await session.get(User, data["student_id"])

        lesson = Lesson(
            tutor_id=tutor.id,
            student_id=student.id,
            scheduled_time=datetime.fromisoformat(data["lesson_dt"]),
            duration=data["duration"],
            repeat_interval=interval,
        )
        session.add(lesson)
        await session.flush()

        # уведомление ученику о новом уроке
        st_time = (lesson.scheduled_time + timedelta(hours=student.timezone_offset)).strftime("%d.%m %H:%M")
        await msg.bot.send_message(
            student.telegram_id,
            f"📚 Новое занятие: {st_time}. "
            f"Длительность {lesson.duration} мин.",
        )

        # ставим напоминания / follow-up
        schedule_jobs(lesson, tutor, student)

        # автогенерация повторений (14 дней вперёд)
        if interval:
            delta = (
                timedelta(days=int(interval))
                if interval.isdigit()
                else timedelta(days=1) if interval == "daily"
                else timedelta(weeks=1)
            )
            horizon = datetime.utcnow() + timedelta(days=14)
            nxt = lesson.scheduled_time + delta
            while nxt <= horizon:
                copy = Lesson(
                    tutor_id=tutor.id,
                    student_id=student.id,
                    scheduled_time=nxt,
                    duration=lesson.duration,
                    repeat_interval=interval,
                    series_id=lesson.id,
                )
                session.add(copy)
                await session.flush()
                schedule_jobs(copy, tutor, student)
                nxt += delta

        if own:
            await session.commit()

    await msg.answer("✅ Урок сохранён.", reply_markup=TUTOR_MENU)
    await state.clear()

# ─────────────────────── list lessons (unchanged) ─────────────────────── #
@router.message(Command("lessons"))
@router.message(F.text == "📚 Расписание")
@router.message(F.text == "📚 Мои занятия")
async def list_lessons(msg: Message, session):
    user: User = (
        await session.execute(select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()

    now = datetime.utcnow()
    future = now + timedelta(days=30)

    if user.is_tutor:
        cond = Lesson.tutor_id == user.id
        extra_load = selectinload(Lesson.student)      # понадобится имя студента
    else:
        cond = Lesson.student_id == user.id
        extra_load = selectinload(Lesson.tutor)        # вдруг понадобится позже

    lessons = (
        await session.execute(
            select(Lesson)
            .options(extra_load)                       # ← EAGER-load
            .where(
                cond,
                Lesson.scheduled_time >= now,
                Lesson.scheduled_time <= future,
                Lesson.is_canceled.is_(False),
            )
            .order_by(Lesson.scheduled_time)
        )
    ).scalars().all()

    if not lessons:
        await msg.answer(
            "Ближайших уроков нет.",
            reply_markup=TUTOR_MENU if user.is_tutor else STUDENT_MENU,
        )
        return

    lines = ["<b>Ближайшие уроки</b>"]
    for les in lessons:
        dt_local = les.scheduled_time + timedelta(hours=user.timezone_offset)
        if user.is_tutor:
            lines.append(f"• {dt_local.strftime('%d.%m %H:%M')} — {les.student.full_name}")
        else:
            lines.append(f"• {dt_local.strftime('%d.%m %H:%M')}")
    await msg.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=TUTOR_MENU if user.is_tutor else STUDENT_MENU,
    )


# ─────────────────────── edit lesson (отмена / перенос) ─────────────────────── #
@router.message(Command("edit_lesson"))
@router.message(F.text == "✏️ Изменить урок")
async def edit_lesson_start(msg: Message, session, state: FSMContext):
    tutor: User | None = (
        await session.execute(select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()

    if not tutor or not tutor.is_tutor:
        await msg.answer("Только для репетитора.")
        return

    # 👉 подгружаем student, чтобы не было ленивого запроса
    lessons = (
        await session.execute(
            select(Lesson)
            .options(selectinload(Lesson.student))                 # <-- ключевая строчка
            .where(
                Lesson.tutor_id == tutor.id,
                Lesson.scheduled_time >= datetime.utcnow(),
                Lesson.is_canceled.is_(False),
            )
            .order_by(Lesson.scheduled_time)
        )
    ).scalars().all()

    if not lessons:
        await msg.answer("Нет запланированных уроков.")
        return

    kb, mapping = _lessons_kb(lessons)
    await state.update_data(map=mapping)
    await msg.answer("Выберите урок:", reply_markup=kb)
    await state.set_state(LessonEdit.waiting_pick)


@router.message(LessonEdit.waiting_pick)
async def edit_pick(msg: Message, state: FSMContext):
    if msg.text.startswith("↩️"):
        await msg.answer("Отмена.", reply_markup=TUTOR_MENU)
        await state.clear()
        return

    mapping = (await state.get_data())["map"]
    les_id = mapping.get(msg.text)
    if not les_id:
        await msg.answer("Нажмите кнопку из списка.")
        return

    await state.update_data(lesson_id=les_id)
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="🚫 Отменить урок"), KeyboardButton(text="📅 Перенести")],
        [KeyboardButton(text="↩️ Отмена")],
    ])
    await msg.answer("Действие с уроком:", reply_markup=kb)
    await state.set_state(LessonEdit.waiting_action)

@router.message(LessonEdit.waiting_action, F.text == "🚫 Отменить урок")
async def cancel_lesson(msg: Message, state: FSMContext, session):
    les_id = (await state.get_data())["lesson_id"]
    lesson: Lesson = await session.get(Lesson, les_id)
    lesson.is_canceled = True

    student: User = await session.get(User, lesson.student_id)
    st_time = (lesson.scheduled_time + timedelta(hours=student.timezone_offset)).strftime("%d.%m %H:%M")
    await msg.bot.send_message(student.telegram_id, f"❌ Занятие {st_time} отменено репетитором.")
    await msg.answer("Урок отменён.", reply_markup=TUTOR_MENU)
    await state.clear()


@router.message(LessonEdit.waiting_action, F.text == "📅 Перенести")
async def reschedule_prompt(msg: Message, state: FSMContext):
    await msg.answer("Введите новую дату-время (ДД.ММ.ГГГГ ЧЧ:ММ) по времени ученика:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(LessonEdit.waiting_new_time)


@router.message(LessonEdit.waiting_new_time)
async def reschedule_save(msg: Message, state: FSMContext, session):
    try:
        dt_local = datetime.strptime(msg.text.strip(), "%d.%m.%Y %H:%M")
    except ValueError:
        await msg.answer("Формат неверен. Повторите:")
        return

    data = await state.get_data()
    lesson: Lesson = await session.get(Lesson, data["lesson_id"])
    student: User = await session.get(User, lesson.student_id)
    tutor: User = await session.get(User, lesson.tutor_id)

    dt_utc = dt_local - timedelta(hours=student.timezone_offset)
    if dt_utc <= datetime.utcnow():
        await msg.answer("Дата в прошлом. Попробуйте ещё раз.")
        return

    lesson.scheduled_time = dt_utc
    # Пересоздаём напоминания
    schedule_jobs(lesson, tutor, student)

    st_time = dt_local.strftime("%d.%m %H:%M")
    await msg.bot.send_message(student.telegram_id, f"📅 Занятие перенесено на {st_time}.")
    await msg.answer("Перенос сохранён.", reply_markup=TUTOR_MENU)
    await state.clear()