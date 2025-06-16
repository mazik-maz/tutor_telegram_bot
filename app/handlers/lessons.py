from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from sqlalchemy import select
from sqlalchemy.orm import selectinload  # ← добавили импорт для подгрузки связанных данных
from datetime import datetime, timedelta
from app.keyboards.menus import TUTOR_MENU, STUDENT_MENU
from app.models.models import User, Lesson
from app.services.scheduler import schedule_job

router = Router()

# ---------- FSM States ---------- #
class LessonCreate(StatesGroup):
    waiting_student = State()
    waiting_datetime = State()
    waiting_repeat = State()

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
        caption = f"{les.time.strftime('%d.%m %H:%M')} — {les.student.full_name}"
        mapping[caption] = les.id
        mas.append([KeyboardButton(text=caption)])
    mas.append([KeyboardButton(text="↩️ Отмена")])
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=mas)
    return kb, mapping

# ---------- Add lesson ---------- #
@router.message(Command("add_lesson"))
@router.message(F.text == "➕ Добавить урок")
async def add_lesson_start(msg: Message, session, state: FSMContext):
    """Начало диалога добавления урока: просим выбрать ученика."""
    tutor: User | None = (await session.execute(
        select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    if not tutor or not tutor.is_tutor:
        await msg.answer("Команда доступна только репетитору.")
        return
    # Явно получаем список учеников репетитора (избегаем lazy-отношения tutor.students)
    students = (await session.execute(
        select(User).where(User.tutor_id == tutor.id, User.is_tutor.is_(False)))
    ).scalars().all()
    if not students:
        await msg.answer("Сначала добавьте учеников.")
        return
    await msg.answer("Выберите ученика:", reply_markup=_student_kb(students))
    await state.set_state(LessonCreate.waiting_student)
    await state.update_data(students={s.full_name: s.id for s in students})

@router.message(LessonCreate.waiting_student, F.text)
async def add_lesson_got_student(msg: Message, state: FSMContext):
    data = await state.get_data()
    mapping: dict = data["students"]
    student_id = mapping.get(msg.text)
    if not student_id:
        await msg.answer("Пожалуйста, нажмите кнопку с именем ученика.")
        return
    await state.update_data(student_id=student_id)
    await msg.answer("Введите дату и время занятия (ДД.ММ.ГГГГ ЧЧ:ММ):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(LessonCreate.waiting_datetime)

@router.message(LessonCreate.waiting_datetime, F.text)
async def add_lesson_got_dt(msg: Message, state: FSMContext, session):
    try:
        dt = datetime.strptime(msg.text.strip(), "%d.%m.%Y %H:%M")
    except ValueError:
        await msg.answer("Неверный формат. Попробуйте ещё раз.")
        return
    if dt < datetime.now():
        await msg.answer("Нельзя назначить урок в прошлом.")
        return
    await state.update_data(lesson_dt=dt.isoformat())
    # ask repeat
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="Каждый день"), KeyboardButton(text="Каждую неделю")],
        [KeyboardButton(text="Не повторять")]
    ])
    await msg.answer("Повторять это занятие?", reply_markup=kb)
    await state.set_state(LessonCreate.waiting_repeat)

@router.message(LessonCreate.waiting_repeat, F.text)
async def add_lesson_finish(msg: Message, state: FSMContext, session):
    repeat_map = {
        "каждый день": "daily",
        "каждую неделю": "weekly",
        "не повторять": None,
    }
    choice = msg.text.lower()
    interval = repeat_map.get(choice)
    data = await state.get_data()
    tutor = (await session.execute(select(User).where(User.telegram_id == msg.from_user.id))).scalar()
    lesson = Lesson(
        tutor_id=tutor.id,
        student_id=data["student_id"],
        time=datetime.fromisoformat(data["lesson_dt"]),
        repeat_interval=interval
    )
    session.add(lesson)
    await session.flush()
    schedule_job(lesson.id, lesson.time - timedelta(hours=1))
    # auto‑generate recurrences (simpified: only here)
    if interval:
        delta = timedelta(days=1) if interval == "daily" else timedelta(weeks=1)
        horizon = datetime.now() + timedelta(days=14)
        nxt = lesson.time + delta
        while nxt <= horizon:
            copy = Lesson(tutor_id=lesson.tutor_id, student_id=lesson.student_id, time=nxt, series_id=lesson.id)
            session.add(copy)
            await session.flush()
            schedule_job(copy.id, copy.time - timedelta(hours=1))
            nxt += delta
    await msg.answer("Урок сохранён ✅", reply_markup=TUTOR_MENU)
    await state.clear()

# ---------- List & choose for edit ---------- #
@router.message(Command("lessons"))
@router.message(F.text == "📚 Расписание")
@router.message(F.text == "📚 Мои занятия")
async def list_lessons(msg: Message, session):
    """Вывод ближайших уроков (для репетитора или ученика)."""
    user: User = (await session.execute(
        select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    now = datetime.now()
    future = now + timedelta(days=30)
    if user.is_tutor:
        # Подгружаем связанные объекты Student для каждого Lesson, чтобы избежать lazy loading
        query = select(Lesson).where(
            Lesson.tutor_id == user.id,
            Lesson.time >= now, Lesson.time <= future,
            Lesson.is_canceled == False
        ).options(selectinload(Lesson.student)).order_by(Lesson.time)
    else:
        query = select(Lesson).where(
            Lesson.student_id == user.id,
            Lesson.time >= now, Lesson.time <= future,
            Lesson.is_canceled == False
        ).options(selectinload(Lesson.student)).order_by(Lesson.time)
    lessons = (await session.execute(query)).scalars().all()
    from app.messages.schedule import lessons_list_text
    txt = lessons_list_text(lessons, "Ближайшие уроки")
    await msg.answer(
        txt,
        reply_markup=(TUTOR_MENU if user.is_tutor else STUDENT_MENU),
        parse_mode="HTML"
    )

# Edit flow simplified: tutor can type /edit_lesson to pick
@router.message(Command("edit_lesson"))
@router.message(F.text == "✏️ Изменить урок")
async def edit_lesson_start(msg: Message, session, state: FSMContext):
    """Начало редактирования урока – выбор урока из списка."""
    tutor: User = (await session.execute(
        select(User).where(User.telegram_id == msg.from_user.id))
    ).scalar()
    if not tutor or not tutor.is_tutor:
        await msg.answer("Только для репетитора.")
        return
    # Получаем будущие уроки репетитора и сразу загружаем данные учеников
    lessons = (await session.execute(
        select(Lesson).where(
            Lesson.tutor_id == tutor.id,
            Lesson.time >= datetime.now(),
            Lesson.is_canceled == False
        ).options(selectinload(Lesson.student)))
    ).scalars().all()
    if not lessons:
        await msg.answer("Нет будущих уроков.")
        return
    kb, mapping = _lessons_kb(lessons)  # _lessons_kb использует les.student.full_name
    await state.update_data(map=mapping)
    await msg.answer("Выберите урок:", reply_markup=kb)
    await state.set_state(LessonEdit.waiting_pick)

@router.message(LessonEdit.waiting_pick)
async def edit_lesson_choose(msg: Message, state: FSMContext):
    data = await state.get_data()
    mapping: dict[str, int] = data["map"]
    lesson_id = mapping.get(msg.text)
    if not lesson_id:
        await msg.answer("Выберите урок из меню.")
        return
    await state.update_data(lesson_id=lesson_id)
    kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[[KeyboardButton(text="🚫 Отменить"), KeyboardButton(text="📅 Перенести")],
                                                             [KeyboardButton(text="↩️ Отмена")]])
    await msg.answer("Что сделать с уроком?", reply_markup=kb)
    await state.set_state(LessonEdit.waiting_action)

@router.message(LessonEdit.waiting_action, F.text == "🚫 Отменить")
async def edit_lesson_cancel(msg: Message, state: FSMContext, session):
    lesson_id = (await state.get_data())["lesson_id"]
    lesson: Lesson = await session.get(Lesson, lesson_id)
    lesson.is_canceled = True
    await msg.answer("Урок отменён.", reply_markup=TUTOR_MENU)
    await state.clear()

@router.message(LessonEdit.waiting_action, F.text == "📅 Перенести")
async def edit_lesson_reschedule(msg: Message, state: FSMContext):
    await msg.answer("Введите новую дату и время (ДД.ММ.ГГГГ ЧЧ:ММ):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(LessonEdit.waiting_new_time)

@router.message(LessonEdit.waiting_new_time)
async def edit_lesson_newdate(msg: Message, state: FSMContext, session):
    try:
        new_dt = datetime.strptime(msg.text.strip(), "%d.%m.%Y %H:%M")
    except ValueError:
        await msg.answer("Формат неверен. Попробуйте ещё раз.")
        return
    lesson_id = (await state.get_data())["lesson_id"]
    lesson: Lesson = await session.get(Lesson, lesson_id)
    lesson.time = new_dt
    schedule_job(lesson.id, new_dt - timedelta(hours=1))
    await msg.answer("Урок перенесён.", reply_markup=TUTOR_MENU)
    await state.clear()